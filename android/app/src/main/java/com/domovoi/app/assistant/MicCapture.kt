package com.domovoi.app.assistant

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.IOException

/** Where microphone samples come from: the real mic, or a test's fake. */
interface AudioSource {
    fun start()

    /** Blocks until samples arrive; returns how many, or a negative error. */
    fun read(buffer: ShortArray): Int

    fun stop()
}

/** The phone's microphone: 16 kHz mono PCM16, tuned for recognition. The
 *  caller holds RECORD_AUDIO; nothing is written to disk. */
class AndroidMic : AudioSource {
    private var record: AudioRecord? = null

    @SuppressLint("MissingPermission")
    override fun start() {
        val min = AudioRecord.getMinBufferSize(SPEECH_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val created = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SPEECH_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(min, SPEECH_SAMPLE_RATE / 2 * 2),   // half a second
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

enum class EndedBy { SILENCE, TAP, LENGTH_CAP }

/** One spoken question. Times are System.nanoTime(). */
class Utterance(
    val samples: FloatArray,
    val speechStartedNs: Long?,
    /** When the detector decided the speaker had finished. */
    val endOfSpeechNs: Long,
    val endedBy: EndedBy,
) {
    val seconds: Double get() = samples.size.toDouble() / SPEECH_SAMPLE_RATE
}

/**
 * Opens the mic only for one utterance: feeds it to the voice detector until
 * the speaker stops (silence), [stop] is called, or [maxSeconds] passes, and
 * always closes the mic. Audio stays in memory; only the transcript is kept.
 */
class MicCapture(
    private val source: () -> AudioSource = { AndroidMic() },
    private val clock: () -> Long = System::nanoTime,
) {
    @Volatile private var stopRequested = false

    /** Ends the current [listen] with what has been heard so far. */
    fun stop() {
        stopRequested = true
    }

    /** Null when no speech was heard at all. */
    suspend fun listen(
        detector: SpeechDetector,
        maxSeconds: Double = 15.0,
        onSpeechStart: () -> Unit = {},
    ): Utterance? = withContext(Dispatchers.IO) {
        stopRequested = false
        detector.reset()
        val mic = source()
        val chunk = ShortArray(CHUNK_SAMPLES)
        var heard = 0L
        var speechStart: Long? = null
        mic.start()
        try {
            while (true) {
                ensureActive()
                if (stopRequested) {
                    return@withContext detector.flush()?.let { Utterance(it, speechStart, clock(), EndedBy.TAP) }
                }
                val n = mic.read(chunk)
                if (n < 0) throw IOException("the microphone stopped ($n)")
                if (n == 0) continue
                heard += n
                val finished = detector.accept(pcm16ToFloat(chunk, n))
                if (speechStart == null && (detector.hearingSpeech || finished != null)) {
                    speechStart = clock()
                    onSpeechStart()
                }
                if (finished != null) return@withContext Utterance(finished, speechStart, clock(), EndedBy.SILENCE)
                if (heard >= (maxSeconds * SPEECH_SAMPLE_RATE).toLong()) {
                    return@withContext detector.flush()?.let { Utterance(it, speechStart, clock(), EndedBy.LENGTH_CAP) }
                }
            }
            @Suppress("UNREACHABLE_CODE")
            null
        } finally {
            mic.stop()
        }
    }

    companion object {
        /** 32 ms at 16 kHz: one Silero window. */
        const val CHUNK_SAMPLES = 512
    }
}
