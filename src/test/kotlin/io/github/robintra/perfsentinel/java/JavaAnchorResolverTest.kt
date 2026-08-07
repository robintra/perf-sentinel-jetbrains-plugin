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

        val result = runBlocking {
            JavaAnchorResolver().resolve(project, finding("com.example.OrderService", "loadItems"))
        }

        assertInstanceOf(result, PsiMethod::class.java)
        assertEquals("loadItems", (result as PsiMethod).name)
    }

    fun testReturnsNullWhenTheJavaSymbolDoesNotExist() {
        assertNull(runBlocking { JavaAnchorResolver().resolve(project, finding("com.example.Missing", "missingMethod")) })
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
