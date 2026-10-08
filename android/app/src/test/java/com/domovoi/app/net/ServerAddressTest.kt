package com.domovoi.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * One rule for a typed server address, shared by the picker and Settings →
 * Connection: default scheme and port, and the cleartext check before
 * anything is probed or saved (security round 3, A6-05 / P2-at-01).
 */
class ServerAddressTest {

    @Test fun aBareHostGetsHttpAndTheDashboardPort() {
        assertEquals(ServerAddress.Result.Ok("http://192.168.1.30:6369"), ServerAddress.fromTyped("192.168.1.30"))
        assertEquals(ServerAddress.Result.Ok("http://192.168.1.30:6390"), ServerAddress.fromTyped(" 192.168.1.30:6390/ "))
        assertEquals(ServerAddress.Result.Ok("http://domovoi.lan:6369"), ServerAddress.fromTyped("domovoi.lan"))
        assertEquals(ServerAddress.Result.Ok("https://domovoi.example.net:6369"), ServerAddress.fromTyped("https://domovoi.example.net"))
    }

    @Test fun aPlainHttpAddressOutsideTheHomeNetworkIsRefusedWithTheReason() {
        assertEquals(
            ServerAddress.Result.Refused(CleartextPolicy.refusalMessage("203.0.113.5")),
            ServerAddress.fromTyped("http://203.0.113.5:6369"),
        )
        assertEquals(
            ServerAddress.Result.Refused(CleartextPolicy.refusalMessage("domovoi.example.net")),
            ServerAddress.fromTyped("domovoi.example.net"),
        )
    }

    @Test fun nonsenseIsRefusedAndBlankIsNothing() {
        assertEquals(ServerAddress.Result.Refused("that is not a server address"), ServerAddress.fromTyped("http://:::"))
        assertNull(ServerAddress.fromTyped("   "))
        assertNull(ServerAddress.fromTyped(""))
    }
}
