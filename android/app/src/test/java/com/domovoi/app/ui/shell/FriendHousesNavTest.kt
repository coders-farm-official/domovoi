package com.domovoi.app.ui.shell

import com.domovoi.app.net.Capabilities
import com.domovoi.app.testing.ourCaps
import com.domovoi.app.ui.screens.home.everythingTiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Friend houses in the navigation: in Tools beside Lists, on no launcher
 * and not rendered unless the server said it is on ([Capabilities.friendHouses],
 * set by the shell from GET /api/friend-houses), with the inbox's unread
 * count as its badge.
 */
class FriendHousesNavTest {
    private val on = Capabilities.EMPTY.copy(friendHouses = true)
    private val tools = DefaultNav.groups.first { it.id == "tools" }

    @Test fun hiddenEverywhereUntilTheServerSaysOn() {
        assertFalse(Route.FriendHouses.visibleWith(Capabilities.EMPTY))
        assertFalse(Route.FriendHouses.visibleWith(ourCaps()))
        assertFalse(Route.FriendHouses in WorkspaceRoutes.filter { it.visibleOn(Capabilities.EMPTY, false) })
        assertFalse(Route.FriendHouses in everythingTiles(Capabilities.EMPTY, shared = false))
        val saved = listOf(Route.Home, Route.FriendHouses, Route.Music)
        assertEquals(listOf(Route.Home, Route.Music), PhoneBar.routes(saved, Capabilities.EMPTY, shared = false))
        // On: every launcher may offer it, a shared screen included (one
        // inbox for the whole house, not one person's).
        assertTrue(Route.FriendHouses.visibleWith(on))
        assertTrue(Route.FriendHouses.visibleOn(on, shared = true))
        assertTrue(Route.FriendHouses in everythingTiles(on, shared = false))
        assertEquals(saved, PhoneBar.routes(saved, on, shared = false))
        // The flag never comes from the manifest JSON.
        assertFalse(com.domovoi.app.net.DomovoiJson.decodeFromString(Capabilities.serializer(), """{"friendHouses":true}""").friendHouses)
    }

    @Test fun itSitsInToolsBesideLists() {
        assertEquals("tools", DefaultNav.groupOf[Route.FriendHouses])
        assertEquals("friend-houses", NavKeys[Route.FriendHouses])
        assertEquals(Route.FriendHouses, routeForNavKey("friend-houses"))
        assertEquals("Friend houses", Route.FriendHouses.label)
        val tree = navTree(WorkspaceRoutes.filter { it.visibleOn(on, false) })
        val group = tree.filterIsInstance<NavEntry.Group>().first { it.group == tools }
        assertEquals(listOf(Route.News, Route.Calendar, Route.Lists, Route.FriendHouses, Route.Files), group.routes)
        assertNull(Route.FriendHouses.requiredCapability())
        assertNull(settingsTabFor(Route.FriendHouses))
        // A server screen: not in the local shell.
        assertFalse(Route.FriendHouses in localTabs(assistant = true, shared = false, savedBooks = true))
    }

    @Test fun theBadgeIsTheUnreadCount() {
        assertEquals(3, SidebarCounts(friendHouses = 3).forRoute(Route.FriendHouses))
        assertNull(SidebarCounts().forRoute(Route.FriendHouses))
        assertNull(SidebarCounts(friendHouses = 3).forRoute(Route.Lists))
    }
}
