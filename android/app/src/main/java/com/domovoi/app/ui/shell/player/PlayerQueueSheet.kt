package com.domovoi.app.ui.shell.player

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.LocalApp
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
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
    val pos by app.player.positionSec.collectAsState()
    val dur by app.player.durationSec.collectAsState()
    val target by app.player.target.collectAsState()
    val remote by app.player.remote.collectAsState()

    val isRemote = target is PlayTarget.Room
    val current = queue.getOrNull(index)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = Domovoi.colors.card,
    ) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 4.dp),
        ) {
            // ── Now playing header ────────────────────────────────────────
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (!isRemote && current?.coverPath != null) {
                    AsyncImage(
                        model = app.api.absolute(current.coverPath),
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.size(56.dp).clip(RoundedCornerShape(8.dp)),
                    )
                    Spacer(Modifier.width(14.dp))
                }
                Column(Modifier.weight(1f)) {
                    Text(
                        (if (isRemote) remote?.title else current?.title) ?: "nothing playing",
                        style = MaterialTheme.typography.titleMedium,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    val artist = if (isRemote) remote?.artist else current?.artist
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
                        live = remote?.state == "play",
                    )
                }
            }

            // ── Seek ──────────────────────────────────────────────────────
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

            // ── Transport ─────────────────────────────────────────────────
            val effPlaying = if (isRemote) remote?.state == "play" else playing
            Row(
                Modifier.fillMaxWidth().padding(vertical = 6.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = { app.player.prev() }, enabled = !isRemote) {
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
                        if (effPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                        "play/pause",
                        tint = Domovoi.colors.brandFg,
                    )
                }
                IconButton(onClick = { app.player.next() }) {
                    Icon(Icons.Filled.SkipNext, "next", tint = Domovoi.colors.fg)
                }
                IconButton(onClick = { app.player.stop(); onDismiss() }) {
                    Icon(Icons.Filled.Stop, "stop", tint = Domovoi.colors.fgMuted)
                }
            }

            // ── Queue ─────────────────────────────────────────────────────
            Row(
                Modifier.fillMaxWidth().padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SectionLabel("queue · ${queue.size}")
                Spacer(Modifier.weight(1f))
                if (!isRemote && queue.isNotEmpty()) {
                    TextButton(onClick = { app.player.clearQueue(); onDismiss() }) {
                        Icon(
                            Icons.Filled.DeleteSweep, contentDescription = null,
                            tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("clear", color = Domovoi.colors.fgMuted)
                    }
                }
            }

            if (isRemote) {
                Text(
                    "casting — the queue lives on the room's speaker; transport controls act on the room",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgSubtle,
                    modifier = Modifier.padding(vertical = 16.dp),
                )
            } else if (queue.isEmpty()) {
                Text(
                    "queue is empty — play something from the library",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgSubtle,
                    modifier = Modifier.padding(vertical = 16.dp),
                )
            } else {
                LazyColumn(
                    Modifier.fillMaxWidth().weight(1f, fill = false).padding(bottom = 16.dp),
                ) {
                    itemsIndexed(queue, key = { i, it -> "${it.uid}-$i" }) { i, item ->
                        QueueRow(
                            position = i,
                            title = item.title,
                            artist = item.artist,
                            durationSec = item.durationSec,
                            isCurrent = i == index,
                            isPlaying = effPlaying,
                            canMoveUp = i > 0,
                            canMoveDown = i < queue.lastIndex,
                            onJump = { app.player.jumpTo(i) },
                            onUp = { app.player.moveItem(i, i - 1) },
                            onDown = { app.player.moveItem(i, i + 1) },
                            onRemove = { app.player.removeAt(i) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun QueueRow(
    position: Int,
    title: String,
    artist: String?,
    durationSec: Double?,
    isCurrent: Boolean,
    isPlaying: Boolean,
    canMoveUp: Boolean,
    canMoveDown: Boolean,
    onJump: () -> Unit,
    onUp: () -> Unit,
    onDown: () -> Unit,
    onRemove: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(if (isCurrent) Domovoi.colors.brandSoft else androidx.compose.ui.graphics.Color.Transparent)
            .clickable(onClick = onJump)
            .padding(horizontal = 8.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
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
        IconButton(onClick = onUp, enabled = canMoveUp, modifier = Modifier.size(30.dp)) {
            Icon(Icons.Filled.KeyboardArrowUp, "move up", tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp))
        }
        IconButton(onClick = onDown, enabled = canMoveDown, modifier = Modifier.size(30.dp)) {
            Icon(Icons.Filled.KeyboardArrowDown, "move down", tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp))
        }
        IconButton(onClick = onRemove, modifier = Modifier.size(30.dp)) {
            Icon(Icons.Filled.Close, "remove", tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(15.dp))
        }
    }
}
