package com.domovoi.app.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * A dashboard that answered `/api/health`. [identity] is the key and
 * fingerprint it advertises there (`identity.public_key` /
 * `identity.fingerprint`, `SHA256:…`), unproven at this point — shown in
 * the trust dialog so a person can compare it with the dashboard's
 * Settings → About, PINNED when the person says yes to that dialog, and
 * proven with a signed challenge against that pin the first time the app
 * talks to the server (net/IdentityGate.kt). Null for an older backend or
 * a block whose key and fingerprint do not agree.
 */
data class FoundDomovoi(val url: String, val name: String?, val identity: ServerIdentity.Pin? = null) {
    val fingerprint: String? get() = identity?.fingerprint
}

/**
 * LAN discovery for domovoi dashboards: probes the phone's /24
 * subnet for web backends answering /api/health on :6369, and labels
 * hits with the bot name from /api/config. Mirrors the web UI's
 * ServerStore.scan (web/static/data.js).
 *
 * Every probe is UNAUTHENTICATED. `/api/health` and `/api/config` are open
 * reads, and the hosts probed are by definition not (yet) the active
 * server — a sweep touches 254 addresses and a typed address is probed
 * before the trust dialog — so the client used here is the app's own minus
 * [DeviceAuthInterceptor] ([client]). Until 2026-10-08 the sweep carried
 * the active server's household token to every one of them (A6-02).
 */
object Discovery {
    const val DEFAULT_PORT = 6369

    /** True when the device is on wifi or ethernet (i.e. plausibly on the LAN). */
    fun onLan(context: Context): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
    }

    /** The device's site-local IPv4, e.g. "192.168.1.57". */
    fun localIpv4(): String? = runCatching {
        NetworkInterface.getNetworkInterfaces().asSequence()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.asSequence() }
            .filterIsInstance<Inet4Address>()
            .firstOrNull { it.isSiteLocalAddress }
            ?.hostAddress
    }.getOrNull()

    /**
     * A short-fused copy of [http] with no household token on it: the
     * cleartext policy and everything else stay, [DeviceAuthInterceptor]
     * goes. (The interceptor would strip the token from a foreign host
     * anyway; a probe has no business even considering it.)
     */
    fun client(http: OkHttpClient, timeoutMs: Long): OkHttpClient =
        http.newBuilder()
            .apply { interceptors().removeAll { it is DeviceAuthInterceptor } }
            .connectTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .readTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .build()

    /** Probe one base URL; returns the hit (with bot name) or null. */
    suspend fun probe(http: OkHttpClient, base: String, timeoutMs: Long = 1000): FoundDomovoi? =
        withContext(Dispatchers.IO) {
            val client = client(http, timeoutMs)
            val clean = base.trimEnd('/')
            runCatching {
                val identity = client.newCall(Request.Builder().url("$clean/api/health").build())
                    .execute().use { resp ->
                        if (!resp.isSuccessful) return@withContext null
                        advertisedIdentity(resp.body?.string())
                    }
                val name = runCatching {
                    client.newCall(Request.Builder().url("$clean/api/config").build())
                        .execute().use { resp ->
                            if (!resp.isSuccessful) return@runCatching null
                            DomovoiJson.parseToJsonElement(resp.body?.string().orEmpty())
                                .jsonObject["bot_name"]?.jsonPrimitive?.contentOrNull
                        }
                }.getOrNull()
                FoundDomovoi(clean, name, identity)
            }.getOrNull()
        }

    /** The identity a health answer advertises — only when its key and
     *  fingerprint are well-formed and agree ([ServerIdentity.advertised]);
     *  a fingerprint with no key behind it is nothing to pin. */
    internal fun advertisedIdentity(healthBody: String?): ServerIdentity.Pin? = runCatching {
        val identity = (DomovoiJson.parseToJsonElement(healthBody.orEmpty()) as? JsonObject)
            ?.get("identity")?.let { it as? JsonObject } ?: return null
        fun field(name: String) = identity[name]?.jsonPrimitive?.contentOrNull
        ServerIdentity.advertised(field("algorithm"), field("public_key"), field("fingerprint"))
    }.getOrNull()

    /** The `identity.fingerprint` a health answer advertises, if any — as
     *  [advertisedIdentity] vets it. */
    internal fun advertisedFingerprint(healthBody: String?): String? = advertisedIdentity(healthBody)?.fingerprint

    /**
     * Scan the /24 around the phone's address for dashboards on
     * [DEFAULT_PORT]. ~254 probes at 40-way concurrency with sub-second
     * timeouts — a few seconds wall-clock on a quiet network.
     */
    suspend fun scan(
        http: OkHttpClient,
        onProgress: (done: Int, total: Int, found: Int) -> Unit = { _, _, _ -> },
    ): List<FoundDomovoi> {
        val ip = localIpv4() ?: return emptyList()
        val prefix = ip.substringBeforeLast('.')
        val done = AtomicInteger(0)
        val foundCount = AtomicInteger(0)
        val gate = Semaphore(40)
        return coroutineScope {
            (1..254).map { n ->
                async(Dispatchers.IO) {
                    gate.withPermit {
                        val hit = probe(http, "http://$prefix.$n:$DEFAULT_PORT", timeoutMs = 700)
                        if (hit != null) foundCount.incrementAndGet()
                        onProgress(done.incrementAndGet(), 254, foundCount.get())
                        hit
                    }
                }
            }.awaitAll().filterNotNull().sortedBy { it.url }
        }
    }
}
