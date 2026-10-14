package io.github.robintra.perfsentinel.service

import com.intellij.openapi.components.service
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.sun.net.httpserver.HttpServer
import io.github.robintra.perfsentinel.core.DEFAULT_ENDPOINT
import io.github.robintra.perfsentinel.core.EndpointSnapshot
import io.github.robintra.perfsentinel.settings.EndpointCredentials
import io.github.robintra.perfsentinel.settings.PerfSentinelSettings
import java.net.InetSocketAddress
import java.nio.file.FileSystems
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.CopyOnWriteArrayList

class PerfSentinelProjectServiceTest : BasePlatformTestCase() {
    // refresh() publishes on the EDT, and the password safe refuses slow work there.
    override fun runInDispatchThread() = false

    private val authorizations = CopyOnWriteArrayList<String?>()
    private lateinit var server: HttpServer
    private lateinit var endpoint: String
    private lateinit var tokenDir: Path

    @Volatile
    private var status = 200

    override fun setUp() {
        super.setUp()
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
            createContext("/api/findings") { exchange ->
                authorizations += exchange.requestHeaders.getFirst("Authorization")
                val body = "[]".toByteArray()
                exchange.sendResponseHeaders(status, body.size.toLong())
                exchange.responseBody.use { it.write(body) }
            }
            start()
        }
        endpoint = "http://127.0.0.1:${server.address.port}"
        // Never the developer's own sign-in.
        tokenDir = Files.createTempDirectory("perf-sentinel-token")
        project.service<PerfSentinelProjectService>().cliTokenPath = { tokenDir.resolve("hub-token.json") }
    }

    override fun tearDown() {
        try {
            server.stop(0)
            EndpointCredentials.set(endpoint, null)
            project.service<PerfSentinelSettings>().update(listOf(DEFAULT_ENDPOINT), "")
            tokenDir.toFile().deleteRecursively()
        } finally {
            super.tearDown()
        }
    }

    fun testRefreshSendsTheSecretStoredForTheEndpoint() {
        EndpointCredentials.set(endpoint, SECRET)

        assertNull(refresh().error)
        assertEquals(listOf("Bearer $SECRET"), authorizations)
    }

    fun testRefreshSendsThePerfSentinelLoginTokenForItsHub() {
        signIn(endpoint)

        assertNull(refresh().error)
        assertEquals(listOf("Bearer $LOGIN_TOKEN"), authorizations)
    }

    fun testTheEndpointCredentialWinsOverThePerfSentinelLogin() {
        signIn(endpoint)
        EndpointCredentials.set(endpoint, SECRET)

        assertNull(refresh().error)
        assertEquals(listOf("Bearer $SECRET"), authorizations)
    }

    fun testALoginForAnotherHubSendsNothing() {
        signIn("https://hub.example.test")

        assertNull(refresh().error)
        assertEquals(listOf<String?>(null), authorizations)
    }

    fun testANewLoginIsReadAtTheNextRefresh() {
        assertNull(refresh().error)
        signIn(endpoint)
        assertNull(refresh().error)

        assertEquals(listOf(null, "Bearer $LOGIN_TOKEN"), authorizations)
    }

    fun testASecondLoginReplacesTheFirstAtTheNextRefresh() {
        signIn(endpoint, expiresAtMs = 1)
        assertNotNull(refresh().error)
        signIn(endpoint, token = "pshub_t_8Xc1mB")
        assertNull(refresh().error)
        signIn(endpoint)
        assertNull(refresh().error)

        assertEquals(listOf("Bearer pshub_t_8Xc1mB", "Bearer $LOGIN_TOKEN"), authorizations)
    }

    fun testA401WithThePerfSentinelLoginPointsAtTheLoginCommand() {
        signIn(endpoint)
        status = 401

        assertEquals(
            "Not signed in to $endpoint, or the sign-in expired or was revoked: " +
                "run `perf-sentinel login --hub $endpoint`, or set a credential for this endpoint.",
            refresh().error,
        )
        assertEquals(listOf("Bearer $LOGIN_TOKEN"), authorizations)
    }

    fun testA401WithTheEndpointCredentialKeepsTheCredentialMessage() {
        signIn(endpoint)
        EndpointCredentials.set(endpoint, SECRET)
        status = 401

        assertEquals("Credential missing, expired or revoked for $endpoint", refresh().error)
    }

    fun testATokenFileThatDoesNotParseIsSkipped() {
        Files.writeString(tokenDir.resolve("hub-token.json"), """{"token":"$LOGIN_TOKEN"""")

        assertNull(refresh().error)
        assertEquals(listOf<String?>(null), authorizations)
    }

    fun testAnExpiredLoginShowsOnItsEndpointAndSendsNothing() {
        signIn(endpoint, expiresAtMs = 1)

        assertEquals(
            "The perf-sentinel sign-in to $endpoint expired: run `perf-sentinel login --hub $endpoint`.",
            refresh().error,
        )
        assertEquals(emptyList<String?>(), authorizations)
    }

    private fun signIn(hubUrl: String, expiresAtMs: Long = System.currentTimeMillis() + 3_600_000, token: String = LOGIN_TOKEN) {
        val file = tokenDir.resolve("hub-token.json")
        Files.writeString(file, """{"hub_url":"$hubUrl","token":"$token","expires_at_ms":$expiresAtMs}""")
        if ("posix" in FileSystems.getDefault().supportedFileAttributeViews()) {
            Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("rw-------"))
        }
    }

    private fun refresh(): EndpointSnapshot {
        project.service<PerfSentinelSettings>().update(listOf(endpoint), "")
        val service = project.service<PerfSentinelProjectService>()
        val before = service.state

        service.refresh()

        // A refresh publishes a refreshing state, then its result: a new state that is no longer refreshing.
        // The endpoint check skips a late result of the project's startup refresh, which reads the default one.
        val deadline = System.nanoTime() + 10_000_000_000
        while (service.state === before || service.state.refreshing || service.state.endpoints.singleOrNull()?.endpoint != endpoint) {
            check(System.nanoTime() < deadline) { "refresh did not finish: ${service.state}" }
            Thread.sleep(20)
        }
        return service.state.endpoints.single()
    }

    private companion object {
        const val SECRET = "psh_machine_9Tz3kD"
        const val LOGIN_TOKEN = "pshub_t_4Rv8pN"
    }
}
