package com.domovoi.app.assistant

/** Sample rate of everything that goes into speech recognition: 16 kHz mono. */
const val SPEECH_SAMPLE_RATE = 16_000

/** Turns one utterance into text. */
interface SpeechToText : AutoCloseable {
    /** [samples]: 16 kHz mono, in [-1, 1]. Returns the trimmed transcript. */
    fun transcribe(samples: FloatArray): String

    override fun close()
}

/**
 * The voice detector that decides when the speaker has finished: fed the
 * microphone as it arrives, it hands back the utterance once speech has
 * started and then stayed silent long enough.
 */
interface SpeechDetector : AutoCloseable {
    /** True while it hears speech. */
    val hearingSpeech: Boolean

    /** Feeds 16 kHz samples; returns the finished utterance, or null while
     *  the speaker has not started or not yet stopped. */
    fun accept(samples: FloatArray): FloatArray?

    /** Ends now (a tap, or the length cap): the speech heard so far, or null. */
    fun flush(): FloatArray?

    fun reset()

    override fun close()
}

/** How long one spoken sentence took. All times are System.nanoTime(). */
data class SpokenSentence(
    val text: String,
    val startedNs: Long,
    /** When the first synthesized audio was handed to the speaker. */
    val firstAudioNs: Long?,
    val endedNs: Long,
    val audioSeconds: Double,
)

/** Speaks text aloud, one sentence at a time. */
interface SpeechOut : AutoCloseable {
    /** Synthesizes and plays [sentence]; returns when it has been played
     *  (or [stop] cut it off). */
    suspend fun speak(sentence: String): SpokenSentence

    /** Silences it at once and abandons the sentence being synthesized. */
    fun stop()

    override fun close()
}

/** PCM16 (what AudioRecord gives) to floats in [-1, 1]. */
fun pcm16ToFloat(pcm: ShortArray, count: Int = pcm.size): FloatArray =
    FloatArray(count) { pcm[it] / 32768f }

/**
 * Linear resampling, enough for feeding synthesized speech (22.05 kHz) to
 * the 16 kHz recognizer in the benchmark's self-test.
 */
fun resample(samples: FloatArray, fromRate: Int, toRate: Int): FloatArray {
    if (fromRate == toRate || samples.isEmpty()) return samples.copyOf()
    val outLength = ((samples.size.toLong() * toRate) / fromRate).toInt()
    val step = fromRate.toDouble() / toRate
    return FloatArray(outLength) { i ->
        val pos = i * step
        val left = pos.toInt().coerceAtMost(samples.size - 1)
        val right = (left + 1).coerceAtMost(samples.size - 1)
        val frac = (pos - left).toFloat()
        samples[left] * (1 - frac) + samples[right] * frac
    }
}

/** 16-bit mono RIFF WAV bytes of [samples] (for the audio-input experiment). */
fun wavBytes(samples: FloatArray, sampleRate: Int = SPEECH_SAMPLE_RATE): ByteArray {
    val dataBytes = samples.size * 2
    val out = java.nio.ByteBuffer.allocate(44 + dataBytes).order(java.nio.ByteOrder.LITTLE_ENDIAN)
    out.put("RIFF".toByteArray(Charsets.US_ASCII)).putInt(36 + dataBytes)
    out.put("WAVE".toByteArray(Charsets.US_ASCII))
    out.put("fmt ".toByteArray(Charsets.US_ASCII)).putInt(16)
        .putShort(1).putShort(1)                    // PCM, mono
        .putInt(sampleRate).putInt(sampleRate * 2)  // byte rate
        .putShort(2).putShort(16)                   // block align, bits
    out.put("data".toByteArray(Charsets.US_ASCII)).putInt(dataBytes)
    for (s in samples) out.putShort((s.coerceIn(-1f, 1f) * 32767f).toInt().toShort())
    return out.array()
}
