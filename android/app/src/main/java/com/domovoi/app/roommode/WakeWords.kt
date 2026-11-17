package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.put

/**
 * Several wake words in one room (1.4.0; the Pi's satellite/wake_words.py,
 * the core's domovoi/wake_default.py): which words, and how many.
 *
 * A room wakes on up to [MAX_WORDS] words, main word first. The main word
 * is the one `set_wake_word.slug` names (the one an older core knows
 * about) and wakes at the room's threshold ([RoomStore.wakeThreshold]); an
 * extra word wakes at its own threshold when the push gave it one, else at
 * the room's. Every word has the same patience and cooldown ([WakeGates]).
 *
 * openWakeWord runs every word on one audio front end (the melspectrogram
 * and the speech embedding, the expensive part); each word adds only its
 * own small classifier ([WakeDetector]). Measured on a desktop: 1.79 ms
 * front end vs 0.012-0.026 ms per word per 80 ms chunk, ~1 MB each. So the
 * CPU allows five on any phone that runs one; what an extra word does cost
 * is false wakes (each can fire on its own).
 *
 * **How many this phone runs** ([limitFor]): [MAX_WORDS], or
 * [SMALL_MAX_WORDS] on a 32-bit phone (armeabi-v7a only) or one with about
 * 2 GB of memory or less: the phones that are already the slowest at
 * keeping up with the mic, as the Pi Zero 2 W is on the Pi side. The hello
 * says it (`wake_words_max`); the core never pushes more, and the phone
 * loads no more whatever it is sent ([plan]).
 */
object WakeWords {
    /** The most wake words a room may have (the core's MAX_ROOM_WORDS). */
    const val MAX_WORDS = 5

    /** The most a small phone runs (32-bit, or ~2 GB of memory). */
    const val SMALL_MAX_WORDS = 3

    /**
     * Memory at or under this is a "2 GB phone". `ActivityManager`'s
     * `totalMem` is what the kernel hands out, a little under the label
     * (a 2 GB phone reports ~1.8-1.95 GiB, a 3 GB one ~2.7-2.9 GiB), so the
     * line sits between the two.
     */
    const val SMALL_RAM_BYTES = 2_560L * 1024 * 1024

    /** One word the room listens for: [threshold] its own (0-1), or null
     *  for the room's. */
    data class Word(val slug: String, val threshold: Float? = null)

    /**
     * How many words this phone runs at once. [is64Bit]: the phone (and
     * this process) run 64-bit code; [totalRamBytes]: its memory (null:
     * unknown, not held against it); [lowRam]: Android says it is a low-RAM
     * device (Android Go).
     */
    fun limitFor(is64Bit: Boolean, totalRamBytes: Long?, lowRam: Boolean = false): Int = when {
        !is64Bit -> SMALL_MAX_WORDS
        lowRam -> SMALL_MAX_WORDS
        totalRamBytes != null && totalRamBytes in 1..SMALL_RAM_BYTES -> SMALL_MAX_WORDS
        else -> MAX_WORDS
    }

    /** A word's own threshold as the room keeps it: 0-1, else null (the
     *  room's). The Pi's `clean_threshold`. */
    fun threshold(raw: Double?): Float? = raw?.takeIf { it.isFinite() && it in 0.0..1.0 }?.toFloat()

    /**
     * A pushed `words` list (`[{"slug", "threshold"?}, ...]`, or bare
     * slugs, as the Pi takes it): slugs this phone can load
     * ([RoomModels.isSlug]: the model file stem), no repeats, at most
     * [MAX_WORDS], in order. Null when there is no list (an older core, a
     * one-word room): the phone then runs `slug` alone.
     */
    fun parse(raw: JsonElement?): List<Word>? {
        if (raw !is JsonArray) return null
        val out = ArrayList<Word>()
        for (item in raw) {
            val (slug, t) = when (item) {
                is JsonObject -> (item["slug"] as? JsonPrimitive)?.takeIf { it.isString }?.content to
                    threshold((item["threshold"] as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull)
                is JsonPrimitive -> item.takeIf { it.isString }?.contentOrNull to null
                else -> null to null
            }
            val s = slug?.trim()
            if (!RoomModels.isSlug(s) || out.any { it.slug == s }) continue
            out += Word(s!!, t)
            if (out.size >= MAX_WORDS) break
        }
        return out
    }

    /**
     * What a room told [slug] (and maybe [words]) runs on a phone that runs
     * at most [max]: the list when it starts with [slug], cut to [max];
     * otherwise [slug] alone (no list: an older core or one word; a list
     * that doesn't start with the main word is not this push's).
     */
    fun plan(slug: String, words: List<Word>?, max: Int): List<Word> {
        val cap = max.coerceIn(1, MAX_WORDS)
        if (words.isNullOrEmpty() || words[0].slug != slug) return listOf(Word(slug))
        return words.distinctBy { it.slug }.take(cap)
    }

    /** The threshold each of [slugs] wakes at: the main word (the first)
     *  at [roomThreshold], an extra at its own in [words] or the room's. */
    fun thresholds(slugs: List<String>, words: List<Word>, roomThreshold: Float): List<Float> =
        slugs.mapIndexed { i, slug ->
            if (i == 0) roomThreshold else words.firstOrNull { it.slug == slug }?.threshold ?: roomThreshold
        }

    /** The list as the room keeps it (RoomStore): a JSON array. */
    fun encode(words: List<Word>): String = JsonArray(
        words.map { w ->
            buildJsonObject {
                put("slug", w.slug)
                w.threshold?.let { put("threshold", Math.round(it.toDouble() * 10_000) / 10_000.0) }
            }
        },
    ).toString()

    /** [encode]'s text back, or null when it is not one. */
    fun decode(text: String?): List<Word>? {
        if (text.isNullOrBlank()) return null
        return parse(runCatching { Json.parseToJsonElement(text) }.getOrNull())?.takeIf { it.isNotEmpty() }
    }
}
