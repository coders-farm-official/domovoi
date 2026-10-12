package com.domovoi.app.phonemizer

/**
 * Domovoi's permissive English text-to-phoneme converter for its Piper
 * voices (norman, kristin, ljspeech, all trained on espeak-ng's British
 * "en" symbols): a Kotlin port of `domovoi/phonemizer/convert.py` that must
 * produce exactly what the Python one does, case for case, on the shared
 * fixture `domovoi/phonemizer/golden.json` (PhonemizerGoldenTest).
 *
 * normalize -> words -> ARPAbet (the owner's "say it like" list, context
 * rules for heteronyms, CMUdict + corrections, compound split, the
 * out-of-dictionary model, spelling) -> IPA with stress in the "en"
 * conventions -> one phoneme string per sentence.
 *
 * Build it once and share it: [phonemize] is safe from several threads,
 * and [setPronunciations] swaps the owner's list in one assignment.
 * Licences: CMUdict BSD-2-Clause style; everything else Domovoi's (MIT).
 */
class Phonemizer(
    private val data: PhonemizerData,
    private val model: G2pModel?,
    pronunciations: List<Pair<String, String>>? = null,
) {
    private val modelCache = HashMap<String, List<String>>()

    @Volatile
    private var pron: Map<String, Pronunciation> = emptyMap()

    init {
        setPronunciations(pronunciations ?: data.defaultPronunciations)
    }

    /** Whether words in no dictionary go through the model (else spelled). */
    val hasModel: Boolean get() = model != null

    val idMap: Map<String, IntArray> get() = data.idMap

    // ---------------- the owner's "say it like" list ----------------

    /** Replaces the list ((word, say it like) pairs; a later pair for the
     *  same word wins). Throws [PronunciationException] naming the first
     *  entry it can't use; the old list stays in force then. */
    fun setPronunciations(entries: List<Pair<String, String>>) {
        val resolved = LinkedHashMap<String, Pronunciation>()
        for ((word, say) in entries) {
            val p = resolvePronunciation(word, say)
            resolved[p.key] = p
        }
        pron = resolved
    }

    /** The list in force: key (normalized word) -> entry. */
    fun pronunciations(): Map<String, Pronunciation> = LinkedHashMap(pron)

    /** One entry -> ARPAbet read from the respelling, or the /phonemes/ form. */
    fun resolvePronunciation(word: String, say: String): Pronunciation {
        val entry = Pronunciations.parseSayItLike(word, say, data.idMap)
        if (entry.ipa != null) return entry
        val phones = mutableListOf<String>()
        entry.pieces.forEachIndexed { k, piece ->
            val ph = wordArpa(piece.lowercase()).first.toMutableList()
            if (k == entry.stressed) {
                if (ph.none { it.endsWith("1") }) {
                    val j = ph.indexOfFirst { isVowel(it) }
                    if (j >= 0) ph[j] = base(ph[j]) + "1"
                }
            } else {
                val capital = entry.hasCapitals && Py.isUpper(piece)
                for (j in ph.indices) {
                    val p = ph[j]
                    if (!isVowel(p)) continue
                    if (!capital && base(p) in setOf("AH", "ER")) {
                        ph[j] = base(p) + "0"   // an unstressed "uh" / "er" is a schwa
                    } else if (p.endsWith("1")) {
                        ph[j] = base(p) + "2"
                    }
                }
            }
            phones += ph
        }
        if (phones.none { isVowel(it) }) {
            throw PronunciationException("$word: '$say' has no vowel sound")
        }
        return entry.copy(phones = phones.toList())
    }

    // ---------------- word -> ARPAbet ----------------

    private fun lexPhones(w: String): List<String>? = data.lex[w]?.let { if (it.isEmpty()) emptyList() else it.split(' ') }

    fun modelArpa(word: String): List<String>? {
        val m = model ?: return null
        synchronized(modelCache) { modelCache[word]?.let { return it.toList() } }
        val ids = word.mapNotNull { data.vocab.letters[it] }.take(data.vocab.maxChars)
        if (ids.isEmpty()) return null
        val frames = m.logits(ids.toLongArray())
        val out = mutableListOf<String>()
        var prev = 0
        for (frame in frames) {
            var best = 0
            for (k in 1 until frame.size) if (frame[k] > frame[best]) best = k
            if (best != prev && best != 0) out += data.vocab.phones[best]
            prev = best
        }
        synchronized(modelCache) {
            if (modelCache.size >= MODEL_CACHE_MAX) modelCache.clear()
            modelCache[word] = out.toList()
        }
        return out
    }

    fun heteronym(w: String, prev: String, nxt: String, initial: Boolean): String? = when (w) {
        "read" -> when {
            prev in MODAL_TO || initial -> "R IY1 D"
            prev in PERFECT || prev in SUBJ -> "R EH1 D"
            else -> "R IY1 D"
        }
        "live" -> if (prev in MODAL_TO || prev in SUBJ || prev in setOf("where", "that", "which")) "L IH1 V" else "L AY1 V"
        "close" -> if (nxt in setOf("to", "by") || prev in CLOSE_S) "K L OW1 S" else "K L OW1 Z"
        "wind" -> if (prev in MODAL_TO) "W AY1 N D" else "W IH1 N D"
        "record" -> if (prev in DET) "R EH1 K ER0 D" else "R IH0 K AO1 R D"
        "use" -> if (prev in DET) "Y UW1 S" else "Y UW1 Z"
        "lead" -> "L IY1 D"
        "tear" -> if (prev in DET) "T IH1 R" else "T EH1 R"
        else -> null
    }

    /** (phones, source). */
    fun wordArpa(w: String, prev: String = "", nxt: String = "", initial: Boolean = false): Pair<List<String>, String> {
        heteronym(w, prev, nxt, initial)?.let { return it.split(' ') to "rule" }
        lexPhones(w)?.let { return it to "lex" }
        if (w.endsWith("'s")) {
            lexPhones(w.dropLast(2))?.let { b -> return (b + possessiveArpa(b)) to "lex+s" }
        }
        if (w.endsWith("s'")) {
            lexPhones(w.dropLast(1))?.let { return it to "lex" }
        }
        if ('-' in w) {
            val parts = w.split('-').filter { it.isNotEmpty() }
            val out = mutableListOf<String>()
            parts.forEachIndexed { k, p ->
                val ph = wordArpa(p).first
                out += if (k == 0) ph else ph.map { it.replace("1", "2") }
            }
            return out to "hyphen"
        }
        val cw = w.replace("'", "")
        if (cw.length >= 8) {
            var best: Triple<Int, String, String>? = null
            for (i in 4 until cw.length - 3) {
                val a = cw.substring(0, i)
                val b = cw.substring(i)
                if (a in data.lex && b in data.lex) {
                    val score = minOf(a.length, b.length)
                    if (best == null || score > best.first) best = Triple(score, a, b)
                }
            }
            if (best != null) {
                return (lexPhones(best.second)!! + lexPhones(best.third)!!.map { it.replace("1", "2") }) to "compound"
            }
        }
        val ph = modelArpa(w)
        if (ph != null && ph.any { isVowel(it) }) return ph to "model"
        val out = mutableListOf<String>()
        for (c in w) LETTERS[c]?.let { out += it.split(' ') }
        return out to "spell"
    }

    // ---------------- ARPAbet -> IPA ("en" conventions) ----------------

    private fun toIpa(phones: List<String>, word: String?, nextVowel: Boolean = false, keepPostSecondary: Boolean = false): String {
        val al = if (!word.isNullOrEmpty()) align(word, phones) else emptyMap()
        val w = (word ?: "").lowercase()
        val out = StringBuilder()
        val n = phones.size
        val smap = stressMarks(phones, keepPostSecondary)
        var prim = false
        var i = 0
        while (i < n) {
            val p = phones[i]
            val b = base(p)
            val st = p.substring(b.length)
            if (b !in VOWELS) {
                if (b == "N" && i + 1 < n && base(phones[i + 1]) in setOf("K", "G")) {
                    out.append("ŋ")   // velar assimilation; espeak never writes "nk"/"ng"
                } else {
                    out.append(CONS_IPA[b] ?: "")
                }
                i++
                continue
            }
            val g = al[i]
            val grp = g?.second
            val gs = g?.first ?: 0
            val nb = if (i + 1 < n) base(phones[i + 1]) else null
            val r = nb == "R"
            val rPrev = if (r) {
                val after = if (i + 2 < n) phones[i + 2] else null
                if (after != null) isVowel(after) else nextVowel
            } else {
                false
            }
            val (mark, sd) = smap.getValue(i)
            val stressed = (st == "1" || st == "2") && !(b == "IY" && st == "2" && sd == '0' && i >= n - 2)
            var v = vowel(b, stressed, grp, gs, w, r, rPrev, phones, i)
            if (sd == '1') prim = true
            if (i > 0 && base(phones[i - 1]) in setOf("T", "D", "N") && !grp.isNullOrEmpty() &&
                ((b in setOf("UW", "UH") && grp[0] == 'u') || (b == "UW" && grp in setOf("ue", "ew", "eu")))
            ) {
                out.append("j")
            }
            if (b == "ER") {
                val after = if (i + 1 < n) phones[i + 1] else null
                v = if (stressed) "ɜː" else if (i == 0 && grp == "a") "ɐ" else "ə"
                if ((after != null && isVowel(after)) || (after == null && nextVowel)) v += "ɹ"
                out.append(mark).append(v)
                i++
                continue
            }
            out.append(mark).append(v)
            if (r) {
                if (rPrev) out.append("ɹ")
                i += 2
                continue
            }
            i++
        }
        return out.toString()
    }

    // ---------------- sentences ----------------

    /** Text -> one Piper phoneme string per sentence. */
    fun phonemize(text: String): List<String> = phonemizeDetailed(text).map { it.first }

    /** One word of a sentence, as the converter saw it. */
    class Item internal constructor(
        val kind: Kind,
        val w: String? = null,
        val letters: List<Char> = emptyList(),
        val p: String? = null,
    ) {
        enum class Kind { WORD, SPELL, PUNCT }

        var ph: List<String> = emptyList()
            internal set
        /** Where the pronunciation came from: say, rule, lex, lex+s, fw,
         *  hyphen, compound, model, spell. */
        var src: String = ""
            internal set
        internal var fixed: String? = null
        internal var align: String? = null
        var ipa: String = ""
            internal set
    }

    /** Text -> (sentence phoneme string, its words). */
    fun phonemizeDetailed(text: String): List<Pair<String, List<Item>>> {
        val pron = this.pron
        val res = mutableListOf<Pair<String, List<Item>>>()
        for (sent in Normalize.normalize(text)) {
            val items = sent.map { t ->
                when {
                    t is Token.Word && t.spell && t.w !in pron -> Item(Item.Kind.SPELL, letters = t.w.toList())
                    t is Token.Word -> Item(Item.Kind.WORD, w = t.w)
                    else -> Item(Item.Kind.PUNCT, p = (t as Token.Punct).p)
                }
            }
            val words = items.filter { it.kind != Item.Kind.PUNCT }
            for (it in items) {
                if (it.kind != Item.Kind.SPELL) continue
                val ph = mutableListOf<String>()
                val l = it.letters
                l.forEachIndexed { j, c ->
                    var lp = LETTERS[c]?.split(' ') ?: emptyList()
                    if (j < l.size - 1) lp = lp.map { x -> x.replace("1", "2") }
                    ph += lp
                }
                it.ph = ph
                it.src = "spell"
            }
            words.forEachIndexed { k, it ->
                if (it.kind != Item.Kind.WORD) return@forEachIndexed
                val w = it.w!!
                var p = pron[w]
                var poss = false
                if (p == null && w.endsWith("'s")) {
                    p = pron[w.dropLast(2)]
                    poss = p != null
                }
                if (p != null) {
                    it.src = "say"
                    if (p.ipa != null) {
                        it.ph = emptyList()
                        it.fixed = p.ipa + (if (poss) possessiveIpa(p.ipa) else "")
                    } else {
                        val ph = p.phones.orEmpty()
                        it.ph = ph + (if (poss) possessiveArpa(ph) else emptyList())
                        it.align = p.spelling + (if (poss) "'s" else "")
                    }
                    return@forEachIndexed
                }
                val prev = if (k > 0) words[k - 1].w ?: "" else ""
                val nxt = if (k + 1 < words.size) words[k + 1].w ?: "" else ""
                val (ph, src) = wordArpa(w, prev, nxt, k == 0)
                it.ph = ph
                it.src = src
            }
            // IPA
            val out = mutableListOf<String>()
            items.forEachIndexed { k, it ->
                if (it.kind == Item.Kind.PUNCT) {
                    if (out.isNotEmpty() && out.last() == " ") out.removeAt(out.size - 1)
                    out += it.p!!
                    out += " "
                    return@forEachIndexed
                }
                val nxtIt = items.getOrNull(k + 1)
                val clauseFinal = nxtIt == null || nxtIt.kind == Item.Kind.PUNCT
                val nv = !clauseFinal && startsWithVowel(nxtIt!!)
                var ipa: String
                val fixed = it.fixed
                if (fixed != null) {
                    ipa = fixed
                } else if (it.kind == Item.Kind.WORD && it.w in data.fw && (it.src == "lex" || it.src == "rule")) {
                    val (d, v, f) = data.fw.getValue(it.w!!)
                    ipa = if (clauseFinal) f else if (nv) v else d
                    it.src = "fw"
                } else {
                    ipa = toIpa(it.ph, if (it.kind == Item.Kind.WORD) (it.align ?: it.w) else null, nv,
                        keepPostSecondary = it.kind == Item.Kind.SPELL)
                    if (it.kind == Item.Kind.WORD && ipa.none { c -> c == 'ˈ' || c == 'ˌ' } && it.src != "fw") {
                        ipa = forceStress(ipa)
                    }
                }
                it.ipa = ipa
                // espeak-style joins: "of the", "in the", "on the", "from the"
                if (it.kind == Item.Kind.WORD && it.w == "the" && k > 0 &&
                    items[k - 1].w in setOf("of", "in", "on", "from") && out.isNotEmpty() && out.last() == " "
                ) {
                    out.removeAt(out.size - 1)
                    if (items[k - 1].w == "from") out[out.size - 1] = "fɹʌm"
                }
                out += ipa
                out += " "
            }
            while (out.isNotEmpty() && out.last() == " ") out.removeAt(out.size - 1)
            res += out.joinToString("") to items
        }
        return res
    }

    private fun startsWithVowel(it: Item): Boolean {
        it.fixed?.let { return IPA_VOWEL_START.matchesAt(it) }
        return it.ph.isNotEmpty() && isVowel(it.ph[0])
    }

    companion object {
        private const val MODEL_CACHE_MAX = 20_000

        val VOWELS = setOf("AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW")
        private val CONS_IPA = mapOf(
            "B" to "b", "CH" to "tʃ", "D" to "d", "DH" to "ð", "F" to "f", "G" to "ɡ", "HH" to "h", "JH" to "dʒ", "K" to "k",
            "L" to "l", "M" to "m", "N" to "n", "NG" to "ŋ", "P" to "p", "R" to "ɹ", "S" to "s", "SH" to "ʃ", "T" to "t",
            "TH" to "θ", "V" to "v", "W" to "w", "Y" to "j", "Z" to "z", "ZH" to "ʒ",
        )
        private val LETTERS = mapOf(
            'a' to "EY1", 'b' to "B IY1", 'c' to "S IY1", 'd' to "D IY1", 'e' to "IY1", 'f' to "EH1 F", 'g' to "JH IY1",
            'h' to "EY1 CH", 'i' to "AY1", 'j' to "JH EY1", 'k' to "K EY1", 'l' to "EH1 L", 'm' to "EH1 M", 'n' to "EH1 N",
            'o' to "OW1", 'p' to "P IY1", 'q' to "K Y UW1", 'r' to "AA1 R", 's' to "EH1 S", 't' to "T IY1", 'u' to "Y UW1",
            'v' to "V IY1", 'w' to "D AH1 B AH0 L Y UW0", 'x' to "EH1 K S", 'y' to "W AY1", 'z' to "Z IY1",
            '0' to "Z IH1 R OW0", '1' to "W AH1 N", '2' to "T UW1", '3' to "TH R IY1", '4' to "F AO1 R", '5' to "F AY1 V",
            '6' to "S IH1 K S", '7' to "S EH1 V AH0 N", '8' to "EY1 T", '9' to "N AY1 N",
        )
        private val MODAL_TO = setOf(
            "to", "will", "would", "can", "could", "should", "must", "might", "may", "shall", "do", "does",
            "did", "don't", "didn't", "doesn't", "won't", "can't", "couldn't", "wouldn't", "shouldn't",
            "i'd", "you'd", "we'd", "they'd", "he'd", "she'd", "i'll", "you'll", "we'll", "they'll",
            "he'll", "she'll", "let's", "please", "and", "or", "gonna", "wanna", "let", "help",
        )
        private val PERFECT = setOf(
            "have", "has", "had", "i've", "you've", "we've", "they've", "was", "were", "been", "being",
            "is", "are", "be", "having", "already", "just", "never", "ever",
        )
        private val SUBJ = setOf("i", "you", "we", "they", "he", "she", "who", "people")
        private val DET = setOf(
            "a", "an", "the", "this", "that", "my", "your", "his", "her", "our", "their", "its", "new", "world",
            "vinyl", "track", "personal", "every", "each", "no",
        )
        private val CLOSE_S = setOf(
            "so", "very", "too", "pretty", "quite", "is", "are", "was", "be", "get", "getting", "how",
        )
        private val IH_CONTEXT = setOf("T", "D", "S", "Z", "K", "G", "JH", "CH", "SH", "NG", "F", "V", "P", "B", "TH")
        private const val IPA_VOWELS = "aeiouæɑɒɔəɛɜɪʊʌɐ"
        private val IPA_VOWEL_START = Regex("[ˈˌ]*[$IPA_VOWELS]")

        private fun Regex.matchesAt(s: String): Boolean = find(s)?.range?.first == 0

        fun base(p: String): String = p.trimEnd('0', '1', '2')

        fun isVowel(p: String): Boolean = base(p) in VOWELS

        /** The ARPAbet ending of "'s" after [phones]. */
        fun possessiveArpa(phones: List<String>): List<String> {
            val last = if (phones.isNotEmpty()) base(phones.last()) else ""
            if (last in setOf("S", "Z", "SH", "ZH", "CH", "JH")) return listOf("IH0", "Z")
            return if (last in setOf("P", "T", "K", "F", "TH")) listOf("S") else listOf("Z")
        }

        /** The IPA ending of "'s" after a phoneme string. */
        fun possessiveIpa(ipa: String): String {
            if (ipa.endsWith("s") || ipa.endsWith("z") || ipa.endsWith("ʃ") || ipa.endsWith("ʒ")) return "ɪz"
            return if (listOf("p", "t", "k", "f", "θ").any { ipa.endsWith(it) }) "s" else "z"
        }

        /** Vowel letter groups of [word] matched to the vowels of [phones], by
         *  index into phones: (start in the word, the group's letters). Empty
         *  when the counts disagree. */
        fun align(word: String?, phones: List<String>): Map<Int, Pair<Int, String>> {
            if (word.isNullOrEmpty()) return emptyMap()
            val w = word.lowercase()
            fun isV(i: Int): Boolean {
                val c = w[i]
                if (c in "aeiou") return true
                if (c == 'y') return i > 0
                if (c == 'w') return i > 0 && w[i - 1] in "aeo" && !(i + 1 < w.length && w[i + 1] in "aeiouy")
                return false
            }
            var groups = mutableListOf<Pair<Int, String>>()
            var i = 0
            while (i < w.length) {
                if (w[i].isLetter() && isV(i)) {
                    var j = i
                    while (j < w.length && w[j].isLetter() && isV(j)) j++
                    groups += i to w.substring(i, j)
                    i = j
                } else {
                    i++
                }
            }
            val vidx = phones.indices.filter { isVowel(phones[it]) }
            if (groups.size == vidx.size + 1) {
                val (s, t) = groups.last()
                if (t == "e" && (s == w.length - 1 || w.substring(s) in setOf("es", "ed", "e's"))) {
                    groups = groups.dropLast(1).toMutableList()
                }
            }
            if (groups.size != vidx.size) return emptyMap()
            return vidx.withIndex().associate { (j, k) -> k to groups[j] }
        }

        /** Per-vowel stress mark (mark, digit), by index into phones.
         *  espeak-style: a secondary stress right next to the primary
         *  syllable is dropped (okay, Tuesday, takeout); word-final CMUdict
         *  IY2 (company, thirty) is unstressed. */
        fun stressMarks(phones: List<String>, keepAll: Boolean = false): Map<Int, Pair<String, Char>> {
            val vi = phones.indices.filter { isVowel(phones[it]) }
            val st = vi.map { k -> phones[k].last().let { if (it in "012") it else '0' } }.toMutableList()
            if (vi.isNotEmpty() && base(phones[vi.last()]) == "IY" && st.last() == '2' && vi.last() >= phones.size - 2) {
                st[st.size - 1] = '0'
            }
            val p1 = st.indexOf('1')
            if (p1 >= 0) {
                for (j in st.indices) if (j != p1 && st[j] == '1') st[j] = '2'
                if (!keepAll) {
                    for (j in listOf(p1 - 1, p1 + 1)) if (j in st.indices && st[j] == '2') st[j] = '0'
                }
            }
            return vi.withIndex().associate { (j, k) ->
                k to (when (st[j]) { '1' -> "ˈ"; '2' -> "ˌ"; else -> "" } to st[j])
            }
        }

        private fun vowel(
            b: String, stressed: Boolean, grp: String?, gs: Int, w: String,
            r: Boolean, rPrev: Boolean, phones: List<String>, i: Int,
        ): String {
            when (b) {
                "AA" -> {
                    val oSp = grp != null && 'o' in grp
                    if (r && !rPrev) return "ɑː"
                    if (r) return if (oSp) "ɒ" else "ɑː"
                    if (grp == null) return "ɒ"
                    if (oSp || grp.startsWith("ua")) return "ɒ"
                    val pre = Py.slice(w, maxOf(0, gs - 2), gs)
                    if (pre.endsWith("w") || pre.endsWith("qu")) return "ɒ"
                    return "ɑː"
                }
                "AO" -> {
                    if (r && !rPrev) return "ɔː"
                    if (r) return if (grp == "o" && Py.slice(w, gs + 1, gs + 3) == "rr") "ɒ" else "ɔː"
                    return if (grp == "o") "ɒ" else "ɔː"
                }
                "AE" -> return if (r && !rPrev) "eə" else "a"
                "EH" -> {
                    if (r && !rPrev) return "eə"
                    if (r && grp != null && grp.isNotEmpty() && 'a' in grp) return "eə"
                    return "ɛ"
                }
                "IH" -> {
                    if (r && !rPrev) return "iə"
                    if (r && !grp.isNullOrEmpty() && grp != "i" && grp != "y") return "iə"
                    return "ɪ"
                }
                "IY" -> {
                    if (r) return "iə"
                    if (stressed) return "iː"
                    val nxt = if (i + 1 < phones.size) phones[i + 1] else null
                    if (nxt == null || isVowel(nxt)) return "i"
                    if (i + 2 == phones.size && base(nxt) in setOf("Z", "S", "D")) return "i"
                    return "ɪ"
                }
                "UH" -> return if (r) "ʊə" else "ʊ"
                "UW" -> return if (r) "ʊə" else "uː"
                "AH" -> {
                    if (stressed) return "ʌ"
                    val nxt = if (i + 1 < phones.size) base(phones[i + 1]) else null
                    if (grp == "a" && i == 0) return "ɐ"
                    if (grp == "i") return "ɪ"
                    if (grp == "e" && nxt in IH_CONTEXT) return "ɪ"
                    if (grp == "a" && nxt == "JH") return "ɪ"
                    return "ə"
                }
                "AW" -> return if (r) "aʊə" else "aʊ"
                "AY" -> return if (r) "aɪə" else "aɪ"
                "EY" -> return if (r) "eə" else "eɪ"
                "OW" -> return if (r) "ɔː" else "əʊ"
                "OY" -> return "ɔɪ"
            }
            return "ə"
        }

        private fun forceStress(ipa: String): String {
            val at = ipa.indexOfFirst { it in IPA_VOWELS }
            if (at < 0) return ipa
            return ipa.substring(0, at) + "ˈ" + ipa.substring(at)
        }
    }
}
