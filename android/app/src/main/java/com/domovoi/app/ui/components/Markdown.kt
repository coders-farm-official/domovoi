package com.domovoi.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily

/*
 * A small Markdown renderer for assistant replies — the subset a chat model
 * actually writes: headings, paragraphs, bullet and numbered lists, block
 * quotes, fenced code, rules, and inline bold / italic / code / links.
 * No HTML, no tables, no images. Parsing is pure Kotlin (unit-tested on the
 * JVM); [MarkdownText] only draws the result.
 *
 * It is rendered on every streamed delta, so it must tolerate half-written
 * input: an unclosed `**` or backtick stays literal until its closer
 * arrives, and an unclosed code fence runs to the end of the text.
 */

internal sealed interface MdBlock {
    data class Heading(val level: Int, val text: String) : MdBlock
    data class Paragraph(val text: String) : MdBlock
    /** [marker] is "•" for a bullet, or "3." for a numbered item as written. */
    data class ListItem(val marker: String, val text: String, val depth: Int) : MdBlock
    data class Quote(val text: String) : MdBlock
    data class Code(val text: String) : MdBlock
    data object Rule : MdBlock
}

private val HEADING = Regex("""^(#{1,6})\s+(.*?)\s*#*\s*$""")
private val BULLET = Regex("""^(\s*)[-*+]\s+(.*)$""")
private val NUMBERED = Regex("""^(\s*)(\d{1,3})[.)]\s+(.*)$""")
private val RULE = Regex("""^\s*([-*_])(\s*\1){2,}\s*$""")
private val FENCE = Regex("""^\s*(```|~~~)""")

internal fun parseMarkdown(src: String): List<MdBlock> {
    val out = mutableListOf<MdBlock>()
    val para = mutableListOf<String>()
    fun flush() {
        if (para.isNotEmpty()) out += MdBlock.Paragraph(para.joinToString("\n"))
        para.clear()
    }
    val lines = src.replace("\r\n", "\n").split('\n')
    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        val fence = FENCE.find(line)
        when {
            fence != null -> {
                flush()
                val closer = fence.groupValues[1]
                val body = mutableListOf<String>()
                i++
                while (i < lines.size && !lines[i].trimStart().startsWith(closer)) body += lines[i++]
                out += MdBlock.Code(body.joinToString("\n"))
            }
            line.isBlank() -> flush()
            RULE.matches(line) -> { flush(); out += MdBlock.Rule }
            HEADING.matches(line) -> {
                flush()
                val m = HEADING.find(line)!!
                out += MdBlock.Heading(m.groupValues[1].length, m.groupValues[2])
            }
            BULLET.matches(line) -> {
                flush()
                val m = BULLET.find(line)!!
                out += MdBlock.ListItem("•", m.groupValues[2], depthOf(m.groupValues[1]))
            }
            NUMBERED.matches(line) -> {
                flush()
                val m = NUMBERED.find(line)!!
                out += MdBlock.ListItem("${m.groupValues[2]}.", m.groupValues[3], depthOf(m.groupValues[1]))
            }
            line.trimStart().startsWith(">") -> {
                flush()
                val quote = mutableListOf<String>()
                while (i < lines.size && lines[i].trimStart().startsWith(">")) {
                    quote += lines[i].trimStart().removePrefix(">").removePrefix(" ")
                    i++
                }
                out += MdBlock.Quote(quote.joinToString("\n"))
                continue
            }
            else -> para += line.trim()
        }
        i++
    }
    flush()
    return out
}

private fun depthOf(indent: String): Int =
    (indent.replace("\t", "    ").length / 2).coerceAtMost(3)

/** One run of inline text. [link] is set only for http(s) targets. */
internal data class MdSpan(
    val text: String,
    val bold: Boolean = false,
    val italic: Boolean = false,
    val code: Boolean = false,
    val link: String? = null,
)

internal fun parseInline(s: String, bold: Boolean = false, italic: Boolean = false): List<MdSpan> {
    val out = mutableListOf<MdSpan>()
    val plain = StringBuilder()
    fun emit(span: MdSpan) {
        if (plain.isNotEmpty()) { out += MdSpan(plain.toString(), bold, italic); plain.clear() }
        out += span
    }
    var i = 0
    while (i < s.length) {
        val c = s[i]
        // \* \_ \` \[ — the escaped character, literally.
        if (c == '\\' && i + 1 < s.length && s[i + 1] in "\\`*_[]()#>-+.!") {
            plain.append(s[i + 1]); i += 2; continue
        }
        if (c == '`') {
            val end = s.indexOf('`', i + 1)
            if (end > i + 1) {
                emit(MdSpan(s.substring(i + 1, end), bold, italic, code = true)); i = end + 1; continue
            }
        }
        if ((c == '*' || c == '_') && s.startsWith("$c$c", i)) {
            val end = s.indexOf("$c$c", i + 2)
            if (end > i + 2 && !s[i + 2].isWhitespace()) {
                if (plain.isNotEmpty()) { out += MdSpan(plain.toString(), bold, italic); plain.clear() }
                out += parseInline(s.substring(i + 2, end), bold = true, italic = italic)
                i = end + 2; continue
            }
        }
        if (c == '*' || c == '_') {
            // `_` only at a word edge, so snake_case stays snake_case.
            val opens = i + 1 < s.length && !s[i + 1].isWhitespace() &&
                (c == '*' || i == 0 || !s[i - 1].isLetterOrDigit())
            val end = if (opens) closingEmphasis(s, i + 1, c) else -1
            if (end > i + 1) {
                if (plain.isNotEmpty()) { out += MdSpan(plain.toString(), bold, italic); plain.clear() }
                out += parseInline(s.substring(i + 1, end), bold = bold, italic = true)
                i = end + 1; continue
            }
        }
        if (c == '[') {
            val close = s.indexOf("](", i + 1)
            val end = if (close > i) s.indexOf(')', close + 2) else -1
            if (close > i && end > close + 2) {
                val label = s.substring(i + 1, close)
                val url = webLinkOrNull(s.substring(close + 2, end))
                if (plain.isNotEmpty()) { out += MdSpan(plain.toString(), bold, italic); plain.clear() }
                out += parseInline(label, bold, italic).map { it.copy(link = url) }
                i = end + 1; continue
            }
        }
        plain.append(c); i++
    }
    if (plain.isNotEmpty()) out += MdSpan(plain.toString(), bold, italic)
    return out
}

private fun closingEmphasis(s: String, from: Int, c: Char): Int {
    var j = from
    while (j < s.length) {
        val k = s.indexOf(c, j)
        if (k < 0) return -1
        val doubled = k + 1 < s.length && s[k + 1] == c
        val wordEdge = c == '*' || k + 1 >= s.length || !s[k + 1].isLetterOrDigit()
        if (!doubled && !s[k - 1].isWhitespace() && wordEdge) return k
        j = if (doubled) k + 2 else k + 1
    }
    return -1
}

/** Assistant text as formatted Markdown. Links open in the browser (http/https only). */
@Composable
fun MarkdownText(text: String, modifier: Modifier = Modifier, trailing: String = "") {
    val blocks = remember(text) { parseMarkdown(text) }
    val context = LocalContext.current
    val colors = Domovoi.colors
    val codeBg = colors.sunken
    val inline: (String) -> AnnotatedString = { src ->
        buildAnnotatedString {
            parseInline(src).forEach { span ->
                val style = SpanStyle(
                    fontWeight = if (span.bold) FontWeight.SemiBold else null,
                    fontStyle = if (span.italic) FontStyle.Italic else null,
                    fontFamily = if (span.code) MonoFamily else null,
                    background = if (span.code) codeBg else Color.Unspecified,
                )
                val link = span.link
                if (link != null) {
                    withLink(
                        LinkAnnotation.Clickable(
                            tag = link,
                            styles = TextLinkStyles(SpanStyle(color = colors.brand, textDecoration = TextDecoration.Underline)),
                        ) { openWebLink(context, link) },
                    ) { withStyle(style) { append(span.text) } }
                } else {
                    withStyle(style) { append(span.text) }
                }
            }
        }
    }
    val body = MaterialTheme.typography.bodyMedium
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        blocks.forEachIndexed { idx, b ->
            val tail = if (idx == blocks.lastIndex) trailing else ""
            when (b) {
                is MdBlock.Heading -> Text(
                    inline(b.text + tail),
                    style = when (b.level) {
                        1 -> MaterialTheme.typography.titleLarge
                        2 -> MaterialTheme.typography.titleMedium
                        else -> MaterialTheme.typography.titleSmall
                    },
                    fontWeight = FontWeight.SemiBold,
                    color = colors.fg,
                    modifier = Modifier.padding(top = if (idx > 0) 4.dp else 0.dp),
                )
                is MdBlock.Paragraph -> Text(inline(b.text + tail), style = body, color = colors.fg)
                is MdBlock.ListItem -> Row(Modifier.padding(start = (b.depth * 16).dp)) {
                    Text(
                        b.marker, style = body, color = colors.fgMuted,
                        modifier = Modifier.widthIn(min = 20.dp).padding(end = 6.dp),
                    )
                    Text(inline(b.text + tail), style = body, color = colors.fg)
                }
                is MdBlock.Quote -> Row(Modifier.height(IntrinsicSize.Min)) {
                    Box(Modifier.width(3.dp).fillMaxHeight().background(colors.border, RoundedCornerShape(2.dp)))
                    Text(
                        inline(b.text + tail), style = body, color = colors.fgMuted,
                        modifier = Modifier.padding(start = 10.dp),
                    )
                }
                is MdBlock.Code -> Box(
                    Modifier.fillMaxWidth()
                        .background(codeBg, RoundedCornerShape(8.dp))
                        .horizontalScroll(rememberScrollState())
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                ) {
                    Text(
                        b.text + tail,
                        style = MaterialTheme.typography.bodySmall.copy(fontFamily = MonoFamily),
                        color = colors.fg,
                        softWrap = false,
                    )
                }
                MdBlock.Rule -> Box(Modifier.fillMaxWidth().height(1.dp).background(colors.border))
            }
        }
        if (blocks.isEmpty() && trailing.isNotEmpty()) Text(trailing, style = body, color = colors.fg)
    }
}
