package com.domovoi.app.ui.shell

import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Route visibility is data-driven by the capability manifest (design section 8). */
class RoutesTest {

    private fun caps(vararg c: String) =
        Capabilities(plugins = listOf(CapabilityPlugin(slug = "p", androidCapabilities = c.toList())))

    @Test fun onlyPluginBackedRoutesRequireACapability() {
        assertEquals(CAP_STATIONS, Route.Stations.requiredCapability())
        assertEquals(CAP_IMAGEGEN, Route.Images.requiredCapability())
        Route.entries.filter { it != Route.Stations && it != Route.Images }
            .forEach { assertNull("${it.name} should be ungated", it.requiredCapability()) }
    }

    @Test fun gatedRoutesHiddenWithoutTheirCapability() {
        assertFalse(Route.Stations.visibleWith(Capabilities.EMPTY))
        assertFalse(Route.Images.visibleWith(Capabilities.EMPTY))
        assertTrue(Route.Music.visibleWith(Capabilities.EMPTY))
        assertTrue(Route.Stations.visibleWith(caps(CAP_STATIONS)))
        assertFalse(Route.Images.visibleWith(caps(CAP_STATIONS)))
        assertTrue(Route.Images.visibleWith(caps(CAP_STATIONS, CAP_IMAGEGEN)))
    }

    @Test fun appOpensOnHome() {
        // The web opens (and falls back) on #home; so does the app.
        assertEquals(Route.Home, StartRoute)
        assertTrue(StartRoute.visibleWith(Capabilities.EMPTY))
        assertTrue(StartRoute.visibleOn(Capabilities.EMPTY, shared = true))
    }

    @Test fun navigationTablesCoverEveryDestinationExactlyOnce() {
        // Compact nav: the web phone strip's default, left to right; every
        // other screen lives on Home's "everything" grid (the grid drops
        // whatever is on this phone's bar, PhoneBarTest).
        assertEquals(listOf(Route.Home, Route.Music, Route.Videos, Route.Chat), DefaultPhoneBar)
        val reachable = (listOf(Route.Home) + EverythingRoutes).toSet()
        assertEquals(Route.entries.toSet() - LauncherlessRoutes, reachable)
        assertEquals(1 + EverythingRoutes.size, reachable.size) // no duplicates
        assertTrue(DefaultPhoneBar.all { it == Route.Home || it in EverythingRoutes })
        // Settings and the manual have no tab, so the grid must carry them.
        assertTrue(EverythingRoutes.contains(Route.Settings))
        assertTrue(EverythingRoutes.contains(Route.Manual))
        // Workspace sidebar keeps the web order and excludes chrome-only
        // routes; Home is the drawer's brand row, not a list item.
        assertFalse(WorkspaceRoutes.contains(Route.Home))
        assertFalse(WorkspaceRoutes.contains(Route.Settings))
        assertFalse(WorkspaceRoutes.contains(Route.Manual))
        assertEquals(Route.Chat, WorkspaceRoutes.first())
        assertEquals(Route.Files, WorkspaceRoutes.last())
    }

    @Test fun theContainersScreenIsOnNoLauncher() {
        // Reached from the banner and from an incident's notification only,
        // like the web's #containers, which is not in the nav.
        assertEquals(setOf(Route.Containers, Route.Satellites), LauncherlessRoutes)
        assertFalse(Route.Containers in DefaultPhoneBar)
        assertFalse(Route.Containers in EverythingRoutes)
        assertFalse(Route.Containers in WorkspaceRoutes)
        // Never gated: the banner's "details" must always land somewhere.
        assertNull(Route.Containers.requiredCapability())
        assertTrue(Route.Containers.visibleOn(Capabilities.EMPTY, shared = true))
    }

    @Test fun satellitesIsASettingsTabOnNoLauncher() {
        // The web's Settings → Satellites (2026-10-11): the route is how a
        // link names it, and the shell opens Settings on that tab.
        assertEquals("satellites", settingsTabFor(Route.Satellites))
        Route.entries.filter { it != Route.Satellites }.forEach { assertNull(it.name, settingsTabFor(it)) }
        assertFalse(Route.Satellites in WorkspaceRoutes)
        assertFalse(Route.Satellites in EverythingRoutes)
        assertFalse(Route.Satellites in DefaultPhoneBar)
        assertFalse(Route.Satellites in PhoneBar.Choices)
    }

    @Test fun sharedScreenLeavesPersonalScreensOffEveryLauncher() {
        // web/static/components.jsx SHARED_SCREEN_HIDDEN: people, chat, files, news.
        assertEquals(setOf(Route.People, Route.Chat, Route.Files, Route.News), SharedScreenHidden)
        SharedScreenHidden.forEach {
            assertTrue(it.visibleOn(Capabilities.EMPTY, shared = false))
            assertFalse(it.visibleOn(Capabilities.EMPTY, shared = true))
        }
        // The default bottom bar keeps three tabs on a shared screen.
        assertEquals(
            listOf(Route.Home, Route.Music, Route.Videos),
            PhoneBar.routes(null, Capabilities.EMPTY, shared = true),
        )
        // Capability gating still applies on top.
        assertFalse(Route.Stations.visibleOn(Capabilities.EMPTY, shared = false))
        assertTrue(Route.Stations.visibleOn(caps(CAP_STATIONS), shared = true))
    }
}
