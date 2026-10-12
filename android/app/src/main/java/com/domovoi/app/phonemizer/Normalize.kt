package com.domovoi.app.phonemizer

import com.domovoi.app.phonemizer.Py.B
import com.domovoi.app.phonemizer.Py.D
import com.domovoi.app.phonemizer.Py.S
import com.domovoi.app.phonemizer.Py.W
import com.domovoi.app.phonemizer.NumberWords as N

/** One token of a normalized sentence. */
internal sealed interface Token {
    /** A word; [spell]: say its letters. */
    data class Word(val w: String, val spell: Boolean) : Token

    /** Punctuation the voices know: , . ! ? ; : */
    data class Punct(val p: String) : Token
}

/**
 * Text normalization: numbers, money, dates, times, %, degrees, units,
 * abbreviations, acronyms, stylized names. A line-for-line port of
 * `domovoi/phonemizer/normalize.py`; every pattern is the Python one with
 * `\w \d \s \b` spelled out ([Py]).
 */
internal object Normalize {
    val MONTHS = listOf(
        "january", "february", "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december",
    )

    private fun capitalize(s: String) = s.take(1).uppercase() + s.drop(1).lowercase()

    private val MONTH_RE = MONTHS.joinToString("|") { capitalize(it) } + "|" +
        MONTHS.filter { it.length > 4 }.joinToString("|") { capitalize(it.take(3)) + """\.""" }

    private val ABBREV = mapOf(
        "mr." to "mister", "mrs." to "missus", "ms." to "miz", "dr." to "doctor", "prof." to "professor",
        "jr." to "junior", "sr." to "senior", "st." to "saint", "mt." to "mount", "ave." to "avenue",
        "blvd." to "boulevard", "rd." to "road", "vs." to "versus", "etc." to "et cetera", "approx." to "approximately",
        "inc." to "incorporated", "ltd." to "limited", "co." to "company", "dept." to "department",
        "no." to "number", "min." to "minutes", "hr." to "hour", "hrs." to "hours", "sec." to "seconds",
        "jan." to "january", "feb." to "february", "mar." to "march", "apr." to "april", "aug." to "august",
        "sep." to "september", "sept." to "september", "oct." to "october", "nov." to "november", "dec." to "december",
        "mon." to "monday", "tue." to "tuesday", "tues." to "tuesday", "wed." to "wednesday", "thu." to "thursday",
        "thurs." to "thursday", "fri." to "friday", "sat." to "saturday", "sun." to "sunday",
    )
    private val UNITS = linkedMapOf(
        "mph" to "miles per hour", "km/h" to "kilometers per hour", "kph" to "kilometers per hour",
        "km" to "kilometers", "cm" to "centimeters", "mm" to "millimeters", "kg" to "kilograms",
        "lb" to "pounds", "lbs" to "pounds", "oz" to "ounces", "ft" to "feet", "mi" to "miles",
        "min" to "minutes", "mins" to "minutes", "hr" to "hours", "hrs" to "hours", "sec" to "seconds",
        "secs" to "seconds", "ms" to "milliseconds", "gb" to "gigabytes", "mb" to "megabytes", "tb" to "terabytes",
        "kb" to "kilobytes", "hz" to "hertz", "khz" to "kilohertz", "mhz" to "megahertz", "ghz" to "gigahertz",
        "kw" to "kilowatts", "kwh" to "kilowatt hours", "mah" to "milliamp hours", "ml" to "milliliters",
    )
    private val UNIT_SING = mapOf(
        "miles per hour" to "mile per hour", "kilometers per hour" to "kilometer per hour",
        "feet" to "foot", "hours" to "hour", "minutes" to "minute", "seconds" to "second",
        "pounds" to "pound", "ounces" to "ounce", "miles" to "mile", "kilometers" to "kilometer",
        "centimeters" to "centimeter", "millimeters" to "millimeter", "kilograms" to "kilogram",
        "milliseconds" to "millisecond", "gigabytes" to "gigabyte", "megabytes" to "megabyte",
        "terabytes" to "terabyte", "kilobytes" to "kilobyte", "kilowatts" to "kilowatt",
        "kilowatt hours" to "kilowatt hour", "milliamp hours" to "milliamp hour",
        "milliliters" to "milliliter",
    )
    private val SYMBOLS = linkedMapOf(
        "&" to " and ", "+" to " plus ", "=" to " equals ", "@" to " at ", "#" to " number ", "°" to " degrees ",
    )
    private val CURRENCY = mapOf(
        "$" to listOf("dollar", "dollars", "cent", "cents"),
        "£" to listOf("pound", "pounds", "penny", "pence"),
        "€" to listOf("euro", "euros", "cent", "cents"),
    )
    /** null: spell it. */
    private val WORD_CAPS_OK = mapOf("OK" to "okay", "TV" to null)
    private const val KEEP_PUNCT = ",.!?;:"
    private val TITLES = setOf("mr.", "mrs.", "ms.", "dr.", "prof.", "mt.", "no.")

    // The patterns, in prenormalize's order.
    private val STYLIZED = Regex("""(?<=[A-Za-z])\$|\$(?=[A-Za-z])""")
    private val MONEY = Regex(
        """([$£€])$S?($D[$D,]*)(?:\.($D+))?(?:$S?(thousand|million|billion|trillion|[KMB]$B))?""",
    )
    private val TEMP = Regex("""(-?$D+(?:\.$D+)?)$S?°$S?([FC])?$B""")
    private val PERCENT = Regex("""(-?$D+(?:\.$D+)?)$S?%""")
    private val TIME = Regex("""$B($D{1,2}):($D{2})(?:$S*([AaPp]\.?$S?[Mm]\.?)(?![A-Za-z]))?""")
    private val AMPM = Regex("""$B($D{1,2})$S*([AaPp])\.?$S?[Mm]$B\.?""")
    private val PHONE = Regex("""(?:\($D{3}\)$S?|$B$D{3}[-.])?$B$D{3}[-.]$D{4}$B""")
    private val DATE_MD = Regex("""$B($D{1,2})/($D{1,2})(?:/($D{4}|$D{2}))?$B""")
    private val UNIT_RE = Regex(
        """(?i)($D+(?:\.$D+)?)$S?(""" +
            UNITS.keys.sortedByDescending { it.length }.joinToString("|") + """)$B""",
    )
    private val ORDINAL = Regex("""$B($D+)(st|nd|rd|th)$B""")
    private val MONTH_DAY = Regex("""$B($MONTH_RE)$S+($D{1,2})$B(?![:.,]?$D)""")
    private val ABBREV_RE = Regex("""$B[A-Za-z]{1,6}\.""")
    private val DOTTED = Regex("""$B(?:[A-Za-z]\.){2,}""")
    private val CAPS_SLASH = Regex("""$B([A-Z]{1,4})/([A-Z]{1,4})$B""")
    private val SLASH = Regex("""(?<=$W)/(?=$W)""")
    private val NUMBER = Regex("""(?:(?<![\p{L}\p{N}_-])-)?$D[$D,]*(?:\.$D+)?""")
    private val FOURDIGITS = Regex("""$D{4}""")
    private val SPACE_THEN_CAP_OR_QUOTE = Regex("""^$S+[A-Z"']""")
    private val SPACE_THEN_CAP = Regex("""^$S+[A-Z]""")
    private val VOWEL_CAP = Regex("[AEIOU]")

    val TOKEN_RE = Regex("""<[A-Z0-9]+>|[A-Za-zÀ-ɏ](?:[A-Za-zÀ-ɏ'\-]*[A-Za-zÀ-ɏ])?'?|[,.!?;:]""")

    /** `re.sub(pattern, fn, text)`, where [fn] also sees the text being
     *  substituted (Python's `m.string`). */
    private fun Regex.sub(text: String, fn: (MatchResult, String) -> String): String =
        replace(text) { fn(it, text) }

    private fun MatchResult.g(i: Int): String? = groups[i]?.value

    private fun money(m: MatchResult): String {
        val sym = m.g(1)!!
        val whole = m.g(2)!!
        val cents = m.g(3)
        val scaleRaw = m.g(4)
        val (one, many, c1, cmany) = CURRENCY.getValue(sym)
        val w = Py.int(whole.replace(",", ""))
        val scale = when (scaleRaw) {
            "K" -> "thousand"
            "M" -> "million"
            "B" -> "billion"
            else -> scaleRaw
        }
        if (!scale.isNullOrEmpty()) {
            val num = N.decimal(whole + (if (cents != null) ".$cents" else ""))
            return " $num ${scale.lowercase()} $many "
        }
        if (cents != null && cents.length != 2) return " ${N.decimal("$whole.$cents")} $many "
        val c = if (cents != null) cents.toInt() else 0
        val parts = mutableListOf<String>()
        if (w.signum() != 0 || c == 0) parts += "${N.cardinal(w)} ${if (w == java.math.BigInteger.ONE) one else many}"
        if (c != 0) parts += "${N.cardinal(c.toLong())} ${if (c == 1) c1 else cmany}"
        return " " + parts.joinToString(" and ") + " "
    }

    /** '9 a.m.' at the end of a sentence: the abbreviation's dot is also the full stop. */
    private fun keepPeriod(m: MatchResult, text: String): String {
        if (!m.value.endsWith(".")) return " "
        val rest = text.substring(m.range.last + 1)
        if (Py.strip(rest).isEmpty() || SPACE_THEN_CAP_OR_QUOTE.containsMatchIn(rest)) return " . "
        return " "
    }

    private fun time(m: MatchResult, text: String): String {
        val h = m.g(1)!!.let(Py::int).toLong()
        val mi = m.g(2)!!.let(Py::int).toLong()
        val ampm = m.g(3)
        var s = N.cardinal(h)
        if (mi == 0L) {
            s += if (ampm != null) "" else " o'clock"
        } else if (mi < 10) {
            s += " oh " + N.cardinal(mi)
        } else {
            s += " " + N.cardinal(mi)
        }
        if (ampm != null) s += spell(if (ampm[0].lowercase() == "a") "AM" else "PM")
        return " " + s + keepPeriod(m, text)
    }

    private fun ampm(m: MatchResult, text: String): String =
        " " + N.cardinal(Py.int(m.g(1)!!)) + spell(if (m.g(2)!!.lowercase() == "a") "AM" else "PM") + keepPeriod(m, text)

    private fun temp(m: MatchResult): String {
        val num = m.g(1)!!
        val words = if ("." in num) N.decimal(num) else N.cardinal(Py.int(num))
        val unit = when ((m.g(2) ?: "").lowercase()) {
            "f" -> " Fahrenheit"
            "c" -> " Celsius"
            else -> ""
        }
        val deg = if (num == "1" || num == "-1") "degree" else "degrees"
        return " $words $deg$unit "
    }

    private fun numberWord(raw: String): String {
        val s = raw.replace(",", "")
        if ("." in s) return N.decimal(s)
        if (s.length > 1 && s.startsWith("0")) return N.digits(s)
        if (s.length >= 10) return N.digits(s)
        return N.cardinal(Py.int(s))
    }

    private fun plainNumber(s: String): String {
        if (FOURDIGITS.matches(s)) {
            val n = Py.int(s).toInt()
            if (n in 1100..1999 || n in 2010..2099) return " " + N.year(n) + " "
        }
        return " " + numberWord(s) + " "
    }

    private fun dateMd(m: MatchResult): String {
        val mo = Py.int(m.g(1)!!).toInt()
        val d = Py.int(m.g(2)!!).toInt()
        val y = m.g(3)
        if (!(mo in 1..12 && d in 1..31)) return m.value
        var s = " ${MONTHS[mo - 1]} ${N.ordinal(d.toLong())}"
        if (y != null) {
            val yy = if (y.length == 4) Py.int(y).toInt() else 2000 + Py.int(y).toInt()
            s += " " + N.year(yy)
        }
        return "$s "
    }

    private fun spell(s: String): String =
        " <" + s.filter { Py.isAlnum(it) }.uppercase() + "> "

    private fun units(m: MatchResult): String {
        val num = m.g(1)!!
        val unit = m.g(2)!!
        var words = UNITS.getValue(unit.lowercase())
        if (num == "1") words = UNIT_SING[words] ?: words
        return " $num $words "
    }

    private fun abbrev(m: MatchResult, text: String): String {
        val k = m.value.lowercase()
        val word = ABBREV[k] ?: return m.value
        val rest = text.substring(m.range.last + 1)
        val w = if (k == "st.") (if (SPACE_THEN_CAP.containsMatchIn(rest)) "saint" else "street") else word
        val end = if (k !in TITLES && Py.strip(rest).isEmpty()) "." else ""
        return " $w $end"
    }

    fun prenormalize(text: String): String {
        var t = Py.nfc(text)
        t = t.replace("’", "'").replace("‘", "'").replace("“", "\"").replace("”", "\"")
        t = t.replace("—", ", ").replace("–", " to ").replace("…", "...")
        // stylized names: $ glued to letters is an S ($uicideboy$ -> suicideboys)
        t = STYLIZED.replace(t, "s")
        t = MONEY.sub(t) { m, _ -> money(m) }
        t = TEMP.sub(t) { m, _ -> temp(m) }
        t = PERCENT.sub(t) { m, _ -> " ${m.g(1)} percent " }
        t = TIME.sub(t) { m, src -> time(m, src) }
        t = AMPM.sub(t) { m, src -> ampm(m, src) }
        t = PHONE.sub(t) { m, _ -> " " + N.digits(m.value) + " " }
        t = DATE_MD.sub(t) { m, _ -> dateMd(m) }
        t = t.replace("24/7", " twenty four seven ")
        t = UNIT_RE.sub(t) { m, _ -> units(m) }
        t = ORDINAL.sub(t) { m, _ -> " " + N.ordinal(Py.int(m.g(1)!!)) + " " }
        t = MONTH_DAY.sub(t) { m, _ -> "${m.g(1)} ${N.ordinal(Py.int(m.g(2)!!))}" }
        t = ABBREV_RE.sub(t) { m, src -> abbrev(m, src) }
        t = DOTTED.sub(t) { m, src -> spell(m.value) + keepPeriod(m, src) }
        t = CAPS_SLASH.sub(t) { m, _ -> spell(m.g(1)!!) + spell(m.g(2)!!) }
        t = SLASH.replace(t, " slash ")
        t = NUMBER.sub(t) { m, _ ->
            (if (m.value.startsWith("-")) " minus " else "") + plainNumber(m.value.trimStart('-'))
        }
        for ((k, v) in SYMBOLS) t = t.replace(k, v)
        return t
    }

    /** Text -> sentences of tokens. */
    fun normalize(text: String): List<List<Token>> {
        val t = prenormalize(text)
        val sents = mutableListOf<List<Token>>()
        var cur = mutableListOf<Token>()
        for (match in TOKEN_RE.findAll(t)) {
            val tok = match.value
            if (tok.length == 1 && tok[0] in KEEP_PUNCT) {
                if (tok == "." && cur.isNotEmpty() && cur.last() is Token.Punct) continue
                if (cur.isEmpty()) continue
                cur += Token.Punct(tok)
                if (tok in setOf(".", "!", "?")) {
                    sents += cur
                    cur = mutableListOf()
                }
                continue
            }
            if (tok.startsWith("<")) {
                cur += Token.Word(tok.substring(1, tok.length - 1).lowercase(), true)
                continue
            }
            if (Py.isUpper(tok) && tok.length >= 2 && Py.isAlpha(tok)) {
                if (tok in WORD_CAPS_OK && WORD_CAPS_OK[tok] != null) {
                    cur += Token.Word(WORD_CAPS_OK.getValue(tok)!!, false)
                    continue
                }
                if (tok.length <= 3 || !VOWEL_CAP.containsMatchIn(tok)) {
                    cur += Token.Word(tok.lowercase(), true)
                    continue
                }
            }
            cur += Token.Word(Py.stripAccents(tok).lowercase(), false)
        }
        if (cur.isNotEmpty()) sents += cur
        return sents
    }
}
