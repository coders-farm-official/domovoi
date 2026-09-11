package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder

/**
 * Room queue tab — the web QueueTab analog (web/static/music.jsx).
 *
 * Unlike the Player tab (this phone's own queue) this is the queue the ROOM's
 * speaker plays from, held by MPD, so every client sees the same list and an
 * edit lands for everyone.
 *
 * Each row carries an unobtrusive "added by <device>" line when the server has
 * a record of who queued it — absent for voice commands and for casts from
 * before devices had names, in which case nothing is rendered rather than a
 * guess.
 *
 * Reordering is arrow buttons rather than drag: a long-press drag inside a
 * LazyColumn that is itself inside the screen's scrolling LazyColumn fights
 * the parent scroll, and two taps that always work beat a gesture that
 * sometimes doesn't.
 */

private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")

internal fun LazyListScope.queueTab(rooms: List<String>, playingRoom: String?) {
    item(key = "room-queue") { RoomQueueCard(rooms, playingRoom) }
}

@Composable
private fun RoomQueueCard(rooms: List<String>, playingRoom: String?) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    // Default to a room that's actually playing — that's the queue you opened
    // this tab to look at.
    var room by remember { mutableStateOf(playingRoom ?: rooms.firstOrNull()) }
    LaunchedEffect(playingRoom, rooms.size) {
        if (room == null) room = playingRoom ?: rooms.firstOrNull()
    }
    var busy by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }

    val deviceId = app.prefs.deviceId
    val queueState = rememberApi(room, eventTypes = setOf("music.now_playing.changed")) {
        val r = room ?: return@rememberApi RoomQueue()
        it.api.get(
            "/api/music/queue/${enc(r)}?device_id=${enc(deviceId)}",
        ).decode<RoomQueue>()
    }
    val queue = queueState.data ?: RoomQueue()
    val items = queue.items
    val editable = queue.editable

    suspend fun post(path: String, body: kotlinx.serialization.json.JsonObject) {
        app.api.post("/api/music/queue/${enc(room ?: return)}/$path", body)
    }

    fun remove(item: QueueItem) {
        if (busy) return
        scope.launch {
            busy = true
            runCatching {
                post(
                    "remove",
                    buildJsonObject {
                        put("song_ids", buildJsonArray { add(JsonPrimitive(item.songId)) })
                        put("device_id", deviceId)
                    },
                )
            }.onSuccess {
                toast("removed \"${item.title ?: "track"}\"")
                queueState.refresh()
            }.onFailure { toast("remove failed: ${it.message}") }
            busy = false
        }
    }

    fun move(item: QueueItem, toPosition: Int) {
        if (busy) return
        scope.launch {
            busy = true
            runCatching {
                post(
                    "move",
                    buildJsonObject {
                        put("song_id", item.songId)
                        put("to_position", toPosition)
                        put("device_id", deviceId)
                    },
                )
            }.onSuccess { queueState.refresh() }
                .onFailure { toast("move failed: ${it.message}") }
            busy = false
        }
    }

    fun clear() {
        scope.launch {
            busy = true
            runCatching { post("clear", buildJsonObject { put("device_id", deviceId) }) }
                .onSuccess {
                    toast("cleared ${room}'s queue")
                    queueState.refresh()
                }
                .onFailure { toast("clear failed: ${it.message}") }
            busy = false
        }
    }

    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        if (rooms.isEmpty()) {
            EmptyState("no rooms provisioned yet", "connect a satellite to bring its room online")
            return@DomovoiCard
        }

        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            SectionLabel("room")
            rooms.forEach { r ->
                FilterChip(
                    selected = room == r,
                    onClick = { room = r },
                    label = { Text(r, style = MaterialTheme.typography.labelMedium) },
                )
            }
            Spacer(Modifier.weight(1f))
            Text(
                "${items.size}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
        }

        if (items.isNotEmpty() && editable) {
            Row(
                Modifier.fillMaxWidth().padding(start = 14.dp, end = 14.dp, bottom = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(onClick = { confirmClear = true }, enabled = !busy) {
                    Icon(
                        Icons.Filled.DeleteSweep, contentDescription = null,
                        modifier = Modifier.size(14.dp),
                    )
                    Spacer(Modifier.size(4.dp))
                    Text("clear queue")
                }
            }
        }

        queue.blockedReason?.takeIf { !editable }?.let { reason ->
            Row(
                Modifier.fillMaxWidth().background(Domovoi.colors.sunken)
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Filled.Lock, contentDescription = null,
                    tint = Domovoi.colors.fgMuted, modifier = Modifier.size(13.dp),
                )
                Text(
                    "$reason. You can watch the queue but not change it.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                )
            }
        }

        HorizontalDivider(color = Domovoi.colors.borderSoft)
        when {
            queueState.loading && items.isEmpty() -> LoadingState()
            items.isEmpty() -> EmptyState(
                "queue is empty",
                "cast from the Player tab, or say \"play something\" in ${room ?: "a room"}",
            )
            else -> Column(Modifier.fillMaxWidth()) {
                items.forEachIndexed { idx, item ->
                    QueueRow(
                        item = item,
                        index = idx,
                        last = idx == items.lastIndex,
                        editable = editable && !busy,
                        onUp = { move(item, idx - 1) },
                        onDown = { move(item, idx + 1) },
                        onRemove = { remove(item) },
                    )
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                }
            }
        }
    }

    if (confirmClear) {
        ConfirmDialog(
            title = "clear ${room}'s queue?",
            body = "Empties the queue and stops playback in that room.",
            confirmLabel = "clear",
            destructive = true,
            onConfirm = { clear() },
            onDismiss = { confirmClear = false },
        )
    }
}

@Composable
private fun QueueRow(
    item: QueueItem,
    index: Int,
    last: Boolean,
    editable: Boolean,
    onUp: () -> Unit,
    onDown: () -> Unit,
    onRemove: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth()
            .background(if (item.playing) Domovoi.colors.brandSoft else Domovoi.colors.card)
            .padding(start = 12.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (item.playing) {
            Icon(
                Icons.Filled.VolumeUp, contentDescription = "now playing",
                tint = Domovoi.colors.brandPress, modifier = Modifier.size(14.dp),
            )
        } else {
            Text(
                "${index + 1}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.size(14.dp),
            )
        }
        Column(Modifier.weight(1f)) {
            Text(
                item.title ?: item.file.ifBlank { "unknown" },
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (item.playing) FontWeight.SemiBold else FontWeight.Normal,
                color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            Text(
                buildString {
                    append(item.artist ?: "unknown artist")
                    item.durationSec?.let { append(" · ").append(fmtDur(it.toDouble())) }
                    // The "added by" tag: appended to the secondary line, never
                    // a line of its own, and simply absent when unknown.
                    item.addedBy?.let { append(" · added by ").append(it) }
                },
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        if (editable) {
            IconButton(onClick = onUp, enabled = index > 0, modifier = Modifier.size(32.dp)) {
                Icon(
                    Icons.Filled.ArrowUpward, contentDescription = "move up",
                    tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(15.dp),
                )
            }
            IconButton(onClick = onDown, enabled = !last, modifier = Modifier.size(32.dp)) {
                Icon(
                    Icons.Filled.ArrowDownward, contentDescription = "move down",
                    tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(15.dp),
                )
            }
            IconButton(onClick = onRemove, modifier = Modifier.size(32.dp)) {
                Icon(
                    Icons.Filled.Close, contentDescription = "remove from queue",
                    tint = Domovoi.colors.err, modifier = Modifier.size(15.dp),
                )
            }
        }
    }
}
