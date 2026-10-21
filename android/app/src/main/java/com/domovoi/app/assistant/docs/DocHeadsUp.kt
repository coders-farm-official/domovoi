package com.domovoi.app.assistant.docs

/**
 * The seams for later milestones, where they meet the phone's code (the
 * server's twin is `domovoi/doc_qa/longdoc.py`):
 *
 * - **The heads-up (D3)**: [check] is asked before a document reply is
 *   written and returns the card to show; D3 fills it in from the measured
 *   weak-spots table (`chat-weak-spots.json`). D1 shows none.
 * - **Map/reduce (D2)**: a summary of a document longer than one pass
 *   answers with [DocQa.summaryTooLongText] and no model call until then;
 *   [mapChunks] already groups parts into the ~3,500-token pieces a map step
 *   reads. "Summarize the whole thing" and "search the whole document" are
 *   not offered before they exist (no control that can't do what it says).
 */
object DocHeadsUp {
    /** A heads-up card: what it says and its options. */
    data class Card(val kind: String, val text: String, val options: List<String>)

    fun check(mode: String, docs: List<QaDoc>, question: String): Card? = null

    const val MAP_CHUNK_TOKENS = 3500
    private const val CHARS_PER_TOKEN = 4.0

    fun mapChunks(parts: List<DocPart>, tokens: Int = MAP_CHUNK_TOKENS): List<List<Int>> {
        val limit = tokens * CHARS_PER_TOKEN
        val out = ArrayList<List<Int>>()
        var cur = ArrayList<Int>()
        var size = 0.0
        parts.forEachIndexed { i, p ->
            val n = p.text.length + p.head.length
            if (cur.isNotEmpty() && size + n > limit) {
                out += cur
                cur = ArrayList()
                size = 0.0
            }
            cur += i
            size += n
        }
        if (cur.isNotEmpty()) out += cur
        return out
    }
}
