package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * "This phone belongs to" is a person id, and a person id belongs to the
 * server that issued it: the setting is kept per server, by the same
 * normalised address as every other per-server answer.
 */
class OwnerPerServerTest {
    private val home = "http://192.168.1.20:6369"
    private val cabin = "http://10.0.0.5:6369"

    @Test fun eachServerKeepsItsOwnOwner() {
        var owners = ServerCredentials.withOwner(emptyMap(), home, "3")
        owners = ServerCredentials.withOwner(owners, cabin, "1")
        assertEquals("3", ServerCredentials.ownerFor(owners, home))
        assertEquals("1", ServerCredentials.ownerFor(owners, cabin))
        // Another spelling of the same address finds the same answer.
        assertEquals("3", ServerCredentials.ownerFor(owners, "$home/"))
        // A server nobody picked for has nobody.
        assertNull(ServerCredentials.ownerFor(owners, "http://10.9.9.9:6369"))
    }

    @Test fun clearingOneServerLeavesTheOthers() {
        val owners = ServerCredentials.withOwner(mapOf(ServerCredentials.normalize(home) to "3", cabin to "1"), home, null)
        assertNull(ServerCredentials.ownerFor(owners, home))
        assertEquals("1", ServerCredentials.ownerFor(owners, cabin))
        // No server, nothing stored.
        assertEquals(owners, ServerCredentials.withOwner(owners, "  ", "9"))
    }

    @Test fun theStoredMapRoundTripsAndACorruptOneIsNobody() {
        val owners = mapOf(home to "3", cabin to "1")
        assertEquals(owners, ServerCredentials.decodeOwners(ServerCredentials.encodeOwners(owners)))
        assertEquals(emptyMap<String, String>(), ServerCredentials.decodeOwners("{not json"))
        assertEquals(emptyMap<String, String>(), ServerCredentials.decodeOwners(null))
    }
}
