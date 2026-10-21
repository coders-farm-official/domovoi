package com.domovoi.app.assistant.drafts

import java.text.Normalizer

/** One laid-out line (hundredths of a point; [y] is the baseline from the
 *  page's top). */
data class DraftLine(
    val style: String,
    val font: String,
    val size: Int,
    val x: Int,
    val y: Int,
    val text: String,
    val marker: String = "",
    val markerX: Int = 0,
)

data class DraftLaidOut(val width: Int, val height: Int, val pages: List<List<DraftLine>>) {
    /** The text as drawn: page by page, a line per line. */
    fun text(): String = pages.joinToString("\u000c") { p ->
        p.joinToString("\n") { (if (it.marker.isNotEmpty()) it.marker + " " else "") + it.text }
    }
}

/**
 * Pages and lines for a draft in a template, the twin of the server's
 * `domovoi/drafts/layout.py` (held to it by shared_cases.json): NFKC first
 * (the fonts have no ff/ffi/ffl ligature glyphs), our own greedy line
 * breaking in integer arithmetic with the font's own advance widths, an
 * explicit space kept at every wrap (so a text extractor reads "if I", never
 * "ifI"), and the same pagination. Both PDF renderers draw exactly these
 * lines, which is what makes a template's PDF text identical from the server
 * and the phone.
 */
object DraftLayout {
    const val SCALE = 100
    private val KEEP_WITH_NEXT = setOf("title", "heading", "strong")

    /** Points as hundredths of a point (Python's round() on values with at
     *  most two decimals). */
    fun cpt(points: Double): Int = Math.round(points * SCALE).toInt()

    /** NFKC, one space for any run of spaces and tabs, "?" for a character
     *  [font] can't draw. */
    fun prepare(text: String, font: DraftFont): String {
        val s = Normalizer.normalize(text, Normalizer.Form.NFKC)
        val out = StringBuilder()
        var space = false
        var i = 0
        while (i < s.length) {
            val cp = s.codePointAt(i)
            i += Character.charCount(cp)
            if (cp == ' '.code || cp == '\t'.code || cp == '\n'.code || cp == '\r'.code) {
                space = true
                continue
            }
            if (space && out.isNotEmpty()) out.append(' ')
            space = false
            if (font.has(cp)) out.appendCodePoint(cp) else out.append('?')
        }
        return out.toString()
    }

    fun fits(text: String, font: DraftFont, size: Int, room: Int): Boolean =
        font.widthUnits(text) * size <= room.toLong() * font.unitsPerEm

    /** [text] (prepared) broken into lines that fit [room]; every line but
     *  the last that was broken at a space ends with that space. */
    fun wrap(text: String, font: DraftFont, size: Int, room: Int): List<String> {
        val words = text.split(" ").filter { it.isNotEmpty() }
        if (words.isEmpty()) return emptyList()
        val lines = mutableListOf<String>()
        var cur = ""
        for (w in words) {
            val cand = if (cur.isEmpty()) w else "$cur $w"
            if (fits(cand, font, size, room)) {
                cur = cand
                continue
            }
            if (cur.isNotEmpty()) lines += "$cur "
            cur = w
            // A word wider than the line: cut it between letters (no space).
            while (!fits(cur, font, size, room) && cur.codePointCount(0, cur.length) > 1) {
                val cps = cur.codePoints().toArray()
                var n = cps.size - 1
                while (n > 1 && !fits(String(cps, 0, n), font, size, room)) n--
                lines += String(cps, 0, n)
                cur = String(cps, n, cps.size - n)
            }
        }
        lines += cur
        return lines
    }

    fun layout(markdown: String, template: DraftTemplate, fonts: (String) -> DraftFont): DraftLaidOut {
        val width = cpt(template.width)
        val height = cpt(template.height)
        val margin = cpt(template.margin)
        val bottom = height - margin
        val content = width - 2 * margin
        val parsed = DraftBlocks.parse(markdown)
        val pages = mutableListOf(mutableListOf<DraftLine>())
        var cursor = margin
        for ((i, block) in parsed.withIndex()) {
            val st = template.styles.getValue(block.style)
            val font = fonts(template.fontFile(st.font))
            val size = cpt(st.size)
            val leading = cpt(st.leading)
            val indent = if (block.style == "bullet") cpt(st.indent) else 0
            val room = content - indent
            val wrapped = block.lines.map { wrap(prepare(it, font), font, size, room) }
            val count = wrapped.sumOf { it.size }
            if (count == 0) continue
            var before = if (cursor > margin) cpt(st.before) else 0
            var need = before + leading * count
            if (block.style in KEEP_WITH_NEXT && i + 1 < parsed.size) {
                val nxt = template.styles.getValue(parsed[i + 1].style)
                need += cpt(st.after) + cpt(nxt.before) + cpt(nxt.leading)
            }
            if (cursor + need > bottom && cursor > margin) {
                pages += mutableListOf<DraftLine>()
                cursor = margin
                before = 0
            }
            cursor += before
            var first = true
            for (src in wrapped) {
                for (text in src) {
                    if (cursor + leading > bottom && cursor > margin) {
                        pages += mutableListOf<DraftLine>()
                        cursor = margin
                    }
                    val marker = if (block.style == "bullet" && first) st.marker else ""
                    pages.last() += DraftLine(
                        style = block.style, font = font.stem, size = size, x = margin + indent,
                        y = cursor + size, text = text, marker = marker, markerX = if (marker.isNotEmpty()) margin else 0,
                    )
                    cursor += leading
                    first = false
                }
            }
            cursor += cpt(st.after)
        }
        if (pages.size > 1 && pages.last().isEmpty()) pages.removeAt(pages.lastIndex)
        return DraftLaidOut(width, height, pages)
    }
}
