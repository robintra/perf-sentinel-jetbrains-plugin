package io.github.robintra.perfsentinel.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class EndpointConfigTest {
    @Test
    fun `normalizes and deduplicates valid endpoints`() {
        assertEquals(
            listOf("http://127.0.0.1:4318", "https://sentinel.example.test", "http://localhost"),
            normalizeEndpoints(
                listOf(
                    " http://127.0.0.1:4318/ ",
                    "http://127.0.0.1:4318",
                    "https://sentinel.example.test/",
                    "HTTP://LOCALHOST:80/",
                    "http://localhost",
                ),
            ),
        )
    }

    @Test
    fun `rejects endpoints outside the supported HTTP boundary`() {
        listOf(
            "file:///tmp/findings.json",
            "http://user:secret@localhost:4318",
            "http://localhost:4318/base?token=secret",
            "http://localhost:4318/base#fragment",
        ).forEach { endpoint ->
            assertThrows(IllegalArgumentException::class.java) {
                normalizeEndpoints(listOf(endpoint))
            }
        }
    }

    @Test
    fun `accepts hostnames java net URI refuses to parse as a host`() {
        assertEquals(
            listOf("http://perf_sentinel:4318", "https://svc_a"),
            normalizeEndpoints(listOf("http://perf_sentinel:4318/", "HTTPS://SVC_A:443")),
        )
    }

    @Test
    fun `keeps a bracketed IPv6 literal and its port apart`() {
        assertEquals(
            listOf("http://[::1]", "http://[::1]:4318"),
            normalizeEndpoints(listOf("http://[::1]:80", "http://[::1]:4318")),
        )
    }

    @Test
    fun `uses the loopback daemon when no endpoint is configured`() {
        assertEquals(listOf(DEFAULT_ENDPOINT), normalizeEndpoints(listOf(" ", "")))
    }

    @Test
    fun `reports malformed endpoint syntax as a validation error`() {
        assertThrows(IllegalArgumentException::class.java) {
            normalizeEndpoints(listOf("http://bad host:4318"))
        }
    }
}
