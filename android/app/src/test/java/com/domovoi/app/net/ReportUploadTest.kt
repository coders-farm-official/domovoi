package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * "Send to coders farm" (assistant/ReplyReport.kt HttpsReportSink): the POST
 * itself. https only; never the household token, even when the household's
 * server and the address are the same host; a redirect is not "received"
 * and is not followed; only a 2xx is a yes. MockWebServer speaks plain http,
 * so these use the test-only constructor; the https rule has its own test.
 */
class ReportUploadTest {
    private lateinit var server: MockWebServer

    @Before fun up() { server = MockWebServer().also { it.start() } }

    @After fun down() = server.shutdown()

    /** The app's own client, paired with a server at the mock's address. */
    private fun appClient(): ApiClient {
        val base = server.url("/").toString().trimEnd('/')
        return ApiClient(baseUrlProvider = { base }, deviceTokenProvider = { "household-secret" })
    }

    private val json = """{"kind":"domovoi-reply-report","version":1,"report":{"answer":"x"}}"""

    @Test fun aReportIsPostedAsJsonWithoutTheHouseholdToken() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(201))
        val up = ReportUpload(appClient().http, allowHttpForTests = true)
        assertTrue(up.post(server.url("/api/domovoi/reports").toString(), json))
        val req = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("POST", req.method)
        assertEquals("/api/domovoi/reports", req.path)
        assertEquals(json, req.body.readUtf8())
        assertTrue(req.getHeader("Content-Type")!!.startsWith("application/json"))
        assertNull("the household token never goes with a report", req.getHeader("X-Device-Token"))
    }

    @Test fun onlyA2xxIsAYes() = runBlocking {
        val up = ReportUpload(appClient().http, allowHttpForTests = true)
        val url = server.url("/r").toString()
        server.enqueue(MockResponse().setResponseCode(500))
        assertFalse(up.post(url, json))
        server.enqueue(MockResponse().setResponseCode(404))
        assertFalse(up.post(url, json))
        server.enqueue(MockResponse().setResponseCode(200))
        assertTrue(up.post(url, json))
    }

    @Test fun aRedirectIsNotFollowedAndNotAYes() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(307).setHeader("Location", server.url("/elsewhere").toString()))
        val up = ReportUpload(appClient().http, allowHttpForTests = true)
        assertFalse(up.post(server.url("/r").toString(), json))
        assertEquals(1, server.requestCount)
    }

    @Test fun nothingAnsweringIsANo() = runBlocking {
        val url = server.url("/r").toString()
        server.shutdown()
        assertFalse(ReportUpload(appClient().http, allowHttpForTests = true).post(url, json))
    }

    /** The shipping constructor refuses anything but https, before any
     *  connection is made. */
    @Test fun theAppsUploaderIsHttpsOnly() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200))
        assertFalse(ReportUpload(appClient().http).post(server.url("/r").toString(), json))
        assertEquals(0, server.requestCount)
    }
}
