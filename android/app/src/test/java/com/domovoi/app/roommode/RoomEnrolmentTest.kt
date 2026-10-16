package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * The room code a phone scans or pastes to become a room, held to the
 * shared contract `docs/uml/spare-device-enrol-fixture.json` that the
 * reference parser (`satellite/enrolment.py`) and the dashboard's builder
 * test against: every valid case's normalised values and room id, every
 * invalid case's reason code, zero differences.
 *
 * The fixture is owned by the spare-device Linux work (wf/sat-pc); the test
 * reads that one file (no copy of it), so the three parsers can't drift.
 */
class RoomEnrolmentTest {
    private val fixture: JsonObject by lazy {
        val path = System.getProperty("domovoi.enrol.fixture")
            ?: File(System.getProperty("domovoi.repo.dir") ?: "../..", "docs/uml/spare-device-enrol-fixture.json").path
        Json.parseToJsonElement(File(path).readText(Charsets.UTF_8)) as JsonObject
    }

    @Test
    fun everyValidCaseParsesToTheContractsValues() {
        val diffs = mutableListOf<String>()
        val cases = fixture["valid"] as JsonArray
        assertTrue(cases.size >= 20)
        for (case in cases) {
            val c = case.jsonObject
            val name = c["name"]!!.jsonPrimitive.content
            val expect = c["expect"]!!.jsonObject
            when (val r = RoomEnrolment.parse(c["payload"]!!.jsonPrimitive.content)) {
                is RoomEnrolment.Result.Invalid -> diffs += "$name refused (${r.reasonCode.code}): ${r.reason}"
                is RoomEnrolment.Result.Ok -> {
                    val e = r.enrolment
                    fun check(what: String, got: String, key: String) {
                        val want = expect[key]!!.jsonPrimitive.content
                        if (got != want) diffs += "$name $what: '$got' != '$want'"
                    }
                    check("core", e.coreUrl, "core")
                    check("ws_url", e.wsUrl, "ws_url")
                    check("fp", e.fingerprint, "fp")
                    check("room", e.roomLabel, "room")
                    check("room_id", e.roomId, "room_id")
                }
            }
        }
        assertEquals(emptyList<String>(), diffs)
    }

    @Test
    fun everyInvalidCaseIsRefusedForTheContractsReason() {
        val diffs = mutableListOf<String>()
        val cases = fixture["invalid"] as JsonArray
        assertTrue(cases.size >= 50)
        for (case in cases) {
            val c = case.jsonObject
            val name = c["name"]!!.jsonPrimitive.content
            val want = c["reason"]!!.jsonPrimitive.content
            when (val r = RoomEnrolment.parse(c["payload"]!!.jsonPrimitive.content)) {
                is RoomEnrolment.Result.Ok -> diffs += "$name accepted (want $want): ${r.enrolment}"
                is RoomEnrolment.Result.Invalid -> {
                    if (r.reasonCode.code != want) diffs += "$name: ${r.reasonCode.code} != $want (${r.reason})"
                    if (r.reason.isBlank()) diffs += "$name: no words for the screen"
                }
            }
        }
        assertEquals(emptyList<String>(), diffs)
    }

    @Test
    fun theReasonCodesAreTheContracts() {
        val codes = (fixture["reasons"] as JsonArray).map { it.jsonPrimitive.content }
        assertEquals(codes, RoomEnrolment.Reason.entries.map { it.code })
    }

    @Test
    fun whatTheDashboardBuildsParses() {
        for (case in fixture["build"] as JsonArray) {
            val b = case.jsonObject
            val r = RoomEnrolment.parse(b["payload"]!!.jsonPrimitive.content)
            if (r !is RoomEnrolment.Result.Ok) fail("${b["payload"]} refused: $r")
            r as RoomEnrolment.Result.Ok
            assertEquals(b["fp"]!!.jsonPrimitive.content.trim(), r.enrolment.fingerprint)
            assertEquals(b["room"]!!.jsonPrimitive.content.trim(), r.enrolment.roomLabel)
        }
    }

    /** The QR code is a join link (`<core>/join#<payload>`): what the app's
     *  scanner reads from it is exactly what the bare payload gives. */
    @Test
    fun theDashboardsJoinLinkParsesLikeItsPayload() {
        val cases = fixture["build_join"] as JsonArray
        assertTrue(cases.isNotEmpty())
        for (case in cases) {
            val b = case.jsonObject
            val link = RoomEnrolment.parse(b["join_link"]!!.jsonPrimitive.content)
            val bare = RoomEnrolment.parse(b["payload"]!!.jsonPrimitive.content)
            if (link !is RoomEnrolment.Result.Ok) fail("${b["join_link"]} refused: $link")
            assertEquals(bare, link)
        }
        val names = (fixture["valid"] as JsonArray).map { it.jsonObject["name"]!!.jsonPrimitive.content }
        assertTrue("the fixture has join-link cases", names.any { it.startsWith("join_link") })
    }

    @Test
    fun aNameTypedOnThePhoneFollowsTheSameRules() {
        assertNull(RoomEnrolment.typedRoomName("  Living Room "))
        assertEquals("living_room", RoomEnrolment.roomIdFor("Living Room"))
        assertEquals("kids_room_den", RoomEnrolment.roomIdFor("Kid's Room & Den"))
        assertEquals(RoomEnrolment.Reason.BAD_ROOM, RoomEnrolment.typedRoomName("!!!")?.reasonCode)
        assertEquals(RoomEnrolment.Reason.BAD_ROOM, RoomEnrolment.typedRoomName("a".repeat(33))?.reasonCode)
        assertEquals(RoomEnrolment.Reason.BAD_ROOM, RoomEnrolment.typedRoomName("kit\u0000chen")?.reasonCode)
    }

    @Test
    fun theHomeNetworkIsTheContractsAndNoMore() {
        for (h in listOf("10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.2", "fd00::1", "fe80::1", "core.local", "x.home.arpa", "box.lan", "a.internal")) {
            assertTrue(h, RoomEnrolment.isHomeNetworkHost(h))
        }
        for (h in listOf("127.0.0.1", "localhost", "100.64.0.1", "169.254.1.1", "8.8.8.8", "172.32.0.1", "::1", "::ffff:192.168.1.1", "2001:db8::1", "local", "x.localhost", "010.0.0.1")) {
            assertTrue(h, !RoomEnrolment.isHomeNetworkHost(h))
        }
    }
}
