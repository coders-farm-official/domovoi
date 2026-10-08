package com.domovoi.app.net

import android.app.DownloadManager
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * DownloadManager is its own HTTP stack that keeps every request header
 * in its own database, so the app's rules are applied to what it is
 * handed (security round 3, A6-05 and its review): no plain http outside
 * the home network; no household token at all for the open routes (music,
 * podcasts, audiobooks); and for the video stream, the one device-tier
 * save, the token only for the active server once it has proved itself on
 * this network right now — and not for longer than needed.
 */
class DeviceDownloadsTest {
    private lateinit var server: MockWebServer
    private var token: String? = "household-abc"

    @Before fun up() { server = MockWebServer().also { it.start() } }
    @After fun down() = server.shutdown()

    private fun base() = server.url("/").toString().trimEnd('/')

    /** A gate that records how often it was asked and answers as told. */
    private class HandGate : TokenGate {
        var admitted = false
        var asked = 0
        override fun requireAdmitted(base: okhttp3.HttpUrl) {
            asked++
            if (!admitted) throw ServerIdentityException("server identity mismatch: not the Domovoi this phone paired with")
        }
    }

    @Test fun anOpenRouteSaveCarriesNoTokenEvenFromTheActiveProvenServer() {
        val gate = HandGate().apply { admitted = true }
        val api = ApiClient({ base() }, { token }, gate)
        for (path in listOf(
            "/api/music/library/7/audio?download=1",
            "/api/podcasts/episodes/3/audio?download=1",
            "/api/audiobooks/3/download",
        )) {
            val plan = DeviceDownloads.plan(api, path)
            assertNull(plan.refusal)
            assertEquals("${base()}$path", plan.url)
            assertNull("the system database never holds the token for an open read", plan.token)
        }
        assertEquals("and no proof is asked for", 0, gate.asked)
    }

    @Test fun aPublicPlainHttpAddressIsRefusedWithThePolicysOwnWords() = runBlocking {
        // The A6-05 shape: a public address saved as the server (the
        // Connection panel now refuses it up front; this is the backstop).
        val api = ApiClient({ "http://203.0.113.5:6369" }, { token })
        val plan = DeviceDownloads.plan(api, "/api/music/library/7/audio?download=1")
        assertEquals(CleartextPolicy.refusalMessage("203.0.113.5"), plan.refusal)
        assertNull("no token for a request that will not be made", plan.token)
        assertEquals(plan, DeviceDownloads.planWithToken(api, "/api/music/library/7/audio?download=1"))
    }

    @Test fun theVideoSaveCarriesTheTokenOnlyAfterTheServerProvedItselfNow() = runBlocking {
        val gate = HandGate()
        val api = ApiClient({ base() }, { token }, gate)
        val path = "/api/videos/stream?library_id=films&path=a.mkv&download=1"

        // Away from home, or before the proof: a refusal that says why, and
        // nothing queued with the token in it.
        val refused = DeviceDownloads.planWithToken(api, path)
        assertNull(refused.token)
        assertTrue(refused.refusal, refused.refusal!!.startsWith("not saved: server identity mismatch"))
        assertEquals(1, gate.asked)

        // Proved: the token goes with it — asked for again, not remembered here.
        gate.admitted = true
        val plan = DeviceDownloads.planWithToken(api, path)
        assertNull(plan.refusal)
        assertEquals("household-abc", plan.token)
        assertEquals(2, gate.asked)
    }

    @Test fun aVideoSaveOnAnotherHostOrFromAnUnpairedPhoneGetsNoTokenAndAsksNoProof() = runBlocking {
        val gate = HandGate().apply { admitted = true }
        val api = ApiClient({ base() }, { token }, gate)
        val other = DeviceDownloads.planWithToken(api, "http://127.0.0.1:${server.port}/grab.mp4")
        assertNull(other.refusal)
        assertNull(other.token)
        assertEquals(0, gate.asked)

        token = null
        val unpaired = DeviceDownloads.planWithToken(api, "/api/videos/stream?library_id=films&path=a.mkv&download=1")
        assertNull(unpaired.token)
        assertEquals("pair this phone first (Settings → Connection)", unpaired.refusal)
        assertEquals(0, gate.asked)
    }

    @Test fun aTokenBearingSaveIsCancelledWhenTheNetworkChangesAndForgottenWhenDone() {
        val statuses = mapOf(
            1L to DownloadManager.STATUS_RUNNING,
            2L to DownloadManager.STATUS_PAUSED,
            3L to DownloadManager.STATUS_SUCCESSFUL,
            4L to DownloadManager.STATUS_FAILED,
            5L to DownloadManager.STATUS_PENDING,
        )
        // The network changed: nothing unfinished carries on, a failed row
        // goes (it keeps the header for nothing), a finished one is only
        // forgotten (removing it would delete the file).
        assertEquals(
            DeviceDownloads.Triage(cancel = setOf(1L, 2L, 4L, 5L), forget = setOf(3L)),
            DeviceDownloads.triage(statuses, cancelRunning = true),
        )
        // At a plain start, a running one is on the connection it began on.
        assertEquals(
            DeviceDownloads.Triage(cancel = setOf(2L, 4L, 5L), forget = setOf(1L, 3L)),
            DeviceDownloads.triage(statuses, cancelRunning = false),
        )
        assertEquals(DeviceDownloads.Triage(emptySet(), emptySet()), DeviceDownloads.triage(emptyMap(), cancelRunning = true))
    }

    @Test fun safeNameStillScrubsWhatTheBackendWould() {
        assertEquals("a b c", DeviceDownloads.safeName("a/b\\c"))
        assertEquals("audio", DeviceDownloads.safeName("..."))
    }
}
