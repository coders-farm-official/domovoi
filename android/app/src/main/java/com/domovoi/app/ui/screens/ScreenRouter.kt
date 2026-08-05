package com.domovoi.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.screens.audiobooks.AudiobooksScreen
import com.domovoi.app.ui.screens.calendar.CalendarScreen
import com.domovoi.app.ui.screens.chat.ChatScreen
import com.domovoi.app.ui.screens.files.FilesScreen
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
import com.domovoi.app.ui.shell.OverflowRoutes
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.visibleWith
import com.domovoi.app.ui.theme.Domovoi

@Composable
fun ScreenRouter(route: Route, navigate: (Route) -> Unit) {
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
        Route.More -> MoreScreen(navigate)
    }
}

/** Compact-width overflow hub for destinations not in the bottom bar. */
@Composable
fun MoreScreen(navigate: (Route) -> Unit) {
    val caps = LocalCapabilities.current
    LazyColumn(
        Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item { PageHeader("More", "everything else in the workspace") }
        items(OverflowRoutes.filter { it.visibleWith(caps) }) { r ->
            DomovoiCard(Modifier.fillMaxWidth().padding(top = 4.dp).clickable { navigate(r) }) {
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Icon(r.icon, contentDescription = r.label, tint = Domovoi.colors.brand, modifier = Modifier.size(20.dp))
                    Text(r.label, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                }
            }
        }
    }
}
