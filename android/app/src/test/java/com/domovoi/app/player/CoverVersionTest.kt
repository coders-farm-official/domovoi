package com.domovoi.app.player

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.testing.Bytecode
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The cover's version (V059): a row's `cover_v` / `cover_url` as the cache
 * key, and the live `library.cover.changed` event moving covers already on
 * screen, in the phone's queue and in the media session.
 */
class CoverVersionTest {

    private val server = "http://192.168.1.20:6369"

    @After fun clean() {
        CoverArt.forgetVersions()
        CoverArt.forgetMisses()
    }

    /** The event's `data` exactly as web/backend/realtime.py
     *  `_snapshot_library_covers` builds it: newest first, each track's id,
     *  cover_v and cover_url (domovoi/library_cover_version.py cover_url). */
    private fun coverChanged(vararg tracks: Pair<Long, Int>) = DomovoiJson.parseToJsonElement(
        tracks.joinToString(",", prefix = """{"tracks": [""", postfix = "]}") { (id, v) ->
            """{"id": $id, "cover_v": $v, "cover_url": "/api/music/library/$id/cover?v=$v"}"""
        },
    )

    // ── The cache key ────────────────────────────────────────────────────

    @Test fun aVersionedRowAsksForTheServersOwnCoverUrl() {
        // cover_url(7, 3) == "/api/music/library/7/cover?v=3" (test_library_followups.py).
        assertEquals("/api/music/library/7/cover?v=3", CoverArt.libraryPath(7, 3))
        // A row whose art never changed still carries ?v=0, as cover_url does.
        assertEquals("/api/music/library/7/cover?v=0", CoverArt.libraryPath(7, 0))
        // An older server's row (no cover_v): the bare route, checked by ETag.
        assertEquals("/api/music/library/7/cover", CoverArt.libraryPath(7))
        assertEquals("/api/music/library/7/cover", CoverArt.libraryPath(7, null))
    }

    @Test fun aKnownVersionIsDrawnFromMemoryWhateverTheEpoch() {
        val v4 = "$server/api/music/library/12/cover?v=4"
        assertEquals(CoverArt.memoryKey(v4, 0), CoverArt.memoryKey(v4, 7))
        // A new version is a new key: fetched fresh.
        assertNotEquals(CoverArt.memoryKey(v4, 0), CoverArt.memoryKey("$server/api/music/library/12/cover?v=5", 0))
        // Without a version the epoch still sends it back to the server.
        val bare = "$server/api/music/library/12/cover"
        assertNotEquals(CoverArt.memoryKey(bare, 0), CoverArt.memoryKey(bare, 1))
    }

    @Test fun aVersionedCoverIsKeptOnDiskWithoutAsking() {
        val mock = MockWebServer().apply { start() }
        try {
            mock.enqueue(
                MockResponse()
                    .setHeader("ETag", "\"3f2a9c-c1731574800000000000\"")
                    .setHeader("Cache-Control", "public, max-age=86400")
                    .setHeader("Content-Type", "image/jpeg")
                    .setBody("jpeg-bytes"),
            )
            val client = OkHttpClient.Builder().addInterceptor(CoverArt.revalidate).build()
            val request = Request.Builder().url(mock.url("/api/music/library/12/cover?v=4")).build()
            val response = client.newCall(request).execute().also { it.body?.close() }
            assertFalse(response.cacheControl.noCache)
            assertEquals("public, max-age=86400", response.header("Cache-Control"))
            assertNull(nextAsk(request, response))
        } finally {
            mock.shutdown()
        }
    }

    /** Coil's own disk-cache decision (as CoverArtTest.nextAsk): the request it
     *  would send for a stored [response], or null for "use the disk copy". */
    private fun nextAsk(request: Request, response: okhttp3.Response): Request? {
        val cacheResponse = Class.forName("coil.network.CacheResponse")
        val stored = cacheResponse.getConstructor(okhttp3.Response::class.java).newInstance(response)
        val factory = Class.forName("coil.network.CacheStrategy\$Factory")
        val strategy = factory.getConstructor(Request::class.java, cacheResponse)
            .newInstance(request, stored).let { factory.getMethod("compute").invoke(it) }
        return strategy.javaClass.getMethod("getNetworkRequest").invoke(strategy) as Request?
    }

    @Test fun onlyAVQueryCountsAsAVersion() {
        assertTrue(CoverArt.isVersioned("v=4"))
        assertTrue(CoverArt.isVersioned("x=1&v=4"))
        assertFalse(CoverArt.isVersioned(null))
        assertFalse(CoverArt.isVersioned("vv=4"))
        assertTrue(CoverArt.isVersionedModel("$server/api/music/library/12/cover?v=4"))
        assertFalse(CoverArt.isVersionedModel("$server/api/music/library/12/cover"))
    }

    // ── library.cover.changed ────────────────────────────────────────────

    @Test fun theEventIsReadAsTheServerSendsIt() {
        assertEquals(mapOf(12L to 5, 3L to 2), CoverArt.parseCoverChanged(coverChanged(12L to 5, 3L to 2)))
        assertEquals(emptyMap<Long, Int>(), CoverArt.parseCoverChanged(null))
        assertEquals(
            mapOf(4L to 1),
            CoverArt.parseCoverChanged(DomovoiJson.parseToJsonElement("""{"tracks": [{"id": 4, "cover_v": 1}, {"cover_v": 2}, "x"]}""")),
        )
    }

    @Test fun aMovedVersionIsTakenAndAnUnchangedOneIgnored() {
        assertEquals(setOf(12L, 3L), CoverArt.onCoverChanged(server, server, coverChanged(12L to 5, 3L to 2)))
        // The event repeats the 50 newest every time: nothing moved, nothing redrawn.
        assertEquals(emptySet<Long>(), CoverArt.onCoverChanged(server, server, coverChanged(12L to 5, 3L to 2)))
        // Only the one that moved.
        assertEquals(setOf(3L), CoverArt.onCoverChanged(server, server, coverChanged(3L to 3, 12L to 5)))
        // Never backwards.
        assertEquals(emptySet<Long>(), CoverArt.onCoverChanged(server, server, coverChanged(12L to 4)))
        assertEquals(5, CoverArt.knownVersion(12))
        assertEquals(3, CoverArt.knownVersion(3))
    }

    @Test fun aKnownVersionRaisesWhatARowAsksFor() {
        CoverArt.onCoverChanged(server, server, coverChanged(12L to 5))
        assertEquals("/api/music/library/12/cover?v=5", CoverArt.libraryPath(12, 4))
        assertEquals("/api/music/library/12/cover?v=5", CoverArt.libraryPath(12))
        // A row read after the change is newer still: its own.
        assertEquals("/api/music/library/12/cover?v=6", CoverArt.libraryPath(12, 6))
        assertEquals("/api/music/library/13/cover", CoverArt.libraryPath(13))
    }

    @Test fun aCoverOnScreenMovesToTheNewVersion() {
        CoverArt.onCoverChanged(server, server, coverChanged(12L to 5))
        val known = CoverArt.versions.value
        assertEquals("$server/api/music/library/12/cover?v=5",
            CoverArt.withKnownVersion("$server/api/music/library/12/cover?v=4", known))
        assertEquals("$server/api/music/library/12/cover?v=5",
            CoverArt.withKnownVersion("$server/api/music/library/12/cover", known))
        assertEquals("/api/music/library/12/cover?v=5",
            CoverArt.withKnownVersion("/api/music/library/12/cover?v=4", known))
        // Already that version: the very same string, so nothing reloads.
        val same = "$server/api/music/library/12/cover?v=5"
        assertTrue(same === CoverArt.withKnownVersion(same, known))
        // Another track, another server, a phone song: untouched.
        assertEquals("$server/api/music/library/13/cover?v=1",
            CoverArt.withKnownVersion("$server/api/music/library/13/cover?v=1", known))
        assertEquals("http://10.0.2.2:6390/api/music/library/12/cover?v=4",
            CoverArt.withKnownVersion("http://10.0.2.2:6390/api/music/library/12/cover?v=4", known))
        assertEquals("content://media/external/audio/albumart/12",
            CoverArt.withKnownVersion("content://media/external/audio/albumart/12", known))
        assertNull(CoverArt.withKnownVersion(null, known))
    }

    @Test fun anEventFromAServerTheAppLeftIsDropped() {
        CoverArt.onCoverChanged(server, server, coverChanged(12L to 5))
        val other = "http://10.0.2.2:6390"
        // The old server's socket, heard after the switch: dropped, and the
        // old server's versions are forgotten (its track 12 is another song).
        assertEquals(emptySet<Long>(), CoverArt.onCoverChanged(server, other, coverChanged(12L to 9)))
        assertNull(CoverArt.knownVersion(12))
        assertEquals(setOf(12L), CoverArt.onCoverChanged(other, other, coverChanged(12L to 1)))
        CoverArt.serverIs(server)
        assertNull(CoverArt.knownVersion(12))
    }

    @Test fun thePlayersSongTakesTheNewCover() {
        val playing = PlayItem.fromTrack(12, "Song", "Main", null, 200.0, coverV = 4)
        assertEquals("/api/music/library/12/cover?v=4", playing.coverPath)
        val other = PlayItem.fromTrack(13, "Other", null, null, 100.0, coverV = 1)
        val episode = PlayItem.fromEpisode(12, "Episode", "Show", 60.0, "/api/podcasts/1/artwork", emptyList())
        CoverArt.onCoverChanged(server, server, coverChanged(12L to 5))
        assertEquals("/api/music/library/12/cover?v=5", CoverArt.refreshedItem(playing)?.coverPath)
        assertNull(CoverArt.refreshedItem(other))
        assertNull(CoverArt.refreshedItem(episode))
        // An unchanged version changes nothing (no swap, no reload of the artwork).
        assertNull(CoverArt.refreshedItem(PlayItem.fromTrack(12, "Song", "Main", null, 200.0, coverV = 5)))
        // The media session's artwork is the item's cover (its largest
        // smaller copy, CoverSize.XL), so it moves too.
        assertEquals(
            "$server/api/music/library/12/cover?v=5&size=xl",
            SessionMedia.artwork(CoverArt.refreshedItem(playing)!!) { server + it },
        )
    }

    @Test fun theAppIsWiredToTheEvent() {
        assertTrue(
            Bytecode.callers("com.domovoi.app.AppContainer", "com/domovoi/app/player/CoverArt", setOf("onCoverChanged"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.AppContainer", "com/domovoi/app/player/PlayerController", setOf("coversChanged"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.AppContainer", "com/domovoi/app/player/CoverArt", setOf("serverIs"))
                .isNotEmpty(),
        )
        // The player swaps the engine's item, which is what makes Media3's
        // bitmap loader (it keeps the last picture by URI) load the new one.
        assertTrue(
            Bytecode.callers("com.domovoi.app.player.PlayerController", "androidx/media3/exoplayer/ExoPlayer", setOf("replaceMediaItem"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.player.PlayerController", "com/domovoi/app/player/CoverArt", setOf("refreshedItem\$default", "refreshedItem"))
                .isNotEmpty(),
        )
        // Every cover drawn holds its model against the known versions.
        assertTrue(
            Bytecode.callers("com.domovoi.app.ui.components.CoverImageKt", "com/domovoi/app/player/CoverArt", setOf("withKnownVersion"))
                .isNotEmpty(),
        )
        // The library row and the drawer ask with the row's version.
        for (facade in listOf(
            "com.domovoi.app.ui.screens.music.LibraryTabKt",
            "com.domovoi.app.ui.screens.music.TrackDrawerKt",
        )) {
            assertTrue(facade, Bytecode.callers(facade, "com/domovoi/app/ui/screens/music/LibraryTrack", setOf("getCoverV")).isNotEmpty())
        }
    }
}
