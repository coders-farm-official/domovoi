package com.domovoi.app.ui.shell.player

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
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
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi

/**
 * Global docked mini player (web MiniPlayer analog). Collapses to nothing
 * when the queue is empty and no room is being remote-controlled.
 */
@Composable
fun MiniPlayer(onOpenPlayer: () -> Unit) {
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
    if (current == null && !isRemote) return

    val title = if (isRemote) remote?.title ?: "—" else current?.title ?: "—"
    val artist = if (isRemote) remote?.artist else current?.artist
    val effPlaying = if (isRemote) remote?.state == "play" else playing
    val progress = if (isRemote) {
        val d = remote?.durationSec ?: 0.0
        if (d > 0) ((remote?.elapsedSec ?: 0.0) / d).toFloat() else 0f
    } else if (dur > 0) (pos / dur).toFloat() else 0f

    Surface(color = Domovoi.colors.card) {
        Column {
            // progress hairline
            Box(Modifier.fillMaxWidth().height(2.dp).background(Domovoi.colors.border)) {
                Box(
                    Modifier.fillMaxWidth(progress.coerceIn(0f, 1f)).fillMaxHeight()
                        .background(Domovoi.colors.brand)
                )
            }
            Row(
                Modifier.fillMaxWidth().clickable(onClick = onOpenPlayer).padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (!isRemote && current?.coverPath != null) {
                    AsyncImage(
                        model = app.api.absolute(current.coverPath),
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.size(36.dp).clip(RoundedCornerShape(6.dp)),
                    )
                }
                Column(Modifier.weight(1f).padding(horizontal = 10.dp)) {
                    Text(
                        title,
                        style = MaterialTheme.typography.titleSmall,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (isRemote) {
                            Pill("casting to ${(target as PlayTarget.Room).roomId}", Tone.Brand, live = effPlaying)
                        } else if (artist != null) {
                            Text(
                                artist,
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fgMuted,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
                IconButton(onClick = { app.player.toggle() }) {
                    Icon(
                        if (effPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                        contentDescription = "play/pause",
                        tint = Domovoi.colors.fg,
                    )
                }
                IconButton(onClick = { app.player.next() }) {
                    Icon(Icons.Filled.SkipNext, contentDescription = "next", tint = Domovoi.colors.fgMuted)
                }
                IconButton(onClick = { app.player.stop() }) {
                    Icon(Icons.Filled.Close, contentDescription = "stop", tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(16.dp))
                }
            }
        }
    }
}
