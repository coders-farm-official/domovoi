package com.domovoi.app.player

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The toast for something in the queue that would not play
 * ([PlayerController.playFailures]). Before 2026-11-19 a song from home
 * failed away from home with nothing on screen at all.
 */
class PlayFailureTextTest {

    @Test fun aSongFromHomeAwayFromHomeSaysWhy() {
        assertEquals(
            "Couldn't play “Holocene”: it's at home, and plays again when the phone reaches home.",
            playFailureText(PlayFailure("Holocene", fromServer = true), homeOutOfReach = true),
        )
    }

    @Test fun atHomeItDoesNotBlameTheDistance() {
        // In reach, the server is not why: no guessing about the reason.
        assertEquals(
            "Couldn't play “Holocene”.",
            playFailureText(PlayFailure("Holocene", fromServer = true), homeOutOfReach = false),
        )
    }

    @Test fun thePhonesOwnSongIsNeverAboutHome() {
        assertEquals(
            "Couldn't play “Voice memo”.",
            playFailureText(PlayFailure("Voice memo", fromServer = false), homeOutOfReach = true),
        )
    }

    @Test fun noTitleSaysThat() {
        assertEquals("Couldn't play that.", playFailureText(PlayFailure(null, fromServer = false), homeOutOfReach = false))
        assertEquals("Couldn't play that.", playFailureText(PlayFailure("  ", fromServer = false), homeOutOfReach = false))
    }
}
