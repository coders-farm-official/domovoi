package com.domovoi.app.alerts

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonElement

// ---------------------------------------------------------------------------
// The container guard's logic on the phone, with Android at arm's length
// (the shape of AlertEngine): what arrives (a `containers.changed` push, a
// read at start, on reconnect, on coming back to the app, or from the
// background sync's tick), what the shell and the Containers screen show,
// and which incidents get a notification. IncidentAlerts wires it to the
// live socket and the notification shade; the JVM tests wire it to a fake
// sink and a MockWebServer.
//
// Notifications are for a phone that can read the household list: a phone
// refused it (not paired) falls back to the open summary, which feeds the
// banner only and never posts.
// ---------------------------------------------------------------------------

/** Where incident notifications go. The Android one is [IncidentNotifier]. */
interface IncidentSink {
    /** Notifications are on for this app and the incidents' channel. */
    fun canPost(): Boolean

    /** This install is a shared screen for the active server. */
    fun shared(): Boolean

    fun post(serverKey: String, content: IncidentContent)

    /** Take back the notification posted for [incidentId], if it is still
     *  in the shade (it resolved, or an admin cleared it). */
    fun withdraw(serverKey: String, incidentId: Long)
}

/** The web backend's reads, as paths (the tests pin them). */
object IncidentsApi {
    const val INCIDENTS_PATH = "/api/containers/incidents"
    const val SUMMARY_PATH = "/api/containers/summary"
    const val EVENT = "containers.changed"
}

/** A household read this recent makes a background tick's own redundant. */
internal const val INCIDENT_FRESH_MS = 60_000L

/** What one background tick did (logged; pinned by the tests). */
enum class IncidentTick {
    /** Read the household list (and posted what was new). */
    CHECKED,

    /** A household read (or push) landed under [INCIDENT_FRESH_MS] ago. */
    FRESH,

    /** No household list: refused, down, or an older server. */
    UNREACHABLE,

    /** Not on Wi-Fi or Ethernet: nothing asked (TimerSync's rule). */
    OFF_LAN,

    /** Notifications are off: nothing would post, so nothing asked. */
    NOTIFICATIONS_OFF,

    /** No server configured. */
    NO_SERVER,

    /** Ran out of the tick's budget. */
    TIMED_OUT,
}

/** The `container_incidents` DataStore: one file, app-private (the app
 *  opts out of backups and device transfers). */
private val Context.incidentsDataStore by preferencesDataStore(name = "container_incidents")

/**
 * What the phone remembers between runs: `notified`, {serverKey: the
 * incidents it has posted ([incidentMark]), newest last}, so a restart of
 * the app, a reconnect or a re-read never posts one twice.
 */
class IncidentStore(private val ds: DataStore<Preferences>) {
    constructor(context: Context) : this(context.applicationContext.incidentsDataStore)

    private val kNotified = stringPreferencesKey("notified")
    private val kWithdrawn = stringPreferencesKey("withdrawn")

    suspend fun notified(serverKey: String): List<String> =
        decodeNotified(ds.data.first()[kNotified])[serverKey].orEmpty()

    suspend fun setNotified(serverKey: String, book: List<String>) {
        ds.edit { p -> p[kNotified] = encodeNotified(decodeNotified(p[kNotified]) + (serverKey to book)) }
    }

    /** The posted incidents whose notification was taken back (planWithdrawals). */
    suspend fun withdrawn(serverKey: String): List<String> =
        decodeNotified(ds.data.first()[kWithdrawn])[serverKey].orEmpty()

    suspend fun setWithdrawn(serverKey: String, marks: List<String>) {
        ds.edit { p -> p[kWithdrawn] = encodeNotified(decodeNotified(p[kWithdrawn]) + (serverKey to marks)) }
    }
}

/** How a read went: the server's body, a non-2xx answer, or no answer. */
private sealed interface Read<out T> {
    data class Ok<T>(val value: T) : Read<T>
    data class Answered(val status: Int) : Read<Nothing>
    data object Failed : Read<Nothing>
}

class IncidentEngine(
    private val api: ApiClient,
    private val store: IncidentStore,
    private val sink: IncidentSink,
    private val serverUrl: () -> String,
    /** A monotonic clock (elapsed realtime in the app). */
    private val elapsed: () -> Long = { System.nanoTime() / 1_000_000L },
    private val budgetMs: Long = SYNC_BUDGET_MS,
    private val log: (String) -> Unit = {},
) {
    private val _state = MutableStateFlow(IncidentState())

    /** What the shell's banner and the Containers screen show. */
    val state: StateFlow<IncidentState> = _state

    /** One payload at a time: a push and a read landing together must not
     *  both find an incident unposted and post it twice. */
    private val applyLock = Mutex()

    /** When the household list last arrived (read or pushed), [elapsed]. */
    @Volatile
    private var lastHouseholdAt: Long? = null

    private fun activeKey(): String? = serverUrl().takeIf { it.isNotBlank() }?.let { serverKey(it) }

    /** The active server changed (or went): what is shown was the old one's. */
    fun serverChanged() {
        _state.value = IncidentState(serverKey = activeKey())
        lastHouseholdAt = null
    }

    /** A `containers.changed` push: the household list, as the read gives it. */
    suspend fun onEvent(payload: JsonElement?) {
        val parsed = parseIncidents(payload) ?: return
        val key = activeKey() ?: return
        apply(key, parsed)
    }

    private suspend fun apply(key: String, payload: IncidentsPayload) {
        applyLock.withLock {
            if (activeKey() != key) return
            _state.value = IncidentState(key, IncidentTier.HOUSEHOLD, loaded = true, payload = payload)
            lastHouseholdAt = elapsed()
            notify(key, payload.incidents)
        }
    }

    /**
     * Withdraw what [planWithdrawals] says — whether or not notifications
     * are on (taking one back needs no permission) — then post what
     * [planNotifications] says. With notifications off nothing posts and
     * nothing is remembered: an incident still open when they come back on
     * posts then (the next read: coming back to the app re-reads).
     */
    private suspend fun notify(key: String, incidents: List<ContainerIncident>) {
        val gone = planWithdrawals(incidents, store.notified(key), store.withdrawn(key))
        if (gone.withdraw.isNotEmpty()) {
            store.setWithdrawn(key, gone.withdrawn)
            gone.withdraw.forEach { sink.withdraw(key, it) }
        }
        if (!sink.canPost()) return
        val plan = planNotifications(incidents, store.notified(key))
        if (plan.post.isEmpty()) return
        // Remembered first: a crash in between loses a post rather than
        // doubling one.
        store.setNotified(key, plan.book)
        val shared = sink.shared()
        plan.post.forEach { sink.post(key, incidentContent(it, shared)) }
    }

    private suspend fun <T> read(block: suspend () -> T): Read<T> = try {
        Read.Ok(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: ApiException) {
        log("container incidents read answered ${e.status}")
        Read.Answered(e.status)
    } catch (e: Exception) {
        log("container incidents read failed: ${e.message}")
        Read.Failed
    }

    /**
     * Read the household list; on any failure, the open summary for the
     * banner alone. True when the household list was read (and applied).
     *
     *  * A phone refused the list (401/403: not paired, or its token gone)
     *    shows the summary. A paired phone whose read merely failed keeps
     *    the list it has rather than drop to the guest's line.
     *  * Neither read existing (an older server) is "nothing to show".
     *  * No answer at all keeps whatever this server last showed.
     */
    suspend fun refresh(): Boolean {
        val key = activeKey()
        if (key == null) {
            _state.value = IncidentState()
            return false
        }
        // Untracked: neither read may raise or clear the pairing screen
        // (ApiClient.getUntracked).
        val household = read { parseIncidents(api.getUntracked(IncidentsApi.INCIDENTS_PATH)) }
        if (activeKey() != key) return false
        if (household is Read.Ok && household.value != null) {
            apply(key, household.value)
            return true
        }
        val refused = household is Read.Answered && (household.status == 401 || household.status == 403)
        val summary = read { parseSummary(api.getUntracked(IncidentsApi.SUMMARY_PATH)) }
        applyLock.withLock {
            if (activeKey() != key) return false
            val current = _state.value.takeIf { it.serverKey == key }
            val keepList = current?.tier == IncidentTier.HOUSEHOLD && !refused
            when {
                keepList -> Unit
                summary is Read.Ok && summary.value != null ->
                    _state.value = IncidentState(key, IncidentTier.OPEN, loaded = true, summary = summary.value)
                summary is Read.Ok || summary is Read.Answered ->
                    _state.value = IncidentState(key, IncidentTier.NONE, loaded = true)
                current == null -> _state.value = IncidentState(serverKey = key)
                else -> Unit
            }
        }
        return false
    }

    /**
     * The background sync's tick (TimerSyncReceiver): read the list so an
     * incident that opened while the app was off screen still gets its
     * notification. Same rules as the timers' tick: only on Wi-Fi or
     * Ethernet (the read carries the household token), not while
     * notifications are off, not when a read just landed, within the tick's
     * budget. Never throws.
     */
    suspend fun backgroundTick(onLan: Boolean): IncidentTick {
        return try {
            when {
                activeKey() == null -> IncidentTick.NO_SERVER
                !sink.canPost() -> IncidentTick.NOTIFICATIONS_OFF
                !onLan -> IncidentTick.OFF_LAN
                lastHouseholdAt?.let { elapsed() - it < INCIDENT_FRESH_MS } == true -> IncidentTick.FRESH
                else -> withTimeoutOrNull(budgetMs) {
                    if (refresh()) IncidentTick.CHECKED else IncidentTick.UNREACHABLE
                } ?: IncidentTick.TIMED_OUT
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("container incidents tick failed: ${e.message}")
            IncidentTick.UNREACHABLE
        }
    }
}
