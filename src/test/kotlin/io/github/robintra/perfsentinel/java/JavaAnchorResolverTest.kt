package io.github.robintra.perfsentinel.java

import com.intellij.psi.PsiClass
import com.intellij.psi.PsiMethod
import com.intellij.testFramework.DumbModeTestUtils
import com.intellij.testFramework.fixtures.LightJavaCodeInsightFixtureTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
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

        val result = resolve(finding("com.example.OrderService", "loadItems", "SELECT * FROM orders"))

        assertInstanceOf(result, PsiMethod::class.java)
    }

    fun testDoesNotUseSqlFallbackForNonSqlFindingsOrDuringDumbMode() {
        addJpaTable("jakarta.persistence")
        addJava(
            "com/example/OrderEntity.java",
            "package com.example; @jakarta.persistence.Table(name=\"orders\") public class OrderEntity {}",
        )
        assertNull(resolve(finding(null, null, "SELECT * FROM orders", type = "slow_http")))

        val result = DumbModeTestUtils.computeInDumbModeSynchronously(project) {
            runBlocking { JavaAnchorResolver().resolve(project, finding(null, null, "SELECT * FROM orders")) }
        }
        assertNull(result)
    }

    private fun resolve(namespace: String, function: String) = runBlocking {
        JavaAnchorResolver().resolve(project, finding(namespace, function))
    }

    private fun resolve(finding: Finding) = runBlocking { JavaAnchorResolver().resolve(project, finding) }

    private fun resolveSql(sql: String) = resolve(finding(null, null, sql))

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

    private fun finding(
        namespace: String?,
        function: String?,
        sql: String = "SELECT 1",
        type: String = "n_plus_one_sql",
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
        codeLocation = namespace?.let { CodeLocation(function, null, null, it) },
        signature = "n_plus_one_sql:order",
    )
}
