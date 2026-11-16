package com.domovoi.app.ui.screens.people

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * "last heard in" on a person's profile — the web People page's heardIn
 * (web/static/people.jsx): the room of the newest row of
 * GET /api/people/{id}/conversations, which the server sends newest first.
 */
class PersonLastHeardInTest {

    private fun turn(room: String?, at: String = "2026-11-15T20:00:00Z") = PersonTurn(at = at, room_id = room)

    @Test fun aRoomIsNamedWithDashesAndUnderscoresAsSpaces() {
        assertEquals("kitchen", lastHeardIn(listOf(turn("kitchen"))))
        assertEquals("living room", lastHeardIn(listOf(turn("living-room"))))
        assertEquals("dining room", lastHeardIn(listOf(turn("dining__room"))))
        assertEquals("office", lastHeardIn(listOf(turn("  office "))))
    }

    @Test fun aPhonesChatMicIsThePhone() {
        assertEquals("the phone", lastHeardIn(listOf(turn("phone-3f9a"))))
        assertEquals("the phone", lastHeardIn(listOf(turn("phone-"))))
        // Only the prefix counts: a room merely named like a phone is a room.
        assertEquals("phone booth", lastHeardIn(listOf(turn("phone_booth"))))
    }

    @Test fun aTurnWithNoRoomCameFromChat() {
        assertEquals("chat", lastHeardIn(listOf(turn(null))))
        assertEquals("chat", lastHeardIn(listOf(turn(""))))
        assertEquals("chat", lastHeardIn(listOf(turn("   "))))
    }

    @Test fun neverHeardIsADash() {
        assertEquals("—", lastHeardIn(emptyList()))
    }

    /** The server sends newest first (ORDER BY at DESC), so the first row is
     *  the newest and wins over every older one. */
    @Test fun theNewestRowWins() {
        val turns = listOf(
            turn("phone-3f9a", at = "2026-11-15T21:00:00Z"),
            turn("kitchen", at = "2026-11-15T20:00:00Z"),
            turn(null, at = "2026-11-14T08:00:00Z"),
        )
        assertEquals("the phone", lastHeardIn(turns))
        assertEquals("kitchen", lastHeardIn(turns.drop(1)))
        assertEquals("chat", lastHeardIn(turns.drop(2)))
    }

    /** A failed read is not "never heard" (F-A008: "?", like the tab counts). */
    @Test fun aFailedReadIsAQuestionMarkNotADash() {
        assertEquals("?", lastHeardIn(emptyList(), failed = true))
        assertEquals("?", lastHeardIn(listOf(turn("kitchen")), failed = true))
    }

    /** Before the first read lands nothing is known yet; a refresh keeps
     *  what was shown. */
    @Test fun loadingWithNothingReadIsAnEllipsis() {
        assertEquals("…", lastHeardIn(emptyList(), loading = true))
        assertEquals("kitchen", lastHeardIn(listOf(turn("kitchen")), loading = true))
    }
}
