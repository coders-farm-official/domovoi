package com.domovoi.app.away

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/** The Noise IK pattern against the published cacophony vectors the house
 *  is checked against (domovoi/tests/fixtures/noise_vectors.json). */
class AwayNoiseTest {
    private fun hex(s: String) = s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    private val vector: JsonObject by lazy {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val doc = Json.parseToJsonElement(File(repo, "domovoi/tests/fixtures/noise_vectors.json").readText()).jsonObject
        doc["vectors"]!!.jsonArray.map { it.jsonObject }
            .single { it["protocol_name"]!!.jsonPrimitive.content == NoiseIK.PROTOCOL }
    }

    private fun s(key: String) = vector[key]!!.jsonPrimitive.content

    @Test fun theCacophonyVectorRunsBothWays() {
        val init = NoiseIK(true, hex(s("init_prologue")), hex(s("init_static")), hex(s("init_remote_static")), hex(s("init_ephemeral")))
        val resp = NoiseIK(false, hex(s("resp_prologue")), hex(s("resp_static")), ephemeral = hex(s("resp_ephemeral")))
        val messages = vector["messages"]!!.jsonArray.map { it.jsonObject }
        assertTrue(messages.size > 2)
        var initT: NoiseTransport? = null
        var respT: NoiseTransport? = null
        messages.forEachIndexed { i, m ->
            val payload = hex(m["payload"]!!.jsonPrimitive.content)
            val expected = hex(m["ciphertext"]!!.jsonPrimitive.content)
            val initiatorWrites = i % 2 == 0
            if (!init.complete) {
                val (w, r) = if (initiatorWrites) init to resp else resp to init
                assertArrayEquals("handshake message $i", expected, w.writeMessage(payload))
                assertArrayEquals("handshake payload $i", payload, r.readMessage(expected))
                if (init.complete) {
                    assertArrayEquals(hex(s("handshake_hash")), init.handshakeHash)
                    assertArrayEquals(hex(s("handshake_hash")), resp.handshakeHash)
                    initT = init.transport()
                    respT = resp.transport()
                }
            } else {
                val (w, r) = if (initiatorWrites) initT!! to respT!! else respT!! to initT!!
                assertArrayEquals("transport message $i", expected, w.encrypt(payload))
                assertArrayEquals("transport payload $i", payload, r.decrypt(expected))
            }
        }
        // The responder learned the initiator's static key from message 1.
        assertArrayEquals(Curve25519.publicOf(hex(s("init_static"))), resp.remoteStaticKey)
    }

    @Test fun aTamperedMessageIsRefusedAndTheCounterStays() {
        val house = Curve25519.newPrivate()
        val phone = Curve25519.newPrivate()
        val i = NoiseIK(true, AwayWire.PROLOGUE, phone, Curve25519.publicOf(house))
        val r = NoiseIK(false, AwayWire.PROLOGUE, house)
        r.readMessage(i.writeMessage(byteArrayOf(1)))
        i.readMessage(r.writeMessage(byteArrayOf(2)))
        val it = i.transport()
        val rt = r.transport()
        val c = it.encrypt("hello".toByteArray())
        val bad = c.copyOf().also { b -> b[0] = (b[0].toInt() xor 1).toByte() }
        try {
            rt.decrypt(bad)
            fail("a tampered message decrypted")
        } catch (e: NoiseException) {
        }
        // A refused message doesn't move the counter (spec 5.1).
        assertEquals("hello", String(rt.decrypt(c)))
    }

    @Test fun aWrongPrologueOrHouseKeyFailsTheHandshake() {
        val house = Curve25519.newPrivate()
        val phone = Curve25519.newPrivate()
        val wrongHouse = Curve25519.publicOf(Curve25519.newPrivate())
        val i = NoiseIK(true, AwayWire.PROLOGUE, phone, wrongHouse)
        val r = NoiseIK(false, AwayWire.PROLOGUE, house)
        try {
            r.readMessage(i.writeMessage(ByteArray(0)))
            fail("the house read a message meant for another key")
        } catch (e: NoiseException) {
        }
        val i2 = NoiseIK(true, "friend-houses".toByteArray(), phone, Curve25519.publicOf(house))
        val r2 = NoiseIK(false, AwayWire.PROLOGUE, house)
        try {
            r2.readMessage(i2.writeMessage(ByteArray(0)))
            fail("another prologue passed")
        } catch (e: NoiseException) {
        }
        assertFalse(r2.complete)
    }

    @Test fun aLowOrderKeyIsRefused() {
        try {
            Curve25519.dh(Curve25519.newPrivate(), ByteArray(32))
            fail("an all-zero point was accepted")
        } catch (e: NoiseException) {
        }
    }

    @Test fun messageOneHasTheSizeTheHouseExpects() {
        // e (32) + s (32 + 16) + PHONE_HELLO + tag (16): wire.py MSG1_MIN..MSG1_MAX.
        val i = NoiseIK(true, AwayWire.PROLOGUE, Curve25519.newPrivate(), Curve25519.publicOf(Curve25519.newPrivate()))
        val hello = AwayWire.phoneHello("1.5.0")
        assertEquals(32 + 48 + hello.size + 16, i.writeMessage(hello).size)
    }
}
