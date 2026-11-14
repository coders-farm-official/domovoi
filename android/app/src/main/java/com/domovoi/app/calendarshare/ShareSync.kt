package com.domovoi.app.calendarshare

import com.domovoi.app.net.ApiException
import kotlinx.serialization.json.JsonObject
import java.io.IOException

/**
 * One pass of calendar sharing for one server: send the picked calendars
 * (`PUT /api/calendar/phones/{device_id}`) or delete the house's copy
 * (`DELETE`), and record what happened in the [store] for the settings
 * line. No Android classes: the phone's calendars and the two calls come
 * in as functions, so the JVM tests drive it whole.
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

    suspend fun run(server: String): Outcome {
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
        val onPhone = calendars()
        if (onPhone == null) {
            // The permission was taken back in the phone's settings. The
            // house keeps what it had until this is sorted: said, not silent.
            store.update(server) { ShareRules.failed(it ?: ShareEntry(), PERMISSION_GONE, t) }
            return Outcome.Done
        }
        val entry = store.get(server) ?: return Outcome.Done
        val ids = entry.calendarIds.toSet().intersect(onPhone.map { it.id }.toSet())
        val window = SharePayload.window(t)
        val rows = instances(window, ids)
        if (rows == null) {
            store.update(server) { ShareRules.failed(it ?: ShareEntry(), PERMISSION_GONE, t) }
            return Outcome.Done
        }
        val built = SharePayload.buildWithinSize(phoneName(), window, onPhone, rows, ids, t)
        return call(server, t) {
            put(server, built.body)
            store.update(server) { ShareRules.sent(it ?: ShareEntry(), now(), built.droppedEvents) }
        }
    }

    private suspend fun remove(server: String): Outcome {
        val t = now()
        return try {
            delete(server)
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
        block()
        Outcome.Done
    } catch (e: ApiException) {
        store.update(server) { ShareRules.failed(it ?: ShareEntry(), ShareRules.errorText(e.status), t, e.retryAfterSec) }
        if (retries(e.status)) Outcome.Retry else Outcome.Done
    } catch (e: IOException) {
        store.update(server) { ShareRules.failed(it ?: ShareEntry(), ShareRules.errorText(null), t) }
        Outcome.Retry
    }

    companion object {
        const val PERMISSION_GONE =
            "calendar permission is off: allow Calendars for Domovoi in the phone's settings, or turn this off"

        /** Worth trying again by itself: the server busy, asked to wait, or
         *  out of reach. A refusal (401/403/404/413/422) waits for the next
         *  change or the periodic pass instead. */
        fun retries(status: Int): Boolean = status == 408 || status == 429 || status >= 500
    }
}
