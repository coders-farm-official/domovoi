package com.domovoi.app.ui.shell

import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.net.ServerIdentity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the shell shows for a server that answers but will not take this
 * phone's household token (2026-10-17): one card with pair again, the wait
 * if the server set one, and the identity warning FIRST when the server's
 * key changed too. Never "offline" and a page of raw errors.
 */
class TokenTroubleTest {
    private val url = "http://192.168.1.30:6369"

    // ---- which card -----------------------------------------------------------

    @Test fun aRefusedTokenIsTheNotRecognisedCard() {
        assertEquals(
            TokenTrouble.NotRecognised(0),
            tokenTrouble(url, paired = true, rejected = true, waitMs = 0),
        )
        assertEquals(
            TokenTrouble.NotRecognised(90_000),
            tokenTrouble(url, paired = true, rejected = true, waitMs = 90_000),
        )
    }

    @Test fun aWaitWithoutAKnownRefusalIsTheWaitingCard() {
        // The app restarted inside the server's window: the first answer is a
        // 429, and only after the wait will the token be looked at.
        assertEquals(TokenTrouble.Waiting(42_000), tokenTrouble(url, paired = true, rejected = false, waitMs = 42_000))
        assertNull("over: the workspace, which asks again", tokenTrouble(url, paired = true, rejected = false, waitMs = 0))
    }

    @Test fun anIdentityChangeComesFirstAndIsNotThisCard() {
        // The token is not even sent to a server that proved another key:
        // the person is asked about the key before anything about pairing.
        assertNull(tokenTrouble(url, paired = true, rejected = true, waitMs = 10_000, identityChanged = true))
        assertEquals(
            ShellMode.Local,
            shellMode(url, unreachable = false, pairingRequired = false, identityChanged = true, tokenTrouble = false),
        )
    }

    @Test fun noTokenNoCard() {
        assertNull(tokenTrouble(url, paired = false, rejected = true, waitMs = 10_000))
        assertNull(tokenTrouble("", paired = true, rejected = true, waitMs = 0))
    }

    @Test fun theCardKeepsTheWorkspaceUpEvenIfTheProbesFail() {
        assertEquals(
            ShellMode.Workspace,
            shellMode(url, unreachable = true, pairingRequired = false, tokenTrouble = true),
        )
        // ...but the person's own "this phone" still wins.
        assertEquals(
            ShellMode.Local,
            shellMode(url, unreachable = false, pairingRequired = false, preferLocal = true, tokenTrouble = true),
        )
    }

    // ---- what it says -----------------------------------------------------------

    @Test fun theWaitReadsAsMinutesAndSecondsRoundedUp() {
        assertEquals("0:01", waitClock(1))
        assertEquals("0:53", waitClock(52_100))
        assertEquals("4:05", waitClock(245_000))
        assertEquals("0:00", waitClock(-5))
    }

    @Test fun theCardSpeaksHouseholdNotDashboard() {
        val nr = TokenTrouble.NotRecognised(0)
        assertEquals("Domovoi doesn't recognise this phone any more", tokenTroubleTitle(nr))
        val text = tokenTroubleText(nr, "192.168.1.30:6369")
        assertTrue(text, text.contains("reinstalled"))
        assertTrue(text, text.contains("stopped sending the old one"))
        val hint = tokenTroubleHint(nr)
        assertTrue(hint, hint.contains("Settings → Phones & browsers → Household token"))
        for (copy in listOf(text, hint, tokenTroubleText(TokenTrouble.Waiting(53_000), "x"), tokenTroubleHint(TokenTrouble.Waiting(1)))) {
            assertFalse("no raw status or header names: $copy", copy.contains("429") || copy.contains("X-Device-Token"))
            assertFalse("not the dashboard's advice: $copy", copy.contains("sign in as an admin"))
        }
    }

    @Test fun aRefusedTokenIsNeverSaidToBeTriedAgain() {
        // Found on the emulator: the refused card said "trying again in 0:38",
        // and the refused token is not tried again; only a new one is.
        assertEquals("a new token can be tried in 0:38", tokenTroubleWaitLine(TokenTrouble.NotRecognised(38_000)))
        assertEquals("trying again in 0:38", tokenTroubleWaitLine(TokenTrouble.Waiting(38_000)))
        assertNull(tokenTroubleWaitLine(TokenTrouble.NotRecognised(0)))
    }

    @Test fun theCardNamesTheOldBrowserTab() {
        // The owner's case: a dashboard tab in the phone's own browser.
        for (t in listOf(TokenTrouble.NotRecognised(0), TokenTrouble.NotRecognised(5_000), TokenTrouble.Waiting(5_000))) {
            assertTrue(tokenTroubleHint(t).contains("close that tab"))
        }
        assertTrue(tokenTroubleText(TokenTrouble.Waiting(5_000), "x").contains("browser tab"))
    }

    @Test fun theWaitIsNamedOnBothCards() {
        assertTrue(tokenTroubleText(TokenTrouble.Waiting(53_000), "x").contains("0:53"))
        assertTrue(tokenTroubleHint(TokenTrouble.NotRecognised(125_000)).contains("2:05"))
        assertFalse(tokenTroubleHint(TokenTrouble.NotRecognised(0)).contains("wait"))
    }

    // ---- identity changed: the warning, and the yes -------------------------------

    private val newPin = ServerIdentity.Pin("bmV3LWtleQ==", "SHA256:new")

    @Test fun theIdentityWarningIsInHouseholdWords() {
        val t = identityChangedText("192.168.1.30:6369", canTrustAgain = true)
        assertTrue(t, t.startsWith("This looks like a different Domovoi, or it was reinstalled."))
        assertTrue(t, t.contains("sent it nothing"))
        assertTrue(t, t.contains("trust it again"))
        val old = identityChangedText("192.168.1.30:6369", canTrustAgain = false)
        assertTrue("without a proven key, the old way out", old.contains("forget it"))
    }

    @Test fun onlyAProvenKeyCanBeTrustedAgain() {
        assertEquals(
            IdentityVerdict.Mismatch("SHA256:old", "SHA256:new", newPin),
            changedIdentity(IdentityVerdict.Mismatch("SHA256:old", "SHA256:new", newPin)),
        )
        assertNull(changedIdentity(IdentityVerdict.Mismatch("SHA256:old", "SHA256:new")))
        assertNull(changedIdentity(IdentityVerdict.Unproven("SHA256:old", "offered no identity")))
        assertNull(changedIdentity(IdentityVerdict.Verified("SHA256:old", pinnedNow = false)))
        assertNull(changedIdentity(null))
    }

    @Test fun theYesShowsBothKeys() {
        val t = trustAgainText("SHA256:old", "SHA256:new")
        assertTrue(t.contains("SHA256:old") && t.contains("SHA256:new"))
        assertTrue(t.contains("Settings → About & updates"))
        assertTrue(t.contains("say no"))
    }

    @Test fun theConnectionDialogSaysItTheSameWay() {
        assertEquals(
            "looks like a different Domovoi, or it was reinstalled",
            serverChoice(url, "Domovoi", unreachable = false, notOurs = true, pairingRequired = false).reason(),
        )
    }
}
