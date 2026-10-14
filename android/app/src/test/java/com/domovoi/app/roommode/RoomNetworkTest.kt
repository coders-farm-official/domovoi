package com.domovoi.app.roommode

import com.domovoi.app.net.TokenScope
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * The room talks to the one core it proved, and nowhere else: its client
 * follows no redirect, HTTP or WebSocket (security review of wf/room-mode,
 * F1 — before this, a 302 on the stream upgrade handed the hello, with the
 * room's pairing key, to whatever host `Location` named, and then the mic).
 */
class RoomNetworkTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    private lateinit var core: MockWebServer
    private lateinit var elsewhere: MockWebServer
    private val heardElsewhere = LinkedBlockingQueue<String>()

    @Before
    fun setUp() {
        elsewhere = MockWebServer()
        elsewhere.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                heardElsewhere.put(text)
            }
        }))
        elsewhere.start()
        core = MockWebServer()
        core.start()
    }

    @After
    fun tearDown() {
        core.shutdown()
        elsewhere.shutdown()
    }

    @Test
    fun aRedirectedStreamUpgradeFailsAndTheHelloGoesNowhere() {
        core.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://127.0.0.1:${elsewhere.port}/somewhere-else"))
        val url = RoomProtocol.streamUrl("http://127.0.0.1:${core.port}", "kitchen")
        val hello = RoomProtocol.hello("kitchen", "hey_jarvis", "ROOM-PAIRING-KEY", "123456", echoCanceller = false)
        val failed = CountDownLatch(1)
        var failure: Response? = null
        RoomNetwork.BASE.newWebSocket(RoomNetwork.streamRequest(url), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(hello.toString())
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                failure = response
                failed.countDown()
            }
        })
        assertNotNull("the core saw the upgrade", core.takeRequest(5, TimeUnit.SECONDS))
        assertTrue("the upgrade must fail on a 302", failed.await(10, TimeUnit.SECONDS))
        assertEquals(302, failure?.code)
        assertNull("nothing reached the other host", elsewhere.takeRequest(2, TimeUnit.SECONDS))
        assertNull(heardElsewhere.poll(1, TimeUnit.SECONDS))
    }

    @Test
    fun aRedirectedReadIsAnErrorNotAHop() {
        core.enqueue(MockResponse().setResponseCode(301).setHeader("Location", "http://127.0.0.1:${elsewhere.port}/v1/health"))
        assertThrows(IOException::class.java) {
            RoomModels.get(RoomNetwork.BASE, "http://127.0.0.1:${core.port}/v1/health?challenge=x")
        }
        assertNull(elsewhere.takeRequest(2, TimeUnit.SECONDS))
    }

    @Test
    fun theClientHasNoRedirectFollowerAndTheUpgradeIsTagged() {
        assertTrue(!RoomNetwork.BASE.followRedirects && !RoomNetwork.BASE.followSslRedirects)
        assertTrue(RoomNetwork.BASE.interceptors.none { it === com.domovoi.app.net.RedirectPolicy.interceptor })
        val r = RoomNetwork.streamRequest("ws://192.168.0.240:6370/v1/stream/kitchen")
        assertNotNull(r.tag(TokenScope.WsUpgrade::class.java))
    }
}
