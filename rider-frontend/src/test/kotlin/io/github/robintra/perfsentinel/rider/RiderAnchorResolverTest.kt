package io.github.robintra.perfsentinel.rider

import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.editor.LazyRangeMarkerFactory
import com.intellij.openapi.editor.RangeMarker
import com.intellij.openapi.project.Project
import com.intellij.testFramework.LightVirtualFile
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import io.github.robintra.perfsentinel.rider.model.SourceAnchor
import junit.framework.TestCase
import kotlinx.coroutines.runBlocking
import java.lang.reflect.Proxy

class RiderAnchorResolverTest : TestCase() {
    private val rangeMarkerFactory = object : LazyRangeMarkerFactory() {
        override fun createRangeMarker(file: com.intellij.openapi.vfs.VirtualFile, offset: Int) = marker(offset)

        override fun createRangeMarker(
            file: com.intellij.openapi.vfs.VirtualFile,
            offset: Int,
            persistentOffset: Int,
            surviveOnExternalChange: Boolean,
        ) = marker(offset)

        private fun marker(offset: Int) = Proxy.newProxyInstance(
            RangeMarker::class.java.classLoader,
            arrayOf(RangeMarker::class.java),
        ) { proxy, method, args ->
            when (method.name) {
                "getStartOffset", "getEndOffset" -> offset
                "isValid" -> true
                "dispose" -> Unit
                "equals" -> proxy === args?.firstOrNull()
                "hashCode" -> System.identityHashCode(proxy)
                else -> when (method.returnType) {
                    Boolean::class.javaPrimitiveType -> false
                    Int::class.javaPrimitiveType -> 0
                    else -> null
                }
            }
        } as RangeMarker
    }

    private val project = Proxy.newProxyInstance(
        Project::class.java.classLoader,
        arrayOf(Project::class.java),
    ) { proxy, method, args ->
        when (method.name) {
            "isDisposed" -> false
            "getName" -> "test"
            "getService" -> rangeMarkerFactory.takeIf { args?.firstOrNull() == LazyRangeMarkerFactory::class.java }
            "equals" -> proxy === args?.firstOrNull()
            "hashCode" -> System.identityHashCode(proxy)
            "toString" -> "TestProject"
            else -> throw UnsupportedOperationException(method.name)
        }
    } as Project

    fun testConvertsBackendAnchorToOpenFileDescriptor() {
        val file = sourceFile("class Orders { void Load() {} }")

        val result = resolve(file) { _, _, _ -> SourceAnchor(file.path, 15) } as OpenFileDescriptor

        assertEquals(file, result.file)
        assertEquals(15, result.offset)
    }

    fun testPassesQualifiedFunctionUnchanged() {
        val file = sourceFile("class Orders {}")
        var receivedNamespace: String? = null
        var receivedFunction: String? = null

        resolve(file, "Shop.Orders", "Shop.Orders.LoadAsync(System.String)") { _, namespace, function ->
            receivedNamespace = namespace
            receivedFunction = function
            SourceAnchor(file.path, 0)
        }

        assertEquals("Shop.Orders", receivedNamespace)
        assertEquals("Shop.Orders.LoadAsync(System.String)", receivedFunction)
    }

    fun testReturnsNullForMissingNamespaceOrFunction() {
        var calls = 0
        val lookup: CSharpLookup = { _, _, _ ->
            calls++
            null
        }

        assertNull(resolve(namespace = null, function = "Load()", lookup = lookup))
        assertNull(resolve(namespace = "Shop.Orders", function = null, lookup = lookup))
        assertNull(resolve(namespace = " ", function = "Load()", lookup = lookup))
        assertNull(resolve(namespace = "Shop.Orders", function = " ", lookup = lookup))
        assertEquals(0, calls)
    }

    fun testReturnsNullForMissingFileOrInvalidOffset() {
        val file = sourceFile("class Orders {}")

        assertNull(resolve { _, _, _ -> SourceAnchor("/missing/Orders.cs", 0) })
        assertNull(resolve(file) { _, _, _ -> SourceAnchor(file.path, -1) })
        assertNull(resolve(file) { _, _, _ -> SourceAnchor(file.path, file.length.toInt() + 1) })
    }

    fun testReturnsNullWhenBackendDoesNotResolveSymbol() {
        assertNull(resolve { _, _, _ -> null })
    }

    private fun resolve(
        file: LightVirtualFile? = null,
        namespace: String? = "Shop.Orders",
        function: String? = "Load()",
        lookup: CSharpLookup,
    ) = runBlocking {
        RiderAnchorResolver(lookup) { path -> file?.takeIf { it.path == path } }
            .resolve(project, finding(namespace, function))
    }

    private fun finding(namespace: String?, function: String?) = Finding(
        type = "n_plus_one_sql",
        severity = "warning",
        traceId = "trace",
        service = "dotnet-svc",
        grouping = emptyList(),
        sourceEndpoint = "POST /api/fault/n-plus-one-sql",
        pattern = FindingPattern("SELECT 1", 8, 1000, 8),
        suggestion = "Batch the lookup",
        firstTimestamp = "2026-08-09T12:00:00Z",
        lastTimestamp = "2026-08-09T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, null, null, namespace),
        signature = "n_plus_one_sql:dotnet-svc",
    )

    private fun sourceFile(content: String) = object : LightVirtualFile("Orders.cs", content) {
        override fun getLength() = content.length.toLong()
    }
}
