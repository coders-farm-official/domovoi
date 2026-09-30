package com.domovoi.app.alerts

import android.content.Context
import android.util.Log
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.StateBus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * Timer and reminder alerts for the whole house, process-wide: not a
 * screen, not a foreground service. Created in AppContainer and started by
 * DomovoiApplication right after the live socket.
 *
 *  * The live path: `timer_fires.changed` on the existing /ws/state socket
 *    posts every fire newer than the last one this phone saw; on every
 *    (re)connect, and at start, GET /api/timers/fires?since_id catches up on
 *    what fired while it wasn't listening (30 minutes back at most).
 *  * The alarm mirror: `timers.changed` (debounced), each connect, start
 *    and a server switch re-read GET /api/timers and arm a local alarm for
 *    every running timer, so the phone rings when the socket is down.
 *
 * Only the ACTIVE server is watched and mirrored. With notifications off
 * for the app or its channel, nothing posts and nothing is armed; coming
 * back to the app re-checks.
 */
class TimerAlerts(
    context: Context,
    api: ApiClient,
    private val bus: StateBus,
    private val prefs: Prefs,
) {
    private val ctx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val store = AlertStore(ctx)
    private val notifier = TimerNotifier(ctx, prefs)

    val engine = AlertEngine(
        api = api,
        store = store,
        sink = notifier,
        alarms = AndroidAlarms(ctx),
        serverUrl = { prefs.serverUrl.value },
        log = { Log.d(TAG, it) },
    )

    /** Home's "Timer alerts are off on this phone" was answered "not now". */
    val hintDismissed: StateFlow<Boolean> = store.hintDismissed.stateIn(scope, SharingStarted.Eagerly, false)

    private var started = false
    private var syncJob: Job? = null

    @Volatile
    private var lastCanPost: Boolean? = null

    fun canPost(): Boolean = notifier.canPost()

    @Synchronized
    fun start() {
        if (started) return
        started = true
        lastCanPost = notifier.canPost()

        scope.launch {
            bus.events.collect { ev ->
                when (ev.type) {
                    FIRES_EVENT -> safely("timer fires push") { engine.onFiresEvent(ev.payload) }
                    TIMERS_EVENT -> requestSync(SYNC_DEBOUNCE_MS)
                }
            }
        }
        // Every false -> true of the live connection: catch up, re-mirror.
        scope.launch {
            var was = bus.connected.value
            bus.connected.collect { now ->
                if (now && !was) {
                    safely("timer catch-up") { engine.catchUp() }
                    requestSync(0)
                }
                was = now
            }
        }
        // A different server is a different house: its alarms, not the old one's.
        scope.launch {
            var last = prefs.serverUrl.value
            prefs.serverUrl.collect { url ->
                if (url != last) {
                    last = url
                    safely("timer mirror clear") { engine.clearMirror() }
                    requestSync(0)
                }
            }
        }
        scope.launch {
            safely("timer catch-up") { engine.catchUp() }
            safely("timer mirror sync") { engine.syncMirror() }
        }
    }

    /** MainActivity.onResume: notifications may have been turned on or off
     *  in the system settings meanwhile. */
    fun onAppResumed() {
        val now = notifier.canPost()
        if (now != lastCanPost) {
            lastCanPost = now
            requestSync(0)
        }
    }

    fun dismissHint() {
        scope.launch { safely("hint") { store.dismissHint() } }
    }

    @Synchronized
    private fun requestSync(delayMs: Long) {
        syncJob?.cancel()
        syncJob = scope.launch {
            if (delayMs > 0) delay(delayMs)
            safely("timer mirror sync") { engine.syncMirror() }
        }
    }

    private suspend fun safely(what: String, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "$what failed: ${e.message}")
        }
    }

    companion object {
        const val FIRES_EVENT = "timer_fires.changed"
        const val TIMERS_EVENT = "timers.changed"
        const val SYNC_DEBOUNCE_MS = 500L
        private const val TAG = "TimerAlerts"
    }
}
