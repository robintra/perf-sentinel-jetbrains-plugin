package io.github.robintra.perfsentinel.java

import com.intellij.psi.PsiMethod
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

    private fun resolve(namespace: String, function: String) = runBlocking {
        JavaAnchorResolver().resolve(project, finding(namespace, function))
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "order-service",
        grouping = emptyList(),
        sourceEndpoint = "GET /orders",
        pattern = FindingPattern("SELECT 1", 2, 10, 2),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-07T12:00:00Z",
        lastTimestamp = "2026-08-07T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:order",
    )
}
