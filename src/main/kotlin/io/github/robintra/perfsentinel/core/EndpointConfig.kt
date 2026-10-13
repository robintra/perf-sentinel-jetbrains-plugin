package io.github.robintra.perfsentinel.core

import io.github.robintra.perfsentinel.PerfSentinelBundle
import java.net.URI
import java.net.URISyntaxException

const val DEFAULT_ENDPOINT = "http://127.0.0.1:4318"

fun normalizeEndpoints(values: List<String>): List<String> = values
    .map(String::trim)
    .filter(String::isNotEmpty)
    .ifEmpty { listOf(DEFAULT_ENDPOINT) }
    .map { raw ->
        val uri = try {
            URI(raw)
        } catch (error: URISyntaxException) {
            throw IllegalArgumentException("Invalid endpoint URL", error)
        }
        val scheme = uri.scheme?.lowercase()
        require(scheme == "http" || scheme == "https") { "Only HTTP and HTTPS endpoints are supported" }
        // URI.getHost() is null for a registry-based authority — notably any label containing an
        // underscore, which is legal for docker-compose service names. Parse the authority instead
        // so `http://perf_sentinel:4318` is accepted rather than reported as having no host.
        val authority = uri.rawAuthority
        require(!authority.isNullOrBlank()) { "Endpoint host is required" }
        require(uri.rawUserInfo == null && '@' !in authority) { "Credentials are not allowed in endpoint URLs" }
        require(uri.rawQuery == null) { "Endpoint URLs cannot contain a query" }
        require(uri.rawFragment == null) { "Endpoint URLs cannot contain a fragment" }

        // Last colon splits the port; inside a bracketed IPv6 literal the trailing "]" makes the
        // suffix non-numeric, so `[::1]` keeps its brackets and reports no port.
        val separator = authority.lastIndexOf(':')
        val port = if (separator > 0) authority.substring(separator + 1).toIntOrNull() else null
        val host = (if (port == null) authority else authority.take(separator)).lowercase()
        require(host.isNotBlank()) { "Endpoint host is required" }

        val explicitPort = port?.takeIf { !(it == 80 && scheme == "http" || it == 443 && scheme == "https") }
        "$scheme://$host${explicitPort?.let { ":$it" }.orEmpty()}${uri.normalize().rawPath.orEmpty()}".trimEnd('/')
    }.distinct()

private val LOOPBACK_HOSTS = setOf("localhost", "127.0.0.1", "[::1]")

// A port forward lands on loopback; any other plain-http host can sit behind a network that reads the header.
fun credentialAllowed(endpoint: String): Boolean {
    val uri = URI(endpoint)
    return uri.scheme.equals("https", ignoreCase = true) || uri.host?.lowercase() in LOOPBACK_HOSTS
}

/** Why [secret] must not go to [endpoint], or null when it may. */
fun credentialRefusal(endpoint: String, secret: String): String? = when {
    !credentialAllowed(endpoint) -> PerfSentinelBundle.message("error.credential.transport")
    // The JDK quotes the whole header value when it refuses one, which would put the secret in a message.
    secret.any(Char::isISOControl) -> PerfSentinelBundle.message("error.credential.control")
    else -> null
}
