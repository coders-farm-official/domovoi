package com.domovoi.app.roommode

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.util.Log
import com.domovoi.app.assistant.AudioSource
import java.io.File
import java.util.concurrent.LinkedBlockingQueue

/**
 * DEBUG BUILDS ONLY: feeds the room recorded audio instead of the
 * microphone, for an emulator run (the maintainers' functional test
 * harness, kept outside this repo), so a test never listens to the host's
 * real microphone.
 *
 *     adb push hey_jarvis.pcm /sdcard/Android/data/com.domovoi.app/files/feed/
 *     adb shell am broadcast -a com.domovoi.app.debug.ROOM_FEED -n com.domovoi.app/.roommode.DebugFeedReceiver --ez on true
 *     adb shell am broadcast -a com.domovoi.app.debug.ROOM_FEED -n com.domovoi.app/.roommode.DebugFeedReceiver --es play hey_jarvis.pcm
 *     adb shell am broadcast ... --ez on false
 *
 * `on true` replaces the room's mic with the feed (takes effect when the
 * room next opens its mic: mute and unmute, or start room mode after it);
 * `play` queues a raw 16 kHz mono PCM16 file from the app's external
 * `files/feed/` (or its internal one, for an Android whose adb cannot write
 * Android/data), played at real-time pace, low noise in between.
 */
class DebugFeedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.hasExtra("on")) {
            RoomTestHooks.feed = if (intent.getBooleanExtra("on", false)) FeedSource.shared else null
            Log.i(TAG, "feed ${if (RoomTestHooks.feed != null) "on" else "off"}")
        }
        intent.getStringExtra("play")?.let { name ->
            // The app's external files/feed (adb push), else its internal
            // files/feed (`run-as … cp`, where adb cannot reach Android/data).
            val dirs = listOfNotNull(context.getExternalFilesDir("feed"), File(context.filesDir, "feed"))
            val file = dirs.map { File(it, name) }.firstOrNull { it.isFile && it.parentFile in dirs } ?: run {
                Log.w(TAG, "no feed file $name in $dirs")
                return
            }
            FeedSource.shared.queue(file.readBytes())
            Log.i(TAG, "queued $name (${file.length() / 32} ms)")
        }
    }

    private companion object {
        const val TAG = "RoomFeed"
    }
}

/** Real-time paced PCM: queued clips, else quiet noise (never exact zeros,
 *  which the room would take for a blocked mic). */
class FeedSource : AudioSource {
    private val clips = LinkedBlockingQueue<ShortArray>()
    private var current: ShortArray? = null
    private var at = 0
    private var next = 0L
    private var noise = 1

    fun queue(pcm: ByteArray) {
        clips.put(ShortArray(pcm.size / 2) { ((pcm[2 * it].toInt() and 0xff) or (pcm[2 * it + 1].toInt() shl 8)).toShort() })
    }

    override fun start() {
        next = SystemClock.elapsedRealtime()
    }

    override fun read(buffer: ShortArray): Int {
        val now = SystemClock.elapsedRealtime()
        if (next > now) Thread.sleep(next - now)
        next = maxOf(next, now) + buffer.size * 1000L / 16_000
        for (i in buffer.indices) {
            val c = current ?: clips.poll()?.also { current = it; at = 0 }
            if (c != null && at < c.size) {
                buffer[i] = c[at++]
                if (at >= c.size) current = null
            } else {
                noise = -noise
                buffer[i] = (noise * 3).toShort()
            }
        }
        return buffer.size
    }

    override fun stop() {}

    companion object {
        val shared = FeedSource()
    }
}
