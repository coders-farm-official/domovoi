package com.domovoi.app.ui.screens.music

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.RoomChip
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.toneForSlug
import com.domovoi.app.ui.theme.Domovoi

/** One card per provisioned room — web NPCard grid. Single column on
 *  phones, two columns on wider screens. */
@Composable
internal fun NowPlayingStrip(
    nowPlaying: List<NowPlayingRoom>,
    tick: Int,
    onPlayRandom: (String) -> Unit,
    onPause: (String) -> Unit,
    onResume: (String) -> Unit,
    onSkip: (String) -> Unit,
    onStop: (String) -> Unit,
    onFavorite: (String) -> Unit,
) {
    if (nowPlaying.isEmpty()) {
        DomovoiCard(Modifier.fillMaxWidth()) {
            EmptyState(
                "no rooms provisioned yet",
                "connect a satellite to bring its room online",
            )
        }
        return
    }
    val cols = if (compactWidth()) 1 else 2
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        nowPlaying.chunked(cols).forEach { row ->
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                row.forEach { np ->
                    NPCard(
                        np, tick, Modifier.weight(1f),
                        onPlayRandom = onPlayRandom, onPause = onPause, onResume = onResume,
                        onSkip = onSkip, onStop = onStop, onFavorite = onFavorite,
                    )
                }
                repeat(cols - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

@Composable
private fun NPCard(
    np: NowPlayingRoom,
    tick: Int,
    modifier: Modifier,
    onPlayRandom: (String) -> Unit,
    onPause: (String) -> Unit,
    onResume: (String) -> Unit,
    onSkip: (String) -> Unit,
    onStop: (String) -> Unit,
    onFavorite: (String) -> Unit,
) {
    val context = LocalContext.current
    val playing = np.state == "play" && np.song != null
    val paused = np.state == "pause" && np.song != null
    val songDur = np.song?.durationSec ?: 0.0
    val elapsed = if (playing || paused) (np.elapsedSec ?: 0.0) + (if (playing) tick else 0) else 0.0
    val progress = if (songDur > 0) (elapsed / songDur).toFloat().coerceAtMost(1f) else 0f

    DomovoiCard(modifier = modifier, padding = 14) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            RoomChip(np.roomId)
            if (playing) Pill("live", Tone.Brand, live = true)
            if (paused) Pill("paused", Tone.Idle)
            // Provider-agnostic "open externally" pill: rendered whenever
            // the now-playing source supplied a source_url (design §10.2).
            // Label + tone come from the server's handler_display metadata.
            if ((playing || paused) && np.sourceUrl != null) {
                val caps = LocalCapabilities.current
                Box(
                    Modifier
                        .clip(RoundedCornerShape(999.dp))
                        .clickable {
                            runCatching {
                                context.startActivity(
                                    Intent(Intent.ACTION_VIEW, Uri.parse(np.sourceUrl)),
                                )
                            }
                        },
                ) {
                    Pill(
                        (caps.labelFor(np.source) ?: "source").lowercase() + " ↗",
                        toneForSlug(caps.toneFor(np.source)),
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))

        if (playing || paused) {
            val title = np.song?.title
                ?: np.song?.file?.let { f ->
                    if (f.startsWith("http")) "online stream" else f.substringAfterLast('/')
                }
                ?: "unknown"
            Text(
                title,
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val artist = np.song?.artist
            if (artist != null) {
                Text(
                    artist,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.height(10.dp))
            ProgressBar(progress)
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    fmtDur(elapsed) + if (songDur > 0) " / " + fmtDur(songDur) else "",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                )
                SmallIconButton(Icons.Filled.SkipPrevious, "previous", enabled = false) {}
                SmallIconButton(
                    if (playing) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                    if (playing) "pause" else "resume",
                    tint = Domovoi.colors.fg,
                ) { if (playing) onPause(np.roomId) else onResume(np.roomId) }
                SmallIconButton(Icons.Filled.SkipNext, "skip") { onSkip(np.roomId) }
                SmallIconButton(Icons.Filled.Stop, "stop") { onStop(np.roomId) }
                SmallIconButton(
                    if (np.favorited) Icons.Filled.Favorite else Icons.Filled.FavoriteBorder,
                    "favorite",
                    tint = if (np.favorited) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                ) { onFavorite(np.roomId) }
            }
        } else {
            Text(
                "nothing playing in ${np.roomId}",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            Spacer(Modifier.height(10.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                OutlinedButton(onClick = { onPlayRandom(np.roomId) }) {
                    Icon(
                        Icons.Filled.Shuffle,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = Domovoi.colors.fg,
                    )
                    Spacer(Modifier.size(6.dp))
                    Text("play something", color = Domovoi.colors.fg)
                }
            }
        }
    }
}
