package com.domovoi.app.ui.shell

import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.FEATURE_AI
import com.domovoi.app.ui.screens.home.everythingTiles
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The household's "AI features" switch (`features.ai`, AI optional) governs
 * the home server's chat only. Off: no launcher offers the server's Chat.
 * The on-device assistant (this phone's own chats, opt-in) stays where the
 * install has its engine. Absent (an older server, a manifest that failed
 * to load) and true keep Chat.
 */
class AiOffChatTest {
    private val aiOff = Capabilities(features = mapOf(FEATURE_AI to false))
    private val aiOn = Capabilities(features = mapOf(FEATURE_AI to true))
    private val before = onDeviceAssistant

    @After fun restore() {
        onDeviceAssistant = before
    }

    @Test fun theServersChatFollowsItsAiSwitch() {
        onDeviceAssistant = { false }
        assertTrue(Route.Chat.visibleWith(Capabilities.EMPTY))
        assertTrue(Route.Chat.visibleWith(aiOn))
        assertFalse(Route.Chat.visibleWith(aiOff))
        assertFalse(Route.Chat.visibleOn(aiOff, shared = false))
        // features.chat is the voice chat mode, not this switch.
        assertTrue(Route.Chat.visibleWith(Capabilities(features = mapOf("chat" to false))))
        // Every other screen is as it was; plugin gates still apply on top.
        Route.entries.filter { it != Route.Chat }.forEach {
            assertEquals(it.name, it.visibleWith(Capabilities.EMPTY), it.visibleWith(aiOff))
        }
        val radioAiOff = Capabilities(
            plugins = listOf(CapabilityPlugin(slug = "p", androidCapabilities = listOf(CAP_STATIONS))),
            features = mapOf(FEATURE_AI to false),
        )
        assertTrue(Route.Stations.visibleWith(radioAiOff))
        assertFalse(Route.Chat.visibleWith(radioAiOff))
        // Off every launcher: the drawer/rail list, Home's grid, the bar.
        assertFalse(Route.Chat in WorkspaceRoutes.filter { it.visibleOn(aiOff, shared = false) })
        assertFalse(Route.Chat in everythingTiles(aiOff, shared = false))
        assertFalse(Route.Chat in everythingTiles(aiOff, shared = false, bar = listOf(Route.Home, Route.Music)))
        assertTrue(Route.Chat in everythingTiles(aiOn, shared = false, bar = listOf(Route.Home, Route.Music)))
        assertEquals(listOf(Route.Home, Route.Music, Route.Videos), PhoneBar.routes(null, aiOff, shared = false))
        // Home, where the shell lands when the open route stops existing, stays.
        assertTrue(StartRoute.visibleWith(aiOff))
    }

    /** ONE-CHAT MERGE: delete this test. One Chat deletes Route.Assistant,
     *  and its RoutesTest.chatStaysWhileOnePlaceWorks covers the same rule
     *  (with the engine on the phone, Chat stays for the phone's chats). */
    @Test fun thePhonesOwnAssistantIgnoresTheServersAiSwitch() {
        onDeviceAssistant = { true }
        assertTrue(Route.Assistant.visibleWith(aiOff))
        assertTrue(Route.Assistant in everythingTiles(aiOff, shared = false))
        assertTrue(Route.Assistant in localTabs())
        assertEquals(
            listOf(Route.Home, Route.Assistant, Route.Music),
            PhoneBar.routes(listOf(Route.Home, Route.Chat, Route.Assistant, Route.Music), aiOff, shared = false),
        )
        // The server's Chat still goes: the switch is the server's.
        assertFalse(Route.Chat.visibleWith(aiOff))
        onDeviceAssistant = { false }
        assertFalse(Route.Assistant.visibleWith(aiOn))
    }

    @Test fun aHiddenChatsPlaceholderSaysWhy() {
        assertEquals(
            "chat is turned off" to "this server has its AI features turned off",
            Route.Chat.unavailableNote(aiOff),
        )
        assertEquals("stations isn't available", Route.Stations.unavailableNote(aiOff).first)
        assertEquals("chat isn't available", Route.Chat.unavailableNote(Capabilities.EMPTY).first)
    }
}
