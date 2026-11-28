package com.domovoi.app.away

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.net.InetAddress
import java.net.Socket

/**
 * Away chat end to end against a real house: the web app, the away
 * listener and the core's relay, running the house's own chat code against
 * the test database (a maintainer's local harness: `-Pdomovoi.away.house=`
 * names its house.json; skipped otherwise). This phone's code all the way:
 * registration with a device key, POST /api/away/enroll, GET status, the
 * DAC1 handshake, chat.list, chat.send with its streamed reply, a picture,
 * a reply picked up again after a dropped connection, chat.cancel, and
 * turning away chat off (the house's BYE revoked, then silence). No Tor:
 * the listener's port stands in for the onion.
 */
class AwayLoopbackE2ETest {
    private val houseFile = System.getProperty("domovoi.away.house")

    private class Http(private val base: String, private val token: String) : AwayHttp {
        val client = OkHttpClient()
        var deviceKey: String? = null
        var deviceId: String = ""

        private fun go(method: String, path: String, body: JsonObject?): JsonElement {
            val b = Request.Builder().url(base + path)
                .header("X-Requested-With", "DomovoiApp")
                .header("X-Device-Token", token)
                .header("X-Domovoi-Device-Id", deviceId)
            deviceKey?.let { b.header("X-Domovoi-Device-Key", it) }
            b.method(method, body?.toString()?.toRequestBody("application/json".toMediaType()))
            client.newCall(b.build()).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (!r.isSuccessful) throw AwayHttpError(r.code, text)
                return Json.parseToJsonElement(text.ifBlank { "null" })
            }
        }

        override suspend fun post(path: String, body: JsonObject) = go("POST", path, body)
        override suspend fun get(path: String) = go("GET", path, null)
        override suspend fun delete(path: String) = go("DELETE", path, null)
    }

    private class Book : AwayKeyBook {
        var e: AwayEnrolment? = null
        override fun awayEnrolmentFor(server: String) = e
        override fun rememberAwayEnrolment(server: String, e: AwayEnrolment?): Boolean { this.e = e; return true }
    }

    private class Direct(private val port: Int) : AwayTransport {
        override suspend fun open(e: AwayEnrolment): AwayStream = SocketStream(Socket(InetAddress.getLoopbackAddress(), port))
        override fun stop() {}
    }

    private fun text(m: JsonObject?) = m?.get("content")?.jsonPrimitive?.content

    @Test fun aPhoneChatsWithItsHouseFromAway() = runBlocking {
        assumeTrue("no house (-Pdomovoi.away.house)", houseFile != null)
        val house = Json.parseToJsonElement(File(houseFile!!).readText()).jsonObject
        val base = house["web_url"]!!.jsonPrimitive.content
        val http = Http(base, house["token"]!!.jsonPrimitive.content)
        val log = { s: String -> println("[e2e] $s") }

        // Register as a household phone and get the device key (1.5.0).
        http.deviceId = "android-e2e-" + System.nanoTime().toString(16).takeLast(8)
        val reg = http.post("/api/devices/register", buildJsonObject {
            put("device_id", http.deviceId); put("want_device_key", true); put("name", "e2e phone")
        }).jsonObject
        http.deviceKey = reg["device_key"]!!.jsonPrimitive.content
        log("registered ${http.deviceId}")

        // Enrol on the "home network".
        val book = Book()
        val r = AwayEnroll.enroll(http, book, base)
        assertTrue("enrol: $r", r is EnrollResult.Ok)
        val e = (r as EnrollResult.Ok).enrolment
        assertEquals(house["onion"]!!.jsonPrimitive.content, e.onion)
        assertEquals(house["house_noise_pub"]!!.jsonPrimitive.content, e.houseNoisePub)
        assertEquals(80, e.port)
        val st = AwayEnroll.status(http)!!
        assertEquals(AwayHouseStatus("on", true, "ready"), st)
        log("enrolled: $e, status $st")

        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val silences = mutableListOf<String?>()
        val session = AwaySession(Direct(house["listener_port"]!!.jsonPrimitive.content.toInt()), { book.e }, "1.5.0-e2e", scope,
            onSilence = { silences += it })
        session.acquire()

        // chat.send, streamed.
        val q = "What's on the shopping list?"
        var t0 = System.currentTimeMillis()
        val sent = withTimeout(60_000) { session.client.send(null, q).toList() }
        val stored = sent.first() as SendUpdate.Stored
        val chatId = stored.chatId!!
        val deltas = sent.filterIsInstance<SendUpdate.Delta>()
        assertTrue("streamed in several deltas: ${deltas.size}", deltas.size > 2)
        assertEquals("you said: $q", deltas.joinToString("") { it.text })
        assertEquals("you said: $q", text((sent.last() as SendUpdate.Done).message))
        log("send: chat $chatId, ${deltas.size} deltas, ${System.currentTimeMillis() - t0} ms")

        // chat.list and chat.history.
        val threads = session.client.listAll()
        assertTrue(threads.any { it["id"]!!.jsonPrimitive.content.toLong() == chatId })
        val h = session.client.history(chatId)
        val msgs = (h["messages"] as JsonArray).map { it.jsonObject }
        assertEquals(listOf("user", "assistant"), msgs.map { it["role"]!!.jsonPrimitive.content })
        log("list: ${threads.size} thread(s); history: ${msgs.size} messages")

        // A picture, with no words.
        // A JPEG the harness made (the app makes its own with Bitmap: AwayPicture).
        val pic = File(house["test_jpeg"]!!.jsonPrimitive.content).readBytes()
        val withPic = withTimeout(60_000) { session.client.send(chatId, "", pic).toList() }
        val picStored = withPic.first() as SendUpdate.Stored
        assertEquals("[{\"kind\":\"picture\"}]", picStored.body["user_message"]!!.jsonObject["images"].toString())
        assertTrue("picture reply: $withPic", withPic.last() is SendUpdate.Done)
        log("picture: ${pic.size} bytes sent; reply \"${text((withPic.last() as SendUpdate.Done).message)}\"")

        // A reply picked up again after the connection drops mid-reply.
        val long = (1..24).joinToString(" ") { "word$it" }
        val firstPart = StringBuilder()
        val drop = withTimeout(60_000) {
            session.client.send(chatId, long).first { u ->
                if (u is SendUpdate.Delta) firstPart.append(u.text)
                u is SendUpdate.Delta && firstPart.length > 12
            }
        }
        assertTrue(drop is SendUpdate.Delta)
        session.stopNow() // the network drops
        session.acquire()
        delay(300)
        t0 = System.currentTimeMillis()
        val resumed = withTimeout(60_000) { session.client.stream(chatId).toList() }
        val rs = resumed.first() as SendUpdate.Stored
        assertEquals(true, rs.body["streaming"]!!.jsonPrimitive.content.toBoolean())
        val again = resumed.filterIsInstance<SendUpdate.Delta>().joinToString("") { it.text }
        assertTrue("picked up from the start: $again", again.startsWith("you said: word1"))
        assertEquals("you said: $long", again)
        assertTrue(resumed.last() is SendUpdate.Done)
        log("resume: picked up after a drop, ${resumed.size} updates, ${System.currentTimeMillis() - t0} ms")
        // Nothing running now.
        assertEquals(listOf(SendUpdate.NotRunning), session.client.stream(chatId).toList())

        // chat.cancel stops a running reply; it ends with error "cancelled".
        val running = async(Dispatchers.IO) { session.client.send(chatId, long).toList() }
        delay(1_200)
        assertTrue("cancelled", session.client.cancel(chatId))
        val cancelled = withTimeout(30_000) { running.await() }
        val end = cancelled.last()
        assertTrue("ends with cancelled: $end", end is SendUpdate.Failed && end.code == "cancelled")
        log("cancel: ${cancelled.filterIsInstance<SendUpdate.Delta>().size} deltas before it stopped")

        // Turning it off: the open connection gets BYE revoked, the next connect silence.
        val open = session.connection()
        assertTrue(AwayEnroll.unenroll(http, Book().also { it.e = e }, base))
        val bye = withTimeout(15_000) { open.closed.first { it != null } }
        assertEquals("revoked", bye!!.code)
        val gone = AwaySession(Direct(house["listener_port"]!!.jsonPrimitive.content.toInt()), { e }, "1.5.0-e2e", scope,
            onSilence = { silences += "silence" })
        gone.acquire()
        val refused = runCatching { gone.connection() }
        assertTrue(refused.exceptionOrNull() is AwayDropped)
        assertEquals(AwayLink.Failed(AwayText.UNREACHABLE), gone.link.value)
        assertEquals(AwayHouseStatus("on", false, null), AwayEnroll.status(http))
        assertEquals(AwayText.SET_UP_AGAIN, AwayEnroll.status(http)!!.line)
        log("off: BYE ${bye.code}, then silence; status enrolled:false; silences $silences")
        session.stopNow()
        gone.stopNow()
    }
}
