package com.domovoi.app.net

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/**
 * The per-device key (1.5.0, PRIVATE-CHATS.md §2.1, core-0056/core-0063):
 * every registration asks for it, the key answered once is kept and
 * presented from then on, a lost key starts the phone over under a new
 * device id at that server only, an older server leaves the phone keyless,
 * and the header goes only to the active server's key routes, only after
 * it proved itself, never in room mode and never anywhere else.
 */
class DeviceKeyTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    /** The server's register (devices.py `_device_key`), with its rows. */
    private open class FakeServer(val keys: Boolean = true) : Dispatcher() {
        /** device id -> its key (null: a row without one). */
        val rows = HashMap<String, String?>()
        val registrations = CopyOnWriteArrayList<Triple<String, Boolean?, String?>>()
        var minted = 0

        @Synchronized
        override fun dispatch(request: RecordedRequest): MockResponse {
            if (request.path != "/api/devices/register") return MockResponse().setBody("{}")
            val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
            val id = body["device_id"]!!.jsonPrimitive.content
            val want = body[WANT_DEVICE_KEY]?.jsonPrimitive?.booleanOrNull
            val presented = request.getHeader(DEVICE_KEY_HEADER)
            registrations += Triple(id, want, presented)
            val stored = rows[id]
            if (id !in rows) rows[id] = null
            var key: String? = null
            var ok: Boolean? = null
            if (keys) {
                if (stored != null) {
                    ok = stored == presented
                } else if (want == true) {
                    key = "dk-${++minted}-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    rows[id] = key
                    ok = true
                }
            }
            fun q(v: String?) = if (v == null) "null" else "\"$v\""
            val extra = if (keys) ""","device_key":${q(key)},"device_key_ok":${ok ?: "null"}""" else ""
            return MockResponse().setHeader("Content-Type", "application/json").setBody(
                """{"device_id":"$id","name":"Pixel 8","platform":"android","shared_screen":false,""" +
                    """"owner_person_id":null,"owner_person_name":null,"owner_claimed_at":null,"claim_key":null$extra}""",
            )
        }
    }

    private lateinit var web: MockWebServer
    private lateinit var base: String
    private lateinit var fake: FakeServer
    private val book = DeviceRegistryTest.MapBook()
    private lateinit var api: ApiClient

    private fun start(server: FakeServer = FakeServer()) {
        fake = server
        web = MockWebServer().also { it.dispatcher = server; it.start() }
        base = web.url("/").toString().trimEnd('/')
        api = ApiClient({ base }, { "household-abc" }, deviceKeyForServer = { book.deviceKeyFor(it) })
    }

    @After fun down() {
        HouseholdLock.roomModeActive = false
        if (::web.isInitialized) web.shutdown()
    }

    private suspend fun register() = registerWith(api, book, book, base, "Pixel 8", "Android 15; Pixel 8")

    // ── Registration ─────────────────────────────────────────────────────

    @Test fun everyRegistrationAsksForTheKey() = runBlocking {
        start()
        assertEquals(true, registerPayload("android-1a2b", "Pixel 8", "ua")[WANT_DEVICE_KEY]!!.jsonPrimitive.booleanOrNull)
        // At pairing, and on every start after: always asked.
        repeat(3) { register() }
        assertEquals(listOf(true, true, true), fake.registrations.map { it.second })
    }

    @Test fun theKeyAnsweredOnceIsKeptAndPresentedFromThenOn() = runBlocking {
        start()
        register()
        val key = book.deviceKeyFor(base)
        assertEquals("dk-1-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", key)
        assertNull("the first registration had none to present", fake.registrations[0].third)
        // A repeat registration presents it, and changes nothing.
        register()
        register()
        assertEquals(key, fake.registrations[1].third)
        assertEquals(key, fake.registrations[2].third)
        assertEquals("one key minted, ever", 1, fake.minted)
        assertEquals(key, book.deviceKeyFor(base))
        assertEquals("android-1a2b", fake.registrations.last().first)
        assertEquals(0, book.startedOver)
    }

    @Test fun anUpgradedPhonesKeylessRowGetsItsKeyWithoutPairingAgain() = runBlocking {
        start()
        // A 1.4.x app registered without asking: the row exists, keyless.
        fake.rows["android-1a2b"] = null
        register()
        assertEquals("same id, no new device", "android-1a2b", fake.registrations.single().first)
        assertEquals("dk-1-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", book.deviceKeyFor(base))
    }

    @Test fun aLostKeyStartsOverUnderANewIdAndRegistersAgain() = runBlocking {
        start()
        // The row has a key this phone no longer holds (a reinstall, cleared
        // storage); another server's id and key are this phone's too.
        fake.rows["android-1a2b"] = "dk-old-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        book.deviceKeys["http://other:6369"] = "other-key"
        register()
        assertEquals(2, fake.registrations.size)
        assertEquals("android-1a2b", fake.registrations[0].first)
        val fresh = fake.registrations[1].first
        assertNotEquals("a NEW device id", "android-1a2b", fresh)
        assertEquals(fresh, book.deviceIdFor(base))
        assertEquals("the new row's key is kept", fake.rows[fresh], book.deviceKeyFor(base))
        assertEquals("the old row keeps its key (its chats stay, for admins)",
            "dk-old-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", fake.rows["android-1a2b"])
        assertEquals("another server is untouched", "other-key", book.deviceKeyFor("http://other:6369"))
        assertEquals("android-1a2b", book.deviceIdFor("http://other:6369"))
        // And the next start is just a repeat.
        register()
        assertEquals(fresh, fake.registrations[2].first)
        assertEquals(book.deviceKeyFor(base), fake.registrations[2].third)
        assertEquals(1, book.startedOver)
    }

    @Test fun aWrongHeldKeyIsALostKeyToo() = runBlocking {
        start()
        fake.rows["android-1a2b"] = "dk-real-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
        book.deviceKeys[base] = "dk-wrong-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"
        register()
        assertEquals(1, book.startedOver)
        assertEquals(fake.rows[book.deviceIdFor(base)], book.deviceKeyFor(base))
    }

    @Test fun aSecondLostAnswerDoesNotLoop() = runBlocking {
        start(object : FakeServer() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                super.dispatch(request)
                return MockResponse().setBody("""{"device_id":"x","name":"n","device_key":null,"device_key_ok":false}""")
            }
        })
        register()
        assertEquals("the first try and one start-over, then keyless", 2, fake.registrations.size)
        assertNull(book.deviceKeyFor(base))
    }

    @Test fun anOlderServerLeavesThePhoneKeyless() = runBlocking {
        start(FakeServer(keys = false))
        register()
        register()
        assertNull(book.deviceKeyFor(base))
        assertEquals("no start-over against a server that says nothing", 0, book.startedOver)
        assertEquals(listOf("android-1a2b", "android-1a2b"), fake.registrations.map { it.first })
    }

    @Test fun registrationsRacingAtStartMintOneKeyAndLoseNothing() = runBlocking {
        start()
        // The shell's start, the Connection panel and the People page can all
        // register at once: one after the other, the later ones present the
        // key the first one kept.
        (1..4).map { async(kotlinx.coroutines.Dispatchers.IO) { register() } }.awaitAll()
        assertEquals(1, fake.minted)
        assertEquals(0, book.startedOver)
        assertEquals(setOf("android-1a2b"), fake.registrations.map { it.first }.toSet())
    }

    @Test fun theAnswerIsReadWithoutKeepingTheKeyInAPrintableRow() {
        val answer = Json.parseToJsonElement(
            """{"device_id":"a","device_key":"dk-secret-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE","device_key_ok":true}""",
        ) as JsonObject
        val read = deviceKeyAnswer(answer)
        assertTrue(read is DeviceKeyAnswer.Minted)
        assertFalse("never in a log line", read.toString().contains("secret"))
        assertFalse(answer.decode<DeviceRow>().toString().contains("secret"))
        assertEquals(DeviceKeyAnswer.Ok, deviceKeyAnswer(Json.parseToJsonElement("""{"device_key":null,"device_key_ok":true}""") as JsonObject))
        assertEquals(DeviceKeyAnswer.Lost, deviceKeyAnswer(Json.parseToJsonElement("""{"device_key":null,"device_key_ok":false}""") as JsonObject))
        assertEquals(DeviceKeyAnswer.None, deviceKeyAnswer(Json.parseToJsonElement("""{"device_key_ok":null}""") as JsonObject))
        assertEquals(DeviceKeyAnswer.None, deviceKeyAnswer(Json.parseToJsonElement("""{}""") as JsonObject))
        // A value OkHttp would refuse is no key at all.
        assertEquals(DeviceKeyAnswer.Ok, deviceKeyAnswer(Json.parseToJsonElement("""{"device_key":"bad key\n","device_key_ok":true}""") as JsonObject))
    }

    @Test fun aNewDeviceIdIsLongAndWellFormed() {
        val a = newDeviceId()
        val b = newDeviceId()
        assertNotEquals(a, b)
        assertTrue(a, Regex("^android-[0-9a-f]{12}$").matches(a))
    }

    // ── Where the header goes ──────────────────────────────────────────────

    private fun MockWebServer.next(): RecordedRequest =
        takeRequest(5, TimeUnit.SECONDS) ?: error("no request reached the server")

    private fun keyedApi(gate: TokenGate? = null, token: String? = "household-abc") =
        ApiClient({ base }, { token }, gate = gate, deviceKeyForServer = { if (it == base) "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF" else "dk-elsewhere" })

    private fun plain(): MockWebServer =
        MockWebServer().also { it.start() }.also { w -> web = w; base = w.url("/").toString().trimEnd('/') }

    @Test fun theKeyRoutesCarryItAndNothingElseDoes() = runBlocking {
        val w = plain()
        val api = keyedApi()
        val keyed = listOf(
            "/api/chat/threads", "/api/chat/threads/7/messages", "/api/chat/uploads/abc", "/api/help/deliveries?person_id=3",
            "/api/auth/phone-grant", "/api/auth/phone-grant/exchange", "/api/people/3/music-mix",
            "/api/music/listens/people/3", "/api/music/up-next/sessions", "/api/music/play-track",
            "/api/faces/people/3/forget", "/api/media/search?q=x", "/api/devices/register", "/api/devices/android-1/person",
        )
        val plainRoutes = listOf("/api/music/library", "/api/timers", "/api/chatter", "/api/health", "/api/people", "/api/music/playlists")
        (keyed + plainRoutes).forEach { w.enqueue(MockResponse().setBody("{}")); api.get(it) }
        keyed.forEach { assertEquals(it, "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", w.next().getHeader(DEVICE_KEY_HEADER)) }
        plainRoutes.forEach { assertNull(it, w.next().getHeader(DEVICE_KEY_HEADER)) }
    }

    @Test fun theStateSocketCarriesIt() {
        val w = plain()
        val api = keyedApi()
        w.enqueue(MockResponse().withWebSocketUpgrade(object : okhttp3.WebSocketListener() {}))
        val socket = api.http.newWebSocket(api.wsRequest("ws://${w.hostName}:${w.port}/ws/state"), object : okhttp3.WebSocketListener() {})
        assertEquals("dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", w.next().getHeader(DEVICE_KEY_HEADER))
        socket.cancel()
    }

    @Test fun neverToAnotherHostOrPortEvenWhenACallerSetsIt() {
        val w = plain()
        val api = keyedApi()
        val other = MockWebServer().also { it.start() }
        try {
            // Another host name for the same socket, and another port on the
            // same host (where the drop-in socket keeps the token): no key.
            other.enqueue(MockResponse().setBody("{}"))
            api.http.newCall(okhttp3.Request.Builder().url("http://127.0.0.1:${other.port}/api/chat/threads")
                .header(DEVICE_KEY_HEADER, "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF").build()).execute().close()
            assertNull(other.next().getHeader(DEVICE_KEY_HEADER))
            other.enqueue(MockResponse().withWebSocketUpgrade(object : okhttp3.WebSocketListener() {}))
            val ws = api.http.newWebSocket(api.wsRequest("ws://${w.hostName}:${other.port}/ws/state"), object : okhttp3.WebSocketListener() {})
            assertNull("the token's other-port exception is not the key's", other.next().getHeader(DEVICE_KEY_HEADER))
            ws.cancel()
            // A caller's own header on a plain route of the active server is
            // stripped: the interceptor decides.
            w.enqueue(MockResponse().setBody("{}"))
            api.http.newCall(okhttp3.Request.Builder().url("$base/api/music/library")
                .header(DEVICE_KEY_HEADER, "forged").build()).execute().close()
            assertNull(w.next().getHeader(DEVICE_KEY_HEADER))
            // And a forged one on a key route is replaced by the real one.
            w.enqueue(MockResponse().setBody("{}"))
            api.http.newCall(okhttp3.Request.Builder().url("$base/api/chat/threads")
                .header(DEVICE_KEY_HEADER, "forged").build()).execute().close()
            assertEquals("dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", w.next().getHeader(DEVICE_KEY_HEADER))
        } finally {
            other.shutdown()
        }
    }

    @Test fun aRedirectOffTheServerDropsIt() = runBlocking {
        val w = plain()
        val api = keyedApi()
        val other = MockWebServer().also { it.start() }
        try {
            w.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://127.0.0.1:${other.port}/api/chat/threads"))
            other.enqueue(MockResponse().setBody("{}"))
            api.get("/api/chat/threads")
            assertEquals("dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", w.next().getHeader(DEVICE_KEY_HEADER))
            assertNull(other.next().getHeader(DEVICE_KEY_HEADER))
        } finally {
            other.shutdown()
        }
    }

    @Test fun neverInRoomMode() = runBlocking {
        val w = plain()
        val api = keyedApi()
        HouseholdLock.roomModeActive = true
        w.enqueue(MockResponse().setBody("{}"))
        api.get("/api/chat/threads")
        assertNull(w.next().getHeader(DEVICE_KEY_HEADER))
        HouseholdLock.roomModeActive = false
        w.enqueue(MockResponse().setBody("{}"))
        api.get("/api/chat/threads")
        assertEquals("dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", w.next().getHeader(DEVICE_KEY_HEADER))
    }

    @Test fun itWaitsForTheServerToProveItselfTokenOrNot() = runBlocking {
        val w = plain()
        var refuse = true
        var asked = 0
        val gate = TokenGate { asked++; if (refuse) throw IOException("server identity mismatch") }
        // Even an unpaired phone's key waits for the proof.
        val api = keyedApi(gate, token = null)
        try {
            api.get("/api/chat/threads")
            fail("expected the gate's IOException")
        } catch (e: IOException) {
            assertEquals("server identity mismatch", e.message)
        }
        assertEquals("nothing left the phone", 0, w.requestCount)
        refuse = false
        w.enqueue(MockResponse().setBody("{}"))
        api.get("/api/chat/threads")
        assertEquals("dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", w.next().getHeader(DEVICE_KEY_HEADER))
        // A keyless route of an unpaired phone asks nobody, as before.
        val before = asked
        w.enqueue(MockResponse().setBody("{}"))
        api.get("/api/health")
        assertEquals(before, asked)
    }

    @Test fun theKeyIsTheOneTheActiveServerIssued() = runBlocking {
        val w = plain()
        val keys = mapOf(base to "dk-A-GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG")
        var active = base
        val api = ApiClient({ active }, { "household-abc" }, deviceKeyForServer = { keys[it] })
        w.enqueue(MockResponse().setBody("{}"))
        api.get("/api/chat/threads")
        assertEquals("dk-A-GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG", w.next().getHeader(DEVICE_KEY_HEADER))
        // Another server is active now: a request still addressed to the old
        // one is out of scope, and the new one has no key of its own.
        val other = MockWebServer().also { it.start() }
        try {
            active = other.url("/").toString().trimEnd('/')
            other.enqueue(MockResponse().setBody("{}"))
            api.get("/api/chat/threads")
            assertNull(other.next().getHeader(DEVICE_KEY_HEADER))
            w.enqueue(MockResponse().setBody("{}"))
            api.http.newCall(okhttp3.Request.Builder().url("$base/api/chat/threads").build()).execute().close()
            assertNull(w.next().getHeader(DEVICE_KEY_HEADER))
        } finally {
            other.shutdown()
        }
    }

    @Test fun theRouteListMatchesWholeSegments() {
        assertTrue(DeviceKeyRoutes.carries("/api/chat"))
        assertTrue(DeviceKeyRoutes.carries("/api/chat/threads?scope=all"))
        assertFalse(DeviceKeyRoutes.carries("/api/chatter"))
        assertFalse(DeviceKeyRoutes.carries("/api/music/playlists"))
        assertTrue(DeviceKeyRoutes.carries("/api/music/play"))
        assertFalse(DeviceKeyRoutes.carries("/api/people/3"))
        assertTrue(DeviceKeyRoutes.carries("/api/people/3/music-mix"))
        assertFalse(DeviceKeyRoutes.carries("/api/people/3/music-mix/extra"))
        assertFalse(DeviceKeyRoutes.carries("/v1/dropin/kitchen"))
    }
}
