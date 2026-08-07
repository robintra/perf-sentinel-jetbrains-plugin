package io.github.robintra.perfsentinel.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class EndpointConfigTest {
    @Test
    fun `normalizes and deduplicates valid endpoints`() {
        assertEquals(
            listOf("http://127.0.0.1:4318", "https://sentinel.example.test"),
            normalizeEndpoints(
                listOf(
                    " http://127.0.0.1:4318/ ",
                    "http://127.0.0.1:4318",
                    "https://sentinel.example.test/",
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
    fun `uses the loopback daemon when no endpoint is configured`() {
        assertEquals(listOf(DEFAULT_ENDPOINT), normalizeEndpoints(listOf(" ", "")))
    }
}
