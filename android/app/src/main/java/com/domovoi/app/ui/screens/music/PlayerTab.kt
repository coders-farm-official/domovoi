package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Cast
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.State
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.player.CastOutcome
import com.domovoi.app.player.CastPlanner
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.CoverArt
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.ui.components.CoverImage
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlin.math.abs

/*
 * Player tab: the rich local-player panel bound to PlayerController's state
 * flows (cover, seek, transport, speed, sleep timer, cast target, chapters
 * and the queue). Android analog of web NowPlayingPanel.
 *
 * It is emitted as separate LazyColumn items, a row per chapter and per queue
 * entry, never as one item holding everything. The single-item version built
 * a clickable row with three icon buttons for EVERY queue entry on the main
 * thread in one frame: with a few thousand tracks queued that froze the app
 * for seconds, then ran it out of memory (the 2026-09-30 freeze and crash).
 * Now only rows on screen exist.
 *
 * Position ticks (every 500 ms while playing) are read by the seek row, the
 * current-chapter state and the lyrics panel alone, so they no longer
 * rebuild the whole tab.
 */

/** What the player tab's item list is built from; see [rememberPlayerTabModel]. */
@Stable
internal class PlayerTabModel(
    val queue: List<PlayItem>,
    val index: Int,
    val roomTarget: PlayTarget.Room?,
    val chapters: List<Chapter>,
    /** Which chapter is playing; follows the position without the caller
     *  reading it, so only the chapter rows recompose when it moves on. */
    val currentChapter: State<Int>,
    val hasLibraryItems: Boolean,
    /** The library track whose lyrics the tab shows: the phone's current
     *  library item, or the track the room plays while casting. Null for
     *  radio, podcasts, audiobooks, songs on the phone and streams. */
    val lyricsTrackId: Long? = null,
) {
    val current: PlayItem? get() = queue.getOrNull(index)
    val isRemote: Boolean get() = roomTarget != null

    /** Whose clock the lyrics follow. */
    val lyricsFollow: LyricsFollow get() = roomTarget?.let { LyricsFollow.Room(it.roomId) } ?: LyricsFollow.Local
}

/**
 * Collects the player state the tab's item LIST depends on (queue, index,
 * target), so the caller recomposes only when those change. The playback
 * position is collected but its value is read only inside [derivedStateOf];
 * so is the room's 2 s reading, of which only the track id counts here (the
 * lyrics panel reads the rest itself).
 */
@Composable
internal fun rememberPlayerTabModel(): PlayerTabModel {
    val app = LocalApp.current
    val queue by app.player.queue.collectAsState()
    val index by app.player.index.collectAsState()
    val target by app.player.target.collectAsState()
    val position = app.player.positionSec.collectAsState()
    val remote = app.player.remote.collectAsState()

    val roomTarget = target as? PlayTarget.Room
    val chapters = if (roomTarget != null) emptyList() else queue.getOrNull(index)?.chapters.orEmpty()
    val currentChapter = remember(chapters, position) {
        derivedStateOf { currentChapterIndex(chapters, position.value) }
    }
    val hasLibrary = remember(queue) { queue.any { it.kind == PlayKind.Library } }
    val roomTrackId = remember(remote, roomTarget) {
        derivedStateOf { remote.value?.takeIf { it.roomId == roomTarget?.roomId }?.trackId }
    }
    val lyricsTrackId = if (roomTarget != null) roomTrackId.value else lyricsTrackIdOf(queue.getOrNull(index))
    return remember(queue, index, roomTarget, chapters, currentChapter, hasLibrary, lyricsTrackId) {
        PlayerTabModel(queue, index, roomTarget, chapters, currentChapter, hasLibrary, lyricsTrackId)
    }
}

/** A queue item's lyrics track: library tracks only ([D10]). */
internal fun lyricsTrackIdOf(item: PlayItem?): Long? = item?.takeIf { it.kind == PlayKind.Library }?.id

/** The chapter playing at [positionSec]: the last one whose start has
 *  passed, and the first one before any has. */
internal fun currentChapterIndex(chapters: List<Chapter>, positionSec: Double): Int {
    var cur = 0
    chapters.forEachIndexed { i, c -> if (positionSec >= c.startSec) cur = i }
    return cur
}

/** Lazy keys for the player tab. Unique across the Music page's list (a
 *  track can be queued twice, so a queue key carries its position too). */
internal fun playerQueueKey(index: Int, item: PlayItem): String = "player-q-$index-${item.uid}"
internal fun playerChapterKey(index: Int): String = "player-chapter-$index"

internal fun LazyListScope.playerTab(
    model: PlayerTabModel,
    rooms: List<String>,
    onSaveQueue: () -> Unit,
) {
    if (model.current == null && !model.isRemote) {
        item(key = "player-empty") {
            EmptyState(
                "nothing queued",
                "play a library track on this device from the library tab to start",
            )
        }
        return
    }

    item(key = "player-head") { PlayerHead(model.current, model.roomTarget, rooms) }

    // Lyrics: a library track, on this phone or in the room being cast to.
    model.lyricsTrackId?.let { trackId ->
        val follow = model.lyricsFollow
        item(key = "player-lyrics") { PlayerLyrics(trackId, follow) }
    }

    // Chapters (podcasts / audiobooks)
    val chapters = model.chapters
    if (chapters.isNotEmpty()) {
        item(key = "player-chapters-label") {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                HorizontalDivider(color = Domovoi.colors.borderSoft)
                SectionLabel("chapters · ${chapters.size}")
            }
        }
        items(
            count = chapters.size,
            key = { playerChapterKey(it) },
            contentType = { "player-chapter" },
        ) { i ->
            ChapterRow(i, chapters[i], current = model.currentChapter.value == i)
        }
    }

    // Queue
    val queue = model.queue
    item(key = "player-queue-label") {
        PlayerQueueHeader(queue.size, canSave = model.hasLibraryItems, onSaveQueue = onSaveQueue)
    }
    if (queue.isEmpty()) {
        item(key = "player-queue-empty") {
            Text(
                "queue is empty",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
        }
    } else {
        val index = model.index
        items(
            count = queue.size,
            key = { playerQueueKey(it, queue[it]) },
            contentType = { "player-queue-row" },
        ) { i ->
            PlayerQueueRow(
                i, queue[i],
                isCurrent = i == index, last = i == queue.lastIndex, room = model.roomTarget?.roomId,
            )
        }
    }
}

/** Cover, identity, seek, transport + cast, speed and sleep: everything above
 *  the chapter and queue lists. */
@Composable
private fun PlayerHead(current: PlayItem?, roomTarget: PlayTarget.Room?, rooms: List<String>) {
    val app = LocalApp.current
    val playing by app.player.isPlaying.collectAsState()
    val remote by app.player.remote.collectAsState()

    val isRemote = roomTarget != null
    val effPlaying = if (isRemote) remote?.state == "play" else playing

    Column(
        Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (roomTarget != null) {
            Pill("casting to ${roomTarget.roomId}", Tone.Brand, live = effPlaying)
        }

        // Cover + identity
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // Server covers are paths; on-device covers are already content://
            // URIs (CoverArt.model). No cover, or a 404, keeps the gradient.
            CoverImage(
                CoverArt.model(if (isRemote) null else current?.coverPath, app.api::absolute), 112.dp,
                corner = 10.dp,
                placeholder = Brush.linearGradient(listOf(Color(0xFFF2CD8C), Color(0xFFDD8A2E))),
                iconTint = Color.White.copy(alpha = 0.85f), iconSize = 44.dp,
            )
            Column(Modifier.weight(1f)) {
                Text(
                    (if (isRemote) remote?.title else current?.title) ?: "—",
                    style = MaterialTheme.typography.titleLarge,
                    color = Domovoi.colors.fg,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    (if (isRemote) remote?.artist else current?.artist) ?: "—",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val album = if (isRemote) null else current?.album
                if (!album.isNullOrBlank()) {
                    Text(
                        album,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgSubtle,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        SeekRow(current, isRemote)
        TransportRow(isRemote, effPlaying, rooms)
        if (!isRemote) SpeedRow()
        SleepRow()
    }
}

/** The tab's "lyrics" section: open until closed (Prefs `lyrics_panel_open`). */
@Composable
private fun PlayerLyrics(trackId: Long, follow: LyricsFollow) {
    val app = LocalApp.current
    val open by app.prefs.lyricsPanelOpen.collectAsState()
    LyricsSection(
        trackId, follow, PLAYER_LYRICS_HEIGHT, open,
        onOpenChange = app.prefs::setLyricsPanelOpen,
        divider = true,
    )
}

/** The only part of the tab that follows the 500 ms position tick
 *  (besides the lyrics panel, which follows it inside itself). */
@Composable
private fun SeekRow(current: PlayItem?, isRemote: Boolean) {
    val app = LocalApp.current
    val pos by app.player.positionSec.collectAsState()
    val dur by app.player.durationSec.collectAsState()
    val remote by app.player.remote.collectAsState()

    val effDur = if (isRemote) (remote?.durationSec ?: 0.0)
    else if (dur > 0) dur else (current?.durationSec ?: 0.0)
    val effPos = if (isRemote) (remote?.elapsedSec ?: 0.0) else pos
    val seekable = !isRemote && current?.seekable != false

    var dragPos by remember { mutableStateOf<Float?>(null) }
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            fmtDur((dragPos ?: effPos.toFloat()).toDouble()),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = Domovoi.colors.fgMuted,
        )
        Slider(
            value = (dragPos ?: effPos.toFloat()).coerceIn(0f, maxOf(1f, effDur.toFloat())),
            onValueChange = { if (seekable) dragPos = it },
            onValueChangeFinished = {
                dragPos?.let { app.player.seekTo(it.toDouble()) }
                dragPos = null
            },
            valueRange = 0f..maxOf(1f, effDur.toFloat()),
            enabled = seekable,
            modifier = Modifier.weight(1f),
        )
        Text(
            if (current?.seekable == false) "live" else fmtDur(effDur),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = Domovoi.colors.fgMuted,
        )
    }
}

@Composable
private fun TransportRow(isRemote: Boolean, effPlaying: Boolean, rooms: List<String>) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // Live while casting too: the room's previous (PlayerController.prev).
        SmallIconButton(Icons.Filled.SkipPrevious, "previous") {
            app.player.prev()
        }
        IconButton(
            onClick = { app.player.toggle() },
            modifier = Modifier
                .size(48.dp)
                .background(Domovoi.colors.brand, CircleShape),
        ) {
            Icon(
                if (effPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                contentDescription = if (effPlaying) "pause" else "play",
                tint = Domovoi.colors.brandFg,
            )
        }
        SmallIconButton(Icons.Filled.SkipNext, "next") { app.player.next() }
        SmallIconButton(Icons.Filled.Stop, "stop") { app.player.stop() }
        Spacer(Modifier.weight(1f))
        Box {
            var castOpen by remember { mutableStateOf(false) }
            SmallIconButton(
                Icons.Filled.Cast, "cast",
                tint = if (isRemote) Domovoi.colors.brand else Domovoi.colors.fgMuted,
            ) { castOpen = true }
            DropdownMenu(expanded = castOpen, onDismissRequest = { castOpen = false }) {
                // What a cast would send, worked out as the menu opens (its
                // content leaves composition when it closes). A queue with
                // nothing a room can play (songs saved on this phone) gets
                // its rooms greyed out and the reason, instead of a "casting"
                // label over a silent room.
                val refusal = remember { CastPlanner.refusal(app.player.castPlan()) }
                DropdownMenuItem(
                    text = { Text("this device") },
                    onClick = {
                        castOpen = false
                        scope.launch {
                            // The toast is what happened: "playing" only when
                            // the phone took over from a room that was
                            // playing and was paused (CastOutcome.Here).
                            runCatching { app.player.castTo(null) }
                                .onSuccess { toast(it.note) }
                                .onFailure { toast(castFailure(it, null)) }
                        }
                    },
                )
                if (rooms.isEmpty()) {
                    // Only rooms that answered now-playing are listed; with
                    // none, say so rather than offer a room that isn't there.
                    Text(
                        "no rooms online — connect a satellite",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier
                            .widthIn(max = 260.dp)
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                    )
                }
                if (refusal != null && rooms.isNotEmpty()) {
                    Text(
                        refusal,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier
                            .widthIn(max = 260.dp)
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                    )
                }
                rooms.forEach { r ->
                    DropdownMenuItem(
                        text = { Text(r) },
                        enabled = refusal == null,
                        onClick = {
                            castOpen = false
                            scope.launch {
                                runCatching { app.player.castTo(r) }
                                    .onSuccess { toast(it.note) }
                                    .onFailure { toast(castFailure(it, r)) }
                            }
                        },
                    )
                }
            }
        }
    }
}

/** Speed (local only). */
@Composable
private fun SpeedRow() {
    val app = LocalApp.current
    val speed by app.player.speed.collectAsState()
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            "speed",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
        )
        listOf(0.75f, 1f, 1.25f, 1.5f, 2f).forEach { v ->
            SmallChip(speedLabel(v), selected = abs(speed - v) < 0.01f) {
                app.player.setSpeed(v)
            }
        }
    }
}

/** Sleep timer; its countdown ticks every second, so it recomposes alone. */
@Composable
private fun SleepRow() {
    val app = LocalApp.current
    val sleepSec by app.player.sleepRemainingSec.collectAsState()
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(
            Icons.Filled.Bedtime, contentDescription = null,
            tint = Domovoi.colors.fgMuted, modifier = Modifier.size(14.dp),
        )
        Text(
            "sleep",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
        )
        listOf(15, 30, 45, 60).forEach { m ->
            SmallChip("${m}m") { app.player.setSleepMinutes(m) }
        }
        SmallChip("end of track") { app.player.setSleepEndOfTrack() }
        val sleep = sleepSec
        if (sleep != null) {
            Text(
                fmtDur(sleep.toDouble()),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.brand,
            )
            SmallIconButton(Icons.Filled.Close, "cancel sleep timer") {
                app.player.cancelSleep()
            }
        }
    }
}

@Composable
private fun ChapterRow(i: Int, c: Chapter, current: Boolean) {
    val app = LocalApp.current
    Row(
        Modifier
            .fillMaxWidth()
            .background(
                if (current) Domovoi.colors.brandSoft else Color.Transparent,
                RoundedCornerShape(6.dp),
            )
            .clickable { app.player.jumpToChapter(i) }
            // The list's 12dp item spacing stands in for most of the old
            // vertical padding, keeping the rows' pitch as it was.
            .padding(horizontal = 8.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            fmtDur(c.startSec),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.width(48.dp),
        )
        Text(
            c.title.ifBlank { "Chapter ${i + 1}" },
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fg,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun PlayerQueueHeader(size: Int, canSave: Boolean, onSaveQueue: () -> Unit) {
    val app = LocalApp.current
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SectionLabel("queue · $size", Modifier.weight(1f))
            TextButton(onClick = onSaveQueue, enabled = canSave) {
                Text("save as playlist", color = Domovoi.colors.brand)
            }
            TextButton(onClick = { app.player.clearQueue() }, enabled = size > 0) {
                Text("clear", color = Domovoi.colors.fgMuted)
            }
        }
    }
}

/** A failed cast in words for the person, never the server's raw reply
 *  ([CastPlanner.failureNote]); [room] is where it was going. The cast menu
 *  and the queue rows toast this; before 2026-10-01 a room that refused read
 *  'cast failed: 502 Bad Gateway: {"detail":"MPD error: ..."}'. */
internal fun castFailure(e: Throwable, room: String?): String = CastPlanner.failureNote(e, room)

@Composable
private fun PlayerQueueRow(i: Int, item: PlayItem, isCurrent: Boolean, last: Boolean, room: String?) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    Row(
        Modifier
            .fillMaxWidth()
            .background(
                if (isCurrent) Domovoi.colors.brandSoft else Color.Transparent,
                RoundedCornerShape(6.dp),
            )
            .clickable {
                // While casting, a tapped row starts the ROOM there; playing
                // it on the phone would put two players on at once.
                if (room != null) {
                    scope.launch {
                        runCatching { app.player.castFrom(i) }
                            .onSuccess {
                                toast(if (it is CastOutcome.ToRoom) "playing \"${item.title}\" in $room" else it.note)
                            }
                            .onFailure { toast(castFailure(it, room)) }
                    }
                } else {
                    app.player.jumpTo(i)
                }
            }
            .padding(horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "${i + 1}",
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = Domovoi.colors.fgFaint,
            maxLines = 1,
            modifier = Modifier.width(26.dp),
        )
        Column(Modifier.weight(1f)) {
            Text(
                item.title,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                item.artist ?: "—",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        SmallIconButton(
            Icons.Filled.KeyboardArrowUp, "move up",
            enabled = i > 0,
        ) { app.player.moveItem(i, i - 1) }
        SmallIconButton(
            Icons.Filled.KeyboardArrowDown, "move down",
            enabled = !last,
        ) { app.player.moveItem(i, i + 1) }
        SmallIconButton(Icons.Filled.Close, "remove from queue") {
            app.player.removeAt(i)
        }
    }
}

private fun speedLabel(v: Float): String =
    if (v == v.toInt().toFloat()) "${v.toInt()}x" else "${v}x"
