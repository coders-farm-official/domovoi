package com.domovoi.app.phonemizer

import java.math.BigInteger

/**
 * Numbers to English words (American style), for the text normalizer: a
 * line-for-line port of `domovoi/phonemizer/number_words.py`. Python's
 * integers have no size limit, so the numbers here are [BigInteger] where
 * the text can make them arbitrarily long.
 */
internal object NumberWords {
    val ONES = listOf(
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
        "nineteen",
    )
    private val TENS = listOf("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
    private val SCALES = listOf(
        1_000_000_000_000L to "trillion", 1_000_000_000L to "billion", 1_000_000L to "million", 1000L to "thousand",
    )
    private val ORD_IRREG = mapOf(
        "one" to "first", "two" to "second", "three" to "third", "five" to "fifth", "eight" to "eighth",
        "nine" to "ninth", "twelve" to "twelfth",
    )
    private val LIMIT: BigInteger = BigInteger.TEN.pow(15)

    private fun under1000(start: Int): List<String> {
        var n = start
        val out = mutableListOf<String>()
        if (n >= 100) {
            out += ONES[n / 100]
            out += "hundred"
            n %= 100
        }
        if (n >= 20) {
            out += TENS[n / 10]
            if (n % 10 != 0) out += ONES[n % 10]
        } else if (n != 0 || out.isEmpty()) {
            out += ONES[n]
        }
        return out
    }

    fun cardinal(n: Long): String = cardinal(BigInteger.valueOf(n))

    fun cardinal(n: BigInteger): String {
        if (n.signum() < 0) return "minus " + cardinal(n.negate())
        if (n.signum() == 0) return "zero"
        if (n >= LIMIT) return digits(n.toString())   // past the scale words: read the digits
        var m = n.toLong()
        val out = mutableListOf<String>()
        for ((value, name) in SCALES) {
            if (m >= value) {
                out += under1000((m / value).toInt())
                out += name
                m %= value
            }
        }
        if (m != 0L) out += under1000(m.toInt())
        return out.joinToString(" ")
    }

    fun ordinal(n: BigInteger): String {
        val words = Py.split(cardinal(n)).toMutableList()
        val last = words.last()
        words[words.size - 1] = ORD_IRREG[last]
            ?: if (last.endsWith("y")) last.dropLast(1) + "ieth" else last + "th"
        return words.joinToString(" ")
    }

    fun ordinal(n: Long): String = ordinal(BigInteger.valueOf(n))

    fun year(n: Int): String {
        if (n in 2000..2009) return cardinal(n.toLong())
        val hi = n / 100
        val lo = n % 100
        if (lo == 0) return cardinal(hi.toLong()) + " hundred"
        if (lo < 10) return cardinal(hi.toLong()) + " oh " + cardinal(lo.toLong())
        return cardinal(hi.toLong()) + " " + cardinal(lo.toLong())
    }

    /** Digit by digit; an ASCII "0" is "oh" (Python's str.isdigit / int(c)). */
    fun digits(s: String): String =
        s.filter { it.isDigit() }.map { if (it == '0') "oh" else ONES[Character.digit(it, 10)] }.joinToString(" ")

    /** "3.14" -> "three point one four". */
    fun decimal(s: String): String {
        val dot = s.indexOf('.')
        val whole = if (dot < 0) s else s.substring(0, dot)
        val frac = if (dot < 0) "" else s.substring(dot + 1)
        val w = cardinal(Py.int(whole.replace(",", "").ifEmpty { "0" }))
        if (frac.isEmpty()) return w
        return w + " point " + frac.map { ONES[Character.digit(it, 10)] }.joinToString(" ")
    }
}
