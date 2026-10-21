package com.domovoi.app.audiobooks

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
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

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return when (request.path) {
                    "/api/auth/status" -> MockResponse().setBody("""{"setup_complete": $setupComplete, "authenticated": false}""")
                    "/api/auth/login" -> {
                        val pw = DomovoiJson.parseToJsonElement(request.body.readUtf8()).jsonObject["password"]!!.jsonPrimitive.content
                        if (pw == "right-password") MockResponse().setBody("""{"ok": true, "token": "tok-123"}""")
                        else MockResponse().setResponseCode(401).setBody("""{"detail": "wrong password"}""")
                    }
                    "/api/auth/logout" -> MockResponse().setBody("""{"ok": true, "revoked": true}""")
                    "/api/files/delete" -> {
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

    private fun api() = ApiClient({ server.url("").toString().trimEnd('/') })

    private fun paths() = seen.map { it.path }

    @Test fun anAdminRemovesWithThePasswordAndIsSignedOutAgain() = runBlocking {
        val r = ServerRemove(api())
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
        val out = ServerRemove(api()).remove("Dune", isFolder = true, password = "nope")
        assertEquals(RemoveOutcome.Refused("wrong admin password"), out)
        assertFalse("/api/files/delete" in paths())
    }

    @Test fun withoutAPasswordTheServerRefusesAndSaysAdminOnly() = runBlocking {
        val out = ServerRemove(api()).remove("Dune.m4b", isFolder = false, password = null)
        assertEquals(RemoveOutcome.Refused("only an admin can remove books from the server"), out)
        // No sign-in was made, so there is none to end.
        assertFalse("/api/auth/logout" in paths())
    }

    @Test fun aServerBeforeAdminSetupNeedsNoPassword() = runBlocking {
        setupComplete = false
        val r = ServerRemove(api())
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
}
