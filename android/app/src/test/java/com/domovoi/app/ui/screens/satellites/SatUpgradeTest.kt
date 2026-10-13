package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
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
import java.time.Instant

/**
 * The satellite upgrade button stays locked from the server's yes until the
 * satellite is back (Kamron, 2026-10-13: it stayed enabled after a
 * successful request, on the dashboard and here). The web twin is
 * satellites.jsx satUpgradeView; the server's record is
 * domovoi/satellite_upgrades.py.
 */
class SatUpgradeTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(
            baseUrlProvider = { server.url("/").toString().trimEnd('/') },
            deviceTokenProvider = { "household-token-1234" },
        )
    }

    @After fun down() = server.shutdown()

    private val t0 = Instant.parse("2026-10-13T22:00:00Z").toEpochMilli()

    private fun sat(
        status: String = "online",
        version: String? = "old1234",
        upgrade: SatUpgrade? = null,
    ) = Satellite(room_id = "kitchen", status = status, version = version, upgrade = upgrade)

    private fun rec(state: String, started: String = "2026-10-13T22:00:00+00:00", back: String? = null) =
        SatUpgrade(state = state, expected_sha = "new1234", from_sha = "old1234", back_sha = back, started_at = started)

    private val press = SatUpgradePress(atMs = t0, expected = "new1234", serverStartedMs = t0)

    @Test fun theRosterCarriesTheRecordAndAnOlderServerNone() {
        val rows = DomovoiJson.parseToJsonElement(
            """[{"room_id":"kitchen","status":"online","upgrade":{"state":"running","expected_sha":"new1234",
               "started_at":"2026-10-13T22:00:00+00:00","deadline_at":"2026-10-13T22:05:00+00:00"}},
               {"room_id":"garage","status":"online"}]""",
        ).decode<List<Satellite>>()
        assertEquals("running", rows[0].upgrade?.state)
        assertEquals("new1234", rows[0].upgrade?.expected_sha)
        assertNull(rows[1].upgrade)
    }

    @Test fun aPressLocksUntilTheRosterCarriesItsRecord() {
        // The roster in hand predates the press: still locked.
        val v = satUpgradeView(sat(), press, t0 + 1000)
        assertTrue(v.running)
        assertEquals("new1234", v.expected)
        // An older record (a previous upgrade) is not this press's news.
        val old = satUpgradeView(sat(upgrade = rec("done", started = "2026-10-13T21:00:00+00:00")), press, t0 + 1000)
        assertTrue(old.running)
    }

    @Test fun theRecordOfThePressSaysHowItEnded() {
        assertTrue(satUpgradeView(sat(upgrade = rec("running")), press, t0 + 5000).running)
        val done = satUpgradeView(sat(version = "new1234", upgrade = rec("done")), press, t0 + 60_000)
        assertEquals("done", done.state)
        assertTrue(done.finished)
        assertEquals("upgraded to new1234", satUpgradeOutcome("kitchen", done, line = true))
        val back = satUpgradeView(sat(upgrade = rec("rolled_back", back = "old1234")), press, t0 + 60_000)
        assertEquals("rolled_back", back.state)
        assertTrue(satUpgradeOutcome("kitchen", back).startsWith("kitchen came back on its old code (old1234)"))
        val silent = satUpgradeView(sat(upgrade = rec("no_news")), press, t0 + 400_000)
        assertEquals("no_news", silent.state)
    }

    @Test fun aReloadWithoutAPressStillLocksOnARunningRecord() {
        val v = satUpgradeView(sat(status = "offline", version = null, upgrade = rec("running")), null, t0 + 20_000)
        assertTrue(v.running)
        assertEquals(t0, v.startedMs)
        assertFalse(satUpgradeView(sat(), null, t0).running)
        assertNull(satUpgradeView(sat(), null, t0).state)
    }

    @Test fun anOlderServerEndsThePressOnTheReportedVersionOrTheWait() {
        val noRecord = SatUpgradePress(atMs = t0, expected = "new1234", serverStartedMs = null)
        assertTrue(satUpgradeView(sat(status = "offline", version = null), noRecord, t0 + 10_000).running)
        assertEquals("done", satUpgradeView(sat(version = "new1234"), noRecord, t0 + 30_000).state)
        assertEquals("no_news", satUpgradeView(sat(), noRecord, t0 + SAT_UPGRADE_WAIT_MS + 1).state)
        // Settled: the outcome holds until the next press...
        val settled = noRecord.copy(outcome = "done")
        assertEquals("done", satUpgradeView(sat(version = "new1234"), settled, t0 + 40_000).state)
        // ...unless a dashboard starts another one meanwhile.
        assertTrue(satUpgradeView(sat(upgrade = rec("running")), settled, t0 + 50_000).running)
    }

    @Test fun theClockReadsMinutesAndSeconds() {
        assertEquals("0:00", satUpgradeClock(-5))
        assertEquals("1:05", satUpgradeClock(65_000))
    }

    @Test fun thePressRemembersWhatTheServerRecorded() = runBlocking {
        server.enqueue(MockResponse().setBody(
            """{"requested":true,"room_id":"kitchen","expected_sha":"new1234",
               "upgrade":{"state":"running","expected_sha":"new1234","started_at":"2026-10-13T22:00:00+00:00"}}""",
        ))
        val p = requestSatUpgrade(api, "kitchen", nowMs = t0 + 7)
        val r = server.takeRequest()
        assertEquals("POST", r.method)
        assertEquals("/api/satellites/kitchen/upgrade", r.path)
        assertEquals("new1234", p.expected)
        assertEquals(t0, p.serverStartedMs)
        assertEquals(t0 + 7, p.atMs)
    }

    @Test fun anOlderServersAnswerHasNoRecord() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"requested":true,"room_id":"kitchen","expected_sha":"new1234"}"""))
        val p = requestSatUpgrade(api, "kitchen")
        assertEquals("new1234", p.expected)
        assertNull(p.serverStartedMs)
    }

    @Test fun aRefusalThrowsSoTheButtonStaysOffered() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(409).setBody(
            """{"detail":"kitchen is already upgrading (asked 12 s ago)"}""",
        ))
        try {
            requestSatUpgrade(api, "kitchen")
            fail("a 409 must throw")
        } catch (e: ApiException) {
            assertEquals(409, e.status)
        }
    }
}
