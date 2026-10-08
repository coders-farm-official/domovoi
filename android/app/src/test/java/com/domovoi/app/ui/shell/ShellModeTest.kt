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
    ) = serverChoice(serverUrl, "Domovoi", unreachable, notOurs, pairingRequired)

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
}
