package io.github.robintra.perfsentinel.core

import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.net.InetSocketAddress

class DaemonClientTest {
    @Test
    fun `fetches findings and records their endpoint source`() {
        withServer(200, MINIMAL_FINDING) { endpoint ->
            val findings = runBlocking { DaemonClient().fetch(endpoint, "order-service") }

            assertEquals(1, findings.size)
            assertEquals(endpoint, findings.single().source)
        }
    }

    @Test
    fun `rejects non-success responses`() {
        withServer(503, "unavailable") { endpoint ->
            assertThrows(DaemonRequestException::class.java) {
                runBlocking { DaemonClient().fetch(endpoint, "order-service") }
            }
        }
    }

    @Test
    fun `rejects responses above the five mebibyte boundary`() {
        withServer(200, "x".repeat(MAX_RESPONSE_BYTES + 1)) { endpoint ->
            assertThrows(ResponseTooLargeException::class.java) {
                runBlocking { DaemonClient().fetch(endpoint, "order-service") }
            }
        }
    }

    @Test
    fun `stops a slow streaming response at the overall deadline`() {
        withSlowServer { endpoint ->
            assertThrows(DaemonTimeoutException::class.java) {
                runBlocking { DaemonClient(timeoutMillis = 200).fetch(endpoint, "order-service") }
            }
        }
    }

    private fun withServer(status: Int, body: String, test: (String) -> Unit) {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/api/findings") { exchange ->
            val bytes = body.toByteArray()
            exchange.sendResponseHeaders(status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            test("http://127.0.0.1:${server.address.port}")
        } finally {
            server.stop(0)
        }
    }

    private fun withSlowServer(test: (String) -> Unit) {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/api/findings") { exchange ->
            exchange.sendResponseHeaders(200, 0)
            try {
                exchange.responseBody.use { body ->
                    repeat(20) {
                        body.write(' '.code)
                        body.flush()
                        Thread.sleep(75)
                    }
                }
            } catch (_: Exception) {
                // The client closes the connection when its deadline expires.
            }
        }
        server.start()
        try {
            test("http://127.0.0.1:${server.address.port}")
        } finally {
            server.stop(0)
        }
    }

    companion object {
        const val MINIMAL_FINDING_FOR_REUSE = """
            [{"finding":{"type":"slow_sql","severity":"warning","trace_id":"trace","service":"order-service","source_endpoint":"GET /orders","pattern":{"template":"SELECT 1","occurrences":1,"window_ms":10,"distinct_params":1},"suggestion":"Inspect the query","first_timestamp":"2026-08-07T12:00:00Z","last_timestamp":"2026-08-07T12:00:01Z","confidence":"daemon_staging","signature":"slow_sql:order"},"stored_at_ms":1}]
        """

        const val MINIMAL_FINDING = MINIMAL_FINDING_FOR_REUSE
    }
}
