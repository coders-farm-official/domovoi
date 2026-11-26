package com.domovoi.app.ui.screens.printing

import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
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

/** Rename and remove a printer as admin on this phone: when they are
 *  offered, the dashboard's confirm, the name rule, and the two routes. */
class PrinterAdminTest {

    private val server = "http://192.168.1.20:6369"

    @Test fun offeredOnlyWithAdminAndNeverOnASharedScreen() {
        val signedIn = AdminSession.State.SignedIn(server, null)
        val kept = AdminSession.State.Kept(server, 1L)
        assertTrue(printersAdminHeld(signedIn, false))
        assertTrue(printersAdminHeld(kept, false))
        assertFalse(printersAdminHeld(signedIn, true))
        assertFalse(printersAdminHeld(AdminSession.State.SignedOut, false))
        assertNull(printersAdminLine(signedIn, false))
        assertEquals(ADMIN_ONLY_LINE, printersAdminLine(AdminSession.State.SignedOut, true))
        assertEquals(
            "An admin can rename or remove printers on the web dashboard, or sign in as admin on this phone.",
            printersAdminLine(AdminSession.State.SignedOut, false),
        )
        assertTrue(ADMIN_JOBS.contains("rename or remove a printer (Settings → Printers)"))
    }

    @Test fun removeAsksInTheDashboardsWords() {
        assertEquals("Remove Office laser?", PrinterAdminWords.removeTitle("Office laser"))
        assertEquals("Domovoi stops printing with it. You can add it again later.", PrinterAdminWords.removeBody(false))
        assertEquals("Domovoi stops printing and scanning with it. You can add it again later.", PrinterAdminWords.removeBody(true))
        assertEquals("removed Office laser", PrinterAdminWords.removed("Office laser"))
    }

    @Test fun aRenameSendsTheNameTrimmedOrNothing() {
        assertEquals("Hall printer", printerRenameTo("Office", "  Hall printer "))
        assertNull(printerRenameTo("Office", "Office"))
        assertNull(printerRenameTo("Office", "   "))
        assertEquals(60, printerRenameTo("Office", "x".repeat(80))!!.length)
    }

    private lateinit var mock: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())
    private val bodies = Collections.synchronizedList(mutableListOf<String>())

    @Before fun setUp() {
        mock = MockWebServer()
        mock.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                bodies += request.body.readUtf8()
                return when ("${request.method} ${request.path}") {
                    "POST /api/printers/4/rename" -> MockResponse().setBody("""{"id":4,"name":"Hall"}""")
                    "DELETE /api/printers/4" -> MockResponse().setBody("""{"removed":true}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        mock.start()
    }

    @After fun tearDown() = mock.shutdown()

    @Test fun theRoutesCarryTheBearer() = runBlocking {
        val base = mock.url("").toString().trimEnd('/')
        val api = PrinterAdminApi(ApiClient({ base }))
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        api.rename(4, "Hall", h, base)
        api.remove(4, h, base)
        assertEquals(listOf("POST", "DELETE"), seen.map { it.method })
        assertEquals("""{"name":"Hall"}""", bodies.first())
        assertTrue(seen.all { it.getHeader("Authorization") == h["Authorization"] })
    }
}
