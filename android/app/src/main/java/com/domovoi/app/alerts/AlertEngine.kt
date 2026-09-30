package com.domovoi.app.alerts

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonElement

// ---------------------------------------------------------------------------
// The alerts' logic, with Android at arm's length: what arrives (a push, a
// catch-up read, a ringing alarm), what that means for the phone, and what
// gets posted, armed or forgotten. TimerAlerts wires it to the live socket,
// the notification shade and AlarmManager; the JVM tests wire it to fakes
// and a MockWebServer.
//
// Two paths can post the same timer: the live socket (a fire the server
// recorded) and the local alarm (a mirrored timer's due time). Both go
// through AlertStore.markAlerted, keyed by server and timer id, so whichever
// comes first posts and the other stays quiet — except that the live path
// may refresh a notification still on screen with the server's summary.
// ---------------------------------------------------------------------------

/** Where posts go. The Android one is [TimerNotifier]. */
interface AlertSink {
    /** Notifications are on for this app and its channel. While false,
     *  nothing posts and the mirror arms nothing. */
    fun canPost(): Boolean

    /** This install is a shared screen for the active server. */
    fun shared(): Boolean

    fun post(serverKey: String, content: AlertContent, silent: Boolean)

    /** The notification for [timerId] is still showing (not dismissed). */
    fun isActive(serverKey: String, timerId: Long): Boolean
}

/** The web backend's fire reads, as paths (the tests pin them). */
object AlertsApi {
    const val FIRES_PAGE = 50

    /** Newest first with no [sinceId] (a first run); ascending after it. */
    fun firesSincePath(sinceId: Long?, limit: Int = FIRES_PAGE): String =
        if (sinceId == null) "/api/timers/fires?limit=$limit"
        else "/api/timers/fires?since_id=$sinceId&limit=$limit"

    fun fireForTimerPath(timerId: Long): String = "/api/timers/fires?timer_id=$timerId&limit=1"

    /** The newest fire the server has: a catch-up that found nothing past
     *  the remembered id checks the server is not BEHIND it. */
    const val NEWEST_FIRE_PATH = "/api/timers/fires?limit=1"

    const val TIMERS_PATH = "/api/timers"

    suspend fun fires(api: ApiClient, path: String): TimerFireList = api.get(path).decode()

    suspend fun timers(api: ApiClient): AlertTimerList = api.get(TIMERS_PATH).decode()
}

/** How long a ringing alarm may spend asking the server what happened. */
internal const val CONFIRM_BUDGET_MS = 2_000L

/** A catch-up reads at most this many pages of [AlertsApi.FIRES_PAGE]. */
internal const val CATCH_UP_PAGES = 5

class AlertEngine(
    private val api: ApiClient,
    private val store: AlertStore,
    private val sink: AlertSink,
    private val alarms: AlarmSink,
    private val serverUrl: () -> String,
    private val clock: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = {},
) : SyncWork {
    /** One batch of fires at a time: a push and a catch-up must not both
     *  read the same seen id and post the same fire. */
    private val fireLock = Mutex()

    /** One mirror change at a time (sync, clear, re-arm, a ringing alarm). */
    private val mirrorLock = Mutex()

    /** What each notification last said, so a push that changes nothing
     *  re-posts nothing. */
    private val lastPosted = HashMap<String, AlertContent>()

    /** The server's clock minus this phone's, from the last read that said. */
    @Volatile
    var serverOffsetMs: Long = 0L
        private set

    private fun activeKey(): String? = serverUrl().takeIf { it.isNotBlank() }?.let { serverKey(it) }

    private fun noteClock(serverNow: String?, receivedAtMs: Long) {
        isoMs(serverNow)?.let { serverOffsetMs = it - receivedAtMs }
    }

    private suspend fun postOnce(key: String, content: AlertContent): Boolean {
        if (!store.markAlerted(key, content.timerId, clock())) return false
        sink.post(key, content, silent = false)
        synchronized(lastPosted) { lastPosted["$key|${content.timerId}"] = content }
        return true
    }

    /** Refresh a notification still on screen; never re-post a dismissed one. */
    private fun refreshIfShowing(key: String, content: AlertContent) {
        val slot = "$key|${content.timerId}"
        val same = synchronized(lastPosted) { lastPosted[slot] == content }
        if (same || !sink.isActive(key, content.timerId)) return
        sink.post(key, content, silent = true)
        synchronized(lastPosted) { lastPosted[slot] = content }
    }

    private suspend fun process(key: String, fires: List<TimerFire>, serverNowMs: Long) {
        var seen = store.seen(key)
        if (historyRestarted(fires, seen, isoMs(store.seenAt(key)))) {
            // A fire at or below the remembered id went off after the
            // remembered one: the server's ids started again.
            log("timer fire history started again on this server; starting over")
            store.startOver(key)
            seen = null
        }
        val plan = planFires(fires, seen, serverNowMs, CATCH_UP_WINDOW_MS)
        if (sink.canPost()) {
            val shared = sink.shared()
            val posted = HashSet<Long>()
            for (f in plan.post) {
                if (postOnce(key, fireContent(f, shared, serverOffsetMs))) posted += f.id
            }
            // A later word on a fire already posted (the alarm path's, or an
            // earlier push's) updates the line under it: "heard in garage".
            for (f in fires) {
                if (f.id !in posted && store.wasAlerted(key, f.timer_id)) {
                    refreshIfShowing(key, fireContent(f, shared, serverOffsetMs))
                }
            }
        }
        if (plan.seen != seen || plan.seenAt != null) store.setSeen(key, plan.seen, plan.seenAt)
    }

    /** A `timer_fires.changed` push: the fires of the last hour. */
    suspend fun onFiresEvent(payload: JsonElement?) {
        if (payload == null) return
        val fires = runCatching {
            DomovoiJson.decodeFromJsonElement(ListSerializer(TimerFire.serializer()), payload)
        }.getOrNull() ?: return
        val key = activeKey() ?: return
        fireLock.withLock { process(key, fires, clock() + serverOffsetMs) }
    }

    /**
     * On connect and at start: whatever fired while this phone wasn't
     * listening. A 404 (an older server) or 503 (no fire ledger yet) means
     * there is nothing to catch up from. True when the server answered.
     */
    override suspend fun catchUp(): Boolean {
        val key = activeKey() ?: return false
        return fireLock.withLock catchUp@{
            var seen = store.seen(key)
            repeat(CATCH_UP_PAGES) { page ->
                val received = clock()
                val list = try {
                    AlertsApi.fires(api, AlertsApi.firesSincePath(seen))
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    log("timer catch-up skipped: ${e.message}")
                    return@catchUp false
                }
                if (activeKey() != key) return@catchUp false
                noteClock(list.server_now, received)
                val remembered = seen
                if (remembered != null && page == 0 && list.fires.isEmpty()) {
                    // Nothing past the remembered fire. If the server's
                    // newest is BEHIND it, this phone remembers another
                    // history (a rebuilt database on the same address):
                    // read the newest page as a first run instead of
                    // skipping every new fire up to the old id.
                    val top = try {
                        AlertsApi.fires(api, AlertsApi.NEWEST_FIRE_PATH)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        log("timer catch-up check skipped: ${e.message}")
                        return@catchUp false
                    }
                    if (activeKey() != key) return@catchUp false
                    if (!historyBehind(top.fires.firstOrNull(), remembered, windowed = top.window_sec != null)) {
                        return@catchUp true
                    }
                    log("timer fire history is behind this phone's; starting over")
                    store.startOver(key)
                    seen = null
                    return@repeat
                }
                process(key, list.fires, isoMs(list.server_now) ?: (received + serverOffsetMs))
                val next = store.seen(key)
                if (seen == null || list.fires.size < AlertsApi.FIRES_PAGE || next == seen) return@catchUp true
                seen = next
            }
            true
        }
    }

    /**
     * Bring the alarm mirror in line with a fresh GET /api/timers: arm new
     * and moved timers, disarm the ones that fired or were cancelled. Kept as
     * it is when the server can't be asked. With notifications off, nothing
     * stays armed. True when the mirror is in line with the server (or,
     * with notifications off, disarmed); false when the server couldn't say.
     */
    override suspend fun syncMirror(): Boolean {
        return mirrorLock.withLock syncMirror@{
            val key = activeKey()
            val stored = store.mirror()
            if (key == null || !sink.canPost()) {
                stored.alarms.forEach { alarms.cancel(it.timer_id) }
                if (stored.alarms.isNotEmpty() || stored.serverKey != key) store.setMirror(MirrorBook(key))
                return@syncMirror key != null
            }
            val received = clock()
            val list = try {
                AlertsApi.timers(api)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log("timer mirror sync skipped: ${e.message}")
                return@syncMirror false
            }
            if (activeKey() != key) return@syncMirror false
            noteClock(list.server_now, received)
            val desired = desiredAlarms(list, received)
            val current = if (stored.serverKey == key) {
                stored.alarms
            } else {
                // Another server's alarms: none of them is this server's.
                stored.alarms.forEach { alarms.cancel(it.timer_id) }
                emptyList()
            }
            val r = reconcile(desired, current)
            r.toCancel.forEach { alarms.cancel(it) }
            r.toSchedule.forEach { alarms.schedule(key, it) }
            store.setMirror(MirrorBook(key, desired))
            true
        }
    }

    /** The server changed: every alarm armed for the old one goes. */
    suspend fun clearMirror() {
        mirrorLock.withLock {
            store.mirror().alarms.forEach { alarms.cancel(it.timer_id) }
            store.setMirror(MirrorBook())
        }
    }

    /** After a reboot or an app update: re-arm what is still ahead. Past
     *  ones are dropped; the next connect's catch-up posts the real fires. */
    override suspend fun rearm() {
        mirrorLock.withLock {
            val book = store.mirror()
            val key = book.serverKey ?: return
            if (key != activeKey() || !sink.canPost()) return
            val now = clock()
            val ahead = book.alarms.filter { it.trigger_at_ms > now }
            ahead.forEach { alarms.schedule(key, it) }
            if (ahead.size != book.alarms.size) store.setMirror(book.copy(alarms = ahead))
        }
    }

    private suspend fun <T> lookup(read: suspend () -> T): Lookup<T> {
        val at = clock()
        return try {
            Lookup.Ok(read(), at)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("timer confirm read failed: ${e.message}")
            Lookup.Failed
        }
    }

    /** Ask the server what became of [timerId] (see [confirmDecision]). The
     *  fire is read again before a cancel is concluded: the timer may have
     *  gone off between the two reads, and the ledger row lands in the same
     *  transaction that removes the timer. */
    private suspend fun confirm(timerId: Long): ConfirmDecision {
        val fires = lookup { AlertsApi.fires(api, AlertsApi.fireForTimerPath(timerId)) }
        if (fires !is Lookup.Ok || fires.value.fires.any { it.timer_id == timerId }) {
            return confirmDecision(timerId, fires, null)
        }
        val timers = lookup { AlertsApi.timers(api) }
        if (timers !is Lookup.Ok || timers.value.timers.any { it.id == timerId }) {
            return confirmDecision(timerId, fires, timers)
        }
        val again = lookup { AlertsApi.fires(api, AlertsApi.fireForTimerPath(timerId)) }
        return confirmDecision(timerId, again, timers)
    }

    /** A mirrored alarm rang (TimerAlarmReceiver). */
    suspend fun onAlarm(timerId: Long, alarmServerKey: String?) {
        val key = activeKey() ?: return
        if (alarmServerKey != null && alarmServerKey != key) {
            log("timer alarm for another server ignored")
            return
        }
        val row = mirrorLock.withLock {
            store.mirror().takeIf { it.serverKey == key }?.alarms?.firstOrNull { it.timer_id == timerId }
        }
        if (!sink.canPost()) return
        if (store.wasAlerted(key, timerId)) {
            // The live path already said so.
            forget(key, timerId)
            return
        }
        val decision = withTimeoutOrNull(CONFIRM_BUDGET_MS) { confirm(timerId) } ?: ConfirmDecision.PostUnconfirmed
        val shared = sink.shared()
        when (decision) {
            is ConfirmDecision.PostFire -> postOnce(key, fireContent(decision.fire, shared, serverOffsetMs))
            // The server's row is the fresher word on what it is called.
            is ConfirmDecision.PostNow -> postOnce(
                key,
                alarmContent(alarmFrom(decision.timer, row?.trigger_at_ms ?: clock()), shared, SUB_GOING_OFF),
            )
            is ConfirmDecision.Reschedule -> {
                val next = (row ?: alarmFrom(decision.timer, decision.triggerAtMs))
                    .copy(trigger_at_ms = decision.triggerAtMs)
                mirrorLock.withLock {
                    val book = store.mirror()
                    if (book.serverKey == key) {
                        alarms.schedule(key, next)
                        store.setMirror(book.copy(alarms = book.alarms.filter { it.timer_id != timerId } + next))
                    }
                }
                return
            }
            ConfirmDecision.Suppress -> log("timer $timerId was cancelled while this phone wasn't listening; not posting")
            ConfirmDecision.PostUnconfirmed ->
                if (row != null) postOnce(key, alarmContent(row, shared, SUB_UNCONFIRMED))
                else log("timer $timerId rang with no mirrored row and no server; not posting")
        }
        forget(key, timerId)
    }

    private suspend fun forget(key: String, timerId: Long) {
        mirrorLock.withLock {
            val book = store.mirror()
            if (book.serverKey == key && book.alarms.any { it.timer_id == timerId }) {
                store.setMirror(book.copy(alarms = book.alarms.filter { it.timer_id != timerId }))
            }
        }
    }
}

private fun alarmFrom(t: AlertTimer, triggerAtMs: Long): MirrorAlarm = MirrorAlarm(
    timer_id = t.id,
    kind = kindOf(t.is_reminder),
    label = t.label,
    message = t.message,
    room_id = t.room_id,
    created_at = t.created_at,
    expires_at = t.expires_at,
    trigger_at_ms = triggerAtMs,
    masked = t.masked,
)
