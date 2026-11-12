package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/**
 * A room's queue — the web QueueTab analog (web/static/music.jsx), drawn in
 * Music's "room queue" tab, in a room's queue sheet ([RoomQueueSheet]: Home's
 * room rows, Music's room cards, the room's settings) and in the full
 * player's Queue tab while casting.
 *
 * Unlike the phone's own queue this is the queue the ROOM's speaker plays
 * from, held by MPD, so every client sees the same list and an edit lands
 * for everyone. At its top the search box adds to it ([QueueAddBox]).
 *
 * Each row carries an unobtrusive "added by <device>" when the server has a
 * record of who queued it — absent for voice commands, for casts from before
 * devices had names, and whatever the device id says (a non-admin reads
 * `added_by_device_id: null` since 2026-11-12; the name is what is shown).
 *
 * Rows: tap plays that song now ([RoomQueueApi.playNow]), hold the handle
 * and drag to reorder, × removes. 48dp targets throughout.
 */

/** The tab's own state, hoisted out of the lazy items so it survives the
 *  header scrolling out of composition. Remembered by [rememberRoomQueue]. */
@Stable
internal class RoomQueueSelection(initialRoom: String?) {
    var room by mutableStateOf(initialRoom)
    var busy by mutableStateOf(false)
    var confirmClear by mutableStateOf(false)
    val drag = QueueDragState()
}

/** What [queueTab] draws: this composition's queue plus the edit actions. */
internal class RoomQueueModel(
    val sel: RoomQueueSelection,
    val queue: RoomQueue,
    val loading: Boolean,
    val remove: (QueueItem) -> Unit,
    val move: (QueueItem, Int) -> Unit,
    val clear: () -> Unit,
    /** Plays the entry now; null where the room can't (nothing playing). */
    val playNow: ((QueueItem) -> Unit)? = null,
)

/** The secondary line of a room-queue row: artist · length · added by. */
internal fun roomQueueSubline(item: QueueItem): String = buildString {
    append(item.artist ?: "unknown artist")
    item.durationSec?.let { append(" · ").append(fmtDur(it.toDouble())) }
    // The "added by" tag: the device's NAME, appended to the secondary line,
    // never a line of its own, and simply absent when unknown. The device
    // id is never shown (and a non-admin no longer gets it).
    item.addedBy?.takeIf { it.isNotBlank() }?.let { append(" · added by ").append(it) }
}

/**
 * Fetches the selected room's queue and wires its edits. Called by
 * MusicScreen while this tab is open, and by [RoomQueueSheet]; the rows
 * themselves are separate lazy items (see [queueTab]), so the state cannot
 * live inside one of them.
 */
@Composable
internal fun rememberRoomQueue(rooms: List<String>, playingRoom: String?): RoomQueueModel {
    val app = LocalApp.current
    val toast = LocalToast.current
    // The app's scope: this model leaves composition with its tab, and an
    // edit already sent must still finish and say how it went
    // (ServerAction.kt says why).
    val scope = app.scope

    // Default to a room that's actually playing — that's the queue you opened
    // this tab to look at.
    val sel = remember { RoomQueueSelection(playingRoom ?: rooms.firstOrNull()) }
    LaunchedEffect(playingRoom, rooms.size) {
        if (sel.room == null) sel.room = playingRoom ?: rooms.firstOrNull()
    }

    val rq = remember(app) { RoomQueueApi(app.api, { app.prefs.serverUrl.value }, { app.prefs.deviceId }) }
    val room = sel.room
    val queueState = rememberApi(room, eventTypes = setOf("music.now_playing.changed")) {
        val r = room ?: return@rememberApi RoomQueue()
        rq.read(r)
    }

    /** One edit at a time; its answer refreshes the list. */
    fun edit(failLabel: String, done: String? = null, action: suspend (String) -> Unit) {
        val r = sel.room ?: return
        if (sel.busy) return
        scope.launch {
            sel.busy = true
            try {
                runAction { action(r) }
                    .onSuccess { done?.let(toast); queueState.refresh() }
                    .onFailure { toast("$failLabel failed: ${apiErrorText(it)}") }
            } finally {
                sel.busy = false
            }
        }
    }

    val queue = queueState.data ?: RoomQueue()
    val canPlayNow = RoomQueueApi.playingPos(queue) != null
    return RoomQueueModel(
        sel = sel,
        queue = queue,
        loading = queueState.loading,
        remove = { item -> edit("remove", "removed \"${item.title ?: "track"}\"") { rq.remove(it, listOf(item.songId)) } },
        move = { item, to -> edit("move") { rq.move(it, item.songId, to) } },
        clear = { edit("clear", "cleared ${sel.room}'s queue") { rq.clear(it) } },
        playNow = if (!canPlayNow) null else { item ->
            edit("play", "playing \"${item.title ?: "track"}\" in ${sel.room}") { rq.playNow(it, queue, item.songId) }
        },
    )
}

/**
 * A header card (room picker, count, clear, blocked notice), the search box
 * that adds to the room, then one lazy item per queue entry. A room queue
 * can hold hundreds of songs (a playlist, a cast from the phone); drawing
 * them all inside one item built every row on the main thread in a single
 * frame, the same freeze the player tab had. [picker]: the room chips (off
 * where the room is already chosen, a room's own sheet).
 */
internal fun LazyListScope.queueTab(rooms: List<String>, rq: RoomQueueModel, picker: Boolean = true) {
    item(key = "room-queue-head") { RoomQueueHeader(rooms, rq, picker) }
    if (rooms.isEmpty()) return

    val room = rq.sel.room
    if (room != null && rq.queue.editable) {
        item(key = "room-queue-add") { QueueAddBox(QueueTarget.Room(room), modifier = Modifier.padding(top = 4.dp)) }
    }

    val items = rq.queue.items
    when {
        rq.loading && items.isEmpty() -> item(key = "room-queue-loading") { LoadingState() }
        items.isEmpty() -> item(key = "room-queue-empty") {
            EmptyState(
                "queue is empty",
                "search above to add a song, or say \"play something\" in ${rq.sel.room ?: "a room"}",
            )
        }
        else -> items(
            count = items.size,
            key = { "room-queue-${items[it].songId}-$it" },
            contentType = { "room-queue-row" },
        ) { idx ->
            val item = items[idx]
            val editable = rq.queue.editable && !rq.sel.busy
            RoomQueueRow(
                item = item,
                index = idx,
                count = items.size,
                editable = editable,
                drag = rq.sel.drag,
                onPlayNow = rq.playNow?.takeIf { editable && !item.playing }?.let { f -> { f(item) } },
                onMove = { from, to -> rq.move(items[from], to) },
                onRemove = { rq.remove(item) },
            )
        }
    }
}

/** The clear-queue confirmation; drawn outside the list. */
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
private fun RoomQueueHeader(rooms: List<String>, rq: RoomQueueModel, picker: Boolean) {
    val items = rq.queue.items
    val editable = rq.queue.editable

    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        if (rooms.isEmpty()) {
            EmptyState("no rooms provisioned yet", "set up a room to bring it online")
            return@DomovoiCard
        }

        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (picker) {
                SectionLabel("room")
                Row(
                    Modifier.weight(1f).horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    rooms.forEach { r ->
                        FilterChip(
                            selected = rq.sel.room == r,
                            onClick = { rq.sel.room = r },
                            label = { Text(r, style = MaterialTheme.typography.labelMedium) },
                            modifier = Modifier.heightIn(min = 48.dp),
                        )
                    }
                }
            } else {
                SectionLabel("${rq.sel.room ?: "room"}'s queue", Modifier.weight(1f))
            }
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
                OutlinedButton(
                    onClick = { rq.sel.confirmClear = true },
                    enabled = !rq.sel.busy,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) {
                    Icon(
                        Icons.Filled.DeleteSweep, contentDescription = null,
                        modifier = Modifier.size(16.dp),
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
private fun RoomQueueRow(
    item: QueueItem,
    index: Int,
    count: Int,
    editable: Boolean,
    drag: QueueDragState,
    onPlayNow: (() -> Unit)?,
    onMove: (Int, Int) -> Unit,
    onRemove: () -> Unit,
) {
    val title = item.title ?: item.file.ifBlank { "unknown" }
    Row(
        drag.rowModifier(index)
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .onSizeChanged { if (it.height > 0) drag.rowPx = it.height.toFloat() }
            .background(
                if (item.playing || drag.from == index) Domovoi.colors.brandSoft else Color.Transparent,
                RoundedCornerShape(6.dp),
            ),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (editable) {
            QueueDragHandle(drag, index, count, title, onMove)
        } else {
            Spacer(Modifier.width(12.dp))
        }
        Row(
            Modifier
                .weight(1f)
                .then(if (onPlayNow != null) Modifier.clickable(onClickLabel = "play now", onClick = onPlayNow) else Modifier)
                .padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (item.playing) {
                Icon(
                    Icons.Filled.VolumeUp, contentDescription = "now playing",
                    tint = Domovoi.colors.brandPress, modifier = Modifier.size(16.dp),
                )
            } else {
                Text(
                    "${index + 1}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                    modifier = Modifier.width(20.dp),
                )
            }
            Column(Modifier.weight(1f)) {
                Text(
                    title,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = if (item.playing) FontWeight.SemiBold else FontWeight.Normal,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Text(
                    roomQueueSubline(item),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        if (editable) {
            IconButton(onClick = onRemove, modifier = Modifier.size(48.dp)) {
                Icon(
                    Icons.Filled.Close, contentDescription = "remove $title from the queue",
                    tint = Domovoi.colors.err, modifier = Modifier.size(18.dp),
                )
            }
        }
    }
}
