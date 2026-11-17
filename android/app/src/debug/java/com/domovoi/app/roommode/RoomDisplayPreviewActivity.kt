package com.domovoi.app.roommode

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.domovoi.app.ui.theme.DomovoiTheme
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.ByteArrayOutputStream
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import android.graphics.Color as AndroidColor

/**
 * DEBUG BUILDS ONLY: the room display drawn from made-up state, with no
 * room, no server and no microphone, for design review screenshots on an
 * emulator. Only a caller holding DUMP may start it (adb's shell does).
 *
 *     adb shell am start -n com.domovoi.app/.roommode.RoomDisplayPreviewActivity --es scene playing
 *
 * Scenes: `playing` (music, two timers, an alarm, listening), `idle`
 * (nothing playing, waiting for the wake word), `ringing` (a timer going
 * off), `night` (idle late at night, dimmed). `--es time 18:42` pins the
 * clock (default 18:42; night: 23:15).
 */
class RoomDisplayPreviewActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(SystemBarStyle.dark(AndroidColor.TRANSPARENT), SystemBarStyle.dark(AndroidColor.TRANSPARENT))
        val scene = intent.getStringExtra("scene") ?: "playing"
        val time = intent.getStringExtra("time")?.let { runCatching { LocalTime.parse(it) }.getOrNull() }
            ?: if (scene == "night") LocalTime.of(23, 15) else LocalTime.of(18, 42)
        val pinned = LocalDate.now().atTime(time).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
        val startedAt = SystemClock.elapsedRealtime()
        val source = FakeSource(feedFor(scene))
        val status = RoomEngine.Status(
            link = RoomEngine.Link.Ready,
            phase = when (scene) {
                "playing" -> RoomEngine.Phase.CAPTURING
                "ringing" -> RoomEngine.Phase.SPEAKING
                else -> RoomEngine.Phase.LISTENING
            },
            micOpen = true,
            micLive = scene == "playing",
            wakeReady = true,
            musicPlaying = scene == "playing",
        )
        setContent {
            DomovoiTheme(mode = ThemeMode.Dark) {
                RoomDisplay(
                    label = "kitchen",
                    status = status,
                    running = true,
                    approvalCode = null,
                    wakeWord = "hey_jarvis",
                    needsLock = false,
                    onMute = {}, onResume = {}, onSetLock = {}, onCheckNow = {}, onExit = {},
                    source = source,
                    clock = { pinned + (SystemClock.elapsedRealtime() - startedAt) },
                )
            }
        }
    }

    private fun feedFor(scene: String): RoomFeed? {
        val now = SystemClock.elapsedRealtime()
        return when (scene) {
            "playing" -> RoomFeed(
                nowPlaying = FeedNowPlaying(
                    state = PlayState.PLAY, title = "Harvest Moon", artist = "The Lanterns", album = "Kitchen Radio",
                    source = MediaSource.LIBRARY, art = "/v1/music/cover/12", durationSec = 245.0, elapsedSec = 82.0,
                    controls = setOf(Transport.PAUSE, Transport.SKIP, Transport.PREVIOUS),
                ),
                timers = listOf(
                    FeedTimer(1, TimerKind.TIMER, "pasta", 4 * 60_000L + 37_000L, 10 * 60_000L),
                    FeedTimer(2, TimerKind.TIMER, null, 23 * 60_000L + 5_000L, 30 * 60_000L),
                    FeedTimer(3, TimerKind.ALARM, "7 am alarm", 12 * 3_600_000L + 18 * 60_000L, null),
                ),
                ringing = null,
                receivedAt = now,
            )
            "ringing" -> RoomFeed(
                nowPlaying = null,
                timers = listOf(FeedTimer(2, TimerKind.TIMER, "bread", 17 * 60_000L, 40 * 60_000L)),
                ringing = FeedRinging(41, TimerKind.TIMER, "pasta"),
                receivedAt = now,
            )
            else -> null
        }
    }

    private class FakeSource(feed: RoomFeed?) : RoomDisplaySource {
        override val feed: StateFlow<RoomFeed?> = MutableStateFlow(feed)
        private var level = 40
        override fun control(action: RoomControl): Boolean = true
        override fun volume(): Int = level
        override fun setVolume(level: Int): Boolean {
            this.level = level
            return true
        }

        /** A made-up cover: the dashboard's no-art gradient. */
        override suspend fun art(path: String): ByteArray? {
            val bmp = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888)
            val paint = Paint().apply {
                shader = LinearGradient(0f, 0f, 256f, 256f, AndroidColor.rgb(0xE9, 0xC8, 0x8E), AndroidColor.rgb(0xC0, 0x6A, 0x2A), Shader.TileMode.CLAMP)
            }
            Canvas(bmp).drawRect(0f, 0f, 256f, 256f, paint)
            return ByteArrayOutputStream().also { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        }
    }
}
