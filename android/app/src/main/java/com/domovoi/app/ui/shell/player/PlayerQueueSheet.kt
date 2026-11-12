package com.domovoi.app.ui.shell.player

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.player.CoverArt
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.ui.components.CoverImage
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.screens.music.LyricsFollow
import com.domovoi.app.ui.screens.music.QueueAddBox
import com.domovoi.app.ui.screens.music.QueueDragHandle
import com.domovoi.app.ui.screens.music.QueueDragState
import com.domovoi.app.ui.screens.music.QueueTarget
import com.domovoi.app.ui.screens.music.RoomQueueDialogs
import com.domovoi.app.ui.screens.music.queueTab
import com.domovoi.app.ui.screens.music.rememberRoomQueue
import com.domovoi.app.ui.screens.music.savableTrackIds
import com.domovoi.app.player.LyricsResult
import com.domovoi.app.ui.screens.music.LyricsBody
import com.domovoi.app.ui.screens.music.rememberLyricsLoad
import com.domovoi.app.ui.screens.music.SHEET_LYRICS_HEIGHT
import com.domovoi.app.ui.screens.music.capLyricsHeight
import com.domovoi.app.ui.screens.music.lyricsTrackIdOf
import com.domovoi.app.ui.theme.Domovoi

/**
 * The docked mini player + its expanding queue sheet. Tapping the tray
 * grows into a near-full-screen bottom sheet with now-playing, seek,
 * transport, and queue editing (jump / move / remove / clear).
 */
@Composable
fun DockedPlayer() {
    var showSheet by remember { mutableStateOf(false) }
    MiniPlayer(onOpenPlayer = { showSheet = true })
    if (showSheet) {
        PlayerQueueSheet(onDismiss = { showSheet = false })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlayerQueueSheet(onDismiss: () -> Unit) {
    val app = LocalApp.current
    val queue by app.player.queue.collectAsState()
    val index by app.player.index.collectAsState()
    val playing by app.player.isPlaying.collectAsState()
    val target by app.player.target.collectAsState()
    val remote by app.player.remote.collectAsState()

    val isRemote = target is PlayTarget.Room
    val current = queue.getOrNull(index)

    // The lyrics: a library track, on this phone or in the room being cast
    // to. Loaded here, above the tabs, so the default tab can follow them.
    val room = target as? PlayTarget.Room
    val lyricsTrackId = if (room != null) {
        remote?.takeIf { it.roomId == room.roomId }?.trackId
    } else {
        lyricsTrackIdOf(current)
    }
    val lyrics = lyricsTrackId?.let { rememberLyricsLoad(it, room?.roomId) }
    val lyricsFollow = lyricsTrackId?.let { room?.let { r -> LyricsFollow.Room(r.roomId) } ?: LyricsFollow.Local }
    val lyricsTab = lyrics != null && lyrics.result !is LyricsResult.Hidden
    // The song a tab was picked for: the room's track while casting.
    val songKey = if (room != null) "room:${lyricsTrackId ?: ""}:${current?.uid ?: ""}" else current?.uid.orEmpty()
    var here by remember { mutableStateOf<Pair<String, SheetTab>?>(null) }
    val tab = playerSheetTab(
        here = here?.takeIf { it.first == songKey }?.second,
        picked = SheetTabMemory.picked,
        lyricsTab = lyricsTab,
        lyricsFound = lyricsFound(lyrics?.result),
        lyricsLoading = lyrics != null && lyrics.result == null,
    )
    val tabs = sheetTabs(lyricsTab)
    // The room's own queue while casting (a new room, a new model).
    val roomQueue = room?.let { r -> key(r.roomId) { rememberRoomQueue(listOf(r.roomId), r.roomId) } }
    val phoneDrag = remember { QueueDragState() }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = Domovoi.colors.card,
    ) {
        // One list for everything, so it all scrolls: a phone on its side
        // has a few hundred dp, and the lyrics must never push the queue out
        // of reach (the 2026-10-06 review). The lyrics panel's height comes
        // from the sheet's own (at most half of it).
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val lyricsHeight = capLyricsHeight(SHEET_LYRICS_HEIGHT, maxHeight)
            LazyColumn(
                Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 4.dp, bottom = 16.dp),
            ) {
                // ── Now playing header ────────────────────────────────────
                item(key = "sheet-head") { SheetHead(current, isRemote, target, remote?.title, remote?.artist, remote?.state) }

                // ── Seek ──────────────────────────────────────────────────
                item(key = "sheet-seek") { SheetSeek(current, isRemote) }

                // ── Transport ─────────────────────────────────────────────
                val effPlaying = if (isRemote) remote?.state == "play" else playing
                item(key = "sheet-transport") { SheetTransport(playing = effPlaying, onDismiss = onDismiss) }

                // ── Panel tabs: Lyrics · Queue · Hide ─────────────────────
                item(key = "sheet-tabs") {
                    SheetTabStrip(tabs, tab) { t ->
                        here = songKey to t
                        SheetTabMemory.picked = t
                    }
                }

                // ── Lyrics (a library track, here or in the room) ─────────
                if (tab == SheetTab.Lyrics && lyrics != null && lyricsFollow != null) {
                    item(key = "sheet-lyrics") {
                        LyricsBody(lyrics, lyricsFollow, lyricsHeight, Modifier.padding(top = 4.dp, bottom = 2.dp))
                    }
                }

                // ── Queue ─────────────────────────────────────────────────
                // While casting, the queue shown is the ROOM's: its rows,
                // edits and search box (batch 2 §2). Otherwise this phone's,
                // with the search box at its top.
                if (tab != SheetTab.Queue) {
                    // Lyrics or Hide: no queue rows.
                } else if (roomQueue != null) {
                    queueTab(listOf(roomQueue.sel.room.orEmpty()), roomQueue, picker = false)
                } else {
                    item(key = "sheet-queue-head") {
                        Row(
                            Modifier.fillMaxWidth().padding(top = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            SectionLabel("queue · ${queue.size}")
                            Spacer(Modifier.weight(1f))
                            if (queue.isNotEmpty()) {
                                TextButton(
                                    onClick = { app.player.clearQueue(); onDismiss() },
                                    modifier = Modifier.heightIn(min = 48.dp),
                                ) {
                                    Icon(
                                        Icons.Filled.DeleteSweep, contentDescription = null,
                                        tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp),
                                    )
                                    Spacer(Modifier.width(4.dp))
                                    Text("clear", color = Domovoi.colors.fgMuted)
                                }
                            }
                        }
                    }
                    item(key = "sheet-queue-add") {
                        QueueAddBox(QueueTarget.Phone, saveIds = savableTrackIds(queue), modifier = Modifier.padding(bottom = 4.dp))
                    }
                    if (queue.isEmpty()) {
                        item(key = "sheet-queue-empty") {
                            Text(
                                "queue is empty — search above, or play something from the library",
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fgSubtle,
                                modifier = Modifier.padding(vertical = 16.dp),
                            )
                        }
                    } else {
                        itemsIndexed(queue, key = { i, it -> "${it.uid}-$i" }) { i, item ->
                            QueueRow(
                                position = i,
                                count = queue.size,
                                title = item.title,
                                artist = item.artist,
                                durationSec = item.durationSec,
                                isCurrent = i == index,
                                isPlaying = effPlaying,
                                drag = phoneDrag,
                                onJump = { app.player.jumpTo(i) },
                                onMove = { from, to -> app.player.moveItem(from, to) },
                                onRemove = { app.player.removeAt(i) },
                            )
                        }
                    }
                }
            }
        }
    }
    roomQueue?.let { RoomQueueDialogs(it) }
}

/** The sheet's now-playing header: the cover (this phone's playback), the
 *  title and artist (the room's while casting), the casting pill. */
@Composable
private fun SheetHead(
    current: PlayItem?,
    isRemote: Boolean,
    target: PlayTarget,
    remoteTitle: String?,
    remoteArtist: String?,
    remoteState: String?,
) {
    val app = LocalApp.current
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (!isRemote && current?.coverPath != null) {
            CoverImage(CoverArt.model(current.coverPath, app.api::absolute), 56.dp)
            Spacer(Modifier.width(14.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(
                (if (isRemote) remoteTitle else current?.title) ?: "nothing playing",
                style = MaterialTheme.typography.titleMedium,
                color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            val artist = if (isRemote) remoteArtist else current?.artist
            if (artist != null) {
                Text(
                    artist,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        if (isRemote) {
            Pill(
                "casting to ${(target as PlayTarget.Room).roomId}",
                Tone.Brand,
                live = remoteState == "play",
            )
        }
    }
}

/**
 * The sheet's transport row. Every button takes a tap while casting too and
 * acts on the room: PlayerController sends previous, play/pause and next to
 * it. Previous was greyed out while casting until rooms had a previous
 * (2026-10-01). [playing] is the room's state while casting. A composable
 * of its own so a JVM test can compose it; the sheet is a dialog window.
 */
@Composable
internal fun SheetTransport(playing: Boolean, onDismiss: () -> Unit) {
    val app = LocalApp.current
    // Greyed when it would do nothing (a book's last chapter, ChapterSkip);
    // the flow moves only when that answer does, never on a position tick.
    val canNext by remember(app.player) { app.player.canNext }.collectAsState(initial = app.player.canNextNow())
    Row(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = { app.player.prev() }) {
            Icon(Icons.Filled.SkipPrevious, "previous", tint = Domovoi.colors.fg)
        }
        IconButton(
            onClick = { app.player.toggle() },
            modifier = Modifier
                .padding(horizontal = 10.dp)
                .size(56.dp)
                .background(Domovoi.colors.brand, RoundedCornerShape(999.dp)),
        ) {
            Icon(
                if (playing) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                "play/pause",
                tint = Domovoi.colors.brandFg,
            )
        }
        IconButton(onClick = { app.player.next() }, enabled = canNext) {
            Icon(Icons.Filled.SkipNext, "next", tint = if (canNext) Domovoi.colors.fg else Domovoi.colors.fgFaint)
        }
        IconButton(onClick = { app.player.stop(); onDismiss() }) {
            Icon(Icons.Filled.Stop, "stop", tint = Domovoi.colors.fgMuted)
        }
    }
}

/** The panel's tab strip: [tabs], [selected] underlined; a tap picks one. */
@Composable
private fun SheetTabStrip(tabs: List<SheetTab>, selected: SheetTab, onPick: (SheetTab) -> Unit) {
    TabRow(
        selectedTabIndex = tabs.indexOf(selected).coerceAtLeast(0),
        containerColor = Color.Transparent,
        contentColor = Domovoi.colors.fg,
        modifier = Modifier.padding(top = 4.dp),
    ) {
        tabs.forEach { t ->
            Tab(
                selected = t == selected,
                onClick = { onPick(t) },
                text = {
                    Text(
                        t.label,
                        style = MaterialTheme.typography.labelLarge,
                        color = if (t == selected) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                    )
                },
            )
        }
    }
}

/** The seek bar and times: the only part of the sheet that follows the
 *  500 ms position tick (besides the lyrics, inside their own panel), so
 *  the tick does not rebuild the queue list. */
@Composable
private fun SheetSeek(current: PlayItem?, isRemote: Boolean) {
    val app = LocalApp.current
    val pos by app.player.positionSec.collectAsState()
    val dur by app.player.durationSec.collectAsState()
    val remote by app.player.remote.collectAsState()
    val effPos = if (isRemote) remote?.elapsedSec ?: 0.0 else pos
    val effDur = if (isRemote) remote?.durationSec ?: 0.0 else dur
    var dragging by remember { mutableStateOf(false) }
    var dragValue by remember { mutableFloatStateOf(0f) }
    val seekable = !isRemote && current?.seekable != false
    Slider(
        value = if (dragging) dragValue
        else if (effDur > 0) (effPos / effDur).toFloat().coerceIn(0f, 1f) else 0f,
        onValueChange = { dragging = true; dragValue = it },
        onValueChangeFinished = {
            if (seekable && effDur > 0) app.player.seekTo(dragValue * effDur)
            dragging = false
        },
        enabled = seekable,
        colors = SliderDefaults.colors(
            thumbColor = Domovoi.colors.brand,
            activeTrackColor = Domovoi.colors.brand,
            inactiveTrackColor = Domovoi.colors.border,
        ),
        modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
    )
    Row(Modifier.fillMaxWidth()) {
        Text(fmtDur(effPos), style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgSubtle)
        Spacer(Modifier.weight(1f))
        Text(
            if (current?.seekable == false) "live" else fmtDur(effDur),
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fgSubtle,
        )
    }
}

/** A row of this phone's queue: hold the handle and drag to reorder (TalkBack:
 *  move up / move down on it), tap to jump, × to remove. 48dp targets. */
@Composable
private fun QueueRow(
    position: Int,
    count: Int,
    title: String,
    artist: String?,
    durationSec: Double?,
    isCurrent: Boolean,
    isPlaying: Boolean,
    drag: QueueDragState,
    onJump: () -> Unit,
    onMove: (Int, Int) -> Unit,
    onRemove: () -> Unit,
) {
    Row(
        drag.rowModifier(position)
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .onSizeChanged { if (it.height > 0) drag.rowPx = it.height.toFloat() }
            .clip(RoundedCornerShape(8.dp))
            .background(
                if (isCurrent || drag.from == position) Domovoi.colors.brandSoft
                else androidx.compose.ui.graphics.Color.Transparent,
            ),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        QueueDragHandle(drag, position, count, title, onMove)
        Row(
            Modifier.weight(1f).clickable(onClickLabel = "play", onClick = onJump).padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            QueueRowText(position, title, artist, durationSec, isCurrent, isPlaying)
        }
        IconButton(onClick = onRemove, modifier = Modifier.size(48.dp)) {
            Icon(Icons.Filled.Close, "remove $title from the queue", tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(18.dp))
        }
    }
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.QueueRowText(
    position: Int,
    title: String,
    artist: String?,
    durationSec: Double?,
    isCurrent: Boolean,
    isPlaying: Boolean,
) {
    run {
        Box(Modifier.width(26.dp), contentAlignment = Alignment.Center) {
            if (isCurrent) {
                com.domovoi.app.ui.components.StatusDot(Tone.Brand, live = isPlaying)
            } else {
                Text(
                    "${position + 1}",
                    style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        Column(Modifier.weight(1f).padding(horizontal = 8.dp)) {
            Text(
                title,
                style = MaterialTheme.typography.titleSmall,
                color = if (isCurrent) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            if (artist != null) {
                Text(
                    artist,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgSubtle,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Text(
            fmtDur(durationSec),
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(end = 2.dp),
        )
    }
}
