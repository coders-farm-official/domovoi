package com.domovoi.app.net

import com.domovoi.app.net.NetworkWatch.Source.DEFAULT
import com.domovoi.app.net.NetworkWatch.Source.PHYSICAL
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the identity gate keys a verdict on: not the default Network
 * object alone, but a fingerprint of every network the phone could reach
 * the server over (security round 3, A6-03 — the review's blocker). A VPN
 * that stays up while the Wi-Fi under it changes, a roam that keeps the
 * network and changes the addresses, a change of transports: each is a
 * change. The same facts again are not, and the first picture is not.
 */
class NetworkWatchTest {
    private var changes = 0
    private val log = mutableListOf<String>()
    private val watch = NetworkWatch(onChange = { changes++ }, log = { log += it })

    private fun wifi(address: String, gateway: String = "192.168.1.1", iface: String = "wlan0") =
        { f: NetworkWatch.Facts ->
            f.copy(transports = setOf("wifi"), iface = iface, addresses = setOf(address), gateways = setOf(gateway), dns = setOf(gateway))
        }

    private fun vpn() = { f: NetworkWatch.Facts ->
        f.copy(transports = setOf("vpn"), iface = "tun0", addresses = setOf("100.64.0.5/32"))
    }

    @Test fun theFirstPictureIsNotAChange() {
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        assertNotEquals(NetworkWatch.NONE, watch.fingerprint)
        assertEquals(0, changes)
        assertEquals(0, watch.changeCount)
    }

    @Test fun theSameFactsReportedAgainAreNotAChange() {
        // onAvailable reads the whole picture; onCapabilitiesChanged and
        // onLinkPropertiesChanged then repeat parts of it, and the lot
        // comes again at the next callback round.
        repeat(2) {
            watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
            watch.seen(DEFAULT, "101") { it.copy(transports = setOf("wifi")) }
            watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        }
        assertEquals(0, changes)
    }

    @Test fun learningMoreAboutTheSameNetworkCountsAsAChangeTheSafeWay() {
        // A network reported before DHCP finished, then with its addresses:
        // a different picture, so a proof taken on the first is not reused.
        watch.seen(DEFAULT, "101") { it.copy(transports = setOf("wifi")) }
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        assertEquals(1, changes)
    }

    @Test fun theNetworksUpAtStartAreOnePictureNotAChangeEach() {
        watch.batch {
            seen(DEFAULT, "120", vpn())
            seen(PHYSICAL, "101", wifi("192.168.1.57/24"))
        }
        assertEquals(0, changes)
        assertTrue(watch.fingerprint.contains("D|120"))
        assertTrue(watch.fingerprint.contains("P|101"))
    }

    @Test fun theWifiUnderAVpnChangingIsAChangeThoughTheDefaultNetworkIsNot() {
        // Tailscale, WireGuard, an ad blocker: the VPN is the default
        // network and its object never changes.
        watch.batch {
            seen(DEFAULT, "120", vpn())
            seen(PHYSICAL, "101", wifi("192.168.1.57/24"))
        }
        val home = watch.fingerprint
        assertEquals(0, changes)

        // The phone joins a hostile hotspot that hands out the same private
        // range: a new Wi-Fi network under the same VPN.
        watch.lost(PHYSICAL, "101")
        watch.seen(PHYSICAL, "102", wifi("192.168.1.57/24"))

        assertNotEquals(home, watch.fingerprint)
        assertEquals(2, changes)
        assertTrue(log.any { it.startsWith("network changed") })
    }

    @Test fun aRoamThatKeepsTheNetworkButChangesTheAddressesIsAChange() {
        // A twin access point with the home SSID and key keeps the Network
        // object; its DHCP hands out a different lease.
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        watch.seen(DEFAULT, "101", wifi("192.168.1.23/24", gateway = "192.168.1.254"))
        assertEquals(1, changes)
    }

    @Test fun aChangeOfTransportsOrWifiNameIsAChange() {
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        watch.seen(DEFAULT, "101") { it.copy(wifi = "\"home\"/aa:bb:cc:dd:ee:ff") }
        assertEquals(1, changes)
        watch.seen(DEFAULT, "101") { it.copy(wifi = "\"home\"/aa:bb:cc:dd:ee:ff") }
        assertEquals("the same name again is not a change", 1, changes)
        watch.seen(DEFAULT, "101") { it.copy(transports = setOf("wifi", "vpn")) }
        assertEquals(2, changes)
    }

    @Test fun aNewDefaultReplacesTheOldOneEvenWithoutAnOnLost() {
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        watch.seen(DEFAULT, "103") { it.copy(transports = setOf("cellular"), iface = "rmnet0", addresses = setOf("10.20.30.40/32")) }
        assertEquals(1, changes)
        assertFalse("one default at a time", watch.fingerprint.contains("D|101"))
        assertTrue(watch.fingerprint.contains("D|103"))
    }

    @Test fun theDefaultAndPhysicalViewsOfOneNetworkAreKeptApart() {
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        watch.seen(PHYSICAL, "101", wifi("192.168.1.57/24"))
        watch.lost(DEFAULT, "101")
        assertTrue("the physical callback still sees it", watch.fingerprint.contains("P|101"))
        assertFalse(watch.fingerprint.contains("D|101"))
    }

    @Test fun losingEveryNetworkAndFindingOneLaterIsAChangeTwice() {
        watch.seen(DEFAULT, "101", wifi("192.168.1.57/24"))
        watch.lost(DEFAULT, "101")
        assertEquals(NetworkWatch.NONE, watch.fingerprint)
        assertEquals(1, changes)
        watch.seen(DEFAULT, "102", wifi("192.168.1.57/24"))
        assertEquals("not the first picture any more", 2, changes)
    }

    @Test fun theFingerprintDoesNotDependOnTheOrderTheCallbacksCame() {
        val other = NetworkWatch()
        watch.batch {
            seen(DEFAULT, "120", vpn())
            seen(PHYSICAL, "101", wifi("192.168.1.57/24"))
        }
        other.seen(PHYSICAL, "101") { it.copy(addresses = setOf("192.168.1.57/24"), dns = setOf("192.168.1.1")) }
        other.seen(PHYSICAL, "101") { it.copy(transports = setOf("wifi"), iface = "wlan0", gateways = setOf("192.168.1.1")) }
        other.seen(DEFAULT, "120", vpn())
        assertEquals(watch.fingerprint, other.fingerprint)
    }
}
