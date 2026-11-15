package com.domovoi.app.ui.screens.people

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * Voice ID's "needs to learn voices again" state (GET /api/people/voice-id,
 * Person.voice_relearn; V061) — the web People page's VoiceIdNotice,
 * PersonVoiceRelearn and roster dot (web/static/people.jsx), on the phone.
 */
class VoiceIdNoticeTest {

    private fun person(json: String) = DomovoiJson.decodeFromString(Person.serializer(), json)
    private fun status(json: String) = parseVoiceIdStatus(DomovoiJson.parseToJsonElement(json))

    private val ready = VoiceIdStatus(ready = true)
    private val off = VoiceIdStatus(ready = false, problem = "Internet access is off, so the voice model can't be fetched.")
    private val alex = Person(id = 1, name = "Alex", voice_relearn = true)
    private val jordan = Person(id = 3, name = "Jordan", voice_profile_count = 2)

    private val relearnBody = "Voice recognition was upgraded, and voices it knew before can't be used. " +
        "Until each person says “my name is …” in a room and answers yes, Domovoi won’t know who is speaking. "

    // ---- parsing ----------------------------------------------------------

    @Test fun voiceRelearnIsReadAndMissingMeansFalse() {
        assertTrue(person("""{"id":1,"name":"Alex","voice_relearn":true}""").voice_relearn)
        assertFalse(person("""{"id":1,"name":"Alex","voice_relearn":false}""").voice_relearn)
        // An older server has no such field.
        assertFalse(person("""{"id":1,"name":"Alex","voice_profile_count":3}""").voice_relearn)
        assertFalse(person("""{"id":1,"name":"Alex","voice_relearn":null}""").voice_relearn)
    }

    @Test fun statusIsReadWithMissingFieldsHarmless() {
        assertEquals(VoiceIdStatus(ready = true, problem = null, relearn = 2), status("""{"ready":true,"problem":null,"relearn":2}"""))
        assertEquals(off, status("""{"ready":false,"problem":"${off.problem}","relearn":0}"""))
        // Missing pieces never claim voice ID is off, nor that anyone must relearn.
        assertEquals(VoiceIdStatus(ready = true, problem = null, relearn = 0), status("""{}"""))
        assertEquals(VoiceIdStatus(ready = true, relearn = 0), status("""{"ready":null,"relearn":null,"extra":1}"""))
        assertNull(status("""[]"""))
        assertNull(status("""null"""))
    }

    // ---- loading: a missing route is quiet --------------------------------

    @Test fun aServerWithoutTheRouteIsAbsent() = runBlocking {
        assertEquals(VoiceIdRead.Absent, loadVoiceId { throw ApiException(404, """404 Not Found: {"detail":"Not Found"}""") })
        // An older server takes "voice-id" for /api/people/{person_id} and refuses a non-number.
        assertEquals(VoiceIdRead.Absent, loadVoiceId { throw ApiException(422, "422 Unprocessable Entity") })
        assertNull(nextVoiceId(null, VoiceIdRead.Absent))
        assertNull(nextVoiceId(ready.copy(relearn = 2), VoiceIdRead.Absent))
        assertNull(voiceIdNotice(nextVoiceId(null, VoiceIdRead.Absent)))
    }

    @Test fun otherFailuresKeepWhatIsShown() = runBlocking {
        assertEquals(VoiceIdRead.Failed, loadVoiceId { throw ApiException(500, "500 Internal Server Error") })
        assertEquals(VoiceIdRead.Failed, loadVoiceId { throw IOException("Failed to connect to /192.168.1.20:6369") })
        assertEquals(VoiceIdRead.Failed, loadVoiceId { DomovoiJson.parseToJsonElement("[1]") })
        val shown = ready.copy(relearn = 2)
        assertEquals(shown, nextVoiceId(shown, VoiceIdRead.Failed))
        assertNull(nextVoiceId(null, VoiceIdRead.Failed))
    }

    @Test fun anAnswerReplacesWhatIsShown() = runBlocking {
        val read = loadVoiceId { DomovoiJson.parseToJsonElement("""{"ready":true,"relearn":3}""") }
        assertEquals(VoiceIdRead.Known(ready.copy(relearn = 3)), read)
        assertEquals(ready.copy(relearn = 3), nextVoiceId(off, read))
    }

    // ---- the household notice ---------------------------------------------

    @Test fun offSaysSoWithTheServersProblemAsIs() {
        assertEquals(
            VoiceNotice("Voice recognition is off", "Internet access is off, so the voice model can't be fetched."),
            voiceIdNotice(off),
        )
        // Off wins over relearn: nobody is asked to introduce themselves.
        assertEquals(VOICE_ID_OFF_TITLE, voiceIdNotice(off.copy(relearn = 4))?.title)
        assertEquals(VoiceNotice("Voice recognition is off", null), voiceIdNotice(VoiceIdStatus(ready = false, problem = " ")))
    }

    @Test fun relearnSaysOnePersonInTheSingular() {
        assertEquals(
            VoiceNotice("Domovoi needs to learn voices again", relearnBody + "1 person still has to."),
            voiceIdNotice(ready.copy(relearn = 1)),
        )
    }

    @Test fun relearnSaysNPeopleInThePlural() {
        assertEquals(
            VoiceNotice("Domovoi needs to learn voices again", relearnBody + "3 people still have to."),
            voiceIdNotice(ready.copy(relearn = 3)),
        )
    }

    @Test fun nothingToSayWhenReadyAndNobodyRelearns() {
        assertNull(voiceIdNotice(null))
        assertNull(voiceIdNotice(ready))
        assertNull(voiceIdNotice(ready.copy(relearn = -1)))
    }

    // ---- the person's notice ----------------------------------------------

    @Test fun personNoticeNamesThem() {
        assertEquals(
            VoiceNotice(
                "Not recognized by voice yet",
                "Voice recognition was upgraded, so Domovoi has to learn Alex voice again. " +
                    "To be recognized again, Alex can say “my name is Alex” in a room and answer yes.",
            ),
            personVoiceRelearnNotice(alex, ready),
        )
    }

    @Test fun personNoticeWithoutANameSaysTheirAndThey() {
        assertEquals(
            "Voice recognition was upgraded, so Domovoi has to learn their voice again. " +
                "To be recognized again, they can say “my name is …” in a room and answer yes.",
            personVoiceRelearnNotice(alex.copy(name = " "), ready)?.body,
        )
    }

    @Test fun personNoticeOnlyWhenTheyRelearnAndVoiceIdIsNotOff() {
        assertNull(personVoiceRelearnNotice(jordan, ready))
        assertNull(personVoiceRelearnNotice(alex, off))
        // Voice ID's state unknown (older server, not read yet): the person's own flag decides.
        assertEquals(PERSON_RELEARN_TITLE, personVoiceRelearnNotice(alex, null)?.title)
    }

    // ---- the roster dot ---------------------------------------------------

    @Test fun rosterDotForSomeoneWhoRelearnsUnlessVoiceIdIsOff() {
        assertEquals("has to say “my name is …” again to be recognized by voice", RELEARN_DOT_DESCRIPTION)
        assertTrue(showRelearnDot(alex, ready))
        assertTrue(showRelearnDot(alex, null))
        assertFalse(showRelearnDot(alex, off))
        assertFalse(showRelearnDot(jordan, ready))
    }

    // ---- an older server changes nothing ----------------------------------

    @Test fun anOlderServerShowsNothingNew() = runBlocking {
        val p = person("""{"id":3,"name":"Jordan","voice_profile_count":2}""")
        val shown = nextVoiceId(null, loadVoiceId { throw ApiException(404, "404 Not Found") })
        assertNull(voiceIdNotice(shown))
        assertNull(personVoiceRelearnNotice(p, shown))
        assertFalse(showRelearnDot(p, shown))
        assertTrue(showVoiceRooms(p, listOf(VoiceRoom("kitchen", current = true)), shown))
    }
}
