package com.domovoi.app.assistant

import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Some voices run away on a short sentence said on its own: Norman, the
 * shipped default, says "Cameron." as four to six seconds of babble in no
 * language (and "Okay.", "Done.", "Hi, I'm Norman." much the same), while
 * the very same ids come out right after a sentence of context; Kristin
 * and Linda say them right either way. A voice is checked once (each of
 * [PROBES] said alone, longer than [RUNAWAY_SECONDS] means it runs away);
 * then its short sentences are said after [TEXT] in the same call and cut
 * off again at the pause between the two ([cut]).
 *
 * The server's engine does the same with the same numbers
 * (domovoi/clients/tts.py: LEAD_IN_*, RUNAWAY_*, cut_after_lead_in).
 */
object LeadIn {
    const val TEXT = "The weather today is sunny with a high of seventy two degrees."

    /** A sentence with fewer phonemes than this ([phonemeCount]) is short. */
    const val MAX_PHONEMES = 20
    val PROBES = listOf("Cameron.", "Norman.")
    const val RUNAWAY_SECONDS = 1.5

    /** A pause: 10 ms frames this many dB under the loudest frame, at least
     *  [MIN_PAUSE_S] long, the first one starting after [SEARCH_FROM] of the
     *  lead-in's own length; the sentence starts [KEEP_S] before it ends. */
    const val PAUSE_DB = -40.0
    const val MIN_PAUSE_S = 0.16
    const val SEARCH_FROM = 0.6
    const val KEEP_S = 0.1
    const val FADE_S = 0.005

    /** Calls before the sentence is said alone when no pause is found. */
    const val TRIES = 2

    /** How many phonemes a phoneme string holds: its letters, without the
     *  stress and length marks (ˈ ˌ ː), spaces or punctuation. */
    fun phonemeCount(phonemes: String): Int = phonemes.count { it.isLetter() && it !in "ˈˌː" }

    /** The part of [audio] (the lead-in, then a short sentence, from one
     *  call) after the pause that ends the lead-in, faded in; null when no
     *  such pause is found. [leadInSeconds]: the lead-in alone. */
    fun cut(audio: FloatArray, sampleRate: Int, leadInSeconds: Double): FloatArray? {
        val hop = max(1, (sampleRate * 0.01).toInt())
        val n = audio.size / hop
        if (n < 3) return null
        val rms = DoubleArray(n) { f ->
            var sum = 0.0
            for (i in f * hop until (f + 1) * hop) sum += audio[i].toDouble() * audio[i]
            sqrt(sum / hop) + 1e-12
        }
        val loudest = rms.max()
        val quiet = BooleanArray(n) { 20 * log10(rms[it] / loudest) < PAUSE_DB }
        val minFrames = (MIN_PAUSE_S / 0.01).roundToInt()
        var k = max(1, (SEARCH_FROM * leadInSeconds / 0.01).toInt())
        while (k < n) {
            if (!quiet[k]) {
                k++
                continue
            }
            var j = k
            while (j < n && quiet[j]) j++
            if (j - k >= minFrames && j < n) {
                val from = max(k, j - (KEEP_S / 0.01).roundToInt()) * hop
                val out = audio.copyOfRange(from, audio.size)
                val fade = min(out.size, max(1, (sampleRate * FADE_S).toInt()))
                for (i in 0 until fade) out[i] *= if (fade > 1) i.toFloat() / (fade - 1) else 0f
                return out
            }
            k = j
        }
        return null
    }
}
