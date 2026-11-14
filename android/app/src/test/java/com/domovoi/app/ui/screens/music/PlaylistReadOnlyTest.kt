package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * Playlists made from the music files (V058) are read-only: decoded from
 * the server's own list, left out of every add picker, their edit controls
 * hidden, a stale 409 said in plain words, and "Copy to a new playlist"
 * posting to the copy route. The words are the web's and the server's,
 * read from the repo so they can't drift apart.
 */
class PlaylistReadOnlyTest {
    private fun repoFile(rel: String): File =
        listOf("../../$rel", "../$rel").map(::File).first { it.isFile }

    /** `GET /api/playlists` as web/backend/api/playlists.py answers it:
     *  every Playlist field (pydantic dumps them all), Favorites first. */
    private val listJson = """
        [
          {"id": 0, "name": "Favorites", "track_count": 3, "created_at": null, "is_virtual": true,
           "description": null, "cover_color": null, "cover_emoji": null, "imported": false},
          {"id": 4, "name": "Road trip", "track_count": 12, "created_at": "2026-11-01T10:00:00Z",
           "is_virtual": false, "description": "long drives", "cover_color": "#7c5cff",
           "cover_emoji": null, "imported": false},
          {"id": 9, "name": "Dinner party", "track_count": 20, "created_at": "2026-11-14T08:00:00Z",
           "is_virtual": false, "description": null, "cover_color": null, "cover_emoji": null,
           "imported": true}
        ]
    """.trimIndent()

    private val all: List<Playlist> = DomovoiJson.parseToJsonElement(listJson).decode()

    @Test fun theFixtureHasExactlyTheContractsPlaylistFields() {
        val schema = DomovoiJson.parseToJsonElement(repoFile("web/openapi.json").readText())
            .jsonObject["components"]!!.jsonObject["schemas"]!!.jsonObject["Playlist"]!!
            .jsonObject["properties"]!!.jsonObject.keys
        assertTrue("imported" in schema)
        DomovoiJson.parseToJsonElement(listJson).jsonArray.forEach {
            assertEquals(schema, it.jsonObject.keys)
        }
    }

    @Test fun theImportedFlagIsDecoded() {
        assertEquals(listOf(false, false, true), all.map { it.imported })
        // An older server sends no flag: a household playlist, as before.
        val old: Playlist = DomovoiJson.parseToJsonElement(
            """{"id": 4, "name": "Road trip", "track_count": 12, "is_virtual": false}""",
        ).decode()
        assertFalse(old.imported)
    }

    @Test fun theAddSheetLeavesOutPlaylistsFromTheFiles() {
        // Favorites stays: there it's the favourite toggle.
        assertEquals(listOf(0L, 4L), addTargets(all).map { it.id })
    }

    @Test fun theBulkPickerOffersHouseholdPlaylistsOnly() {
        assertEquals(listOf(4L), bulkAddTargets(all).map { it.id })
    }

    @Test fun onlyAHouseholdPlaylistIsEditable() {
        assertEquals(listOf(false, true, false), all.map { it.editable })
    }

    private fun conflict(body: String) = ApiException(409, "409 Conflict: $body", body = body)

    private val serverReadOnly: String by lazy {
        // READ_ONLY_MESSAGE = ( "…" "…" ) in domovoi/library_playlists_import.py
        val src = repoFile("domovoi/library_playlists_import.py").readText()
        val block = Regex("""READ_ONLY_MESSAGE = \(([\s\S]*?)\)""").find(src)!!.groupValues[1]
        Regex(""""((?:[^"\\]|\\.)*)"""").findAll(block).joinToString("") { it.groupValues[1] }
    }

    @Test fun aStale409IsSaidInTheServersPlainWords() {
        assertEquals(serverReadOnly, READ_ONLY_TEXT)
        val body = """{"detail": "$serverReadOnly"}"""
        assertEquals(serverReadOnly, playlistConflictText(conflict(body)))
        // A 409 with no reason of its own still reads as words, never "409 Conflict: ".
        assertEquals(READ_ONLY_TEXT, playlistConflictText(conflict("")))
        // Anything else is the caller's usual failure text.
        assertNull(playlistConflictText(ApiException(500, "500 Internal Server Error: boom", body = "boom")))
        assertNull(playlistConflictText(java.io.IOException("offline")))
    }

    @Test fun aBulkAddTellsADuplicateFromARefusal() {
        assertEquals(
            BulkAddFailure.Duplicate,
            bulkAddFailure(conflict("""{"detail": "track 5 is already in this playlist"}""")),
        )
        // The defect: every 409 counted as "already in", so a playlist that
        // refuses all adds reported each song as a duplicate.
        assertEquals(
            BulkAddFailure.Refused(serverReadOnly),
            bulkAddFailure(conflict("""{"detail": "$serverReadOnly"}""")),
        )
        assertTrue(bulkAddFailure(ApiException(404, "404 Not Found: x", body = "x")) is BulkAddFailure.Other)
    }

    @Test fun theWordsAreTheWebs() {
        val jsx = repoFile("web/static/music.jsx").readText()
        fun squash(s: String) = s.replace(Regex("\\s+"), " ")
        assertTrue(jsx.contains("'$IMPORTED_EYEBROW'"))
        assertTrue(jsx.contains(">$COPY_TO_NEW_PLAYLIST<"))
        assertTrue(squash(jsx).contains(IMPORTED_NOTE))
        assertTrue(jsx.contains("fire(`copied to “\${p.name}”`)"))
        assertEquals("copied to “Dinner party (copy)”", copiedText(all[2].copy(name = "Dinner party (copy)")))
    }

    /** The JVM names of PlaylistReadOnly.kt's functions that start with [prefix]. */
    private fun rule(prefix: String): Set<String> =
        Class.forName("com.domovoi.app.ui.screens.music.PlaylistReadOnlyKt").declaredMethods
            .map { it.name }.filter { it.startsWith(prefix) }.toSet()
            .also { assertTrue(prefix, it.isNotEmpty()) }

    private fun calls(facade: String, prefix: String) =
        com.domovoi.app.testing.Bytecode.callers(
            "com.domovoi.app.ui.screens.music.$facade",
            "com/domovoi/app/ui/screens/music/PlaylistReadOnlyKt", rule(prefix),
        ).isNotEmpty()

    @Test fun theScreensUseTheRules() {
        // The pickers filter; the bulk add sorts its failures.
        assertTrue(calls("AddToPlaylistSheetKt", "addTargets"))
        assertTrue(calls("AddToPlaylistSheetKt", "playlistConflictText"))
        assertTrue(calls("MusicScreenKt", "bulkAddTargets"))
        assertTrue(calls("MusicScreenKt", "bulkAddFailure"))
        // The drawer hides edits by the flag, copies, and words a 409.
        assertTrue(calls("PlaylistDrawerKt", "getEditable"))
        assertTrue(calls("PlaylistDrawerKt", "copyPlaylist"))
        assertTrue(calls("PlaylistDrawerKt", "playlistConflictText"))
    }

    private lateinit var server: MockWebServer

    @Before fun start() {
        server = MockWebServer()
        server.start()
    }

    @After fun stop() = server.shutdown()

    @Test fun copyPostsToTheCopyRouteAndReadsTheNewPlaylist() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(201).setBody(
                """{"id": 15, "name": "Dinner party (copy)", "track_count": 20, "created_at": "2026-11-14T09:00:00Z",
                   "is_virtual": false, "description": null, "cover_color": null, "cover_emoji": null,
                   "imported": false}""",
            ),
        )
        val api = ApiClient({ server.url("").toString().trimEnd('/') })
        val made = copyPlaylist(api, 9)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/playlists/9/copy", req.path)
        assertEquals(15L, made.id)
        assertEquals("Dinner party (copy)", made.name)
        assertTrue(made.editable)
    }
}
