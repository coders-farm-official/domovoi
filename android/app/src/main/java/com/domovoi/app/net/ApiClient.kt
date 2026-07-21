package com.domovoi.app.net

import com.domovoi.app.data.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

val DomovoiJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    isLenient = true
}

class ApiException(val status: Int, message: String) : IOException(message)

/**
 * Thin JSON client over OkHttp — the Android analog of web/static/data.js
 * (apiGet/apiPost/apiPatch/apiDelete/apiUpload). Same error contract:
 * non-2xx throws with "{status} {reason}: {body}".
 */
class ApiClient(private val prefs: Prefs) {
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .build()

    val baseUrl: String get() = prefs.serverUrl.value

    fun absolute(path: String): String {
        if (path.startsWith("http://") || path.startsWith("https://")) return path
        return baseUrl + path
    }

    private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
        enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!cont.isCancelled) cont.resumeWithException(e)
            }
            override fun onResponse(call: Call, response: Response) = cont.resume(response)
        })
        cont.invokeOnCancellation { runCatching { cancel() } }
    }

    suspend fun raw(method: String, path: String, body: RequestBody? = null): String =
        withContext(Dispatchers.IO) {
            if (baseUrl.isBlank()) throw IOException("no server configured")
            val req = Request.Builder()
                .url(absolute(path))
                .method(method, body)
                .build()
            http.newCall(req).await().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    throw ApiException(resp.code, "${resp.code} ${resp.message}: ${text.take(200)}")
                }
                text
            }
        }

    private fun jsonBody(body: JsonElement?): RequestBody =
        (body ?: JsonObject(emptyMap())).toString().toRequestBody("application/json".toMediaType())

    suspend fun get(path: String): JsonElement = parse(raw("GET", path))
    suspend fun post(path: String, body: JsonElement? = null): JsonElement =
        parse(raw("POST", path, jsonBody(body)))
    suspend fun patch(path: String, body: JsonElement? = null): JsonElement =
        parse(raw("PATCH", path, jsonBody(body)))
    suspend fun put(path: String, body: JsonElement? = null): JsonElement =
        parse(raw("PUT", path, jsonBody(body)))
    suspend fun delete(path: String, body: JsonElement? = null): JsonElement =
        parse(raw("DELETE", path, body?.let { jsonBody(it) }))

    suspend fun upload(path: String, form: MultipartBody): JsonElement =
        parse(raw("POST", path, form))

    /** GET returning raw bytes (audio samples, clip playback, zip downloads). */
    suspend fun bytes(path: String): Pair<ByteArray, Map<String, String>> =
        withContext(Dispatchers.IO) {
            val req = Request.Builder().url(absolute(path)).build()
            http.newCall(req).await().use { resp ->
                if (!resp.isSuccessful) throw ApiException(resp.code, "${resp.code} ${resp.message}")
                val headers = resp.headers.names().associateWith { resp.headers[it].orEmpty() }
                (resp.body?.bytes() ?: ByteArray(0)) to headers
            }
        }

    private fun parse(text: String): JsonElement =
        if (text.isBlank()) JsonNull else DomovoiJson.parseToJsonElement(text)
}

/** Decode a JsonElement into a @Serializable model. */
inline fun <reified T> JsonElement.decode(): T = DomovoiJson.decodeFromJsonElement(kotlinx.serialization.serializer(), this)
