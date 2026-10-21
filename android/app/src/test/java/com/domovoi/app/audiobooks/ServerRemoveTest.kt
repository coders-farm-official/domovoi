package com.domovoi.app.audiobooks

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonArray
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
import org.junit.Before
import org.junit.Test
import java.util.Collections

/** "Remove from server": admin only, the password used once and signed out. */
class ServerRemoveTest {
    private lateinit var server: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())
    @Volatile private var setupComplete = true
    @Volatile private var loginReply: MockResponse? = null
    @Volatile private var deleteReply: MockResponse? = null
    /** What the identity gate says about the server; proven by default. */
    @Volatile private var verdict: IdentityVerdict = IdentityVerdict.Verified("SHA256:test", pinnedNow = false)
    private val admitted = Collections.synchronizedList(mutableListOf<String>())

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return when (request.path) {
                    "/api/auth/status" -> MockResponse().setBody("""{"setup_complete": $setupComplete, "authenticated": false}""")
                    "/api/auth/login" -> loginReply ?: run {
                        val pw = DomovoiJson.parseToJsonElement(request.body.readUtf8()).jsonObject["password"]!!.jsonPrimitive.content
                        if (pw == "right-password") MockResponse().setBody("""{"ok": true, "token": "tok-123"}""")
                        else MockResponse().setResponseCode(401).setBody("""{"detail": "wrong password"}""")
                    }
                    "/api/auth/logout" -> MockResponse().setBody("""{"ok": true, "revoked": true}""")
                    "/api/files/delete" -> deleteReply ?: run {
                        val auth = request.getHeader("Authorization")
                        if (setupComplete && auth != "Bearer tok-123") {
                            MockResponse().setResponseCode(401).setBody("""{"detail": "admin session required"}""")
                        } else {
                            MockResponse().setBody("""{"deleted": ["Dune"], "failed": [], "reindex_triggered": true}""")
                        }
                    }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }

    @After fun tearDown() = server.shutdown()

    /** An unpaired phone's client: no household token at all, so the
     *  token's own identity gate never runs. */
    private fun api() = ApiClient({ server.url("").toString().trimEnd('/') })

    private fun remover() = ServerRemove(api()) { base ->
        admitted += base.toString()
        IdentityAdmission.verified(verdict, base)
    }

    private fun paths() = seen.map { it.path }

    @Test fun anAdminRemovesWithThePasswordAndIsSignedOutAgain() = runBlocking {
        val r = remover()
        assertTrue(r.needsPassword())
        assertEquals(RemoveOutcome.Removed(reindexed = true), r.remove("Dune", isFolder = true, password = "right-password"))
        assertEquals(listOf("/api/auth/status", "/api/auth/login", "/api/files/delete", "/api/auth/logout"), paths())
        val delete = seen.first { it.path == "/api/files/delete" }
        val body = DomovoiJson.parseToJsonElement(delete.body.readUtf8()).jsonObject
        assertEquals("core:audiobooks", body["library_id"]!!.jsonPrimitive.content)
        assertEquals("Dune", body["paths"]!!.jsonArray.single().jsonPrimitive.content)
        assertEquals("true", body["recursive"]!!.jsonPrimitive.content)
        assertEquals("DomovoiApp", delete.getHeader("X-Requested-With"))
        assertEquals("Bearer tok-123", seen.first { it.path == "/api/auth/logout" }.getHeader("Authorization"))
    }

    @Test fun aWrongPasswordRemovesNothing() = runBlocking {
        val out = remover().remove("Dune", isFolder = true, password = "nope")
        assertEquals(RemoveOutcome.Refused("wrong admin password"), out)
        assertFalse("/api/files/delete" in paths())
    }

    @Test fun withoutAPasswordTheServerRefusesAndSaysAdminOnly() = runBlocking {
        val out = remover().remove("Dune.m4b", isFolder = false, password = null)
        assertEquals(RemoveOutcome.Refused("only an admin can remove books from the server"), out)
        // No sign-in was made, so there is none to end.
        assertFalse("/api/auth/logout" in paths())
    }

    @Test fun aServerBeforeAdminSetupNeedsNoPassword() = runBlocking {
        setupComplete = false
        val r = remover()
        assertFalse(r.needsPassword())
        assertEquals(RemoveOutcome.Removed(reindexed = true), r.remove("Dune.m4b", isFolder = false, password = null))
        assertNull(seen.first { it.path == "/api/files/delete" }.getHeader("Authorization"))
    }

    @Test fun theDeleteAnswerReadsAsAnOutcome() {
        assertEquals(RemoveOutcome.Removed(false), ServerRemove.parseDelete("""{"deleted": ["x"], "failed": []}"""))
        assertEquals(RemoveOutcome.Refused("not found"), ServerRemove.parseDelete("""{"deleted": [], "failed": ["Dune: not found"]}"""))
        assertTrue(ServerRemove.parseDelete("nonsense") is RemoveOutcome.Refused)
        val single = ServerRemove.deleteBody("Solo.m4b", isFolder = false)
        assertEquals("false", single["recursive"]!!.jsonPrimitive.content)
    }

    @Test fun anOldServersFolderRefusalIsRecognised() {
        val old = ApiException(404, "404 Not Found", body = """{"detail":"destination directory not found"}""")
        assertTrue(BookUploadRules.isOldServerRefusal(old))
        assertFalse(BookUploadRules.isOldServerRefusal(ApiException(404, "x", body = """{"detail":"unknown library"}""")))
        assertFalse(BookUploadRules.isOldServerRefusal(java.io.IOException("x")))
    }

    // ---- review round 1 ------------------------------------------------------

    /** BLOCKER fix: the password goes only to a server that just proved the
     *  identity this phone pinned for it, paired or not. */
    @Test fun thePasswordGoesOnlyToAProvenServer() = runBlocking {
        for (v in listOf(
            IdentityVerdict.Legacy(),
            IdentityVerdict.Mismatch("SHA256:pinned", "SHA256:other"),
            IdentityVerdict.Unproven("SHA256:pinned", "did not answer the challenge"),
            IdentityVerdict.Unavailable("SHA256:pinned", "core down"),
        )) {
            verdict = v
            seen.clear()
            val out = remover().remove("Dune", isFolder = true, password = "right-password")
            assertTrue("$v -> $out", out is RemoveOutcome.Refused)
            assertFalse("$v sent the password", "/api/auth/login" in paths())
            assertFalse("/api/files/delete" in paths())
        }
        // The gate was asked about the active server itself.
        assertTrue(admitted.all { it.trimEnd('/') == server.url("").toString().trimEnd('/') })
        // A legacy server is refused in words that say what to do.
        verdict = IdentityVerdict.Legacy()
        val legacy = remover().remove("Dune", isFolder = true, password = "right-password") as RemoveOutcome.Refused
        assertTrue(legacy.reason, "dashboard" in legacy.reason)
    }

    @Test fun aServerWithoutAdminSetupIsStillProvenFirst() = runBlocking {
        setupComplete = false
        verdict = IdentityVerdict.Unproven(null, "no answer")
        assertTrue(remover().remove("Dune.m4b", isFolder = false, password = null) is RemoveOutcome.Refused)
        assertFalse("/api/files/delete" in paths())
    }

    @Test fun answersThatMakeNoSenseAreRefusalsNotCrashes() = runBlocking {
        loginReply = MockResponse().setBody("not json at all")
        assertEquals(RemoveOutcome.Refused("the server's sign-in made no sense"), remover().remove("Dune", true, "right-password"))
        loginReply = MockResponse().setBody("""{"ok": true, "token": {"nested": 1}}""")
        assertTrue(remover().remove("Dune", true, "right-password") is RemoveOutcome.Refused)
        // A token that can't be a header is never sent as one.
        loginReply = MockResponse().setBody("{\"ok\": true, \"token\": \"tok\u0001bad\"}")
        assertEquals(RemoveOutcome.Refused("the server's sign-in made no sense"), remover().remove("Dune", true, "right-password"))
        assertFalse("/api/files/delete" in paths())
        loginReply = null
        deleteReply = MockResponse().setBody("<html>proxy error</html>")
        assertEquals(RemoveOutcome.Refused("the server's answer made no sense"), remover().remove("Dune", true, "right-password"))
        deleteReply = MockResponse().setBody("""{"deleted": [], "failed": [{"x": 1}]}""")
        assertTrue(remover().remove("Dune", true, "right-password") is RemoveOutcome.Refused)
        // Every sign-in that happened was ended.
        assertEquals(paths().count { it == "/api/auth/login" } - 3, paths().count { it == "/api/auth/logout" })
    }

    @Test fun aThrottledSignInSaysTheServersWait() = runBlocking {
        loginReply = MockResponse().setResponseCode(429).setHeader("Retry-After", "125")
            .setBody("""{"detail": "too many failed sign-ins from this address; try again in 125 s"}""")
        assertEquals(
            RemoveOutcome.Refused("too many failed sign-ins from this address; try again in 125 s"),
            remover().remove("Dune", true, "x"),
        )
        loginReply = MockResponse().setResponseCode(429).setHeader("Retry-After", "125").setBody("")
        assertEquals(RemoveOutcome.Refused("too many tries; try again in 2 m"), remover().remove("Dune", true, "x"))
        loginReply = MockResponse().setResponseCode(429).setBody("")
        assertEquals(RemoveOutcome.Refused("too many tries; try again later"), remover().remove("Dune", true, "x"))
    }

    @Test fun onlyARelativeLibraryPathIsEverSent() = runBlocking {
        for (bad in listOf("", " ", "/etc", "C:/x", "c:x", "a\b", "..", "a/../b", "./a", "a//b", "a/", " a", "a")) {
            assertNull(bad, BookModels.safeLibraryPath(bad))
            assertTrue(remover().remove(bad, true, "right-password") is RemoveOutcome.Refused)
        }
        assertFalse("/api/auth/login" in paths())
        assertNull(BookModels.safeLibraryPath(null))
        assertEquals("Dune", BookModels.safeLibraryPath("Dune"))
        assertEquals("Series/Dune 2.m4b", BookModels.safeLibraryPath("Series/Dune 2.m4b"))
    }

    @Test fun theAdmissionAcceptsOnlyAVerifiedPin() {
        val base = server.url("/")
        IdentityAdmission.verified(IdentityVerdict.Verified("SHA256:x", pinnedNow = true), base)
        for (v in listOf(IdentityVerdict.Legacy(), IdentityVerdict.Mismatch("a", "b"), IdentityVerdict.Unproven(null, "r"),
            IdentityVerdict.Unavailable(null, "r"))) {
            try {
                IdentityAdmission.verified(v, base)
                throw AssertionError("admitted $v")
            } catch (e: java.io.IOException) {
                // refused
            }
        }
    }
}
