package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * Once this phone is paired, the household device token goes out on
 * EVERYTHING it sends — JSON calls, the media3 / Coil loads that share the
 * client, and both WebSocket upgrades — and a refusal that asks for it
 * sends the app back to the pairing screen (security batch B1).
 */
class DeviceAuthTest {
    private lateinit var server: MockWebServer
    private var token: String? = "household-abc"
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient({ server.url("/").toString().trimEnd('/') }, { token })
    }

    @After fun down() = server.shutdown()

    @Test fun everyRequestCarriesTheToken() = runBlocking {
        repeat(4) { server.enqueue(MockResponse().setBody("{}")) }
        api.get("/api/music/library")
        api.post("/api/music/queue", null)
        api.patch("/api/devices/x", null)
        api.delete("/api/music/queue/1")
        repeat(4) {
            assertEquals("household-abc", server.takeRequest().getHeader(DEVICE_TOKEN_HEADER))
        }
    }

    @Test fun mediaAndImageLoadsOnTheSharedClientCarryItToo() {
        // media3's OkHttpDataSource and Coil are handed api.http, so a plain
        // call through that client must be authenticated the same way.
        server.enqueue(MockResponse().setBody("audio"))
        val req = okhttp3.Request.Builder().url(server.url("/api/music/library/7/audio")).build()
        api.http.newCall(req).execute().use { it.body?.string() }
        assertEquals("household-abc", server.takeRequest().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun bothWebSocketUpgradesCarryIt() {
        val ws = api.wsRequest("ws://host:6370/ws/state")
        val dropin = api.wsRequest("ws://host:6370/v1/dropin/kitchen?phone_id=android-1")
        assertEquals("household-abc", ws.header(DEVICE_TOKEN_HEADER))
        assertEquals("household-abc", dropin.header(DEVICE_TOKEN_HEADER))
    }

    @Test fun anUnpairedPhoneSendsNoHeaderAtAll() = runBlocking {
        token = null
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull(server.takeRequest().getHeader(DEVICE_TOKEN_HEADER))
        assertNull(api.wsRequest("ws://host/ws/state").header(DEVICE_TOKEN_HEADER))

        token = "   "
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull("a blank token is not a token", server.takeRequest().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun pairingTakesEffectOnTheNextRequestWithoutARestart() = runBlocking {
        token = null
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertNull(server.takeRequest().getHeader(DEVICE_TOKEN_HEADER))

        token = "paired-now"
        server.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertEquals("paired-now", server.takeRequest().getHeader(DEVICE_TOKEN_HEADER))
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

    @Test fun refusalClassificationIsAboutTheTierNotTheStatus() {
        assertTrue(isDeviceTokenRefusal(401, """{"detail":"X-Device-Token or admin session required"}"""))
        assertTrue(isDeviceTokenRefusal(403, """{"detail":"device token required"}"""))
        assertFalse(isDeviceTokenRefusal(401, """{"detail":"admin session required"}"""))
        assertFalse(isDeviceTokenRefusal(500, """{"detail":"X-Device-Token"}"""))
        assertFalse(isDeviceTokenRefusal(401, null))
    }
}
