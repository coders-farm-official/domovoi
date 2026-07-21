package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SubdirectoryArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.ApiState
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi

/** Library tab — server-paginated track list with filters, bulk selection
 *  and per-row actions. Mirrors web LibraryTab. */
internal fun LazyListScope.libraryTab(
    ui: MusicUi,
    lib: ApiState<LibraryPage>,
    libraryTotal: Int?,
    pages: Int,
    realPlaylists: List<Playlist>,
    onSelect: (LibraryTrack) -> Unit,
    onToggleFavorite: (LibraryTrack) -> Unit,
    onAddToPlaylist: (LibraryTrack) -> Unit,
    onBulkAdd: (Long, List<Long>) -> Unit,
    onBrowserPlay: (LibraryTrack) -> Unit,
    onQueueTrack: (LibraryTrack) -> Unit,
    onPlayNext: (LibraryTrack) -> Unit,
    onSaveToDevice: (LibraryTrack) -> Unit,
) {
    val tracks = lib.data?.items.orEmpty()
    val total = lib.data?.total ?: 0

    item(key = "lib-filters") {
        LibraryFilters(ui, tracks, total, libraryTotal)
    }
    if (ui.selectedIds.isNotEmpty()) {
        item(key = "lib-bulk") { BulkBar(ui, realPlaylists, onBulkAdd) }
    }

    val filtered = ui.q.isNotBlank() || ui.source != "all" || ui.favoritedOnly
    when {
        lib.loading && tracks.isEmpty() -> item(key = "lib-loading") { LoadingState() }
        lib.error != null && tracks.isEmpty() -> item(key = "lib-error") {
            ErrorState(lib.error ?: "request failed", lib.refresh)
        }
        tracks.isEmpty() -> item(key = "lib-empty") {
            if (filtered) {
                EmptyState(
                    title = "no tracks match",
                    sub = when {
                        ui.q.isNotBlank() -> "q = \"${ui.q}\""
                        ui.favoritedOnly -> "no favorited tracks yet"
                        else -> "no ${ui.source} tracks"
                    },
                    action = {
                        TextButton(onClick = {
                            ui.q = ""; ui.source = "all"; ui.favoritedOnly = false; ui.page = 0
                        }) { Text("clear filters", color = Domovoi.colors.brand) }
                    },
                )
            } else {
                EmptyState(title = "library is empty", sub = "ask any room: download …")
            }
        }
        else -> items(tracks, key = { "track-${it.id}" }) { t ->
            LibraryRow(
                t,
                checked = t.id in ui.selectedIds,
                onCheck = {
                    if (t.id in ui.selectedIds) ui.selectedIds.remove(t.id)
                    else ui.selectedIds.add(t.id)
                },
                onClick = { onSelect(t) },
                onToggleFavorite = { onToggleFavorite(t) },
                onAddToPlaylist = { onAddToPlaylist(t) },
                onBrowserPlay = { onBrowserPlay(t) },
                onQueueTrack = { onQueueTrack(t) },
                onPlayNext = { onPlayNext(t) },
                onSaveToDevice = { onSaveToDevice(t) },
            )
        }
    }

    item(key = "lib-pager") {
        Row(
            Modifier.fillMaxWidth().padding(top = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "page ${ui.page + 1} of $pages",
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f),
            )
            SmallIconButton(
                Icons.Filled.ChevronLeft, "previous page",
                enabled = ui.page > 0,
            ) { ui.page = (ui.page - 1).coerceAtLeast(0) }
            SmallIconButton(
                Icons.Filled.ChevronRight, "next page",
                enabled = ui.page < pages - 1,
            ) { ui.page = (ui.page + 1).coerceAtMost(pages - 1) }
        }
    }
}

@Composable
private fun LibraryFilters(
    ui: MusicUi,
    pageTracks: List<LibraryTrack>,
    total: Int,
    libraryTotal: Int?,
) {
    val filtered = ui.q.isNotBlank() || ui.source != "all" || ui.favoritedOnly
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = ui.q,
            onValueChange = { ui.q = it; ui.page = 0 },
            placeholder = {
                Text("search title, artist, album, path…", color = Domovoi.colors.fgSubtle)
            },
            leadingIcon = {
                Icon(
                    Icons.Filled.Search, contentDescription = null,
                    tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(18.dp),
                )
            },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Data-driven: options are the distinct source values actually
            // present in the library (open enum — plugins stamp their own
            // slugs). The active selection always stays listed.
            val sourceOptions = (ui.seenSources.sorted() + ui.source)
                .filter { it != "all" }
                .distinct()
            SelectMenu(
                "source", ui.source,
                listOf("all" to "all") + sourceOptions.map { it to it },
                onSelect = { ui.source = it; ui.page = 0 },
            )
            SelectMenu(
                "sort", ui.sort,
                listOf(
                    "added_desc" to "newest", "added_asc" to "oldest",
                    "title" to "title", "artist" to "artist", "duration" to "longest",
                ),
                onSelect = { ui.sort = it; ui.page = 0 },
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(
                    checked = ui.favoritedOnly,
                    onCheckedChange = { ui.favoritedOnly = it; ui.page = 0 },
                )
                Text(
                    "favorites only",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val allSelected = pageTracks.isNotEmpty() && pageTracks.all { it.id in ui.selectedIds }
            Checkbox(
                checked = allSelected,
                onCheckedChange = {
                    if (allSelected) {
                        pageTracks.forEach { ui.selectedIds.remove(it.id) }
                    } else {
                        pageTracks.forEach { if (it.id !in ui.selectedIds) ui.selectedIds.add(it.id) }
                    }
                },
            )
            Text(
                "select page",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f),
            )
            Text(
                if (filtered) "$total of ${libraryTotal ?: "—"}"
                else "$total track" + (if (total == 1) "" else "s"),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgSubtle,
            )
        }
    }
}

@Composable
private fun BulkBar(
    ui: MusicUi,
    realPlaylists: List<Playlist>,
    onBulkAdd: (Long, List<Long>) -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 6.dp)
            .horizontalScroll(rememberScrollState()),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            "${ui.selectedIds.size} selected",
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fg,
        )
        SelectMenu(
            label = null,
            value = ui.bulkPid?.toString() ?: "",
            options = listOf("" to "add to playlist…") +
                realPlaylists.map { it.id.toString() to it.name },
            onSelect = { ui.bulkPid = it.toLongOrNull() },
        )
        Button(
            onClick = { ui.bulkPid?.let { pid -> onBulkAdd(pid, ui.selectedIds.toList()) } },
            enabled = ui.bulkPid != null,
        ) { Text("add ${ui.selectedIds.size}") }
        TextButton(onClick = { ui.selectedIds.clear() }) {
            Text("clear", color = Domovoi.colors.fgMuted)
        }
    }
}

@Composable
private fun LibraryRow(
    t: LibraryTrack,
    checked: Boolean,
    onCheck: () -> Unit,
    onClick: () -> Unit,
    onToggleFavorite: () -> Unit,
    onAddToPlaylist: () -> Unit,
    onBrowserPlay: () -> Unit,
    onQueueTrack: () -> Unit,
    onPlayNext: () -> Unit,
    onSaveToDevice: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = checked, onCheckedChange = { onCheck() })
            SmallIconButton(Icons.Filled.PlayArrow, "play on this device", tint = Domovoi.colors.fg) {
                onBrowserPlay()
            }
            Column(Modifier.weight(1f).padding(start = 4.dp)) {
                Text(
                    t.title ?: "—",
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    t.artist ?: "—",
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
                modifier = Modifier.padding(start = 8.dp),
            )
        }
        Row(
            Modifier
                .fillMaxWidth()
                .padding(start = 44.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                Modifier
                    .weight(1f)
                    .horizontalScroll(rememberScrollState()),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Pill(t.addedVia ?: "—", if (t.addedVia == "voice") Tone.Brand else Tone.Idle)
                Pill(t.source ?: "manual", if (t.source != null) Tone.Brand else Tone.Idle)
                if (!t.album.isNullOrBlank()) {
                    Text(
                        t.album,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Text(
                    relTime(t.addedAt),
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = Domovoi.colors.fgSubtle,
                )
            }
            SmallIconButton(Icons.Filled.QueueMusic, "add to queue") { onQueueTrack() }
            SmallIconButton(Icons.Filled.SubdirectoryArrowRight, "play next") { onPlayNext() }
            SmallIconButton(
                if (t.favorited) Icons.Filled.Favorite else Icons.Filled.FavoriteBorder,
                if (t.favorited) "unfavorite" else "favorite",
                tint = if (t.favorited) Domovoi.colors.brand else Domovoi.colors.fgMuted,
            ) { onToggleFavorite() }
            SmallIconButton(Icons.Filled.Add, "add to playlist") { onAddToPlaylist() }
            SmallIconButton(Icons.Filled.Download, "save to device") { onSaveToDevice() }
        }
    }
    HorizontalDivider(color = Domovoi.colors.borderSoft)
}
