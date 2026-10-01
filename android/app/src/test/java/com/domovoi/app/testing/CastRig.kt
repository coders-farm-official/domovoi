package com.domovoi.app.testing

import android.content.ContextWrapper
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.player.PlayerController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.cancel
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import java.lang.reflect.Proxy
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/**
 * A real [PlayerController] casting to rooms on the JVM: its ExoPlayer is a
 * [StatefulExoPlayer] and its server a [MockWebServer] playing the web's
 * music routes. Both write to one [log], in the order things happened, so a
 * test can say "the room was told before the phone paused".
 *
 * The controller's own code runs unchanged: what it posts, what it does to
 * the player, and when it switches its target.
 */
internal class CastRig : AutoCloseable {
    /** Everything that happened: "POST /api/music/play-tracks {...}",
     *  "GET /api/music/now-playing", "exo.pause", "exo.seekTo(2, 7000)". */
    val log = CopyOnWriteArrayList<String>()

    /** What GET /api/music/now-playing answers, by room. */
    val rooms = java.util.concurrent.ConcurrentHashMap<String, JsonObject>()

    /** Paths that answer with an error status instead of 200. */
    val failing = java.util.concurrent.ConcurrentHashMap<String, Int>()

    /** Paths that answer only after this many ms: a room readying its
     *  stream, so a second pick can land while a cast is on its way. */
    val delays = java.util.concurrent.ConcurrentHashMap<String, Long>()

    /** Paths that answer 200 with this body instead of {"ok":true}: a
     *  control the room says it didn't do ({"ok":false}). */
    val answers = java.util.concurrent.ConcurrentHashMap<String, String>()

    val exo = StatefulExoPlayer(log)

    private val server = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val body = request.body.readUtf8()
                log += "${request.method} $path" + (if (body.isNotBlank() && body != "{}") " $body" else "") +
                    if (request.method == "POST") targetTag() else ""
                delays[path]?.let { Thread.sleep(it) }
                failing[path]?.let { return MockResponse().setResponseCode(it).setBody("""{"detail":"refused"}""") }
                answers[path]?.let { return MockResponse().setBody(it) }
                if (request.method == "GET" && path == "/api/music/now-playing") {
                    return MockResponse().setBody(JsonArray(rooms.values.toList()).toString())
                }
                return MockResponse().setBody("""{"ok":true}""")
            }
        }
        start()
    }

    val player: PlayerController = PlayerController(
        ContextWrapper(null),
        ApiClient({ server.url("/").toString().trimEnd('/') }),
        allocateWithoutConstructor(Prefs::class.java),
    ).also {
        setField(it, "exoPlayer\$delegate", lazyOf(exo.player))
        exo.tag = ::targetTag
    }

    /** " [target=office]" or " [target=phone]": where the controls pointed. */
    private fun targetTag(): String = " [target=${roomTarget ?: "phone"}]"

    /** The room's now-playing row, as the web's /api/music/now-playing has it. */
    fun room(id: String, state: String, title: String?, elapsedSec: Double, artist: String? = "Kettle Band") {
        rooms[id] = buildJsonObject {
            put("room_id", id)
            put("state", state)
            put("elapsed_sec", elapsedSec)
            if (title != null) {
                put("song", buildJsonObject {
                    put("title", title)
                    put("artist", artist)
                    put("duration_sec", 200.0)
                })
            }
        }
    }

    /** The log without the background now-playing poll and the reads. */
    fun actions(): List<String> = log.filter { !it.startsWith("GET ") }

    /** Wait for a line starting with [prefix] (fire-and-forget posts). */
    fun awaitLog(prefix: String, timeoutMs: Long = 5_000): String {
        val until = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        while (System.nanoTime() < until) {
            log.firstOrNull { it.startsWith(prefix) }?.let { return it }
            Thread.sleep(10)
        }
        throw AssertionError("nothing starting with \"$prefix\" in $log")
    }

    /** Wait until the controller's poll has read [roomId]. */
    fun awaitRemote(roomId: String, timeoutMs: Long = 5_000) {
        val until = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        while (System.nanoTime() < until) {
            if (player.remote.value?.roomId == roomId) return
            Thread.sleep(10)
        }
        throw AssertionError("the poll never read $roomId (remote=${player.remote.value})")
    }

    val roomTarget: String? get() = (player.target.value as? PlayTarget.Room)?.roomId

    override fun close() {
        // The controller's scope lives as long as the app; here it ends with
        // the test, so its now-playing poll stops.
        (field(player, "scope") as CoroutineScope).cancel()
        server.shutdown()
    }
}

/** Library tracks, as the Music page queues them. */
internal fun libraryQueue(vararg titles: String): List<PlayItem> =
    titles.mapIndexed { i, t -> PlayItem.fromTrack(101L + i, t, "Kettle Band", "Cellar", 200.0) }

internal fun phoneSong(id: Long, title: String): PlayItem = PlayItem.fromDeviceAudio(
    id, title, "me", null, 120.0, "content://media/external/audio/media/$id", null,
)

internal fun ids(vararg v: Long): JsonArray = buildJsonArray { v.forEach { add(JsonPrimitive(it)) } }

/** Wait (polling) until [ok] holds; for what the controller posts in the
 *  background or reads on its 2 s poll. */
internal fun awaitUntil(timeoutMs: Long = 6_000, what: String = "condition", ok: () -> Boolean) {
    val until = System.currentTimeMillis() + timeoutMs
    while (System.currentTimeMillis() < until) {
        if (ok()) return
        Thread.sleep(20)
    }
    throw AssertionError("$what never held")
}

internal fun field(target: Any, name: String): Any? {
    var cls: Class<*>? = target.javaClass
    while (cls != null) {
        cls.declaredFields.firstOrNull { it.name == name }?.let {
            it.isAccessible = true
            return it.get(target)
        }
        cls = cls.superclass
    }
    error("${target.javaClass.name} has no field $name")
}

/**
 * An ExoPlayer that keeps the state the controller reads back — the
 * playlist, the current item and position, play/pause, idle/ready — and
 * logs every call that changes it. Everything else answers a default.
 */
internal class StatefulExoPlayer(private val log: MutableList<String>) {
    var items = mutableListOf<MediaItem>()
    var index = 0
    var positionMs = 0L
    var playWhenReady = false
    var state = Player.STATE_IDLE
    val listeners = CopyOnWriteArrayList<Player.Listener>()

    /** Commands this player says it can't do now (an ExoPlayer on its
     *  queue's first item has no previous item, say). */
    val unavailable = mutableSetOf<Int>()

    val playing: Boolean get() = playWhenReady && state == Player.STATE_READY

    /** Appended to each logged call: what else was true at that moment. */
    var tag: () -> String = { "" }

    private fun note(line: String) { log += line + tag() }

    val player: ExoPlayer = Proxy.newProxyInstance(
        ExoPlayer::class.java.classLoader,
        arrayOf(ExoPlayer::class.java),
    ) { proxy, method, args ->
        val a = args?.toList() ?: emptyList()
        when (method.name) {
            "equals" -> proxy === a.getOrNull(0)
            "hashCode" -> System.identityHashCode(proxy)
            "toString" -> "StatefulExoPlayer"
            "setMediaItems" -> {
                @Suppress("UNCHECKED_CAST")
                items = (a[0] as List<MediaItem>).toMutableList()
                if (a.size == 3) { index = a[1] as Int; positionMs = a[2] as Long } else { index = 0; positionMs = 0 }
                note("exo.setMediaItems(${items.size}, $index, $positionMs)"); null
            }
            "addMediaItem" -> {
                if (a.size == 2) items.add(a[0] as Int, a[1] as MediaItem) else items.add(a[0] as MediaItem)
                null
            }
            "removeMediaItem" -> { items.removeAt(a[0] as Int); null }
            "clearMediaItems" -> { items.clear(); index = 0; positionMs = 0; null }
            "prepare" -> { if (items.isNotEmpty()) state = Player.STATE_READY; note("exo.prepare"); null }
            "play" -> { playWhenReady = true; note("exo.play"); null }
            "pause" -> { playWhenReady = false; note("exo.pause"); null }
            "setPlayWhenReady" -> { playWhenReady = a[0] as Boolean; note("exo.setPlayWhenReady(${a[0]})"); null }
            "stop" -> { state = Player.STATE_IDLE; note("exo.stop"); null }
            "seekTo" -> {
                if (a.size == 2) { index = a[0] as Int; positionMs = a[1] as Long } else positionMs = a[0] as Long
                note("exo.seekTo(${a.joinToString(", ")})"); null
            }
            "seekToNext", "seekToNextMediaItem" -> {
                if (index < items.lastIndex) { index++; positionMs = 0 }
                note("exo.${method.name}"); null
            }
            "seekToPrevious", "seekToPreviousMediaItem" -> {
                if (index > 0) { index--; positionMs = 0 }
                note("exo.${method.name}"); null
            }
            "getCurrentMediaItemIndex" -> index
            "getMediaItemCount" -> items.size
            "getCurrentPosition", "getContentPosition" -> positionMs
            "getPlayWhenReady" -> playWhenReady
            "isPlaying" -> playing
            "getPlaybackState" -> state
            "getDuration", "getContentDuration" -> androidx.media3.common.C.TIME_UNSET
            "getMediaMetadata" -> MediaMetadata.Builder().setTitle("phone item $index").build()
            "isCommandAvailable" -> (a[0] as Int) !in unavailable
            "getAvailableCommands" -> Player.Commands.Builder().addAllCommands().build()
            "addListener" -> { listeners += a[0] as Player.Listener; null }
            "removeListener" -> { listeners -= a[0] as Player.Listener; null }
            else -> defaultOf(method.returnType)
        }
    } as ExoPlayer

    private fun defaultOf(type: Class<*>): Any? = when (type) {
        java.lang.Boolean.TYPE -> false
        java.lang.Integer.TYPE -> 0
        java.lang.Long.TYPE -> 0L
        java.lang.Float.TYPE -> 0f
        java.lang.Double.TYPE -> 0.0
        java.lang.Short.TYPE -> 0.toShort()
        java.lang.Byte.TYPE -> 0.toByte()
        java.lang.Character.TYPE -> 0.toChar()
        else -> null
    }
}
