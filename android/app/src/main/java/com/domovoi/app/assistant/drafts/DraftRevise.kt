package com.domovoi.app.assistant.drafts

/**
 * How a version is revised: in one pass when it fits, otherwise section by
 * section (documents plan part 2: at a 4K window about 1,600 tokens, since
 * the rewritten text has to fit too). The twin of the server's
 * `chat_drafts.split_sections` / `_revise_passes`.
 */
object DraftRevise {
    /** About 1,600 tokens of draft: past it, section by section. */
    const val ONE_PASS_CHARS = 6400

    /** [markdown] cut at its `##` headings (or between paragraphs when it
     *  has none) into pieces of at most about [limit] characters. */
    fun splitSections(markdown: String, limit: Int = ONE_PASS_CHARS): List<String> {
        val lines = markdown.replace("\r\n", "\n").split("\n")
        val pieces = mutableListOf(mutableListOf<String>())
        val hasHeads = lines.any { it.startsWith("## ") }
        for (ln in lines) {
            val start = if (hasHeads) ln.startsWith("## ")
            else ln.isBlank() && pieces.last().isNotEmpty() && pieces.last().sumOf { it.length } > limit / 2
            if (start && pieces.last().isNotEmpty()) pieces += mutableListOf<String>()
            pieces.last() += ln
        }
        val out = mutableListOf<String>()
        for (p in pieces) {
            val chunk = p.joinToString("\n").trim()
            if (chunk.isEmpty()) continue
            if (out.isNotEmpty() && out.last().length + chunk.length + 2 <= limit) out[out.lastIndex] = out.last() + "\n\n" + chunk
            else out += chunk
        }
        return out.ifEmpty { listOf(markdown) }
    }

    /** The (system, user) of each pass revising [body] by [instruction]. */
    fun passes(body: String, instruction: String, limit: Int = ONE_PASS_CHARS): List<Pair<String, String>> {
        if (body.length <= limit) return listOf(DraftPrompts.revision(body, instruction))
        val pieces = splitSections(body, limit)
        return pieces.mapIndexed { i, piece ->
            val (system, user) = DraftPrompts.revision(piece, instruction)
            system to "$user\n\nThis is part ${i + 1} of ${pieces.size} of the document: revise only this part."
        }
    }
}
