package io.github.robintra.perfsentinel.navigation

import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.pom.Navigatable
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import java.nio.file.Files
import java.nio.file.Paths
import kotlinx.coroutines.runBlocking

class DirectAnchorResolverTest : BasePlatformTestCase() {
    fun testNavigatesToAnExistingInProjectCSharpLine() {
        val file = Paths.get(project.basePath!!).resolve("Program.cs")
        Files.createDirectories(file.parent)
        Files.writeString(file, "first\nsecond\nthird")

        val result = runBlocking {
            DirectAnchorResolver.resolve(
                project,
                finding(file.toString(), 2, "SlowPath", "PerfSentinel.RiderSmoke.Program"),
            )
        }

        assertInstanceOf(result, OpenFileDescriptor::class.java)
        assertTrue(result!!.canNavigate())
    }

    fun testDoesNotInventATargetForASymbolOnlyCSharpFinding() {
        val result = runBlocking {
            DirectAnchorResolver.resolve(
                project,
                finding(null, null, "SlowPath", "PerfSentinel.RiderSmoke.Program"),
            )
        }

        assertNull(result)
    }

    fun testRejectsALineOutsideTheFile() {
        val file = Paths.get(project.basePath!!).resolve("service.rb")
        Files.createDirectories(file.parent)
        Files.writeString(file, "one line")

        assertNull(runBlocking { DirectAnchorResolver.resolve(project, finding(file.toString(), 4)) })
    }

    fun testUsesFallbackOnlyWhenNoSemanticResolverMatches() {
        val semantic = StubNavigatable()
        val fallback = StubNavigatable()

        assertSame(
            semantic,
            runBlocking {
                AnchorNavigator.resolve(
                    project,
                    finding(null, null),
                    listOf(StubResolver(semantic, fallback), StubResolver(null, fallback)),
                )
            },
        )
        assertSame(
            fallback,
            runBlocking {
                AnchorNavigator.resolve(project, finding(null, null), listOf(StubResolver(null, fallback)))
            },
        )
    }

    fun testDoesNotFallBackWhenSemanticResolutionIsAmbiguous() {
        val fallback = StubNavigatable()

        assertNull(
            runBlocking {
                AnchorNavigator.resolve(
                    project,
                    finding(null, null),
                    listOf(StubResolver(StubNavigatable(), fallback), StubResolver(StubNavigatable(), null)),
                )
            },
        )
    }

    private fun finding(
        filepath: String?,
        lineNumber: Int?,
        function: String? = null,
        namespace: String? = null,
    ) = Finding(
        type = "slow_http",
        severity = "warning",
        traceId = "trace",
        service = "service",
        grouping = emptyList(),
        sourceEndpoint = "GET /",
        pattern = FindingPattern("GET /downstream", 1, 10, 1),
        suggestion = "Inspect the call",
        firstTimestamp = "2026-08-07T12:00:00Z",
        lastTimestamp = "2026-08-07T12:00:01Z",
        confidence = "daemon_staging",
        codeLocation = CodeLocation(function, filepath, lineNumber, namespace),
        signature = "slow_http:service",
    )

    private class StubResolver(
        private val semantic: Navigatable?,
        private val fallback: Navigatable?,
    ) : AnchorResolver {
        override suspend fun resolve(project: com.intellij.openapi.project.Project, finding: Finding) = semantic

        override suspend fun resolveFallback(project: com.intellij.openapi.project.Project, finding: Finding) = fallback
    }

    private class StubNavigatable : Navigatable {
        override fun navigate(requestFocus: Boolean) = Unit
        override fun canNavigate() = true
        override fun canNavigateToSource() = true
    }
}
