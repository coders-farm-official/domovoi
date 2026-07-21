package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Forum
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.StickyNote2
import androidx.compose.material.icons.outlined.Timer
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi

/**
 * Drill-in detail for one satellite room — the web SatDrawer analog.
 * Icon-only tabs (the 7 labels overflow narrow widths, same reason as web).
 */
private enum class SatTab(val label: String, val icon: ImageVector) {
    Overview("overview", Icons.Outlined.Info),
    Sessions("sessions", Icons.Outlined.Schedule),
    Conversations("conversations", Icons.Outlined.Forum),
    Recently("recently played", Icons.Outlined.MusicNote),
    Notes("notes", Icons.Outlined.StickyNote2),
    Timers("timers", Icons.Outlined.Timer),
    Settings("settings", Icons.Outlined.Settings),
}

@Composable
fun SatelliteDetail(
    s: Satellite,
    sats: List<Satellite>,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var tab by remember(s.room_id) { mutableStateOf(SatTab.Overview) }

    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = Domovoi.colors.card,
        border = BorderStroke(1.dp, Domovoi.colors.border),
    ) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                StatusDot(if (s.online) Tone.Ok else Tone.Idle, live = s.online)
                Text(
                    s.room_id,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Pill(s.status ?: "offline", if (s.online) Tone.Brand else Tone.Idle, live = s.online)
                IconButton(onClick = onClose) {
                    Icon(Icons.Filled.Close, contentDescription = "close", tint = Domovoi.colors.fgMuted)
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)

            Row(Modifier.fillMaxWidth()) {
                SatTab.entries.forEach { t ->
                    val selected = t == tab
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .clickable { tab = t }
                            .padding(vertical = 10.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Icon(
                            t.icon,
                            contentDescription = t.label,
                            tint = if (selected) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                            modifier = Modifier.size(18.dp),
                        )
                        Box(
                            Modifier
                                .padding(top = 6.dp)
                                .width(18.dp)
                                .height(2.dp)
                                .background(if (selected) Domovoi.colors.brand else Color.Transparent),
                        )
                    }
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)

            Column(
                Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
            ) {
                when (tab) {
                    SatTab.Overview -> SatOverviewTab(s, sats)
                    SatTab.Sessions -> SatSessionsTab(s.room_id)
                    SatTab.Conversations -> SatConversationsTab(s.room_id)
                    SatTab.Recently -> SatRecentlyPlayedTab(s.room_id)
                    SatTab.Notes -> SatNotesTab(s.room_id)
                    SatTab.Timers -> SatTimersTab(s.room_id)
                    SatTab.Settings -> SatSettingsTab(s.room_id)
                }
            }
        }
    }
}

/** Simple non-experimental dropdown used by the drop-in peer picker and the
 *  settings "choice" fields. */
@Composable
internal fun SatDropdown(
    value: String?,
    options: List<String>,
    placeholder: String,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
    onSelect: (String) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(
            onClick = { open = true },
            enabled = enabled,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(
                value?.takeIf { it.isNotBlank() } ?: placeholder,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Icon(
                Icons.Filled.ArrowDropDown,
                contentDescription = null,
                modifier = Modifier.size(18.dp),
            )
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { opt ->
                DropdownMenuItem(
                    text = { Text(opt, style = MaterialTheme.typography.bodySmall) },
                    onClick = {
                        onSelect(opt)
                        open = false
                    },
                )
            }
        }
    }
}
