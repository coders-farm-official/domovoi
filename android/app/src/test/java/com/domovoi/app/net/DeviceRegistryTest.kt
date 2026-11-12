package com.domovoi.app.net

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.coroutines.runBlocking
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

/**
 * "This is me" on the phone (MOBILE-HANDOFF-2026-11-12 §1): the register
 * body carries no owner (an app start must not undo a claim made
 * elsewhere), the server's answer is what the phone stores as its owner,
 * per server, and the claim key it hands back once is kept, sent, and
 * dropped by the web's rules (data.js DeviceIdentity.rememberOwner).
 */
class DeviceRegistryTest {
    private lateinit var web: MockWebServer
    private lateinit var api: ApiClient
    private lateinit var base: String
    private val book = MapBook()

    /** [ClaimBook] as a map, the way Prefs keeps it (by normalised address). */
    class MapBook : ClaimBook {
        val owners = HashMap<String, DeviceOwner>()
        val keys = HashMap<String, String>()
        val shared = HashMap<String, Boolean>()
        private fun k(s: String) = s.trim().trimEnd('/')
        override fun deviceOwnerFor(server: String) = owners[k(server)]
        override fun claimKeyFor(server: String) = keys[k(server)]
        override fun rememberDeviceOwner(server: String, owner: DeviceOwner?, claimKey: String?) {
            if (owner == null) owners.remove(k(server)) else owners[k(server)] = owner
            if (claimKey == null) keys.remove(k(server)) else keys[k(server)] = claimKey
        }
        override fun setSharedScreen(url: String, shared: Boolean) { this.shared[k(url)] = shared }
    }

    @Before fun up() {
        web = MockWebServer().also { it.start() }
        base = web.url("/").toString().trimEnd('/')
        api = ApiClient(baseUrlProvider = { base })
    }

    @After fun down() = web.shutdown()

    /** A device row exactly as web/backend/api/devices.py answers it. */
    private fun row(
        owner: Long?,
        name: String? = null,
        claimedAt: String? = null,
        key: String? = null,
        shared: Boolean = false,
    ): String {
        fun q(v: String?) = if (v == null) "null" else "\"$v\""
        return """{"device_id":"android-1a2b","name":"Pixel 8","platform":"android",
            "user_agent":"Android 15; Pixel 8","first_seen_at":"2026-11-01T10:00:00Z",
            "last_seen_at":"2026-11-12T18:00:00.123456Z","shared_screen":$shared,
            "owner_person_id":${owner ?: "null"},"owner_person_name":${q(name)},
            "owner_claimed_at":${q(claimedAt)},"claim_key":${q(key)}}"""
    }

    private fun json(code: Int, body: String) =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    private suspend fun register() =
        registerWith(api, book, base, "android-1a2b", "Pixel 8", "Android 15; Pixel 8")

    // ── The register body ────────────────────────────────────────────────

    @Test fun theRegisterBodyHasNoOwner() = runBlocking {
        val b = registerPayload("android-1a2b", "Pixel 8", "Android 15; Pixel 8")
        assertFalse("left out, so the server keeps the person it has", "owner_person_id" in b)
        assertEquals("android", (b["platform"] as JsonPrimitive).content)

        web.enqueue(json(200, row(null)))
        register()
        val sent = web.takeRequest()
        assertEquals("/api/devices/register", sent.path)
        val body = api.let { kotlinx.serialization.json.Json.parseToJsonElement(sent.body.readUtf8()).jsonObject }
        assertFalse("owner_person_id" in body)
        assertEquals("android-1a2b", body["device_id"]!!.jsonPrimitive.content)
    }

    // ── The answer is the truth, per server ─────────────────────────────

    @Test fun theAnswersOwnerIsStoredForThatServer() = runBlocking {
        book.rememberDeviceOwner("http://other:6369", DeviceOwner(9, "Sam"), "other-key")
        web.enqueue(json(200, row(3, "Alex", "2026-11-12T17:00:00Z")))
        register()
        assertEquals(DeviceOwner(3, "Alex", "2026-11-12T17:00:00Z"), book.deviceOwnerFor(base))
        assertEquals("another server's owner and key untouched", DeviceOwner(9, "Sam"), book.deviceOwnerFor("http://other:6369"))
        assertEquals("other-key", book.claimKeyFor("http://other:6369"))

        // An admin set it to nobody from the dashboard: the next start says so.
        web.enqueue(json(200, row(null)))
        register()
        assertNull(book.deviceOwnerFor(base))
    }

    @Test fun anAnswerFromAServerWithoutOwnersChangesNothing() = runBlocking {
        book.rememberDeviceOwner(base, DeviceOwner(3, "Alex"), "k1")
        web.enqueue(json(200, """{"device_id":"android-1a2b","name":"Pixel 8","shared_screen":false}"""))
        register()
        assertEquals(DeviceOwner(3, "Alex"), book.deviceOwnerFor(base))
        assertEquals("k1", book.claimKeyFor(base))
    }

    // ── "This is me" / "not me" ───────────────────────────────────────────

    @Test fun thisIsMeSendsThePutWithTheDeviceIdAndStoresTheKey() = runBlocking {
        web.enqueue(json(200, row(3, "Alex", "2026-11-12T18:03:11Z", key = "k-minted_once-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")))
        val owner = claimThisPhone(api, book, base, "android-1a2b", 3)
        val sent = web.takeRequest()
        assertEquals("PUT", sent.method)
        assertEquals("/api/devices/android-1a2b/person", sent.path)
        assertEquals("android-1a2b", sent.getHeader(DEVICE_ID_HEADER))
        assertNull("no key held yet: none sent", sent.getHeader(CLAIM_KEY_HEADER))
        assertEquals(3L, kotlinx.serialization.json.Json.parseToJsonElement(sent.body.readUtf8())
            .jsonObject["person_id"]!!.jsonPrimitive.longOrNull)
        assertEquals(DeviceOwner(3, "Alex", "2026-11-12T18:03:11Z"), owner)
        assertEquals("k-minted_once-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", book.claimKeyFor(base))
    }

    @Test fun notMeSendsTheHeldKeyAndTheKeyGoesWithTheOwner() = runBlocking {
        book.rememberDeviceOwner(base, DeviceOwner(3, "Alex"), "held-key")
        web.enqueue(json(200, row(null)))
        val owner = claimThisPhone(api, book, base, "android-1a2b", null)
        val sent = web.takeRequest()
        assertEquals("held-key", sent.getHeader(CLAIM_KEY_HEADER))
        assertEquals("android-1a2b", sent.getHeader(DEVICE_ID_HEADER))
        assertTrue(sent.body.readUtf8().contains("\"person_id\":null"))
        assertNull(owner)
        assertNull("nobody's: the key is dead", book.claimKeyFor(base))
    }

    @Test fun aKeyIsDroppedWhenAnAdminChangedThePerson() = runBlocking {
        book.rememberDeviceOwner(base, DeviceOwner(3, "Alex"), "held-key")
        // Same person, no new key (a plain registration): kept.
        web.enqueue(json(200, row(3, "Alex")))
        register()
        assertEquals("held-key", book.claimKeyFor(base))
        // Another person, no key (an admin set it): dropped.
        web.enqueue(json(200, row(5, "Sam")))
        register()
        assertEquals(DeviceOwner(5, "Sam"), book.deviceOwnerFor(base))
        assertNull(book.claimKeyFor(base))
    }

    @Test fun aRefusedClaimIsNeverShownAsTheOwner() = runBlocking {
        web.enqueue(json(403, """{"detail":"Alex is already claimed by another phone or browser. An admin can add this one too."}"""))
        try {
            claimThisPhone(api, book, base, "android-1a2b", 3)
            fail("a refusal throws")
        } catch (e: ApiException) {
            assertEquals(ClaimRefusal.TakenByAnother, claimRefusal(e.status, apiDetail(e)))
            assertEquals(
                "Alex is already claimed by another phone or browser. An admin can add this one too.",
                apiDetail(e),
            )
        }
        assertNull("still nobody's", book.deviceOwnerFor(base))
    }

    @Test fun cantProveDropsTheStoredKey() = runBlocking {
        book.rememberDeviceOwner(base, DeviceOwner(3, "Alex"), "stale-key")
        web.enqueue(json(403, """{"detail":"this phone or browser can't prove it is Alex's (its claim key is missing or out of date). An admin can change it, or set it to nobody so it can say \"this is me\" again."}"""))
        runCatching { claimThisPhone(api, book, base, "android-1a2b", null) }
        assertEquals("stale-key", web.takeRequest().getHeader(CLAIM_KEY_HEADER))
        assertNull(book.claimKeyFor(base))
        assertEquals("the owner stands: the server said so", DeviceOwner(3, "Alex"), book.deviceOwnerFor(base))
    }

    @Test fun theRefusalsAreTold() {
        assertEquals(ClaimRefusal.SharedScreen, claimRefusal(409, "this is a shared screen, so it is the whole household's"))
        assertEquals(ClaimRefusal.Gone, claimRefusal(404, "person 3 not found"))
        assertEquals(ClaimRefusal.NotThisDevice, claimRefusal(403, "only that phone or browser itself, or an admin, can change whose it is"))
        assertEquals(ClaimRefusal.CantProve, claimRefusal(403,
            "this phone or browser said it was Alex's before claims had keys: say \"this is me\" again on Alex's People page first"))
    }

    @Test fun aClaimForAServerTheAppLeftIsNotSent() = runBlocking {
        book.rememberDeviceOwner(base, DeviceOwner(3, "Alex"), "held-key")
        try {
            claimThisPhone(api, book, "http://10.0.0.9:6369", "android-1a2b", null)
            fail("not sent")
        } catch (e: ServerChangedException) { /* expected */ }
        assertEquals(0, web.requestCount)
    }

    @Test fun theAnswerDecodes() {
        val r = kotlinx.serialization.json.Json.parseToJsonElement(row(3, "Alex", "2026-11-12T18:03:11.5+00:00")).jsonObject
        val a = ownerAnswer(r as JsonObject, null, null)!!
        assertEquals(DeviceOwner(3, "Alex", "2026-11-12T18:03:11.5+00:00"), a.owner)
        assertNull(a.claimKey)
    }
}
