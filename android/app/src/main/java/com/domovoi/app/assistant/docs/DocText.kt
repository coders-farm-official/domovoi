package com.domovoi.app.assistant.docs

import java.text.Normalizer

/**
 * Passages and words: how a document's parts become something to search
 * (documents plan, part 3). The Kotlin twin of the server's
 * `domovoi/doc_qa/text.py`; both are held to `domovoi/doc_qa/shared_cases.json`
 * by DocQaSharedCasesTest, so a rule changed on one side fails the other's
 * tests until it changes there too. Plain Kotlin, no Android.
 */
object DocText {
    /** About 300 tokens: English runs near 4 characters a token. */
    const val PASSAGE_CHARS = 1200

    /** A "sentence" longer than this is cut at a space. */
    const val SENTENCE_MAX_CHARS = 1800

    /** Words that carry no meaning for the search (the server's list). */
    val STOP_WORDS: Set<String> = """
        a an and are as at be been but by can could did do does for from had has have
        he her him his how i if in into is it its me my no not of on or our she so
        than that the their them then there these they this to too us was we were
        what when where which who whom why will with would you your about any all
        also am do does just may might must shall should some such very
        s t d ll re ve m
    """.trim().split(Regex("\\s+")).toSet()

    /** NFKC (ligatures, full-width letters, non-breaking spaces become their
     *  plain forms), less soft hyphens: what extracted PDF text keeps. */
    fun nfkc(s: String?): String = Normalizer.normalize(s ?: "", Normalizer.Form.NFKC).replace("­", "")

    /** Text as a file wrote it, less soft hyphens and NULs (Word, ODT,
     *  sheets, text). */
    fun clean(s: String?): String = (s ?: "").replace("­", "").replace("\u0000", "")

    /** [nfkc] with curly quotes and dashes made plain: text as it is
     *  compared, never as it is shown. */
    fun normalize(s: String?): String = nfkc(s)
        .replace('‘', '\'').replace('’', '\'').replace('“', '"')
        .replace('”', '"').replace('–', '-').replace('—', '-')

    /** Python's `str.split()`: runs of whitespace, no empty pieces. */
    fun pySplit(s: String): List<String> {
        val out = ArrayList<String>()
        val cur = StringBuilder()
        for (ch in s) {
            if (ch.isWhitespace()) {
                if (cur.isNotEmpty()) { out += cur.toString(); cur.setLength(0) }
            } else {
                cur.append(ch)
            }
        }
        if (cur.isNotEmpty()) out += cur.toString()
        return out
    }

    private fun isAlpha(w: String) = w.isNotEmpty() && w.all { it.isLetter() }

    internal fun stem(word: String): String {
        var w = word
        if (w.length <= 3 || !isAlpha(w)) return w
        if (w.endsWith("ies") && w.length > 4) {
            w = w.dropLast(3) + "y"
        } else if (w.endsWith("sses")) {
            w = w.dropLast(2)
        } else if (w.endsWith("s") && !w.endsWith("ss") && !w.endsWith("us") && !w.endsWith("is")) {
            w = w.dropLast(1)
        }
        if (w.endsWith("ing") && w.length > 5) {
            w = w.dropLast(3)
        } else if (w.endsWith("ed") && w.length > 4) {
            w = w.dropLast(2)
        } else if (w.endsWith("ly") && w.length > 5) {
            w = w.dropLast(2)
        }
        if (w.length > 3 && w[w.length - 1] == w[w.length - 2] && w.last() !in "lsz" && !w.last().isDigit()) {
            w = w.dropLast(1)
        }
        if (w.endsWith("e") && w.length > 3) w = w.dropLast(1)
        return w
    }

    /** Lowercase words: runs of letters or digits; a digit run keeps its
     *  inner "," "." ":" "/" ("1,200.50", "3/4", "10:30"). */
    fun words(text: String?): List<String> {
        val s = normalize(text).lowercase()
        val out = ArrayList<String>()
        val cur = StringBuilder()
        val n = s.length
        for (i in 0 until n) {
            val ch = s[i]
            if (ch.isLetterOrDigit()) {
                cur.append(ch)
                continue
            }
            if (ch in ",.:/" && cur.isNotEmpty() && cur.last().isDigit() && i + 1 < n && s[i + 1].isDigit()) {
                cur.append(ch)
                continue
            }
            if (cur.isNotEmpty()) { out += cur.toString(); cur.setLength(0) }
        }
        if (cur.isNotEmpty()) out += cur.toString()
        return out
    }

    /** The index's terms: [words] less stop words, stemmed. */
    fun tokens(text: String?): List<String> = words(text).filter { it !in STOP_WORDS }.map { stem(it) }

    /** [text] cut into sentences, by the server's rules (text.py `sentences`). */
    fun sentences(text: String?): List<String> {
        val out = ArrayList<String>()
        for (raw in (text ?: "").replace("\r", "").split("\n")) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            var start = 0
            var i = 0
            val n = line.length
            while (i < n) {
                val ch = line[i]
                if (ch in ".!?" && i + 2 < n && line[i + 1] == ' ') {
                    val nxt = line[i + 2]
                    if (nxt.isUpperCase() || nxt.isDigit() || nxt in "\"'([“‘") {
                        out += line.substring(start, i + 1).trim()
                        start = i + 2
                        i += 2
                        continue
                    }
                }
                i++
            }
            val rest = line.substring(start).trim()
            if (rest.isNotEmpty()) out += rest
        }
        val capped = ArrayList<String>()
        for (sentence in out) {
            var s = sentence
            while (s.length > SENTENCE_MAX_CHARS) {
                var cut = s.lastIndexOf(' ', SENTENCE_MAX_CHARS - 1)
                if (cut <= 0) cut = SENTENCE_MAX_CHARS
                capped += s.substring(0, cut).trim()
                s = s.substring(cut).trim()
            }
            if (s.isNotEmpty()) capped += s
        }
        return capped
    }

    /** Each part's text in passages of about [target] characters, on
     *  sentence boundaries, with one sentence of overlap (text.py). */
    fun cutPassages(parts: List<DocPart>, doc: Int = 0, target: Int = PASSAGE_CHARS): List<Passage> {
        val out = ArrayList<Passage>()
        parts.forEachIndexed { pi, part ->
            val head = part.head.trim()
            val sents = sentences(part.text)
            if (sents.isEmpty()) return@forEachIndexed
            var cur = ArrayList<String>()
            var size = 0
            var fresh = 0
            for (s in sents) {
                if (cur.isNotEmpty() && fresh > 0 && size + s.length + 1 > target) {
                    out += passage(doc, pi, part.label, cur, head)
                    val last = cur.last()
                    cur = if (last.length < target / 2) arrayListOf(last) else ArrayList()
                    size = cur.sumOf { it.length + 1 }
                    fresh = 0
                }
                cur += s
                size += s.length + 1
                fresh++
            }
            if (cur.isNotEmpty() && fresh > 0) out += passage(doc, pi, part.label, cur, head)
        }
        return out
    }

    private fun passage(doc: Int, part: Int, label: String, sents: List<String>, head: String): Passage {
        val text = if (sents.any { it.startsWith("|") }) sents.joinToString("\n") else sents.joinToString(" ")
        val p = Passage(doc, part, label, text, head)
        return p.copy(terms = tokens(p.shown()))
    }
}

/** One part of a document: what an answer cites ([label]) and its text;
 *  [head] (a table's header row) is repeated above every passage. [page]:
 *  a PDF page's number. */
@kotlinx.serialization.Serializable
data class DocPart(
    val label: String = "",
    val text: String = "",
    val head: String = "",
    val page: Int? = null,
)

/** One searchable piece of a document. */
data class Passage(
    val doc: Int,
    val part: Int,
    val label: String,
    val text: String,
    val head: String = "",
    val terms: List<String> = emptyList(),
) {
    fun shown(): String = if (head.isNotEmpty()) "$head\n$text" else text
}
