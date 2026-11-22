package com.domovoi.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A phone is never an admin, so an admin-only refusal reads as one plain
 * sentence, never as the raw `401 … {"detail":"admin session required"}`
 * (the phone admin audit, 22 Nov 2026). The device tier's refusal ends with
 * the same words and means "pair this phone", so it must not match.
 */
class AdminRefusalTest {

    private val admin = """{"detail":"admin session required"}"""
    private val cookieOnly =
        """{"detail":"mutations require Authorization: Bearer — the dashboard cookie only renders GET state"}"""
    private val device = """{"detail":"X-Device-Token or admin session required"}"""

    @Test fun theAdminTiersRefusalsAreRecognised() {
        assertTrue(isAdminRefusal(401, admin))
        assertTrue(isAdminRefusal(403, cookieOnly))
    }

    @Test fun theDeviceTierAndOtherErrorsAreNot() {
        assertFalse(isAdminRefusal(401, device))
        assertFalse(isAdminRefusal(500, admin))
        assertFalse(isAdminRefusal(403, """{"detail":"Only friends can write here"}"""))
        assertFalse(isAdminRefusal(401, "admin session required"))   // not JSON
        assertFalse(isAdminRefusal(401, ""))
    }

    @Test fun theMessageAndTheToastSayItPlainly() {
        assertEquals(ADMIN_ONLY, apiErrorMessage(401, "Unauthorized", admin))
        assertEquals("""401 Unauthorized: $device""", apiErrorMessage(401, "Unauthorized", device))
        val e = ApiException(401, apiErrorMessage(401, "Unauthorized", admin), body = admin)
        assertEquals(ADMIN_ONLY, e.message)
        assertEquals(ADMIN_ONLY, failureText("rescan", e))
        val other = ApiException(409, "409 Conflict: taken", body = """{"detail":"taken"}""")
        assertEquals("delete failed: 409 Conflict: taken", failureText("delete", other))
    }
}
