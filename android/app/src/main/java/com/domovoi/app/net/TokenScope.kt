package com.domovoi.app.net

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where the household device token may go: to the active server, and
 * nowhere else.
 *
 * Until 2026-10-08 [DeviceAuthInterceptor] put the token on EVERY request
 * the shared OkHttpClient made, whatever its host — so anything that could
 * hand the client an absolute URL (a radio stream's address, a LAN sweep's
 * 254 probes, a second app driving the exported media session) collected
 * the token. The rule is now the dashboard's: the token goes only to the
 * origin it was issued by, compared on scheme, host and port.
 *
 * The one documented exception is the drop-in call socket, which the web
 * backend points at the core's own port on the SAME host
 * (`/api/satellites/{room}/dropin/phone-info`); [ApiClient.wsRequest] tags
 * that upgrade and the interceptor admits a tagged request on the active
 * host whatever its port. Pure, so the rule is unit-tested as written.
 */
object TokenScope {

    /** A marker tag [ApiClient.wsRequest] puts on the WebSocket upgrades the
     *  app builds itself, so the interceptor can tell them from a URL some
     *  library was handed. */
    class WsUpgrade private constructor() {
        companion object {
            val MARK = WsUpgrade()
        }
    }

    /** The active server's address as OkHttp sees it, or null when there is
     *  none (or it does not parse). `ws://` / `wss://` are the http forms. */
    fun baseOf(serverUrl: String?): HttpUrl? {
        val raw = serverUrl?.trim()?.trimEnd('/').orEmpty()
        if (raw.isBlank()) return null
        return raw.replaceFirst("ws://", "http://").replaceFirst("wss://", "https://").toHttpUrlOrNull()
    }

    /** [url] is the active server itself: same scheme, host and port. */
    fun sameServer(base: HttpUrl?, url: HttpUrl): Boolean =
        base != null && base.scheme == url.scheme && base.host.equals(url.host, ignoreCase = true) &&
            base.port == url.port

    /** [url] is on the active server's host, in the same scheme family, on
     *  any port — what the drop-in socket to the core's port needs. */
    fun sameHost(base: HttpUrl?, url: HttpUrl): Boolean =
        base != null && base.scheme == url.scheme && base.host.equals(url.host, ignoreCase = true)

    /**
     * Whether a request to [url] may carry the active server's token:
     * the active server itself, or — for an upgrade the app built
     * ([wsUpgrade]) — another port on its host.
     */
    fun admits(base: HttpUrl?, url: HttpUrl, wsUpgrade: Boolean): Boolean =
        sameServer(base, url) || (wsUpgrade && sameHost(base, url))
}
