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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
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
private data class VideoList(val videos: List<VideoRow> = emptyList())

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

@Composable
fun VideosScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val list = rememberApi("videos") {
        it.api.get("/api/videos/list").decode<VideoList>().videos
    }
    val recent = rememberApi(
        "videos-recent", eventTypes = setOf("video_positions.changed"),
    ) {
        it.api.get("/api/videos/recent?${it.videoIdentityQuery()}").decode<RecentList>().recent
    }

    var filter by remember { mutableStateOf("") }
    var playing by remember { mutableStateOf<Pair<VideoRow, Long>?>(null) } // video + resume sec

    fun play(v: VideoRow, resumeOverride: Long? = null) {
        scope.launch {
            val resume = resumeOverride ?: runCatching {
                app.api.get(
                    "/api/videos/position?library_id=${enc(v.library_id)}&path=${enc(v.rel)}" +
                        "&${app.videoIdentityQuery()}"
                ).decode<PositionRow>().position_sec.toLong()
            }.getOrDefault(0L)
            playing = v to resume
        }
    }

    fun saveToDevice(v: VideoRow) {
        val name = DeviceDownloads.safeName(v.name, fallback = "video")
        val err = DeviceDownloads.enqueue(context, app.api.absolute(streamPath(v, download = true)), name)
        toast(err ?: "saving \"$name\" to Downloads/Domovoi")
    }

    val q = filter.trim().lowercase()
    val filtered = list.data.orEmpty().filter {
        q.isBlank() || it.name.lowercase().contains(q) || it.rel.lowercase().contains(q)
    }
    val grouped = filtered.groupBy { it.library_id }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Videos", "videos across your files libraries · resume where you left off")
        OutlinedTextField(
            value = filter, onValueChange = { filter = it },
            placeholder = { Text("filter…") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )
        Spacer(Modifier.height(12.dp))

        when {
            list.data == null && list.loading -> LoadingState()
            list.data == null && list.error != null ->
                ErrorState(list.error ?: "request failed", list.refresh)
            list.data.isNullOrEmpty() -> EmptyState(
                "no videos found",
                "drop video files (mp4 · webm · mkv · mov) into any files library",
            )
            else -> LazyVerticalGrid(
                columns = GridCells.Adaptive(160.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.weight(1f),
            ) {
                val rec = recent.data.orEmpty()
                if (rec.isNotEmpty() && q.isBlank()) {
                    item(span = { GridItemSpan(maxLineSpan) }) {
                        Column {
                            SectionLabel("recently played")
                            LazyRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                items(rec, key = { "${it.library_id}:${it.rel}" }) { r ->
                                    Column(Modifier.width(200.dp)) {
                                        PosterTile(
                                            r,
                                            progress = r.duration_sec?.takeIf { it > 0 }
                                                ?.let { d -> (r.position_sec ?: 0).toFloat() / d },
                                        ) { play(r, (r.position_sec ?: 0).toLong()) }
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
                    }
                }
                grouped.forEach { (libId, vids) ->
                    item(span = { GridItemSpan(maxLineSpan) }, key = "hdr:$libId") {
                        SectionLabel("${vids.first().library_label ?: libId} · ${vids.size}")
                    }
                    items(vids, key = { "${it.library_id}:${it.rel}" }) { v ->
                        Column {
                            PosterTile(v) { play(v) }
                            Text(
                                v.name,
                                style = MaterialTheme.typography.labelMedium,
                                color = Domovoi.colors.fg,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.padding(top = 4.dp),
                            )
                        }
                    }
                }
            }
        }
    }

    playing?.let { (video, resumeSec) ->
        VideoPlayerDialog(
            title = video.title ?: video.name,
            mediaUri = app.api.absolute(streamPath(video)),
            resumeSec = resumeSec,
            onSave = { saveToDevice(video) },
            onPersist = { pos, dur, ended ->
                savePosition(app, video, if (ended) 0 else pos, dur)
            },
            onClose = { playing = null; recent.refresh() },
        )
    }
}

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

// Full-screen playback uses the shared VideoPlayerDialog (VideoPlayerDialog.kt),
// with resume persistence wired through savePosition above.
