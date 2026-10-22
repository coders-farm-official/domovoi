package com.domovoi.app.assistant.drafts

import com.domovoi.app.assistant.docs.DocWord
import com.domovoi.app.assistant.docs.DocXml
import java.io.ByteArrayInputStream
import java.text.Normalizer
import java.util.zip.Inflater
import java.util.zip.ZipInputStream

/** The ATS self-check's answer: [line] is what the save line says. */
data class DraftCheck(val ok: Boolean, val line: String, val read: Int, val expected: Int)

/**
 * The ATS self-check that runs after every save (documents plan part 2), the
 * twin of the server's `domovoi/drafts/ats.py`: the saved file's text read
 * back and compared, word by word and in order, with the draft. A PDF is
 * read twice: as written ([pdfStreamText]: the text in the order it was
 * drawn, a space only where a space was written, the way poppler `-raw`
 * and pdfminer read it), then with the phone's own PDF text API when it has
 * one (pdfium, which fills spaces in from gaps); a .docx from its paragraph
 * XML. A match reads `text readable · single column`; a mismatch says
 * where. (Review round 1: checked by pdfium alone, Skia's PDFs passed while
 * other readers ran words together.)
 */
object DraftAts {
    const val PASSED = "text readable · single column"

    private val WORD = DraftSpace.WORDS
    private val BULLETS = setOf("•", "-", "*", "·")

    /** The words, NFKC, soft hyphens dropped (a break hint readers keep
     *  differently; the server's `ats.words` drops them too). */
    fun words(text: String): List<String> =
        WORD.findAll(Normalizer.normalize(text, Normalizer.Form.NFKC).replace(SOFT_HYPHEN, ""))
            .map { it.value }.filter { it !in BULLETS }.toList()

    private const val SOFT_HYPHEN = "\u00AD"

    fun expectedWords(markdown: String): List<String> = words(DraftBlocks.plainText(DraftBlocks.parse(markdown)))

    fun compare(read: List<String>, want: List<String>): DraftCheck {
        if (read == want) return DraftCheck(true, PASSED, read.size, want.size)
        var i = 0
        while (i < minOf(read.size, want.size) && read[i] == want[i]) i++
        val line = if (i < want.size) {
            val got = if (i < read.size) read[i] else "nothing"
            "text check: “${want[i]}” reads as “$got” (word ${i + 1} of ${want.size})"
        } else {
            "text check: ${read.size - want.size} extra words after the end"
        }
        return DraftCheck(false, line, read.size, want.size)
    }

    /** The text of a .docx we wrote: every paragraph's text, in order. */
    fun docxText(data: ByteArray): String {
        var xml: String? = null
        ZipInputStream(ByteArrayInputStream(data)).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                if (e.name == "word/document.xml") {
                    xml = z.readBytes().toString(Charsets.UTF_8)
                    break
                }
            }
        }
        val root = DocXml.parse(xml ?: error("no document part"), "Word document")
        return root.find("p").joinToString("\n") { DocWord.paraText(it) }
    }

    fun checkDocx(data: ByteArray, markdown: String): DraftCheck = runCatching {
        compare(words(docxText(data)), expectedWords(markdown))
    }.getOrElse { DraftCheck(false, "text check: the saved file couldn't be read back", 0, expectedWords(markdown).size) }

    fun checkText(text: String, markdown: String): DraftCheck = compare(words(text), expectedWords(markdown))

    /** The PDF check: as written first, then the phone's reader's
     *  [readerText] when there is one; the first that fails is said. */
    fun checkPdf(data: ByteArray, markdown: String, readerText: String?): DraftCheck {
        val raw = runCatching { pdfStreamText(data) }.getOrNull()
            ?: return DraftCheck(false, "text check: the saved file couldn't be read back", 0, expectedWords(markdown).size)
        val asWritten = checkText(raw, markdown)
        if (!asWritten.ok || readerText == null) return asWritten
        return checkText(readerText, markdown)
    }

    private val OBJ = Regex("(\\d+) 0 obj\\s*")
    private val REF = Regex("/(\\w+) (\\d+) 0 R")
    private val TOKENS = Regex(
        "/([^\\s/<>\\[\\]()]+)\\s+[-\\d.]+\\s+Tf|<([0-9A-Fa-f\\s]*)>\\s*Tj|([-\\d.]+)\\s+([-\\d.]+)\\s+Tm|(Td|TD|T\\*)(?![A-Za-z])",
    )
    private val BFCHAR = Regex("<([0-9A-Fa-f]+)>\\s*<([0-9A-Fa-f]*)>")
    private val BFRANGE = Regex("<([0-9A-Fa-f]+)>\\s*<([0-9A-Fa-f]+)>\\s*<([0-9A-Fa-f]+)>")

    /**
     * A PDF's text as it was written: page by page, each text line's `Tj`
     * strings turned back into characters through the font's ToUnicode
     * map, a new line at every text position. No space is guessed from a
     * gap between glyphs, so a writer that leaves spaces out (or draws
     * glyph by glyph) fails here. Reads the PDFs [DraftPdfWriter] writes:
     * Flate streams, Identity-H fonts with ToUnicode maps, `Tj` strings.
     */
    fun pdfStreamText(data: ByteArray): String {
        val s = String(data, Charsets.ISO_8859_1)
        val dicts = HashMap<Int, String>()
        val streams = HashMap<Int, ByteArray>()
        for (m in OBJ.findAll(s)) {
            val id = m.groupValues[1].toInt()
            val start = m.range.last + 1
            val st = s.indexOf("stream", start).let { if (it < 0) Int.MAX_VALUE else it }
            val eo = s.indexOf("endobj", start).let { if (it < 0) s.length else it }
            if (st < eo) {
                val dict = s.substring(start, st)
                dicts[id] = dict
                var from = st + "stream".length
                if (s.startsWith("\r\n", from)) from += 2 else if (s.startsWith("\n", from)) from += 1
                val len = Regex("/Length (\\d+)").find(dict)?.groupValues?.get(1)?.toInt() ?: continue
                val raw = data.copyOfRange(from, from + len)
                streams[id] = if (dict.contains("/FlateDecode")) inflate(raw) else raw
            } else {
                dicts[id] = s.substring(start, eo)
            }
        }
        fun ref(dict: String, key: String): Int? = Regex("/$key (\\d+) 0 R").find(dict)?.groupValues?.get(1)?.toInt()
        val root = Regex("/Root (\\d+) 0 R").findAll(s).lastOrNull()?.groupValues?.get(1)?.toInt() ?: error("no catalog")
        val pagesId = ref(dicts.getValue(root), "Pages") ?: error("no pages")
        val kids = Regex("/Kids \\[([^\\]]*)]").find(dicts.getValue(pagesId))?.groupValues?.get(1) ?: error("no kids")
        val maps = HashMap<Int, Map<Int, String>>()
        val out = ArrayList<String>()
        for (k in Regex("(\\d+) 0 R").findAll(kids)) {
            val page = dicts.getValue(k.groupValues[1].toInt())
            val fontBlock = page.substringAfter("/Font <<", "").substringBefore(">>")
            val fonts = REF.findAll(fontBlock).associate { it.groupValues[1] to it.groupValues[2].toInt() }
            val content = String(streams.getValue(ref(page, "Contents") ?: error("no contents")), Charsets.ISO_8859_1)
            var cmap: Map<Int, String> = emptyMap()
            var baseline: String? = null
            var moved = false
            val lines = ArrayList<String>()
            val line = StringBuilder()
            fun endLine() {
                if (line.isNotEmpty()) lines += line.toString()
                line.setLength(0)
            }
            for (t in TOKENS.findAll(content)) {
                val (font, hex, _, y, op) = t.destructured
                when {
                    font.isNotEmpty() -> {
                        val fid = fonts[font] ?: error("no font $font")
                        cmap = maps.getOrPut(fid) {
                            val tu = ref(dicts.getValue(fid), "ToUnicode") ?: error("no ToUnicode for $font")
                            toUnicode(String(streams.getValue(tu), Charsets.ISO_8859_1))
                        }
                    }
                    // A new baseline is a new line (a bullet and its text
                    // share one).
                    y.isNotEmpty() -> {
                        if (y != baseline) endLine()
                        baseline = y
                        moved = false
                    }
                    // To the next line: what's drawn after it (without a
                    // new position first) starts one.
                    op.isNotEmpty() -> moved = true
                    else -> {
                        if (moved) {
                            endLine()
                            moved = false
                        }
                        val h = hex.filter { !it.isWhitespace() }
                        var i = 0
                        while (i + 4 <= h.length) {
                            line.append(cmap[h.substring(i, i + 4).toInt(16)] ?: "�")
                            i += 4
                        }
                    }
                }
            }
            endLine()
            out += lines.joinToString("\n")
        }
        return out.joinToString("\n")
    }

    private fun toUnicode(cmap: String): Map<Int, String> {
        val out = HashMap<Int, String>()
        fun utf16(hex: String) = String(CharArray(hex.length / 4) { hex.substring(4 * it, 4 * it + 4).toInt(16).toChar() })
        for (block in Regex("beginbfchar(.*?)endbfchar", RegexOption.DOT_MATCHES_ALL).findAll(cmap)) {
            for (m in BFCHAR.findAll(block.groupValues[1])) out[m.groupValues[1].toInt(16)] = utf16(m.groupValues[2])
        }
        for (block in Regex("beginbfrange(.*?)endbfrange", RegexOption.DOT_MATCHES_ALL).findAll(cmap)) {
            for (m in BFRANGE.findAll(block.groupValues[1])) {
                val lo = m.groupValues[1].toInt(16)
                val hi = m.groupValues[2].toInt(16)
                val first = m.groupValues[3].toInt(16)
                for (c in lo..hi) out[c] = String(Character.toChars(first + c - lo))
            }
        }
        return out
    }

    private fun inflate(data: ByteArray): ByteArray {
        val inf = Inflater()
        try {
            inf.setInput(data)
            val out = java.io.ByteArrayOutputStream()
            val chunk = ByteArray(8192)
            while (!inf.finished()) {
                val k = inf.inflate(chunk)
                if (k == 0 && (inf.needsInput() || inf.needsDictionary())) break
                out.write(chunk, 0, k)
                if (out.size() > 32 shl 20) error("too big")
            }
            return out.toByteArray()
        } finally {
            inf.end()
        }
    }
}

