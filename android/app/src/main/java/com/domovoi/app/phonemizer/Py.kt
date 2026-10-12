package com.domovoi.app.phonemizer

import java.math.BigInteger
import java.text.Normalizer

/**
 * The few Python string and `re` behaviours the converter depends on,
 * spelled out so the port behaves the same on the JVM (the golden test)
 * and on Android, whose regular expressions are ICU's: `\w`, `\d`, `\s`
 * and `\b` mean different things on the two (ASCII-only on the JVM unless
 * a flag is set, Unicode on ICU, which rejects that flag), so the patterns
 * here never use them and use these classes instead.
 */
internal object Py {
    /** Python's `\w` on str patterns: letters, digits/numbers, underscore. */
    const val W = """[\p{L}\p{N}_]"""

    /** Python's `\d`: any Unicode decimal digit. */
    const val D = """\p{Nd}"""

    /** Python's `\s`: exactly the characters str.isspace() accepts. */
    const val S = """[\t\n\x0B\f\r\x1C-\x1F \x85\xA0  -     　]"""

    /** Python's `\b`: between a [W] character and a non-[W] one (or an end). */
    const val B = """(?:(?<=$W)(?!$W)|(?<!$W)(?=$W))"""

    private const val SPACES = "\t\n\u000B\u000C\r\u001C\u001D\u001E\u001F \u0085      　"

    fun isSpace(c: Char): Boolean = c in SPACES || c in ' '..' '

    /** str.strip() */
    fun strip(s: String): String = s.trim { isSpace(it) }

    /** str.split() with no separator: runs of whitespace, no empty parts. */
    fun split(s: String): List<String> {
        val out = mutableListOf<String>()
        var start = -1
        for (i in s.indices) {
            if (isSpace(s[i])) {
                if (start >= 0) out += s.substring(start, i)
                start = -1
            } else if (start < 0) {
                start = i
            }
        }
        if (start >= 0) out += s.substring(start)
        return out
    }

    /** str.isupper(): at least one cased character and none lower or title case. */
    fun isUpper(s: String): Boolean {
        var cased = false
        for (c in s) {
            if (c.isLowerCase() || c.isTitleCase()) return false
            if (c.isUpperCase()) cased = true
        }
        return cased
    }

    /** str.isalpha() */
    fun isAlpha(s: String): Boolean = s.isNotEmpty() && s.all { it.isLetter() }

    /** str.isalnum() for one character. */
    fun isAlnum(c: Char): Boolean = c.isLetter() || when (Character.getType(c).toByte()) {
        Character.DECIMAL_DIGIT_NUMBER, Character.LETTER_NUMBER, Character.OTHER_NUMBER -> true
        else -> false
    }

    /** int(s) for a run of (Unicode) digits, with an optional sign. */
    fun int(s: String): BigInteger = BigInteger(s)

    fun nfc(s: String): String = Normalizer.normalize(s, Normalizer.Form.NFC)

    fun nfd(s: String): String = Normalizer.normalize(s, Normalizer.Form.NFD)

    /** NFKD, then without combining marks (normalize.strip_accents). */
    fun stripAccents(w: String): String {
        val decomposed = Normalizer.normalize(w, Normalizer.Form.NFKD)
        val out = StringBuilder(decomposed.length)
        var i = 0
        while (i < decomposed.length) {
            val cp = decomposed.codePointAt(i)
            if (!isCombining(cp)) out.appendCodePoint(cp)
            i += Character.charCount(cp)
        }
        return out.toString()
    }

    /** unicodedata.combining(c) != 0, as near as the JVM can tell without
     *  ICU: the marks that attach to a base letter. */
    private fun isCombining(cp: Int): Boolean = when (Character.getType(cp).toByte()) {
        Character.NON_SPACING_MARK, Character.ENCLOSING_MARK -> true
        else -> false
    }

    /** s[a:b] with Python's clamping. */
    fun slice(s: String, from: Int, to: Int = s.length): String {
        val a = from.coerceIn(0, s.length)
        val b = to.coerceIn(0, s.length)
        return if (a >= b) "" else s.substring(a, b)
    }
}
