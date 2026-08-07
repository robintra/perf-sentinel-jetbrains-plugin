package io.github.robintra.perfsentinel.core

import java.net.URI

const val DEFAULT_ENDPOINT = "http://127.0.0.1:4318"

fun normalizeEndpoints(values: List<String>): List<String> = values
    .map(String::trim)
    .filter(String::isNotEmpty)
    .ifEmpty { listOf(DEFAULT_ENDPOINT) }
    .map { raw ->
    val uri = URI(raw)
    require(uri.scheme == "http" || uri.scheme == "https") { "Only HTTP and HTTPS endpoints are supported" }
    require(!uri.host.isNullOrBlank()) { "Endpoint host is required" }
    require(uri.userInfo == null) { "Credentials are not allowed in endpoint URLs" }
    require(uri.rawQuery == null) { "Endpoint URLs cannot contain a query" }
    require(uri.rawFragment == null) { "Endpoint URLs cannot contain a fragment" }
    uri.toString().trimEnd('/')
    }.distinct()
