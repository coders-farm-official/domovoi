package com.domovoi.app.ui.screens.music

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.PromptDialog
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLEncoder

private const val PAGE_SIZE = 12

/** Music page — Android parity build of web/static/music.jsx:
 *  now-playing strip, Library / Player / Playlists / Stats tabs, track +
 *  playlist drawers, uploads. Provider search/download surfaces are
 *  web-plugin-only — this screen stays provider-agnostic. */
@Composable
fun MusicScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val ui = remember { MusicUi() }

    // ── Data ──────────────────────────────────────────────────────────
    var qDebounced by remember { mutableStateOf("") }
    LaunchedEffect(ui.q) {
        delay(250)
        qDebounced = ui.q
    }
    val libPath = remember(qDebounced, ui.source, ui.sort, ui.favoritedOnly, ui.page) {
        buildString {
            append("/api/music/library?sort=").append(ui.sort)
            append("&limit=").append(PAGE_SIZE)
            append("&offset=").append(ui.page * PAGE_SIZE)
            if (qDebounced.isNotBlank()) {
                append("&q=").append(URLEncoder.encode(qDebounced, "UTF-8"))
            }
            if (ui.source != "all") append("&source=").append(ui.source)
            if (ui.favoritedOnly) append("&favorited=true")
        }
    }
    val lib = rememberApi(libPath, eventTypes = setOf("library.indexer.changed")) {
        it.api.get(libPath).decode<LibraryPage>()
    }
    val stats = rememberApi(eventTypes = setOf("library.indexer.changed")) {
        it.api.get("/api/music/library/stats").decode<LibraryStats>()
    }
    val nowPlaying = rememberApi(eventTypes = setOf("music.now_playing.changed")) {
        it.api.get("/api/music/now-playing").decode<List<NowPlayingRoom>>()
    }
    val playlists = rememberApi(eventTypes = setOf("playlists.changed")) {
        it.api.get("/api/playlists").decode<List<Playlist>>()
    }

    // Accumulate the distinct sources seen across fetched pages — the
    // source filter is data-driven (open enum), never hardcoded.
    LaunchedEffect(lib.data) {
        lib.data?.items.orEmpty()
            .mapNotNull { it.source }
            .distinct()
            .forEach { if (it !in ui.seenSources) ui.seenSources.add(it) }
    }

    // Clamp the page back when the filtered total shrinks under the offset.
    val total = lib.data?.total ?: 0
    val pages = maxOf(1, (total + PAGE_SIZE - 1) / PAGE_SIZE)
    LaunchedEffect(total, pages) {
        if (total > 0 && ui.page >= pages) ui.page = pages - 1
    }

    // 1s local tick so elapsed time advances between WS pushes; the server
    // elapsed_sec is canonical, the tick is additive on top of it.
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            tick++
        }
    }
    LaunchedEffect(nowPlaying.data) { tick = 0 }

    val npList = nowPlaying.data.orEmpty()
    val rooms = if (npList.isNotEmpty()) npList.map { it.roomId } else listOf("kitchen")
    val realPlaylists = playlists.data.orEmpty().filter { !it.isVirtual }

    // ── Local playback (this device) ──────────────────────────────────
    fun toItem(t: LibraryTrack): PlayItem = PlayItem.fromTrack(
        t.id,
        t.title
            ?: t.filePath?.substringAfterLast('/')?.substringAfterLast('\\')
            ?: "track",
        t.artist, t.album, t.durationSec,
    )

    val onBrowserPlay: (LibraryTrack) -> Unit = { t ->
        app.player.playItems(listOf(toItem(t)))
        toast("playing \"${t.title ?: "track"}\" on this device")
    }
    val onQueueTrack: (LibraryTrack) -> Unit = { t ->
        app.player.enqueue(listOf(toItem(t)))
        toast("queued \"${t.title ?: "track"}\"")
    }
    val onPlayNext: (LibraryTrack) -> Unit = { t ->
        app.player.playNext(listOf(toItem(t)))
        toast("playing next: \"${t.title ?: "track"}\"")
    }
    val onSaveToDevice: (LibraryTrack) -> Unit = { t -> saveTrackToDevice(context, app, toast, t) }

    // ── Room transport ────────────────────────────────────────────────
    fun roomCtl(action: String, room: String, failLabel: String) {
        scope.launch {
            runCatching { app.api.post("/api/music/$action/$room") }
                .onSuccess { nowPlaying.refresh() }
                .onFailure { toast("$failLabel failed: ${it.message}") }
        }
    }

    val onPlayRandom: (String) -> Unit = { room ->
        toast("shuffle requested in $room…")
        scope.launch {
            runCatching {
                app.api.post("/api/music/play", buildJsonObject {
                    put("room_id", room)
                    put("query", "something random")
                })
            }.onSuccess { nowPlaying.refresh() }
                .onFailure { toast("play failed: ${it.message}") }
        }
    }

    val onFavoriteNp: (String) -> Unit = { room ->
        scope.launch {
            runCatching {
                app.api.post("/api/music/now-playing/$room/favorite").decode<NpFavoriteResponse>()
            }.onSuccess { r ->
                val label = r.title ?: "track"
                // Prefer server-supplied copy; the kind vocabulary is open
                // (plugins report their own slugs), so fall back generically.
                val msg = r.message ?: when {
                    r.alreadyFavorited && r.kind == "library" -> "$label is already favorited"
                    r.alreadyFavorited && r.kind == "radio" -> "$label is already a favorite station"
                    r.alreadyFavorited -> "$label is already favorited"
                    r.kind == "radio" -> "favorited station $label"
                    else -> "favorited $label"
                }
                toast(msg)
                if (r.kind == "library" && !r.alreadyFavorited) lib.refresh()
                nowPlaying.refresh()
            }.onFailure { toast("favorite failed: ${it.message}") }
        }
    }

    // ── Library actions ───────────────────────────────────────────────
    val onToggleFavorite: (LibraryTrack) -> Unit = { t ->
        scope.launch {
            runCatching {
                app.api.patch("/api/music/library/${t.id}", buildJsonObject {
                    put("favorited", !t.favorited)
                })
            }.onSuccess {
                lib.refresh()
                nowPlaying.refresh()
            }.onFailure { toast("favorite failed: ${it.message}") }
        }
    }

    val onBulkAdd: (Long, List<Long>) -> Unit = { pid, trackIds ->
        scope.launch {
            var added = 0
            var dupes = 0
            for (tid in trackIds) {
                try {
                    app.api.post("/api/playlists/$pid/tracks", buildJsonObject {
                        put("track_id", tid)
                    })
                    added++
                } catch (e: Exception) {
                    val msg = e.message ?: ""
                    if (msg.contains("already", ignoreCase = true) || msg.contains("409")) dupes++
                    else toast("add failed: $msg")
                }
            }
            toast(
                "added $added track" + (if (added == 1) "" else "s") +
                    (if (dupes > 0) ", $dupes already in" else ""),
            )
            playlists.refresh()
            ui.selectedIds.clear()
            ui.bulkPid = null
        }
    }

    val onRescan: () -> Unit = {
        scope.launch {
            runCatching { app.api.post("/api/music/library/reindex") }
                .onSuccess { toast("rescan started") }
                .onFailure { toast("rescan failed: ${it.message}") }
        }
    }
    val onEnrich: () -> Unit = {
        scope.launch {
            runCatching { app.api.post("/api/music/library/enrich") }
                .onSuccess { toast("enrich started") }
                .onFailure { toast("enrich failed: ${it.message}") }
        }
    }

    // ── Upload ────────────────────────────────────────────────────────
    val pickFiles = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents(),
    ) { uris ->
        if (uris.isNotEmpty()) {
            ui.uploading = true
            toast("uploading ${uris.size} item" + (if (uris.size == 1) "" else "s") + "…")
            scope.launch {
                try {
                    val form = withContext(Dispatchers.IO) {
                        val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
                        uris.forEach { uri ->
                            val bytes = context.contentResolver.openInputStream(uri)
                                ?.use { s -> s.readBytes() }
                            if (bytes != null) {
                                val mime = context.contentResolver.getType(uri)
                                    ?: "application/octet-stream"
                                builder.addFormDataPart(
                                    "files",
                                    displayName(context, uri),
                                    bytes.toRequestBody(mime.toMediaTypeOrNull()),
                                )
                            }
                        }
                        builder.build()
                    }
                    val res = app.api.upload("/api/music/library/upload", form).jsonObject
                    val saved = res["saved"]?.jsonPrimitive?.intOrNull ?: 0
                    val skipped = (res["skipped"] as? JsonArray)?.size ?: 0
                    val reindex = res["reindex_triggered"]?.jsonPrimitive?.booleanOrNull ?: false
                    val parts = mutableListOf("uploaded $saved track" + (if (saved == 1) "" else "s"))
                    if (skipped > 0) parts.add("$skipped skipped")
                    parts.add(if (reindex) "indexing…" else "server offline — will index on next boot")
                    toast(parts.joinToString(" · "))
                } catch (e: Exception) {
                    toast("upload failed: ${e.message}")
                } finally {
                    ui.uploading = false
                }
            }
        }
    }

    // ── Playlists actions ─────────────────────────────────────────────
    val onPlayPlaylist: (Playlist) -> Unit = { p ->
        val room = rooms.first()
        toast("playing ${p.name} in $room…")
        scope.launch {
            runCatching {
                app.api.post("/api/music/play-playlist", buildJsonObject {
                    put("room_id", room)
                    put("playlist_id", p.id)
                    put("shuffle", false)
                })
            }.onSuccess { nowPlaying.refresh() }
                .onFailure { toast("play failed: ${it.message}") }
        }
    }

    // ── Header derived counts ─────────────────────────────────────────
    val playingCount = npList.count { it.state == "play" }
    val libraryTotal: Int? = stats.data?.totalTracks
    val tabLabels = listOf(
        "library" + (libraryTotal?.let { " · $it" } ?: ""),
        "player",
        "playlists" + (playlists.data.orEmpty().size.takeIf { it > 0 }?.let { " · $it" } ?: ""),
        "stats",
    )

    // ── Layout ────────────────────────────────────────────────────────
    Box(Modifier.fillMaxSize()) {
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item(key = "header") {
                PageHeader(
                    title = "Music",
                    sub = "${libraryTotal?.toString() ?: "—"} tracks · " +
                        "$playingCount room" + (if (playingCount == 1) "" else "s") + " playing",
                    actions = {
                        IconButton(onClick = { pickFiles.launch("*/*") }, enabled = !ui.uploading) {
                            Icon(
                                Icons.Filled.Upload,
                                contentDescription = "upload",
                                tint = if (ui.uploading) Domovoi.colors.fgFaint else Domovoi.colors.brand,
                            )
                        }
                        IconButton(onClick = onRescan) {
                            Icon(
                                Icons.Filled.Refresh,
                                contentDescription = "rescan library",
                                tint = Domovoi.colors.fgMuted,
                            )
                        }
                        IconButton(onClick = onEnrich) {
                            Icon(
                                Icons.Filled.AutoAwesome,
                                contentDescription = "enrich tags",
                                tint = Domovoi.colors.fgMuted,
                            )
                        }
                    },
                )
            }

            item(key = "now-playing") {
                if (nowPlaying.data == null && nowPlaying.loading) {
                    LoadingState()
                } else {
                    NowPlayingStrip(
                        npList, tick,
                        onPlayRandom = onPlayRandom,
                        onPause = { roomCtl("pause", it, "pause") },
                        onResume = { roomCtl("resume", it, "resume") },
                        onSkip = { roomCtl("skip", it, "skip") },
                        onStop = { roomCtl("stop", it, "stop") },
                        onFavorite = onFavoriteNp,
                    )
                }
            }

            item(key = "tabs") {
                ScrollableTabRow(
                    selectedTabIndex = ui.tab,
                    containerColor = Color.Transparent,
                    contentColor = Domovoi.colors.fg,
                    edgePadding = 0.dp,
                ) {
                    tabLabels.forEachIndexed { i, label ->
                        Tab(
                            selected = ui.tab == i,
                            onClick = { ui.tab = i },
                            text = {
                                Text(
                                    label,
                                    style = MaterialTheme.typography.labelLarge,
                                    color = if (ui.tab == i) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                                )
                            },
                        )
                    }
                }
            }

            when (ui.tab) {
                0 -> libraryTab(
                    ui = ui,
                    lib = lib,
                    libraryTotal = libraryTotal,
                    pages = pages,
                    realPlaylists = realPlaylists,
                    onSelect = { ui.detailTrack = it },
                    onToggleFavorite = onToggleFavorite,
                    onAddToPlaylist = { ui.addTrack = it },
                    onBulkAdd = onBulkAdd,
                    onBrowserPlay = onBrowserPlay,
                    onQueueTrack = onQueueTrack,
                    onPlayNext = onPlayNext,
                    onSaveToDevice = onSaveToDevice,
                )
                1 -> item(key = "player") {
                    PlayerPanel(rooms, onSaveQueue = { ui.saveQueueOpen = true })
                }
                2 -> playlistsTab(
                    playlists = playlists,
                    onSelect = { ui.openPlaylist = it },
                    onPlay = onPlayPlaylist,
                )
                3 -> statsTab(stats = stats)
            }
        }

        // ── Overlaid drawers (full-screen sheets on compact width) ────
        ui.detailTrack?.let { t ->
            TrackDrawer(
                track = t,
                rooms = rooms,
                onClose = { ui.detailTrack = null },
                onBrowserPlay = onBrowserPlay,
                onQueueTrack = onQueueTrack,
                refreshLib = lib.refresh,
                refreshStats = stats.refresh,
                refreshNP = nowPlaying.refresh,
            )
        }
        ui.openPlaylist?.let { p ->
            PlaylistDrawer(
                playlist = p,
                rooms = rooms,
                onClose = { ui.openPlaylist = null },
                onEdited = { ui.openPlaylist = it },
                refreshPlaylists = playlists.refresh,
                refreshNP = nowPlaying.refresh,
            )
        }
        ui.addTrack?.let { t ->
            AddToPlaylistSheet(
                track = t,
                onClose = { ui.addTrack = null },
                onMutated = {
                    lib.refresh()
                    playlists.refresh()
                    nowPlaying.refresh()
                },
            )
        }
    }

    if (ui.saveQueueOpen) {
        PromptDialog(
            title = "save queue as playlist",
            placeholder = "playlist name…",
            confirmLabel = "create",
            onConfirm = { name ->
                scope.launch {
                    runCatching {
                        val created = app.api.post("/api/playlists", buildJsonObject {
                            put("name", name)
                        })
                        val pid = created.jsonObject["id"]?.jsonPrimitive?.longOrNull
                            ?: error("no playlist id returned")
                        val ids = app.player.queue.value
                            .filter { it.kind == PlayKind.Library }
                            .map { it.id }
                        var added = 0
                        ids.forEach { tid ->
                            runCatching {
                                app.api.post("/api/playlists/$pid/tracks", buildJsonObject {
                                    put("track_id", tid)
                                })
                            }.onSuccess { added++ }
                        }
                        added
                    }.onSuccess { added ->
                        toast("created $name · added $added track" + (if (added == 1) "" else "s"))
                        playlists.refresh()
                    }.onFailure { toast("save failed: ${it.message}") }
                }
            },
            onDismiss = { ui.saveQueueOpen = false },
        )
    }
}

private fun displayName(context: Context, uri: Uri): String {
    context.contentResolver.query(
        uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null,
    )?.use { c ->
        val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (i >= 0 && c.moveToFirst()) {
            val name = c.getString(i)
            if (!name.isNullOrBlank()) return name
        }
    }
    return uri.lastPathSegment ?: "upload"
}
