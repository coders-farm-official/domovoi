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
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
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
    /** The response body (its first 4,096 characters; the message keeps
     *  200); empty when there was none. */
    val body: String = "",
) : IOException(message) {
    /**
     * The part of the house the server says failed, from `failed` in a JSON
     * error body: "music_player" (a room's music player, on the domovoi
     * server) or "satellite" (2026-10-01, the core's music controls and
     * casts). Null when the server named none — an older core, or any other
     * error. Read from [body], or from the message's copy of it.
     */
    val failedPart: String? by lazy {
        val text = body.ifBlank { message?.substringAfter(": ", "").orEmpty() }
        runCatching {
            (DomovoiJson.parseToJsonElement(text) as? JsonObject)
                ?.get("failed")?.let { it as? JsonPrimitive }
                ?.takeIf { it.isString }?.content
        }.getOrNull()
    }
}

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
    /** What the active server must prove before the token goes to it
     *  (A6-03); null = nothing beyond being the active server. */
    private val gate: TokenGate? = null,
    /** The token by server address (Prefs.tokenForServer), so the one
     *  that goes out is the one issued by the server a request is scoped
     *  to; absent, [deviceTokenProvider] answers for every base (the
     *  tests' shape). */
    private val tokenForServer: ((String) -> String?)? = null,
    /** What the server's answers said about the token: refused, or "wait"
     *  ([TokenHealth]); null = nothing is learnt (the tests' default). */
    val health: TokenHealth? = null,
) {
    /** Production wiring: the base URL and the household device token both
     *  follow the saved preferences for the active server. */
    constructor(prefs: Prefs, gate: TokenGate? = null, health: TokenHealth? = null) :
        this({ prefs.serverUrl.value }, { prefs.deviceToken.value }, gate, { prefs.tokenForServer(it) }, health)

    private val tokenFor: (String) -> String? = tokenForServer ?: { deviceTokenProvider() }

    /** The ONE http client the app uses — JSON calls, media3 playback,
     *  Coil images and both WebSockets (discovery takes a copy WITHOUT the
     *  token interceptor, [Discovery.client]). Every one of them gets all
     *  three interceptors, on EVERY hop: [RedirectPolicy] follows redirects
     *  itself (OkHttp's follower is off, since it would carry the token to
     *  whatever a 3xx named and skip the cleartext rule for that hop),
     *  [CleartextPolicy] runs next, so a plain-http connection the policy
     *  refuses never has the household token attached to it, and
     *  DeviceAuthInterceptor puts that token on everything that is
     *  addressed to the active server — and strips it from anything that
     *  is not ([TokenScope]). */
    val http: OkHttpClient = OkHttpClient.Builder()
        .followRedirects(false)
        .followSslRedirects(false)
        .addInterceptor(RedirectPolicy.interceptor)
        .addInterceptor(CleartextPolicy.interceptor)
        .addInterceptor(DeviceAuthInterceptor(tokenFor, baseUrlProvider, gate, health))
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        // Walking out of Wi-Fi range often leaves the state socket half-open
        // rather than closed: no FIN arrives, so without a ping it would read
        // "live" indefinitely and the shell would never fall back to local
        // media. A missed pong fails the socket and StateBus reconnects.
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    /** Flipped when the server refuses this phone for want of the device
     *  token; the shell shows the pairing screen while it is true, and
     *  pairing (or switching server) clears it. */
    private val _pairingRequired = MutableStateFlow(false)
    val pairingRequired: StateFlow<Boolean> = _pairingRequired

    fun clearPairingRequired() { _pairingRequired.value = false }

    /** Called from the response path and from the WebSocket listeners,
     *  which see the refusal as a failed upgrade rather than a body. Only a
     *  server that has proved itself (or has nothing to prove) may raise
     *  the pairing screen: a rogue's 401 must not invite a paste (A6-03). */
    fun notePossiblePairingRefusal(status: Int, body: String?) {
        if (isPairingRefusalFromOurServer(status, body)) _pairingRequired.value = true
    }

    private fun isPairingRefusalFromOurServer(status: Int, body: String?): Boolean =
        isDeviceTokenRefusal(status, body) &&
            (gate?.admitsPairingPrompt(TokenScope.baseOf(baseUrl)) ?: true)

    val baseUrl: String get() = baseUrl()

    private fun baseUrl(): String = baseUrlProvider()

    /** The token for the active server (by its address). */
    val deviceToken: String? get() = tokenFor(baseUrl)

    /**
     * The token a request to [url] made OUTSIDE this client (the system
     * DownloadManager, for the one save-to-device route on the device
     * tier) may carry: the same rule the interceptor applies — the active
     * server itself, and only once it has proved itself on this network —
     * taking the proof now when there is no fresh one, which is why this
     * suspends (the probe runs on IO). Null when [url] is not the active
     * server or the phone is not paired; throws the gate's IOException
     * when the server did not prove itself, so the caller can say so
     * rather than queue a request that would carry the token and fail.
     */
    suspend fun tokenForDownload(url: HttpUrl): String? = withContext(Dispatchers.IO) {
        if (HouseholdLock.roomModeActive) return@withContext null
        val raw = baseUrl
        val base = TokenScope.baseOf(raw) ?: return@withContext null
        if (!TokenScope.sameServer(base, url)) return@withContext null
        val token = headerSafeToken(tokenFor(raw)) ?: return@withContext null
        // The interceptor's rules, here too: a refused token is not handed
        // out, and while the server asks the phone to wait, nothing is.
        health?.let { h ->
            if (h.isRejected(raw)) return@withContext null
            h.waitMs(raw).takeIf { it > 0 }?.let { throw TokenThrottledException(it) }
        }
        gate?.requireAdmitted(base)
        token
    }

    /**
     * Whether the active server accepts [candidate] as the household token,
     * asked ONCE before it is saved (the pairing screen, Settings →
     * Connection, the refused-token card): one device-tier read carrying the
     * candidate and nothing else. A wrong paste costs one wrong token on the
     * server's count, as it must; nothing here retries. The candidate goes
     * only to the active server, after the identity gate admits it, and not
     * at all while the server makes this address wait.
     */
    suspend fun checkDeviceToken(candidate: String): TokenCheck = withContext(Dispatchers.IO) {
        if (HouseholdLock.roomModeActive) return@withContext TokenCheck.Unavailable
        val raw = baseUrl
        val base = TokenScope.baseOf(raw) ?: return@withContext TokenCheck.Unavailable
        val token = headerSafeToken(candidate) ?: return@withContext TokenCheck.Refused
        health?.waitMs(raw)?.takeIf { it > 0 }?.let { return@withContext TokenCheck.Throttled(it) }
        try {
            gate?.requireAdmitted(base)
        } catch (e: IOException) {
            return@withContext TokenCheck.Unavailable
        }
        val probe = http.newBuilder()
            .apply { interceptors().removeAll { it is DeviceAuthInterceptor } }
            .callTimeout(8, TimeUnit.SECONDS)
            .build()
        val url = base.newBuilder().encodedPath(TOKEN_CHECK_PATH).build()
        val req = Request.Builder().url(url)
            .header(DEVICE_TOKEN_HEADER, token)
            .header("X-Requested-With", "DomovoiApp")
            .build()
        try {
            probe.newCall(req).execute().use { resp ->
                val body = runCatching { resp.peekBody(4096).string() }.getOrDefault("")
                when {
                    resp.isSuccessful || resp.code == 404 -> TokenCheck.Accepted
                    isServerDeviceRefusal(resp.code, body) -> TokenCheck.Refused
                    resp.code == 429 && TokenHealth.isDeviceTokenThrottle(body) -> {
                        val wait = TokenHealth.retryAfterMs(resp.header("Retry-After"))
                        health?.noteWait(raw, wait)
                        TokenCheck.Throttled(wait)
                    }
                    else -> TokenCheck.Unavailable
                }
            }
        } catch (e: IOException) {
            TokenCheck.Unavailable
        }
    }

    fun absolute(path: String): String {
        if (path.startsWith("http://") || path.startsWith("https://")) return path
        return baseUrl + path
    }

    /** Short-fused copy of [http] for [answers]: same interceptors, but a
     *  probe that hangs is itself the answer. */
    private val probeHttp: OkHttpClient by lazy {
        http.newBuilder().callTimeout(5, TimeUnit.SECONDS).build()
    }

    /**
     * Whether the server answers at all. Any HTTP status counts — a phone
     * that is not paired yet has its live socket refused (403), and that
     * server is plainly there — so only a transport failure means "out of
     * reach". A cleartext refusal is a setting to fix, not an outage, so it
     * counts as an answer too and the workspace stays up to say so. So does
     * a server that asked this phone to wait ([TokenThrottledException]):
     * it answered, and the shell shows the wait.
     */
    suspend fun answers(): Boolean = withContext(Dispatchers.IO) {
        val base = baseUrl
        if (base.isBlank()) return@withContext false
        try {
            probeHttp.newCall(Request.Builder().url("$base/api/health").build()).execute().use { true }
        } catch (e: UnknownServiceException) {
            true
        } catch (e: TokenThrottledException) {
            true
        } catch (e: IOException) {
            false
        }
    }

    /** A WebSocket upgrade carrying this phone's household token. Used by
     *  StateBus (/ws/state) and DropinCallClient (/v1/dropin/{room}). The
     *  tag tells DeviceAuthInterceptor this upgrade is the app's own, so the
     *  drop-in socket to the core's port on the server's host keeps its
     *  token; the interceptor still strips it from any other host. */
    fun wsRequest(url: String): Request =
        Request.Builder().url(url)
            .tag(TokenScope.WsUpgrade::class.java, TokenScope.WsUpgrade.MARK)
            .withDeviceToken(tokenFor(baseUrl))
            .build()

    private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
        enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!cont.isCancelled) cont.resumeWithException(e)
            }
            override fun onResponse(call: Call, response: Response) = cont.resume(response)
        })
        cont.invokeOnCancellation { runCatching { cancel() } }
    }

    /** [trackPairing] false: the answer neither raises nor clears
     *  [pairingRequired] ([getUntracked]). */
    suspend fun raw(method: String, path: String, body: RequestBody? = null, trackPairing: Boolean = true): String =
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
                    val pairing = isPairingRefusalFromOurServer(resp.code, text)
                    if (pairing && trackPairing) _pairingRequired.value = true
                    throw ApiException(
                        resp.code, "${resp.code} ${resp.message}: ${text.take(200)}", pairing,
                        body = text.take(4096),
                    )
                }
                if (trackPairing && _pairingRequired.value) _pairingRequired.value = false
                text
            }
        }

    private fun jsonBody(body: JsonElement?): RequestBody =
        (body ?: JsonObject(emptyMap())).toString().toRequestBody("application/json".toMediaType())

    suspend fun get(path: String): JsonElement = parse(raw("GET", path))

    /**
     * A GET whose answer says nothing about pairing: a background read that
     * falls back to an OPEN endpoint (the container guard's summary) would
     * otherwise clear the pairing screen with its 200 the moment its
     * household read had raised it with a 401, and the shell would flicker
     * between the two. Errors still throw as from [get].
     */
    suspend fun getUntracked(path: String): JsonElement = parse(raw("GET", path, trackPairing = false))
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

/** What the server said to a household token checked before saving
 *  ([ApiClient.checkDeviceToken]). */
sealed class TokenCheck {
    data object Accepted : TokenCheck()
    data object Refused : TokenCheck()
    data class Throttled(val waitMs: Long) : TokenCheck()
    /** 503, no answer, or a server that did not prove itself: not a verdict. */
    data object Unavailable : TokenCheck()
}

/** A device-tier read (the satellites list) any household token may make. */
internal const val TOKEN_CHECK_PATH = "/api/satellites"

/** Decode a JsonElement into a @Serializable model. */
inline fun <reified T> JsonElement.decode(): T = DomovoiJson.decodeFromJsonElement(kotlinx.serialization.serializer(), this)
