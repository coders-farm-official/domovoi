package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.ZoneId

/**
 * Home's calls against a fake web backend: the paths and bodies the web page
 * sends (web/static/home.jsx), the household token riding along, and how a
 * refusal or a race reads in the toast.
 */
class HomeApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(
            baseUrlProvider = { server.url("/").toString().trimEnd('/') },
            deviceTokenProvider = { "household-token-1234" },
        )
    }

    @After fun down() = server.shutdown()

    private fun body(r: RecordedRequest) = Json.parseToJsonElement(r.body.readUtf8()).jsonObject

    @Test fun readsDecodeWhatTheServerSendsAndIgnoreTheRest() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"bot_name":"Hearth","tts_voice":"x","rooms":[],"web_version":"1","wake_word_min_clips":3,"home_problems_visibility":"summary"}"""))
        assertEquals(HomeConfig("Hearth", "summary"), HomeApi.config(api))
        assertEquals("/api/config", server.takeRequest().path)

        server.enqueue(MockResponse().setBody("""{"status":"degraded","db_reachable":true,"domovoi_reachable":false,"stt":null}"""))
        assertEquals(HomeHealth("degraded", true, false, null), HomeApi.health(api))
        assertEquals("/api/health", server.takeRequest().path)

        server.enqueue(
            MockResponse().setBody(
                """[{"room_id":"kitchen","status":"online","sat_type":"video","room_label":"Downstairs",
                   "display":{"on":true,"kiosk_alive":false,"brightness":null},"pairing":{"paired":true},
                   "now_playing":{"state":"play","song":{"title":"Creep","artist":"Radiohead","duration_sec":238.5},"elapsed_sec":12},
                   "wifi":{"rx_mbits":39.0,"tx_mbits":72.2,"ssid":"hidden"},"in_call_with":null,"mic_enabled":true}]""",
            ),
        )
        val kitchen = HomeApi.rooms(api).single()
        assertEquals("video", kitchen.sat_type)
        assertEquals("Downstairs", kitchen.room_label)
        assertEquals(false, kitchen.display?.kiosk_alive)
        assertEquals(12.0, kitchen.now_playing?.elapsed_sec)
        assertEquals("/api/satellites", server.takeRequest().path)
    }

    @Test fun timersCarryTheServersClock() = runBlocking {
        server.enqueue(
            MockResponse().setBody(
                """{"server_now":"2026-09-28T12:00:00.123456+00:00","timers":[
                   {"id":7,"expires_at":"2026-09-28T12:10:00+00:00","created_at":"2026-09-28T12:00:00+00:00","label":"pasta","message":null,"room_id":"kitchen","is_reminder":false},
                   {"id":8,"expires_at":"2026-09-28T13:00:00+00:00","created_at":"2026-09-28T12:00:00+00:00","label":null,"message":null,"room_id":null,"is_reminder":true}]}""",
            ),
        )
        val list = HomeApi.timers(api)
        assertEquals("/api/timers", server.takeRequest().path)
        assertEquals(listOf(7L, 8L), list.timers.map { it.id })
        assertEquals(isoMs("2026-09-28T12:00:00.123Z"), isoMs(list.server_now))
        // A roomless reminder read without the token comes back wordless.
        assertEquals("reminder", timerTitle(list.timers[1], shared = false))
    }

    @Test fun cancelIsADeleteByIdAndSaysWhatHappened() = runBlocking {
        val pasta = HomeTimer(7, label = "pasta", room_id = "kitchen")
        server.enqueue(MockResponse().setResponseCode(204))
        assertEquals(CancelOutcome.Cancelled("cancelled pasta timer"), HomeApi.cancelTimer(api, pasta, shared = false))
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/api/timers/7", req.path)
        assertEquals("household-token-1234", req.getHeader("X-Device-Token"))

        // It fired between the read and the tap.
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"timer 7 not found"}"""))
        assertEquals(CancelOutcome.Gone("that one already finished"), HomeApi.cancelTimer(api, pasta, false))

        server.enqueue(MockResponse().setResponseCode(500).setBody("boom"))
        val failed = HomeApi.cancelTimer(api, pasta, false)
        assertTrue(failed is CancelOutcome.Failed)
        assertTrue(failed.toast, failed.toast.startsWith("cancel failed: 500"))

        // A reminder's words stay off the toast on a shared screen, and off
        // it anywhere: a reminder is "reminder".
        server.enqueue(MockResponse().setResponseCode(204))
        val r = HomeTimer(9, message = "call mom", room_id = "office", is_reminder = true)
        assertEquals("cancelled reminder", HomeApi.cancelTimer(api, r, shared = true).toast)
    }

    @Test fun roomTransportUsesTheMusicEndpoints() = runBlocking {
        server.enqueue(MockResponse().setBody("{}"))
        HomeApi.roomAction(api, "living room", "pause")
        val r = server.takeRequest()
        assertEquals("POST", r.method)
        assertEquals("/api/music/pause/living%20room", r.path)
        assertEquals("DomovoiApp", r.getHeader("X-Requested-With"))
    }

    @Test fun stopAllStopsEveryRoomAndReportsAPartialFailure() = runBlocking {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/api/music/stop/garage" -> MockResponse().setResponseCode(502).setStatus("HTTP/1.1 502 Bad Gateway")
                    .setBody("""{"detail":"mpd unreachable"}""")
                else -> MockResponse().setBody("{}")
            }
        }
        val out = HomeApi.stopRooms(api, listOf("kitchen", "garage", "office"))
        assertEquals(listOf("kitchen", "office"), out.stopped)
        assertEquals(listOf("garage"), out.failed.map { it.first })
        val paths = (1..3).map { server.takeRequest().path }.toSet()
        assertEquals(setOf("/api/music/stop/kitchen", "/api/music/stop/garage", "/api/music/stop/office"), paths)
        assertEquals(
            """stopped 2 of 3 rooms · stop failed: 502 Bad Gateway: {"detail":"mpd unreachable"}""",
            stopAllToast(3, out),
        )
        assertEquals("stopped 2 rooms", stopAllToast(2, StopOutcome(listOf("a", "b"), emptyList())))
        assertEquals(
            "stop failed (offline?)",
            stopAllToast(1, StopOutcome(emptyList(), listOf("a" to java.io.IOException("reset")))),
        )
    }

    @Test fun announceSpeaksInEveryRoomAndReadsWhoHeardIt() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"announced_to":["kitchen","office"]}"""))
        val heard = HomeApi.announceAll(api, "dinner's ready")
        val r = server.takeRequest()
        assertEquals("/api/satellites/announce-all", r.path)
        assertEquals("dinner's ready", body(r)["message"]!!.jsonPrimitive.content)
        assertEquals(listOf("kitchen", "office"), heard)
        assertEquals("broadcasted to 2 satellites", announceToast(heard, 2))
        assertEquals("broadcast partial — 2/3 reached (kitchen, office)", announceToast(heard, 3))
        assertEquals("broadcasted to 1 satellite", announceToast(listOf("kitchen"), 1))
        assertEquals("broadcast queued but no satellites accepted it (dead connections?)", announceToast(emptyList(), 2))
    }

    @Test fun calendarAsksFromLocalMidnight() = runBlocking {
        val zone = ZoneId.of("UTC")
        server.enqueue(MockResponse().setBody("""[{"id":1,"title":"Dentist","starts_at":"2026-09-28T15:00:00+00:00","description":"private"}]"""))
        val day = dayStartMs(isoMs("2026-09-28T19:00:00Z")!!, zone)
        val events = HomeApi.calendar(api, day, zone)
        assertEquals(
            "/api/calendar/events?start=2026-09-28T00%3A00%3A00Z&end=2026-10-05T00%3A00%3A00Z&limit=20",
            server.takeRequest().path,
        )
        assertEquals("Dentist", events.single().title)
    }

    @Test fun pluginsAndMediaRequestsFeedTheProblemRows() = runBlocking {
        server.enqueue(
            MockResponse().setBody(
                """{"plugins":[{"slug":"radio","name":"Radio","enabled":true,"status":"active","page_errors":[],"web_load_error":null,"permissions":{}},
                               {"slug":"sleep","name":"Sleep","enabled":true,"status":"load_error","last_error":"ImportError"}]}""",
            ),
        )
        server.enqueue(
            MockResponse().setBody(
                """{"acquisitions":[{"id":1,"kind":"query","text":"secret song","status":"pending"}],
                   "can_fulfill_query":false,"can_fulfill_url":null,"core_reachable":true}""",
            ),
        )
        val plugins = HomeApi.plugins(api)
        val acq = HomeApi.acquisitions(api)
        assertEquals("/api/plugins", server.takeRequest().path)
        assertEquals("/api/acquisitions?status=pending&limit=100", server.takeRequest().path)
        val rows = attentionRows(HomeHealth(db_reachable = true, domovoi_reachable = true), emptyList(), plugins, acq)
        assertEquals(listOf("the Sleep plugin failed to load", "a media request is waiting · no provider plugin can fill it"), rows.map { it.text })
    }
}
