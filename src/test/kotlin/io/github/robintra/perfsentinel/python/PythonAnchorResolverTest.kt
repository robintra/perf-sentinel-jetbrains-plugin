package io.github.robintra.perfsentinel.python

import com.intellij.psi.PsiElement
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking

class PythonAnchorResolverTest : BasePlatformTestCase() {
    fun testResolvesClassMethod() {
        myFixture.configureByText(
            "handlers.py",
            """
            class OrderService:
                def load_items(self):
                    return []
            """.trimIndent(),
        )

        val result = runBlocking {
            PythonAnchorResolver().resolve(project, finding("handlers.OrderService", "load_items"))
        }

        assertTrue((result as PsiElement).text.startsWith("def load_items"))
    }

    fun testResolvesModuleFunction() {
        myFixture.configureByText("handlers.py", "def load_items():\n    return []")

        val result = runBlocking {
            PythonAnchorResolver().resolve(project, finding("handlers", "load_items()"))
        }

        assertTrue((result as PsiElement).text.startsWith("def load_items"))
    }

    fun testRejectsDuplicateDeclarations() {
        myFixture.configureByText(
            "handlers.py",
            """
            def load_items():
                return []

            def load_items():
                return [1]
            """.trimIndent(),
        )

        assertNull(
            runBlocking {
                PythonAnchorResolver().resolve(project, finding("handlers", "load_items"))
            },
        )
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(
            runBlocking {
                PythonAnchorResolver().resolve(project, finding("handlers", "missing"))
            },
        )
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "django-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:django-svc",
    )
}
