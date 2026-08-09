package io.github.robintra.perfsentinel.navigation

import com.intellij.ide.actions.QualifiedNameProviderUtil
import com.intellij.navigation.ChooseByNameRegistry
import com.intellij.navigation.GotoClassContributor
import com.intellij.openapi.application.readAction
import com.intellij.openapi.project.IndexNotReadyException
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.pom.Navigatable
import com.intellij.psi.PsiElement
import io.github.robintra.perfsentinel.core.Finding

object PlatformSymbolAnchorResolver {
    suspend fun resolve(
        project: Project,
        finding: Finding,
        languageIds: Set<String>,
        qualifiedNameFallback: (PsiElement) -> String? = { null },
    ): Navigatable? {
        val namespace = finding.codeLocation?.namespace?.trim()?.takeIf(String::isNotEmpty) ?: return null
        val function = finding.symbolName() ?: return null
        val expectedName = normalizeQualifiedName("$namespace.$function")
        return try {
            readAction {
                val fileIndex = ProjectFileIndex.getInstance(project)
                   ChooseByNameRegistry.getInstance().symbolModelContributors
                       .asSequence()
                       .flatMap { contributor ->
                           contributor.getItemsByName(function, function, project, false)
                               .asSequence()
                               .map { contributor to it }
                       }
                       .mapNotNull { (contributor, item) ->
                        val element = (item as? PsiElement)?.navigationElement ?: return@mapNotNull null
                        val file = element.containingFile?.virtualFile ?: return@mapNotNull null
                        if (!fileIndex.isInContent(file) || !element.matchesLanguage(languageIds)) return@mapNotNull null
                           val qualifiedNames = sequenceOf(
                               (contributor as? GotoClassContributor)?.getQualifiedName(item),
                               QualifiedNameProviderUtil.getQualifiedName(element),
                               qualifiedNameFallback(element),
                           )
                           if (qualifiedNames.filterNotNull().none { normalizeQualifiedName(it) == expectedName }) {
                               return@mapNotNull null
                           }
                        Triple(file.path, element.textOffset, item)
                    }
                    .distinctBy { (path, offset) -> path to offset }
                    .map { it.third }
                    .singleOrNull()
            }
        } catch (_: IndexNotReadyException) {
            null
        }
    }

    private fun PsiElement.matchesLanguage(languageIds: Set<String>): Boolean =
        languageIds.any { languageId ->
            language.id.equals(languageId, ignoreCase = true) ||
                containingFile?.language?.id.equals(languageId, ignoreCase = true)
        }

    private fun normalizeQualifiedName(value: String): String = value
        .replace("::", ".")
        .replace('\\', '.')
        .replace('#', '.')
        .replace('/', '.')
        .split('.')
        .map { it.substringBefore('(').trim() }
        .filter(String::isNotEmpty)
        .joinToString(".")
}
