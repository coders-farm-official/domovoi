package com.domovoi.app.ui.screens.documents

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

/** The phone's CSV reader/writer against what Python's csv module does (excel dialect). */
class CsvSheetTest {

    @Test fun plainAndQuotedFields() {
        assertEquals(
            listOf(listOf("a", "b,c", "say \"hi\"", "")),
            CsvSheet.parse("a,\"b,c\",\"say \"\"hi\"\"\",\r\n"),
        )
    }

    @Test fun newlinesInsideQuotesStayInTheField() {
        assertEquals(listOf(listOf("line 1\r\nline 2", "x")), CsvSheet.parse("\"line 1\r\nline 2\",x"))
        assertEquals(listOf(listOf("a\nb")), CsvSheet.parse("\"a\nb\"\n"))
    }

    @Test fun lineEndingsAndBlankLines() {
        assertEquals(listOf(listOf("a"), listOf(), listOf("b")), CsvSheet.parse("a\r\n\r\nb"))
        assertEquals(listOf(listOf("a"), listOf("b")), CsvSheet.parse("a\rb\r"))
        assertEquals(listOf(listOf("a", "")), CsvSheet.parse("a,"))
        assertEquals(emptyList<List<String>>(), CsvSheet.parse(""))
    }

    @Test fun quotesOutsideTheStartOfAFieldAreLiteral() {
        assertEquals(listOf(listOf("ab\"c", "abcd")), CsvSheet.parse("ab\"c,\"ab\"cd"))
    }

    @Test fun aBomIsDroppedAndBadBytesDoNotThrow() {
        val bytes = byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()) + "x,y".toByteArray() + byteArrayOf(0xFF.toByte())
        val g = CsvSheet.read(bytes)
        assertEquals("x", g[0][0].v)
        assertEquals("y�", g[0][1].v)
    }

    @Test fun readIsBoundedToTheWindow() {
        val text = (1..1003).joinToString("\n") { r -> (0 until 62).joinToString(",") { "$r" } }
        val g = CsvSheet.read(text.toByteArray())
        assertEquals(SHEET_MAX_ROWS, g.size)
        assertEquals(SHEET_MAX_COLS, g[0].size)
    }

    @Test fun writeQuotesOnlyWhatNeedsIt() {
        assertEquals("a,\"b,c\",\"d\"\"e\",\"f\ng\"", CsvSheet.line(listOf("a", "b,c", "d\"e", "f\ng")))
        assertEquals("a lone empty field is written as Python does", "\"\"", CsvSheet.line(listOf("")))
        assertEquals("", CsvSheet.line(emptyList()))
    }

    private fun padded(rows: List<List<String>>): List<List<String>> {
        val cols = maxOf(4, rows.maxOfOrNull { it.size } ?: 0)
        return List(maxOf(8, rows.size)) { r -> List(cols) { c -> rows.getOrNull(r)?.getOrNull(c) ?: "" } }
    }

    @Test fun anUneditedFileWritesBackTheSame() {
        val text = "name,qty\r\napples,3\r\nragged\r\n\r\n\"a,b\",=SUM(B2)\r\n"
        val rows = CsvSheet.parse(text)
        assertEquals(text, CsvSheet.write(padded(rows), rows, CsvSheet.newlineOf(text)))
    }

    @Test fun editsWidenOnlyTheirRowAndPaddingIsDropped() {
        val text = "a,b\nc\n"
        val rows = CsvSheet.parse(text)
        val grid = padded(rows).map { it.toMutableList() }
        grid[1][2] = "new"
        grid[0][0] = "A"
        assertEquals("A,b\nc,,new\n", CsvSheet.write(grid, rows, CsvSheet.newlineOf(text)))
    }

    @Test fun aNewRowPastTheEndIsKeptBlankRowsBeforeItToo() {
        val rows = CsvSheet.parse("a\r\n")
        val grid = padded(rows).map { it.toMutableList() }
        grid[3][1] = "z"
        assertEquals("a\r\n\r\n\r\n,z\r\n", CsvSheet.write(grid, rows, "\r\n"))
    }

    @Test fun rowsAndColumnsPastTheWindowAreCarriedOver() {
        val wide = (0 until 62).map { "c$it" }
        val original = listOf(wide) + List(SHEET_MAX_ROWS) { listOf("r${it + 1}") }
        val grid = CsvSheet.read(
            original.joinToString("\n") { CsvSheet.line(it) }.toByteArray(),
        ).map { r -> r.map { it.display() }.toMutableList() }
        grid[0][0] = "first"
        val out = CsvSheet.parse(CsvSheet.write(grid, original, "\n"))
        assertEquals(SHEET_MAX_ROWS + 1, out.size)
        assertEquals("first", out[0][0])
        assertEquals("c61", out[0][61])
        assertEquals("r1000", out[SHEET_MAX_ROWS][0])
    }

    @Test fun writingPastTheColumnBoundIsRefused() {
        try {
            CsvSheet.write(listOf(List(SHEET_MAX_COLS + 1) { if (it == SHEET_MAX_COLS) "x" else "" }), emptyList(), "\n")
            fail("expected a refusal")
        } catch (_: SheetRefused) {
        }
    }

    @Test fun newlineFollowsTheFile() {
        assertEquals("\n", CsvSheet.newlineOf("a\nb\n"))
        assertEquals("\r\n", CsvSheet.newlineOf("a\r\nb"))
        assertEquals("\r\n", CsvSheet.newlineOf(""))
    }
}
