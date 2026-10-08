package com.domovoi.app.ui.screens.documents

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/**
 * The phone's .xlsx reader and cell-patching writer (XlsxSheet.kt), against a
 * hand-built workbook shaped like Excel's own output: shared strings (one rich,
 * with a phonetic run), an inline string, numbers, a boolean, dates through
 * built-in and custom formats, a shared formula, a calcChain, styles, and a
 * second sheet that must never be read or touched.
 */
class XlsxSheetTest {

    private val main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    private val contentTypes = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/><Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/></Types>"""

    private val rootRels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""

    // The workbook lists "Data" first even though its part is sheet2.xml: the
    // first sheet is the workbook's first, not the first file.
    private val workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="$main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr/><sheets><sheet name="Data" sheetId="2" r:id="rId2"/><sheet name="Other" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="191029"/></workbook>"""

    private val workbookRels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/><Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>"""

    // cellXfs: 0 General, 1 bold (font 1), 2 builtin date 14, 3 custom yyyy-mm-dd,
    // 4 builtin h:mm (20), 5 a currency-ish custom format with a quoted "d".
    private val styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="$main"><numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy\-mm\-dd"/><numFmt numFmtId="165" formatCode="&quot;d&quot;#,##0.00"/></numFmts><fonts count="2"><font><sz val="11"/></font><font><b/><sz val="11"/></font></fonts><cellStyleXfs count="1"><xf numFmtId="0" fontId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" xfId="0"/><xf numFmtId="0" fontId="1" xfId="0" applyFont="1"/><xf numFmtId="14" fontId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="20" fontId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" xfId="0" applyNumberFormat="1"/></cellXfs></styleSheet>"""

    private val shared = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="$main" count="3" uniqueCount="3"><si><t>Name</t></si><si><r><rPr><b/></rPr><t xml:space="preserve">App</t></r><r><t>les</t></r><rPh sb="0" eb="1"><t>ignored</t></rPh></si><si><t>Tom &amp; Jerry</t></si></sst>"""

    private val dataSheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="$main"><dimension ref="A1:E5"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><cols><col min="1" max="1" width="20" customWidth="1"/></cols><sheetData><row r="1" spans="1:5"><c r="A1" s="1" t="s"><v>0</v></c><c r="B1" s="1" t="inlineStr"><is><t>Qty</t></is></c><c r="C1" t="s"><v>2</v></c></row><row r="2" spans="1:5"><c r="A2" t="s"><v>1</v></c><c r="B2" s="1"><v>3</v></c><c r="C2"><v>2.5</v></c><c r="D2" t="b"><v>1</v></c><c r="E2"><f t="shared" ref="E2:E4" si="0">B2*C2</f><v>7.5</v></c></row><row r="3" spans="1:5"><c r="B3"><v>4</v></c><c r="C3"><v>1E-5</v></c><c r="E3"><f t="shared" si="0"/><v>0.00004</v></c></row><row r="4" spans="1:5"><c r="A4" s="2"><v>45306</v></c><c r="B4" s="3"><v>45306.25</v></c><c r="C4" s="4"><v>0.5</v></c><c r="D4" s="5"><v>12</v></c><c r="E4"><f t="shared" si="0"/><v>0</v></c></row><row r="5" spans="1:5"><c r="A5" t="str"><f>"x"&amp;"y"</f><v>xy</v></c><c r="B5" t="e"><v>#DIV/0!</v></c><c r="C5" s="1"/></row><row r="7"><c r="A7" s="1"/></row></sheetData><mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells><pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>"""

    private val otherSheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="$main"><sheetData><row r="1"><c r="A1"><v>999</v></c></row></sheetData></worksheet>"""

    private val calcChain = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<calcChain xmlns="$main"><c r="E2" i="2" l="1"/><c r="E3"/><c r="E4"/></calcChain>"""

    private fun zip(vararg parts: Pair<String, String>): ByteArray {
        val bos = ByteArrayOutputStream()
        ZipOutputStream(bos).use { z ->
            for ((name, text) in parts) {
                z.putNextEntry(ZipEntry(name))
                z.write(text.toByteArray())
                z.closeEntry()
            }
        }
        return bos.toByteArray()
    }

    private fun unzip(bytes: ByteArray): LinkedHashMap<String, String> {
        val out = LinkedHashMap<String, String>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                out[e.name] = String(z.readBytes())
            }
        }
        return out
    }

    private fun book(sheet: String = dataSheet): ByteArray = zip(
        "[Content_Types].xml" to contentTypes,
        "_rels/.rels" to rootRels,
        "xl/workbook.xml" to workbook,
        "xl/_rels/workbook.xml.rels" to workbookRels,
        "xl/worksheets/sheet1.xml" to otherSheet,
        "xl/worksheets/sheet2.xml" to sheet,
        "xl/styles.xml" to styles,
        "xl/sharedStrings.xml" to shared,
        "xl/calcChain.xml" to calcChain,
    )

    private fun List<List<SheetCell>>.text(r: Int, c: Int): String = getOrNull(r)?.getOrNull(c).display()

    // ── read ────────────────────────────────────────────────────────────

    @Test fun readsTheWorkbooksFirstSheetNotTheFirstFile() {
        val g = XlsxSheet.read(book())
        assertEquals("Name", g.text(0, 0))
        assertTrue(g.none { r -> r.any { it.v == "999" } })
    }

    @Test fun sharedStringsInlineStringsAndRichRuns() {
        val g = XlsxSheet.read(book())
        assertEquals("Qty", g.text(0, 1))
        assertEquals("rich runs joined, the phonetic run left out", "Apples", g.text(1, 0))
        assertEquals("entities decoded", "Tom & Jerry", g.text(0, 2))
    }

    @Test fun numbersBooleansErrorsAndStringFormulas() {
        val g = XlsxSheet.read(book())
        assertEquals("3", g.text(1, 1))
        assertEquals("2.5", g.text(1, 2))
        assertEquals("True", g.text(1, 3))
        assertEquals("Python prints 1E-5 as 1e-05", "1e-05", g.text(2, 2))
        assertEquals("=\"x\"&\"y\"", g.text(4, 0))
        assertEquals("#DIV/0!", g.text(4, 1))
    }

    @Test fun sharedFormulasAreTranslatedPerCell() {
        val g = XlsxSheet.read(book())
        assertEquals("=B2*C2", g.text(1, 4))
        assertEquals("=B3*C3", g.text(2, 4))
        assertEquals("=B4*C4", g.text(3, 4))
        assertEquals(SheetCell(f = "=B2*C2"), g[1][4])
    }

    @Test fun datesReadAsPythonPrintsThem() {
        val g = XlsxSheet.read(book())
        assertEquals("2024-01-15 00:00:00", g.text(3, 0))
        assertEquals("custom yyyy-mm-dd", "2024-01-15 06:00:00", g.text(3, 1))
        assertEquals("time-only below one day", "12:00:00", g.text(3, 2))
        assertEquals("a quoted d is not a date", "12", g.text(3, 3))
    }

    @Test fun styledEmptyCellsAndRowsAreTrimmed() {
        val g = XlsxSheet.read(book())
        assertEquals("row 7 holds only a styled blank", 5, g.size)
        assertEquals("C5 is a styled blank", 2, g[4].size)
    }

    @Test fun readIsBoundedToTheServersWindow() {
        val rows = StringBuilder()
        for (r in 1..1005) {
            rows.append("<row r=\"$r\">")
            for (c in listOf(0, 59, 60, 61)) rows.append("<c r=\"${colName(c)}$r\"><v>$r</v></c>")
            rows.append("</row>")
        }
        val g = XlsxSheet.read(book("<worksheet xmlns=\"$main\"><sheetData>$rows</sheetData></worksheet>"))
        assertEquals(SHEET_MAX_ROWS, g.size)
        assertEquals(SHEET_MAX_COLS, g[0].size)
        assertEquals("1000", g[999][59].v)
    }

    @Test fun rowsAndCellsWithoutReferencesAreImplied() {
        val sheet = "<worksheet xmlns=\"$main\"><sheetData><row><c><v>1</v></c><c><v>2</v></c></row>" +
            "<row><c t=\"inlineStr\"><is><t>x</t></is></c></row></sheetData></worksheet>"
        val g = XlsxSheet.read(book(sheet))
        assertEquals(listOf("1", "2"), g[0].map { it.display() })
        assertEquals("x", g.text(1, 0))
    }

    @Test fun aPrefixedNamespaceReads() {
        val sheet = "<x:worksheet xmlns:x=\"$main\"><x:sheetData><x:row r=\"1\"><x:c r=\"A1\" t=\"inlineStr\">" +
            "<x:is><x:t>hi</x:t></x:is></x:c><x:c r=\"B1\"><x:v>5</x:v></x:c></x:row></x:sheetData></x:worksheet>"
        val g = XlsxSheet.read(book(sheet))
        assertEquals("hi", g.text(0, 0))
        assertEquals("5", g.text(0, 1))
    }

    @Test fun notAZipIsAnError() {
        try {
            XlsxSheet.read("hello".toByteArray())
            fail("expected an error")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("xlsx"))
        }
    }

    // ── patch ───────────────────────────────────────────────────────────

    private fun editedGrid(g: List<List<SheetCell>>, edits: Map<CellAt, String>): Pair<List<List<String>>, List<List<String>>> {
        val rows = maxOf(8, g.size, (edits.keys.maxOfOrNull { it.row } ?: 0) + 1)
        val cols = maxOf(4, g.maxOfOrNull { it.size } ?: 0, (edits.keys.maxOfOrNull { it.col } ?: 0) + 1)
        val loaded = List(rows) { r -> List(cols) { c -> g.getOrNull(r)?.getOrNull(c).display() } }
        val edited = loaded.mapIndexed { r, row -> row.mapIndexed { c, t -> edits[CellAt(r, c)] ?: t } }
        return loaded to edited
    }

    private fun patched(edits: Map<CellAt, String>, source: ByteArray = book()): ByteArray {
        val (loaded, edited) = editedGrid(XlsxSheet.read(source), edits)
        return XlsxSheet.patch(source, sheetChanges(loaded, edited))
    }

    @Test fun noChangesGiveBackTheSameBytes() {
        val src = book()
        val (loaded, edited) = editedGrid(XlsxSheet.read(src), emptyMap())
        assertSame(src, XlsxSheet.patch(src, sheetChanges(loaded, edited)))
    }

    @Test fun editsLandAndReadBack() {
        val out = patched(
            mapOf(
                CellAt(1, 1) to "4",             // number over a styled number
                CellAt(1, 0) to "Pears",         // text over a shared string
                CellAt(6, 5) to "note",          // a new cell in a new row
                CellAt(1, 6) to "=B2*2",         // a new formula
                CellAt(1, 3) to "",              // cleared
            ),
        )
        val g = XlsxSheet.read(out)
        assertEquals("4", g.text(1, 1))
        assertEquals("Pears", g.text(1, 0))
        assertEquals("note", g.text(6, 5))
        assertEquals("=B2*2", g.text(1, 6))
        assertEquals("", g.text(1, 3))
        // Untouched cells read exactly as before.
        assertEquals("Name", g.text(0, 0))
        assertEquals("2024-01-15 00:00:00", g.text(3, 0))
        assertEquals("=B3*C3", g.text(2, 4))
    }

    @Test fun everythingButTheEditedSheetSurvivesByteForByte() {
        val before = unzip(book())
        val after = unzip(patched(mapOf(CellAt(1, 1) to "4")))
        for (part in listOf("xl/styles.xml", "xl/sharedStrings.xml", "xl/worksheets/sheet1.xml", "_rels/.rels")) {
            assertEquals(part, before[part], after[part])
        }
        assertEquals("entry order kept", before.keys.toList(), after.keys.toList())
    }

    @Test fun untouchedCellsAndSheetFeaturesKeepTheirXml() {
        val sheet = unzip(patched(mapOf(CellAt(1, 1) to "4", CellAt(1, 0) to "Pears")))["xl/worksheets/sheet2.xml"]!!
        for (kept in listOf(
            "<c r=\"A1\" s=\"1\" t=\"s\"><v>0</v></c>",
            "<row r=\"4\" spans=\"1:5\"><c r=\"A4\" s=\"2\"><v>45306</v></c>",
            "<cols><col min=\"1\" max=\"1\" width=\"20\" customWidth=\"1\"/></cols>",
            "<mergeCells count=\"1\"><mergeCell ref=\"A1:B1\"/></mergeCells>",
            "<c r=\"C2\"><v>2.5</v></c>",
        )) {
            assertTrue(kept, sheet.contains(kept))
        }
    }

    @Test fun anEditedCellKeepsItsStyle() {
        val sheet = unzip(patched(mapOf(CellAt(1, 1) to "4")))["xl/worksheets/sheet2.xml"]!!
        assertTrue(sheet, sheet.contains("<c r=\"B2\" s=\"1\"><v>4</v></c>"))
    }

    @Test fun textIsAnEscapedInlineStringAndSharedStringsAreLeftAlone() {
        val out = patched(mapOf(CellAt(1, 0) to "  a <b> & c"))
        val parts = unzip(out)
        assertTrue(parts["xl/worksheets/sheet2.xml"]!!.contains(
            "<c r=\"A2\" t=\"inlineStr\"><is><t xml:space=\"preserve\">  a &lt;b&gt; &amp; c</t></is></c>",
        ))
        assertEquals(shared, parts["xl/sharedStrings.xml"])
        assertEquals("  a <b> & c", XlsxSheet.read(out).text(1, 0))
    }

    @Test fun clearingAStyledCellKeepsTheStyleClearingAPlainOneDropsIt() {
        val sheet = unzip(patched(mapOf(CellAt(1, 1) to "", CellAt(1, 2) to "")))["xl/worksheets/sheet2.xml"]!!
        assertTrue(sheet.contains("<c r=\"B2\" s=\"1\"/>"))
        assertFalse(sheet.contains("r=\"C2\""))
    }

    @Test fun newRowsAndCellsGoInOrderAndTheDimensionGrows() {
        val sheet = unzip(patched(mapOf(CellAt(5, 2) to "x", CellAt(1, 7) to "y")))["xl/worksheets/sheet2.xml"]!!
        val r5 = sheet.indexOf("<row r=\"5\"")
        val r6 = sheet.indexOf("<row r=\"6\">")
        val r7 = sheet.indexOf("<row r=\"7\"")
        assertTrue("row 6 sits between 5 and 7", r5 in 0 until r6 && r6 < r7)
        assertTrue("H2 after E2", sheet.indexOf("r=\"H2\"") > sheet.indexOf("r=\"E2\""))
        assertTrue(sheet, sheet.contains("<dimension ref=\"A1:H6\"/>") || sheet.contains("<dimension ref=\"A1:H7\"/>"))
    }

    @Test fun rowsOfAnEditKeepNoStaleSpans() {
        val sheet = unzip(patched(mapOf(CellAt(1, 7) to "y")))["xl/worksheets/sheet2.xml"]!!
        assertTrue(sheet.contains("<row r=\"2\">"))
        assertTrue("other rows untouched", sheet.contains("<row r=\"3\" spans=\"1:5\">"))
    }

    @Test fun numbersAndFormulasAreStoredByTheServersRules() {
        val sheet = unzip(patched(mapOf(CellAt(2, 0) to "007", CellAt(2, 3) to "1.50", CellAt(2, 5) to "=SUM(B2:B4)")))
            .getValue("xl/worksheets/sheet2.xml")
        assertTrue(sheet.contains("<c r=\"A3\"><v>7</v></c>"))
        assertTrue(sheet.contains("<c r=\"D3\"><v>1.5</v></c>"))
        assertTrue(sheet.contains("<c r=\"F3\"><f>SUM(B2:B4)</f></c>"))
    }

    @Test fun formulaEditsDropTheCalcChainAndAskForARecalc() {
        val parts = unzip(patched(mapOf(CellAt(1, 6) to "=B2*2")))
        assertNull(parts["xl/calcChain.xml"])
        assertFalse(parts["xl/_rels/workbook.xml.rels"]!!.contains("calcChain"))
        assertFalse(parts["[Content_Types].xml"]!!.contains("calcChain"))
        assertTrue(parts["xl/workbook.xml"]!!.contains("<calcPr fullCalcOnLoad=\"1\" calcId=\"191029\"/>"))
    }

    @Test fun plainValueEditsLeaveTheCalcChainAlone() {
        val parts = unzip(patched(mapOf(CellAt(0, 1) to "Count")))
        assertEquals(calcChain, parts["xl/calcChain.xml"])
        assertEquals(workbook, parts["xl/workbook.xml"])
    }

    @Test fun editingASharedFormulaMasterExpandsItsGroup() {
        val out = patched(mapOf(CellAt(1, 4) to "=B2+C2"))
        val sheet = unzip(out)["xl/worksheets/sheet2.xml"]!!
        assertFalse("no dependent still points at the removed master", sheet.contains("t=\"shared\""))
        assertTrue(sheet.contains("<c r=\"E3\"><f>B3*C3</f><v>0.00004</v></c>"))
        val g = XlsxSheet.read(out)
        assertEquals("=B2+C2", g.text(1, 4))
        assertEquals("=B3*C3", g.text(2, 4))
        assertEquals("=B4*C4", g.text(3, 4))
    }

    @Test fun editsPastTheColumnBoundAreRefused() {
        try {
            sheetChanges(listOf(listOf("")), listOf(List(SHEET_MAX_COLS + 1) { if (it == SHEET_MAX_COLS) "x" else "" }))
            fail("expected a refusal")
        } catch (e: SheetRefused) {
            assertTrue(e.message!!.contains("60"))
        }
    }

    @Test fun anEmptySheetDataTakesNewCells() {
        val out = patched(mapOf(CellAt(0, 0) to "first"), XlsxSheet.blank())
        assertEquals("first", XlsxSheet.read(out).text(0, 0))
        // No calcPr and no formulas: the workbook is left as it was.
        assertFalse(unzip(out)["xl/workbook.xml"]!!.contains("calcPr"))
    }

    @Test fun aPrefixedSheetPatchesWithItsPrefix() {
        val sheet = "<x:worksheet xmlns:x=\"$main\"><x:sheetData><x:row r=\"1\"><x:c r=\"A1\"><x:v>1</x:v></x:c>" +
            "</x:row></x:sheetData></x:worksheet>"
        val out = patched(mapOf(CellAt(0, 1) to "two"), book(sheet))
        val xml = unzip(out)["xl/worksheets/sheet2.xml"]!!
        assertTrue(xml, xml.contains("<x:c r=\"B1\" t=\"inlineStr\"><x:is><x:t>two</x:t></x:is></x:c>"))
        assertEquals("two", XlsxSheet.read(out).text(0, 1))
    }

    @Test fun cellsPastTheWindowAreCarriedOver() {
        val sheet = "<worksheet xmlns=\"$main\"><sheetData><row r=\"1\"><c r=\"A1\"><v>1</v></c><c r=\"BZ1\"><v>9</v></c>" +
            "</row></sheetData></worksheet>"
        val out = patched(mapOf(CellAt(0, 0) to "2"), book(sheet))
        assertTrue(unzip(out)["xl/worksheets/sheet2.xml"]!!.contains("<c r=\"BZ1\"><v>9</v></c>"))
    }

    @Test fun theBlankWorkbookReadsEmpty() {
        assertEquals(emptyList<List<SheetCell>>(), XlsxSheet.read(XlsxSheet.blank()))
        assertArrayEquals(XlsxSheet.blank().take(4).toByteArray(), byteArrayOf(0x50, 0x4b, 0x03, 0x04))
    }

    // ── helpers ─────────────────────────────────────────────────────────

    @Test fun excelDatesMatchOpenpyxl() {
        assertEquals("2024-01-15 00:00:00", XlsxSheet.excelDateText(45306.0, false, false))
        assertEquals("1900-01-01 00:00:00", XlsxSheet.excelDateText(1.0, false, false))
        assertEquals("1900-03-01 00:00:00", XlsxSheet.excelDateText(61.0, false, false))
        assertEquals("06:30:00", XlsxSheet.excelDateText(0.2708333333333333, false, false))
        assertEquals("1904-01-02 00:00:00", XlsxSheet.excelDateText(1.0, true, false))
        assertEquals("1 day, 12:00:00", XlsxSheet.excelDateText(1.5, false, true))
        assertEquals("3:00:00", XlsxSheet.excelDateText(0.125, false, true))
        assertEquals("2024-01-15 12:00:00.500000", XlsxSheet.excelDateText(45306.5 + 0.5 / 86400, false, false))
    }

    @Test fun dateFormatDetection() {
        assertTrue(XlsxSheet.isDateFormat("yyyy-mm-dd"))
        assertTrue(XlsxSheet.isDateFormat("[h]:mm:ss"))
        assertTrue(XlsxSheet.isDateFormat("[\$-409]d-mmm-yy;@"))
        assertFalse(XlsxSheet.isDateFormat("General"))
        assertFalse(XlsxSheet.isDateFormat("0.00"))
        assertFalse(XlsxSheet.isDateFormat("[Red]#,##0"))
        assertFalse(XlsxSheet.isDateFormat("\"days\"0"))
        assertTrue(XlsxSheet.isTimedeltaFormat("[h]:mm:ss"))
        assertFalse(XlsxSheet.isTimedeltaFormat("h:mm:ss"))
    }

    @Test fun formulaTranslation() {
        assertEquals(
            "=B2+\$B\$1+C\$2+\$A3+SUM(D2:D4)+LOG10(B2)",
            XlsxSheet.translate("=A1+\$B\$1+B\$2+\$A2+SUM(C1:C3)+LOG10(A1)", 1, 1),
        )
        assertEquals("=\"A1\"&B2", XlsxSheet.translate("=\"A1\"&A1", 1, 1))
        assertEquals("=Sheet2!B2+'My Sheet'!B2", XlsxSheet.translate("=Sheet2!A1+'My Sheet'!A1", 1, 1))
        assertEquals("=SUM(B:B)+SUM(3:4)", XlsxSheet.translate("=SUM(A:A)+SUM(2:3)", 1, 1))
        assertEquals("=TRUE", XlsxSheet.translate("=TRUE", 1, 1))
    }
}
