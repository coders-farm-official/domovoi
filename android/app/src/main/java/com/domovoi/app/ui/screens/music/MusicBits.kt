package com.domovoi.app.ui.screens.music

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi

/** All screen-level UI state — hoisted out of LazyColumn item blocks so it
 *  survives items scrolling out of composition. Remembered in MusicScreen. */
internal class MusicUi {
    var tab by mutableIntStateOf(0)

    // Library tab (web useLibraryPage)
    var q by mutableStateOf("")
    var source by mutableStateOf("all")
    var sort by mutableStateOf("added_desc")
    var favoritedOnly by mutableStateOf(false)
    var page by mutableIntStateOf(0)
    val selectedIds = mutableStateListOf<Long>()
    var bulkPid by mutableStateOf<Long?>(null)

    // Drawers / sheets
    var detailTrack by mutableStateOf<LibraryTrack?>(null)
    var openPlaylist by mutableStateOf<Playlist?>(null)
    var addTrack by mutableStateOf<LibraryTrack?>(null)
    var saveQueueOpen by mutableStateOf(false)

    // Distinct `source` values observed on fetched library pages — feeds
    // the data-driven source filter (open enum: plugins stamp their own
    // slugs, so the options can't be hardcoded).
    val seenSources = mutableStateListOf<String>()

    var uploading by mutableStateOf(false)
}

@Composable
internal fun compactWidth(): Boolean =
    currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT

internal fun truncMid(s: String, max: Int = 50): String =
    if (s.length <= max) s else s.take(max / 2) + "…" + s.takeLast(max / 2)

/** Thin amber progress bar — the web's 4px track bar. */
@Composable
internal fun ProgressBar(fraction: Float, modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxWidth()
            .height(4.dp)
            .clip(RoundedCornerShape(2.dp))
            .background(Domovoi.colors.sunken),
    ) {
        Box(
            Modifier
                .fillMaxWidth(fraction.coerceIn(0f, 1f))
                .fillMaxHeight()
                .background(Domovoi.colors.brand),
        )
    }
}

/** Compact 32dp icon button for action rows. */
@Composable
internal fun SmallIconButton(
    icon: ImageVector,
    contentDescription: String?,
    tint: Color = Domovoi.colors.fgMuted,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(32.dp)) {
        Icon(
            icon,
            contentDescription = contentDescription,
            tint = if (enabled) tint else Domovoi.colors.fgFaint,
            modifier = Modifier.size(18.dp),
        )
    }
}

/** The web <select> analog: bordered chip that opens a DropdownMenu. */
@Composable
internal fun SelectMenu(
    label: String?,
    value: String,
    options: List<Pair<String, String>>,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var open by remember { mutableStateOf(false) }
    Row(
        modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (label != null) {
            Text(label.lowercase(), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        }
        Box {
            Row(
                Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .border(1.dp, Domovoi.colors.border, RoundedCornerShape(6.dp))
                    .clickable { open = true }
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    options.firstOrNull { it.first == value }?.second ?: value,
                    style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Icon(
                    Icons.Filled.ArrowDropDown,
                    contentDescription = null,
                    tint = Domovoi.colors.fgMuted,
                    modifier = Modifier.size(16.dp),
                )
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                options.forEach { (v, l) ->
                    DropdownMenuItem(
                        text = { Text(l, style = MaterialTheme.typography.bodyMedium) },
                        onClick = { open = false; onSelect(v) },
                    )
                }
            }
        }
    }
}

/** Pill-shaped selectable chip (room pickers, speed, sleep presets). */
@Composable
internal fun SmallChip(text: String, selected: Boolean = false, onClick: () -> Unit) {
    Box(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(if (selected) Domovoi.colors.brandSoft else Color.Transparent)
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 5.dp),
    ) {
        Text(
            text.lowercase(),
            style = MaterialTheme.typography.labelMedium,
            color = if (selected) Domovoi.colors.brandPress else Domovoi.colors.fgMuted,
            maxLines = 1,
        )
    }
}

/** Wrapping row of room chips with a single selection. */
@Composable
internal fun RoomPickRow(rooms: List<String>, selected: String, onSelect: (String) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        rooms.forEach { r -> SmallChip(r, selected == r) { onSelect(r) } }
    }
}

/** Right-hand drawer (the web <aside>): full-screen sheet on compact width,
 *  fixed-width panel with scrim on wide. Back gesture closes. */
@Composable
internal fun DrawerScaffold(
    onClose: () -> Unit,
    widthDp: Int = 420,
    content: @Composable ColumnScope.() -> Unit,
) {
    val compact = compactWidth()
    BackHandler(onBack = onClose)
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.35f))
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onClose,
            ),
    ) {
        Surface(
            modifier = Modifier
                .align(Alignment.CenterEnd)
                .fillMaxHeight()
                .then(if (compact) Modifier.fillMaxWidth() else Modifier.width(widthDp.dp))
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = {},
                ),
            color = Domovoi.colors.card,
            border = BorderStroke(1.dp, Domovoi.colors.border),
        ) {
            Column(content = content)
        }
    }
}

@Composable
internal fun DrawerHeader(
    eyebrow: String,
    title: String? = null,
    sub: String? = null,
    onClose: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 16.dp, end = 8.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            SectionLabel(eyebrow)
            if (title != null) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (sub != null) {
                Text(
                    sub,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        IconButton(onClick = onClose) {
            Icon(Icons.Filled.Close, contentDescription = "close", tint = Domovoi.colors.fgMuted)
        }
    }
    HorizontalDivider(color = Domovoi.colors.border)
}
