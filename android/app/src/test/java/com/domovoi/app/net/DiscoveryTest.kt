package com.domovoi.app.net

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.util.concurrent.TimeUnit

/**
 * The LAN sweep and the manual-add probe ask `/api/health` and
 * `/api/config` of hosts that are not (yet) the server, before the trust
 * dialog. Neither request may carry the household token — until 2026-10-08
 * both did, for every one of the 254 addresses swept (security round 3,
 * A6-02).
 */
class DiscoveryTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        // The phone is paired with THIS server, the worst case: a probe of
        // the active server itself would be in the token's scope.
        api = ApiClient({ server.url("/").toString().trimEnd('/') }, { "household-abc" })
    }

    @After fun down() = server.shutdown()

    @Test fun theDiscoveryClientHasNoTokenInterceptor() {
        val client = Discovery.client(api.http, 500)
        assertTrue(api.http.interceptors.any { it is DeviceAuthInterceptor })
        assertTrue(client.interceptors.none { it is DeviceAuthInterceptor })
        assertTrue("the cleartext policy stays", client.interceptors.contains(CleartextPolicy.interceptor))
    }

    /** A well-formed advertised identity: the RFC 8032 test key. */
    private val seed = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
        .chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private val publicKey = java.util.Base64.getEncoder().encodeToString(Ed25519.publicKey(seed))
    private val fingerprint = ServerIdentity.fingerprintOf(Ed25519.publicKey(seed))
    private val identityBlock = """{"algorithm":"ed25519","public_key":"$publicKey","fingerprint":"$fingerprint"}"""

    @Test fun aProbeCarriesNoTokenEvenToTheActiveServer() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"status":"ok","identity":$identityBlock}"""))
        server.enqueue(MockResponse().setBody("""{"bot_name":"kitchen-box"}"""))

        val hit = Discovery.probe(api.http, server.url("/").toString())

        assertEquals(server.url("/").toString().trimEnd('/'), hit?.url)
        assertEquals("kitchen-box", hit?.name)
        assertEquals(fingerprint, hit?.fingerprint)
        assertEquals(ServerIdentity.Pin(publicKey, fingerprint), hit?.identity)
        val health = server.takeRequest(5, TimeUnit.SECONDS)!!
        val config = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("/api/health", health.path)
        assertEquals("/api/config", config.path)
        assertNull(health.getHeader(DEVICE_TOKEN_HEADER))
        assertNull(config.getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun aHostThatIsNotADashboardIsNotAHit() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404))
        assertNull(Discovery.probe(api.http, server.url("/").toString()))
        assertNull(server.takeRequest(5, TimeUnit.SECONDS)!!.getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun anAdvertisedIdentityIsShownOnlyWhenItsKeyAndFingerprintAgree() {
        assertEquals(fingerprint, Discovery.advertisedFingerprint("""{"identity":$identityBlock}"""))
        assertNull("an older web backend", Discovery.advertisedFingerprint("""{"status":"ok"}"""))
        assertNull("a fingerprint with no key behind it is nothing to pin", Discovery.advertisedFingerprint("""{"identity":{"fingerprint":"SHA256:abc"}}"""))
        assertNull(
            "a fingerprint that is not the key's",
            Discovery.advertisedFingerprint("""{"identity":{"algorithm":"ed25519","public_key":"$publicKey","fingerprint":"SHA256:abc"}}"""),
        )
        assertNull(
            "an algorithm the phone does not verify",
            Discovery.advertisedFingerprint("""{"identity":{"algorithm":"rsa","public_key":"$publicKey","fingerprint":"$fingerprint"}}"""),
        )
        assertNull(Discovery.advertisedFingerprint("""{"identity":{"fingerprint":""}}"""))
        assertNull(Discovery.advertisedFingerprint("not json"))
        assertNull(Discovery.advertisedFingerprint(null))
    }
}
