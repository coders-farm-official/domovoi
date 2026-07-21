package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Track detail drawer — metadata, play-in-room chips, local play/queue,
 *  delete (optionally with the file on disk). Mirrors the web Drawer. */
@Composable
internal fun TrackDrawer(
    track: LibraryTrack,
    rooms: List<String>,
    onClose: () -> Unit,
    onBrowserPlay: (LibraryTrack) -> Unit,
    onQueueTrack: (LibraryTrack) -> Unit,
    refreshLib: () -> Unit,
    refreshStats: () -> Unit,
    refreshNP: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var room by remember(track.id) { mutableStateOf(rooms.firstOrNull() ?: "kitchen") }
    var alsoFile by remember(track.id) { mutableStateOf(false) }
    var confirmDelete by remember(track.id) { mutableStateOf(false) }

    fun saveToDevice() = saveTrackToDevice(context, app, toast, track)

    fun playInRoom() {
        toast("playing \"${track.title ?: "track"}\" in $room…")
        // Close immediately so the tap feels responsive; failure still toasts.
        onClose()
        scope.launch {
            runCatching {
                app.api.post("/api/music/play-track", buildJsonObject {
                    put("room_id", room)
                    put("track_id", track.id)
                })
            }.onSuccess { refreshNP() }
                .onFailure { toast("play failed: ${it.message}") }
        }
    }

    DrawerScaffold(onClose = onClose, widthDp = 420) {
        DrawerHeader(eyebrow = "track · #${track.id}", onClose = onClose)
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState()),
        ) {
            // Identity
            Row(
                Modifier.fillMaxWidth().padding(16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Box(
                    Modifier
                        .size(64.dp)
                        .background(
                            Brush.linearGradient(
                                listOf(Color(0xFFF2CD8C), Color(0xFFDD8A2E)),
                            ),
                            RoundedCornerShape(8.dp),
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Filled.MusicNote, contentDescription = null,
                        tint = Color.White.copy(alpha = 0.85f), modifier = Modifier.size(28.dp),
                    )
                }
                Column(Modifier.weight(1f)) {
                    Text(
                        track.title ?: "unknown title",
                        style = MaterialTheme.typography.titleMedium,
                        color = Domovoi.colors.fg,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        track.artist ?: "unknown artist",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        track.album ?: "—",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgSubtle,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)

            // Metadata
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                MetaRow("duration") { MonoText(fmtDur(track.durationSec)) }
                MetaRow("source") {
                    Pill(track.source ?: "manual", if (track.source != null) Tone.Brand else Tone.Idle)
                }
                MetaRow("source id") { MonoText(track.sourceId ?: "—") }
                MetaRow("added") { MonoText(relTime(track.addedAt)) }
                MetaRow("added via") {
                    Pill(track.addedVia ?: "—", if (track.addedVia == "voice") Tone.Brand else Tone.Idle)
                }
                MetaRow("enriched") {
                    MonoText(if (track.enrichedAt != null) relTime(track.enrichedAt) else "—")
                }
                MetaRow("path") {
                    Text(
                        track.filePath ?: "—",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)

            // Play targets
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
                    Button(onClick = { playInRoom() }) {
                        Icon(
                            Icons.Filled.PlayArrow, contentDescription = null,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.size(6.dp))
                        Text("play in $room")
                    }
                    OutlinedButton(onClick = { onBrowserPlay(track) }) {
                        Icon(
                            Icons.Filled.Headphones, contentDescription = null,
                            modifier = Modifier.size(16.dp), tint = Domovoi.colors.fg,
                        )
                        Spacer(Modifier.size(6.dp))
                        Text("play here", color = Domovoi.colors.fg)
                    }
                    OutlinedButton(onClick = { onQueueTrack(track) }) {
                        Icon(
                            Icons.Filled.QueueMusic, contentDescription = null,
                            modifier = Modifier.size(16.dp), tint = Domovoi.colors.fg,
                        )
                        Spacer(Modifier.size(6.dp))
                        Text("queue", color = Domovoi.colors.fg)
                    }
                    OutlinedButton(onClick = { saveToDevice() }) {
                        Icon(
                            Icons.Filled.Download, contentDescription = null,
                            modifier = Modifier.size(16.dp), tint = Domovoi.colors.fg,
                        )
                        Spacer(Modifier.size(6.dp))
                        Text("save", color = Domovoi.colors.fg)
                    }
                }
            }
        }

        // Danger footer
        HorizontalDivider(color = Domovoi.colors.border)
        Column(
            Modifier
                .fillMaxWidth()
                .background(Domovoi.colors.sunken)
                .padding(horizontal = 16.dp, vertical = 10.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = alsoFile, onCheckedChange = { alsoFile = it })
                Text(
                    "also delete file on disk",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            TextButton(onClick = { confirmDelete = true }) {
                Icon(
                    Icons.Filled.Delete, contentDescription = null,
                    tint = Domovoi.colors.err, modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.size(6.dp))
                Text("delete track", color = Domovoi.colors.err)
            }
        }
    }

    if (confirmDelete) {
        ConfirmDialog(
            title = "delete track",
            body = if (alsoFile) {
                "Permanently delete the file for \"${track.title ?: "track"}\" from disk? " +
                    "This can't be undone.\n\n${track.filePath ?: ""}"
            } else {
                "Remove \"${track.title ?: "track"}\" from the library? " +
                    "The file stays on disk; a rescan will re-add it."
            },
            confirmLabel = "delete",
            destructive = true,
            onConfirm = {
                scope.launch {
                    runCatching {
                        app.api.delete(
                            "/api/music/library/${track.id}" +
                                (if (alsoFile) "?also_file=true" else ""),
                        )
                    }.onSuccess {
                        toast(
                            if (alsoFile) {
                                "deleted \"${track.title ?: "track"}\" · file removed from disk"
                            } else {
                                "removed \"${track.title ?: "track"}\" from library (file kept; rescan will re-add)"
                            },
                        )
                        onClose()
                        refreshLib()
                        refreshStats()
                    }.onFailure { toast("delete failed: ${it.message}") }
                }
            },
            onDismiss = { confirmDelete = false },
        )
    }
}

@Composable
private fun MetaRow(label: String, content: @Composable () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label.lowercase(),
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.width(96.dp),
        )
        Box(Modifier.weight(1f)) { content() }
    }
}

@Composable
private fun MonoText(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        fontFamily = FontFamily.Monospace,
        color = Domovoi.colors.fg,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
    )
}
