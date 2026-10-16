package com.domovoi.app.net

import com.domovoi.app.testing.Bytecode
import kotlinx.coroutines.runBlocking
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.util.concurrent.TimeUnit

/**
 * While this phone is a room (roommode/), the household token goes on NO
 * request, whoever builds it — a JSON call, a WebSocket upgrade that set
 * the header itself, a save-to-device download — and comes back the moment
 * room mode ends (security review of wf/room-mode, F8: the gate had no
 * test).
 */
class HouseholdLockTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient({ server.url("/").toString().trimEnd('/') }, { "household-abc" })
    }

    @After fun down() {
        HouseholdLock.roomModeActive = false
        server.shutdown()
    }

    private fun MockWebServer.next(): RecordedRequest =
        takeRequest(5, TimeUnit.SECONDS) ?: error("no request reached the server")

    @Test fun inRoomModeNoRequestCarriesTheHouseholdToken() = runBlocking {
        repeat(3) { server.enqueue(MockResponse().setBody("{}")) }
        api.get("/api/music/library")
        assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))

        HouseholdLock.roomModeActive = true
        api.get("/api/music/library")
        assertNull(server.next().getHeader(DEVICE_TOKEN_HEADER))
        assertNull(api.tokenForDownload(server.url("/api/videos/1/stream")))

        HouseholdLock.roomModeActive = false
        api.get("/api/music/library")
        assertEquals("household-abc", server.next().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun anUpgradeThatSetTheHeaderItselfLosesItInRoomMode() {
        HouseholdLock.roomModeActive = true
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
        val url = server.url("/ws/state").toString().replaceFirst("http://", "ws://")
        val ws = api.http.newWebSocket(api.wsRequest(url), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {}
        })
        val upgrade = server.next()
        assertNull(upgrade.getHeader(DEVICE_TOKEN_HEADER))
        ws.cancel()
    }

    @Test fun theProcessSetsTheLockBeforeAnythingCanMakeARequest() {
        // DomovoiApplication.onCreate sets it from the room's store before the
        // app's container (and its client) exists; the room's store sets it
        // whenever room mode starts or ends.
        val app = Bytecode.callers("com.domovoi.app.DomovoiApplication", "com/domovoi/app/net/HouseholdLock", setOf("setRoomModeActive"))
        assertTrue(app.toString(), app.any { it.method == "onCreate" })
        val store = Bytecode.callers("com.domovoi.app.roommode.RoomStore", "com/domovoi/app/net/HouseholdLock", setOf("setRoomModeActive"))
        assertTrue(store.toString(), store.any { it.method == "setActive" })
    }
}
