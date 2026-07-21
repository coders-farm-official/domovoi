package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/** "Add to playlist" drawer for one track — memberships highlighted, tap
 *  toggles add/remove (Favorites routes through the library favorited
 *  PATCH), footer creates a new playlist. Mirrors web LibraryAddDrawer. */
@Composable
internal fun AddToPlaylistSheet(
    track: LibraryTrack,
    onClose: () -> Unit,
    onMutated: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val memberships = rememberApi(
        track.id,
        eventTypes = setOf("playlists.changed"),
    ) { it.api.get("/api/music/library/${track.id}/playlists").decode<List<Playlist>>() }
    val all = rememberApi(
        track.id,
        eventTypes = setOf("playlists.changed"),
    ) { it.api.get("/api/playlists").decode<List<Playlist>>() }

    var creating by remember(track.id) { mutableStateOf("") }

    val memberIds = memberships.data.orEmpty().map { it.id }.toSet()
    val playlists = all.data.orEmpty()

    fun toggle(p: Playlist) {
        val inIt = p.id in memberIds
        scope.launch {
            runCatching {
                when {
                    p.isVirtual -> {
                        app.api.patch("/api/music/library/${track.id}", buildJsonObject {
                            put("favorited", !inIt)
                        })
                        (if (inIt) "unfavorited" else "favorited") + " \"${track.title ?: "track"}\""
                    }
                    inIt -> {
                        app.api.delete("/api/playlists/${p.id}/tracks/${track.id}")
                        "removed from ${p.name}"
                    }
                    else -> {
                        app.api.post("/api/playlists/${p.id}/tracks", buildJsonObject {
                            put("track_id", track.id)
                        })
                        "added to ${p.name}"
                    }
                }
            }.onSuccess { msg ->
                toast(msg)
                onMutated()
                onClose()
            }.onFailure {
                toast("failed: ${it.message}")
                memberships.refresh()
                all.refresh()
            }
        }
    }

    fun create() {
        val name = creating.trim()
        if (name.isEmpty()) return
        scope.launch {
            runCatching {
                val created = app.api.post("/api/playlists", buildJsonObject { put("name", name) })
                val pid = created.jsonObject["id"]?.jsonPrimitive?.longOrNull
                    ?: error("no playlist id returned")
                app.api.post("/api/playlists/$pid/tracks", buildJsonObject {
                    put("track_id", track.id)
                })
            }.onSuccess {
                toast("created $name · added \"${track.title ?: "track"}\"")
                onMutated()
                onClose()
            }.onFailure { toast("create failed: ${it.message}") }
        }
    }

    DrawerScaffold(onClose = onClose, widthDp = 380) {
        DrawerHeader(
            eyebrow = "add to playlist",
            title = track.title ?: "track",
            sub = track.artist,
            onClose = onClose,
        )
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState()),
        ) {
            when {
                all.loading && playlists.isEmpty() -> LoadingState()
                playlists.isEmpty() -> Box(
                    Modifier.fillMaxWidth().padding(24.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        "no playlists yet — create one below",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
                else -> playlists.forEachIndexed { i, p ->
                    val inIt = p.id in memberIds
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .background(if (inIt) Domovoi.colors.brandSoft else Color.Transparent)
                            .clickable { toggle(p) }
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Icon(
                            when {
                                p.isVirtual -> Icons.Filled.Star
                                inIt -> Icons.Filled.Check
                                else -> Icons.Filled.Add
                            },
                            contentDescription = null,
                            tint = if (inIt || p.isVirtual) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                            modifier = Modifier.size(16.dp),
                        )
                        Column(Modifier.weight(1f)) {
                            Text(
                                p.name,
                                style = MaterialTheme.typography.titleSmall,
                                color = Domovoi.colors.fg,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                if (inIt) "in this playlist"
                                else "${p.trackCount} track" + (if (p.trackCount == 1) "" else "s"),
                                style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgMuted,
                            )
                        }
                    }
                    if (i < playlists.size - 1) {
                        HorizontalDivider(color = Domovoi.colors.borderSoft)
                    }
                }
            }
        }

        HorizontalDivider(color = Domovoi.colors.border)
        Column(
            Modifier
                .fillMaxWidth()
                .background(Domovoi.colors.sunken)
                .padding(14.dp),
        ) {
            SectionLabel("create new playlist")
            Row(
                Modifier.fillMaxWidth().padding(top = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = creating,
                    onValueChange = { creating = it },
                    placeholder = { Text("playlist name…", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                Button(onClick = { create() }, enabled = creating.isNotBlank()) {
                    Text("create")
                }
            }
        }
    }
}
