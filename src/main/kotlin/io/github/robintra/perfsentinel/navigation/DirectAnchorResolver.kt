package io.github.robintra.perfsentinel.navigation

import com.intellij.openapi.application.readAction
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.pom.Navigatable
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.resolveProjectFile
import io.github.robintra.perfsentinel.core.zeroBasedLine
import java.nio.file.Paths
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

object DirectAnchorResolver {
    suspend fun resolve(project: Project, finding: Finding): Navigatable? {
        val root = project.basePath?.let(Paths::get) ?: return null
        val location = finding.codeLocation ?: return null
        val file = withContext(Dispatchers.IO) {
            val path = location.filepath?.let { resolveProjectFile(root, it) } ?: return@withContext null
            LocalFileSystem.getInstance().refreshAndFindFileByNioFile(path)
        } ?: return null
        return readAction {
            val document = FileDocumentManager.getInstance().getDocument(file) ?: return@readAction null
            val line = zeroBasedLine(location.lineNumber, document.lineCount) ?: return@readAction null
            OpenFileDescriptor(project, file, line, 0)
        }
    }
}

object AnchorNavigator {
    suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        DirectAnchorResolver.resolve(project, finding)
            ?: resolve(project, finding, AnchorResolver.EP_NAME.extensionList)

    internal suspend fun resolve(
        project: Project,
        finding: Finding,
        resolvers: List<AnchorResolver>,
    ): Navigatable? {
        // Resolve every language before trying heuristic fallbacks: the same qualified name can exist
        // in two languages, and registration order must not pick a winner.
        val semantic = resolvers.mapNotNull { resolver -> isolate { resolver.resolve(project, finding) } }
        if (semantic.isNotEmpty()) return semantic.singleOrNull()
        return resolvers.mapNotNull { resolver -> isolate { resolver.resolveFallback(project, finding) } }
            .singleOrNull()
    }

    // One resolver reaching a bad reflective PSI edge must remove only itself from the candidate set,
    // not abort the pass for every other language.
    private suspend fun isolate(resolve: suspend () -> Navigatable?): Navigatable? = try {
        resolve()
    } catch (cancellation: CancellationException) {
        throw cancellation
    } catch (_: Exception) {
        null
    }
}
