package com.domovoi.app.player

import android.content.Context
import android.content.Intent
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.put
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.contentOrNull
import androidx.core.net.toUri

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
        val dataSource = OkHttpDataSource.Factory(api.http)
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

    private fun mediaItemFor(item: PlayItem): MediaItem {
        val meta = MediaMetadata.Builder()
            .setTitle(item.title)
            .setArtist(item.artist)
            .setAlbumTitle(item.album)
            .apply { item.coverPath?.let { setArtworkUri(api.absolute(it).toUri()) } }
            .build()
        return MediaItem.Builder()
            .setMediaId(item.uid)
            .setUri(api.absolute(item.src))
            .setMediaMetadata(meta)
            .build()
    }

    private fun ensureService() {
        runCatching {
            context.startService(Intent(context, PlaybackService::class.java))
        }
    }

    // ---- queue ------------------------------------------------------------
    fun playItems(items: List<PlayItem>, startIndex: Int = 0, resumeSec: Double = 0.0, speed: Float? = null) {
        if (items.isEmpty()) return
        ensureService()
        _target.value = PlayTarget.Local
        _queue.value = items
        _index.value = startIndex
        exoPlayer.setMediaItems(items.map(::mediaItemFor), startIndex, (resumeSec * 1000).toLong())
        speed?.let { exoPlayer.setPlaybackSpeed(it) }
        exoPlayer.prepare()
        exoPlayer.play()
    }

    fun enqueue(items: List<PlayItem>) {
        if (items.isEmpty()) return
        if (_queue.value.isEmpty()) return playItems(items)
        _queue.value = _queue.value + items
        items.forEach { exoPlayer.addMediaItem(mediaItemFor(it)) }
    }

    fun playNext(items: List<PlayItem>) {
        if (items.isEmpty()) return
        if (_queue.value.isEmpty()) return playItems(items)
        val at = _index.value + 1
        _queue.value = _queue.value.toMutableList().apply { addAll(at, items) }
        items.forEachIndexed { i, it -> exoPlayer.addMediaItem(at + i, mediaItemFor(it)) }
    }

    fun jumpTo(i: Int) {
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
        // Web behavior: restart if >3s in, else go to previous item.
        if (exoPlayer.currentPosition > 3000) exoPlayer.seekTo(0)
        else exoPlayer.seekToPreviousMediaItem()
    }

    fun seekTo(sec: Double) {
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
    private fun roomAction(action: String, roomId: String) {
        scope.launch(Dispatchers.IO) {
            runCatching { api.post("/api/music/$action/$roomId") }
        }
    }

    /** Hand the current queue (library tracks only) to a satellite room. */
    suspend fun castTo(roomId: String?) {
        if (roomId == null) {
            _target.value = PlayTarget.Local
            remotePollJob?.cancel()
            _remote.value = null
            return
        }
        val trackIds = _queue.value.filter { it.kind == PlayKind.Library }.map { it.id }
        if (trackIds.isNotEmpty()) {
            api.post("/api/music/play-tracks", buildJsonObject {
                put("room_id", roomId)
                put("track_ids", kotlinx.serialization.json.buildJsonArray {
                    trackIds.forEach { add(kotlinx.serialization.json.JsonPrimitive(it)) }
                })
            })
            exoPlayer.pause()
        }
        _target.value = PlayTarget.Room(roomId)
        startRemotePoll(roomId)
    }

    private fun startRemotePoll(roomId: String) {
        remotePollJob?.cancel()
        remotePollJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                runCatching {
                    val rows = api.get("/api/music/now-playing").jsonArray
                    val row = rows.map { it.jsonObject }
                        .firstOrNull { it["room_id"]?.jsonPrimitive?.contentOrNull == roomId }
                    if (row != null) {
                        val song = row["song"] as? kotlinx.serialization.json.JsonObject
                        _remote.value = RemoteNowPlaying(
                            roomId = roomId,
                            state = row["state"]?.jsonPrimitive?.contentOrNull ?: "stop",
                            title = song?.get("title")?.jsonPrimitive?.contentOrNull,
                            artist = song?.get("artist")?.jsonPrimitive?.contentOrNull,
                            elapsedSec = row["elapsed_sec"]?.jsonPrimitive?.doubleOrNull ?: 0.0,
                            durationSec = song?.get("duration_sec")?.jsonPrimitive?.doubleOrNull,
                        )
                    }
                }
                delay(2000)
            }
        }
    }
}
