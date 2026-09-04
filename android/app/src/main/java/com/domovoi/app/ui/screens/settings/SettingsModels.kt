package com.domovoi.app.ui.screens.settings

import android.content.Context
import android.media.MediaPlayer
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import java.io.File

// ---------------------------------------------------------------------------
// Serializable models — tolerant of missing fields (the backend evolves).
// Property names deliberately match the JSON keys (snake_case).
// ---------------------------------------------------------------------------

@Serializable
internal data class Greeting(
    val id: Long = 0,
    val text: String = "",
    val category: String = "generic",
    val enabled: Boolean = true,
)

@Serializable
internal data class Voice(
    val id: Long = 0,
    val name: String = "",
    val engine: String = "",       // "piper" | "edge"
    val model_ref: String? = null,
    val is_default: Boolean = false,
)

@Serializable
internal data class WakeWord(
    val id: Long = 0,
    val name: String = "",
    val slug: String = "",
    val phrase: String = "",
    val threshold: Double = 0.5,
    val model_ref: String? = null,
    val is_default: Boolean = false,
    val status: String = "recording",  // recording | training | ready | failed
    val source_room_id: String? = null,
    val clip_count: Int = 0,
    val error: String? = null,
)

@Serializable
internal data class ClipMetrics(
    val peak_dbfs: Double? = null,
    val rms_dbfs: Double? = null,
    val noise_dbfs: Double? = null,
    val snr_db: Double? = null,
    val clipping_pct: Double? = null,
    val speech_ratio: Double? = null,
    val voiced_ms: Long? = null,
    val leading_silence_ms: Long? = null,
    val trailing_silence_ms: Long? = null,
)

@Serializable
internal data class WakeClip(
    val name: String = "",
    val verdict: String = "",      // good | fair | poor
    val issues: List<String> = emptyList(),
    val selected: Boolean = true,
    val raw_duration_ms: Long? = null,
    val trimmed_duration_ms: Long? = null,
    val has_trimmed: Boolean = false,
    val metrics: ClipMetrics? = null,
    val envelope: List<Double> = emptyList(),
    val score: Double? = null,
)

@Serializable
internal data class WakeClipList(
    val slug: String = "",
    val count: Int = 0,
    val selected_count: Int = 0,
    val min_clips: Int = 15,
    val clips: List<WakeClip> = emptyList(),
)

@Serializable
internal data class WakeScoreSummary(
    val raw_recall: Double? = null,
    val silence_score: Double? = null,
)

@Serializable
internal data class WakeScoreResult(val summary: WakeScoreSummary? = null)

@Serializable
internal data class SettingsSatellite(
    val room_id: String = "",
    val status: String = "",
)

@Serializable
internal data class ConfigSummary(
    val bot_name: String? = null,
    val web_version: String? = null,
    val wake_word_min_clips: Int? = null,
)

@Serializable
internal data class VersionInfo(
    /** The RUNNING code, captured at the core's boot — not the working tree. */
    val sha: String? = null,
    val running_sha: String? = null,
    /** What's on disk right now; diverges from [sha] after a pull. */
    val checkout_sha: String? = null,
    val restart_required: Boolean = false,
    /** Whether the host has the sudoers grant to bounce itself. */
    val restart_capable: Boolean = false,
    val restart_hint: String? = null,
    val uptime_sec: Double? = null,
)

@Serializable
internal data class VersionRestart(
    val ok: Boolean = false,
    val units: List<String> = emptyList(),
    val error: String? = null,
)

@Serializable
internal data class VersionCheck(
    val behind: Int? = null,
    val ahead: Int? = null,
    val upstream: String? = null,
    val error: String? = null,
)

@Serializable
internal data class VersionPull(
    val pulled: Boolean = false,
    val new_sha: String? = null,
    val error: String? = null,
)

/** One editable domovoi config field. `value` is freeform (bool /
 * number / string) — keep it as JsonElement and render by `type`. */
@Serializable
internal data class ConfigField(
    val name: String = "",
    val label: String = "",
    val value: JsonElement? = null,
    val type: String = "text",     // bool | choice | int | float | text
    val group: String = "",
    val section: String? = null,   // "advanced" hides behind the warning
    val help: String? = null,
    val unit: String? = null,
    val min: Double? = null,
    val max: Double? = null,
    val choices: List<String> = emptyList(),
    val tier: String? = null,      // "restart" needs a core-service restart
)

@Serializable
internal data class ConfigEditable(val fields: List<ConfigField> = emptyList())

/** PATCH /api/config/editable and POST /api/models/active both return this. */
@Serializable
internal data class ConfigSaveResult(
    val applied: List<String> = emptyList(),
    val restart_required: List<String> = emptyList(),
    val rejected: Map<String, String> = emptyMap(),
)

@Serializable
internal data class HwGpu(
    val name: String? = null,
    val util_pct: Double? = null,
    val mem_used_mb: Double? = null,
    val mem_total_mb: Double? = null,
    val mem_free_mb: Double? = null,
    val temp_c: Double? = null,
)

@Serializable
internal data class HwCpu(
    val percent: Double? = null,
    val logical_cores: Int? = null,
    val physical_cores: Int? = null,
)

@Serializable
internal data class HwRam(
    val used_mb: Double? = null,
    val total_mb: Double? = null,
    val percent: Double? = null,
)

@Serializable
internal data class HwDisk(
    val path: String? = null,
    val free_mb: Double? = null,
    val total_mb: Double? = null,
    val percent: Double? = null,
)

@Serializable
internal data class Hardware(
    val gpus: List<HwGpu> = emptyList(),
    val cpu: HwCpu? = null,
    val ram: HwRam? = null,
    val disk: HwDisk? = null,
)

@Serializable
internal data class ActiveRole(
    val role: String = "",         // qa | tool | stt
    val field: String? = null,
    val model: String? = null,
    val tier: String? = null,
)

@Serializable
internal data class ActiveRoles(val roles: List<ActiveRole> = emptyList())

@Serializable
internal data class InstalledModel(
    val name: String = "",
    val size_bytes: Long? = null,
    val modified_at: String? = null,
    val quant: String? = null,
    val family: String? = null,
    val param_size: String? = null,
    val loaded: Boolean = false,
)

@Serializable
internal data class InstalledModels(
    val ollama_reachable: Boolean = true,
    val installed: List<InstalledModel> = emptyList(),
)

@Serializable
internal data class ModelJob(
    val id: Long = 0,
    val model: String = "",
    val status: String = "",       // pending | running | done | failed | cancelled
    val pct: Int? = null,
    val status_text: String? = null,
    val error: String? = null,
)

@Serializable
internal data class ModelJobs(val jobs: List<ModelJob> = emptyList())

@Serializable
internal data class CatalogModel(
    val name: String = "",
    val role: String? = null,      // qa | tool | both | embedding
    val params: String? = null,
    val quant: String? = null,
    val est_vram_gb: Double? = null,
    val desc: String? = null,
)

@Serializable
internal data class WhisperCatalogModel(
    val name: String = "",
    val compute: String? = null,
    val accuracy: String? = null,
    val est_vram_gb: Double? = null,
)

@Serializable
internal data class ModelCatalog(
    val ollama: List<CatalogModel> = emptyList(),
    val whisper: List<WhisperCatalogModel> = emptyList(),
)

@Serializable
internal data class SettingsPerson(
    val id: Long = 0,
    val name: String = "",
)

// ---------------------------------------------------------------------------
// JSON value helpers for freeform config values
// ---------------------------------------------------------------------------

internal fun JsonElement?.boolValue(): Boolean =
    (this as? JsonPrimitive)?.booleanOrNull ?: false

internal fun JsonElement?.textValue(): String {
    val p = this as? JsonPrimitive ?: return ""
    if (p is JsonNull) return ""
    return p.content
}

/** "3", "0.5", "—" — trims trailing zeros off doubles. */
internal fun fmtNum(d: Double?): String = when {
    d == null -> "—"
    d % 1.0 == 0.0 && kotlin.math.abs(d) < 1e15 -> d.toLong().toString()
    else -> "%.2f".format(d).trimEnd('0').trimEnd('.')
}

// ---------------------------------------------------------------------------
// Mutation helper — the web `guard()` analog. Every mutation toasts.
// ---------------------------------------------------------------------------

internal fun CoroutineScope.settingsMutation(
    toast: (String) -> Unit,
    okMsg: String? = null,
    refresh: (() -> Unit)? = null,
    block: suspend () -> Unit,
): Job = launch {
    try {
        block()
        okMsg?.let(toast)
        refresh?.invoke()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        toast("failed: ${e.message ?: "request failed"}")
    }
}

// ---------------------------------------------------------------------------
// Shared UI bits
// ---------------------------------------------------------------------------

/** The web Card(title, sub) analog used by every settings panel. */
@Composable
internal fun PanelCard(
    title: String,
    sub: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    DomovoiCard(modifier = modifier.fillMaxWidth()) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
        if (sub != null) {
            Text(
                sub,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        Spacer(Modifier.height(10.dp))
        content()
    }
}

/** A read-only exposed dropdown — the web <select> analog. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun <T> SettingsDropdown(
    selected: T,
    options: List<T>,
    label: (T) -> String,
    onSelect: (T) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded && enabled,
        onExpandedChange = { if (enabled) expanded = it },
        modifier = modifier,
    ) {
        OutlinedTextField(
            value = label(selected),
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyMedium,
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded && enabled) },
            modifier = Modifier
                .menuAnchor(MenuAnchorType.PrimaryNotEditable, enabled)
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded && enabled, onDismissRequest = { expanded = false }) {
            options.forEach { opt ->
                DropdownMenuItem(
                    text = { Text(label(opt)) },
                    onClick = {
                        expanded = false
                        onSelect(opt)
                    },
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Temp-file MediaPlayer — plays audio bytes fetched via api.bytes()
// (voice samples, wake-word clips). One sound at a time.
// ---------------------------------------------------------------------------

internal class TempAudioPlayer(private val context: Context) {
    private var player: MediaPlayer? = null
    private var tempFile: File? = null

    fun stop() {
        val p = player
        player = null
        runCatching { p?.stop() }
        runCatching { p?.release() }
        tempFile?.let { f -> runCatching { f.delete() } }
        tempFile = null
    }

    /** Writes [bytes] to a cache temp file and plays it. [onDone] fires on
     *  completion or error (on the main looper). Stops any prior playback. */
    suspend fun play(bytes: ByteArray, suffix: String = ".wav", onDone: () -> Unit) {
        stop()
        val file = withContext(Dispatchers.IO) {
            File.createTempFile("domovoi-audio", suffix, context.cacheDir).apply { writeBytes(bytes) }
        }
        tempFile = file
        val mp = MediaPlayer()
        player = mp
        var finished = false
        val finish: (MediaPlayer) -> Unit = { m ->
            if (!finished) {
                finished = true
                runCatching { m.release() }
                if (player === m) {
                    player = null
                    tempFile = null
                }
                runCatching { file.delete() }
                onDone()
            }
        }
        runCatching {
            mp.setDataSource(file.absolutePath)
            mp.setOnCompletionListener { finish(it) }
            mp.setOnErrorListener { m, _, _ ->
                finish(m)
                true
            }
            mp.prepare()
            mp.start()
        }.onFailure { finish(mp) }
    }
}

// ---------------------------------------------------------------------------
// Fit math (models.jsx _fitBadge) — conservative, labeled estimates.
// ---------------------------------------------------------------------------

internal fun fitBadge(estGb: Double?, freeGb: Double?, ramFreeGb: Double?): Pair<String, Tone> = when {
    estGb == null -> "size unknown" to Tone.Idle
    freeGb == null -> "~${fmtNum(estGb)} GB" to Tone.Idle
    estGb <= freeGb * 0.85 -> "fits" to Tone.Ok
    estGb <= freeGb -> "tight" to Tone.Warn
    ramFreeGb != null && estGb <= freeGb + ramFreeGb -> "spills to CPU" to Tone.Warn
    else -> "won't fit" to Tone.Err
}
