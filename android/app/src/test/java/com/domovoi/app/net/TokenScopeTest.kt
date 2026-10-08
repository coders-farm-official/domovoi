package com.domovoi.app.net

import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The household token goes to the active server and nowhere else: same
 * scheme, host and port, like the dashboard's own origin rule. The app's
 * own WebSocket upgrades may also reach another port on that host (the
 * drop-in socket to the core). Security round 3, A6-01 / A6-02.
 */
class TokenScopeTest {
    private val base = TokenScope.baseOf("http://192.168.0.117:6369")

    @Test fun theActiveServerItselfIsInScope() {
        assertTrue(TokenScope.admits(base, "http://192.168.0.117:6369/api/music/library".toHttpUrl(), wsUpgrade = false))
        assertTrue(TokenScope.admits(base, "http://192.168.0.117:6369/ws/state".toHttpUrl(), wsUpgrade = true))
    }

    @Test fun anotherHostIsNotWhateverThePortOrScheme() {
        assertFalse(TokenScope.admits(base, "http://192.168.0.118:6369/api/health".toHttpUrl(), wsUpgrade = false))
        assertFalse(TokenScope.admits(base, "http://127.0.0.1:4444/grab.mp3".toHttpUrl(), wsUpgrade = false))
        assertFalse(TokenScope.admits(base, "https://attacker.example/grab.mp3".toHttpUrl(), wsUpgrade = false))
        assertFalse("an upgrade is still host-bound", TokenScope.admits(base, "http://192.168.0.118:6370/v1/dropin/x".toHttpUrl(), wsUpgrade = true))
    }

    @Test fun anotherPortOnTheSameHostOnlyForTheAppsOwnUpgrades() {
        val core = "http://192.168.0.117:6370/v1/dropin/kitchen".toHttpUrl()
        assertFalse("a plain request to another port carries nothing", TokenScope.admits(base, core, wsUpgrade = false))
        assertTrue("the drop-in socket to the core's port does", TokenScope.admits(base, core, wsUpgrade = true))
    }

    @Test fun theSchemeHasToMatchToo() {
        assertFalse(TokenScope.admits(base, "https://192.168.0.117:6369/api/health".toHttpUrl(), wsUpgrade = false))
        val tls = TokenScope.baseOf("https://domovoi.lan:6369")
        assertFalse("an https server never gets its token in the clear", TokenScope.admits(tls, "http://domovoi.lan:6369/api/health".toHttpUrl(), wsUpgrade = false))
        assertTrue(TokenScope.admits(tls, "https://domovoi.lan:6369/api/health".toHttpUrl(), wsUpgrade = false))
    }

    @Test fun hostsCompareCaseInsensitivelyAndPortsResolveTheirDefaults() {
        val named = TokenScope.baseOf("http://Domovoi.LAN:6369/")
        assertTrue(TokenScope.admits(named, "http://domovoi.lan:6369/api/x".toHttpUrl(), wsUpgrade = false))
        val defaultPort = TokenScope.baseOf("https://domovoi.lan")
        assertTrue(TokenScope.admits(defaultPort, "https://domovoi.lan:443/api/x".toHttpUrl(), wsUpgrade = false))
        assertFalse(TokenScope.admits(defaultPort, "https://domovoi.lan:6369/api/x".toHttpUrl(), wsUpgrade = false))
    }

    @Test fun noServerMeansNoScopeAtAll() {
        assertNull(TokenScope.baseOf(""))
        assertNull(TokenScope.baseOf(null))
        assertNull(TokenScope.baseOf("not a url"))
        assertFalse(TokenScope.admits(null, "http://192.168.0.117:6369/api/x".toHttpUrl(), wsUpgrade = false))
        assertFalse(TokenScope.admits(null, "http://192.168.0.117:6369/api/x".toHttpUrl(), wsUpgrade = true))
    }

    @Test fun aSocketAddressIsReadAsItsHttpForm() {
        assertEquals("http", TokenScope.baseOf("ws://192.168.0.117:6369")?.scheme)
        assertEquals("https", TokenScope.baseOf("wss://domovoi.lan:6369")?.scheme)
        assertEquals(6369, TokenScope.baseOf("http://192.168.0.117:6369/")?.port)
    }
}
