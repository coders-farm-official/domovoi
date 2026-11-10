package com.domovoi.app.ui.shell

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.PermMedia
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Newspaper
import androidx.compose.material.icons.filled.Podcasts
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.ui.graphics.vector.ImageVector
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities

/**
 * Route table — mirrors the web hash router's routes. Home is where the
 * app opens and where it falls back to, as `#home` is on the web; the
 * top-left brand (the "domovoi" crumb, the rail glyph, the drawer's brand
 * row) leads back to it. The web sidebar order is preserved, grouped as
 * the web groups it ([DefaultNav], [navTree]); Settings comes from Home's
 * "everything" grid (and the rail/drawer), Manual from the grid and
 * Settings > About & updates. Satellites is a Settings tab since
 * 2026-10-11 (the web's Settings → Rooms), and People since the nav
 * regroup (Settings → Household → People): [Route.Satellites] and
 * [Route.People] stay as the way a link names them, and the shell opens
 * Settings on that section ([settingsTabFor]).
 *
 * The table stays a compiled-in enum, but *visibility* is data-driven
 * (design §8): routes backed by a plugin declare a required capability
 * and only render when `/api/capabilities` lists it.
 */
enum class Route(val label: String, val icon: ImageVector) {
    Home("Home", Icons.Filled.Home),
    Chat("Chat", Icons.AutoMirrored.Filled.Chat),
    Music("Music", Icons.Filled.MusicNote),
    Podcasts("Podcasts", Icons.Filled.Podcasts),
    Audiobooks("Audiobooks", Icons.AutoMirrored.Filled.MenuBook),
    Videos("Videos", Icons.Filled.Movie),
    Images("Images", Icons.Filled.Image),
    News("News", Icons.Filled.Newspaper),

    /** Not on the drawer, rail or grid since the nav regroup: navigating
     *  here opens Settings → Household → People ([settingsTabFor]), as the
     *  web's old #people does. Still a bar choice ([PhoneBar.Choices]), so
     *  a bar saved with it keeps its tab. */
    People("People", Icons.Filled.Groups),

    /** Not a screen of its own any more: navigating here opens Settings on
     *  its Rooms section ([settingsTabFor]), as the web's old #satellites
     *  opens Settings → Rooms. On no launcher ([LauncherlessRoutes]). */
    Satellites("Rooms", Icons.Filled.CellTower),
    Calendar("Calendar", Icons.Filled.CalendarMonth),
    Stations("Stations", Icons.Filled.Radio),
    Files("Files", Icons.Filled.Folder),
    Settings("Settings", Icons.Filled.Settings),
    Manual("Manual", Icons.Filled.HelpOutline),

    /** The container guard's details, part of Settings → Health since
     *  2026-10-21 (navigating here opens that section, [settingsTabFor], as
     *  the web's old #containers does): reached from the banner the shell
     *  shows while an incident is open and from an incident's notification,
     *  never from a launcher ([LauncherlessRoutes]). */
    Containers("Containers", Icons.Filled.Shield),
}

/** Where the app opens, and where it lands when a route stops existing. */
val StartRoute = Route.Home

/** Capability a route needs before it renders; null = always visible. */
fun Route.requiredCapability(): String? = when (this) {
    Route.Stations -> CAP_STATIONS
    // The Images (generation) screen belongs to the Image Generation
    // plugin — visible only when the connected domovoi has it installed.
    Route.Images -> CAP_IMAGEGEN
    else -> null
}

/** Whether this phone can start a chat on itself: the on-device engine (a
 *  64-bit phone and APK) and the memory for its model
 *  ([AssistantSupport.phoneCanChat]): Chat's "on this phone" place; a hook
 *  so tests can say either. */
internal var onDeviceAssistant: () -> Boolean = { AssistantSupport.phoneCanChat }

/**
 * Whether Chat is offered: at least one place can start a chat. The home
 * server can while its AI features are on ([Capabilities.aiEnabled]; the
 * server is in reach wherever this is asked with its manifest); this phone
 * can where [phoneCanChat]. The AI switch governs the home-server place
 * only: with it off, Chat stays for this phone's own chats.
 */
fun chatAvailable(caps: Capabilities, phoneCanChat: Boolean = onDeviceAssistant()): Boolean =
    caps.aiEnabled || phoneCanChat

/** True when the server's capability manifest allows this route; Chat
 *  needs one place that can start a chat ([chatAvailable]). */
fun Route.visibleWith(caps: Capabilities): Boolean =
    (requiredCapability()?.let { caps.has(it) } ?: true) &&
        (this != Route.Chat || chatAvailable(caps))

/**
 * Pages a shared screen (an admin marks the device in the dashboard's
 * Settings → Phones & browsers; the kitchen tablet) leaves off EVERY launcher — the
 * bottom bar, the rail, the drawer and Home's "everything" grid — the web's
 * SHARED_SCREEN_HIDDEN (web/static/components.jsx): one person's things.
 * Chat holds the server's chats and this device's own; if something opens
 * it anyway, the device's own chats stay hidden there (ChatPlace.kt).
 * Presentational, like the rest of the shared view: the screens still
 * render if something navigates to them, and the tablet still holds the
 * household token.
 */
val SharedScreenHidden: Set<Route> = setOf(Route.People, Route.Chat, Route.Files, Route.News)

/** True when a launcher should offer this route: the capability manifest
 *  allows it and, on a shared screen, it is not somebody's own page. */
fun Route.visibleOn(caps: Capabilities, shared: Boolean): Boolean =
    visibleWith(caps) && !(shared && this in SharedScreenHidden)

/** Routes no launcher offers (not the bottom bar, the rail, the drawer or
 *  Home's grid): the Containers screen, which the banner and a container
 *  incident's notification open, as the web's banner opens #settings/health/containers;
 *  and Satellites, a Settings tab, which Settings and Home's rooms open. */
val LauncherlessRoutes: Set<Route> = setOf(Route.Containers, Route.Satellites)

/** Routes that are a Settings section, by the section's key ([settingsTabFor]):
 *  Satellites is Settings → Rooms, and the container guard's details are
 *  part of Settings → Health (both since 2026-10-21, the web's regroup);
 *  People is Settings → Household → People (the nav regroup). */
private val SettingsTabRoutes: Map<Route, String> = mapOf(
    Route.Satellites to "rooms",
    Route.Containers to "health",
    Route.People to "people",
)

/** The Settings tab [route] stands for, or null for a screen of its own:
 *  navigating to such a route opens Settings on that tab (the web's
 *  SETTINGS_TAB_ROUTES / settingsTabRoute). */
fun settingsTabFor(route: Route): String? = SettingsTabRoutes[route]

/** Everything shown in the web sidebar "workspace" section, in its default
 *  order: Chat, then Media's members (the radio plugin's Stations and the
 *  Image Generation plugin's Images after the core ones), then Tools'.
 *  This order is each route's default sort position ([navTree]). Home is
 *  not in it: in the drawer, as on the web desktop, the brand row is the
 *  link home. People is not either: it is a Settings section. Filter with
 *  [visibleOn] before rendering, then [navTree] for the groups. */
val WorkspaceRoutes = listOf(
    Route.Chat,
    Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Stations, Route.Images,
    Route.News, Route.Calendar, Route.Files,
)

/**
 * Each route's key in the navigation's data, the web's route strings
 * (`#music`, …; the plugin pages by their web page key: Stations is the
 * radio plugin's "stations", the Image Generation plugin's "imagegen"): what a nav override ([NavOverride]) names. The
 * one place a [Route] maps to its key.
 */
val NavKeys: Map<Route, String> = mapOf(
    Route.Chat to "chat",
    Route.Music to "music",
    Route.Podcasts to "podcasts",
    Route.Audiobooks to "audiobooks",
    Route.Videos to "videos",
    Route.Images to "imagegen",
    Route.Stations to "stations",
    Route.News to "news",
    Route.Calendar to "calendar",
    Route.Files to "files",
    Route.Home to "home",
    Route.People to "people",
    Route.Settings to "settings",
    Route.Manual to "manual",
)

/** The route a nav key names, or null for one this build does not know. */
fun routeForNavKey(key: String): Route? = NavKeys.entries.firstOrNull { it.value == key }?.key

/** A navigation group as the drawer shows it: its id (the key its open or
 *  shut state is kept under), label and icon, and whether it starts open. */
data class NavGroupInfo(
    val id: String,
    val label: String,
    val icon: ImageVector = Icons.Filled.Apps,
    val openByDefault: Boolean = false,
)

/** The main navigation's default groups and which core route sits in
 *  which: one table, so a rename is a one-word change here. The web keeps
 *  the same (Media open at first, Tools shut). */
data class NavDefaults(val groups: List<NavGroupInfo>, val groupOf: Map<Route, String>)

val DefaultNav = NavDefaults(
    groups = listOf(
        NavGroupInfo("media", "Media", Icons.Filled.PermMedia, openByDefault = true),
        NavGroupInfo("tools", "Tools", Icons.Filled.Build),
    ),
    groupOf = mapOf(
        Route.Music to "media",
        Route.Podcasts to "media",
        Route.Audiobooks to "media",
        Route.Videos to "media",
        Route.News to "tools",
        Route.Calendar to "tools",
        Route.Files to "tools",
    ),
)

/** The groups plugin pages declare (the web manifest's `nav_group`). This
 *  app lists no plugin pages, so its capability-backed routes say theirs
 *  here, as data: Stations (radio) and Images (image generation) are Media. */
val PluginNavGroups: Map<Route, String> = mapOf(
    Route.Stations to "media",
    Route.Images to "media",
)

/** A person's own sidebar, the web's JSON `{groups:[{id,label,items}],
 *  top:[...]}`; items are [NavKeys] values. Nothing edits it yet. */
data class NavOverride(val groups: List<NavOverrideGroup> = emptyList(), val top: List<String> = emptyList())

data class NavOverrideGroup(val id: String, val label: String, val items: List<String> = emptyList())

/** One line of the grouped navigation: a route on its own, or a group of
 *  two or more routes. */
sealed interface NavEntry {
    data class Item(val route: Route) : NavEntry
    data class Group(val group: NavGroupInfo, val routes: List<Route>) : NavEntry
}

/**
 * The one resolver for the grouped navigation (the web's same rules).
 * [visible]: launcher routes already filtered ([visibleOn]: capabilities,
 * shared screen), in their default order, which is each route's sort
 * position. [defaults]: the default groups; [pluginGroups]: the groups
 * plugin pages declare; [override]: a person's own sidebar, or null.
 *
 * - No override: the default groups, each route in its default (or
 *   plugin-declared) group.
 * - An override: its groups (and labels) are the groups; a default group it
 *   leaves out is gone. A route listed in a group goes there, in the listed
 *   order; one listed in `top` is top level (the groups are read first, and
 *   a route's first mention wins). Keys this build does not know, or routes
 *   not visible, are ignored. A route the override does not mention goes to
 *   its default group if that group exists, after the listed ones, else
 *   top level.
 * - Top-level routes and groups are ordered by sort position, a group at
 *   its earliest member's.
 * - Then: a group with one route is that route, flat; a group with none is
 *   not there.
 */
fun navTree(
    visible: List<Route>,
    override: NavOverride? = null,
    defaults: NavDefaults = DefaultNav,
    pluginGroups: Map<Route, String> = PluginNavGroups,
): List<NavEntry> {
    val routes = visible.distinct()
    val position = routes.withIndex().associate { it.value to it.index }
    val defaultGroupOf = { r: Route -> defaults.groupOf[r] ?: pluginGroups[r] }

    val groups: List<NavGroupInfo> = if (override == null) {
        defaults.groups
    } else {
        override.groups.distinctBy { it.id }.map { g ->
            val base = defaults.groups.firstOrNull { it.id == g.id }
            base?.copy(label = g.label) ?: NavGroupInfo(g.id, g.label)
        }
    }
    val members = groups.associate { it.id to mutableListOf<Route>() }
    val top = mutableListOf<Route>()
    val placed = mutableSetOf<Route>()

    if (override != null) {
        for (g in override.groups) {
            val into = members[g.id] ?: continue
            for (key in g.items) {
                val r = routeForNavKey(key) ?: continue
                if (r in position && placed.add(r)) into += r
            }
        }
        for (key in override.top) {
            val r = routeForNavKey(key) ?: continue
            if (r in position && placed.add(r)) top += r
        }
    }
    for (r in routes) {
        if (r in placed) continue
        val into = defaultGroupOf(r)?.let { members[it] }
        if (into != null) into += r else top += r
    }

    val lines = top.map { (position.getValue(it)) to (NavEntry.Item(it) as NavEntry) } +
        groups.mapNotNull { g ->
            val rs = members.getValue(g.id)
            when (rs.size) {
                0 -> null
                1 -> position.getValue(rs[0]) to NavEntry.Item(rs[0])
                else -> rs.minOf { position.getValue(it) } to NavEntry.Group(g, rs.toList())
            }
        }
    return lines.sortedBy { it.first }.map { it.second }
}

/**
 * Which groups are open in the drawer, as this phone keeps them (Prefs
 * `nav_groups_open`, "media=1,tools=0"; the web keeps the same in
 * localStorage). A group never toggled is [NavGroupInfo.openByDefault];
 * arriving on a page opens its group ([openedFor]). Unreadable parts, and
 * ids no group has any more (the retired "listen", "view", "play"), are
 * ignored, never fatal.
 */
object NavGroupsOpen {
    fun decode(raw: String?): Map<String, Boolean> = runCatching {
        raw.orEmpty().split(',').mapNotNull { part ->
            val kv = part.split('=')
            if (kv.size != 2 || kv[0].isBlank()) return@mapNotNull null
            when (kv[1].trim()) {
                "1" -> kv[0].trim() to true
                "0" -> kv[0].trim() to false
                else -> null
            }
        }.toMap()
    }.getOrDefault(emptyMap())

    fun encode(state: Map<String, Boolean>): String =
        state.entries.sortedBy { it.key }.joinToString(",") { "${it.key}=${if (it.value) 1 else 0}" }

    fun isOpen(group: NavGroupInfo, state: Map<String, Boolean>): Boolean = state[group.id] ?: group.openByDefault

    fun toggled(group: NavGroupInfo, state: Map<String, Boolean>): Map<String, Boolean> =
        state + (group.id to !isOpen(group, state))

    /** [state] with the group of [tree] holding [route] open (a group
     *  containing the current page is open); the same map when it already
     *  is, or when the route is in no group. */
    fun openedFor(route: Route, tree: List<NavEntry>, state: Map<String, Boolean>): Map<String, Boolean> {
        val g = tree.filterIsInstance<NavEntry.Group>().firstOrNull { route in it.routes }?.group ?: return state
        return if (isOpen(g, state)) state else state + (g.id to true)
    }
}

/** Bottom navigation (compact width) until someone picks their own (the
 *  phone bar, [PhoneBar]): the web phone strip's default, left to right —
 *  home · music · videos · chat. */
val DefaultPhoneBar = listOf(Route.Home, Route.Music, Route.Videos, Route.Chat)

/**
 * Every destination Home's "everything" grid may hold — the compact
 * width's "more" menu — in the drawer's grouped order ([WorkspaceRoutes]),
 * then Settings and the manual, which are never on the bottom bar. People
 * is not here: it is a Settings section. The grid shows the ones that are
 * not on this phone's bar ([everythingTiles][com.domovoi.app.ui.screens.home.everythingTiles]),
 * under the group headings [navTree] gives them; while one of those is
 * open, the home tab is the one lit.
 */
val EverythingRoutes = WorkspaceRoutes + listOf(Route.Settings, Route.Manual)

/**
 * The local shell's tabs (no server, or none in reach), left to right:
 * the phone's own music, videos and files, and Chat with the chats kept on
 * this phone where this install has the engine ([assistant]). Those work
 * with no server at all; the server's chats are listed again once the
 * server is ([ChatPlace.kt][com.domovoi.app.ui.screens.chat.AWAY_LINE]).
 *
 * On a shared screen ([shared]: the kitchen tablet) the local shell, which
 * "this phone" opens with one tap, leaves off what the workspace's
 * launchers leave off ([SharedScreenHidden]): the tablet's own files and
 * the chats kept on it (delta review N7, 2026-10-16).
 * Presentational, like the rest of the shared view.
 *
 * Audiobooks joins them once a book is saved on the phone ([savedBooks]):
 * the saved books play with no server (audiobooks/BookStore.kt). A phone
 * with none has nothing to show there, so it has no tab.
 */
fun localTabs(assistant: Boolean = onDeviceAssistant(), shared: Boolean = false, savedBooks: Boolean = false): List<Route> =
    (
        listOf(Route.Music, Route.Videos, Route.Files) +
            (if (savedBooks) listOf(Route.Audiobooks) else emptyList()) +
            (if (assistant) listOf(Route.Chat) else emptyList())
        ).filterNot { shared && it in SharedScreenHidden }
