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

    fun testHandlesCommentsStringsCtesAndQuotedIdentifiers() {
        assertEquals(ref("orders"), SqlTableExtractor.extract("SELECT '-- FROM fake' FROM /* FROM ignored */ orders"))
        assertEquals(ref("Order", "Sales", quoted = true), SqlTableExtractor.extract("SELECT * FROM \"Sales\".\"Order\""))
        assertEquals(ref("orders", "sales", quoted = true), SqlTableExtractor.extract("SELECT * FROM `sales`.`orders`"))
        assertEquals(ref("orders", "sales", quoted = true), SqlTableExtractor.extract("SELECT * FROM [sales].[orders]"))
        assertEquals(
            ref("orders"),
            SqlTableExtractor.extract(
                "WITH recent AS (SELECT * FROM audit_log) SELECT * FROM orders JOIN recent ON recent.id = orders.id",
            ),
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
            "SELECT * FROM \${dynamic_table}",
            "UPDATE",
            "INSERT INTO",
            "SELECT * FROM orders /* unterminated",
            "SELECT * FROM \"orders",
        ).forEach { sql -> assertNull(sql, SqlTableExtractor.extract(sql)) }
    }

    private fun ref(table: String, schema: String? = null, quoted: Boolean = false) = SqlTableReference(
        schema = schema?.let { SqlIdentifier(it, quoted) },
        table = SqlIdentifier(table, quoted),
    )
}
