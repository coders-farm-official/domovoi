package com.domovoi.app.assistant.docs

import java.io.File

/**
 * Word (.docx) text from the paragraph XML itself, as the server reads it
 * (`domovoi/doc_extract/word.py`; D0: python-docx's paragraph text dropped
 * tracked insertions): every `w:t` in order, inserted runs included;
 * `w:delText` and `w:instrText` are other elements, so deletions and field
 * codes are skipped; `w:tab` a tab, `w:br`/`w:cr` a line break; Title and
 * "Heading N" styles (named in styles.xml) and outline levels are headings,
 * numbered paragraphs list items; tables markdown rows; content controls read
 * through; a text box's fallback copy (`mc:Fallback`) skipped; headers and
 * footers from the parts the sections name; footnotes and endnotes.
 */
object DocWord {
    private val HEADING = Regex("""(?i)^heading\s*(\d)$""")

    fun paraText(p: XmlEl): String {
        val sb = StringBuilder()
        fun walk(el: XmlEl) {
            when (el.name) {
                "t" -> if (el.qname.startsWith("w:") || el.qname == "t") el.children.filterIsInstance<String>().forEach { sb.append(it) }
                "tab" -> sb.append('\t')
                "br", "cr" -> sb.append('\n')
            }
            for (c in el.elements()) {
                if (c.qname == "mc:Fallback") continue
                walk(c)
            }
        }
        walk(p)
        return DocText.clean(sb.toString())
    }

    private fun styles(z: DocZip): Map<String, String> {
        if (!z.has("word/styles.xml")) return emptyMap()
        val out = HashMap<String, String>()
        DocXml.parse(z.text("word/styles.xml"), "Word document").find("style").forEach { st ->
            val id = st.attr("styleId") ?: return@forEach
            out[id] = st.child("name")?.attr("val") ?: id
        }
        return out
    }

    private fun block(p: XmlEl, styles: Map<String, String>): DocBlock {
        val text = paraText(p)
        val ppr = p.child("pPr")
        var style = ""
        var level: Int? = null
        var numbered = false
        if (ppr != null) {
            ppr.child("pStyle")?.attr("val")?.let { style = styles[it] ?: it }
            ppr.child("outlineLvl")?.attr("val")?.toIntOrNull()?.let { level = it + 2 }
            numbered = ppr.child("numPr") != null
        }
        if (style.equals("title", ignoreCase = true)) return DocBlock("h", text, 1)
        HEADING.find(style.trim())?.let { return DocBlock("h", text, it.groupValues[1].toInt() + 1) }
        level?.let { if (it <= 7) return DocBlock("h", text, it) }
        if (numbered || style.lowercase().startsWith("list")) return DocBlock("p", "- " + text.trim())
        return DocBlock("p", text)
    }

    private fun table(tbl: XmlEl): String {
        val rows = tbl.all("tr").map { tr ->
            tr.all("tc").map { tc -> tc.find("p").map { paraText(it).trim() }.filter { it.isNotEmpty() }.joinToString(" ") }
        }
        return DocStructure.mdTable(rows)
    }

    private fun walk(el: XmlEl, styles: Map<String, String>, out: MutableList<DocBlock>) {
        for (ch in el.elements()) {
            when (ch.name) {
                "p" -> out += block(ch, styles)
                "tbl" -> table(ch).takeIf { it.isNotEmpty() }?.let { out += DocBlock("table", it) }
                "sdt" -> ch.child("sdtContent")?.let { walk(it, styles, out) }
                "customXml", "ins", "smartTag" -> walk(ch, styles, out)
            }
        }
    }

    private fun rels(z: DocZip): Map<String, String> {
        if (!z.has("word/_rels/document.xml.rels")) return emptyMap()
        val out = HashMap<String, String>()
        DocXml.parse(z.text("word/_rels/document.xml.rels"), "Word document").find("Relationship").forEach { r ->
            val id = r.attrs["Id"] ?: return@forEach
            val target = r.attrs["Target"] ?: ""
            if (target.startsWith("/") || target.startsWith("http:") || target.startsWith("https:") || r.attrs["TargetMode"] == "External") return@forEach
            out[id] = "word/" + target.trimStart('.', '/')
        }
        return out
    }

    private fun partText(z: DocZip, name: String): String {
        if (!z.has(name)) return ""
        return DocXml.parse(z.text(name), "Word document").find("p").map { paraText(it).trim() }
            .filter { it.isNotEmpty() }.joinToString("\n")
    }

    private fun notes(z: DocZip, name: String, tag: String): List<String> {
        if (!z.has(name)) return emptyList()
        return DocXml.parse(z.text(name), "Word document").find(tag).mapNotNull { fn ->
            if (fn.attr("type") in setOf("separator", "continuationSeparator", "continuationNotice")) return@mapNotNull null
            val id = fn.attr("id")?.toIntOrNull() ?: return@mapNotNull null
            if (id <= 0) return@mapNotNull null
            fn.find("p").map { DocText.pySplit(paraText(it)).joinToString(" ") }.joinToString(" ").trim().ifEmpty { null }
        }.toList()
    }

    fun extract(file: File): List<DocPart> = DocZip(file, "Word document").use { z ->
        if (!z.has("word/document.xml")) throw DocRefused("That isn't a Word document Domovoi can read (no document part inside).")
        val styles = styles(z)
        val root = DocXml.parse(z.text("word/document.xml"), "Word document")
        val blocks = ArrayList<DocBlock>()
        root.child("body")?.let { walk(it, styles, blocks) }
        val rels = rels(z)
        val headers = ArrayList<String>()
        val footers = ArrayList<String>()
        for ((tag, dst) in listOf("headerReference" to headers, "footerReference" to footers)) {
            root.find(tag).forEach { ref ->
                val target = rels[ref.attr("id") ?: ""] ?: return@forEach
                val t = partText(z, target)
                if (t.isNotEmpty() && t !in dst) dst += t
            }
        }
        val fn = notes(z, "word/footnotes.xml", "footnote") + notes(z, "word/endnotes.xml", "endnote")
        DocStructure.toParts(blocks, headers, footers, fn)
    }
}
