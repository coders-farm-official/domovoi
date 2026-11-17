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
 * comes from the server, over the room's own socket (PROPOSED protocol, not
 * on any core yet; see [RoomDisplayFeed]). Until a core sends it, [feed] is
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

data class FeedNowPlaying(
    val state: PlayState,
    val title: String?,
    val artist: String?,
    val album: String?,
    /** A generic source name for a stream ("radio"), never a provider's. */
    val source: String?,
    /** A path on the room's server for the cover; null: none. */
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
)

sealed class RoomControl {
    data class Music(val verb: Transport) : RoomControl()
    data class StopRinging(val fireId: Long) : RoomControl()
}

/**
 * The PROPOSED frames, both on `/v1/stream/{room}` (the core team's to
 * accept or reshape; until then no core sends or takes them):
 *
 * Server → room, after `ready` and on every change to this room's music,
 * timers or a fire going off here; gated on the ability [ABILITY]:
 *
 *     {"type":"room_display",
 *      "now_playing": null | {"state":"play"|"pause","title":..,"artist":..,"album":..,
 *                             "source":..,"art":"/v1/...","duration_sec":N,"elapsed_sec":N,
 *                             "controls":["pause","resume","skip","previous"]},
 *      "timers": [{"id":N,"kind":"timer"|"alarm","label":..,"remaining_ms":N,"total_ms":N}],
 *      "ringing": null | {"fire_id":N,"kind":"timer"|"alarm","label":..}}
 *
 * Room → server, only when `ready.features` lists [ABILITY]:
 *
 *     {"type":"room_control","action":"pause"|"resume"|"skip"|"previous"}
 *     {"type":"room_control","action":"stop_ringing","fire_id":N}
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
            source = o.text("source"),
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

    /** Only a plain path on the room's own server: the room never follows
     *  a link to another host, and nothing climbs out of `/v1/`. */
    fun isArtPath(p: String): Boolean =
        p.startsWith("/v1/") && p.length <= 300 && ".." !in p && "//" !in p && "\\" !in p &&
            p.none { it.isWhitespace() || it.code < 0x20 || it.code == 0x7f }

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
