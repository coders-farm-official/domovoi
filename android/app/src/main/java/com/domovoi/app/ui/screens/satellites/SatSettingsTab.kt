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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.screens.settings.SettingsDropdown
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
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
 * A room's settings: what its device reports (web RoomSettingsBody). Saving
 * them is PATCH /api/satellites/{room}/config, admin (security tier) on the
 * server. Without admin on this phone they are read-only, as they have been
 * since the phone admin audit (22 Nov 2026: an editor whose save 401'd from
 * every phone). With admin held (SatAdmin.kt, never on a shared screen) the
 * common settings are an editor like the dashboard's: Save asks first in the
 * dashboard's words and goes through AdminSession.act; the Advanced ones stay
 * read-only ([RoomAdminWords.ADVANCED_ON_DASHBOARD]). The iPhone does the same.
 */
@Composable
fun SatSettingsTab(room: String) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val shared = LocalSharedScreen.current
    val adminState by app.admin.state.collectAsState()
    val held = roomAdminHeld(adminState, shared)
    val scope = rememberCoroutineScope()
    val adminApi = remember(app) { RoomAdminApi(app.api) }
    val edits = remember(room) { mutableStateMapOf<String, kotlinx.serialization.json.JsonPrimitive?>() }
    val rejected = remember(room) { mutableStateMapOf<String, String>() }
    var saving by remember(room) { mutableStateOf(false) }
    var confirmSave by remember(room) { mutableStateOf(false) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }

    // Kept on the server for every room (domovoi/quiet_hours.py), so it is
    // here whether or not the room's device takes remote settings.
    SatQuietHoursSection(room)

    val state = rememberApi(room) {
        it.api.get(satPath(room, "/config")).decode<SatConfigResponse>()
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
                        when {
                            held -> RoomAdminWords.CONFIG_EDIT
                            // A shared screen never holds admin: its words stay.
                            shared -> ROOM_CONFIG_READ_ONLY
                            else -> RoomAdminWords.CONFIG_OR_PHONE
                        },
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

                if (held) {
                    EditableGroups(common, edits, rejected, saving)
                    val changes = configChanges(fields, edits)
                    val problems = configEditProblems(fields, edits)
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Button(
                            enabled = !saving && changes.isNotEmpty() && problems.isEmpty(),
                            onClick = { confirmSave = true },
                        ) { Text(if (saving) RoomAdminWords.SAVING else RoomAdminWords.SAVE) }
                        if (edits.isNotEmpty()) {
                            TextButton(enabled = !saving, onClick = { edits.clear(); rejected.clear() }) {
                                Text(RoomAdminWords.UNDO, color = Domovoi.colors.fgMuted)
                            }
                        }
                    }
                } else {
                    ConfigGroups(common)
                }

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
                    if (advOpen) {
                        if (held) {
                            Text(
                                RoomAdminWords.ADVANCED_ON_DASHBOARD,
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fgMuted,
                            )
                        }
                        ConfigGroups(advanced)
                    }
                }
            }
            if (confirmSave) {
                ConfirmDialog(
                    title = RoomAdminWords.SAVE,
                    body = RoomAdminWords.saveConfirm(room),
                    confirmLabel = "save",
                    onConfirm = {
                        val changes = configChanges(fields, edits)
                        saving = true
                        scope.launch {
                            try {
                                val r = app.admin.act { h, srv -> adminApi.saveConfig(room, changes, h, srv) }
                                // What was refused stays edited, with why under it.
                                rejected.clear()
                                rejected.putAll(r.rejected)
                                edits.keys.retainAll(r.rejected.keys)
                                toast(RoomAdminWords.saved(room, r))
                                state.refresh()
                            } catch (e: CancellationException) {
                                throw e
                            } catch (e: Exception) {
                                toast(if (e is AdminSession.AdminUnavailable) e.message ?: "admin isn't available" else failureText("save", e))
                            } finally {
                                saving = false
                            }
                        }
                    },
                    onDismiss = { confirmSave = false },
                )
            }
        }
    }
}

/* ---- The editor (admin held) ---------------------------------------------- */

@Composable
private fun EditableGroups(
    fields: List<SatConfigField>,
    edits: MutableMap<String, kotlinx.serialization.json.JsonPrimitive?>,
    rejected: Map<String, String>,
    saving: Boolean,
) {
    fields.groupBy { it.group ?: "general" }.forEach { (group, list) ->
        SectionLabel(group, Modifier.padding(top = 14.dp, bottom = 2.dp))
        list.forEach { f ->
            if (f.editableHere()) EditableFieldRow(f, edits, rejected[f.name], saving) else ConfigFieldRow(f)
        }
    }
}

@Composable
private fun EditableFieldRow(
    f: SatConfigField,
    edits: MutableMap<String, kotlinx.serialization.json.JsonPrimitive?>,
    refused: String?,
    saving: Boolean,
) {
    val current = if (f.name in edits) edits[f.name] else f.value as? JsonPrimitive
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(f.label ?: f.name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg, modifier = Modifier.weight(1f))
            if (f.tier == "restart") Pill("restart", Tone.Idle)
            when (f.type) {
                "bool" -> Switch(
                    checked = current?.booleanOrNull == true, enabled = !saving,
                    onCheckedChange = { edits[f.name] = JsonPrimitive(it) },
                )
                "choice" -> {
                    val choices = f.choices.orEmpty()
                    val now = current?.takeIf { it.isString }?.content
                    SettingsDropdown(
                        selected = now ?: "",
                        options = choices,
                        label = { c -> if (c.isEmpty()) "(not set)" else f.choice_labels?.get(c) ?: c },
                        onSelect = { edits[f.name] = JsonPrimitive(it) },
                        enabled = !saving,
                    )
                }
                else -> Unit
            }
        }
        if (f.type == "int" || f.type == "float" || f.type == "str") {
            val text = when {
                current == null || current is JsonNull -> ""
                else -> current.content
            }
            OutlinedTextField(
                value = text,
                onValueChange = { typed ->
                    // As typed: a number is read at save (configChanges), and
                    // one that isn't one says so under the row.
                    edits[f.name] = JsonPrimitive(typed)
                },
                singleLine = true,
                enabled = !saving,
                suffix = f.unit?.let { u -> { Text(u) } },
                keyboardOptions = if (f.type == "str") KeyboardOptions.Default else KeyboardOptions(keyboardType = KeyboardType.Decimal),
                isError = configEditProblem(f, current) != null,
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
            )
        }
        (configEditProblem(f, current) ?: refused)?.let {
            Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.err)
        }
        if (f.help != null) {
            Text(f.help, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgSubtle)
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
