package com.domovoi.app.net

import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * DownloadManager is its own HTTP stack, so the app's two rules are applied
 * to what it is handed (security round 3, A6-05): no plain http outside the
 * home network, and the household token only for the active server — and
 * only once that server has proved itself on this network.
 */
class DeviceDownloadsTest {
    private lateinit var server: MockWebServer
    private var token: String? = "household-abc"

    @Before fun up() { server = MockWebServer().also { it.start() } }
    @After fun down() = server.shutdown()

    private fun base() = server.url("/").toString().trimEnd('/')

    @Test fun aSaveFromTheActiveServerCarriesTheToken() {
        val api = ApiClient({ base() }, { token })
        val plan = DeviceDownloads.plan(api, "/api/music/library/7/audio?download=1")
        assertNull(plan.refusal)
        assertEquals("${base()}/api/music/library/7/audio?download=1", plan.url)
        assertEquals("household-abc", plan.token)
    }

    @Test fun aPublicPlainHttpAddressIsRefusedWithThePolicysOwnWords() {
        // The A6-05 shape: a public address saved as the server (the
        // Connection panel now refuses it up front; this is the backstop).
        val api = ApiClient({ "http://203.0.113.5:6369" }, { token })
        val plan = DeviceDownloads.plan(api, "/api/music/library/7/audio?download=1")
        assertEquals(CleartextPolicy.refusalMessage("203.0.113.5"), plan.refusal)
        assertNull("no token for a request that will not be made", plan.token)
    }

    @Test fun aUrlOnAnotherHostGetsNoToken() {
        val api = ApiClient({ base() }, { token })
        val plan = DeviceDownloads.plan(api, "http://127.0.0.1:${server.port}/grab.mp3")
        assertNull(plan.refusal)
        assertNull(plan.token)
        assertNull(DeviceDownloads.plan(api, "https://cdn.example/episode.mp3").token)
    }

    @Test fun anUnpairedPhoneSendsNoHeader() {
        token = null
        val api = ApiClient({ base() }, { token })
        assertNull(DeviceDownloads.plan(api, "/api/videos/1/stream?download=1").token)
    }

    @Test fun theTokenWaitsForTheServersProofLikeEveryOtherRequest() {
        // A gate that has not admitted the server yet (a new network, no
        // proof so far) holds the token back; one that has lets it through.
        var admitted = false
        val gate = object : TokenGate {
            override fun requireAdmitted(base: okhttp3.HttpUrl) {
                if (!admitted) throw java.io.IOException("not yet")
            }
            override fun admitsTokenNow(base: okhttp3.HttpUrl): Boolean = admitted
        }
        val api = ApiClient({ base() }, { token }, gate)
        assertNull(DeviceDownloads.plan(api, "/api/audiobooks/3/download").token)
        admitted = true
        assertEquals("household-abc", DeviceDownloads.plan(api, "/api/audiobooks/3/download").token)
    }

    @Test fun safeNameStillScrubsWhatTheBackendWould() {
        assertEquals("a b c", DeviceDownloads.safeName("a/b\\c"))
        assertEquals("audio", DeviceDownloads.safeName("..."))
    }
}
