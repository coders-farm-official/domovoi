package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.io.IOException
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Before the household token goes to the saved server on a network, the
 * server proves it holds the key this phone pinned (security round 3,
 * A6-03): the ApiClient's interceptor asks the gate, the gate asks
 * `/api/health?challenge=…` without a token, and only a signed answer from
 * the pinned key lets the request out. A different key, or no identity
 * where one is pinned, and the request never leaves — the state socket,
 * the background sync and a ringing alarm's confirm all go through the
 * same client. A rogue's `401` does not raise the pairing screen either.
 */
class IdentityGateTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(90)

    /** A fake Domovoi web backend on a MockWebServer: signs challenges with
     *  [seed] (null: a server from before identity), and records what the
     *  `/api/timers` read carried. */
    private inner class FakeDomovoi {
        var seed: ByteArray? = hex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        var healthStatus = 200
        /** The health route accepts the connection and never answers. */
        var healthStalls = false
        val requests = CopyOnWriteArrayList<RecordedRequest>()
        val server = MockWebServer().apply {
            dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests += request
                    val path = request.path.orEmpty()
                    return when {
                        path.startsWith("/api/health") -> {
                            if (healthStalls) return MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)
                            if (healthStatus != 200) return MockResponse().setResponseCode(healthStatus)
                            val challenge = request.requestUrl?.queryParameter("challenge")
                            MockResponse().setBody(healthBody(challenge))
                        }
                        path.startsWith("/ws/state") -> MockResponse().withWebSocketUpgrade(object : WebSocketListener() {})
                        path == "/api/refuse" -> MockResponse().setResponseCode(401)
                            .setBody("""{"detail":"X-Device-Token or admin session required"}""")
                        else -> MockResponse().setBody("{}")
                    }
                }
            }
            start()
        }

        fun healthBody(challenge: String?): String {
            val s = seed ?: return """{"status":"ok","stt":"ok"}"""
            val pub = Ed25519.publicKey(s)
            val b64 = Base64.getEncoder()
            val identity = buildString {
                append("""{"algorithm":"ed25519","public_key":"${b64.encodeToString(pub)}",""")
                append(""""fingerprint":"${ServerIdentity.fingerprintOf(pub)}"""")
                if (challenge != null) {
                    val sig = Ed25519.sign(s, ServerIdentity.healthMessage(challenge))
                    append(""","challenge":"$challenge","signature":"${b64.encodeToString(sig)}"""")
                }
                append("}")
            }
            return """{"status":"ok","stt":"ok","identity":$identity}"""
        }

        val url: String get() = server.url("/").toString().trimEnd('/')
        fun probes() = requests.filter { it.path.orEmpty().startsWith("/api/health") }
        fun reads() = requests.filter { it.path == "/api/timers" }
        fun fingerprint(): String = ServerIdentity.fingerprintOf(Ed25519.publicKey(seed!!))
    }

    private class Pins : IdentityGate.PinStore {
        val book = HashMap<String, ServerIdentity.Pin>()
        override fun pinFor(key: String) = book[key]
        override fun pin(key: String, pin: ServerIdentity.Pin) { book[key] = pin }
    }

    private fun hex(s: String): ByteArray = s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    private lateinit var fake: FakeDomovoi
    private val pins = Pins()
    private val log = CopyOnWriteArrayList<String>()
    private var now = 1_000_000L
    /** The network fingerprint the phone is on (NetworkWatch in the app). */
    private var net = "D|101|wifi|||wlan0|192.168.1.57/24|192.168.1.1|192.168.1.1|"
    private var token: String? = "household-abc"
    private lateinit var gate: IdentityGate
    private lateinit var api: ApiClient

    @Before fun up() {
        fake = FakeDomovoi()
        // Wired as AppContainer wires it: the probe runs on a token-less copy
        // of the app's own client, and the verdict is keyed to the network.
        gate = IdentityGate(
            probe = { base, challenge -> IdentityGate.httpProbe(Discovery.client(api.http, 1000), base, challenge) },
            pins = pins,
            network = { net },
            clock = { now },
            log = { log += it },
        )
        api = ApiClient({ fake.url }, { token }, gate)
    }

    @After fun down() = fake.server.shutdown()

    private fun read() = runBlocking { api.get("/api/timers") }

    private fun key() = IdentityGate.pinKey(TokenScope.baseOf(fake.url)!!)

    // ---- first contact ------------------------------------------------------

    @Test fun theFirstContactPinsTheServerAndTheTokenFlows() {
        read()
        // One token-less probe, then the read with the token.
        assertEquals(1, fake.probes().size)
        assertNull(fake.probes()[0].getHeader(DEVICE_TOKEN_HEADER))
        assertTrue(fake.probes()[0].path!!.contains("challenge="))
        assertEquals("household-abc", fake.reads().single().getHeader(DEVICE_TOKEN_HEADER))
        assertEquals(fake.fingerprint(), pins.book[key()]?.fingerprint)
        assertEquals(IdentityVerdict.Verified(fake.fingerprint(), pinnedNow = true), gate.status.value?.verdict)
        assertTrue(log.any { it.startsWith("identity: pinned ") })

        // The proof holds for the rest of this network: no second probe.
        read(); read()
        assertEquals(1, fake.probes().size)
        assertEquals(3, fake.reads().size)
    }

    @Test fun aServerFromBeforeIdentityIsTreatedAsBeforeAndSaidSo() {
        fake.seed = null
        read()
        assertEquals("household-abc", fake.reads().single().getHeader(DEVICE_TOKEN_HEADER))
        assertTrue(pins.book.isEmpty())
        assertEquals(IdentityVerdict.Legacy, gate.status.value?.verdict)
        assertTrue(log.any { it.contains("offers no identity") })
    }

    // ---- after a network change ---------------------------------------------

    @Test fun afterANetworkChangeADifferentKeyHoldsTheTokenBack() {
        read()
        gate.networkChanged()
        // Away from home: whoever answers at the saved address has its own key.
        fake.seed = hex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        val rogueFingerprint = fake.fingerprint()
        try {
            read()
            fail("expected ServerIdentityException")
        } catch (e: ServerIdentityException) {
            assertTrue(e.message, e.message!!.contains("mismatch"))
        }
        assertEquals("the rogue saw a token-less probe and nothing else", 2, fake.probes().size)
        assertEquals(1, fake.reads().size)
        assertNull(fake.probes()[1].getHeader(DEVICE_TOKEN_HEADER))
        assertEquals(
            IdentityVerdict.Mismatch(pins.book[key()]!!.fingerprint, rogueFingerprint),
            gate.status.value?.verdict,
        )
        assertFalse("not our server, so out of reach", runBlocking { api.answers() })
        // The pin is untouched: coming home, the real server proves itself again.
        assertFalse(pins.book[key()]!!.fingerprint == rogueFingerprint)
    }

    @Test fun afterANetworkChangeTheSameServerProvesItselfOnceMore() {
        read()
        gate.networkChanged()
        read()
        assertEquals(2, fake.probes().size)
        assertEquals(2, fake.reads().size)
        assertEquals(IdentityVerdict.Verified(fake.fingerprint(), pinnedNow = false), gate.status.value?.verdict)
    }

    @Test fun aPinnedServerThatOffersNoIdentityIsNotTrustedWithTheToken() {
        read()
        gate.networkChanged()
        fake.seed = null   // a plain http.server at the saved address
        try {
            read(); fail("expected ServerIdentityException")
        } catch (e: ServerIdentityException) {
            assertTrue(e.message, e.message!!.contains("unproven"))
        }
        assertEquals(1, fake.reads().size)
        assertTrue(gate.status.value?.verdict is IdentityVerdict.Unproven)
    }

    @Test fun aRefusalIsRememberedThenRetried() {
        read()
        gate.networkChanged()
        fake.seed = null
        repeat(3) { runCatching { read() } }
        assertEquals("one probe, not one per attempt", 2, fake.probes().size)
        now += IdentityGate.RETRY_AFTER_MS
        runCatching { read() }
        assertEquals(3, fake.probes().size)
        // The real server is back at the address (home again): the next
        // retry proves it and the read goes through.
        fake.seed = hex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        now += IdentityGate.RETRY_AFTER_MS
        read()
        assertEquals(2, fake.reads().size)
    }

    @Test fun aServerThatDoesNotAnswerIsUnreachableNotRemembered() {
        read()
        gate.networkChanged()
        fake.server.shutdown()
        try {
            read(); fail("expected IOException")
        } catch (e: ServerIdentityException) {
            fail("unreachable is not an identity verdict")
        } catch (e: IOException) {
            // as before: out of reach
        }
        assertNull("nothing cached for this network", gate.cachedVerdict(TokenScope.baseOf(fake.url)!!))
    }

    @Test fun aHealthThatIsNot200ProvesNothing() {
        read()
        gate.networkChanged()
        fake.healthStatus = 404
        try {
            read(); fail("expected ServerIdentityException")
        } catch (e: ServerIdentityException) {
            assertTrue(e.message!!.contains("did not answer /api/health"))
        }
    }

    @Test fun aHealthThatStallsHoldsTheReadBackAndRemembersNothing() {
        // Only /api/health hangs; /api/timers would answer at once. The
        // probe's timeout is the answer, and the guarded read never leaves.
        read()
        gate.networkChanged()
        fake.healthStalls = true
        try {
            read(); fail("expected IOException")
        } catch (e: ServerIdentityException) {
            fail("a stalled probe is not an identity verdict")
        } catch (e: IOException) {
            // the probe's read timeout
        }
        assertEquals("the read before the change, and no other", 1, fake.reads().size)
        assertNull(gate.cachedVerdict(TokenScope.baseOf(fake.url)!!))
    }

    // ---- what a verdict is keyed to (the review's blocker) --------------------

    @Test fun aVerdictHoldsOnlyOnTheNetworkFingerprintItWasTakenOn() {
        read()
        assertEquals(1, fake.probes().size)
        // The VPN case: nothing called networkChanged(), but the watch's
        // fingerprint moved (the Wi-Fi under the VPN was replaced).
        net = "D|120|vpn||tun0|100.64.0.5/32|||;P|102|wifi||wlan0|192.168.1.57/24|192.168.1.1|192.168.1.1|"
        read()
        assertEquals("proved again on the new network", 2, fake.probes().size)
        assertEquals(2, fake.reads().size)
        // The same fingerprint again: the proof stands.
        read()
        assertEquals(2, fake.probes().size)
        // And back on the first network, the old proof is not revived.
        net = "D|101|wifi|||wlan0|192.168.1.57/24|192.168.1.1|192.168.1.1|"
        read()
        assertEquals(3, fake.probes().size)
    }

    @Test fun aProofOlderThanTheTtlIsTakenAgainOnTheSameNetwork() {
        read()
        now += IdentityGate.VERDICT_TTL_MS - 1
        read()
        assertEquals("still within the ttl", 1, fake.probes().size)
        now += 1
        read()
        assertEquals("the ttl ran out: proved again", 2, fake.probes().size)
        assertEquals(3, fake.reads().size)
    }

    @Test fun comingBackToTheForegroundAfterASpellInTheBackgroundProvesAgain() {
        read()
        // The first start is not a return from the background.
        gate.appForegrounded()
        read()
        assertEquals(1, fake.probes().size)
        // Home button, then back (or screen off, then unlock).
        gate.appBackgrounded()
        now += 5_000
        gate.appForegrounded()
        read()
        assertEquals(2, fake.probes().size)
        assertTrue(log.any { it.contains("back in the foreground") })
        // A rotation never calls appBackgrounded(), so it proves nothing new.
        gate.appForegrounded()
        read()
        assertEquals(2, fake.probes().size)
    }

    @Test fun aProofThatStraddledANetworkChangeBelongsToTheNewNetwork() {
        // The ConnectivityManager callback lands while the probe is in
        // flight: the answer came over one network, the request would leave
        // on another. The gate asks again rather than trusting a proof it
        // cannot place.
        var straddled = false
        gate = IdentityGate(
            probe = { base, challenge ->
                if (!straddled) {
                    straddled = true
                    net = "D|102|wifi|||wlan0|192.168.1.23/24|192.168.1.254|192.168.1.254|"
                }
                IdentityGate.httpProbe(Discovery.client(api.http, 1000), base, challenge)
            },
            pins = pins,
            network = { net },
            clock = { now },
            log = { log += it },
        )
        api = ApiClient({ fake.url }, { token }, gate)
        read()
        assertEquals("the straddling proof was thrown away and taken again", 2, fake.probes().size)
        assertEquals(1, fake.reads().size)
        assertTrue(log.any { it.contains("changed while") })
        assertTrue(gate.cachedVerdict(TokenScope.baseOf(fake.url)!!) is IdentityVerdict.Verified)
        // ...and under the old fingerprint nothing was remembered.
        net = "D|101|wifi|||wlan0|192.168.1.57/24|192.168.1.1|192.168.1.1|"
        assertNull(gate.cachedVerdict(TokenScope.baseOf(fake.url)!!))
    }

    // ---- the other paths through the same client ----------------------------

    @Test fun theStateSocketUpgradeIsHeldBackTheSameWay() {
        read()
        gate.networkChanged()
        fake.seed = hex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        val failed = CountDownLatch(1)
        var failure: Throwable? = null
        api.http.newWebSocket(
            api.wsRequest(fake.url.replaceFirst("http", "ws") + "/ws/state"),
            object : WebSocketListener() {
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    failure = t; failed.countDown()
                }
            },
        )
        assertTrue(failed.await(5, TimeUnit.SECONDS))
        assertTrue(failure is ServerIdentityException)
        assertTrue(fake.requests.none { it.path.orEmpty().startsWith("/ws/state") })
    }

    @Test fun aRoguesRefusalDoesNotRaiseThePairingScreen() {
        read()
        gate.networkChanged()
        fake.seed = hex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        // An unpaired phone (nothing to protect, so the request goes out
        // without a token) meets a 401 that names the header...
        token = null
        try {
            runBlocking { api.get("/api/refuse") }; fail("expected ApiException")
        } catch (e: ApiException) {
            assertFalse("...and it is not a pairing refusal from a server that is not ours", e.deviceTokenRequired)
        }
        assertFalse(api.pairingRequired.value)
        api.notePossiblePairingRefusal(401, "X-Device-Token or admin session required")
        assertFalse(api.pairingRequired.value)

        // From the real server the same refusal does ask the phone to pair.
        fake.seed = hex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        gate.networkChanged()
        try {
            runBlocking { api.get("/api/refuse") }; fail("expected ApiException")
        } catch (e: ApiException) {
            assertTrue(e.deviceTokenRequired)
        }
        assertTrue(api.pairingRequired.value)
    }

    @Test fun theGateIsNotAskedForAForeignHost() {
        val other = MockWebServer().also { it.start() }
        try {
            other.enqueue(MockResponse().setBody("{}"))
            runBlocking { api.get("http://127.0.0.1:${other.port}/api/x") }
            assertNull(other.takeRequest().getHeader(DEVICE_TOKEN_HEADER))
            assertTrue("no probe anywhere", fake.probes().isEmpty())
        } finally {
            other.shutdown()
        }
    }

    @Test fun pinKeysAreOneSpellingPerServer() {
        assertEquals("http://domovoi.lan:6369", IdentityGate.pinKey("http://Domovoi.LAN:6369/"))
        assertEquals("http://domovoi.lan:6369", IdentityGate.pinKey("http://domovoi.lan:6369"))
        assertEquals("https://domovoi.lan:443", IdentityGate.pinKey("https://domovoi.lan"))
        assertNull(IdentityGate.pinKey(""))
        assertNull(IdentityGate.pinKey("nonsense"))
    }
}
