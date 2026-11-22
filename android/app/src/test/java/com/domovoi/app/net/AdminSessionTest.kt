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
                    "/api/satellites/kitchen/restart" ->
                        if (sessionLive && request.getHeader("Authorization") == "Bearer ${"a".repeat(64)}") {
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

    private fun session() = AdminSession(api, { b -> IdentityAdmission.verified(verdict, b) }, now = { clock })

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

    @Test fun signingOutEndsTheSessionAtTheServer() = runBlocking {
        val s = session()
        s.signIn("right-password", "phone")
        s.signOut()
        assertFalse(s.signedIn)
        assertEquals("Bearer ${"a".repeat(64)}", seen.last { it.path == "/api/auth/logout" }.getHeader("Authorization"))
    }
}
