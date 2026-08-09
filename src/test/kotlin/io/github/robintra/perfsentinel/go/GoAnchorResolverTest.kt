package io.github.robintra.perfsentinel.go

import com.intellij.psi.PsiElement
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking

class GoAnchorResolverTest : BasePlatformTestCase() {
    fun testResolvesPackageFunction() {
        myFixture.configureByText(
            "orders.go",
            "package orders\n\nfunc LoadItems() []int { return nil }",
        )

        val result = resolve("orders", "LoadItems")

        assertTrue((result as PsiElement).text.startsWith("func LoadItems"))
    }

    fun testResolvesReceiverMethod() {
        myFixture.configureByText(
            "orders.go",
            """
            package orders

            type OrderService struct{}

            func (service OrderService) LoadItems() []int { return nil }
            """.trimIndent(),
        )

        val result = resolve("orders.OrderService", "LoadItems()")

        assertTrue((result as PsiElement).text.startsWith("func (service OrderService) LoadItems"))
    }

    fun testRejectsDuplicateDeclarations() {
        myFixture.addFileToProject("one.go", "package orders\n\nfunc LoadItems() []int { return nil }")
        myFixture.addFileToProject("two.go", "package orders\n\nfunc LoadItems() []int { return nil }")

        assertNull(resolve("orders", "LoadItems"))
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(resolve("orders", "Missing"))
    }

    private fun resolve(namespace: String, function: String) = runBlocking {
        GoAnchorResolver().resolve(project, finding(namespace, function))
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "go-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:go-svc",
    )
}
