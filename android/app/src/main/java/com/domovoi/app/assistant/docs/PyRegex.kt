package com.domovoi.app.assistant.docs

import com.domovoi.app.phonemizer.Py

/**
 * A pattern written the way the server's Python writes it (`\b`, `\w`, `\W`,
 * `\d`, `\s` meaning Unicode letters, digits and spaces), as a [Regex] that
 * means the same on the JVM and on Android. ICU (Android) rejects the
 * Unicode-classes flag and the JVM's `\b \w \d \s` are ASCII-only without
 * it, so each is spelled out with [Py]'s explicit classes; inside a
 * character class `\w` and `\d` become their ranges, flat (a nested class is
 * read differently by the two engines). `\W`, `\S`, `\D` and `\b` inside a
 * class have no flat spelling and are refused, so a pattern that needs one
 * fails its tests instead of matching differently on a phone.
 */
internal fun pyRegex(pattern: String): Regex {
    val out = StringBuilder()
    var inClass = false
    var i = 0
    while (i < pattern.length) {
        val c = pattern[i]
        if (c == '\\' && i + 1 < pattern.length) {
            val n = pattern[i + 1]
            val rep = if (inClass) when (n) {
                'w' -> """\p{L}\p{N}_"""
                'd' -> """\p{Nd}"""
                'W', 'D', 'S', 'b', 's' -> throw IllegalArgumentException("pyRegex: \\$n inside [...] in $pattern")
                else -> null
            } else when (n) {
                'b' -> Py.B
                'w' -> Py.W
                'W' -> """[^\p{L}\p{N}_]"""
                'd' -> Py.D
                'D' -> """\P{Nd}"""
                's' -> Py.S
                else -> null
            }
            out.append(rep ?: "$c$n")
            i += 2
            continue
        }
        if (!inClass && c == '[') {
            inClass = true
            out.append(c)
            // A ']' first in a class (after an optional '^') is a literal.
            if (i + 1 < pattern.length && pattern[i + 1] == '^') { out.append('^'); i++ }
            if (i + 1 < pattern.length && pattern[i + 1] == ']') { out.append(']'); i++ }
        } else if (inClass && c == ']') {
            inClass = false
            out.append(c)
        } else {
            out.append(c)
        }
        i++
    }
    return Regex(out.toString())
}
