package com.domovoi.app.alerts

import android.content.Context
import android.util.Log
import com.domovoi.app.data.Prefs
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.StateBus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * Voice help's notifications, process-wide like [IncidentAlerts]: created
 * in AppContainer, started by DomovoiApplication beside it.
 *
 *  * The live path: `help_deliveries.changed` on the existing /ws/state
 *    socket carries the last hour's deliveries.
 *  * Catch-up reads (GET /api/help/deliveries): at start, on every
 *    false -> true of the live connection, on a server switch, when this
 *    phone's owner changes and on coming back to the app.
 *
 * Only the ACTIVE server is watched.
 */
class HelpAlerts(
    context: Context,
    api: ApiClient,
    private val bus: StateBus,
    private val prefs: Prefs,
) {
    private val ctx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val engine = HelpDeliveryEngine(
        api = api,
        book = HelpSeenStore(ctx),
        sink = HelpNotifier(ctx, prefs),
        serverUrl = { prefs.serverUrl.value },
        owner = { ServerCredentials.ownerFor(prefs.owners.value, prefs.serverUrl.value) },
        log = { Log.d(TAG, it) },
    )

    private val requests = Channel<Unit>(Channel.CONFLATED)
    private var started = false

    @Synchronized
    fun start() {
        if (started) return
        started = true
        scope.launch {
            while (true) {
                requests.receive()
                safely("help deliveries read") { engine.refresh() }
            }
        }
        scope.launch {
            bus.events.collect { ev ->
                if (ev.type == HelpDeliveriesApi.EVENT) safely("help deliveries push") { engine.onEvent(ev.payload) }
            }
        }
        scope.launch {
            var was = bus.connected.value
            bus.connected.collect { now ->
                if (now && !was) refresh()
                was = now
            }
        }
        // A server switch, or a new "This phone belongs to": read again.
        scope.launch {
            prefs.serverUrl.map { url -> url to ServerCredentials.ownerFor(prefs.owners.value, url) }
                .distinctUntilChanged().drop(1).collect { refresh() }
        }
        scope.launch {
            prefs.owners.map { ServerCredentials.ownerFor(it, prefs.serverUrl.value) }
                .distinctUntilChanged().drop(1).collect { refresh() }
        }
        refresh()
    }

    fun refresh() {
        requests.trySend(Unit)
    }

    /** MainActivity.onResume: something may have been sent off screen. */
    fun onAppResumed() = refresh()

    private suspend fun safely(what: String, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "$what failed: ${e.message}")
        }
    }

    private companion object {
        const val TAG = "HelpAlerts"
    }
}
