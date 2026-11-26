package com.domovoi.app.ui.screens.settings

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Settings → Privacy → Keep voice history (KeepVoiceHistoryPanel.kt):
 *  the value from GET /api/voice-history, and the dashboard link only for a
 *  server whose dashboard has the page (1.5.0 and later). */
class KeepVoiceHistoryTest {
    private val body = """{"keep":"90","keep_label":"90 days","keep_days":90,
        "choices":[{"value":"30","label":"30 days"},{"value":"90","label":"90 days"},
                   {"value":"365","label":"1 year"},{"value":"forever","label":"Forever"}],
        "turns":4120,"oldest_at":"2026-08-03T10:00:00Z","delete_all":null,"last_cleanup":null}"""

    @Test fun theValueIsTheServersWords() {
        assertEquals("90 days", KeepVoiceHistory.label(Json.parseToJsonElement(body)))
        // No keep_label: the choice's label, else the value itself.
        assertEquals("1 year", KeepVoiceHistory.label(Json.parseToJsonElement(
            """{"keep":"365","choices":[{"value":"365","label":"1 year"}]}""")))
        assertEquals("forever", KeepVoiceHistory.label(Json.parseToJsonElement("""{"keep":"forever"}""")))
        // Nothing to say: no value.
        assertNull(KeepVoiceHistory.label(Json.parseToJsonElement("""{"turns":3}""")))
        assertNull(KeepVoiceHistory.label(Json.parseToJsonElement("[]")))
        assertNull(KeepVoiceHistory.label(null))
    }

    @Test fun onlyA150DashboardHasThePage() {
        listOf("1.5.0", "1.5.0 + 14 (abc1234)", "1.5.2", "1.10.0", "2.0.0").forEach {
            assertTrue(it, KeepVoiceHistory.dashboardHasPage(it))
        }
        // An older dashboard, a pre-release main house still labelled after
        // 1.4.1, a label that is no version, or none: value only.
        listOf("1.4.1", "1.4.1 + 128 (3a606cd)", "0.9.0 (abc1234)", "abc1234", "", null, "1.45").forEach {
            assertFalse("$it", KeepVoiceHistory.dashboardHasPage(it))
        }
    }

    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') }, deviceTokenProvider = { "household-token-1234" })
    }

    @After fun down() = server.shutdown()

    private fun answer(voice: MockResponse, version: MockResponse) {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/api/voice-history" -> voice
                "/api/config/version" -> version
                else -> MockResponse().setResponseCode(404)
            }
        }
    }

    @Test fun aNewServerGetsTheValueAndTheLink() = runBlocking {
        answer(MockResponse().setBody(body), MockResponse().setBody("""{"version":"1.5.0","sha":"x"}"""))
        assertEquals(KeepVoiceHistory.Read("90 days", linkable = true), KeepVoiceHistory.read(api))
        val asked = (1..server.requestCount).map { server.takeRequest() }
        assertTrue(asked.all { it.getHeader("X-Device-Token") == "household-token-1234" })
    }

    @Test fun anOlderServerGetsTheValueOnly() = runBlocking {
        answer(MockResponse().setBody(body), MockResponse().setBody("""{"version":"1.4.1"}"""))
        assertEquals(KeepVoiceHistory.Read("90 days", linkable = false), KeepVoiceHistory.read(api))
        // A version read that fails is no link either.
        answer(MockResponse().setBody(body), MockResponse().setResponseCode(401))
        assertEquals(KeepVoiceHistory.Read("90 days", linkable = false), KeepVoiceHistory.read(api))
    }

    @Test fun aRefusalOrNoRouteIsNoValueAndNoAnswerThrows() = runBlocking {
        answer(MockResponse().setResponseCode(401), MockResponse().setBody("""{"version":"1.5.0"}"""))
        assertNull(KeepVoiceHistory.read(api).label)
        answer(MockResponse().setResponseCode(404), MockResponse().setBody("""{"version":"1.5.0"}"""))
        assertNull(KeepVoiceHistory.read(api).label)
        answer(MockResponse().setBody("<html>not the dashboard</html>"), MockResponse().setBody("""{"version":"1.5.0"}"""))
        assertNull(KeepVoiceHistory.read(api).label)
        answer(MockResponse().setResponseCode(502), MockResponse().setBody("""{"version":"1.5.0"}"""))
        assertTrue(runCatching { KeepVoiceHistory.read(api) }.exceptionOrNull() is ApiException)
    }
}
