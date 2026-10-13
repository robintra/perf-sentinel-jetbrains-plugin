package io.github.robintra.perfsentinel.core

import com.intellij.openapi.diagnostic.Logger
import com.intellij.testFramework.LoggedErrorProcessor
import com.intellij.testFramework.TestLoggerFactory
import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList
import java.util.logging.Handler
import java.util.logging.Level
import java.util.logging.LogRecord
import java.util.logging.Logger as JulLogger

class DaemonClientTest {
    // The Authorization header of every request the stub served, null when absent.
    private val authorizations = CopyOnWriteArrayList<String?>()

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

    @Test
    fun `sends the endpoint secret as a bearer credential`() {
        withServer(200, MINIMAL_FINDING) { endpoint ->
            runBlocking { DaemonClient().fetch(endpoint, "order-service", SECRET) }

            assertEquals(listOf("Bearer $SECRET"), authorizations)
        }
    }

    @Test
    fun `sends no authorization header without a secret`() {
        withServer(200, MINIMAL_FINDING) { endpoint ->
            runBlocking { DaemonClient().fetch(endpoint, "order-service") }

            assertEquals(listOf<String?>(null), authorizations)
        }
    }

    @Test
    fun `a 401 reads as a missing, expired or revoked credential`() {
        withServer(401, "") { endpoint ->
            val error = assertThrows(DaemonRequestException::class.java) {
                runBlocking { DaemonClient().fetch(endpoint, "order-service", SECRET) }
            }

            assertEquals("Credential missing, expired or revoked for $endpoint", error.message)
        }
    }

    @Test
    fun `a 403 reads as a credential no grant reaches`() {
        withServer(403, "") { endpoint ->
            val error = assertThrows(DaemonRequestException::class.java) {
                runBlocking { DaemonClient().fetch(endpoint, "order-service", SECRET) }
            }

            assertEquals("No grant on this Hub reaches this credential", error.message)
        }
    }

    @Test
    fun `never sends a secret over plain http to a host outside the loopback names`() {
        withServer(200, MINIMAL_FINDING) { endpoint ->
            // The IPv4-mapped literal still reaches the stub, so the stub proves nothing left the client.
            val elsewhere = endpoint.replace("127.0.0.1", "[::ffff:127.0.0.1]")

            val error = assertThrows(CredentialRefusedException::class.java) {
                runBlocking { DaemonClient().fetch(elsewhere, "order-service", SECRET) }
            }

            assertEquals("A credential is sent only over HTTPS or to this machine.", error.message)
            assertEquals(emptyList<String?>(), authorizations)
        }
    }

    @Test
    fun `refuses a secret holding a control character before sending it`() {
        withServer(200, MINIMAL_FINDING) { endpoint ->
            val error = assertThrows(CredentialRefusedException::class.java) {
                runBlocking { DaemonClient().fetch(endpoint, "order-service", "$SECRET\nx") }
            }

            assertEquals("The credential holds a control character, so it is not sent.", error.message)
            assertEquals(emptyList<String?>(), authorizations)
        }
    }

    @Test
    fun `does not carry a secret through a redirect`() {
        withServer(200, MINIMAL_FINDING) { target ->
            withRedirect("$target/api/findings") { endpoint ->
                assertThrows(DaemonRequestException::class.java) {
                    runBlocking { DaemonClient().fetch(endpoint, "order-service", SECRET) }
                }

                assertEquals(emptyList<String?>(), authorizations)
            }
        }
    }

    @Test
    fun `the secret reaches no log record and no exception message`() {
        // Every logger at ALL but HttpURLConnection's, whose header dump makes fetch() refuse the secret (next test).
        val verbose = mapOf("" to Level.ALL, HTTP_URL_CONNECTION to Level.INFO)
        val (messages, logged) = capturingLogs(verbose) {
            // Proves that a debug line from the IDE logger reaches the capture.
            Logger.getInstance(DaemonClient::class.java).debug(CANARY)
            listOf(200, 401, 403, 500).flatMap { status ->
                withServer(status, if (status == 200) MINIMAL_FINDING else "") { endpoint ->
                    // A line break makes the JDK echo the whole header value in its exception.
                    listOf(endpoint, endpoint.replace("127.0.0.1", "[::ffff:127.0.0.1]")).flatMap { target ->
                        listOf(SECRET, "$SECRET\nx").map { secret ->
                            runCatching { runBlocking { DaemonClient().fetch(target, "order-service", secret) } }
                                .exceptionOrNull()?.toString().orEmpty()
                        }
                    }
                }
            }
        }

        assertTrue(logged.toString(), logged.any { CANARY in it })
        (messages + logged).forEach { assertFalse(it, it.contains(SECRET)) }
    }

    @Test
    fun `sends no secret while HttpURLConnection debug logging would write its headers`() {
        withServer(200, MINIMAL_FINDING) { endpoint ->
            val (error, logged) = capturingLogs(mapOf(HTTP_URL_CONNECTION to Level.FINE)) {
                runCatching { runBlocking { DaemonClient().fetch(endpoint, "order-service", SECRET) } }.exceptionOrNull()
            }

            logged.forEach { assertFalse(it, it.contains(SECRET)) }
            assertTrue("$error", error is CredentialRefusedException)
            assertEquals(
                "Debug logging for sun.net.www.protocol.http.HttpURLConnection would write the credential to the IDE log, so it is not sent.",
                error?.message,
            )
            assertEquals(emptyList<String?>(), authorizations)
        }
    }

    // Warnings and errors through the IDE logger, and every JUL record, with [levels] set on those JUL loggers.
    private fun <T> capturingLogs(levels: Map<String, Level>, block: () -> T): Pair<T, List<String>> {
        val logged = CopyOnWriteArrayList<String>()
        val errors = object : LoggedErrorProcessor() {
            override fun processWarn(category: String, message: String, t: Throwable?): Boolean {
                logged += "$message $t"
                return false
            }

            override fun processError(
                category: String,
                message: String,
                details: Array<String>,
                t: Throwable?,
            ): Set<Action> {
                logged += "$message ${details.joinToString()} $t"
                return Action.NONE
            }
        }
        val records = object : Handler() {
            override fun publish(record: LogRecord) {
                logged += "${record.message} ${record.parameters?.joinToString()} ${record.thrown}"
            }

            override fun flush() = Unit

            override fun close() = Unit
        }.apply { level = Level.ALL }
        val root = JulLogger.getLogger("")
        // Held until restored: JUL keeps a logger, and the level set on it, only while something references it.
        val previous = levels.keys.map(JulLogger::getLogger).associateWith(JulLogger::getLevel)
        previous.keys.forEach { it.level = levels.getValue(it.name) }
        root.addHandler(records)
        try {
            return LoggedErrorProcessor.executeWith(errors).use { block() } to logged
        } finally {
            root.removeHandler(records)
            previous.forEach { (logger, level) -> logger.level = level }
        }
    }

    private fun <T> withServer(status: Int, body: String, test: (String) -> T): T {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/api/findings") { exchange ->
            authorizations += exchange.requestHeaders.getFirst("Authorization")
            val bytes = body.toByteArray()
            exchange.sendResponseHeaders(status, if (bytes.isEmpty()) -1 else bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            return test("http://127.0.0.1:${server.address.port}")
        } finally {
            server.stop(0)
        }
    }

    private fun withRedirect(location: String, test: (String) -> Unit) {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/api/findings") { exchange ->
            exchange.responseHeaders.add("Location", location)
            exchange.sendResponseHeaders(302, -1)
            exchange.close()
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
        // A plain JUnit class keeps Logger's default factory, which drops debug lines where the IDE hands them to JUL.
        // shortcut: a class loaded earlier in this JVM keeps the logger it got then. If HubContractTest loads HttpRequests
        // first, its debug lines (URL, status, redirect target, never a request header) escape this capture.
        @JvmStatic
        @BeforeClass
        fun routeIdeLoggingThroughJul() {
            if (Logger.getFactory() !is TestLoggerFactory) Logger.setFactory(TestLoggerFactory::class.java)
            // The first logger reconfigures JUL and drops its handlers, so that happens here, not inside a capture.
            Logger.getInstance(DaemonClientTest::class.java)
        }

        const val HTTP_URL_CONNECTION = "sun.net.www.protocol.http.HttpURLConnection"

        const val MINIMAL_FINDING_FOR_REUSE = """
            [{"finding":{"type":"slow_sql","severity":"warning","trace_id":"trace","service":"order-service","source_endpoint":"GET /orders","pattern":{"template":"SELECT 1","occurrences":1,"window_ms":10,"distinct_params":1},"suggestion":"Inspect the query","first_timestamp":"2026-08-07T12:00:00Z","last_timestamp":"2026-08-07T12:00:01Z","confidence":"daemon_staging","signature":"slow_sql:order"},"stored_at_ms":1}]
        """

        const val MINIMAL_FINDING = MINIMAL_FINDING_FOR_REUSE

        const val SECRET = "psh_dummy_dummy_dummy"

        const val CANARY = "perf-sentinel debug canary"
    }
}
