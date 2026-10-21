package com.domovoi.app.assistant.docs

import com.domovoi.app.assistant.docs.DocFixtures.docx
import com.domovoi.app.assistant.docs.DocFixtures.odt
import com.domovoi.app.assistant.docs.DocFixtures.p
import com.domovoi.app.assistant.docs.DocFixtures.write
import com.domovoi.app.assistant.docs.DocFixtures.xlsx
import com.domovoi.app.assistant.docs.DocFixtures.zip
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.RandomAccessFile

/**
 * The phone's extractors: every format to the server's part shapes and
 * labels, and the guards (zip bomb, encrypted zip, DOCTYPE and entities,
 * oversize, old formats, a PDF on a phone without the text API).
 */
class DocExtractTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun extract(name: String, bytes: ByteArray, pdf: PdfTextReader? = null) =
        DocExtract.extract(write(tmp.root, name, bytes), name, pdf)

    @Test fun docxReadsHeadingsTablesTrackedInsertionsAndAHeader() {
        val body = p("Residential Lease", "Title") +
            p("This lease is between Alex and Sam.") +
            p("Deposit", "Heading1") +
            "<w:p><w:r><w:t>The deposit is </w:t></w:r><w:ins w:id=\"1\"><w:r><w:t>\$1,500</w:t></w:r></w:ins>" +
            "<w:del w:id=\"2\"><w:r><w:delText>\$900</w:delText></w:r></w:del><w:r><w:t>.</w:t></w:r></w:p>" +
            "<w:p><w:pPr><w:numPr><w:ilvl w:val=\"0\"/></w:numPr></w:pPr><w:r><w:t>Returned in 30 days</w:t></w:r></w:p>" +
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Cost</w:t></w:r></w:p></w:tc></w:tr>" +
            "<w:tr><w:tc><w:p><w:r><w:t>Rent</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1200</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        val e = extract("lease.docx", docx(body, header = "12 Elm Street"))
        assertEquals(listOf("page header", "Section: Residential Lease", "Section: Deposit"), e.parts.map { it.label })
        val deposit = e.parts[2].text
        assertTrue(deposit, "The deposit is \$1,500." in deposit)
        assertFalse(deposit, "900" in deposit)
        assertTrue(deposit, "- Returned in 30 days" in deposit)
        assertTrue(deposit, "| Item | Cost |\n|---|---|\n| Rent | 1200 |" in deposit)
        assertEquals("12 Elm Street", e.parts[0].text)
        assertEquals("docx", e.kind)
        assertTrue(e.words > 10)
    }

    @Test fun aWordDocumentWithNoHeadingsIsLabelledByParagraph() {
        val long = "word ".repeat(900).trim()
        val e = extract("notes.docx", docx(p(long) + p(long) + p("short")))
        assertEquals(listOf("para 1", "para 2", "para 3"), e.parts.map { it.label })
    }

    @Test fun odtReadsSpacesFootnotesAndHeaders() {
        val body = "<text:h text:outline-level=\"1\">Rent</text:h>" +
            "<text:p>Due on the first<text:s text:c=\"3\"/>day.<text:note><text:note-citation>1</text:note-citation>" +
            "<text:note-body><text:p>By bank transfer.</text:p></text:note-body></text:note></text:p>" +
            "<text:p>Huge<text:s text:c=\"99999999\"/>gap</text:p>"
        val styles = "<office:master-styles><style:master-page><style:header><text:p>Elm Street</text:p></style:header></style:master-page></office:master-styles>"
        val e = extract("lease.odt", odt(body, styles))
        assertEquals(listOf("page header", "Section: Rent", "footnotes"), e.parts.map { it.label })
        assertTrue(e.parts[1].text, "Due on the first   day." in e.parts[1].text)
        assertEquals("1. By bank transfer.", e.parts[2].text)
        // A space run is capped at 1,000: a crafted count can't blow up memory.
        assertTrue(e.parts[1].text.length < 2000)
    }

    @Test fun xlsxReadsEverySheetWithFormulasAndTotals() {
        val sheets = linkedMapOf(
            "Budget" to listOf(listOf("Item", "Amount"), listOf("Rent", "1200"), listOf("Power", "85.5"), listOf("Total", "=SUM(B2:B3)")),
            "Notes" to listOf(listOf("Note"), listOf("paid")),
        )
        val e = extract("budget.xlsx", xlsx(sheets))
        assertEquals(listOf("Sheet Budget, rows 2-4", "Sheet Budget, column totals", "Sheet Notes, row 2"), e.parts.map { it.label })
        assertEquals("| Item | Amount |\n|---|---|", e.parts[0].head)
        assertTrue(e.parts[0].text, "| Total | =SUM(B2:B3) |" in e.parts[0].text)
        assertEquals(1, e.formulasWithoutValue)
        assertTrue(e.notes.any { it.code == "formulas_without_value" })
        assertTrue(e.parts[1].text, "Amount: total 1,285.50" in e.parts[1].text)
        assertEquals(2, e.sheets)
        // A formula whose value the file kept reads as the value.
        val cached = extract("budget2.xlsx", xlsx(sheets, cached = mapOf("B4" to "1285.5")))
        assertTrue("| Total | 1285.5 |" in cached.parts[0].text)
        assertEquals(0, cached.formulasWithoutValue)
    }

    @Test fun csvSniffsSemicolonsDropsTheBomAndFallsBackToCp1252() {
        val semi = byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()) + "Date;Amount\n2026-01-01;12,50\n2026-01-02;7\n".toByteArray()
        val e = extract("bank.csv", semi)
        assertEquals(listOf("rows 2-3"), e.parts.map { it.label }.take(1))
        assertEquals("| Date | Amount |\n|---|---|", e.parts[0].head)
        val cp = "Name,City\nJosé,München\n".toByteArray(charset("windows-1252"))
        val c = extract("contacts.csv", cp)
        assertTrue(c.parts[0].text, "| José | München |" in c.parts[0].text)
    }

    @Test fun markdownSectionsAndPlainText() {
        val md = extract("notes.md", "# Trip\nWe leave at 9.\n\n## Packing\n- tent\n- stove\n".toByteArray())
        assertEquals(listOf("Section: Trip", "Section: Packing"), md.parts.map { it.label })
        val txt = extract("notes.txt", "First paragraph.\n\nSecond paragraph.".toByteArray())
        assertEquals(listOf("para 1"), txt.parts.map { it.label })
    }

    @Test fun pdfPagesThroughTheReaderWithColumnsHeadersAndScans() {
        fun line(t: String, top: Float, left: Float = 50f, right: Float = 550f) = PdfBlock(t, left, top, right, top + 12)
        val body = "The tenant pays rent monthly and keeps the flat clean and tidy at all times."
        val pages = listOf(
            PdfPageText(listOf(line("ACME LEASE", 20f), line(body, 100f), line("Page 1", 760f)), 612f, 0),
            PdfPageText(listOf(line("ACME LEASE", 20f), line("Deposit is held by the landlord for the term of the lease agreement.", 100f), line("Page 2", 760f)), 612f, 0),
            PdfPageText(listOf(line("", 100f)), 612f, 1),
            PdfPageText(listOf(line("ACME LEASE", 20f), line("xq zzv qrt pkk wvv bnm zxcv qwrt plk mnb vcx zzq ttr ppq kkj llm", 100f), line("Page 4", 760f)), 612f, 0),
        )
        val e = extract("lease.pdf", "%PDF-1.7\n".toByteArray(), pdf = { _, _ -> pages })
        assertEquals(listOf("page header", "p. 1", "p. 2", "page footer"), e.parts.map { it.label })
        assertEquals(listOf(3), e.scannedPages)
        assertEquals(listOf(4), e.garbledPages)
        assertEquals(1, e.parts[1].page)
        assertTrue(e.notes.map { it.code }.containsAll(listOf("scanned_pages", "garbled_pages")))
    }

    @Test fun twoColumnsReadLeftThenRight() {
        val left = (0 until 9).map { PdfBlock("Left line $it of the column with running text", 40f, 100f + it * 14, 290f, 112f + it * 14) }
        val right = (0 until 9).map { PdfBlock("Right line $it of the column with running text", 320f, 100f + it * 14, 570f, 112f + it * 14) }
        val interleaved = left.zip(right).flatMap { listOf(it.second, it.first) }
        val (lines, cols) = DocPdf.columnSort(interleaved, 612f)
        assertEquals(2, cols)
        assertTrue(lines.indexOf("Left line 8 of the column with running text") < lines.indexOf("Right line 0 of the column with running text"))
        // A table's narrow cells are not columns (the running-text guard).
        val cells = (0 until 9).flatMap { listOf(PdfBlock("a$it", 40f, 100f + it * 14, 80f, 112f + it * 14), PdfBlock("b$it", 320f, 100f + it * 14, 360f, 112f + it * 14)) }
        assertEquals(1, DocPdf.columnSort(cells, 612f).second)
    }

    @Test fun aPdfOnAPhoneWithoutTheTextApiAsksForTheServer() {
        assertThrows(NoPdfTextApi::class.java) { extract("lease.pdf", "%PDF-1.4\n".toByteArray(), pdf = null) }
    }

    @Test fun anImageOnlyPdfIsRefusedSayingItIsAScan() {
        val e = assertThrows(DocRefused::class.java) {
            extract("scan.pdf", "%PDF-1.4\n".toByteArray(), pdf = { _, _ -> listOf(PdfPageText(emptyList(), 612f, 1)) })
        }
        assertEquals(DocNotes.NO_TEXT_SCANNED, e.message)
    }

    // ── The guards ──────────────────────────────────────────────────────

    @Test fun aZipBombIsRefusedBeforeAnythingIsInflated() {
        val bomb = zip(mapOf("word/document.xml" to ByteArray(3 * 1024 * 1024)))
        val e = assertThrows(DocRefused::class.java) { extract("bomb.docx", bomb) }
        assertTrue(e.message, "zip bomb" in e.message!!)
    }

    @Test fun anEncryptedZipIsRefused() {
        val bytes = docx(p("secret"))
        // Set the "encrypted" flag on the first central directory entry.
        for (i in bytes.size - 22 downTo 0) {
            if (bytes[i] == 0x50.toByte() && bytes[i + 1] == 0x4B.toByte() && bytes[i + 2] == 1.toByte() && bytes[i + 3] == 2.toByte()) {
                bytes[i + 8] = (bytes[i + 8].toInt() or 1).toByte()
            }
        }
        val e = assertThrows(DocRefused::class.java) { extract("locked.docx", bytes) }
        assertTrue(e.message, "encrypted" in e.message!!)
    }

    @Test fun aDoctypeOrEntityIsRefused() {
        val evil = "<?xml version=\"1.0\"?><!DOCTYPE d [<!ENTITY x SYSTEM \"http://example.com/x\">]><w:document " +
            "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"
        val bytes = docx(p("x"), extra = mapOf("word/document.xml" to evil))
        val e = assertThrows(DocRefused::class.java) { extract("evil.docx", bytes) }
        assertTrue(e.message, "entity" in e.message!!)
        val sheet = xlsx(mapOf("S" to listOf(listOf("a"))))
        val evilSheet = zip(
            java.util.zip.ZipInputStream(sheet.inputStream()).use { z ->
                generateSequence { z.nextEntry }.associate { it.name to z.readBytes() }
            } + ("xl/sharedStrings.xml" to "<!DOCTYPE x [<!ENTITY a \"aaaa\">]><sst/>".toByteArray()),
        )
        assertThrows(DocRefused::class.java) { extract("evil.xlsx", evilSheet) }
    }

    @Test fun anOversizeFileIsRefused() {
        val f = tmp.newFile("huge.txt")
        RandomAccessFile(f, "rw").use { it.setLength(DocExtract.MAX_FILE_BYTES + 1) }
        val e = assertThrows(DocRefused::class.java) { DocExtract.extract(f, "huge.txt", null) }
        assertTrue(e.message, "too large" in e.message!!)
    }

    @Test fun oldFormatsAndStrangersAreRefusedWithWhatToDo() {
        assertEquals("Domovoi can't read that older format. Save it as .docx or PDF, then attach it again.", DocExtract.refusalFor("letter.doc"))
        assertEquals("Domovoi can't read that older format. Save it as .xlsx or .csv, then attach it again.", DocExtract.refusalFor("old.xls"))
        assertTrue(DocExtract.refusalFor("song.mp3")!!.startsWith("Chat reads PDF"))
        val e = assertThrows(DocRefused::class.java) { extract("fake.pdf", "not a pdf".toByteArray(), pdf = { _, _ -> emptyList() }) }
        assertEquals("That file isn't a PDF, though its name ends in .pdf.", e.message)
        assertThrows(DocRefused::class.java) { extract("bin.txt", byteArrayOf(1, 0, 2, 0)) }
    }

    @Test fun deeplyNestedXmlIsRefused() {
        val deep = "<a>".repeat(DocXml.MAX_DEPTH + 5) + "</a>".repeat(DocXml.MAX_DEPTH + 5)
        assertThrows(DocRefused::class.java) { DocXml.parse(deep, "Word document") }
    }

    @Test fun theStoreCopiesCapsAndDeletesWithTheConversation() {
        val store = ChatDocStore(tmp.newFolder("att"))
        val (id, file) = store.import("c-1-a", "lease.pdf", "%PDF-1.4".byteInputStream())
        assertTrue(file.isFile)
        store.saveParts("c-1-a", id, DocExtraction(kind = "pdf", parts = listOf(DocPart("p. 1", "x"))))
        assertEquals("p. 1", store.loadParts("c-1-a", id)!!.parts.single().label)
        assertTrue(store.delete("c-1-a"))
        assertFalse(file.exists())
        assertThrows(IllegalArgumentException::class.java) { store.import("../x", "a.txt", "x".byteInputStream()) }
    }
}
