package io.github.robintra.perfsentinel.java

import com.intellij.openapi.Disposable
import com.intellij.openapi.util.Disposer
import com.intellij.psi.PsiClass
import com.intellij.psi.PsiMethod
import com.intellij.testFramework.DumbModeTestUtils
import com.intellij.testFramework.PsiTestUtil
import com.intellij.testFramework.fixtures.LightJavaCodeInsightFixtureTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import io.github.robintra.perfsentinel.navigation.AnchorNavigator
import java.io.File
import java.nio.file.Files
import java.nio.file.Path
import java.util.jar.JarEntry
import java.util.jar.JarOutputStream
import kotlinx.coroutines.runBlocking

class JavaAnchorResolverTest : LightJavaCodeInsightFixtureTestCase() {
    fun testResolvesNamespaceAndFunctionToJavaMethod() {
        myFixture.configureByText(
            "OrderService.java",
            """
            package com.example;
            class OrderService {
                void loadItems() {}
            }
            """.trimIndent(),
        )

        val result = resolve("com.example.OrderService", "loadItems")

        assertInstanceOf(result, PsiMethod::class.java)
        assertEquals("loadItems", (result as PsiMethod).name)
    }

    fun testRejectsAmbiguousOverloads() {
        myFixture.configureByText(
            "OrderService.java",
            """
            package com.example;
            class OrderService {
                void loadItems() {}
                void loadItems(int page) {}
            }
            """.trimIndent(),
        )

        assertNull(resolve("com.example.OrderService", "loadItems"))
    }

    fun testResolvesAnOverrideWithoutReadingTheBaseMethodAsAmbiguity() {
        myFixture.configureByText(
            "Overrider.java",
            """
            package com.example;
            class OverriddenBase {
                void loadItems() {}
            }
            class Overrider extends OverriddenBase {
                @Override
                void loadItems() {}
            }
            """.trimIndent(),
        )

        val result = resolve("com.example.Overrider", "loadItems")

        assertEquals("Overrider", (result as PsiMethod).containingClass?.name)
    }

    fun testResolvesAnInheritedMethodThroughTheBaseClass() {
        myFixture.configureByText(
            "Inheritor.java",
            """
            package com.example;
            class InheritedBase {
                void loadItems() {}
            }
            class Inheritor extends InheritedBase {}
            """.trimIndent(),
        )

        val result = resolve("com.example.Inheritor", "loadItems")

        assertEquals("InheritedBase", (result as PsiMethod).containingClass?.name)
    }

    fun testReturnsNullWhenTheJavaSymbolDoesNotExist() {
        assertNull(resolve("com.example.Missing", "missingMethod"))
    }

    fun testResolvesSqlTableToUniqueJakartaEntity() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            """
            package com.example;
            @jakarta.persistence.Table(name = "orders")
            public class OrderEntity {}
            """.trimIndent(),
        )

        val result = resolveSql("SELECT * FROM orders")

        assertInstanceOf(result, PsiClass::class.java)
        assertEquals("com.example.OrderEntity", (result as PsiClass).qualifiedName)
    }

    fun testRejectsJpaFallbackForANonJavaFilepathEvenWhenTheNamespaceResolves() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        addJava("com/example/OrderService.java", "package com.example; public class OrderService {}")

        // The reported path is the strongest signal: a resolvable namespace must not rescue it,
        // or a Kotlin or Node service jumps into an unrelated Java entity.
        assertNull(
            resolveFallback(
                finding(
                    "com.example.OrderService",
                    null,
                    "SELECT * FROM orders",
                    filepath = "src/main/kotlin/com/example/OrderService.kt",
                ),
            ),
        )
    }

    fun testRejectsJpaFallbackWhenTheOnlyNamespaceDoesNotResolveToJava() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )

        assertNull(resolveFallback(finding("app.services.orders", null, "SELECT * FROM orders")))
    }

    fun testAcceptsJpaFallbackWhenTheFindingCarriesNoCodeLocation() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )

        // Spans with no code.* attributes are exactly what this fallback exists for.
        assertInstanceOf(
            resolveFallback(finding(null, null, "SELECT * FROM orders")),
            PsiClass::class.java,
        )
    }

    fun testAcceptsSchemaQualifiedSqlForAnEntityInheritingItsSchemaFromConfiguration() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )

        // hibernate.default_schema and search_path live outside the annotation.
        assertInstanceOf(
            resolveSql("SELECT * FROM audit.orders"),
            PsiClass::class.java,
        )
    }

    fun testRefusesSchemaQualifiedSqlWhenTwoSchemaLessEntitiesShareTheBareName() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        addJava(
            "com/example/LegacyOrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class LegacyOrderEntity {}",
        )

        assertNull(resolveSql("SELECT * FROM audit.orders"))
    }

    fun testAcceptsJpaFallbackWhenNamespaceResolvesToJava() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        addJava("com/example/OrderService.java", "package com.example; public class OrderService {}")

        assertInstanceOf(
            resolveFallback(finding("com.example.OrderService", null, "SELECT * FROM orders")),
            PsiClass::class.java,
        )
    }

    fun testSupportsJavaxConstantNamesAndExactSchemas() {
        addJpaTable("javax.persistence")
        addJava(
            "com/example/Names.java",
            "package com.example; public final class Names { public static final String TABLE = \"orders\"; }",
        )
        addJava(
            "com/example/OrderEntity.java",
            """
            package com.example;
            @javax.persistence.Table(name = com.example.Names.TABLE, schema = "sales")
            public class OrderEntity {}
            """.trimIndent(),
        )

        assertEquals("OrderEntity", (resolveSql("SELECT * FROM orders") as PsiClass).name)
        assertEquals("OrderEntity", (resolveSql("SELECT * FROM sales.orders") as PsiClass).name)
        assertNull(resolveSql("SELECT * FROM audit.orders"))
    }

    fun testRejectsDuplicateEntitiesAndForeignTableAnnotations() {
        addJpaTable("jakarta.persistence")
        addJava("other/Table.java", "package other; public @interface Table { String name(); }")
        addJava("a/A.java", "package a; @jakarta.persistence.Table(name=\"orders\") public class A {}")
        addJava("b/B.java", "package b; @jakarta.persistence.Table(name=\"orders\") public class B {}")
        addJava("c/C.java", "package c; @other.Table(name=\"customers\") public class C {}")

        assertNull(resolveSql("SELECT * FROM orders"))
        assertNull(resolveSql("SELECT * FROM customers"))
    }

    fun testKeepsMethodResolutionAheadOfJpaFallback() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        addJava(
            "com/example/OrderService.java",
            "package com.example; public class OrderService { public void loadItems() {} }",
        )

        // Through the dispatcher: calling the resolver directly never reaches resolveFallback, so the
        // ordering this test is named for would hold no matter how the two tiers were wired.
        val result = navigate(finding("com.example.OrderService", "loadItems", "SELECT * FROM orders"))

        assertInstanceOf(result, PsiMethod::class.java)
    }

    fun testRefusesTheJpaGuessWhenTheMethodItselfIsAmbiguous() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        addJava(
            "com/example/OrderService.java",
            """
            package com.example;
            public class OrderService {
                public void loadItems() {}
                public void loadItems(int page) {}
            }
            """.trimIndent(),
        )

        assertNull(navigate(finding("com.example.OrderService", "loadItems", "SELECT * FROM orders")))
    }

    fun testDoesNotUseSqlFallbackForNonSqlFindingsOrDuringDumbMode() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        // Java provenance on purpose: this pins the type and dumb-mode guards, not the gate.
        val java = "src/main/java/com/example/OrderService.java"
        assertNull(
            resolveFallback(finding(null, null, "SELECT * FROM orders", type = "slow_http", filepath = java)),
        )

        val result = DumbModeTestUtils.computeInDumbModeSynchronously(project) {
            runBlocking {
                JavaAnchorResolver().resolveFallback(
                    project,
                    finding(null, null, "SELECT * FROM orders", filepath = java),
                )
            }
        }
        assertNull(result)
    }

    fun testFallsBackToUniqueRepositoryForAnExternalEntity() {
        addExternalJpaLibrary()
        addJava(
            "com/example/OrderRepository.java",
            """
            package com.example;
            public interface OrderRepository
                extends org.springframework.data.jpa.repository.JpaRepository<external.ExternalOrder, Long> {}
            """.trimIndent(),
        )

        val result = resolveSql("SELECT * FROM orders")

        assertInstanceOf(result, PsiClass::class.java)
        assertEquals("com.example.OrderRepository", (result as PsiClass).qualifiedName)
    }

    fun testRejectsAmbiguousExternalEntitiesEvenWithOneRepository() {
        addExternalJpaLibrary(
            "external/OtherOrder.java" to
                "package external; @jakarta.persistence.Table(name=\"orders\") public class OtherOrder {}",
        )
        addJava(
            "com/example/OrderRepository.java",
            "package com.example; public interface OrderRepository extends org.springframework.data.jpa.repository.JpaRepository<external.ExternalOrder, Long> {}",
        )

        assertNull(resolveSql("SELECT * FROM orders"))
    }

    fun testRejectsRawOrMultipleRepositories() {
        addExternalJpaLibrary()
        addJava(
            "com/example/RawRepository.java",
            "package com.example; public interface RawRepository extends org.springframework.data.repository.Repository {}",
        )
        assertNull(resolveSql("SELECT * FROM orders"))

        addJava(
            "com/example/First.java",
            "package com.example; public interface First extends org.springframework.data.jpa.repository.JpaRepository<external.ExternalOrder, Long> {}",
        )
        addJava(
            "com/example/Second.java",
            "package com.example; public interface Second extends org.springframework.data.jpa.repository.JpaRepository<external.ExternalOrder, Long> {}",
        )
        assertNull(resolveSql("SELECT * FROM orders"))
    }

    private fun resolve(namespace: String, function: String) = runBlocking {
        JavaAnchorResolver().resolve(project, finding(namespace, function))
    }

    private fun resolve(finding: Finding) = runBlocking { JavaAnchorResolver().resolve(project, finding) }

    private fun navigate(finding: Finding) = runBlocking {
        AnchorNavigator.resolve(project, finding, listOf(JavaAnchorResolver()))
    }

    private fun resolveSql(sql: String) = resolveFallback(
        finding(null, null, sql, filepath = "src/main/java/com/example/OrderService.java"),
    )

    private fun resolveFallback(finding: Finding) =
        runBlocking { JavaAnchorResolver().resolveFallback(project, finding) }

    private fun addJpaTable(packageName: String) {
        addJava(
            "${packageName.replace('.', '/')}/Table.java",
            "package $packageName; public @interface Table { String name(); String schema() default \"\"; }",
        )
    }

    private fun addJava(path: String, source: String) {
        val file = myFixture.addFileToProject(path, source)
        myFixture.configureFromExistingVirtualFile(file.virtualFile)
    }

    private fun addExternalJpaLibrary(vararg extraSources: Pair<String, String>) {
        val root = Files.createTempDirectory("perf-sentinel-external-jpa")
        val sourceRoot = Files.createDirectories(root.resolve("src"))
        val classes = Files.createDirectories(root.resolve("classes"))
        val sources = (mapOf(
            "jakarta/persistence/Table.java" to
                "package jakarta.persistence; public @interface Table { String name(); String schema() default \"\"; }",
            "external/ExternalOrder.java" to
                "package external; @jakarta.persistence.Table(name=\"orders\") public class ExternalOrder {}",
            "org/springframework/data/repository/Repository.java" to
                "package org.springframework.data.repository; public interface Repository<T, ID> {}",
            "org/springframework/data/jpa/repository/JpaRepository.java" to
                "package org.springframework.data.jpa.repository; public interface JpaRepository<T, ID> extends org.springframework.data.repository.Repository<T, ID> {}",
        ) + extraSources).map { (relative, content) ->
            sourceRoot.resolve(relative).also { file ->
                Files.createDirectories(file.parent)
                Files.writeString(file, content)
            }
        }
        val javac = listOfNotNull(System.getenv("JAVA_HOME"), System.getProperty("java.home"))
            .map { Path.of(it, "bin", "javac") }
            .firstOrNull(Files::isExecutable)
            ?: error("No javac executable is available for the library fixture")
        val compiler = ProcessBuilder(listOf(javac.toString(), "-d", classes.toString()) + sources.map(Path::toString))
            .redirectErrorStream(true)
            .start()
        val compilerOutput = compiler.inputStream.bufferedReader().readText()
        assertEquals(compilerOutput, 0, compiler.waitFor())
        val jar = root.resolve("external-jpa.jar")
        JarOutputStream(Files.newOutputStream(jar)).use { output ->
            Files.walk(classes).use { files ->
                files.filter(Files::isRegularFile).forEach { file ->
                    output.putNextEntry(JarEntry(classes.relativize(file).toString().replace(File.separatorChar, '/')))
                    Files.copy(file, output)
                    output.closeEntry()
                }
            }
        }
        Disposer.register(testRootDisposable, Disposable { root.toFile().deleteRecursively() })
        PsiTestUtil.addLibrary(
            testRootDisposable,
            module,
            "external-jpa",
            jar.parent.toString(),
            jar.fileName.toString(),
        )
    }

    private fun finding(
        namespace: String?,
        function: String?,
        sql: String = "SELECT 1",
        type: String = "n_plus_one_sql",
        filepath: String? = null,
    ) = Finding(
        type = type,
        severity = "warning",
        traceId = "trace",
        service = "order-service",
        grouping = emptyList(),
        sourceEndpoint = "GET /orders",
        pattern = FindingPattern(sql, 2, 10, 2),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-07T12:00:00Z",
        lastTimestamp = "2026-08-07T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = if (namespace != null || filepath != null) {
            CodeLocation(function, filepath, null, namespace)
        } else {
            null
        },
        signature = "n_plus_one_sql:order",
    )
}
