package com.domovoi.app.mix

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DEVICE_ID_HEADER
import com.domovoi.app.net.DeviceOwner
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.TokenHealth
import com.domovoi.app.net.decode
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The Music mix (1.3.0): the shapes as web/backend/api/music_mix.py writes
 * them, whose sliders win, which controls this app may show (wf/mix-gate),
 * how a refusal is said, and the words the dashboard uses
 * (web/static/music_mix.jsx).
 */
class MusicMixTest {

    // ── The wire ─────────────────────────────────────────────────────────

    /** `_mix_answer`: Mix.json() (discovery_favorites, close_wander,
     *  autoplay, list_repeat_min) + steps, list_repeat_min_range, internet,
     *  people_with_own_mix. Every value off its default, so a misnamed field
     *  can't pass on the default. */
    @Test fun decodesTheHouseholdMixFieldForField() {
        val json = """
            {"discovery_favorites":1,"close_wander":5,"autoplay":false,"list_repeat_min":120,
             "steps":[1,2,3,4,5],"list_repeat_min_range":[20,500],
             "internet":{"similar_artists":true,"popularity":false,"state":"ok"},
             "people_with_own_mix":[4,7]}
        """.trimIndent()
        val m = DomovoiJson.parseToJsonElement(json).decode<MusicMix>()
        assertEquals(1, m.discoveryFavorites)
        assertEquals(5, m.closeWander)
        assertFalse(m.autoplay)
        assertEquals(120, m.listRepeatMin)
        assertEquals(listOf(1, 2, 3, 4, 5), m.steps)
        assertEquals(listOf(20, 500), m.listRepeatMinRange)
        assertEquals(true, m.internet.similarArtists)
        assertEquals(false, m.internet.popularity)
        assertEquals("ok", m.internet.state)
        assertEquals(listOf(4L, 7L), m.peopleWithOwnMix)
    }

    /** `_internet` before the core reported: nulls and "unknown". */
    @Test fun anInternetNotReportedYetIsNotKnown() {
        val m = DomovoiJson.parseToJsonElement(
            """{"discovery_favorites":3,"close_wander":3,"autoplay":true,"list_repeat_min":50,"steps":[1,2,3,4,5],
               "list_repeat_min_range":[20,500],"internet":{"similar_artists":null,"popularity":null,"state":"unknown"},
               "people_with_own_mix":[]}""",
        ).decode<MusicMix>()
        assertNull(m.internet.similarArtists)
        assertEquals(NOT_KNOWN_YET, onOff(m.internet.similarArtists))
        assertEquals("on", onOff(true))
        assertEquals("off", onOff(false))
    }

    /** `_person_answer`: {person_id, override, uses}. */
    @Test fun decodesAPersonsMixBothWays() {
        val house = DomovoiJson.parseToJsonElement(
            """{"person_id":4,"override":null,"uses":{"discovery_favorites":2,"close_wander":4}}""",
        ).decode<PersonMix>()
        assertEquals(4L, house.personId)
        assertFalse("null override: the household's", house.ownMix)
        assertEquals(MixSliders(2, 4), house.uses)

        // A stored override may hold one slider only (mix.py person_override).
        val own = DomovoiJson.parseToJsonElement(
            """{"person_id":7,"override":{"close_wander":5},"uses":{"discovery_favorites":2,"close_wander":5}}""",
        ).decode<PersonMix>()
        assertTrue(own.ownMix)
        assertNull(own.override!!.discoveryFavorites)
        assertEquals(5, own.override!!.closeWander)
        assertEquals(MixSliders(2, 5), own.uses)
    }

    // ── Whose sliders win ────────────────────────────────────────────────

    @Test fun turningTheHouseholdsMixOffStartsFromWhatThePersonUsesNow() {
        // They used the household's (2, 4): their own starts there, not at 3/3.
        val uses = MixSliders(2, 4)
        assertEquals(MixSliders(2, 4), ownMixFrom(uses))
        assertEquals("""{"override":{"discovery_favorites":2,"close_wander":4}}""", personMixBody(ownMixFrom(uses)).toString())
        // Moving one slider keeps the other as it is used now.
        assertEquals(MixSliders(5, 4), withStep(uses, discovery = true, step = 5))
        assertEquals(MixSliders(2, 1), withStep(uses, discovery = false, step = 1))
        // Back to the household's: null, never the household's numbers copied.
        assertEquals("""{"override":null}""", personMixBody(null).toString())
        // Missing values (an older answer) fall back to the middle, as the web does.
        assertEquals(MixSliders(3, 3), ownMixFrom(MixSliders()))
    }

    // ── Which controls a phone shows ─────────────────────────────────────

    @Test fun aPhoneNeverOffersTheHouseholdsMixOrTheInternetSwitches() {
        assertFalse("the app holds no admin session", APP_IS_ADMIN)
        assertEquals(MixRights(household = false, internet = false), mixRights())
        assertEquals(MixRights(household = true, internet = true), mixRights(admin = true))
    }

    @Test fun onlyThePersonThisPhoneIsGetsSliders() {
        val sam = DeviceOwner(id = 4, name = "Sam")
        assertTrue("this phone is Sam's", personMixEditable(4, sam, shared = false))
        assertFalse("someone else's page", personMixEditable(7, sam, shared = false))
        assertFalse("a phone that is nobody's", personMixEditable(4, null, shared = false))
        assertFalse("a shared screen is nobody's", personMixEditable(4, sam, shared = true))
        assertTrue("an admin may change anyone's", personMixEditable(7, null, shared = false, admin = true))
    }

    // ── Refusals ─────────────────────────────────────────────────────────

    @Test fun aRefusedPersonMixIsSaidInTheServersWords() {
        val words = "Only an admin, or this person's own phone, can change their mix."
        assertEquals(words, PERSON_MIX_NOT_YOURS)
        val other = """{"detail":"something the server said"}"""
        assertEquals("something the server said", mixWriteRefusal(ApiException(403, "403", body = other)))
        assertEquals(PERSON_MIX_NOT_YOURS, mixWriteRefusal(ApiException(403, "403", body = "")))
        assertNull("not a refusal: the usual failure line", mixWriteRefusal(ApiException(500, "500")))
        assertNull(mixWriteRefusal(java.io.IOException("down")))
    }

    @Test fun theAdminGates401IsAnAdminsNotALostPairing() {
        val admin = ApiException(401, "401", body = """{"detail":"admin session required"}""")
        assertEquals(ADMIN_CAN_CHANGE_THIS, mixWriteRefusal(admin))
        val token = ApiException(401, "401", body = """{"detail":"X-Device-Token or admin session required"}""")
        assertNull("the household token itself: the pairing flow's", mixWriteRefusal(token))
    }

    /** Through the real client: `PUT /api/music/mix`'s 401 neither raises
     *  the pairing screen nor marks the household token refused. */
    @Test fun aHouseholdMixPut401LeavesThePairingAlone() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            val base = server.url("/").toString().trimEnd('/')
            val health = TokenHealth()
            val api = ApiClient({ base }, { "household-phrase-1234" }, health = health)
            server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"admin session required"}"""))
            val e = try {
                api.put("/api/music/mix", DomovoiJson.parseToJsonElement("""{"autoplay":false}"""))
                fail("a phone's PUT is refused"); null
            } catch (x: ApiException) { x }!!
            assertFalse("no pairing screen", api.pairingRequired.value)
            assertFalse("no pairing screen", e.deviceTokenRequired)
            assertFalse("the token is not marked refused", health.isRejected(base))
            assertEquals(ADMIN_CAN_CHANGE_THIS, mixWriteRefusal(e))
        } finally {
            server.shutdown()
        }
    }

    /** `PUT /api/people/{id}/music-mix` names this phone (the server
     *  matches its owner), and a 403 comes back in the server's words. */
    @Test fun aPersonMixPutNamesThisPhoneAndSaysTheServers403() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            val base = server.url("/").toString().trimEnd('/')
            val api = ApiClient({ base }, { "household-phrase-1234" })
            server.enqueue(MockResponse().setBody(
                """{"person_id":4,"override":{"discovery_favorites":5,"close_wander":3},"uses":{"discovery_favorites":5,"close_wander":3}}""",
            ))
            val saved = savePersonMix(api, base, "android-1a2b", "held-key", 4, MixSliders(5, 3))
            val req = server.takeRequest()
            assertEquals("PUT", req.method)
            assertEquals("/api/people/4/music-mix", req.path)
            assertEquals("android-1a2b", req.getHeader(DEVICE_ID_HEADER))
            // As for clearing listening history (security review 2026-11-22, L40).
            assertEquals("held-key", req.getHeader(com.domovoi.app.net.CLAIM_KEY_HEADER))
            assertEquals("""{"override":{"discovery_favorites":5,"close_wander":3}}""", req.body.readUtf8())
            assertEquals(MixSliders(5, 3), saved.uses)

            server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"$PERSON_MIX_NOT_YOURS"}"""))
            val e = try {
                savePersonMix(api, base, "android-1a2b", null, 7, null)
                fail("refused"); null
            } catch (x: ApiException) { x }!!
            assertEquals(PERSON_MIX_NOT_YOURS, mixWriteRefusal(e))
            assertFalse(api.pairingRequired.value)
        } finally {
            server.shutdown()
        }
    }

    // ── Autoplay: the person's choice here, else the household's ────────

    @Test fun effectiveAutoplayIsTheChoiceThenTheHouseholdThenOn() {
        assertTrue(effectiveAutoplay(null, null))
        assertFalse(effectiveAutoplay(null, false))
        assertTrue(effectiveAutoplay(null, true))
        assertTrue(effectiveAutoplay(true, false))
        assertFalse(effectiveAutoplay(false, true))
    }

    @Test fun theHouseholdsDefaultIsKeptByServer() {
        val d = MixDefaults()
        d.heard("http://192.0.2.1:6369/", MusicMix(autoplay = false))
        assertEquals(false, d.autoplayFor("http://192.0.2.1:6369"))
        assertNull("another house's switch learns nothing", d.autoplayFor("http://192.0.2.9:6369"))
    }

    @Test fun thisPhonesLineUnderTheDefault() {
        assertEquals("This phone follows this until you switch autoplay in its queue.", phoneAutoplayNote(null))
        assertEquals("This phone: autoplay on (switched in its queue).", phoneAutoplayNote(true))
        assertEquals("This phone: autoplay off (switched in its queue).", phoneAutoplayNote(false))
    }

    // ── The words (music_mix.jsx) ────────────────────────────────────────

    @Test fun theDashboardsWords() {
        assertEquals("Discovery", MIX_DISCOVERY.left)
        assertEquals("Favorites", MIX_DISCOVERY.right)
        assertEquals("Stay close", MIX_WANDER.left)
        assertEquals("Wander", MIX_WANDER.right)
        assertEquals("a balance (the default)", MIX_DISCOVERY.said(3))
        assertEquals("a balance (the default)", MIX_WANDER.said(null))
        assertEquals("mostly songs you haven’t heard in a while", MIX_DISCOVERY.said(1))
        assertEquals("mostly what you play most", MIX_DISCOVERY.said(5))
        assertEquals("stays close to the song playing", MIX_WANDER.said(1))
        assertEquals("wanders widely across your library", MIX_WANDER.said(5))
        assertEquals("Autoplay when a playlist ends", AUTOPLAY_DEFAULT_LABEL)
        assertEquals("Similar artists from the internet", SIMILAR_ARTISTS_LABEL)
        assertEquals("Song popularity from the internet", POPULARITY_LABEL)
        assertEquals("Use the household’s mix", USE_HOUSEHOLDS_MIX)
        assertEquals("An admin can change this.", ADMIN_CAN_CHANGE_THIS)
        assertEquals("An admin, or Sam’s own phone, can change this.", personMixReadOnlyNote("Sam"))
        assertEquals("120 songs", noRepeatsValue(120))
    }
}
