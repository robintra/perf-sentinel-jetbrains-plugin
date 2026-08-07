package io.github.robintra.perfsentinel.core

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
        require(!uri.host.isNullOrBlank()) { "Endpoint host is required" }
        require(uri.userInfo == null) { "Credentials are not allowed in endpoint URLs" }
        require(uri.rawQuery == null) { "Endpoint URLs cannot contain a query" }
        require(uri.rawFragment == null) { "Endpoint URLs cannot contain a fragment" }

        val host = uri.host.lowercase().let { if (':' in it && !it.startsWith('[')) "[$it]" else it }
        val port = uri.port.takeIf { it >= 0 && !(it == 80 && scheme == "http" || it == 443 && scheme == "https") }
        "$scheme://$host${port?.let { ":$it" }.orEmpty()}${uri.normalize().rawPath.orEmpty()}".trimEnd('/')
    }.distinct()
