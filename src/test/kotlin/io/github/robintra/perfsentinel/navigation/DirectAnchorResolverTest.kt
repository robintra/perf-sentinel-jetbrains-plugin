package io.github.robintra.perfsentinel.navigation

import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import io.github.robintra.perfsentinel.core.CodeLocation
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.FindingPattern
import java.nio.file.Files
import java.nio.file.Paths
import kotlinx.coroutines.runBlocking

class DirectAnchorResolverTest : BasePlatformTestCase() {
    fun testNavigatesToAnExistingInProjectLine() {
        val file = Paths.get(project.basePath!!).resolve("service-invalid.rb")
        Files.createDirectories(file.parent)
        Files.writeString(file, "first\nsecond\nthird")

        val result = runBlocking { DirectAnchorResolver.resolve(project, finding(file.toString(), 2)) }

        assertInstanceOf(result, OpenFileDescriptor::class.java)
        assertTrue(result!!.canNavigate())
    }

    fun testRejectsALineOutsideTheFile() {
        val file = Paths.get(project.basePath!!).resolve("service.rb")
        Files.createDirectories(file.parent)
        Files.writeString(file, "one line")

        assertNull(runBlocking { DirectAnchorResolver.resolve(project, finding(file.toString(), 4)) })
    }

    private fun finding(filepath: String, lineNumber: Int) = Finding(
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
        codeLocation = CodeLocation(null, filepath, lineNumber, null),
        signature = "slow_http:service",
    )
}
