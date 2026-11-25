package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.ZoneId
import java.util.Locale

/**
 * Photo and video results on the room display (1.5.0, `room_display.media`,
 * domovoi/room_media.py), against docs/uml/satellite-fixtures.json's
 * `room_display_frames`, the file the core's tests read: what is parsed,
 * what is shown and for how long, and which thumbnail paths the room fetches.
 */
class RoomDisplayMediaTest {
    private val frames: JsonObject by lazy {
        val path = System.getProperty("domovoi.satellite.fixtures")
            ?: File(System.getProperty("domovoi.repo.dir") ?: "../..", "docs/uml/satellite-fixtures.json").path
        (Json.parseToJsonElement(File(path).readText()) as JsonObject)["room_display_frames"]!!.jsonObject
    }

    private fun feed(json: String): RoomFeed = (RoomProtocol.parse(json) as RoomProtocol.ServerFrame.Display).feed
    private fun feed(o: JsonObject): RoomFeed = feed(o.toString())

    private val ready = RoomEngine.Status(link = RoomEngine.Link.Ready, wakeReady = true)

    private fun state(f: RoomFeed?, elapsed: Long, width: Int = 411) = RoomDisplayModel.build(
        RoomDisplayModel.Input(
            status = ready, running = true, needsLock = false, wakeWord = "hey_jarvis", approvalCode = null,
            feed = f, volume = 40, nowMs = 0L, elapsedMs = elapsed, zone = ZoneId.of("UTC"), locale = Locale.US,
            use24h = false, widthDp = width, heightDp = 891,
        ),
    )

    @Test fun theFixturesMediaFrameReads() {
        val m = feed(frames["with_media"]!!.jsonObject).media!!
        assertEquals(7L, m.id)
        assertEquals(12, m.count)
        assertFalse(m.more)
        assertEquals(90_000L, m.expiresInMs)
        assertTrue(m.said.startsWith("I found 12 photos"))
        assertEquals(2, m.items.size)
        val (photo, video) = m.items
        assertEquals(MediaKind.IMAGE, photo.kind)
        assertEquals("/v1/room-media/PLACEHOLDERtokenAAAAAAAAAAAAAAAA", photo.thumb)
        assertEquals("A beach with a red umbrella.", photo.caption)
        assertEquals("2025-07-14", photo.date)
        assertNull(photo.atS)
        assertEquals(MediaKind.VIDEO, video.kind)
        assertNull(video.caption)
        assertEquals(42.0, video.atS!!, 0.0)
        // Every key the fixture lists is one this app reads.
        assertEquals(setOf("id", "said", "count", "more", "expires_in_ms", "items"), frames["media_keys"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet())
        assertEquals(setOf("kind", "thumb", "caption", "date", "at_s"), frames["media_item_keys"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet())
    }

    @Test fun beforeMediaAndClearedHaveNone() {
        val before = feed(frames["before_media"]!!.jsonObject)
        val cleared = feed(frames["media_cleared"]!!.jsonObject)
        assertNull(before.media)
        assertNull(cleared.media)
        // The rest of the frame is the same with or without media.
        assertEquals(before.timers, feed(frames["with_media"]!!.jsonObject).timers)
        assertEquals(before.timers, cleared.timers)
    }

    /** A bad `media` is no media; the frame still applies. */
    @Test fun aBadMediaIsNoMediaNeverAFailedFrame() {
        val timers = """"timers":[{"id":7,"kind":"timer","label":"pasta","remaining_ms":1000}]"""
        for (bad in listOf(
            "\"oops\"", "[]", "{}", """{"id":"x","items":[]}""", """{"id":1}""", """{"id":1,"items":"no"}""",
            """{"id":1,"items":[{"kind":"gif","thumb":"/v1/room-media/AAAAAAAAAAAAAAAAAAAAAAAA"}]}""",
        )) {
            val f = feed("""{"type":"room_display",$timers,"media":$bad}""")
            assertNull(bad, f.media)
            assertEquals(bad, 1, f.timers.size)
        }
        // At most six items, captions trimmed, a photo's `at_s` dropped, a
        // date that is not one dropped, an unknown thumb a blank tile.
        val items = (1..9).joinToString(",") {
            """{"kind":"image","thumb":"https://example.com/x.jpg","caption":"  sea  ","date":"last summer","at_s":3}"""
        }
        val m = feed("""{"type":"room_display","media":{"id":3,"said":"s","count":2,"more":true,"items":[$items]}}""").media!!
        assertEquals(6, m.items.size)
        assertEquals(6, m.count)
        assertTrue(m.more)
        assertEquals(90_000L, m.expiresInMs)
        m.items.forEach {
            assertNull(it.thumb)
            assertEquals("sea", it.caption)
            assertNull(it.date)
            assertNull(it.atS)
        }
    }

    /** Exactly the core's route plus one of its tokens (domovoi/room_media.py
     *  TOKEN_RE `^[A-Za-z0-9_-]{20,64}$`), and the cover route as before. */
    @Test fun theRoomFetchesOnlyTheCoresThumbnailRoute() {
        for (good in listOf(
            "/v1/room-media/PLACEHOLDERtokenAAAAAAAAAAAAAAAA",
            "/v1/room-media/" + "a".repeat(20), "/v1/room-media/" + "Z9_-".repeat(16),
        )) assertTrue(good, RoomDisplayFeed.isMediaThumbPath(good) && RoomDisplayFeed.isImagePath(good))
        for (bad in listOf(
            "/v1/room-media/", "/v1/room-media/" + "a".repeat(19), "/v1/room-media/" + "a".repeat(65),
            "/v1/room-media/abc%2Fdefghijklmnopqrstuv", "/v1/room-media/abcdefghijklmnopqrstuv/x",
            "/v1/room-media/abcdefghijklmnopqrstuv?x=1", "/v1/room-media/abcdefghijklmnopqrstuv#x",
            "/v1/room-media/../music/cover/1", "/v1/room-media/abcdefghij.lmnopqrstuv", "//evil.example/v1/room-media/abcdefghijklmnopqrstuv",
            "http://192.168.1.20:6370/v1/room-media/abcdefghijklmnopqrstuv", "/v1/room-media2/abcdefghijklmnopqrstuv",
            "/api/images/thumb?path=x", " /v1/room-media/abcdefghijklmnopqrstuv",
        )) assertFalse(bad, RoomDisplayFeed.isMediaThumbPath(bad) || RoomDisplayFeed.isImagePath(bad))
        assertTrue(RoomDisplayFeed.isImagePath("/v1/music/cover/12"))
        assertFalse("the cover route takes no token", RoomDisplayFeed.isArtPath("/v1/room-media/PLACEHOLDERtokenAAAAAAAAAAAAAAAA"))
    }

    @Test fun resultsShowUntilTheyExpireAndANewIdReplacesThem() {
        val f = feed(frames["with_media"]!!.jsonObject).copy(receivedAt = 10_000L)
        val shown = state(f, elapsed = 10_000L).media!!
        assertEquals(7L, shown.id)
        assertTrue(state(f, elapsed = 10_000L).hasCards)
        assertNotNull("still there a moment before", state(f, elapsed = 99_999L).media)
        assertNull("gone after expires_in_ms, with no new frame", state(f, elapsed = 100_000L).media)
        // Newer results replace them; a cleared frame hides them.
        val next = feed("""{"type":"room_display","media":{"id":8,"said":"two","count":1,"more":false,"expires_in_ms":90000,
            "items":[{"kind":"image","thumb":null,"caption":null,"date":null,"at_s":null}]}}""").copy(receivedAt = 20_000L)
        assertEquals(8L, state(next, elapsed = 20_000L).media!!.id)
        assertNull(state(feed(frames["media_cleared"]!!.jsonObject), elapsed = 0L).media)
        // Not connected: nothing the server said is shown.
        val off = RoomDisplayModel.build(
            RoomDisplayModel.Input(
                status = RoomEngine.Status(link = RoomEngine.Link.Connecting), running = true, needsLock = false,
                wakeWord = "hey_jarvis", approvalCode = null, feed = f, volume = null, nowMs = 0L, elapsedMs = 10_000L,
                zone = ZoneId.of("UTC"), locale = Locale.US, use24h = false, widthDp = 411, heightDp = 891,
            ),
        )
        assertNull(off.media)
    }

    @Test fun eachTileSaysWhatTheKioskSays() {
        val m = state(feed(frames["with_media"]!!.jsonObject).copy(receivedAt = 0L), elapsed = 0L).media!!
        val (photo, video) = m.tiles
        assertEquals("Might show: A beach with a red umbrella.", photo.caption)
        assertEquals("Jul 14, 2025", photo.date)
        assertFalse(photo.video)
        assertNull(photo.at)
        assertEquals("photo, Might show: A beach with a red umbrella., Jul 14, 2025", photo.description)
        assertTrue(video.video)
        assertEquals("0:42", video.at)
        assertNull(video.caption)
        assertEquals("video at 0:42, Aug 2, 2025", video.description)
        // 12 found, 2 sent: the rest are on the Photos page.
        assertEquals(RoomDisplayModel.MEDIA_MORE, m.more)
        assertEquals("Might show: a dog", RoomDisplayModel.mightShow("a dog"))
        assertNull(RoomDisplayModel.mediaDate("2025-13-40", Locale.US))
        // All sent and no more: no line.
        val all = feed("""{"type":"room_display","media":{"id":9,"said":"s","count":1,"more":false,"expires_in_ms":90000,
            "items":[{"kind":"image","thumb":null}]}}""").copy(receivedAt = 0L)
        assertNull(state(all, elapsed = 0L).media!!.more)
    }
}
