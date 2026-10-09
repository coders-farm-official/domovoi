package com.domovoi.app.ui.screens.documents

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The grid model shared by the server and phone sheet paths (SheetModel.kt). */
class SheetModelTest {

    @Test fun typedTextIsClassifiedByTheServersRules() {
        assertEquals(CellWrite.Blank, cellWrite(""))
        assertEquals(CellWrite.Formula("SUM(A1:A3)"), cellWrite("=SUM(A1:A3)"))
        assertEquals("a bare = is text", CellWrite.Text("="), cellWrite("="))
        assertEquals(CellWrite.Number("7"), cellWrite("007"))
        assertEquals(CellWrite.Number("5"), cellWrite(" 5 "))
        assertEquals(CellWrite.Number("1.5"), cellWrite("1.50"))
        assertEquals(CellWrite.Number("1000"), cellWrite("1e3"))
        assertEquals(CellWrite.Number("-0.25"), cellWrite("-.25"))
        assertEquals(CellWrite.Number("0"), cellWrite("-0"))
        assertEquals("Java float suffixes are not numbers to Python", CellWrite.Text("5d"), cellWrite("5d"))
        assertEquals(CellWrite.Text("NaN"), cellWrite("NaN"))
        assertEquals(CellWrite.Text("12 apples"), cellWrite("12 apples"))
        assertEquals("text keeps its spaces", CellWrite.Text(" x "), cellWrite(" x "))
    }

    @Test fun pythonFloatRepr() {
        assertEquals("0.1", pyFloat(0.1))
        assertEquals("123.0", pyFloat(123.0))
        assertEquals("-2.5", pyFloat(-2.5))
        assertEquals("1e+16", pyFloat(1e16))
        assertEquals("1.5e-05", pyFloat(1.5e-5))
        assertEquals("0.0001", pyFloat(1e-4))
        assertEquals("1234567.0", pyFloat(1234567.0))
    }

    @Test fun cellAddresses() {
        assertEquals("A", colName(0))
        assertEquals("Z", colName(25))
        assertEquals("AA", colName(26))
        assertEquals("BH", colName(59))
        assertEquals(59, colIndex("BH"))
        assertEquals(CellAt(11, 1), parseRef("B12"))
        assertEquals(CellAt(0, 27), parseRef("\$AB\$1"))
        assertNull(parseRef("12B"))
        assertEquals("C3", refOf(CellAt(2, 2)))
    }

    @Test fun onlyChangedCellsAreChanges() {
        val loaded = listOf(listOf("a", "b", ""), listOf("", "", ""))
        val edited = listOf(listOf("a", "B", ""), listOf("", "", "new"), listOf("x"))
        assertEquals(
            mapOf(CellAt(0, 1) to "B", CellAt(1, 2) to "new", CellAt(2, 0) to "x"),
            sheetChanges(loaded, edited),
        )
        assertTrue(sheetChanges(loaded, loaded).isEmpty())
    }

    @Test fun displayPrefersTheFormula() {
        assertEquals("=A1", SheetCell(v = "3", f = "=A1").display())
        assertEquals("3", SheetCell(v = "3").display())
        assertEquals("", (null as SheetCell?).display())
    }
}
