package com.domovoi.app.ui.screens.settings

import com.domovoi.app.net.TokenCheck
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the pairing screen, Settings -> Connection and the refused-token card
 * say after a pasted token was checked with the server (2026-10-17: the
 * owner's old custom token was saved without a word on a reinstalled
 * server, and every page then failed).
 */
class TokenCheckMessageTest {
    @Test fun acceptedSaysNothingAndIsSaved() {
        assertNull(tokenCheckMessage(TokenCheck.Accepted))
        assertNull(tokenCheckMessage(null))
    }

    @Test fun refusedSaysWhereTheRightTokenIs() {
        assertEquals(
            "That token doesn't match this server. Copy it from Settings → Phones & browsers → Household token " +
                "on this server's dashboard.",
            tokenCheckMessage(TokenCheck.Refused),
        )
    }

    @Test fun throttledCountsTheWaitDown() {
        assertEquals("Too many tries — wait 0:42 before the next one.", tokenCheckMessage(TokenCheck.Throttled(42_000), 42_000))
        assertEquals("Too many tries — wait 0:01 before the next one.", tokenCheckMessage(TokenCheck.Throttled(42_000), 300))
        assertEquals("You can try again now.", tokenCheckMessage(TokenCheck.Throttled(42_000), 0))
    }

    @Test fun unavailableSaysTryAgain() {
        assertTrue(tokenCheckMessage(TokenCheck.Unavailable)!!.startsWith("Couldn't check it"))
    }
}
