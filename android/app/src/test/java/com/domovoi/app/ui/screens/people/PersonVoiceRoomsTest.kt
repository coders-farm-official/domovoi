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
 * Rooms that know this voice (GET /api/people/{id}/voice-rooms) — the web
 * People page's PersonVoiceRooms (web/static/people.jsx), on the phone.
 */
class PersonVoiceRoomsTest {

    private fun rooms(json: String) = parseVoiceRooms(DomovoiJson.parseToJsonElement(json))

    private val kitchen = VoiceRoom("kitchen", current = true)
    private val office = VoiceRoom("office", legacy = true)
    private val garage = VoiceRoom("garage")

    @Test fun parsesEveryRoomWithMissingFlagsAsFalse() {
        val parsed = rooms(
            """[
              {"room_id":"kitchen","current":true,"legacy":false},
              {"room_id":"office","legacy":true},
              {"room_id":"garage"},
              {"room_id":"den","current":null,"legacy":true,"extra":1}
            ]""",
        )
        assertEquals(
            listOf(kitchen, office, garage, VoiceRoom("den", legacy = true)),
            parsed,
        )
        assertTrue(parsed[0].known)
        assertTrue(parsed[1].known)
        assertFalse(parsed[2].known)
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

    @Test fun chipsMarkOnlyRoomsWithNeitherSample() {
        assertEquals("kitchen", voiceRoomChip(kitchen))
        assertEquals("office", voiceRoomChip(office))
        assertEquals("garage · not yet", voiceRoomChip(garage))
        assertEquals("rooms that know this voice", VOICE_ROOMS_LABEL)
    }

    @Test fun hintOnlyWhenARoomIsNotYet() {
        assertNull(voiceRoomsHint("Priya", listOf(kitchen, office)))
        assertEquals(
            "Rooms recognize a voice best once they have heard it themselves. In each room " +
                "marked “not yet”, Priya can say “my name is Priya” and answer yes.",
            voiceRoomsHint("Priya", listOf(kitchen, garage)),
        )
    }

    @Test fun hintWithoutANameSaysTheyAndAnEllipsis() {
        assertEquals(
            "Rooms recognize a voice best once they have heard it themselves. In each room " +
                "marked “not yet”, they can say “my name is …” and answer yes.",
            voiceRoomsHint("  ", listOf(garage)),
        )
    }

    @Test fun hiddenWhenThereAreNoRooms() {
        assertFalse(showVoiceRooms(emptyList()))
        assertTrue(showVoiceRooms(listOf(garage)))
    }

    @Test fun aServerWithoutTheRouteHidesTheSectionQuietly() = runBlocking {
        val loaded = loadVoiceRooms { throw ApiException(404, """404 Not Found: {"detail":"Not Found"}""") }
        assertEquals(emptyList<VoiceRoom>(), loaded)
        assertFalse(showVoiceRooms(loaded!!))
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
