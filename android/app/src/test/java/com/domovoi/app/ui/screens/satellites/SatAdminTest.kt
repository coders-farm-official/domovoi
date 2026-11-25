package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
import java.util.Collections

/** A room as admin on this phone: when its controls are offered, what they
 *  say, the settings editor's rules, and the three routes. */
class SatAdminTest {
    private val server = "http://192.168.1.20:6369"

    @Test fun theControlsAreThereOnlyWithAdminAndNeverOnASharedScreen() {
        val signedIn = AdminSession.State.SignedIn(server, null)
        val kept = AdminSession.State.Kept(server, 1L)
        assertTrue(roomAdminHeld(signedIn, shared = false))
        assertTrue("kept today: the tap asks for the fingerprint first", roomAdminHeld(kept, shared = false))
        assertFalse(roomAdminHeld(AdminSession.State.SignedOut, shared = false))
        assertFalse(roomAdminHeld(signedIn, shared = true))
        assertFalse(roomAdminHeld(kept, shared = true))
    }

    @Test fun whatADeviceDeclaredDecidesRestartAndUpgrade() {
        val pi = Satellite(room_id = "kitchen")
        assertTrue(satCan(pi, "restart") && satCan(pi, "code_sync"))
        val phone = Satellite(room_id = "den", supports = listOf("speech_pause", "room_display"))
        assertFalse(satCan(phone, "restart"))
        assertFalse(satCan(phone, "code_sync"))
        assertEquals("this device restarts and updates itself", RoomAdminWords.notOffered(false, false))
        assertEquals("this device updates itself", RoomAdminWords.notOffered(true, false))
        assertNull(RoomAdminWords.notOffered(true, true))
    }

    @Test fun theConfirmsAreTheDashboards() {
        assertEquals(
            "Restart the kitchen room? It'll drop offline for a few seconds while the service bounces.",
            RoomAdminWords.restartConfirm("kitchen"),
        )
        assertTrue(RoomAdminWords.upgradeConfirm("kitchen", "abc1234").startsWith("Upgrade the kitchen room to abc1234?\n\n"))
        assertTrue(RoomAdminWords.upgradeConfirm("kitchen", null).startsWith("Upgrade the kitchen room to the latest domovoi code?"))
        assertTrue(RoomAdminWords.upgradeConfirm("kitchen", null).contains("If the new code won't start, it rolls back automatically."))
        assertEquals(
            "Save and restart the kitchen room? It'll drop offline for a few seconds while it applies the change.",
            RoomAdminWords.saveConfirm("kitchen"),
        )
    }

    @Test fun signedOutTheDashboardLinesNameThePhoneToo() {
        assertTrue(RoomAdminWords.MAINTENANCE_OR_PHONE.startsWith(ROOM_MAINTENANCE_ON_DASHBOARD))
        assertTrue(RoomAdminWords.MAINTENANCE_OR_PHONE.endsWith(", or sign in as admin on this phone"))
        assertTrue(RoomAdminWords.CONFIG_OR_PHONE.startsWith(ROOM_CONFIG_READ_ONLY.removeSuffix(".")))
        assertTrue(RoomAdminWords.CONFIG_OR_PHONE.endsWith(", or sign in as admin on this phone."))
        assertTrue(com.domovoi.app.ui.screens.settings.ADMIN_JOBS.contains("restart, upgrade or change the settings of a room"))
    }

    private fun field(name: String, type: String, value: Any?, section: String? = null, min: Double? = null, max: Double? = null, supported: Boolean? = null) =
        SatConfigField(
            name = name, type = type, section = section, min = min, max = max, supported = supported,
            value = when (value) {
                null -> null
                is Boolean -> JsonPrimitive(value)
                is Number -> JsonPrimitive(value)
                else -> JsonPrimitive(value.toString())
            },
        )

    @Test fun onlyTheCommonSettingsTheRoomHasAreEditableHere() {
        assertTrue(field("wake.threshold", "float", 0.5).editableHere())
        assertTrue(field("audio.mute", "bool", false).editableHere())
        assertFalse("advanced stays on the dashboard", field("audio.device", "str", "hw:1", section = "advanced").editableHere())
        assertFalse("not on this room's software yet", field("x.y", "bool", null, supported = false).editableHere())
        assertFalse("a kind the phone doesn't draw", field("x.z", "secret", null).editableHere())
    }

    @Test fun numbersAreCheckedAsTheDashboardChecksThem() {
        val threshold = field("wake.threshold", "float", 0.5, min = 0.0, max = 1.0)
        val gain = field("mic.gain", "int", 4, min = 0.0, max = 10.0)
        assertNull(configEditProblem(threshold, JsonPrimitive("0.7")))
        assertEquals("must be at most 1", configEditProblem(threshold, JsonPrimitive("1.5")))
        assertEquals("must be at least 0", configEditProblem(threshold, JsonPrimitive("-0.1")))
        assertEquals("expected a number", configEditProblem(threshold, JsonPrimitive("")))
        assertEquals("expected a number", configEditProblem(threshold, JsonPrimitive("abc")))
        assertEquals("expected a whole number", configEditProblem(gain, JsonPrimitive("2.5")))
        assertNull(configEditProblem(gain, JsonPrimitive("7")))
        assertNull("only numbers have rules", configEditProblem(field("audio.mute", "bool", false), JsonPrimitive("abc")))
        assertEquals(listOf("mic.gain"), configEditProblems(listOf(threshold, gain), mapOf("wake.threshold" to JsonPrimitive("0.6"), "mic.gain" to JsonPrimitive("x"))))
    }

    @Test fun onlyRealChangesAreSentAndTypedNumbersGoAsNumbers() {
        val fields = listOf(
            field("wake.threshold", "float", 0.5),
            field("mic.gain", "int", 4),
            field("audio.mute", "bool", false),
            field("greeting.mode", "choice", "chime"),
            field("audio.device", "str", "hw:1", section = "advanced"),
        )
        val changes = configChanges(
            fields,
            mapOf(
                "wake.threshold" to JsonPrimitive("0.50"),   // the same value: no change
                "mic.gain" to JsonPrimitive("6"),
                "audio.mute" to JsonPrimitive(true),
                "greeting.mode" to JsonPrimitive("chime"),
                "audio.device" to JsonPrimitive("hw:2"),      // advanced: never from here
                "nope" to JsonPrimitive(1),
            ),
        )
        assertEquals(mapOf("mic.gain" to JsonPrimitive(6L), "audio.mute" to JsonPrimitive(true)), changes)
        assertFalse(changes.getValue("mic.gain").isString)
        assertEquals(
            """{"changes":{"mic.gain":6,"audio.mute":true}}""",
            configSaveBody(changes).toString(),
        )
    }

    @Test fun whatASaveSaysFollowsTheAnswer() {
        val ok = parseConfigSave(DomovoiJson.parseToJsonElement("""{"sent":["mic.gain"],"rejected":{},"restarting":true}"""))
        assertEquals(listOf("mic.gain"), ok.sent)
        assertEquals("kitchen restarting to apply…", RoomAdminWords.saved("kitchen", ok))
        val some = parseConfigSave(DomovoiJson.parseToJsonElement("""{"sent":["a"],"rejected":{"b":"not an editable setting"},"restarting":true}"""))
        assertEquals("kitchen restarting to apply… (1 not saved)", RoomAdminWords.saved("kitchen", some))
        val none = parseConfigSave(DomovoiJson.parseToJsonElement("""{"sent":[],"rejected":{"b":"this room device doesn't have this setting yet — upgrade it first"},"restarting":false}"""))
        assertEquals("nothing saved: this room device doesn't have this setting yet — upgrade it first", RoomAdminWords.saved("kitchen", none))
        assertEquals(ConfigSaveResult(emptyList(), emptyMap(), false), parseConfigSave(null))
    }

    // ---- the routes ------------------------------------------------------------

    private lateinit var mock: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())
    private val bodies = Collections.synchronizedList(mutableListOf<String>())

    @Before fun setUp() {
        mock = MockWebServer()
        mock.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                bodies += request.body.readUtf8()
                return when (request.path) {
                    "/api/satellites/kitchen/restart" -> MockResponse().setBody("""{"ok":true}""")
                    "/api/satellites/kitchen/upgrade" ->
                        MockResponse().setBody("""{"requested":true,"room_id":"kitchen","expected_sha":"abc1234","upgrade":{"state":"running","started_at":"2026-11-25T10:00:00Z"}}""")
                    "/api/satellites/kitchen/config" -> MockResponse().setBody("""{"sent":["mic.gain"],"rejected":{},"restarting":true}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        mock.start()
    }

    @After fun tearDown() = mock.shutdown()

    @Test fun theRoutesCarryTheBearerToTheServerTheyAreFor() = runBlocking {
        val base = mock.url("").toString().trimEnd('/')
        val api = RoomAdminApi(ApiClient({ base }))
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        api.restart("kitchen", h, base)
        val press = api.upgrade("kitchen", h, base, nowMs = 7)
        assertEquals("abc1234", press.expected)
        assertEquals(7L, press.atMs)
        val r = api.saveConfig("kitchen", mapOf("mic.gain" to JsonPrimitive(6)), h, base)
        assertTrue(r.restarting)
        assertEquals(listOf("POST", "POST", "PATCH"), seen.map { it.method })
        assertTrue(seen.all { it.getHeader("Authorization") == h["Authorization"] })
        assertEquals(6, DomovoiJson.parseToJsonElement(bodies.last()).jsonObject["changes"]!!.jsonObject["mic.gain"]!!.jsonPrimitive.content.toInt())
        // Never to another server than the one the session is for.
        var refused = false
        try {
            api.restart("kitchen", h, "http://192.168.1.30:6369")
        } catch (e: Exception) {
            refused = true
        }
        assertTrue(refused)
        assertEquals(3, seen.size)
    }
}
