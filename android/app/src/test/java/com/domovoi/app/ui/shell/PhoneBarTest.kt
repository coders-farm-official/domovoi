package com.domovoi.app.ui.shell

import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone bar (web/static/components.jsx phoneBarRoutes / phoneBarEdit,
 * driven by domovoi/tests/test_web_phone_bar.py): home · music · videos ·
 * chat by default, each phone's own pick otherwise — Home first, two to
 * five tabs, and never a personal screen on a shared screen.
 */
class PhoneBarTest {

    private val none = Capabilities.EMPTY
    private val radio = Capabilities(plugins = listOf(CapabilityPlugin(slug = "radio", androidCapabilities = listOf(CAP_STATIONS))))
    private val default = listOf(Route.Home, Route.Music, Route.Videos, Route.Chat)

    @Test fun theDefaultIsHomeMusicVideosChat() {
        assertEquals(default, PhoneBar.routes(null, none, shared = false))
        assertEquals(DefaultPhoneBar, default)
        assertEquals(5, PhoneBar.MAX)
        assertEquals(2, PhoneBar.MIN)
    }

    @Test fun aPhonePicksItsOwnTabsAndOrderPluginScreensIncluded() {
        val saved = listOf(Route.Home, Route.Calendar, Route.Stations, Route.Music)
        assertEquals(saved, PhoneBar.routes(saved, radio, shared = false))
        // The plugin gone from this server: its tab goes, the rest stays.
        assertEquals(listOf(Route.Home, Route.Calendar, Route.Music), PhoneBar.routes(saved, none, shared = false))
    }

    @Test fun homeLeadsDuplicatesDropAndFiveIsTheCap() {
        val saved = listOf(Route.Chat, Route.Chat, Route.People, Route.Files, Route.News, Route.Calendar, Route.Podcasts)
        assertEquals(
            listOf(Route.Home, Route.Chat, Route.People, Route.Files, Route.News),
            PhoneBar.routes(saved, none, shared = false),
        )
    }

    @Test fun onlyLauncherScreensMaySitOnTheBar() {
        // Settings, the manual, Satellites (a Settings tab) and Containers
        // are never tabs: nothing of the saved bar is left but Home, so the
        // default comes back.
        val saved = listOf(Route.Settings, Route.Manual, Route.Satellites, Route.Containers)
        assertEquals(default, PhoneBar.routes(saved, none, shared = false))
    }

    @Test fun aSharedScreenNeverShowsAPersonalTabWhateverTheBarSays() {
        val saved = listOf(Route.Home, Route.Chat, Route.People, Route.Music)
        assertEquals(listOf(Route.Home, Route.Music), PhoneBar.routes(saved, none, shared = true))
        // Nothing left but Home: the default, minus chat.
        assertEquals(
            listOf(Route.Home, Route.Music, Route.Videos),
            PhoneBar.routes(listOf(Route.Chat, Route.People), none, shared = true),
        )
    }

    @Test fun prefsRoundTripAndJunkIsTheDefault() {
        val saved = listOf(Route.Home, Route.Calendar, Route.Music)
        assertEquals(saved, PhoneBar.decode(PhoneBar.encode(saved)))
        assertEquals("[\"Home\",\"Calendar\",\"Music\"]", PhoneBar.encode(saved))
        assertNull(PhoneBar.decode(null))
        assertNull(PhoneBar.decode(""))
        assertNull(PhoneBar.decode("{oops"))
        // A name this build doesn't know (a route renamed or removed) drops.
        assertEquals(listOf(Route.Home, Route.Music), PhoneBar.decode("[\"Home\",\"Hologram\",\"Music\"]"))
        assertEquals(default, PhoneBar.routes(PhoneBar.decode("{oops"), none, shared = false))
    }

    @Test fun theEditRules() {
        val five = listOf(Route.Home, Route.Music, Route.Videos, Route.Chat, Route.Calendar)
        val two = listOf(Route.Home, Route.Music)
        assertEquals(five, PhoneBar.edit(default, PhoneBar.Op.Add, Route.Calendar))
        assertEquals(listOf(Route.Home, Route.Music, Route.Chat), PhoneBar.edit(default, PhoneBar.Op.Remove, Route.Videos))
        assertEquals(listOf(Route.Home, Route.Music, Route.Chat, Route.Videos), PhoneBar.edit(default, PhoneBar.Op.Left, Route.Chat))
        assertEquals(listOf(Route.Home, Route.Videos, Route.Music, Route.Chat), PhoneBar.edit(default, PhoneBar.Op.Right, Route.Music))
        // Past the limits: no edit, the same list back.
        assertSame(five, PhoneBar.edit(five, PhoneBar.Op.Add, Route.Files))
        assertSame(two, PhoneBar.edit(two, PhoneBar.Op.Remove, Route.Music))
        assertSame(default, PhoneBar.edit(default, PhoneBar.Op.Add, Route.Music))
        assertSame(default, PhoneBar.edit(default, PhoneBar.Op.Add, Route.Settings))
        assertSame(default, PhoneBar.edit(default, PhoneBar.Op.Remove, Route.Home))
        assertSame(default, PhoneBar.edit(default, PhoneBar.Op.Left, Route.Music))
        assertSame(default, PhoneBar.edit(default, PhoneBar.Op.Right, Route.Chat))
        assertSame(default, PhoneBar.edit(default, PhoneBar.Op.Right, Route.Home))
    }

    @Test fun everyChoiceIsALauncherScreen() {
        assertEquals(Route.Home, PhoneBar.Choices.first())
        assertTrue(PhoneBar.Choices.drop(1) == WorkspaceRoutes)
        assertTrue(PhoneBar.Choices.none { it in LauncherlessRoutes })
    }

    /** One Chat (C1): a bar saved with the retired Assistant tab gets Chat
     *  in its place, once (Prefs writes the answer back), and nothing else
     *  about it changes. */
    @Test fun aSavedAssistantTabMovesToChatOnce() {
        val moved = PhoneBar.migrate("""["Home","Assistant","Music"]""")
        assertEquals("""["Home","Chat","Music"]""", moved)
        assertEquals(listOf(Route.Home, Route.Chat, Route.Music), PhoneBar.routes(PhoneBar.decode(moved), none, shared = false))
        // Already moved: the same string back, so Prefs writes nothing again.
        assertSame(moved, PhoneBar.migrate(moved))
        // A bar that had both keeps its Chat where it was.
        assertEquals("""["Home","Chat","Music"]""", PhoneBar.migrate("""["Home","Chat","Assistant","Music"]"""))
        assertEquals("""["Home","Music","Chat"]""", PhoneBar.migrate("""["Home","Music","Chat","Assistant"]"""))
        // ...even when the Assistant came first: Chat does not jump forward.
        assertEquals("""["Home","Music","Chat"]""", PhoneBar.migrate("""["Home","Assistant","Music","Chat"]"""))
        // Nothing to move: untouched, including the default and junk.
        val plain = """["Home","Calendar"]"""
        assertSame(plain, PhoneBar.migrate(plain))
        assertNull(PhoneBar.migrate(null))
        assertEquals("not json", PhoneBar.migrate("not json"))
        // Without the move the retired name would just drop out of the bar.
        assertEquals(listOf(Route.Home, Route.Music), PhoneBar.decode("""["Home","Assistant","Music"]"""))
    }
}
