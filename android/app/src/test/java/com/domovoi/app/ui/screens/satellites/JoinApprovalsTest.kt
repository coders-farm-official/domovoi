package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.DomovoiJson
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Devices asking to join, as the phone's admin sees them (§3.7a). */
class JoinApprovalsTest {
    private val sample = """
        [{"satellite_id": "kitchen_2", "room_id": "kitchen_2",
          "requested_room": "kitchen", "requested_room_name": "Kitchen",
          "has_code": true, "proof": false, "waiting_for_device": false,
          "code_rejected": false, "proof_failures": 0,
          "mac": null, "board": "android_room", "sat_type": "voice",
          "first_seen_at": "2026-11-22T18:00:00Z", "last_seen_at": "2026-11-22T18:02:10Z",
          "attempts": 3}]
    """.trimIndent()
    private val rooms = listOf(JoinRoom("den", "Den"), JoinRoom("kitchen", "Kitchen"))

    @Test fun theApiReferenceExampleReads() {
        val r = DomovoiJson.decodeFromString(ListSerializer(JoinRequest.serializer()), sample).single()
        assertEquals("kitchen_2", r.id)
        assertEquals("An Android phone or tablet", joinDeviceWords(r))
    }

    @Test fun anOlderServersRequestUsesItsRoomIdAsTheId() {
        val r = DomovoiJson.decodeFromString(JoinRequest.serializer(), """{"room_id":"porch","has_code":true}""")
        assertEquals("porch", r.id)
    }

    @Test fun theRoomItAskedForIsPreselectedWhenItExists() {
        val r = JoinRequest("kitchen_2", "kitchen_2", "kitchen", "Kitchen")
        assertEquals(JoinChoice.Existing("kitchen", "Kitchen"), joinPreselect(r, rooms))
        // Asked for a room that's gone (no name): a new room after the device.
        val gone = JoinRequest("porch_2", "porch_2", "porch", null)
        assertEquals(JoinChoice.NewRoom("Porch 2"), joinPreselect(gone, rooms))
        // Asked for nothing (a device from before 1.5.0).
        assertEquals(JoinChoice.NewRoom("Garage"), joinPreselect(JoinRequest(room_id = "garage"), rooms))
    }

    @Test fun theChoicesAreEveryRoomThenANewOne() {
        val c = joinChoices(JoinRequest("kitchen_2", "kitchen_2"), rooms)
        assertEquals(listOf(JoinChoice.Existing("den", "Den"), JoinChoice.Existing("kitchen", "Kitchen"), JoinChoice.NewRoom("Kitchen 2")), c)
    }

    @Test fun approveSendsTheCodeAndExactlyOneRoomChoice() {
        val a = joinApproveBody("123456", JoinChoice.Existing("kitchen", "Kitchen"))
        assertEquals("123456", a["code"]!!.jsonPrimitive.content)
        assertEquals("kitchen", a["room_id"]!!.jsonPrimitive.content)
        assertNull(a["new_room_name"])
        val b = joinApproveBody("123456", JoinChoice.NewRoom("  Kitchen 2 "))
        assertEquals("Kitchen 2", b["new_room_name"]!!.jsonPrimitive.content)
        assertNull(b["room_id"])
    }

    @Test fun onlySixDigitsGoOut() {
        assertTrue(joinCodeOk("012345"))
        for (bad in listOf("", "12345", "1234567", "12a456", "12 456")) assertFalse(bad, joinCodeOk(bad))
    }

    @Test fun whatHappenedIsSaidInHouseholdWords() {
        assertEquals("Added to Kitchen.", joinApprovedText(false, JoinChoice.Existing("kitchen", "Kitchen")))
        assertTrue(joinApprovedText(true, JoinChoice.NewRoom("Den 2")).startsWith("Code sent. The device joins Den 2"))
        assertEquals("That code doesn't match the one the device shows.", joinRefusalText(422, "wrong code"))
        assertTrue(joinRefusalText(429, null).contains("five minutes"))
        assertNull(joinStatus(JoinRequest(room_id = "x")))
        assertTrue(joinStatus(JoinRequest(room_id = "x", waiting_for_device = true))!!.contains("next visit"))
        assertTrue(joinStatus(JoinRequest(room_id = "x", code_rejected = true))!!.contains("wasn't this device's"))
    }

    @Test fun noIdIsShownForWhatIsAsking() {
        for (board in listOf("android_room", "ios_room", "raspberry_pi_zero_2_w", "linux_pc", null)) {
            val words = joinDeviceWords(JoinRequest(satellite_id = "kitchen_2", room_id = "kitchen_2", board = board))
            assertFalse(words, words.contains("kitchen_2") || words.contains("_"))
        }
    }
}
