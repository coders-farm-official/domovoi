package com.domovoi.app.assistant

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.domovoi.app.audio.MicArbiter
import com.domovoi.app.audio.MicOwnership
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
 *  caller holds RECORD_AUDIO; nothing is written to disk.
 *
 *  Room mode (roommode/RoomMic.kt) opens it too, with its own [audioSource]
 *  (VOICE_COMMUNICATION, the platform's echo canceller, during a drop-in)
 *  and a deeper buffer, and reads [sessionId] to attach the canceller. */
class AndroidMic(
    private val audioSource: Int = MediaRecorder.AudioSource.VOICE_RECOGNITION,
    private val bufferSeconds: Double = 0.5,
) : AudioSource {
    private var record: AudioRecord? = null

    /** The open recorder's audio session (0 when closed). */
    val sessionId: Int get() = record?.audioSessionId ?: 0

    @SuppressLint("MissingPermission")
    override fun start() {
        val min = AudioRecord.getMinBufferSize(SPEECH_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val created = AudioRecord(
            audioSource,
            SPEECH_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(min, (SPEECH_SAMPLE_RATE * bufferSeconds).toInt() * 2),
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
        // One owner at a time (audio/MicArbiter.kt): refused, with words,
        // while room mode or a call has the microphone.
        val lease = MicArbiter.acquireOrThrow(MicOwnership.Owner.ASSISTANT)
        val mic = source()
        val chunk = ShortArray(CHUNK_SAMPLES)
        var heard = 0L
        var speechStart: Long? = null
        try {
            mic.start()
        } catch (e: Throwable) {
            lease.close()
            throw e
        }
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
            lease.close()
        }
    }

    companion object {
        /** 32 ms at 16 kHz: one Silero window. */
        const val CHUNK_SAMPLES = 512
    }
}
