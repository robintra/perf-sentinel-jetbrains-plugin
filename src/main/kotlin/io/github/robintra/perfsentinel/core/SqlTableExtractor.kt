package io.github.robintra.perfsentinel.core

import java.util.Locale

internal data class SqlIdentifier(val value: String, val quoted: Boolean) {
    fun normalized(): String = if (quoted) value else value.lowercase(Locale.ROOT)
}

internal data class SqlTableReference(
    val schema: SqlIdentifier?,
    val table: SqlIdentifier,
)

internal object SqlTableExtractor {
    fun extract(sql: String): SqlTableReference? {
        val statement = skipCtes(lex(sql) ?: return null) ?: return null
        val target = when (statement.keyword()) {
            "SELECT" -> statement.indexOfKeyword("FROM", 1)?.plus(1)
            "INSERT", "MERGE" -> statement.requireKeyword("INTO", 1)
            "UPDATE" -> 1
            "DELETE" -> statement.requireKeyword("FROM", 1)
            else -> null
        } ?: return null
        val parsed = parseQualifiedIdentifier(statement.tokens, target) ?: return null
        if (parsed.value.schema == null && parsed.value.table.normalized() in statement.cteNames) return null
        if (statement.keyword() == "SELECT" && statement.hasCommaAfter(parsed.nextIndex)) return null
        return parsed.value
    }
}

private enum class SqlTokenKind { WORD, QUOTED_IDENTIFIER, DOT, COMMA, GROUP, OTHER }

private data class SqlToken(val text: String, val kind: SqlTokenKind) {
    fun keyword(): String? = text.uppercase(Locale.ROOT).takeIf { kind == SqlTokenKind.WORD }

    fun identifier(): SqlIdentifier? = when (kind) {
        SqlTokenKind.WORD -> SqlIdentifier(text, quoted = false)
        SqlTokenKind.QUOTED_IDENTIFIER -> SqlIdentifier(text, quoted = true)
        else -> null
    }
}

private data class SqlStatement(val tokens: List<SqlToken>, val cteNames: Set<String>) {
    fun keyword(): String? = tokens.firstOrNull()?.keyword()

    fun indexOfKeyword(value: String, from: Int): Int? =
        (from until tokens.size).firstOrNull { tokens[it].keyword() == value }

    fun requireKeyword(value: String, at: Int): Int? =
        if (tokens.getOrNull(at)?.keyword() == value) at + 1 else null

    fun hasCommaAfter(from: Int): Boolean {
        val boundaries = setOf("WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET", "FETCH", "FOR", "UNION", "EXCEPT", "INTERSECT")
        return tokens.drop(from).takeWhile { it.keyword() !in boundaries }.any { it.kind == SqlTokenKind.COMMA }
    }
}

private data class ParsedTable(val value: SqlTableReference, val nextIndex: Int)

private fun skipCtes(tokens: List<SqlToken>): SqlStatement? {
    if (tokens.firstOrNull()?.keyword() != "WITH") return SqlStatement(tokens, emptySet())
    var index = 1
    if (tokens.getOrNull(index)?.keyword() == "RECURSIVE") index++
    val names = mutableSetOf<String>()
    while (true) {
        val name = tokens.getOrNull(index)?.identifier() ?: return null
        names += name.normalized()
        index++
        if (tokens.getOrNull(index)?.kind == SqlTokenKind.GROUP) index++
        if (tokens.getOrNull(index)?.keyword() != "AS" || tokens.getOrNull(index + 1)?.kind != SqlTokenKind.GROUP) return null
        index += 2
        if (tokens.getOrNull(index)?.kind != SqlTokenKind.COMMA) break
        index++
    }
    return SqlStatement(tokens.drop(index), names)
}

private fun parseQualifiedIdentifier(tokens: List<SqlToken>, start: Int): ParsedTable? {
    val first = tokens.getOrNull(start)?.identifier() ?: return null
    if (tokens.getOrNull(start + 1)?.kind != SqlTokenKind.DOT) {
        return ParsedTable(SqlTableReference(null, first), start + 1)
    }
    val second = tokens.getOrNull(start + 2)?.identifier() ?: return null
    if (tokens.getOrNull(start + 3)?.kind == SqlTokenKind.DOT) return null
    return ParsedTable(SqlTableReference(first, second), start + 3)
}

private fun lex(sql: String): List<SqlToken>? {
    val tokens = mutableListOf<SqlToken>()
    var index = 0
    while (index < sql.length) {
        when {
            sql[index].isWhitespace() -> index++
            sql.startsWith("--", index) -> {
                index = sql.indexOf('\n', index + 2).takeIf { it >= 0 } ?: sql.length
            }
            sql.startsWith("/*", index) -> {
                val end = sql.indexOf("*/", index + 2)
                if (end < 0) return null
                index = end + 2
            }
            sql[index] == '\'' -> index = skipQuoted(sql, index, '\'', '\'') ?: return null
            sql[index] == '"' -> {
                val quoted = readQuoted(sql, index, '"', '"') ?: return null
                tokens += SqlToken(quoted.first, SqlTokenKind.QUOTED_IDENTIFIER)
                index = quoted.second
            }
            sql[index] == '`' -> {
                val quoted = readQuoted(sql, index, '`', '`') ?: return null
                tokens += SqlToken(quoted.first, SqlTokenKind.QUOTED_IDENTIFIER)
                index = quoted.second
            }
            sql[index] == '[' -> {
                val quoted = readQuoted(sql, index, ']', ']') ?: return null
                tokens += SqlToken(quoted.first, SqlTokenKind.QUOTED_IDENTIFIER)
                index = quoted.second
            }
            sql[index] == '(' -> {
                index = skipGroup(sql, index) ?: return null
                tokens += SqlToken("", SqlTokenKind.GROUP)
            }
            sql[index] == ')' -> return null
            sql[index] == '.' -> {
                tokens += SqlToken(".", SqlTokenKind.DOT)
                index++
            }
            sql[index] == ',' -> {
                tokens += SqlToken(",", SqlTokenKind.COMMA)
                index++
            }
            sql[index].isLetter() || sql[index] == '_' -> {
                val start = index++
                while (index < sql.length && (sql[index].isLetterOrDigit() || sql[index] == '_' || sql[index] == '$')) index++
                tokens += SqlToken(sql.substring(start, index), SqlTokenKind.WORD)
            }
            else -> {
                tokens += SqlToken(sql[index].toString(), SqlTokenKind.OTHER)
                index++
            }
        }
    }
    return tokens
}

private fun readQuoted(sql: String, start: Int, closing: Char, escaped: Char): Pair<String, Int>? {
    val value = StringBuilder()
    var index = start + 1
    while (index < sql.length) {
        if (sql[index] == closing) {
            if (index + 1 < sql.length && sql[index + 1] == escaped) {
                value.append(closing)
                index += 2
            } else {
                return value.toString() to index + 1
            }
        } else {
            value.append(sql[index++])
        }
    }
    return null
}

private fun skipQuoted(sql: String, start: Int, closing: Char, escaped: Char): Int? =
    readQuoted(sql, start, closing, escaped)?.second

private fun skipGroup(sql: String, start: Int): Int? {
    var depth = 1
    var index = start + 1
    while (index < sql.length) {
        when {
            sql.startsWith("--", index) -> index = sql.indexOf('\n', index + 2).takeIf { it >= 0 } ?: sql.length
            sql.startsWith("/*", index) -> {
                val end = sql.indexOf("*/", index + 2)
                if (end < 0) return null
                index = end + 2
            }
            sql[index] == '\'' -> index = skipQuoted(sql, index, '\'', '\'') ?: return null
            sql[index] == '"' -> index = skipQuoted(sql, index, '"', '"') ?: return null
            sql[index] == '`' -> index = skipQuoted(sql, index, '`', '`') ?: return null
            sql[index] == '[' -> index = skipQuoted(sql, index, ']', ']') ?: return null
            sql[index] == '(' -> {
                depth++
                index++
            }
            sql[index] == ')' -> {
                depth--
                index++
                if (depth == 0) return index
            }
            else -> index++
        }
    }
    return null
}
