package com.domovoi.app.phonemizer

import com.domovoi.app.phonemizer.Py.S

/** An entry of the "say it like" list the converter can't use; the message
 *  says which and why. */
class PronunciationException(message: String) : IllegalArgumentException(message)

/** One "say it like" entry (`domovoi/phonemizer/pronunciations.py`). */
data class Pronunciation(
    /** As the owner wrote it. */
    val word: String,
    /** What text is matched on: accents stripped, lower case. */
    val key: String,
    val sayItLike: String,
    /** The respelling's syllables (empty for the /phonemes/ form). */
    val pieces: List<String> = emptyList(),
    /** Index of the syllable with the main stress. */
    val stressed: Int = 0,
    val hasCapitals: Boolean = false,
    /** The /phonemes/ form, slashes removed. */
    val ipa: String? = null,
    /** ARPAbet, once the converter has resolved the respelling. */
    val phones: List<String>? = null,
) {
    /** The respelling's letters, run together (vowel-spelling cues for IPA). */
    val spelling: String get() = pieces.joinToString("").lowercase()

    /** As the golden fixture's `resolved` writes it: ARPAbet, or /phonemes/. */
    val resolved: String get() = if (ipa != null) "/$ipa/" else phones.orEmpty().joinToString(" ")
}

/**
 * Parsing and checking of the owner's "say it like" list: a port of
 * `domovoi/phonemizer/pronunciations.py`. Resolving a respelling to
 * ARPAbet needs the dictionary and the model, so it lives in [Phonemizer].
 *
 * Text form, one entry per line, `#` comments: `Nguyen = win`,
 * `Domovoi = DOH-moe-voy`, or the advanced `Domovoi = /dˈəʊməʊvɔɪ/`.
 */
object Pronunciations {
    const val MAX_WORD_CHARS = 48
    const val MAX_SAY_CHARS = 100
    const val MAX_SYLLABLES = 12

    private val PIECE_RE = Regex("""[A-Za-z]+(?:'[A-Za-z]+)*'?""")
    private val SYLLABLE_SPLIT = Regex("""(?:$S|-)+""")

    private fun repr(s: String) = "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    /** The normalized word an entry matches. */
    fun key(word: String?): String {
        val w = Py.strip(word ?: "")
        if (w.isEmpty()) throw PronunciationException("the word is empty")
        if (w.codePointCount(0, w.length) > MAX_WORD_CHARS) {
            throw PronunciationException("${w.take(20)}...: a word is at most $MAX_WORD_CHARS letters")
        }
        if (!Normalize.TOKEN_RE.matches(w) || w.startsWith("<") || w.none { it.isLetter() }) {
            throw PronunciationException("$w: one word only (letters, with ' or - inside), as it is written in text")
        }
        return Py.stripAccents(Py.nfc(w)).lowercase()
    }

    /** Checks one entry; the result has no [Pronunciation.phones] yet
     *  (the converter resolves a respelling). [idMap]: the voices' symbols. */
    fun parseSayItLike(word: String, say: String?, idMap: Map<String, IntArray>): Pronunciation {
        val key = key(word)
        val s = Py.strip(say ?: "")
        if (s.isEmpty()) throw PronunciationException("$word: say it like what?")
        if (s.codePointCount(0, s.length) > MAX_SAY_CHARS) throw PronunciationException("$word: at most $MAX_SAY_CHARS characters")
        if (s.startsWith("/")) {
            if (s.length < 3 || !s.endsWith("/")) {
                throw PronunciationException("$word: phonemes go between two slashes, like /wɪn/")
            }
            val ipa = Py.strip(s.substring(1, s.length - 1))
            val bad = mutableListOf<String>()
            val nfd = Py.nfd(ipa)
            var i = 0
            while (i < nfd.length) {
                val cp = nfd.codePointAt(i)
                val ch = String(Character.toChars(cp))
                if ((ch !in idMap || ch == PhonemeIds.BOS || ch == PhonemeIds.EOS || ch == PhonemeIds.PAD) && ch !in bad) bad += ch
                i += Character.charCount(cp)
            }
            if (ipa.isEmpty() || bad.isNotEmpty()) {
                val what = bad.joinToString(" ") { repr(it) }.ifEmpty { "an empty phoneme string" }
                throw PronunciationException("$word: the voices don't know $what")
            }
            return Pronunciation(word = Py.strip(word), key = key, sayItLike = s, ipa = ipa)
        }
        val pieces = SYLLABLE_SPLIT.split(s).filter { it.isNotEmpty() }
        if (pieces.size > MAX_SYLLABLES) throw PronunciationException("$word: at most $MAX_SYLLABLES syllables")
        for (p in pieces) {
            if (!PIECE_RE.matches(p)) {
                throw PronunciationException("$word: write ${repr(s)} in plain letters (a-z), syllables split by - or spaces")
            }
        }
        val upper = pieces.map { Py.isUpper(it) }
        val hasCapitals = upper.any { it } && !upper.all { it }
        val stressed = if (hasCapitals) upper.indexOf(true) else 0
        return Pronunciation(
            word = Py.strip(word), key = key, sayItLike = s, pieces = pieces,
            stressed = stressed, hasCapitals = hasCapitals,
        )
    }

    /** `Word = say it like` lines -> (word, say it like) pairs, in order.
     *  Blank lines and `#` comment lines are skipped; only the shape is
     *  checked here. */
    fun parseText(text: String?): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        (text ?: "").lines().forEachIndexed { index, raw ->
            val line = Py.strip(raw)
            if (line.isEmpty() || line.startsWith("#")) return@forEachIndexed
            val eq = line.indexOf('=')
            val word = if (eq < 0) line else line.substring(0, eq)
            val say = if (eq < 0) "" else line.substring(eq + 1)
            if (eq < 0 || Py.strip(word).isEmpty() || Py.strip(say).isEmpty()) {
                throw PronunciationException("line ${index + 1}: expected 'Word = say it like', got ${repr(line)}")
            }
            out += Py.strip(word) to Py.strip(say)
        }
        return out
    }

    /** (word, say it like) pairs -> the text form, one entry per line. */
    fun formatText(pairs: List<Pair<String, String>>): String =
        pairs.joinToString("") { (w, s) -> "$w = $s\n" }

    /*
     * A respelling syllable no dictionary knows ("shuh", "zhahn", "voy") is
     * read with this key before the out-of-dictionary model, which reads it
     * as a word ("shuh" came out SH UW1): the conventions of dictionaries'
     * and Wikipedia's pronunciation respellings. Consonants, one vowel
     * spelling, consonants; anything else is left to the model. The same
     * tables as pronunciations.py's RESPELL_*.
     */
    private val RESPELL_VOWELS = mapOf(
        "eye" to "AY", "igh" to "AY", "air" to "EH R", "ear" to "IH R", "eer" to "IH R", "oor" to "UH R", "our" to "AW R",
        "ahr" to "AA R", "ah" to "AA", "ai" to "EY", "ar" to "AA R", "au" to "AO", "aw" to "AO", "ay" to "EY",
        "ea" to "IY", "ee" to "IY", "eh" to "EH", "er" to "ER", "ew" to "Y UW", "ih" to "IH", "ir" to "ER",
        "oa" to "OW", "oe" to "OW", "oh" to "OW", "oi" to "OY", "oo" to "UW", "or" to "AO R", "ou" to "AW", "ow" to "AW",
        "oy" to "OY", "uh" to "AH", "ur" to "ER", "uu" to "UH",
        "a" to "AE", "e" to "EH", "i" to "IH", "o" to "AA", "u" to "AH",
    )
    /** A lone vowel letter ending the syllable: "pa", "mo", "ki", "zu". */
    private val RESPELL_OPEN_VOWELS = mapOf("a" to "AA", "e" to "EH", "i" to "IY", "o" to "OW", "u" to "UW")
    private val RESPELL_CONSONANTS = mapOf(
        "dzh" to "JH", "tch" to "CH", "ch" to "CH", "ck" to "K", "dh" to "DH", "dz" to "D Z", "gh" to "G", "kh" to "K",
        "ng" to "NG", "nk" to "NG K", "ph" to "F", "qu" to "K W", "sh" to "SH", "th" to "TH", "ts" to "T S", "wh" to "W",
        "zh" to "ZH",
        "b" to "B", "c" to "K", "d" to "D", "f" to "F", "g" to "G", "h" to "HH", "j" to "JH", "k" to "K", "l" to "L",
        "m" to "M", "n" to "N", "p" to "P", "q" to "K", "r" to "R", "s" to "S", "t" to "T", "v" to "V", "w" to "W",
        "x" to "K S", "y" to "Y", "z" to "Z",
    )
    private const val RESPELL_LETTER_VOWELS = "aeiou"
    private val RESPELL_VOWEL_PHONES = setOf("AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW")

    private fun longest(s: String, i: Int, table: Map<String, String>): String? {
        for (n in 3 downTo 1) {
            if (i + n <= s.length && s.substring(i, i + n) in table) return s.substring(i, i + n)
        }
        return null
    }

    /** One respelling syllable -> ARPAbet by the key above (its vowel with
     *  stress 1, a doubled sound said once), or null when it is not
     *  consonants, one vowel spelling, consonants. */
    fun respellArpa(syllable: String): List<String>? {
        val s = syllable.lowercase()
        if (s.isEmpty() || !s.all { it in 'a'..'z' }) return null
        val onset = mutableListOf<String>()
        val coda = mutableListOf<String>()
        var i = 0
        while (i < s.length && s[i] !in RESPELL_LETTER_VOWELS) {
            val m = longest(s, i, RESPELL_CONSONANTS)!!
            onset += RESPELL_CONSONANTS.getValue(m).split(' ')
            i += m.length
        }
        if (i == s.length) return null
        val v = longest(s, i, RESPELL_VOWELS)!!
        i += v.length
        while (i < s.length) {
            if (s[i] in RESPELL_LETTER_VOWELS) return null
            val m = longest(s, i, RESPELL_CONSONANTS)!!
            coda += RESPELL_CONSONANTS.getValue(m).split(' ')
            i += m.length
        }
        val vowel = (if (v.length == 1 && coda.isEmpty()) RESPELL_OPEN_VOWELS.getValue(v) else RESPELL_VOWELS.getValue(v))
            .split(' ').map { if (it in RESPELL_VOWEL_PHONES) it + "1" else it }
        val out = mutableListOf<String>()
        for (p in onset + vowel + coda) {
            if (out.isEmpty() || out.last().trimEnd('0', '1', '2') != p.trimEnd('0', '1', '2')) out += p
        }
        return out
    }
}

/**
 * Phoneme string -> the ids a Piper voice reads, Piper's rule
 * (`domovoi/phonemizer/phoneme_ids.py`): NFD, then `^ _`, each known code
 * point's ids followed by `_`, then `$`. A code point the map lacks is
 * skipped. Norman, kristin and ljspeech share one map.
 */
object PhonemeIds {
    const val BOS = "^"
    const val EOS = "$"
    const val PAD = "_"

    fun ids(phonemes: String, map: Map<String, IntArray>): IntArray {
        val pad = map.getValue(PAD)
        val out = ArrayList<Int>(phonemes.length * 2 + 4)
        map.getValue(BOS).forEach { out += it }
        pad.forEach { out += it }
        val nfd = Py.nfd(phonemes)
        var i = 0
        while (i < nfd.length) {
            val cp = nfd.codePointAt(i)
            i += Character.charCount(cp)
            val got = map[String(Character.toChars(cp))] ?: continue
            got.forEach { out += it }
            pad.forEach { out += it }
        }
        map.getValue(EOS).forEach { out += it }
        return out.toIntArray()
    }

    /** What a symbol becomes for a voice whose map lacks it: the first
     *  candidate whose every code point the map has, else nothing (the
     *  same table as phoneme_ids.py's FALLBACKS). */
    val FALLBACKS: Map<String, List<String>> = mapOf(
        "ɾ" to listOf("t", "d"), "ᵻ" to listOf("ɪ", "i", "ə"), "ɚ" to listOf("əɹ", "ər", "ə"),
        "ɐ" to listOf("ə", "a"), "æ" to listOf("a", "ɛ"), "a" to listOf("æ", "ɐ"),
        "ɒ" to listOf("ɑ", "ɔ", "o"), "ɔ" to listOf("ɒ", "o"), "ɑ" to listOf("a", "ɒ"), "ɜ" to listOf("ə", "ɛ"),
        "ʌ" to listOf("ə", "a"), "ə" to listOf("ɐ", "e"), "ɛ" to listOf("e"), "e" to listOf("ɛ"),
        "ɪ" to listOf("i"), "i" to listOf("ɪ"), "ʊ" to listOf("u"), "u" to listOf("ʊ"), "o" to listOf("ɔ", "ʊ"),
        "ɹ" to listOf("r", "ɾ"), "ɡ" to listOf("g"), "ŋ" to listOf("n"), "θ" to listOf("t"), "ð" to listOf("d"),
        "ʃ" to listOf("s"), "ʒ" to listOf("z", "ʃ"), "ː" to listOf(""), "ˈ" to listOf(""), "ˌ" to listOf(""),
        "j" to listOf("i"),
    )

    /** [phonemes] (NFD) with each code point [map] lacks replaced by its
     *  first [FALLBACKS] candidate the map has wholly, else dropped, so a
     *  voice trained on fewer symbols says the nearest sound. [memo] (one
     *  per voice): missing code point -> what took its place, filled as
     *  they turn up. */
    fun fit(phonemes: String, map: Map<String, IntArray>, memo: MutableMap<String, String> = HashMap()): String {
        val nfd = Py.nfd(phonemes)
        val out = StringBuilder(nfd.length)
        var i = 0
        while (i < nfd.length) {
            val cp = nfd.codePointAt(i)
            i += Character.charCount(cp)
            val ch = String(Character.toChars(cp))
            if (ch in map) {
                out.append(ch)
                continue
            }
            val sub = synchronized(memo) {
                memo.getOrPut(ch) {
                    FALLBACKS[ch].orEmpty().firstOrNull { c -> c.all { x -> x.toString() in map } } ?: ""
                }
            }
            out.append(sub)
        }
        return out.toString()
    }

    /** Whether a sentence's ids hold any sound: something besides the
     *  begin, end and pad ids and the map's space and punctuation. */
    fun speaksAnything(ids: IntArray, map: Map<String, IntArray>): Boolean {
        val quiet = HashSet<Int>()
        for (sym in listOf(BOS, EOS, PAD, " ", ",", ".", "!", "?", ";", ":", "ˈ", "ˌ")) map[sym]?.forEach { quiet += it }
        return ids.any { it !in quiet }
    }

    /** Code points of [phonemes] (NFD) the map lacks, in order, once each. */
    fun unknownSymbols(phonemes: String, map: Map<String, IntArray>): List<String> {
        val out = mutableListOf<String>()
        val nfd = Py.nfd(phonemes)
        var i = 0
        while (i < nfd.length) {
            val cp = nfd.codePointAt(i)
            i += Character.charCount(cp)
            val ch = String(Character.toChars(cp))
            if (ch !in map && ch !in out) out += ch
        }
        return out
    }
}
