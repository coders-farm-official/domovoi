package com.domovoi.app.ui.screens.music

import org.junit.Assert.assertEquals
import org.junit.Test

/** The cast menu lists real rooms only (MusicScreen's room list). */
class CastRoomsTest {
    @Test fun noRoomReportedMeansNoRoomOffered() {
        // Before 2026-10-01 an empty now-playing became listOf("kitchen"): a
        // phantom room in the cast menu and the "play in room" pickers.
        assertEquals(emptyList<String>(), castRooms(emptyList()))
    }

    @Test fun theRoomsAreTheOnesTheServerReported() {
        val rows = listOf(NowPlayingRoom("office"), NowPlayingRoom("den"), NowPlayingRoom("office"), NowPlayingRoom(""))
        assertEquals(listOf("office", "den"), castRooms(rows))
    }
}
