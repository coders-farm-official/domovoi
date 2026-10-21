package com.domovoi.app.assistant.docs

import kotlin.math.ceil
import kotlin.math.roundToInt

/** A text block on a PDF page, in page points, top-left origin. */
data class PdfBlock(val text: String, val left: Float, val top: Float, val right: Float, val bottom: Float) {
    val width: Float get() = right - left
}

/** One page as the phone's PDF text API cut it: its blocks (in the API's
 *  order), the page's width, and how many images it has. */
data class PdfPageText(val blocks: List<PdfBlock>, val width: Float, val images: Int)

/** Reads a PDF's text page by page; on the phone, the PDF text API
 *  (PhonePdfText). Throws [DocRefused] for a file it can't open. */
fun interface PdfTextReader {
    fun read(file: java.io.File, maxPages: Int): List<PdfPageText>
}

/** This phone has no PDF text API (Android 11 and older, or 12-14 without
 *  the PDF module update): the chat offers the home server instead. */
class NoPdfTextApi : Exception(PDF_NO_API)

const val PDF_NO_API = "This phone can't read the text of PDFs itself (that needs Android 15, or Android 12-14 with the PDF update)."

/**
 * PDF pages to parts, plain Kotlin (the Android half is PhonePdfText): the
 * D0 column sort with its running-text guard (a page reads as two columns
 * only when both sides look like running text, so a table is not taken for
 * columns), running headers and footers kept once (the server's rule: a
 * line, digits ignored, in the top or bottom two lines of at least 60% of
 * the text pages), pages that need vision found (fewer than 20 visible
 * characters on a page with an image, or more than half the words
 * gibberish: the server's `domovoi/doc_extract/pdf.py` rules), and the text
 * NFKC'd. Parts are labelled "p. N", "page header", "page footer".
 */
object DocPdf {
    const val SCAN_VISIBLE_CHARS = 20
    const val GIBBERISH_SHARE = 0.5
    private const val MIN_COLUMN_BLOCKS = 8
    private const val LINE_SLOP = 3f
    private val REAL = Regex("""^[A-Za-z][a-z]*[A-Za-z]?$|^\d[\d,.:/$%-]*$|^[A-Z]{2,}$""")
    private val VOWEL = Regex("[aeiouyAEIOUY]")

    fun gibberishShare(text: String): Double {
        val toks = DocText.pySplit(text).map { it.trim('.', ',', ';', ':', '(', ')', '"', '\'', '“', '”', '‘', '’', '!', '?') }
            .filter { it.isNotEmpty() }
        if (toks.size < 15) return 0.0
        val real = toks.count { t -> REAL.matches(t) && (VOWEL.containsMatchIn(t) || t[0].isDigit() || (t.any { it.isLetter() } && t.all { !it.isLetter() || it.isUpperCase() })) }
        return 1 - real.toDouble() / toks.size
    }

    /** The API's contents as blocks: a content whose lines match its bounds
     *  one to one gives a block per line; otherwise one block over the union
     *  of its bounds (D0Text.blocks). [bounds] are (left, top, right, bottom). */
    fun blocks(contents: List<Pair<String, List<FloatArray>>>): List<PdfBlock> = contents.flatMap { (text, bounds) ->
        val lines = text.split('\n').filter { it.isNotBlank() }
        when {
            bounds.isEmpty() -> listOf(PdfBlock(text, 0f, 0f, 0f, 0f))
            lines.size == bounds.size -> lines.zip(bounds) { l, b -> PdfBlock(l, b[0], b[1], b[2], b[3]) }
            else -> listOf(PdfBlock(text, bounds.minOf { it[0] }, bounds.minOf { it[1] }, bounds.maxOf { it[2] }, bounds.maxOf { it[3] }))
        }
    }

    /** A page's blocks in reading order, and how many columns it has (D0). */
    fun columnSort(blocks: List<PdfBlock>, pageWidth: Float): Pair<List<String>, Int> {
        if (blocks.isEmpty()) return emptyList<String>() to 1
        val mid = pageWidth / 2
        val gutter = pageWidth * 0.02f
        val left = blocks.filter { it.right <= mid + gutter }
        val right = blocks.filter { it.left >= mid - gutter }
        val wide = blocks.filter { it !in left && it !in right }
        fun medianWidth(b: List<PdfBlock>) = b.map { it.width }.sorted().let { if (it.isEmpty()) 0f else it[it.size / 2] }
        val two = left.size >= MIN_COLUMN_BLOCKS && right.size >= MIN_COLUMN_BLOCKS &&
            medianWidth(left) > pageWidth / 4 && medianWidth(right) > pageWidth / 4
        fun reading(b: List<PdfBlock>) = b.sortedWith(compareBy<PdfBlock>({ (it.top / LINE_SLOP).roundToInt() }, { it.left }))
        fun lines(b: List<PdfBlock>) = b.flatMap { it.text.split('\n') }.map { it.trimEnd() }
        if (!two) return lines(reading(blocks)) to 1
        val colTop = minOf(left.minOf { it.top }, right.minOf { it.top })
        val above = wide.filter { it.top < colTop }
        val below = wide - above.toSet()
        return lines(reading(above) + reading(left) + reading(right) + reading(below)) to 2
    }

    private fun hfNorm(s: String): String =
        DocText.pySplit(s.lowercase()).joinToString(" ").replace(Regex("\\d+"), "#").trim()

    /** (pages, headers, footers): running lines dropped and kept once. */
    fun stripRunning(pages: List<List<String>>, zone: Int = 2): Triple<List<List<String>>, List<String>, List<String>> {
        val n = pages.count { it.isNotEmpty() }
        if (n < 2) return Triple(pages, emptyList(), emptyList())
        val need = maxOf(2, ceil(0.6 * n).toInt())
        val tops = HashMap<String, Int>()
        val bots = HashMap<String, Int>()
        for (lines in pages) {
            lines.take(zone).map(::hfNorm).toSet().forEach { tops[it] = (tops[it] ?: 0) + 1 }
            lines.takeLast(zone).map(::hfNorm).toSet().forEach { bots[it] = (bots[it] ?: 0) + 1 }
        }
        val rt = tops.filter { it.value >= need && it.key.isNotEmpty() }.keys
        val rb = bots.filter { it.value >= need && it.key.isNotEmpty() }.keys
        val headers = ArrayList<String>()
        val footers = ArrayList<String>()
        val out = pages.map { lines ->
            val keep = lines.toMutableList()
            repeat(minOf(zone, keep.size)) {
                if (keep.isNotEmpty() && hfNorm(keep[0]) in rt) {
                    val h = keep.removeAt(0)
                    if (headers.none { hfNorm(it) == hfNorm(h) }) headers += h
                }
            }
            repeat(minOf(zone, keep.size)) {
                if (keep.isNotEmpty() && hfNorm(keep.last()) in rb) {
                    val f = keep.removeAt(keep.lastIndex)
                    if (footers.none { hfNorm(it) == hfNorm(f) }) footers += f
                }
            }
            keep.toList()
        }
        return Triple(out, headers, footers)
    }

    data class Result(
        val pages: Int,
        val parts: List<DocPart>,
        val scanned: List<Int>,
        val garbled: List<Int>,
        val twoColumn: List<Int>,
    )

    fun parts(pages: List<PdfPageText>): Result {
        val texts = ArrayList<List<String>>()
        val scanned = ArrayList<Int>()
        val garbled = ArrayList<Int>()
        val twoCol = ArrayList<Int>()
        pages.forEachIndexed { i, page ->
            DocCancel.check()
            val (raw, cols) = columnSort(page.blocks, page.width)
            var lines = raw.map { DocText.nfkc(it).trim() }.filter { it.isNotEmpty() }
            val text = lines.joinToString(" ")
            val visible = text.count { !it.isWhitespace() }
            when {
                visible < SCAN_VISIBLE_CHARS && page.images > 0 -> { scanned += i + 1; lines = emptyList() }
                gibberishShare(text) > GIBBERISH_SHARE -> { garbled += i + 1; lines = emptyList() }
                cols == 2 -> twoCol += i + 1
            }
            texts += lines
        }
        val (stripped, headers, footers) = stripRunning(texts)
        val parts = ArrayList<DocPart>()
        if (headers.isNotEmpty()) parts += DocPart("page header", headers.joinToString("\n"))
        stripped.forEachIndexed { i, lines ->
            if (lines.isNotEmpty()) parts += DocPart("p. ${i + 1}", lines.joinToString("\n"), page = i + 1)
        }
        if (footers.isNotEmpty()) parts += DocPart("page footer", footers.joinToString("\n"))
        return Result(pages.size, parts, scanned, garbled, twoCol)
    }

    /** The blocks of [page] that hold [sentence]'s words, for the page
     *  sheet's box: the ones sharing most of its terms. Empty when none. */
    fun boxesFor(page: PdfPageText, sentence: String): List<PdfBlock> {
        val want = DocText.tokens(sentence).toSet()
        if (want.isEmpty()) return emptyList()
        val scored = page.blocks.map { it to DocText.tokens(it.text).count { t -> t in want } }
        val best = scored.maxOfOrNull { it.second } ?: 0
        if (best == 0) return emptyList()
        return scored.filter { it.second * 2 >= best && it.second > 0 }.map { it.first }
    }
}
