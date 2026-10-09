package com.domovoi.app.ui.screens.documents

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * The tag scanner behind the phone's .xlsx code (MiniXml.kt) on input a file
 * can carry but a reader must not choke on. The scanner expands no DOCTYPE
 * entities and resolves nothing external by construction; what it does
 * decode, the five predefined entities and numeric references, has to cope
 * with a reference that names no code point.
 */
class MiniXmlTest {

    @Test fun thePredefinedEntitiesAndNumericReferencesDecode() {
        assertEquals("A&<>\"'\u00e9\uD83D\uDE00", MiniXml.unescape("&#x41;&amp;&lt;&gt;&quot;&apos;&#233;&#x1F600;"))
    }

    @Test fun aNumericReferenceThatIsNotACodePointIsLeftAsWrittenNotThrown() {
        // Each of these made Character.toChars throw IllegalArgumentException
        // out of unescape, i.e. out of XlsxSheet.read for a crafted file.
        for (bad in listOf("&#x110000;", "&#-5;", "&#xD800;", "&#xDFFF;", "&#99999999999;", "&#xFFFFFFFFF;", "&#;", "&#x;")) {
            assertEquals(bad, "a${bad}b".let { MiniXml.unescape(it) }.removePrefix("a").removeSuffix("b"))
        }
    }

    @Test fun aCustomEntityIsNotExpanded() {
        // No DOCTYPE processing: an entity the file declares is just text.
        assertEquals("&lol9;", MiniXml.unescape("&lol9;"))
    }

    @Test fun aWorkbookCellWithSuchAReferenceStillReads() {
        val main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        val sheet = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" +
            "<!DOCTYPE x [<!ENTITY lol \"lol\"><!ENTITY lol2 \"&lol;&lol;&lol;\">]>" +
            "<worksheet xmlns=\"$main\"><sheetData><row r=\"1\">" +
            "<c r=\"A1\" t=\"inlineStr\"><is><t>x&#x110000;y&lol2;</t></is></c>" +
            "</row></sheetData></worksheet>"
        val bytes = ByteArrayOutputStream().also { bos ->
            ZipOutputStream(bos).use { z ->
                fun put(name: String, text: String) { z.putNextEntry(ZipEntry(name)); z.write(text.toByteArray()); z.closeEntry() }
                put("[Content_Types].xml", "<Types/>")
                put("_rels/.rels", "<Relationships><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>")
                put("xl/workbook.xml", "<workbook xmlns=\"$main\"><sheets><sheet name=\"S\" sheetId=\"1\" r:id=\"rId1\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"/></sheets></workbook>")
                put("xl/_rels/workbook.xml.rels", "<Relationships><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/></Relationships>")
                put("xl/worksheets/sheet1.xml", sheet)
            }
        }.toByteArray()
        assertEquals("x&#x110000;y&lol2;", XlsxSheet.read(bytes)[0][0].v)
    }
}
