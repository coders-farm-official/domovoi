package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.filled.SubdirectoryArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.PagedState
import com.domovoi.app.net.Pager
import com.domovoi.app.net.Paging
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.net.toPage
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadMoreNearEnd
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PagedFooter
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.launchAction
import com.domovoi.app.ui.components.rememberPager
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import java.net.URLEncoder

/*
 * Music → Genres (owner, 2026-11-12): the library by genre. The web twin is
 * music.jsx GenresTab / GenreDrawer; the server is web/backend/api/
 * music_genres.py (V050).
 *
 * Genres come from the files' own tags. For tracks that were in the library
 * before genres were read, the server reads them in the background; until it
 * is done (`reading`) the tab says "reading genres from your files… N of M"
 * and re-reads itself every [GENRE_READ_POLL_MS], rather than passing a
 * partial list off as the whole one. Read tracks whose tags name no genre
 * are one last row, "no genre", so nothing is hidden.
 */

/** One genre row of `GET /api/music/genres`. Key "" is the no-genre bucket ([NO_GENRE_KEY]). */
@Serializable
data class GenreRow(
    val key: String = "",
    val name: String = "",
    @SerialName("track_count") val trackCount: Int = 0,
    @SerialName("cover_track_id") val coverTrackId: Long? = null,
)

/** The rest of a `GET /api/music/genres` answer: how far the genre read has got. */
@Serializable
data class GenreProgress(
    @SerialName("tags_read") val tagsRead: Int = 0,
    @SerialName("tags_total") val tagsTotal: Int = 0,
    val reading: Boolean = false,
    @SerialName("untagged_count") val untaggedCount: Int = 0,
) {
    companion object {
        /** From a page's [com.domovoi.app.net.Page.extra]; null before any answer. */
        fun of(extra: JsonObject?): GenreProgress? =
            extra?.let { runCatching { DomovoiJson.decodeFromJsonElement(serializer(), it) }.getOrNull() }
    }
}

/** `GET /api/music/genre?key=`: one genre and every track id (artist order), for a room. */
@Serializable
data class GenreDetail(
    val key: String = "",
    val name: String = "",
    @SerialName("track_count") val trackCount: Int = 0,
    @SerialName("track_ids") val trackIds: List<Long> = emptyList(),
    @SerialName("track_ids_truncated") val trackIdsTruncated: Boolean = false,
)

/** The genre key of the tracks whose tags name no genre. */
internal const val NO_GENRE_KEY = ""
internal const val GENRE_READ_POLL_MS = 10_000L

private val countFmt = java.text.NumberFormat.getIntegerInstance(java.util.Locale.US)

/** "reading genres from your files… 1,200 of 5,232" while the server reads, else null. */
internal fun genreReadingLine(p: GenreProgress?): String? =
    if (p != null && p.reading) {
        "reading genres from your files… ${countFmt.format(p.tagsRead)} of ${countFmt.format(p.tagsTotal)}"
    } else null

/**
 * The rows the tab draws: the server's genres, then — once the whole list is
 * loaded and no search narrows it — "no genre" with the read tracks that have
 * none, last, as the web does.
 */
internal fun genreListRows(items: List<GenreRow>, p: GenreProgress?, q: String, hasMore: Boolean): List<GenreRow> {
    val untagged = p?.untaggedCount ?: 0
    return if (q.isBlank() && !hasMore && untagged > 0) {
        items + GenreRow(key = NO_GENRE_KEY, name = "no genre", trackCount = untagged)
    } else items
}

/** `GET /api/music/genres` for a search and sort (paging added by the pager). */
internal fun genresPath(q: String, sort: String): String = buildString {
    append("/api/music/genres?sort=").append(sort)
    if (q.isNotBlank()) append("&q=").append(URLEncoder.encode(q, "UTF-8"))
}

/** One genre's songs as library rows, by artist (the web's ?genre=&sort=artist). */
internal fun genreSongsPath(key: String): String =
    "/api/music/library?genre=" + URLEncoder.encode(key, "UTF-8") + "&sort=artist"

private fun tracksWord(n: Int) = "$n track" + (if (n == 1) "" else "s")

/** The Genres tab's state, made by [rememberGenresTab] while the tab is open. */
internal class GenresTabState(
    val list: PagedState<GenreRow>,
    val pager: Pager<GenreRow>,
    val progress: GenreProgress?,
)

@Composable
internal fun rememberGenresTab(ui: MusicUi): GenresTabState {
    var qDebounced by remember { mutableStateOf(ui.genreQ) }
    LaunchedEffect(ui.genreQ) {
        delay(250)
        qDebounced = ui.genreQ
    }
    val path = remember(qDebounced, ui.genreSort) { genresPath(qDebounced, ui.genreSort) }
    val pager = rememberPager<GenreRow>(
        path, keyOf = { it.key }, eventTypes = setOf("library.indexer.changed"),
    ) { a, req -> a.api.get(Paging.path(path, req)).toPage<GenreRow>(loadedBefore = req.offset) }
    val list by pager.state.collectAsState()
    val progress = GenreProgress.of(list.extra)
    // While the server is still reading tags, re-read every 10 s so the list
    // (and the "N of M") fills in without a pull.
    val reading = progress?.reading == true
    LaunchedEffect(reading, pager) {
        while (reading) {
            delay(GENRE_READ_POLL_MS)
            pager.reload()
        }
    }
    return GenresTabState(list, pager, progress)
}

internal fun LazyListScope.genresTab(
    ui: MusicUi,
    state: GenresTabState,
    onOpen: (GenreRow) -> Unit,
) {
    val list = state.list
    val rows = genreListRows(list.items, state.progress, ui.genreQ, list.hasMore)
    val reading = state.progress?.reading == true

    item(key = "genres-filters") { GenreFilters(ui, list.total ?: 0) }
    genreReadingLine(state.progress)?.let { line ->
        item(key = "genres-reading") { GenreReadingLine(line) }
    }
    when {
        list.loading && rows.isEmpty() && !list.loaded -> item(key = "genres-loading") { LoadingState() }
        list.error != null && rows.isEmpty() -> item(key = "genres-error") {
            ErrorState(list.error ?: "request failed", state.pager::retry)
        }
        rows.isEmpty() -> item(key = "genres-empty") {
            val q = ui.genreQ
            if (q.isNotBlank()) {
                EmptyState(title = "no genres match", sub = "q = “$q”") {
                    TextButton(onClick = { ui.genreQ = "" }) { Text("clear search", color = Domovoi.colors.brand) }
                }
            } else {
                EmptyState(
                    title = if (reading) "no genres found yet" else "no genres yet",
                    sub = if (reading) "genres show up here as your files are read"
                    else "songs whose tags name a genre show up here",
                )
            }
        }
        else -> items(rows, key = { "genre-${it.key}" }) { g -> GenreListRow(g) { onOpen(g) } }
    }
    if (list.items.isNotEmpty()) {
        item(key = "genres-footer") {
            PagedFooter(list, "genres", onRetry = state.pager::retry, onLoadMore = state.pager::loadMore)
        }
    }
}

@Composable
private fun GenreFilters(ui: MusicUi, total: Int) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = ui.genreQ,
            onValueChange = { ui.genreQ = it },
            placeholder = { Text("search genres…", color = Domovoi.colors.fgSubtle) },
            leadingIcon = {
                Icon(
                    Icons.Filled.Search, contentDescription = null,
                    tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(18.dp),
                )
            },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            SelectMenu(
                "sort", ui.genreSort,
                listOf("name" to "name", "tracks" to "most tracks"),
                onSelect = { ui.genreSort = it },
            )
            Spacer(Modifier.weight(1f))
            Text(
                "$total genre" + (if (total == 1) "" else "s"),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgFaint,
            )
        }
    }
}

@Composable
private fun GenreReadingLine(line: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .semantics { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        CircularProgressIndicator(
            color = Domovoi.colors.brand, strokeWidth = 2.dp, modifier = Modifier.size(12.dp),
        )
        Text(
            "$line · the list fills in as it goes",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
        )
    }
}

@Composable
private fun GenreListRow(g: GenreRow, onClick: () -> Unit) {
    val muted = g.key == NO_GENRE_KEY
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                g.name,
                style = MaterialTheme.typography.titleSmall,
                color = if (muted) Domovoi.colors.fgMuted else Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                tracksWord(g.trackCount),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgMuted,
            )
        }
        Icon(
            Icons.Filled.KeyboardArrowRight, contentDescription = null,
            tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp),
        )
    }
    HorizontalDivider(color = Domovoi.colors.borderSoft)
}

/**
 * One genre: its songs by artist, loaded as they scroll, with play / shuffle /
 * add to queue in a room (every track id the server gave, at most
 * [ROOM_TRACK_CAP]) and play / shuffle / queue on this phone (the songs
 * loaded so far). Mirrors web GenreDrawer.
 */
@Composable
internal fun GenreDrawer(
    genre: GenreRow,
    rooms: List<String>,
    onClose: () -> Unit,
    onSelectTrack: (LibraryTrack) -> Unit,
    refreshNP: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    // The app's scope: "play" closes the drawer before the server answers.
    val scope = app.scope
    val server by app.prefs.serverUrl.collectAsState()

    // The ids for a room, tagged with the server and genre they were read
    // for: an answer that lands after either changed is never sent on.
    val detail = rememberApi(genre.key, eventTypes = setOf("library.indexer.changed")) { a ->
        val asked = a.prefs.serverUrl.value
        val d = a.api.get("/api/music/genre?key=" + URLEncoder.encode(genre.key, "UTF-8")).decode<GenreDetail>()
        asked to d
    }
    val d = detail.data?.takeIf { (s, g) -> s == server && g.key == genre.key }?.second
    val ids = d?.trackIds.orEmpty()
    val count = d?.trackCount ?: genre.trackCount

    val songsPath = remember(genre.key) { genreSongsPath(genre.key) }
    val songsPager = rememberPager<LibraryTrack>(
        songsPath, pageSize = 100, keyOf = { it.id }, eventTypes = setOf("library.indexer.changed"),
    ) { a, req -> a.api.get(Paging.path(songsPath, req)).toPage<LibraryTrack>(loadedBefore = req.offset) }
    val songs by songsPager.state.collectAsState()
    val loaded = songs.items

    var picked by remember(genre.key) { mutableStateOf<String?>(null) }
    val room = picked?.takeIf { it in rooms } ?: rooms.firstOrNull()

    fun playInRoom(shuffle: Boolean) {
        val r = room ?: return
        val send = roomTrackIds(ids, shuffle)
        if (send.isEmpty()) return
        toast((if (shuffle) "shuffling" else "playing") + " ${genre.name} in $r…")
        scope.launchAction(
            action = {
                app.api.post("/api/music/play-tracks", buildJsonObject {
                    put("room_id", r)
                    putJsonArray("track_ids") { send.forEach { add(it) } }
                    put("device_id", app.prefs.deviceId)
                })
            },
            onSuccess = { refreshNP() },
            onFailure = { toast((if (shuffle) "shuffle" else "play") + " failed: ${it.message}") },
        )
        onClose()
    }

    val queueSong = rememberSongQueue()

    fun queueInRoom() {
        val r = room ?: return
        val send = roomTrackIds(ids, shuffle = false)
        if (send.isEmpty()) return
        scope.launchAction(
            action = {
                app.api.post("/api/music/queue/${android.net.Uri.encode(r)}/add", buildJsonObject {
                    putJsonArray("track_ids") { send.forEach { add(it) } }
                    put("device_id", app.prefs.deviceId)
                })
            },
            onSuccess = {
                toast("added ${send.size} song" + (if (send.size == 1) "" else "s") + " · ${genre.name} to $r's queue")
                refreshNP()
            },
            onFailure = { toast("add failed: ${it.message}") },
        )
    }

    DrawerScaffold(onClose = onClose, widthDp = 460) {
        DrawerHeader(eyebrow = "genre", onClose = onClose)
        val listState = rememberLazyListState()
        LoadMoreNearEnd(listState, songsPager)
        LazyColumn(Modifier.weight(1f), state = listState) {
            item(key = "genre-head") {
                Column {
                    Column(Modifier.fillMaxWidth().padding(16.dp)) {
                        Text(
                            genre.name,
                            style = MaterialTheme.typography.titleMedium,
                            color = Domovoi.colors.fg,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            tracksWord(count) + (if (genre.key == NO_GENRE_KEY) " · their tags name no genre" else ""),
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                    Column(Modifier.fillMaxWidth().padding(16.dp)) {
                        // A room: only with a room online (never a made-up one).
                        SectionLabel(if (rooms.isEmpty()) "play in room · no rooms online" else "play in room")
                        if (rooms.isNotEmpty()) {
                            Spacer(Modifier.size(8.dp))
                            Row(Modifier.horizontalScroll(rememberScrollState())) {
                                RoomPickRow(rooms, room.orEmpty()) { picked = it }
                            }
                            Spacer(Modifier.size(12.dp))
                            Row(
                                Modifier.horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                val can = room != null && ids.isNotEmpty()
                                Button(onClick = { playInRoom(false) }, enabled = can) {
                                    Icon(Icons.Filled.PlayArrow, null, Modifier.size(16.dp))
                                    Spacer(Modifier.size(6.dp))
                                    Text("play")
                                }
                                OutlinedButton(onClick = { playInRoom(true) }, enabled = can) {
                                    Icon(Icons.Filled.Shuffle, null, Modifier.size(16.dp), tint = Domovoi.colors.fg)
                                    Spacer(Modifier.size(6.dp))
                                    Text("shuffle", color = Domovoi.colors.fg)
                                }
                                OutlinedButton(onClick = { queueInRoom() }, enabled = can) {
                                    Icon(Icons.Filled.QueueMusic, null, Modifier.size(16.dp), tint = Domovoi.colors.fg)
                                    Spacer(Modifier.size(6.dp))
                                    Text("add to queue", color = Domovoi.colors.fg)
                                }
                            }
                            roomCapNote(ids.size)?.let { note ->
                                Spacer(Modifier.size(8.dp))
                                Text(note, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                            }
                        }
                        Spacer(Modifier.size(12.dp))
                        SectionLabel("this device")
                        Spacer(Modifier.size(8.dp))
                        Row(
                            Modifier.horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            val can = loaded.isNotEmpty()
                            OutlinedButton(onClick = {
                                playTracksHereAndSay(app.player, loaded, genre.name, false, toast, onMain = { scope.launch { it() } })
                                onClose()
                            }, enabled = can) {
                                Icon(Icons.Filled.Headphones, null, Modifier.size(16.dp), tint = Domovoi.colors.fg)
                                Spacer(Modifier.size(6.dp))
                                Text("play here", color = Domovoi.colors.fg)
                            }
                            OutlinedButton(onClick = {
                                playTracksHereAndSay(app.player, loaded, genre.name, true, toast, onMain = { scope.launch { it() } })
                                onClose()
                            }, enabled = can) {
                                Icon(Icons.Filled.Shuffle, null, Modifier.size(16.dp), tint = Domovoi.colors.fg)
                                Spacer(Modifier.size(6.dp))
                                Text("shuffle here", color = Domovoi.colors.fg)
                            }
                            OutlinedButton(onClick = {
                                queueTracksHereAndSay(app.player, loaded, genre.name, toast)
                            }, enabled = can) {
                                Icon(Icons.Filled.QueueMusic, null, Modifier.size(16.dp), tint = Domovoi.colors.fg)
                                Spacer(Modifier.size(6.dp))
                                Text("queue here", color = Domovoi.colors.fg)
                            }
                        }
                        if (songs.hasMore) {
                            Spacer(Modifier.size(8.dp))
                            Text(
                                "this device takes the " + (if (loaded.size == 1) "1 song" else "${loaded.size} songs") +
                                    " loaded so far — scroll the list for more",
                                style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgMuted,
                            )
                        }
                    }
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                }
            }
            when {
                songs.loading && loaded.isEmpty() -> item(key = "genre-songs-loading") { LoadingState() }
                songs.error != null && loaded.isEmpty() -> item(key = "genre-songs-error") {
                    ErrorState(songs.error ?: "request failed", songsPager::retry)
                }
                loaded.isEmpty() -> item(key = "genre-songs-empty") {
                    EmptyState("no songs", "nothing in the library for this choice")
                }
                else -> items(loaded, key = { "genre-song-${it.id}" }) { t ->
                    GenreSongRow(
                        t,
                        onClick = { onSelectTrack(t) },
                        onPlay = { playHereAndSay(app.player, libraryPlayItem(t), t.title ?: "track", toast) { scope.launch { it() } } },
                        // This phone's queue, or the room it casts to (batch 2 §2).
                        onPlayNext = { queueSong(t, true) },
                        onQueue = { queueSong(t, false) },
                    )
                }
            }
            if (loaded.isNotEmpty()) {
                item(key = "genre-songs-footer") {
                    PagedFooter(songs, "songs", onRetry = songsPager::retry, onLoadMore = songsPager::loadMore)
                }
            }
        }
    }
}

@Composable
private fun GenreSongRow(
    t: LibraryTrack,
    onClick: () -> Unit,
    onPlay: () -> Unit,
    onPlayNext: () -> Unit,
    onQueue: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                t.title ?: t.filePath?.substringAfterLast('/') ?: "—",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                listOfNotNull(t.artist, t.album).filter { it.isNotBlank() }.joinToString(" · ").ifEmpty { "—" },
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Text(
            fmtDur(t.durationSec),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(horizontal = 6.dp),
        )
        SmallIconButton(Icons.Filled.PlayArrow, "play on this device", tint = Domovoi.colors.fg) { onPlay() }
        SmallIconButton(Icons.Filled.SubdirectoryArrowRight, "play next") { onPlayNext() }
        SmallIconButton(Icons.Filled.QueueMusic, "add to queue") { onQueue() }
    }
    HorizontalDivider(color = Domovoi.colors.borderSoft)
}
