package com.domovoi.app.roommode

import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import com.domovoi.app.assistant.AndroidMic
import com.domovoi.app.assistant.AudioSource

/**
 * The room's microphone, through the app's one mic class
 * (assistant/MicCapture.kt [AndroidMic]): 16 kHz mono PCM16 with a 2 s
 * buffer (a slow wake-word step on an old phone must not lose audio),
 * VOICE_RECOGNITION while it listens for the wake word, VOICE_COMMUNICATION
 * plus the platform's echo canceller during a drop-in, when it has to hear
 * the room while the other room's voice plays.
 *
 * Silence the OS hands over (Android 12+'s microphone privacy toggle, or a
 * phone call holding the mic) is all-zero samples: [blocked] turns true
 * after two seconds of them and false at the first real sample.
 *
 * Mute is enforced HERE, not by its callers: every [read] asks [muted]
 * first and, when it is, closes the recorder and answers [MUTED] — so in
 * every state (listening, the acknowledgement, a capture, the wait for a
 * reply, a drop-in) the recorder is shut within one frame of Mute, and
 * [open] refuses while muted. [onOpenChanged] hears every open and close,
 * which is what the screen's mic chip shows (it never says "mic off" while
 * a recorder is open).
 */
class RoomMic(
    private val muted: () -> Boolean = { false },
    private val onOpenChanged: (Boolean) -> Unit = {},
) {
    private var source: AudioSource? = null
    private var aec: AcousticEchoCanceler? = null
    private var zeroFrames = 0

    /** The mode it is open in, or null when closed. */
    var openAs: Boolean? = null
        private set

    /** Two seconds of exact zeros: the OS is handing over silence. */
    @Volatile var blocked = false
        private set

    fun open(call: Boolean) {
        close()
        if (muted()) return
        val feed = RoomTestHooks.feed
        val mic = feed ?: AndroidMic(
            audioSource = if (call) MediaRecorder.AudioSource.VOICE_COMMUNICATION else MediaRecorder.AudioSource.VOICE_RECOGNITION,
            bufferSeconds = 2.0,
        )
        mic.start()
        if (call && mic is AndroidMic && AcousticEchoCanceler.isAvailable()) {
            aec = runCatching { AcousticEchoCanceler.create(mic.sessionId)?.also { it.enabled = true } }.getOrNull()
        }
        source = mic
        openAs = call
        zeroFrames = 0
        onOpenChanged(true)
    }

    /** One frame into [buf]; the sample count, or negative on failure. */
    fun read(buf: ShortArray): Int {
        if (muted()) {
            close()
            return MUTED
        }
        val s = source ?: return -1
        var got = 0
        while (got < buf.size) {
            val chunk = if (got == 0) buf else ShortArray(buf.size - got)
            val n = s.read(chunk)
            if (n < 0) return n
            if (n == 0) continue
            if (got > 0) System.arraycopy(chunk, 0, buf, got, n)
            got += n
        }
        noteZeros(buf, got)
        return got
    }

    private fun noteZeros(buf: ShortArray, n: Int) {
        var allZero = true
        for (i in 0 until n) if (buf[i].toInt() != 0) { allZero = false; break }
        if (allZero) {
            zeroFrames += 1
            if (zeroFrames >= BLOCKED_AFTER_FRAMES) blocked = true
        } else {
            zeroFrames = 0
            blocked = false
        }
    }

    fun close() {
        val wasOpen = source != null
        runCatching { aec?.release() }
        aec = null
        runCatching { source?.stop() }
        source = null
        openAs = null
        if (wasOpen) onOpenChanged(false)
    }

    /** A recorder is open now. */
    val isOpen: Boolean get() = source != null

    companion object {
        /** What [read] answers while muted (the recorder is then closed). */
        const val MUTED = -100

        /** Two seconds of 30 ms frames. */
        const val BLOCKED_AFTER_FRAMES = 2_000 / RoomProtocol.FRAME_MS

        val echoCanceller: Boolean get() = runCatching { AcousticEchoCanceler.isAvailable() }.getOrDefault(false)
    }
}

/**
 * Test hooks for the emulator run (functional-testing/room-mode/): a debug
 * build's receiver (src/debug, DebugFeedReceiver) may set [feed] so the room
 * hears recorded PCM instead of the host's microphone. Nothing in a release
 * build sets it; the receiver is not in a release APK.
 */
object RoomTestHooks {
    @Volatile var feed: AudioSource? = null
}
