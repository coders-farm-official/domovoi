package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * The enrolment payload a phone scans or pastes to become a room. The cases
 * are a fixture the dashboard side shares (docs/uml/spare-device-enrol-
 * fixture.json, from wf/sat-pc); until that file exists the test reads the
 * app's own copy of the same contract (test resources, roommode/
 * enrol-fixture.json).
 */
class RoomEnrolmentTest {
    private val fixture: JsonObject by lazy {
        val shared = System.getProperty("domovoi.enrol.fixture")?.let(::File)?.takeIf { it.isFile }
        val text = shared?.readText()
            ?: javaClass.classLoader!!.getResource("roommode/enrol-fixture.json")!!.readText()
        Json.parseToJsonElement(text) as JsonObject
    }

    @Test
    fun everyValidPayloadParsesToItsRoom() {
        val cases = fixture["valid"] as JsonArray
        assertTrue(cases.isNotEmpty())
        for (case in cases) {
            val c = case as JsonObject
            val payload = c["payload"]!!.jsonPrimitive.content
            when (val r = RoomEnrolment.parse(payload)) {
                is RoomEnrolment.Result.Ok -> {
                    assertEquals(payload, c["core"]!!.jsonPrimitive.content, r.enrolment.coreUrl)
                    assertEquals(payload, c["fp"]!!.jsonPrimitive.content, r.enrolment.fingerprint)
                    assertEquals(payload, c["room"]!!.jsonPrimitive.content, r.enrolment.roomId)
                    c["room_label"]?.let { assertEquals(payload, it.jsonPrimitive.content, r.enrolment.roomLabel) }
                }
                is RoomEnrolment.Result.Invalid -> fail("$payload refused: ${r.reason}")
            }
        }
    }

    @Test
    fun everyInvalidPayloadIsRefusedWithAReason() {
        val cases = fixture["invalid"] as JsonArray
        assertTrue(cases.isNotEmpty())
        for (case in cases) {
            val c = case as JsonObject
            val payload = c["payload"]!!.jsonPrimitive.content
            val r = RoomEnrolment.parse(payload)
            assertTrue("$payload (${c["why"]}) was accepted: $r", r is RoomEnrolment.Result.Invalid)
            assertTrue((r as RoomEnrolment.Result.Invalid).reason.isNotBlank())
        }
    }

    @Test
    fun roomIdsFollowTheCoresRule() {
        assertEquals("kitchen", RoomEnrolment.roomIdFor("kitchen"))
        assertEquals("living_room", RoomEnrolment.roomIdFor("  Living   Room "))
        assertEquals("room_3", RoomEnrolment.roomIdFor("3"))
        assertEquals(32, RoomEnrolment.roomIdFor("a".repeat(50))!!.length)
        assertEquals(null, RoomEnrolment.roomIdFor("!!!"))
        for (name in listOf("Ünïcödé Hall", "x-y.z", "Ζ room", "_lead")) {
            val id = RoomEnrolment.roomIdFor(name)
            if (id != null) assertTrue(id, RoomEnrolment.ROOM_ID.matches(id))
        }
    }

    @Test
    fun aRealPlusIsPercentEncodedAndAPlusIsASpace() {
        assertEquals("a b", RoomEnrolment.percentDecode("a+b"))
        assertEquals("a+b", RoomEnrolment.percentDecode("a%2Bb"))
        assertEquals("é", RoomEnrolment.percentDecode("%C3%A9"))
        assertEquals(null, RoomEnrolment.percentDecode("%C3"))
        assertEquals(null, RoomEnrolment.percentDecode("%4"))
    }

    @Test
    fun theFingerprintMustBeThirtyTwoBytes() {
        val good = "SHA256:LWkJXcK5ihhSQJiBnv9OddRsfntOVYlM6YKDAes6f2A"
        assertEquals(good, RoomEnrolment.normalizeFingerprint(good))
        assertEquals(good, RoomEnrolment.normalizeFingerprint("$good="))
        assertEquals(null, RoomEnrolment.normalizeFingerprint("sha256:LWkJXcK5ihhSQJiBnv9OddRsfntOVYlM6YKDAes6f2A"))
        assertEquals(null, RoomEnrolment.normalizeFingerprint("SHA256:LWkJXcK5ihhSQJiBnv9OddRsfntOVYlM6YKDAes6f2!"))
    }
}
