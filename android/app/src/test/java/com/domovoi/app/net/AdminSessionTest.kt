package com.domovoi.app.net

import com.domovoi.app.audiobooks.IdentityAdmission
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.Collections

/** Admin on this phone: the password signs in once, the Bearer lives in
 *  memory only and goes only to the server that issued it, and a refusal
 *  that says the session ended signs the phone out. */
class AdminSessionTest {
    private lateinit var server: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())
    @Volatile private var verdict: IdentityVerdict = IdentityVerdict.Verified("SHA256:test", pinnedNow = false)
    @Volatile private var sessionLive = true
    @Volatile private var base: String = ""
    @Volatile private var loginBody: String = ""
    @Volatile private var grantBody: String = ""
    @Volatile private var exchangeBody: String = ""
    @Volatile private var revokeBody: String = ""
    @Volatile private var freshSignIn = true
    @Volatile private var grantExpired = false
    /** The server takes the phone-grant routes only with this device key
     *  (1.5.0: the grant is bound to the per-device key). */
    @Volatile private var keyRequired = false
    private var clock = 1_000_000L

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return when (request.path) {
                    "/api/auth/login" -> {
                        loginBody = request.body.readUtf8()
                        val pw = DomovoiJson.parseToJsonElement(loginBody)
                            .jsonObject["password"]!!.jsonPrimitive.content
                        when (pw) {
                            "right-password" -> MockResponse().setBody(
                                """{"ok":true,"token":"${"a".repeat(64)}","idle_timeout_min":60,"idle_in_sec":3600}""",
                            )
                            "slow-down" -> MockResponse().setResponseCode(429)
                                .setBody("""{"detail":"too many failed attempts — retry in 30s"}""")
                            else -> MockResponse().setResponseCode(401).setBody("""{"detail":"wrong password"}""")
                        }
                    }
                    "/api/auth/logout" -> MockResponse().setBody("""{"ok":true,"revoked":true}""")
                    "/api/auth/phone-grant" -> {
                        grantBody = request.body.readUtf8()
                        when {
                            request.getHeader("Authorization") != "Bearer ${"a".repeat(64)}" ->
                                MockResponse().setResponseCode(401).setBody("""{"detail":"admin session required"}""")
                            keyRequired && request.getHeader("X-Domovoi-Device-Key") != DEVICE_KEY -> MockResponse().setResponseCode(403).setBody(
                                """{"detail":"this phone can't prove it is that device (its device key is missing or out of date) — register it again, then sign in with the password"}""",
                            )
                            !freshSignIn -> MockResponse().setResponseCode(403).setBody("""{"detail":"sign in with the password again"}""")
                            else -> MockResponse().setBody("""{"grant":"$GRANT","expires_at":"2026-11-25T10:00:00Z"}""")
                        }
                    }
                    "/api/auth/phone-grant/exchange" -> {
                        exchangeBody = request.body.readUtf8()
                        val o = DomovoiJson.parseToJsonElement(exchangeBody).jsonObject
                        when {
                            grantExpired -> MockResponse().setResponseCode(401).setBody("""{"detail":"phone grant has expired"}""")
                            keyRequired && request.getHeader("X-Domovoi-Device-Key") != DEVICE_KEY ->
                                MockResponse().setResponseCode(401).setBody("""{"detail":"phone grant not valid — sign in with the password"}""")
                            o["grant"]!!.jsonPrimitive.content != GRANT || o["device_id"]!!.jsonPrimitive.content != "android-test" ->
                                MockResponse().setResponseCode(401).setBody("""{"detail":"phone grant not valid"}""")
                            else -> MockResponse().setBody(
                                """{"token":"${"b".repeat(64)}","grant_expires_at":"2026-11-25T10:00:00Z","idle_timeout_min":60,"idle_until":"x","idle_in_sec":3600}""",
                            )
                        }
                    }
                    "/api/auth/phone-grant/revoke" -> {
                        revokeBody = request.body.readUtf8()
                        MockResponse().setBody("""{"ok":true}""")
                    }
                    "/api/satellites/kitchen/restart" ->
                        if (sessionLive && request.getHeader("Authorization") in setOf("Bearer ${"a".repeat(64)}", "Bearer ${"b".repeat(64)}")) {
                            MockResponse().setBody("""{"ok":true}""")
                        } else {
                            MockResponse().setResponseCode(401).setBody("""{"detail":"admin session required"}""")
                        }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        base = server.url("").toString().trimEnd('/')
    }

    @After fun tearDown() = server.shutdown()

    private val api by lazy { ApiClient({ base }) }

    private fun session() = AdminSession(api, { b -> IdentityAdmission.fingerprint(verdict, b) }, now = { clock })

    private suspend fun restart(s: AdminSession) =
        s.call { headers, srv -> api.post("/api/satellites/kitchen/restart", null, headers, onlyTo = srv) }

    @Test fun thePasswordSignsInAndTheBearerGoesOnAdminCalls() = runBlocking {
        val s = session()
        assertEquals(AdminSession.SignIn.Ok, s.signIn("right-password", "phone: Pixel"))
        assertTrue(s.signedIn)
        val state = s.state.value as AdminSession.State.SignedIn
        assertEquals(clock + 3_600_000, state.idleUntilMs)
        restart(s)
        val login = seen.first { it.path == "/api/auth/login" }
        val body = DomovoiJson.parseToJsonElement(loginBody).jsonObject
        assertEquals("phone: Pixel", body["label"]!!.jsonPrimitive.content)
        assertEquals("Bearer ${"a".repeat(64)}", seen.last().getHeader("Authorization"))
        // Nothing but admin calls carries it.
        assertNull(login.getHeader("Authorization"))
    }

    @Test fun aWrongPasswordAndAThrottleSayWhy() = runBlocking {
        val s = session()
        assertEquals(AdminSession.SignIn.Refused("wrong admin password"), s.signIn("nope", "phone"))
        assertEquals(
            AdminSession.SignIn.Refused("too many failed attempts — retry in 30s"),
            s.signIn("slow-down", "phone"),
        )
        assertFalse(s.signedIn)
    }

    @Test fun thePasswordGoesOnlyToAServerThatProvedItself() = runBlocking {
        verdict = IdentityVerdict.Legacy()
        val out = session().signIn("right-password", "phone")
        assertTrue(out is AdminSession.SignIn.Refused)
        assertTrue("/api/auth/login" !in seen.map { it.path })
    }

    @Test fun aSessionTheServerEndedSignsThePhoneOut() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        sessionLive = false
        try {
            restart(s)
            fail("the refusal should be thrown")
        } catch (e: ApiException) {
            assertEquals(ADMIN_ONLY, e.message)
        }
        assertFalse(s.signedIn)
        assertEquals(AdminSession.State.SignedOut, s.state.value)
    }

    @Test fun anotherServerNeverGetsTheBearer() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        base = "http://192.0.2.1:6369"
        assertFalse(s.signedIn)
        try {
            restart(s)
            fail("no admin session for this server")
        } catch (e: AdminSession.NotSignedIn) {
            assertEquals(ADMIN_ONLY, e.message)
        }
        assertEquals(1, seen.count { it.path == "/api/auth/login" })
        assertTrue(seen.none { it.path == "/api/satellites/kitchen/restart" })
    }

    // ---- security review 2026-11-22, L57 ----------------------------------

    @Test fun aServerThatProvesAnotherKeyNeverGetsTheBearer() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        // A re-pin, or another machine answering at the same address.
        verdict = IdentityVerdict.Verified("SHA256:other", pinnedNow = true)
        try {
            restart(s)
            fail("no admin session for this identity")
        } catch (e: AdminSession.NotSignedIn) {
            assertEquals(ADMIN_ONLY, e.message)
        }
        assertFalse(s.signedIn)
        assertTrue(seen.none { it.path == "/api/satellites/kitchen/restart" })
    }

    @Test fun aServerThatCannotProveItselfNowGetsNothing() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        verdict = IdentityVerdict.Legacy()
        try {
            restart(s)
            fail("the identity check should refuse")
        } catch (e: java.io.IOException) {
            // Refused before anything was sent.
        }
        assertTrue(seen.none { it.path == "/api/satellites/kitchen/restart" })
    }

    @Test fun roomModeTurnsAdminOff() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        HouseholdLock.roomModeActive = true
        try {
            assertFalse(s.signedIn)
            try {
                restart(s)
                fail("no admin calls from a room")
            } catch (e: AdminSession.NotSignedIn) {
                assertEquals(ADMIN_ONLY, e.message)
            }
            assertEquals(AdminSession.SignIn.Refused(AdminSession.ROOM_MODE), s.signIn("right-password", "phone"))
        } finally {
            HouseholdLock.roomModeActive = false
        }
        assertFalse("forgotten, not just hidden", s.signedIn)
    }

    @Test fun aBearerNeverGoesToAnAbsoluteUrl() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        try {
            s.call { headers, srv -> api.post("http://192.0.2.1:6369/api/x", null, headers, onlyTo = srv) }
            fail("an absolute URL with the Bearer is refused")
        } catch (e: java.io.IOException) {
            assertEquals("an admin call goes only to this server's own paths", e.message)
        }
    }

    @Test fun signingOutEndsTheSessionAtTheServer() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        s.signOut()
        assertFalse(s.signedIn)
        assertEquals("Bearer ${"a".repeat(64)}", seen.last { it.path == "/api/auth/logout" }.getHeader("Authorization"))
    }

    // ---- security review 2026-11-23, L56: sign-out proves the server first ----

    @Test fun signOutToAServerThatNowProvesAnotherKeySendsNothing() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        verdict = IdentityVerdict.Verified("SHA256:other", pinnedNow = true)
        s.signOut()
        assertFalse(s.signedIn)
        assertTrue("the Bearer never went out", seen.none { it.path == "/api/auth/logout" })
    }

    @Test fun signOutToAServerThatCannotProveItselfSendsNothing() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        verdict = IdentityVerdict.Legacy()
        s.signOut()
        assertFalse(s.signedIn)
        assertTrue(seen.none { it.path == "/api/auth/logout" })
    }

    @Test fun signOutAfterTheServerChangedJustForgets() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        val first = base
        base = "http://192.0.2.1:6369"
        s.signOut()
        base = first
        assertFalse(s.signedIn)
        assertTrue(seen.none { it.path == "/api/auth/logout" })
    }

    @Test fun signOutInRoomModeJustForgets() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        // The session survives only if room mode was set without its setter
        // (it can't be); sign out from a room still sends nothing.
        HouseholdLock.roomModeActive = true
        try {
            s.signOut()
        } finally {
            HouseholdLock.roomModeActive = false
        }
        assertTrue(seen.none { it.path == "/api/auth/logout" })
    }

    // ---- security review 2026-11-23, L57: forgotten at once ----------------

    @Test fun enteringRoomModeForgetsAtOnceNotAtTheNextCall() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        HouseholdLock.roomModeActive = true
        HouseholdLock.roomModeActive = false
        assertEquals(AdminSession.State.SignedOut, s.state.value)
        assertFalse("forgotten while no call was made", s.signedIn)
        assertTrue(seen.none { it.path == "/api/satellites/kitchen/restart" })
    }

    @Test fun forgetAllEndsEverySessionOnThePhone() = runBlocking {
        val a = session()
        val b = session()
        a.signIn("right-password", "phone")
        b.signIn("right-password", "phone")
        AdminSessions.forgetAll()
        assertFalse(a.signedIn)
        assertFalse(b.signedIn)
        assertEquals(AdminSession.State.SignedOut, a.state.value)
        assertEquals(AdminSession.State.SignedOut, b.state.value)
    }

    /** Prefs needs an Android context, so its three hooks are held to the
     *  source: a server switch, forgetting the server, any pin change. */
    @Test fun prefsForgetsAdminOnAServerSwitchAndAPinChange() {
        val src = java.io.File("src/main/java/com/domovoi/app/data/Prefs.kt").readText()
        fun body(sig: String): String {
            val i = src.indexOf(sig)
            assertTrue("$sig is in Prefs.kt", i >= 0)
            return src.substring(i, src.indexOf("\n    }", i))
        }
        for (sig in listOf("fun setServerUrl(", "fun forgetActiveServer(", "private fun setPins(")) {
            assertTrue("$sig calls AdminSessions.forgetAll", "AdminSessions.forgetAll(" in body(sig))
        }
    }

    // ---- keep admin today: the day's grant ---------------------------------

    /** The phone's biometric key, faked: sealing XORs, and the test says
     *  whether the person passes the check. */
    private class FakeLocker : GrantLocker {
        var ok = true
        var gone = false
        var destroyed = 0
        var opens = 0
        override val available = true
        override suspend fun seal(plain: ByteArray): GrantLocker.Sealed {
            if (!ok) throw GrantLocker.Cancelled("cancelled")
            return GrantLocker.Sealed(byteArrayOf(1, 2, 3), plain.map { (it.toInt() xor 0x5a).toByte() }.toByteArray())
        }
        override suspend fun open(sealed: GrantLocker.Sealed): ByteArray {
            opens++
            if (gone) throw GrantLocker.KeyGone()
            if (!ok) throw GrantLocker.Cancelled("cancelled")
            return sealed.ciphertext.map { (it.toInt() xor 0x5a).toByte() }.toByteArray()
        }
        override fun destroy() { destroyed++ }
    }

    private class FakeShelf : GrantShelf {
        var g: StoredGrant? = null
        override fun readGrant() = g
        override fun writeGrant(grant: StoredGrant?) { g = grant }
    }

    private val locker = FakeLocker()
    private val shelf = FakeShelf()

    @Volatile private var deviceId = "android-test"
    @Volatile private var phoneKey: String? = DEVICE_KEY

    private fun keeping(client: ApiClient = api) = AdminSession(
        client, { b -> IdentityAdmission.fingerprint(verdict, b) }, now = { clock },
        grants = shelf, locker = locker, deviceId = { deviceId },
    )

    /** The app's client: the household token, and this phone's device key
     *  for its own server only (Prefs.deviceKeyFor). */
    private val keyedApi by lazy {
        ApiClient({ base }, { "household-abc" }, deviceKeyForServer = { if (it.trimEnd('/') == base) phoneKey else null })
    }

    // ---- the grant is bound to the per-device key (1.5.0) ------------------

    @Test fun issueExchangeAndRevokeCarryTheDeviceKey() = runBlocking {
        keyRequired = true
        val s = keeping(keyedApi)
        s.signIn("right-password", "phone")
        assertTrue(s.keepToday("phone") is AdminSession.Keep.Ok)
        s.forget()
        assertEquals(AdminSession.SignIn.Ok, s.resume())
        s.signOut()
        for (path in listOf("/api/auth/phone-grant", "/api/auth/phone-grant/exchange", "/api/auth/phone-grant/revoke")) {
            assertEquals(path, DEVICE_KEY, seen.last { it.path == path }.getHeader("X-Domovoi-Device-Key"))
        }
        // The password sign-in is not one of its routes.
        assertNull(seen.last { it.path == "/api/auth/login" }.getHeader("X-Domovoi-Device-Key"))
    }

    @Test fun aMissingKeyOnIssueIsSaidInPlainWords() = runBlocking {
        keyRequired = true
        phoneKey = null
        val s = keeping(keyedApi)
        s.signIn("right-password", "phone")
        val r = s.keepToday("phone") as AdminSession.Keep.Refused
        assertEquals(AdminSession.DEVICE_KEY_REFUSED, r.reason)
        assertNull(shelf.g)
        assertEquals(
            "sign in with the password again first: keeping admin today needs a sign-in from the last 5 minutes",
            AdminSession.keepRefusal(403, "sign in with the password again", null),
        )
        assertEquals("too many tries; try again in 9 s", AdminSession.keepRefusal(429, null, 9))
    }

    @Test fun aWrongKeyOnExchangeDropsTheGrantForThePassword() = runBlocking {
        keyRequired = true
        val s = keeping(keyedApi)
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        phoneKey = "dk-1-" + "B".repeat(36)
        val r = s.resume() as AdminSession.SignIn.Refused
        assertTrue(r.reason, r.reason.contains("sign in with the password"))
        assertNull("the grant went", shelf.g)
        assertEquals(AdminSession.State.SignedOut, s.state.value)
    }

    @Test fun startingOverUnderANewIdDropsTheKeptGrant() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        assertTrue(s.state.value is AdminSession.State.Kept)
        // Another server starting over leaves it.
        s.startedOver("http://192.168.1.30:6369")
        assertTrue(shelf.g != null)
        // This one: it goes at once, and the key is destroyed.
        s.startedOver("$base/")
        assertNull(shelf.g)
        assertEquals(AdminSession.State.SignedOut, s.state.value)
        assertTrue(locker.destroyed > 0)
    }

    @Test fun resumeUnderAnotherIdDropsTheGrant() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        deviceId = "android-started-over"
        val r = s.resume() as AdminSession.SignIn.Refused
        assertEquals("this phone's id changed; sign in with the password", r.reason)
        assertNull(shelf.g)
        assertFalse("nothing was exchanged", seen.any { it.path == "/api/auth/phone-grant/exchange" })
    }

    @Test fun keepTodaySealsTheGrantBoundToThisServerAndIdentity() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone: Pixel")
        val r = s.keepToday("phone: Pixel")
        assertTrue("$r", r is AdminSession.Keep.Ok)
        val g = shelf.g!!
        assertEquals(base, g.server)
        assertEquals("SHA256:test", g.fingerprint)
        assertEquals("android-test", g.deviceId)
        assertEquals(java.time.Instant.parse("2026-11-25T10:00:00Z").toEpochMilli(), g.expiresAtMs)
        assertFalse("never stored in the clear", g.ciphertext.contains(GRANT) || java.util.Base64.getDecoder().decode(g.ciphertext).decodeToString() == GRANT)
        val sent = DomovoiJson.parseToJsonElement(grantBody).jsonObject
        assertEquals("android-test", sent["device_id"]!!.jsonPrimitive.content)
        assertEquals("phone: Pixel", sent["label"]!!.jsonPrimitive.content)
        assertEquals(g.expiresAtMs, (s.state.value as AdminSession.State.SignedIn).keptUntilMs)
    }

    @Test fun keepTodayNeedsAFreshPasswordSignIn() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        freshSignIn = false
        val r = s.keepToday("phone") as AdminSession.Keep.Refused
        assertTrue(r.reason, r.reason.contains("sign in with the password again"))
        assertNull(shelf.g)
    }

    @Test fun aGrantTheFingerprintCheckDidntSealIsRevokedAtOnce() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        locker.ok = false
        assertTrue(s.keepToday("phone") is AdminSession.Keep.Refused)
        assertNull(shelf.g)
        assertEquals(GRANT, DomovoiJson.parseToJsonElement(revokeBody).jsonObject["grant"]!!.jsonPrimitive.content)
    }

    @Test fun afterTheIdleHourTheFingerprintSwapsTheGrantForASession() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()  // the idle hour lapsed, or the app restarted
        assertTrue(s.state.value is AdminSession.State.Kept)
        assertEquals(AdminSession.SignIn.Ok, s.resume())
        val ex = DomovoiJson.parseToJsonElement(exchangeBody).jsonObject
        assertEquals(GRANT, ex["grant"]!!.jsonPrimitive.content)
        assertEquals("android-test", ex["device_id"]!!.jsonPrimitive.content)
        restart(s)
        assertEquals("Bearer ${"b".repeat(64)}", seen.last { it.path == "/api/satellites/kitchen/restart" }.getHeader("Authorization"))
    }

    @Test fun theGrantGoesOnlyToTheServerThatProvesTheSameIdentity() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        verdict = IdentityVerdict.Verified("SHA256:other", pinnedNow = true)
        assertTrue(s.resume() is AdminSession.SignIn.Refused)
        assertNull("dropped", shelf.g)
        assertEquals(0, locker.opens)
        assertTrue(seen.none { it.path == "/api/auth/phone-grant/exchange" })
    }

    @Test fun anotherServerDoesntSeeTheKeptGrant() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        val first = base
        base = "http://192.0.2.1:6369"
        s.refresh()
        assertEquals(AdminSession.State.SignedOut, s.state.value)
        assertTrue(s.resume() is AdminSession.SignIn.Refused)
        base = first
        assertTrue("kept for its own server", shelf.g != null)
    }

    @Test fun newBiometricsOrAnEndedGrantDropIt() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        locker.gone = true
        assertTrue(s.resume() is AdminSession.SignIn.Refused)
        assertNull(shelf.g)
        // A revoked or expired grant at the server: dropped too.
        locker.gone = false
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        grantExpired = true
        assertEquals(AdminSession.SignIn.Refused(AdminSession.DAY_OVER), s.resume())
        assertNull(shelf.g)
    }

    @Test fun pastTheDayTheGrantMadeSessionIsNotUsed() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        s.resume()
        clock = java.time.Instant.parse("2026-11-25T10:00:01Z").toEpochMilli()
        assertFalse(s.signedIn)
    }

    @Test fun signOutRevokesTheGrantAndDeletesTheKey() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.signOut()
        assertNull(shelf.g)
        assertTrue(locker.destroyed > 0)
        assertEquals(GRANT, DomovoiJson.parseToJsonElement(revokeBody).jsonObject["grant"]!!.jsonPrimitive.content)
        assertEquals(AdminSession.State.SignedOut, s.state.value)
    }

    @Test fun roomModeDropsTheKeptGrant() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        HouseholdLock.roomModeActive = true
        HouseholdLock.roomModeActive = false
        assertNull(shelf.g)
        assertEquals(AdminSession.State.SignedOut, s.state.value)
    }

    // ---- act: a tap after the hour asks for the fingerprint, then acts ----

    @Test fun actWithAKeptGrantResumesThenActs() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        s.act { h, srv -> api.post("/api/satellites/kitchen/restart", null, h, onlyTo = srv) }
        assertEquals(1, locker.opens)
        assertEquals("Bearer ${"b".repeat(64)}", seen.last { it.path == "/api/satellites/kitchen/restart" }.getHeader("Authorization"))
    }

    @Test fun actAfterTheServerEndedTheSessionResumesOnce() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        sessionLive = false  // the idle hour, at the server
        try {
            s.act { h, srv -> api.post("/api/satellites/kitchen/restart", null, h, onlyTo = srv) }
            fail("the exchanged session is refused too here")
        } catch (e: ApiException) {
            assertEquals(401, e.status)
        }
        assertEquals("one fingerprint check, not a loop", 1, locker.opens)
    }

    @Test fun actWithNothingKeptIsNotSignedIn() = runBlocking {
        val s = keeping()
        try {
            s.act { h, srv -> api.post("/api/satellites/kitchen/restart", null, h, onlyTo = srv) }
            fail("no admin")
        } catch (e: AdminSession.NotSignedIn) {
            assertEquals(ADMIN_ONLY, e.message)
        }
        assertEquals(0, locker.opens)
    }

    @Test fun aCancelledFingerprintSaysSoAndKeepsTheGrant() = runBlocking {
        val s = keeping()
        s.signIn("right-password", "phone")
        s.keepToday("phone")
        s.forget()
        locker.ok = false
        try {
            s.act { h, srv -> api.post("/api/satellites/kitchen/restart", null, h, onlyTo = srv) }
            fail("cancelled")
        } catch (e: AdminSession.AdminUnavailable) {
            assertTrue(e.message!!.contains("didn't finish"))
        }
        assertTrue(shelf.g != null)
    }

    private companion object {
        val GRANT = "0123456789abcdef".repeat(4)
        val DEVICE_KEY = "dk-1-" + "A".repeat(36)
    }
}
