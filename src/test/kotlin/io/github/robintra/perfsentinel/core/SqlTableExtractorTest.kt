package io.github.robintra.perfsentinel.core

import junit.framework.TestCase

class SqlTableExtractorTest : TestCase() {
    fun testExtractsThePrimaryTableFromSupportedStatements() {
        val cases = mapOf(
            "SELECT * FROM orders WHERE id = ?" to ref("orders"),
            "INSERT INTO audit.orders(id) VALUES (?)" to ref("orders", "audit"),
            "UPDATE orders SET state = ?" to ref("orders"),
            "DELETE FROM orders WHERE id = ?" to ref("orders"),
            "MERGE INTO audit.orders target USING incoming source ON target.id = source.id" to ref("orders", "audit"),
        )

        cases.forEach { (sql, expected) -> assertEquals(sql, expected, SqlTableExtractor.extract(sql)) }
    }

    fun testHandlesCommentsStringsCTEsAndQuotedIdentifiers() {
        assertExtracts(ref("orders"), "SELECT '-- FROM fake' FROM /* FROM ignored */ orders")
        // Only ANSI double quotes make an identifier case-sensitive. MySQL backticks and T-SQL
        // brackets are pure delimiters, so `Orders` still has to match @Table(name = "orders").
        assertExtracts(ref("Order", "Sales", quoted = true), "SELECT * FROM \"Sales\".\"Order\"")
        assertExtracts(ref("orders", "sales"), "SELECT * FROM `sales`.`orders`")
        assertExtracts(ref("Orders", "Sales"), "SELECT * FROM [Sales].[Orders]")
        assertExtracts(
            ref("orders"),
            "WITH recent AS (SELECT * FROM audit_log) SELECT * FROM orders JOIN recent ON recent.id = orders.id",
        )
    }

    fun testRejectsUnsafeOrAmbiguousTargets() {
        listOf(
            "",
            "SELECT 1",
            "SELECT * FROM (SELECT * FROM orders) nested",
            "SELECT * FROM generate_series(1, 10)",
            "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
            "SELECT * FROM orders, customers",
            $$"SELECT * FROM ${dynamic_table}",
            "UPDATE",
            "INSERT INTO",
            "SELECT * FROM orders /* unterminated",
            "SELECT * FROM \"orders",
            // Multi-table UPDATE: the ambiguity guard used to run for SELECT only.
            "UPDATE orders o, customers c SET c.total = o.total WHERE o.cid = c.id",
        ).forEach { sql -> assertNull(sql, SqlTableExtractor.extract(sql)) }
    }

    fun testKeepsTheLexerInSyncThroughDialectQuirks() {
        // Nested block comment: ending at the first */ would leave "FROM shadow_orders" as live SQL.
        assertExtracts(
            ref("orders"),
            "SELECT id /* audit /* legacy */ FROM shadow_orders */ FROM orders WHERE id = ?",
        )
        // MySQL backslash escape: closing the literal early would surface the FROM inside it.
        assertExtracts(
            ref("orders"),
            "SELECT 'a\\' FROM users --' FROM orders WHERE id = ?",
        )
        // A quoted CTE definition must still shadow its unquoted reference.
        assertNull(SqlTableExtractor.extract("WITH \"Recent\" AS (SELECT * FROM audit_log) SELECT * FROM Recent"))
    }

    fun testReadsUpdateTargetsPastDialectModifiers() {
        assertExtracts(ref("orders"), "UPDATE ONLY orders SET state = ?")
        assertExtracts(ref("orders"), "UPDATE IGNORE orders SET state = ?")
        // SET bounds the table list, so assignment commas are not read as a second table.
        assertExtracts(ref("orders"), "UPDATE orders SET state = ?, total = ? WHERE id = ?")
    }

    private fun assertExtracts(expected: SqlTableReference, sql: String) {
        assertEquals(expected, SqlTableExtractor.extract(sql))
    }

    private fun ref(table: String, schema: String? = null, quoted: Boolean = false) = SqlTableReference(
        schema = schema?.let { SqlIdentifier(it, quoted) },
        table = SqlIdentifier(table, quoted),
    )
}
