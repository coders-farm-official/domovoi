package com.domovoi.app.ui.screens.videos

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.DeviceDownloads
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.Pager
import com.domovoi.app.net.Paging
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.net.toPage
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.FILES_CHANGED
import com.domovoi.app.ui.components.LibraryRefresh
import com.domovoi.app.ui.components.LibraryRefreshBox
import com.domovoi.app.ui.components.LibraryRefreshButton
import com.domovoi.app.ui.components.rememberLibraryRefresh
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadMoreNearEnd
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.PagedFooter
import com.domovoi.app.ui.components.rememberPager
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// ---------------------------------------------------------------------------
// Models — /api/videos rows (web/static/videos.jsx).
// ---------------------------------------------------------------------------

@Serializable
data class VideoRow(
    val library_id: String = "",
    val library_label: String? = null,
    val rel: String = "",
    val name: String = "",
    val size: Long? = null,
    val mtime: Double? = null,
    // recent-strip extras (absent on /list rows)
    val position_sec: Int? = null,
    val duration_sec: Int? = null,
    val title: String? = null,
)

@Serializable
private data class RecentList(val recent: List<VideoRow> = emptyList())

@Serializable
private data class PositionRow(val position_sec: Int = 0, val duration_sec: Int? = null)

private fun enc(s: String): String = Uri.encode(s)

private fun streamPath(v: VideoRow, download: Boolean = false): String =
    "/api/videos/stream?library_id=${enc(v.library_id)}&path=${enc(v.rel)}" +
        if (download) "&download=1" else ""

private fun posterPath(v: VideoRow): String =
    "/api/videos/poster?library_id=${enc(v.library_id)}&path=${enc(v.rel)}"

private fun AppContainer.videoIdentityQuery(): String {
    val pid = prefs.listenerPersonId.value
    return "device_id=${enc(prefs.deviceId)}" + (pid?.let { "&person_id=$it" } ?: "")
}

private suspend fun savePosition(app: AppContainer, v: VideoRow, positionSec: Long, durationSec: Long?) {
    runCatching {
        app.api.post("/api/videos/position", buildJsonObject {
            put("library_id", v.library_id)
            put("path", v.rel)
            put("device_id", app.prefs.deviceId)
            app.prefs.listenerPersonId.value?.toLongOrNull()?.let { put("person_id", it) }
            put("position_sec", positionSec.coerceAtLeast(0))
            durationSec?.takeIf { it > 0 }?.let { put("duration_sec", it) }
            put("title", v.title ?: v.name)
        })
    }
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

/**
 * The grid's list and its refresh. `files.changed` (the state socket): a video
 * uploaded from this phone's "Your device" page or anywhere else, a delete, a
 * file a plugin wrote, re-reads the grid while it is on screen (the videos
 * have no index; the server says when one of their folders moved). The
 * header's "rescan videos" and the pull-to-refresh re-walk the folders.
 */
@Composable
internal fun rememberVideosList(listPath: String): Pair<Pager<VideoRow>, LibraryRefresh> {
    val pager = rememberPager<VideoRow>(
        listPath, keyOf = ::videoKey, eventTypes = setOf(FILES_CHANGED),
    ) { a, req ->
        a.api.get(Paging.path(listPath, req)).toPage<VideoRow>("videos", loadedBefore = req.offset)
    }
    val refresh = rememberLibraryRefresh {
        pager.reload(fresh = true)
        pager.idle()
    }
    return pager to refresh
}

@Composable
fun VideosScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var filter by rememberSaveable { mutableStateOf("") }
    var needle by remember { mutableStateOf(filter.trim()) }
    LaunchedEffect(filter) {
        delay(250)
        needle = filter.trim()
    }
    // A page of videos at a time as the grid scrolls (net/Paging.kt). The
    // filter is the server's, over every video in every library; the order
    // is the server's "library" sort (library, then path), which keeps a
    // library's tiles together under its header.
    val listPath = "/api/videos/list?sort=library" +
        (if (needle.isNotEmpty()) "&q=" + Uri.encode(needle) else "")
    val (pager, refresh) = rememberVideosList(listPath)
    val list by pager.state.collectAsState()
    val totals = remember(list.extra) {
        runCatching {
            list.extra?.get("libraries")?.let {
                DomovoiJson.decodeFromJsonElement(ListSerializer(LibraryTotal.serializer()), it)
            }.orEmpty().associate { it.library_id to it.total }
        }.getOrDefault(emptyMap())
    }
    val recent = rememberApi(
        "videos-recent", eventTypes = setOf("video_positions.changed"),
    ) {
        it.api.get("/api/videos/recent?${it.videoIdentityQuery()}").decode<RecentList>().recent
    }

    var feed by remember { mutableStateOf<OpenFeed?>(null) }
    /** Set when a feed closes: which list to centre, and on which video. */
    var centerOn by remember { mutableStateOf<Pair<FeedFrom, String>?>(null) }
    val gridState = rememberLazyGridState()
    val recentState = rememberLazyListState()

    /** Open the swipe feed on [v], with [order] (the list as shown) as the viewing order. */
    fun play(v: VideoRow, order: List<VideoRow>, from: FeedFrom, resumeOverride: Long? = null) {
        scope.launch {
            val resume = resumeOverride ?: runCatching {
                app.api.get(
                    "/api/videos/position?library_id=${enc(v.library_id)}&path=${enc(v.rel)}" +
                        "&${app.videoIdentityQuery()}"
                ).decode<PositionRow>().position_sec.toLong()
            }.getOrDefault(0L)
            val start = order.indexOfFirst { videoKey(it) == videoKey(v) }.coerceAtLeast(0)
            feed = OpenFeed(from, order, start, resume)
        }
    }

    fun saveToDevice(v: VideoRow) {
        val name = DeviceDownloads.safeName(v.name, fallback = "video")
        // The video stream is a device-tier read (web/backend/api/videos.py),
        // the one save that needs the household token: it is handed over
        // only after the server proved itself on this network, right now.
        scope.launch {
            val err = DeviceDownloads.enqueueWithToken(context, app.api, streamPath(v, download = true), name)
            toast(err ?: "saving \"$name\" to Downloads/Domovoi")
        }
    }

    val grouped = list.items.groupBy { it.library_id }
    val rec = recent.data.orEmpty()
    val entries = videoGridEntries(showRecent = rec.isNotEmpty() && needle.isEmpty(), grouped = grouped, totals = totals)
    val gridOrder = gridFeedOrder(entries)
    LoadMoreNearEnd(gridState, pager)

    LaunchedEffect(centerOn) {
        val (from, key) = centerOn ?: return@LaunchedEffect
        when (from) {
            FeedFrom.Grid -> gridState.centerOn(entries.indexOfFirst { it.key == key })
            FeedFrom.Recent -> {
                gridState.scrollToItem(0)
                recentState.centerOn(rec.indexOfFirst { videoKey(it) == key })
            }
        }
        centerOn = null
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Videos", "videos across your files libraries · resume where you left off") {
            LibraryRefreshButton(refresh, "rescan videos")
        }
        OutlinedTextField(
            value = filter, onValueChange = { filter = it },
            placeholder = { Text("filter…") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )
        Spacer(Modifier.height(12.dp))

        LibraryRefreshBox(refresh, Modifier.weight(1f)) { when {
            list.items.isEmpty() && list.loading -> LoadingState()
            list.items.isEmpty() && list.error != null ->
                ErrorState(list.error ?: "request failed", pager::retry)
            list.items.isEmpty() && needle.isNotEmpty() -> EmptyState("no videos match", "filter = \"$needle\"")
            list.items.isEmpty() -> EmptyState(
                "no videos found",
                "drop video files (mp4 · webm · mkv · mov) into any files library",
            )
            else -> LazyVerticalGrid(
                state = gridState,
                columns = GridCells.Adaptive(160.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                // Rendered straight from `entries`, so a video's index there is its grid index.
                items(
                    entries,
                    key = { it.key },
                    span = { if (it is VideoGridEntry.Tile) GridItemSpan(1) else GridItemSpan(maxLineSpan) },
                ) { entry ->
                    when (entry) {
                        VideoGridEntry.Recent -> Column {
                            SectionLabel("recently played")
                            LazyRow(state = recentState, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                items(rec, key = { videoKey(it) }) { r ->
                                    Column(Modifier.width(200.dp)) {
                                        PosterTile(
                                            r,
                                            progress = r.duration_sec?.takeIf { it > 0 }
                                                ?.let { d -> (r.position_sec ?: 0).toFloat() / d },
                                        ) { play(r, rec, FeedFrom.Recent, (r.position_sec ?: 0).toLong()) }
                                        Text(
                                            r.title ?: r.name,
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Domovoi.colors.fg,
                                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                                            modifier = Modifier.padding(top = 4.dp),
                                        )
                                        Text(
                                            fmtDur((r.position_sec ?: 0).toDouble()) +
                                                (r.duration_sec?.let { " / ${fmtDur(it.toDouble())}" } ?: ""),
                                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                                            color = Domovoi.colors.fgFaint,
                                        )
                                    }
                                }
                            }
                            Spacer(Modifier.height(8.dp))
                        }
                        is VideoGridEntry.Header -> SectionLabel("${entry.label} · ${entry.count}")
                        is VideoGridEntry.Tile -> Column {
                            PosterTile(entry.video) { play(entry.video, gridOrder, FeedFrom.Grid) }
                            Text(
                                entry.video.name,
                                style = MaterialTheme.typography.labelMedium,
                                color = Domovoi.colors.fg,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.padding(top = 4.dp),
                            )
                        }
                    }
                }
                item(key = "videos-footer", span = { GridItemSpan(maxLineSpan) }) {
                    PagedFooter(list, "videos", onRetry = pager::retry, onLoadMore = pager::loadMore)
                }
            }
        } }
    }

    feed?.let { open ->
        // A feed opened from the grid follows the grid: as it loads more
        // pages, they join the feed's end (and the feed asks for them).
        val order = if (open.from == FeedFrom.Grid) gridOrder else open.videos
        val byKey = remember(order) { order.associateBy(::videoKey) }
        val feedVideos = remember(order) {
            order.map { v ->
                FeedVideo(
                    key = videoKey(v),
                    title = v.title ?: v.name,
                    uri = app.api.absolute(streamPath(v)),
                    posterModel = app.api.absolute(posterPath(v)),
                    sizeBytes = v.size,
                    modifiedEpochSec = v.mtime,
                    location = "${v.library_label ?: v.library_id}/${v.rel}",
                )
            }
        }
        VideoFeedPlayer(
            videos = feedVideos,
            startIndex = open.start,
            resumeSec = open.resumeSec,
            onSave = { fv -> byKey[fv.key]?.let(::saveToDevice) },
            onPersist = { fv, pos, dur, ended ->
                byKey[fv.key]?.let { savePosition(app, it, if (ended) 0 else pos, dur) }
            },
            onClose = { key ->
                feed = null
                recent.refresh()
                if (key != null) centerOn = open.from to key
            },
            onNearEnd = if (open.from == FeedFrom.Grid) pager::loadMore else null,
        )
    }
}

/** Which list a feed was opened from, and so which list its close centres. */
private enum class FeedFrom { Grid, Recent }

private data class OpenFeed(val from: FeedFrom, val videos: List<VideoRow>, val start: Int, val resumeSec: Long)

@Composable
private fun SectionLabel(text: String) {
    Text(
        text.lowercase(),
        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
        color = Domovoi.colors.fgMuted,
        modifier = Modifier.padding(vertical = 4.dp),
    )
}

@Composable
private fun PosterTile(v: VideoRow, progress: Float? = null, onClick: () -> Unit) {
    val app = LocalApp.current
    val shape = RoundedCornerShape(8.dp)
    Box(
        Modifier.fillMaxWidth()
            .aspectRatio(16f / 9f)
            .clip(shape)
            .background(Domovoi.colors.sunken)
            .border(1.dp, Domovoi.colors.border, shape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        // Poster 204s (no frame extracted) land in the error slot → glyph tile.
        SubcomposeAsyncImage(
            model = app.api.absolute(posterPath(v)),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
            loading = { PosterFallback() },
            error = { PosterFallback() },
        )
        Box(
            Modifier.size(40.dp).background(Domovoi.colors.brand, RoundedCornerShape(50)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Filled.PlayArrow, contentDescription = "play",
                tint = Color.Black.copy(alpha = 0.8f), modifier = Modifier.size(24.dp),
            )
        }
        progress?.let { p ->
            Box(
                Modifier.align(Alignment.BottomStart).fillMaxWidth().height(3.dp)
                    .background(Color.Black.copy(alpha = 0.4f)),
            ) {
                Box(
                    Modifier.fillMaxWidth(p.coerceIn(0f, 1f)).height(3.dp)
                        .background(Domovoi.colors.brand),
                )
            }
        }
    }
}

@Composable
private fun PosterFallback() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Icon(
            Icons.Outlined.Movie, contentDescription = null,
            tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(28.dp),
        )
    }
}

// Full-screen playback is the swipe feed (VideoFeed.kt), with resume
// persistence wired through savePosition above.
