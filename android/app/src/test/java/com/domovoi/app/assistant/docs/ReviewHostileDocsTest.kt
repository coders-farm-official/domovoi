package com.domovoi.app.assistant.docs

// Review round 1 of wf/documents-d1: hostile files through the PHONE
// readers (adapted from the reviewer's probe). Each case runs on a thread
// with a 1 MB stack (an Android background thread's size) and a wall
// clock; where the probe found a problem the assertion now pins the
// fixed behaviour: entity declarations refused whatever the encoding or
// part name, deep trees refused quickly, a header read once however many
// sections name it, sheets neither padded nor unbounded, and a part only
// the local headers know never read.

import com.domovoi.app.assistant.docs.DocFixtures.docx
import com.domovoi.app.assistant.docs.DocFixtures.p
import com.domovoi.app.assistant.docs.DocFixtures.write
import com.domovoi.app.assistant.docs.DocFixtures.zip
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class ReviewHostileDocsTest {
    @get:Rule val tmp = TemporaryFolder()

    private val W = "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\""

    private class Outcome(val extraction: DocExtraction?, val refused: String?, val other: Throwable?, val ms: Long) {
        val text: String get() = extraction?.parts?.joinToString("\n") { it.head + "\n" + it.text }.orEmpty()
        override fun toString() = when {
            extraction != null -> "ok parts=${extraction.parts.size} chars=${extraction.chars} ms=$ms"
            refused != null -> "refused: $refused ms=$ms"
            else -> "THROWABLE ${other?.javaClass?.simpleName}: ${other?.message?.take(120)} ms=$ms"
        }
    }

    private fun probe(case: String, name: String, bytes: ByteArray, pdf: PdfTextReader? = null): Outcome {
        val f = write(tmp.root, name, bytes)
        var out: Outcome? = null
        val t0 = System.nanoTime()
        val th = Thread(null, {
            out = try {
                Outcome(DocExtract.extract(f, name, pdf), null, null, 0)
            } catch (e: DocRefused) {
                Outcome(null, e.message, null, 0)
            } catch (e: Throwable) {
                Outcome(null, null, e, 0)
            }
        }, "review-$case", 1024L * 1024)
        th.start()
        th.join(120_000)
        val ms = (System.nanoTime() - t0) / 1_000_000
        assertFalse("$case still running after $ms ms", th.isAlive)
        val o = out!!.let { Outcome(it.extraction, it.refused, it.other, ms) }
        println("REVIEW $case file=${bytes.size / 1024}KB outcome=$o")
        assertEquals("$case escaped as ${o.other}", null, o.other)
        return o
    }

    /** Patch the central-directory uncompressed size of [member] to [size]. */
    private fun patchCdSize(z: ByteArray, member: String, size: Int): ByteArray {
        val b = z.copyOf()
        var i = 0
        while (i + 46 < b.size) {
            if (b[i] == 0x50.toByte() && b[i + 1] == 0x4B.toByte() && b[i + 2] == 1.toByte() && b[i + 3] == 2.toByte()) {
                val nlen = (b[i + 28].toInt() and 0xFF) or ((b[i + 29].toInt() and 0xFF) shl 8)
                val name = String(b, i + 46, nlen)
                if (name == member) {
                    for (k in 0 until 4) b[i + 24 + k] = ((size ushr (8 * k)) and 0xFF).toByte()
                }
            }
            i++
        }
        return b
    }

    private fun entriesOf(xlsx: ByteArray): MutableMap<String, ByteArray> =
        java.util.zip.ZipInputStream(xlsx.inputStream()).use { zin ->
            generateSequence { zin.nextEntry }.map { it.name to zin.readBytes() }.toMap().toMutableMap()
        }

    @Test fun lyingCentralDirectory() {
        val big = "<?xml version=\"1.0\"?><w:document $W><w:body>" + p("A".repeat(100 * 1024 * 1024)) + "</w:body></w:document>"
        val z = zip(mapOf("word/document.xml" to big.toByteArray()))
        val o = probe("lying_central", "lying.docx", patchCdSize(z, "word/document.xml", 200))
        assertTrue(o.toString(), o.refused != null)
    }

    @Test fun billionLaughsInEveryEncodingIsRefused() {
        val laughs = "<!DOCTYPE l [<!ENTITY a \"aaaaaaaaaa\"><!ENTITY b \"&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;\">]>" +
            "<w:document $W><w:body><w:p><w:r><w:t>&b;&b;</w:t></w:r></w:p></w:body></w:document>"
        val cases = listOf(
            Triple("laughs_utf8", "l8.docx", ("<?xml version=\"1.0\"?>" + laughs).toByteArray()),
            Triple("laughs_utf16_bom", "l16.docx", ("﻿<?xml version=\"1.0\" encoding=\"UTF-16\"?>" + laughs).toByteArray(Charsets.UTF_16LE)),
            Triple("laughs_utf16_nobom", "l16n.docx", ("<?xml version=\"1.0\" encoding=\"UTF-16LE\"?>" + laughs).toByteArray(Charsets.UTF_16LE)),
            Triple("laughs_utf32", "l32.docx", ("﻿<?xml version=\"1.0\" encoding=\"UTF-32\"?>" + laughs).toByteArray(charset("UTF-32LE"))),
        )
        for ((case, name, bytes) in cases) {
            val o = probe(case, name, zip(mapOf("word/document.xml" to bytes)))
            assertTrue("$case: $o", o.refused != null)
            assertFalse("$case expanded the entity: $o", o.text.contains("aaaaaaaaaa"))
        }
    }

    @Test fun deepNestingIsRefusedQuickly() {
        val n = 1990
        val inner = "<w:sdt><w:sdtContent>".repeat(n / 2) + "<w:p>" + "<w:r/>".repeat(300_000) + "<w:r><w:t>deep</w:t></w:r></w:p>" +
            "</w:sdtContent></w:sdt>".repeat(n / 2)
        val a = probe("deep_many_leaves", "deep.docx", docx(inner))
        assertTrue(a.toString(), a.refused != null && a.ms < 20_000)
        val cellDeep = "<w:tbl><w:tr><w:tc>" + "<w:customXml>".repeat(1900) + "<w:p>" + "<w:r/>".repeat(200_000) +
            "<w:r><w:t>cell</w:t></w:r></w:p>" + "</w:customXml>".repeat(1900) + "</w:tc></w:tr></w:tbl>"
        val b = probe("deep_table_cell", "deept.docx", docx(cellDeep))
        assertTrue(b.toString(), b.refused != null && b.ms < 20_000)
    }

    @Test fun aDeepButLegalTreeIsWalkedInOneStepPerElement() {
        // 200 levels (under the limit) and 100,000 leaves in a table cell:
        // the cell's paragraphs are found without nested sequences.
        val cell = "<w:tbl><w:tr><w:tc>" + "<w:customXml>".repeat(200) + "<w:p>" + "<w:r/>".repeat(100_000) +
            "<w:r><w:t>cell</w:t></w:r></w:p>" + "</w:customXml>".repeat(200) + "</w:tc></w:tr></w:tbl>"
        val o = probe("legal_deep_cell", "legal.docx", docx(cell))
        assertTrue(o.toString(), o.extraction != null && o.text.contains("cell") && o.ms < 20_000)
    }

    @Test fun aHugeAttributeIsRefused() {
        val o = probe("huge_attr", "attr.docx", docx("<w:p><w:pPr><w:pStyle w:val=\"${"A".repeat(60 * 1024 * 1024)}\"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>"))
        assertTrue(o.toString(), o.refused != null)
    }

    private val rnd = java.util.Random(7)
    private fun ra() = (1..6).map { "abcdefghijklmnopqrstuvwxyz0123456789"[rnd.nextInt(36)] }.joinToString("")

    @Test fun deepNestingPastTheRatioCheckIsRefusedByDepth() {
        // Random attributes keep the part under the zip-bomb ratio, so the
        // XML depth limit is what refuses it.
        val n = 995
        val deep = (1..n).joinToString("") { "<w:sdt x=\"${ra()}\"><w:sdtContent y=\"${ra()}\">" } + "<w:p>" +
            (1..300_000).joinToString("") { "<w:r x=\"${ra()}\"/>" } + "<w:r><w:t>deep</w:t></w:r></w:p>" + "</w:sdtContent></w:sdt>".repeat(n)
        val o = probe("rnd_deep_many_leaves", "rdeep.docx", docx(deep))
        assertTrue(o.toString(), o.refused?.contains("nested too deeply") == true && o.ms < 20_000)
    }

    @Test fun aHeaderNamedTwentyThousandTimesIsReadOnce() {
        val hdr = "<?xml version=\"1.0\"?><w:hdr $W>" + (1..1000).joinToString("") { p(ra().repeat(160)) } + "</w:hdr>"
        val rels = "<?xml version=\"1.0\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rIdH\" Type=\"h\" Target=\"header1.xml\"/></Relationships>"
        val refs = (1..20_000).joinToString("") { "<w:headerReference xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" r:id=\"rIdH\" x=\"${ra()}\"/>" }
        val o = probe("many_header_refs", "hdr.docx", docx(p("x") + "<w:sectPr>$refs</w:sectPr>", mapOf("word/header1.xml" to hdr, "word/_rels/document.xml.rels" to rels)))
        assertTrue(o.toString(), o.extraction != null && o.ms < 20_000)
        assertEquals(1, o.extraction!!.parts.count { it.label == "page header" })
    }

    @Test fun aFarCellIsLeftOutAndNothingIsPadded() {
        val rows = (1..3000).joinToString("") { r -> "<row r=\"$r\"><c r=\"A$r\"><v>$r</v></c><c r=\"XFD$r\"><v>1</v></c></row>" }
        val sheet = "<?xml version=\"1.0\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>$rows</sheetData></worksheet>"
        val entries = entriesOf(DocFixtures.xlsx(mapOf("S" to listOf(listOf("a")))))
        entries["xl/worksheets/sheet1.xml"] = sheet.toByteArray()
        val o = probe("xlsx_far_cell", "far.xlsx", zip(entries))
        val e = o.extraction!!
        assertTrue(o.toString(), e.chars < 200_000)
        assertTrue(e.truncated)
        assertTrue(e.notes.any { it.code == "truncated" })
        assertTrue(e.parts.first().text.lines().all { it.count { c -> c == '|' } <= 2 })
    }

    @Test fun aSharedStringsBombStopsAtTheTextCap() {
        val sst = "<?xml version=\"1.0\"?><sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><si><t>${"Z".repeat(32000)}</t></si></sst>"
        val rows = (1..20000).joinToString("") { r ->
            "<row r=\"$r\">" + "ABCDEFGHIJ".map { c -> "<c r=\"$c$r\" t=\"s\"><v>0</v></c>" }.joinToString("") + "</row>"
        }
        val sheet = "<?xml version=\"1.0\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>$rows</sheetData></worksheet>"
        val entries = entriesOf(DocFixtures.xlsx(mapOf("S" to listOf(listOf("a")))))
        entries["xl/worksheets/sheet1.xml"] = sheet.toByteArray()
        entries["xl/sharedStrings.xml"] = sst.toByteArray()
        entries["xl/_rels/workbook.xml.rels"] = String(entries["xl/_rels/workbook.xml.rels"]!!).replace(
            "</Relationships>",
            "<Relationship Id=\"rIdS\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings\" Target=\"sharedStrings.xml\"/></Relationships>",
        ).toByteArray()
        val o = probe("xlsx_sst_bomb", "sst.xlsx", zip(entries))
        assertTrue(o.toString(), o.refused != null || o.extraction!!.chars <= DocExtract.MAX_TEXT_CHARS + 100_000)
        assertTrue(o.toString(), o.ms < 60_000)
    }

    @Test fun aSheetReachedThroughARelationshipUnderAnyNameIsChecked() {
        val evil = "<?xml version=\"1.0\"?><!DOCTYPE w [<!ENTITY e \"EVIL\">]><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>" +
            "<row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>x &e;</t></is></c></row></sheetData></worksheet>"
        val entries = entriesOf(DocFixtures.xlsx(mapOf("S" to listOf(listOf("clean")))))
        entries.remove("xl/worksheets/sheet1.xml")
        entries["xl/worksheets/sheet1.bin"] = evil.toByteArray()
        entries["xl/_rels/workbook.xml.rels"] = String(entries["xl/_rels/workbook.xml.rels"]!!).replace("sheet1.xml", "sheet1.bin").toByteArray()
        val o = probe("xlsx_bin_doctype", "bin.xlsx", zip(entries))
        assertTrue(o.toString(), o.refused?.contains("entity declarations") == true)
    }

    @Test fun aPartOnlyTheLocalHeadersKnowIsNeverRead() {
        val evil = "<?xml version=\"1.0\"?><!DOCTYPE w [<!ENTITY e \"EVIL\">]><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>" +
            "<row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>from-the-local-only-record &e;</t></is></c></row></sheetData></worksheet>"
        val base = DocFixtures.xlsx(mapOf("S" to listOf(listOf("clean"))))
        val entries = java.util.zip.ZipInputStream(base.inputStream()).use { zin ->
            generateSequence { zin.nextEntry }.map { it.name to zin.readBytes() }.toList()
        }
        val evilRec = ByteArrayOutputStream().also { o ->
            ZipOutputStream(o).use { z -> z.putNextEntry(ZipEntry("xl/worksheets/sheet1.xml")); z.write(evil.toByteArray()); z.closeEntry() }
        }.toByteArray()
        val cdAt = run {
            var k = evilRec.size - 22
            while (k > 0 && !(evilRec[k] == 0x50.toByte() && evilRec[k + 1] == 0x4B.toByte() && evilRec[k + 2] == 1.toByte() && evilRec[k + 3] == 2.toByte())) k--
            k
        }
        val prefix = evilRec.copyOf(cdAt)
        val normal = ByteArrayOutputStream().also { o ->
            ZipOutputStream(o).use { z ->
                for ((n, d) in entries) { if (n == "xl/worksheets/sheet1.xml") continue; z.putNextEntry(ZipEntry(n)); z.write(d); z.closeEntry() }
                z.putNextEntry(ZipEntry("xl/worksheets/sheet1.xml")); z.write(entries.first { it.first == "xl/worksheets/sheet1.xml" }.second); z.closeEntry()
            }
        }.toByteArray()
        val shifted = normal.copyOf()
        var i = 0
        while (i + 46 < shifted.size) {
            if (shifted[i] == 0x50.toByte() && shifted[i + 1] == 0x4B.toByte() && shifted[i + 2] == 1.toByte() && shifted[i + 3] == 2.toByte()) {
                val off = (shifted[i + 42].toLong() and 0xFF) or ((shifted[i + 43].toLong() and 0xFF) shl 8) or
                    ((shifted[i + 44].toLong() and 0xFF) shl 16) or ((shifted[i + 45].toLong() and 0xFF) shl 24)
                val n2 = off + prefix.size
                for (k in 0 until 4) shifted[i + 42 + k] = ((n2 ushr (8 * k)) and 0xFF).toByte()
            }
            if (shifted[i] == 0x50.toByte() && shifted[i + 1] == 0x4B.toByte() && shifted[i + 2] == 5.toByte() && shifted[i + 3] == 6.toByte()) {
                val off = (shifted[i + 16].toLong() and 0xFF) or ((shifted[i + 17].toLong() and 0xFF) shl 8) or
                    ((shifted[i + 18].toLong() and 0xFF) shl 16) or ((shifted[i + 19].toLong() and 0xFF) shl 24)
                val n2 = off + prefix.size
                for (k in 0 until 4) shifted[i + 16 + k] = ((n2 ushr (8 * k)) and 0xFF).toByte()
            }
            i++
        }
        val bos = ByteArrayOutputStream()
        bos.write(prefix)
        bos.write(shifted)
        val o = probe("xlsx_local_vs_central", "mismatch.xlsx", bos.toByteArray())
        assertFalse(o.toString(), o.text.contains("from-the-local-only-record"))
        assertFalse(o.toString(), o.text.contains("EVIL"))
    }

    @Test fun csvColumnsAreCappedAndRowsNeverPadded() {
        val million = probe("csv_million_cols", "mc.csv", ("a,".repeat(3_000_000) + "a\n" + "1\n".repeat(10)).toByteArray())
        val m = million.extraction!!
        assertTrue(million.toString(), m.truncated && m.chars < 10_000)
        val amp = probe("csv_amplify", "amp.csv", ("h,".repeat(39_999) + "h\n" + "x\n".repeat(2500)).toByteArray())
        val a = amp.extraction!!
        assertTrue(amp.toString(), a.chars < 200_000)
        assertTrue(a.parts.first().text.lines().all { it == "| x |" })
    }

    @Test fun pathTraversalNamesAreNeverExtracted() {
        val z = zip(mapOf(
            "word/document.xml" to ("<?xml version=\"1.0\"?><w:document $W><w:body>" + p("traversal") + "</w:body></w:document>").toByteArray(),
            "../../../../evil.xml" to "<a/>".toByteArray(),
            "/abs/evil.xml" to "<a/>".toByteArray(),
        ))
        probe("traversal", "trav.docx", z)
        val written = tmp.root.parentFile!!.walkTopDown().filter { it.name == "evil.xml" }.toList()
        assertEquals(0, written.size)
    }

    @Test fun storeNamesCantEscape() {
        val att = tmp.newFolder("att")
        val store = ChatDocStore(att)
        val (_, f) = store.import("c-1-a", "../../../x.pdf", "%PDF-1.4".byteInputStream())
        assertTrue(f.canonicalPath.startsWith(att.canonicalPath + File.separator))
        assertTrue(runCatching { store.import("../evil", "a.pdf", "%PDF".byteInputStream()) }.isFailure)
    }

    @Test fun aHugelyWidePageIsSortedQuickly() {
        val blocks = (0 until 16).map { PdfBlock("Line number $it of a very wide page", 10f + (it % 2) * 300, 100f + it * 14, 290f + (it % 2) * 300, 112f + it * 14) }
        val o = probe("pdf_wide_page", "wide.pdf", "%PDF-1.4\n".toByteArray(), pdf = { _, _ -> listOf(PdfPageText(blocks, 1.0e9f, 0)) })
        assertTrue(o.toString(), o.ms < 10_000)
    }

    @Test fun aHugeRandomAttributeIsRefused() {
        val ab = ByteArray(45 * 1024 * 1024).also { rnd.nextBytes(it) }
        for (k in ab.indices) ab[k] = (65 + (ab[k].toInt() and 15)).toByte()
        val attr = String(ab, Charsets.ISO_8859_1)
        val o = probe("rnd_huge_attr", "rattr.docx", docx("<w:p><w:pPr><w:pStyle w:val=\"$attr\"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>"))
        assertTrue(o.toString(), o.refused != null)
    }

    @Test fun aTextFileIsReadUpToTheCapWithANote() {
        val big = ("Rent is due on the first day of each month and the deposit is returned. ".repeat(14) + "\n\n").repeat(60_000)
        val o = probe("txt_big", "big.txt", big.toByteArray())
        val e = o.extraction!!
        assertTrue(o.toString(), e.chars <= DocExtract.MAX_TEXT_CHARS)
        assertTrue(e.truncated)
        assertTrue(e.notes.single { it.code == "truncated" }.text.startsWith("This document is very large; Domovoi read the first part of it"))
    }

    @Test fun aLongPdfIsCutAtTheCapAndSaysHowManyPagesWereRead() {
        val page = (0 until 40).map { PdfBlock("Words on this page about the garden and the boiler, line $it of many.", 50f, 50f + it * 14, 550f, 62f + it * 14) }
        val pages = List(2000) { PdfPageText(page, 612f, 0) }
        val o = probe("pdf_long", "long.pdf", "%PDF-1.4\n".toByteArray(), pdf = { _, _ -> pages })
        val e = o.extraction!!
        val read = e.parts.mapNotNull { it.page }.max()
        assertTrue(o.toString(), read in 2..1999 && e.chars <= DocExtract.MAX_TEXT_CHARS)
        assertEquals("This document is very large; Domovoi read the first $read pages.", e.notes.single { it.code == "truncated" }.text)
    }
}
