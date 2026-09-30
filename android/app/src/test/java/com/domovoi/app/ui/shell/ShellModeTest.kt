package com.domovoi.app.ui.shell

import org.junit.Assert.assertEquals
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
}
