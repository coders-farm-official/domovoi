package com.domovoi.app.ui.screens.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The Settings screen has the dashboard's groups and names; what only an
 * admin changes on the server is handed off to the dashboard with a deep
 * link built from the connected server's base URL (web/static/index.html
 * routes on `location.hash`, so `#settings/<section>` lands on it).
 */
class SettingsModelsTest {

    @Test fun settingsHasTheDashboardsGroupsAndNames() {
        // The web's regroup (2026-10-21): the same five groups, the same
        // plain names, in the same order.
        assertEquals(
            listOf("Rooms & devices", "Voice", "Chat & AI", "Privacy", "System"),
            SettingsGroup.entries.map { it.label },
        )
        assertEquals(
            listOf(
                listOf("Rooms", "Phones & browsers", "App layout"),
                listOf("Wake words", "Voices", "Greetings"),
                listOf("Models"),
                listOf("Internet", "Voice history"),
                listOf("About & updates", "Health", "Advanced"),
            ),
            SettingsGroup.entries.map { g -> sectionsOf(g).map { it.label } },
        )
    }

    @Test fun whatOnlyTheDashboardChangesOpensItThereInWordsEveryDashboardKnows() {
        // The app has no admin session. An older server has no "advanced"
        // or "voice-history" address, but every one has these.
        assertEquals(
            mapOf("wake-words" to "wake-words", "greetings" to "greetings", "models" to "models",
                "internet" to "internet", "voice-history" to "recordings", "advanced" to "configuration"),
            SettingsSection.entries.filter { it.dashboard != null }.associate { it.key to it.dashboard },
        )
        assertEquals(
            "http://192.168.1.20:6369/#settings/configuration",
            dashboardSettingsUrl("http://192.168.1.20:6369/", SettingsSection.Advanced.dashboard),
        )
    }

    @Test fun aLinkNamesASectionByItsKeyOrTheKeyItHadBefore() {
        // ui/shell: Route.Satellites opens "rooms", Route.Containers
        // "health"; Home's "edit the bar" opens "app-layout". The tab keys
        // from before the regroup still land.
        val routeKey = { r: com.domovoi.app.ui.shell.Route -> com.domovoi.app.ui.shell.settingsTabFor(r) }
        assertEquals(SettingsSection.Rooms, SettingsSection.named(routeKey(com.domovoi.app.ui.shell.Route.Satellites)))
        assertEquals(SettingsSection.Health, SettingsSection.named(routeKey(com.domovoi.app.ui.shell.Route.Containers)))
        assertEquals(SettingsSection.AppLayout, SettingsSection.named("app-layout"))
        assertEquals(SettingsSection.AppLayout, SettingsSection.named("phonebar"))
        assertEquals(SettingsSection.Rooms, SettingsSection.named("satellites"))
        assertEquals(SettingsSection.PhonesAndBrowsers, SettingsSection.named("connection"))
        assertEquals(SettingsSection.Voices, SettingsSection.named("voice"))
        assertEquals(SettingsSection.Advanced, SettingsSection.named("server"))
        assertNull(SettingsSection.named(null))
        assertNull(SettingsSection.named("nonsense"))
        val keys = SettingsSection.entries.flatMap { listOf(it.key) + it.aliases }
        assertEquals(keys.size, keys.toSet().size)
    }

    @Test fun backStepsOutOneLevel() {
        assertEquals(SettingsPlace.Index, placeOf(""))
        assertEquals(SettingsPlace.Group(SettingsGroup.Voice), placeOf("group:voice"))
        // "voice" is the old key of the phone's Voice tab: a link to it opens Voices.
        assertEquals(SettingsPlace.Section(SettingsSection.Voices), placeOf("voice"))
        assertEquals("group:voice", SettingsPlace.Group(SettingsGroup.Voice).key)
        assertEquals(SettingsPlace.Section(SettingsSection.WakeWords), placeOf("wake-words"))
        assertEquals(SettingsPlace.Group(SettingsGroup.Voice), placeOf("wake-words").up())
        assertEquals(SettingsPlace.Index, placeOf("group:voice").up())
        assertNull(placeOf("").up())
    }

    @Test fun searchJumpsStraightToASection() {
        assertEquals(SettingsSection.WakeWords, searchSettings("wake").first())
        assertEquals(SettingsSection.VoiceHistory, searchSettings("voice history").first())
        assertEquals(SettingsSection.VoiceHistory, searchSettings("recordings").first())
        assertEquals(SettingsSection.Rooms, searchSettings("satellite").first())
        assertEquals(SettingsSection.PhonesAndBrowsers, searchSettings("token").first())
        assertEquals(emptyList<SettingsSection>(), searchSettings("printer"))
        assertEquals(emptyList<SettingsSection>(), searchSettings("  "))
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
