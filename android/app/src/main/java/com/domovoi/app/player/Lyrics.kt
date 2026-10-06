package com.domovoi.app.player

import android.os.SystemClock
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.net.URLEncoder
import kotlin.math.roundToLong

/*
 * Lyrics for what is playing (lyrics-build CONTRACT §13, [D1]): the shapes the
 * web serves, the timing math the players share, and the repository the app
 * reads them through.
 *
 * The server does all of the LRC work: `.lrc` sidecars, the songs' own tags
 * and LRCLIB are read, parsed and stored there, the file's [offset:] is
 * already applied, and a doc arrives as time-ordered `{t, text}` lines in
 * milliseconds — an empty text marks a gap — plus the plain text. What is
 * left for a client is to keep that order, follow a position through it
 * ([LyricsMath]) and show the right state ([LyricsView]).
 *
 * Lyrics are the household's only (device token / admin / dashboard cookie):
 * a 401 or 403 is [LyricsResult.Hidden] and every lyrics surface shows
 * nothing. Their text is NEVER logged — no Log.*, no ProblemLog, no
 * Diagnostics, no notification, no media session — and nothing here builds
 * an exception message out of it ([D11]).
 */

/** One timed line: [t] milliseconds from the start, offset applied. An empty [text] is a gap. */
@Serializable
data class LyricLine(val t: Long = 0, val text: String = "")

@Serializable
data class LyricsDoc(
    @SerialName("track_id") val trackId: Long = 0,
    val status: String = "none",                 // synced | plain | instrumental | none
    val checking: Boolean = false,
    val source: String? = null,                  // sidecar | embedded | lrclib
    @SerialName("source_label") val sourceLabel: String? = null,
    val lines: List<LyricLine>? = null,
    val text: String? = null,
) {
    /**
     * The timed lines the way the players use them: time order (the server
     * sends it; kept stable here, so equal times stay in file order), no
     * negative times, at most [LyricsMath.MAX_LINES]. Empty unless the doc
     * is `synced` and at least one line has words.
     */
    fun timedLines(): List<LyricLine> {
        if (status != "synced") return emptyList()
        val raw = lines.orEmpty()
        if (raw.none { it.text.isNotBlank() }) return emptyList()
        val clean = raw.asSequence()
            .map { if (it.t < 0) it.copy(t = 0) else it }
            .take(LyricsMath.MAX_LINES)
            .toList()
        return if (clean.zipWithNext().all { (a, b) -> a.t <= b.t }) clean else clean.sortedBy { it.t }
    }

    /** What a lyrics surface shows for this doc (the web's LyricsView states, [U11]). */
    fun view(): LyricsView {
        val timed = timedLines()
        val plain = text?.takeIf { it.isNotBlank() }
        return when {
            timed.isNotEmpty() -> LyricsView.Timed(timed)
            // A synced doc whose lines are unusable still has its plain text.
            (status == "synced" || status == "plain") && plain != null -> LyricsView.Plain(plain)
            status == "instrumental" -> LyricsView.Instrumental
            checking -> LyricsView.Looking
            else -> LyricsView.NoLyrics
        }
    }
}

/** The states a lyrics surface can be in once a doc is here. */
sealed interface LyricsView {
    data class Timed(val lines: List<LyricLine>) : LyricsView
    data class Plain(val text: String) : LyricsView
    data object Instrumental : LyricsView
    /** No lyrics yet, and Domovoi is still looking (the doc's `checking`). */
    data object Looking : LyricsView
    data object NoLyrics : LyricsView
}

/**
 * `GET /api/music/now-playing/{room}/lyrics`: what a room plays, where it
 * is, and that song's lyrics in one call. [receivedAtMs] is this phone's
 * clock ([SystemClock.elapsedRealtime] by default) when the answer came: the
 * room's position is anchored on the receipt, never on the server's clock.
 */
@Serializable
data class RoomLyrics(
    @SerialName("room_id") val roomId: String = "",
    val state: String = "stop",
    @SerialName("track_id") val trackId: Long? = null,
    @SerialName("elapsed_sec") val elapsedSec: Double? = null,
    @SerialName("duration_sec") val durationSec: Double? = null,
    @SerialName("line_index") val lineIndex: Int? = null,
    val lyrics: LyricsDoc? = null,
    @Transient val receivedAtMs: Long = 0L,
)

/** Pure timing math, shared by the player tab, the sheet and the room cards. */
object LyricsMath {
    /** A line is shown this much before its time. */
    const val LEAD_MS = 150L

    /** Timed lines kept per song (the server stores at most as many). */
    const val MAX_LINES = 4000

    /** A backward correction smaller than this, while playing, is held (see [steady]). */
    const val STEADY_WINDOW_MS = 300L

    /** The last line with t <= positionMs + LEAD_MS; -1 before the first (binary search). */
    fun activeIndex(lines: List<LyricLine>, positionMs: Long): Int {
        if (lines.isEmpty()) return -1
        val at = if (positionMs > Long.MAX_VALUE - LEAD_MS) Long.MAX_VALUE else positionMs + LEAD_MS
        var lo = 0
        var hi = lines.lastIndex
        var found = -1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            if (lines[mid].t <= at) {
                found = mid
                lo = mid + 1
            } else {
                hi = mid - 1
            }
        }
        return found
    }

    /** elapsed*1000 + (playing ? now - readAt : 0) - nudge, clamped to [0, duration]. */
    fun roomPositionMs(
        elapsedSec: Double, readAtMs: Long, nowMs: Long, playing: Boolean,
        durationSec: Double?, nudgeMs: Long,
    ): Long {
        val base = if (elapsedSec.isFinite()) (elapsedSec * 1000.0).roundToLong() else 0L
        // A clock read before the anchor (it never should be) adds nothing.
        val run = if (playing) (nowMs - readAtMs).coerceAtLeast(0L) else 0L
        return clampToTrack(base + run - nudgeMs, durationSec)
    }

    /**
     * This phone's own playback between the player's 500 ms position ticks:
     * the tick's position, plus the time since it was seen at the playback
     * [speed] while playing, clamped to [0, duration].
     */
    fun localPositionMs(
        tickSec: Double, tickAtMs: Long, nowMs: Long, playing: Boolean,
        speed: Float, durationSec: Double?,
    ): Long {
        val base = if (tickSec.isFinite()) (tickSec * 1000.0).roundToLong() else 0L
        val rate = if (speed.isFinite() && speed > 0f) speed.toDouble() else 1.0
        val run = if (playing) ((nowMs - tickAtMs).coerceAtLeast(0L) * rate).roundToLong() else 0L
        return clampToTrack(base + run, durationSec)
    }

    /**
     * The position to show after [previousMs] when the clock now says
     * [nextMs]. While playing, a step back smaller than [STEADY_WINDOW_MS] is
     * a re-anchoring jitter (a tick or a poll seen a little late), not a seek,
     * and is held — so a line never flickers back and forth at its boundary.
     * A bigger step back is a seek and is followed at once; paused, everything is.
     */
    fun steady(previousMs: Long?, nextMs: Long, playing: Boolean): Long =
        if (playing && previousMs != null && nextMs < previousMs && previousMs - nextMs < STEADY_WINDOW_MS) {
            previousMs
        } else {
            nextMs
        }

    private fun clampToTrack(ms: Long, durationSec: Double?): Long {
        val atLeastZero = ms.coerceAtLeast(0L)
        val dur = durationSec?.takeIf { it.isFinite() && it > 0 } ?: return atLeastZero
        return atLeastZero.coerceAtMost((dur * 1000.0).roundToLong())
    }
}

/**
 * The room timing nudge ([D5], the web's [U9]): a room's speaker plays a
 * little behind MPD's clock (its stream buffer), so each room keeps an
 * offset this phone applies to that room's lyrics. A positive value shows
 * the lyrics LATER. Stored in Prefs `lyrics_room_nudge` as a JSON map
 * room → milliseconds.
 */
object LyricsNudge {
    const val STEP_MS = 250L
    const val MAX_MS = 10_000L

    fun clamp(ms: Long): Long = ms.coerceIn(-MAX_MS, MAX_MS)

    /** The stored map; anything unreadable is dropped, every value clamped. */
    fun decode(json: String?): Map<String, Long> {
        if (json.isNullOrBlank()) return emptyMap()
        val obj = runCatching { DomovoiJson.parseToJsonElement(json) as? JsonObject }.getOrNull() ?: return emptyMap()
        return obj.mapNotNull { (room, value) ->
            val ms = (value as? JsonPrimitive)?.takeIf { !it.isString }?.longOrNull ?: return@mapNotNull null
            if (room.isBlank()) null else room to clamp(ms)
        }.filter { it.second != 0L }.toMap()
    }

    fun encode(map: Map<String, Long>): String =
        buildJsonObject { map.toSortedMap().forEach { (room, ms) -> put(room, ms) } }.toString()

    /** [map] with [room] set to [ms] (clamped); zero removes the room. */
    fun with(map: Map<String, Long>, room: String, ms: Long): Map<String, Long> {
        val v = clamp(ms)
        return if (v == 0L) map - room else map + (room to v)
    }

    /** "timing", or "timing +0.75 s" / "timing −0.5 s" while nudged. */
    fun label(ms: Long): String {
        if (ms == 0L) return "timing"
        val sign = if (ms > 0) "+" else "−"
        val sec = kotlin.math.abs(ms) / 1000.0
        val text = if (sec == sec.toLong().toDouble()) sec.toLong().toString() else sec.toString().trimEnd('0')
        return "timing $sign$text s"
    }
}

sealed interface LyricsResult {
    data class Loaded(val doc: LyricsDoc) : LyricsResult
    /** 401 / 403: not the household tier — show nothing. Also a server
     *  without lyrics at all (see [LyricsRepository]). */
    data object Hidden : LyricsResult
    data object Failed : LyricsResult
}

/**
 * Lyrics through the household tier's routes. [forTrack] keeps the last
 * [CACHE_SIZE] docs (least recently used out); a doc that is still
 * `checking` (Domovoi is still looking) is kept only [CHECKING_TTL_MS].
 * Only a doc is cached: a refusal or a failure is asked again next time, so
 * a phone paired a moment later sees its lyrics. Concurrent asks for one
 * track share one request. Keyed by server, so switching servers never
 * shows another house's lyrics for a track id they happen to share.
 *
 * Hidden also covers a server from before lyrics (its route is missing:
 * FastAPI's plain 404 "Not Found", or 503 "lyrics are not set up on this
 * server yet"): there is nothing to show there, rather than "couldn't load
 * the lyrics" on every song. A track the server doesn't know (404 "track N
 * not found") is Failed, like any other error.
 */
class LyricsRepository(
    private val api: ApiClient,
    private val clock: () -> Long = { SystemClock.elapsedRealtime() },
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Any()

    private class Cached(val doc: LyricsDoc, val atMs: Long)

    private val cache = object : LinkedHashMap<String, Cached>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Cached>?): Boolean = size > CACHE_SIZE
    }
    private val inflight = HashMap<String, Deferred<LyricsResult>>()

    /** GET /api/music/library/{id}/lyrics; LRU of 30; a checking doc expires after 60 s. */
    suspend fun forTrack(trackId: Long, refresh: Boolean = false): LyricsResult {
        val key = keyFor(trackId)
        val job = synchronized(lock) {
            if (!refresh) cachedLocked(key)?.let { return LyricsResult.Loaded(it) }
            inflight[key] ?: scope.async(start = CoroutineStart.LAZY) { fetchTrack(trackId, key) }.also { job ->
                inflight[key] = job
                job.invokeOnCompletion { synchronized(lock) { if (inflight[key] === job) inflight.remove(key) } }
                job.start()
            }
        }
        return job.await()
    }

    /** [trackId]'s cached doc, without asking: what a surface shows on its first frame. */
    fun cached(trackId: Long): LyricsDoc? = synchronized(lock) { cachedLocked(keyFor(trackId)) }

    /**
     * GET /api/music/now-playing/{room}/lyrics: the room's song, its place
     * and its lyrics in one call; null when that can't be read (any
     * refusal or failure — the caller falls back to [forTrack], which tells
     * a refusal apart). The doc it carries goes into the cache.
     */
    suspend fun forRoom(roomId: String): RoomLyrics? {
        val room = try {
            api.get("/api/music/now-playing/${pathSegment(roomId)}/lyrics").decode<RoomLyrics>()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return null
        }
        val stamped = room.copy(receivedAtMs = clock())
        val id = stamped.trackId
        val doc = stamped.lyrics
        if (id != null && doc != null) synchronized(lock) { cache[keyFor(id)] = Cached(doc, clock()) }
        return stamped
    }

    private suspend fun fetchTrack(trackId: Long, key: String): LyricsResult {
        val result = try {
            LyricsResult.Loaded(api.get("/api/music/library/$trackId/lyrics").decode<LyricsDoc>())
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (isHiddenRefusal(e)) LyricsResult.Hidden else LyricsResult.Failed
        } catch (e: Exception) {
            // A transport failure, or a body that is not a doc. Its message
            // can quote the body, so it goes nowhere ([D11]).
            LyricsResult.Failed
        }
        if (result is LyricsResult.Loaded) synchronized(lock) { cache[key] = Cached(result.doc, clock()) }
        return result
    }

    private fun cachedLocked(key: String): LyricsDoc? {
        val hit = cache[key] ?: return null
        if (hit.doc.checking && clock() - hit.atMs >= CHECKING_TTL_MS) {
            cache.remove(key)
            return null
        }
        return hit.doc
    }

    private fun keyFor(trackId: Long): String = "${api.baseUrl}#$trackId"

    companion object {
        const val CACHE_SIZE = 30
        const val CHECKING_TTL_MS = 60_000L

        internal fun isHiddenRefusal(e: ApiException): Boolean = when (e.status) {
            401, 403 -> true
            404 -> detailOf(e) == "Not Found"
            503 -> detailOf(e)?.contains("not set up", ignoreCase = true) == true
            else -> false
        }

        private fun detailOf(e: ApiException): String? = runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content
        }.getOrNull()

        private fun pathSegment(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
