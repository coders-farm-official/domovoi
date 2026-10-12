package com.domovoi.app.ui.screens.local

import com.domovoi.app.ui.components.rememberPager
import com.domovoi.app.ui.components.PagedFooter
import com.domovoi.app.ui.components.LoadMoreNearEnd
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.collectAsState
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import coil.decode.VideoFrameDecoder
import coil.request.ImageRequest
import com.domovoi.app.LocalApp
import com.domovoi.app.data.LocalMedia
import com.domovoi.app.data.LocalTrack
import com.domovoi.app.data.LocalVideo
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.QueueWindow
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.screens.videos.FeedVideo
import com.domovoi.app.ui.screens.videos.VideoFeedPlayer
import com.domovoi.app.ui.screens.videos.centerOn
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Offline/local mode screens: on-device music + videos via MediaStore
 * (data/LocalMedia.kt). These are two of the local shell's three tabs (the
 * third is Files, ui/screens/files/PhoneFiles.kt) when no domovoi is
 * connected; files saved by the connected-mode download actions land in
 * Downloads/Domovoi, get indexed by MediaStore, and show up here.
 *
 * The connected Music and Videos pages show these same screens under their
 * "your device" switch (YourDevice.kt), with an [upload] that puts an Upload
 * button on each row; without one (the local shell) they are unchanged.
 */

/** Ask-once permission gate shared by both screens. */
@Composable
private fun PermissionGate(
    permission: String,
    emptyTitle: String,
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    var granted by remember { mutableStateOf(LocalMedia.hasPermission(context, permission)) }
    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted = it }

    if (granted) {
        content()
    } else {
        EmptyState(
            emptyTitle,
            "domovoi needs permission to read media on this device",
            action = {
                Button(onClick = { launcher.launch(permission) }) { Text("allow access") }
            },
        )
    }
}

// ---------------------------------------------------------------------------
// Music
// ---------------------------------------------------------------------------

@Composable
internal fun LocalMusicScreen(
    subtitle: String = "on this device — connect to a domovoi for your full library",
    upload: DeviceUploader? = null,
) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Music", subtitle)
        Spacer(Modifier.height(12.dp))
        upload?.refused?.let {
            UploadRefusedCard(it)
            Spacer(Modifier.height(10.dp))
        }
        PermissionGate(LocalMedia.audioPermission(), "no access to device music") {
            LocalMusicList(upload)
        }
    }
}

@Composable
private fun LocalMusicList(upload: DeviceUploader?) {
    val app = LocalApp.current
    val context = LocalContext.current
    var filter by remember { mutableStateOf("") }
    var needle by remember { mutableStateOf("") }
    LaunchedEffect(filter) {
        kotlinx.coroutines.delay(250)
        needle = filter.trim()
    }
    // The phone's music a page at a time (data/LocalMedia.kt), the filter
    // applied by MediaStore over every track.
    val pager = rememberPager<LocalTrack>(needle, keyOf = { it.id }) { _, req ->
        withContext(Dispatchers.IO) { LocalMedia.pageTracks(context, needle, req) }
    }
    val tracks by pager.state.collectAsState()
    val shown = tracks.items
    val listState = rememberLazyListState()
    LoadMoreNearEnd(listState, pager)

    // The tapped track plus its neighbours, not every track on the phone: a
    // whole-device queue (thousands of songs) stalled playback start and,
    // later, the Music page's player tab. See player/QueueWindow.kt.
    fun play(track: LocalTrack) {
        val window = QueueWindow.around(shown, shown.indexOf(track).coerceAtLeast(0))
        val items = window.items.map {
            PlayItem.fromDeviceAudio(
                it.id, it.title, it.artist, it.album, it.durationSec, it.uri, it.albumArtUri,
            )
        }
        app.player.playItems(items, window.index)
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = filter, onValueChange = { filter = it },
            placeholder = { Text("filter…") }, singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))
        when {
            !tracks.loaded -> Text(
                "reading device library…",
                style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
            )
            shown.isEmpty() && needle.isNotEmpty() -> EmptyState("no tracks match", "filter = \"$needle\"")
            shown.isEmpty() -> EmptyState(
                "no music on this device",
                "downloads from a domovoi land in Downloads/Domovoi and show up here",
            )
            else -> LazyColumn(Modifier.fillMaxSize(), state = listState) {
                items(shown, key = { it.id }) { t ->
                    val key = trackUploadKey(t.id)
                    LocalTrackRow(
                        t,
                        upload = upload?.state(key),
                        onUpload = { upload?.upload(key, Uri.parse(t.uri), t.title) },
                    ) { play(t) }
                }
                item(key = "local-tracks-footer") {
                    PagedFooter(tracks, "tracks", onRetry = pager::retry, onLoadMore = pager::loadMore)
                }
            }
        }
    }
}

/** [upload] null: no Upload button (the local shell). */
@Composable
private fun LocalTrackRow(
    t: LocalTrack,
    upload: UploadButtonState? = null,
    onUpload: () -> Unit = {},
    onPlay: () -> Unit,
) {
    val shape = RoundedCornerShape(6.dp)
    Row(
        Modifier.fillMaxWidth()
            .clickable(onClick = onPlay)
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(
            Modifier.size(40.dp).clip(shape)
                .background(Domovoi.colors.sunken)
                .border(1.dp, Domovoi.colors.border, shape),
            contentAlignment = Alignment.Center,
        ) {
            if (t.albumArtUri != null) {
                SubcomposeAsyncImage(
                    model = t.albumArtUri,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                    loading = { TrackGlyph() },
                    error = { TrackGlyph() },
                )
            } else {
                TrackGlyph()
            }
        }
        Column(Modifier.weight(1f)) {
            Text(
                t.title, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            Text(
                listOfNotNull(t.artist, t.album).joinToString(" · ").ifBlank { "unknown" },
                style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        Text(
            t.durationSec?.let { fmtDur(it) } ?: "—",
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
            color = Domovoi.colors.fgFaint,
        )
        upload?.let { UploadRowButton(it, onUpload) }
        Icon(
            Icons.Filled.PlayArrow, contentDescription = "play",
            tint = Domovoi.colors.brand, modifier = Modifier.size(20.dp),
        )
    }
}

@Composable
private fun TrackGlyph() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Icon(
            Icons.Outlined.MusicNote, contentDescription = null,
            tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(18.dp),
        )
    }
}

// ---------------------------------------------------------------------------
// Videos
// ---------------------------------------------------------------------------

@Composable
internal fun LocalVideosScreen(
    subtitle: String = "on this device — connect to a domovoi for your full library",
    upload: DeviceUploader? = null,
) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Videos", subtitle)
        Spacer(Modifier.height(12.dp))
        upload?.refused?.let {
            UploadRefusedCard(it)
            Spacer(Modifier.height(10.dp))
        }
        PermissionGate(LocalMedia.videoPermission(), "no access to device videos") {
            LocalVideosGrid(upload)
        }
    }
}

@Composable
private fun LocalVideosGrid(upload: DeviceUploader?) {
    val context = LocalContext.current
    /** Index of the tapped video; the feed's order is the grid's. */
    var feedStart by remember { mutableStateOf<Int?>(null) }
    var centerKey by remember { mutableStateOf<String?>(null) }
    val gridState = rememberLazyGridState()
    // The phone's videos a page at a time (data/LocalMedia.kt).
    val pager = rememberPager<LocalVideo>("local-videos", keyOf = { it.id }) { _, req ->
        withContext(Dispatchers.IO) { LocalMedia.pageVideos(context, req) }
    }
    val paged by pager.state.collectAsState()
    val videos: List<LocalVideo>? = if (paged.loaded) paged.items else null
    LoadMoreNearEnd(gridState, pager)
    // Back from the feed: put the video it ended on in the middle of the grid.
    LaunchedEffect(centerKey) {
        val key = centerKey ?: return@LaunchedEffect
        gridState.centerOn(videos.orEmpty().indexOfFirst { localKey(it) == key })
        centerKey = null
    }

    when {
        videos == null -> Text(
            "reading device videos…",
            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
        )
        videos.isNullOrEmpty() -> EmptyState(
            "no videos on this device",
            "downloads from a domovoi land in Downloads/Domovoi and show up here",
        )
        else -> LazyVerticalGrid(
            state = gridState,
            columns = GridCells.Adaptive(160.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            items(videos.orEmpty(), key = { it.id }) { v ->
                Column {
                    LocalVideoTile(v) { feedStart = videos.orEmpty().indexOf(v) }
                    if (upload == null) {
                        LocalVideoCaption(v)
                    } else {
                        val key = videoUploadKey(v.id)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) { LocalVideoCaption(v) }
                            UploadRowButton(upload.state(key)) { upload.upload(key, Uri.parse(v.uri), v.name) }
                        }
                    }
                }
            }
            item(key = "local-videos-footer", span = { GridItemSpan(maxLineSpan) }) {
                PagedFooter(paged, "videos", onRetry = pager::retry, onLoadMore = pager::loadMore)
            }
        }
    }

    feedStart?.let { start ->
        val list = videos.orEmpty()
        val feedVideos = remember(list) {
            list.map { v ->
                FeedVideo(
                    key = localKey(v),
                    title = v.name,
                    uri = v.uri,
                    posterModel = ImageRequest.Builder(context)
                        .data(v.uri)
                        .decoderFactory(VideoFrameDecoder.Factory())
                        .build(),
                    sizeBytes = v.sizeBytes,
                    shareable = true,
                )
            }
        }
        VideoFeedPlayer(
            videos = feedVideos,
            startIndex = start,
            onUpload = upload?.let { u ->
                { fv ->
                    list.firstOrNull { localKey(it) == fv.key }
                        ?.let { v -> u.upload(videoUploadKey(v.id), Uri.parse(v.uri), v.name) }
                }
            },
            onClose = { key ->
                feedStart = null
                centerKey = key
            },
            // The feed follows the grid's pages: nearing its end loads the next.
            onNearEnd = pager::loadMore,
        )
    }
}

private fun localKey(v: LocalVideo): String = "local:${v.id}"

@Composable
private fun LocalVideoCaption(v: LocalVideo) {
    Text(
        v.name, style = MaterialTheme.typography.labelMedium,
        color = Domovoi.colors.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.padding(top = 4.dp),
    )
    Text(
        v.durationSec?.let { fmtDur(it) } ?: "—",
        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
        color = Domovoi.colors.fgFaint,
    )
}

@Composable
private fun LocalVideoTile(v: LocalVideo, onClick: () -> Unit) {
    val context = LocalContext.current
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
        SubcomposeAsyncImage(
            model = ImageRequest.Builder(context)
                .data(v.uri)
                .decoderFactory(VideoFrameDecoder.Factory())
                .build(),
            contentDescription = v.name,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
            loading = { VideoGlyph() },
            error = { VideoGlyph() },
        )
        Box(
            Modifier.size(40.dp).background(Domovoi.colors.brand, RoundedCornerShape(50)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Filled.PlayArrow, contentDescription = "play",
                tint = androidx.compose.ui.graphics.Color.Black.copy(alpha = 0.8f),
                modifier = Modifier.size(24.dp),
            )
        }
    }
}

@Composable
private fun VideoGlyph() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Icon(
            Icons.Outlined.Movie, contentDescription = null,
            tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(26.dp),
        )
    }
}
