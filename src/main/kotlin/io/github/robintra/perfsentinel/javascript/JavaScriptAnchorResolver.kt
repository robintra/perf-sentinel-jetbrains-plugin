package io.github.robintra.perfsentinel.javascript

import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiNamedElement
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.PlatformSymbolAnchorResolver

class JavaScriptAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        PlatformSymbolAnchorResolver.resolve(
            project,
            finding,
            setOf("ECMAScript 6", "JavaScript", "TypeScript"),
            ::moduleQualifiedName,
        )

    private fun moduleQualifiedName(element: PsiElement): String? {
        val name = (element as? PsiNamedElement)?.name ?: return null
        val module = element.containingFile?.virtualFile?.nameWithoutExtension ?: return null
        return "$module.$name"
    }
}
