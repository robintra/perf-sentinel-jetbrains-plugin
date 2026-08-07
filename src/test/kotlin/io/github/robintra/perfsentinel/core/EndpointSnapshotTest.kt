package io.github.robintra.perfsentinel.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EndpointSnapshotTest {
    @Test
    fun `a failed refresh keeps the last successful findings and marks them stale`() {
        val finding = parseFindings(DaemonClientTest.MINIMAL_FINDING_FOR_REUSE).single()
        val previous = EndpointSnapshot("http://daemon", listOf(finding), 100, null)

        val updated = previous.updated(Result.failure(IllegalStateException("offline")), 200)

        assertEquals(previous.findings, updated.findings)
        assertEquals(100L, updated.lastSuccessAtMillis)
        assertEquals("offline", updated.error)
    }

    @Test
    fun `a successful refresh replaces stale data and clears the error`() {
        val finding = parseFindings(DaemonClientTest.MINIMAL_FINDING_FOR_REUSE).single()
        val previous = EndpointSnapshot("http://daemon", emptyList(), null, "offline")

        val updated = previous.updated(Result.success(listOf(finding)), 200)

        assertEquals(listOf(finding), updated.findings)
        assertEquals(200L, updated.lastSuccessAtMillis)
        assertNull(updated.error)
    }

    @Test
    fun `partial endpoint failure keeps successful and stale findings together`() {
        val finding = parseFindings(DaemonClientTest.MINIMAL_FINDING_FOR_REUSE).single()
        val healthy = EndpointSnapshot("http://healthy", emptyList(), null, null)
            .updated(Result.success(listOf(finding)), 200)
        val failed = EndpointSnapshot("http://failed", listOf(finding), 100, null)
            .updated(Result.failure(IllegalStateException("offline")), 200)

        val state = RefreshState(endpoints = listOf(healthy, failed))

        assertEquals(2, state.findings.size)
        assertEquals("offline", state.endpoints.single { it.endpoint == "http://failed" }.error)
        assertNull(state.endpoints.single { it.endpoint == "http://healthy" }.error)
    }

    @Test
    fun `changing service discards findings cached for the previous service`() {
        val finding = parseFindings(DaemonClientTest.MINIMAL_FINDING_FOR_REUSE).single()
        val previous = EndpointSnapshot(
            endpoint = "http://daemon",
            findings = listOf(finding),
            lastSuccessAtMillis = 100,
            service = "order-service",
        )

        val current = previous.forService("billing-service")

        assertEquals("billing-service", current.service)
        assertEquals(emptyList<FindingResponse>(), current.findings)
        assertNull(current.lastSuccessAtMillis)
        assertNull(current.error)
    }
}
