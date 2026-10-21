package com.domovoi.app.assistant.lookup

/**
 * A web page's main text, with no HTML library and no JavaScript (the same
 * rules as the server's domovoi/page_text.py): scripts, styles, forms and
 * the page's chrome (nav, header, footer, aside) go, anything hidden goes,
 * an `<article>` or `<main>` is kept alone when the page has one, block
 * elements end a line, and entities are decoded. What comes back is data
 * for the model (fenced by [LookupRules.systemPrompt]), never markup.
 */
object PageText {
    private val SKIP = listOf(
        "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "form", "button",
        "select", "textarea", "nav", "header", "footer", "aside", "head", "math",
    )
    private val BLOCK = Regex(
        "</?(?:p|div|section|article|main|li|ul|ol|dl|dt|dd|h[1-6]|blockquote|pre|table|tr|td|th|figcaption|br|hr|address|details|summary)\\b[^>]*>",
        RegexOption.IGNORE_CASE,
    )
    private val HIDDEN = Regex(
        "<(\\w+)\\b[^>]*(?:\\shidden(?:\\s|=|>|/)|aria-hidden\\s*=\\s*[\"']?true|style\\s*=\\s*[\"'][^\"']*(?:display\\s*:\\s*none|visibility\\s*:\\s*hidden))[^>]*>.*?</\\1\\s*>",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )
    private val TITLE = Regex("<title\\b[^>]*>(.*?)</title\\s*>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
    private val COMMENT = Regex("<!--.*?-->", RegexOption.DOT_MATCHES_ALL)
    private val TAG = Regex("<[^>]*>")
    private val SPACE = Regex("[ \\t\\r\\u000c\\u000b\\u00a0]+")
    private val ENTITY = Regex("&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);")
    private val NAMED = mapOf(
        "amp" to "&", "lt" to "<", "gt" to ">", "quot" to "\"", "apos" to "'", "nbsp" to " ", "ndash" to "–",
        "mdash" to "—", "hellip" to "…", "rsquo" to "’", "lsquo" to "‘", "rdquo" to "”", "ldquo" to "“", "copy" to "©",
    )

    fun unescape(text: String): String = ENTITY.replace(text) { m ->
        val e = m.groupValues[1]
        when {
            e.startsWith("#x") || e.startsWith("#X") -> e.drop(2).toIntOrNull(16)?.let { cp -> String(Character.toChars(cp)) }
            e.startsWith("#") -> e.drop(1).toIntOrNull()?.takeIf { it in 1..0x10FFFF }?.let { cp -> String(Character.toChars(cp)) }
            else -> NAMED[e]
        } ?: m.value
    }

    fun tidy(text: String): String = text.lineSequence().map { SPACE.replace(it, " ").trim() }.filter { it.isNotEmpty() }
        .joinToString("\n")

    private fun dropElements(html: String): String {
        var s = COMMENT.replace(html, " ")
        for (tag in SKIP) {
            s = Regex("<$tag\\b[^>]*>.*?</$tag\\s*>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)).replace(s, " ")
            s = Regex("<$tag\\b[^>]*/>", RegexOption.IGNORE_CASE).replace(s, " ")
        }
        // Nested hidden blocks: a few passes catch the usual depth.
        repeat(3) { s = HIDDEN.replace(s, " ") }
        return s
    }

    private fun text(html: String): String = tidy(unescape(TAG.replace(BLOCK.replace(html, "\n"), "")))

    /** (title, text) of a page, capped at [maxChars]. Never throws. */
    fun mainText(html: String, maxChars: Int = 64 * 1024): Pair<String, String> = runCatching {
        val title = TITLE.find(html)?.groupValues?.get(1)?.let { tidy(unescape(TAG.replace(it, ""))).replace("\n", " ") }.orEmpty()
        val body = dropElements(html)
        val main = Regex("<(article|main)\\b[^>]*>(.*?)</\\1\\s*>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
            .findAll(body).joinToString("\n") { it.groupValues[2] }
        val picked = text(main).takeIf { it.length >= 200 } ?: text(body)
        title.take(200) to picked.take(maxChars)
    }.getOrDefault("" to "")
}
