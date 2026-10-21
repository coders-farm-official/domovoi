package com.domovoi.app.assistant.docs

/** A block of a document with headings: "h" (with [level]), "p" (a
 *  paragraph or list item) or "table" (markdown rows). */
data class DocBlock(val kind: String, val text: String, val level: Int = 0)

/**
 * Blocks to parts, for the formats with headings (.docx, .odt, .md, .txt),
 * by the server's rules (`domovoi/doc_extract/structure.py`): a part is a
 * section under a heading ("Section: Deposit"); a section longer than
 * [PART_CHARS] is split between paragraphs ("Section: Deposit (para 3)");
 * text before the first heading is "Opening"; a document with no headings
 * is labelled by paragraph ("para 14"). Headers, footers and footnotes are
 * parts of their own.
 */
object DocStructure {
    const val PART_CHARS = 4000
    private const val LABEL_MAX = 60

    private fun short(s: String): String {
        val one = DocText.pySplit(s).joinToString(" ")
        return if (one.length <= LABEL_MAX) one else one.take(LABEL_MAX - 1).trimEnd() + "…"
    }

    fun toParts(
        blocks: List<DocBlock>,
        headers: List<String> = emptyList(),
        footers: List<String> = emptyList(),
        footnotes: List<String> = emptyList(),
        partChars: Int = PART_CHARS,
    ): List<DocPart> {
        val hasHeadings = blocks.any { it.kind == "h" && it.text.isNotBlank() }
        data class Section(val head: String?, val line: String, val paras: List<String>)
        val sections = ArrayList<Section>()
        var curHead: String? = null
        var curLine = ""
        var cur = ArrayList<String>()
        for (b in blocks) {
            val t = b.text.trim()
            if (t.isEmpty()) continue
            if (b.kind == "h") {
                if (cur.isNotEmpty() || curHead != null) sections += Section(curHead, curLine, cur)
                curHead = t
                curLine = "#".repeat(b.level.coerceIn(1, 6)) + " " + t
                cur = ArrayList()
            } else {
                cur += t
            }
        }
        if (cur.isNotEmpty() || curHead != null) sections += Section(curHead, curLine, cur)

        val parts = ArrayList<DocPart>()
        if (headers.isNotEmpty()) parts += DocPart("page header", headers.joinToString("\n"))
        var paraNo = 0
        for (s in sections) {
            val base: String? = when {
                s.head != null -> "Section: ${short(s.head)}"
                hasHeadings -> "Opening"
                else -> null
            }
            var chunk = ArrayList<String>().apply { if (s.line.isNotEmpty()) add(s.line) }
            var size = s.line.length
            var inChunk = 0
            var startK = 1
            var startDoc = paraNo + 1
            var first = true
            s.paras.forEachIndexed { i, p ->
                val k = i + 1
                paraNo++
                if (inChunk > 0 && size + p.length > partChars) {
                    parts += part(base, startK, startDoc, chunk, first)
                    first = false
                    chunk = ArrayList()
                    size = 0
                    inChunk = 0
                    startK = k
                    startDoc = paraNo
                }
                chunk += p
                size += p.length + 2
                inChunk++
            }
            if (chunk.isNotEmpty()) parts += part(base, startK, startDoc, chunk, first)
        }
        if (footers.isNotEmpty()) parts += DocPart("page footer", footers.joinToString("\n"))
        if (footnotes.isNotEmpty()) {
            parts += DocPart("footnotes", footnotes.mapIndexed { i, t -> "${i + 1}. $t" }.joinToString("\n"))
        }
        return parts
    }

    private fun part(base: String?, startK: Int, startDoc: Int, chunk: List<String>, first: Boolean): DocPart {
        val label = when {
            base == null -> "para $startDoc"
            first -> base
            else -> "$base (para $startK)"
        }
        return DocPart(label, chunk.joinToString("\n\n"))
    }

    /** Rows as a markdown table (the first row is the header). */
    fun mdTable(rowsIn: List<List<String>>): String {
        val rows = rowsIn.map { r -> r.map { DocText.pySplit(it.replace("|", "/")).joinToString(" ") } }
            .filter { r -> r.any { it.isNotBlank() } }
        if (rows.isEmpty()) return ""
        val width = rows.maxOf { it.size }
        val padded = rows.map { it + List(width - it.size) { "" } }
        val out = ArrayList<String>()
        out += "| " + padded[0].joinToString(" | ") + " |"
        out += "|" + "---|".repeat(width)
        padded.drop(1).forEach { out += "| " + it.joinToString(" | ") + " |" }
        return out.joinToString("\n")
    }

    fun mdRow(r: List<String>, width: Int): String {
        val cells = r.map { DocText.pySplit(it.replace("|", "/")).joinToString(" ") } + List((width - r.size).coerceAtLeast(0)) { "" }
        return "| " + cells.joinToString(" | ") + " |"
    }
}
