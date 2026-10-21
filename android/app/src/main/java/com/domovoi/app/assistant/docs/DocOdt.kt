package com.domovoi.app.assistant.docs

import java.io.File

/**
 * OpenDocument text (.odt), as the server reads it
 * (`domovoi/doc_extract/odf.py`): `text:h` headings by outline level,
 * `text:p`, `text:list` items, `table:table` as markdown rows, `text:s`
 * (runs of spaces, at most 1,000), `text:tab`, `text:line-break`, footnotes
 * (`text:note`) gathered apart; tracked deletions (`text:tracked-changes`)
 * are not read; headers and footers from styles.xml's master pages.
 */
object DocOdt {
    private val SKIP = setOf("text:tracked-changes", "text:note-citation", "office:annotation", "text:changed-region", "text:deletion")

    fun inline(el: XmlEl, notes: MutableList<String>): String {
        val sb = StringBuilder()
        for (c in el.children) {
            if (c is String) {
                sb.append(c)
                continue
            }
            val ch = c as XmlEl
            when {
                ch.qname in SKIP -> {}
                ch.qname == "text:s" -> sb.append(" ".repeat((ch.attr("c")?.toIntOrNull() ?: 1).coerceIn(1, 1000)))
                ch.qname == "text:tab" -> sb.append('\t')
                ch.qname == "text:line-break" -> sb.append('\n')
                ch.qname == "text:note" -> ch.child("note-body")?.let { body ->
                    notes += body.find("p").map { DocText.pySplit(inline(it, notes)).joinToString(" ") }.joinToString(" ").trim()
                }
                else -> sb.append(inline(ch, notes))
            }
        }
        return sb.toString()
    }

    private fun blocks(el: XmlEl, notes: MutableList<String>, out: MutableList<DocBlock>) {
        for (ch in el.elements()) {
            when (ch.qname) {
                "text:h" -> out += DocBlock("h", DocText.clean(inline(ch, notes)), ch.attr("outline-level")?.toIntOrNull() ?: 1)
                "text:p" -> out += DocBlock("p", DocText.clean(inline(ch, notes)))
                "text:list" -> ch.find("list-item").forEach { item ->
                    item.elements().filter { it.qname == "text:p" || it.qname == "text:h" }.forEach { p ->
                        out += DocBlock("p", "- " + DocText.clean(inline(p, notes)).trim())
                    }
                }
                "table:table" -> {
                    val rows = ch.find("table-row").map { r ->
                        r.all("table-cell").map { c ->
                            c.find("p").map { DocText.pySplit(DocText.clean(inline(it, notes))).joinToString(" ") }.joinToString(" ").trim()
                        }
                    }.toList()
                    DocStructure.mdTable(rows).takeIf { it.isNotEmpty() }?.let { out += DocBlock("table", it) }
                }
                "text:section", "table:table-header-rows", "text:index-body", "text:table-of-content" -> blocks(ch, notes, out)
            }
        }
    }

    fun extract(file: File): List<DocPart> = DocZip(file, "OpenDocument file").use { z ->
        if (!z.has("content.xml")) throw DocRefused("That isn't an OpenDocument text Domovoi can read (no content part inside).")
        val content = DocXml.parse(z.text("content.xml"), "OpenDocument file")
        val styles = if (z.has("styles.xml")) DocXml.parse(z.text("styles.xml"), "OpenDocument file") else null
        val body = content.child("body")?.child("text")
            ?: throw DocRefused("That OpenDocument file has no text (is it a spreadsheet or a drawing?).")
        val notes = ArrayList<String>()
        val out = ArrayList<DocBlock>()
        blocks(body, notes, out)
        val headers = ArrayList<String>()
        val footers = ArrayList<String>()
        styles?.find("master-page")?.forEach { mp ->
            for ((tag, dst) in listOf("header" to headers, "footer" to footers)) {
                mp.all(tag).forEach { h ->
                    val t = DocText.clean(h.find("p").map { inline(it, ArrayList()) }.joinToString("\n")).trim()
                    if (t.isNotEmpty() && t !in dst) dst += t
                }
            }
        }
        DocStructure.toParts(out, headers, footers, notes.filter { it.isNotEmpty() }.map { DocText.clean(it) })
    }
}
