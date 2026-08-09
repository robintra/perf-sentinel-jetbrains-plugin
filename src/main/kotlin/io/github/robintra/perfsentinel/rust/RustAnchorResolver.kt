package io.github.robintra.perfsentinel.rust

import com.intellij.openapi.project.Project
import com.intellij.navigation.NavigationItem
import com.intellij.pom.Navigatable
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiNamedElement
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.PlatformSymbolAnchorResolver

class RustAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        PlatformSymbolAnchorResolver.resolve(project, finding, setOf("Rust"), ::rustQualifiedName)

    private fun rustQualifiedName(element: PsiElement): String? {
        val name = (element as? PsiNamedElement)?.name ?: return null
        val relativePath = element.noArgMethod("getCrateRelativePath") as? String
            ?: (element as? NavigationItem)?.presentation?.locationString
                ?.removePrefix("(in ")
                ?.removeSuffix(")")
                ?.let { "::$it::$name" }
            ?: return null
        val impl = generateSequence(element.parent) { it.parent }
            .firstOrNull { it.javaClass.simpleName.startsWith("RsImplItem") }
            ?: return relativePath
        val type = impl.noArgMethod("getTypeReference") as? PsiElement ?: return relativePath
        return relativePath.substringBeforeLast("::") + "::${type.text}::$name"
    }

    private fun PsiElement.noArgMethod(name: String): Any? = runCatching {
        javaClass.methods.firstOrNull { it.name == name && it.parameterCount == 0 }?.invoke(this)
    }.getOrNull()
}
