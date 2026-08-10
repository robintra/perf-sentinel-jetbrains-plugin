package io.github.robintra.perfsentinel.core

import java.util.Locale

internal data class SqlIdentifier(val value: String, val quoted: Boolean)

internal data class SqlTableReference(
    val schema: SqlIdentifier?,
    val table: SqlIdentifier,
)

// Statements whose table reference can be followed by a second one. INSERT/MERGE are excluded: the
// column list right after the table is a GROUP, not an ambiguity.
private val MULTI_TABLE_STATEMENTS = setOf("SELECT", "UPDATE", "DELETE")

// Dialect modifiers that sit between UPDATE and the table name (Postgres ONLY, MySQL IGNORE, ...).
private val UPDATE_MODIFIERS = setOf("ONLY", "IGNORE", "LOW_PRIORITY")

internal object SqlTableExtractor {
    fun extract(sql: String): SqlTableReference? {
        val statement = skipCtes(lex(sql) ?: return null) ?: return null
        val target = when (statement.keyword()) {
            "SELECT" -> statement.indexOfKeyword("FROM", 1)?.plus(1)
            "INSERT", "MERGE" -> statement.requireKeyword("INTO", 1)
            "UPDATE" -> statement.skipKeywords(1, UPDATE_MODIFIERS)
            "DELETE" -> statement.requireKeyword("FROM", 1)
            else -> null
        } ?: return null
        val parsed = parseQualifiedIdentifier(statement.tokens, target) ?: return null
        // Folded on both sides: the guard only ever refuses navigation, so over-matching is safe
        // while under-matching would let a CTE alias resolve to an unrelated entity.
        if (parsed.value.schema == null && parsed.value.table.folded() in statement.cteNames) return null
        if (statement.keyword() in MULTI_TABLE_STATEMENTS &&
            (statement.tokens.getOrNull(parsed.nextIndex)?.kind == SqlTokenKind.GROUP || statement.hasCommaAfter(parsed.nextIndex))
        ) return null
        return parsed.value
    }
}

private fun SqlIdentifier.folded(): String = value.lowercase(Locale.ROOT)

// FOLDED_IDENTIFIER is a delimited identifier in a dialect where delimiting does not make the name
// case-sensitive (MySQL backticks, T-SQL brackets) — unlike ANSI/Postgres double quotes.
private enum class SqlTokenKind { WORD, QUOTED_IDENTIFIER, FOLDED_IDENTIFIER, DOT, COMMA, GROUP, OTHER }

private data class SqlToken(val text: String, val kind: SqlTokenKind) {
    fun keyword(): String? = text.uppercase(Locale.ROOT).takeIf { kind == SqlTokenKind.WORD }

    fun identifier(): SqlIdentifier? = when (kind) {
        SqlTokenKind.WORD, SqlTokenKind.FOLDED_IDENTIFIER -> SqlIdentifier(text, quoted = false)
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

    fun skipKeywords(from: Int, skipped: Set<String>): Int? =
        (from until tokens.size).firstOrNull { tokens[it].keyword() !in skipped }

    fun hasCommaAfter(from: Int): Boolean {
        // SET ends the table list of an UPDATE; without it every `SET a = 1, b = 2` would read as a
        // second table and refuse a perfectly unambiguous single-table statement.
        val boundaries = setOf(
            "SET", "WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET", "FETCH", "FOR", "UNION", "EXCEPT", "INTERSECT",
        )
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
        names += name.folded()
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
            sql.startsWith("/*", index) -> index = skipBlockComment(sql, index) ?: return null
            sql[index] == '\'' -> index = skipQuoted(sql, index, '\'', '\'', backslashEscapes = true) ?: return null
            sql[index] == '"' -> {
                val quoted = readQuoted(sql, index, '"', '"') ?: return null
                tokens += SqlToken(quoted.first, SqlTokenKind.QUOTED_IDENTIFIER)
                index = quoted.second
            }
            sql[index] == '`' -> {
                val quoted = readQuoted(sql, index, '`', '`') ?: return null
                tokens += SqlToken(quoted.first, SqlTokenKind.FOLDED_IDENTIFIER)
                index = quoted.second
            }
            sql[index] == '[' -> {
                val quoted = readQuoted(sql, index, ']', ']') ?: return null
                tokens += SqlToken(quoted.first, SqlTokenKind.FOLDED_IDENTIFIER)
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

// Nesting matters: PostgreSQL and T-SQL both allow /* ... /* ... */ ... */, and ending at the first
// */ leaves the tail of the comment to be lexed as real SQL.
private fun skipBlockComment(sql: String, start: Int): Int? {
    var depth = 1
    var index = start + 2
    while (index < sql.length) {
        when {
            sql.startsWith("/*", index) -> {
                depth++
                index += 2
            }
            sql.startsWith("*/", index) -> {
                depth--
                index += 2
                if (depth == 0) return index
            }
            else -> index++
        }
    }
    return null
}

// backslashEscapes covers MySQL/MariaDB string literals. Assuming an escape where the dialect has
// none only ever runs past the closing quote and yields no navigation; assuming none where there is
// one desynchronizes the lexer and yields a table name read out of a string literal.
private fun readQuoted(
    sql: String,
    start: Int,
    closing: Char,
    escaped: Char,
    backslashEscapes: Boolean = false,
): Pair<String, Int>? {
    val value = StringBuilder()
    var index = start + 1
    while (index < sql.length) {
        if (backslashEscapes && sql[index] == '\\' && index + 1 < sql.length) {
            value.append(sql[index + 1])
            index += 2
        } else if (sql[index] == closing) {
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

private fun skipQuoted(
    sql: String,
    start: Int,
    closing: Char,
    escaped: Char,
    backslashEscapes: Boolean = false,
): Int? = readQuoted(sql, start, closing, escaped, backslashEscapes)?.second

private fun skipGroup(sql: String, start: Int): Int? {
    var depth = 1
    var index = start + 1
    while (index < sql.length) {
        when {
            sql.startsWith("--", index) -> index = sql.indexOf('\n', index + 2).takeIf { it >= 0 } ?: sql.length
            sql.startsWith("/*", index) -> index = skipBlockComment(sql, index) ?: return null
            sql[index] == '\'' -> index = skipQuoted(sql, index, '\'', '\'', backslashEscapes = true) ?: return null
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
