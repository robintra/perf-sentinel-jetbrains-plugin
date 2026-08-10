package io.github.robintra.perfsentinel.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.nio.file.Files

class FindingContractTest {
    @Test
    fun `builds the bounded read-only findings request`() {
        assertEquals(
            "http://127.0.0.1:4318/api/findings?service=order+service%2Feu&limit=1000&include_acked=true",
            findingsUri("http://127.0.0.1:4318", "order service/eu").toString(),
        )
    }

    @Test
    fun `uses override then project directory for service name`() {
        assertEquals("billing-api", resolveServiceName("fallback", "/work/order-service", " billing-api "))
        assertEquals("order-service", resolveServiceName("fallback", "/work/order-service", "  "))
        assertEquals("fallback", resolveServiceName("fallback", null, null))
    }

    @Test
    fun `tolerates explicit nulls in every optional field`() {
        val row = parseFindings("[${finding(nulls = true)}]").single()

        assertEquals("ci_batch", row.finding.confidence)
        assertEquals("", row.finding.signature)
        assertEquals(emptyList<GroupingAttribute>(), row.finding.grouping)
        assertEquals(0, row.firstSeenMs)
        assertEquals(1, row.seenCount)
        assertNull(row.finding.codeLocation)
    }

    @Test
    fun `drops only the malformed finding and keeps the rest of the endpoint`() {
        val response = parseFindings("[${finding()}, {\"finding\": {\"type\": \"broken\"}}, ${finding()}]")

        assertEquals(2, response.size)
    }

    private fun finding(nulls: Boolean = false) = """
        {
          "finding": {
            "type": "n_plus_one_sql",
            "severity": "warning",
            "trace_id": "abc123",
            "service": "order-service",
            "grouping": ${if (nulls) "null" else """[{"key":"env","value":"prod"}]"""},
            "source_endpoint": "POST /orders",
            "pattern": {"template":"SELECT 1","occurrences":2,"window_ms":10,"distinct_params":2},
            "suggestion": "Batch the lookup",
            "first_timestamp": "2026-08-07T12:00:00Z",
            "last_timestamp": "2026-08-07T12:00:01Z",
            "confidence": ${if (nulls) "null" else "\"daemon_staging\""},
            "code_location": null,
            "signature": ${if (nulls) "null" else "\"sig\""}
          },
          "stored_at_ms": 1250,
          "first_seen_ms": ${if (nulls) "null" else "1000"},
          "seen_count": ${if (nulls) "null" else "3"}
        }
    """.trimIndent()

    @Test
    fun `decodes daemon findings and ignores future fields`() {
        val response = parseFindings(
            """
            [{
              "finding": {
                "type": "n_plus_one_sql",
                "severity": "critical",
                "trace_id": "abc123",
                "service": "order-service",
                "grouping": [{"key":"deployment.environment","value":"production"}],
                "source_endpoint": "POST /orders",
                "pattern": {"template":"SELECT * FROM item WHERE id = ?","occurrences":12,"window_ms":75,"distinct_params":12},
                "suggestion": "Batch the lookup",
                "first_timestamp": "2026-08-07T12:00:00Z",
                "last_timestamp": "2026-08-07T12:00:01Z",
                "confidence": "daemon_production",
                "code_location": {"function":"loadItems","filepath":"src/OrderService.java","lineno":42,"namespace":"com.example.OrderService"},
                "instrumentation_scopes": ["hibernate"],
                "signature": "n_plus_one_sql:order-service:POST_orders:1234",
                "future_field": {"safe":"to ignore"}
              },
              "stored_at_ms": 1250,
              "first_seen_ms": 1000,
              "seen_count": 3,
              "acknowledged_by": {"source":"daemon","by":"robin","at":"2026-08-07T12:05:00Z","reason":"accepted"},
              "future_envelope_field": true
            }]
            """.trimIndent(),
        )

        assertEquals(1, response.size)
        val row = response.single()
        assertEquals("n_plus_one_sql", row.finding.type)
        assertEquals(12, row.finding.pattern.occurrences)
        assertEquals(42, row.finding.codeLocation?.lineNumber)
        assertEquals(3, row.seenCount)
        assertEquals("daemon", row.acknowledgedBy?.source)
        assertNotNull(row.finding.grouping.singleOrNull())
    }

    @Test
    fun `decodes a Rider C sharp finding and points at the smoke source`() {
        val response = parseFindings(fixture("rider-smoke/file-line.json")).single()
        val location = requireNotNull(response.finding.codeLocation)

        assertEquals("rider-smoke", response.finding.service)
        assertEquals("Program.cs", location.filepath)
        assertEquals(12, location.lineNumber)
        assertEquals("PerfSentinel.RiderSmoke.Program", location.namespace)
        assertEquals("SlowPath", location.function)
        assertEquals(
            "Thread.Sleep(25);",
            fixture("rider-smoke/Program.cs").lineSequence().elementAt(location.lineNumber!! - 1).trim(),
        )
    }

    @Test
    fun `maps confidence to editor signal strength`() {
        assertEquals(HighlightLevel.HINT, highlightLevel("local_batch"))
        assertEquals(HighlightLevel.HINT, highlightLevel("ci_batch"))
        assertEquals(HighlightLevel.WARNING, highlightLevel("daemon_staging"))
        assertEquals(HighlightLevel.ERROR, highlightLevel("daemon_production"))
        assertEquals(HighlightLevel.HINT, highlightLevel("future_context"))
    }

    @Test
    fun `resolves only existing files inside the project`() {
        val root = Files.createTempDirectory("perf-sentinel-project")
        val source = Files.createDirectories(root.resolve("src")).resolve("OrderService.java")
        Files.writeString(source, "class OrderService {}")
        val outside = Files.createTempFile("outside", ".java")

        assertEquals(source.toRealPath(), resolveProjectFile(root, "src/OrderService.java"))
        assertEquals(null, resolveProjectFile(root, "../${outside.fileName}"))
        assertEquals(null, resolveProjectFile(root, outside.toString()))
        assertEquals(null, resolveProjectFile(root, "src/Missing.java"))
    }

    @Test
    fun `converts valid one-based daemon lines to editor offsets`() {
        assertEquals(41, zeroBasedLine(42, 100))
        assertEquals(null, zeroBasedLine(0, 100))
        assertEquals(null, zeroBasedLine(101, 100))
    }

    private fun fixture(path: String): String =
        requireNotNull(javaClass.classLoader.getResource(path)) { "Missing test fixture: $path" }.readText()
}
