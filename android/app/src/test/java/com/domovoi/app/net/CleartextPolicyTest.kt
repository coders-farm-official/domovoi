package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.UnknownServiceException

/**
 * Plain http / ws is accepted only towards the home network (AND-1). The
 * IP-range half of the rule cannot live in network_security_config.xml, so
 * it is pinned here on the policy and on the app's OkHttpClient.
 */
class CleartextPolicyTest {

    private val homeHosts = listOf(
        "10.0.2.2",            // emulator host
        "10.0.0.1", "10.255.255.255",
        "172.16.0.1", "172.31.255.254",
        "192.168.0.117", "192.168.1.10",
        "127.0.0.1", "127.0.0.2",
        "169.254.10.10",
        "100.64.0.1", "100.127.255.254",
        "localhost", "LOCALHOST", "localhost.",
        "domovoi.local", "Domovoi.LOCAL", "pi.domovoi.local",
        "domovoi.home.arpa", "domovoi.internal", "domovoi.lan", "domovoi.home",
        "::1", "fc00::1", "fd12:3456::1", "fe80::1", "[fe80::1]", "fe80::1%wlan0",
    )

    private val otherHosts = listOf(
        "8.8.8.8", "1.1.1.1", "203.0.113.5",
        "11.0.0.1", "9.255.255.255",
        "172.15.255.255", "172.32.0.1",
        "192.169.0.1", "192.167.255.255",
        "100.63.255.255", "100.128.0.1",
        "169.253.0.1",
        "example.com", "domovoi.example.com", "news.example",
        "local.example.com", "notlocal", "home.example.org",
        "2001:db8::1", "2606:4700::1111", "fb00::1", "fec0::1",
        "256.1.1.1", "10.0.0", "10.0.0.0.1", "", "   ",
    )

    // ---- cleartextPermitted ------------------------------------------------

    @Test fun homeNetworkHostsMayUseCleartext() {
        for (h in homeHosts) assertTrue(h, CleartextPolicy.cleartextPermitted(h))
    }

    @Test fun everyOtherHostMayNot() {
        for (h in otherHosts) assertFalse(h, CleartextPolicy.cleartextPermitted(h))
        assertFalse(CleartextPolicy.cleartextPermitted(null))
    }

    // ---- permits(url) -----------------------------------------------------

    @Test fun httpsIsAlwaysPermitted() {
        for (h in listOf("8.8.8.8", "203.0.113.5", "example.com", "domovoi.example.com", "[2001:db8::1]")) {
            val url = "https://$h/api/health".toHttpUrl()
            assertTrue(h, CleartextPolicy.permits(url))
        }
        assertTrue(CleartextPolicy.permits("https://192.168.0.117:6369/".toHttpUrl()))
    }

    @Test fun httpIsPermittedOnlyOnTheHomeNetwork() {
        assertTrue(CleartextPolicy.permits("http://10.0.2.2:6390/api/health".toHttpUrl()))
        assertTrue(CleartextPolicy.permits("http://192.168.0.117:6369/".toHttpUrl()))
        assertTrue(CleartextPolicy.permits("http://domovoi.local:6369/".toHttpUrl()))
        assertFalse(CleartextPolicy.permits("http://203.0.113.5:6369/".toHttpUrl()))
        assertFalse(CleartextPolicy.permits("http://domovoi.example.com:6369/".toHttpUrl()))
    }

    @Test fun webSocketUrlsFollowTheSameRule() {
        // Request.Builder rewrites ws/wss to http/https, which is how the
        // StateBus and drop-in WebSocket requests reach the policy.
        fun ws(s: String) = Request.Builder().url(s).build().url
        assertTrue(CleartextPolicy.permits(ws("ws://192.168.0.117:6369/ws/state")))
        assertTrue(CleartextPolicy.permits(ws("ws://10.0.2.2:6394/v1/dropin/kitchen")))
        assertFalse(CleartextPolicy.permits(ws("ws://203.0.113.5:6370/v1/dropin/kitchen")))
        assertTrue(CleartextPolicy.permits(ws("wss://203.0.113.5:6370/v1/dropin/kitchen")))
    }

    // ---- the interceptor on a real client ---------------------------------

    @Test fun cleartextToLoopbackStillReachesTheServer() {
        // MockWebServer listens on 127.0.0.1 - the same rule that admits the
        // emulator host 10.0.2.2 and any LAN address.
        val server = MockWebServer().also { it.start() }
        try {
            server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
            val client = OkHttpClient.Builder().addInterceptor(CleartextPolicy.interceptor).build()
            val url = server.url("/api/health")
            assertEquals("http", url.scheme)
            client.newCall(Request.Builder().url(url).build()).execute().use { resp ->
                assertEquals(200, resp.code)
                assertEquals("""{"status":"ok"}""", resp.body!!.string())
            }
            assertEquals("/api/health", server.takeRequest().path)
        } finally {
            server.shutdown()
        }
    }

    @Test fun cleartextOutsideTheHomeNetworkFailsBeforeConnecting() {
        val client = OkHttpClient.Builder().addInterceptor(CleartextPolicy.interceptor).build()
        // 203.0.113.0/24 is TEST-NET-3: never routed, so a connection attempt
        // would hang until the timeout. The policy refuses first.
        val started = System.nanoTime()
        val e = assertThrows(UnknownServiceException::class.java) {
            client.newCall(Request.Builder().url("http://203.0.113.5:6390/api/health").build()).execute()
        }
        assertTrue(e.message, e.message!!.contains("203.0.113.5"))
        assertTrue(e.message, e.message!!.contains("https://"))
        assertTrue("refused in ${(System.nanoTime() - started) / 1_000_000} ms",
            (System.nanoTime() - started) < 2_000_000_000L)
    }

    @Test fun theAppsApiClientCarriesThePolicy() = runBlocking {
        val api = ApiClient(baseUrlProvider = { "http://203.0.113.5:6390" })
        val e = assertThrows(UnknownServiceException::class.java) {
            runBlocking { api.get("/api/health") }
        }
        assertTrue(e.message, e.message!!.contains("203.0.113.5"))
        // And the toast text names the reason instead of blaming the network.
        val text = failureText("connect", e)
        assertTrue(text, text.contains("home network"))
        assertFalse(text, text.contains("offline"))
    }

    @Test fun theAppsApiClientStillTalksToTheHomeNetwork() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
            val api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
            val body = api.raw("GET", "/api/health")
            assertEquals("""{"status":"ok"}""", body)
        } finally {
            server.shutdown()
        }
    }
}
