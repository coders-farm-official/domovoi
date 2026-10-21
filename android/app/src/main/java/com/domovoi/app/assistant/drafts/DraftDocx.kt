package com.domovoi.app.assistant.drafts

import com.domovoi.app.ui.screens.documents.MiniXml
import java.io.ByteArrayOutputStream
import java.text.Normalizer
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * A draft as a .docx, written by hand (a few XML parts in a zip), the twin
 * of the server's `domovoi/drafts/docx_file.py`: the template's sizes and
 * spacing, a font that is named and never embedded (Arial or Times New
 * Roman, the Liberation fonts' metric twins), standard structure only (the
 * name as a large bold line in the body, sections as "Heading 2", bullets
 * in a real bulleted list, an address as one paragraph with line breaks).
 * No tables, text boxes or columns.
 */
object DraftDocx {
    private const val W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    private fun clean(text: String): String =
        Normalizer.normalize(text, Normalizer.Form.NFKC).split(Regex("(?U)\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

    private fun esc(s: String) = MiniXml.escape(s)

    /** Points in twentieths (spacing, margins, page size). */
    private fun tw(points: Double) = Math.round(points * 20).toInt()

    /** Points in half-points (font size). */
    private fun hp(points: Double) = Math.round(points * 2).toInt()

    private fun rPr(font: String, size: Double, bold: Boolean): String =
        "<w:rPr><w:rFonts w:ascii=\"${esc(font)}\" w:hAnsi=\"${esc(font)}\" w:cs=\"${esc(font)}\" w:eastAsia=\"${esc(font)}\"/>" +
            (if (bold) "<w:b/><w:bCs/>" else "") + "<w:color w:val=\"000000\"/>" +
            "<w:sz w:val=\"${hp(size)}\"/><w:szCs w:val=\"${hp(size)}\"/></w:rPr>"

    private fun spacing(st: DraftStyle) =
        "<w:spacing w:before=\"${tw(st.before)}\" w:after=\"${tw(st.after)}\" w:line=\"${tw(st.leading)}\" w:lineRule=\"exact\"/>"

    fun render(markdown: String, template: DraftTemplate, title: String = ""): ByteArray {
        val font = template.docxFont
        val body = StringBuilder()
        for (block in DraftBlocks.parse(markdown)) {
            val st = template.styles.getValue(block.style)
            val lines = block.lines.map(::clean).filter { it.isNotEmpty() }
            if (lines.isEmpty()) continue
            val pStyle = when (block.style) {
                "heading" -> "<w:pStyle w:val=\"Heading2\"/>"
                "bullet" -> "<w:pStyle w:val=\"ListBullet\"/><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr>"
                else -> ""
            }
            body.append("<w:p><w:pPr>").append(pStyle).append(spacing(st)).append("</w:pPr>")
            lines.forEachIndexed { i, line ->
                body.append("<w:r>").append(rPr(font, st.size, st.font == "bold"))
                    .append("<w:t xml:space=\"preserve\">").append(esc(line)).append("</w:t>")
                if (i < lines.size - 1) body.append("<w:br/>")
                body.append("</w:r>")
            }
            body.append("</w:p>")
        }
        val m = tw(template.margin)
        val document = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<w:document xmlns:w=\"$W\"><w:body>$body" +
            "<w:sectPr><w:pgSz w:w=\"${tw(template.width)}\" w:h=\"${tw(template.height)}\"/>" +
            "<w:pgMar w:top=\"$m\" w:right=\"$m\" w:bottom=\"$m\" w:left=\"$m\" w:header=\"720\" w:footer=\"720\" w:gutter=\"0\"/>" +
            "</w:sectPr></w:body></w:document>"
        val bodySt = template.styles.getValue("body")
        val head = template.styles.getValue("heading")
        val bullet = template.styles.getValue("bullet")
        val styles = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<w:styles xmlns:w=\"$W\">" +
            "<w:docDefaults><w:rPrDefault>${rPr(font, bodySt.size, false)}</w:rPrDefault></w:docDefaults>" +
            "<w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\"><w:name w:val=\"Normal\"/>" +
            rPr(font, bodySt.size, false) + "</w:style>" +
            "<w:style w:type=\"paragraph\" w:styleId=\"Heading2\"><w:name w:val=\"heading 2\"/>" +
            "<w:basedOn w:val=\"Normal\"/><w:next w:val=\"Normal\"/><w:qFormat/>" +
            "<w:pPr><w:keepNext/><w:outlineLvl w:val=\"1\"/></w:pPr>" + rPr(font, head.size, true) + "</w:style>" +
            "<w:style w:type=\"paragraph\" w:styleId=\"ListBullet\"><w:name w:val=\"List Bullet\"/>" +
            "<w:basedOn w:val=\"Normal\"/><w:pPr><w:numPr><w:numId w:val=\"1\"/></w:numPr>" +
            "<w:ind w:left=\"${tw(bullet.indent + 6)}\" w:hanging=\"${tw(bullet.indent)}\"/></w:pPr>" +
            rPr(font, bullet.size, false) + "</w:style></w:styles>"
        val numbering = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<w:numbering xmlns:w=\"$W\"><w:abstractNum w:abstractNumId=\"0\"><w:multiLevelType w:val=\"singleLevel\"/>" +
            "<w:lvl w:ilvl=\"0\"><w:start w:val=\"1\"/><w:numFmt w:val=\"bullet\"/><w:lvlText w:val=\"•\"/>" +
            "<w:lvlJc w:val=\"left\"/><w:pPr><w:ind w:left=\"${tw(bullet.indent + 6)}\" w:hanging=\"${tw(bullet.indent)}\"/></w:pPr>" +
            "<w:rPr><w:rFonts w:ascii=\"${esc(font)}\" w:hAnsi=\"${esc(font)}\"/></w:rPr></w:lvl></w:abstractNum>" +
            "<w:num w:numId=\"1\"><w:abstractNumId w:val=\"0\"/></w:num></w:numbering>"
        val contentTypes = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">" +
            "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>" +
            "<Default Extension=\"xml\" ContentType=\"application/xml\"/>" +
            "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>" +
            "<Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/>" +
            "<Override PartName=\"/word/numbering.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml\"/>" +
            "<Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/>" +
            "</Types>"
        val rels = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>" +
            "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties\" Target=\"docProps/core.xml\"/>" +
            "</Relationships>"
        val docRels = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>" +
            "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering\" Target=\"numbering.xml\"/>" +
            "</Relationships>"
        val core = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<cp:coreProperties xmlns:cp=\"http://schemas.openxmlformats.org/package/2006/metadata/core-properties\" " +
            "xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><dc:title>${esc(title)}</dc:title><dc:creator></dc:creator>" +
            "</cp:coreProperties>"
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { z ->
            fun put(name: String, text: String) {
                val bytes = text.toByteArray(Charsets.UTF_8)
                val e = ZipEntry(name)
                // A fixed time, so the same draft makes the same bytes.
                e.time = FIXED_TIME
                val crc = CRC32().apply { update(bytes) }
                e.crc = crc.value
                z.putNextEntry(e)
                z.write(bytes)
                z.closeEntry()
            }
            put("[Content_Types].xml", contentTypes)
            put("_rels/.rels", rels)
            put("word/document.xml", document)
            put("word/styles.xml", styles)
            put("word/numbering.xml", numbering)
            put("word/_rels/document.xml.rels", docRels)
            put("docProps/core.xml", core)
        }
        return out.toByteArray()
    }

    /** 2026-01-01T00:00:00Z. */
    private const val FIXED_TIME = 1767225600000L
}
