package com.domovoi.app.listens

import android.content.Context
import android.os.SystemClock
import com.domovoi.app.data.Prefs
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File
import java.util.UUID

/**
 * Listening history from the phone's own player (V051, Up next P1; the
 * web's ListenLog, web/static/player.jsx). Each LIBRARY song the phone
 * plays is one row on its server, written to the [ListenOutbox] when it
 * ends and sent by the [ListenSender]:
 *
 *  * logged: library tracks (`PlayKind.Library`, a `track_id` of the
 *    server's library) played HERE. Never while casting (the room logs its
 *    own), and never a podcast, an audiobook, radio, a stream or a song
 *    stored on the phone that isn't a library track;
 *  * whose: the "listening as" pick for that server when there is one
 *    (`listener_person_id`, or `as_household` for "household" on a claimed
 *    phone), else the server decides (whose the phone is, unless it is a
 *    shared screen). A shared screen sends no pick;
 *  * sent: a few seconds after a song ends, when the network comes back,
 *    when the app goes to the background, and after each registration.
 *
 * The player ([com.domovoi.app.player.PlayerController]) calls the hooks
 * below on the main thread; the outbox is written and sent off it.
 */
class ListenLog(
    private val outbox: ListenOutbox,
    private val tracker: ListenTracker,
    private val sender: ListenSender?,
    /** The active server (normalised), "" for none. */
    private val server: () -> String,
    private val deviceId: () -> String,
    /** The raw "listening as" pick for a server ([ListenerPicks]). */
    private val pickFor: (server: String) -> String?,
    private val sharedAt: (server: String) -> Boolean,
    private val scope: CoroutineScope,
    /** Whether the phone is in room mode (no household calls at all). */
    private val roomMode: () -> Boolean = { false },
    private val newId: () -> String = { UUID.randomUUID().toString() },
    /** Write the outbox off the caller's thread (the player's main thread);
     *  the tests run it in place. */
    private val io: (suspend () -> Unit) -> Unit = { block -> scope.launch(Dispatchers.IO) { block() } },
) {
    /** False once a server said the household keeps no listening history
     *  (`recording: false`): nothing more is logged until the app starts
     *  again. */
    @Volatile
    var recording: Boolean = true
        private set

    private var flushJob: Job? = null

    private fun eligible(item: PlayItem?, local: Boolean): Boolean =
        recording && local && !roomMode() && item != null && item.kind == PlayKind.Library &&
            item.id > 0 && server().isNotBlank()

    /** The player moved to another queue entry ([item] is the new current
     *  one, or null). [natural]: the one before ran to its end (the
     *  player's AUTO / REPEAT transition). */
    fun transition(item: PlayItem?, natural: Boolean, local: Boolean, playing: Boolean) {
        close(natural)
        if (eligible(item, local)) begin(item!!, playing)
    }

    private fun begin(item: PlayItem, playing: Boolean) {
        tracker.begin(item.id, server(), item.durationSec, playing, item.sessionId, item.queueOrigin.wire)
    }

    /** The Up next session's id was just put on [item] (the start's answer
     *  came after the song began): the listen open for it carries it. */
    fun sessionArrived(item: PlayItem?) {
        val id = item?.sessionId ?: return
        if (tracker.openTrack == item.id) tracker.session(id)
    }

    /** The player started or stopped producing sound. A library song that
     *  starts playing here with nothing followed (back from a cast, after a
     *  stop) begins now. [durationSec]: the player's own length, when known. */
    fun playing(isPlaying: Boolean, item: PlayItem?, local: Boolean, durationSec: Double? = null) {
        if (isPlaying && tracker.openTrack == null && eligible(item, local)) {
            begin(item!!, playing = false)
        }
        if (durationSec != null && tracker.openTrack == item?.id) tracker.duration(durationSec)
        tracker.playing(isPlaying)
    }

    /** The song now playing was skipped (next, a queue row, removed). */
    fun skip() = tracker.skip()

    /** The last song ran to its end (the player's STATE_ENDED). */
    fun ended() = close(natural = true)

    /** Stopped: stop, a new play, casting, the queue cleared, the app
     *  closing. */
    fun stopped() = close(natural = false)

    private fun close(natural: Boolean) {
        val row = tracker.close(natural) ?: return
        val out = toOutbox(row)
        io {
            outbox.add(out)
        }
        flushSoon()
    }

    internal fun toOutbox(row: ListenRow): OutboxRow {
        val shared = sharedAt(row.server)
        val pick = if (shared) null else pickFor(row.server)
        return OutboxRow(
            id = newId(),
            server = row.server,
            trackId = row.trackId,
            deviceId = deviceId(),
            startedAt = OutboxRow.isoUtc(row.startedAtMs),
            playedSec = row.playedSec,
            durationSec = row.durationSec,
            outcome = row.outcome,
            listenerPersonId = pick?.takeIf { it != ListenerPicks.HOUSEHOLD }?.toLongOrNull(),
            asHousehold = pick == ListenerPicks.HOUSEHOLD,
            sessionId = row.sessionId,
            origin = row.origin,
        )
    }

    /** Send what waits in a few seconds (debounced: songs that end close
     *  together go in one batch). */
    fun flushSoon(delayMs: Long = FLUSH_DELAY_MS) {
        val s = sender ?: return
        flushJob?.cancel()
        flushJob = scope.launch(Dispatchers.IO) {
            delay(delayMs)
            runCatching { s.flush() }
        }
    }

    /** Send what waits now (the app going to the background). */
    fun flushNow() = flushSoon(0)

    /** A server said it keeps no listening history. */
    fun recordingOff() {
        recording = false
        tracker.close(natural = false)
    }

    companion object {
        const val FLUSH_DELAY_MS = 5_000L

        /** The app's log: the outbox in its private files, sent through the
         *  app's client to the active server while it is paired. */
        fun forApp(context: Context, api: ApiClient, prefs: Prefs): ListenLog {
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
            val outbox = ListenOutbox(File(context.filesDir, "listens-outbox.json"))
            val active = { ServerCredentials.normalize(prefs.serverUrl.value) }
            lateinit var log: ListenLog
            val sender = ListenSender(
                outbox, api, active,
                canSend = { s -> !HouseholdLock.roomModeActive && prefs.tokenForServer(s) != null },
                onRecordingOff = { scope.launch { log.recordingOff() } },
            )
            log = ListenLog(
                outbox = outbox,
                tracker = ListenTracker(monoMs = SystemClock::elapsedRealtime),
                sender = sender,
                server = active,
                deviceId = { prefs.deviceId },
                pickFor = prefs::listenerPickFor,
                sharedAt = prefs::sharedHere,
                scope = scope,
                roomMode = { HouseholdLock.roomModeActive },
            )
            return log
        }
    }
}
