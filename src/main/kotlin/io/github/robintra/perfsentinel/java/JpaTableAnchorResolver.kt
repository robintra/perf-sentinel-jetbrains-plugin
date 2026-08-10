package io.github.robintra.perfsentinel.java

import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.pom.Navigatable
import com.intellij.psi.JavaPsiFacade
import com.intellij.psi.PsiAnnotation
import com.intellij.psi.PsiClass
import com.intellij.psi.PsiClassType
import com.intellij.psi.PsiReferenceExpression
import com.intellij.psi.PsiSubstitutor
import com.intellij.psi.PsiVariable
import com.intellij.psi.search.GlobalSearchScope
import com.intellij.psi.search.searches.AnnotatedElementsSearch
import com.intellij.psi.search.searches.ClassInheritorsSearch
import com.intellij.psi.util.TypeConversionUtil
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.SqlIdentifier
import io.github.robintra.perfsentinel.core.SqlTableExtractor
import io.github.robintra.perfsentinel.core.SqlTableReference

internal object JpaTableAnchorResolver {
    private const val SPRING_REPOSITORY = "org.springframework.data.repository.Repository"
    private val tableAnnotations = listOf("jakarta.persistence.Table", "javax.persistence.Table")

    fun resolve(project: Project, finding: Finding): Navigatable? {
        if (!finding.type.endsWith("_sql")) return null
        val facade = JavaPsiFacade.getInstance(project)
        if (!hasJavaProvenance(project, facade, finding)) return null
        val table = SqlTableExtractor.extract(finding.pattern.template) ?: return null
        val fileIndex = ProjectFileIndex.getInstance(project)
        // Project sources first: the common case answers without enumerating every annotated class in
        // every library jar, twice.
        val projectEntities = facade.entitiesMatching(table, GlobalSearchScope.projectScope(project))
            .filter { it.containingFile?.virtualFile?.let(fileIndex::isInSourceContent) == true }
        if (projectEntities.size > 1) return null
        projectEntities.singleOrNull()?.let { return it }
        val libraryEntity = facade.entitiesMatching(table, GlobalSearchScope.allScope(project))
            .filter { it.containingFile?.virtualFile?.let(fileIndex::isInLibraryClasses) == true }
            .singleOrNull()
            ?: return null
        return resolveRepository(project, facade, libraryEntity)
    }

    /**
     * A finding carries no language, so provenance is read from what it does report.
     *
     * Negative evidence wins: a reported path that is not `.java` means another runtime whatever
     * the namespace resolves to, otherwise a SQL finding from a Node or Kotlin service lands in an
     * unrelated Java entity. A namespace without a path must resolve to a Java class in project
     * sources; `allScope` would let a shaded library class or a Kotlin light class grant the pass.
     * A finding with neither is the case this fallback exists for -- the instrumentation agent
     * emits no `code.*` attributes -- so it stays eligible, as it was before the gate existed.
     */
    private fun hasJavaProvenance(project: Project, facade: JavaPsiFacade, finding: Finding): Boolean {
        val location = finding.codeLocation ?: return true
        val filepath = location.filepath?.takeIf { it.isNotBlank() }
        if (filepath != null) return filepath.endsWith(".java", ignoreCase = true)
        val namespace = location.namespace?.takeIf { it.isNotBlank() } ?: return true
        return facade.findClass(namespace, GlobalSearchScope.projectScope(project))?.language?.id == "JAVA"
    }

    private fun JavaPsiFacade.entitiesMatching(table: SqlTableReference, scope: GlobalSearchScope): List<PsiClass> =
        tableAnnotations.asSequence()
            .mapNotNull { findClass(it, scope) }
            .flatMap { AnnotatedElementsSearch.searchPsiClasses(it, scope).findAll().asSequence() }
            .filter { it.matches(table, this) }
            .distinctBy { it.location() }
            .toList()

    private fun resolveRepository(
        project: Project,
        facade: JavaPsiFacade,
        entity: PsiClass,
    ): PsiClass? {
        val repositoryBase = facade.findClass(SPRING_REPOSITORY, GlobalSearchScope.allScope(project)) ?: return null
        val entityParameter = repositoryBase.typeParameters.firstOrNull() ?: return null
        return ClassInheritorsSearch.search(repositoryBase, GlobalSearchScope.projectScope(project), true)
            .findAll()
            .asSequence()
            .mapNotNull { repository ->
                val substitutor = TypeConversionUtil.getClassSubstitutor(
                    repositoryBase,
                    repository,
                    PsiSubstitutor.EMPTY,
                ) ?: return@mapNotNull null
                val entityType = substitutor.substitute(entityParameter) as? PsiClassType
                    ?: return@mapNotNull null
                val resolvedEntity = entityType.resolve() ?: return@mapNotNull null
                repository.takeIf { entity.manager.areElementsEquivalent(entity, resolvedEntity) }
            }
            .distinctBy { it.location() }
            .singleOrNull()
    }

    private fun PsiClass.matches(table: SqlTableReference, facade: JavaPsiFacade): Boolean {
        val annotation = tableAnnotations.firstNotNullOfOrNull { modifierList?.findAnnotation(it) } ?: return false
        val name = annotation.stringValue("name", facade)?.toSqlIdentifier() ?: return false
        if (!table.table.matches(name)) return false
        val expectedSchema = table.schema ?: return true
        // An entity that declares no schema takes it from configuration (hibernate.default_schema,
        // search_path), so it stays a candidate for schema-qualified SQL. Two entities matching the
        // bare name then read as ambiguous and refuse navigation, which is the safe outcome.
        val schema = annotation.stringValue("schema", facade)?.toSqlIdentifier() ?: return true
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
        // Only ANSI double quotes make an identifier case-sensitive; MySQL backticks and T-SQL
        // brackets are pure delimiters, so they must still compare case-insensitively.
        if (value.length >= 2 && value.first() == '"' && value.last() == '"') {
            return SqlIdentifier(value.substring(1, value.lastIndex), quoted = true)
        }
        val delimited = when {
            value.length >= 2 && value.first() == '`' && value.last() == '`' -> value.substring(1, value.lastIndex)
            value.length >= 2 && value.first() == '[' && value.last() == ']' -> value.substring(1, value.lastIndex)
            else -> value
        }
        return SqlIdentifier(delimited, quoted = false)
    }

    private fun SqlIdentifier.matches(other: SqlIdentifier): Boolean =
        if (quoted || other.quoted) value == other.value else value.equals(other.value, ignoreCase = true)

    private fun PsiClass.location(): Pair<String, Int> =
        (containingFile?.virtualFile?.path ?: "") to textOffset
}
