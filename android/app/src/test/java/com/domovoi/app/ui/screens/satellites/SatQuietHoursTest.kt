package com.domovoi.app.ui.screens.satellites

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The room's quiet-hours choice on the phone: the bodies the PUT sends. */
class SatQuietHoursTest {
    @Test fun theThreeChoicesAndTheirBodies() {
        assertEquals(listOf("house", "custom", "off"), QUIET_CHOICES.map { it.first })
        assertEquals("""{"mode":"house"}""", quietBody("house", "", "").toString())
        assertEquals("""{"mode":"off"}""", quietBody("off", "x", "y").toString())
        assertEquals("""{"mode":"custom","start":"21:30","end":"06:05"}""", quietBody("custom", "21:30", "6:05").toString())
        assertNull(quietBody("custom", "25:00", "06:00"))
        assertNull(quietBody("custom", "21:30", ""))
        assertNull(quietBody("loud", "21:30", "06:00"))
        assertEquals("/api/satellites/pako/quiet-hours", quietPath("pako"))
        assertEquals("pako keeps quiet 21:30–06:05", quietToast("pako", "custom", "21:30", "6:05"))
    }
}
