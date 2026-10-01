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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
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

/** The tab's own state, hoisted out of the lazy items so it survives the
 *  header scrolling out of composition. Remembered by [rememberRoomQueue]. */
@Stable
internal class RoomQueueSelection(initialRoom: String?) {
    var room by mutableStateOf(initialRoom)
    var busy by mutableStateOf(false)
    var confirmClear by mutableStateOf(false)
}

/** What [queueTab] draws: this composition's queue plus the edit actions. */
internal class RoomQueueModel(
    val sel: RoomQueueSelection,
    val queue: RoomQueue,
    val loading: Boolean,
    val remove: (QueueItem) -> Unit,
    val move: (QueueItem, Int) -> Unit,
    val clear: () -> Unit,
)

/**
 * Fetches the selected room's queue and wires its edits. Called by
 * MusicScreen while this tab is open; the rows themselves are separate lazy
 * items (see [queueTab]), so the state cannot live inside one of them.
 */
@Composable
internal fun rememberRoomQueue(rooms: List<String>, playingRoom: String?): RoomQueueModel {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    // Default to a room that's actually playing — that's the queue you opened
    // this tab to look at.
    val sel = remember { RoomQueueSelection(playingRoom ?: rooms.firstOrNull()) }
    LaunchedEffect(playingRoom, rooms.size) {
        if (sel.room == null) sel.room = playingRoom ?: rooms.firstOrNull()
    }

    val deviceId = app.prefs.deviceId
    val room = sel.room
    val queueState = rememberApi(room, eventTypes = setOf("music.now_playing.changed")) {
        val r = room ?: return@rememberApi RoomQueue()
        it.api.get(
            "/api/music/queue/${enc(r)}?device_id=${enc(deviceId)}",
        ).decode<RoomQueue>()
    }

    suspend fun post(path: String, body: kotlinx.serialization.json.JsonObject) {
        app.api.post("/api/music/queue/${enc(sel.room ?: return)}/$path", body)
    }

    fun remove(item: QueueItem) {
        if (sel.busy) return
        scope.launch {
            sel.busy = true
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
            sel.busy = false
        }
    }

    fun move(item: QueueItem, toPosition: Int) {
        if (sel.busy) return
        scope.launch {
            sel.busy = true
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
            sel.busy = false
        }
    }

    fun clear() {
        scope.launch {
            sel.busy = true
            runCatching { post("clear", buildJsonObject { put("device_id", deviceId) }) }
                .onSuccess {
                    toast("cleared ${sel.room}'s queue")
                    queueState.refresh()
                }
                .onFailure { toast("clear failed: ${it.message}") }
            sel.busy = false
        }
    }

    return RoomQueueModel(
        sel = sel,
        queue = queueState.data ?: RoomQueue(),
        loading = queueState.loading,
        remove = ::remove,
        move = ::move,
        clear = ::clear,
    )
}

/**
 * A header card (room picker, count, clear, blocked notice), then one lazy
 * item per queue entry. A room queue can hold hundreds of songs (a playlist,
 * a cast from the phone); drawing them all inside one item built every row on
 * the main thread in a single frame, the same freeze the player tab had.
 */
internal fun LazyListScope.queueTab(rooms: List<String>, rq: RoomQueueModel) {
    item(key = "room-queue-head") { RoomQueueHeader(rooms, rq) }
    if (rooms.isEmpty()) return

    val items = rq.queue.items
    when {
        rq.loading && items.isEmpty() -> item(key = "room-queue-loading") { LoadingState() }
        items.isEmpty() -> item(key = "room-queue-empty") {
            EmptyState(
                "queue is empty",
                "cast from the Player tab, or say \"play something\" in ${rq.sel.room ?: "a room"}",
            )
        }
        else -> items(
            count = items.size,
            key = { "room-queue-${items[it].songId}-$it" },
            contentType = { "room-queue-row" },
        ) { idx ->
            val item = items[idx]
            QueueRow(
                item = item,
                index = idx,
                last = idx == items.lastIndex,
                editable = rq.queue.editable && !rq.sel.busy,
                onUp = { rq.move(item, idx - 1) },
                onDown = { rq.move(item, idx + 1) },
                onRemove = { rq.remove(item) },
            )
        }
    }
}

/** The clear-queue confirmation; drawn by MusicScreen, outside the list. */
@Composable
internal fun RoomQueueDialogs(rq: RoomQueueModel) {
    if (rq.sel.confirmClear) {
        ConfirmDialog(
            title = "clear ${rq.sel.room}'s queue?",
            body = "Empties the queue and stops playback in that room.",
            confirmLabel = "clear",
            destructive = true,
            onConfirm = { rq.clear() },
            onDismiss = { rq.sel.confirmClear = false },
        )
    }
}

@Composable
private fun RoomQueueHeader(rooms: List<String>, rq: RoomQueueModel) {
    val items = rq.queue.items
    val editable = rq.queue.editable

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
                    selected = rq.sel.room == r,
                    onClick = { rq.sel.room = r },
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
                OutlinedButton(onClick = { rq.sel.confirmClear = true }, enabled = !rq.sel.busy) {
                    Icon(
                        Icons.Filled.DeleteSweep, contentDescription = null,
                        modifier = Modifier.size(14.dp),
                    )
                    Spacer(Modifier.size(4.dp))
                    Text("clear queue")
                }
            }
        }

        rq.queue.blockedReason?.takeIf { !editable }?.let { reason ->
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
            .background(
                if (item.playing) Domovoi.colors.brandSoft else Color.Transparent,
                RoundedCornerShape(6.dp),
            )
            .padding(start = 12.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
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
