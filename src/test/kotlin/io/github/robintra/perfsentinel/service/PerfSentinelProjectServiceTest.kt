package io.github.robintra.perfsentinel.service

import com.intellij.openapi.components.service
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.sun.net.httpserver.HttpServer
import io.github.robintra.perfsentinel.core.DEFAULT_ENDPOINT
import io.github.robintra.perfsentinel.settings.EndpointCredentials
import io.github.robintra.perfsentinel.settings.PerfSentinelSettings
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList

class PerfSentinelProjectServiceTest : BasePlatformTestCase() {
    // refresh() publishes on the EDT, and the password safe refuses slow work there.
    override fun runInDispatchThread() = false

    private val authorizations = CopyOnWriteArrayList<String?>()
    private lateinit var server: HttpServer
    private lateinit var endpoint: String

    override fun setUp() {
        super.setUp()
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
            createContext("/api/findings") { exchange ->
                authorizations += exchange.requestHeaders.getFirst("Authorization")
                val body = "[]".toByteArray()
                exchange.sendResponseHeaders(200, body.size.toLong())
                exchange.responseBody.use { it.write(body) }
            }
            start()
        }
        endpoint = "http://127.0.0.1:${server.address.port}"
    }

    override fun tearDown() {
        try {
            server.stop(0)
            EndpointCredentials.set(endpoint, null)
            project.service<PerfSentinelSettings>().update(listOf(DEFAULT_ENDPOINT), "")
        } finally {
            super.tearDown()
        }
    }

    fun testRefreshSendsTheSecretStoredForTheEndpoint() {
        EndpointCredentials.set(endpoint, SECRET)
        project.service<PerfSentinelSettings>().update(listOf(endpoint), "")
        val service = project.service<PerfSentinelProjectService>()

        service.refresh()

        val deadline = System.nanoTime() + 10_000_000_000
        while (service.state.refreshing || service.state.endpoints.singleOrNull()?.endpoint != endpoint) {
            check(System.nanoTime() < deadline) { "refresh did not finish: ${service.state}" }
            Thread.sleep(20)
        }
        assertNull(service.state.endpoints.single().error)
        assertEquals(listOf("Bearer $SECRET"), authorizations)
    }

    private companion object {
        const val SECRET = "psh_machine_9Tz3kD"
    }
}
