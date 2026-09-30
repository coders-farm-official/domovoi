package com.domovoi.app.alerts

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.domovoi.app.DomovoiApplication
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** One server read the alarm path made: what it said (and when this phone
 *  had it), or that it could not be had — an IOException, a timeout, or any
 *  non-2xx (a 404 from an older server, a 503 without the fire ledger). */
sealed interface Lookup<out T> {
    data class Ok<T>(val value: T, val atMs: Long = 0L) : Lookup<T>
    data object Failed : Lookup<Nothing>
}

/** What a ringing alarm does. */
sealed interface ConfirmDecision {
    /** The server recorded the fire: post it, with where it was heard. */
    data class PostFire(val fire: TimerFire) : ConfirmDecision

    /** Still listed and due now: post, "going off now". */
    data class PostNow(val timer: AlertTimer) : ConfirmDecision

    /** Still listed but later (a clock moved): re-arm, post nothing. */
    data class Reschedule(val timer: AlertTimer, val triggerAtMs: Long) : ConfirmDecision

    /** Neither listed nor fired: cancelled while this phone wasn't
     *  listening. Post nothing. */
    data object Suppress : ConfirmDecision

    /** The server could not say: post from the mirror, "couldn't reach
     *  Domovoi to confirm". A timer cancelled while the phone was away
     *  still rings — better a stale ring than a missed one. */
    data object PostUnconfirmed : ConfirmDecision
}

/** Listed timers due within this of the server's clock are going off now. */
internal const val CONFIRM_DUE_SLACK_MS = 3_000L

/**
 * Decide what a ringing alarm for [timerId] does from the server's answers:
 * [fires] from GET /api/timers/fires?timer_id=…&limit=1, then (only when
 * that found nothing) [timers] from GET /api/timers — null when not asked.
 */
fun confirmDecision(
    timerId: Long,
    fires: Lookup<TimerFireList>,
    timers: Lookup<AlertTimerList>?,
): ConfirmDecision {
    if (fires !is Lookup.Ok) return ConfirmDecision.PostUnconfirmed
    fires.value.fires.firstOrNull { it.timer_id == timerId }?.let { return ConfirmDecision.PostFire(it) }
    if (timers !is Lookup.Ok) return ConfirmDecision.PostUnconfirmed
    val list = timers.value
    val t = list.timers.firstOrNull { it.id == timerId } ?: return ConfirmDecision.Suppress
    val at = isoMs(t.expires_at) ?: return ConfirmDecision.PostNow(t)
    val serverNow = isoMs(list.server_now) ?: timers.atMs
    return if (at <= serverNow + CONFIRM_DUE_SLACK_MS) {
        ConfirmDecision.PostNow(t)
    } else {
        ConfirmDecision.Reschedule(t, triggerAtMs(at, serverNow, timers.atMs))
    }
}

/**
 * A mirrored timer's alarm. Not exported: only AlarmManager, holding this
 * app's own immutable PendingIntent, can reach it. The intent carries the
 * timer id and the server key; the words come from the mirror.
 */
class TimerAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val timerId = intent.getLongExtra(EXTRA_TIMER_ID, -1L)
        if (timerId < 0) return
        val serverKey = intent.getStringExtra(EXTRA_SERVER_KEY)
        val app = context.applicationContext as? DomovoiApplication ?: return
        val pending = goAsync()
        receiverScope.launch {
            try {
                app.container.alerts.engine.onAlarm(timerId, serverKey)
                // A re-armed alarm (the server's clock moved) must not share
                // Doze's slot with the next background tick (API 26-30).
                app.container.alerts.sync.reguard()
            } catch (e: Exception) {
                Log.w(TAG, "timer alarm $timerId failed: ${e.message}")
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        const val TAG = "TimerAlarmReceiver"
    }
}

/** Where both receivers finish their work after goAsync(). */
internal val receiverScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
