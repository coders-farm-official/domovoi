package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.io.IOException
import java.net.UnknownServiceException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Once this phone is paired, the household device token goes out on
 * EVERYTHING it sends TO ITS SERVER — JSON calls, the media3 / Coil loads
 * that share the client, and both WebSocket upgrades — and on nothing
 * addressed anywhere else (security round 3, A6-01/A6-02: the interceptor
 * scopes the token to the active server's scheme, host and port). A
 * refusal that asks for it sends the app back to the pairing screen
 * (security batch B1).
 */
class DeviceAuthTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    private lateinit var server: MockWebServer
    private var token: String? = "household-abc"
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient({ server.url("/").toString().trimEnd('/') }, { token })
    }

    @After fun down() = server.shutdown()

    /** The next request, or a failure — never a hang. */
    private fun MockWebServer.next(): RecordedRequest =
        takeRequest(5, TimeUnit.SECONDS) ?: error("no request reached the server")

    @Test fun everyRequestCarriesTheToken() = runBlocking {
        repeat(4) { server.enqueue(MockResponse().setBody("{}")) }
        api.get("/api/music/library")
        api.post("/api/music/queue", null)
        api.patch("/api/devices/x", null)
        api.delete("/api/music/queue/1")
        repeat(4) {
            assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
        }
    }

    @Test fun mediaAndImageLoadsOnTheSharedClientCarryItToo() {
        // media3's OkHttpDataSource and Coil are handed api.http, so a plain
        // call through that client must be authenticated the same way.
        server.enqueue(MockResponse().setBody("audio"))
        val req = okhttp3.Request.Builder().url(server.url("/api/music/library/7/audio")).build()
        api.http.newCall(req).execute().use { it.body?.string() }
        assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun bothWebSocketUpgradesCarryIt() {
        // The state socket is on the server itself; the drop-in socket is on
        // the core's port of the SAME host (phone-info points it there).
        val host = server.hostName
        val ws = api.wsRequest("ws://$host:${server.port}/ws/state")
        val dropin = api.wsRequest("ws://$host:6370/v1/dropin/kitchen?phone_id=android-1")
        assertEquals("household-abc", ws.header(DEVICE_TOKEN_HEADER))
        assertEquals("household-abc", dropin.header(DEVICE_TOKEN_HEADER))

        // ...and the interceptor, which has the last word, lets both through
        // on the wire: the state socket on the server's own port and the
        // drop-in socket on another port of its host.
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
        val stateSocket = api.http.newWebSocket(ws, object : WebSocketListener() {})
        assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
        stateSocket.cancel()

        val core = MockWebServer().also { it.start() }
        try {
            core.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val toCore = api.wsRequest("ws://$host:${core.port}/v1/dropin/kitchen?phone_id=android-1")
            val coreSocket = api.http.newWebSocket(toCore, object : WebSocketListener() {})
            assertEquals("household-abc", core.next().getHeader(DEVICE_TOKEN_HEADER))
            coreSocket.cancel()
        } finally {
            core.shutdown()
        }
    }

    @Test fun anUnpairedPhoneSendsNoHeaderAtAll() = runBlocking {
        token = null
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull(server.next().getHeader(DEVICE_TOKEN_HEADER))
        assertNull(api.wsRequest("ws://host/ws/state").header(DEVICE_TOKEN_HEADER))

        token = "   "
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull("a blank token is not a token", server.next().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun pairingTakesEffectOnTheNextRequestWithoutARestart() = runBlocking {
        token = null
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull(server.next().getHeader(DEVICE_TOKEN_HEADER))

        token = "paired-now"
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertEquals("paired-now", server.next().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun theTokenIsLookedUpByTheServerTheRequestIsScopedTo() = runBlocking {
        // A server switch writes the address, then the token. A request
        // intercepted between the two would pair the NEW address with the
        // OLD household's token if the token were read on its own; it is
        // looked up by the base the request is scoped to instead.
        val other = MockWebServer().also { it.start() }
        try {
            val first = server.url("/").toString().trimEnd('/')
            val second = "http://127.0.0.1:${other.port}"
            var active = first
            val book = mapOf(first to "household-abc", second to "other-house")
            val api = ApiClient({ active }, { "stale-active-token" }, tokenForServer = { book[it] })

            server.enqueue(MockResponse().setBody("{}"))
            api.get("/api/x")
            assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))

            active = second
            other.enqueue(MockResponse().setBody("{}"))
            api.get("/api/x")
            assertEquals("other-house", other.next().getHeader(DEVICE_TOKEN_HEADER))
            assertEquals("other-house", api.wsRequest("ws://127.0.0.1:${other.port}/ws/state").header(DEVICE_TOKEN_HEADER))
            assertEquals("other-house", api.deviceToken)

            // A server with no token of its own: nothing, not the stale one.
            active = "http://127.0.0.1:1"
            assertNull(api.deviceToken)
            assertNull(api.wsRequest("ws://127.0.0.1:1/ws/state").header(DEVICE_TOKEN_HEADER))
        } finally {
            other.shutdown()
        }
    }

    // ---- the token goes to the active server and nowhere else (A6-01/02) ----

    /** A second listener reached by a DIFFERENT host name: MockWebServer
     *  binds localhost, and 127.0.0.1 is the same socket under another name,
     *  which is exactly what the scope compares (names, like an origin). */
    private fun otherHost(other: MockWebServer, path: String): String =
        "http://127.0.0.1:${other.port}$path"

    @Test fun aRequestToAnotherHostCarriesNoToken() {
        val other = MockWebServer().also { it.start() }
        try {
            other.enqueue(MockResponse().setBody("audio"))
            val req = okhttp3.Request.Builder().url(otherHost(other, "/grab.mp3")).build()
            api.http.newCall(req).execute().use { it.body?.string() }
            assertNull("the attacker's listener sees no token", other.next().getHeader(DEVICE_TOKEN_HEADER))

            // An absolute URL through the JSON client is the same request.
            other.enqueue(MockResponse().setBody("{}"))
            runBlocking { api.get(otherHost(other, "/api/health")) }
            assertNull(other.next().getHeader(DEVICE_TOKEN_HEADER))
        } finally {
            other.shutdown()
        }
    }

    @Test fun aRequestToAnotherPortOnTheSameHostCarriesNoToken() {
        val other = MockWebServer().also { it.start() }
        try {
            other.enqueue(MockResponse().setBody("{}"))
            val req = okhttp3.Request.Builder().url("http://${server.hostName}:${other.port}/api/x").build()
            api.http.newCall(req).execute().use { it.body?.string() }
            assertNull(other.next().getHeader(DEVICE_TOKEN_HEADER))
        } finally {
            other.shutdown()
        }
    }

    @Test fun anExplicitHeaderOnAForeignRequestIsStrippedNotHonoured() {
        val other = MockWebServer().also { it.start() }
        try {
            other.enqueue(MockResponse().setBody("{}"))
            val req = okhttp3.Request.Builder().url(otherHost(other, "/api/x"))
                .header(DEVICE_TOKEN_HEADER, "household-abc").build()
            api.http.newCall(req).execute().use { it.body?.string() }
            assertNull("the interceptor decides, not the caller", other.next().getHeader(DEVICE_TOKEN_HEADER))

            // The same for an upgrade the app built itself: host-bound.
            other.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val ws = api.wsRequest("ws://127.0.0.1:${other.port}/ws/state")
            val socket = api.http.newWebSocket(ws, object : WebSocketListener() {})
            assertNull(other.next().getHeader(DEVICE_TOKEN_HEADER))
            socket.cancel()
        } finally {
            other.shutdown()
        }
    }

    @Test fun theSharedClientStillAuthenticatesTheActiveServerAfterAForeignCall() = runBlocking {
        val other = MockWebServer().also { it.start() }
        try {
            other.enqueue(MockResponse().setBody("{}"))
            api.http.newCall(okhttp3.Request.Builder().url(otherHost(other, "/x")).build()).execute().close()
            other.next()
            server.enqueue(MockResponse().setBody("{}"))
            api.get("/api/music/library")
            assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
        } finally {
            other.shutdown()
        }
    }

    // ---- redirects: every hop decides the token and the cleartext rule afresh ----

    @Test fun aRedirectToAnotherHostDropsTheToken() = runBlocking {
        val other = MockWebServer().also { it.start() }
        try {
            // The media path: a plain call on the shared client, as media3
            // and Coil make them.
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", otherHost(other, "/grab.mp3")))
            other.enqueue(MockResponse().setBody("audio"))
            val req = okhttp3.Request.Builder().url(server.url("/api/music/library/7/audio")).build()
            val body = api.http.newCall(req).execute().use { it.body?.string() }
            assertEquals("the redirect was followed", "audio", body)
            assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
            assertNull("the host it was sent on to sees no token", other.next().getHeader(DEVICE_TOKEN_HEADER))

            // The JSON path too.
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", otherHost(other, "/api/x")))
            other.enqueue(MockResponse().setBody("{}"))
            api.get("/api/x")
            server.next()
            assertNull(other.next().getHeader(DEVICE_TOKEN_HEADER))
        } finally {
            other.shutdown()
        }
    }

    @Test fun aRedirectWithinTheServerKeepsTheToken() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "/api/music/library/7/audio?download=1"))
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/music/library/7/audio")
        assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
        val hop = server.next()
        assertEquals("/api/music/library/7/audio?download=1", hop.path)
        assertEquals("household-abc", hop.getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun aRedirectToAPublicPlainHttpHostIsRefusedBeforeAnyConnection() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://203.0.113.5/grab.mp3"))
        val started = System.nanoTime()
        try {
            api.get("/api/x")
            fail("expected UnknownServiceException")
        } catch (e: UnknownServiceException) {
            assertEquals(CleartextPolicy.refusalMessage("203.0.113.5"), e.message)
        }
        assertTrue("refused by the rule, not by a connect timeout", System.nanoTime() - started < 2_000_000_000L)
    }

    @Test fun theStateSocketUpgradeIsNeverRedirected() {
        val other = MockWebServer().also { it.start() }
        try {
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", otherHost(other, "/ws/state")))
            other.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val failed = CountDownLatch(1)
            api.http.newWebSocket(
                api.wsRequest("ws://${server.hostName}:${server.port}/ws/state"),
                object : WebSocketListener() {
                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = failed.countDown()
                },
            )
            assertTrue(failed.await(5, TimeUnit.SECONDS))
            server.next()
            assertEquals("nothing reached the host it was sent on to", 0, other.requestCount)
        } finally {
            other.shutdown()
        }
    }

    // ---- the gate: what the server must prove before the token goes out ----

    @Test fun aGateThatRefusesHoldsTheWholeRequestBack() = runBlocking {
        var asked = 0
        val gated = ApiClient(
            { server.url("/").toString().trimEnd('/') }, { token },
            gate = { asked++; throw IOException("server identity mismatch") },
        )
        server.enqueue(MockResponse().setBody("{}"))
        try {
            gated.get("/api/timers")
            fail("expected the gate's IOException")
        } catch (e: IOException) {
            assertEquals("server identity mismatch", e.message)
        }
        assertEquals(1, asked)
        assertEquals("nothing left the phone", 0, server.requestCount)
        assertFalse("a server that is not ours is out of reach", gated.answers())
    }

    @Test fun theGateIsOnlyAskedWhenThereIsATokenToProtectAndAServerToSendItTo() = runBlocking {
        var asked = 0
        val gated = ApiClient(
            { server.url("/").toString().trimEnd('/') }, { token },
            gate = { asked++ },
        )
        // A foreign host: no token, so nothing to gate.
        val other = MockWebServer().also { it.start() }
        try {
            other.enqueue(MockResponse().setBody("{}"))
            gated.get(otherHost(other, "/api/health"))
            assertEquals(0, asked)
        } finally {
            other.shutdown()
        }
        // An unpaired phone: likewise.
        token = null
        server.enqueue(MockResponse().setBody("{}"))
        gated.get("/api/health")
        assertEquals(0, asked)
        // Paired and addressed to the server: asked once per request, and
        // the token goes out once the gate lets it.
        token = "household-abc"
        server.enqueue(MockResponse().setBody("{}"))
        gated.get("/api/health")
        assertEquals(1, asked)
        server.next()
        assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
    }

    // ---- a refusal routes to the pairing screen ---------------------------

    @Test fun aDeviceTokenRefusalAsksThePhoneToPair() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(401)
                .setBody("""{"detail":"X-Device-Token or admin session required"}"""),
        )
        assertFalse(api.pairingRequired.value)
        try {
            api.post("/api/music/queue", null)
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(401, e.status)
            assertTrue(e.deviceTokenRequired)
        }
        assertTrue(api.pairingRequired.value)

        // Pairing, then a call that works, puts the shell back.
        token = "household-abc"
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertFalse(api.pairingRequired.value)
    }

    @Test fun theCookieOnly403IsAPairingRefusalToo() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(403)
                .setBody("""{"detail":"X-Device-Token required — the dashboard cookie does not authorize device-tier actions"}"""),
        )
        try {
            api.post("/api/x", null); fail("expected ApiException")
        } catch (e: ApiException) {
            assertTrue(e.deviceTokenRequired)
        }
        assertTrue(api.pairingRequired.value)
    }

    @Test fun anAdminRefusalIsNotAPairingRefusal() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"admin session required"}"""))
        try {
            api.post("/api/config", null); fail("expected ApiException")
        } catch (e: ApiException) {
            assertFalse(e.deviceTokenRequired)
        }
        assertFalse("the admin password is a different conversation", api.pairingRequired.value)
    }

    @Test fun anOrdinaryServerErrorIsNotAPairingRefusal() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("boom"))
        try {
            api.get("/api/x"); fail("expected ApiException")
        } catch (e: ApiException) {
            assertFalse(e.deviceTokenRequired)
        }
        assertFalse(api.pairingRequired.value)
    }

    @Test fun aRefusedWebSocketUpgradeAsksThePhoneToPair() {
        // The socket listeners see a status and a reason, never a body.
        api.notePossiblePairingRefusal(401, "X-Device-Token or admin session required")
        assertTrue(api.pairingRequired.value)
        api.clearPairingRequired()
        api.notePossiblePairingRefusal(401, "Unauthorized")
        assertFalse(api.pairingRequired.value)
    }

    // ---- a token an admin CHOSE ------------------------------------------

    @Test fun aChosenTokenWithSpacesAndPunctuationGoesOutVerbatim() = runBlocking {
        token = "Maple Street, 1984!"
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertEquals("Maple Street, 1984!", server.next().getHeader(DEVICE_TOKEN_HEADER))
        assertEquals("Maple Street, 1984!", api.wsRequest("ws://host/ws/state").header(DEVICE_TOKEN_HEADER))
    }

    @Test fun everyPrintableAsciiCharacterSurvivesTheHeader() = runBlocking {
        token = (0x20..0x7E).map { it.toChar() }.joinToString("").trim()
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertEquals(token, server.next().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun aNonAsciiTokenIsDroppedRatherThanThrown() = runBlocking {
        // OkHttp's Headers.checkValue would throw IllegalArgumentException —
        // on EVERY request, with the token in the message (X-Device-Token is
        // not in its isSensitiveHeader set). Drop it instead: the server
        // answers 401 and the phone is sent to the pairing screen.
        token = "café-token-abcdef"
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull(server.next().getHeader(DEVICE_TOKEN_HEADER))
        assertNull(api.wsRequest("ws://host/ws/state").header(DEVICE_TOKEN_HEADER))
    }

    @Test fun theStorableRuleMatchesTheServersAtEveryBoundary() {
        assertEquals(12, DEVICE_TOKEN_MIN_LEN)
        assertEquals(128, DEVICE_TOKEN_MAX_LEN)
        // length floor and cap, measured on the trimmed form
        assertFalse(isStorableDeviceToken("a".repeat(11)))
        assertTrue(isStorableDeviceToken("a".repeat(12)))
        assertTrue(isStorableDeviceToken("a".repeat(128)))
        assertFalse(isStorableDeviceToken("a".repeat(129)))
        // alphabet: 0x1F out, 0x20 in, 0x7E in, 0x7F out, non-ASCII out
        assertFalse(isStorableDeviceToken("abcdef\u001Fghijkl"))
        assertTrue(isStorableDeviceToken("abcdef ghijkl"))
        assertTrue(isStorableDeviceToken("abcdef~ghijkl"))
        assertFalse(isStorableDeviceToken("abcdef\u007Fghijkl"))
        assertFalse(isStorableDeviceToken("abcdeféghijkl"))
        assertFalse(isStorableDeviceToken("abcdef🐱ghijkl"))
        assertFalse(isStorableDeviceToken("abcdef\tghijkl"))
        // the shapes a real server mints
        assertTrue(isStorableDeviceToken("acorn-maple-river-thistle-harbor-quartz-willow-ember"))
        assertTrue(isStorableDeviceToken("a3f0".repeat(16)))
    }

    @Test fun refusalClassificationIsAboutTheTierNotTheStatus() {
        assertTrue(isDeviceTokenRefusal(401, """{"detail":"X-Device-Token or admin session required"}"""))
        assertTrue(isDeviceTokenRefusal(403, """{"detail":"device token required"}"""))
        assertFalse(isDeviceTokenRefusal(401, """{"detail":"admin session required"}"""))
        assertFalse(isDeviceTokenRefusal(500, """{"detail":"X-Device-Token"}"""))
        assertFalse(isDeviceTokenRefusal(401, null))
    }
}
