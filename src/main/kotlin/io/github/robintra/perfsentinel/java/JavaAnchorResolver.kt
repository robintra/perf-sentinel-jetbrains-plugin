package io.github.robintra.perfsentinel.java

import com.intellij.lang.java.JavaLanguage
import com.intellij.openapi.application.readAction
import com.intellij.openapi.project.DumbService
import com.intellij.openapi.project.IndexNotReadyException
import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import com.intellij.psi.JavaPsiFacade
import com.intellij.psi.PsiMethod
import com.intellij.psi.search.GlobalSearchScope
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.symbolName

class JavaAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        safelyResolve(project) { resolveMethod(project, finding) }

    override suspend fun resolveFallback(project: Project, finding: Finding): Navigatable? =
        safelyResolve(project) {
            // Candidates found but refused by singleOrNull() is a deliberate "no navigation on
            // ambiguity", not a miss. Guessing a table here would override that on purpose.
            if (methodCandidates(project, finding).isNotEmpty()) null
            else JpaTableAnchorResolver.resolve(project, finding)
        }

    private suspend fun safelyResolve(project: Project, resolver: () -> Navigatable?): Navigatable? {
        if (DumbService.isDumb(project)) return null
        return try {
            readAction { resolver() }
        } catch (_: IndexNotReadyException) {
            null
        }
    }

    private fun resolveMethod(project: Project, finding: Finding): Navigatable? =
        methodCandidates(project, finding).singleOrNull()

    private fun methodCandidates(project: Project, finding: Finding): List<PsiMethod> {
        val namespace = finding.codeLocation?.namespace ?: return emptyList()
        val function = finding.symbolName() ?: return emptyList()
        val owner = JavaPsiFacade.getInstance(project)
            .findClass(namespace, GlobalSearchScope.projectScope(project))
            ?: return emptyList()
        // JavaPsiFacade also answers with light classes generated from Kotlin sources. Those belong to
        // KotlinAnchorResolver; returning them here makes the dispatcher see one symbol as two hits
        // and refuse to navigate at all.
        if (owner.navigationElement.language.id != JavaLanguage.INSTANCE.id) return emptyList()
        // Own declarations first so an override does not read as ambiguity against the
        // method it overrides; base classes only when the class declares nothing by that name.
        val declared = owner.findMethodsByName(function, false)
        val candidates = if (declared.isNotEmpty()) declared else owner.findMethodsByName(function, true)
        return candidates.toList()
    }
}
