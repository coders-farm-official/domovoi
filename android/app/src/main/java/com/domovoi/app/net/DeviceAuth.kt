package com.domovoi.app.net

import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response

/**
 * The household device token on the wire.
 *
 * Domovoi asks a household device to prove it belongs before it may change
 * anything (`X-Device-Token`, see docs/SECURITY_PRIVACY.md). One phone-wide
 * OkHttp interceptor is what makes that true of EVERY request this app
 * makes — the JSON client, media3's audio/video data source and Coil's
 * image loads all run on the same [okhttp3.OkHttpClient] — rather than of
 * the handful of call sites somebody remembered.
 *
 * WebSockets go through the same client, and the header is also set
 * explicitly on those upgrade requests ([ApiClient.wsRequest]) so a reader
 * of StateBus or DropinCallClient can see what is sent.
 */
const val DEVICE_TOKEN_HEADER = "X-Device-Token"

class DeviceAuthInterceptor(private val tokenProvider: () -> String?) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val token = tokenProvider()?.trim().orEmpty()
        // An explicit header on the request wins; a blank token (not paired
        // yet, or a server that never asked for one) sends nothing.
        if (token.isEmpty() || request.header(DEVICE_TOKEN_HEADER) != null) {
            return chain.proceed(request)
        }
        return chain.proceed(request.newBuilder().header(DEVICE_TOKEN_HEADER, token).build())
    }
}

/** Put the token on a request this app builds itself (the WS upgrades). */
fun Request.Builder.withDeviceToken(token: String?): Request.Builder =
    also { b -> token?.trim()?.takeIf { it.isNotEmpty() }?.let { b.header(DEVICE_TOKEN_HEADER, it) } }

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
