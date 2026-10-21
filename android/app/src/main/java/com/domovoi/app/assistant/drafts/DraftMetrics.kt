package com.domovoi.app.assistant.drafts

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Advance widths from the bundled Liberation fonts, read by our own small
 * TrueType reader (`head`, `hhea`, `maxp`, `hmtx` and `cmap` formats 4 and
 * 12), the twin of the server's `domovoi/drafts/metrics.py`: line breaking
 * ([DraftLayout]) measures every character from the font's own numbers, so
 * the phone and the server break lines identically. Android's own text
 * measuring (kerning, hinting) is never asked.
 */
class DraftFont(
    val stem: String,
    val unitsPerEm: Int,
    private val cmap: Map<Int, Int>,
    private val advances: IntArray,
) {
    fun has(cp: Int): Boolean = (cmap[cp] ?: 0) != 0

    fun advance(cp: Int): Int {
        val gid = cmap[cp] ?: 0
        return if (gid >= advances.size) advances[advances.size - 1] else advances[gid]
    }

    /** The width of [text] in font units (by code point). */
    fun widthUnits(text: String): Long {
        var sum = 0L
        var i = 0
        while (i < text.length) {
            val cp = text.codePointAt(i)
            sum += advance(cp)
            i += Character.charCount(cp)
        }
        return sum
    }

    companion object {
        fun parse(stem: String, data: ByteArray): DraftFont {
            val b = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN)
            fun u16(at: Int) = b.getShort(at).toInt() and 0xFFFF
            fun s16(at: Int) = b.getShort(at).toInt()
            fun u32(at: Int) = b.getInt(at).toLong() and 0xFFFFFFFFL
            val tables = HashMap<String, Int>()
            val num = u16(4)
            for (i in 0 until num) {
                val at = 12 + 16 * i
                val tag = String(data, at, 4, Charsets.ISO_8859_1)
                tables[tag] = u32(at + 8).toInt()
            }
            val head = tables.getValue("head")
            val upem = u16(head + 18)
            val nHm = u16(tables.getValue("hhea") + 34)
            val numGlyphs = u16(tables.getValue("maxp") + 4)
            val hmtx = tables.getValue("hmtx")
            val adv = IntArray(maxOf(nHm, numGlyphs))
            for (i in 0 until nHm) adv[i] = u16(hmtx + 4 * i)
            for (i in nHm until adv.size) adv[i] = adv[nHm - 1]

            val cmapAt = tables.getValue("cmap")
            val count = u16(cmapAt + 2)
            val subs = (0 until count).map { i ->
                Triple(u16(cmapAt + 4 + 8 * i), u16(cmapAt + 6 + 8 * i), cmapAt + u32(cmapAt + 8 + 8 * i).toInt())
            }
            var chosen: Int? = null
            for (want in listOf(3 to 10, 0 to 4, 3 to 1, 0 to 3)) {
                chosen = subs.firstOrNull { it.first == want.first && it.second == want.second }?.third
                if (chosen != null) break
            }
            val at = chosen ?: error("no Unicode cmap")
            val out = HashMap<Int, Int>()
            when (u16(at)) {
                4 -> {
                    val segX2 = u16(at + 6)
                    val seg = segX2 / 2
                    val ends = at + 14
                    val starts = ends + segX2 + 2
                    val deltas = starts + segX2
                    val ranges = deltas + segX2
                    for (i in 0 until seg) {
                        val end = u16(ends + 2 * i)
                        val start = u16(starts + 2 * i)
                        val delta = s16(deltas + 2 * i)
                        val ro = u16(ranges + 2 * i)
                        for (c in start..end) {
                            if (c == 0xFFFF) continue
                            var gid: Int
                            if (ro == 0) {
                                gid = (c + delta) and 0xFFFF
                            } else {
                                gid = u16(ranges + 2 * i + ro + 2 * (c - start))
                                if (gid != 0) gid = (gid + delta) and 0xFFFF
                            }
                            if (gid != 0) out[c] = gid
                        }
                    }
                }
                12 -> {
                    val groups = u32(at + 12).toInt()
                    for (i in 0 until groups) {
                        val g = at + 16 + 12 * i
                        val start = u32(g).toInt()
                        val end = u32(g + 4).toInt()
                        val gid0 = u32(g + 8).toInt()
                        for (c in start..end) out[c] = gid0 + (c - start)
                    }
                }
                else -> error("cmap format not read")
            }
            return DraftFont(stem, upem, out, adv)
        }
    }
}
