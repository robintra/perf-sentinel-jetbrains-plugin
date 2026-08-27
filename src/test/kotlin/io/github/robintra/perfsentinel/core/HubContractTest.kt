package io.github.robintra.perfsentinel.core

import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetSocketAddress

/**
 * The plugin against a payload PerfSentinelHub actually served.
 *
 * Every other test here feeds the parser JSON written in this repository, which
 * proves the parser agrees with itself. The fixture below was captured from a
 * running Hub by the simulation lab's `hub-plugin-contract` scenario, using the
 * exact URI [findingsUri] builds. It is the only place where a rename on the
 * Hub side, or a daemon field the Hub stops passing through, fails a test in
 * this repository.
 *
 * Refreshing it is deliberate: run the lab scenario, review the diff, commit.
 *
 * The fixture carries no `code_location`: the OpenTelemetry Java agent attaches
 * no `code.*` attributes to JDBC spans, so no finding from a JVM service in the
 * lab has an anchor. Anchor resolution stays covered by the resolver tests,
 * which own that contract with the daemon's path conventions.
 */
class HubContractTest {
    @Test
    fun `parses a payload captured from a running Hub without losing findings`() {
        val body = fixture()
        val expected = Regex("\"signature\"").findAll(body).count()

        withServer(body) { endpoint ->
            val findings = runBlocking { DaemonClient().fetch(endpoint, "order-service") }

            // parseFindings drops a malformed row rather than the batch, so a
            // renamed required field empties the tool window silently. Counting
            // is what catches that.
            assertEquals(expected, findings.size)
            findings.forEach { response ->
                assertTrue("stored_at_ms did not survive", response.storedAtMs > 0)
                assertTrue("service is empty", response.finding.service.isNotEmpty())
                assertTrue("template is empty", response.finding.pattern.template.isNotEmpty())
                assertTrue("signature is empty", response.finding.signature.isNotEmpty())
            }
        }
    }

    @Test
    fun `ignores the Hub-owned keys the daemon never sent`() {
        val body = fixture()
        // status, sources, first_seen, last_seen and max_confidence are the
        // Hub's own additions. The parser must pass over them rather than
        // trip on an envelope shape the daemon alone never produces.
        listOf("\"status\"", "\"sources\"", "\"first_seen\"", "\"max_confidence\"").forEach {
            assertTrue("the captured fixture no longer carries $it", body.contains(it))
        }

        withServer(body) { endpoint ->
            val findings = runBlocking { DaemonClient().fetch(endpoint, "order-service") }
            assertTrue("the Hub envelope parsed to nothing", findings.isNotEmpty())
        }
    }

    private fun fixture(): String =
        checkNotNull(javaClass.getResourceAsStream("/hub-contract/lab-order-service.json")) {
            "capture the fixture first: run the simulation lab's hub-plugin-contract scenario"
        }.bufferedReader().readText()

    private fun withServer(body: String, test: (String) -> Unit) {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/api/findings") { exchange ->
            val bytes = body.toByteArray()
            exchange.sendResponseHeaders(200, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            test("http://127.0.0.1:${server.address.port}")
        } finally {
            server.stop(0)
        }
    }
}
