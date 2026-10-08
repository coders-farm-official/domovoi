package com.domovoi.app.ui.shell

import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.net.ServerIdentity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Forgetting the server in use (security round 3 review, P2-at-01): what
 * the confirmation says, and which fingerprint it shows next to the
 * pinned one — the key the server proves now, so a legitimate identity
 * change can be told from an impostor before anything is forgotten.
 */
class ForgetServerTest {

    private val url = "http://10.0.0.42:6369"
    private fun status(verdict: IdentityVerdict) = IdentityGate.Status("http://10.0.0.42:6369", verdict)

    @Test fun theProvenFingerprintIsTheKeyTheServerHoldsNowWhateverTheVerdict() {
        assertEquals("SHA256:new", provenFingerprint(status(IdentityVerdict.Mismatch("SHA256:old", "SHA256:new")), url))
        assertEquals("SHA256:same", provenFingerprint(status(IdentityVerdict.Verified("SHA256:same", pinnedNow = false)), url))
        assertEquals(
            "SHA256:offered",
            provenFingerprint(status(IdentityVerdict.Legacy(ServerIdentity.Pin("a2V5", "SHA256:offered"))), url),
        )
        assertNull(provenFingerprint(status(IdentityVerdict.Legacy()), url))
        assertNull(provenFingerprint(status(IdentityVerdict.Unproven("SHA256:old", "offered no identity")), url))
        assertNull(provenFingerprint(status(IdentityVerdict.Unavailable("SHA256:old", "core down")), url))
        assertNull("another server's verdict is not this one's", provenFingerprint(status(IdentityVerdict.Verified("SHA256:x", false)), "http://10.0.0.43:6369"))
        assertNull(provenFingerprint(null, url))
    }

    @Test fun theConfirmationShowsBothKeysWhenTheyDiffer() {
        val text = forgetActiveServerText("10.0.0.42:6369", "SHA256:old", "SHA256:new")
        assertTrue(text.contains("forget 10.0.0.42:6369"))
        assertTrue(text.contains("household token"))
        assertTrue(text.contains("pinned: SHA256:old"))
        assertTrue(text.contains("it now proves: SHA256:new"))
        assertTrue(text.contains("back at the server list"))
    }

    @Test fun theConfirmationSaysWhenForgettingIsNotNeededForIdentitysSake() {
        val same = forgetActiveServerText("10.0.0.42:6369", "SHA256:same", "SHA256:same")
        assertTrue(same.contains("still proves that key"))
        assertFalse(same.contains("now proves"))
        val unproved = forgetActiveServerText("10.0.0.42:6369", "SHA256:old", null)
        assertTrue(unproved.contains("not proved on this network yet"))
        val nothing = forgetActiveServerText("10.0.0.42:6369", null, null)
        assertFalse(nothing.contains("pinned:"))
        assertFalse(nothing.contains("proves"))
    }
}
