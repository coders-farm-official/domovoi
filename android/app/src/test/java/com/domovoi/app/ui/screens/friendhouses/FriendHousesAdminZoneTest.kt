package com.domovoi.app.ui.screens.friendhouses

import com.domovoi.app.friendhouses.FhConversation
import com.domovoi.app.friendhouses.FhFriend
import com.domovoi.app.friendhouses.FhPaths
import com.domovoi.app.friendhouses.FriendHousesAdminApi
import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.LocalDate
import java.util.Collections

/** A conversation's Save a copy and Delete, as admin on this phone: the
 *  dashboard's words and file name, which conversations offer them, and
 *  the two routes carrying the admin Bearer. */
class FriendHousesAdminZoneTest {

    private val smiths = "ab".repeat(32)
    private val gran = "cd".repeat(32)

    @Test fun theWordsAreTheDashboards() {
        assertEquals("Save a copy", FhAdminWords.SAVE_COPY)
        assertEquals("Save a copy first", FhAdminWords.SAVE_FIRST)
        assertEquals("Delete", FhAdminWords.DELETE)
        assertEquals(
            "Delete every message with The Smiths? This can’t be undone. Save a copy first if you might need it.",
            FhAdminWords.deleteBody("The Smiths"),
        )
        for (w in listOf(FhAdminWords.SAVE_COPY, FhAdminWords.SAVE_FIRST, FhAdminWords.SAVED, FhAdminWords.deleteBody("x"))) {
            assertFalse(w, w.contains("proof", ignoreCase = true))
        }
    }

    @Test fun theFileIsNamedAsTheDashboardNamesIt() {
        val day = LocalDate.of(2026, 11, 5)
        assertEquals("Friend Houses - The Smiths - 2026-11-05.json", fhExportName("The Smiths", day))
        assertEquals("Friend Houses - friend - 2026-11-05.json", fhExportName(null, day))
        assertEquals("Friend Houses - friend - 2026-11-05.json", fhExportName("★★", day))
        assertEquals("Friend Houses - Gran Joness - 2026-11-05.json", fhExportName("Gran  Jones's/", day))
        assertEquals("a".repeat(60), fhSafe("a".repeat(80)))
        assertEquals("a.b_c-d", fhSafe("  a.b_c-d\t"))
    }

    @Test fun aFriendWithMessagesAndAFormerFriendOfferThem() {
        val friends = listOf(FhFriend(id = "f1", name = "The Smiths", house = smiths), FhFriend(id = "f2", name = "Quiet", house = "ef".repeat(32)))
        val inbox = listOf(
            FhConversation(house = smiths, name = "The Smiths", friendId = "f1"),
            FhConversation(house = gran, name = "Gran", friendId = null),
            FhConversation(house = "not-a-key", name = "Odd", friendId = null),
        )
        val (withMessages, former) = fhConversationsByFriend(friends, inbox)
        assertEquals(setOf(smiths), withMessages)
        assertEquals(listOf(gran), former.map { it.house })
    }

    // ---- the routes ----------------------------------------------------------

    private lateinit var mock: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())

    @Before fun setUp() {
        mock = MockWebServer()
        mock.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return when (request.method to request.path) {
                    "GET" to "/api/friend-houses/conversations/$smiths/export" ->
                        MockResponse().setBody("""{"format":"domovoi-friend-houses-export-v1","messages":[]}""")
                    "DELETE" to "/api/friend-houses/conversations/$smiths" -> MockResponse().setBody("""{"deleted":3}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        mock.start()
    }

    @After fun tearDown() = mock.shutdown()

    @Test fun theRoutesCarryTheBearer() = runBlocking {
        val base = mock.url("").toString().trimEnd('/')
        val api = FriendHousesAdminApi(ApiClient({ base }))
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        val file = api.conversationFile(smiths, h, base)
        assertTrue(file.contains("domovoi-friend-houses-export-v1"))
        assertEquals(3, api.deleteConversation(smiths, h, base))
        assertEquals(listOf("GET", "DELETE"), seen.map { it.method })
        assertTrue(seen.all { it.getHeader("Authorization") == h["Authorization"] })
        // Only a house key goes into a path.
        var refused = false
        try { FhPaths.conversationFile("../friends") } catch (e: IllegalArgumentException) { refused = true }
        assertTrue(refused)
    }
}
