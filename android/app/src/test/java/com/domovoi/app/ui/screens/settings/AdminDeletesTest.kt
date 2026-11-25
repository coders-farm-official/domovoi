package com.domovoi.app.ui.screens.settings

import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.ui.screens.music.MusicAdminApi
import com.domovoi.app.ui.screens.music.trackDeletePath
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

/** Deletes and blocks as admin on this phone: when they are offered, the
 *  dashboard's words, the block lists, and the routes. */
class AdminDeletesTest {

    private val server = "http://192.168.1.20:6369"

    @Test fun offeredOnlyWithAdminAndNeverOnASharedScreen() {
        val signedIn = AdminSession.State.SignedIn(server, null)
        val kept = AdminSession.State.Kept(server, 1L)
        assertTrue(deletesAdminHeld(signedIn, false))
        assertTrue(deletesAdminHeld(kept, false))
        assertFalse(deletesAdminHeld(signedIn, true))
        assertFalse(deletesAdminHeld(AdminSession.State.SignedOut, false))
        val old = "rescanning the library, fixing tags and deleting tracks are done on the dashboard"
        assertEquals(AdminDeleteWords.UPKEEP_HELD, musicUpkeepLine(signedIn, false, old))
        assertEquals(old, musicUpkeepLine(signedIn, true, old))
        assertTrue(musicUpkeepLine(AdminSession.State.SignedOut, false, old).startsWith("$old, or sign in as admin on this phone"))
        val files = "deleting is on the dashboard — a phone can't sign in as an admin"
        assertNull(filesDeleteLine(signedIn, false, files))
        assertEquals(files, filesDeleteLine(AdminSession.State.SignedOut, true, files))
        assertEquals("deleting is on the dashboard, or sign in as admin on this phone", filesDeleteLine(AdminSession.State.SignedOut, false, files))
        assertTrue(ADMIN_JOBS.contains("delete a music track (its drawer), a file on the server (Files) or an audiobook (remove from server)"))
        assertTrue(ADMIN_JOBS.contains("lift a phone's queue or Files block"))
    }

    @Test fun aTrackIsDeletedInTheDashboardsWords() {
        assertEquals(
            "Permanently delete the file for \"Blue\" from disk? This can't be undone.\n\n/music/blue.flac",
            AdminDeleteWords.trackConfirm("Blue", "/music/blue.flac", alsoFile = true),
        )
        assertEquals(
            "Remove \"Blue\" from the library? The file stays on disk; a rescan will re-add it.",
            AdminDeleteWords.trackConfirm("Blue", "/music/blue.flac", alsoFile = false),
        )
        assertTrue(AdminDeleteWords.trackConfirm(null, null, true).startsWith("Permanently delete the file for \"track\""))
        assertEquals("deleted \"Blue\" · file removed from disk", AdminDeleteWords.trackDeleted("Blue", true))
        assertEquals("removed \"Blue\" from library (file kept; rescan will re-add)", AdminDeleteWords.trackDeleted("Blue", false))
        assertEquals("/api/music/library/9?also_file=true", trackDeletePath(9, true))
        assertEquals("/api/music/library/9", trackDeletePath(9, false))
    }

    @Test fun aServerFileIsDeletedInTheDashboardsWords() {
        assertEquals("Delete 1 item?", AdminDeleteWords.filesTitle(1))
        assertEquals("Delete 2 items?", AdminDeleteWords.filesTitle(2))
        assertEquals("This can’t be undone.\n\nnotes/a.txt", AdminDeleteWords.filesBody(0, listOf("notes/a.txt")))
        assertEquals(
            "1 folder and everything inside it will be permanently deleted. This can’t be undone.\n\nold",
            AdminDeleteWords.filesBody(1, listOf("old")),
        )
        assertEquals("Delete everything", AdminDeleteWords.filesConfirmLabel(true))
        assertEquals("Delete", AdminDeleteWords.filesConfirmLabel(false))
        assertEquals("deleted 1 item", AdminDeleteWords.filesDeleted(1, 0))
        assertEquals("deleted 2 items · 1 failed", AdminDeleteWords.filesDeleted(2, 1))
    }

    @Test fun theBlocksReadAsTheDashboardListsThem() {
        val list = parseDeviceBlocks(
            """[{"id":3,"device_id":"dev-a","device_name":"Kid's tablet","room_id":"kitchen","note":"bedtime","created_at":"2026-11-20T10:00:00Z"},
                {"id":4,"device_id":"dev-b","device_name":null,"room_id":null,"note":null,"created_at":null},
                {"device_id":"no-id"}]""",
        )
        assertEquals(listOf(3L, 4L), list.map { it.id })
        assertEquals("Kid's tablet", list[0].who)
        assertEquals("dev-b", list[1].who)
        assertEquals("kitchen · bedtime · added X", blockDetail(list[0], queue = true) { "X" })
        assertEquals("every room", blockDetail(list[1], queue = true) { "X" })
        assertEquals("bedtime · added X", blockDetail(list[0], queue = false) { "X" })
        assertEquals(emptyList<DeviceBlock>(), parseDeviceBlocks("{}"))
        assertTrue(AdminDeleteWords.LIFTED_WHERE.startsWith("An admin lifts the block in the dashboard under Settings → Phones & browsers"))
    }

    // ---- the routes ------------------------------------------------------------

    private lateinit var mock: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())

    @Before fun setUp() {
        mock = MockWebServer()
        mock.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return when ("${request.method} ${request.path}") {
                    "GET /api/music/queue-blocks" -> MockResponse().setBody("""[{"id":3,"device_name":"Tablet","room_id":null}]""")
                    "GET /api/files/device-blocks" -> MockResponse().setBody("[]")
                    "DELETE /api/music/queue-blocks/3", "DELETE /api/files/device-blocks/5" -> MockResponse().setResponseCode(204)
                    "DELETE /api/music/library/9?also_file=true" -> MockResponse().setResponseCode(204)
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        mock.start()
    }

    @After fun tearDown() = mock.shutdown()

    @Test fun theRoutesCarryTheBearer() = runBlocking {
        val base = mock.url("").toString().trimEnd('/')
        val client = ApiClient({ base })
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        val blocks = AdminBlocksApi(client)
        assertEquals(listOf(3L), blocks.queueBlocks(h, base).map { it.id })
        assertEquals(emptyList<DeviceBlock>(), blocks.filesBlocks(h, base))
        blocks.lift(true, 3, h, base)
        blocks.lift(false, 5, h, base)
        MusicAdminApi(client).deleteTrack(9, true, h, base)
        assertEquals(5, seen.size)
        assertTrue(seen.all { it.getHeader("Authorization") == h["Authorization"] })
        assertEquals(listOf("GET", "GET", "DELETE", "DELETE", "DELETE"), seen.map { it.method })
    }
}
