package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/** Playlist detail drawer — tracks in playback order, play/shuffle to a
 *  room, edit meta, up/down reorder, remove, delete. Virtual Favorites
 *  can't be edited, reordered, or deleted. Mirrors web PlaylistDrawer. */
@Composable
internal fun PlaylistDrawer(
    playlist: Playlist,
    rooms: List<String>,
    onClose: () -> Unit,
    onEdited: (Playlist) -> Unit,
    refreshPlaylists: () -> Unit,
    refreshNP: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val tracksState = rememberApi(
        playlist.id,
        eventTypes = setOf("playlists.changed", "library.indexer.changed"),
    ) { it.api.get("/api/playlists/${playlist.id}/tracks").decode<List<LibraryTrack>>() }

    var room by remember(playlist.id) { mutableStateOf(rooms.firstOrNull() ?: "kitchen") }
    var editing by remember(playlist.id) { mutableStateOf(false) }
    var editName by remember(playlist.id) { mutableStateOf("") }
    var editDesc by remember(playlist.id) { mutableStateOf("") }
    var editEmoji by remember(playlist.id) { mutableStateOf("") }
    var localOrder by remember(playlist.id) { mutableStateOf<List<LibraryTrack>?>(null) }
    var confirmDelete by remember(playlist.id) { mutableStateOf(false) }

    val items = localOrder ?: tracksState.data.orEmpty()

    fun play(shuffle: Boolean) {
        toast((if (shuffle) "shuffling" else "playing") + " ${playlist.name} in $room…")
        scope.launch {
            runCatching {
                app.api.post("/api/music/play-playlist", buildJsonObject {
                    put("room_id", room)
                    put("playlist_id", playlist.id)
                    put("shuffle", shuffle)
                })
            }.onSuccess { refreshNP() }
                .onFailure { toast((if (shuffle) "shuffle" else "play") + " failed: ${it.message}") }
        }
        onClose()
    }

    fun saveEdit() {
        scope.launch {
            runCatching {
                app.api.patch("/api/playlists/${playlist.id}", buildJsonObject {
                    put("name", editName.trim())
                    put("description", editDesc.trim())
                    put("cover_emoji", editEmoji.trim())
                })
            }.onSuccess {
                toast("playlist updated")
                editing = false
                refreshPlaylists()
                onEdited(
                    playlist.copy(
                        name = editName.trim(),
                        description = editDesc.trim(),
                        coverEmoji = editEmoji.trim(),
                    ),
                )
            }.onFailure { toast("update failed: ${it.message}") }
        }
    }

    fun move(index: Int, dir: Int) {
        val next = items.toMutableList()
        val j = index + dir
        if (index !in next.indices || j !in next.indices) return
        val moved = next.removeAt(index)
        next.add(j, moved)
        localOrder = next
        scope.launch {
            runCatching {
                app.api.patch("/api/playlists/${playlist.id}/order", buildJsonObject {
                    putJsonArray("track_ids") { next.forEach { add(it.id) } }
                })
            }.onSuccess { refreshPlaylists() }
                .onFailure {
                    toast("reorder failed: ${it.message}")
                    localOrder = null
                }
        }
    }

    fun removeTrack(t: LibraryTrack) {
        scope.launch {
            runCatching {
                app.api.delete("/api/playlists/${playlist.id}/tracks/${t.id}")
            }.onSuccess {
                toast("removed \"${t.title ?: "track"}\" from ${playlist.name}")
                localOrder = null
                tracksState.refresh()
                refreshPlaylists()
            }.onFailure { toast("remove failed: ${it.message}") }
        }
    }

    DrawerScaffold(onClose = onClose, widthDp = 460) {
        DrawerHeader(
            eyebrow = (if (playlist.isVirtual) "virtual" else "playlist") + " · #${playlist.id}",
            onClose = onClose,
        )
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState()),
        ) {
            // Identity block
            Row(
                Modifier.fillMaxWidth().padding(16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                PlaylistCover(playlist, 56)
                Column(Modifier.weight(1f)) {
                    Text(
                        playlist.name,
                        style = MaterialTheme.typography.titleMedium,
                        color = Domovoi.colors.fg,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        "${playlist.trackCount} track" + (if (playlist.trackCount == 1) "" else "s"),
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                    if (!playlist.description.isNullOrBlank()) {
                        Text(
                            playlist.description,
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)

            // Play in room
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                SectionLabel("play in room")
                Spacer(Modifier.size(8.dp))
                Row(Modifier.horizontalScroll(rememberScrollState())) {
                    RoomPickRow(rooms, room) { room = it }
                }
                Spacer(Modifier.size(12.dp))
                Row(
                    Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Button(onClick = { play(shuffle = false) }) {
                        Icon(
                            Icons.Filled.PlayArrow, contentDescription = null,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.size(6.dp))
                        Text("play")
                    }
                    OutlinedButton(onClick = { play(shuffle = true) }) {
                        Icon(
                            Icons.Filled.Shuffle, contentDescription = null,
                            modifier = Modifier.size(16.dp), tint = Domovoi.colors.fg,
                        )
                        Spacer(Modifier.size(6.dp))
                        Text(if (compactWidth()) "shuffle" else "shuffle all", color = Domovoi.colors.fg)
                    }
                    if (!playlist.isVirtual) {
                        OutlinedButton(onClick = {
                            if (editing) {
                                editing = false
                            } else {
                                editName = playlist.name
                                editDesc = playlist.description ?: ""
                                editEmoji = playlist.coverEmoji ?: ""
                                editing = true
                            }
                        }) {
                            Icon(
                                Icons.Filled.Edit, contentDescription = null,
                                modifier = Modifier.size(14.dp), tint = Domovoi.colors.fg,
                            )
                            Spacer(Modifier.size(6.dp))
                            Text(if (editing) "cancel" else "edit", color = Domovoi.colors.fg)
                        }
                    }
                }
                if (editing && !playlist.isVirtual) {
                    Spacer(Modifier.size(12.dp))
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = editName,
                            onValueChange = { editName = it },
                            placeholder = { Text("name", color = Domovoi.colors.fgSubtle) },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        OutlinedTextField(
                            value = editDesc,
                            onValueChange = { editDesc = it },
                            placeholder = { Text("description (optional)", color = Domovoi.colors.fgSubtle) },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            OutlinedTextField(
                                value = editEmoji,
                                onValueChange = { editEmoji = it.take(4) },
                                placeholder = { Text("emoji", color = Domovoi.colors.fgSubtle) },
                                singleLine = true,
                                modifier = Modifier.width(110.dp),
                            )
                            Spacer(Modifier.weight(1f))
                            Button(onClick = { saveEdit() }, enabled = editName.isNotBlank()) {
                                Text("save")
                            }
                        }
                    }
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)

            // Track list
            when {
                tracksState.loading && items.isEmpty() -> LoadingState()
                items.isEmpty() -> EmptyState(
                    "no tracks yet",
                    if (playlist.isVirtual) "favorite a track to add it here"
                    else "tap + on a library row to add tracks",
                )
                else -> Column {
                    items.forEachIndexed { i, t ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                "${i + 1}",
                                style = MaterialTheme.typography.labelSmall,
                                fontFamily = FontFamily.Monospace,
                                color = Domovoi.colors.fgFaint,
                                modifier = Modifier.width(24.dp),
                            )
                            Column(Modifier.weight(1f)) {
                                Text(
                                    t.title
                                        ?: t.filePath?.substringAfterLast('/')?.substringAfterLast('\\')
                                        ?: "—",
                                    style = MaterialTheme.typography.bodyMedium,
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
                            if (!playlist.isVirtual) {
                                SmallIconButton(
                                    Icons.Filled.KeyboardArrowUp, "move up",
                                    enabled = i > 0,
                                ) { move(i, -1) }
                                SmallIconButton(
                                    Icons.Filled.KeyboardArrowDown, "move down",
                                    enabled = i < items.size - 1,
                                ) { move(i, 1) }
                            }
                            SmallIconButton(Icons.Filled.Close, "remove from playlist") {
                                removeTrack(t)
                            }
                        }
                        if (i < items.size - 1) {
                            HorizontalDivider(color = Domovoi.colors.borderSoft)
                        }
                    }
                }
            }
        }

        // Delete footer (real playlists only)
        if (!playlist.isVirtual) {
            HorizontalDivider(color = Domovoi.colors.border)
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(Domovoi.colors.sunken)
                    .padding(16.dp),
            ) {
                TextButton(onClick = { confirmDelete = true }) {
                    Icon(
                        Icons.Filled.Delete, contentDescription = null,
                        tint = Domovoi.colors.err, modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.size(6.dp))
                    Text("delete playlist", color = Domovoi.colors.err)
                }
            }
        }
    }

    if (confirmDelete) {
        ConfirmDialog(
            title = "delete playlist",
            body = "Delete playlist \"${playlist.name}\"? Tracks stay in the library.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = {
                scope.launch {
                    runCatching { app.api.delete("/api/playlists/${playlist.id}") }
                        .onSuccess {
                            toast("deleted ${playlist.name}")
                            onClose()
                            refreshPlaylists()
                        }
                        .onFailure { toast("delete failed: ${it.message}") }
                }
            },
            onDismiss = { confirmDelete = false },
        )
    }
}
