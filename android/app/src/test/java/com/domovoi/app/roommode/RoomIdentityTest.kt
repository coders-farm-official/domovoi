package com.domovoi.app.roommode

import com.domovoi.app.net.Ed25519
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

/**
 * The room phone's port of satellite/server_identity.py: an address-bound
 * health proof against the pinned key, and signed file lists. The fixture
 * (test resources, roommode/identity-fixture.json) was signed by the core's
 * own domovoi/server_identity.py, so these bytes are the core's bytes.
 */
class RoomIdentityTest {
    private val fixture: JsonObject by lazy {
        Json.parseToJsonElement(javaClass.classLoader!!.getResource("roommode/identity-fixture.json")!!.readText()) as JsonObject
    }
    private val fp get() = fixture["fingerprint"]!!.jsonPrimitive.content
    private val challenge get() = fixture["challenge"]!!.jsonPrimitive.content
    private val addr get() = fixture["addr"]!!.jsonPrimitive.content

    @Test
    fun theCoresBoundHealthAnswerVerifies() {
        assertEquals(fp, RoomIdentity.verifyHealthDocument(fixture["health"], challenge, fp, addr))
    }

    @Test
    fun anUnboundAnswerIsNotProofForAPinnedRoom() {
        val e = assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyHealthDocument(fixture["health_unbound"], challenge, fp, addr)
        }
        assertTrue(e.message!!, e.message!!.contains("not bound"))
    }

    @Test
    fun anAnswerForAnotherAddressOrNonceOrKeyIsRefused() {
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyHealthDocument(fixture["health"], challenge, fp, "192.168.0.99:6370")
        }
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyHealthDocument(fixture["health"], "00$challenge".take(32), fp, addr)
        }
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyHealthDocument(fixture["health"], challenge, "SHA256:X+zrZv/IbzjZUnhsbWlsecLbwjndTpG0ZynXOif7V+k", addr)
        }
        // A tampered signature.
        val doc = fixture["health"]!!.jsonObject
        val identity = doc["identity"]!!.jsonObject.toMutableMap()
        val sig = Base64.getDecoder().decode(identity["signature"]!!.jsonPrimitive.content).also { it[0] = (it[0].toInt() xor 1).toByte() }
        identity["signature"] = JsonPrimitive(Base64.getEncoder().encodeToString(sig))
        val tampered = JsonObject(doc + ("identity" to JsonObject(identity)))
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyHealthDocument(tampered, challenge, fp, addr)
        }
    }

    @Test
    fun aRefusedAddressSaysSo() {
        val doc = buildJsonObject {
            put("identity", buildJsonObject {
                put("algorithm", "ed25519")
                put("public_key", "qvDjqrqlUyNof5tJphegfPIIF1Uzwu62kyRo9GMxDwc=")
                put("challenge", challenge)
                put("addr_refused", addr)
            })
        }
        val e = assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyHealthDocument(doc, challenge, fp, addr)
        }
        assertTrue(e.message!!, e.message!!.contains("would not sign"))
    }

    @Test
    fun verifyServerAsksWithANewNonceAndTheDialedAddress() {
        val seed = ByteArray(32) { (it * 7 + 3).toByte() }
        val pub = Ed25519.publicKey(seed)
        val pinned = RoomIdentity.fingerprintOf(pub)
        var asked: String? = null
        val proved = RoomIdentity.verifyServer("http://192.168.0.240:6370", pinned) { url ->
            asked = url
            val q = url.substringAfter('?').split('&').associate { it.substringBefore('=') to java.net.URLDecoder.decode(it.substringAfter('='), "UTF-8") }
            val c = q.getValue("challenge")
            val a = q.getValue("addr")
            val sig = Ed25519.sign(seed, RoomIdentity.healthMessage(c, a))
            buildJsonObject {
                put("status", "ok")
                put("identity", buildJsonObject {
                    put("algorithm", "ed25519")
                    put("public_key", Base64.getEncoder().encodeToString(pub))
                    put("fingerprint", pinned)
                    put("challenge", c)
                    put("addr", a)
                    put("signature", Base64.getEncoder().encodeToString(sig))
                })
            }.toString()
        }
        assertEquals(pinned, proved)
        assertTrue(asked!!, asked!!.startsWith("http://192.168.0.240:6370/v1/health?challenge="))
        assertTrue(asked!!, asked!!.endsWith("&addr=192.168.0.240%3A6370"))
        // A transport failure is "not provable", in words.
        val e = assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyServer("http://192.168.0.240:6370", pinned) { throw java.io.IOException("refused") }
        }
        assertTrue(e.message!!.contains("could not reach"))
    }

    @Test
    fun dialedAddressesAreSpelledLikeThePis() {
        assertEquals("192.168.0.240:6370", RoomIdentity.dialedAddress("http://192.168.0.240:6370"))
        assertEquals("domovoi.local:80", RoomIdentity.dialedAddress("http://Domovoi.LOCAL/"))
        assertEquals("[fd00::5]:6370", RoomIdentity.dialedAddress("ws://[fd00::5]:6370"))
        assertEquals("h:443", RoomIdentity.dialedAddress("https://h"))
        // An IPv6 literal is not re-spelled (the reviewer's Python vector).
        assertEquals("[fd00:0:0:0:0:0:0:5]:6370", RoomIdentity.dialedAddress("http://[FD00:0:0:0:0:0:0:5]:6370"))
    }

    @Test
    fun theCoresSignedListVerifiesAndCanonicalisesByteForByte() {
        val envelope = fixture["envelope"]
        val accepted = RoomIdentity.verifyManifestEnvelope(envelope, RoomIdentity.WAKE_BASE_CHANNEL, fp)
        assertEquals(4, accepted.manifest.size)
        assertEquals(fixture["manifest_digest"]!!.jsonPrimitive.content, accepted.digest)
        val manifest = envelope!!.jsonObject["manifest"]!!
        assertEquals(fixture["canonical_manifest"]!!.jsonPrimitive.content, String(RoomIdentity.canonicalJson(manifest), Charsets.UTF_8))
        // Another channel, another key: refused.
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyManifestEnvelope(envelope, RoomIdentity.SOUNDS_CHANNEL, fp)
        }
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyManifestEnvelope(envelope, RoomIdentity.WAKE_BASE_CHANNEL, "SHA256:X+zrZv/IbzjZUnhsbWlsecLbwjndTpG0ZynXOif7V+k")
        }
        // A changed list under the same signature: refused.
        val o = envelope.jsonObject
        val changed = JsonObject(o + ("manifest" to buildJsonObject { put("melspectrogram.onnx", "00".repeat(32)) }))
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyManifestEnvelope(changed, RoomIdentity.WAKE_BASE_CHANNEL, fp)
        }
        // No signature_v2 (a server from before freshness): refused.
        assertThrows(RoomIdentity.IdentityException::class.java) {
            RoomIdentity.verifyManifestEnvelope(JsonObject(o - "signature_v2"), RoomIdentity.WAKE_BASE_CHANNEL, fp)
        }
    }

    @Test
    fun anOlderListIsRefused() {
        val a = RoomIdentity.Accepted(emptyMap(), serial = 5, issuedAt = 1, digest = "x")
        RoomIdentity.checkFreshness(a, null)
        RoomIdentity.checkFreshness(a, RoomIdentity.Seen(5, "x"))
        RoomIdentity.checkFreshness(a, RoomIdentity.Seen(4, "y"))
        assertThrows(RoomIdentity.IdentityException::class.java) { RoomIdentity.checkFreshness(a, RoomIdentity.Seen(6, "x")) }
        assertThrows(RoomIdentity.IdentityException::class.java) { RoomIdentity.checkFreshness(a, RoomIdentity.Seen(5, "y")) }
    }

    @Test
    fun canonicalJsonEscapesOnlyWhatPythonEscapes() {
        val doc = Json.parseToJsonElement("""{"b":"é\n\"\\\u0001\u007f","a":[1,true,null],"😀":"x","Z":"y"}""")
        // json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assertEquals("{\"Z\":\"y\",\"a\":[1,true,null],\"b\":\"é\\n\\\"\\\\\\u0001\u007f\",\"😀\":\"x\"}", String(RoomIdentity.canonicalJson(doc), Charsets.UTF_8))
    }
}
