package io.github.robintra.perfsentinel.java

import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.pom.Navigatable
import com.intellij.psi.JavaPsiFacade
import com.intellij.psi.PsiAnnotation
import com.intellij.psi.PsiClass
import com.intellij.psi.PsiReferenceExpression
import com.intellij.psi.PsiVariable
import com.intellij.psi.search.GlobalSearchScope
import com.intellij.psi.search.searches.AnnotatedElementsSearch
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.SqlIdentifier
import io.github.robintra.perfsentinel.core.SqlTableExtractor
import io.github.robintra.perfsentinel.core.SqlTableReference

internal object JpaTableAnchorResolver {
    private val tableAnnotations = listOf("jakarta.persistence.Table", "javax.persistence.Table")

    fun resolve(project: Project, finding: Finding): Navigatable? {
        if (!finding.type.endsWith("_sql")) return null
        val table = SqlTableExtractor.extract(finding.pattern.template) ?: return null
        val facade = JavaPsiFacade.getInstance(project)
        val scope = GlobalSearchScope.allScope(project)
        val fileIndex = ProjectFileIndex.getInstance(project)
        return tableAnnotations.asSequence()
            .mapNotNull { facade.findClass(it, scope) }
            .flatMap { AnnotatedElementsSearch.searchPsiClasses(it, scope).findAll().asSequence() }
            .filter { it.matches(table, facade) }
            .distinctBy { it.location() }
            .filter { it.containingFile?.virtualFile?.let(fileIndex::isInSourceContent) == true }
            .singleOrNull()
    }

    private fun PsiClass.matches(table: SqlTableReference, facade: JavaPsiFacade): Boolean {
        val annotation = tableAnnotations.firstNotNullOfOrNull { modifierList?.findAnnotation(it) } ?: return false
        val name = annotation.stringValue("name", facade)?.toSqlIdentifier() ?: return false
        if (!table.table.matches(name)) return false
        val expectedSchema = table.schema ?: return true
        val schema = annotation.stringValue("schema", facade)?.toSqlIdentifier() ?: return false
        return expectedSchema.matches(schema)
    }

    private fun PsiAnnotation.stringValue(attribute: String, facade: JavaPsiFacade): String? =
        findAttributeValue(attribute)
            ?.let { expression ->
                facade.constantEvaluationHelper.computeConstantExpression(expression)
                    ?: ((expression as? PsiReferenceExpression)?.resolve() as? PsiVariable)?.let { variable ->
                        variable.computeConstantValue()
                            ?: variable.initializer?.let(facade.constantEvaluationHelper::computeConstantExpression)
                    }
            }
            ?.let { it as? String }

    private fun String.toSqlIdentifier(): SqlIdentifier? {
        val value = trim()
        if (value.isEmpty()) return null
        val quoted = when {
            value.length >= 2 && value.first() == '"' && value.last() == '"' -> value.substring(1, value.lastIndex)
            value.length >= 2 && value.first() == '`' && value.last() == '`' -> value.substring(1, value.lastIndex)
            value.length >= 2 && value.first() == '[' && value.last() == ']' -> value.substring(1, value.lastIndex)
            else -> null
        }
        return SqlIdentifier(quoted ?: value, quoted != null)
    }

    private fun SqlIdentifier.matches(other: SqlIdentifier): Boolean =
        if (quoted || other.quoted) value == other.value else value.equals(other.value, ignoreCase = true)

    private fun PsiClass.location(): Pair<String, Int> =
        (containingFile?.virtualFile?.path ?: "") to textOffset
}
