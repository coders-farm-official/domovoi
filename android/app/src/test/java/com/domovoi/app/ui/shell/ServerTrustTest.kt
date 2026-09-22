package com.domovoi.app.ui.shell

import com.domovoi.app.data.ServerCredentials
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * First connect to a server the app has never used shows its address and
 * waits for a yes: until then no preference is written, no capability or
 * plugin route is loaded and no request is sent (security batch B1, FE-2).
 */
class ServerTrustTest {

    /** Records everything the gate would DO, so a test can assert that a
     *  refused server produced none of it. */
    private class Recorder {
        val trusted = linkedSetOf<String>()
        val connected = mutableListOf<Pair<String, String?>>()
        fun gate() = ServerConnectGate(
            isTrusted = { ServerCredentials.isTrusted(trusted, it) },
            onTrust = { trusted += ServerCredentials.normalize(it) },
            onConnect = { url, name -> connected += url to name },
        )
    }

    @Test fun anUnknownServerIsShownAndNothingHappensUntilItIsConfirmed() {
        val r = Recorder()
        val gate = r.gate()

        assertFalse(gate.select("http://10.0.0.42:6369/", "kitchen-box"))

        // What the user is shown: the address, not just the friendly name.
        val pending = gate.pending.value
        assertEquals("http://10.0.0.42:6369", pending?.url)
        assertEquals("10.0.0.42:6369", pending?.address)
        assertEquals("kitchen-box", pending?.name)
        // ...and nothing else has happened.
        assertTrue("no server was connected", r.connected.isEmpty())
        assertTrue("nothing was trusted", r.trusted.isEmpty())
    }

    @Test fun cancellingLeavesTheAppExactlyAsItWas() {
        val r = Recorder()
        val gate = r.gate()
        gate.select("http://10.0.0.42:6369", "kitchen-box")

        gate.cancel()

        assertNull(gate.pending.value)
        assertTrue(r.connected.isEmpty())
        assertTrue(r.trusted.isEmpty())
        assertFalse("cancel does not connect", gate.confirm())
        assertTrue(r.connected.isEmpty())
    }

    @Test fun confirmingTrustsTheServerAndConnectsOnce() {
        val r = Recorder()
        val gate = r.gate()
        gate.select("http://10.0.0.42:6369", "kitchen-box")

        assertTrue(gate.confirm())

        assertEquals(listOf("http://10.0.0.42:6369"), r.trusted.toList())
        assertEquals(listOf("http://10.0.0.42:6369" to "kitchen-box"), r.connected)
        assertNull(gate.pending.value)
        // The prompt is gone, so a second confirm cannot connect again.
        assertFalse(gate.confirm())
        assertEquals(1, r.connected.size)
    }

    @Test fun anAlreadyTrustedServerConnectsWithoutAsking() {
        val r = Recorder()
        r.trusted += "http://10.0.0.42:6369"
        val gate = r.gate()

        assertTrue(gate.select("http://10.0.0.42:6369", "kitchen-box"))

        assertNull("no prompt for a server already trusted", gate.pending.value)
        assertEquals(listOf("http://10.0.0.42:6369" to "kitchen-box"), r.connected)
    }

    @Test fun trustIsPerServerNotGlobal() {
        val r = Recorder()
        val gate = r.gate()
        gate.select("http://10.0.0.42:6369", null)
        gate.confirm()

        assertFalse("a second server is asked about separately", gate.select("http://10.0.0.43:6369", null))
        assertEquals("10.0.0.43:6369", gate.pending.value?.address)
        assertEquals(1, r.connected.size)
    }

    @Test fun aBlankAddressIsNeverPromptedFor() {
        val r = Recorder()
        val gate = r.gate()
        assertFalse(gate.select("   ", null))
        assertNull(gate.pending.value)
        assertTrue(r.connected.isEmpty())
    }

    @Test fun theSameServerWithATrailingSlashIsTheSameServer() {
        val r = Recorder()
        val gate = r.gate()
        gate.select("http://10.0.0.42:6369", null)
        gate.confirm()

        assertTrue(gate.select("http://10.0.0.42:6369/", null))
        assertNull(gate.pending.value)
        assertEquals(2, r.connected.size)
        assertEquals(1, r.trusted.size)
    }
}
