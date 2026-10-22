package com.domovoi.app.assistant.lookup

import kotlinx.coroutines.runBlocking
import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.UnknownServiceException

/**
 * The person's own SearXNG address on the phone: https anywhere, plain
 * http only to a home-network address (CleartextPolicy, the rule the
 * server address follows too, and the server's domovoi/home_address.py).
 * Before this, any http:// address was taken, and the lookup client
 * skipped CleartextPolicy, so a public plain-http address would have
 * carried the search words in the clear. No network: a refused address is
 * refused before a socket is opened.
 */
class OwnSearchAddressTest {

    private val home = listOf(
        "http://192.168.1.20:8888", "http://10.0.0.5", "http://172.16.4.2:8080/", "http://169.254.10.10",
        "http://searx.local:8888", "http://nas.home.arpa", "http://box.lan", "http://[fd00::1]:8888",
        "http://[fe80::1]:8888", "http://localhost:8888", "http://127.0.0.1:8888", "http://[::1]:8888",
        "http://100.100.1.2",
    )
    private val public = listOf(
        "http://8.8.8.8", "http://8.8.8.8:8888", "http://203.0.113.5:8888", "http://search.example.com",
        "http://searx.be/search", "http://172.32.0.1", "http://192.169.0.1", "http://[2001:db8::1]",
        "http://evil.local.example.com", "http://nas",
    )
    private val https = listOf(
        "https://searx.be", "https://8.8.8.8", "https://search.example.com:8443/searxng", "https://192.168.1.20:8888",
    )

    @Test fun plainHttpOnlyOnTheHomeNetwork() {
        for (u in home) assertNull(u, ownSearchAddressProblem(u))
        for (u in public) assertEquals(u, "Use https:// unless the search is on your home network", ownSearchAddressProblem(u))
        for (u in https) assertNull(u, ownSearchAddressProblem(u))
    }

    @Test fun anythingButAnHttpAddressIsRefused() {
        for (u in listOf("", "192.168.1.20:8888", "searx.local", "ftp://192.168.1.20", "javascript:alert(1)")) {
            assertEquals(u, "Type an address starting with http:// or https://", ownSearchAddressProblem(u))
        }
    }

    @Test fun aSavedPublicHttpAddressIsNeverSearched() = runBlocking {
        val wiki = object : LookupBackend {
            override val destination = "Wikipedia"
            override suspend fun search(query: String, internet: Boolean) = LookupOutcome()
            override suspend fun read(url: String) = LookupOutcome()
        }
        val own = OwnSearchLookup(plainClient(publicOnly = false), "1",
            OwnSearch(OwnSearch.SEARXNG, url = "http://203.0.113.5:8888"), wiki)
        assertEquals("Use https:// unless the search is on your home network.", own.test())
        val out = own.search("red panda")
        assertEquals("Use https:// unless the search is on your home network, so this came from Wikipedia.", out.notice)
    }

    @Test fun theLookupClientsRefusePlainHttpOffTheHomeNetwork() {
        for (client in listOf(plainClient(publicOnly = false), plainClient())) {
            try {
                client.newCall(Request.Builder().url("http://203.0.113.5:8888/search?q=x").build()).execute().close()
                fail("plain http to a public address went out")
            } catch (e: UnknownServiceException) {
                assertTrue(e.message.orEmpty().contains("only allowed on the home network"))
            }
        }
    }

    private fun dnsOf(vararg a: String) = okhttp3.Dns { _ -> a.map { java.net.InetAddress.getByName(it) } }

    @Test fun aHomeLookingNameThatResolvesToAPublicAddressIsRefusedOverHttp() {
        for (answer in listOf(arrayOf("203.0.113.5"), arrayOf("192.168.1.20", "203.0.113.5"), arrayOf("2606:4700::1111"))) {
            assertTrue(answer.joinToString(), cleartextResolvesOffNetwork("box.lan", dnsOf(*answer)))
            for (client in listOf(plainClient(publicOnly = false, homeDns = dnsOf(*answer)), plainClient(homeDns = dnsOf(*answer)))) {
                try {
                    // The address is refused before DNS for the connection or a socket is used.
                    client.newCall(Request.Builder().url("http://box.lan:8888/search?q=x").build()).execute().close()
                    fail("plain http to a name resolving to a public address went out")
                } catch (e: UnknownServiceException) {
                    assertTrue(e.message.orEmpty().startsWith(HTTPS_UNLESS_HOME))
                }
            }
        }
        for (answer in listOf(arrayOf("192.168.1.20"), arrayOf("100.100.1.2", "fd00::20"), arrayOf("127.0.0.1"))) {
            assertTrue(!cleartextResolvesOffNetwork("box.lan", dnsOf(*answer)))
        }
        assertTrue(!cleartextResolvesOffNetwork("box.lan", okhttp3.Dns { throw java.net.UnknownHostException("none") }))
        assertTrue(!cleartextResolvesOffNetwork("203.0.113.5", dnsOf("8.8.8.8")))  // a literal: judged as written
    }
}
