package io.github.robintra.perfsentinel.editor

import com.intellij.openapi.Disposable
import com.intellij.openapi.application.EDT
import com.intellij.openapi.components.Service
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.colors.CodeInsightColors
import com.intellij.openapi.editor.colors.EditorColorsManager
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.editor.markup.RangeHighlighter
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.FileEditorManagerListener
import com.intellij.openapi.fileEditor.TextEditor
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VirtualFile
import io.github.robintra.perfsentinel.core.FindingResponse
import io.github.robintra.perfsentinel.core.HighlightLevel
import io.github.robintra.perfsentinel.core.highlightLevel
import io.github.robintra.perfsentinel.core.resolveProjectFile
import io.github.robintra.perfsentinel.core.zeroBasedLine
import io.github.robintra.perfsentinel.service.FINDINGS_TOPIC
import io.github.robintra.perfsentinel.service.FindingsListener
import java.nio.file.Paths
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Service(Service.Level.PROJECT)
class FindingHighlighter(
    private val project: Project,
    private val coroutineScope: CoroutineScope,
) : Disposable {
    private var findings: List<FindingResponse> = emptyList()
    private val highlighters = mutableMapOf<Editor, MutableList<RangeHighlighter>>()
    private var highlightJob: Job? = null

    init {
        project.messageBus.connect(this).apply {
            subscribe(FINDINGS_TOPIC, FindingsListener { state ->
                findings = state.findings
                scheduleHighlights()
            })
            subscribe(FileEditorManagerListener.FILE_EDITOR_MANAGER, object : FileEditorManagerListener {
                override fun fileOpened(source: FileEditorManager, file: VirtualFile) = scheduleHighlights()
            })
        }
    }

    private fun scheduleHighlights() {
        highlightJob?.cancel()
        val root = project.basePath?.let(Paths::get) ?: return
        val snapshot = findings
        highlightJob = coroutineScope.launch {
            val resolved = withContext(Dispatchers.IO) {
                snapshot.mapNotNull { response ->
                    response.finding.codeLocation?.filepath
                        ?.let { resolveProjectFile(root, it) }
                        // Go through the VFS like DirectAnchorResolver does: a nio path string never
                        // matches VirtualFile.path on Windows, nor under a symlinked project root.
                        ?.let { LocalFileSystem.getInstance().refreshAndFindFileByNioFile(it) }
                        ?.let { ResolvedHighlight(it, response) }
                }
            }
            withContext(Dispatchers.EDT) { applyHighlights(resolved) }
        }
    }

    private fun applyHighlights(resolved: List<ResolvedHighlight>) {
        clearHighlights()
        FileEditorManager.getInstance(project).allEditors.filterIsInstance<TextEditor>().forEach { textEditor ->
            val editor = textEditor.editor
            resolved.forEach { resolvedFinding ->
                val response = resolvedFinding.response
                val location = response.finding.codeLocation ?: return@forEach
                if (resolvedFinding.file != textEditor.file) return@forEach
                val line = zeroBasedLine(location.lineNumber, editor.document.lineCount) ?: return@forEach
                val attributesKey = when (highlightLevel(response.finding.confidence)) {
                    HighlightLevel.HINT -> CodeInsightColors.INFORMATION_ATTRIBUTES
                    HighlightLevel.WARNING -> CodeInsightColors.WARNINGS_ATTRIBUTES
                    HighlightLevel.ERROR -> CodeInsightColors.ERRORS_ATTRIBUTES
                }
                val attributes = EditorColorsManager.getInstance().globalScheme.getAttributes(attributesKey)
                val highlighter = editor.markupModel.addLineHighlighter(line, HighlighterLayer.WARNING, attributes)
                highlighter.errorStripeTooltip = "${response.finding.type}: ${response.finding.suggestion}"
                highlighters.getOrPut(editor, ::mutableListOf).add(highlighter)
            }
        }
    }

    private fun clearHighlights() {
        highlighters.forEach { (editor, ranges) ->
            if (!editor.isDisposed) ranges.forEach(editor.markupModel::removeHighlighter)
        }
        highlighters.clear()
    }

    override fun dispose() {
        highlightJob?.cancel()
        clearHighlights()
    }

    private data class ResolvedHighlight(val file: VirtualFile, val response: FindingResponse)
}
