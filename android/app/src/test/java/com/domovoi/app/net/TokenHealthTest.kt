package com.domovoi.app.net

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
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * A server that no longer knows this phone (2026-10-17): reinstalled at the
 * same address, replaced, or its household token rotated. The phone used to
 * keep sending the old token on every poll, and the server's wrong-token
 * ledger (5 free, then a doubling wait to 5 min, per address) answered 429
 * for good: each time its window opened, the phone's next poll failed again
 * and started a new one.
 *
 * Now: a token the server refuses is sent once and never again (requests go
 * out without it, which the ledger does not count), and while the server
 * says "wait" nothing that would carry the token is sent at all. The fake
 * below keeps the server's real ledger, so "the app never extends a lockout
 * by itself" is measured, not assumed.
 */
class TokenHealthTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    private val now = AtomicLong(5_000_000L)

    /** A Domovoi web backend's device tier with DEVICE_TOKEN_BACKOFF
     *  (domovoi/admin_auth.py): a throttled attempt is not looked at and not
     *  counted; a checked wrong token is a failure; a right one clears it. */
    private inner class FakeHousehold {
        @Volatile var goodToken = "rotated-household-phrase"
        @Volatile var failures = 0
        @Volatile var lastFailure = 0L
        /** Called when the request arrives, before the answer (a re-pair racing it). */
        @Volatile var onArrival: (String?) -> Unit = {}
        /** The credential check cannot reach its database: 503, never a verdict. */
        @Volatile var dbDown = false
        /** Every token the ledger actually LOOKED AT. */
        val evaluated = CopyOnWriteArrayList<String>()
        val requests = CopyOnWriteArrayList<RecordedRequest>()

        fun waitMs(): Long {
            val charged = failures - 5
            if (charged <= 0) return 0
            val delay = minOf(1000L shl (charged - 1).coerceAtMost(20), 300_000L)
            return (lastFailure + delay - now.get()).coerceAtLeast(0)
        }

        val server = MockWebServer().apply {
            dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests += request
                    val path = request.path.orEmpty()
                    val token = request.getHeader(DEVICE_TOKEN_HEADER)
                    onArrival(token)
                    if (path.startsWith("/api/health")) return MockResponse().setBody("""{"status":"ok"}""")
                    if (path.startsWith("/api/admin")) {
                        return MockResponse().setResponseCode(401).setBody("""{"detail":"admin session required"}""")
                    }
                    if (path.startsWith("/api/plugins/")) {
                        // A plugin's own refusal that happens to name the header.
                        return MockResponse().setResponseCode(401)
                            .setBody("""{"detail":"this plugin wants a fresh X-Device-Token"}""")
                    }
                    if (dbDown) {
                        return MockResponse().setResponseCode(503).setHeader("Retry-After", "5")
                            .setBody("""{"detail":"auth check unavailable — the server could not check credentials just now; try again shortly"}""")
                    }
                    if (path.startsWith("/api/fetch")) {
                        return MockResponse().setResponseCode(429).setHeader("Retry-After", "30")
                            .setBody("""{"detail":"too many failed attempts — retry in 30s"}""")
                    }
                    if (token == null) return refusal()
                    val wait = waitMs()
                    if (wait > 0) {
                        val s = (wait + 999) / 1000
                        return MockResponse().setResponseCode(429).setHeader("Retry-After", s.toString())
                            .setBody("""{"detail":"too many wrong X-Device-Token values — retry in ${s}s, or sign in as an admin"}""")
                    }
                    evaluated += token
                    if (token == goodToken) {
                        failures = 0
                        return MockResponse().setBody("{}")
                    }
                    failures += 1
                    lastFailure = now.get()
                    return refusal()
                }
            }
            start()
        }

        private fun refusal() = MockResponse().setResponseCode(401)
            .setBody("""{"detail":"X-Device-Token or admin session required"}""")

        val url: String get() = server.url("/").toString().trimEnd('/')
        fun withToken() = requests.filter { it.getHeader(DEVICE_TOKEN_HEADER) != null }
    }

    private lateinit var fake: FakeHousehold
    private val book = TokenHealth.InMemoryBook()
    private val log = CopyOnWriteArrayList<String>()
    private lateinit var health: TokenHealth
    @Volatile private var token: String? = "old-household-phrase"
    private lateinit var api: ApiClient

    @Before fun up() {
        fake = FakeHousehold()
        health = TokenHealth(book, clock = { now.get() }, log = { log += it })
        api = ApiClient({ fake.url }, { token }, health = health)
    }

    @After fun down() = fake.server.shutdown()

    private fun get(path: String = "/api/rooms"): Result<Any> = runCatching { runBlocking { api.get(path) } }

    // ---- token wrong: stop at once ------------------------------------------

    @Test fun aRefusedTokenIsSentOnceAndThenNeverAgain() {
        val first = get().exceptionOrNull()
        assertTrue(first is ApiException && first.status == 401)
        assertTrue("the refusal is remembered", health.isRejected(fake.url))
        assertTrue(log.any { it.contains("refused this phone's household token") })

        repeat(4) { get() }
        assertEquals("the ledger looked at the old token once", listOf("old-household-phrase"), fake.evaluated.toList())
        assertEquals(1, fake.withToken().size)
        assertEquals("every later request went out without it", 5, fake.requests.size)
        assertEquals(1, fake.failures)
    }

    @Test fun noTokenAtAllIsNotAWrongToken() {
        token = null
        get()
        assertFalse("an unpaired phone is the pairing screen's business", health.isRejected(fake.url))
    }

    @Test fun anAdminTierRefusalIsNotAboutTheHouseholdToken() {
        token = fake.goodToken
        get("/api/admin/x")
        assertFalse(health.isRejected(fake.url))
        assertTrue(get().isSuccess)
    }

    @Test fun aRefusalOfATokenReplacedMeanwhileSaysNothingAboutTheNewOne() {
        // The person pastes the new token while a request with the old one is
        // in flight; the old one's 401 must not mark the new one refused.
        fake.onArrival = { sent -> if (sent == "old-household-phrase") token = fake.goodToken }
        get()
        assertFalse(health.isRejected(fake.url))
        fake.onArrival = {}
        assertTrue(get().isSuccess)
        assertEquals(fake.goodToken, fake.requests.last().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun aNewTokenIsSentAgainOnceTheRefusalIsCleared() {
        get()
        assertTrue(health.isRejected(fake.url))
        // Prefs.setDeviceTokenFor clears the refusal with the new token.
        token = fake.goodToken
        book.clear(fake.url)
        assertTrue(get().isSuccess)
        assertEquals(fake.goodToken, fake.requests.last().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun theWebSocketUpgradeStopsCarryingARefusedTokenToo() {
        get()
        val req = api.wsRequest("ws://${fake.server.hostName}:${fake.server.port}/ws/state")
        assertEquals("the builder still sets it", "old-household-phrase", req.header(DEVICE_TOKEN_HEADER))
        // The upgrade request itself, sent as the plain GET it starts as.
        api.http.newCall(req).execute().close()
        assertTrue(fake.requests.last().path!!.startsWith("/ws/state"))
        assertNull("...but the interceptor has the last word", fake.requests.last().getHeader(DEVICE_TOKEN_HEADER))
    }

    // ---- 429: back off to Retry-After ---------------------------------------

    @Test fun theServersWaitIsHonouredAndThePhoneNeverExtendsItsOwnLockout() {
        // The owner's phone: it has already sent wrong tokens all day and
        // the ledger is deep in its doubling (14 failures: a 256 s step).
        fake.failures = 14
        fake.lastFailure = now.get()
        val first = get().exceptionOrNull()
        assertTrue(first is ApiException && first.status == 429)
        val wait = health.waitMs(fake.url)
        assertTrue("waits what Retry-After said, $wait", wait in 255_000L..256_000L)

        // A minute of polling (the panels, the state socket, the reach probe):
        // nothing leaves the phone, and the shell still counts it as there.
        val before = fake.requests.size
        repeat(12) {
            now.addAndGet(5_000)
            val e = get().exceptionOrNull()
            assertTrue("held here, $e", e is TokenThrottledException)
        }
        assertTrue(runBlocking { api.answers() })
        assertEquals("not one request during the wait", before, fake.requests.size)
        assertEquals("not one token looked at during the wait", 0, fake.evaluated.size)

        // Past the wait: the next request is checked once, refused, and the
        // phone stops. Over the whole session the ledger saw ONE attempt.
        now.addAndGet(200_000)
        assertTrue(get().exceptionOrNull() is ApiException)
        repeat(5) { get() }
        assertEquals(listOf("old-household-phrase"), fake.evaluated.toList())
        assertTrue(health.isRejected(fake.url))
    }

    @Test fun aRightTokenWaitsOutTheServersWindowThenWorks() {
        // Paired again with the right token, but the address is still inside
        // the wait the old token earned: the phone waits, then it works.
        fake.failures = 9
        fake.lastFailure = now.get()
        token = fake.goodToken
        assertTrue((get().exceptionOrNull() as ApiException).status == 429)
        assertTrue(get().exceptionOrNull() is TokenThrottledException)
        now.addAndGet(health.waitMs(fake.url) + 1)
        assertTrue(get().isSuccess)
        assertEquals(listOf(fake.goodToken), fake.evaluated.toList())
    }

    @Test fun aTooManyRequestsAboutSomethingElseDoesNotHoldTheToken() {
        token = fake.goodToken
        get("/api/fetch")
        assertEquals(0L, health.waitMs(fake.url))
        assertTrue(get().isSuccess)
    }

    // ---- review fixes (2026-10-17) -------------------------------------------

    @Test fun aDatabaseBlipIsNotARefusal() {
        // S-T2: the server answers 503 when it cannot check credentials; a good
        // token must survive it.
        token = fake.goodToken
        fake.dbDown = true
        assertEquals(503, (get().exceptionOrNull() as ApiException).status)
        assertFalse(health.isRejected(fake.url))
        assertEquals(0L, health.waitMs(fake.url))
        fake.dbDown = false
        assertTrue(get().isSuccess)
        assertEquals(fake.goodToken, fake.requests.last().getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun onlyTheServersOwnRefusalDropsTheToken() {
        // N-T2: a plugin's 401 naming the header is not the core's verdict.
        token = fake.goodToken
        get("/api/plugins/x/thing")
        assertFalse(health.isRejected(fake.url))
        assertTrue(isServerDeviceRefusal(401, """{"detail":"X-Device-Token or admin session required"}"""))
        assertTrue(isServerDeviceRefusal(403, """{"detail":"X-Device-Token required — the dashboard cookie does not authorize device-tier actions"}"""))
        assertFalse(isServerDeviceRefusal(401, """{"detail":"this plugin wants a fresh X-Device-Token"}"""))
        assertFalse(isServerDeviceRefusal(503, """{"detail":"X-Device-Token or admin session required"}"""))
    }

    // ---- a pasted token is checked before it is saved -----------------------

    @Test fun aPastedTokenTheServerTakesIsAccepted() {
        token = null
        assertEquals(TokenCheck.Accepted, runBlocking { api.checkDeviceToken(fake.goodToken) })
        val probe = fake.requests.last()
        assertTrue(probe.path!!.startsWith(TOKEN_CHECK_PATH))
        assertEquals("the candidate, not the stored token", fake.goodToken, probe.getHeader(DEVICE_TOKEN_HEADER))
        assertEquals(1, fake.requests.size)
    }

    @Test fun aWrongPasteIsRefusedOnceAndNeverRetried() {
        assertEquals(TokenCheck.Refused, runBlocking { api.checkDeviceToken("old-custom-token-1") })
        assertEquals("one wrong token on the server's count, no more", 1, fake.evaluated.size)
        assertEquals(1, fake.requests.size)
        assertFalse("not saved, so nothing is marked", health.isRejected(fake.url))
    }

    @Test fun aPasteDuringTheServersWaitIsNotSentAndSaysHowLong() {
        fake.failures = 9
        fake.lastFailure = now.get()
        val first = runBlocking { api.checkDeviceToken(fake.goodToken) }
        assertTrue(first is TokenCheck.Throttled && first.waitMs in 7_000L..8_000L)
        val sent = fake.requests.size
        val second = runBlocking { api.checkDeviceToken(fake.goodToken) }
        assertTrue(second is TokenCheck.Throttled)
        assertEquals("the wait is respected: nothing sent", sent, fake.requests.size)
    }

    @Test fun aPasteTheServerCouldNotCheckIsUnavailable() {
        fake.dbDown = true
        assertEquals(TokenCheck.Unavailable, runBlocking { api.checkDeviceToken(fake.goodToken) })
        fake.server.shutdown()
        assertEquals(TokenCheck.Unavailable, runBlocking { api.checkDeviceToken(fake.goodToken) })
    }

    @Test fun retryAfterIsReadAsSecondsOrADateClampedAndDefaulted() {
        assertEquals(120_000L, TokenHealth.retryAfterMs("120"))
        assertEquals(TokenHealth.DEFAULT_WAIT_MS, TokenHealth.retryAfterMs(null))
        assertEquals(TokenHealth.DEFAULT_WAIT_MS, TokenHealth.retryAfterMs("soon"))
        assertEquals(1_000L, TokenHealth.retryAfterMs("0"))
        assertEquals(TokenHealth.MAX_WAIT_MS, TokenHealth.retryAfterMs("86400"))
        val wall = 1_800_000_000_000L
        val date = ZonedDateTime.ofInstant(java.time.Instant.ofEpochMilli(wall + 30_000), ZoneOffset.UTC)
            .format(DateTimeFormatter.RFC_1123_DATE_TIME)
        assertEquals(30_000L, TokenHealth.retryAfterMs(date) { wall })
    }

    @Test fun onlyTheDeviceTiersThrottleCounts() {
        assertTrue(TokenHealth.isDeviceTokenThrottle("""{"detail":"too many wrong X-Device-Token values — retry in 53s, or sign in as an admin"}"""))
        // The wording since 2026-10-17, which names the usual cause.
        assertTrue(
            TokenHealth.isDeviceTokenThrottle(
                """{"detail":"too many wrong X-Device-Token values from this address — retry in 53s. A device or browser tab at this address may still be sending an old household token (after a reinstall or a token change): pair it again or close it, or sign in as an admin"}""",
            ),
        )
        assertFalse(TokenHealth.isDeviceTokenThrottle("""{"detail":"too many failed attempts — retry in 30s"}"""))
        assertFalse(TokenHealth.isDeviceTokenThrottle(null))
    }

    @Test fun aSaveToDeviceDownloadNeitherGetsARefusedTokenNorSkipsTheWait() {
        get()
        val url = fake.server.url("/api/files/1/raw")
        assertNull(runBlocking { api.tokenForDownload(url) })

        book.clear(fake.url)
        fake.failures = 9
        fake.lastFailure = now.get()
        get()
        try {
            runBlocking { api.tokenForDownload(url) }
            fail("expected TokenThrottledException")
        } catch (_: TokenThrottledException) {
        }
    }
}
