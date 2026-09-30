package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * A8, the satellite half: the roster's `timers_own_only` (absent from an
 * older server = OFF), and the "Only reminders for this device" switch's
 * PUT — path, body, household token, CSRF header — its exact words, and the
 * failure toast an older server's 404 gets.
 */
class SatTimerScopeTest {
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

    @Test fun theRosterFlagDefaultsToOffWhenAbsent() {
        val rows = DomovoiJson.parseToJsonElement(
            """[{"room_id":"garage","status":"online"},{"room_id":"office","status":"offline","timers_own_only":true}]""",
        ).decode<List<Satellite>>()
        assertFalse(rows[0].timers_own_only)
        assertTrue(rows[1].timers_own_only)
    }

    @Test fun theSwitchPutsOwnOnlyForItsRoom() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"room_id":"garage","own_only":true,"since":"2026-09-30T15:00:00Z"}"""))
        putTimerScope(api, "garage", true)
        val r = server.takeRequest()
        assertEquals("PUT", r.method)
        assertEquals("/api/satellites/garage/timer-announcements", r.path)
        assertEquals("""{"own_only":true}""", r.body.readUtf8())
        assertEquals("household-token-1234", r.getHeader("X-Device-Token"))
        assertEquals("DomovoiApp", r.getHeader("X-Requested-With"))

        server.enqueue(MockResponse().setBody("""{"room_id":"garage","own_only":false,"since":null}"""))
        putTimerScope(api, "garage", false)
        assertEquals("""{"own_only":false}""", server.takeRequest().body.readUtf8())
    }

    @Test fun anOlderServersRefusalReadsAsAFailure() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        try {
            putTimerScope(api, "garage", true)
            fail("a 404 must throw")
        } catch (e: ApiException) {
            assertEquals(404, e.status)
            assertTrue(failureText("change timer announcements", e).startsWith("change timer announcements failed: 404"))
        }
    }

    @Test fun aMaskedReminderReadsWordsHiddenLikeTheWeb() {
        val rows = DomovoiJson.parseToJsonElement(
            """[{"id":7,"is_reminder":true,"label":null,"message":null,"masked":true},
                {"id":8,"is_reminder":true,"label":"call mom","message":"call mom"},
                {"id":9,"is_reminder":false,"label":"pasta"},
                {"id":10,"is_reminder":false}]""",
        ).decode<List<SatTimer>>()
        assertEquals(
            listOf("reminder (words hidden)", "call mom", "pasta", "—"),
            rows.map { satTimerLabel(it) },
        )
        assertFalse("absent from an older server = not masked", rows[1].masked)
    }

    @Test fun theWordsAreTheWebsWords() {
        assertEquals("Only reminders for this device", TIMER_SCOPE_LABEL)
        assertEquals(
            "Covers timers and reminders. Off: this satellite also announces the ones set in other rooms and says " +
                "which room they came from. On: it announces only the ones set on this satellite. The room a timer " +
                "or reminder was set in always announces it.",
            TIMER_SCOPE_HELP,
        )
        assertEquals("garage now announces only its own timers and reminders", timerScopeToast("garage", true))
        assertEquals("garage now announces timers and reminders from every room", timerScopeToast("garage", false))
    }
}
