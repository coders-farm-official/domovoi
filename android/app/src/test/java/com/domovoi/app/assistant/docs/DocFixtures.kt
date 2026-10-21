package com.domovoi.app.assistant.docs

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Small documents built in the tests (no binary fixtures in the repo). */
object DocFixtures {
    fun zip(entries: Map<String, ByteArray>, stored: Boolean = false): ByteArray {
        val bos = ByteArrayOutputStream()
        ZipOutputStream(bos).use { z ->
            for ((name, bytes) in entries) {
                val e = ZipEntry(name)
                if (stored) {
                    e.method = ZipEntry.STORED
                    e.size = bytes.size.toLong()
                    e.compressedSize = bytes.size.toLong()
                    e.crc = CRC32().apply { update(bytes) }.value
                }
                z.putNextEntry(e)
                z.write(bytes)
                z.closeEntry()
            }
        }
        return bos.toByteArray()
    }

    private const val W = "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" " +
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\""

    /** A .docx: [body] is the inside of w:body. */
    fun docx(body: String, extra: Map<String, String> = emptyMap(), header: String? = null): ByteArray {
        val files = linkedMapOf(
            "[Content_Types].xml" to "<?xml version=\"1.0\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>",
            "word/styles.xml" to "<?xml version=\"1.0\"?><w:styles $W>" +
                "<w:style w:type=\"paragraph\" w:styleId=\"Heading1\"><w:name w:val=\"heading 1\"/></w:style>" +
                "<w:style w:type=\"paragraph\" w:styleId=\"Title\"><w:name w:val=\"Title\"/></w:style></w:styles>",
            "word/document.xml" to "<?xml version=\"1.0\" encoding=\"UTF-8\"?><w:document $W><w:body>$body" +
                (if (header != null) "<w:sectPr><w:headerReference w:type=\"default\" r:id=\"rId9\"/></w:sectPr>" else "") +
                "</w:body></w:document>",
        )
        if (header != null) {
            files["word/_rels/document.xml.rels"] = "<?xml version=\"1.0\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
                "<Relationship Id=\"rId9\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/header\" Target=\"header1.xml\"/></Relationships>"
            files["word/header1.xml"] = "<?xml version=\"1.0\"?><w:hdr $W><w:p><w:r><w:t>$header</w:t></w:r></w:p></w:hdr>"
        }
        files.putAll(extra)
        return zip(files.mapValues { it.value.toByteArray() })
    }

    fun p(text: String, style: String? = null) =
        "<w:p>" + (style?.let { "<w:pPr><w:pStyle w:val=\"$it\"/></w:pPr>" } ?: "") + "<w:r><w:t xml:space=\"preserve\">$text</w:t></w:r></w:p>"

    fun odt(body: String, styles: String? = null): ByteArray {
        val ns = "xmlns:office=\"urn:oasis:names:tc:opendocument:xmlns:office:1.0\" xmlns:text=\"urn:oasis:names:tc:opendocument:xmlns:text:1.0\" " +
            "xmlns:table=\"urn:oasis:names:tc:opendocument:xmlns:table:1.0\" xmlns:style=\"urn:oasis:names:tc:opendocument:xmlns:style:1.0\""
        val files = linkedMapOf(
            "mimetype" to "application/vnd.oasis.opendocument.text",
            "content.xml" to "<?xml version=\"1.0\"?><office:document-content $ns><office:body><office:text>$body</office:text></office:body></office:document-content>",
        )
        if (styles != null) files["styles.xml"] = "<?xml version=\"1.0\"?><office:document-styles $ns>$styles</office:document-styles>"
        return zip(files.mapValues { it.value.toByteArray() })
    }

    /** A one-or-more-sheet .xlsx with inline strings and numbers; a cell
     *  starting with "=" is a formula, with [cached] values where given. */
    fun xlsx(sheets: Map<String, List<List<String>>>, cached: Map<String, String> = emptyMap()): ByteArray {
        val files = linkedMapOf<String, String>()
        files["[Content_Types].xml"] = "<?xml version=\"1.0\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>"
        files["_rels/.rels"] = "<?xml version=\"1.0\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>"
        val sheetTags = StringBuilder()
        val rels = StringBuilder()
        sheets.keys.forEachIndexed { i, name ->
            sheetTags.append("<sheet name=\"$name\" sheetId=\"${i + 1}\" r:id=\"rId${i + 1}\"/>")
            rels.append("<Relationship Id=\"rId${i + 1}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet${i + 1}.xml\"/>")
        }
        files["xl/workbook.xml"] = "<?xml version=\"1.0\"?><workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" " +
            "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets>$sheetTags</sheets></workbook>"
        files["xl/_rels/workbook.xml.rels"] = "<?xml version=\"1.0\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">$rels</Relationships>"
        sheets.values.forEachIndexed { i, rows ->
            val sb = StringBuilder()
            rows.forEachIndexed { r, row ->
                sb.append("<row r=\"${r + 1}\">")
                row.forEachIndexed { c, v ->
                    val ref = ('A' + c).toString() + (r + 1)
                    when {
                        v.startsWith("=") -> sb.append("<c r=\"$ref\"><f>${v.substring(1)}</f>" + (cached[ref]?.let { "<v>$it</v>" } ?: "") + "</c>")
                        v.toDoubleOrNull() != null -> sb.append("<c r=\"$ref\"><v>$v</v></c>")
                        else -> sb.append("<c r=\"$ref\" t=\"inlineStr\"><is><t>$v</t></is></c>")
                    }
                }
                sb.append("</row>")
            }
            files["xl/worksheets/sheet${i + 1}.xml"] = "<?xml version=\"1.0\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>$sb</sheetData></worksheet>"
        }
        return zip(files.mapValues { it.value.toByteArray() })
    }

    fun write(dir: File, name: String, bytes: ByteArray): File = File(dir, name).apply { parentFile?.mkdirs(); writeBytes(bytes) }
}
