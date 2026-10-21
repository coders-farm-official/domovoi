package com.domovoi.app.update

import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Where the app learns what its server offers: the dashboard's open
 * `GET /api/devices/android-app` (web/backend/api/devices.py), asked at
 * most every half hour per server; and, for a room, the core's
 * `GET /v1/android-app` (domovoi/android_app.py public_status).
 */
class AppUpdatesTest {
    private lateinit var server: MockWebServer

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        AppUpdates.clear()
    }

    @After fun down() {
        server.shutdown()
        AppUpdates.clear()
    }

    /** The dashboard's answer, as web/backend/api/devices.py makes it. */
    private val webAnswer = """
        {"join_url": "http://192.168.1.20:6370/join", "join_problem": null,
         "release": {"version_name": "1.0.1", "version_code": 26110101,
                     "cert_sha256": "${"ab".repeat(32)}"},
         "apks": [
           {"abi": "arm64-v8a", "file": "domovoi-1.0.1-arm64-v8a.apk", "size": 79996285, "ready": true,
            "path": "/v1/android-app/files/domovoi-1.0.1-arm64-v8a.apk"},
           {"abi": "armeabi-v7a", "file": "domovoi-1.0.1-armeabi-v7a.apk", "size": 48518461, "ready": false,
            "path": "/v1/android-app/files/domovoi-1.0.1-armeabi-v7a.apk"}],
         "ready": false}
    """.trimIndent()

    private fun ok(body: String) = MockResponse().setBody(body).setHeader("Content-Type", "application/json")

    @Test fun theDashboardsAnswerIsReadAndKeptForHalfAnHour() = runBlocking {
        val base = server.url("/").toString().trimEnd('/')
        val api = ApiClient(baseUrlProvider = { base })
        var clock = 1_000_000L
        server.enqueue(ok(webAnswer))
        val offer = AppUpdates.offer(api, base, now = { clock })!!
        assertEquals("/api/devices/android-app", server.takeRequest().path)
        assertEquals("http://192.168.1.20:6370/join", offer.joinUrl)
        assertEquals(OfferedRelease("1.0.1", 26110101, "ab".repeat(32)), offer.release)
        assertEquals(listOf(true, false), offer.apks.map { it.ready })

        clock += AppUpdates.FRESH_MS - 1
        assertEquals(offer, AppUpdates.offer(api, base, now = { clock }))
        assertEquals("asked once", 1, server.requestCount)

        clock += 1
        server.enqueue(ok(webAnswer.replace("26110101", "26110201")))
        assertEquals(26110201L, AppUpdates.offer(api, base, now = { clock })!!.release!!.versionCode)
        assertEquals(2, server.requestCount)
        // Settings → About & updates asks afresh.
        server.enqueue(ok(webAnswer))
        AppUpdates.offer(api, base, maxAgeMs = 0, now = { clock })
        assertEquals(3, server.requestCount)
    }

    @Test fun anOlderServerOrAnErrorIsNoAnswerAskedAgainSooner() = runBlocking {
        val base = server.url("/").toString().trimEnd('/')
        val api = ApiClient(baseUrlProvider = { base })
        var clock = 1_000_000L
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail": "Not Found"}"""))
        assertNull(AppUpdates.offer(api, base, now = { clock }))
        clock += AppUpdates.RETRY_MS - 1
        assertNull(AppUpdates.offer(api, base, now = { clock }))
        assertEquals(1, server.requestCount)
        clock += 1
        server.enqueue(ok("not json"))
        assertNull(AppUpdates.offer(api, base, now = { clock }))
        assertEquals(2, server.requestCount)
    }

    @Test fun noServerIsNoQuestion() = runBlocking {
        assertNull(AppUpdates.offer(ApiClient(baseUrlProvider = { "" }), ""))
        assertEquals(0, server.requestCount)
    }

    @Test fun eachServerIsAskedForItself() = runBlocking {
        val base = server.url("/").toString().trimEnd('/')
        val api = ApiClient(baseUrlProvider = { base })
        server.enqueue(ok(webAnswer))
        server.enqueue(ok("""{"release": null, "apks": [], "ready": false}"""))
        AppUpdates.offer(api, base)
        val other = AppUpdates.offer(api, "$base/other")
        assertNull(other!!.release)
        assertEquals(2, server.requestCount)
    }

    @Test fun theCoresAnswerGetsTheCoresJoinPage() {
        val core = """
            {"release": {"version_name": "1.0.1", "version_code": 26110101, "cert_sha256": "${"ab".repeat(32)}"},
             "apks": [{"abi": "armeabi-v7a", "file": "domovoi-1.0.1-armeabi-v7a.apk", "size": 1, "ready": true,
                       "path": "/v1/android-app/files/domovoi-1.0.1-armeabi-v7a.apk"}],
             "ready": true}
        """.trimIndent()
        val offer = AppUpdates.fromCore("http://192.168.1.20:6370", core)!!
        assertEquals("http://192.168.1.20:6370/join", offer.joinUrl)
        val install = ThisInstall(26101501, "1.0.0-rc1", false, null, setOf("ab".repeat(32)), listOf("armeabi-v7a"))
        val state = AppUpdate.decide(install, offer)
        assertEquals("http://192.168.1.20:6370/join?abi=armeabi-v7a&update=1", (state as UpdateState.Available).link)
        assertNull(AppUpdates.fromCore("http://192.168.1.20:6370", "<html>"))
        assertTrue(AppUpdates.fromCore("http://192.168.1.20:6370/", """{"release": null}""")!!.joinUrl!!.endsWith(":6370/join"))
    }
}
