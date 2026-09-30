package com.domovoi.app.alerts

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import com.domovoi.app.DomovoiApplication
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull

// ---------------------------------------------------------------------------
// The background sync. While the app is on screen the live socket hears
// every timer the moment it is set and every fire the moment it happens.
// Once the app leaves the screen Android freezes it and, from Android 15,
// blocks its network a few seconds later (`blocked=APP_BACKGROUND` in
// `dumpsys netpolicy`), so the socket hears nothing. This wakes the app
// about every 15 minutes to ask the server what changed, with the
// household token like every other request:
//
//   GET /api/timers/fires?since_id=…   post what went off meanwhile (the
//                                      30-minute catch-up rule, deduped by
//                                      fire id and by timer id with the
//                                      live path and the alarm mirror)
//   GET /api/timers                    arm a local alarm for every new
//                                      timer, disarm the cancelled ones
//
// A self-rescheduling EXACT allow-while-idle alarm, not WorkManager
// periodic work and not an inexact alarm, for these reasons on API 26-35:
//
//  * Doze. An exact allow-while-idle alarm fires while the phone dozes and
//    puts the app on the power allowlist for 10 s, which lifts Doze's
//    network block (and a receiver is exempt from Android 15's background
//    block while it runs). Measured on the API 35 emulator in forced Doze,
//    2026-09-30: the exact alarm's receiver read `allowed=POWER_SAVE_ALLOWLIST
//    |NOT_IN_BACKGROUND effective=NONE` in `dumpsys netpolicy` and reached
//    the server; an INEXACT setAndAllowWhileIdle tick fired on time but got
//    no allowlist (`allowed=NOT_IN_BACKGROUND effective=DOZE`) and its
//    requests timed out. A WorkManager job (JobScheduler underneath) waits
//    for a Doze maintenance window instead: about an hour after the phone
//    settles, then two, four, six hours apart.
//  * App standby buckets (API 28+) space jobs out for an app the user
//    seldom opens more than they do its alarms.
//  * Nothing new to ship: WorkManager would bring its own dependency, its
//    own database and its own start-up initializer. Its one advantage —
//    rescheduling itself after a reboot — the app already has for the
//    timer alarms (TimerBootReceiver, which now restarts this chain too).
//  * The app already holds exact alarms for the timers themselves
//    (USE_EXACT_ALARM from 33, SCHEDULE_EXACT_ALARM on 31-32, none needed
//    below), and the sync exists so those timers ring.
//
// Doze rations allow-while-idle alarms per app, and a tick must never hold
// back a timer's own alarm, so how the tick is armed depends on the API
// level ([chainMode]):
//
//  * API 31+ with exact alarms allowed: exact, every 15 minutes. The
//    timers' exact alarms share a budget of 72 an hour with it
//    (`allow_while_idle_quota` in `dumpsys alarm`); four ticks an hour
//    leave the timers the rest.
//  * API 26-30: every allow-while-idle alarm of an app shares ONE slot per
//    ~9 minutes while dozing (or in Battery Saver). Exact every 15 minutes
//    (no permission needed below 31), kept out of the 9 minutes either side
//    of every mirrored timer alarm ([clearOfAlarms]).
//  * API 31-32 with "Alarms & reminders" revoked: no exact alarms at all,
//    and an inexact allow-while-idle tick would get no network in Doze
//    while taking the (then inexact) timer alarms' slot. So the tick is a
//    plain inexact alarm (10 minutes, delivered up to 75% later): it runs
//    while the phone is awake and waits for Doze's maintenance windows,
//    when the network is open.
//
// No foreground service and no permanent notification.
//
// Each tick asks for the next one FIRST, so a sync that fails, times out
// or crashes never ends the chain. A force-stop cancels it with every other
// alarm; the next start of the app begins it again. A reboot or an app
// update clears alarms too: TimerBootReceiver re-arms the mirror and asks
// for a tick within a couple of minutes.
// ---------------------------------------------------------------------------

/** A plain (inexact) tick is asked for this long ahead; it comes 10 to
 *  17.5 minutes later (see above). */
internal const val SYNC_LEAD_MS = 10 * 60_000L

/** An exact tick is asked for this long ahead. */
internal const val SYNC_EXACT_LEAD_MS = 15 * 60_000L

/** ...and this soon after a reboot or an app update: the start's own sync
 *  usually runs before Wi-Fi is up, and anything could have been set or
 *  cancelled while the phone was off. */
internal const val SYNC_AFTER_BOOT_MS = 2 * 60_000L

/** How long one tick may spend: Doze lets an allow-while-idle alarm's app
 *  use the network for 10 s. */
internal const val SYNC_BUDGET_MS = 8_000L

/** A sync that reached the server this recently makes a tick's own sync
 *  redundant. The usual case: the tick's alarm started the process, and the
 *  start ran a full sync a moment earlier. */
internal const val SYNC_FRESH_MS = 60_000L

/** While dozing (or in Battery Saver) API 26-30 fires at most one
 *  allow-while-idle alarm per app in this long, exact or not. */
internal const val IDLE_ALARM_GAP_MS = 9 * 60_000L

/** Why the chain is being (re)armed; decides how soon the next tick is. */
enum class SyncReason { START, TICK, BOOT, SERVER }

/** How the tick's alarm is armed on this phone (see the top of this file). */
enum class ChainMode {
    /** API 26-30: exact, allowed while idle, kept clear of the timer alarms. */
    EXACT_GUARDED,

    /** API 31+ with exact alarms allowed: exact, allowed while idle. */
    EXACT,

    /** API 31+ without exact alarms (31-32 with the grant revoked):
     *  inexact, not while idle. */
    PLAIN,
}

/** [canExact]: exact alarms are allowed (useExact: always below 31, the
 *  exact-alarm grant from 31) — the same test the timer alarms make. */
fun chainMode(sdkInt: Int, canExact: Boolean): ChainMode = when {
    sdkInt < 31 /* S */ -> ChainMode.EXACT_GUARDED
    canExact -> ChainMode.EXACT
    else -> ChainMode.PLAIN
}

/** How long from now the next tick is asked for. */
internal fun syncLeadMs(reason: SyncReason, mode: ChainMode): Long = when {
    reason == SyncReason.BOOT -> SYNC_AFTER_BOOT_MS
    mode == ChainMode.PLAIN -> SYNC_LEAD_MS
    else -> SYNC_EXACT_LEAD_MS
}

/**
 * The first time at or after [atMs] that is at least [gapMs] from every
 * alarm in [alarmsMs] (all wall-clock ms). A tick in the gap BEFORE an
 * alarm would hold that alarm back in Doze; one in the gap AFTER it is held
 * back itself to the gap's end — so either way it goes to the gap's end,
 * and the alarms after that are checked from there.
 */
internal fun clearOfAlarms(atMs: Long, alarmsMs: List<Long>, gapMs: Long = IDLE_ALARM_GAP_MS): Long {
    var at = atMs
    for (a in alarmsMs.sorted()) {
        if (at > a - gapMs && at < a + gapMs) at = a + gapMs
    }
    return at
}

/** What a sync does, in order. [AlertEngine] in the app. */
interface SyncWork {
    /** Re-arm every alarm the stored mirror holds that is still ahead. */
    suspend fun rearm()

    /** Post what fired since the last fire seen. True when the server answered. */
    suspend fun catchUp(): Boolean

    /** Arm new timers, disarm gone ones. True when the mirror is in line
     *  with the server (or, with notifications off, disarmed). */
    suspend fun syncMirror(): Boolean
}

/** The chain's one alarm. The Android one is [AndroidSyncAlarm]. */
interface SyncAlarm {
    /** How a tick is armed on this phone right now ([chainMode]). */
    fun mode(): ChainMode

    /** Replace any pending tick with one at [atElapsedMs] (elapsed realtime). */
    fun schedule(atElapsedMs: Long, mode: ChainMode)

    fun cancel()
}

/** What one tick did (logged; pinned by the tests). */
enum class TickResult {
    /** Caught up and re-mirrored. */
    SYNCED,

    /** A sync reached the server under [SYNC_FRESH_MS] ago; nothing asked. */
    FRESH,

    /** The server did not answer (off the home network, down, refused). */
    UNREACHABLE,

    /** Ran out of [SYNC_BUDGET_MS]. */
    TIMED_OUT,

    /** Notifications are off: the mirror was disarmed, nothing asked. */
    NOTIFICATIONS_OFF,

    /** No server configured: the chain stopped. */
    NO_SERVER,
}

class TimerSync(
    private val work: SyncWork,
    private val alarm: SyncAlarm,
    private val hasServer: () -> Boolean,
    private val canPost: () -> Boolean,
    /** When the mirrored timer alarms ring (wall-clock ms). */
    private val mirrorTimes: suspend () -> List<Long>,
    private val wall: () -> Long,
    private val elapsed: () -> Long,
    private val budgetMs: Long = SYNC_BUDGET_MS,
    private val log: (String) -> Unit = {},
) {
    /** One sync at a time: a tick that arrives while the start's sync runs
     *  waits for it, then finds it fresh. */
    private val lock = Mutex()

    /** One change to the planned tick at a time. */
    private val armLock = Mutex()

    /** When a sync last reached the server (elapsed realtime), this process. */
    @Volatile
    private var lastSyncAt: Long? = null

    /** When the pending tick is due (wall clock), as this process armed it. */
    private var plannedAt: Long? = null

    /** A boot or app update armed a tick in this process: the start's
     *  later one would come later, so it is skipped. */
    private var bootArmed = false

    private suspend fun place(targetMs: Long, mode: ChainMode) {
        val at = if (mode == ChainMode.EXACT_GUARDED) clearOfAlarms(targetMs, mirrorTimes()) else targetMs
        alarm.schedule(elapsed() + (at - wall()), mode)
        plannedAt = at
    }

    /** Ask for the next tick, or stop the chain when there is no server to ask. */
    suspend fun arm(reason: SyncReason) {
        armLock.withLock {
            try {
                if (!hasServer()) {
                    alarm.cancel()
                    plannedAt = null
                    return@withLock
                }
                if (reason == SyncReason.BOOT) bootArmed = true
                else if (reason == SyncReason.START && bootArmed) return@withLock
                val mode = alarm.mode()
                place(wall() + syncLeadMs(reason, mode), mode)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log("background sync not armed: ${e.message}")
            }
        }
    }

    /**
     * The mirror changed: on API 26-30, move the pending tick out of the way
     * of a timer alarm armed since (see [clearOfAlarms]). Elsewhere, and
     * with no tick pending, nothing to do.
     */
    suspend fun reguard() {
        armLock.withLock {
            try {
                val planned = plannedAt ?: return@withLock
                val mode = alarm.mode()
                if (mode != ChainMode.EXACT_GUARDED || planned <= wall()) return@withLock
                if (clearOfAlarms(planned, mirrorTimes()) != planned) place(planned, mode)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log("background sync not re-armed: ${e.message}")
            }
        }
    }

    private suspend fun step(what: String, block: suspend () -> Boolean): Boolean =
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("background sync: $what failed: ${e.message}")
            false
        }

    private suspend fun sync(rearmFirst: Boolean, skipIfFresh: Boolean): TickResult = lock.withLock {
        val last = lastSyncAt
        if (skipIfFresh && last != null && elapsed() - last < SYNC_FRESH_MS) return@withLock TickResult.FRESH
        if (rearmFirst) step("re-arm") { work.rearm(); true }
        // Each step runs even when the one before failed: a catch-up the
        // server refused says nothing about whether it lists the timers.
        val caught = step("catch-up") { work.catchUp() }
        val mirrored = step("mirror sync") { work.syncMirror() }
        if (caught && mirrored) {
            lastSyncAt = elapsed()
            TickResult.SYNCED
        } else {
            TickResult.UNREACHABLE
        }
    }

    /**
     * A cold start (TimerAlerts.start): keep the chain going — the first
     * start after an install, a force-stop (which cancels every alarm) or a
     * data wipe begins it — then re-arm what the stored mirror holds (a
     * force-stop left the mirror listing alarms it cancelled, and the mirror
     * sync only arms timers that are new or moved), catch up and re-mirror.
     */
    suspend fun onStart(): TickResult {
        arm(SyncReason.START)
        val result = sync(rearmFirst = true, skipIfFresh = false)
        reguard()
        return result
    }

    /** The chain's alarm rang. */
    suspend fun onTick(): TickResult {
        arm(SyncReason.TICK)
        if (!hasServer()) return TickResult.NO_SERVER
        if (!canPost()) {
            // Nothing may ring while notifications are off. The mirror sync
            // disarms without asking the server; the chain goes on, so the
            // alarms come back within a tick of notifications coming back.
            step("mirror sync") { work.syncMirror() }
            return TickResult.NOTIFICATIONS_OFF
        }
        val result = withTimeoutOrNull(budgetMs) { sync(rearmFirst = false, skipIfFresh = true) } ?: TickResult.TIMED_OUT
        reguard()
        return result
    }

    /**
     * BOOT_COMPLETED or MY_PACKAGE_REPLACED: every alarm the app had is
     * gone. Ask for a tick soon, and re-arm the mirror's alarms still ahead
     * (no network needed; past ones are dropped and the next catch-up posts
     * what really fired).
     */
    suspend fun onBoot() {
        arm(SyncReason.BOOT)
        lock.withLock { step("re-arm") { work.rearm(); true } }
        reguard()
    }
}

internal const val ACTION_TIMER_SYNC = "com.domovoi.app.action.TIMER_SYNC"

/** The request code of the chain's PendingIntent. The timer alarms use
 *  their timer ids on another receiver, so the two never collide. */
private const val SYNC_REQUEST_CODE = 0

class AndroidSyncAlarm(context: Context) : SyncAlarm {
    private val ctx = context.applicationContext
    private val am = ctx.getSystemService(AlarmManager::class.java)

    private fun pending(flag: Int): PendingIntent? = PendingIntent.getBroadcast(
        ctx, SYNC_REQUEST_CODE,
        Intent(ctx, TimerSyncReceiver::class.java).setAction(ACTION_TIMER_SYNC),
        PendingIntent.FLAG_IMMUTABLE or flag,
    )

    /** The same test the timer alarms make (AndroidAlarms, useExact). */
    override fun mode(): ChainMode = chainMode(
        Build.VERSION.SDK_INT,
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms(),
    )

    override fun schedule(atElapsedMs: Long, mode: ChainMode) {
        val pi = pending(PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        // Elapsed realtime, so a clock change neither skips nor bunches ticks.
        val type = AlarmManager.ELAPSED_REALTIME_WAKEUP
        try {
            when (mode) {
                ChainMode.EXACT_GUARDED, ChainMode.EXACT -> am.setExactAndAllowWhileIdle(type, atElapsedMs, pi)
                ChainMode.PLAIN -> am.set(type, atElapsedMs, pi)
            }
        } catch (e: SecurityException) {
            // The exact-alarm grant went away between the check and the call.
            Log.i("TimerSync", "exact tick refused (${e.message}); arming a plain one")
            am.set(type, atElapsedMs, pi)
        }
    }

    override fun cancel() {
        val pi = pending(PendingIntent.FLAG_NO_CREATE) ?: return
        am.cancel(pi)
        pi.cancel()
    }
}

/**
 * The chain's tick. Not exported: only AlarmManager, holding this app's own
 * immutable PendingIntent, can reach it.
 */
class TimerSyncReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_TIMER_SYNC) return
        val app = context.applicationContext as? DomovoiApplication ?: return
        val pending = goAsync()
        receiverScope.launch {
            try {
                val result = app.container.alerts.sync.onTick()
                Log.i(TAG, "background sync: ${result.name.lowercase()}")
            } catch (e: Exception) {
                Log.w(TAG, "background sync failed: ${e.message}")
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        const val TAG = "TimerSync"
    }
}
