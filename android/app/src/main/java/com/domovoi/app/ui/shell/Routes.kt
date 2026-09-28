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
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.ui.graphics.vector.ImageVector
import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities

/**
 * Route table — mirrors the web hash router's routes. Home is where the
 * app opens and where it falls back to, as `#home` is on the web; the
 * top-left brand (the "domovoi" crumb, the rail glyph, the drawer's brand
 * row) leads back to it. The web sidebar order is preserved; Settings
 * comes from Home's "everything" grid (and the rail/drawer), Manual from
 * the grid and Settings > About.
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
    Satellites("Satellites", Icons.Filled.CellTower),
    Calendar("Calendar", Icons.Filled.CalendarMonth),
    Stations("Stations", Icons.Filled.Radio),
    Files("Files", Icons.Filled.Folder),
    Settings("Settings", Icons.Filled.Settings),
    Manual("Manual", Icons.Filled.HelpOutline),
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

/** True when the server's capability manifest allows this route. */
fun Route.visibleWith(caps: Capabilities): Boolean =
    requiredCapability()?.let { caps.has(it) } ?: true

/**
 * Pages a shared screen (an admin marks the device in the dashboard's
 * Settings → Devices; the kitchen tablet) leaves off EVERY launcher — the
 * bottom bar, the rail, the drawer and Home's "everything" grid — the web's
 * SHARED_SCREEN_HIDDEN (web/static/components.jsx): one person's things.
 * Presentational, like the rest of the shared view: the screens still
 * render if something navigates to them, and the tablet still holds the
 * household token.
 */
val SharedScreenHidden: Set<Route> = setOf(Route.People, Route.Chat, Route.Files, Route.News)

/** True when a launcher should offer this route: the capability manifest
 *  allows it and, on a shared screen, it is not somebody's own page. */
fun Route.visibleOn(caps: Capabilities, shared: Boolean): Boolean =
    visibleWith(caps) && !(shared && this in SharedScreenHidden)

/** Everything shown in the web sidebar "workspace" section, in order.
 *  Home is not in it: in the drawer, as on the web desktop, the brand row
 *  is the link home. Filter with [visibleOn] before rendering. */
val WorkspaceRoutes = listOf(
    Route.Chat, Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos,
    Route.Images, Route.News, Route.People, Route.Satellites, Route.Calendar,
    Route.Stations, Route.Files,
)

/** Bottom navigation (compact width): the web phone strip's five tabs,
 *  left to right — home · music · satellites · calendar · chat. */
val CompactRoutes = listOf(Route.Home, Route.Music, Route.Satellites, Route.Calendar, Route.Chat)

/**
 * Destinations on Home's "everything" grid, which is the compact width's
 * "more" menu — in the web grid's order (nav order: Stations is the radio
 * plugin's nav_order 50, after Files), then Settings and the manual, which
 * have no bottom-bar tab. While one of these is open, the home tab is the
 * one lit.
 */
val EverythingRoutes = listOf(
    Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Images, Route.News,
    Route.People, Route.Files, Route.Stations, Route.Settings, Route.Manual,
)
