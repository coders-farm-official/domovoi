package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Configuration tab (settings.jsx ConfigPanel): the Version section (web
 * build + core git SHA + update check / pull) above the live
 * config editor. Common settings up front; "Advanced" collapses behind a
 * warning because wrong values can stop the core service booting.
 */
@Composable
internal fun ConfigPanel() {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { VersionCard() }
        item { ConfigEditorCard() }
    }
}

// ---------------------------------------------------------------------------
// Version
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun VersionCard() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val cfg = rememberApi(fetch = { it.api.get("/api/config").decode<ConfigSummary>() })
    val core = rememberApi(fetch = { it.api.get("/api/config/version").decode<VersionInfo>() })

    var checking by remember { mutableStateOf(false) }
    var pulling by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<VersionCheck?>(null) }
    var confirmPull by remember { mutableStateOf(false) }

    val sha = core.data?.sha
    val behind = status?.takeIf { it.upstream != null }?.behind

    fun check() {
        scope.launch {
            checking = true
            status = null
            try {
                val res = app.api.post("/api/config/version/check").decode<VersionCheck>()
                status = res
                when {
                    res.error != null -> toast("update check failed: ${res.error}")
                    res.upstream == null -> toast("no upstream configured — can't check")
                    (res.behind ?: 0) > 0 ->
                        toast("${res.behind} commit${if (res.behind == 1) "" else "s"} behind")
                    else -> toast("up to date")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("update check failed: ${e.message ?: "error"}")
            } finally {
                checking = false
            }
        }
    }

    fun pull() {
        scope.launch {
            pulling = true
            try {
                val res = app.api.post("/api/config/version/pull").decode<VersionPull>()
                if (res.pulled) {
                    toast(
                        "pulled" + (res.new_sha?.let { " — now $it" } ?: "") +
                            ". Restart the core service by hand.",
                    )
                    status = null
                    core.refresh()
                } else {
                    toast("pull failed: ${res.error ?: "unknown"}")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("pull failed: ${e.message ?: "error"}")
            } finally {
                pulling = false
            }
        }
    }

    PanelCard(
        "Version",
        "Build identifiers for the web dashboard and the core service, plus a git update check.",
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Column {
                SectionLabel("web build")
                Text(
                    cfg.data?.web_version ?: "—",
                    style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                    color = if (cfg.data?.web_version != null) Domovoi.colors.fg else Domovoi.colors.fgFaint,
                )
            }
            Column {
                SectionLabel("core")
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        sha ?: "—",
                        style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                        color = if (sha != null) Domovoi.colors.fg else Domovoi.colors.fgFaint,
                    )
                    if (sha?.endsWith("-dirty") == true) Pill("uncommitted changes", Tone.Warn)
                }
            }
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                OutlinedButton(enabled = !checking, onClick = { check() }) {
                    Icon(Icons.Filled.Refresh, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text(if (checking) "Checking…" else "Check for updates")
                }
                if ((behind ?: -1) >= 0) {
                    Text(
                        if (behind!! > 0) {
                            "$behind commit${if (behind == 1) "" else "s"} behind"
                        } else {
                            "up to date"
                        },
                        style = MaterialTheme.typography.labelMedium,
                        color = if (behind > 0) Domovoi.colors.warn else Domovoi.colors.ok,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
                if ((behind ?: 0) > 0) {
                    Button(enabled = !pulling, onClick = { confirmPull = true }) {
                        Icon(Icons.Filled.Download, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text(if (pulling) "Pulling…" else "Pull latest")
                    }
                }
            }
            if ((behind ?: 0) > 0) {
                Text(
                    "Pull updates the host files only — the core service does not self-restart; bounce it by hand.",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
    }

    if (confirmPull) {
        ConfirmDialog(
            title = "Pull the latest domovoi code?",
            body = "git pull --ff-only updates the files on the core service host but does NOT restart " +
                "the core service — you must bounce the service by hand for the new code to take " +
                "effect. Satellites can then be upgraded individually.",
            confirmLabel = "pull",
            onConfirm = { pull() },
            onDismiss = { confirmPull = false },
        )
    }
}

// ---------------------------------------------------------------------------
// Editable config
// ---------------------------------------------------------------------------

@Composable
private fun ConfigEditorCard() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val state = rememberApi(fetch = { it.api.get("/api/config/editable").decode<ConfigEditable>() })
    val fields = state.data?.fields ?: emptyList()
    val edits = remember { mutableStateMapOf<String, JsonElement>() }
    var saving by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<ConfigSaveResult?>(null) }
    var saveError by remember { mutableStateOf<String?>(null) }
    var advOpen by remember { mutableStateOf(false) }

    val common = fields.filter { it.section != "advanced" }
    val advanced = fields.filter { it.section == "advanced" }
    val dirtyCount = edits.size

    fun save() {
        if (edits.isEmpty()) return
        scope.launch {
            saving = true
            result = null
            saveError = null
            try {
                val res = app.api.patch(
                    "/api/config/editable",
                    buildJsonObject { put("changes", JsonObject(edits.toMap())) },
                ).decode<ConfigSaveResult>()
                result = res
                // Keep only the still-rejected edits (the web behavior).
                val keep = edits.filterKeys { it in res.rejected }
                edits.clear()
                edits.putAll(keep)
                state.refresh()
                when {
                    res.rejected.isNotEmpty() -> toast(
                        "rejected: " + res.rejected.entries.joinToString("; ") { "${it.key} (${it.value})" },
                    )
                    res.restart_required.isNotEmpty() -> toast(
                        "saved — restart the core service to apply: ${res.restart_required.joinToString(", ")}",
                    )
                    else -> toast("saved")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                saveError = e.message ?: "error"
                toast("save failed: ${e.message ?: "error"}")
            } finally {
                saving = false
            }
        }
    }

    PanelCard(
        "Domovoi configuration",
        "Live-editable settings. Some changes apply instantly; those marked \"restart\" need a core-service restart.",
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (state.loading && fields.isEmpty()) {
                Text(
                    "loading settings…",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            } else {
                ConfigGroups(
                    list = common,
                    valueOf = { f -> if (f.name in edits) edits[f.name] else f.value },
                    onChange = { name, v -> edits[name] = v },
                )
                if (advanced.isNotEmpty()) {
                    HorizontalDivider(color = Domovoi.colors.border, modifier = Modifier.padding(vertical = 6.dp))
                    TextButton(onClick = { advOpen = !advOpen }) {
                        Icon(
                            if (advOpen) Icons.Filled.ExpandMore else Icons.Filled.ChevronRight,
                            contentDescription = null,
                            tint = Domovoi.colors.fg,
                        )
                        Spacer(Modifier.width(4.dp))
                        Text(
                            "Advanced",
                            style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.SemiBold),
                            color = Domovoi.colors.fg,
                        )
                    }
                    if (advOpen) {
                        Text(
                            "These can break the core service. Wrong values — database URL, ports, " +
                                "paths, the STT device — can stop it from starting or reaching its " +
                                "services, and may need you to edit domovoi/.env by hand to " +
                                "recover. Change only if you know what you're doing.",
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fg,
                            modifier = Modifier
                                .fillMaxWidth()
                                .border(1.dp, Domovoi.colors.warn, RoundedCornerShape(8.dp))
                                .background(Domovoi.colors.warnSoft, RoundedCornerShape(8.dp))
                                .padding(10.dp),
                        )
                        Spacer(Modifier.width(0.dp))
                        ConfigGroups(
                            list = advanced,
                            valueOf = { f -> if (f.name in edits) edits[f.name] else f.value },
                            onChange = { name, v -> edits[name] = v },
                        )
                    }
                }
            }

            HorizontalDivider(color = Domovoi.colors.borderSoft, modifier = Modifier.padding(vertical = 8.dp))

            saveError?.let {
                Text("save failed: $it", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err)
            }
            result?.let { res ->
                if (res.rejected.isNotEmpty()) {
                    Text(
                        "rejected: " + res.rejected.entries.joinToString("; ") { "${it.key} (${it.value})" },
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.err,
                    )
                }
                if (res.restart_required.isNotEmpty()) {
                    Text(
                        "saved — restart the core service to apply: ${res.restart_required.joinToString(", ")}",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.warn,
                    )
                }
                if (res.rejected.isEmpty() && res.restart_required.isEmpty() && res.applied.isNotEmpty()) {
                    Text("saved", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.ok)
                }
            }
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    if (dirtyCount > 0) "$dirtyCount unsaved" else "no changes",
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgFaint,
                )
                Button(enabled = !saving && dirtyCount > 0, onClick = { save() }) {
                    Icon(Icons.Filled.Check, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text(if (saving) "Saving…" else "Save")
                }
            }
        }
    }
}

@Composable
private fun ConfigGroups(
    list: List<ConfigField>,
    valueOf: (ConfigField) -> JsonElement?,
    onChange: (String, JsonElement) -> Unit,
) {
    list.groupBy { it.group }.forEach { (group, groupFields) ->
        Column(Modifier.fillMaxWidth().padding(bottom = 10.dp)) {
            SectionLabel(group.ifBlank { "general" }, Modifier.padding(bottom = 2.dp))
            groupFields.forEach { f ->
                HorizontalDivider(color = Domovoi.colors.borderSoft)
                ConfigFieldRow(f, valueOf(f)) { onChange(f.name, it) }
            }
        }
    }
}

@Composable
private fun ConfigFieldRow(
    f: ConfigField,
    value: JsonElement?,
    onChange: (JsonElement) -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                f.label.ifBlank { f.name },
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Medium),
                color = Domovoi.colors.fg,
            )
            if (f.tier == "restart") Pill("restart", Tone.Warn)
            if (f.type == "bool") {
                Switch(
                    checked = value.boolValue(),
                    onCheckedChange = { onChange(JsonPrimitive(it)) },
                )
            }
        }
        if (!f.help.isNullOrBlank()) {
            Text(
                f.help,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        when (f.type) {
            "bool" -> {} // the Switch lives on the label row
            "choice" -> SettingsDropdown(
                selected = value.textValue(),
                options = f.choices,
                label = { it },
                onSelect = { onChange(JsonPrimitive(it)) },
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            )
            "int", "float" -> ConfigNumberField(f, value, onChange)
            else -> {
                var text by remember(f.name, f.value) { mutableStateOf(value.textValue()) }
                OutlinedTextField(
                    value = text,
                    onValueChange = {
                        text = it
                        onChange(JsonPrimitive(it))
                    },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun ConfigNumberField(
    f: ConfigField,
    value: JsonElement?,
    onChange: (JsonElement) -> Unit,
) {
    var text by remember(f.name, f.value) { mutableStateOf(value.textValue()) }
    val unit = f.unit
    OutlinedTextField(
        value = text,
        onValueChange = { t ->
            text = t
            val parsed: JsonElement = when {
                t.isBlank() -> JsonPrimitive("")
                f.type == "int" -> t.toLongOrNull()?.let { JsonPrimitive(it) } ?: JsonPrimitive(t)
                else -> t.toDoubleOrNull()?.let { JsonPrimitive(it) } ?: JsonPrimitive(t)
            }
            onChange(parsed)
        },
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyMedium,
        keyboardOptions = KeyboardOptions(
            keyboardType = if (f.type == "int") KeyboardType.Number else KeyboardType.Decimal,
        ),
        trailingIcon = if (unit != null) {
            {
                Text(
                    unit,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgFaint,
                )
            }
        } else {
            null
        },
        modifier = Modifier.width(180.dp).padding(top = 6.dp),
    )
}
