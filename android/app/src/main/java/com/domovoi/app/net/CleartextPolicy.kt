package com.domovoi.app.net

import okhttp3.HttpUrl
import okhttp3.Interceptor
import java.net.UnknownServiceException

/**
 * Where the app may talk plain http / ws: the home network only.
 *
 * res/xml/network_security_config.xml is the platform half of this rule.
 * Android matches its <domain> entries by exact host or DNS suffix, so it
 * cannot say "any RFC 1918 address" - and the usual server address is one.
 * This object is therefore consulted by the app's single OkHttpClient (the
 * API, both WebSockets, media3 and Coil all share it) before any cleartext
 * connection is attempted; [interceptor] refuses the request outright when
 * the host is not on the list, before DNS or a socket is touched.
 *
 * Permitted in the clear:
 *  - IPv4 private ranges 10/8, 172.16/12, 192.168/16 (RFC 1918) - the
 *    emulator host 10.0.2.2 is inside 10/8
 *  - IPv4 loopback 127/8 and link-local 169.254/16
 *  - IPv4 100.64/10 (RFC 6598 shared space, used by Tailscale-style overlays)
 *  - IPv6 loopback ::1, unique-local fc00::/7, link-local fe80::/10
 *  - the name "localhost" and any name under [privateNameSuffixes]
 * Everything else must use https.
 */
object CleartextPolicy {

    /** Name suffixes that only resolve on a home network (RFC 6762 .local,
     *  RFC 8375 .home.arpa, ICANN-reserved .internal, and the never-delegated
     *  .lan / .home / .localhost that home routers hand out). */
    val privateNameSuffixes: List<String> =
        listOf(".local", ".home.arpa", ".internal", ".lan", ".home", ".localhost")

    /** Whether plain http / ws to [host] (a name or an IP literal) is allowed. */
    fun cleartextPermitted(host: String?): Boolean {
        val h = host?.trim()?.trimEnd('.')?.lowercase().orEmpty()
        if (h.isEmpty()) return false
        if (h == "localhost") return true
        if (privateNameSuffixes.any { h.endsWith(it) }) return true
        parseIpv4(h)?.let { return isPrivateIpv4(it) }
        if (h.contains(':')) return isPrivateIpv6(h)
        return false
    }

    /** https always; http only when [cleartextPermitted] says so. ws/wss URLs
     *  arrive here as http/https - Request.Builder rewrites them. */
    fun permits(url: HttpUrl): Boolean = url.isHttps || cleartextPermitted(url.host)

    /** The one-line reason shown when a plain-http server address is refused. */
    fun refusalMessage(host: String): String =
        "plain http to $host is only allowed on the home network; use https://"

    /**
     * OkHttp application interceptor: a cleartext request to a host outside
     * the home network fails with [UnknownServiceException] (the same type the
     * platform uses for its own cleartext refusals) before any connection.
     */
    val interceptor: Interceptor = Interceptor { chain ->
        val url = chain.request().url
        if (!permits(url)) throw UnknownServiceException(refusalMessage(url.host))
        chain.proceed(chain.request())
    }

    // ---- IP literal helpers (pure, JVM-testable) ----------------------------

    /** Four dotted decimal octets, each 0..255, or null. */
    internal fun parseIpv4(text: String): IntArray? {
        val parts = text.split('.')
        if (parts.size != 4) return null
        val octets = IntArray(4)
        for (i in parts.indices) {
            val p = parts[i]
            if (p.isEmpty() || p.length > 3 || !p.all { it in '0'..'9' }) return null
            val v = p.toInt()
            if (v > 255) return null
            octets[i] = v
        }
        return octets
    }

    internal fun isPrivateIpv4(o: IntArray): Boolean = when {
        o[0] == 10 -> true                                   // 10.0.0.0/8
        o[0] == 172 && o[1] in 16..31 -> true                // 172.16.0.0/12
        o[0] == 192 && o[1] == 168 -> true                   // 192.168.0.0/16
        o[0] == 127 -> true                                  // loopback
        o[0] == 169 && o[1] == 254 -> true                   // link-local
        o[0] == 100 && o[1] in 64..127 -> true               // 100.64.0.0/10
        else -> false
    }

    /** Textual IPv6 (brackets and a zone id are tolerated): loopback,
     *  unique-local fc00::/7 or link-local fe80::/10. */
    internal fun isPrivateIpv6(text: String): Boolean {
        val h = text.removePrefix("[").substringBefore(']').substringBefore('%').lowercase()
        if (h == "::1" || h == "0:0:0:0:0:0:0:1") return true
        val first = h.substringBefore(':')
        if (first.isEmpty() || first.length > 4 || !first.all { it in '0'..'9' || it in 'a'..'f' }) return false
        val hextet = first.toInt(16)
        return (hextet and 0xfe00) == 0xfc00 ||              // fc00::/7
            (hextet and 0xffc0) == 0xfe80                    // fe80::/10
    }
}
