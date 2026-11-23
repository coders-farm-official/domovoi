package com.domovoi.app.player

import com.domovoi.app.net.ApiClient
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
 * The cover's smaller copies (`size=s|m|l|xl`, web/backend/api/music.py
 * `track_cover`): which copy each drawn size asks for, the URL that asks
 * for it (the version kept), and that a small and a large copy of one cover
 * never share a cache entry while a miss counts for every size.
 */
class CoverSizeTest {

    private val server = "http://192.168.1.20:6369"
    private val api = ApiClient(baseUrlProvider = { server })

    @After fun clean() {
        CoverArt.forgetVersions()
        CoverArt.forgetMisses()
    }

    // ── Which copy ───────────────────────────────────────────────────────

    @Test fun theSmallestCopySharpAtXxhdpi() {
        assertEquals(CoverSize.S, CoverSize.forDp(36f))    // mini player
        assertEquals(CoverSize.S, CoverSize.forDp(40f))    // library row
        assertEquals(CoverSize.S, CoverSize.forDp(53f))    // 159 px
        assertEquals(CoverSize.M, CoverSize.forDp(54f))    // 162 px
        assertEquals(CoverSize.M, CoverSize.forDp(56f))    // queue sheet
        assertEquals(CoverSize.M, CoverSize.forDp(64f))    // track drawer
        assertEquals(CoverSize.M, CoverSize.forDp(106f))
        assertEquals(CoverSize.L, CoverSize.forDp(107f))
        assertEquals(CoverSize.L, CoverSize.forDp(112f))   // player tab
        assertEquals(CoverSize.L, CoverSize.forDp(170f))
        assertEquals(CoverSize.XL, CoverSize.forDp(171f))
        assertEquals(CoverSize.XL, CoverSize.forDp(600f))  // nothing bigger: the largest copy
    }

    @Test fun theBucketsAreTheServers() {
        assertEquals(listOf("s" to 160, "m" to 320, "l" to 512, "xl" to 768),
            CoverSize.entries.map { it.param to it.edgePx })
    }

    // ── The URL ──────────────────────────────────────────────────────────

    @Test fun sizeFollowsTheVersion() {
        assertEquals("/api/music/library/7/cover?v=3&size=s", CoverArt.sized(CoverArt.libraryPath(7, 3), CoverSize.S))
        assertEquals("/api/music/library/7/cover?v=0&size=xl", CoverArt.sized("/api/music/library/7/cover?v=0", CoverSize.XL))
        // An older server's row: no version, still a small copy (an older
        // server ignores the parameter and sends the original).
        assertEquals("/api/music/library/7/cover?size=m", CoverArt.sized(CoverArt.libraryPath(7), CoverSize.M))
        assertEquals(
            "$server/api/music/library/7/cover?v=3&size=l",
            CoverArt.sized(CoverArt.model(CoverArt.libraryPath(7, 3), api::absolute), CoverSize.L),
        )
    }

    @Test fun aSizeAlreadyNamedIsReplaced() {
        assertEquals("/api/music/library/7/cover?v=3&size=xl",
            CoverArt.sized("/api/music/library/7/cover?v=3&size=s", CoverSize.XL))
        assertEquals("/api/music/library/7/cover?size=s",
            CoverArt.sized("/api/music/library/7/cover?size=l", CoverSize.S))
    }

    @Test fun otherArtIsLeftAlone() {
        assertNull(CoverArt.sized(null, CoverSize.S))
        for (art in listOf(
            "/api/audiobooks/41/cover",
            "$server/api/audiobooks/41/cover",
            "/api/podcasts/subscriptions/3/artwork",
            "https://feeds.example/show/art.jpg",
            "content://media/external/audio/albumart/42",
            "$server/api/music/library/7/audio",
        )) {
            assertEquals(art, CoverArt.sized(art, CoverSize.S))
        }
    }

    @Test fun unsizedIsTheCoverItself() {
        assertEquals("/api/music/library/7/cover?v=3", CoverArt.unsized("/api/music/library/7/cover?v=3&size=s"))
        assertEquals("/api/music/library/7/cover", CoverArt.unsized("/api/music/library/7/cover?size=s"))
        assertEquals("/api/music/library/7/cover?v=3", CoverArt.unsized("/api/music/library/7/cover?v=3"))
        assertEquals("/api/audiobooks/41/cover", CoverArt.unsized("/api/audiobooks/41/cover"))
    }

    // ── With the version (V059) ──────────────────────────────────────────

    @Test fun aNewerVersionKeepsTheSize() {
        CoverArt.onCoverChanged(server, server, coverChanged(12L to 5))
        val known = CoverArt.versions.value
        assertEquals("$server/api/music/library/12/cover?v=5&size=s",
            CoverArt.withKnownVersion("$server/api/music/library/12/cover?v=4&size=s", known))
        assertEquals("$server/api/music/library/12/cover?v=5&size=m",
            CoverArt.withKnownVersion("$server/api/music/library/12/cover?size=m", known))
        val same = "$server/api/music/library/12/cover?v=5&size=l"
        assertEquals(same, CoverArt.withKnownVersion(same, known))
        assertTrue(CoverArt.isVersionedModel(same))
        assertFalse(CoverArt.isVersionedModel("$server/api/music/library/12/cover?size=l"))
    }

    // ── Caches ───────────────────────────────────────────────────────────

    @Test fun aSmallAndALargeCopyNeverShareAMemoryKey() {
        val small = CoverArt.sized("$server/api/music/library/12/cover?v=4", CoverSize.S)!!
        val large = CoverArt.sized("$server/api/music/library/12/cover?v=4", CoverSize.XL)!!
        assertNotEquals(CoverArt.memoryKey(small, 0), CoverArt.memoryKey(large, 0))
        // A versioned copy is still drawn from memory whatever the epoch.
        assertEquals(CoverArt.memoryKey(small, 0), CoverArt.memoryKey(small, 3))
        val bareSmall = CoverArt.sized("$server/api/music/library/12/cover", CoverSize.S)!!
        val bareLarge = CoverArt.sized("$server/api/music/library/12/cover", CoverSize.L)!!
        assertNotEquals(CoverArt.memoryKey(bareSmall, 0), CoverArt.memoryKey(bareLarge, 0))
        assertNotEquals(CoverArt.memoryKey(bareSmall, 0), CoverArt.memoryKey(bareSmall, 1))
        // Still the same cover, so one copy may stand in while the other loads.
        assertTrue(CoverArt.sameCover(small, large))
    }

    @Test fun noCoverAtOneSizeIsNoCoverAtAny() {
        val url = "$server/api/music/library/9/cover?v=2"
        CoverArt.noteFailure(CoverArt.sized(url, CoverSize.S)!!, 404)
        assertTrue(CoverArt.isMissing(url))
        assertTrue(CoverArt.isMissing(CoverArt.sized(url, CoverSize.XL)!!))
        assertFalse(CoverArt.isMissing("$server/api/music/library/9/cover?v=3&size=s"))
    }

    @Test fun aSizedVersionedCoverIsStillKeptWithoutAsking() {
        val mock = MockWebServer().apply { start() }
        try {
            mock.enqueue(coverAnswer())
            mock.enqueue(coverAnswer())
            val client = OkHttpClient.Builder().addInterceptor(CoverArt.revalidate).build()
            fun get(path: String) = client.newCall(Request.Builder().url(mock.url(path)).build())
                .execute().also { it.body?.close() }
            // v= then size=: versioned, kept as the server sent it.
            assertFalse(get("/api/music/library/12/cover?v=4&size=s").cacheControl.noCache)
            // No version: checked by its ETag as before.
            assertTrue(get("/api/music/library/12/cover?size=s").cacheControl.noCache)
        } finally {
            mock.shutdown()
        }
    }

    // ── Wiring ───────────────────────────────────────────────────────────

    @Test fun coverImageAndTheMediaSessionAskForACopy() {
        assertTrue(
            Bytecode.callers("com.domovoi.app.ui.components.CoverImageKt", "com/domovoi/app/player/CoverArt", setOf("sized"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.player.SessionMedia", "com/domovoi/app/player/CoverArt", setOf("sized"))
                .isNotEmpty(),
        )
    }

    @Test fun theSessionArtworkIsTheLargestCopy() {
        val item = PlayItem.fromTrack(12, "Song", "Main", null, 200.0, coverV = 4)
        assertEquals("$server/api/music/library/12/cover?v=4&size=xl", SessionMedia.artwork(item, api::absolute))
        // The queue item itself keeps the plain cover; each surface sizes it.
        assertEquals("/api/music/library/12/cover?v=4", item.coverPath)
        val episode = PlayItem.fromEpisode(3, "Episode", "Show", 60.0, "/api/podcasts/1/artwork", emptyList())
        assertEquals("$server/api/podcasts/1/artwork", SessionMedia.artwork(episode, api::absolute))
    }

    private fun coverChanged(vararg tracks: Pair<Long, Int>) = DomovoiJson.parseToJsonElement(
        tracks.joinToString(",", """{"tracks": [""", "]}") { (id, v) ->
            """{"id": $id, "cover_v": $v, "cover_url": "/api/music/library/$id/cover?v=$v"}"""
        },
    )

    private fun coverAnswer() = MockResponse()
        .setHeader("ETag", "\"3f2a9c-c1731574800000000000\"")
        .setHeader("Cache-Control", "public, max-age=86400")
        .setHeader("Content-Type", "image/webp")
        .setBody("webp-bytes")
}
