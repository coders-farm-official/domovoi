package com.domovoi.app.away

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class AwayWireTest {
    @Test fun phoneHelloIsFourToThirtySixBytesWithAndroidAsPlatform() {
        val h = AwayWire.phoneHello("1.5.0")
        assertArrayEquals(byteArrayOf(0, 1, 1, 5) + "1.5.0".toByteArray(), h)
        assertEquals(4, AwayWire.phoneHello("").size)
        // Only [0-9A-Za-z.+-], at most 32: what the house accepts.
        val long = AwayWire.phoneHello("1.5.0-test (debug) " + "x".repeat(50))
        assertEquals(36, long.size)
        assertEquals(32, long[3].toInt())
        assertTrue(String(long, 4, 32, Charsets.US_ASCII).matches(Regex("^[0-9A-Za-z.+\\-]*$")))
    }

    @Test fun houseHelloIsSixBytes() {
        assertEquals(1 to 1_048_576L, AwayWire.parseHouseHello(byteArrayOf(0, 1, 0, 0x10, 0, 0)))
        assertNull(AwayWire.parseHouseHello(byteArrayOf(0, 1, 0, 0x10, 0)))
    }

    @Test fun framesAreTwoByteBigEndianLengths() {
        val f = AwayWire.frame(ByteArray(300) { 7 })
        assertEquals(302, f.size)
        assertEquals(1, f[0].toInt())
        assertEquals(44, f[1].toInt())
        try {
            AwayWire.frame(ByteArray(0)); fail("an empty frame")
        } catch (e: IllegalArgumentException) {
        }
        try {
            AwayWire.frame(ByteArray(65536)); fail("an over-long frame")
        } catch (e: IllegalArgumentException) {
        }
    }

    @Test fun theHeaderIsKindReqFlags() {
        assertArrayEquals(byteArrayOf(1, 0x12, 0x34, 0x56, 0x78, 1), AwayWire.header(AwayWire.REQ, 0x12345678L, more = true))
        assertArrayEquals(byteArrayOf(3, 0, 0, 0, 0, 0), AwayWire.message(AwayWire.PING, 0))
        val m = AwayWire.parseHouse(byteArrayOf(0x81.toByte(), 0, 0, 0, 9, 1) + "{}".toByteArray())!!
        assertEquals(AwayWire.RESP, m.kind)
        assertEquals(9L, m.req)
        assertTrue(m.more)
        assertNull("other flag bits", AwayWire.parseHouse(byteArrayOf(0x81.toByte(), 0, 0, 0, 9, 2)))
        assertNull("a phone kind from the house", AwayWire.parseHouse(byteArrayOf(1, 0, 0, 0, 9, 0)))
        assertNull("short", AwayWire.parseHouse(byteArrayOf(0x84.toByte(), 0, 0)))
        val big = AwayWire.parseHouse(byteArrayOf(0x82.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0))!!
        assertEquals(0xFFFF_FFFFL, big.req)
    }

    @Test fun aLongRequestIsSplitWithMoreAndRefusedPastItsCap() {
        val json = ByteArray(150_000) { 'a'.code.toByte() }
        val msgs = AwayWire.requestMessages(5, json)
        assertEquals(3, msgs.size)
        msgs.forEachIndexed { i, m ->
            assertTrue(m.size <= AwayWire.MAX_PLAINTEXT)
            assertEquals(AwayWire.REQ, m[0].toInt())
            assertEquals(if (i < 2) 1 else 0, m[5].toInt())
        }
        assertEquals(json.size, msgs.sumOf { it.size - AwayWire.HEADER_SIZE })
        assertEquals(1, AwayWire.requestMessages(5, "{}".toByteArray()).size)
        try {
            AwayWire.requestMessages(5, ByteArray(AwayWire.MAX_REQ + 1)); fail("over 160 KiB")
        } catch (e: TooLarge) {
        }
    }

    @Test fun pictureBlobsRunInOrderAtMost65000Each() {
        val pic = ByteArray(200_001) { (it % 251).toByte() }
        val bodies = AwayWire.blobBodies(pic)
        assertEquals(4, bodies.size)
        var expected = 0
        val joined = java.io.ByteArrayOutputStream()
        for (b in bodies) {
            val off = ((b[0].toInt() and 0xFF) shl 24) or ((b[1].toInt() and 0xFF) shl 16) or ((b[2].toInt() and 0xFF) shl 8) or (b[3].toInt() and 0xFF)
            assertEquals(expected, off)
            assertTrue(b.size - 4 in 1..AwayWire.MAX_BLOB_PIECE)
            joined.write(b, 4, b.size - 4)
            expected += b.size - 4
            // A BLOB message fits one transport message.
            assertTrue(AwayWire.message(AwayWire.BLOB, 1, b).size <= AwayWire.MAX_PLAINTEXT)
        }
        assertArrayEquals(pic, joined.toByteArray())
    }

    private fun keys(o: JsonObject) = o.keys.toSet()

    @Test fun requestsCarryExactlyTheirOpsKeys() {
        // The house refuses unknown keys (§3.2), so each request is closed.
        assertEquals(setOf("op", "archived", "limit", "cursor"), keys(AwayWire.list()))
        assertEquals(JsonNull, AwayWire.list()["cursor"])
        assertEquals(setOf("op", "chat_id", "before_id", "limit"), keys(AwayWire.history(12)))
        assertEquals(50, AwayWire.history(12, limit = 500)["limit"]!!.jsonPrimitive.content.toInt())
        assertEquals(setOf("op", "chat_id", "client_id", "text"), keys(AwayWire.send(null, AwayWire.newClientId(), "hi")))
        val withPicture = AwayWire.send(12, AwayWire.newClientId(), "", PictureInfo.jpeg(byteArrayOf(-1, -40, -1, 1)))
        assertEquals(setOf("op", "chat_id", "client_id", "text", "picture"), keys(withPicture))
        val pic = withPicture["picture"]!!.jsonObject
        assertEquals(setOf("type", "size", "sha256"), keys(pic))
        assertEquals("jpeg", pic["type"]!!.jsonPrimitive.content)
        assertEquals(4, pic["size"]!!.jsonPrimitive.content.toInt())
        assertTrue(pic["sha256"]!!.jsonPrimitive.content.matches(Regex("^[0-9a-f]{64}$")))
        assertEquals(setOf("op", "chat_id"), keys(AwayWire.stream(3)))
        assertEquals(setOf("op", "chat_id"), keys(AwayWire.cancel(3)))
        assertFalse("no admin grant unless asked", "admin_grant" in AwayWire.send(1, AwayWire.newClientId(), "x"))
    }

    @Test fun clientIdsAreLowerCaseUuids() {
        repeat(20) { assertTrue(AwayWire.newClientId().matches(Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"))) }
    }

    @Test fun theHousesJsonMayCarryKeysThisPhoneDoesntKnow() {
        val o = AwayWire.parseObject("""{"threads":[],"next":null,"later_field":{"x":1}}""".toByteArray())!!
        assertTrue("threads" in o)
        assertNull(AwayWire.parseObject("[1]".toByteArray()))
        assertNull(AwayWire.parseObject("not json".toByteArray()))
    }

    @Test fun textIsCountedInCodePoints() {
        assertEquals(1, AwayWire.codePoints("😀"))
    }

    @Test fun thePictureQualityStepsDownUntilItFits() {
        val sizes = mapOf(80 to 2_000_000, 72 to 1_500_000, 64 to 1_000_000)
        val got = AwayPicture.pick { q -> ByteArray(sizes[q] ?: 10) }!!
        assertEquals(1_000_000, got.size)
        assertNull(AwayPicture.pick { ByteArray(AwayWire.MAX_PICTURE + 1) })
        assertEquals(80, AwayPicture.QUALITIES.first())
    }
}
