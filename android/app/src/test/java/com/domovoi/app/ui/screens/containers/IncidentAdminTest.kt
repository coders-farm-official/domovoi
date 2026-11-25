package com.domovoi.app.ui.screens.containers

import com.domovoi.app.alerts.ContainerIncident
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
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

/** Health incidents as admin on this phone: what the Bearer read adds, when
 *  acknowledge and clear are offered, what clearing says, and the routes. */
class IncidentAdminTest {
    private val adminRead = """
        {"incidents":[
          {"id":7,"class":"tamper","kind":"image","open":true,"can_acknowledge":false,"can_clear":true,
           "expected":{"image":"sha256:aaa","pin":"digest","tags":["a","b"],"empty":"","none":null},
           "found":{"image":"sha256:bbb","ports":[]},
           "acknowledged_by":null,"cleared_by":null,"clear_note":null},
          {"id":8,"class":"failure","kind":"stopped","open":true,"can_acknowledge":true,"can_clear":false},
          {"id":9,"class":"tamper","open":false,"can_clear":false,"cleared_by":"admin via phone: Pixel","clear_note":"I swapped it"},
          {"class":"failure"}
        ],"banner":{"show":true},"frozen":true,"attention":true}
    """.trimIndent()

    @Test fun theBearerReadAddsTheDetailsById() {
        val f = parseAdminIncidents(DomovoiJson.parseToJsonElement(adminRead))
        assertEquals(setOf(7L, 8L, 9L), f.keys)
        val seven = f.getValue(7)
        assertTrue(seven.canClear)
        assertFalse(seven.canAcknowledge)
        assertEquals(listOf("image" to "sha256:aaa", "pin" to "digest", "tags" to "a, b"), seven.expected)
        assertEquals(listOf("image" to "sha256:bbb", "ports" to "—"), seven.found)
        assertNull(IncidentAdminWords.who(seven))
        assertEquals("cleared by admin via phone: Pixel · I swapped it", IncidentAdminWords.who(f.getValue(9)))
        // A household read (no admin fields) is just ids and two noes.
        val household = parseAdminIncidents(DomovoiJson.parseToJsonElement("""{"incidents":[{"id":1,"class":"failure"}]}"""))
        assertEquals(IncidentAdminFields(1), household.getValue(1))
        assertTrue(parseAdminIncidents(DomovoiJson.parseToJsonElement("[]")).isEmpty())
    }

    @Test fun acknowledgeAndClearAreOfferedOnlyWhereTheyCanWork() {
        val f = parseAdminIncidents(DomovoiJson.parseToJsonElement(adminRead))
        val tamper = ContainerIncident(id = 7, cls = "tamper", open = true)
        val failure = ContainerIncident(id = 8, cls = "failure", open = true)
        assertTrue(offersClear(tamper, f[7]))
        assertFalse(offersAcknowledge(tamper, f[7]))
        assertTrue(offersAcknowledge(failure, f[8]))
        assertFalse(offersClear(failure, f[8]))
        // A stale admin read never offers either on a row that has moved on.
        assertFalse(offersClear(tamper.copy(open = false), f[7]))
        assertFalse(offersAcknowledge(failure.copy(acknowledged = true), f[8]))
        // No admin half (signed out): nothing.
        assertFalse(offersClear(tamper, null))
        assertFalse(offersAcknowledge(failure, null))
    }

    @Test fun theModeFollowsTheSessionAndNeverOnASharedScreen() {
        val server = "http://192.168.1.20:6369"
        val signedIn = AdminSession.State.SignedIn(server, null)
        val kept = AdminSession.State.Kept(server, 1L)
        assertEquals(IncidentAdminMode.LIVE, incidentAdminMode(signedIn, shared = false))
        assertEquals(IncidentAdminMode.KEPT, incidentAdminMode(kept, shared = false))
        assertEquals(IncidentAdminMode.NONE, incidentAdminMode(AdminSession.State.SignedOut, shared = false))
        assertEquals(IncidentAdminMode.NONE, incidentAdminMode(signedIn, shared = true))
        assertEquals(IncidentAdminMode.NONE, incidentAdminMode(kept, shared = true))
    }

    @Test fun whatClearingSaysFollowsTheAnswer() {
        assertEquals("cleared · paused changes resume", IncidentAdminWords.cleared(ClearResult(frozen = false, keptImage = null)))
        assertEquals(
            "cleared · risky changes stay paused until every tampering incident is cleared",
            IncidentAdminWords.cleared(ClearResult(frozen = true, keptImage = null)),
        )
        assertTrue(IncidentAdminWords.cleared(ClearResult(false, "sha256:ccc")).endsWith("the music player image it found is now the one expected"))
        assertEquals(ClearResult(false, "sha256:ccc"), parseClearResult(DomovoiJson.parseToJsonElement("""{"incident":{},"frozen":false,"kept_image":"sha256:ccc"}""")))
        assertEquals(ClearResult(true, null), parseClearResult(DomovoiJson.parseToJsonElement("""{"frozen":true,"kept_image":null}""")))
    }

    @Test fun aNoteIsAtMost500CharactersAndBlankIsNone() {
        assertEquals(JsonNull, clearNoteBody("   ")["note"])
        assertEquals("checked it", clearNoteBody("  checked it ")["note"]!!.jsonPrimitive.content)
        assertEquals(500, clearNoteBody("x".repeat(600))["note"]!!.jsonPrimitive.content.length)
    }

    @Test fun signedOutTheDashboardLinesNameThePhoneToo() {
        assertTrue(IncidentAdminWords.DASHBOARD_OR_PHONE.startsWith("an admin acknowledges or clears these on the dashboard's Settings → Health"))
        assertTrue(IncidentAdminWords.DASHBOARD_OR_PHONE.endsWith("or sign in as admin on this phone"))
        assertTrue(IncidentAdminWords.DETAILS_OR_PHONE.endsWith("or sign in as admin on this phone"))
        assertEquals("That one is a failure.", IncidentAdminWords.refusal(409, "That one is a failure."))
        assertEquals("that incident is gone", IncidentAdminWords.refusal(404, null))
        // The panel names the three areas the phone now does.
        for (area in listOf("Settings → Rooms", "Friend Houses → friends & connections", "Settings → Health")) {
            assertTrue(area, com.domovoi.app.ui.screens.settings.ADMIN_JOBS.contains(area))
        }
    }

    // ---- the routes ----------------------------------------------------------

    private lateinit var server: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())
    private val bodies = Collections.synchronizedList(mutableListOf<String>())

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                bodies += request.body.readUtf8()
                return when (request.path) {
                    "/api/containers/incidents" -> MockResponse().setBody(adminRead)
                    "/api/containers/incidents/8/acknowledge" -> MockResponse().setBody("""{"incident":{}}""")
                    "/api/containers/incidents/7/clear" -> MockResponse().setBody("""{"incident":{},"frozen":false,"kept_image":null}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }

    @After fun tearDown() = server.shutdown()

    @Test fun theRoutesCarryTheBearerAndTheNote() = runBlocking {
        val base = server.url("").toString().trimEnd('/')
        val api = IncidentAdminApi(ApiClient({ base }))
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        assertEquals(setOf(7L, 8L, 9L), api.read(h, base).keys)
        api.acknowledge(8, h, base)
        assertEquals(ClearResult(false, null), api.clear(7, "checked the image", h, base))
        assertEquals(listOf("GET", "POST", "POST"), seen.map { it.method })
        assertTrue(seen.all { it.getHeader("Authorization") == h["Authorization"] })
        assertEquals("checked the image", DomovoiJson.parseToJsonElement(bodies.last()).jsonObject["note"]!!.jsonPrimitive.content)
    }
}
