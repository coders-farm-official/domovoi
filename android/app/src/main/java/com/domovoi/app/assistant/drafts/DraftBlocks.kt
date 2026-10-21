package com.domovoi.app.assistant.drafts

/**
 * A draft's markdown as the blocks a template draws, the twin of the
 * server's `domovoi/drafts/blocks.py`: `# Name` is the title, `## Section` a
 * heading, `- item` a bullet, a line bold as a whole a bold line, anything
 * else a body line. Lines next to each other stay separate lines (a letter's
 * address); a blank line ends a paragraph; inline marks are dropped.
 */
data class DraftBlock(val style: String, val lines: MutableList<String> = mutableListOf())

object DraftBlocks {
    private val HEADING = Regex("^(#{1,6})\\s+(.*?)\\s*#*\\s*$")
    private val BULLET = Regex("^\\s*(?:[-*+]|\u2022)\\s+(.*)$")
    private val WHOLE_BOLD = Regex("^(?:\\*\\*|__)(.+?)(?:\\*\\*|__)$")
    private val RULE = Regex("^\\s*(?:-{3,}|\\*{3,}|_{3,})\\s*$")
    private val FENCE = Regex("^\\s*```")
    private val LINK = Regex("\\[([^\\]]*)\\]\\([^)]*\\)")
    private val BOLD = Regex("(\\*\\*|__)(.+?)\\1")
    private val ITALIC = Regex("(?<![A-Za-z0-9_*])([*_])(?![ \\t])(.+?)(?<![ \\t])\\1(?![A-Za-z0-9_*])")
    private val CODE = Regex("`([^`]*)`")
    private val ESCAPE = Regex("\\\\([\\\\`*_{}\\[\\]()#+\\-.!])")
    private val SPACES = Regex("[ \\t\u00a0]+")

    /** [text] without markdown's inline marks, its spaces collapsed. */
    fun inline(text: String): String {
        var s = LINK.replace(text, "$1")
        s = CODE.replace(s, "$1")
        repeat(2) { s = BOLD.replace(s, "$2") }
        s = ITALIC.replace(s, "$2")
        s = ESCAPE.replace(s) { it.groupValues[1] }
        return pyStrip(SPACES.replace(s, " "))
    }

    private fun xmlOk(c: Int): Boolean = c == 0x9 || c == 0xA || c == 0xD || c in 0x20..0xD7FF ||
        c in 0xE000..0xFFFD || c in 0x10000..0x10FFFF

    /** [text] without the characters XML 1.0 can't hold (control characters,
     *  U+FFFE/U+FFFF, lone surrogates): the server's `xml_safe`, one rule,
     *  applied on every edit and again when a file is made. */
    fun xmlSafe(text: String): String {
        val out = StringBuilder(text.length)
        var i = 0
        while (i < text.length) {
            val ch = text[i]
            if (Character.isHighSurrogate(ch) && i + 1 < text.length && Character.isLowSurrogate(text[i + 1])) {
                out.append(ch).append(text[i + 1])
                i += 2
                continue
            }
            if (!Character.isSurrogate(ch) && xmlOk(ch.code)) out.append(ch)
            i++
        }
        return out.toString()
    }

    fun parse(markdown: String): List<DraftBlock> {
        val out = mutableListOf<DraftBlock>()
        var para: DraftBlock? = null
        for (raw in xmlSafe(markdown).replace("\r\n", "\n").replace("\r", "\n").split("\n")) {
            val line = raw.trimEnd()
            if (FENCE.containsMatchIn(line) && FENCE.find(line)!!.range.first == 0 || RULE.matches(line)) {
                para = null
                continue
            }
            if (line.isBlank()) {
                para = null
                continue
            }
            HEADING.matchEntire(pyStrip(line))?.let { m ->
                val text = inline(m.groupValues[2])
                if (text.isNotEmpty()) out += DraftBlock(if (m.groupValues[1].length == 1) "title" else "heading", mutableListOf(text))
                para = null
                return@let true
            }?.let { continue }
            BULLET.matchEntire(line)?.let { m ->
                val text = inline(m.groupValues[1])
                if (text.isNotEmpty()) out += DraftBlock("bullet", mutableListOf(text))
                para = null
                return@let true
            }?.let { continue }
            val wb = WHOLE_BOLD.matchEntire(pyStrip(line))
            if (wb != null && !wb.groupValues[1].contains("**") && !wb.groupValues[1].contains("__")) {
                val text = inline(wb.groupValues[1])
                if (text.isNotEmpty()) out += DraftBlock("strong", mutableListOf(text))
                para = null
                continue
            }
            val text = inline(line)
            if (text.isEmpty()) continue
            val p = para ?: DraftBlock("body").also { out += it; para = it }
            p.lines += text
        }
        return out
    }

    /** The words a reader of the file sees, a line per line (no bullets). */
    fun plainText(blocks: List<DraftBlock>): String = blocks.flatMap { it.lines }.joinToString("\n")

    fun wordCount(markdown: String): Int = parse(markdown).sumOf { b -> b.lines.sumOf { pySplit(it).size } }

    /** Python's `str.strip()` (whitespace either end). */
    internal fun pyStrip(s: String): String = s.trim { it.isWhitespace() }

    /** Python's `str.split()` (runs of whitespace, no empty pieces). */
    internal fun pySplit(s: String): List<String> = s.split(DraftSpace.RUNS).filter { it.isNotEmpty() }
}
