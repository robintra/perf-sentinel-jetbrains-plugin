package io.github.robintra.perfsentinel.kotlin

import com.intellij.openapi.application.readAction
import com.intellij.openapi.project.IndexNotReadyException
import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import com.intellij.psi.search.GlobalSearchScope
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.symbolName
import org.jetbrains.kotlin.idea.stubindex.KotlinFullClassNameIndex
import org.jetbrains.kotlin.idea.stubindex.KotlinFunctionShortNameIndex
import org.jetbrains.kotlin.psi.KtNamedFunction

class KotlinAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? {
        val namespace = finding.codeLocation?.namespace?.trim()?.takeIf(String::isNotEmpty) ?: return null
        val function = finding.symbolName() ?: return null
        return try {
            readAction {
                val scope = GlobalSearchScope.projectScope(project)
                val members = KotlinFullClassNameIndex[namespace, project, scope]
                    .flatMap { owner -> owner.declarations.filterIsInstance<KtNamedFunction>() }
                    .filter { it.name == function }
                val topLevel = KotlinFunctionShortNameIndex[function, project, scope]
                    .filter { it.fqName?.asString() == "$namespace.$function" }
                (members + topLevel).distinct().singleOrNull()
            }
        } catch (_: IndexNotReadyException) {
            null
        }
    }
}
