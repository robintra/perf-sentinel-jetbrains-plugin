package io.github.robintra.perfsentinel.rust

import com.intellij.psi.PsiElement
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking

class RustAnchorResolverTest : BasePlatformTestCase() {
    fun testResolvesFreeFunction() {
        myFixture.configureByText(
            "lib.rs",
            "mod orders { pub fn load_items() -> Vec<i32> { vec![] } }",
        )

        val result = runBlocking {
            RustAnchorResolver().resolve(project, finding("orders", "load_items"))
        }

        assertTrue((result as PsiElement).text.startsWith("pub fn load_items"))
    }

    fun testResolvesAssociatedFunction() {
        myFixture.configureByText(
            "lib.rs",
            """
            mod orders {
                pub struct OrderService;
                impl OrderService {
                    pub fn load_items() -> Vec<i32> { vec![] }
                }
            }
            """.trimIndent(),
        )

        val result = runBlocking {
            RustAnchorResolver().resolve(project, finding("orders::OrderService", "load_items()"))
        }

        assertTrue((result as PsiElement).text.startsWith("pub fn load_items"))
    }

    fun testRejectsDuplicateDeclarations() {
        myFixture.configureByText("one.rs", "mod orders { pub fn load_items() {} }")
        assertNotNull(
            runBlocking {
                RustAnchorResolver().resolve(project, finding("orders", "load_items"))
            },
        )
        myFixture.configureByText("two.rs", "mod orders { pub fn load_items() {} }")

        assertNull(
            runBlocking {
                RustAnchorResolver().resolve(project, finding("orders", "load_items"))
            },
        )
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(
            runBlocking {
                RustAnchorResolver().resolve(project, finding("orders", "missing"))
            },
        )
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "diesel-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:diesel-svc",
    )
}
