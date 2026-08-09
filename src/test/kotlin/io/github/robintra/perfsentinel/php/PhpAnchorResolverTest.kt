package io.github.robintra.perfsentinel.php

import com.intellij.psi.PsiElement
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking

class PhpAnchorResolverTest : BasePlatformTestCase() {
    fun testResolvesClassMethod() {
        myFixture.configureByText(
            "OrderService.php",
            """
            <?php
            namespace App\Service;

            class OrderService {
                public function loadItems(): array { return []; }
            }
            """.trimIndent(),
        )

        val result = runBlocking {
            PhpAnchorResolver().resolve(project, finding("App\\Service\\OrderService", "loadItems"))
        }

        assertTrue((result as PsiElement).text.startsWith("public function loadItems"))
    }

    fun testResolvesNamespacedFunction() {
        myFixture.configureByText(
            "functions.php",
            "<?php\nnamespace App\\Service;\nfunction loadItems(): array { return []; }",
        )

        val result = runBlocking {
            PhpAnchorResolver().resolve(project, finding("App\\Service", "loadItems()"))
        }

        assertTrue((result as PsiElement).text.startsWith("function loadItems"))
    }

    fun testRejectsDuplicateDeclarations() {
        myFixture.configureByText("one.php", "<?php\nnamespace App;\nfunction loadItems() {}")
        myFixture.configureByText("two.php", "<?php\nnamespace App;\nfunction loadItems() {}")

        assertNull(
            runBlocking {
                PhpAnchorResolver().resolve(project, finding("App", "loadItems"))
            },
        )
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(
            runBlocking {
                PhpAnchorResolver().resolve(project, finding("App", "missing"))
            },
        )
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "laravel-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:laravel-svc",
    )
}
