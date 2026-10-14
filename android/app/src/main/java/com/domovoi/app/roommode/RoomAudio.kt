package com.domovoi.app.roommode

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.media.MediaPlayer
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.media3.common.AudioAttributes as ExoAudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import okhttp3.OkHttpClient
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.PI
import kotlin.math.sin

private const val TAG = "RoomAudio"

/**
 * The room's speaker for what the core streams: a reply's or an
 * announcement's speech (raw PCM16 mono at the rate `response_start`
 * names) and, during a drop-in, the other room's voice at 16 kHz. One
 * playback thread writes an [AudioTrack]; the socket's thread only queues.
 *
 * A reply plays from its first 150 ms (a short prebuffer against Wi-Fi
 * jitter) and is over when the track has PLAYED its last frame, not when
 * the last byte arrived: [onIdle] fires then, which is when the room may
 * listen again.
 */
class RoomSpeaker(private val onIdle: () -> Unit) {
    private sealed class Item(val gen: Int) {
        class Begin(gen: Int, val rate: Int, val call: Boolean) : Item(gen)
        class Pcm(gen: Int, val bytes: ByteArray) : Item(gen)
        class End(gen: Int) : Item(gen)
        class Stop(gen: Int) : Item(gen)
    }

    private val queue = LinkedBlockingQueue<Item>()
    @Volatile private var gen = 0
    @Volatile private var playing = false
    @Volatile private var running = true
    private var track: AudioTrack? = null
    private var written = 0L
    private var started = false
    private var pending = java.io.ByteArrayOutputStream()
    private var prebufferBytes = 0
    private val thread = Thread(::loop, "room-speaker").apply { isDaemon = true; start() }

    /** True from [begin] until the last frame has played (or [stop]). */
    val active: Boolean get() = playing

    @Synchronized
    fun begin(rate: Int, call: Boolean = false) {
        gen += 1
        playing = true
        queue.clear()
        queue.put(Item.Begin(gen, rate, call))
    }

    fun write(bytes: ByteArray) {
        if (playing) queue.put(Item.Pcm(gen, bytes))
    }

    fun end() {
        if (playing) queue.put(Item.End(gen))
    }

    /** Silence it now and drop whatever was queued. */
    @Synchronized
    fun stop() {
        gen += 1
        queue.clear()
        queue.put(Item.Stop(gen))
    }

    fun release() {
        running = false
        stop()
        thread.interrupt()
    }

    private fun loop() {
        while (running) {
            val item = try {
                queue.take()
            } catch (_: InterruptedException) {
                break
            }
            try {
                if (item is Item.Stop) {
                    finish(drain = false)
                    continue
                }
                if (item.gen != gen) continue
                when (item) {
                    is Item.Begin -> open(item.rate, item.call)
                    is Item.Pcm -> feed(item.bytes)
                    is Item.End -> finish(drain = true)
                    is Item.Stop -> Unit
                }
            } catch (e: Exception) {
                Log.w(TAG, "speaker: ${e.javaClass.simpleName}: ${e.message}")
                finish(drain = false)
            }
        }
        finish(drain = false)
    }

    private fun open(rate: Int, call: Boolean) {
        closeTrack()
        val attrs = AudioAttributes.Builder()
            .setUsage(if (call) AudioAttributes.USAGE_VOICE_COMMUNICATION else AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val min = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        track = AudioTrack.Builder()
            .setAudioAttributes(attrs)
            .setAudioFormat(
                AudioFormat.Builder().setSampleRate(rate).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build(),
            )
            .setBufferSizeInBytes(maxOf(min * 2, rate / 2 * 2))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        written = 0
        started = false
        pending = java.io.ByteArrayOutputStream()
        // A call plays through at once; a reply holds 150 ms first.
        prebufferBytes = if (call) 0 else rate * 2 * 150 / 1000
    }

    private fun feed(bytes: ByteArray) {
        val t = track ?: return
        if (!started) {
            pending.write(bytes)
            if (pending.size() < prebufferBytes) return
            started = true
            t.play()
            val held = pending.toByteArray()
            pending = java.io.ByteArrayOutputStream()
            writeAll(t, held)
            return
        }
        writeAll(t, bytes)
    }

    private fun writeAll(t: AudioTrack, bytes: ByteArray) {
        var off = 0
        while (off < bytes.size) {
            val n = t.write(bytes, off, bytes.size - off)
            if (n <= 0) break
            off += n
        }
        written += bytes.size / 2
    }

    private fun finish(drain: Boolean) {
        val t = track
        if (t != null && drain) {
            if (!started && pending.size() > 0) {
                started = true
                t.play()
                writeAll(t, pending.toByteArray())
                pending = java.io.ByteArrayOutputStream()
            }
            // Until the last written frame has left the speaker.
            val deadline = SystemClock.elapsedRealtime() + 2_000 + written * 1000 / maxOf(1, t.sampleRate)
            while (started && t.playbackHeadPosition.toLong() and 0xffffffffL < written &&
                SystemClock.elapsedRealtime() < deadline && queue.isEmpty()
            ) {
                Thread.sleep(20)
            }
            if (queue.isNotEmpty()) return // a new response arrived: keep going
        }
        closeTrack()
        if (playing) {
            playing = false
            onIdle()
        }
    }

    private fun closeTrack() {
        track?.let {
            runCatching { it.pause() }
            runCatching { it.flush() }
            runCatching { it.release() }
        }
        track = null
        started = false
    }
}

/**
 * Short sounds the room plays itself, blocking until they end (or a cap):
 * a server-rendered clip (a wake greeting, the "can't reach the server"
 * line) through MediaPlayer, and the wake chime, made here.
 */
object RoomClips {
    /** Plays [file]; true when it played. At most [capMs]. */
    fun play(file: File, capMs: Long): Boolean {
        val done = CountDownLatch(1)
        val player = runCatching {
            MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build(),
                )
                setDataSource(file.absolutePath)
                setOnCompletionListener { done.countDown() }
                setOnErrorListener { _, _, _ -> done.countDown(); true }
                prepare()
                start()
            }
        }.getOrElse {
            Log.w(TAG, "clip ${file.name}: ${it.message}")
            return false
        }
        try {
            done.await(capMs, TimeUnit.MILLISECONDS)
        } finally {
            runCatching { player.stop() }
            player.release()
        }
        return true
    }

    /** Two short notes, 0.24 s, made here (the Pi's chime). Blocking. */
    fun chime() {
        val rate = 16_000
        val n = rate * 240 / 1000
        val pcm = ShortArray(n) { i ->
            val t = i.toDouble() / rate
            val f = if (i < n / 2) 880.0 else 1320.0
            val env = minOf(1.0, i / 160.0, (n - i) / 400.0)
            (sin(2 * PI * f * t) * env * 0.35 * Short.MAX_VALUE).toInt().toShort()
        }
        val track = runCatching {
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder().setSampleRate(rate).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build(),
                )
                .setBufferSizeInBytes(n * 2)
                .setTransferMode(AudioTrack.MODE_STATIC)
                .build()
        }.getOrNull() ?: return
        try {
            track.write(pcm, 0, n)
            track.play()
            Thread.sleep(260)
        } finally {
            runCatching { track.stop() }
            track.release()
        }
    }
}

/**
 * The room's music: the room's MPD stream, in a player of its own (not the
 * app's), bound to the room's Wi-Fi client. Reports `music_ready` once it
 * plays and `music_failed` after ~8 s of refusals, as the Pi does; runs on
 * the main looper (ExoPlayer's rule), callable from any thread.
 */
class RoomMusic(
    private val context: Context,
    private val onReady: () -> Unit,
    private val onFailed: (url: String, reason: String, attempts: Int) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private var player: ExoPlayer? = null
    private var url: String? = null
    private var attempts = 0
    private var firstAttemptAt = 0L
    private var readySent = false
    @Volatile var playing = false
        private set

    fun start(streamUrl: String, http: OkHttpClient) = main.post {
        stopNow()
        url = streamUrl
        attempts = 0
        firstAttemptAt = SystemClock.elapsedRealtime()
        readySent = false
        open(streamUrl, http)
    }

    fun stop() = main.post { stopNow() }

    private fun open(streamUrl: String, http: OkHttpClient) {
        attempts += 1
        val exo = ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(http)))
            .setAudioAttributes(
                ExoAudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                false,
            )
            .build()
        exo.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY && !readySent) {
                    readySent = true
                    onReady()
                }
            }

            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playing = isPlaying
            }

            override fun onPlayerError(error: PlaybackException) {
                val u = url ?: return
                val elapsed = SystemClock.elapsedRealtime() - firstAttemptAt
                exo.release()
                if (player === exo) player = null
                playing = false
                if (elapsed < RETRY_WINDOW_MS) {
                    main.postDelayed({ if (url == u) open(u, http) }, 1_000L * attempts)
                } else {
                    url = null
                    onFailed(u, error.errorCodeName, attempts)
                }
            }
        })
        exo.setMediaItem(MediaItem.fromUri(streamUrl))
        exo.prepare()
        exo.playWhenReady = true
        player = exo
    }

    private fun stopNow() {
        url = null
        main.removeCallbacksAndMessages(null)
        player?.release()
        player = null
        playing = false
    }

    private companion object {
        const val RETRY_WINDOW_MS = 8_000L
    }
}

/** The phone's media volume as the room's 0-100 (`set_volume`, `volume_status`). */
class RoomVolume(context: Context) {
    private val am = context.getSystemService(AudioManager::class.java)

    fun level(): Int {
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
        return am.getStreamVolume(AudioManager.STREAM_MUSIC) * 100 / max
    }

    fun set(level: Int) {
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val index = ((level.coerceIn(0, 100) * max) + 50) / 100
        runCatching { am.setStreamVolume(AudioManager.STREAM_MUSIC, index, 0) }
    }
}
