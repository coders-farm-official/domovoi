package com.domovoi.app.ui.screens.settings

import com.domovoi.app.data.ServerCredentials
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Trust is visible and revocable: Settings → Connection lists every server
 * the phone trusts (and the one it is on), and a switch forgets trust that
 * belongs to nothing — so an address trusted once can never be reused
 * silently (security round 3, P2-at-01).
 */
class TrustedServersTest {

    @Test fun everyTrustedServerIsListedTheActiveOneFirst() {
        val rows = trustedServersRows(
            trusted = setOf("http://10.0.2.2:6390", "http://203.0.113.5:6369"),
            known = mapOf("http://10.0.2.2:6390" to "Domovoi"),
            active = "http://10.0.2.2:6390",
        )
        assertEquals(
            listOf(
                TrustedServerRow("http://10.0.2.2:6390", "Domovoi", active = true),
                TrustedServerRow("http://203.0.113.5:6369", null, active = false),
            ),
            rows,
        )
    }

    @Test fun theActiveServerIsListedEvenWhenNothingElseKnowsIt() {
        // A harness that wrote server_url and trusted_servers straight into
        // the DataStore, or a pre-trust-list install.
        val rows = trustedServersRows(trusted = emptySet(), known = emptyMap(), active = "http://10.0.2.2:6390/")
        assertEquals(listOf(TrustedServerRow("http://10.0.2.2:6390", null, active = true)), rows)
    }

    @Test fun aKnownServerIsListedEvenIfTheTrustListLostIt() {
        val rows = trustedServersRows(
            trusted = emptySet(),
            known = mapOf("http://10.0.0.43:6369" to "den"),
            active = "",
        )
        assertEquals(listOf(TrustedServerRow("http://10.0.0.43:6369", "den", active = false)), rows)
    }

    @Test fun noServerAtAllIsAnEmptyList() {
        assertTrue(trustedServersRows(emptySet(), emptyMap(), "").isEmpty())
    }

    // ---- what a switch forgets -----------------------------------------------

    @Test fun trustThatBelongsToNoKnownServerIsAnOrphanAfterASwitch() {
        // The P2-at-01 observation: the panel trusted 203.0.113.5 without
        // listing it; switching back to the ft server leaves it an orphan.
        val orphans = ServerCredentials.orphanTrust(
            trusted = setOf("http://10.0.2.2:6390", "http://203.0.113.5:6369"),
            known = listOf("http://10.0.2.2:6390"),
            active = "http://10.0.2.2:6390",
        )
        assertEquals(setOf("http://203.0.113.5:6369"), orphans)
    }

    @Test fun theActiveServerAndEveryKnownOneAreKept() {
        val orphans = ServerCredentials.orphanTrust(
            trusted = setOf("http://a:6369", "http://b:6369/", "http://c:6369"),
            known = listOf("http://b:6369"),
            active = "http://c:6369/",
        )
        assertEquals(setOf("http://a:6369"), orphans)
        assertTrue(
            ServerCredentials.orphanTrust(setOf("http://a:6369"), listOf("http://a:6369"), "").isEmpty(),
        )
    }
}
