package com.domovoi.app.assistant

/**
 * A "play" whose words name nothing in the library, but are a near spelling
 * of one name in it (Fold check 2026-10-21: "play Outcast" for OutKast).
 * The phone never plays a guess: it asks "Did you mean OutKast?", and only a
 * yes plays it.
 *
 * Conservative on purpose: the whole of what was said against the whole of
 * an artist, album or title (never a word inside one), the same first
 * letter, at least four letters, and the same sound ([sound]: "c" and "k",
 * "ph" and "f", doubled letters, vowels after the first) within one edit
 * (two from five letters; a swap of two letters is one); from eight letters
 * up, two edits whatever the sound, or three with the same sound. Exactly
 * one name may be closest, or nothing is offered.
 */
object MusicNearMatch {
    private const val MIN_CHARS = 4

    /** Lower case letters and digits only, a leading "the" off. */
    internal fun key(s: String): String {
        val words = MusicEvidence.words(s)
        return words.joinToString("").filter { it.isLetterOrDigit() }
    }

    /** The name in [names] that [said] is a near spelling of, or null (none,
     *  more than one as close, or one that matches exactly: that one isn't
     *  a guess). */
    fun best(said: String, names: Collection<String>): String? {
        val a = key(said)
        if (a.length < MIN_CHARS) return null
        val clean = names.map { it.trim() }.filter { it.isNotEmpty() }.distinctBy { key(it) }
        if (clean.any { key(it) == a }) return null
        val scored = clean.mapNotNull { n -> score(a, key(n))?.let { n to it } }
        if (scored.isEmpty()) return null
        val top = scored.minOf { it.second }
        val best = scored.filter { it.second == top }
        return best.singleOrNull()?.first
    }

    /** How near [b] is to [a] (lower is nearer), or null: not near enough. */
    internal fun score(a: String, b: String): Int? {
        if (b.length < MIN_CHARS || a[0] != b[0]) return null
        if (kotlin.math.abs(a.length - b.length) > 2) return null
        val d = distance(a, b)
        val same = sound(a) == sound(b)
        // Short names: one edit and the same sound ("hope" is not "home").
        if (maxOf(a.length, b.length) < 8) return if (d <= 1 && same || a.length >= 5 && d <= 2 && same) d else null
        if (d <= 2) return d
        if (d <= 3 && same) return d
        return null
    }

    /** Edits between [a] and [b]: insert, delete, change, or swap two
     *  letters side by side (optimal string alignment). */
    internal fun distance(a: String, b: String): Int {
        val d = Array(a.length + 1) { IntArray(b.length + 1) }
        for (i in 0..a.length) d[i][0] = i
        for (j in 0..b.length) d[0][j] = j
        for (i in 1..a.length) for (j in 1..b.length) {
            val cost = if (a[i - 1] == b[j - 1]) 0 else 1
            var v = minOf(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if (i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1]) v = minOf(v, d[i - 2][j - 2] + 1)
            d[i][j] = v
        }
        return d[a.length][b.length]
    }

    /** A rough English sound key: the first letter, then consonants with
     *  like sounds made one, doubles and vowels gone. */
    internal fun sound(s: String): String {
        var t = s.lowercase()
            .replace("ph", "f").replace("ck", "k").replace("qu", "kw").replace("x", "ks")
            .replace(Regex("c(?=[eiy])"), "s").replace('c', 'k').replace('q', 'k').replace('z', 's')
            .replace("gh", "g").replace("wr", "r").replace("kn", "n")
        val first = t.firstOrNull() ?: return ""
        t = t.drop(1).filter { it !in "aeiouyhw" }
        val out = StringBuilder().append(first)
        for (c in t) if (out.last() != c) out.append(c)
        return out.toString()
    }
}
