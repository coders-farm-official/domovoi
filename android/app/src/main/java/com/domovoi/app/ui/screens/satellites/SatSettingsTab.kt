package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

/** What the tab says above a room's settings (the iPhone's
 *  RoomsStrings.configIsReadOnly, word for word). */
internal const val ROOM_CONFIG_READ_ONLY =
    "what this room is set to. Changing any of it is done from the dashboard on a computer."

/**
 * A room's settings, read-only: what its device reports (web
 * RoomSettingsBody, without the editor). Saving them is PATCH
 * /api/satellites/{room}/config, which is admin on the server, and a phone
 * has no admin session. The editor this tab had looked editable (its read is
 * household tier) and its save 401'd from every phone (the phone admin
 * audit, 22 Nov 2026). The iPhone shows them the same way.
 */
@Composable
fun SatSettingsTab(room: String) {
    // Kept on the server for every room (domovoi/quiet_hours.py), so it is
    // here whether or not the room's device takes remote settings.
    SatQuietHoursSection(room)

    val state = rememberApi(room) {
        it.api.get("/api/satellites/$room/config").decode<SatConfigResponse>()
    }
    val cfg = state.data
    var advOpen by remember(room) { mutableStateOf(false) }

    when {
        state.loading && cfg == null -> LoadingState()
        cfg == null -> ErrorState(state.error ?: "request failed", state.refresh)
        cfg.fields.isEmpty() -> EmptyState(
            "settings unavailable",
            "the room must be online to report its settings",
        )
        else -> {
            val fields = cfg.fields
            val common = fields.filter { it.section != "advanced" }
            val advanced = fields.filter { it.section == "advanced" }

            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp)) {
                Column(
                    Modifier.fillMaxWidth()
                        .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    Text(
                        ROOM_CONFIG_READ_ONLY,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                    if (!cfg.reported) {
                        Text(
                            "waiting for the room to report its current config…",
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.warn,
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                }

                ConfigGroups(common)

                if (advanced.isNotEmpty()) {
                    HorizontalDivider(
                        color = Domovoi.colors.border,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                    Row(
                        Modifier.fillMaxWidth().clickable { advOpen = !advOpen }.padding(vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(
                            if (advOpen) Icons.Filled.KeyboardArrowDown
                            else Icons.AutoMirrored.Filled.KeyboardArrowRight,
                            contentDescription = null,
                            tint = Domovoi.colors.fg,
                            modifier = Modifier.size(16.dp),
                        )
                        Text(
                            "Advanced",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = Domovoi.colors.fg,
                        )
                    }
                    if (advOpen) ConfigGroups(advanced)
                }
            }
        }
    }
}

/* ---- Field rendering ------------------------------------------------------- */

@Composable
private fun ConfigGroups(fields: List<SatConfigField>) {
    fields.groupBy { it.group ?: "general" }.forEach { (group, list) ->
        SectionLabel(group, Modifier.padding(top = 14.dp, bottom = 2.dp))
        list.forEach { ConfigFieldRow(it) }
    }
}

@Composable
private fun ConfigFieldRow(f: SatConfigField) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                f.label ?: f.name,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f),
            )
            if (f.tier == "restart") Pill("restart", Tone.Idle)
            Text(
                configValueText(f),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
        }
        if (f.help != null) {
            Text(
                f.help,
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgSubtle,
            )
        }
    }
}

/** A field's value as words (the iPhone's roomsConfigValueText): on or off, a
 *  choice's label, a number with its unit, or a dash when there's nothing. */
internal fun configValueText(f: SatConfigField): String {
    val p = f.value as? JsonPrimitive
    if (p == null || p is JsonNull) return "—"
    if (!p.isString) {
        p.booleanOrNull?.let { return if (it) "on" else "off" }
        val n = p.longOrNull?.toString() ?: p.doubleOrNull?.toString() ?: return "—"
        return f.unit?.let { "$n $it" } ?: n
    }
    val text = p.content
    if (f.type == "choice") return f.choice_labels?.get(text) ?: text
    return text.ifEmpty { "—" }
}
