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

/**
 * Speaks text aloud, one sentence at a time. Android's own TextToSpeech is
 * the implementation today ([AndroidSpeechOut]); an on-device neural voice
 * can plug in behind the same interface later.
 */
interface SpeechOut : AutoCloseable {
    /** Synthesizes and plays [sentence]; returns when it has been played
     *  (or [stop] cut it off). */
    suspend fun speak(sentence: String): SpokenSentence

    /** Silences it at once and abandons the sentence being synthesized. */
    fun stop()

    override fun close()
}

/** Mono audio and its sample rate. */
class SynthesizedSpeech(val samples: FloatArray, val sampleRate: Int) {
    val seconds: Double get() = if (sampleRate > 0) samples.size.toDouble() / sampleRate else 0.0
}

/** A voice that can hand back its audio instead of playing it: the
 *  benchmark's self-tests feed it to the recognizer as if it were the mic. */
interface SpeechSynthesizer {
    suspend fun synthesize(text: String): SynthesizedSpeech
}

/** PCM16 (what AudioRecord gives) to floats in [-1, 1]. */
fun pcm16ToFloat(pcm: ShortArray, count: Int = pcm.size): FloatArray =
    FloatArray(count) { pcm[it] / 32768f }

/**
 * A RIFF WAV file (what TextToSpeech.synthesizeToFile writes) as mono floats.
 * Reads 8/16/24/32-bit integer PCM and 32-bit float; several channels are
 * averaged. Null for anything it cannot read.
 */
fun parseWav(bytes: ByteArray): SynthesizedSpeech? {
    val buf = java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.LITTLE_ENDIAN)
    fun tag(at: Int) = String(bytes, at, 4, Charsets.US_ASCII)
    if (bytes.size < 12 || tag(0) != "RIFF" || tag(8) != "WAVE") return null
    var format = -1
    var channels = 0
    var rate = 0
    var bits = 0
    var at = 12
    while (at + 8 <= bytes.size) {
        val id = tag(at)
        val size = buf.getInt(at + 4).toLong() and 0xFFFFFFFFL
        val body = at + 8
        when (id) {
            "fmt " -> {
                if (size < 16 || body + 16 > bytes.size) return null
                format = buf.getShort(body).toInt() and 0xFFFF
                channels = buf.getShort(body + 2).toInt() and 0xFFFF
                rate = buf.getInt(body + 4)
                bits = buf.getShort(body + 14).toInt() and 0xFFFF
                // WAVE_FORMAT_EXTENSIBLE: the real format is the sub-format's first two bytes.
                if (format == 0xFFFE && size >= 26 && body + 26 <= bytes.size) {
                    format = buf.getShort(body + 24).toInt() and 0xFFFF
                }
            }
            "data" -> {
                if (format < 0 || channels <= 0 || rate <= 0) return null
                // A streaming writer may leave the size unset (0 or too big).
                val end = if (size == 0L || body + size > bytes.size) bytes.size.toLong() else body + size
                val supported = (format == 3 && bits == 32) || (format == 1 && bits in setOf(8, 16, 24, 32))
                if (!supported) return null
                val width = bits / 8
                val frame = width * channels
                val frames = ((end - body) / frame).toInt()
                fun sample(pos: Int): Float = when {
                    format == 3 && bits == 32 -> buf.getFloat(pos)
                    format == 1 && bits == 8 -> ((bytes[pos].toInt() and 0xFF) - 128) / 128f
                    format == 1 && bits == 16 -> buf.getShort(pos) / 32768f
                    format == 1 && bits == 24 ->
                        (((bytes[pos].toInt() and 0xFF) or ((bytes[pos + 1].toInt() and 0xFF) shl 8) or
                            (bytes[pos + 2].toInt() shl 16)) / 8388608f)
                    else -> buf.getInt(pos) / 2147483648f   // 32-bit integer
                }
                val out = FloatArray(frames) { i ->
                    var sum = 0f
                    for (c in 0 until channels) sum += sample(body + i * frame + c * width)
                    sum / channels
                }
                return SynthesizedSpeech(out, rate)
            }
        }
        if (body + size > bytes.size) return null
        at = body + size.toInt() + (size.toInt() and 1)   // chunks are word-aligned
    }
    return null
}

/**
 * Linear resampling, enough for feeding synthesized speech (22.05 or 24 kHz) to
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
