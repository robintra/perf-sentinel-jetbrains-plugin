package io.github.robintra.perfsentinel.javascript

import com.intellij.psi.PsiElement
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import kotlinx.coroutines.runBlocking

class JavaScriptAnchorResolverTest : BasePlatformTestCase() {
    fun testResolvesModuleExportsFunction() = assertResolves(
        "orders.js",
        "module.exports.loadItems = function loadItems() { return []; };",
        "orders",
        "loadItems",
    )

    fun testResolvesExportsFunction() = assertResolves(
        "orders.js",
        "exports.loadItems = function loadItems() { return []; };",
        "orders",
        "loadItems",
    )

    fun testResolvesEsmNamedExport() = assertResolves(
        "orders.js",
        "export function loadItems() { return []; }",
        "orders",
        "function loadItems",
    )

    fun testResolvesTypeScriptFunction() = assertResolves(
        "orders.ts",
        "export namespace Orders { export function loadItems(): number[] { return []; } }",
        "Orders",
        "function loadItems",
    )

    fun testResolvesTypeScriptClassMethod() = assertResolves(
        "order-service.ts",
        "export class OrderService { loadItems(): number[] { return []; } }",
        "OrderService",
        "loadItems",
    )

    fun testResolvesNestControllerMethod() = assertResolves(
        "orders.controller.ts",
        """
        declare function Controller(path: string): ClassDecorator;
        declare function Get(): MethodDecorator;
        @Controller('orders')
        export class OrdersController {
          @Get()
          loadItems(): number[] { return []; }
        }
        """.trimIndent(),
        "OrdersController",
        "loadItems",
    )

    fun testRejectsDuplicateDeclarations() {
        myFixture.addFileToProject(
            "one.ts",
            "namespace Orders { export function loadItems(): number[] { return []; } }",
        )
        myFixture.addFileToProject(
            "two.ts",
            "namespace Orders { export function loadItems(): number[] { return []; } }",
        )

        assertNull(resolve("Orders", "loadItems"))
    }

    fun testReturnsNullForMissingSymbol() {
        assertNull(resolve("Orders", "missing"))
    }

    private fun assertResolves(file: String, source: String, namespace: String, expectedText: String) {
        myFixture.configureByText(file, source)

        val result = resolve(namespace, "loadItems()")

        assertTrue((result as PsiElement).text.contains(expectedText))
    }

    private fun resolve(namespace: String, function: String) = runBlocking {
        JavaScriptAnchorResolver().resolve(project, finding(namespace, function))
    }

    private fun finding(namespace: String, function: String) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "nest-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:nest-svc",
    )
}
