package io.github.robintra.perfsentinel.ruby

import com.intellij.psi.PsiElement
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking

class RubyAnchorResolverTest : BasePlatformTestCase() {
    fun testResolvesInstanceMethod() {
        myFixture.configureByText(
            "order_service.rb",
            """
            module Orders
              class OrderService
                def load_items
                  []
                end
              end
            end
            """.trimIndent(),
        )

        val result = runBlocking {
            RubyAnchorResolver().resolve(project, finding("Orders::OrderService", "load_items"))
        }

        assertTrue((result as PsiElement).text.startsWith("def load_items"))
    }

    fun testResolvesSingletonMethod() {
        myFixture.configureByText(
            "order_service.rb",
            """
            module Orders
              class OrderService
                def self.load_items
                  []
                end
              end
            end
            """.trimIndent(),
        )

        val result = runBlocking {
            RubyAnchorResolver().resolve(project, finding("Orders::OrderService", "load_items()"))
        }

        assertTrue((result as PsiElement).text.startsWith("def self.load_items"))
    }

    fun testRejectsDuplicateDeclarations() {
        myFixture.configureByText("one.rb", "module Orders; def self.load_items; []; end; end")
        myFixture.configureByText("two.rb", "module Orders; def self.load_items; []; end; end")

        assertNull(
            runBlocking {
                RubyAnchorResolver().resolve(project, finding("Orders", "load_items"))
            },
        )
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(
            runBlocking {
                RubyAnchorResolver().resolve(project, finding("Orders", "missing"))
            },
        )
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "rails-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:rails-svc",
    )
}
