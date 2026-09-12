package io.github.robintra.perfsentinel.kotlin

import com.intellij.openapi.application.runReadAction
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking
import org.jetbrains.kotlin.psi.KtNamedFunction

class KotlinAnchorResolverTest : BasePlatformTestCase() {
    // BasePlatformTestCase runs test bodies on the EDT; runBlocking would park it while it holds
    // the write-intent lock, so the readAction inside resolve() is never granted -> deadlock. Off
    // the EDT there is no implicit read access either, hence runReadAction around every PSI read.
    override fun runInDispatchThread() = false

    fun testResolvesClassMethod() {
        myFixture.configureByText(
            "OrderService.kt",
            """
            package com.example
            class OrderService {
                fun loadItems() = Unit
            }
            """.trimIndent(),
        )

        val result = runBlocking {
            KotlinAnchorResolver().resolve(project, finding("com.example.OrderService", "loadItems"))
        }

        assertInstanceOf(result, KtNamedFunction::class.java)
        assertEquals("loadItems", runReadAction { (result as KtNamedFunction).name })
    }

    fun testResolvesTopLevelFunction() {
        myFixture.configureByText("Orders.kt", "package com.example\nfun loadItems() = Unit")

        val result = runBlocking {
            KotlinAnchorResolver().resolve(project, finding("com.example", "loadItems()"))
        }

        assertEquals("loadItems", runReadAction { (result as KtNamedFunction).name })
    }

    fun testRejectsAmbiguousOverloads() {
        myFixture.configureByText(
            "OrderService.kt",
            """
            package com.example
            class OrderService {
                fun loadItems() = Unit
                fun loadItems(id: Int) = id
            }
            """.trimIndent(),
        )

        assertNull(
            runBlocking {
                KotlinAnchorResolver().resolve(project, finding("com.example.OrderService", "loadItems"))
            },
        )
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(
            runBlocking {
                KotlinAnchorResolver().resolve(project, finding("com.example.Missing", "loadItems"))
            },
        )
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
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:order",
    )
}
