package com.domovoi.app.listens

import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * The outbox and its sending (MOBILE-HANDOFF-2026-11-12 §2): rows survive
 * a restart, at most 500 (oldest out), batches of 200, kept on a network
 * failure or 401/403/408/429/5xx, dropped on 422 (any other 4xx), deleted
 * on 200, and only for the server they came from.
 */
class ListenOutboxTest {
    @get:Rule val tmp = TemporaryFolder()
    private lateinit var web: MockWebServer
    private lateinit var base: String
    private lateinit var file: File

    @Before fun up() {
        web = MockWebServer().also { it.start() }
        base = web.url("/").toString().trimEnd('/')
        file = File(tmp.root, "listens-outbox.json")
    }

    @After fun down() = runCatching { web.shutdown() }.let { }

    private fun row(i: Int, server: String = base) = OutboxRow(
        id = "row-$i", server = server, trackId = 1000L + i, deviceId = "android-1a2b",
        startedAt = "2026-11-12T18:03:11Z", playedSec = 187, durationSec = 214, outcome = "finished",
    )

    private fun fill(n: Int, server: String = base) = ListenOutbox(file).also { o -> repeat(n) { o.add(row(it, server)) } }

    private fun sender(outbox: ListenOutbox, onOff: () -> Unit = {}) =
        ListenSender(outbox, ApiClient(baseUrlProvider = { base }), server = { base }, onRecordingOff = onOff)

    private fun ok(recording: Boolean = true) =
        MockResponse().setHeader("Content-Type", "application/json")
            .setBody("""{"accepted":1,"dropped":0,"recording":$recording}""")

    @Test fun rowsSurviveARestart() {
        fill(3)
        val again = ListenOutbox(file) // a new process reads the same file
        assertEquals(listOf("row-0", "row-1", "row-2"), again.all().map { it.id })
        assertEquals(1001L, again.all()[1].trackId)
    }

    @Test fun atMost500TheOldestGoFirst() {
        val o = fill(503)
        val rows = o.all()
        assertEquals(500, rows.size)
        assertEquals("row-3", rows.first().id)
        assertEquals("row-502", rows.last().id)
    }

    @Test fun theWireRowIsTheContracts() {
        val wire = row(0).copy(listenerPersonId = 3).toWire()
        assertEquals(1000L, wire["track_id"]!!.jsonPrimitive.long)
        assertEquals("android-1a2b", wire["device_id"]!!.jsonPrimitive.content)
        assertEquals("2026-11-12T18:03:11Z", wire["started_at"]!!.jsonPrimitive.content)
        assertEquals(187, wire["played_sec"]!!.jsonPrimitive.int)
        assertEquals(214, wire["duration_sec"]!!.jsonPrimitive.int)
        assertEquals("finished", wire["outcome"]!!.jsonPrimitive.content)
        assertEquals(3L, wire["listener_person_id"]!!.jsonPrimitive.long)
        assertEquals("android", wire["player"]!!.jsonPrimitive.content)
        assertEquals("explicit", wire["origin"]!!.jsonPrimitive.content)
        assertFalse("the outbox's own id and server stay here", "id" in wire || "server" in wire)
        assertFalse("as_household only when picked", "as_household" in wire)
        assertTrue("as_household" in row(0).copy(asHousehold = true).toWire())
    }

    @Test fun deletedOn200InBatchesOf200() = runBlocking {
        val o = fill(500)
        repeat(3) { web.enqueue(ok()) }
        assertEquals(ListenSender.Result.Sent, sender(o).flush())
        val sizes = (1..3).map { Json.parseToJsonElement(web.takeRequest().body.readUtf8()).jsonObject["listens"]!!.jsonArray.size }
        assertEquals(listOf(200, 200, 100), sizes)
        assertTrue(o.all().isEmpty())
    }

    @Test fun keptWhenTheNetworkFails() = runBlocking {
        val o = fill(500)
        web.shutdown()
        assertEquals(ListenSender.Result.Kept, sender(o).flush())
        assertEquals(500, o.all().size)
    }

    @Test fun keptOnRefusalsThatPass() = runBlocking {
        for (code in listOf(401, 403, 408, 429, 500, 503)) {
            val o = fill(5)
            web.enqueue(MockResponse().setResponseCode(code).setBody("""{"detail":"no"}"""))
            assertEquals("$code", ListenSender.Result.Kept, sender(o).flush())
            assertEquals("$code keeps", 5, o.all().size)
            file.delete()
        }
    }

    @Test fun droppedOn422() = runBlocking {
        val o = fill(5)
        web.enqueue(MockResponse().setResponseCode(422).setBody(
            """{"detail":"android-1a2b: more seconds played than have passed since its first song began"}"""))
        sender(o).flush()
        assertTrue("a batch that would fail forever goes", o.all().isEmpty())
        assertEquals(1, web.requestCount)
    }

    @Test fun onlyTheSentIdsAreDeleted() = runBlocking {
        val o = fill(2)
        // A song ends while the flush is on its way: its row must stay, and
        // go in the next batch rather than be deleted with the first.
        var first = true
        web.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): MockResponse {
                if (first) { first = false; o.add(row(99)) }
                return ok()
            }
        }
        sender(o).flush()
        val firstBatch = Json.parseToJsonElement(web.takeRequest().body.readUtf8()).jsonObject["listens"]!!.jsonArray
        val secondBatch = Json.parseToJsonElement(web.takeRequest().body.readUtf8()).jsonObject["listens"]!!.jsonArray
        assertEquals(2, firstBatch.size)
        assertEquals(listOf(1099L), secondBatch.map { it.jsonObject["track_id"]!!.jsonPrimitive.long })
        assertTrue(o.all().isEmpty())
    }

    @Test fun aRowWaitsForItsOwnServer() = runBlocking {
        val o = ListenOutbox(file)
        o.add(row(1, server = "http://10.0.0.9:6369"))
        o.add(row(2))
        web.enqueue(ok())
        sender(o).flush()
        assertEquals(1, web.requestCount)
        val sent = Json.parseToJsonElement(web.takeRequest().body.readUtf8()).jsonObject["listens"]!!.jsonArray
        assertEquals(1, sent.size)
        assertEquals(1002L, sent[0].jsonObject["track_id"]!!.jsonPrimitive.long)
        assertEquals(listOf("row-1"), o.all().map { it.id })
    }

    @Test fun recordingOffIsHeard() = runBlocking {
        val o = fill(1)
        web.enqueue(ok(recording = false))
        var off = false
        sender(o) { off = true }.flush()
        assertTrue(off)
        assertTrue(o.all().isEmpty())
    }
}
