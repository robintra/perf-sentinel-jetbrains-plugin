package io.github.robintra.perfsentinel.go

import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiNamedElement
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.PlatformSymbolAnchorResolver

class GoAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        PlatformSymbolAnchorResolver.resolve(
            project,
            finding,
            setOf("go"),
            ::goQualifiedName,
            lookupNameAliases = { namespace, function ->
                sequenceOf("${namespace.substringBefore('.')}.$function")
            },
        )

    private fun goQualifiedName(element: PsiElement): String? {
        val name = (element as? PsiNamedElement)?.name ?: return null
        val packageName = element.containingFile.noArgMethod("getPackageName") as? String ?: return null
        val receiver = (element.noArgMethod("getReceiverType") as? PsiElement)?.text
        return listOfNotNull(packageName, receiver, name).joinToString(".")
    }

    private fun PsiElement.noArgMethod(name: String): Any? = runCatching {
        javaClass.methods.firstOrNull { it.name == name && it.parameterCount == 0 }?.invoke(this)
    }.getOrNull()
}
