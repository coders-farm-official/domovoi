package com.domovoi.app.ui.screens.people

import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.ui.screens.settings.ADMIN_JOBS
import kotlinx.coroutines.runBlocking
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

/** Rename and forget as admin on this phone: when they are offered, the
 *  dashboard's confirm, the name rules, and the two routes. */
class PeopleAdminTest {

    private val server = "http://192.168.1.20:6369"

    @Test fun offeredOnlyWithAdminAndNeverOnASharedScreen() {
        val signedIn = AdminSession.State.SignedIn(server, null)
        val kept = AdminSession.State.Kept(server, 1L)
        assertTrue(peopleAdminHeld(signedIn, false))
        assertTrue(peopleAdminHeld(kept, false))
        assertFalse(peopleAdminHeld(signedIn, true))
        assertFalse(peopleAdminHeld(AdminSession.State.SignedOut, false))
        assertNull(peopleEditLine(signedIn, false))
        assertEquals(PEOPLE_EDIT_ON_DASHBOARD, peopleEditLine(signedIn, true))
        assertEquals(
            "renaming and forgetting someone are done on the dashboard, or sign in as admin on this phone",
            peopleEditLine(AdminSession.State.SignedOut, false),
        )
        assertTrue(ADMIN_JOBS.contains("rename or forget someone (People)"))
    }

    @Test fun aRenameSendsTheNameTidiedOrNothing() {
        assertEquals("Mary Ann", renameTo("Mary", "  Mary   Ann "))
        assertNull(renameTo("Mary", "Mary"))
        assertNull(renameTo("Mary", "  Mary "))
        assertNull(renameTo("Mary", "   "))
        assertEquals("mary", renameTo("Mary", "mary"))
    }

    @Test fun forgetAsksInTheDashboardsWords() {
        val alex = Person(id = 7, name = "Alex", voice_profile_count = 3, namesakes = 0)
        assertEquals("Forget Alex?", PeopleAdminWords.forgetTitle(alex))
        assertEquals("Forget Alex (#7)?", PeopleAdminWords.forgetTitle(alex.copy(namesakes = 1)))
        assertEquals(
            "This is a cascade delete. Domovoi will lose:\n" +
                "• 3 voice profiles\n" +
                "• what they said: 12 conversation turns in 2 sessions, with Domovoi's answers\n" +
                "• their memories, favorites and preferences\n\n" +
                "Turns where someone else said their name stay: Domovoi goes by who was speaking.",
            PeopleAdminWords.forgetBody(alex, sessionCount = 2, turnCount = 12),
        )
        val one = PeopleAdminWords.forgetBody(alex.copy(voice_profile_count = 1), sessionCount = 0, turnCount = 1)
        assertTrue(one.contains("• 1 voice profile\n"))
        assertTrue(one.contains("what they said: 1 conversation turn, with"))
        assertTrue(PeopleAdminWords.forgetBody(alex, 1, 200).contains("200+ conversation turns in 1 session,"))
        assertEquals("Type Alex to confirm.", PeopleAdminWords.typeToConfirm("Alex"))
        assertEquals("Forget Alex", PeopleAdminWords.forgetButton("Alex"))
        assertTrue(forgetConfirmed(" Alex ", "Alex"))
        assertFalse(forgetConfirmed("alex", "Alex"))
        assertFalse(forgetConfirmed("", "Alex"))
        assertEquals("forgot Alex · cascade complete", PeopleAdminWords.forgot("Alex"))
        assertEquals("renamed to Sam", PeopleAdminWords.renamed("Sam"))
    }

    @Test fun aTakenNameIsSaidInTheServersWords() {
        assertEquals("Someone is already called Sam.", peopleProblem("rename", ApiException(409, "409", body = """{"detail":"Someone is already called Sam."}""")))
        assertTrue(peopleProblem("rename", ApiException(500, "500 boom")).startsWith("rename failed"))
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
                return if (request.path == "/api/people/7") MockResponse().setBody("""{"id":7,"name":"Sam"}""")
                else MockResponse().setResponseCode(404)
            }
        }
        mock.start()
    }

    @After fun tearDown() = mock.shutdown()

    @Test fun theRoutesCarryTheBearer() = runBlocking {
        val base = mock.url("").toString().trimEnd('/')
        val api = PeopleAdminApi(ApiClient({ base }))
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        api.rename(7, "Sam", h, base)
        api.forget(7, h, base)
        assertEquals(listOf("PATCH", "DELETE"), seen.map { it.method })
        assertEquals("""{"name":"Sam"}""", bodies.first())
        assertTrue(seen.all { it.getHeader("Authorization") == h["Authorization"] })
    }
}
