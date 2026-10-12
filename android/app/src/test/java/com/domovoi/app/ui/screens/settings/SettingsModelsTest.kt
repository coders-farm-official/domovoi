package com.domovoi.app.ui.screens.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The Settings screen keeps only what belongs to the phone; server
 * administration is handed off to the dashboard with a deep link built from
 * the connected server's base URL (web/static/index.html routes on
 * `location.hash`, so `#settings` lands on the dashboard's Settings page).
 */
class SettingsModelsTest {

    @Test fun settingsKeepsOnlyDeviceLocalTabsPlusTheDashboardHandOff() {
        // Greetings / Voices / Wake Words / Models / Configuration are gone by
        // design: the app has no admin session, so they could never apply.
        // Satellites moved in from the nav (2026-10-11, the web's Settings →
        // Satellites), and the phone bar's editor lives here too. Voice is
        // the phone's own voice (2026-10-12), not the server's Voices tab.
        assertEquals(
            listOf("Connection", "Phone bar", "Voice", "Satellites", "Server settings", "About"),
            SettingsTab.entries.map { it.label },
        )
        assertEquals(SettingsTab.Connection, SettingsTab.entries.first())
    }

    @Test fun aLinkNamesATabByItsKey() {
        // ui/shell: Route.Satellites opens "satellites"; Home's "edit the
        // bar" opens "phonebar".
        assertEquals(SettingsTab.Satellites, SettingsTab.named(com.domovoi.app.ui.shell.settingsTabFor(com.domovoi.app.ui.shell.Route.Satellites)))
        assertEquals(SettingsTab.PhoneBar, SettingsTab.named("phonebar"))
        assertNull(SettingsTab.named(null))
        assertNull(SettingsTab.named("greetings"))
        assertEquals(SettingsTab.entries.size, SettingsTab.entries.map { it.key }.toSet().size)
    }

    @Test fun dashboardSettingsUrl_deepLinksToTheConnectedServersSettingsPage() {
        assertEquals("http://192.168.1.10:6369/#settings", dashboardSettingsUrl("http://192.168.1.10:6369"))
        // A trailing slash or stray whitespace from the Connection field is tolerated.
        assertEquals("http://192.168.1.10:6369/#settings", dashboardSettingsUrl("http://192.168.1.10:6369/"))
        assertEquals("https://domovoi.lan/#settings", dashboardSettingsUrl("  https://domovoi.lan/  "))
    }

    @Test fun dashboardSettingsUrl_isNullWithoutAServer() {
        // No server → the "Open the dashboard" button is disabled with a hint.
        assertNull(dashboardSettingsUrl(null))
        assertNull(dashboardSettingsUrl(""))
        assertNull(dashboardSettingsUrl("   "))
        assertNull(dashboardSettingsUrl("/"))
    }
}
