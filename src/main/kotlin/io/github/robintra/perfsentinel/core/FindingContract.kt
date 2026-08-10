package io.github.robintra.perfsentinel.core

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.nio.file.InvalidPathException
import java.nio.file.Path
import java.nio.file.Paths

fun findingsUri(endpoint: String, service: String): URI {
    val encodedService = URLEncoder.encode(service, StandardCharsets.UTF_8)
    return URI("${endpoint.trimEnd('/')}/api/findings?service=$encodedService&limit=1000&include_acked=true")
}

fun resolveServiceName(projectName: String, basePath: String?, override: String?): String =
    override?.trim()?.takeIf(String::isNotEmpty)
        ?: basePath?.let(Paths::get)?.fileName?.toString()?.takeIf(String::isNotEmpty)
        ?: projectName

// One malformed entry drops itself, not the whole endpoint: a daemon that emits 300 findings and
// gets one of them wrong should still surface the other 299.
fun parseFindings(json: String): List<FindingResponse> =
    JsonParser.parseString(json).asJsonArray.mapNotNull { element ->
        runCatching { parseFinding(element.asJsonObject) }.getOrNull()
    }

private fun parseFinding(envelope: JsonObject): FindingResponse {
    val finding = envelope.requiredObject("finding")
    val pattern = finding.requiredObject("pattern")
    return FindingResponse(
        finding = Finding(
            type = finding.requiredString("type"),
            severity = finding.requiredString("severity"),
            traceId = finding.requiredString("trace_id"),
            service = finding.requiredString("service"),
            grouping = finding.optionalArray("grouping")?.map { grouping ->
                val value = grouping.asJsonObject
                GroupingAttribute(value.requiredString("key"), value.requiredString("value"))
            }.orEmpty(),
            sourceEndpoint = finding.requiredString("source_endpoint"),
            pattern = FindingPattern(
                template = pattern.requiredString("template"),
                occurrences = pattern.requiredInt("occurrences"),
                windowMs = pattern.requiredLong("window_ms"),
                distinctParams = pattern.requiredInt("distinct_params"),
            ),
            suggestion = finding.requiredString("suggestion"),
            firstTimestamp = finding.requiredString("first_timestamp"),
            lastTimestamp = finding.requiredString("last_timestamp"),
            confidence = finding.optionalString("confidence") ?: "ci_batch",
            codeLocation = finding["code_location"]?.takeUnless { it.isJsonNull }?.asJsonObject?.let { location ->
                CodeLocation(
                    function = location.optionalString("function"),
                    filepath = location.optionalString("filepath"),
                    lineNumber = location["lineno"]?.takeUnless { it.isJsonNull }?.asInt,
                    namespace = location.optionalString("namespace"),
                )
            },
            signature = finding.optionalString("signature").orEmpty(),
        ),
        storedAtMs = envelope.requiredLong("stored_at_ms"),
        firstSeenMs = envelope.optionalLong("first_seen_ms") ?: 0,
        seenCount = envelope.optionalLong("seen_count") ?: 1,
        acknowledgedBy = envelope["acknowledged_by"]?.takeUnless { it.isJsonNull }?.asJsonObject?.let { ack ->
            Acknowledgement(
                source = ack.requiredString("source"),
                by = ack.optionalString("by") ?: ack.optionalString("acknowledged_by"),
                reason = ack.optionalString("reason"),
            )
        },
    )
}

fun resolveProjectFile(projectRoot: Path, reportedPath: String): Path? = try {
    val realRoot = projectRoot.toRealPath()
    val reported = Paths.get(reportedPath)
    val candidate = (if (reported.isAbsolute) reported else realRoot.resolve(reported)).toRealPath()
    candidate.takeIf { it.startsWith(realRoot) }
} catch (_: InvalidPathException) {
    null
} catch (_: java.io.IOException) {
    null
}

fun zeroBasedLine(lineNumber: Int?, lineCount: Int): Int? =
    lineNumber?.takeIf { it in 1..lineCount }?.minus(1)

private fun JsonObject.requiredObject(name: String): JsonObject = get(name).asJsonObject
private fun JsonObject.requiredString(name: String): String = get(name).asString
private fun JsonObject.requiredInt(name: String): Int = get(name).asInt
private fun JsonObject.requiredLong(name: String): Long = get(name).asLong
// Gson hands back a JsonNull instance for an explicitly-null field, and every asX() on it throws,
// so every optional read goes through one of these.
private fun JsonObject.optionalString(name: String): String? = get(name)?.takeUnless { it.isJsonNull }?.asString
private fun JsonObject.optionalLong(name: String): Long? = get(name)?.takeUnless { it.isJsonNull }?.asLong
private fun JsonObject.optionalArray(name: String): JsonArray? = get(name)?.takeUnless { it.isJsonNull }?.asJsonArray
