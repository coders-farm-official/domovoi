package com.domovoi.app.assistant.docs

/**
 * Plain text and markdown, as the server reads them
 * (`domovoi/doc_extract/plain.py`): a markdown `#` line is a heading,
 * paragraphs are separated by blank lines, a fenced block stays whole; a
 * .txt has no headings, so its parts are labelled by paragraph.
 */
object DocPlain {
    private val MD_HEADING = Regex("""^(#{1,6})\s+(.+?)\s*#*\s*$""")

    fun blocks(text: String, markdown: Boolean): List<DocBlock> {
        val blocks = ArrayList<DocBlock>()
        val para = ArrayList<String>()
        var fence = false
        fun flush() {
            if (para.isNotEmpty()) {
                blocks += DocBlock("p", para.joinToString("\n").trim())
                para.clear()
            }
        }
        for (line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")) {
            if (markdown && line.trim().startsWith("```")) {
                fence = !fence
                para += line
                continue
            }
            val m = if (markdown && !fence) MD_HEADING.matchEntire(line) else null
            when {
                m != null -> {
                    flush()
                    blocks += DocBlock("h", m.groupValues[2], m.groupValues[1].length)
                }
                line.isBlank() && !fence -> flush()
                else -> para += line.trimEnd()
            }
        }
        flush()
        return blocks
    }

    fun extract(bytes: ByteArray, markdown: Boolean): Pair<List<DocPart>, String> {
        val (text, enc) = DocSheets.decodeText(bytes)
        return DocStructure.toParts(blocks(DocText.clean(text), markdown)) to enc
    }
}
