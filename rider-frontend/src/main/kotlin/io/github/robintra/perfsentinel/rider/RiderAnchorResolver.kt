package io.github.robintra.perfsentinel.rider

import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.pom.Navigatable
import com.jetbrains.rider.projectView.solution
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.rider.model.CSharpSymbolRequest
import io.github.robintra.perfsentinel.rider.model.SourceAnchor
import io.github.robintra.perfsentinel.rider.model.perfSentinelModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

internal typealias CSharpLookup = suspend (Project, String, String) -> SourceAnchor?

class RiderAnchorResolver internal constructor(
    private val lookup: CSharpLookup,
    private val findFile: suspend (String) -> VirtualFile?,
) : AnchorResolver {
    constructor() : this(::resolveCSharpSymbol, ::findLocalFile)

    override suspend fun resolve(project: Project, finding: Finding): Navigatable? {
        val location = finding.codeLocation ?: return null
        val namespace = location.namespace?.takeIf { it.isNotBlank() } ?: return null
        val function = location.function?.takeIf { it.isNotBlank() } ?: return null
        val anchor = lookup(project, namespace, function) ?: return null
        val file = findFile(anchor.path) ?: return null
        if (anchor.offset.toLong() !in 0..file.length) return null
        return OpenFileDescriptor(project, file, anchor.offset)
    }
}

private suspend fun resolveCSharpSymbol(project: Project, namespace: String, function: String): SourceAnchor? =
    project.solution.perfSentinelModel.resolveCSharpSymbol.startSuspending(
        CSharpSymbolRequest(namespace, function),
    )

private suspend fun findLocalFile(path: String): VirtualFile? = withContext(Dispatchers.IO) {
    LocalFileSystem.getInstance().refreshAndFindFileByPath(path)
}
