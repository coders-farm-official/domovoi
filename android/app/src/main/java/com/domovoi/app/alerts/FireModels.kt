package com.domovoi.app.alerts

import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.ui.components.parseInstant
import kotlinx.serialization.Serializable
import java.security.MessageDigest
import kotlin.math.roundToLong

// ---------------------------------------------------------------------------
// Timer and reminder alerts — the wire models and every pure rule the two
// paths (the live socket and the local alarm mirror) share. Kept free of
// Android so the JVM tests can hold them to the contract.
//
// A fire is a timer or reminder that went off, as the web backend's ledger
// (migration V018) records it. Every field is nullable with a default: the
// backend evolves, and an older server sends none of this at all.
// ---------------------------------------------------------------------------

/** Where a fire was (or is being) announced, and how that went. */
@Serializable
data class TimerFireDelivery(
    val room_id: String? = null,
    val is_origin: Boolean = false,
    // pending | sending | spoken | interrupted | failed | offline | busy_timeout | cancelled
    val outcome: String? = null,
    val detail: String? = null,
    val finished_at: String? = null,
)

/** One timer or reminder that went off (GET /api/timers/fires, the
 *  `timer_fires.changed` push, and `fires` on GET /api/timers). */
@Serializable
data class TimerFire(
    val id: Long = 0,
    val timer_id: Long = 0,
    val kind: String? = null,
    val is_reminder: Boolean = false,
    val label: String? = null,
    val message: String? = null,
    // True when the server withheld a reminder's words from this caller.
    val masked: Boolean = false,
    // The ORIGIN room; null = set with no room (the app, dashboard chat).
    val room_id: String? = null,
    val created_at: String? = null,
    val due_at: String? = null,
    val fired_at: String? = null,
    val settled_at: String? = null,
    val acked_at: String? = null,
    val acked_by: String? = null,
    val heard_in: List<String> = emptyList(),
    // Server-computed ("heard in garage · still announcing"); no speech in it.
    val summary: String? = null,
    val deliveries: List<TimerFireDelivery> = emptyList(),
) {
    /** The contract's kind rule: a reminder iff it has a message; the
     *  server sends both spellings of that answer. */
    val reminder: Boolean get() = is_reminder || kind == "reminder"
}

/** GET /api/timers/fires. [window_sec] says how far back the answer
 *  reaches: null for the whole history (the household token's view), 600
 *  for the open view a caller without a valid token gets (rule F1). */
@Serializable
data class TimerFireList(
    val server_now: String? = null,
    val fires: List<TimerFire> = emptyList(),
    val window_sec: Int? = null,
)

/** One row of GET /api/timers, as the alarm mirror needs it. */
@Serializable
data class AlertTimer(
    val id: Long = 0,
    val expires_at: String? = null,
    val created_at: String? = null,
    val label: String? = null,
    val message: String? = null,
    val room_id: String? = null,
    val is_reminder: Boolean = false,
    val masked: Boolean = false,
)

/** GET /api/timers: every running timer, the server's clock, and (from a
 *  server with the fire ledger) the recent fires — null on an older one. */
@Serializable
data class AlertTimerList(
    val server_now: String? = null,
    val timers: List<AlertTimer> = emptyList(),
    val fires: List<TimerFire>? = null,
)

// ─── Words ────────────────────────────────────────────────────────────────

internal fun isoMs(iso: String?): Long? = parseInstant(iso)?.toEpochMilli()

/** "Reminder · garage", "Timer done · garage", "… · no room". All a shared
 *  screen ever shows, and all a lock screen shows when the phone is set to
 *  hide sensitive notification content (TimerNotifier). */
fun fireTitle(kind: String?, roomId: String?): String {
    val room = roomId?.takeIf { it.isNotEmpty() } ?: "no room"
    return if (kind == "reminder") "Reminder · $room" else "Timer done · $room"
}

/** "10 min timer", "45s timer" — what an unlabelled timer is called, from
 *  how long it ran (Home's timerTitle rule); "timer" when that's unknown. */
fun timerNounFromSpan(createdAt: String?, dueAt: String?): String {
    val at = isoMs(dueAt)
    val created = isoMs(createdAt)
    val total = if (at != null && created != null) (at - created) / 1000.0 else Double.NaN
    if (!(total > 0)) return "timer"
    return if (total < 90) "${total.roundToLong()}s timer" else "${(total / 60).roundToLong()} min timer"
}

/**
 * The notification's text line. A reminder's words are household speech:
 * never on a shared screen, and absent when the server masked them (then
 * there is nothing to say beyond the title). A timer's label ("pasta") is
 * low-risk and always shows, as on Home.
 */
internal fun alertBody(
    reminder: Boolean,
    label: String?,
    message: String?,
    masked: Boolean,
    createdAt: String?,
    dueAt: String?,
    shared: Boolean,
): String? {
    if (reminder) {
        if (shared || masked) return null
        return message?.takeIf { it.isNotEmpty() } ?: "reminder"
    }
    return label?.takeIf { it.isNotEmpty() } ?: timerNounFromSpan(createdAt, dueAt)
}

fun fireBody(fire: TimerFire, shared: Boolean): String? =
    alertBody(fire.reminder, fire.label, fire.message, fire.masked, fire.created_at, fire.due_at, shared)

internal fun kindOf(reminder: Boolean): String = if (reminder) "reminder" else "timer"

// ─── What gets posted ─────────────────────────────────────────────────────

/** The alarm path's second line when the server could not be asked... */
const val SUB_UNCONFIRMED = "couldn't reach Domovoi to confirm"

/** ...and when it could, and the timer was still counting its last seconds. */
const val SUB_GOING_OFF = "going off now"

/**
 * One notification, before Android draws it. [publicTitle] is the lock
 * screen's version (kind and room, nothing else). On a shared screen the
 * private version IS the public one, so [text] and [subText] are null.
 */
data class AlertContent(
    val timerId: Long,
    val title: String,
    val text: String?,
    val subText: String?,
    val whenMs: Long?,
) {
    val publicTitle: String get() = title
}

internal fun fireContent(fire: TimerFire, shared: Boolean, serverOffsetMs: Long): AlertContent =
    AlertContent(
        timerId = fire.timer_id,
        title = fireTitle(kindOf(fire.reminder), fire.room_id),
        text = if (shared) null else fireBody(fire, false),
        subText = if (shared) null else fire.summary?.takeIf { it.isNotEmpty() },
        whenMs = isoMs(fire.fired_at)?.minus(serverOffsetMs),
    )

internal fun alarmContent(alarm: MirrorAlarm, shared: Boolean, subText: String): AlertContent =
    AlertContent(
        timerId = alarm.timer_id,
        title = fireTitle(alarm.kind, alarm.room_id),
        text = if (shared) null else alertBody(
            alarm.kind == "reminder", alarm.label, alarm.message, alarm.masked,
            alarm.created_at, alarm.expires_at, false,
        ),
        subText = if (shared) null else subText,
        whenMs = alarm.trigger_at_ms,
    )

// ─── Which server ─────────────────────────────────────────────────────────

/**
 * A short, stable name for a server — the first 8 hex digits of SHA-256 of
 * its normalised URL. It tags notifications and keys the dedupe book, the
 * seen ids and the mirror, so two households never share one.
 */
fun serverKey(url: String): String {
    val digest = MessageDigest.getInstance("SHA-256")
        .digest(ServerCredentials.normalize(url).toByteArray(Charsets.UTF_8))
    return digest.joinToString("") { "%02x".format(it) }.take(8)
}

/** The notification tag both paths post under (the id is the timer id). */
fun alertTag(serverKey: String): String = "timer_fire:$serverKey"
