package com.domovoi.app.listens

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.CLAIM_KEY_HEADER
import com.domovoi.app.net.DEVICE_ID_HEADER
import com.domovoi.app.net.DeviceOwner
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * People → a person: clearing listening history from this phone (own
 * phone → DELETE with its id and claim key; anyone else's → no DELETE, the
 * dashboard's sentence and the two offers), the "this phone" block's
 * words, and "listening as".
 */
class ListeningHistoryTest {
    private lateinit var web: MockWebServer
    private lateinit var base: String
    private val alex = 3L
    private val now = 1_762_970_591_000L                  // 2025-11-12T18:03:11Z
    private val longAgo = "2025-11-12T17:00:00.123456Z"   // an hour before
    private val justNow = "2025-11-12T17:58:11+00:00"     // five minutes before

    @Before fun up() {
        web = MockWebServer().also { it.start() }
        base = web.url("/").toString().trimEnd('/')
    }

    @After fun down() = web.shutdown()

    // ── Clearing ──────────────────────────────────────────────────────────

    @Test fun ownPhoneClears() = runBlocking {
        val check = clearCheck(alex, "Alex", DeviceOwner(alex, "Alex", longAgo), shared = false, keyHeld = true, nowMs = now)
        assertEquals(ClearCheck.Allowed, check)
        web.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("""{"deleted":42}"""))
        val n = clearListeningHistory(ApiClient(baseUrlProvider = { base }), base, "android-1a2b", "held-key", alex)
        val sent = web.takeRequest()
        assertEquals("DELETE", sent.method)
        assertEquals("/api/music/listens/people/3", sent.path)
        assertEquals("android-1a2b", sent.getHeader(DEVICE_ID_HEADER))
        assertEquals("held-key", sent.getHeader(CLAIM_KEY_HEADER))
        assertEquals(42, n)
    }

    @Test fun someoneElsesPhoneDoesNotClear() {
        val check = clearCheck(alex, "Alex", DeviceOwner(5, "Sam", longAgo), shared = false, keyHeld = true, nowMs = now)
        check as ClearCheck.NotHere
        assertEquals("Each person clears their own. Only Alex’s own phone or browser, or an admin, can clear it.", check.sentence)
        assertEquals("This phone is Sam’s.", check.why)
        assertEquals(listOf(ClearOffer.ThisIsMe, ClearOffer.OpenDashboard), check.offers)
        assertEquals("nothing was sent", 0, web.requestCount)
    }

    @Test fun nobodysPhoneSharedScreenNoKeyAndAFreshClaimDoNotClear() {
        fun check(owner: DeviceOwner?, shared: Boolean = false, key: Boolean = true) =
            clearCheck(alex, "Alex", owner, shared, key, now) as ClearCheck.NotHere
        check(null).let {
            assertTrue(it.why.startsWith("If this is Alex’s phone, say “this is me” on this page"))
            assertEquals(listOf(ClearOffer.ThisIsMe, ClearOffer.OpenDashboard), it.offers)
        }
        check(null, shared = true).let {
            assertEquals("This is a shared screen, so it can’t be anyone’s.", it.why)
            assertEquals(listOf(ClearOffer.OpenDashboard), it.offers)
        }
        check(DeviceOwner(alex, "Alex", longAgo), key = false).let {
            assertEquals("This phone is Alex’s but can’t prove it yet: say “this is me” again on this page.", it.why)
            assertEquals(listOf(ClearOffer.ThisIsMe, ClearOffer.OpenDashboard), it.offers)
        }
        check(DeviceOwner(alex, "Alex", justNow)).let {
            assertEquals("This phone said it was Alex’s less than 10 minutes ago. Until then only an admin can clear it.", it.why)
            assertEquals(listOf(ClearOffer.OpenDashboard), it.offers)
        }
        // A claim an admin set (no claimed-at) counts as fresh: an admin's.
        check(DeviceOwner(alex, "Alex", null))
    }

    @Test fun theSummaryDecodesTheServersAnswer() {
        val s = DomovoiJson.decodeFromString(ListensSummary.serializer(), """
            {"keep":"365","keep_label":"1 year","keep_days":365,
             "choices":[{"value":"365","label":"1 year"},{"value":"90","label":"90 days"},{"value":"off","label":"Don't keep"}],
             "total":57,"oldest_at":"2026-10-01T12:00:00Z","household":4,
             "people":[{"person_id":3,"name":"Alex","listens":42},{"person_id":5,"name":"Sam","listens":11}]}
        """)
        assertEquals("42 songs played for Alex · kept a year", listensLine(s, alex, "Alex"))
        assertEquals("nothing kept for Kim", listensLine(s, 9, "Kim"))
        val off = s.copy(keep = "off", keepLabel = "Don't keep")
        assertEquals("Domovoi keeps no listening history (Settings → Privacy → Listening history).", listensLine(off, alex, "Alex"))
        assertEquals(
            "This deletes the 42 songs played for Alex in every room and on every phone and browser. " +
                "Up next stops learning from them. Songs played for the whole household and for other people stay.",
            clearConfirmText("Alex", 42),
        )
    }

    // ── "This phone" ──────────────────────────────────────────────────────

    @Test fun theThisPhoneBlockIsTheDashboards() {
        thisPhoneBlock(alex, "Alex", null, shared = false, keyHeld = false, deviceCount = 0).let {
            assertEquals("Is this Alex’s phone? Then what plays here counts as Alex’s listening, and Alex can clear it from here.", it.line)
            assertEquals(PhoneAction.ThisIsMe, it.action)
            assertEquals("no phone or browser is Alex’s", it.small)
        }
        thisPhoneBlock(alex, "Alex", DeviceOwner(alex, "Alex"), shared = false, keyHeld = true, deviceCount = 2).let {
            assertEquals("This phone is Alex’s. What plays here counts as Alex’s listening, and Alex can clear it from here.", it.line)
            assertEquals(PhoneAction.NotMe, it.action)
            assertEquals("1 other phone or browser is Alex’s", it.small)
        }
        thisPhoneBlock(alex, "Alex", DeviceOwner(alex, "Alex"), shared = false, keyHeld = false, deviceCount = 1).let {
            assertEquals("This phone is Alex’s, but it can’t prove it yet. Say “this is me” again to act as Alex here.", it.line)
            assertEquals(PhoneAction.ThisIsMe, it.action)
        }
        thisPhoneBlock(alex, "Alex", DeviceOwner(5, "Sam"), shared = false, keyHeld = true, deviceCount = 2).let {
            assertEquals("This phone is Sam’s.", it.line)
            assertEquals(PhoneAction.ItsTheirsInstead, it.action)
            assertEquals("2 phones or browsers are Alex’s · an admin has to say yes to add this one", it.small)
        }
        thisPhoneBlock(alex, "Alex", null, shared = true, keyHeld = false, deviceCount = 1).let {
            assertEquals("This is a shared screen, so it isn’t anyone’s: what plays here counts for the whole household.", it.line)
            assertNull("no button on a shared screen", it.action)
            assertEquals("1 phone or browser is Alex’s", it.small)
        }
    }

    // ── "Listening as" ────────────────────────────────────────────────────

    @Test fun listeningAsFollowsThePhoneUntilPicked() {
        assertEquals("3", ListenerPicks.effective(null, 3, shared = false))
        assertNull("a shared screen is the household's", ListenerPicks.effective(null, 3, shared = true))
        assertEquals("5", ListenerPicks.effective("5", 3, shared = false))
        assertNull(ListenerPicks.effective(ListenerPicks.HOUSEHOLD, 3, shared = false))

        assertNull("picking the owner again follows the phone", ListenerPicks.toStore("3", 3, shared = false))
        assertEquals("5", ListenerPicks.toStore("5", 3, shared = false))
        assertEquals(ListenerPicks.HOUSEHOLD, ListenerPicks.toStore(null, 3, shared = false))
        assertNull("household on a phone nobody claimed is just following it", ListenerPicks.toStore(null, null, shared = false))
    }
}
