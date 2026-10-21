package com.domovoi.app.net

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The register body: "This phone belongs to" rides along as
 * `owner_person_id` on every register, a number, or null to clear it, and
 * is never left out (which would keep a stale owner on the server).
 */
class DeviceRegistryTest {
    private fun body(owner: String?) = registerPayload("android-1a2b", "Pixel 8", "Android 15; Pixel 8", owner)

    @Test fun theOwnerIsSentAsANumber() {
        val b = body("3")
        assertEquals(3L, (b["owner_person_id"] as JsonPrimitive).long)
        assertEquals("android-1a2b", (b["device_id"] as JsonPrimitive).content)
        assertEquals("Pixel 8", (b["name"] as JsonPrimitive).content)
        assertEquals("android", (b["platform"] as JsonPrimitive).content)
        assertEquals("Android 15; Pixel 8", (b["user_agent"] as JsonPrimitive).content)
    }

    @Test fun nobodyClearsIt() {
        val b = body(null)
        assertTrue("sent, not left out", "owner_person_id" in b)
        assertEquals(JsonNull, b["owner_person_id"])
    }

    @Test fun anIdThatIsntAPersonClearsIt() {
        assertEquals(JsonNull, body("abc")["owner_person_id"])
        assertEquals(JsonNull, body("0")["owner_person_id"])
        assertEquals(JsonNull, body("")["owner_person_id"])
        assertEquals(12L, (body(" 12 ")["owner_person_id"] as JsonPrimitive).long)
    }
}
