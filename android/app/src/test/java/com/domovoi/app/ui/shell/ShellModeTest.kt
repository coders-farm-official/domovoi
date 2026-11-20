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

    // ---- opening the app: no home pages before the server answers ----------

    @Test fun beforeTheFirstAnswerItIsTheConnectingScreenNotTheWorkspace() {
        // Away from home the workspace's pages could only spin; the owner got
        // lost in them for the whole grace period (2026-11-19).
        assertEquals(
            ShellMode.Connecting,
            shellMode(url, unreachable = false, pairingRequired = false, answeredOnce = false, looking = false),
        )
    }

    @Test fun stillUnansweredAfterTheShortWaitItIsThePhonesOwnMedia() {
        assertEquals(
            ShellMode.Local,
            shellMode(url, unreachable = false, pairingRequired = false, answeredOnce = false, looking = true),
        )
    }

    @Test fun theFirstAnswerBringsTheWorkspace() {
        assertEquals(
            ShellMode.Workspace,
            shellMode(url, unreachable = false, pairingRequired = false, answeredOnce = true, looking = false),
        )
    }

    @Test fun theOldRulesStillComeFirstBeforeTheFirstAnswer() {
        // No server, the phone chosen, a changed identity: the phone's media,
        // never the Connecting screen. A pairing refusal or a token card is
        // itself an answer: the workspace shows it.
        assertEquals(ShellMode.Local, shellMode("", unreachable = false, pairingRequired = false, answeredOnce = false))
        assertEquals(ShellMode.Local, shellMode(url, false, false, preferLocal = true, answeredOnce = false))
        assertEquals(ShellMode.Local, shellMode(url, false, false, identityChanged = true, answeredOnce = false))
        assertEquals(ShellMode.Workspace, shellMode(url, false, pairingRequired = true, answeredOnce = false))
        assertEquals(ShellMode.Workspace, shellMode(url, false, false, tokenTrouble = true, answeredOnce = false))
        assertEquals(ShellMode.Local, shellMode(url, unreachable = true, pairingRequired = false, answeredOnce = false))
    }

    @Test fun afterAnAnswerLookingCoversTheWorkspaceUntilTheGraceSays() {
        val ws = ShellMode.Workspace
        assertTrue(coversWorkspace(ws, answeredOnce = true, looking = true, unreachable = false))
        assertFalse(coversWorkspace(ws, answeredOnce = true, looking = false, unreachable = false))
        assertFalse(coversWorkspace(ShellMode.Local, answeredOnce = true, looking = true, unreachable = true))
        // Before the first answer there is no workspace to cover.
        assertFalse(coversWorkspace(ShellMode.Local, answeredOnce = false, looking = true, unreachable = false))
        assertFalse(coversWorkspace(ShellMode.Connecting, answeredOnce = false, looking = false, unreachable = false))
    }

    @Test fun lookingIsItsOwnChoiceAndCannotBePicked() {
        val c = serverChoice(url, "Hearth", unreachable = false, notOurs = false, pairingRequired = false, looking = true)
        assertEquals(ServerChoice.Looking("Hearth"), c)
        assertFalse(c.enabled)
        assertEquals("looking for it…", c.reason())
        assertEquals("Hearth", c.title())
        // The grace's verdict and the identity verdicts outrank it.
        assertEquals(
            ServerChoice.Unreachable("Hearth"),
            serverChoice(url, "Hearth", unreachable = true, notOurs = false, pairingRequired = false, looking = true),
        )
        assertEquals(
            ServerChoice.NotOurs("Hearth"),
            serverChoice(url, "Hearth", unreachable = false, notOurs = true, pairingRequired = false, looking = true),
        )
    }

    // ---- the phone's-own-media layout says where it is ---------------------

    @Test fun theLocalLayoutsTitleSaysAwayFromHome() {
        assertEquals("Away from home", ServerChoice.Unreachable("Hearth").localTitle())
        assertEquals("Looking for home…", ServerChoice.Looking("Hearth").localTitle())
        assertEquals("On this phone", ServerChoice.NoServer.localTitle())
        // "This phone" chosen with the server right there is not "away".
        assertEquals("On this phone", ServerChoice.Available("Hearth").localTitle())
        assertEquals("On this phone", ServerChoice.CoreDown("Hearth").localTitle())
    }

    @Test fun theAwayLineNamesTheHouseAndSaysTheHomePagesComeBack() {
        val away = ServerChoice.Unreachable("Hearth").awayLine()!!
        assertTrue(away, away.startsWith("You're away from home, or Hearth is off."))
        assertTrue(away, away.contains("come back by themselves when the phone reaches Hearth again"))
        assertTrue(ServerChoice.Looking("Hearth").awayLine()!!.startsWith("Looking for Hearth."))
        assertTrue(ServerChoice.CoreDown("Hearth").awayLine()!!.startsWith("Hearth is up but its core"))
        // Nothing to explain when the server is fine or there is none; the
        // changed-identity words live in IdentityChanged.kt.
        assertEquals(null, ServerChoice.Available("Hearth").awayLine())
        assertEquals(null, ServerChoice.NoServer.awayLine())
        assertEquals(null, ServerChoice.NotOurs("Hearth").awayLine())
    }
}
