package com.domovoi.app.alerts

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlin.math.abs

// ---------------------------------------------------------------------------
// The local alarm mirror: every running timer and reminder on the active
// server gets an AlarmManager alarm on this phone at the moment it is due,
// so the phone rings even when the app is not running and the live socket
// is down (the phone is off the home network, the process was killed, the
// device is dozing). TimerAlarmReceiver asks the server what really
// happened before it posts.
//
// The words to show live only here, in the app-private `alerts` DataStore.
// The alarm's PendingIntent carries ids and nothing else.
// ---------------------------------------------------------------------------

/** The mirror never arms more than this many alarms (the soonest). */
internal const val MIRROR_MAX = 50

/** A timer due within this of the server's clock is not worth an alarm:
 *  the server is about to fire it and the live path will say so. */
internal const val MIRROR_MIN_LEAD_MS = 1_000L

/** A trigger that moved by more than this is re-armed. */
internal const val MIRROR_MOVE_MS = 1_000L

/** One armed alarm, and what to say when it rings. */
@Serializable
data class MirrorAlarm(
    val timer_id: Long = 0,
    val kind: String? = null,
    val label: String? = null,
    val message: String? = null,
    val room_id: String? = null,
    val created_at: String? = null,
    val expires_at: String? = null,
    // When it rings, on THIS phone's clock (the server's time, corrected).
    val trigger_at_ms: Long = 0,
    val masked: Boolean = false,
)

/** The stored mirror: whose alarms these are, and the alarms. */
@Serializable
data class MirrorBook(
    val serverKey: String? = null,
    val alarms: List<MirrorAlarm> = emptyList(),
)

private val mirrorJson = Json { ignoreUnknownKeys = true; explicitNulls = false }

/** Never throws: a corrupt or absent blob is an empty mirror. */
internal fun decodeMirror(raw: String?): MirrorBook =
    runCatching { mirrorJson.decodeFromString(MirrorBook.serializer(), raw ?: "{}") }.getOrDefault(MirrorBook())

internal fun encodeMirror(book: MirrorBook): String = mirrorJson.encodeToString(MirrorBook.serializer(), book)

/**
 * When a timer due at [expiresAtMs] (server clock) rings on this phone: the
 * server's clock ran [serverNowMs] when this phone's read [receivedAtMs], so
 * a phone three minutes slow rings three minutes "early" by its own clock —
 * which is on time.
 */
fun triggerAtMs(expiresAtMs: Long, serverNowMs: Long, receivedAtMs: Long): Long =
    expiresAtMs - (serverNowMs - receivedAtMs)

/**
 * The alarms a fresh GET /api/timers asks for: both kinds (owner decision:
 * plain timers follow the reminders' rule), only timers still more than
 * [MIRROR_MIN_LEAD_MS] away on the server's clock, the [MIRROR_MAX] soonest.
 */
internal fun desiredAlarms(list: AlertTimerList, receivedAtMs: Long): List<MirrorAlarm> {
    val serverNow = isoMs(list.server_now) ?: receivedAtMs
    return list.timers
        .mapNotNull { t -> isoMs(t.expires_at)?.let { t to it } }
        .filter { (_, at) -> at > serverNow + MIRROR_MIN_LEAD_MS }
        .sortedBy { (_, at) -> at }
        .take(MIRROR_MAX)
        .map { (t, at) ->
            MirrorAlarm(
                timer_id = t.id,
                kind = kindOf(t.is_reminder),
                label = t.label,
                message = t.message,
                room_id = t.room_id,
                created_at = t.created_at,
                expires_at = t.expires_at,
                trigger_at_ms = triggerAtMs(at, serverNow, receivedAtMs),
                masked = t.masked,
            )
        }
}

/** What a sync changes: alarms to arm (new or moved) and ids to disarm. */
data class Reconcile(val toSchedule: List<MirrorAlarm>, val toCancel: List<Long>)

/**
 * From what should be armed ([desired]) and what is ([current]): disarm
 * every id no longer wanted — it fired, or somebody cancelled it from any
 * client — and arm every new id or one whose trigger moved by more than
 * [MIRROR_MOVE_MS].
 */
fun reconcile(desired: List<MirrorAlarm>, current: List<MirrorAlarm>): Reconcile {
    val want = desired.associateBy { it.timer_id }
    val have = current.associateBy { it.timer_id }
    val toCancel = have.keys.filter { it !in want }
    val toSchedule = desired.filter { d ->
        val c = have[d.timer_id]
        c == null || abs(c.trigger_at_ms - d.trigger_at_ms) > MIRROR_MOVE_MS
    }
    return Reconcile(toSchedule, toCancel)
}

/**
 * Exact or inexact, by API level. 26–30 always exact. 31–32 exact when the
 * SCHEDULE_EXACT_ALARM grant is in place (the user can revoke it), else
 * inexact — which Doze may hold for minutes. 33+ holds USE_EXACT_ALARM from
 * install, but the platform is still asked.
 */
fun useExact(sdkInt: Int, canScheduleExact: Boolean): Boolean =
    sdkInt < 31 /* S */ || canScheduleExact

/** Arms and disarms the mirror's alarms. The Android one is [AndroidAlarms]. */
interface AlarmSink {
    fun schedule(serverKey: String, alarm: MirrorAlarm)
    fun cancel(timerId: Long)
}

internal const val EXTRA_TIMER_ID = "com.domovoi.app.extra.TIMER_ID"
internal const val EXTRA_SERVER_KEY = "com.domovoi.app.extra.SERVER_KEY"

class AndroidAlarms(context: Context) : AlarmSink {
    private val ctx = context.applicationContext
    private val am = ctx.getSystemService(AlarmManager::class.java)

    private fun intent(timerId: Long, serverKey: String?): Intent =
        Intent(ctx, TimerAlarmReceiver::class.java).apply {
            putExtra(EXTRA_TIMER_ID, timerId)
            if (serverKey != null) putExtra(EXTRA_SERVER_KEY, serverKey)
        }

    private fun canExact(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) am.canScheduleExactAlarms() else true

    override fun schedule(serverKey: String, alarm: MirrorAlarm) {
        val pi = PendingIntent.getBroadcast(
            ctx, alarm.timer_id.toInt(), intent(alarm.timer_id, serverKey),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val exact = useExact(Build.VERSION.SDK_INT, canExact())
        try {
            if (exact) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.trigger_at_ms, pi)
            } else {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.trigger_at_ms, pi)
            }
        } catch (e: SecurityException) {
            // The exact-alarm grant went away between the check and the call.
            Log.i(TAG, "exact alarm refused, arming inexact: ${e.message}")
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.trigger_at_ms, pi)
        }
    }

    override fun cancel(timerId: Long) {
        // Extras are not part of a PendingIntent's identity; the request code
        // (the timer id) and the receiver are.
        val pi = PendingIntent.getBroadcast(
            ctx, timerId.toInt(), intent(timerId, null),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_NO_CREATE,
        ) ?: return
        am.cancel(pi)
        pi.cancel()
    }

    private companion object {
        const val TAG = "TimerAlarms"
    }
}
