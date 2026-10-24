package com.domovoi.app.ui.shell

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Newspaper
import androidx.compose.material.icons.filled.Podcasts
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
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
 * row) leads back to it. The web sidebar order is preserved; Settings
 * comes from Home's "everything" grid (and the rail/drawer), Manual from
 * the grid and Settings > About & updates. Satellites is a Settings tab since
 * 2026-10-11 (the web's Settings → Rooms): [Route.Satellites] stays as
 * the way a link names it, and the shell opens Settings on that tab
 * ([settingsTabFor]).
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
 *  part of Settings → Health (both since 2026-10-21, the web's regroup). */
private val SettingsTabRoutes: Map<Route, String> = mapOf(Route.Satellites to "rooms", Route.Containers to "health")

/** The Settings tab [route] stands for, or null for a screen of its own:
 *  navigating to such a route opens Settings on that tab (the web's
 *  SETTINGS_TAB_ROUTES / settingsTabRoute). */
fun settingsTabFor(route: Route): String? = SettingsTabRoutes[route]

/** Everything shown in the web sidebar "workspace" section, in order.
 *  Home is not in it: in the drawer, as on the web desktop, the brand row
 *  is the link home. Filter with [visibleOn] before rendering. */
val WorkspaceRoutes = listOf(
    Route.Chat, Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos,
    Route.Images, Route.News, Route.People, Route.Calendar,
    Route.Stations, Route.Files,
)

/** Bottom navigation (compact width) until someone picks their own (the
 *  phone bar, [PhoneBar]): the web phone strip's default, left to right —
 *  home · music · videos · chat. */
val DefaultPhoneBar = listOf(Route.Home, Route.Music, Route.Videos, Route.Chat)

/**
 * Every destination Home's "everything" grid may hold — the compact
 * width's "more" menu — in the web grid's order (nav order: Stations is
 * the radio plugin's nav_order 50, after Files), then Settings and the
 * manual, which are never on the bottom bar. The grid shows the ones that
 * are not on this phone's bar ([everythingTiles][com.domovoi.app.ui.screens.home.everythingTiles]);
 * while one of those is open, the home tab is the one lit.
 */
val EverythingRoutes = listOf(
    Route.Chat, Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos,
    Route.Images, Route.News, Route.People, Route.Calendar, Route.Files,
    Route.Stations, Route.Settings, Route.Manual,
)

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
