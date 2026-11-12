package com.domovoi.app.listens

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.IOException

/**
 * Sends the outbox ([ListenOutbox]) to `POST /api/music/listens`, oldest
 * first, in batches of up to 200, for the ACTIVE server only (a row waits
 * for its own server). What each answer does to the batch:
 *
 *  * 2xx: the batch's rows are deleted (exactly their ids), and the next
 *    batch goes;
 *  * 401 / 403 / 408 / 429, any 5xx, no network, or the server changed
 *    under the call: kept, tried again at the next flush;
 *  * any other 4xx (a 422 above all): dropped — it would fail forever.
 *
 * `recording: false` in an answer means the household keeps no listening
 * history: [onRecordingOff] hears it (the app stops logging until it
 * starts again). Retrying is safe: the server takes a row it already has
 * (same device, song and start) once.
 */
class ListenSender(
    private val outbox: ListenOutbox,
    private val api: ApiClient,
    /** The active server (normalised), "" for none. */
    private val server: () -> String,
    /** Whether anything may be sent to it now (paired, not room mode). */
    private val canSend: (server: String) -> Boolean = { true },
    private val onRecordingOff: () -> Unit = {},
) {
    private val lock = Mutex()

    enum class Result { Sent, Nothing, Kept }

    suspend fun flush(): Result = lock.withLock { flushLocked() }

    private suspend fun flushLocked(): Result {
        var sentAny = false
        while (true) {
            val to = server()
            if (to.isBlank() || !canSend(to)) return if (sentAny) Result.Sent else Result.Nothing
            val batch = outbox.batch(to, ListenOutbox.BATCH)
            if (batch.isEmpty()) return if (sentAny) Result.Sent else Result.Nothing
            val body = buildJsonObject {
                put("listens", buildJsonArray { batch.forEach { add(it.toWire()) } })
            }
            val answer = try {
                api.post(PATH, body, onlyTo = to)
            } catch (e: ApiException) {
                if (keeps(e.status)) return Result.Kept
                // A batch the server can't take as it is would fail forever.
                outbox.remove(batch.map { it.id })
                continue
            } catch (e: IOException) {
                return Result.Kept
            }
            outbox.remove(batch.map { it.id })
            sentAny = true
            val recording = ((answer as? JsonObject)?.get("recording") as? JsonPrimitive)?.booleanOrNull
            if (recording == false) onRecordingOff()
        }
    }

    companion object {
        const val PATH = "/api/music/listens"

        /** Answers after which the batch is kept for a later try. */
        val KEPT = setOf(401, 403, 408, 429)

        fun keeps(status: Int): Boolean = status in KEPT || status >= 500 || status < 400
    }
}
