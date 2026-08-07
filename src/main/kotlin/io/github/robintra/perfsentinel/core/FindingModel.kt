package io.github.robintra.perfsentinel.core

data class FindingResponse(
    val finding: Finding,
    val storedAtMs: Long,
    val firstSeenMs: Long,
    val seenCount: Long,
    val acknowledgedBy: Acknowledgement?,
    val source: String = "",
)

data class Finding(
    val type: String,
    val severity: String,
    val traceId: String,
    val service: String,
    val grouping: List<GroupingAttribute>,
    val sourceEndpoint: String,
    val pattern: FindingPattern,
    val suggestion: String,
    val firstTimestamp: String,
    val lastTimestamp: String,
    val confidence: String,
    val codeLocation: CodeLocation?,
    val signature: String,
)

data class GroupingAttribute(val key: String, val value: String)

data class FindingPattern(
    val template: String,
    val occurrences: Int,
    val windowMs: Long,
    val distinctParams: Int,
)

data class CodeLocation(
    val function: String?,
    val filepath: String?,
    val lineNumber: Int?,
    val namespace: String?,
)

data class Acknowledgement(
    val source: String,
    val by: String?,
    val reason: String?,
)

enum class HighlightLevel { HINT, WARNING, ERROR }

fun highlightLevel(confidence: String): HighlightLevel = when (confidence) {
    "daemon_staging" -> HighlightLevel.WARNING
    "daemon_production" -> HighlightLevel.ERROR
    else -> HighlightLevel.HINT
}
