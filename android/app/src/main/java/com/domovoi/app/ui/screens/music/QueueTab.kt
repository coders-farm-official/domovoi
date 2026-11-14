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
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.net.OnStateEvents
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
 * Rows: tap plays that song now ([RoomQueueApi.play], the room's real jump),
 * hold the handle and drag to reorder, × removes. 48dp targets throughout.
 *
 * Up next (P4, RoomUpNext.kt): the room's own picks are marked (a sparkle
 * and a muted reason chip) under an "up next" head that says what the mix
 * is built from and holds the room's autoplay switch; "less like this" on a
 * pick takes its artist out of the mix; "save as playlist" keeps the queue.
 * A guest gets the jump, the moves and remove — no switch, no "less like
 * this", no saving, no clear. Nothing of Up next shows without a session.
 */

/** What re-reads a room's queue (only when that room's own changed: [RoomPlayWatch]). */
private val NOW_PLAYING_EVENTS = setOf("music.now_playing.changed")

/** The tab's own state, hoisted out of the lazy items so it survives the
 *  header scrolling out of composition. Remembered by [rememberRoomQueue]. */
@Stable
internal class RoomQueueSelection(initialRoom: String?) {
    private var picked by mutableStateOf(initialRoom)

    /** The room on show. Picking another starts its list at the top again
     *  ([shown], [earlier]) and forgets what its now-playing was ([watch]). */
    var room: String?
        get() = picked
        set(value) {
            if (value != picked) {
                shown = ROOM_QUEUE_ROWS_STEP
                earlier = false
                watch.reset()
            }
            picked = value
        }

    /** Rows drawn from the window's start ([roomQueueWindow]): more as the list scrolls. */
    var shown by mutableStateOf(ROOM_QUEUE_ROWS_STEP)

    /** The played songs folded away above the playing one were asked for. */
    var earlier by mutableStateOf(false)

    /** Which now-playing events re-read this room's queue. */
    val watch = RoomPlayWatch()

    /** The next [ROOM_QUEUE_ROWS_STEP] rows, once the drawn ones' end is reached. */
    fun showMore() { shown += ROOM_QUEUE_ROWS_STEP }

    var busy by mutableStateOf(false)
    var confirmClear by mutableStateOf(false)
    /** Picks a "less like this" took back, gone from the view at once. */
    var dropped by mutableStateOf<DroppedPicks?>(null)
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
    /** Plays the entry now; null where this caller can't. */
    val playNow: ((QueueItem) -> Unit)? = null,
    /** The rows shown: the queue's, less the picks just taken back. */
    val items: List<QueueItem> = queue.items,
    val controls: RoomQueueControls = roomQueueControls(queue),
    /** "save as playlist", null where it isn't offered. */
    val save: SaveAsPlaylistFlow? = null,
    val saveQueue: () -> Unit = {},
    val setAutoplay: (Boolean) -> Unit = {},
    val lessLike: (QueueItem) -> Unit = {},
    /** [queue] is the server's answer for this room (not a placeholder while
     *  the read is out): adding waits for it ([roomCanAdd]). */
    val answered: Boolean = true,
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
    val server by app.prefs.serverUrl.collectAsState()

    // Default to a room that's actually playing — that's the queue you opened
    // this tab to look at.
    val sel = remember { RoomQueueSelection(playingRoom ?: rooms.firstOrNull()) }
    LaunchedEffect(playingRoom, rooms.size) {
        if (sel.room == null) sel.room = playingRoom ?: rooms.firstOrNull()
    }

    val rq = remember(app) { RoomQueueApi(app.api) { app.prefs.deviceId } }
    val room = sel.room
    // Each read carries the server and room it was asked of: the answer is
    // shown only while they are still the ones on screen ([roomQueueFor]).
    val queueState = rememberApi(room) { a ->
        val r = room ?: return@rememberApi null
        val at = a.prefs.serverUrl.value
        RoomQueueRead(at, r, rq.read(r))
    }
    val refreshNow by rememberUpdatedState(queueState.refresh)
    // Re-read on now-playing only when THIS room's state or song changed
    // ([RoomPlayWatch]): the event carries every room's.
    OnStateEvents(NOW_PLAYING_EVENTS) { ev ->
        if (sel.watch.shouldReread(app.prefs.serverUrl.value, sel.room, ev.payload)) refreshNow()
    }
    // The rest of a long playlist arrives after the play's answer: read
    // again when asked ([RoomQueueNudges], a few times at most).
    LaunchedEffect(room, server) {
        RoomQueueNudges.nudges.collect { (s, r) -> if (s == server && r == room) refreshNow() }
    }
    // One per room and server: a name typed for one room's queue is not
    // saved as another's.
    val save = remember(room, server) { SaveAsPlaylistFlow() }

    /**
     * One edit at a time; its answer refreshes the list. [done] gets the
     * answer only while the app still talks to the server and the room the
     * edit was for (read-after-await): another house's queue never takes
     * it, and says nothing.
     */
    fun <T> edit(
        failLabel: String,
        action: suspend (String) -> T,
        done: (T) -> String? = { null },
    ) {
        val r = sel.room ?: return
        if (sel.busy) return
        val at = app.prefs.serverUrl.value
        scope.launch {
            sel.busy = true
            try {
                runAction { action(r) }
                    .onSuccess {
                        if (app.prefs.serverUrl.value != at || sel.room != r) return@onSuccess
                        done(it)?.let(toast)
                        queueState.refresh()
                    }
                    .onFailure {
                        if (app.prefs.serverUrl.value == at) toast("$failLabel failed: ${apiErrorText(it)}")
                    }
            } finally {
                sel.busy = false
            }
        }
    }

    val answer = roomQueueFor(queueState.data, server, room)
    val queue = answer ?: RoomQueue()
    val items = visibleRoomItems(queue, sel.dropped, server, room)
    val controls = roomQueueControls(queue)
    val sessionId = queue.sessionId
    return RoomQueueModel(
        sel = sel,
        queue = queue,
        // Another room's answer held while this room's read is out is still loading.
        loading = queueState.loading || (answer == null && room != null && queueState.error == null),
        answered = answer != null,
        remove = { item -> edit("remove", { rq.remove(it, listOf(item.songId)) }) { "removed \"${item.title ?: "track"}\"" } },
        move = { item, to -> edit("move", { rq.move(it, item.songId, roomMoveTarget(items, to)) }) },
        clear = { edit("clear", { rq.clear(it) }) { "cleared ${sel.room}'s queue" } },
        playNow = if (!controls.playNow) null else { item ->
            edit("play", { rq.play(it, item.songId) }) { "playing \"${item.title ?: "track"}\" in ${sel.room}" }
        },
        items = items,
        controls = controls,
        save = save.takeIf { controls.save },
        saveQueue = save@{
            val r = sel.room ?: return@save
            if (!save.canSaveRoom()) return@save
            val at = app.prefs.serverUrl.value
            scope.launch {
                // The flow belongs to this room and server: a note for a
                // save sent elsewhere is never shown here.
                if (save.saveRoom { name -> rq.savePlaylist(r, name) } && app.prefs.serverUrl.value == at) {
                    toast(save.note)
                }
            }
        },
        setAutoplay = { on ->
            if (sessionId != null && controls.autoplay) {
                edit("autoplay", { rq.setAutoplay(sessionId, on) }) { roomAutoplayNote(sel.room.orEmpty(), on) }
            }
        },
        lessLike = lessLike@{ item ->
            val seq = item.upNextSeq ?: return@lessLike
            if (sessionId == null || !controls.lessLike) return@lessLike
            val at = app.prefs.serverUrl.value
            edit("less like this", { rq.lessLikeThis(sessionId, seq) }) { removed ->
                // Gone from the view at once (the room drops them within
                // ~5 s), for this server, room and session only.
                val r = sel.room ?: return@edit null
                val prev = sel.dropped?.takeIf { it.server == at && it.room == r && it.sessionId == sessionId }
                sel.dropped = (prev ?: DroppedPicks(at, r, sessionId, emptySet())).plus(removed + seq)
                lessLikeNote(item.artist, removed.size)
            }
        },
    )
}

/**
 * A header card (room picker, count, save, clear, blocked notice), the
 * search box that adds to the room, then one lazy item per queue entry —
 * and the Up next head among them. A room queue can hold hundreds of songs
 * (a playlist, a cast from the phone); drawing them all inside one item
 * built every row on the main thread in a single frame, the same freeze
 * the player tab had. [picker]: the room chips (off where the room is
 * already chosen, a room's own sheet).
 */
internal fun LazyListScope.queueTab(rooms: List<String>, rq: RoomQueueModel, picker: Boolean = true) {
    item(key = "room-queue-head") { RoomQueueHeader(rooms, rq, picker) }
    if (rooms.isEmpty()) return

    // Search to add, at the top: only where adding works ([roomCanAdd]);
    // otherwise the header says the queue's blocked_reason. Keyed by the
    // room: what was typed for one room's queue is not offered to another.
    val room = rq.sel.room
    if (room != null && roomCanAdd(rq.answered, rq.queue)) {
        item(key = "room-queue-add-$room") { QueueAddBox(QueueTarget.Room(room), modifier = Modifier.padding(top = 4.dp)) }
    }

    val items = rq.items
    when {
        rq.loading && items.isEmpty() -> item(key = "room-queue-loading") { LoadingState() }
        items.isEmpty() -> item(key = "room-queue-empty") {
            EmptyState(
                "queue is empty",
                "search above to add a song, or say \"play something\" in ${rq.sel.room ?: "a room"}",
            )
        }
        else -> {
            // A whole playlist can be the room's queue (~1,500 songs): the
            // rows are drawn ROOM_QUEUE_ROWS_STEP at a time, more as the list
            // scrolls to the end of them ([roomQueueWindow]). Entries keep
            // their index in the whole queue.
            val lines = roomQueueWindow(rq.queue, items, rq.sel.shown, rq.sel.earlier)
            val picks = roomUpNextRows(items).toHashSet()
            items(
                count = lines.size,
                key = { i ->
                    when (val line = lines[i]) {
                        RoomQueueLine.UpNextHead -> "room-queue-up-next"
                        is RoomQueueLine.Entry -> "room-queue-${items[line.index].songId}-${line.index}"
                        is RoomQueueLine.ShowEarlier -> "room-queue-earlier"
                        is RoomQueueLine.ShowMore -> "room-queue-more"
                    }
                },
                contentType = { i ->
                    when (lines[i]) {
                        RoomQueueLine.UpNextHead -> "room-queue-up-next"
                        is RoomQueueLine.Entry -> "room-queue-row"
                        is RoomQueueLine.ShowEarlier, is RoomQueueLine.ShowMore -> "room-queue-more"
                    }
                },
            ) { i ->
                when (val line = lines[i]) {
                    RoomQueueLine.UpNextHead -> RoomUpNextHead(rq, room.orEmpty())
                    is RoomQueueLine.ShowEarlier -> RoomQueueMoreButton(showEarlierLabel(line.count)) { rq.sel.earlier = true }
                    is RoomQueueLine.ShowMore -> {
                        // Reached by scrolling: the next rows come in on their
                        // own (keyed by how many are drawn, so a screen the new
                        // rows don't fill asks again); the button is for
                        // whoever doesn't scroll (TalkBack, a keyboard).
                        val drawn = rq.sel.shown
                        LaunchedEffect(room, drawn) { if (rq.sel.shown == drawn) rq.sel.showMore() }
                        RoomQueueMoreButton(showMoreLabel(line.count)) { rq.sel.showMore() }
                    }
                    is RoomQueueLine.Entry -> {
                        val idx = line.index
                        val item = items[idx]
                        val editable = rq.queue.editable && !rq.sel.busy
                        val pick = item.isPick && idx in picks
                        RoomQueueRow(
                            item = item,
                            index = idx,
                            count = items.size,
                            editable = editable,
                            pick = pick,
                            drag = rq.sel.drag,
                            onPlayNow = rq.playNow?.takeIf { editable && !item.playing }?.let { f -> { f(item) } },
                            onMove = { from, to -> rq.move(items[from], to) },
                            // A guest never removes the song playing now (the server's rule).
                            onRemove = if (rq.queue.guest && item.playing) null else { { rq.remove(item) } },
                            onLessLike = if (lessLikeOffered(rq.controls, items, idx) && !rq.sel.busy) {
                                { rq.lessLike(item) }
                            } else null,
                        )
                    }
                }
            }
        }
    }
}

/** "show more (N more)" / "show N played songs": a quiet full-width button. */
@Composable
private fun RoomQueueMoreButton(label: String, onClick: () -> Unit) {
    TextButton(onClick = onClick, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
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
    val items = rq.items
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

        if (rq.controls.clear) {
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

        // "save as playlist": the room's own queue, as the room holds it
        // (the household's; never a guest's).
        rq.save?.let { flow ->
            Column(Modifier.fillMaxWidth().padding(start = 6.dp, end = 14.dp, bottom = 6.dp)) {
                SaveAsPlaylistRow(
                    flow = flow,
                    enabled = true,
                    hint = roomSaveHint(rq.sel.room ?: "the room"),
                    canSave = flow.canSaveRoom(),
                    onSave = rq.saveQueue,
                )
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

/** The "up next" head: what the mix is built from, the autoplay switch, the one muted line. */
@Composable
private fun RoomUpNextHead(rq: RoomQueueModel, room: String) {
    val basis = roomUpNextBasis(rq.queue)
    val line = roomUpNextLine(rq.queue, roomUpNextRows(rq.items).size)
    Column(Modifier.fillMaxWidth().padding(top = 8.dp, start = 12.dp)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                SectionLabel(UP_NEXT_HEAD)
                if (basis.isNotEmpty()) {
                    Text(
                        basis,
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            if (rq.controls.autoplay) {
                Text(AUTOPLAY, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
                Switch(
                    checked = rq.queue.autoplay == true,
                    onCheckedChange = { rq.setAutoplay(it) },
                    enabled = !rq.sel.busy,
                    modifier = Modifier.semantics { contentDescription = roomAutoplayLabel(room) },
                )
            }
        }
        line?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(bottom = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
    }
}

@Composable
private fun RoomQueueRow(
    item: QueueItem,
    index: Int,
    count: Int,
    editable: Boolean,
    pick: Boolean,
    drag: QueueDragState,
    onPlayNow: (() -> Unit)?,
    onMove: (Int, Int) -> Unit,
    onRemove: (() -> Unit)?,
    onLessLike: (() -> Unit)?,
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
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (pick) {
                        Icon(
                            Icons.Filled.AutoAwesome, contentDescription = "up next pick",
                            tint = Domovoi.colors.fgMuted, modifier = Modifier.size(12.dp),
                        )
                        Spacer(Modifier.width(4.dp))
                    }
                    Text(
                        title,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = if (item.playing) FontWeight.SemiBold else FontWeight.Normal,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        roomQueueSubline(item),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    // Why the room picked it: a muted chip ("same artist", "90s").
                    item.reasonLabel?.takeIf { item.isPick && it.isNotBlank() }?.let { Pill(it) }
                }
            }
        }
        onLessLike?.let { f ->
            IconButton(onClick = f, modifier = Modifier.size(48.dp)) {
                Icon(
                    Icons.Filled.ThumbDown, contentDescription = lessLikeLabel(item.artist),
                    tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp),
                )
            }
        }
        if (editable && onRemove != null) {
            IconButton(onClick = onRemove, modifier = Modifier.size(48.dp)) {
                Icon(
                    Icons.Filled.Close, contentDescription = "remove $title from the queue",
                    tint = Domovoi.colors.err, modifier = Modifier.size(18.dp),
                )
            }
        }
    }
}
