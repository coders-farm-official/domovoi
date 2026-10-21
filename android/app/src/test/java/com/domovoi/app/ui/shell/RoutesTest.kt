package com.domovoi.app.ui.shell

import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.FEATURE_AI
import com.domovoi.app.net.serverChatOn
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.After
import org.junit.Test
import com.domovoi.app.ui.screens.home.everythingTiles

/** Route visibility is data-driven by the capability manifest (design section 8). */
class RoutesTest {

    @After fun restore() {
        onDeviceAssistant = { com.domovoi.app.assistant.AssistantSupport.available }
    }

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

    private val aiOff = Capabilities(features = mapOf(FEATURE_AI to false))
    private val aiOn = Capabilities(features = mapOf(FEATURE_AI to true))

    /** The household's AI features off, on a phone without the engine:
     *  no place to chat, so no launcher offers Chat. Every other screen
     *  stays, plugin gates still apply on top (One Chat keeps Chat for the
     *  phone's own chats when the engine is there: chatStaysWhileOnePlaceWorks). */
    @Test fun chatFollowsTheServersAiSwitch() {
        onDeviceAssistant = { false }
        assertFalse(Route.Chat.visibleWith(aiOff))
        assertFalse(Route.Chat.visibleOn(aiOff, shared = false))
        // features.chat is the voice chat mode, not this switch.
        assertTrue(Route.Chat.visibleWith(Capabilities(features = mapOf("chat" to false))))
        Route.entries.filter { it != Route.Chat }.forEach {
            assertEquals(it.name, it.visibleWith(Capabilities.EMPTY), it.visibleWith(aiOff))
        }
        val radioAiOff = Capabilities(
            plugins = listOf(CapabilityPlugin(slug = "p", androidCapabilities = listOf(CAP_STATIONS))),
            features = mapOf(FEATURE_AI to false),
        )
        assertTrue(Route.Stations.visibleWith(radioAiOff))
        assertFalse(Route.Chat.visibleWith(radioAiOff))
        assertFalse(Route.Chat in WorkspaceRoutes.filter { it.visibleOn(aiOff, shared = false) })
        assertFalse(Route.Chat in everythingTiles(aiOff, shared = false))
        assertTrue(StartRoute.visibleWith(aiOff))
    }

    @Test fun aHiddenChatsPlaceholderSaysWhy() {
        assertEquals(
            "chat is turned off" to "this server has its AI features turned off",
            Route.Chat.unavailableNote(aiOff),
        )
        assertEquals("stations isn't available", Route.Stations.unavailableNote(aiOff).first)
        assertEquals("chat isn't available", Route.Chat.unavailableNote(Capabilities.EMPTY).first)
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
        // The web's workspace ends at Files, and so does the app's: the
        // phone's own chats are part of Chat since One Chat (2026-10-21).
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
        // web/static/components.jsx SHARED_SCREEN_HIDDEN: people, chat, files,
        // news. Chat holds the phone's own chats too, hidden with it.
        assertEquals(setOf(Route.People, Route.Chat, Route.Files, Route.News), SharedScreenHidden)
        onDeviceAssistant = { true }
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

    /** Delta review N7: "this phone" (the local shell, one tap from the
     *  shared view) used to show Files and the phone's own chats on the
     *  kitchen tablet; it leaves them off as the workspace's launchers do. */
    @Test fun aSharedScreensLocalShellLeavesFilesAndChatOff() {
        assertEquals(listOf(Route.Music, Route.Videos), localTabs(assistant = true, shared = true))
        assertEquals(listOf(Route.Music, Route.Videos), localTabs(assistant = false, shared = true))
        assertEquals(listOf(Route.Music, Route.Videos, Route.Files, Route.Chat), localTabs(assistant = true, shared = false))
    }

    /** One Chat (C1, 2026-10-21): the separate Assistant route is gone;
     *  the phone's own chats are Chat's, on every launcher Chat is on. */
    @Test fun thereIsOneChatAndNoAssistantRoute() {
        assertEquals(listOf("Chat"), Route.entries.map { it.name }.filter { it == "Chat" || it == "Assistant" })
        assertEquals(1, EverythingRoutes.count { it == Route.Chat })
        assertEquals(1, WorkspaceRoutes.count { it == Route.Chat })
        assertTrue(Route.Chat in PhoneBar.Choices)
        assertTrue(Route.Chat in DefaultPhoneBar)
        assertNull(Route.Chat.requiredCapability())
    }

    /**
     * Chat needs one place to chat in. A server whose AI is off
     * (`features.ai` false, the AI-optional switch) offers no server chat;
     * with the engine on the phone Chat stays, for the phone's chats, and
     * without it Chat is on no launcher. A server that says nothing about AI
     * (older) counts as on.
     */
    @Test fun chatStaysWhileOnePlaceWorks() {
        val aiOff = Capabilities(features = mapOf(FEATURE_AI to false))
        val aiOn = Capabilities(features = mapOf(FEATURE_AI to true))
        assertTrue(Capabilities.EMPTY.serverChatOn())
        assertTrue(aiOn.serverChatOn())
        assertFalse(aiOff.serverChatOn())

        onDeviceAssistant = { true }
        assertTrue(Route.Chat.visibleWith(aiOff))
        assertTrue(Route.Chat in everythingTiles(aiOff, shared = false, bar = listOf(Route.Home)))

        onDeviceAssistant = { false }
        assertTrue(Route.Chat.visibleWith(Capabilities.EMPTY))
        assertTrue(Route.Chat.visibleWith(aiOn))
        assertFalse(Route.Chat.visibleWith(aiOff))
        assertFalse(Route.Chat in everythingTiles(aiOff, shared = false, bar = listOf(Route.Home)))
        assertEquals(listOf(Route.Home, Route.Music, Route.Videos), PhoneBar.routes(null, aiOff, shared = false))
    }

    /** Books saved on the phone play with no server: the local shell gets
     *  an Audiobooks tab once there is one, after the phone's files. */
    @Test fun savedBooksGiveTheLocalShellAnAudiobooksTab() {
        assertEquals(listOf(Route.Music, Route.Videos, Route.Files), localTabs(assistant = false))
        assertEquals(
            listOf(Route.Music, Route.Videos, Route.Files, Route.Audiobooks, Route.Chat),
            localTabs(assistant = true, savedBooks = true),
        )
        // Not somebody's own page: a shared screen keeps it.
        assertEquals(listOf(Route.Music, Route.Videos, Route.Audiobooks), localTabs(assistant = true, shared = true, savedBooks = true))
    }

    @Test fun theLocalShellHasChatWithThePhonesChats() {
        // No server needed: Chat, with the phone's chats only, after the
        // phone's media; where the install has no engine there is no tab.
        assertEquals(listOf(Route.Music, Route.Videos, Route.Files, Route.Chat), localTabs(assistant = true))
        assertEquals(listOf(Route.Music, Route.Videos, Route.Files), localTabs(assistant = false))
        // On the JVM there is no engine: the default asks AssistantSupport.
        assertFalse(Route.Chat in localTabs())
    }
}
