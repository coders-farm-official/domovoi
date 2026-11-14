package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.CoverArt
import com.domovoi.app.testing.Bytecode
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The library follow-ups' fields (server bf28869f, V059) on the phone: a
 * track's credited `artists`, `cover_v` and `cover_url` on every track row
 * and room-queue entry, and the "with B, C" credits line the web shows.
 */
class CreditsAndCoverFieldsTest {

    @After fun clean() = CoverArt.forgetVersions()

    /**
     * A Track as web/backend/schemas.py serialises it, field for field
     * (music.py _row_to_track): the library list's items, a single track,
     * a PATCH answer and a playlist's tracks (Favorites included) all are this.
     */
    private val track = """
        {"id": 41, "file_path": "Main/Album/01 Song.mp3", "title": "Song", "artist": "Main",
         "album": "Album", "duration_sec": 200, "source": null, "source_id": null,
         "musicbrainz_recording_id": null, "added_at": "2026-11-14T09:58:28+00:00",
         "added_via": "manual", "enriched_at": null, "favorited": true,
         "artists": ["Main", "Guest", "Third"], "cover_v": 4,
         "cover_url": "/api/music/library/41/cover?v=4"}
    """.trimIndent()

    @Test fun aTrackRowDecodesItsCreditsAndCoverVersion() {
        val t = DomovoiJson.decodeFromString(LibraryTrack.serializer(), track)
        assertEquals(listOf("Main", "Guest", "Third"), t.artists)
        assertEquals(4, t.coverV)
        assertEquals("/api/music/library/41/cover?v=4", t.coverUrl)
        // What the app asks for is the server's own cover_url.
        assertEquals(t.coverUrl, CoverArt.libraryPath(t.id, t.coverV))
        // And a phone-queue item made from the row carries it to the media session.
        assertEquals(t.coverUrl, libraryPlayItem(t).coverPath)
    }

    @Test fun aLibraryPageDecodes() {
        val page = DomovoiJson.decodeFromString(
            LibraryPage.serializer(),
            """{"total": 1, "items": [$track], "limit": 50, "has_more": false, "next_cursor": null}""",
        )
        assertEquals(4, page.items.single().coverV)
    }

    @Test fun anOlderServersRowHasNoneOfThem() {
        val t = DomovoiJson.decodeFromString(
            LibraryTrack.serializer(),
            """{"id": 5, "file_path": "a.mp3", "title": "A", "artist": "X", "added_at": "2026-01-01T00:00:00+00:00", "favorited": false}""",
        )
        assertEquals(emptyList<String>(), t.artists)
        assertNull(t.coverV)
        assertNull(t.coverUrl)
        assertEquals("/api/music/library/5/cover", CoverArt.libraryPath(t.id, t.coverV))
    }

    /** A room-queue answer (web/backend/api/music_queue.py QueueItem, field
     *  for field): a library song, then a stream ([] / null / null). */
    @Test fun aRoomQueueEntryDecodesThem() {
        val q = DomovoiJson.decodeFromString(
            RoomQueue.serializer(),
            """
            {"room_id": "kitchen", "items": [
              {"song_id": 7, "pos": 0, "file": "Main/Album/01 Song.mp3", "title": "Song", "artist": "Main",
               "album": "Album", "duration_sec": 200, "added_by": "Phone", "added_by_device_id": null,
               "added_at": null, "guest_device_id": null, "guest_source": null, "playing": true,
               "origin": "explicit", "reason_label": null, "why": null, "up_next_seq": null, "track_id": 41,
               "artists": ["Main", "Guest"], "cover_v": 2, "cover_url": "/api/music/library/41/cover?v=2"},
              {"song_id": 8, "pos": 1, "file": "http://radio.example.com/stream", "title": null, "artist": null,
               "album": null, "duration_sec": null, "added_by": null, "added_by_device_id": null,
               "added_at": null, "guest_device_id": null, "guest_source": null, "playing": false,
               "origin": "explicit", "reason_label": null, "why": null, "up_next_seq": null, "track_id": null,
               "artists": [], "cover_v": null, "cover_url": null}
             ], "current_song_id": 7, "editable": true, "blocked_reason": null, "guest": false, "admin": false,
             "session_id": null, "autoplay": null, "up_next": null}
            """.trimIndent(),
        )
        val (song, stream) = q.items
        assertEquals(listOf("Main", "Guest"), song.artists)
        assertEquals(2, song.coverV)
        assertEquals("/api/music/library/41/cover?v=2", song.coverUrl)
        assertEquals(emptyList<String>(), stream.artists)
        assertNull(stream.coverV)
        assertNull(stream.coverUrl)
    }

    // ── "with B, C" (components.jsx creditedWith) ───────────────────────

    @Test fun theCreditsLineIsTheWebsWords() {
        // The artist line already names Main: the rest, in the file's order.
        assertEquals("with Guest, Third", creditedWith("Main", listOf("Main", "Guest", "Third")))
        // One name: no list.
        assertEquals("with Guest", creditedWith("Main", listOf("Main", "Guest")))
        // Three or more: ", " throughout, no "and".
        assertEquals("with B, C, D", creditedWith("A", listOf("A", "B", "C", "D")))
        // Nobody beyond the artist line: nothing at all.
        assertEquals("", creditedWith("Main", emptyList()))
        assertEquals("", creditedWith("Main", listOf("Main")))
        // Already in the artist line, in any case, anywhere: left out.
        assertEquals("with C", creditedWith("Main feat. Guest", listOf("Main", "GUEST", "C")))
        // Blank names are dropped; a missing artist line hides none.
        assertEquals("with B", creditedWith(null, listOf(" ", "B")))
    }

    @Test fun aRowsArtistLineAddsThemAfterADot() {
        assertEquals("Main · with Guest, Third", artistLine("Main", "with Guest, Third"))
        assertEquals("Main", artistLine("Main", ""))
        assertEquals("— · with Guest", artistLine("—", creditedWith(null, listOf("Guest"))))
    }

    @Test fun theRowsAndTheDrawerShowThem() {
        for (facade in listOf(
            "com.domovoi.app.ui.screens.music.LibraryTabKt",
            "com.domovoi.app.ui.screens.music.PlaylistDrawerKt",
            "com.domovoi.app.ui.screens.music.TrackDrawerKt",
        )) {
            assertTrue(
                facade,
                Bytecode.callers(facade, "com/domovoi/app/ui/screens/music/CreditsKt", setOf("creditedWith")).isNotEmpty(),
            )
        }
        for (facade in listOf(
            "com.domovoi.app.ui.screens.music.LibraryTabKt",
            "com.domovoi.app.ui.screens.music.PlaylistDrawerKt",
        )) {
            assertTrue(
                facade,
                Bytecode.callers(facade, "com/domovoi/app/ui/screens/music/CreditsKt", setOf("artistWithCredits")).isNotEmpty(),
            )
        }
    }
}
