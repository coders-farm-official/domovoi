package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder

/**
 * Models tab (models.jsx ModelsPanel): live hardware readout, active role
 * slots (Q&A / tool / STT), install jobs with progress, installed Ollama
 * models, the curated catalog with fit badges, and the Whisper size picker.
 */

private val ROLE_LABEL = mapOf("qa" to "Q&A", "tool" to "Tool routing", "stt" to "Speech-to-text")
private val ROLE_SUB = mapOf(
    "qa" to "conversational fallthrough — 'tell me a joke'",
    "tool" to "routes voice commands to handlers",
    "stt" to "Whisper transcription",
)
private val ROLE_TAG = mapOf(
    "qa" to "Q&A", "tool" to "tool", "both" to "Q&A · tool",
    "embedding" to "embedding", "stt" to "STT",
)

private fun bytesToGb(bytes: Long?): Double? = bytes?.let { it / (1024.0 * 1024.0 * 1024.0) }
private fun fmtGb(gb: Double?): String = gb?.let { "%.1f GB".format(it) } ?: "—"
private fun round1(d: Double): Double = Math.round(d * 10.0) / 10.0

@Composable
internal fun ModelsPanel(onManage: (SettingsTab) -> Unit = {}) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val hwState = rememberApi(fetch = { it.api.get("/api/models/hardware").decode<Hardware>() })
    val catalogState = rememberApi(fetch = { it.api.get("/api/models/catalog").decode<ModelCatalog>() })
    val installedState = rememberApi(
        eventTypes = setOf("model_jobs.changed"),
        fetch = { it.api.get("/api/models/installed").decode<InstalledModels>() },
    )
    val activeState = rememberApi(fetch = { it.api.get("/api/models/active").decode<ActiveRoles>() })
    val jobsState = rememberApi(
        eventTypes = setOf("model_jobs.changed"),
        fetch = { it.api.get("/api/models/jobs").decode<ModelJobs>().jobs },
    )

    // Re-poll hardware on a light cadence while the tab is visible so
    // VRAM/util stay live-ish (the web's 5 s interval).
    LaunchedEffect(Unit) {
        while (true) {
            delay(5000)
            hwState.refresh()
        }
    }

    val hw = hwState.data
    // Fit denominator: largest single GPU's free VRAM + the CPU-offload
    // ceiling (free system RAM). Conservative, labeled estimates.
    val freeGb = hw?.gpus?.takeIf { it.isNotEmpty() }
        ?.maxOfOrNull { (it.mem_free_mb ?: 0.0) / 1024.0 }
    val ramFreeGb = hw?.ram?.let { ((it.total_mb ?: 0.0) - (it.used_mb ?: 0.0)) / 1024.0 }

    val installed = installedState.data?.installed ?: emptyList()
    val ollamaReachable = installedState.data?.ollama_reachable ?: true
    val installedNames = installed.map { it.name }.toSet()
    val roles = activeState.data?.roles ?: emptyList()
    val catOllama = catalogState.data?.ollama ?: emptyList()
    val catWhisper = catalogState.data?.whisper ?: emptyList()
    val catalogByName = catOllama.associateBy { it.name }
    val sttActive = roles.firstOrNull { it.role == "stt" }?.model
    val whisperNames = catWhisper.map { it.name }.distinct()
    val jobs = jobsState.data ?: emptyList()
    val pulling = jobs
        .filter { it.status == "pending" || it.status == "running" }
        .map { it.model }
        .toSet()

    var deleting by remember { mutableStateOf<InstalledModel?>(null) }
    var pullName by remember { mutableStateOf("") }

    fun switchModel(role: String, model: String) {
        scope.launch {
            try {
                val res = app.api.post(
                    "/api/models/active",
                    buildJsonObject {
                        put("role", role)
                        put("model", model)
                    },
                ).decode<ConfigSaveResult>()
                toast(
                    if (res.restart_required.isNotEmpty()) {
                        "saved — restart the core service to apply $model"
                    } else {
                        "switched to $model"
                    },
                )
                activeState.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("failed: ${e.message ?: "request failed"}")
            }
        }
    }

    fun install(name: String) {
        if (name.isBlank()) return
        scope.settingsMutation(toast, "pulling $name…", jobsState.refresh) {
            app.api.post("/api/models/pull", buildJsonObject { put("model", name) })
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { HardwareCard(hw, hwState.loading) }

        item {
            PanelCard(
                "Active models",
                "One model per role. Switching writes config — Ollama applies instantly; Whisper needs a restart.",
            ) {
                if (roles.isEmpty()) {
                    Text(
                        if (activeState.error != null) {
                            "core service unreachable — active models unavailable"
                        } else {
                            "loading…"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                } else {
                    roles.forEachIndexed { i, r ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        ActiveRoleRow(
                            r = r,
                            installed = installed,
                            whisperNames = whisperNames,
                            catalogByName = catalogByName,
                            freeGb = freeGb,
                            ramFreeGb = ramFreeGb,
                            onSwitch = { model -> switchModel(r.role, model) },
                        )
                    }
                }
            }
        }

        if (jobs.isNotEmpty()) {
            item {
                PanelCard(
                    "Installs in progress",
                    "Ollama pulls run in the background — you can leave this page.",
                ) {
                    jobs.forEachIndexed { i, j ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        PullJobRow(j) {
                            scope.settingsMutation(toast, "pull cancelled", jobsState.refresh) {
                                app.api.post("/api/models/pull/${j.id}/cancel")
                            }
                        }
                    }
                }
            }
        }

        item {
            PanelCard(
                "Installed (${installed.size})",
                "On-disk Ollama models. 'loaded' means it's resident in VRAM right now.",
            ) {
                when {
                    !ollamaReachable -> EmptyState(
                        "Ollama offline",
                        "The local Ollama server isn't reachable — start it to list installed models.",
                    )
                    installed.isEmpty() -> EmptyState(
                        "No models installed",
                        "Install one from the catalog below.",
                    )
                    else -> installed.forEachIndexed { i, m ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        InstalledRow(m, freeGb, ramFreeGb) { deleting = m }
                    }
                }
            }
        }

        item {
            PanelCard(
                "Browse & install",
                "Curated Ollama models with fit estimates. Or pull anything by name.",
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedTextField(
                        value = pullName,
                        onValueChange = { pullName = it },
                        placeholder = {
                            Text("Pull by name (e.g. qwen2.5:7b)", color = Domovoi.colors.fgSubtle)
                        },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    Button(
                        enabled = pullName.isNotBlank(),
                        onClick = {
                            install(pullName.trim())
                            pullName = ""
                        },
                    ) {
                        Icon(Icons.Filled.Download, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("Install")
                    }
                }
            }
        }

        items(catOllama, key = { it.name }) { m ->
            CatalogCard(
                m = m,
                freeGb = freeGb,
                ramFreeGb = ramFreeGb,
                isInstalled = m.name in installedNames,
                isPulling = m.name in pulling,
                onInstall = { install(m.name) },
            )
        }

        item {
            PanelCard(
                "Speech-to-text (Whisper)",
                "Selecting a size writes whisper_model — a restart-tier change. int8 halves VRAM at near-identical accuracy.",
            ) {
                catWhisper.forEachIndexed { i, m ->
                    if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                    SttRow(
                        m = m,
                        freeGb = freeGb,
                        ramFreeGb = ramFreeGb,
                        active = sttActive,
                        onSelect = { switchModel("stt", m.name) },
                    )
                }
            }
        }

        item {
            PanelCard(
                "Voices & wake words",
                "Managed in their own tabs — surfaced here so everything model-shaped is in one place.",
            ) {
                FoldedRow(
                    icon = { Icon(Icons.Filled.RecordVoiceOver, null, tint = Domovoi.colors.fgMuted) },
                    title = "TTS voices",
                    sub = "Edge (cloud) + Piper (local) voice registry — each satellite speaks in one.",
                ) { onManage(SettingsTab.Voices) }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
                FoldedRow(
                    icon = { Icon(Icons.Filled.Mic, null, tint = Domovoi.colors.fgMuted) },
                    title = "Wake words",
                    sub = "Custom openWakeWord models — record clips on a satellite, train, push.",
                ) { onManage(SettingsTab.WakeWords) }
            }
        }
    }

    deleting?.let { m ->
        ConfirmDialog(
            title = "Delete ${m.name}?",
            body = "Removes it from disk. It can be re-pulled later.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = {
                scope.settingsMutation(toast, "deleted ${m.name}", installedState.refresh) {
                    app.api.delete("/api/models/" + URLEncoder.encode(m.name, "UTF-8"))
                }
            },
            onDismiss = { deleting = null },
        )
    }
}

// ---------------------------------------------------------------------------
// Hardware
// ---------------------------------------------------------------------------

@Composable
private fun MeterBar(fraction: Float, tone: Tone, modifier: Modifier = Modifier) {
    val color = toneColor(tone)
    Box(
        modifier
            .fillMaxWidth()
            .height(6.dp)
            .clip(RoundedCornerShape(4.dp))
            .background(Domovoi.colors.borderSoft),
    ) {
        Box(
            Modifier
                .fillMaxHeight()
                .fillMaxWidth(fraction.coerceIn(0f, 1f))
                .background(color, RoundedCornerShape(4.dp)),
        )
    }
}

@Composable
private fun MeterRow(label: String, valueText: String, pct: Double?, tone: Tone) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                label,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
            )
            Text(
                valueText,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgMuted,
            )
        }
        MeterBar(((pct ?: 0.0) / 100.0).toFloat(), tone)
    }
}

@Composable
private fun HardwareCard(hw: Hardware?, loading: Boolean) {
    PanelCard("Hardware", "Live host readout — the denominator for every fit estimate below.") {
        when {
            hw == null && loading -> Text(
                "reading hardware…",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            hw == null -> Text(
                "hardware readout unavailable",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
            else -> Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (hw.gpus.isEmpty()) {
                    Text(
                        "No GPU detected (nvidia-smi unavailable or CPU-only host) — fit badges will show as unknown.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgSubtle,
                    )
                } else {
                    hw.gpus.forEach { g -> GpuCard(g) }
                }
                hw.cpu?.let { cpu ->
                    MeterRow(
                        label = "CPU" + (cpu.logical_cores?.let { " · $it threads" } ?: ""),
                        valueText = "${fmtNum(cpu.percent)}%",
                        pct = cpu.percent,
                        tone = if ((cpu.percent ?: 0.0) > 85) Tone.Warn else Tone.Brand,
                    )
                }
                hw.ram?.let { ram ->
                    MeterRow(
                        label = "RAM",
                        valueText = "%.1f / %.0f GB".format(
                            (ram.used_mb ?: 0.0) / 1024.0,
                            (ram.total_mb ?: 0.0) / 1024.0,
                        ),
                        pct = ram.percent,
                        tone = when {
                            (ram.percent ?: 0.0) > 90 -> Tone.Err
                            (ram.percent ?: 0.0) > 75 -> Tone.Warn
                            else -> Tone.Brand
                        },
                    )
                }
                hw.disk?.let { disk ->
                    MeterRow(
                        label = "Model disk free",
                        valueText = "%.0f GB free".format((disk.free_mb ?: 0.0) / 1024.0),
                        pct = disk.percent,
                        tone = when {
                            (disk.percent ?: 0.0) > 90 -> Tone.Err
                            (disk.percent ?: 0.0) > 75 -> Tone.Warn
                            else -> Tone.Brand
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun GpuCard(g: HwGpu) {
    val usedGb = (g.mem_used_mb ?: 0.0) / 1024.0
    val totalGb = (g.mem_total_mb ?: 0.0) / 1024.0
    val memPct = if (totalGb > 0) usedGb / totalGb else 0.0
    val hot = (g.temp_c ?: 0.0) >= 80.0
    val busy = (g.util_pct ?: 0.0) >= 5.0

    Column(
        Modifier
            .fillMaxWidth()
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Filled.Memory, null,
                modifier = Modifier.size(16.dp),
                tint = Domovoi.colors.fgMuted,
            )
            Text(
                g.name ?: "GPU",
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Pill("${fmtNum(g.util_pct)}%", if (busy) Tone.Brand else Tone.Idle, live = busy)
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                "VRAM",
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
            )
            Text(
                "%.1f / %.1f GB".format(usedGb, totalGb),
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgMuted,
            )
        }
        MeterBar(
            memPct.toFloat(),
            when {
                memPct > 0.9 -> Tone.Err
                memPct > 0.7 -> Tone.Warn
                else -> Tone.Brand
            },
        )
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                "free %.1f GB".format(totalGb - usedGb),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.ok,
            )
            Text(
                "${fmtNum(g.temp_c)}°C",
                style = MaterialTheme.typography.labelSmall,
                color = if (hot) Domovoi.colors.err else Domovoi.colors.fgMuted,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Active role slots
// ---------------------------------------------------------------------------

@Composable
private fun FitBadgePill(estGb: Double?, freeGb: Double?, ramFreeGb: Double?) {
    val (label, tone) = fitBadge(estGb, freeGb, ramFreeGb)
    Pill(label, tone)
}

@Composable
private fun ActiveRoleRow(
    r: ActiveRole,
    installed: List<InstalledModel>,
    whisperNames: List<String>,
    catalogByName: Map<String, CatalogModel>,
    freeGb: Double?,
    ramFreeGb: Double?,
    onSwitch: (String) -> Unit,
) {
    var pick by remember(r.role, r.model) { mutableStateOf(r.model ?: "") }
    // Always include the current model even if it isn't in the option source.
    val options = (listOf(r.model) + if (r.role == "stt") whisperNames else installed.map { it.name })
        .filterNotNull()
        .filter { it.isNotBlank() }
        .distinct()
    val est: Double? = catalogByName[r.model]?.est_vram_gb
        ?: if (r.role != "stt") {
            bytesToGb(installed.firstOrNull { it.name == r.model }?.size_bytes)
        } else {
            null
        }

    Column(
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            ROLE_LABEL[r.role] ?: r.role,
            style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
            color = Domovoi.colors.fg,
        )
        ROLE_SUB[r.role]?.let {
            Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        }
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                r.model ?: "—",
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium,
                ),
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            est?.let { FitBadgePill(round1(it), freeGb, ramFreeGb) }
            if (r.tier == "restart") Pill("restart to apply", Tone.Warn)
        }
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SettingsDropdown(
                selected = pick,
                options = options,
                label = { it },
                onSelect = { pick = it },
                modifier = Modifier.weight(1f),
            )
            Button(
                enabled = pick.isNotBlank() && pick != r.model,
                onClick = { onSwitch(pick) },
            ) { Text("Switch") }
        }
    }
}

// ---------------------------------------------------------------------------
// Pull jobs / installed / catalog / whisper
// ---------------------------------------------------------------------------

@Composable
private fun PullJobRow(j: ModelJob, onCancel: () -> Unit) {
    val active = j.status == "pending" || j.status == "running"
    val tone = when (j.status) {
        "done" -> Tone.Ok
        "failed" -> Tone.Err
        "cancelled" -> Tone.Idle
        else -> Tone.Brand
    }
    Column(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                j.model,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium,
                ),
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Pill(j.status, tone, live = active)
            j.pct?.let {
                Text(
                    "$it%",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgMuted,
                )
            }
            if (active) {
                IconButton(onClick = onCancel, modifier = Modifier.size(28.dp)) {
                    Icon(
                        Icons.Filled.Close, "cancel pull",
                        modifier = Modifier.size(16.dp),
                        tint = Domovoi.colors.fgMuted,
                    )
                }
            }
        }
        if (active) MeterBar((j.pct ?: 0) / 100f, Tone.Brand)
        val statusLine = j.error ?: j.status_text ?: if (active) "starting…" else ""
        if (statusLine.isNotBlank()) {
            Text(
                statusLine,
                style = MaterialTheme.typography.bodySmall,
                color = if (j.status == "failed") Domovoi.colors.err else Domovoi.colors.fgFaint,
            )
        }
    }
}

@Composable
private fun InstalledRow(
    m: InstalledModel,
    freeGb: Double?,
    ramFreeGb: Double?,
    onDelete: () -> Unit,
) {
    val gb = bytesToGb(m.size_bytes)
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    m.name,
                    style = MaterialTheme.typography.bodyMedium.copy(
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Medium,
                    ),
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (m.loaded) Pill("loaded", Tone.Brand, live = true)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                m.quant?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = Domovoi.colors.fgFaint,
                    )
                }
                Text(
                    fmtGb(gb),
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
        if (gb != null) FitBadgePill(round1(gb), freeGb, ramFreeGb)
        IconButton(onClick = onDelete) {
            Icon(Icons.Filled.Delete, "delete from disk", tint = Domovoi.colors.fgMuted)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CatalogCard(
    m: CatalogModel,
    freeGb: Double?,
    ramFreeGb: Double?,
    isInstalled: Boolean,
    isPulling: Boolean,
    onInstall: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .background(Domovoi.colors.card, RoundedCornerShape(10.dp))
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                m.name,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                ),
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Pill(ROLE_TAG[m.role] ?: (m.role ?: "?"), Tone.Idle)
        }
        m.desc?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            m.params?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgFaint,
                )
            }
            m.quant?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgFaint,
                )
            }
            FitBadgePill(m.est_vram_gb, freeGb, ramFreeGb)
        }
        if (isInstalled) {
            Pill("installed", Tone.Ok)
        } else {
            Button(enabled = !isPulling, onClick = onInstall) {
                Icon(Icons.Filled.Download, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text(if (isPulling) "installing…" else "Install")
            }
        }
    }
}

@Composable
private fun SttRow(
    m: WhisperCatalogModel,
    freeGb: Double?,
    ramFreeGb: Double?,
    active: String?,
    onSelect: () -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                m.name,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium,
                ),
                color = Domovoi.colors.fg,
            )
            m.compute?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgFaint,
                )
            }
            FitBadgePill(m.est_vram_gb, freeGb, ramFreeGb)
        }
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                m.accuracy ?: "",
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            if (active == m.name) {
                Pill("active", Tone.Brand)
            } else {
                OutlinedButton(onClick = onSelect) { Text("Use (restart)") }
            }
        }
    }
}

@Composable
private fun FoldedRow(
    icon: @Composable () -> Unit,
    title: String,
    sub: String,
    onManage: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        icon()
        Column(Modifier.weight(1f)) {
            Text(
                title,
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
                color = Domovoi.colors.fg,
            )
            Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        OutlinedButton(onClick = onManage) { Text("Manage") }
    }
}
