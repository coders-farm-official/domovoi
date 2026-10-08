package com.domovoi.app.net

import okhttp3.HttpUrl
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response
import java.io.IOException

/**
 * The household device token on the wire.
 *
 * Domovoi asks a household device to prove it belongs before it may change
 * anything (`X-Device-Token`, see docs/SECURITY_PRIVACY.md). One phone-wide
 * OkHttp interceptor is what makes that true of EVERY request this app
 * makes to its server — the JSON client, media3's audio/video data source
 * and Coil's image loads all run on the same [okhttp3.OkHttpClient] —
 * rather than of the handful of call sites somebody remembered.
 *
 * And ONLY to its server. The interceptor is authoritative about where the
 * token goes ([TokenScope]): a request to any other scheme, host or port
 * leaves without it, whatever header a caller put on it. Before 2026-10-08
 * the token rode on everything the client sent — a radio stream's host, the
 * LAN sweep's probes, a URL another app pushed into the media session.
 *
 * WebSockets go through the same client and the same interceptor; the
 * upgrade requests the app builds ([ApiClient.wsRequest]) also set the
 * header themselves so a reader of StateBus or DropinCallClient can see
 * what is sent, and the interceptor still decides.
 */
const val DEVICE_TOKEN_HEADER = "X-Device-Token"

/** Smallest household token the server will store — the pairing screen
 *  refuses anything shorter so a typo never becomes a saved credential.
 *  Mirrors DEVICE_TOKEN_MIN_LEN in domovoi/admin_auth.py. */
const val DEVICE_TOKEN_MIN_LEN = 12

/** Largest, likewise (DEVICE_TOKEN_MAX_LEN). */
const val DEVICE_TOKEN_MAX_LEN = 128

/**
 * True when [token] is a value this phone may put in a header at all:
 * printable ASCII, 0x20 through 0x7E.
 *
 * This matters because OkHttp does NOT degrade gracefully — `Headers`
 * `require(c == '\t' || c in ' '..'~')` and throws
 * `IllegalArgumentException` on the request builder, on EVERY request the
 * app makes, with the offending value in the message (`X-Device-Token` is
 * not in OkHttp's `isSensitiveHeader` set, so it is not redacted). A
 * server will never mint a token outside this set, but the pairing screen
 * is a paste target, so the check belongs in front of the save.
 *
 * Tab is legal to OkHttp and still refused here: the server trims it away
 * at the edges and refuses it anywhere else.
 */
fun isStorableDeviceToken(token: String): Boolean =
    token.length in DEVICE_TOKEN_MIN_LEN..DEVICE_TOKEN_MAX_LEN &&
        token.all { it.code in 0x20..0x7E }

/** The token as it may appear in a header, or null: blank is "not paired",
 *  and a value OkHttp would refuse is dropped rather than thrown (an
 *  IllegalArgumentException here would escape through every call in the
 *  app AND carry the token in its message). */
internal fun headerSafeToken(token: String?): String? =
    token?.trim()?.takeIf { it.isNotEmpty() && it.all { c -> c.code in 0x20..0x7E } }

/**
 * Something the active server has to prove before the token goes to it
 * (security round 3, A6-03: its identity, after every network change).
 * [requireAdmitted] returns normally when a token-bearing request to
 * [base] may go out now and throws an [IOException] — the request never
 * leaves — otherwise. Consulted only when there is a token to protect.
 * The other two answer without blocking on a probe where that matters.
 */
fun interface TokenGate {
    @Throws(IOException::class)
    fun requireAdmitted(base: HttpUrl)

    /** Whether a refusal from [base] may raise the pairing screen (a server
     *  that has not proved itself must not invite a paste of the token). */
    fun admitsPairingPrompt(base: HttpUrl?): Boolean = base != null

    /** Whether the token may go to [base] right now, judged from what is
     *  already known (no probe) — the DownloadManager hand-off asks this
     *  on the main thread. */
    fun admitsTokenNow(base: HttpUrl): Boolean = true
}

class DeviceAuthInterceptor(
    private val tokenProvider: () -> String?,
    /** The active server; the token goes to it and nowhere else. */
    private val baseUrlProvider: () -> String?,
    private val gate: TokenGate? = null,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        // Whatever a caller set, the decision is made here.
        val bare = request.newBuilder().removeHeader(DEVICE_TOKEN_HEADER).build()
        val base = TokenScope.baseOf(baseUrlProvider())
        val wsUpgrade = request.tag(TokenScope.WsUpgrade::class.java) != null
        if (!TokenScope.admits(base, request.url, wsUpgrade)) return chain.proceed(bare)
        val token = headerSafeToken(tokenProvider()) ?: return chain.proceed(bare)
        gate?.requireAdmitted(base!!)
        return chain.proceed(bare.newBuilder().header(DEVICE_TOKEN_HEADER, token).build())
    }
}

/** Put the token on a request this app builds itself (the WS upgrades).
 *  Same drop-rather-than-throw rule as the interceptor, which still has the
 *  last word on whether it stays. */
fun Request.Builder.withDeviceToken(token: String?): Request.Builder =
    also { b -> headerSafeToken(token)?.let { b.header(DEVICE_TOKEN_HEADER, it) } }

/**
 * True when a refusal is the server asking to be paired rather than asking
 * for the admin password: the device tier answers `401` (nothing, or a
 * stale token) and `403` (a credential that does not authorize this tier),
 * and names the header in the detail. An admin-tier `401 admin session
 * required` is a different conversation and must not send the phone to the
 * pairing screen.
 */
fun isDeviceTokenRefusal(status: Int, body: String?): Boolean {
    if (status != 401 && status != 403) return false
    val text = body.orEmpty()
    return text.contains("device token", ignoreCase = true) ||
        text.contains(DEVICE_TOKEN_HEADER, ignoreCase = true)
}
