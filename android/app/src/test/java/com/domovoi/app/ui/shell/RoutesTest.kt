package com.domovoi.app.ui.shell

import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.FEATURE_AI
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
        // People is a Settings section since the nav regroup: on the bar
        // only if a phone picks it, never on the grid.
        assertEquals(Route.entries.toSet() - LauncherlessRoutes - Route.People, reachable)
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
        // like the web's #settings/health/containers, which is not in the nav.
        assertEquals(setOf(Route.Containers, Route.Satellites), LauncherlessRoutes)
        assertFalse(Route.Containers in DefaultPhoneBar)
        assertFalse(Route.Containers in EverythingRoutes)
        assertFalse(Route.Containers in WorkspaceRoutes)
        // Never gated: the banner's "details" must always land somewhere.
        assertNull(Route.Containers.requiredCapability())
        assertTrue(Route.Containers.visibleOn(Capabilities.EMPTY, shared = true))
    }

    @Test fun satellitesIsASettingsTabOnNoLauncher() {
        // The web's Settings → Rooms (2026-10-11): the route is how a
        // link names it, and the shell opens Settings on that tab.
        assertEquals("rooms", settingsTabFor(Route.Satellites))
        // The container guard's details are part of Settings → Health (2026-10-21).
        assertEquals("health", settingsTabFor(Route.Containers))
        // People is Settings → Household → People (the nav regroup).
        assertEquals("people", settingsTabFor(Route.People))
        Route.entries.filter { it != Route.Satellites && it != Route.Containers && it != Route.People }
            .forEach { assertNull(it.name, settingsTabFor(it)) }
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

    // ─── the nav regroup ─────────────────────────────────────────────────

    private val both = caps(CAP_STATIONS, CAP_IMAGEGEN)
    private val media = DefaultNav.groups.first { it.id == "media" }
    private val tools = DefaultNav.groups.first { it.id == "tools" }

    private fun visible(c: Capabilities, shared: Boolean) = WorkspaceRoutes.filter { it.visibleOn(c, shared) }

    private fun group(t: List<NavEntry>, id: String) =
        t.filterIsInstance<NavEntry.Group>().firstOrNull { it.group.id == id }

    @Test fun theDefaultDrawerIsChatThenMediaThenTools() {
        onDeviceAssistant = { true }
        assertEquals(
            listOf(
                NavEntry.Item(Route.Chat),
                NavEntry.Group(media, listOf(Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Stations, Route.Images)),
                NavEntry.Group(tools, listOf(Route.News, Route.Calendar, Route.Lists, Route.Files)),
            ),
            navTree(visible(both, shared = false)),
        )
        assertEquals(listOf("media" to "Media", "tools" to "Tools"), DefaultNav.groups.map { it.id to it.label })
        assertTrue(media.openByDefault)
        assertFalse(tools.openByDefault)
    }

    @Test fun withoutThePluginsMediaHoldsTheCoreFour() {
        onDeviceAssistant = { true }
        assertEquals(
            listOf(Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos),
            group(navTree(visible(Capabilities.EMPTY, false)), "media")!!.routes,
        )
        // The plugin pages' groups are data, a parameter of the resolver.
        assertEquals(mapOf(Route.Stations to "media", Route.Images to "media"), PluginNavGroups)
        val t = navTree(visible(both, false), pluginGroups = mapOf(Route.Stations to "tools"))
        // Members keep the default order: Stations (position 5) leads News (7).
        assertEquals(listOf(Route.Stations, Route.News, Route.Calendar, Route.Lists, Route.Files), group(t, "tools")!!.routes)
        // A plugin group no group has: the page is top level, at its position.
        assertTrue(NavEntry.Item(Route.Images) in t)
    }

    @Test fun aGroupWithOneVisibleItemIsThatItemFlatAndAnEmptyOneGoes() {
        assertEquals(
            listOf(NavEntry.Item(Route.Chat), NavEntry.Item(Route.Music), NavEntry.Item(Route.Files)),
            navTree(listOf(Route.Chat, Route.Music, Route.Files)),
        )
        assertEquals(listOf(NavEntry.Item(Route.Chat)), navTree(listOf(Route.Chat)))
        assertEquals(emptyList<NavEntry>(), navTree(emptyList()))
    }

    @Test fun aSharedScreenDropsPeopleNewsFilesAndChat() {
        onDeviceAssistant = { true }
        assertEquals(
            listOf(
                NavEntry.Group(media, listOf(Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Stations, Route.Images)),
                // Tools keeps Calendar and Lists (the kitchen tablet's
                // shopping list is the point of it).
                NavEntry.Group(tools, listOf(Route.Calendar, Route.Lists)),
            ),
            navTree(visible(both, shared = true)),
        )
    }

    @Test fun anOverrideRenamesMediaAndMovesFilesIntoIt() {
        onDeviceAssistant = { true }
        val o = NavOverride(groups = listOf(
            NavOverrideGroup("media", "Watch & listen", listOf("files", "music")),
            NavOverrideGroup("tools", "Tools", emptyList()),
        ))
        val t = navTree(visible(both, false), o)
        val m = group(t, "media")!!
        assertEquals("Watch & listen", m.group.label)
        // Listed first, in the listed order; the unmentioned members after, in default order.
        assertEquals(
            listOf(Route.Files, Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Stations, Route.Images),
            m.routes,
        )
        // Keeps the default group's icon and open-by-default.
        assertEquals(media.icon, m.group.icon)
        assertTrue(m.group.openByDefault)
        assertEquals(listOf(Route.News, Route.Calendar, Route.Lists), group(t, "tools")!!.routes)
        assertEquals(listOf(NavEntry.Item(Route.Chat), m, group(t, "tools")), t)
    }

    @Test fun anOverrideWithItsOwnGroupAndTopAndAnUnknownKey() {
        onDeviceAssistant = { true }
        val o = NavOverride(
            groups = listOf(
                NavOverrideGroup("media", "Media", listOf("music")),
                NavOverrideGroup("kids", "Kids", listOf("videos", "bogus", "stations", "videos")),
                NavOverrideGroup("tools", "Tools"),
            ),
            top = listOf("calendar", "music", "bogus"),
        )
        val t = navTree(visible(both, false), o)
        val kids = group(t, "kids")!!
        assertEquals(NavGroupInfo("kids", "Kids"), kids.group)
        assertEquals(listOf(Route.Videos, Route.Stations), kids.routes)
        // Music's first mention (Media) wins over top.
        assertEquals(listOf(Route.Music, Route.Podcasts, Route.Audiobooks, Route.Images), group(t, "media")!!.routes)
        assertEquals(listOf(Route.News, Route.Lists, Route.Files), group(t, "tools")!!.routes)
        // Sorted by default position: chat 0, media (music 1), kids (videos 4),
        // tools (news 7), calendar 8.
        assertEquals(
            listOf(NavEntry.Item(Route.Chat), group(t, "media"), kids, group(t, "tools"), NavEntry.Item(Route.Calendar)),
            t,
        )
        // An override's route that is not visible is ignored too.
        val noRadio = navTree(visible(Capabilities.EMPTY, false), o)
        assertEquals(NavEntry.Item(Route.Videos), noRadio.first { it == NavEntry.Item(Route.Videos) })
    }

    @Test fun aDefaultGroupTheOverrideDropsSendsItsMembersTopLevel() {
        onDeviceAssistant = { true }
        // Only Media: Tools is gone, so News, Calendar and Files are top level.
        val o = NavOverride(groups = listOf(NavOverrideGroup("media", "Media")))
        assertEquals(
            listOf(
                NavEntry.Item(Route.Chat),
                NavEntry.Group(media, listOf(Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Stations, Route.Images)),
                NavEntry.Item(Route.News), NavEntry.Item(Route.Calendar), NavEntry.Item(Route.Lists), NavEntry.Item(Route.Files),
            ),
            navTree(visible(both, false), o),
        )
        // An empty override: no groups at all, everything top level in default order.
        assertEquals(
            visible(both, false).map { NavEntry.Item(it) },
            navTree(visible(both, false), NavOverride()),
        )
    }

    // ─── Lists (design-notes/LISTS-MOBILE-HANDOFF.md §4, §7.5) ────────────

    @Test fun listsIsInToolsAfterCalendarByDefault() {
        assertEquals("lists", NavKeys[Route.Lists])
        assertEquals(Route.Lists, routeForNavKey("lists"))
        assertEquals("tools", DefaultNav.groupOf[Route.Lists])
        assertEquals(listOf(Route.News, Route.Calendar, Route.Lists, Route.Files), WorkspaceRoutes.takeLast(4))
        assertEquals(
            listOf(Route.News, Route.Calendar, Route.Lists, Route.Files),
            group(navTree(visible(Capabilities.EMPTY, false)), "tools")!!.routes,
        )
        // A bar choice, as Calendar is; on no launcher of the local shell
        // (no server: nothing to read the lists from).
        assertTrue(Route.Calendar in PhoneBar.Choices)
        assertTrue(Route.Lists in PhoneBar.Choices)
        val saved = listOf(Route.Home, Route.Lists, Route.Music)
        assertEquals(saved, PhoneBar.routes(saved, Capabilities.EMPTY, shared = false))
        listOf(true, false).forEach { a ->
            listOf(true, false).forEach { sh ->
                assertFalse(Route.Lists in localTabs(assistant = a, shared = sh, savedBooks = true))
            }
        }
        assertNull(Route.Lists.requiredCapability())
        assertTrue(Route.Lists.visibleOn(Capabilities.EMPTY, shared = true))
    }

    /** A layout saved before Lists existed never names it. With a Tools
     *  group it lands there after the pages the layout lists (the web's
     *  resolveNavTree); without one, top level at its default place. */
    @Test fun aSavedLayoutThatNeverNamedListsStillShowsIt() {
        onDeviceAssistant = { true }
        val withTools = NavOverride(groups = listOf(
            NavOverrideGroup("media", "Media", listOf("music", "videos")),
            NavOverrideGroup("tools", "Things", listOf("files", "calendar")),
        ))
        val t = navTree(visible(Capabilities.EMPTY, false), withTools)
        assertEquals(listOf(Route.Files, Route.Calendar, Route.News, Route.Lists), group(t, "tools")!!.routes)
        assertEquals("Things", group(t, "tools")!!.group.label)

        val noTools = NavOverride(
            groups = listOf(NavOverrideGroup("media", "Media", listOf("music", "videos", "files"))),
            top = listOf("calendar", "news"),
        )
        val u = navTree(visible(Capabilities.EMPTY, false), noTools)
        assertTrue(u.none { it is NavEntry.Group && Route.Lists in it.routes })
        assertEquals(
            listOf(
                NavEntry.Item(Route.Chat),
                group(u, "media"),
                NavEntry.Item(Route.News), NavEntry.Item(Route.Calendar), NavEntry.Item(Route.Lists),
            ),
            u,
        )
    }

    @Test fun everyWorkspaceRouteHasTheWebsKey() {
        WorkspaceRoutes.forEach { r -> assertEquals(r, routeForNavKey(NavKeys.getValue(r))) }
        assertEquals("stations", NavKeys[Route.Stations])
        assertEquals("imagegen", NavKeys[Route.Images])
        assertNull(routeForNavKey("bogus"))
        assertEquals(NavKeys.size, NavKeys.values.toSet().size)
    }

    @Test fun peopleLeftTheMainNavButStaysABarChoice() {
        assertFalse(Route.People in WorkspaceRoutes)
        assertFalse(Route.People in EverythingRoutes)
        assertTrue(Route.People in PhoneBar.Choices)
        // A bar saved with People keeps the tab; the shell opens Settings → People.
        val saved = listOf(Route.Home, Route.People, Route.Music)
        assertEquals(saved, PhoneBar.routes(saved, Capabilities.EMPTY, shared = false))
        assertEquals("people", settingsTabFor(Route.People))
    }

    @Test fun groupOpenStateIsPerDeviceMediaOpenByDefault() {
        val none = NavGroupsOpen.decode(null)
        assertTrue(NavGroupsOpen.isOpen(media, none))
        assertFalse(NavGroupsOpen.isOpen(tools, none))
        val shut = NavGroupsOpen.toggled(media, none)
        assertFalse(NavGroupsOpen.isOpen(media, NavGroupsOpen.decode(NavGroupsOpen.encode(shut))))
        assertEquals("media=0,tools=1", NavGroupsOpen.encode(mapOf("tools" to true, "media" to false)))
        // Junk, and the retired groups' keys, change nothing.
        val old = NavGroupsOpen.decode("listen=0,view=1,,=1,tools=maybe,nonsense")
        assertTrue(NavGroupsOpen.isOpen(media, old))
        assertFalse(NavGroupsOpen.isOpen(tools, old))
        // A group containing the current page opens; an open one stays as it is.
        val tree = navTree(WorkspaceRoutes)
        assertTrue(NavGroupsOpen.isOpen(tools, NavGroupsOpen.openedFor(Route.Files, tree, none)))
        assertEquals(none, NavGroupsOpen.openedFor(Route.Music, tree, none))
        assertEquals(none, NavGroupsOpen.openedFor(Route.Chat, tree, none))
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
        assertTrue(Capabilities.EMPTY.aiEnabled)
        assertTrue(aiOn.aiEnabled)
        assertFalse(aiOff.aiEnabled)
        assertTrue(chatAvailable(aiOff, phoneCanChat = true))
        assertFalse(chatAvailable(aiOff, phoneCanChat = false))
        assertTrue(chatAvailable(aiOn, phoneCanChat = false))

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

    /** Review round 1: a 64-bit phone under the memory floor can't chat on
     *  itself, so it adds no Chat of its own: none in the local shell, and
     *  none in the workspace while the server's AI is off. */
    @Test fun aPhoneThatCannotRunTheModelAddsNoChat() {
        val small = com.domovoi.app.assistant.PhoneMemory(6L shl 30, lowRamDevice = false)
        val big = com.domovoi.app.assistant.PhoneMemory(8L shl 30, lowRamDevice = false)
        val lowRam = com.domovoi.app.assistant.PhoneMemory(8L shl 30, lowRamDevice = true)
        assertFalse(com.domovoi.app.assistant.AssistantSupport.canChat(small))
        assertFalse(com.domovoi.app.assistant.AssistantSupport.canChat(lowRam))
        assertTrue(com.domovoi.app.assistant.AssistantSupport.canChat(big))
        // The hook is that whole answer (engine and memory): false here.
        onDeviceAssistant = { false }
        assertEquals(listOf(Route.Music, Route.Videos, Route.Files), localTabs())
        assertFalse(Route.Chat.visibleWith(Capabilities(features = mapOf(FEATURE_AI to false))))
        assertTrue(Route.Chat.visibleWith(Capabilities.EMPTY))
    }
}
