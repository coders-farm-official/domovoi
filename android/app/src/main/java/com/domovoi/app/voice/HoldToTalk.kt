package com.domovoi.app.voice

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.IOException
import kotlin.math.sqrt

/*
 * The phone's microphone for one push-to-talk turn — the chat mic's capture
 * (ui/screens/chat/VoiceView.kt), and the piece the on-device assistant is
 * meant to reuse (design-notes/ANDROID-LOCAL-ASSISTANT-PLAN: mic capture,
 * hold-to-talk view and reply playback are shared). [AudioSource] and
 * [AndroidMic] have exactly the shape of the assistant spike's
 * (wf/android-assistant-m0, assistant/MicCapture.kt) so the two can become
 * one when both are on main; nothing here depends on that branch.
 *
 * Audio stays in memory: nothing is written to disk.
 */

/** Sample rate of everything the chat mic records: 16 kHz mono PCM16, what
 *  the core's speech-to-text and the drop-in socket take. */
const val VOICE_SAMPLE_RATE = 16_000

/** Where microphone samples come from: the real mic, or a test's fake. */
interface AudioSource {
    fun start()

    /** Blocks until samples arrive; returns how many, or a negative error. */
    fun read(buffer: ShortArray): Int

    fun stop()
}

/** The phone's microphone: 16 kHz mono PCM16, tuned for recognition. The
 *  caller holds RECORD_AUDIO. */
class AndroidMic : AudioSource {
    private var record: AudioRecord? = null

    @SuppressLint("MissingPermission")
    override fun start() {
        val min = AudioRecord.getMinBufferSize(VOICE_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val created = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            VOICE_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(min, VOICE_SAMPLE_RATE / 2 * 2), // half a second
        )
        if (created.state != AudioRecord.STATE_INITIALIZED) {
            created.release()
            throw IOException("the microphone could not be opened")
        }
        created.startRecording()
        record = created
    }

    override fun read(buffer: ShortArray): Int = record?.read(buffer, 0, buffer.size) ?: -1

    override fun stop() {
        record?.let {
            runCatching { it.stop() }
            it.release()
        }
        record = null
    }
}

/** Why a recording ended. */
enum class HeldEnd { RELEASE, LENGTH_CAP }

/** One push-to-talk recording: 16 kHz mono PCM16, little-endian. */
class Recording(val pcm: ByteArray, val endedBy: HeldEnd) {
    val seconds: Double get() = pcm.size / 2.0 / VOICE_SAMPLE_RATE
}

/**
 * Records while the button is held. [record] runs until [release] (keep
 * what was said), [cancel] (throw it away: the finger slid off the button)
 * or [maxSeconds] (the core takes 30 s at most), and always closes the mic.
 * [onLevel] hears a 0..1 loudness for each chunk, for the button's meter.
 */
class HoldToTalkRecorder(
    private val source: () -> AudioSource = { AndroidMic() },
    private val maxSeconds: Double = MAX_SECONDS,
) {
    @Volatile private var released = false
    @Volatile private var cancelled = false

    /** The finger came up on the button: send what was said. */
    fun release() {
        released = true
    }

    /** The finger slid off: nothing is sent. */
    fun cancel() {
        cancelled = true
    }

    /** Null when the recording was cancelled. Throws [IOException] when the
     *  microphone could not be opened or stopped delivering. One recorder
     *  is one press: a [release] or [cancel] that lands before the mic has
     *  opened still counts. */
    suspend fun record(onLevel: (Float) -> Unit = {}): Recording? = withContext(Dispatchers.IO) {
        if (cancelled) return@withContext null
        val mic = source()
        val chunk = ShortArray(CHUNK_SAMPLES)
        val out = ByteArrayOutputStream(VOICE_SAMPLE_RATE * 2 * 4)
        val cap = (maxSeconds * VOICE_SAMPLE_RATE).toLong()
        var heard = 0L
        mic.start()
        try {
            while (true) {
                ensureActive()
                if (cancelled) return@withContext null
                if (released) return@withContext Recording(out.toByteArray(), HeldEnd.RELEASE)
                val n = mic.read(chunk)
                if (n < 0) throw IOException("the microphone stopped ($n)")
                if (n == 0) continue
                val take = minOf(n.toLong(), cap - heard).toInt()
                appendLittleEndian(out, chunk, take)
                heard += take
                onLevel(level(chunk, take))
                if (heard >= cap) return@withContext Recording(out.toByteArray(), HeldEnd.LENGTH_CAP)
            }
            @Suppress("UNREACHABLE_CODE")
            null
        } finally {
            mic.stop()
        }
    }

    companion object {
        /** The longest turn the core takes (domovoi/phone_voice.py MAX_CLIP_SEC). */
        const val MAX_SECONDS = 30.0

        /** Shorter than this is a tap, not speech (the core's MIN_CLIP_SEC). */
        const val MIN_SECONDS = 0.3

        /** 32 ms at 16 kHz, the assistant spike's chunk. */
        const val CHUNK_SAMPLES = 512
    }
}

internal fun appendLittleEndian(out: ByteArrayOutputStream, samples: ShortArray, count: Int) {
    val bytes = ByteArray(count * 2)
    for (i in 0 until count) {
        val v = samples[i].toInt()
        bytes[2 * i] = (v and 0xFF).toByte()
        bytes[2 * i + 1] = ((v shr 8) and 0xFF).toByte()
    }
    out.write(bytes)
}

/** RMS loudness of the first [count] samples, scaled so speech near the
 *  phone reads around 0.5-1. */
internal fun level(samples: ShortArray, count: Int): Float {
    if (count <= 0) return 0f
    var sum = 0.0
    for (i in 0 until count) {
        val s = samples[i] / 32768.0
        sum += s * s
    }
    return (sqrt(sum / count) * 4.0).coerceIn(0.0, 1.0).toFloat()
}
