package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.ApiState
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi

/** Playlists tab — Favorites (virtual) pinned by the backend, then real
 *  playlists. Tap opens the PlaylistDrawer. Mirrors web PlaylistsTab. */
internal fun LazyListScope.playlistsTab(
    playlists: ApiState<List<Playlist>>,
    onSelect: (Playlist) -> Unit,
    onPlay: (Playlist) -> Unit,
) {
    val list = playlists.data.orEmpty()
    when {
        playlists.loading && list.isEmpty() -> item(key = "pl-loading") { LoadingState() }
        playlists.error != null && list.isEmpty() -> item(key = "pl-error") {
            ErrorState(playlists.error ?: "request failed", playlists.refresh)
        }
        list.isEmpty() -> item(key = "pl-empty") {
            EmptyState(
                "no playlists yet",
                "tap + on a library row to start one, or say \"make a new playlist called X\"",
            )
        }
        else -> items(list, key = { "pl-${it.id}" }) { p ->
            PlaylistRow(p, onClick = { onSelect(p) }, onPlay = { onPlay(p) })
        }
    }
}

@Composable
internal fun PlaylistCover(p: Playlist, sizeDp: Int) {
    val coverBg = p.coverColor?.let { c ->
        runCatching { androidx.compose.ui.graphics.Color(android.graphics.Color.parseColor(c)) }.getOrNull()
    }
    Box(
        Modifier
            .size(sizeDp.dp)
            .background(
                when {
                    p.isVirtual -> Domovoi.colors.brandSoft
                    coverBg != null -> coverBg
                    else -> Domovoi.colors.sunken
                },
                RoundedCornerShape(8.dp),
            )
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp)),
        contentAlignment = Alignment.Center,
    ) {
        when {
            !p.coverEmoji.isNullOrBlank() && !p.isVirtual ->
                Text(p.coverEmoji, style = MaterialTheme.typography.titleMedium)
            p.isVirtual -> Icon(
                Icons.Filled.Star, contentDescription = null,
                tint = Domovoi.colors.brand, modifier = Modifier.size((sizeDp / 2).dp),
            )
            else -> Icon(
                Icons.Filled.QueueMusic, contentDescription = null,
                tint = Domovoi.colors.fgMuted, modifier = Modifier.size((sizeDp / 2).dp),
            )
        }
    }
}

@Composable
private fun PlaylistRow(p: Playlist, onClick: () -> Unit, onPlay: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PlaylistCover(p, 36)
        Column(Modifier.weight(1f).padding(horizontal = 10.dp)) {
            Text(
                p.name,
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                when {
                    !p.description.isNullOrBlank() -> p.description
                    p.isVirtual -> "derived from your favorites"
                    else -> "${p.trackCount} track" + (if (p.trackCount == 1) "" else "s")
                },
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(
                "${p.trackCount}",
                style = MaterialTheme.typography.labelMedium,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fg,
            )
            Text(
                if (p.createdAt != null) relTime(p.createdAt) else "—",
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgSubtle,
            )
        }
        SmallIconButton(Icons.Filled.PlayArrow, "play", tint = Domovoi.colors.fg) { onPlay() }
    }
    HorizontalDivider(color = Domovoi.colors.borderSoft)
}
