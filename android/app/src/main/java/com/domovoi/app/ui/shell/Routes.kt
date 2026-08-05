package com.domovoi.app.ui.shell

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.MoreHoriz
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
 * Route table — mirrors the web hash router (#music default). The web
 * sidebar order is preserved; Settings comes from the topbar gear and
 * Manual from Settings > About.
 *
 * The table stays a compiled-in enum, but *visibility* is data-driven
 * (design §8): routes backed by a plugin declare a required capability
 * and only render when `/api/capabilities` lists it.
 */
enum class Route(val label: String, val icon: ImageVector) {
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
    More("More", Icons.Filled.MoreHoriz), // compact-width overflow hub
}

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

/** Everything shown in the web sidebar "workspace" section, in order.
 *  Filter with [visibleWith] before rendering. */
val WorkspaceRoutes = listOf(
    Route.Chat, Route.Music, Route.Podcasts, Route.Audiobooks, Route.Videos,
    Route.Images, Route.News, Route.People, Route.Satellites, Route.Calendar,
    Route.Stations, Route.Files,
)

/** Bottom navigation (compact width): four primaries + More. */
val CompactRoutes = listOf(Route.Chat, Route.Music, Route.Satellites, Route.Calendar, Route.More)

/** Destinations that live behind the More hub on compact width. */
val OverflowRoutes = listOf(
    Route.Podcasts, Route.Audiobooks, Route.Videos, Route.Images, Route.News,
    Route.People, Route.Stations, Route.Files, Route.Settings, Route.Manual,
)
