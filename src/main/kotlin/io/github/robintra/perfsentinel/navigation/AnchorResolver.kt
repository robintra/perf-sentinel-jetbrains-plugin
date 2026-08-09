package io.github.robintra.perfsentinel.navigation

import com.intellij.openapi.extensions.ExtensionPointName
import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import io.github.robintra.perfsentinel.core.Finding

interface AnchorResolver {
    suspend fun resolve(project: Project, finding: Finding): Navigatable?

    companion object {
        val EP_NAME: ExtensionPointName<AnchorResolver> =
            ExtensionPointName.create("io.github.robintra.perfsentinel.anchorResolver")
    }
}

internal fun Finding.symbolName(): String? = codeLocation?.function
    ?.substringBefore('(')
    ?.substringAfterLast('.')
    ?.trim()
    ?.takeIf(String::isNotEmpty)
