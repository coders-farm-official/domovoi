package com.domovoi.app.player

import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * [LyricsRepository] against a fake web (lyrics-build CONTRACT [D1], [D12]):
 * a doc is cached (30, least recently used out), a refusal hides the
 * lyrics and is asked again next time, a doc that is still `checking`
 * expires after 60 s, and a room's read primes the track's cache.
 * Invented lyrics only.
 */
class LyricsRepositoryTest {
    private lateinit var server: MockWebServer
    private var now = 1_000_000L
    private lateinit var repo: LyricsRepository

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        val api = ApiClient(
            baseUrlProvider = { server.url("/").toString().trimEnd('/') },
            deviceTokenProvider = { "household-token-for-tests" },
        )
        repo = LyricsRepository(api, clock = { now })
    }

    @After fun down() = server.shutdown()

    private fun docJson(id: Long, status: String = "synced", checking: Boolean = false) = """
        {"track_id": $id, "status": "$status", "checking": $checking, "source": "sidecar",
         "source_label": "from Lantern Song.lrc",
         "lines": [{"t": 12400, "text": "the lantern hums beside the river door"},
                   {"t": 16850, "text": "and every copper kettle sings at dawn"}],
         "text": "the lantern hums beside the river door\nand every copper kettle sings at dawn",
         "updated_at": "2026-10-05T12:00:00+00:00"}
    """.trimIndent()

    private fun ok(body: String) = MockResponse().setBody(body).setHeader("Content-Type", "application/json")

    @Test fun aDocIsLoadedThenServedFromTheCache() = runBlocking {
        server.enqueue(ok(docJson(12)))
        val first = repo.forTrack(12)
        assertTrue(first is LyricsResult.Loaded)
        val doc = (first as LyricsResult.Loaded).doc
        assertEquals("synced", doc.status)
        assertEquals(2, doc.timedLines().size)

        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("/api/music/library/12/lyrics", req.path)
        // The household tier rides on every call.
        assertEquals("household-token-for-tests", req.getHeader("X-Device-Token"))

        assertEquals(first, repo.forTrack(12))
        assertEquals(doc, repo.cached(12))
        assertEquals(1, server.requestCount)
    }

    @Test fun refreshAsksAgain() = runBlocking {
        server.enqueue(ok(docJson(12)))
        server.enqueue(ok(docJson(12, status = "plain")))
        repo.forTrack(12)
        val again = repo.forTrack(12, refresh = true) as LyricsResult.Loaded
        assertEquals("plain", again.doc.status)
        assertEquals(2, server.requestCount)
    }

    @Test fun aRefusalHidesTheLyricsAndIsAskedAgainNextTime() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"household device token required"}"""))
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"forbidden"}"""))
        server.enqueue(ok(docJson(12)))
        assertEquals(LyricsResult.Hidden, repo.forTrack(12))
        assertEquals(LyricsResult.Hidden, repo.forTrack(12))
        assertNull(repo.cached(12))
        // Paired a moment later: the lyrics are there.
        assertTrue(repo.forTrack(12) is LyricsResult.Loaded)
        assertEquals(3, server.requestCount)
    }

    @Test fun aServerWithoutLyricsHidesThemTooButAMissingTrackFails() = runBlocking {
        // A server from before lyrics: no such route (FastAPI's own 404).
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
        assertEquals(LyricsResult.Hidden, repo.forTrack(12))
        // A lane without V021.
        server.enqueue(MockResponse().setResponseCode(503).setBody("""{"detail":"lyrics are not set up on this server yet"}"""))
        assertEquals(LyricsResult.Hidden, repo.forTrack(12))
        // The route is there; the track is not.
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"track 12 not found"}"""))
        assertEquals(LyricsResult.Failed, repo.forTrack(12))
    }

    @Test fun failuresFailAndAreNotCached() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}"""))
        server.enqueue(MockResponse().setResponseCode(503).setBody("""{"detail":"busy"}"""))
        // A 200 that is not a doc (its text must go nowhere, so nothing is thrown).
        server.enqueue(ok("""{"track_id": 12, "lines": "the lantern hums beside the river door"}"""))
        server.enqueue(ok("not json at all"))
        repeat(4) { assertEquals(LyricsResult.Failed, repo.forTrack(12)) }
        assertNull(repo.cached(12))
        server.shutdown()
        assertEquals(LyricsResult.Failed, repo.forTrack(12))
    }

    @Test fun aCheckingDocIsAskedAgainAfterSixtySeconds() = runBlocking {
        server.enqueue(ok(docJson(12, status = "none", checking = true)))
        server.enqueue(ok(docJson(12)))
        val looking = repo.forTrack(12) as LyricsResult.Loaded
        assertTrue(looking.doc.checking)

        now += 59_999
        assertEquals(looking, repo.forTrack(12))
        assertEquals(1, server.requestCount)

        now += 1
        val found = repo.forTrack(12) as LyricsResult.Loaded
        assertEquals("synced", found.doc.status)
        assertEquals(2, server.requestCount)

        // A doc that is done looking stays.
        now += 10 * 60_000
        assertEquals(found, repo.forTrack(12))
        assertEquals(2, server.requestCount)
    }

    @Test fun theCacheKeepsTheThirtyMostRecentlyUsed() = runBlocking {
        for (id in 1L..30L) {
            server.enqueue(ok(docJson(id)))
            repo.forTrack(id)
        }
        // Track 1 was used again, so 2 is the oldest when 31 comes in.
        assertNotNull(repo.cached(1))
        server.enqueue(ok(docJson(31)))
        repo.forTrack(31)
        assertNotNull(repo.cached(1))
        assertNull(repo.cached(2))
        assertNotNull(repo.cached(31))
        assertEquals(LyricsRepository.CACHE_SIZE + 1, server.requestCount)
    }

    @Test fun concurrentAsksForOneTrackShareOneRequest() = runBlocking {
        server.enqueue(ok(docJson(12)).setBodyDelay(200, TimeUnit.MILLISECONDS))
        val results = (1..3).map { async { repo.forTrack(12) } }.awaitAll()
        assertTrue(results.all { it is LyricsResult.Loaded })
        assertEquals(1, server.requestCount)
    }

    @Test fun aRoomsReadIsStampedOnReceiptAndPrimesTheTrack() = runBlocking {
        server.enqueue(
            ok(
                """
                {"room_id": "living room", "state": "play", "track_id": 12, "elapsed_sec": 42.37,
                 "duration_sec": 205, "read_at": "2026-10-05T12:00:00.123+00:00", "line_index": 0,
                 "lyrics": ${docJson(12)}}
                """.trimIndent(),
            ),
        )
        now = 5_000_000
        val r = repo.forRoom("living room")!!
        assertEquals("/api/music/now-playing/living%20room/lyrics", server.takeRequest().path)
        assertEquals(5_000_000L, r.receivedAtMs)
        assertEquals(12L, r.trackId)
        // The room card and the player ask for track 12 next: no request.
        assertTrue(repo.forTrack(12) is LyricsResult.Loaded)
        assertEquals(1, server.requestCount)
    }

    @Test fun aRoomThatCannotBeReadIsNull() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"room den is not provisioned"}"""))
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"household device token required"}"""))
        server.enqueue(ok("""{"room_id": "den", "state": "stop", "track_id": null, "lyrics": null}"""))
        assertNull(repo.forRoom("den"))
        assertNull(repo.forRoom("den"))
        val idle = repo.forRoom("den")!!
        assertNull(idle.lyrics)
        assertNull(idle.trackId)
    }

    @Test fun eachServerHasItsOwnCache() = runBlocking {
        var base = server.url("/").toString().trimEnd('/')
        val api = ApiClient(baseUrlProvider = { base })
        val switching = LyricsRepository(api, clock = { now })
        server.enqueue(ok(docJson(12)))
        switching.forTrack(12)
        val other = MockWebServer().also { it.start() }
        try {
            base = other.url("/").toString().trimEnd('/')
            assertNull(switching.cached(12))
            other.enqueue(ok(docJson(12, status = "plain")))
            assertEquals("plain", (switching.forTrack(12) as LyricsResult.Loaded).doc.status)
            assertEquals(1, other.requestCount)
        } finally {
            other.shutdown()
        }
    }
}
