package com.domovoi.app.ui.screens.local

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
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.screens.videos.VideoPlayerDialog
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Offline/local mode screens: on-device music + videos via MediaStore
 * (data/LocalMedia.kt). These are the only two tabs when no domovoi is
 * connected; files saved by the connected-mode download actions land in
 * Downloads/Domovoi, get indexed by MediaStore, and show up here.
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
fun LocalMusicScreen() {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Music", "on this device — connect to a domovoi for your full library")
        Spacer(Modifier.height(12.dp))
        PermissionGate(LocalMedia.audioPermission(), "no access to device music") {
            LocalMusicList()
        }
    }
}

@Composable
private fun LocalMusicList() {
    val app = LocalApp.current
    val context = LocalContext.current
    var tracks by remember { mutableStateOf<List<LocalTrack>?>(null) }
    var filter by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        tracks = withContext(Dispatchers.IO) { LocalMedia.queryTracks(context) }
    }

    val q = filter.trim().lowercase()
    val shown = tracks.orEmpty().filter {
        q.isBlank() || it.title.lowercase().contains(q)
            || it.artist?.lowercase()?.contains(q) == true
            || it.album?.lowercase()?.contains(q) == true
    }

    fun play(track: LocalTrack) {
        val items = shown.map {
            PlayItem.fromDeviceAudio(
                it.id, it.title, it.artist, it.album, it.durationSec, it.uri, it.albumArtUri,
            )
        }
        app.player.playItems(items, shown.indexOf(track).coerceAtLeast(0))
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = filter, onValueChange = { filter = it },
            placeholder = { Text("filter…") }, singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))
        when {
            tracks == null -> Text(
                "reading device library…",
                style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
            )
            shown.isEmpty() -> EmptyState(
                "no music on this device",
                "downloads from a domovoi land in Downloads/Domovoi and show up here",
            )
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(shown, key = { it.id }) { t -> LocalTrackRow(t) { play(t) } }
            }
        }
    }
}

@Composable
private fun LocalTrackRow(t: LocalTrack, onPlay: () -> Unit) {
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
fun LocalVideosScreen() {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Videos", "on this device — connect to a domovoi for your full library")
        Spacer(Modifier.height(12.dp))
        PermissionGate(LocalMedia.videoPermission(), "no access to device videos") {
            LocalVideosGrid()
        }
    }
}

@Composable
private fun LocalVideosGrid() {
    val context = LocalContext.current
    var videos by remember { mutableStateOf<List<LocalVideo>?>(null) }
    var playing by remember { mutableStateOf<LocalVideo?>(null) }

    LaunchedEffect(Unit) {
        videos = withContext(Dispatchers.IO) { LocalMedia.queryVideos(context) }
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
            columns = GridCells.Adaptive(160.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            items(videos.orEmpty(), key = { it.id }) { v ->
                Column {
                    LocalVideoTile(v) { playing = v }
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
            }
        }
    }

    playing?.let { v ->
        VideoPlayerDialog(
            title = v.name,
            mediaUri = v.uri,
            onClose = { playing = null },
        )
    }
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
