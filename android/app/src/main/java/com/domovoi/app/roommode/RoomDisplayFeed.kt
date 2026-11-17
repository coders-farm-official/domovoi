package com.domovoi.app.roommode

import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/**
 * What the room display shows beyond the room's own status: this room's
 * music, its timers and alarms, and a timer going off right now. All of it
 * comes from the server, over the room's own socket (the room display
 * protocol, on a core since 1.4.0; see [RoomDisplayFeed]). Until a core sends it, [feed] is
 * null and the display hides those sections: nothing is guessed, nothing is
 * a placeholder.
 *
 * Why the socket and not the dashboard's routes: a room phone holds only
 * the room's pairing key, which the core accepts on `/v1/stream/{room}` and
 * nowhere else. The routes the app and the dashboard use for a room's music
 * and timers are the web dashboard's (`/api/music/now-playing`,
 * `/api/satellites/{room}/timers`), on a server address the room never
 * proved, or take the household token, which a room never sends.
 */
interface RoomDisplaySource {
    /** The latest `room_display` from the server; null: none (an older
     *  core, or no session right now). */
    val feed: StateFlow<RoomFeed?>

    /** Ask the server to do [action] in this room. False when it could not
     *  be sent (no session, or a core that does not take it). */
    fun control(action: RoomControl): Boolean

    /** This room's output level, 0-100. */
    fun volume(): Int

    /** Set this room's output level and tell the server (`volume_status`,
     *  the frame a Pi reports its level with). False with no session. */
    fun setVolume(level: Int): Boolean

    /** The bytes of an artwork path from the feed, from the room's own
     *  server; null when it cannot be had. */
    suspend fun art(path: String): ByteArray?
}

enum class PlayState { PLAY, PAUSE }

/** The transport verbs, named as the core's `/v1/admin/music/{action}`. */
enum class Transport(val wire: String) { PAUSE("pause"), RESUME("resume"), SKIP("skip"), PREVIOUS("previous") }

enum class TimerKind { TIMER, ALARM }

/** What kind of thing a room is playing (`now_playing.source`). */
enum class MediaSource(val wire: String) {
    LIBRARY("library"), RADIO("radio"), SLEEP("sleep"), PODCAST("podcast"), AUDIOBOOK("audiobook"), OTHER("other");

    companion object {
        /** A value this app does not know yet is "other"; none at all is null. */
        fun of(wire: String?): MediaSource? = wire?.let { w -> entries.firstOrNull { it.wire == w } ?: OTHER }
    }
}

data class FeedNowPlaying(
    val state: PlayState,
    val title: String?,
    val artist: String?,
    val album: String?,
    val source: MediaSource?,
    /** A path on the room's server for the cover (`/v1/music/cover/{id}`,
     *  library tracks only); null: none, and no artwork tile is drawn. */
    val art: String?,
    val durationSec: Double?,
    val elapsedSec: Double?,
    /** What `room_control` will do in this room right now. */
    val controls: Set<Transport>,
)

data class FeedTimer(
    val id: Long,
    val kind: TimerKind,
    val label: String?,
    /** Left when the frame was sent. */
    val remainingMs: Long,
    /** The whole length, when known. */
    val totalMs: Long?,
)

data class FeedRinging(val fireId: Long, val kind: TimerKind, val label: String?)

data class RoomFeed(
    val nowPlaying: FeedNowPlaying?,
    val timers: List<FeedTimer>,
    val ringing: FeedRinging?,
    /** Monotonic ms when it arrived (the engine stamps it): every
     *  remaining time counts down from here, so the phone's wall clock
     *  being off never moves a countdown. */
    val receivedAt: Long = 0L,
    /** The server's monotonic ms when it sent this frame (`sent_at_ms`):
     *  for ordering only, never shown. Null: an older shape. */
    val sentAtMs: Long? = null,
)

sealed class RoomControl {
    data class Music(val verb: Transport) : RoomControl()
    data class StopRinging(val fireId: Long) : RoomControl()
}

/**
 * The frames, both on `/v1/stream/{room}` (shape accepted by the core
 * team 2026-11-16; no released core sends or takes them yet):
 *
 * Server → room, once after `ready` and then on change only (at most about
 * once a second); gated on the ability [ABILITY]:
 *
 *     {"type":"room_display","sent_at_ms":N,
 *      "now_playing": null | {"state":"play"|"pause","title":..,"artist":..,"album":..,
 *                             "source":"library"|"radio"|"sleep"|"podcast"|"audiobook"|"other",
 *                             "art":"/v1/music/cover/{id}"|null,"duration_sec":N,"elapsed_sec":N,
 *                             "controls":["pause","resume","skip","previous"]},
 *      "timers": [{"id":N,"kind":"timer"|"alarm","label":..,"remaining_ms":N,"total_ms":N}],
 *      "ringing": null | {"fire_id":N,"kind":"timer"|"alarm","label":..}}
 *
 * Between frames the phone moves things on itself: `elapsed_sec` while
 * the state is "play", each `remaining_ms` always ([RoomDisplayModel]). A
 * frame older than the last one applied (`sent_at_ms`) is dropped
 * ([newer]). In a group every member gets the group's `now_playing` and
 * its controls act on the group. Reminders are never sent.
 *
 * Room → server, only when `ready.features` lists [ABILITY]:
 *
 *     {"type":"room_control","action":"pause"|"resume"|"skip"|"previous"}
 *     {"type":"room_control","action":"stop_ringing","fire_id":N}   (a timer or an alarm)
 */
object RoomDisplayFeed {
    /** The ability name (hello `supports`, and the core's `ready.features`). */
    const val ABILITY = "room_display"

    /** Longest text kept from the server (a title, a label). */
    private const val MAX_TEXT = 200
    private const val MAX_TIMERS = 20

    fun parse(o: JsonObject): RoomFeed {
        val np = o["now_playing"] as? JsonObject
        return RoomFeed(
            sentAtMs = o.long("sent_at_ms"),
            nowPlaying = np?.let(::nowPlaying),
            timers = (o["timers"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::timer) }.take(MAX_TIMERS),
            ringing = (o["ringing"] as? JsonObject)?.let(::ringing),
        )
    }

    private fun nowPlaying(o: JsonObject): FeedNowPlaying? {
        val state = when (o.str("state")) {
            "play" -> PlayState.PLAY
            "pause" -> PlayState.PAUSE
            else -> return null
        }
        val controls = (o["controls"] as? JsonArray).orEmpty().mapNotNull { e ->
            val w = (e as? JsonPrimitive)?.contentOrNull
            Transport.entries.firstOrNull { it.wire == w }
        }.toSet()
        return FeedNowPlaying(
            state = state,
            title = o.text("title"),
            artist = o.text("artist"),
            album = o.text("album"),
            source = MediaSource.of(o.str("source")),
            art = o.str("art")?.takeIf(::isArtPath),
            durationSec = o.num("duration_sec")?.takeIf { it > 0 },
            elapsedSec = o.num("elapsed_sec")?.takeIf { it >= 0 },
            controls = controls,
        )
    }

    private fun timer(o: JsonObject): FeedTimer? {
        val id = o.long("id") ?: return null
        val remaining = o.long("remaining_ms") ?: return null
        return FeedTimer(
            id = id,
            kind = kind(o.str("kind")) ?: return null,
            label = o.text("label"),
            remainingMs = remaining.coerceAtLeast(0),
            totalMs = o.long("total_ms")?.takeIf { it > 0 },
        )
    }

    private fun ringing(o: JsonObject): FeedRinging? {
        val id = o.long("fire_id") ?: return null
        return FeedRinging(id, kind(o.str("kind")) ?: TimerKind.TIMER, o.text("label"))
    }

    private fun kind(s: String?): TimerKind? = when (s) {
        "timer" -> TimerKind.TIMER
        "alarm" -> TimerKind.ALARM
        else -> null
    }

    /** Whether [incoming] may replace [current]: frames can overtake each
     *  other, so one sent before the frame on screen is dropped. A frame
     *  (or a screen) without `sent_at_ms` is applied as it comes. */
    fun newer(current: RoomFeed?, incoming: RoomFeed): Boolean {
        val was = current?.sentAtMs ?: return true
        val now = incoming.sentAtMs ?: return true
        return now >= was
    }

    /** Only the core's cover route, exactly as the core writes it
     *  (`/v1/music/cover/{track_id}`, domovoi/room_display.py): the room
     *  never follows a link to another host, and a path with anything else
     *  in it (percent-encoding, `..`, a query, another route) is refused. */
    fun isArtPath(p: String): Boolean = ART_PATH.matches(p)

    private val ART_PATH = Regex("/v1/music/cover/[0-9]{1,10}")

    /** A cover's longest side is decoded to at most about this. */
    const val ART_LONG_SIDE = 1024

    /** A cover with a side longer than this is not decoded at all. */
    const val ART_MAX_SIDE = 4096

    /** Nor one longer than this many times its width (or wider than tall). */
    const val ART_MAX_ASPECT = 8

    /**
     * The `inSampleSize` for a cover of [width] x [height] (read with
     * inJustDecodeBounds first), or null: do not decode it. Refused: no
     * size, a side over [ART_MAX_SIDE], or a ratio past [ART_MAX_ASPECT]
     * to 1 (a strip that small-side sampling would leave enormous). The
     * sample is chosen from the LONGER side, so the bitmap's long side is
     * at most [ART_LONG_SIDE].
     */
    fun artSampleSize(width: Int, height: Int): Int? {
        if (width <= 0 || height <= 0) return null
        val long = maxOf(width, height)
        val short = minOf(width, height)
        if (long > ART_MAX_SIDE) return null
        if (long > short.toLong() * ART_MAX_ASPECT) return null
        var sample = 1
        while (long / sample > ART_LONG_SIDE) sample *= 2
        return sample
    }

    fun control(c: RoomControl): JsonObject = buildJsonObject {
        put("type", "room_control")
        when (c) {
            is RoomControl.Music -> put("action", c.verb.wire)
            is RoomControl.StopRinging -> {
                put("action", "stop_ringing")
                put("fire_id", c.fireId)
            }
        }
    }

    private fun JsonObject.str(k: String): String? = (this[k] as? JsonPrimitive)?.takeIf { it.isString }?.content
    private fun JsonObject.text(k: String): String? = str(k)?.trim()?.takeIf { it.isNotEmpty() }?.take(MAX_TEXT)
    private fun JsonObject.long(k: String): Long? = (this[k] as? JsonPrimitive)?.let { it.longOrNull ?: it.doubleOrNull?.toLong() }
    private fun JsonObject.num(k: String): Double? = (this[k] as? JsonPrimitive)?.doubleOrNull?.takeIf { it.isFinite() }
}
