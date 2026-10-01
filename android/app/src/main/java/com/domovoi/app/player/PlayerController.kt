package com.domovoi.app.player

import android.content.Context
import android.content.Intent
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.put
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import androidx.core.net.toUri
import java.util.concurrent.atomic.AtomicInteger

/** Where transport controls are pointed: this device, or a satellite room. */
sealed class PlayTarget {
    data object Local : PlayTarget()
    data class Room(val roomId: String) : PlayTarget()
}

data class RemoteNowPlaying(
    val roomId: String,
    val state: String,
    val title: String?,
    val artist: String?,
    val elapsedSec: Double,
    val durationSec: Double?,
)

/**
 * The Android analog of player.jsx's PlaybackProvider: one queue for
 * library / radio / podcast / audiobook items, an ExoPlayer engine exposed
 * through PlaybackService (media notification + background audio), spoken
 * position save/restore keyed by device x listener person, and
 * Spotify-Connect-style casting to satellite rooms.
 */
@UnstableApi
class PlayerController(
    private val context: Context,
    private val api: ApiClient,
    private val prefs: Prefs,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    val exoPlayer: ExoPlayer by lazy {
        // DefaultDataSource routes http(s) through OkHttp and handles
        // content:// / file:// natively — required for on-device media
        // (PlayKind.Device) in offline/local mode.
        val dataSource = DefaultDataSource.Factory(context, OkHttpDataSource.Factory(api.http))
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSource))
            .setHandleAudioBecomingNoisy(true)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .build(),
                /* handleAudioFocus = */ true,
            )
            .build()
            .also { attach(it) }
    }

    // ---- observable state -------------------------------------------------
    private val _queue = MutableStateFlow<List<PlayItem>>(emptyList())
    val queue: StateFlow<List<PlayItem>> = _queue

    private val _index = MutableStateFlow(0)
    val index: StateFlow<Int> = _index

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying

    private val _positionSec = MutableStateFlow(0.0)
    val positionSec: StateFlow<Double> = _positionSec

    private val _durationSec = MutableStateFlow(0.0)
    val durationSec: StateFlow<Double> = _durationSec

    private val _speed = MutableStateFlow(1.0f)
    val speed: StateFlow<Float> = _speed

    private val _target = MutableStateFlow<PlayTarget>(PlayTarget.Local)
    val target: StateFlow<PlayTarget> = _target

    private val _remote = MutableStateFlow<RemoteNowPlaying?>(null)
    val remote: StateFlow<RemoteNowPlaying?> = _remote

    private val _sleepRemainingSec = MutableStateFlow<Int?>(null)
    val sleepRemainingSec: StateFlow<Int?> = _sleepRemainingSec

    val current: PlayItem? get() = _queue.value.getOrNull(_index.value)

    private var saveJob: Job? = null
    private var sleepJob: Job? = null
    private var remotePollJob: Job? = null

    private fun attach(player: Player) {
        player.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                _isPlaying.value = isPlaying
                if (!isPlaying) flushSpokenPosition()
            }
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                _index.value = player.currentMediaItemIndex
            }
            override fun onPlaybackParametersChanged(params: androidx.media3.common.PlaybackParameters) {
                _speed.value = params.speed
            }
        })
        // Position ticker + throttled spoken-position save (web: rAF ticker,
        // save every 10s while playing, flush on pause).
        scope.launch {
            var sinceSave = 0
            while (isActive) {
                delay(500)
                if (player.playbackState != Player.STATE_IDLE) {
                    _positionSec.value = player.currentPosition / 1000.0
                    val dur = player.duration
                    _durationSec.value = if (dur > 0) dur / 1000.0 else (current?.durationSec ?: 0.0)
                }
                if (_isPlaying.value) {
                    sinceSave++
                    if (sinceSave >= 20) { // 10s
                        sinceSave = 0
                        flushSpokenPosition()
                    }
                } else sinceSave = 0
            }
        }
    }

    /** Server-relative paths resolve against the API base; on-device items
     *  carry full content:// (or other scheme) URIs that pass through. */
    private fun resolveSrc(pathOrUri: String): String =
        if (pathOrUri.startsWith("/")) api.absolute(pathOrUri) else pathOrUri

    private fun mediaItemFor(item: PlayItem): MediaItem {
        val meta = MediaMetadata.Builder()
            .setTitle(item.title)
            .setArtist(item.artist)
            .setAlbumTitle(item.album)
            .apply { item.coverPath?.let { setArtworkUri(resolveSrc(it).toUri()) } }
            .build()
        return MediaItem.Builder()
            .setMediaId(item.uid)
            .setUri(resolveSrc(item.src))
            .setMediaMetadata(meta)
            .build()
    }

    private fun ensureService() {
        runCatching {
            context.startService(Intent(context, PlaybackService::class.java))
        }
    }

    // ---- queue ------------------------------------------------------------
    /**
     * Replace the queue and play it ON THIS PHONE. A list longer than
     * [QueueWindow.MAX] is cut to a window around [startIndex]: every item
     * costs a MediaItem built here on the main thread, and the media session
     * publishes them all.
     *
     * Every caller is a "play here" (a library row's play-here, a station,
     * a podcast or audiobook, a song saved on the phone), so while casting
     * this ends the cast rather than sending the list to the room: the room
     * is paused and no longer watched ([leaveRoom]). Before 2026-10-01 the
     * target quietly became this phone while the room played on and its
     * poll kept running, so two players were heard. Starting the room
     * somewhere else is the cast menu's job, and a queue row tapped while
     * casting ([castFrom]) still re-casts. Returns the room that was left;
     * [onLeft] hears whether that room's pause happened (called on a
     * background thread), so a toast can say "paused office" only once it
     * has — the core answers a pause its player never got with a 502.
     *
     * A play here also wins over a change of target still on its way
     * ([playHereGen]): a cast picked before it does nothing more, and a room
     * that already took that cast's queue is paused again. Before
     * 2026-10-01 such a cast landed after the play here and paused it.
     */
    fun playItems(
        items: List<PlayItem>,
        startIndex: Int = 0,
        resumeSec: Double = 0.0,
        speed: Float? = null,
        onLeft: ((room: String, paused: Boolean) -> Unit)? = null,
    ): String? {
        if (items.isEmpty()) return null
        val window = QueueWindow.around(items, startIndex)
        playHereGen.incrementAndGet()
        val left = leaveRoom(onLeft)
        ensureService()
        _queue.value = window.items
        _index.value = window.index
        exoPlayer.setMediaItems(window.items.map(::mediaItemFor), window.index, (resumeSec * 1000).toLong())
        speed?.let { exoPlayer.setPlaybackSpeed(it) }
        exoPlayer.prepare()
        exoPlayer.play()
        return left
    }

    fun enqueue(items: List<PlayItem>) {
        if (items.isEmpty()) return
        if (_queue.value.isEmpty()) { playItems(items); return }
        _queue.value = _queue.value + items
        items.forEach { exoPlayer.addMediaItem(mediaItemFor(it)) }
    }

    fun playNext(items: List<PlayItem>) {
        if (items.isEmpty()) return
        if (_queue.value.isEmpty()) { playItems(items); return }
        val at = _index.value + 1
        _queue.value = _queue.value.toMutableList().apply { addAll(at, items) }
        items.forEachIndexed { i, it -> exoPlayer.addMediaItem(at + i, mediaItemFor(it)) }
    }

    fun jumpTo(i: Int) {
        // While casting, a jump is a re-cast (castFrom), never local
        // playback under a room that is still playing.
        if (_target.value is PlayTarget.Room) return
        if (i in _queue.value.indices) {
            if (exoPlayer.playbackState == Player.STATE_IDLE) {
                ensureService()
                exoPlayer.prepare()
            }
            exoPlayer.seekTo(i, 0)
            exoPlayer.play()
        }
    }

    fun removeAt(i: Int) {
        if (i !in _queue.value.indices) return
        _queue.value = _queue.value.toMutableList().apply { removeAt(i) }
        exoPlayer.removeMediaItem(i)
    }

    fun moveItem(from: Int, to: Int) {
        val q = _queue.value.toMutableList()
        if (from !in q.indices || to !in q.indices) return
        val it = q.removeAt(from); q.add(to, it)
        _queue.value = q
        exoPlayer.moveMediaItem(from, to)
    }

    fun clearQueue() {
        flushSpokenPosition()
        _queue.value = emptyList()
        _index.value = 0
        exoPlayer.stop()
        exoPlayer.clearMediaItems()
    }

    // ---- transport ----------------------------------------------------------
    /**
     * Recover from STATE_IDLE with a queue still loaded — happens when the
     * media notification is swiped away while paused (its delete intent
     * sends COMMAND_STOP to the player). Re-prepare and re-post the
     * notification, then play.
     */
    private fun resumeLocal() {
        if (exoPlayer.mediaItemCount == 0) return
        if (exoPlayer.playbackState == Player.STATE_IDLE) {
            ensureService()
            exoPlayer.prepare()
        }
        exoPlayer.play()
    }

    fun toggle() {
        val t = _target.value
        if (t is PlayTarget.Room) {
            val playing = _remote.value?.state == "play"
            roomAction(if (playing) "pause" else "resume", t.roomId)
            return
        }
        if (exoPlayer.isPlaying) exoPlayer.pause() else resumeLocal()
    }

    /** Play: the room while casting, else this phone. */
    fun resume() {
        val t = _target.value
        if (t is PlayTarget.Room) return roomAction("resume", t.roomId)
        resumeLocal()
    }

    fun pause() {
        val t = _target.value
        if (t is PlayTarget.Room) return roomAction("pause", t.roomId)
        exoPlayer.pause()
    }

    fun stop() {
        val t = _target.value
        if (t is PlayTarget.Room) return roomAction("stop", t.roomId)
        clearQueue()
    }

    fun next() {
        val t = _target.value
        if (t is PlayTarget.Room) return roomAction("skip", t.roomId)
        exoPlayer.seekToNextMediaItem()
    }

    fun prev() {
        // While casting: back one song in the ROOM's queue (the core's MPD
        // previous, /api/music/previous; on the queue's first song it starts
        // again). Until 2026-10-01 there was no previous for a room and this
        // did nothing; the phone's own player is never the one moved.
        val t = _target.value
        if (t is PlayTarget.Room) return roomAction("previous", t.roomId)
        // Web behavior: restart if >3s in, else go to previous item.
        if (exoPlayer.currentPosition > 3000) exoPlayer.seekTo(0)
        else exoPlayer.seekToPreviousMediaItem()
    }

    fun seekTo(sec: Double) {
        if (_target.value is PlayTarget.Room) return
        if (current?.seekable != false) exoPlayer.seekTo((sec * 1000).toLong())
    }

    fun seekBy(sec: Double) = seekTo((_positionSec.value + sec).coerceAtLeast(0.0))

    fun setSpeed(v: Float) {
        exoPlayer.setPlaybackSpeed(v)
        flushSpokenPosition()
    }

    fun jumpToChapter(i: Int) {
        current?.chapters?.getOrNull(i)?.let { seekTo(it.startSec) }
    }

    // ---- sleep timer --------------------------------------------------------
    fun setSleepMinutes(minutes: Int) {
        sleepJob?.cancel()
        _sleepRemainingSec.value = minutes * 60
        sleepJob = scope.launch {
            while (isActive) {
                delay(1000)
                val left = (_sleepRemainingSec.value ?: break) - 1
                _sleepRemainingSec.value = left
                if (left <= 0) {
                    pause()
                    _sleepRemainingSec.value = null
                    break
                }
            }
        }
    }

    fun setSleepEndOfTrack() {
        sleepJob?.cancel()
        val left = (_durationSec.value - _positionSec.value).toInt().coerceAtLeast(1)
        setSleepMinutes(0)
        _sleepRemainingSec.value = left
        sleepJob = scope.launch {
            while (isActive) {
                delay(1000)
                val l = (_sleepRemainingSec.value ?: break) - 1
                _sleepRemainingSec.value = l
                if (l <= 0) { pause(); _sleepRemainingSec.value = null; break }
            }
        }
    }

    fun cancelSleep() {
        sleepJob?.cancel()
        _sleepRemainingSec.value = null
    }

    // ---- spoken position sync (podcasts/audiobooks) --------------------------
    private fun positionPath(item: PlayItem): String? = when (item.kind) {
        PlayKind.Podcast -> "/api/podcasts/positions/${item.id}"
        PlayKind.Audiobook -> "/api/audiobooks/${item.id}/position"
        else -> null
    }

    suspend fun fetchPosition(item: PlayItem): Pair<Double, Float> {
        val base = positionPath(item) ?: return 0.0 to 1.0f
        val person = prefs.listenerPersonId.value
        val q = "?device_id=${prefs.deviceId}" + (person?.let { "&person_id=$it" } ?: "")
        return runCatching {
            val obj = api.get(base + q).jsonObject
            val pos = obj["position_sec"]?.jsonPrimitive?.doubleOrNull ?: 0.0
            val sp = obj["speed"]?.jsonPrimitive?.doubleOrNull?.toFloat() ?: 1.0f
            pos to sp
        }.getOrDefault(0.0 to 1.0f)
    }

    private fun flushSpokenPosition() {
        val item = current ?: return
        val path = positionPath(item) ?: return
        val pos = _positionSec.value
        val sp = _speed.value
        if (saveJob?.isActive == true) return
        saveJob = scope.launch(Dispatchers.IO) {
            runCatching {
                api.post(path, buildJsonObject {
                    put("device_id", prefs.deviceId)
                    put("position_sec", pos.toInt())
                    prefs.listenerPersonId.value?.let { put("person_id", it) }
                    put("speed", sp.toDouble())
                })
            }
        }
    }

    // ---- casting to rooms -----------------------------------------------------
    //
    // The hand-off rules (2026-10-01), the same on the web dashboard
    // (web/static/player.jsx):
    //  - A room that is LEFT — for this phone, for another room, or by a
    //    "play here" while casting — is paused, never left playing unheard.
    //    Paused rather than stopped: a person's pause holds against every
    //    automatic restart (a voice turn's auto-resume, an announcement's
    //    restart: domovoi/music_pause.py), so the room cannot come back on by
    //    itself; it keeps its queue and place for someone in that room to
    //    resume; and a later cast back to it is a new start, which clears the
    //    hold. Stop would throw the place away and tear down the satellite's
    //    music stream for nothing.
    //  - The new player starts where the old one had got to: a room-to-room
    //    cast and a hand-back to this phone both follow the room's track and
    //    elapsed time (CastPlanner.planFor / handBack).
    //  - The new room is told first; the old player is silenced only once
    //    it has taken the queue, so a failed cast changes nothing.
    private fun roomAction(action: String, roomId: String) {
        scope.launch(Dispatchers.IO) {
            runCatching { api.post("/api/music/$action/$roomId") }
        }
    }

    /**
     * The same, waited for: whether the room DID it. A non-2xx is a no —
     * since 2026-10-01 the core answers a control its player never got with
     * 502 (409 nothing playing, 503 no speakers); before, it answered 200
     * with "I couldn't reach the music player." and every hand-off took the
     * room for paused — and so is a 2xx that says `ok: false`.
     */
    private suspend fun roomActionNow(action: String, roomId: String): Boolean = runCatching {
        val answer = api.post("/api/music/$action/$roomId")
        (answer as? JsonObject)?.get("ok")?.jsonPrimitive?.booleanOrNull != false
    }.getOrDefault(false)

    /**
     * Stop casting without a hand-back (a "play here" while casting): stop
     * watching the room, point the controls at this phone, and pause the
     * room in the background; [onLeft] hears whether the pause happened.
     * Returns the room that was left, if any.
     */
    private fun leaveRoom(onLeft: ((room: String, paused: Boolean) -> Unit)? = null): String? {
        val room = (_target.value as? PlayTarget.Room)?.roomId ?: return null
        remotePollJob?.cancel()
        remotePollJob = null
        _remote.value = null
        _target.value = PlayTarget.Local
        scope.launch(Dispatchers.IO) {
            val paused = roomActionNow("pause", room)
            onLeft?.invoke(room, paused)
        }
        return room
    }

    /**
     * How many "play here"s ([playItems]) there have been. A change of
     * target reads it when it is PICKED and again at each step that waits
     * (the lock, a read, the cast's POST): if a play here came in between,
     * the play here won and the change stops there ([CastOutcome.Superseded])
     * — a room that already took its queue is paused again.
     */
    private val playHereGen = AtomicInteger(0)

    /** A cast that would send a room nothing. Its message is for the person. */
    class NothingToCast(message: String) : Exception(message)

    /**
     * What a cast right now would send: the library tracks from the current
     * one on, starting at the current position. While already casting, from
     * where that room has got to rather than where the phone stopped
     * ([CastPlanner.planFor], JVM-tested).
     */
    fun castPlan(): CastPlan =
        // The player's own position, not the 500 ms tick's copy of it.
        CastPlanner.planFor(
            _queue.value, _index.value, exoPlayer.currentPosition / 1000.0, _target.value, _remote.value,
        )

    /**
     * Hand the queue to a satellite room (see [CastPlan]), or with null come
     * back to this device ([castHere]). Throws [NothingToCast] when the queue
     * has nothing a room can play, and then leaves the target alone: the
     * player never reads "casting" for a room that was sent nothing.
     *
     * From another room, the plan follows where THAT room has got to (read
     * afresh first), and the room being left is paused once the new one has
     * taken the queue. The room starts the way the player it takes over
     * from was: playing, or PAUSED there when the phone (or the room left)
     * was paused — Spotify Connect's hand-off. Before 2026-10-01 a cast from
     * a paused phone started the room playing.
     *
     * Not cancelled with its caller: the cast menu's scope ends as soon as
     * the person leaves the player tab — very often to "play here" from the
     * library — and a cast cut off mid-POST left a room playing that nothing
     * watched or paused. It runs to the end and then says what happened.
     */
    suspend fun castTo(roomId: String?): CastOutcome {
        val gen = playHereGen.get()
        return withContext(NonCancellable) {
            castLock.withLock {
                when {
                    playHereGen.get() != gen -> CastOutcome.Superseded(roomId)
                    roomId == null -> castHere(gen)
                    else -> castToRoom(roomId, gen)
                }
            }
        }
    }

    /**
     * One change of target at a time. The cast menu closes as soon as a
     * room is picked, and a cast takes seconds (the room readies its stream
     * first), so a second pick can come while the first is still on its way.
     * Before 2026-10-01 both then started from the target as it was: office
     * then den from this phone started BOTH rooms, office playing on with
     * nothing watching or pausing it; office then "this device" said
     * "playing on this device" and then cast to office anyway. Now the
     * second pick waits for the first and starts from where it left things:
     * room to room (office is paused) or back here (office is paused).
     */
    private val castLock = Mutex()

    private suspend fun castToRoom(roomId: String, gen: Int): CastOutcome {
        val from = (_target.value as? PlayTarget.Room)?.roomId
        if (from != null) {
            readRoom(from)?.let { _remote.value = it }
            if (playHereGen.get() != gen) return CastOutcome.Superseded(roomId)
        }
        val plan = castPlan()
        CastPlanner.refusal(plan)?.let { throw NothingToCast(it) }
        val playing = if (from != null) {
            // A room that can't be read at all is taken to be playing, as before.
            _remote.value?.takeIf { it.roomId == from }?.let { it.state == "play" } ?: true
        } else {
            exoPlayer.playWhenReady &&
                exoPlayer.playbackState != Player.STATE_ENDED && exoPlayer.playbackState != Player.STATE_IDLE
        }
        if (!sendCast(roomId, plan, startPaused = !playing, gen = gen)) {
            return CastOutcome.Superseded(roomId, sent = true, undone = roomActionNow("pause", roomId))
        }
        val left = from?.takeIf { it != roomId } ?: return CastOutcome.ToRoom(plan, roomId, paused = !playing)
        return CastOutcome.ToRoom(plan, roomId, left, leftPaused = roomActionNow("pause", left), paused = !playing)
    }

    /**
     * While casting: start the room on queue entry [i] (a tapped queue row).
     * Waits in line with the casts, and a play here meanwhile wins the same
     * way ([CastOutcome.Superseded]).
     */
    suspend fun castFrom(i: Int): CastOutcome {
        val gen = playHereGen.get()
        return withContext(NonCancellable) {
            castLock.withLock {
                val room = (_target.value as? PlayTarget.Room)?.roomId
                    ?: if (playHereGen.get() != gen) return@withLock CastOutcome.Superseded(null)
                    else throw NothingToCast("not casting to a room")
                val item = _queue.value.getOrNull(i) ?: throw NothingToCast("that queue entry is gone")
                CastPlanner.refusal(item)?.let { throw NothingToCast(it) }
                val plan = CastPlanner.plan(_queue.value, i, 0.0)
                if (!sendCast(room, plan, startPaused = false, gen = gen)) {
                    CastOutcome.Superseded(room, sent = true, undone = roomActionNow("pause", room))
                } else {
                    CastOutcome.ToRoom(plan, room)
                }
            }
        }
    }

    /**
     * Back to this phone from a room: pause the room, move the phone's queue
     * to where the room had got to (its track and elapsed time,
     * [CastPlanner.handBack]), and play on the phone only if the room was
     * playing AND took the pause — never two players at once, and never a
     * "playing" toast over a silent phone ([CastOutcome.Here.note]). A play
     * here that came meanwhile has already done all of that its own way,
     * and the phone is left to it.
     */
    private suspend fun castHere(gen: Int): CastOutcome {
        val room = (_target.value as? PlayTarget.Room)?.roomId
            ?: return CastOutcome.Here(left = null, playing = exoPlayer.isPlaying)
        // Where the room is now; the last poll's reading if that fails.
        val reading = readRoom(room) ?: _remote.value?.takeIf { it.roomId == room }
        val wasPlaying = reading?.state == "play"
        val paused = roomActionNow("pause", room)
        if (playHereGen.get() != gen) return CastOutcome.Superseded(null)
        remotePollJob?.cancel()
        remotePollJob = null
        val queue = _queue.value
        if (queue.isNotEmpty()) {
            val at = CastPlanner.handBack(queue, _index.value, reading)
            val posMs = at.positionSec?.let { (it * 1000).toLong() }
            if (at.index < exoPlayer.mediaItemCount) {
                when {
                    posMs != null -> exoPlayer.seekTo(at.index, posMs)
                    at.index != exoPlayer.currentMediaItemIndex -> exoPlayer.seekTo(at.index, 0L)
                }
            }
            _index.value = at.index
            at.positionSec?.let { _positionSec.value = it }
        }
        _target.value = PlayTarget.Local
        _remote.value = null
        val play = wasPlaying && paused && queue.isNotEmpty()
        if (play) resumeLocal()
        return CastOutcome.Here(
            left = room, playing = play, leftPaused = paused,
            leftWasPlaying = wasPlaying, queued = queue.isNotEmpty(),
        )
    }

    /**
     * Send [plan] to [roomId] and, once the room has taken it, make it the
     * target. False — and nothing here touched — when a play here ([gen]
     * moved on) came while the room was taking it; the caller pauses the
     * room again.
     */
    private suspend fun sendCast(roomId: String, plan: CastPlan, startPaused: Boolean, gen: Int): Boolean {
        api.post("/api/music/play-tracks", buildJsonObject {
            put("room_id", roomId)
            put("track_ids", kotlinx.serialization.json.buildJsonArray {
                plan.trackIds.forEach { add(kotlinx.serialization.json.JsonPrimitive(it)) }
            })
            // A server from before start_sec ignores it and starts the
            // track from the top; the track itself is still the right one.
            if (plan.startSec > 0) put("start_sec", plan.startSec)
            // The room waits paused there until play (a core from before
            // 2026-10-01 ignores it and plays, as every cast used to).
            if (startPaused) put("start_paused", true)
        })
        if (playHereGen.get() != gen) return false
        // Only once the room has taken it: a failed cast leaves the phone
        // playing and the target where it was.
        exoPlayer.pause()
        // The phone's queue position follows the room's start, so the
        // highlighted row is the one the room is on, and coming back to this
        // device picks up from there.
        if (plan.startIndex >= 0 && plan.startIndex != exoPlayer.currentMediaItemIndex &&
            plan.startIndex < exoPlayer.mediaItemCount
        ) {
            exoPlayer.seekTo(plan.startIndex, plan.startSec * 1000L)
        }
        if (plan.startIndex >= 0) _index.value = plan.startIndex
        // A reading of the room being left says nothing about this one.
        if (_remote.value?.roomId != roomId) _remote.value = null
        _target.value = PlayTarget.Room(roomId)
        startRemotePoll(roomId)
        return true
    }

    /** [roomId]'s now-playing row, read now; null when it can't be read. */
    private suspend fun readRoom(roomId: String): RemoteNowPlaying? = runCatching {
        api.get("/api/music/now-playing").jsonArray
            .map { it.jsonObject }
            .firstOrNull { it["room_id"]?.jsonPrimitive?.contentOrNull == roomId }
            ?.let { remoteFrom(roomId, it) }
    }.getOrNull()

    private fun remoteFrom(roomId: String, row: kotlinx.serialization.json.JsonObject): RemoteNowPlaying {
        val song = row["song"] as? kotlinx.serialization.json.JsonObject
        return RemoteNowPlaying(
            roomId = roomId,
            state = row["state"]?.jsonPrimitive?.contentOrNull ?: "stop",
            title = song?.get("title")?.jsonPrimitive?.contentOrNull,
            artist = song?.get("artist")?.jsonPrimitive?.contentOrNull,
            elapsedSec = row["elapsed_sec"]?.jsonPrimitive?.doubleOrNull ?: 0.0,
            durationSec = song?.get("duration_sec")?.jsonPrimitive?.doubleOrNull,
        )
    }

    private fun startRemotePoll(roomId: String) {
        remotePollJob?.cancel()
        remotePollJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                readRoom(roomId)?.let { reading ->
                    // A poll that lands after the cast moved on is dropped.
                    if ((_target.value as? PlayTarget.Room)?.roomId == roomId) _remote.value = reading
                }
                delay(2000)
            }
        }
    }

    // ---- the media session ------------------------------------------------------
    /**
     * The player the media notification, the lock screen and headset
     * buttons drive. While casting it acts on the ROOM and shows the room
     * ([CastAwarePlayer]); before 2026-10-01 the session held the ExoPlayer
     * itself, so a lock-screen "play" started the phone under a playing room.
     */
    val sessionPlayer: CastAwarePlayer by lazy {
        CastAwarePlayer(exoPlayer, this).also { p ->
            // The session reads the player's getters only when told
            // something changed; a room's play state and track change
            // without the ExoPlayer knowing, so say so whenever they do.
            scope.launch {
                combine(_target, _remote) { t, r ->
                    listOf(t, r?.roomId, r?.state, r?.title, r?.artist)
                }.distinctUntilChanged().collect { p.refresh() }
            }
        }
    }
}
