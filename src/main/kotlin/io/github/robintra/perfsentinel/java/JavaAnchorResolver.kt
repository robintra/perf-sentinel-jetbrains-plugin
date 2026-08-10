package io.github.robintra.perfsentinel.java

import com.intellij.openapi.application.readAction
import com.intellij.openapi.project.DumbService
import com.intellij.openapi.project.IndexNotReadyException
import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import com.intellij.psi.JavaPsiFacade
import com.intellij.psi.search.GlobalSearchScope
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.symbolName

class JavaAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        safelyResolve(project) { resolveMethod(project, finding) }

    override suspend fun resolveFallback(project: Project, finding: Finding): Navigatable? =
        safelyResolve(project) { JpaTableAnchorResolver.resolve(project, finding) }

    private suspend fun safelyResolve(project: Project, resolver: () -> Navigatable?): Navigatable? {
        if (DumbService.isDumb(project)) return null
        return try {
            readAction { resolver() }
        } catch (_: IndexNotReadyException) {
            null
        }
    }

    private fun resolveMethod(project: Project, finding: Finding): Navigatable? {
        val namespace = finding.codeLocation?.namespace ?: return null
        val function = finding.symbolName() ?: return null
        val owner = JavaPsiFacade.getInstance(project)
            .findClass(namespace, GlobalSearchScope.projectScope(project))
            ?: return null
        // Own declarations first so an override does not read as ambiguity against the
        // method it overrides; base classes only when the class declares nothing by that name.
        val declared = owner.findMethodsByName(function, false)
        val candidates = if (declared.isNotEmpty()) declared else owner.findMethodsByName(function, true)
        return candidates.singleOrNull()
    }
}
