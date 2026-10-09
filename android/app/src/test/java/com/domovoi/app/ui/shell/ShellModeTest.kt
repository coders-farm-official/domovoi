package com.domovoi.app.ui.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShellModeTest {

    private val url = "http://192.168.1.20:6370"

    @Test fun noServerIsLocalMedia() {
        assertEquals(ShellMode.Local, shellMode("", unreachable = false, pairingRequired = false))
        assertEquals(ShellMode.Local, shellMode("", unreachable = true, pairingRequired = true))
    }

    @Test fun reachableServerIsTheWorkspace() {
        assertEquals(ShellMode.Workspace, shellMode(url, unreachable = false, pairingRequired = false))
    }

    @Test fun unreachableServerFallsBackToLocalMedia() {
        // The phone left the house: a saved server must not strand the app
        // on panels that can only spin.
        assertEquals(ShellMode.Local, shellMode(url, unreachable = true, pairingRequired = false))
    }

    @Test fun pairingRefusalKeepsTheWorkspace() {
        // The server answered — it wants the household token, so the
        // workspace stays up to show the pairing screen.
        assertEquals(ShellMode.Workspace, shellMode(url, unreachable = true, pairingRequired = true))
    }

    // ---- the connection dialog's "this phone" choice -----------------------

    @Test fun choosingThePhoneHoldsEvenWithTheServerAnswering() {
        assertEquals(ShellMode.Local, shellMode(url, unreachable = false, pairingRequired = false, preferLocal = true))
    }

    @Test fun choosingThePhoneBeatsAPairingRefusal() {
        // The pairing screen is a workspace screen; a person who chose the
        // phone has not asked to see it.
        assertEquals(ShellMode.Local, shellMode(url, unreachable = false, pairingRequired = true, preferLocal = true))
    }

    @Test fun choosingTheServerKeepsTheAutomaticFallback() {
        assertEquals(ShellMode.Workspace, shellMode(url, unreachable = false, pairingRequired = false, preferLocal = false))
        assertEquals(ShellMode.Local, shellMode(url, unreachable = true, pairingRequired = false, preferLocal = false))
    }

    // ---- serverChoice: when the server side can be picked, and why not -----

    private fun choice(
        serverUrl: String = url,
        unreachable: Boolean = false,
        notOurs: Boolean = false,
        pairingRequired: Boolean = false,
        coreDown: Boolean = false,
    ) = serverChoice(serverUrl, "Domovoi", unreachable, notOurs, pairingRequired, coreDown)

    @Test fun noSavedServerCannotBePicked() {
        val c = choice(serverUrl = "")
        assertEquals(ServerChoice.NoServer, c)
        assertFalse(c.enabled)
        assertEquals("no server yet · pick one below", c.reason())
    }

    @Test fun anUnreachableServerIsGreyedWithItsReason() {
        val c = choice(unreachable = true)
        assertEquals(ServerChoice.Unreachable("Domovoi"), c)
        assertFalse(c.enabled)
        assertEquals("can't reach it right now", c.reason())
    }

    @Test fun aServerThatFailedTheIdentityProofIsGreyedEvenIfItAnswers() {
        // Identity beats reachability: something answering at the address is
        // exactly the case the proof exists for.
        val c = choice(unreachable = false, notOurs = true)
        assertEquals(ServerChoice.NotOurs("Domovoi"), c)
        assertFalse(c.enabled)
    }

    @Test fun anAnsweringServerCanBePicked() {
        val c = choice()
        assertTrue(c.enabled)
        assertEquals("available", c.reason())
        assertEquals("Domovoi", c.title())
    }

    @Test fun aServerWantingTheTokenCanStillBePickedAndSaysSo() {
        val c = choice(pairingRequired = true)
        assertTrue(c.enabled)
        assertEquals("available · needs the household token", c.reason())
    }

    // ---- the core-down verdict (A6-03 review): held, not an impostor ------

    @Test fun aServerWhoseCoreIsDownIsGreyedWithItsOwnReasonNotUnreachable() {
        // The web backend answered, so "can't reach it" would be wrong; the
        // token is held until the core proves the identity, so it cannot be
        // picked either. Named before unreachable: with the token held every
        // request fails and the shell's probe reads the server as unreachable.
        val c = choice(unreachable = true, coreDown = true)
        assertEquals(ServerChoice.CoreDown("Domovoi"), c)
        assertFalse(c.enabled)
        assertEquals("up, but its core isn't answering yet", c.reason())
        assertEquals("Domovoi", c.title())
    }

    @Test fun anIdentityFailureBeatsACoreDownReading() {
        // Both cannot be true of one verdict, but if the inputs disagree the
        // impostor reading wins: it is the one that must never be softened.
        val c = choice(notOurs = true, coreDown = true)
        assertEquals(ServerChoice.NotOurs("Domovoi"), c)
    }

    @Test fun coreDownDefaultsToFalseForEveryOlderCaller() {
        assertTrue(choice().enabled)
    }
}
