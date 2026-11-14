package com.domovoi.app.player

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DEVICE_ID_HEADER
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLEncoder

/**
 * The phone's Up next calls (API_REFERENCE §3.5f, device tier). Each names
 * the server it is for ([server]: the address the session lives on) and is
 * never sent once the app talks to another one (ApiClient `onlyTo`). One
 * request each: nothing here chains a second call on an answer.
 */
interface UpNextApi {
    /** `POST /api/music/up-next/sessions`. */
    suspend fun start(server: String, body: JsonObject): UpNextSession

    /** `POST …/{id}/next {have_through_seq}`. */
    suspend fun next(server: String, id: String, haveThrough: Int): UpNextBatch

    /** `POST …/{id}/feedback {seq, kind}`: the seqs the session took back. */
    suspend fun feedback(server: String, id: String, seq: Int, kind: String): List<Int>

    /** `PATCH …/{id}` (`autoplay`, `listener_person_id`). */
    suspend fun patch(server: String, id: String, body: JsonObject): UpNextSession?

    /** `GET …/{id}`: the session with the picks still waiting. */
    suspend fun read(server: String, id: String): UpNextSession

    /** `POST …/{id}/move {target}`. */
    suspend fun move(server: String, id: String, target: JsonObject): UpNextSession?
}

/** [UpNextApi] over the app's client, naming this device in `X-Device-Id`. */
class HttpUpNextApi(
    private val api: ApiClient,
    private val deviceId: () -> String,
) : UpNextApi {
    private fun path(id: String? = null, tail: String = ""): String =
        "/api/music/up-next/sessions" + (id?.let { "/" + URLEncoder.encode(it, "UTF-8").replace("+", "%20") } ?: "") + tail

    private suspend fun call(method: String, server: String, path: String, body: JsonObject?): JsonElement {
        val text = api.raw(
            method, path,
            body = body?.toString()?.toRequestBody("application/json".toMediaType()),
            headers = mapOf(DEVICE_ID_HEADER to deviceId()),
            onlyTo = server,
        )
        return if (text.isBlank()) JsonNull else DomovoiJson.parseToJsonElement(text)
    }

    override suspend fun start(server: String, body: JsonObject): UpNextSession =
        call("POST", server, path(), body).decode()

    override suspend fun next(server: String, id: String, haveThrough: Int): UpNextBatch =
        call("POST", server, path(id, "/next"), buildJsonObject { put("have_through_seq", haveThrough) }).decode()

    override suspend fun feedback(server: String, id: String, seq: Int, kind: String): List<Int> =
        removedOf(call("POST", server, path(id, "/feedback"), buildJsonObject { put("seq", seq); put("kind", kind) }))

    override suspend fun patch(server: String, id: String, body: JsonObject): UpNextSession? =
        sessionOrNull(call("PATCH", server, path(id), body))

    override suspend fun read(server: String, id: String): UpNextSession =
        call("GET", server, path(id), null).decode()

    override suspend fun move(server: String, id: String, target: JsonObject): UpNextSession? =
        sessionOrNull(call("POST", server, path(id, "/move"), buildJsonObject { put("target", target) }))

    companion object {
        /** `{removed: [seq]}`. */
        fun removedOf(answer: JsonElement): List<Int> = runCatching {
            answer.jsonObject["removed"]?.jsonArray?.mapNotNull { it.jsonPrimitive.intOrNull }
        }.getOrNull().orEmpty()

        private fun sessionOrNull(answer: JsonElement): UpNextSession? =
            (answer as? JsonObject)?.let { runCatching { it.decode<UpNextSession>() }.getOrNull() }
    }
}
