package com.domovoi.app.ui.screens.podcasts

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Podcasts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

@Composable
fun PodcastsScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val subs = rememberApi("podcast-subs", eventTypes = setOf("podcasts.changed")) {
        it.api.get("/api/podcasts/subscriptions").decode<List<PodcastSubscription>>()
    }

    var selected by remember { mutableStateOf<PodcastSubscription?>(null) }
    var showSubscribe by remember { mutableStateOf(false) }
    var resume by remember { mutableStateOf<ResumeRequest?>(null) }
    var polling by remember { mutableStateOf(false) }

    fun poll() {
        if (polling) return
        polling = true
        scope.launch {
            runCatching { app.api.post("/api/podcasts/poll").jsonObject }
                .onSuccess { r ->
                    val downloaded = r["downloaded"]?.jsonPrimitive?.intOrNull ?: 0
                    val fresh = r["new"]?.jsonPrimitive?.intOrNull ?: 0
                    toast("Polled — $downloaded downloaded, $fresh new")
                    subs.refresh()
                }
                .onFailure { toast("Poll failed (offline?)") }
            polling = false
        }
    }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            PageHeader(
                "Podcasts",
                "subscriptions + episodes, played here or cast to a room",
                actions = {
                    OutlinedButton(onClick = { poll() }, enabled = !polling) {
                        Text(if (polling) "polling…" else "poll now")
                    }
                    Button(onClick = { showSubscribe = true }) { Text("subscribe") }
                },
            )
            ListeningAsChip(Modifier.padding(top = 8.dp))
            Spacer(Modifier.height(12.dp))

            when {
                subs.data == null && subs.loading -> LoadingState()
                subs.data == null && subs.error != null ->
                    ErrorState(subs.error ?: "request failed", subs.refresh)
                subs.data.isNullOrEmpty() -> EmptyState(
                    "no subscriptions yet",
                    "subscribe to a podcast by RSS URL or search by name",
                    action = {
                        Button(onClick = { showSubscribe = true }) { Text("subscribe") }
                    },
                )
                else -> LazyVerticalGrid(
                    columns = GridCells.Adaptive(220.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    items(subs.data.orEmpty(), key = { it.id }) { sub ->
                        SubscriptionCard(sub) { selected = sub }
                    }
                }
            }
        }

        selected?.let { sub ->
            key(sub.id) {
                EpisodeOverlay(
                    sub = sub,
                    onClose = { selected = null },
                    onUnsubscribed = {
                        selected = null
                        subs.refresh()
                    },
                    onPlay = { ep -> playEpisode(app, scope, sub, ep) { resume = it } },
                )
            }
        }
    }

    if (showSubscribe) {
        SubscribeDialog(
            onClose = { showSubscribe = false },
            onDone = {
                showSubscribe = false
                subs.refresh()
            },
        )
    }

    resume?.let { req ->
        ResumeDialog(req, onDismiss = { resume = null }) { startSec ->
            app.player.playItems(listOf(req.item), 0, startSec, req.speed)
            resume = null
        }
    }
}

private fun playEpisode(
    app: AppContainer,
    scope: CoroutineScope,
    sub: PodcastSubscription,
    ep: PodcastEpisode,
    onResumePrompt: (ResumeRequest) -> Unit,
) {
    if (!ep.has_file) return
    val item = PlayItem.fromEpisode(
        id = ep.id,
        title = ep.title ?: "episode",
        show = sub.title,
        durationSec = ep.duration_sec,
        artwork = sub.artwork,
        chapters = ep.chapters.map { Chapter(it.title ?: "", it.start_sec) },
    )
    scope.launch {
        val (pos, speed) = app.player.fetchPosition(item)
        if (pos > 5) onResumePrompt(ResumeRequest(item, pos, speed))
        else app.player.playItems(listOf(item), 0, 0.0, speed)
    }
}

@Composable
private fun SubscriptionCard(sub: PodcastSubscription, onClick: () -> Unit) {
    DomovoiCard(modifier = Modifier.fillMaxWidth().clickable(onClick = onClick), padding = 12) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            PodArt(sub.artwork, 48)
            Column(Modifier.weight(1f)) {
                Text(
                    sub.title ?: sub.feed_url ?: "podcast",
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                sub.author?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Text(
                    "${sub.downloaded_count}/${sub.episode_count} downloaded",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
    }
}

/** Artwork square with a podcast-glyph fallback (web PodArt). */
@Composable
internal fun PodArt(url: String?, size: Int) {
    val app = LocalApp.current
    val shape = RoundedCornerShape(8.dp)
    if (url.isNullOrBlank()) {
        Box(
            Modifier.size(size.dp)
                .background(Domovoi.colors.sunken, shape)
                .border(1.dp, Domovoi.colors.border, shape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Outlined.Podcasts,
                contentDescription = null,
                tint = Domovoi.colors.fgSubtle,
                modifier = Modifier.size((size * 0.45).dp),
            )
        }
    } else {
        AsyncImage(
            model = app.api.absolute(url),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.size(size.dp)
                .clip(shape)
                .border(1.dp, Domovoi.colors.border, shape),
        )
    }
}

/** "listening as" hint chip — the full selector lives in Settings > Connection. */
@Composable
internal fun ListeningAsChip(modifier: Modifier = Modifier) {
    val app = LocalApp.current
    val listenerId by app.prefs.listenerPersonId.collectAsState()
    val id = listenerId ?: return
    val people = rememberApi("people-hint") { it.api.get("/api/people").decode<List<PersonRow>>() }
    val name = people.data?.firstOrNull { it.id.toString() == id }?.name
    Box(modifier) { Pill("listening as ${name ?: "…"}", Tone.Brand) }
}

/** A spoken item with a saved position, waiting on a resume/start-over choice. */
internal class ResumeRequest(val item: PlayItem, val positionSec: Double, val speed: Float)

@Composable
internal fun ResumeDialog(
    req: ResumeRequest,
    onDismiss: () -> Unit,
    onPlay: (resumeSec: Double) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(req.item.title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Text(
                "You were at ${fmtDur(req.positionSec)}.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
        },
        confirmButton = {
            TextButton(onClick = { onPlay(req.positionSec) }) {
                Text("resume", color = Domovoi.colors.brand)
            }
        },
        dismissButton = {
            TextButton(onClick = { onPlay(0.0) }) {
                Text("start over", color = Domovoi.colors.fgMuted)
            }
        },
    )
}
