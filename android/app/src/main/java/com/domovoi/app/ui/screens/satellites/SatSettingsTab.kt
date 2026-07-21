package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Per-satellite config editor — web RoomSettingsBody analog. Saving pushes
 * the edits to the Pi, which rewrites config.toml and restarts to apply.
 */
@Composable
fun SatSettingsTab(room: String) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val state = rememberApi(room) {
        it.api.get("/api/satellites/$room/config").decode<SatConfigResponse>()
    }
    val cfg = state.data

    val edits = remember(room) { mutableStateMapOf<String, JsonElement>() }
    var saving by remember(room) { mutableStateOf(false) }
    var saveError by remember(room) { mutableStateOf<String?>(null) }
    var rejected by remember(room) { mutableStateOf<Map<String, String>>(emptyMap()) }
    var restarting by remember(room) { mutableStateOf(false) }
    var advOpen by remember(room) { mutableStateOf(false) }
    var confirmSave by remember(room) { mutableStateOf(false) }

    fun save() {
        if (edits.isEmpty()) return
        scope.launch {
            saving = true
            saveError = null
            restarting = false
            runCatching {
                app.api.patch(
                    "/api/satellites/$room/config",
                    buildJsonObject { put("changes", JsonObject(edits.toMap())) },
                ).decode<SatConfigSaveResult>()
            }.onSuccess { res ->
                rejected = res.rejected
                restarting = res.restarting
                // Keep only the still-rejected edits so the user can fix them.
                val keep = edits.filterKeys { it in res.rejected }
                edits.clear()
                edits.putAll(keep)
                when {
                    res.rejected.isNotEmpty() -> toast(
                        "rejected: " + res.rejected.entries.joinToString("; ") { "${it.key} (${it.value})" },
                    )
                    res.restarting -> toast("$room restarting to apply…")
                    else -> toast("saved")
                }
            }.onFailure {
                saveError = it.message
                toast("save failed: ${it.message}")
            }
            saving = false
        }
    }

    when {
        state.loading && cfg == null -> LoadingState()
        cfg == null -> ErrorState(state.error ?: "request failed", state.refresh)
        cfg.fields.isEmpty() -> EmptyState(
            "settings unavailable",
            "the satellite must be online to edit its config",
        )
        else -> {
            val fields = cfg.fields
            val common = fields.filter { it.section != "advanced" }
            val advanced = fields.filter { it.section == "advanced" }
            val dirtyCount = edits.size

            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp)) {
                Column(
                    Modifier.fillMaxWidth()
                        .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    Text(
                        "Saving rewrites the Pi's config.toml and restarts the satellite to apply — " +
                            "it drops offline for a few seconds.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                    if (!cfg.reported) {
                        Text(
                            "waiting for the satellite to report its current config…",
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.warn,
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                }

                ConfigGroups(
                    fields = common,
                    valueOf = { f -> edits[f.name] ?: f.value },
                    onChange = { name, v -> edits[name] = v },
                )

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
                        Column(
                            Modifier.fillMaxWidth()
                                .background(Domovoi.colors.warnSoft, RoundedCornerShape(8.dp))
                                .border(1.dp, Domovoi.colors.warn, RoundedCornerShape(8.dp))
                                .padding(horizontal = 12.dp, vertical = 10.dp),
                        ) {
                            Text(
                                "These can take the satellite offline. A wrong audio device, LED, or " +
                                    "mic-gain value can leave it without mic or sound until you SSH in and " +
                                    "fix ~/.domovoi/config.toml (a .bak is saved on every change).",
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fg,
                            )
                        }
                        ConfigGroups(
                            fields = advanced,
                            valueOf = { f -> edits[f.name] ?: f.value },
                            onChange = { name, v -> edits[name] = v },
                        )
                    }
                }

                HorizontalDivider(
                    color = Domovoi.colors.borderSoft,
                    modifier = Modifier.padding(top = 12.dp, bottom = 10.dp),
                )
                saveError?.let {
                    Text(
                        "save failed: $it",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.err,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
                if (rejected.isNotEmpty()) {
                    Text(
                        "rejected: " + rejected.entries.joinToString("; ") { "${it.key} (${it.value})" },
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.err,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
                if (restarting) {
                    Text(
                        "saved — $room is restarting to apply. It'll reconnect shortly; reopen to confirm.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.warn,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        if (dirtyCount > 0) "$dirtyCount unsaved" else "no changes",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                    )
                    Spacer(Modifier.weight(1f))
                    Button(
                        onClick = { confirmSave = true },
                        enabled = !saving && dirtyCount > 0,
                    ) {
                        Text(if (saving) "saving…" else "save & restart")
                    }
                }
            }

            if (confirmSave) {
                ConfirmDialog(
                    title = "save & restart $room?",
                    body = "It'll drop offline for a few seconds while it applies the change.",
                    confirmLabel = "save & restart",
                    onConfirm = { save() },
                    onDismiss = { confirmSave = false },
                )
            }
        }
    }
}

/* ---- Field rendering ------------------------------------------------------- */

@Composable
private fun ConfigGroups(
    fields: List<SatConfigField>,
    valueOf: (SatConfigField) -> JsonElement?,
    onChange: (String, JsonElement) -> Unit,
) {
    fields.groupBy { it.group ?: "general" }.forEach { (group, list) ->
        SectionLabel(group, Modifier.padding(top = 14.dp, bottom = 2.dp))
        list.forEach { f ->
            ConfigFieldRow(f, valueOf(f)) { v -> onChange(f.name, v) }
        }
    }
}

@Composable
private fun ConfigFieldRow(
    f: SatConfigField,
    value: JsonElement?,
    onChange: (JsonElement) -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    f.label ?: f.name,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                )
                if (f.help != null) {
                    Text(
                        f.help,
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgSubtle,
                    )
                }
            }
            if (f.tier == "restart") Pill("restart", Tone.Idle)
            if (f.type == "bool") {
                Switch(
                    checked = jsonBool(value),
                    onCheckedChange = { onChange(JsonPrimitive(it)) },
                )
            }
        }
        when (f.type) {
            "bool" -> Unit
            "choice" -> SatDropdown(
                value = jsonText(value),
                options = f.choices ?: emptyList(),
                placeholder = "choose…",
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                onSelect = { onChange(JsonPrimitive(it)) },
            )
            "int", "float" -> NumberEditor(f, value, onChange)
            else -> TextEditor(f, value, onChange)
        }
    }
}

@Composable
private fun NumberEditor(
    f: SatConfigField,
    value: JsonElement?,
    onChange: (JsonElement) -> Unit,
) {
    var draft by remember(f.name) { mutableStateOf(jsonText(value)) }
    val unit = f.unit
    val suffix: (@Composable () -> Unit)? = if (unit == null) null else {
        { Text(unit, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted) }
    }
    OutlinedTextField(
        value = draft,
        onValueChange = { txt ->
            draft = txt
            val el: JsonElement = if (f.type == "int") {
                txt.trim().toLongOrNull()?.let { JsonPrimitive(it) } ?: JsonPrimitive(txt)
            } else {
                txt.trim().toDoubleOrNull()?.let { JsonPrimitive(it) } ?: JsonPrimitive(txt)
            }
            onChange(el)
        },
        singleLine = true,
        textStyle = MaterialTheme.typography.bodySmall,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        suffix = suffix,
        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
    )
}

@Composable
private fun TextEditor(
    f: SatConfigField,
    value: JsonElement?,
    onChange: (JsonElement) -> Unit,
) {
    var draft by remember(f.name) { mutableStateOf(jsonText(value)) }
    OutlinedTextField(
        value = draft,
        onValueChange = { txt ->
            draft = txt
            onChange(JsonPrimitive(txt))
        },
        singleLine = true,
        textStyle = MaterialTheme.typography.bodySmall,
        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
    )
}

private fun jsonText(v: JsonElement?): String {
    val p = v as? JsonPrimitive ?: return ""
    return if (p is JsonNull) "" else p.content
}

private fun jsonBool(v: JsonElement?): Boolean =
    (v as? JsonPrimitive)?.booleanOrNull ?: false
