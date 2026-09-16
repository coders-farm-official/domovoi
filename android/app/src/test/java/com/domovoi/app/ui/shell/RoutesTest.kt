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

    @Test fun navigationTablesCoverEveryDestinationExactlyOnce() {
        // Compact nav: four primaries + More; everything else lives in the More hub.
        assertEquals(Route.More, CompactRoutes.last())
        assertEquals(5, CompactRoutes.size)
        val reachable = (CompactRoutes + OverflowRoutes).toSet()
        val expected = Route.entries.toSet()
        assertEquals(expected, reachable)
        assertEquals(CompactRoutes.size + OverflowRoutes.size, reachable.size) // no duplicates
        // Workspace sidebar keeps the web order and excludes chrome-only routes.
        assertFalse(WorkspaceRoutes.contains(Route.Settings))
        assertFalse(WorkspaceRoutes.contains(Route.Manual))
        assertFalse(WorkspaceRoutes.contains(Route.More))
        assertEquals(Route.Chat, WorkspaceRoutes.first())
        assertEquals(Route.Files, WorkspaceRoutes.last())
    }
}
