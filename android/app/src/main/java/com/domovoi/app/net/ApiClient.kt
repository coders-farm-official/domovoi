package com.domovoi.app.net

import com.domovoi.app.data.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
import java.net.UnknownServiceException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

val DomovoiJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    isLenient = true
}

class ApiException(
    val status: Int,
    message: String,
    /** The server asked this phone to pair, not to log in (see
     *  [isDeviceTokenRefusal]) — the UI routes to the pairing screen. */
    val deviceTokenRequired: Boolean = false,
) : IOException(message)

/**
 * Toast text for a failed mutation (CONVENTIONS rule 4). A non-2xx response is
 * the backend's fault, not the network's, so it reports the server's own
 * "{status} {reason}: {body}" message; only a real transport failure is blamed
 * on the connection.
 */
fun failureText(action: String, e: Throwable): String {
    val detail = e.message?.trim()?.takeIf { it.isNotEmpty() }
    return when {
        e is ApiException -> "$action failed: ${detail ?: "HTTP ${e.status}"}"
        // A cleartext refusal (CleartextPolicy or the platform) is not an
        // outage: say why, so the address can be corrected.
        e is UnknownServiceException -> "$action failed: ${detail ?: "not permitted"}"
        e is IOException -> "$action failed (offline?)"
        else -> "$action failed: ${detail ?: e.javaClass.simpleName}"
    }
}

/**
 * Thin JSON client over OkHttp — the Android analog of web/static/data.js
 * (apiGet/apiPost/apiPatch/apiDelete/apiUpload). Same error contract:
 * non-2xx throws with "{status} {reason}: {body}".
 */
class ApiClient(
    private val baseUrlProvider: () -> String,
    private val deviceTokenProvider: () -> String? = { null },
) {
    /** Production wiring: the base URL and the household device token both
     *  follow the saved preferences for the active server. */
    constructor(prefs: Prefs) : this({ prefs.serverUrl.value }, { prefs.deviceToken.value })

    /** The ONE http client the app uses — JSON calls, media3 playback,
     *  Coil images, discovery and both WebSockets. Every one of them gets
     *  both interceptors: [CleartextPolicy] runs first, so a plain-http
     *  connection the policy refuses never has the household token attached
     *  to it, and DeviceAuthInterceptor puts that token on everything else. */
    val http: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(CleartextPolicy.interceptor)
        .addInterceptor(DeviceAuthInterceptor(deviceTokenProvider))
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .build()

    /** Flipped when the server refuses this phone for want of the device
     *  token; the shell shows the pairing screen while it is true, and
     *  pairing (or switching server) clears it. */
    private val _pairingRequired = MutableStateFlow(false)
    val pairingRequired: StateFlow<Boolean> = _pairingRequired

    fun clearPairingRequired() { _pairingRequired.value = false }

    /** Called from the response path and from the WebSocket listeners,
     *  which see the refusal as a failed upgrade rather than a body. */
    fun notePossiblePairingRefusal(status: Int, body: String?) {
        if (isDeviceTokenRefusal(status, body)) _pairingRequired.value = true
    }

    val baseUrl: String get() = baseUrl()

    private fun baseUrl(): String = baseUrlProvider()

    val deviceToken: String? get() = deviceTokenProvider()

    fun absolute(path: String): String {
        if (path.startsWith("http://") || path.startsWith("https://")) return path
        return baseUrl + path
    }

    /** A WebSocket upgrade carrying this phone's household token. Used by
     *  StateBus (/ws/state) and DropinCallClient (/v1/dropin/{room}). */
    fun wsRequest(url: String): Request =
        Request.Builder().url(url).withDeviceToken(deviceTokenProvider()).build()

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
                // Makes every call a preflighted one: a multipart or body-less
                // POST would otherwise be a CORS simple request the server
                // can't refuse before the side effect lands (WEB-6). The
                // dashboard sends the same header.
                .header("X-Requested-With", "DomovoiApp")
                .method(method, body)
                .build()
            http.newCall(req).await().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    val pairing = isDeviceTokenRefusal(resp.code, text)
                    if (pairing) _pairingRequired.value = true
                    throw ApiException(
                        resp.code, "${resp.code} ${resp.message}: ${text.take(200)}", pairing,
                    )
                }
                if (_pairingRequired.value) _pairingRequired.value = false
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

    private fun parse(text: String): JsonElement =
        if (text.isBlank()) JsonNull else DomovoiJson.parseToJsonElement(text)
}

/** Decode a JsonElement into a @Serializable model. */
inline fun <reified T> JsonElement.decode(): T = DomovoiJson.decodeFromJsonElement(kotlinx.serialization.serializer(), this)
