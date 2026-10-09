package com.domovoi.app.net

import okhttp3.HttpUrl
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response
import java.net.ProtocolException

/**
 * Redirects, followed by the app rather than by OkHttp.
 *
 * OkHttp's own follower (`followRedirects`, on by default) runs AFTER the
 * application interceptors and once per call: it builds the follow-up
 * from the request those interceptors emitted — which already carries
 * `X-Device-Token` — and strips only `Authorization` when the host
 * changes. So a `3xx` from the active server would have delivered the
 * household token to whatever `Location` named, and the cleartext rule,
 * itself an application interceptor, would never have seen that hop
 * (review of security round 3, A6-01: no route redirects off-host today;
 * any plugin route, reverse proxy or future endpoint that did would have).
 *
 * The client therefore has `followRedirects(false)`, and this interceptor
 * — the FIRST application interceptor — follows them itself by calling
 * `chain.proceed` again, so every hop passes through the interceptors
 * below it: [CleartextPolicy] refuses a plain-http hop to a public host
 * before any connection is made, and [DeviceAuthInterceptor] decides the
 * token for the hop's own URL (the active server keeps it, anywhere else
 * loses it) and asks the identity gate. The follow-up is built the way
 * OkHttp builds it (the method and body rules, `Authorization` dropped
 * off host, at most [MAX_HOPS] hops), with three stricter rules: a
 * WebSocket upgrade is never redirected (the socket lands where it was
 * aimed or fails), an https→http downgrade is never followed, and the
 * token header never rides along (the hop decides it afresh).
 *
 * media3's data source, Coil and both WebSockets share the client, so the
 * same rules hold for them; the discovery client keeps this interceptor
 * too.
 */
object RedirectPolicy {
    /** OkHttp's own ceiling. */
    const val MAX_HOPS = 20

    private val REDIRECT_CODES = setOf(300, 301, 302, 303, 307, 308)

    val interceptor: Interceptor = Interceptor { chain -> follow(chain) }

    private fun follow(chain: Interceptor.Chain): Response {
        var request = chain.request()
        var response = chain.proceed(request)
        var hops = 0
        while (true) {
            val next = followUp(request, response) ?: return response
            response.close()
            if (++hops > MAX_HOPS) throw ProtocolException("too many redirects: $hops")
            request = next
            response = chain.proceed(request)
        }
    }

    /**
     * The request that follows [response], or null when it is not to be
     * followed: not a redirect, no usable `Location`, a WebSocket upgrade,
     * an https→http downgrade, or a `307`/`308` of anything but GET/HEAD
     * (OkHttp's rule too). Pure, so the rules are tested as written.
     */
    fun followUp(request: Request, response: Response): Request? {
        val code = response.code
        if (code !in REDIRECT_CODES) return null
        if (request.tag(TokenScope.WsUpgrade::class.java) != null) return null
        val location = response.header("Location") ?: return null
        val url = request.url.resolve(location) ?: return null
        if (request.url.isHttps && !url.isHttps) return null
        val method = request.method
        val builder = request.newBuilder()
        if (code == 307 || code == 308) {
            if (method != "GET" && method != "HEAD") return null
        } else if (method != "GET" && method != "HEAD") {
            // 300/301/302/303: the follow-up is a GET without the body.
            builder.method("GET", null)
                .removeHeader("Transfer-Encoding")
                .removeHeader("Content-Length")
                .removeHeader("Content-Type")
        }
        if (!sameConnection(request.url, url)) builder.removeHeader("Authorization")
        builder.removeHeader(DEVICE_TOKEN_HEADER)
        return builder.url(url).build()
    }

    private fun sameConnection(a: HttpUrl, b: HttpUrl): Boolean =
        a.scheme == b.scheme && a.host == b.host && a.port == b.port
}
