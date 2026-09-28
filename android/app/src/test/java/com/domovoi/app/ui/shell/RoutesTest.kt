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
        // Compact nav: the web phone strip, left to right; everything else
        // lives on Home's "everything" grid.
        assertEquals(
            listOf(Route.Home, Route.Music, Route.Satellites, Route.Calendar, Route.Chat),
            CompactRoutes,
        )
        val reachable = (CompactRoutes + EverythingRoutes).toSet()
        assertEquals(Route.entries.toSet(), reachable)
        assertEquals(CompactRoutes.size + EverythingRoutes.size, reachable.size) // no duplicates
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

    @Test fun sharedScreenLeavesPersonalScreensOffEveryLauncher() {
        // web/static/components.jsx SHARED_SCREEN_HIDDEN: people, chat, files, news.
        assertEquals(setOf(Route.People, Route.Chat, Route.Files, Route.News), SharedScreenHidden)
        SharedScreenHidden.forEach {
            assertTrue(it.visibleOn(Capabilities.EMPTY, shared = false))
            assertFalse(it.visibleOn(Capabilities.EMPTY, shared = true))
        }
        // The bottom bar keeps four tabs on a shared screen.
        assertEquals(
            listOf(Route.Home, Route.Music, Route.Satellites, Route.Calendar),
            CompactRoutes.filter { it.visibleOn(Capabilities.EMPTY, shared = true) },
        )
        // Capability gating still applies on top.
        assertFalse(Route.Stations.visibleOn(Capabilities.EMPTY, shared = false))
        assertTrue(Route.Stations.visibleOn(caps(CAP_STATIONS), shared = true))
    }
}
