package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.add
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayInputStream

/**
 * The phone → server upload behind "Your device" (net/PhoneUpload.kt): the
 * file streams into the multipart body with the fields each door wants,
 * progress is whole percent and reported only when it moves, both doors'
 * replies read the same, and the per-row tracker can't be started twice or
 * revived by a late progress report.
 */
class PhoneUploadTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
    }

    @After fun down() = server.shutdown()

    @Test fun progressIsWholePercentAndOnlyWhenItMoves() {
        val meter = ProgressMeter(total = 1000)
        assertEquals(0, meter.step(1))
        assertNull("still 0%", meter.step(5))
        assertEquals(1, meter.step(10))
        assertEquals(50, meter.step(500))
        assertNull(meter.step(509))
        assertEquals(100, meter.step(1000))
        assertNull("never past 100 nor repeated", meter.step(1200))
    }

    @Test fun anUnknownLengthReportsNoPercent() {
        val meter = ProgressMeter(total = -1)
        assertNull(meter.step(10))
        assertNull(meter.step(10_000))
    }

    @Test fun theFileStreamsAsThePartFilesAfterTheDoorsFields() = runBlocking {
        server.enqueue(
            MockResponse().setBody("""{"saved":["clip.mp4"],"skipped":[],"reindex_triggered":false}"""),
        )
        val bytes = ByteArray(300_000) { (it % 251).toByte() }
        val seen = mutableListOf<Int>()
        val reply = uploadStream(
            api, FILES_UPLOAD_PATH,
            listOf("library_id" to "core:videos", "path" to "", "device_id" to "phone-123"),
            name = "clip.mp4", mime = "video/mp4", length = bytes.size.toLong(),
            open = { ByteArrayInputStream(bytes) },
            onPercent = { seen += it },
        )
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/files/upload", req.path)
        assertEquals("the preflight-forcing header the files door requires", "DomovoiApp", req.getHeader("X-Requested-With"))
        val body = req.body.readByteArray()
        val text = String(body, Charsets.ISO_8859_1)
        // Each field is its own part, before the file's.
        val fields = listOf("library_id" to "core:videos", "path" to "", "device_id" to "phone-123")
        fields.forEach { (k, v) ->
            val at = text.indexOf("name=\"$k\"")
            assertTrue("field $k is sent", at >= 0)
            assertTrue("field $k comes before the file", at < text.indexOf("name=\"files\""))
            assertTrue("field $k carries \"$v\"", text.indexOf("\r\n\r\n$v\r\n", at) in at..(at + 80))
        }
        assertTrue(text.contains("name=\"files\"; filename=\"clip.mp4\""))
        assertTrue(text.contains("Content-Type: video/mp4"))
        // The file's bytes, whole and in order, follow its part header.
        val start = text.indexOf("filename=\"clip.mp4\"").let { text.indexOf("\r\n\r\n", it) + 4 }
        assertTrue(body.copyOfRange(start, start + bytes.size).contentEquals(bytes))
        assertEquals("ends at 100", 100, seen.last())
        assertEquals("reported once per percent at most", seen.distinct(), seen)
        assertTrue("climbs", seen.zipWithNext().all { (a, b) -> b > a })
        assertEquals(UploadResult(saved = 1, skipped = 0, reindexTriggered = false), parseUploadReply(reply))
    }

    @Test fun aRefusalComesBackAsTheServersOwnWords() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(403)
                .setBody("""{"detail":"Pixel 7 isn't allowed to change files"}"""),
        )
        try {
            uploadStream(
                api, MUSIC_UPLOAD_PATH, listOf("device_id" to "phone-123"),
                name = "song.mp3", mime = "audio/mpeg", length = 3,
                open = { ByteArrayInputStream(byteArrayOf(1, 2, 3)) }, onPercent = {},
            )
            fail("a 403 must throw")
        } catch (e: ApiException) {
            assertEquals(403, e.status)
            assertTrue(e.body.contains("isn't allowed to change files"))
        }
        assertEquals("/api/music/library/upload", server.takeRequest().path)
    }

    @Test fun bothDoorsRepliesReadTheSame() {
        // /api/files/upload lists what it saved...
        val files = buildJsonObject {
            put("saved", buildJsonArray { add("a.pdf"); add("b.pdf") })
            put("skipped", buildJsonArray { add("c: bad filename") })
            put("reindex_triggered", false)
        }
        assertEquals(UploadResult(2, 1, false), parseUploadReply(files))
        // ...the music door counts it, and names the files under `files`.
        val music = buildJsonObject {
            put("saved", 1)
            put("files", buildJsonArray { add("song.mp3") })
            put("skipped", buildJsonArray { })
            put("reindex_triggered", true)
        }
        assertEquals(UploadResult(1, 0, true), parseUploadReply(music))
        assertEquals(UploadResult(0, 0, false), parseUploadReply(JsonNull))
    }

    @Test fun aRowsUploadRunsOnceAndALateReportCannotReviveIt() {
        val uploads = PhoneUploads()
        assertFalse(uploads.inFlight("track:1"))
        assertTrue(uploads.begin("track:1"))
        assertFalse("a second tap starts nothing", uploads.begin("track:1"))
        assertTrue(uploads.inFlight("track:1"))
        assertNull("size not known yet", uploads.percent("track:1"))
        uploads.update("track:1", 42)
        assertEquals(42, uploads.percent("track:1"))
        uploads.update("track:1", 140)
        assertEquals(100, uploads.percent("track:1"))
        assertTrue("other rows are independent", uploads.begin("track:2"))
        uploads.end("track:1")
        uploads.update("track:1", 99)
        assertFalse("a report after the end is ignored", uploads.inFlight("track:1"))
        assertNull(uploads.percent("track:1"))
        assertTrue("and it can be sent again", uploads.begin("track:1"))
    }
}
