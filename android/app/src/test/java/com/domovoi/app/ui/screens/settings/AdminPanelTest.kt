package com.domovoi.app.ui.screens.settings

import com.domovoi.app.net.AdminSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

/** Settings → Household → Admin on this phone: what it says, and where. */
class AdminPanelTest {
    private val utc = ZoneId.of("UTC")
    private val seven = Instant.parse("2026-11-25T19:00:00Z").toEpochMilli()

    @Test fun eachStateSaysWhatAdminThePhoneHolds() {
        assertEquals("Not signed in as admin.", adminStateLine(AdminSession.State.SignedOut, utc))
        assertEquals(
            "Signed in as admin. It ends after an hour with no admin change.",
            adminStateLine(AdminSession.State.SignedIn("http://192.168.1.20:6369", null), utc),
        )
        assertEquals(
            "Signed in as admin, kept on this phone until 7:00pm.",
            adminStateLine(AdminSession.State.SignedIn("http://192.168.1.20:6369", null, seven), utc),
        )
        assertTrue(adminStateLine(AdminSession.State.Kept("http://192.168.1.20:6369", seven), utc).startsWith("Admin is kept on this phone until 7:00pm."))
    }

    @Test fun itIsUnderHouseholdAndNeverOnASharedScreen() {
        assertEquals(SettingsGroup.Household, SettingsSection.AdminOnPhone.group)
        assertTrue(SettingsSection.AdminOnPhone in SharedScreenHiddenSections)
        assertTrue(SettingsSection.AdminOnPhone !in sectionsOf(SettingsGroup.Household, shared = true))
        assertEquals(SettingsSection.AdminOnPhone, SettingsSection.named("admin-on-phone"))
    }
}
