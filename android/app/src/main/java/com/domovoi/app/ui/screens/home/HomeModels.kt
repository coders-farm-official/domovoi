package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.Capabilities
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.shell.EverythingRoutes
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.visibleOn
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToLong

// ---------------------------------------------------------------------------
// Home — the pure half: wire models for the reads Home composes, and every
// rule web/static/home.jsx applies to them, kept free of Compose so the JVM
// tests can hold them to the web's behaviour. HomeScreen.kt draws it.
//
// Every model tolerates missing fields (nullable + defaults): the backend
// evolves, and DomovoiJson ignores unknown keys.
// ---------------------------------------------------------------------------

internal const val HOME_LIVE_POLL_MS = 30_000L       // rooms + timers, only while the socket is down
internal const val HOME_HEALTH_MS = 60_000L          // health + the problem-rows setting
internal const val HOME_FOCUS_GAP_MS = 15_000L       // back on screen: re-read at most this often
internal const val HOME_DONE_MS = 60_000L            // "done · kitchen" lingers this long
internal const val HOME_ROOM_DEBOUNCE_MS = 300L      // every /api/satellites read opens an MPD connection per room
internal const val HOME_STOP_ALL_ARM_MS = 4_000L
internal const val HOME_PHONE_ROWS = 3               // attention rows and today rows a phone shows
internal const val HOME_TODAY_ROWS = 6               // ...and a wider screen
internal const val HOME_PHONE_TIMERS = 2             // timers a phone shows, so the rooms start above the fold
internal const val HOME_FRESH_MS = 5_000L            // a read this young needs no re-read on (re)connect
internal const val HOME_SOON_SEC = 600L              // countdowns turn warn under 10 min

/** Room pushes Home re-reads on (debounced). The Wi-Fi one is merged, not re-read. */
internal val HOME_ROOM_EVENTS = setOf(
    "satellites.presence.changed", "satellites.wifi.changed", "satellites.dropins.changed",
    "satellites.display.changed", "satellites.pending.changed", "music.now_playing.changed",
)
internal const val HOME_WIFI_EVENT = "satellites.wifi.changed"

// ─── Wire models ──────────────────────────────────────────────────────────

/** GET /api/config — only what Home reads. */
@Serializable
internal data class HomeConfig(
    val bot_name: String? = null,
    // "everyone" | "summary" | "admins" (HOME_PROBLEMS_VISIBILITY). Absent
    // from a server older than the setting, which behaves as "everyone".
    val home_problems_visibility: String? = null,
)

/** GET /api/health. */
@Serializable
internal data class HomeHealth(
    val status: String? = null,
    val db_reachable: Boolean? = null,
    val domovoi_reachable: Boolean? = null,
    // ok / fallback / unavailable / stub / not_loaded; null when the core
    // did not answer (or the server predates the field).
    val stt: String? = null,
)

/** One row of GET /api/timers. A reminder is a timer with a message. */
@Serializable
internal data class HomeTimer(
    val id: Long = 0,
    val expires_at: String? = null,
    val created_at: String? = null,
    val label: String? = null,
    val message: String? = null,
    val room_id: String? = null,
    val is_reminder: Boolean = false,
)

/** Where a fire was announced, and how it went (only what Home reads). */
@Serializable
internal data class HomeFireDelivery(
    val room_id: String? = null,
    // pending | sending | spoken | interrupted | failed | offline | busy_timeout | cancelled
    val outcome: String? = null,
)

/** A timer or reminder that went off, from the server's fire ledger — only
 *  the fields Home needs. `room_id` is where it was set (null: no room). */
@Serializable
internal data class HomeFire(
    val id: Long = 0,
    val timer_id: Long = 0,
    val kind: String? = null,
    val is_reminder: Boolean = false,
    val label: String? = null,
    val message: String? = null,
    val masked: Boolean = false,
    val room_id: String? = null,
    val created_at: String? = null,
    val due_at: String? = null,
    val fired_at: String? = null,
    val heard_in: List<String> = emptyList(),
    // "heard in garage, kitchen · still announcing" — the server's words, no speech.
    val summary: String? = null,
    val deliveries: List<HomeFireDelivery> = emptyList(),
)

/** GET /api/timers: every timer in the house, soonest first, plus the
 *  server's clock — the one that decides when a timer fires — and the
 *  fires of the last ten minutes. `fires` is null from a server without
 *  the fire ledger (older, or V017 not applied): Home then falls back to
 *  guessing from rows that vanish ([TimerBook]). */
@Serializable
internal data class HomeTimerList(
    val server_now: String? = null,
    val timers: List<HomeTimer> = emptyList(),
    val fires: List<HomeFire>? = null,
)

@Serializable
internal data class HomeSong(
    val title: String? = null,
    val artist: String? = null,
    val file: String? = null,
    val duration_sec: Double? = null,
)

@Serializable
internal data class HomeNowPlaying(
    val state: String? = null,
    val song: HomeSong? = null,
    val elapsed_sec: Double? = null,
)

@Serializable
internal data class HomeWifi(
    val rx_mbits: Double? = null,
    val tx_mbits: Double? = null,
)

@Serializable
internal data class HomeDisplay(
    val kiosk_alive: Boolean? = null,
)

/** One room of GET /api/satellites (all rooms in one call). */
@Serializable
internal data class HomeRoom(
    val room_id: String = "",
    // "online" | "offline" | "waiting" (set up, never connected)
    val status: String? = null,
    val last_connected_at: String? = null,
    val now_playing: HomeNowPlaying? = null,
    val wifi: HomeWifi? = null,
    val in_call_with: String? = null,
    val sat_type: String = "voice",
    val room_label: String? = null,
    val display: HomeDisplay? = null,
) {
    val online: Boolean get() = status == "online"
}

/** One plugin of GET /api/plugins (open: errors show for everyone). */
@Serializable
internal data class HomePlugin(
    val slug: String = "",
    val name: String? = null,
    val enabled: Boolean? = null,
    val status: String? = null,
    val web_load_error: JsonElement? = null,
    val page_errors: JsonElement? = null,
)

@Serializable
internal data class HomePlugins(val plugins: List<HomePlugin> = emptyList())

/** A media request — only its kind and status; never its text. */
@Serializable
internal data class HomeAcquisition(
    val id: Long = 0,
    val kind: String? = null,
    val status: String? = null,
)

@Serializable
internal data class HomeAcquisitions(
    val acquisitions: List<HomeAcquisition> = emptyList(),
    val can_fulfill_query: Boolean? = null,
    val can_fulfill_url: Boolean? = null,
    val core_reachable: Boolean = false,
)

/** A calendar event as Home shows it. No description field on purpose:
 *  Home never shows one, so it never even decodes one. */
@Serializable
internal data class HomeEvent(
    val id: Long = 0,
    val title: String = "",
    val starts_at: String = "",
    val ends_at: String? = null,
    val location: String? = null,
)

@Serializable
internal data class HomeManualHandler(
    val name: String = "",
    val example_phrases: List<String> = emptyList(),
)

/** GET /api/capabilities/manual — only asked for on a first run. */
@Serializable
internal data class HomeManual(val handlers: List<HomeManualHandler> = emptyList())

@Serializable
internal data class HomeAnnounceResult(val announced_to: List<String> = emptyList())

// ─── Small helpers ────────────────────────────────────────────────────────

internal fun isoMs(iso: String?): Long? = parseInstant(iso)?.toEpochMilli()

internal fun plural(n: Int, one: String, many: String = "${one}s"): String =
    "$n ${if (n == 1) one else many}"

private val HOME_WEEKDAYS = listOf("mon", "tue", "wed", "thu", "fri", "sat", "sun")
private val HOME_MONTHS = listOf("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

/** "mon 28 sep" — the header's date (web HomeFmtDate). */
internal fun homeDate(ms: Long, zone: ZoneId): String {
    val d = Instant.ofEpochMilli(ms).atZone(zone)
    return "${HOME_WEEKDAYS[d.dayOfWeek.value - 1]} ${d.dayOfMonth} ${HOME_MONTHS[d.monthValue - 1]}"
}

private val clockFmt = DateTimeFormatter.ofPattern("h:mm a", Locale.US)

/** "9:00am" — the calendar's clock (web fmtClock). */
internal fun homeClock(ms: Long, zone: ZoneId): String =
    Instant.ofEpochMilli(ms).atZone(zone).format(clockFmt).lowercase(Locale.US).replace(" ", "")

/** Local midnight of the day [ms] falls in, [plusDays] later. */
internal fun dayStartMs(ms: Long, zone: ZoneId, plusDays: Long = 0): Long =
    Instant.ofEpochMilli(ms).atZone(zone).toLocalDate().plusDays(plusDays)
        .atStartOfDay(zone).toInstant().toEpochMilli()

/** "12m 04s", "1h 5m", "40s" — the web's fmtRemaining; "2d 3h" past a day. */
internal fun fmtLeft(sec: Long): String {
    val s = max(0L, sec)
    if (s >= 86_400) return "${s / 86_400}d ${(s % 86_400) / 3600}h"
    val h = s / 3600
    val m = (s % 3600) / 60
    val ss = s % 60
    return when {
        h > 0 -> "${h}h ${m}m"
        m > 0 -> "${m}m ${ss.toString().padStart(2, '0')}s"
        else -> "${ss}s"
    }
}

// ─── Timers ───────────────────────────────────────────────────────────────

/**
 * What a timer is called. A reminder's words are household content and show
 * — except on a shared screen, where it reads "reminder · office". A timer's
 * own label ("pasta") is low-risk and always shows.
 */
internal fun timerTitle(t: HomeTimer, shared: Boolean): String {
    if (t.is_reminder) {
        return if (shared) "reminder · ${t.room_id ?: "no room"}"
        else t.message?.takeIf { it.isNotEmpty() } ?: t.label?.takeIf { it.isNotEmpty() } ?: "reminder"
    }
    t.label?.takeIf { it.isNotEmpty() }?.let { return it }
    val at = isoMs(t.expires_at)
    val created = isoMs(t.created_at)
    val total = if (at != null && created != null) (at - created) / 1000.0 else Double.NaN
    if (!(total > 0)) return "timer"
    return if (total < 90) "${total.roundToLong()}s timer" else "${(total / 60).roundToLong()} min timer"
}

/** ...and what a toast calls it: "cancelled pasta timer". */
internal fun timerNoun(t: HomeTimer, shared: Boolean): String = when {
    t.is_reminder -> "reminder"
    !t.label.isNullOrEmpty() -> "${t.label} timer"
    else -> timerTitle(t, shared)
}

/** Whole seconds until [t] fires, against the SERVER's clock ([nowMs] is
 *  already offset by [serverOffsetMs]). Never negative. */
internal fun secondsLeft(t: HomeTimer, nowMs: Long): Long {
    val at = isoMs(t.expires_at) ?: return 0
    return max(0L, ((at - nowMs) / 1000.0).roundToLong())
}

/** How much of [t] has run, 0..1, for its elapsed bar. */
internal fun elapsedFraction(t: HomeTimer, nowMs: Long): Float {
    val at = isoMs(t.expires_at) ?: return 0f
    val created = isoMs(t.created_at) ?: return 0f
    val span = at - created
    if (span <= 0) return 0f
    return ((nowMs - created).toFloat() / span).coerceIn(0f, 1f)
}

/** Server clock minus this phone's, from a read received at [receivedAtMs]:
 *  a phone whose clock is minutes off still counts down to the second. */
internal fun serverOffsetMs(serverNow: String?, receivedAtMs: Long): Long =
    isoMs(serverNow)?.let { it - receivedAtMs } ?: 0L

/** A "done · kitchen" line: what fired, when, and — from the fire ledger —
 *  where it was heard ([summary]) and the dot's tone. */
internal data class DoneTimer(
    val timer: HomeTimer,
    val doneAtMs: Long,
    val summary: String? = null,
    val tone: Tone = Tone.Ok,
)

internal data class TimerView(val active: List<HomeTimer>, val done: List<DoneTimer>)

/**
 * The house's timers plus the ones that just fired (web HomeHooks.useTimers).
 * The server deletes a timer the moment it fires, so this is the only place
 * that remembers it for the minute of "done · kitchen". A timer that vanishes
 * well before its time was cancelled, not fired, and leaves no line; so does
 * one this phone cancelled ([cancelled]). One instance per Home, main thread.
 */
internal class TimerBook {
    private var prev: Map<Long, HomeTimer> = emptyMap()
    private val fired = LinkedHashMap<Long, DoneTimer>()

    /** Ids this phone asked to cancel: never "done" when they vanish. */
    val cancelled: MutableSet<Long> = mutableSetOf()

    /** A new read arrived: anything gone from it that was due within 2 s fired. */
    fun observe(list: List<HomeTimer>, nowMs: Long) {
        val next = list.associateBy { it.id }
        for ((id, t) in prev) {
            if (id in next || id in cancelled) continue
            val at = isoMs(t.expires_at) ?: continue
            if (at <= nowMs + 2000) fired[id] = DoneTimer(t, at)
        }
        prev = next
    }

    /** Running timers (in the read's order, soonest first) and the done lines,
     *  newest first, each for [HOME_DONE_MS] after it fired. */
    fun view(list: List<HomeTimer>, nowMs: Long): TimerView {
        val active = mutableListOf<HomeTimer>()
        val done = LinkedHashMap<Long, DoneTimer>()
        for (t in list) {
            val at = isoMs(t.expires_at) ?: continue
            if (at > nowMs) active += t
            else if (t.id !in cancelled) done[t.id] = DoneTimer(t, at)
        }
        val it = fired.entries.iterator()
        while (it.hasNext()) {
            val (id, d) = it.next()
            if (nowMs - d.doneAtMs >= HOME_DONE_MS) it.remove()
            else if (id !in done) done[id] = d
        }
        val doneList = done.values
            .filter { nowMs - it.doneAtMs < HOME_DONE_MS }
            .sortedByDescending { it.doneAtMs }
        return TimerView(active, doneList)
    }
}

/** A fire as the timer it was, for the title rules ([timerTitle]). */
internal fun fireAsTimer(f: HomeFire): HomeTimer = HomeTimer(
    id = f.timer_id,
    expires_at = f.due_at,
    created_at = f.created_at,
    label = f.label,
    message = f.message,
    room_id = f.room_id,
    is_reminder = f.is_reminder || f.kind == "reminder",
)

/** Heard somewhere: ok. Still announcing: warn. Heard nowhere: err. */
internal fun fireTone(f: HomeFire): Tone = when {
    f.heard_in.isNotEmpty() -> Tone.Ok
    f.deliveries.any { it.outcome == "pending" || it.outcome == "sending" } -> Tone.Warn
    else -> Tone.Err
}

/** The done lines, from the fire ledger alone: every fire younger than
 *  [HOME_DONE_MS] on the server's clock ([nowMs]), newest first. A timer
 *  that merely vanished (cancelled anywhere) draws nothing. */
internal fun fireDoneLines(fires: List<HomeFire>, nowMs: Long): List<DoneTimer> =
    fires.mapNotNull { f -> isoMs(f.fired_at)?.let { f to it } }
        .filter { (_, at) -> nowMs - at < HOME_DONE_MS }
        .sortedByDescending { (_, at) -> at }
        .map { (f, at) -> DoneTimer(fireAsTimer(f), at, f.summary?.takeIf { it.isNotEmpty() }, fireTone(f)) }

/**
 * Home's timers: the running ones, and the done lines — from the server's
 * fire ledger when the read carries one (`fires` present), else from
 * [TimerBook]'s watching rows vanish, exactly as before the ledger existed.
 */
internal fun homeTimerView(list: HomeTimerList?, book: TimerBook, nowMs: Long): TimerView {
    val rows = list?.timers.orEmpty()
    val legacy = book.view(rows, nowMs)
    val fires = list?.fires ?: return legacy
    return TimerView(legacy.active, fireDoneLines(fires, nowMs))
}

/** Seconds to the soonest running timer in each room — the rooms' chips. */
internal fun timerLeftByRoom(active: List<HomeTimer>, nowMs: Long): Map<String, Long> {
    val out = LinkedHashMap<String, Long>()
    for (t in active) {
        val room = t.room_id ?: continue
        if (room !in out) out[room] = secondsLeft(t, nowMs)
    }
    return out
}

// ─── Rooms ────────────────────────────────────────────────────────────────

/** Playing, then paused, then online and quiet, then waiting, then offline. */
internal fun roomRank(r: HomeRoom): Int {
    val np = r.now_playing
    val hasSong = np?.song != null
    return when {
        r.online && hasSong && np?.state == "play" -> 0
        r.online && hasSong && np?.state == "pause" -> 1
        r.online -> 2
        r.status == "waiting" -> 3
        else -> 4
    }
}

internal fun sortRooms(rooms: List<HomeRoom>): List<HomeRoom> =
    rooms.sortedWith(compareBy<HomeRoom>({ roomRank(it) }, { it.room_id }))

internal data class RoomGroup(val label: String?, val rooms: List<HomeRoom>)

/** Rooms under their physical-room labels, labels A→Z, the unlabelled
 *  last as "ungrouped" — or one unlabelled group when nobody set a label. */
internal fun groupRooms(sorted: List<HomeRoom>): List<RoomGroup> {
    val labels = sorted.mapNotNull { it.room_label?.takeIf { l -> l.isNotEmpty() } }.distinct().sorted()
    if (labels.isEmpty()) return listOf(RoomGroup(null, sorted))
    val out = labels.map { l -> RoomGroup(l, sorted.filter { it.room_label == l }) }
    val loose = sorted.filter { it.room_label.isNullOrEmpty() }
    return if (loose.isEmpty()) out else out + RoomGroup("ungrouped", loose)
}

/** A Wi-Fi push carries every room's new rx/tx: merged in, never re-read
 *  (every room sends one a minute). Nothing else is taken from it. */
internal fun mergeWifi(rooms: List<HomeRoom>, push: Map<String, HomeWifi>?): List<HomeRoom> {
    if (push.isNullOrEmpty()) return rooms
    return rooms.map { r ->
        val w = push[r.room_id] ?: return@map r
        r.copy(wifi = (r.wifi ?: HomeWifi()).copy(rx_mbits = w.rx_mbits, tx_mbits = w.tx_mbits))
    }
}

/** Where a room's song is now: the read's elapsed_sec plus the time since
 *  the read while it plays. A stale read (the core is down) is where it was.
 *  Never past the song's end: between a song ending and the read that
 *  brings the next one, the count used to run on ("0:14 / 0:13"). */
internal fun roomElapsedSec(r: HomeRoom, stale: Boolean, sinceReadSec: Double): Double {
    val np = r.now_playing ?: return 0.0
    val song = np.song
    if (!r.online || song == null) return 0.0
    val playing = np.state == "play"
    val ran = (np.elapsed_sec ?: 0.0) + if (playing && !stale) sinceReadSec else 0.0
    val dur = song.duration_sec ?: 0.0
    return if (dur > 0) min(ran, dur) else ran
}

internal fun roomProgress(r: HomeRoom, elapsedSec: Double): Float {
    val dur = r.now_playing?.song?.duration_sec ?: 0.0
    return if (dur > 0) min(1.0, elapsedSec / dur).toFloat() else 0f
}

internal fun songTitle(song: HomeSong?): String =
    song?.title?.takeIf { it.isNotEmpty() }
        ?: song?.file?.substringAfterLast('/')?.takeIf { it.isNotEmpty() }
        ?: "unknown"

/** Weak Wi-Fi is the web wifiTone's 'err' band: under 5 Mbit/s. */
internal fun weakWifi(r: HomeRoom): Boolean {
    val rx = r.wifi?.rx_mbits ?: return false
    return r.online && rx < 5
}

internal fun kioskDead(r: HomeRoom): Boolean =
    r.online && r.sat_type == "video" && r.display?.kiosk_alive == false

// ─── Status line ──────────────────────────────────────────────────────────

/**
 * "3 online · 1 offline · 2 playing · 1 timer" — from the sections below,
 * no extra fetch. [rooms] is null until the rooms read has answered with
 * data; the timer counts come from the database, not that read, so they
 * count on their own.
 */
internal fun statusLine(rooms: List<HomeRoom>?, coreDown: Boolean, active: List<HomeTimer>): List<String> {
    val line = mutableListOf<String>()
    if (rooms != null) {
        if (rooms.isEmpty()) {
            line += "no rooms yet"
        } else {
            // With the core down these are what it said last, not what is true.
            if (coreDown) line += "last known"
            line += "${rooms.count { it.online }} online"
            val off = rooms.count { it.status == "offline" }
            val wait = rooms.count { it.status == "waiting" }
            if (off > 0) line += "$off offline"
            if (wait > 0) line += "$wait waiting"
            val playing = if (coreDown) 0 else rooms.count { roomRank(it) == 0 }
            if (playing > 0) line += "$playing playing"
        }
    }
    val nTimers = active.count { !it.is_reminder }
    val nReminders = active.size - nTimers
    if (nTimers > 0) line += plural(nTimers, "timer")
    if (nReminders > 0) line += plural(nReminders, "reminder")
    return line
}

// ─── Needs attention ──────────────────────────────────────────────────────

/** Where a problem row leads: an app screen, or — for what only the
 *  dashboard can show (the plugin list) — the dashboard in the browser. */
internal sealed interface HomeTarget {
    data class Screen(val route: Route) : HomeTarget
    data class Dashboard(val hash: String) : HomeTarget
}

/** Deep link into the connected server's dashboard ("<server>/#plugins");
 *  null with no server configured. */
internal fun dashboardUrl(serverUrl: String?, hash: String): String? {
    val base = serverUrl?.trim()?.trimEnd('/').orEmpty()
    if (base.isEmpty()) return null
    return "$base/$hash"
}

internal data class AttentionRow(
    val key: String,
    val tone: Tone,
    val text: String,
    val target: HomeTarget,
    /** When it happened (ISO), shown relative — "offline · 2h ago". */
    val at: String? = null,
)

private fun JsonElement?.truthy(): Boolean = when (this) {
    null, JsonNull -> false
    is JsonPrimitive -> if (isString) content.isNotEmpty() else content != "false" && content != "0"
    else -> true
}

private fun JsonElement?.nonEmptyList(): Boolean = (this as? JsonArray)?.isNotEmpty() == true

/**
 * Every problem this app can name, ranked err → warn (web HomeAttentionRows,
 * the 'open' rules). The app has no admin session, so the admin-only rows
 * (approvals, adoption, updates, disk) and the claim row are never built:
 * their reads are never made. When the core or the database is down that
 * is the only story — every rule that reads through them would just repeat
 * it, so they wait. Home never fixes anything itself; each row leads to the
 * page that does.
 */
internal fun attentionRows(
    health: HomeHealth?,
    rooms: List<HomeRoom>?,
    plugins: HomePlugins?,
    acq: HomeAcquisitions?,
): List<AttentionRow> {
    val rows = mutableListOf<AttentionRow>()
    val dbDown = health?.db_reachable == false
    val coreDown = health?.domovoi_reachable == false
    if (dbDown) {
        rows += AttentionRow("db", Tone.Err, "the database isn't answering · nothing new can be saved",
            HomeTarget.Screen(Route.Settings))
    }
    if (coreDown) {
        rows += AttentionRow("core", Tone.Err, "the Domovoi server isn't answering · rooms show the last known state",
            HomeTarget.Screen(Route.Satellites))
    }
    if (!dbDown && !coreDown) {
        when (health?.stt) {
            "unavailable" -> rows += AttentionRow("stt", Tone.Err,
                "speech recognition is off · the rooms can't understand anyone", HomeTarget.Screen(Route.Settings))
            "fallback" -> rows += AttentionRow("stt", Tone.Warn,
                "speech recognition is on its slower fallback model", HomeTarget.Screen(Route.Settings))
        }

        val list = rooms.orEmpty()
        val sats = HomeTarget.Screen(Route.Satellites)
        val offline = list.filter { it.status == "offline" }
        if (offline.size == 1) {
            rows += AttentionRow("offline", Tone.Warn,
                "${offline[0].room_id} is offline · it can't hear anyone right now", sats,
                at = offline[0].last_connected_at)
        } else if (offline.size > 1) {
            rows += AttentionRow("offline", Tone.Warn, "${offline.size} rooms offline", sats)
        }
        val waiting = list.filter { it.status == "waiting" }
        if (waiting.isNotEmpty()) {
            rows += AttentionRow("waiting", Tone.Warn,
                if (waiting.size == 1) "${waiting[0].room_id} was set up but hasn't connected yet"
                else "${waiting.size} rooms were set up but haven't connected yet", sats)
        }
        val dead = list.filter { kioskDead(it) }
        if (dead.isNotEmpty()) {
            rows += AttentionRow("kiosk", Tone.Warn,
                if (dead.size == 1) "the ${dead[0].room_id} screen stopped showing anything"
                else "${dead.size} screens stopped showing anything", sats)
        }

        // Plugins, as the server sees them (the web page adds errors only
        // its own browser saw; this app loads no plugin scripts).
        val bad = LinkedHashMap<String, Pair<String, Tone>>()
        plugins?.plugins.orEmpty().forEach { p ->
            if (p.status == "uninstalled") return@forEach
            val enabled = p.enabled != false
            val name = p.name?.takeIf { it.isNotEmpty() } ?: p.slug
            val broken = p.status == "load_error" ||
                (enabled && (p.web_load_error.truthy() || p.page_errors.nonEmptyList()))
            if (broken) bad[p.slug] = name to Tone.Err
            else if (enabled && p.status == "degraded") bad[p.slug] = name to Tone.Warn
        }
        val dash = HomeTarget.Dashboard("#plugins")
        if (bad.size == 1) {
            val (name, tone) = bad.values.first()
            rows += AttentionRow("plugins", tone,
                if (tone == Tone.Err) "the $name plugin failed to load" else "the $name plugin is degraded", dash)
        } else if (bad.size > 1) {
            rows += AttentionRow("plugins",
                if (bad.values.any { it.second == Tone.Err }) Tone.Err else Tone.Warn,
                "${bad.size} plugins have problems", dash)
        }

        // A media request is waiting and no provider plugin can fill it.
        // Never the request's own text.
        if (acq != null && acq.core_reachable) {
            val stuck = acq.acquisitions.count { a ->
                a.status == "pending" &&
                    (if (a.kind == "url") acq.can_fulfill_url == false else acq.can_fulfill_query == false)
            }
            if (stuck > 0) {
                rows += AttentionRow("acq", Tone.Warn,
                    if (stuck == 1) "a media request is waiting · no provider plugin can fill it"
                    else "$stuck media requests are waiting · no provider plugin can fill them",
                    HomeTarget.Screen(Route.Music))
            }
        }
    }
    // err before warn; otherwise the order they were found in (stable sort).
    return rows.sortedBy { if (it.tone == Tone.Err) 0 else 1 }
}

/** What the "needs attention" card shows. */
internal sealed interface AttentionView {
    data object None : AttentionView
    /** "something needs the admin's attention", with how many things. */
    data class Summary(val count: Int) : AttentionView
    data class Rows(val rows: List<AttentionRow>) : AttentionView
}

/**
 * Who sees which rows (HOME_PROBLEMS_VISIBILITY, admin-set; web
 * HomeAttentionView) — for a household member, which is all this app ever is:
 *   everyone  the rows they can notice
 *   summary   one neutral line
 *   admins    nothing
 * A shared screen shows the neutral line at most, whatever the setting.
 * Until the setting has answered ([settingKnown]) nothing shows, so an
 * "admins only" house never flashes its rows while /api/config loads.
 */
internal fun attentionView(
    rows: List<AttentionRow>,
    visibility: String?,
    shared: Boolean,
    settingKnown: Boolean,
): AttentionView {
    if (!settingKnown) return AttentionView.None
    val summary = if (rows.isEmpty()) AttentionView.None else AttentionView.Summary(rows.size)
    if (shared) return if (visibility != "admins") summary else AttentionView.None
    return when (visibility) {
        "admins" -> AttentionView.None
        "summary" -> summary
        else -> AttentionView.Rows(rows)
    }
}

// ─── Today ────────────────────────────────────────────────────────────────

internal fun eventStartMs(e: HomeEvent): Long = isoMs(e.starts_at) ?: Long.MAX_VALUE

/** An event with no end runs an hour. */
internal fun eventEndMs(e: HomeEvent): Long =
    isoMs(e.ends_at) ?: (eventStartMs(e).takeIf { it != Long.MAX_VALUE }?.plus(3_600_000) ?: Long.MAX_VALUE)

internal data class TodayRow(
    val event: HomeEvent,
    val startMs: Long,
    val endMs: Long?,
    /** "busy" on a shared screen. */
    val title: String,
    /** Never on a shared screen. */
    val location: String?,
    val running: Boolean,
)

internal data class TodayDay(val label: String, val rows: List<TodayRow>)

/**
 * Today's and tomorrow's events still to come or under way (at most
 * [limit]: [HOME_TODAY_ROWS], or [HOME_PHONE_ROWS] on a phone), under
 * "today" / "tomorrow". The cap is applied before the grouping, so a day
 * whose every row was cut leaves no bare label behind. Never the
 * description; a shared screen gets the times and "busy" only. Empty when
 * there is none — then [todayEmptyText] says so.
 */
internal fun todayDays(
    events: List<HomeEvent>,
    nowMs: Long,
    zone: ZoneId,
    shared: Boolean,
    limit: Int = HOME_TODAY_ROWS,
): List<TodayDay> {
    val d1 = dayStartMs(nowMs, zone, 1)
    val d2 = dayStartMs(nowMs, zone, 2)
    val shown = events.sortedBy { eventStartMs(it) }
        .filter { eventStartMs(it) < d2 && eventEndMs(it) >= nowMs }
        .take(limit)
    fun row(e: HomeEvent): TodayRow {
        val start = eventStartMs(e)
        return TodayRow(
            event = e,
            startMs = start,
            endMs = isoMs(e.ends_at),
            title = if (shared) "busy" else e.title,
            location = if (shared) null else e.location?.takeIf { it.isNotEmpty() },
            running = start <= nowMs && nowMs < eventEndMs(e),
        )
    }
    return listOf(
        TodayDay("today", shown.filter { eventStartMs(it) < d1 }.map(::row)),
        TodayDay("tomorrow", shown.filter { eventStartMs(it) >= d1 }.map(::row)),
    ).filter { it.rows.isNotEmpty() }
}

/** "nothing on today", "nothing more today · next: thu 1 oct 9:00am Dentist". */
internal fun todayEmptyText(events: List<HomeEvent>, nowMs: Long, zone: ZoneId, shared: Boolean): String {
    val d1 = dayStartMs(nowMs, zone, 1)
    val d2 = dayStartMs(nowMs, zone, 2)
    val sorted = events.sortedBy { eventStartMs(it) }
    val next = sorted.firstOrNull { eventStartMs(it) >= d2 && eventStartMs(it) != Long.MAX_VALUE }
    val earlier = sorted.any { eventStartMs(it) < d1 && eventEndMs(it) < nowMs }
    val lead = if (earlier) "nothing more today" else "nothing on today"
    if (next == null) return lead
    val at = eventStartMs(next)
    return "$lead · next: ${homeDate(at, zone)} ${homeClock(at, zone)} ${if (shared) "busy" else next.title}"
}

/** GET path for the events Home needs: from local midnight (an event
 *  already under way must still show — the server filters on starts_at)
 *  through the week, so an empty today can still name the next one. */
internal fun calendarPath(dayStartMs: Long, zone: ZoneId): String {
    val end = dayStartMs(dayStartMs, zone, 7)
    fun enc(ms: Long) = java.net.URLEncoder.encode(Instant.ofEpochMilli(ms).toString(), "UTF-8")
    return "/api/calendar/events?start=${enc(dayStartMs)}&end=${enc(end)}&limit=20"
}

// ─── First run ────────────────────────────────────────────────────────────

private val HOME_HINT_HANDLERS = listOf("timer", "reminder", "clock", "music")
internal const val HOME_HINT_FALLBACK = "set a timer for 10 minutes"

/** An example phrase from the user manual's feature table, the timer's first. */
internal fun hintPhrase(manual: HomeManual?): String {
    val handlers = manual?.handlers.orEmpty()
    for (name in HOME_HINT_HANDLERS) {
        handlers.firstOrNull { it.name == name }?.example_phrases?.firstOrNull()?.let { return it }
    }
    return handlers.firstOrNull { it.example_phrases.isNotEmpty() }?.example_phrases?.first() ?: HOME_HINT_FALLBACK
}

// ─── Everything ───────────────────────────────────────────────────────────

/** The "everything" grid: every screen that is not a bottom-bar tab, that
 *  this server's plugins allow, and — on a shared screen — that is not
 *  somebody's own. */
internal fun everythingTiles(caps: Capabilities, shared: Boolean): List<Route> =
    EverythingRoutes.filter { it.visibleOn(caps, shared) }

// ─── Stop all ─────────────────────────────────────────────────────────────

/**
 * "stop all" touches other people's rooms, so it always takes a second tap
 * within [HOME_STOP_ALL_ARM_MS]: the first arms it ("stop 3 rooms?"), the
 * second fires, and an armed button left alone disarms itself.
 */
internal data class StopAllArm(val armedAtMs: Long? = null) {
    fun isArmed(nowMs: Long): Boolean = armedAtMs != null && nowMs - armedAtMs < HOME_STOP_ALL_ARM_MS

    /** Returns the next state and whether this tap is the one that stops. */
    fun tap(nowMs: Long): Pair<StopAllArm, Boolean> =
        if (isArmed(nowMs)) StopAllArm() to true else StopAllArm(nowMs) to false
}
