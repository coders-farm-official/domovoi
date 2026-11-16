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
 * Rooms that have learned this voice (GET /api/people/{id}/voice-rooms) — the web
 * People page's PersonVoiceRooms (web/static/people.jsx), on the phone.
 */
class PersonVoiceRoomsTest {

    private fun rooms(json: String) = parseVoiceRooms(DomovoiJson.parseToJsonElement(json))

    private val kitchen = VoiceRoom("kitchen", current = true)
    private val office = VoiceRoom("office", current = true)
    private val garage = VoiceRoom("garage")

    /** What a 1.3.0 build from before V061 (never released) sent for a room
     *  whose only sample was pre-1.3.0; that server still used it. */
    private val oldBuildRoom = VoiceRoom("den", legacy = true)

    private val priya = Person(id = 2, name = "Priya", voice_profile_count = 2)

    @Test fun parsesEveryRoomWithMissingFlagsAsFalse() {
        val parsed = rooms(
            """[
              {"room_id":"kitchen","current":true},
              {"room_id":"office","current":true,"extra":1},
              {"room_id":"garage"},
              {"room_id":"hall","current":null}
            ]""",
        )
        assertEquals(listOf(kitchen, office, garage, VoiceRoom("hall")), parsed)
        assertEquals(listOf(true, true, false, false), parsed.map { it.known })
    }

    /** Since V061 a room knows a voice only with a sample of the current
     *  model: the server sends `current` alone, and false is "not yet learned". */
    @Test fun aRoomIsKnownOnlyWhenCurrent() {
        val parsed = rooms("""[{"room_id":"kitchen","current":true},{"room_id":"garage","current":false}]""")
        assertEquals("1/2", voiceRoomsCount(parsed))
        assertEquals(listOf("kitchen · learned", "garage · not yet learned"), parsed.map(::voiceRoomChip))
        assertEquals(
            "Every room can recognize Priya from any sample. A room recognizes a voice more " +
                "reliably once it has learned it there: in a room marked “not yet learned”, Priya can " +
                "say “my name is Priya” and answer yes.",
            voiceRoomsHint("Priya", parsed),
        )
    }

    @Test fun aPreV061BuildsLegacyRoomIsNotCalledNotYetLearned() {
        val parsed = rooms("""[{"room_id":"den","current":false,"legacy":true}]""")
        assertEquals(listOf(oldBuildRoom), parsed)
        assertEquals("1/1", voiceRoomsCount(parsed))
        assertEquals("den · learned", voiceRoomChip(parsed.single()))
        assertNull(voiceRoomsHint("Priya", parsed))
    }

    @Test fun dropsRowsThatAreNotRooms() {
        assertEquals(
            listOf(kitchen),
            rooms("""[{"current":true}, {"room_id":""}, 7, null, {"room_id":"kitchen","current":true}]"""),
        )
        assertEquals(emptyList<VoiceRoom>(), rooms("""{"detail":"Not Found"}"""))
        assertEquals(emptyList<VoiceRoom>(), rooms("[]"))
    }

    @Test fun countIsKnownOverTotal() {
        assertEquals("2/3", voiceRoomsCount(listOf(kitchen, office, garage)))
        assertEquals("0/1", voiceRoomsCount(listOf(garage)))
        assertEquals("2/2", voiceRoomsCount(listOf(kitchen, office)))
    }

    /** Owner, 1.3.0 device check: every chip says learned or not yet
     *  learned, never "knows" (any room can recognize the person). */
    @Test fun chipsSayLearnedOrNotYetLearned() {
        assertEquals("kitchen · learned", voiceRoomChip(kitchen))
        assertEquals("office · learned", voiceRoomChip(office))
        assertEquals("garage · not yet learned", voiceRoomChip(garage))
        assertEquals("dining-room · not yet learned", voiceRoomChip(VoiceRoom("dining-room")))
        assertEquals("rooms that have learned this voice", VOICE_ROOMS_LABEL)
    }

    @Test fun hintOnlyWhenARoomHasNotYetLearned() {
        assertNull(voiceRoomsHint("Priya", listOf(kitchen, office)))
        assertEquals(
            "Every room can recognize Priya from any sample. A room recognizes a voice more " +
                "reliably once it has learned it there: in a room marked “not yet learned”, Priya can " +
                "say “my name is Priya” and answer yes.",
            voiceRoomsHint("Priya", listOf(kitchen, garage)),
        )
    }

    @Test fun hintWithoutANameSaysThisPersonTheyAndAnEllipsis() {
        assertEquals(
            "Every room can recognize this person from any sample. A room recognizes a voice more " +
                "reliably once it has learned it there: in a room marked “not yet learned”, they can " +
                "say “my name is …” and answer yes.",
            voiceRoomsHint("  ", listOf(garage)),
        )
    }

    @Test fun hiddenWhenThereAreNoRooms() {
        assertFalse(showVoiceRooms(priya, emptyList(), null))
        assertTrue(showVoiceRooms(priya, listOf(garage), null))
    }

    @Test fun hiddenWhileThePersonHasToIntroduceThemselvesAgain() {
        val relearning = priya.copy(voice_relearn = true, voice_profile_count = 0)
        assertFalse(showVoiceRooms(relearning, listOf(kitchen, garage), null))
        assertFalse(showVoiceRooms(relearning, listOf(kitchen, garage), VoiceIdStatus(ready = true, relearn = 1)))
    }

    @Test fun hiddenWhileVoiceIdCannotRun() {
        val off = VoiceIdStatus(ready = false, problem = "The voice model isn't on this server yet.")
        assertFalse(showVoiceRooms(priya, listOf(kitchen, garage), off))
        assertTrue(showVoiceRooms(priya, listOf(kitchen, garage), VoiceIdStatus(ready = true)))
    }

    @Test fun aServerWithoutTheRouteHidesTheSectionQuietly() = runBlocking {
        val loaded = loadVoiceRooms { throw ApiException(404, """404 Not Found: {"detail":"Not Found"}""") }
        assertEquals(emptyList<VoiceRoom>(), loaded)
        assertFalse(showVoiceRooms(priya, loaded!!, null))
    }

    @Test fun otherFailuresKeepWhatIsShown() = runBlocking {
        assertNull(loadVoiceRooms { throw ApiException(500, "500 Internal Server Error") })
        assertNull(loadVoiceRooms { throw IOException("Failed to connect to /192.168.1.20:6369") })
    }

    @Test fun anAnswerIsParsed() = runBlocking {
        assertEquals(
            listOf(kitchen, garage),
            loadVoiceRooms {
                DomovoiJson.parseToJsonElement("""[{"room_id":"kitchen","current":true},{"room_id":"garage"}]""")
            },
        )
    }
}
