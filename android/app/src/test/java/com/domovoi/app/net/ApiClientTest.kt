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
        api = ApiClient { server.url("/").toString().trimEnd('/') }
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

    @Test fun noServerConfigured_failsBeforeAnyRequest() = runBlocking {
        val blank = ApiClient { "" }
        try {
            blank.get("/api/health")
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("no server configured", e.message)
        }
        assertEquals(0, server.requestCount)
    }
}
