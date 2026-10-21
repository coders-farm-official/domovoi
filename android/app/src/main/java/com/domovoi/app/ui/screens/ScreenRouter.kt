package com.domovoi.app.ui.screens

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PluginScreenFrame
import com.domovoi.app.ui.screens.audiobooks.AudiobooksScreen
import com.domovoi.app.ui.screens.audiobooks.SavedBooksScreen
import com.domovoi.app.ui.screens.calendar.CalendarScreen
import com.domovoi.app.ui.screens.chat.ChatScreen
import com.domovoi.app.ui.screens.containers.ContainersScreen
import com.domovoi.app.ui.screens.files.FilesScreen
import com.domovoi.app.ui.screens.home.HomeScreen
import com.domovoi.app.ui.screens.manual.ManualScreen
import com.domovoi.app.ui.screens.music.MusicScreen
import com.domovoi.app.ui.screens.news.NewsScreen
import com.domovoi.app.ui.screens.people.PeopleScreen
import com.domovoi.app.ui.screens.podcasts.PodcastsScreen
import com.domovoi.app.ui.screens.satellites.SatellitesScreen
import com.domovoi.app.ui.screens.settings.SettingsScreen
import com.domovoi.app.ui.screens.stations.StationsScreen
import com.domovoi.app.ui.screens.images.ImagesScreen
import com.domovoi.app.ui.screens.local.DevicePage
import com.domovoi.app.ui.screens.local.SourceSwitchPage
import com.domovoi.app.ui.screens.local.YourDeviceFiles
import com.domovoi.app.ui.screens.local.YourDeviceMusic
import com.domovoi.app.ui.screens.local.YourDeviceVideos
import com.domovoi.app.ui.screens.videos.VideosScreen
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.SidebarCounts
import com.domovoi.app.ui.shell.unavailableNote
import com.domovoi.app.ui.shell.visibleWith

/** [counts] is the shell's one sidebar-counts read, handed to Home's
 *  "everything" grid for its badges rather than fetched a second time. */
@Composable
fun ScreenRouter(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts = SidebarCounts()) {
    // Capability-gated routes (design §8): render only when the server's
    // manifest allows them — reachable-but-hidden states (deep link, race
    // with the manifest fetch) get a quiet placeholder, never a dead screen.
    val caps = LocalCapabilities.current
    if (!route.visibleWith(caps)) {
        val (title, body) = route.unavailableNote(caps)
        DomovoiCard(Modifier.fillMaxWidth().padding(16.dp)) {
            EmptyState(title, body)
        }
        return
    }
    when (route) {
        Route.Home -> HomeScreen(navigate, counts)
        Route.Chat -> ChatScreen()
        // Music, Videos, Audiobooks and Files carry the server / "your device" switch.
        Route.Music -> SourceSwitchPage(DevicePage.Music, server = { MusicScreen() }, device = { YourDeviceMusic() })
        Route.Podcasts -> PodcastsScreen()
        // Audiobooks' device side: the books saved on this phone.
        Route.Audiobooks -> SourceSwitchPage(DevicePage.Audiobooks, server = { AudiobooksScreen() }, device = { SavedBooksScreen() })
        Route.Videos -> SourceSwitchPage(DevicePage.Videos, server = { VideosScreen() }, device = { YourDeviceVideos() })
        // Screens a plugin provides sit in its frame (ui/components/PluginFrame.kt):
        // its name, publisher and colour, as the dashboard frames its pages.
        Route.Images -> PluginScreenFrame(CAP_IMAGEGEN, route.icon) { ImagesScreen() }
        Route.News -> NewsScreen()
        Route.People -> PeopleScreen()
        Route.Satellites -> SatellitesScreen()
        Route.Calendar -> CalendarScreen()
        Route.Stations -> PluginScreenFrame(CAP_STATIONS, route.icon) { StationsScreen() }
        Route.Files -> SourceSwitchPage(DevicePage.Files, server = { FilesScreen() }, device = { YourDeviceFiles() })
        Route.Settings -> SettingsScreen(navigate)
        Route.Manual -> ManualScreen(navigate)
        Route.Containers -> ContainersScreen()
    }
}
