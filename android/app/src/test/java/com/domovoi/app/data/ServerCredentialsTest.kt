package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What this phone remembers about a server: whether the user trusted it, and
 * the household device token it issued. Both are per server, because two
 * Domovois are two households (security batch B1).
 */
class ServerCredentialsTest {

    @Test fun addressIsWhatAPersonCanCheckAgainstTheBox() {
        assertEquals("10.0.0.42:6369", ServerCredentials.address("http://10.0.0.42:6369/"))
        assertEquals("domovoi.lan:6369", ServerCredentials.address(" https://domovoi.lan:6369 "))
    }

    @Test fun trustIsPerServerAndSurvivesATrailingSlash() {
        val trusted = ServerCredentials.withTrusted(emptySet(), "http://10.0.0.42:6369/")
        assertTrue(ServerCredentials.isTrusted(trusted, "http://10.0.0.42:6369"))
        assertTrue(ServerCredentials.isTrusted(trusted, "http://10.0.0.42:6369/"))
        assertFalse(ServerCredentials.isTrusted(trusted, "http://10.0.0.43:6369"))
        assertFalse("an empty url is never trusted", ServerCredentials.isTrusted(trusted, ""))
        assertFalse(ServerCredentials.isTrusted(ServerCredentials.withoutTrusted(trusted, "http://10.0.0.42:6369"),
                                                "http://10.0.0.42:6369"))
    }

    @Test fun eachServerHasItsOwnToken() {
        var tokens = ServerCredentials.withToken(emptyMap(), "http://a:6369", "token-a")
        tokens = ServerCredentials.withToken(tokens, "http://b:6369/", "token-b")

        assertEquals("token-a", ServerCredentials.tokenFor(tokens, "http://a:6369/"))
        assertEquals("token-b", ServerCredentials.tokenFor(tokens, "http://b:6369"))
        assertNull(ServerCredentials.tokenFor(tokens, "http://c:6369"))
    }

    @Test fun unpairingRemovesTheToken() {
        val tokens = ServerCredentials.withToken(emptyMap(), "http://a:6369", "token-a")
        assertNull(ServerCredentials.tokenFor(ServerCredentials.withToken(tokens, "http://a:6369", null), "http://a:6369"))
        assertNull(ServerCredentials.tokenFor(ServerCredentials.withToken(tokens, "http://a:6369", "   "), "http://a:6369"))
    }

    @Test fun tokensSurviveARoundTripAndACorruptBlobIsNotFatal() {
        val tokens = ServerCredentials.withToken(emptyMap(), "http://a:6369", "token-a")
        assertEquals(tokens, ServerCredentials.decodeTokens(ServerCredentials.encodeTokens(tokens)))
        assertEquals(emptyMap<String, String>(), ServerCredentials.decodeTokens("not json"))
        assertEquals(emptyMap<String, String>(), ServerCredentials.decodeTokens(null))
    }

    @Test fun trustedListSurvivesARoundTripAndACorruptBlobIsNotFatal() {
        val trusted = setOf("http://a:6369", "http://b:6369")
        assertEquals(trusted, ServerCredentials.decodeTrusted(ServerCredentials.encodeTrusted(trusted)))
        assertEquals(emptySet<String>(), ServerCredentials.decodeTrusted("{"))
        assertEquals(emptySet<String>(), ServerCredentials.decodeTrusted(null))
    }
}
