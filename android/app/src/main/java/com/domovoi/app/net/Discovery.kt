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
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

data class FoundDomovoi(val url: String, val name: String?)

/**
 * LAN discovery for domovoi dashboards: probes the phone's /24
 * subnet for web backends answering /api/health on :6369, and labels
 * hits with the bot name from /api/config. Mirrors the web UI's
 * ServerStore.scan (web/static/data.js).
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

    /** Probe one base URL; returns the hit (with bot name) or null. */
    suspend fun probe(http: OkHttpClient, base: String, timeoutMs: Long = 1000): FoundDomovoi? =
        withContext(Dispatchers.IO) {
            val client = http.newBuilder()
                .connectTimeout(timeoutMs, TimeUnit.MILLISECONDS)
                .readTimeout(timeoutMs, TimeUnit.MILLISECONDS)
                .build()
            val clean = base.trimEnd('/')
            runCatching {
                client.newCall(Request.Builder().url("$clean/api/health").build())
                    .execute().use { resp ->
                        if (!resp.isSuccessful) return@withContext null
                    }
                val name = runCatching {
                    client.newCall(Request.Builder().url("$clean/api/config").build())
                        .execute().use { resp ->
                            if (!resp.isSuccessful) return@runCatching null
                            DomovoiJson.parseToJsonElement(resp.body?.string().orEmpty())
                                .jsonObject["bot_name"]?.jsonPrimitive?.contentOrNull
                        }
                }.getOrNull()
                FoundDomovoi(clean, name)
            }.getOrNull()
        }

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
