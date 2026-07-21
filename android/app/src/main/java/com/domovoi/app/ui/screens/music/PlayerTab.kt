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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Cast
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.MusicNote
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
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.player.PlayKind
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlin.math.abs

/** Player tab — the rich local-player panel bound to PlayerController's
 *  state flows: cover, seek, transport, speed, sleep timer, cast target,
 *  chapters and the queue. Android analog of web NowPlayingPanel. */
@Composable
internal fun PlayerPanel(rooms: List<String>, onSaveQueue: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val queue by app.player.queue.collectAsState()
    val index by app.player.index.collectAsState()
    val playing by app.player.isPlaying.collectAsState()
    val pos by app.player.positionSec.collectAsState()
    val dur by app.player.durationSec.collectAsState()
    val speed by app.player.speed.collectAsState()
    val target by app.player.target.collectAsState()
    val remote by app.player.remote.collectAsState()
    val sleepSec by app.player.sleepRemainingSec.collectAsState()

    val roomTarget = target as? PlayTarget.Room
    val isRemote = roomTarget != null
    val current = queue.getOrNull(index)

    if (current == null && !isRemote) {
        EmptyState(
            "nothing queued",
            "play a library track on this device from the library tab to start",
        )
        return
    }

    val effPlaying = if (isRemote) remote?.state == "play" else playing
    val effDur = if (isRemote) (remote?.durationSec ?: 0.0)
    else if (dur > 0) dur else (current?.durationSec ?: 0.0)
    val effPos = if (isRemote) (remote?.elapsedSec ?: 0.0) else pos
    val seekable = !isRemote && current?.seekable != false

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
            val cover = if (isRemote) null else current?.coverPath
            if (cover != null) {
                AsyncImage(
                    model = app.api.absolute(cover),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.size(112.dp).clip(RoundedCornerShape(10.dp)),
                )
            } else {
                Box(
                    Modifier
                        .size(112.dp)
                        .background(
                            Brush.linearGradient(listOf(Color(0xFFF2CD8C), Color(0xFFDD8A2E))),
                            RoundedCornerShape(10.dp),
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Filled.MusicNote, contentDescription = null,
                        tint = Color.White.copy(alpha = 0.85f), modifier = Modifier.size(44.dp),
                    )
                }
            }
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

        // Seek
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

        // Transport + cast
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SmallIconButton(Icons.Filled.SkipPrevious, "previous", enabled = !isRemote) {
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
                    DropdownMenuItem(
                        text = { Text("this device") },
                        onClick = {
                            castOpen = false
                            scope.launch {
                                runCatching { app.player.castTo(null) }
                                    .onSuccess { toast("playing on this device") }
                                    .onFailure { toast("cast failed: ${it.message}") }
                            }
                        },
                    )
                    rooms.forEach { r ->
                        DropdownMenuItem(
                            text = { Text(r) },
                            onClick = {
                                castOpen = false
                                scope.launch {
                                    runCatching { app.player.castTo(r) }
                                        .onSuccess { toast("casting to $r") }
                                        .onFailure { toast("cast failed: ${it.message}") }
                                }
                            },
                        )
                    }
                }
            }
        }

        // Speed (local only)
        if (!isRemote) {
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

        // Sleep timer
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

        // Chapters (podcasts / audiobooks)
        val chapters = if (isRemote) emptyList() else current?.chapters.orEmpty()
        if (chapters.isNotEmpty()) {
            HorizontalDivider(color = Domovoi.colors.borderSoft)
            SectionLabel("chapters · ${chapters.size}")
            var cur = 0
            chapters.forEachIndexed { i, c -> if (effPos >= c.startSec) cur = i }
            Column {
                chapters.forEachIndexed { i, c ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .background(
                                if (i == cur) Domovoi.colors.brandSoft else Color.Transparent,
                                RoundedCornerShape(6.dp),
                            )
                            .clickable { app.player.jumpToChapter(i) }
                            .padding(horizontal = 8.dp, vertical = 8.dp),
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
            }
        }

        // Queue
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SectionLabel("queue · ${queue.size}", Modifier.weight(1f))
            TextButton(
                onClick = onSaveQueue,
                enabled = queue.any { it.kind == PlayKind.Library },
            ) { Text("save as playlist", color = Domovoi.colors.brand) }
            TextButton(
                onClick = { app.player.clearQueue() },
                enabled = queue.isNotEmpty(),
            ) { Text("clear", color = Domovoi.colors.fgMuted) }
        }
        if (queue.isEmpty()) {
            Text(
                "queue is empty",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
        } else {
            Column {
                queue.forEachIndexed { i, item ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .background(
                                if (i == index) Domovoi.colors.brandSoft else Color.Transparent,
                                RoundedCornerShape(6.dp),
                            )
                            .clickable { app.player.jumpTo(i) }
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            "${i + 1}",
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = Domovoi.colors.fgFaint,
                            modifier = Modifier.width(22.dp),
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
                            enabled = i < queue.size - 1,
                        ) { app.player.moveItem(i, i + 1) }
                        SmallIconButton(Icons.Filled.Close, "remove from queue") {
                            app.player.removeAt(i)
                        }
                    }
                }
            }
        }
    }
}

private fun speedLabel(v: Float): String =
    if (v == v.toInt().toFloat()) "${v.toInt()}x" else "${v}x"
