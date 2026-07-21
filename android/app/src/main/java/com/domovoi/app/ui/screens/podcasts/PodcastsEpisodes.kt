package com.domovoi.app.ui.screens.podcasts

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.DeviceDownloads
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch

/**
 * Episode list for one subscription (web EpisodeDrawer). Full-screen on
 * compact width; a right-hand panel over a scrim on expanded.
 */
@Composable
internal fun EpisodeOverlay(
    sub: PodcastSubscription,
    onClose: () -> Unit,
    onUnsubscribed: () -> Unit,
    onPlay: (PodcastEpisode) -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val compact = currentWindowAdaptiveInfo()
        .windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT
    var confirmUnsub by remember { mutableStateOf(false) }

    fun saveToDevice(ep: PodcastEpisode) {
        val name = DeviceDownloads.safeName(ep.title ?: "episode-${ep.id}", fallback = "episode") +
            (ep.file_ext ?: ".mp3")
        val err = DeviceDownloads.enqueue(
            context,
            app.api.absolute("/api/podcasts/episodes/${ep.id}/audio?download=1"),
            name,
        )
        toast(err ?: "saving \"$name\" to Downloads/Domovoi")
    }

    val episodes = rememberApi(
        sub.id,
        eventTypes = setOf("podcasts.changed", "podcast_positions.changed"),
    ) {
        it.api.get("/api/podcasts/subscriptions/${sub.id}/episodes")
            .decode<List<PodcastEpisode>>()
    }

    BackHandler(onBack = onClose)

    Box(Modifier.fillMaxSize()) {
        Box(
            Modifier.fillMaxSize()
                .background(Color.Black.copy(alpha = 0.35f))
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = onClose,
                ),
        )
        Surface(
            modifier = if (compact) Modifier.fillMaxSize()
            else Modifier.fillMaxHeight().width(520.dp).align(Alignment.CenterEnd),
            color = Domovoi.colors.canvas,
        ) {
            Column(Modifier.fillMaxSize()) {
                Row(
                    Modifier.fillMaxWidth().padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    PodArt(sub.artwork, 56)
                    Column(Modifier.weight(1f)) {
                        Text(
                            sub.title ?: sub.feed_url ?: "podcast",
                            style = MaterialTheme.typography.titleMedium,
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
                    }
                    IconButton(onClick = { confirmUnsub = true }) {
                        Icon(Icons.Outlined.Delete, "unsubscribe", tint = Domovoi.colors.fgMuted)
                    }
                    IconButton(onClick = onClose) {
                        Icon(Icons.Outlined.Close, "close", tint = Domovoi.colors.fgMuted)
                    }
                }
                HorizontalDivider(color = Domovoi.colors.border)

                when {
                    episodes.data == null && episodes.loading -> LoadingState()
                    episodes.data == null && episodes.error != null ->
                        ErrorState(episodes.error ?: "request failed", episodes.refresh)
                    episodes.data.isNullOrEmpty() ->
                        EmptyState("no episodes yet", "try \"poll now\"")
                    else -> LazyColumn(Modifier.weight(1f)) {
                        items(episodes.data.orEmpty(), key = { it.id }) { ep ->
                            EpisodeRow(ep, onDownload = { saveToDevice(ep) }) { onPlay(ep) }
                        }
                    }
                }
            }
        }
    }

    if (confirmUnsub) {
        ConfirmDialog(
            title = "unsubscribe",
            body = "Unsubscribe from \"${sub.title ?: sub.feed_url}\"? Downloaded episodes are removed.",
            confirmLabel = "unsubscribe",
            destructive = true,
            onConfirm = {
                scope.launch {
                    runCatching { app.api.delete("/api/podcasts/subscriptions/${sub.id}") }
                        .onSuccess {
                            toast("Unsubscribed")
                            onUnsubscribed()
                        }
                        .onFailure { toast("failed: ${it.message}") }
                }
            },
            onDismiss = { confirmUnsub = false },
        )
    }
}

@Composable
private fun EpisodeRow(ep: PodcastEpisode, onDownload: () -> Unit, onPlay: () -> Unit) {
    Column {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            FilledIconButton(
                onClick = onPlay,
                enabled = ep.has_file,
                modifier = Modifier.size(32.dp),
                colors = IconButtonDefaults.filledIconButtonColors(
                    containerColor = Domovoi.colors.brand,
                    contentColor = Domovoi.colors.brandFg,
                ),
            ) {
                Icon(Icons.Filled.PlayArrow, "play", modifier = Modifier.size(16.dp))
            }
            Column(Modifier.weight(1f)) {
                Text(
                    ep.title ?: "episode",
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val meta = buildString {
                    append(if (ep.duration_sec != null) fmtDur(ep.duration_sec) else "—")
                    if (ep.chapters.isNotEmpty()) append(" · ${ep.chapters.size} chapters")
                    if (!ep.has_file && !ep.download_status.isNullOrBlank()) {
                        append(" · ${ep.download_status}")
                    }
                }
                Text(
                    meta,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgMuted,
                )
            }
            if (ep.has_file) {
                IconButton(onClick = onDownload) {
                    Icon(
                        Icons.Outlined.Download, "save to this device",
                        tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp),
                    )
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
    }
}
