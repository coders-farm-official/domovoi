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
