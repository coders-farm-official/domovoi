package com.domovoi.app.roommode

import com.domovoi.app.net.Ed25519
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The listening models are taken only from a list our server signed, never
 * older than the last one, only when the app's own pins agree, and only
 * bodies that hash to what the list says; a trained model is re-hashed
 * before every load (security review F7/F8: these checks had no test).
 */
class RoomModelsTest {
    @get:Rule val tmp = TemporaryFolder()

    private val seed = ByteArray(32) { (it * 11 + 5).toByte() }
    private val fingerprint = RoomIdentity.fingerprintOf(Ed25519.publicKey(seed))

    private class Book : RoomModels.ModelBook {
        val seen = HashMap<String, RoomIdentity.Seen>()
        val trained = HashMap<String, String>()
        override fun seen(record: String) = seen[record]
        override fun remember(record: String, accepted: RoomIdentity.Accepted) {
            seen[record] = RoomIdentity.Seen(accepted.serial, accepted.digest)
        }
        override fun trainedSha(name: String) = trained[name]
        override fun setTrainedSha(name: String, sha: String) {
            trained[name] = sha
        }
    }

    private lateinit var server: MockWebServer
    private val requests = CopyOnWriteArrayList<String>()
    private var baseList: Map<String, String> = emptyMap()
    private var baseSerial = 7L
    private var trainedList: Map<String, String> = emptyMap()
    private val bodies = HashMap<String, ByteArray>()

    private fun envelope(channel: String, manifest: Map<String, String>, serial: Long): String {
        val m = JsonObject(manifest.mapValues { JsonPrimitive(it.value) })
        val issued = 1_760_000_000L
        val sig = Ed25519.sign(seed, RoomIdentity.manifestMessageV2(channel, m, issued, serial))
        return buildJsonObject {
            put("algorithm", "ed25519")
            put("public_key", Base64.getEncoder().encodeToString(Ed25519.publicKey(seed)))
            put("fingerprint", fingerprint)
            put("channel", channel)
            put("manifest", m)
            put("issued_at", issued)
            put("serial", serial)
            put("signature", "unused")
            put("signature_v2", Base64.getEncoder().encodeToString(sig))
        }.toString()
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                requests += path
                return when {
                    path == "/v1/wake-base/manifest.sig" -> MockResponse().setBody(envelope(RoomIdentity.WAKE_BASE_CHANNEL, baseList, baseSerial))
                    path == "/v1/wake-models/manifest.sig" -> MockResponse().setBody(envelope(RoomIdentity.WAKE_MODELS_CHANNEL, trainedList, 3))
                    bodies.containsKey(path) -> MockResponse().setBody(Buffer().write(bodies.getValue(path)))
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }

    @After
    fun tearDown() = server.shutdown()

    private val core get() = "http://127.0.0.1:${server.port}"

    private fun models(dir: File, book: Book) = RoomModels(dir, book)

    @Test
    fun anOlderListThanTheLastAcceptedIsRefusedAndNothingIsFetched() {
        val book = Book()
        book.seen[RoomIdentity.WAKE_BASE_CHANNEL] = RoomIdentity.Seen(10, null)
        baseSerial = 5
        baseList = mapOf("melspectrogram.onnx" to RoomModels.PINS.getValue("melspectrogram.onnx").first)
        val dir = tmp.newFolder()
        val r = models(dir, book).sync(RoomNetwork.BASE, core, fingerprint, "hey_jarvis")
        assertTrue(r.toString(), r is RoomModels.Outcome.Failed && r.reason.contains("older"))
        assertTrue(requests.none { it == "/v1/wake-base/melspectrogram.onnx" })
        assertEquals(10L, book.seen[RoomIdentity.WAKE_BASE_CHANNEL]!!.serial)
    }

    @Test
    fun aListedDigestTheAppDoesNotPinIsRefused() {
        val book = Book()
        baseList = mapOf("melspectrogram.onnx" to "00".repeat(32))
        bodies["/v1/wake-base/melspectrogram.onnx"] = ByteArray(16)
        val dir = tmp.newFolder()
        val r = models(dir, book).sync(RoomNetwork.BASE, core, fingerprint, "hey_jarvis")
        assertTrue(r.toString(), r is RoomModels.Outcome.Failed && r.reason.contains("does not pin"))
        assertTrue(requests.none { it == "/v1/wake-base/melspectrogram.onnx" })
        assertFalse(File(dir, "melspectrogram.onnx").exists())
    }

    @Test
    fun aBodyThatIsNotTheListedFileIsNeverWritten() {
        val book = Book()
        val (sha, size) = RoomModels.PINS.getValue("melspectrogram.onnx")
        baseList = mapOf("melspectrogram.onnx" to sha)
        bodies["/v1/wake-base/melspectrogram.onnx"] = ByteArray(size.toInt())   // the right size, not the file
        val dir = tmp.newFolder()
        val r = models(dir, book).sync(RoomNetwork.BASE, core, fingerprint, "hey_jarvis")
        assertTrue(r.toString(), r is RoomModels.Outcome.Failed && r.reason.contains("not the file"))
        assertTrue(requests.contains("/v1/wake-base/melspectrogram.onnx"))
        assertFalse(File(dir, "melspectrogram.onnx").exists())
        assertTrue(dir.listFiles().orEmpty().none { it.name.endsWith(".part") })
    }

    @Test
    fun aListSignedByAnotherKeyIsRefused() {
        val book = Book()
        val dir = tmp.newFolder()
        val other = "SHA256:X+zrZv/IbzjZUnhsbWlsecLbwjndTpG0ZynXOif7V+k"
        val r = models(dir, book).sync(RoomNetwork.BASE, core, other, "hey_jarvis")
        assertTrue(r.toString(), r is RoomModels.Outcome.Failed)
        assertNull(book.seen[RoomIdentity.WAKE_BASE_CHANNEL])
    }

    @Test
    fun aTrainedModelIsTakenOnlyAsListedAndReHashedBeforeEveryLoad() {
        val book = Book()
        val good = "trained model body".toByteArray()
        trainedList = mapOf("hey_house.onnx" to RoomModels.sha256(good))
        bodies["/v1/wake-models/hey_house.onnx"] = "something else".toByteArray()
        val dir = tmp.newFolder()
        val m = models(dir, book)
        val bad = m.sync(RoomNetwork.BASE, core, fingerprint, "hey_house")
        assertTrue(bad.toString(), bad is RoomModels.Outcome.Failed)
        assertFalse(File(dir, "hey_house.onnx").exists())
        assertNull(book.trained["hey_house.onnx"])

        bodies["/v1/wake-models/hey_house.onnx"] = good
        val ok = m.sync(RoomNetwork.BASE, core, fingerprint, "hey_house")
        // The base models are not on this server: the room says so.
        assertTrue(ok.toString(), ok is RoomModels.Outcome.Missing)
        assertTrue(m.trainedModelIntact("hey_house.onnx"))
        File(dir, "hey_house.onnx").writeBytes("tampered".toByteArray())
        assertFalse("a changed file is not loaded", m.trainedModelIntact("hey_house.onnx"))
        assertNull("nothing loads without its base models", m.local("hey_house"))
    }
}
