package com.domovoi.app.calendarshare

import com.domovoi.app.net.ApiException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import java.io.IOException

/**
 * One pass of calendar sharing for one server: send the picked calendars
 * (`PUT /api/calendar/phones/{device_id}`) or delete the house's copy
 * (`DELETE`), and record what happened in the [store] for the settings
 * line. No Android classes: the phone's calendars and the two calls come
 * in as functions, so the JVM tests drive it whole.
 *
 * Every pass, and [withdraw], runs under one lock for the whole process
 * ([LOCK]), and a call once started is finished even if the job running it
 * is cancelled: a send cancelled half-way could otherwise reach the server
 * AFTER the delete that followed it, and leave the copy there for good.
 * After a send the stored state is read again, and if sharing was turned
 * off meanwhile the delete goes at once.
 */
class ShareSync(
    private val store: ShareStore,
    /** The phone's calendars; null when READ_CALENDAR is not held. */
    private val calendars: () -> List<PhoneCalendar>?,
    private val instances: (ShareWindow, Set<Long>) -> List<InstanceRow>?,
    private val put: suspend (server: String, body: JsonObject) -> Unit,
    private val delete: suspend (server: String) -> Unit,
    private val phoneName: () -> String?,
    private val now: () -> Long = System::currentTimeMillis,
) {
    enum class Outcome { Done, Retry }

    suspend fun run(server: String): Outcome = LOCK.withLock { step(server) }

    private suspend fun step(server: String): Outcome {
        val entry = store.get(server) ?: return Outcome.Done
        val action = ShareRules.action(entry)
        if (action == ShareAction.Nothing) return Outcome.Done
        if (ShareRules.waitMs(entry, now()) > 0) return Outcome.Retry
        return when (action) {
            ShareAction.Put -> send(server)
            ShareAction.Delete -> remove(server)
            ShareAction.Nothing -> Outcome.Done
        }
    }

    private suspend fun send(server: String): Outcome {
        val t = now()
        // A provider that failed (IOException) is tried again later and
        // changes nothing; no permission is as good as turned off.
        val onPhone = try { calendars() } catch (e: IOException) { return Outcome.Retry }
            ?: return permissionLost(server)
        // A ticked calendar the phone no longer shows (its account removed,
        // the ids changed by a re-sync, or hidden in the calendar app) is
        // unticked and named, not silently dropped from the send.
        val before = store.get(server) ?: return Outcome.Done
        val present = onPhone.map { it.id }.toSet()
        val gone = before.calendarIds.filter { it !in present }.toSet()
        val names = onPhone.filter { it.id in before.calendarIds }.associate { it.id to it.name }
        val entry = store.update(server) {
            val e = it ?: ShareEntry()
            ShareRules.vanished(e, gone).let { v -> v.copy(calendarNames = v.calendarNames + names) }
        }
        // Everything ticked is gone: as when the last calendar is unticked,
        // the send still goes if the house has something from this phone.
        if (ShareRules.action(entry) != ShareAction.Put) return Outcome.Done
        val ids = entry.calendarIds.toSet()
        val window = SharePayload.window(t)
        val rows = try { instances(window, ids) } catch (e: IOException) { return Outcome.Retry }
            ?: return permissionLost(server)
        val built = SharePayload.buildWithinSize(phoneName(), window, onPhone, rows, ids, t)
        val hash = SharePayload.hash(built.body)
        if (ShareRules.unchanged(entry, hash, t)) return afterSend(server)
        val out = call(server, t) {
            put(server, built.body)
            store.update(server) { ShareRules.sent(it ?: ShareEntry(), now(), built.droppedEvents, hash) }
        }
        return if (out == Outcome.Done) afterSend(server) else out
    }

    /** Read the state again after a send: turned off while it was on its
     *  way, the delete goes now (this pass holds the lock, so nothing else
     *  can send it in between). */
    private suspend fun afterSend(server: String): Outcome =
        if (ShareRules.action(store.get(server)) == ShareAction.Delete) remove(server) else Outcome.Done

    /** The permission was taken back in the phone's settings: sharing is
     *  as good as off, so the house's copy goes, and the panel says why. */
    private suspend fun permissionLost(server: String): Outcome {
        store.update(server) { ShareRules.permissionLost(it) }
        return remove(server)
    }

    private suspend fun remove(server: String): Outcome {
        val t = now()
        return try {
            withContext(NonCancellable) { delete(server) }
            store.update(server) { ShareRules.deleted(it ?: ShareEntry()) }
            Outcome.Done
        } catch (e: ApiException) {
            if (e.status == 404) {
                // A server with no such route never had a copy to keep.
                store.update(server) { ShareRules.deleted(it ?: ShareEntry()) }
                Outcome.Done
            } else {
                store.update(server) { ShareRules.failed(it ?: ShareEntry(), ShareRules.errorText(e.status), t, e.retryAfterSec) }
                Outcome.Retry
            }
        } catch (e: IOException) {
            store.update(server) { ShareRules.failed(it ?: ShareEntry(), ShareRules.errorText(null), t) }
            Outcome.Retry
        }
    }

    private suspend fun call(server: String, t: Long, block: suspend () -> Unit): Outcome = try {
        withContext(NonCancellable) { block() }
        Outcome.Done
    } catch (e: ApiException) {
        store.update(server) { ShareRules.failed(it ?: ShareEntry(), ShareRules.errorText(e.status), t, e.retryAfterSec) }
        if (retries(e.status)) Outcome.Retry else Outcome.Done
    } catch (e: IOException) {
        store.update(server) { ShareRules.failed(it ?: ShareEntry(), ShareRules.errorText(null), t) }
        Outcome.Retry
    }

    /**
     * The phone is forgetting [server] (or unpairing from it): delete the
     * house's copy while the token still works, then forget the entry
     * whatever happened. True when nothing is left behind (deleted, or
     * there was nothing to delete); false when the house still holds this
     * phone's calendars, which the forget flow then says. At most
     * [timeoutMs], so forgetting never hangs on an unreachable house.
     */
    suspend fun withdraw(server: String, timeoutMs: Long = WITHDRAW_TIMEOUT_MS): Boolean {
        val ok = withTimeoutOrNull(timeoutMs) {
            LOCK.withLock {
                val e = store.get(server)
                if (!ShareRules.mayHoldCopy(e)) return@withLock true
                try {
                    withContext(NonCancellable) { delete(server) }
                    true
                } catch (x: ApiException) {
                    x.status == 404
                } catch (x: IOException) {
                    false
                }
            }
        } ?: false
        store.forget(server)
        return ok
    }

    companion object {
        /** One pass (or withdraw) at a time in this process. */
        internal val LOCK = Mutex()

        const val WITHDRAW_TIMEOUT_MS = 10_000L

        /** Worth trying again by itself: the server busy, asked to wait, or
         *  out of reach. A refusal (401/403/404/413/422) waits for the next
         *  change or the periodic pass instead. */
        fun retries(status: Int): Boolean = status == 408 || status == 429 || status >= 500
    }
}
