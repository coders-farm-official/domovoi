package com.domovoi.app.ui.screens.stations

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Detail pane for a selected favorite — web StationDetail analog. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StationDetailCard(s: Station, refresh: () -> Unit, modifier: Modifier = Modifier) {
    DomovoiCard(modifier, padding = 0) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Icon(
                    if (s.source == "fm") Icons.Filled.CellTower else Icons.Filled.Radio,
                    contentDescription = null,
                    tint = Domovoi.colors.fgMuted,
                    modifier = Modifier.size(18.dp),
                )
                Text(
                    s.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (s.last_sampled_at != null) {
                    Text(
                        "last sampled ${relTime(s.last_sampled_at)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
            Text(
                "station · #${s.id} · ${s.source ?: "online"}" +
                    (s.frequency_mhz?.let { " · ${fmtFreq(it)} mhz" } ?: ""),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        // Now-playing callout — freshest signal right under the name.
        val np = s.now_playing
        if (np != null) {
            Row(
                Modifier.fillMaxWidth().background(Domovoi.colors.brandSoft)
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Filled.MusicNote, contentDescription = null,
                    tint = Domovoi.colors.brand, modifier = Modifier.size(14.dp),
                )
                Text(
                    np,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (s.now_playing_updated_at != null) {
                    Text(
                        relTime(s.now_playing_updated_at),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)
        }

        Column(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            FieldRow("stream") { StreamUrlEditor(s, refresh) }
            FieldRow("country") {
                Text(
                    s.country_code ?: "—",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                )
            }
            FieldRow("language") {
                Text(
                    s.language ?: "—",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                )
            }
            FieldRow("tags") {
                if (s.tags.isEmpty()) {
                    Text("—", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgFaint)
                } else {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        s.tags.forEach { t -> DetailTagChip(t) }
                    }
                }
            }
            FieldRow("interval") {
                Text(
                    "${s.sample_interval_sec ?: "—"}s",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                )
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        Row(
            Modifier.fillMaxWidth().background(Domovoi.colors.sunken)
                .padding(horizontal = 16.dp, vertical = 10.dp),
        ) {
            SectionLabel("recent detections")
        }
        DetectionFeed(s.id)
    }
}

@Composable
private fun FieldRow(label: String, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        SectionLabel(label, Modifier.width(80.dp).padding(top = 2.dp))
        Box(Modifier.weight(1f)) { content() }
    }
}

@Composable
private fun DetailTagChip(t: String) {
    Box(
        Modifier
            .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(t, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
    }
}

/* ---- Stream URL editor ----------------------------------------------------- */
/*
 * Three states (web StreamUrlEditor analog):
 *   view    — stream_url set; pencil enters edit mode.
 *   edit    — input validating http(s):// + save/cancel → PATCH.
 *   resolve — empty fm row: "resolve" (radio-browser simulcast lookup)
 *             plus "paste URL" fallback; other sources just get "paste URL".
 */
@Composable
private fun StreamUrlEditor(s: Station, refresh: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var editing by remember(s.id, s.stream_url) { mutableStateOf(false) }
    var draft by remember(s.id, s.stream_url) { mutableStateOf(s.stream_url ?: "") }
    var resolving by remember(s.id) { mutableStateOf(false) }

    fun save() {
        val v = draft.trim()
        if (v.isNotEmpty() &&
            !(v.startsWith("http://", ignoreCase = true) || v.startsWith("https://", ignoreCase = true))
        ) {
            toast("stream URL must start with http(s)://")
            return
        }
        scope.launch {
            runCatching {
                app.api.patch(
                    "/api/plugins/radio/stations/${s.id}",
                    buildJsonObject { put("stream_url", v.ifBlank { null }) },
                )
            }.onSuccess {
                toast(if (v.isNotEmpty()) "stream URL saved" else "stream URL cleared")
                editing = false
                refresh()
            }.onFailure { toast("save failed: ${it.message}") }
        }
    }

    fun resolve() {
        if (resolving) return
        resolving = true
        scope.launch {
            runCatching {
                app.api.post("/api/plugins/radio/stations/${s.id}/resolve-simulcast").decode<SimulcastResult>()
            }.onSuccess { res ->
                if (res.resolved) {
                    toast("found simulcast for ${s.call_sign ?: s.name}")
                    refresh()
                } else {
                    toast(res.message ?: "no simulcast found")
                }
            }.onFailure { toast("resolve failed: ${it.message}") }
            resolving = false
        }
    }

    val url = s.stream_url
    when {
        editing -> Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                placeholder = { Text("http(s)://…", color = Domovoi.colors.fgSubtle) },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            Button(onClick = { save() }) { Text("save") }
            OutlinedButton(onClick = {
                draft = s.stream_url ?: ""
                editing = false
            }) { Text("cancel") }
        }
        !url.isNullOrBlank() -> Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                url,
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { editing = true }, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.Edit, contentDescription = "edit stream URL",
                    tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(14.dp),
                )
            }
        }
        else -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                "no stream URL — polling can't reach this station",
                style = MaterialTheme.typography.labelSmall,
                fontStyle = FontStyle.Italic,
                color = Domovoi.colors.fgFaint,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (s.source == "fm") {
                    OutlinedButton(onClick = { resolve() }, enabled = !resolving) {
                        Text(if (resolving) "resolving…" else "resolve")
                    }
                }
                OutlinedButton(onClick = { editing = true }) { Text("paste url") }
            }
        }
    }
}

/* ---- Detection feed ---------------------------------------------------------- */

@Composable
private fun DetectionFeed(stationId: Long) {
    val state = rememberApi(stationId, eventTypes = setOf("radio.detections.changed")) {
        it.api.get("/api/plugins/radio/detections?station_id=$stationId&limit=100")
            .decode<List<RadioDetection>>()
    }
    val detections = state.data
    when {
        state.loading && detections == null -> LoadingState()
        detections == null -> ErrorState(state.error ?: "request failed", state.refresh)
        detections.isEmpty() -> EmptyState(
            "no detections yet",
            "sampler runs every few minutes — give it a song or two",
        )
        else -> Column(Modifier.fillMaxWidth()) {
            detections.forEach { d ->
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            d.title ?: "—",
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.Medium,
                            color = Domovoi.colors.fg,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            "${d.artist ?: "unknown artist"} · ${relTime(d.detected_at)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    // "local" = local fingerprint hit (highest trust); other
                    // values ("icy", "shazam", …) render as-is — the source
                    // vocabulary is the plugin's, not ours.
                    Pill(
                        when (d.fingerprint_source) {
                            "dejavu_local", "local" -> "local"
                            null -> "—"
                            else -> d.fingerprint_source
                        },
                        if (d.fingerprint_source == "dejavu_local" || d.fingerprint_source == "local") {
                            Tone.Brand
                        } else {
                            Tone.Idle
                        },
                    )
                    when {
                        d.in_library || d.library_track_id != null -> Pill("in library", Tone.Ok)
                        else -> Pill("new", Tone.Idle)
                    }
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }
        }
    }
}
