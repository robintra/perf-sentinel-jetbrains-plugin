package io.github.robintra.perfsentinel.java

import com.intellij.openapi.application.readAction
import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import com.intellij.psi.JavaPsiFacade
import com.intellij.psi.search.GlobalSearchScope
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver

class JavaAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? {
        val location = finding.codeLocation ?: return null
        val namespace = location.namespace ?: return null
        val function = location.function?.substringBefore('(')?.substringAfterLast('.') ?: return null
        return readAction {
            JavaPsiFacade.getInstance(project)
                .findClass(namespace, GlobalSearchScope.projectScope(project))
                ?.findMethodsByName(function, true)
                ?.firstOrNull()
        }
    }
}
