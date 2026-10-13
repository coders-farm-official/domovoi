package com.domovoi.app.voice

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import java.nio.ByteBuffer
import java.nio.ByteOrder

/*
 * Domovoi's spoken reply, played on the phone — the chat mic's reply
 * playback, shared later with the on-device assistant (its SpeechOut
 * renders a sentence to PCM and can hand it to [ReplyPlayer.playPcm]).
 */

/** Mono or stereo PCM16 and its rate, as a WAV file carries it. */
class Pcm16(val sampleRate: Int, val channels: Int, val data: ByteArray) {
    val seconds: Double get() = if (sampleRate <= 0 || channels <= 0) 0.0 else data.size / 2.0 / channels / sampleRate
}

/**
 * The PCM16 inside a RIFF/WAVE file — what the core's text-to-speech sends
 * (Piper, or the stub). Walks the chunks rather than
 * assuming a 44-byte header, since some writers add a LIST chunk; a data
 * chunk whose size says more than the file holds (a streamed writer's
 * placeholder) is read to the end. Null for anything else.
 */
fun parseWav(bytes: ByteArray): Pcm16? {
    if (bytes.size < 12) return null
    val b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
    if (String(bytes, 0, 4, Charsets.US_ASCII) != "RIFF" || String(bytes, 8, 4, Charsets.US_ASCII) != "WAVE") return null
    var pos = 12
    var rate = 0
    var channels = 0
    var bits = 0
    var format = 0
    while (pos + 8 <= bytes.size) {
        val id = String(bytes, pos, 4, Charsets.US_ASCII)
        val size = b.getInt(pos + 4)
        val body = pos + 8
        if (size < 0) return null
        when (id) {
            "fmt " -> {
                if (body + 16 > bytes.size) return null
                format = b.getShort(body).toInt() and 0xFFFF
                channels = b.getShort(body + 2).toInt() and 0xFFFF
                rate = b.getInt(body + 4)
                bits = b.getShort(body + 14).toInt() and 0xFFFF
            }
            "data" -> {
                if (format != 1 || bits != 16 || channels !in 1..2 || rate <= 0) return null
                val end = minOf(bytes.size.toLong(), body.toLong() + size).toInt()
                val len = (end - body) / (2 * channels) * (2 * channels)
                return Pcm16(rate, channels, bytes.copyOfRange(body, body + len))
            }
        }
        // Chunks are word-aligned.
        pos = body + size + (size and 1)
    }
    return null
}

/**
 * Plays one reply at a time through the speech stream, holding transient
 * audio focus while it plays so the phone's own music ducks under it and
 * comes back after. [stop] silences it at once (a press on the talk button
 * while Domovoi is still speaking).
 */
class ReplyPlayer(private val context: Context) {
    @Volatile private var track: AudioTrack? = null
    @Volatile private var stopped = false

    /** Plays a WAV reply; returns once it has played or [stop] cut it off.
     *  False when it was cut off (or could not be read). */
    suspend fun play(wav: ByteArray): Boolean {
        val pcm = parseWav(wav) ?: return false
        return playPcm(pcm)
    }

    suspend fun playPcm(pcm: Pcm16): Boolean = withContext(Dispatchers.IO) {
        if (pcm.data.isEmpty()) return@withContext true
        stopped = false
        val focus = VoiceFocus(context)
        focus.hold(VoiceFocus.Kind.REPLY)
        val mask = if (pcm.channels == 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
        val min = AudioTrack.getMinBufferSize(pcm.sampleRate, mask, AudioFormat.ENCODING_PCM_16BIT)
        val t = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(pcm.sampleRate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(mask)
                    .build(),
            )
            .setBufferSizeInBytes(maxOf(min, 1) * 4)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track = t
        try {
            t.play()
            // Streamed in slices so a stop lands within one of them.
            var at = 0
            while (at < pcm.data.size && isActive && !stopped) {
                val n = t.write(pcm.data, at, minOf(WRITE_SLICE, pcm.data.size - at))
                if (n <= 0) break
                at += n
            }
            val frames = at / (2 * pcm.channels)
            // Then wait for what was written to be heard.
            while (isActive && !stopped && t.playbackHeadPosition < frames) {
                if (t.playState != AudioTrack.PLAYSTATE_PLAYING) break
                delay(40)
            }
            !stopped
        } finally {
            runCatching { t.stop() }
            t.release()
            track = null
            focus.release()
        }
    }

    /** Silences the reply now. */
    fun stop() {
        stopped = true
        runCatching { track?.pause() }
        runCatching { track?.flush() }
    }

    val playing: Boolean get() = track != null && !stopped

    private companion object {
        const val WRITE_SLICE = 8 * 1024
    }
}

/**
 * Audio focus for a turn. While the button is held the phone's player is
 * paused (exclusive transient focus — the mic should not hear the music);
 * while the reply plays it ducks. ExoPlayer handles focus itself
 * (PlayerController), so releasing brings the music back.
 */
class VoiceFocus(context: Context) {
    enum class Kind { CAPTURE, REPLY }

    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
    private var request: AudioFocusRequest? = null

    fun hold(kind: Kind) {
        release()
        val am = audio ?: return
        val gain = when (kind) {
            Kind.CAPTURE -> AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE
            Kind.REPLY -> AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK
        }
        val r = AudioFocusRequest.Builder(gain)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .build()
        runCatching { am.requestAudioFocus(r) }
        request = r
    }

    fun release() {
        val r = request ?: return
        request = null
        runCatching { audio?.abandonAudioFocusRequest(r) }
    }
}
