package com.domovoi.app.assistant.docs

import java.util.Locale

/**
 * The notes an attached document carries, worded once for both places (the
 * server's twin is `domovoi/doc_qa/notes.py`; DocQaSharedCasesTest holds them
 * to the same sentences).
 */
object DocNotes {
    fun pagesPhrase(pages: List<Int>): String = when {
        pages.size == 1 -> "Page ${pages[0]}"
        pages.size <= 6 -> "Pages " + pages.dropLast(1).joinToString(", ") + " and ${pages.last()}"
        else -> "${pages.size} pages (" + pages.take(3).joinToString(", ") + ", …)"
    }

    fun scannedNote(pages: List<Int>): DocNote {
        val one = pages.size == 1
        return DocNote(
            "scanned_pages",
            "${pagesPhrase(pages)} ${if (one) "is a picture" else "are pictures"} of text " +
                "(scanned), so Domovoi can't search ${if (one) "it" else "them"} yet.",
        )
    }

    fun garbledNote(pages: List<Int>): DocNote {
        val one = pages.size == 1
        return DocNote(
            "garbled_pages",
            "${pagesPhrase(pages)} ${if (one) "has" else "have"} text the file stores scrambled " +
                "(a font with no character map), so Domovoi can't search ${if (one) "it" else "them"} yet.",
        )
    }

    fun formulasNote(n: Int): DocNote {
        val one = n == 1
        return DocNote(
            "formulas_without_value",
            "${String.format(Locale.US, "%,d", n)} formula cell${if (one) "" else "s"} ${if (one) "has" else "have"} " +
                "no saved value (the file was saved without calculating ${if (one) "it" else "them"}), so " +
                "${if (one) "it reads" else "they read"} as ${if (one) "a formula" else "formulas"}, like =SUM(B2:B9).",
        )
    }

    fun longNote(words: Int): DocNote = DocNote(
        "long_document",
        "Long document (about ${DocQa.about(words)} words): each question is answered from the " +
            "passages that best match it.",
    )

    /** A document cut at the text cap: what was read. */
    fun truncatedNote(pagesRead: Int?, words: Int): DocNote = DocNote(
        "truncated",
        if (pagesRead != null) {
            "This document is very large; Domovoi read the first $pagesRead page${if (pagesRead == 1) "" else "s"}."
        } else {
            "This document is very large; Domovoi read the first part of it (about ${DocQa.about(words)} words)."
        },
    )

    const val NO_TEXT_SCANNED = "That PDF is pictures of text (a scan), so there is no text Domovoi can search yet."
    const val NO_TEXT = "That file has no text Domovoi can read."

    fun countWords(parts: List<DocPart>): Int = parts.sumOf { DocText.words(it.text).size }

    /** "lease.pdf · 30 pages · about 9,800 words". */
    fun summaryLine(name: String, kind: String, pages: Int?, words: Int, sheets: Int? = null): String {
        val bits = mutableListOf(name)
        if (kind == "pdf" && pages != null && pages > 0) bits += "$pages page${if (pages == 1) "" else "s"}"
        if (kind == "xlsx" && sheets != null && sheets > 0) bits += "$sheets sheet${if (sheets == 1) "" else "s"}"
        bits += if (words >= 1000) "about ${DocQa.about(words)} word${if (words == 1) "" else "s"}"
        else "$words word${if (words == 1) "" else "s"}"
        return bits.joinToString(" · ")
    }

    fun attachNotes(
        scanned: List<Int>,
        garbled: List<Int>,
        formulasWithoutValue: Int,
        chars: Int,
        words: Int,
        singlePass: Int,
    ): List<DocNote> = buildList {
        if (scanned.isNotEmpty()) add(scannedNote(scanned))
        if (garbled.isNotEmpty()) add(garbledNote(garbled))
        if (formulasWithoutValue > 0) add(formulasNote(formulasWithoutValue))
        if (chars > singlePass) add(longNote(words))
    }

    /** The reply to an attachment sent with no question: no model call. */
    fun attachedReply(name: String, kind: String, pages: Int?, words: Int): String {
        val what = if (kind == "pdf" && pages != null && pages > 0) "$pages page${if (pages == 1) "" else "s"}"
        else if (words >= 1000) "about ${DocQa.about(words)} words" else "$words word${if (words == 1) "" else "s"}"
        return "I've read $name ($what). Ask me anything about it."
    }
}
