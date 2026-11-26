package com.domovoi.app.ui.screens.settings

import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.ServerChangedException
import kotlinx.coroutines.runBlocking
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
import java.io.IOException
import java.util.Collections

/** The Domovoi server as admin on this phone: what the card offers, the
 *  dashboard's confirms, how a restart is followed, and the routes. */
class ServerUpdateTest {

    private val server = "http://192.168.1.20:6369"

    private fun v(json: String) = parseServerVersion(DomovoiJson.parseToJsonElement(json))!!

    @Test fun theCardIsThereOnlyWithAdminAndNeverOnASharedScreen() {
        val signedIn = AdminSession.State.SignedIn(server, null)
        val kept = AdminSession.State.Kept(server, 1L)
        assertTrue(serverAdminHeld(signedIn, false))
        assertTrue(serverAdminHeld(kept, false))
        assertFalse(serverAdminHeld(signedIn, true))
        assertFalse(serverAdminHeld(AdminSession.State.SignedOut, false))
        // The About line: gone with the card, as it was on a shared screen,
        // and naming this phone's sign-in otherwise.
        assertNull(serverBuildLine(signedIn, false))
        assertEquals(SERVER_BUILD_LINE, serverBuildLine(signedIn, true))
        assertEquals(SERVER_BUILD_LINE, serverBuildLine(AdminSession.State.SignedOut, true))
        assertEquals(
            "The server's build / version identifiers live on the dashboard under About & updates, or sign in as admin on this phone.",
            serverBuildLine(AdminSession.State.SignedOut, false),
        )
        assertTrue(ADMIN_JOBS.contains("install an update or restart the Domovoi server (Settings → About & updates)"))
    }

    @Test fun theVersionIsReadAsTheDashboardReadsIt() {
        val x = v(
            """{"version":"1.5.0","sha":"aaaa111","running_sha":"aaaa111","checkout_sha":"bbbb222","started_at":1700000000,
               "restart_required":true,"code_restart_required":true,"restart_capable":true,"restart_mode":"update",
               "last_update":{"status":"ok","started_at":"2026-11-20T10:00:00Z","to_sha":"aaaa111"},
               "plugins_pending_restart":[{"slug":"radio","from_version":"1.1.0","to_version":"1.2.0"}],
               "windows_side":{"present":true,"kind":"windows"}}""",
        )
        assertEquals("1.5.0", x.version)
        assertEquals("1700000000", x.startedAt)
        assertTrue(x.codePending && x.updateUnit && x.restartCapable)
        assertEquals(listOf("radio 1.1.0 → 1.2.0"), x.pluginsPending)
        assertEquals("windows", x.hostSide)
        assertEquals("ok", x.lastUpdate?.status)
        // A Mac without the updater has no host step; an absent side, none.
        assertNull(v("""{"windows_side":{"present":true,"kind":"mac"}}""").hostSide)
        assertEquals("mac", v("""{"windows_side":{"present":true,"kind":"mac","updates":true}}""").hostSide)
        assertNull(v("""{"windows_side":{"present":false}}""").hostSide)
        // An older core: restart_required alone means the code.
        assertTrue(v("""{"restart_required":true}""").codePending)
        assertNull(parseServerVersion(DomovoiJson.parseToJsonElement("[]")))
    }

    @Test fun theModeFollowsTheDashboardsOrder() {
        val idle = v("""{"restart_capable":true}""")
        assertEquals(ServerMode.Check, serverMode(idle, null, following = false))
        assertEquals(ServerMode.Busy, serverMode(idle, null, following = true))
        assertEquals(ServerMode.Busy, serverMode(v("""{"restart_in_progress":true,"restart_required":true}"""), null, false))
        assertEquals(ServerMode.Restart, serverMode(v("""{"restart_required":true}"""), null, false))
        val behind = UpdateCheck(upstream = true, behind = 3, upstreamSha = "cccc333")
        assertEquals(ServerMode.Pull, serverMode(idle, behind, false))
        assertEquals(ServerMode.Held, serverMode(v("""{"bad_sha":"cccc333"}"""), behind, false))
        assertEquals(ServerMode.Check, serverMode(idle, UpdateCheck(upstream = true, behind = 0), false))
        assertEquals(ServerMode.Check, serverMode(idle, UpdateCheck(upstream = false, behind = 3), false))
        // One press only where the update unit can restart unattended, and
        // never with a Windows or Mac part.
        assertTrue(onePress(v("""{"restart_mode":"update","restart_capable":true}""")))
        assertFalse(onePress(v("""{"restart_mode":"restart","restart_capable":true}""")))
        assertFalse(onePress(v("""{"restart_mode":"update","restart_capable":false}""")))
        assertFalse(onePress(v("""{"restart_mode":"update","restart_capable":true,"windows_side":{"present":true}}""")))
    }

    @Test fun theConfirmsAreTheDashboards() {
        val (title, body) = ServerUpdateWords.split(ServerUpdateWords.ONE_PRESS_CONFIRM)
        assertEquals("Update Domovoi?", title)
        assertTrue(body.startsWith("Domovoi pulls the latest code, then backs up, applies it and restarts"))
        assertEquals("Update Domovoi?", ServerUpdateWords.split(ServerUpdateWords.PULL_CONFIRM).first)
        assertTrue(ServerUpdateWords.PULL_CONFIRM.contains("but does not start it yet"))
        val plain = v("""{"restart_mode":"restart"}""")
        assertTrue(ServerUpdateWords.restartConfirm(plain, plain = true).startsWith("Restart Domovoi now?\n\ndomovoi-core and domovoi-web restart"))
        assertTrue(ServerUpdateWords.restartConfirm(plain, plain = false).startsWith("Restart the Domovoi services to load the pulled code?\n\n"))
        val unit = v("""{"restart_mode":"update"}""")
        assertTrue(ServerUpdateWords.restartConfirm(unit, plain = false).startsWith("Apply the pulled code?\n\nThis backs up the database"))
        // A plain press the update unit would run as its full update is asked as that.
        val pulled = v("""{"restart_mode":"update","restart_required":true,"sha":"aaaa111","checkout_sha":"bbbb222"}""")
        assertEquals("Pulled code is waiting to load (running aaaa111, checked out bbbb222)", restartRunsFullUpdate(pulled))
        assertTrue(ServerUpdateWords.restartConfirm(pulled, plain = true).startsWith("Restart Domovoi now, as a full update?\n\nPulled code is waiting"))
        val moved = v("""{"restart_mode":"update","checkout_sha":"bbbb222","last_update":{"status":"ok","to_sha":"aaaa1112"}}""")
        assertEquals("The checkout (bbbb222) isn’t the commit the update unit last applied (aaaa111)", restartRunsFullUpdate(moved))
        val same = v("""{"restart_mode":"update","checkout_sha":"aaaa111","last_update":{"status":"ok","to_sha":"aaaa1112"}}""")
        assertNull(restartRunsFullUpdate(same))
        assertNull(restartRunsFullUpdate(plain))
    }

    @Test fun theWordsAreTheDashboards() {
        assertEquals("3 commits behind", ServerUpdateWords.checkWords(UpdateCheck(upstream = true, channel = "main", behind = 3)))
        assertEquals("1 commit behind", ServerUpdateWords.checkWords(UpdateCheck(upstream = true, channel = "main", behind = 1)))
        assertEquals("up to date", ServerUpdateWords.checkWords(UpdateCheck(upstream = true, channel = "main", behind = 0)))
        assertEquals("Domovoi 1.6.0 is available", ServerUpdateWords.checkWords(UpdateCheck(upstream = true, channel = "stable", behind = 2, upstreamVersion = "1.6.0")))
        assertEquals("newer than the latest release", ServerUpdateWords.checkWords(UpdateCheck(upstream = true, channel = "stable", behind = 0, ahead = 4)))
        assertEquals("the latest release", ServerUpdateWords.checkWords(UpdateCheck(upstream = true, channel = "stable", behind = 0)))
        assertNull(ServerUpdateWords.checkWords(UpdateCheck(upstream = false)))
        assertEquals("update check failed: offline", ServerUpdateWords.checked(UpdateCheck(error = "offline")))
        assertEquals("no release has been published on the stable channel yet", ServerUpdateWords.checked(UpdateCheck(channel = "stable")))
        assertEquals("no upstream configured — can’t check", ServerUpdateWords.checked(UpdateCheck(channel = "main")))
        assertEquals(
            "New code is on disk but this process is still running the old modules — restart below to load it.",
            ServerUpdateWords.codePending(true),
        )
        assertEquals(
            "Plugin upgrade waiting for a restart: radio 1.1.0 → 1.2.0 — restart below to finish.",
            ServerUpdateWords.pluginsPending(listOf("radio 1.1.0 → 1.2.0"), true),
        )
        assertEquals(
            "systemctl not found — not a systemd host. Restart the Domovoi services the way they were started.",
            ServerUpdateWords.byHand(v("""{"restart_hint":"systemctl not found — not a systemd host"}""")),
        )
        assertTrue(ServerUpdateWords.held("cccc3334444").startsWith("The latest commit (cccc333) failed to apply"))
    }

    @Test fun aRestartIsFollowedUntilANewProcessAnswers() {
        val press = RestartPress("restart", plain = true, previousRun = null, previousStart = "100")
        assertEquals(RestartProgress.Waiting, press.after(v("""{"started_at":100}"""), wentAway = true))
        assertEquals(RestartProgress.Waiting, press.after(v("""{"started_at":200,"restart_in_progress":true}"""), wentAway = true))
        assertEquals(
            RestartProgress.Done(true, "restarted — now running aaaa111"),
            press.after(v("""{"started_at":200,"sha":"aaaa111"}"""), wentAway = false),
        )
        // A server that can't say when it started: having been away is enough.
        val blind = RestartPress("restart", plain = true, previousRun = null, previousStart = null)
        assertEquals(RestartProgress.Waiting, blind.after(v("{}"), wentAway = false))
        assertTrue(blind.after(v("{}"), wentAway = true) is RestartProgress.Done)
        assertEquals("restart is taking longer than expected — check the service by hand", press.tooLong())
        assertEquals(90_000L, press.deadlineMs)
    }

    @Test fun anUpdateEndsWithTheUnitsResult() {
        val press = RestartPress("update", plain = false, previousRun = "t1", previousStart = "100")
        assertEquals(15 * 60_000L, press.deadlineMs)
        // The old run, or one still running: wait.
        assertEquals(RestartProgress.Waiting, press.after(v("""{"started_at":200,"last_update":{"status":"ok","started_at":"t1"}}"""), true))
        assertEquals(RestartProgress.Waiting, press.after(v("""{"last_update":{"status":"running","started_at":"t2"}}"""), true))
        assertEquals(
            RestartProgress.Done(false, "update failed at health and was rolled back"),
            press.after(v("""{"last_update":{"status":"rolled_back","started_at":"t2","error":"update failed at health and was rolled back"}}"""), true),
        )
        assertEquals(
            RestartProgress.Done(false, "update not applied — see journalctl -u domovoi-update"),
            press.after(v("""{"last_update":{"status":"refused","started_at":"t2"}}"""), true),
        )
        assertEquals(
            RestartProgress.Done(true, "restarted — now running bbbb222"),
            press.after(v("""{"running_sha":"bbbb222","last_update":{"status":"ok","started_at":"t2"}}"""), true),
        )
        assertEquals("the update is taking longer than expected — check journalctl -u domovoi-update", press.tooLong())
    }

    @Test fun onlyALostConnectionIsTakenForTheBounce() {
        assertTrue(cutOffMidRequest(IOException("connection reset")))
        assertFalse(cutOffMidRequest(ApiException(403, "403")))
        assertFalse(cutOffMidRequest(ServerChangedException()))
        assertFalse(cutOffMidRequest(IllegalStateException()))
    }

    // ---- the routes ------------------------------------------------------------

    private lateinit var mock: MockWebServer
    private val seen = Collections.synchronizedList(mutableListOf<RecordedRequest>())

    @Before fun setUp() {
        mock = MockWebServer()
        mock.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return when (request.path) {
                    "/api/config/version" -> MockResponse().setBody("""{"version":"1.5.0","restart_capable":true}""")
                    "/api/config/version/check" -> MockResponse().setBody("""{"upstream":"origin/main","channel":"main","behind":2}""")
                    "/api/config/version/pull" -> MockResponse().setBody("""{"pulled":true,"new_sha":"bbbb222"}""")
                    "/api/config/version/restart" -> MockResponse().setBody("""{"ok":true,"mode":"update"}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        mock.start()
    }

    @After fun tearDown() = mock.shutdown()

    @Test fun theMutationsCarryTheBearerAndTheReadsDoNot() = runBlocking {
        val base = mock.url("").toString().trimEnd('/')
        val api = ServerUpdateApi(ApiClient({ base }))
        val h = mapOf("Authorization" to "Bearer ${"a".repeat(64)}")
        assertEquals("1.5.0", api.version()?.version)
        assertEquals(2, api.check().behind)
        assertEquals("bbbb222", (api.pull(h, base)?.get("new_sha") as? kotlinx.serialization.json.JsonPrimitive)?.content)
        assertEquals("update", (api.restart(h, base)?.get("mode") as? kotlinx.serialization.json.JsonPrimitive)?.content)
        val byPath = seen.associateBy { it.path }
        assertNull(byPath["/api/config/version"]?.getHeader("Authorization"))
        assertNull(byPath["/api/config/version/check"]?.getHeader("Authorization"))
        assertEquals(h["Authorization"], byPath["/api/config/version/pull"]?.getHeader("Authorization"))
        assertEquals(h["Authorization"], byPath["/api/config/version/restart"]?.getHeader("Authorization"))
        assertEquals("POST", byPath["/api/config/version/restart"]?.method)
    }
}
