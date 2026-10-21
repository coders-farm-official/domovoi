package com.domovoi.app.assistant

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.AlarmClock
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import com.domovoi.app.audiobooks.BookItems
import com.domovoi.app.audiobooks.BookStore
import com.domovoi.app.audiobooks.PositionSync
import com.domovoi.app.data.LocalMedia
import com.domovoi.app.net.PageRequest
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.player.PlayerController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * The Clock app's intents (android.provider.AlarmClock). Each needs the
 * normal SET_ALARM permission, and from API 30 the app sees which apps take
 * them only through the manifest's `<queries>`: an intent no app takes is
 * never offered ([takes]). Timers and alarms go with EXTRA_SKIP_UI, so the
 * Clock app sets them without opening; showing timers or alarms opens it.
 */
object ClockIntents {
    fun action(i: ClockIntent): String = when (i) {
        ClockIntent.SET_TIMER -> AlarmClock.ACTION_SET_TIMER
        ClockIntent.SET_ALARM -> AlarmClock.ACTION_SET_ALARM
        ClockIntent.SHOW_TIMERS -> AlarmClock.ACTION_SHOW_TIMERS
        ClockIntent.SHOW_ALARMS -> AlarmClock.ACTION_SHOW_ALARMS
    }

    fun intent(r: ClockRequest): Intent = Intent(action(r.intent)).apply {
        when (r.intent) {
            ClockIntent.SET_TIMER -> {
                putExtra(AlarmClock.EXTRA_LENGTH, (r.seconds ?: 0).coerceIn(1, AssistantController.MAX_TIMER_S))
                r.label?.let { putExtra(AlarmClock.EXTRA_MESSAGE, it) }
                putExtra(AlarmClock.EXTRA_SKIP_UI, r.skipUi)
            }
            ClockIntent.SET_ALARM -> {
                putExtra(AlarmClock.EXTRA_HOUR, r.hour ?: 0)
                putExtra(AlarmClock.EXTRA_MINUTES, r.minute ?: 0)
                if (r.days.isNotEmpty()) putExtra(AlarmClock.EXTRA_DAYS, ArrayList(r.days))
                r.label?.let { putExtra(AlarmClock.EXTRA_MESSAGE, it) }
                putExtra(AlarmClock.EXTRA_SKIP_UI, r.skipUi)
            }
            ClockIntent.SHOW_TIMERS, ClockIntent.SHOW_ALARMS -> Unit
        }
    }

    fun takes(context: Context, i: ClockIntent): Boolean = runCatching {
        context.packageManager.resolveActivity(Intent(action(i)), PackageManager.MATCH_DEFAULT_ONLY) != null
    }.getOrDefault(false)

    fun send(context: Context, r: ClockRequest): Boolean = try {
        context.startActivity(intent(r).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    } catch (_: ActivityNotFoundException) {
        false
    } catch (_: SecurityException) {
        false
    }
}

/**
 * The app's [PhoneActionHost] (AppContainer builds it): the Clock app, the
 * app's own player, and the songs, videos and books saved on the phone.
 * Nothing here holds the server client: a phone chat's actions never reach
 * the home server (the player is told nothing while it casts to a room).
 */
@OptIn(UnstableApi::class)
class AppActionHost(
    context: Context,
    private val playerController: PlayerController,
    private val bookStore: BookStore,
    private val positions: PositionSync,
) : PhoneActionHost {
    private val ctx = context.applicationContext

    override fun clockTakes(intent: ClockIntent): Boolean = ClockIntents.takes(ctx, intent)

    // Paced: the Clock app can drop an intent sent right after another.
    override suspend fun clock(request: ClockRequest): Boolean =
        pacer.send { withContext(Dispatchers.Main) { ClockIntents.send(ctx, request) } }

    override val player: PhonePlayer = Adapter(playerController)

    override suspend fun songs(query: String, limit: Int): List<PlayItem>? = withContext(Dispatchers.IO) {
        if (!LocalMedia.hasPermission(ctx, LocalMedia.audioPermission())) return@withContext null
        LocalMedia.pageTracks(ctx, query, PageRequest(null, 0, limit)).items.map {
            PlayItem.fromDeviceAudio(it.id, it.title, it.artist, it.album, it.durationSec, it.uri, it.albumArtUri)
        }
    }

    override suspend fun videos(query: String, limit: Int): List<VideoChoice>? = withContext(Dispatchers.IO) {
        if (!LocalMedia.hasPermission(ctx, LocalMedia.videoPermission())) return@withContext null
        LocalMedia.pageVideos(ctx, PageRequest(null, 0, limit), query).items.map {
            VideoChoice("local:${it.id}", it.name, it.uri, it.sizeBytes)
        }
    }

    override suspend fun genres(): List<String>? = withContext(Dispatchers.IO) {
        if (!LocalMedia.hasPermission(ctx, LocalMedia.audioPermission())) return@withContext null
        runCatching { LocalMedia.genres(ctx) }.getOrDefault(emptyList())
    }

    override suspend fun genreSongs(name: String, limit: Int): List<PlayItem>? = withContext(Dispatchers.IO) {
        if (!LocalMedia.hasPermission(ctx, LocalMedia.audioPermission())) return@withContext null
        runCatching { LocalMedia.genreTracks(ctx, name, limit) }.getOrDefault(emptyList()).map {
            PlayItem.fromDeviceAudio(it.id, it.title, it.artist, it.album, it.durationSec, it.uri, it.albumArtUri)
        }
    }

    override suspend fun books(): List<BookChoice> = withContext(Dispatchers.IO) {
        runCatching { bookStore.list() }.getOrDefault(emptyList()).filter { it.meta.complete }.mapNotNull { b ->
            val item = BookItems.saved(b, bookStore) ?: return@mapNotNull null
            // The phone's own place only: a phone chat never asks the server.
            val place = runCatching { positions.local(b.meta.bookId, b.meta.server) }.getOrNull()
            BookChoice(item, b.meta.title, b.meta.author, place?.positionSec ?: 0.0, place?.speed ?: 1f, place?.updatedAtMs ?: 0L)
        }
    }

    private companion object {
        /** One for the app: every Clock intent from it is spaced out. */
        val pacer = ClockPacer()
    }

    /** The player on its own thread (ExoPlayer's is the main one). */
    private class Adapter(private val p: PlayerController) : PhonePlayer {
        private suspend fun <T> main(block: () -> T): T = withContext(Dispatchers.Main) { block() }

        override suspend fun snapshot(): PlayerSnapshot = main {
            PlayerSnapshot(
                queue = p.queue.value, index = p.index.value, positionSec = p.exoPlayer.currentPosition / 1000.0,
                playing = p.isPlaying.value, sleepSec = p.sleepRemainingSec.value, speed = p.speed.value,
            )
        }

        override suspend fun castingTo(): String? = main { (p.target.value as? PlayTarget.Room)?.roomId }

        override suspend fun pause() = main { p.pause() }
        override suspend fun resume() = main { p.resume() }
        override suspend fun next() = main { p.next() }
        override suspend fun previous() = main { p.prev() }
        override suspend fun stop() = main { p.stop() }
        override suspend fun sleep(minutes: Int) = main { p.setSleepMinutes(minutes) }
        override suspend fun cancelSleep() = main { p.cancelSleep() }

        override suspend fun play(items: List<PlayItem>, start: Int, positionSec: Double, speed: Float?) = main {
            p.playItems(items, start, positionSec, speed)
            Unit
        }

        override suspend fun restore(s: PlayerSnapshot) = main {
            if (s.empty) {
                p.clearQueue()
            } else {
                val same = p.queue.value.map { it.uid } == s.queue.map { it.uid }
                if (same) {
                    val at = p.exoPlayer.currentPosition / 1000.0
                    if (p.index.value != s.index || abs(at - s.positionSec) > SEEK_SLACK_S) {
                        p.exoPlayer.seekTo(s.index, (s.positionSec * 1000).toLong())
                    }
                } else {
                    p.playItems(s.queue, s.index, s.positionSec, s.speed)
                }
                if (s.playing) p.resume() else p.pause()
            }
            val sleep = s.sleepSec
            if (sleep != null && sleep > 0) p.setSleepMinutes((sleep + 59) / 60) else p.cancelSleep()
        }

        private companion object {
            const val SEEK_SLACK_S = 2.0
        }
    }
}
