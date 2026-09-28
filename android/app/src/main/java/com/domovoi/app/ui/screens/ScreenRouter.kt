package com.domovoi.app.ui.screens

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.screens.audiobooks.AudiobooksScreen
import com.domovoi.app.ui.screens.calendar.CalendarScreen
import com.domovoi.app.ui.screens.chat.ChatScreen
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
import com.domovoi.app.ui.screens.videos.VideosScreen
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.SidebarCounts
import com.domovoi.app.ui.shell.visibleWith

/** [counts] is the shell's one sidebar-counts read, handed to Home's
 *  "everything" grid for its badges rather than fetched a second time. */
@Composable
fun ScreenRouter(route: Route, navigate: (Route) -> Unit, counts: SidebarCounts = SidebarCounts()) {
    // Capability-gated routes (design §8): render only when the server's
    // manifest allows them — reachable-but-hidden states (deep link, race
    // with the manifest fetch) get a quiet placeholder, never a dead screen.
    if (!route.visibleWith(LocalCapabilities.current)) {
        DomovoiCard(Modifier.fillMaxWidth().padding(16.dp)) {
            EmptyState(
                "${route.label.lowercase()} isn't available",
                "the plugin providing this screen isn't installed on this server",
            )
        }
        return
    }
    when (route) {
        Route.Home -> HomeScreen(navigate, counts)
        Route.Chat -> ChatScreen()
        Route.Music -> MusicScreen()
        Route.Podcasts -> PodcastsScreen()
        Route.Audiobooks -> AudiobooksScreen()
        Route.Videos -> VideosScreen()
        Route.Images -> ImagesScreen()
        Route.News -> NewsScreen()
        Route.People -> PeopleScreen()
        Route.Satellites -> SatellitesScreen()
        Route.Calendar -> CalendarScreen()
        Route.Stations -> StationsScreen()
        Route.Files -> FilesScreen()
        Route.Settings -> SettingsScreen(navigate)
        Route.Manual -> ManualScreen()
    }
}
