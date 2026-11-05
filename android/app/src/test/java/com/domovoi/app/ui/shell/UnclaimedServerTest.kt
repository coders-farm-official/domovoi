package com.domovoi.app.ui.shell

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The app on a Domovoi nobody has claimed yet (owner, 2026-11-04): one
 * screen that says to claim it on the dashboard, instead of a workspace for
 * a house with no admin. Only an explicit `setup_complete: false` from the
 * open status read shows it; anything doubtful leaves the house alone.
 */
class UnclaimedServerTest {
    private val url = "http://192.168.1.20:6369"

    private fun status(text: String) = Json.parseToJsonElement(text)

    @Test fun theStatusReadSaysWhetherSetupIsDone() {
        assertEquals(false, setupCompleteOf(status("""{"setup_complete": false, "authenticated": false}""")))
        assertEquals(true, setupCompleteOf(status("""{"setup_complete": true, "authenticated": true}""")))
    }

    @Test fun aStatusThatDoesNotSayIsNotAnAnswer() {
        assertNull(setupCompleteOf(null))
        assertNull(setupCompleteOf(status("""{"authenticated": false}""")))
        assertNull(setupCompleteOf(status("""{"setup_complete": "no"}""")))
        assertNull(setupCompleteOf(status("""[]""")))
        assertNull(setupCompleteOf(status("""{"setup_complete": null}""")))
    }

    @Test fun onlyAnExplicitFalseOnAConfiguredServerBlocks() {
        assertTrue(showsUnclaimed(url, false))
        assertFalse("claimed", showsUnclaimed(url, true))
        assertFalse("not read yet, or the read failed", showsUnclaimed(url, null))
        assertFalse("no server: the local shell", showsUnclaimed("", false))
        assertFalse(showsUnclaimed("   ", false))
    }

    @Test fun theWordsSayWhatToDoAndWhere() {
        assertEquals("This Domovoi isn't set up yet", UNCLAIMED_TITLE)
        val text = unclaimedText("192.168.1.20:6369")
        assertTrue(text.startsWith("Open its dashboard in a web browser and claim it, then come back."))
        assertTrue(text.contains("192.168.1.20:6369"))
        assertTrue(text.contains("setup code"))
        // The household token is not asked for here: before the claim
        // there is none to give out.
        assertFalse(text.contains("household token"))
    }

    @Test fun onlyTheButtonsOwnReadSaysStillNotSetUp() {
        assertTrue(saysStillUnclaimed(fromButton = true, read = false))
        assertFalse("claimed now: the screen just goes", saysStillUnclaimed(fromButton = true, read = true))
        assertFalse("the read failed: nothing to say", saysStillUnclaimed(fromButton = true, read = null))
        assertFalse("a resume or socket change", saysStillUnclaimed(fromButton = false, read = false))
    }

    @Test fun anUnclaimedServerIsStillTheWorkspaceSideSoItsScreenShows() {
        // The screen renders in place of the workspace (ShellContent), so a
        // reachable unclaimed server must not drop to local media...
        assertEquals(ShellMode.Workspace, shellMode(url, unreachable = false, pairingRequired = false))
        // ...while "use this phone's media" (preferLocal) still wins.
        assertEquals(ShellMode.Local, shellMode(url, unreachable = false, pairingRequired = false, preferLocal = true))
    }
}
