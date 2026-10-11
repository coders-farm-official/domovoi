package com.domovoi.app.alerts

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.StateBus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * The container guard on the phone, process-wide like [TimerAlerts]:
 * created in AppContainer, started by DomovoiApplication right after the
 * timer alerts.
 *
 *  * The live path: `containers.changed` on the existing /ws/state socket
 *    carries the household list; each one updates the banner and posts
 *    what newly needs an admin.
 *  * Re-reads: at start, on every false -> true of the live connection, on
 *    a server switch, when the household token changes, on coming back to
 *    the app (MainActivity.onResume), and every 30 s while the app is on
 *    screen with the socket down (the shell). A phone refused the list
 *    shows the open summary's banner and posts nothing.
 *  * The background sync's tick (TimerSyncReceiver) reads the list too, so
 *    an incident that opens while the app is off screen still notifies.
 *
 * Only the ACTIVE server is watched. [state] is what the shell's banner and
 * the Containers screen read.
 */
class IncidentAlerts(
    context: Context,
    api: ApiClient,
    private val bus: StateBus,
    private val prefs: Prefs,
) {
    private val ctx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val notifier = IncidentNotifier(ctx, prefs)

    val engine = IncidentEngine(
        api = api,
        store = IncidentStore(ctx),
        sink = notifier,
        serverUrl = { prefs.serverUrl.value },
        elapsed = SystemClock::elapsedRealtime,
        log = { Log.d(TAG, it) },
    )

    val state: StateFlow<IncidentState> get() = engine.state

    /** Asks for a re-read; asks made while one runs fold into one more. */
    private val requests = Channel<Unit>(Channel.CONFLATED)

    private var started = false

    @Synchronized
    fun start() {
        if (started) return
        started = true

        scope.launch {
            while (true) {
                requests.receive()
                safely("container incidents read") { engine.refresh() }
            }
        }
        scope.launch {
            bus.events.collect { ev ->
                if (ev.type == IncidentsApi.EVENT) safely("container incidents push") { engine.onEvent(ev.payload) }
            }
        }
        scope.launch {
            var was = bus.connected.value
            bus.connected.collect { now ->
                if (now && !was) refresh()
                was = now
            }
        }
        // A different server is a different house: its incidents, not the old one's.
        scope.launch {
            var last = prefs.serverUrl.value
            prefs.serverUrl.collect { url ->
                if (url != last) {
                    last = url
                    engine.serverChanged()
                    refresh()
                }
            }
        }
        // Paired (or unpaired) just now: the list may be readable, or not.
        scope.launch {
            var last = prefs.deviceToken.value
            prefs.deviceToken.collect { token ->
                if (token != last) {
                    last = token
                    refresh()
                }
            }
        }
        refresh()
    }

    /** Re-read the list (or the summary) soon. */
    fun refresh() {
        requests.trySend(Unit)
    }

    /** MainActivity.onResume: anything may have changed off screen, and
     *  notifications may have been turned on (what is still open posts). */
    fun onAppResumed() = refresh()

    /** The background sync's tick (TimerSyncReceiver). Never throws. */
    suspend fun onBackgroundTick(): IncidentTick =
        engine.backgroundTick(onLan = runCatching { Discovery.onLan(ctx) }.getOrDefault(false))

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
        /** How often the shell re-reads while the socket is down. */
        const val POLL_WHILE_DOWN_MS = 30_000L
        private const val TAG = "IncidentAlerts"
    }
}
