package com.domovoi.app.ui.screens.podcasts

import com.domovoi.app.ui.components.rememberPager
import com.domovoi.app.ui.components.PagedFooter
import com.domovoi.app.ui.components.LoadMoreNearEnd
import com.domovoi.app.net.toPage
import com.domovoi.app.net.Paging
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import java.time.Instant
import java.time.ZoneId
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
import com.domovoi.app.ui.components.launchAction
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily

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
    // The app's scope, not this overlay's: a back gesture can close it
    // while "unsubscribe" is on its way (ui/components/ServerAction.kt).
    val scope = app.scope
    val compact = currentWindowAdaptiveInfo()
        .windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT
    var confirmUnsub by remember { mutableStateOf(false) }

    fun saveToDevice(ep: PodcastEpisode) {
        val name = DeviceDownloads.safeName(ep.title ?: "episode-${ep.id}", fallback = "episode") +
            (ep.file_ext ?: ".mp3")
        val err = DeviceDownloads.enqueue(
            context,
            app.api,
            "/api/podcasts/episodes/${ep.id}/audio?download=1",
            name,
        )
        toast(err ?: "saving \"$name\" to Downloads/Domovoi")
    }

    // A long back catalogue arrives a page at a time as the list scrolls
    // (net/Paging.kt); the search runs on the server over every episode.
    var search by remember { mutableStateOf("") }
    var needle by remember { mutableStateOf("") }
    LaunchedEffect(search) {
        kotlinx.coroutines.delay(250)
        needle = search.trim()
    }
    // The listening-as person's place in each episode comes with the rows
    // (the device_id + person_id slot the player saves positions under);
    // changing the person changes the path, which re-reads them.
    val listenerId by app.prefs.listenerPersonId.collectAsState()
    val episodesPath = PodFormat.episodesPath(sub.id, needle, app.prefs.deviceId, listenerId) {
        android.net.Uri.encode(it)
    }
    val pager = rememberPager<PodcastEpisode>(
        episodesPath, keyOf = { it.id },
        eventTypes = setOf("podcasts.changed", "podcast_positions.changed"),
    ) { a, req -> a.api.get(Paging.path(episodesPath, req)).toPage<PodcastEpisode>(loadedBefore = req.offset) }
    val episodes by pager.state.collectAsState()
    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
    LoadMoreNearEnd(listState, pager)

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
                // The show's info line ("6 episodes · updated 2h ago") and
                // its description, as plain text that expands.
                val now = Instant.now()
                Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 8.dp)) {
                    Text(
                        PodFormat.subInfo(sub, episodes.total, now),
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                        color = Domovoi.colors.fgMuted,
                    )
                    val showText = remember(sub.description) { PodFormat.plainText(sub.description) }
                    if (showText.isNotEmpty()) PodText(showText, Modifier.padding(top = 4.dp))
                }
                androidx.compose.material3.OutlinedTextField(
                    value = search, onValueChange = { search = it },
                    placeholder = { Text("search episodes…") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                )

                when {
                    episodes.items.isEmpty() && episodes.loading -> LoadingState()
                    episodes.items.isEmpty() && episodes.error != null ->
                        ErrorState(episodes.error ?: "request failed", pager::retry)
                    episodes.items.isEmpty() && needle.isNotEmpty() ->
                        EmptyState("no episodes match", "search = \"$needle\"")
                    episodes.items.isEmpty() ->
                        EmptyState("no episodes yet", "try \"poll now\"")
                    else -> LazyColumn(Modifier.weight(1f), state = listState) {
                        items(episodes.items, key = { it.id }) { ep ->
                            EpisodeRow(ep, now, onDownload = { saveToDevice(ep) }) { onPlay(ep) }
                        }
                        item(key = "episodes-footer") {
                            PagedFooter(episodes, "episodes", onRetry = pager::retry, onLoadMore = pager::loadMore)
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
                scope.launchAction(
                    action = { app.api.delete("/api/podcasts/subscriptions/${sub.id}") },
                    onSuccess = {
                        toast("Unsubscribed")
                        onUnsubscribed()
                    },
                    onFailure = { toast("failed: ${it.message}") },
                )
            },
            onDismiss = { confirmUnsub = false },
        )
    }
}

@Composable
private fun EpisodeRow(ep: PodcastEpisode, now: Instant, onDownload: () -> Unit, onPlay: () -> Unit) {
    val prog = PodFormat.progress(ep)
    val isNew = prog == PodFormat.Progress.New
    val desc = remember(ep.description) { PodFormat.plainText(ep.description) }
    val progLabel = PodFormat.progressLabel(prog)
    val meta = PodFormat.meta(ep, now, ZoneId.systemDefault())
    Column {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Top,
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
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (isNew) {
                        Box(
                            Modifier.size(7.dp)
                                .background(Domovoi.colors.brand, CircleShape)
                                .semantics { contentDescription = "not played yet" },
                        )
                    }
                    Text(
                        ep.title ?: "episode",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = if (isNew) FontWeight.SemiBold else FontWeight.Medium,
                        color = Domovoi.colors.fg,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                // "today · 38 min · 24 min left · 12 chapters · downloaded";
                // an unfinished place is in the brand colour, as on the web.
                val brand = Domovoi.colors.brand
                Text(
                    buildAnnotatedString {
                        meta.forEachIndexed { i, m ->
                            if (i > 0) append(" · ")
                            if (progLabel != null && m == progLabel && prog !is PodFormat.Progress.Played) {
                                withStyle(SpanStyle(color = brand)) { append(m) }
                            } else {
                                append(m)
                            }
                        }
                    },
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(top = 2.dp),
                )
                if (prog is PodFormat.Progress.Partial) {
                    Box(
                        Modifier.padding(top = 4.dp).fillMaxWidth().height(3.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(Domovoi.colors.sunken),
                    ) {
                        Box(Modifier.fillMaxHeight().fillMaxWidth(prog.pct / 100f).background(brand))
                    }
                }
                if (desc.isNotEmpty()) PodText(desc, Modifier.padding(top = 4.dp))
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

/** A feed's description, already plain text (PodFormat.plainText): two
 *  lines, and a tap shows the whole text (scrolling past a screenful). */
@Composable
private fun PodText(text: String, modifier: Modifier = Modifier) {
    var open by remember(text) { mutableStateOf(false) }
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = Domovoi.colors.fgMuted,
        maxLines = if (open) Int.MAX_VALUE else 2,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .fillMaxWidth()
            .clickable(onClickLabel = if (open) "show less" else "show more") { open = !open }
            .then(if (open) Modifier.heightIn(max = 280.dp).verticalScroll(rememberScrollState()) else Modifier),
    )
}
