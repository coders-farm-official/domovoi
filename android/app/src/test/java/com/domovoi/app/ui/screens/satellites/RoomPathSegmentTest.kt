package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.player.roomActionPath
import com.domovoi.app.roommode.RoomProtocol
import com.domovoi.app.ui.screens.music.encComponent
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * A room id goes into a URL path as ONE escaped segment: a `/`, `?`, `#`,
 * space or `%` in it must not change which URL the phone asks for.
 */
class RoomPathSegmentTest {
    private val odd = "a/b?c d%e#f"
    private val escaped = "a%2Fb%3Fc%20d%25e%23f"

    @Test fun theHelperEscapesEverythingAPathSegmentCannotCarry() {
        assertEquals(escaped, encComponent(odd))
        assertEquals("kitchen", encComponent("kitchen"))
        assertEquals("living_room", encComponent("living_room"))
    }

    @Test fun theSatellitePathsEscapeTheRoom() {
        assertEquals("/api/satellites/kitchen/health", satPath("kitchen", "/health"))
        assertEquals("/api/satellites/$escaped/sessions?limit=100", satPath(odd, "/sessions?limit=100"))
        assertEquals("/api/satellites/$escaped/quiet-hours", quietPath(odd))
        assertEquals("/api/satellites/$escaped/timer-announcements", timerScopePath(odd))
    }

    @Test fun theMusicTransportPathEscapesTheRoom() {
        assertEquals("/api/music/skip/office", roomActionPath("skip", "office"))
        assertEquals("/api/music/pause/$escaped", roomActionPath("pause", odd))
    }

    @Test fun theRoomStreamEscapesTheRoom() {
        assertEquals("ws://192.168.1.30:6370/v1/stream/$escaped", RoomProtocol.streamUrl("http://192.168.1.30:6370", odd))
    }
}
