package com.domovoi.app.assistant

/**
 * Cuts a streamed reply into sentences as it arrives, so speech can start on
 * the first sentence while the model is still writing the rest, and strips
 * Markdown from each one so the voice never reads out asterisks or links.
 *
 * A sentence ends at `.`, `!`, `?` or `…` (with any closing quotes or
 * brackets) followed by whitespace, or at a line break. Not at the period of
 * an abbreviation ("Dr.", "e.g."), an initial ("J."), or a list number at the
 * start of a line ("1."); a decimal ("3.5") never ends one because no
 * whitespace follows its period. A terminator at the very end of the text so
 * far is held until the next delta (or [finish]) shows what follows it.
 */
class SentenceSplitter {
    private val pending = StringBuilder()

    /** Adds one streamed delta; returns the sentences it completed. */
    fun push(delta: String): List<String> {
        pending.append(delta)
        val out = mutableListOf<String>()
        while (true) {
            val end = nextBoundary(pending) ?: break
            emit(pending.substring(0, end), out)
            pending.delete(0, end)
        }
        return out
    }

    /** The end of the stream: whatever is left is the last sentence. */
    fun finish(): List<String> {
        val out = mutableListOf<String>()
        emit(pending.toString(), out)
        pending.setLength(0)
        return out
    }

    /** Inside a fenced code block, which is never read aloud. */
    private var inFence = false

    private fun emit(raw: String, out: MutableList<String>) {
        if (raw.trim().startsWith("```")) {
            inFence = !inFence
            return
        }
        if (inFence) return
        val text = speakable(raw)
        if (text.isNotBlank()) out += text
    }

    companion object {
        private const val TERMINATORS = ".!?…"
        private const val CLOSERS = "\"')]”’*_"

        private val ABBREVIATIONS = setOf(
            "mr", "mrs", "ms", "dr", "st", "sr", "jr", "prof", "vs", "etc", "e.g", "i.e",
            "approx", "no", "fig", "inc", "ltd", "co", "mt", "ft", "min", "max", "est",
        )

        /** Index just past the first complete sentence in [text], or null. */
        internal fun nextBoundary(text: CharSequence): Int? {
            var i = 0
            while (i < text.length) {
                val c = text[i]
                if (c == '\n') {
                    if (text.substring(0, i).isNotBlank()) return i + 1
                } else if (c in TERMINATORS) {
                    var j = i + 1
                    while (j < text.length && (text[j] in TERMINATORS || text[j] in CLOSERS)) j++
                    if (j == text.length) return null          // wait: what follows decides
                    if (text[j].isWhitespace() && !(c == '.' && isAbbreviation(text, i))) return j
                    i = j
                    continue
                }
                i++
            }
            return null
        }

        /** True when the period at [dot] closes an abbreviation, an initial
         *  or a list number rather than a sentence. */
        private fun isAbbreviation(text: CharSequence, dot: Int): Boolean {
            var start = dot
            while (start > 0 && !text[start - 1].isWhitespace()) start--
            val word = text.substring(start, dot).trimStart('(', '"', '\'', '“', '‘', '*', '_')
            if (word.isEmpty()) return false
            if (word.lowercase() in ABBREVIATIONS) return true
            // "J." or "U.S" style: single letters joined by periods ("I." is
            // the pronoun ending a sentence far more often than an initial).
            if (word != "I" && word.split('.').all { it.length == 1 && it[0].isLetter() }) return true
            // "1." opening a line is a list marker, not a full stop.
            val lineStart = start == 0 || text[start - 1] == '\n'
            return lineStart && word.all { it.isDigit() }
        }

        private val FENCE = Regex("```[a-zA-Z0-9]*")
        private val LINK = Regex("""\[([^\]]*)]\([^)]*\)""")
        private val HEADING = Regex("""(?m)^\s{0,3}#{1,6}\s+""")
        private val BULLET = Regex("""(?m)^\s*(?:[-*+•]|\d+[.)])\s+""")
        private val EMPHASIS = Regex("""\*+|(?<![\p{L}\p{N}])_+|_+(?![\p{L}\p{N}])|`+|~~""")
        private val SPACES = Regex("""\s+""")

        /** [markdown] as plain words to speak: no fences, emphasis markers,
         *  heading hashes, bullets or link targets; whitespace collapsed. */
        fun speakable(markdown: String): String =
            markdown
                .replace(FENCE, " ")
                .replace(LINK, "$1")
                .replace(HEADING, "")
                .replace(BULLET, "")
                .replace(EMPHASIS, "")
                .replace(SPACES, " ")
                .trim()
    }
}
