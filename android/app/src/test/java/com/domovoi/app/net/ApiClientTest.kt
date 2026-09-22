package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.IOException

/**
 * The JSON client's contract with the web backend, the same one
 * web/static/data.js keeps: non-2xx throws "{status} {reason}: {body}".
 */
class ApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
    }

    @After fun down() = server.shutdown()

    @Test fun absolute_prefixesRelativePathsOnly() {
        assertEquals("${api.baseUrl}/api/health", api.absolute("/api/health"))
        assertEquals("https://x.example/a.mp3", api.absolute("https://x.example/a.mp3"))
    }

    @Test fun get_parsesJson() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true,"bot_name":"domovoi"}"""))
        val el = api.get("/api/health")
        assertEquals("domovoi", el.jsonObject["bot_name"]!!.jsonPrimitive.content)
        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("/api/health", req.path)
    }

    @Test fun emptyBodyIsJsonNull() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(204))
        assertEquals(JsonNull, api.delete("/api/things/1"))
        assertEquals("DELETE", server.takeRequest().method)
    }

    @Test fun post_sendsJsonBodyAndDefaultsToEmptyObject() = runBlocking {
        server.enqueue(MockResponse().setBody("{}"))
        server.enqueue(MockResponse().setBody("{}"))
        api.post("/api/a", buildJsonObject { put("room_id", "kitchen") })
        val r1 = server.takeRequest()
        assertEquals("POST", r1.method)
        assertTrue(r1.getHeader("Content-Type")!!.startsWith("application/json"))
        assertEquals("""{"room_id":"kitchen"}""", r1.body.readUtf8())
        api.post("/api/b")
        assertEquals("{}", server.takeRequest().body.readUtf8())
    }

    @Test fun non2xx_throwsApiExceptionWithStatusReasonAndBody() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(502).setStatus("HTTP/1.1 502 Bad Gateway")
                .setBody("""{"detail":"core unreachable"}"""),
        )
        try {
            api.get("/api/rooms")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(502, e.status)
            assertEquals("""502 Bad Gateway: {"detail":"core unreachable"}""", e.message)
        }
    }

    @Test fun errorBodyIsTruncatedTo200Chars() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("x".repeat(1000)))
        try {
            api.get("/boom")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals("500 Server Error: " + "x".repeat(200), e.message)
        }
    }

    // ---- failureText (F-A001) ----------------------------------------------

    @Test fun failureText_reportsTheServerErrorNotOffline() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}"""))
        try {
            api.post("/api/podcasts/poll")
            fail("expected ApiException")
        } catch (e: ApiException) {
            val msg = failureText("Poll", e)
            assertEquals("""Poll failed: 500 Server Error: {"detail":"boom"}""", msg)
            assertFalse(msg.contains("offline"))
        }
    }

    @Test fun failureText_blamesTheConnectionOnlyForTransportFailures() {
        assertEquals(
            "Poll failed (offline?)",
            failureText("Poll", IOException("Failed to connect to /10.0.0.9:6370")),
        )
    }

    // ---- F-A009: subscribe / discover / news mutations use the same shape ----

    @Test fun failureText_namesTheServerReasonForSubscribeAndFeedFailures() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(422).setStatus("HTTP/1.1 422 Unprocessable Entity")
                .setBody("""{"detail":"not a feed"}"""),
        )
        try {
            api.post("/api/podcasts/subscriptions")
            fail("expected ApiException")
        } catch (e: ApiException) {
            // POD-09: the toast carries the status and the server's own detail.
            assertEquals("""Subscribe failed: 422 Unprocessable Entity: {"detail":"not a feed"}""", failureText("Subscribe", e))
            // NEWS-08: same exception, news wording.
            assertEquals("""add feed failed: 422 Unprocessable Entity: {"detail":"not a feed"}""", failureText("add feed", e))
        }
    }

    @Test fun failureText_discoveryBlamesTheConnectionOnlyWhenTheRequestNeverArrived() {
        assertEquals("Discovery failed (offline?)", failureText("Discovery", IOException("timeout")))
        assertEquals(
            "Discovery failed: 502 Bad Gateway: upstream itunes lookup failed",
            failureText("Discovery", ApiException(502, "502 Bad Gateway: upstream itunes lookup failed")),
        )
    }

    @Test fun failureText_fallsBackWhenThereIsNoMessage() {
        assertEquals("Poll failed: HTTP 502", failureText("Poll", ApiException(502, "")))
        assertEquals(
            "Poll failed: IllegalStateException",
            failureText("Poll", IllegalStateException()),
        )
    }

    @Test fun noServerConfigured_failsBeforeAnyRequest() = runBlocking {
        val blank = ApiClient(baseUrlProvider = { "" })
        try {
            blank.get("/api/health")
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("no server configured", e.message)
        }
        assertEquals(0, server.requestCount)
    }
}
