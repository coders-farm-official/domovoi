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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder
import kotlin.math.roundToInt

/**
 * Wake Words tab (settings.jsx WakeWordsPanel): create a wake word, record
 * positive clips on a satellite, curate the clip bank, train an openWakeWord
 * model, and push the ready model to a room. Live clip-count/status updates
 * ride the `wake_words.changed` bus channel.
 */

private const val WAKE_MIN_CLIPS = 15 // mirrors settings.wake_word_min_clips default

@Composable
internal fun WakeWordsPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val wwState = rememberApi(
        eventTypes = setOf("wake_words.changed"),
        fetch = { it.api.get("/api/wake-words").decode<List<WakeWord>>() },
    )
    // The satellite roster drives the Record / Push room pickers; only online
    // rooms can take a control frame.
    val satState = rememberApi(
        eventTypes = setOf("satellites.presence.changed"),
        fetch = { it.api.get("/api/satellites").decode<List<SettingsSatellite>>() },
    )
    // The train gate is server config; the server's /train still 409s
    // authoritatively below it.
    val cfgState = rememberApi(fetch = { it.api.get("/api/config").decode<ConfigSummary>() })

    val words = wwState.data ?: emptyList()
    val rooms = (satState.data ?: emptyList()).filter { it.status == "online" }.map { it.room_id }
    val minClips = cfgState.data?.wake_word_min_clips ?: WAKE_MIN_CLIPS

    var deleting by remember { mutableStateOf<WakeWord?>(null) }
    var newName by remember { mutableStateOf("") }
    var newPhrase by remember { mutableStateOf("") }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Create a wake word",
                "Name it, then record positive clips on a satellite and train an openWakeWord model.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = newName,
                        onValueChange = { newName = it },
                        placeholder = { Text("Name (e.g. Hey Domovoi)", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = newPhrase,
                        onValueChange = { newPhrase = it },
                        placeholder = { Text("Spoken phrase (e.g. hey domovoi)", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Button(
                        enabled = newName.isNotBlank() && newPhrase.isNotBlank(),
                        onClick = {
                            val n = newName.trim()
                            val p = newPhrase.trim()
                            scope.settingsMutation(toast, "wake word created", wwState.refresh) {
                                app.api.post(
                                    "/api/wake-words",
                                    buildJsonObject {
                                        put("name", n)
                                        put("phrase", p)
                                    },
                                )
                            }
                            newName = ""
                            newPhrase = ""
                        },
                    ) {
                        Icon(Icons.Filled.Add, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("Create")
                    }
                }
            }
        }

        item {
            Text(
                "Record clips on the same mic board the satellite uses at runtime — the XVF3800's " +
                    "on-chip beamforming/AGC reshapes the signal, so a model trained from HAT-recorded " +
                    "clips can detect poorly on an XVF array. Training runs on the server and is " +
                    "Linux-only / off by default (see scripts/wake_word/README.md).",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
        }

        val err = wwState.error
        when {
            err != null && wwState.data == null -> item { ErrorState(err, wwState.refresh) }
            wwState.loading && wwState.data == null -> item { LoadingState() }
            words.isEmpty() -> item {
                EmptyState("No wake words yet", "Create one above to start recording clips.")
            }
            else -> item {
                PanelCard(
                    "Wake words (${words.size})",
                    "Record clips, train, then push a ready model to a room.",
                ) {
                    words.forEachIndexed { i, w ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        WakeWordRow(
                            w = w,
                            rooms = rooms,
                            minClips = minClips,
                            onRecordStart = { room ->
                                if (room.isBlank()) {
                                    toast("no online satellite to record on")
                                } else {
                                    scope.settingsMutation(
                                        toast, "recording \"${w.phrase}\" on $room…", wwState.refresh,
                                    ) {
                                        app.api.post(
                                            "/api/wake-words/${w.id}/record/start",
                                            buildJsonObject { put("room_id", room) },
                                        )
                                    }
                                }
                            },
                            onRecordStop = { room ->
                                if (room.isBlank()) {
                                    toast("pick a room")
                                } else {
                                    scope.settingsMutation(toast, "recording stopped", wwState.refresh) {
                                        app.api.post(
                                            "/api/wake-words/${w.id}/record/stop",
                                            buildJsonObject { put("room_id", room) },
                                        )
                                    }
                                }
                            },
                            onTrain = {
                                scope.settingsMutation(toast, "training \"${w.name}\"…", wwState.refresh) {
                                    app.api.post("/api/wake-words/${w.id}/train")
                                }
                            },
                            onRename = { name ->
                                scope.settingsMutation(toast, "wake word renamed", wwState.refresh) {
                                    app.api.patch(
                                        "/api/wake-words/${w.id}",
                                        buildJsonObject { put("name", name) },
                                    )
                                }
                            },
                            onThreshold = { value ->
                                scope.settingsMutation(toast, "threshold updated", wwState.refresh) {
                                    app.api.patch(
                                        "/api/wake-words/${w.id}",
                                        buildJsonObject { put("threshold", value) },
                                    )
                                }
                            },
                            onSetDefault = {
                                scope.settingsMutation(
                                    toast, "${w.name} is now the default", wwState.refresh,
                                ) {
                                    app.api.patch(
                                        "/api/wake-words/${w.id}",
                                        buildJsonObject { put("set_default", true) },
                                    )
                                }
                            },
                            onPush = { room ->
                                if (room.isBlank()) {
                                    toast("no online satellite to push to")
                                } else {
                                    scope.settingsMutation(
                                        toast, "pushed \"${w.name}\" to $room", wwState.refresh,
                                    ) {
                                        app.api.post(
                                            "/api/wake-words/${w.id}/push",
                                            buildJsonObject { put("room_id", room) },
                                        )
                                    }
                                }
                            },
                            onDelete = { deleting = w },
                        )
                    }
                }
            }
        }
    }

    deleting?.let { w ->
        ConfirmDialog(
            title = "Delete ${w.name}?",
            body = "Removes the wake word and its recorded clips.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = {
                scope.settingsMutation(toast, "wake word removed", wwState.refresh) {
                    app.api.delete("/api/wake-words/${w.id}")
                }
            },
            onDismiss = { deleting = null },
        )
    }
}

// ---------------------------------------------------------------------------
// One wake word: status, clip count, threshold, record / train / push
// actions, and the expandable clip grid.
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WakeWordRow(
    w: WakeWord,
    rooms: List<String>,
    minClips: Int,
    onRecordStart: (String) -> Unit,
    onRecordStop: (String) -> Unit,
    onTrain: () -> Unit,
    onRename: (String) -> Unit,
    onThreshold: (Double) -> Unit,
    onSetDefault: () -> Unit,
    onPush: (String) -> Unit,
    onDelete: () -> Unit,
) {
    val focus = LocalFocusManager.current
    var editing by remember(w.id) { mutableStateOf(false) }
    var name by remember(w.id, w.name) { mutableStateOf(w.name) }
    var room by remember(w.id) { mutableStateOf("") }
    var thr by remember(w.id, w.threshold) { mutableStateOf(fmtNum(w.threshold)) }
    var showClips by remember(w.id) { mutableStateOf(false) }
    // Flips the moment Record is clicked so the banner shows before the first
    // clip lands; cleared on Stop or when the row leaves `recording`.
    var recActive by remember(w.id) { mutableStateOf(false) }
    LaunchedEffect(w.status) { if (w.status != "recording") recActive = false }

    val statusTone = when (w.status) {
        "training" -> Tone.Warn
        "ready" -> Tone.Ok
        "failed" -> Tone.Err
        else -> Tone.Idle
    }
    val roomFor = room.ifBlank { rooms.firstOrNull() ?: "" }
    val haveRooms = rooms.isNotEmpty()
    val canTrain = w.status == "recording" && w.clip_count >= minClips
    val canPush = w.status == "ready"
    val capturing = recActive && w.status == "recording"

    fun commitThreshold() {
        val v = thr.toDoubleOrNull()
        if (v == null || v == w.threshold) {
            thr = fmtNum(w.threshold)
        } else {
            onThreshold(v)
        }
        focus.clearFocus()
    }

    Column(
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // Top line — name, status, default, edit / delete
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (editing) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
            } else {
                Text(
                    w.name,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Pill(w.status, statusTone, live = if (w.status == "ready") true else capturing)
            if (w.is_default) Pill("default", Tone.Brand)
            if (editing) {
                Button(onClick = {
                    val t = name.trim()
                    if (t.isNotBlank() && t != w.name) onRename(t)
                    editing = false
                }) { Text("Save") }
            } else {
                IconButton(onClick = { editing = true }) {
                    Icon(Icons.Filled.Edit, "rename", tint = Domovoi.colors.fgMuted)
                }
                if (!w.is_default) {
                    IconButton(onClick = onDelete) {
                        Icon(Icons.Filled.Delete, "delete", tint = Domovoi.colors.fgMuted)
                    }
                }
            }
        }

        // Second line — phrase, clip count, threshold
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "\"${w.phrase}\"",
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "${w.clip_count} / $minClips clips",
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
                color = if (w.clip_count >= minClips) Domovoi.colors.ok else Domovoi.colors.fgMuted,
            )
            Text("threshold", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
            OutlinedTextField(
                value = thr,
                onValueChange = { thr = it },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Decimal,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = { commitThreshold() }),
                modifier = Modifier.width(90.dp),
            )
        }

        if (w.status == "failed" && !w.error.isNullOrBlank()) {
            Text(w.error, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err)
        }

        // Live recording banner — prominent while a take is capturing.
        if (capturing) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .border(1.dp, Domovoi.colors.err, RoundedCornerShape(8.dp))
                    .background(Domovoi.colors.errSoft, RoundedCornerShape(8.dp))
                    .padding(10.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Pill("recording", Tone.Err, live = true)
                    Text(
                        "${w.clip_count} clip${if (w.clip_count == 1) "" else "s"} captured",
                        style = MaterialTheme.typography.titleSmall,
                        color = Domovoi.colors.fg,
                    )
                }
                Text(
                    if (w.clip_count >= minClips) {
                        "enough to train — keep going or tap Stop"
                    } else {
                        "${minClips - w.clip_count} more to reach the train minimum"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
                OutlinedButton(onClick = {
                    recActive = false
                    onRecordStop(roomFor)
                }) {
                    Icon(Icons.Filled.Stop, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text("Stop recording")
                }
            }
        }

        // Action line — record on a room, train, push, set default, clips
        if (haveRooms) {
            SettingsDropdown(
                selected = roomFor,
                options = rooms,
                label = { it },
                onSelect = { room = it },
                modifier = Modifier.width(200.dp),
            )
        } else {
            Text(
                "no online satellites",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgSubtle,
            )
        }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            OutlinedButton(
                enabled = haveRooms && w.status == "recording" && !capturing,
                onClick = {
                    recActive = true
                    onRecordStart(roomFor)
                },
            ) {
                Icon(Icons.Filled.Mic, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("Record")
            }
            TextButton(enabled = haveRooms, onClick = {
                recActive = false
                onRecordStop(roomFor)
            }) {
                Icon(Icons.Filled.Stop, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("Stop")
            }
            Button(enabled = canTrain, onClick = onTrain) {
                Icon(Icons.Filled.Memory, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("Train")
            }
            OutlinedButton(enabled = haveRooms && canPush, onClick = { onPush(roomFor) }) {
                Icon(Icons.Filled.Upload, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("Push to room")
            }
            if (canPush && !w.is_default) {
                TextButton(onClick = onSetDefault) { Text("make default") }
            }
            TextButton(onClick = { showClips = !showClips }) {
                Icon(
                    if (showClips) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                    contentDescription = null,
                )
                Spacer(Modifier.width(4.dp))
                Text(if (showClips) "hide clips" else "clips (${w.clip_count})")
            }
        }

        // Expandable per-clip review: quality, auto-trim, playback, curation
        if (showClips) {
            HorizontalDivider(color = Domovoi.colors.borderSoft)
            WakeClipGrid(wid = w.id, canScore = w.status == "ready")
        }
    }
}

// ---------------------------------------------------------------------------
// Clip grid — curation controls + clip cards. One sound plays at a time.
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WakeClipGrid(wid: Long, canScore: Boolean) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val state = rememberApi(
        wid,
        eventTypes = setOf("wake_words.changed"),
        fetch = { it.api.get("/api/wake-words/$wid/clips").decode<WakeClipList>() },
    )
    val data = state.data
    val clips = data?.clips ?: emptyList()
    val selectedCount = data?.selected_count ?: 0
    val minClips = data?.min_clips ?: WAKE_MIN_CLIPS

    val player = remember { TempAudioPlayer(context) }
    DisposableEffect(Unit) { onDispose { player.stop() } }
    var playing by remember { mutableStateOf<Pair<String, String>?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun act(okMsg: String?, block: suspend () -> Unit) {
        scope.launch {
            busy = true
            try {
                block()
                okMsg?.let(toast)
                state.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("failed: ${e.message ?: "request failed"}")
            } finally {
                busy = false
            }
        }
    }

    // Stop the previous sound before starting a new one; tapping the playing
    // variant again stops it.
    fun play(name: String, variant: String) {
        if (playing == name to variant) {
            player.stop()
            playing = null
            return
        }
        playing = name to variant
        scope.launch {
            try {
                val encoded = URLEncoder.encode(name, "UTF-8")
                val (bytes, _) = app.api.bytes(
                    "/api/wake-words/$wid/clips/$encoded/audio?variant=$variant",
                )
                player.play(bytes) { if (playing == name to variant) playing = null }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("clip playback failed")
                playing = null
            }
        }
    }

    if (state.loading && data == null) {
        Text("loading clips…", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        return
    }
    if (clips.isEmpty()) {
        Text(
            "No clips recorded yet — hit Record on a room.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        return
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            "$selectedCount / $minClips selected for training",
            style = MaterialTheme.typography.labelMedium,
            color = if (selectedCount >= minClips) Domovoi.colors.ok else Domovoi.colors.fgMuted,
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            TextButton(enabled = !busy, onClick = {
                act("all clips selected") {
                    app.api.post(
                        "/api/wake-words/$wid/clips/selection",
                        buildJsonObject { put("selected", true) },
                    )
                }
            }) { Text("Select all") }
            TextButton(enabled = !busy, onClick = {
                act("all clips deselected") {
                    app.api.post(
                        "/api/wake-words/$wid/clips/selection",
                        buildJsonObject { put("selected", false) },
                    )
                }
            }) { Text("None") }
            TextButton(enabled = !busy, onClick = {
                act("poor clips deselected") {
                    app.api.post(
                        "/api/wake-words/$wid/clips/selection",
                        buildJsonObject {
                            put("selected", false)
                            put("only_verdict", "poor")
                        },
                    )
                }
            }) { Text("Deselect poor") }
            TextButton(enabled = !busy, onClick = {
                act("re-analyzed clips") {
                    app.api.post("/api/wake-words/$wid/clips/reanalyze")
                }
            }) {
                Icon(Icons.Filled.Refresh, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("Re-analyze")
            }
            if (canScore) {
                OutlinedButton(enabled = !busy, onClick = {
                    act(null) {
                        val res = app.api.post("/api/wake-words/$wid/score").decode<WakeScoreResult>()
                        res.summary?.let { s ->
                            toast(
                                "scored: real recall ${((s.raw_recall ?: 0.0) * 100).roundToInt()}% · " +
                                    "silence ${fmtNum(s.silence_score)}",
                            )
                        }
                    }
                }) {
                    Icon(Icons.Filled.GraphicEq, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text("Score clips")
                }
            }
        }

        clips.chunked(2).forEach { rowClips ->
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                rowClips.forEach { c ->
                    WakeClipCard(
                        c = c,
                        playing = playing,
                        modifier = Modifier.weight(1f),
                        onToggle = { selected ->
                            act(null) {
                                app.api.patch(
                                    "/api/wake-words/$wid/clips/${URLEncoder.encode(c.name, "UTF-8")}",
                                    buildJsonObject { put("selected", selected) },
                                )
                            }
                        },
                        onPlay = { variant -> play(c.name, variant) },
                        onDelete = {
                            act("deleted ${c.name}") {
                                app.api.delete(
                                    "/api/wake-words/$wid/clips/${URLEncoder.encode(c.name, "UTF-8")}",
                                )
                            }
                        },
                    )
                }
                if (rowClips.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WakeClipCard(
    c: WakeClip,
    playing: Pair<String, String>?,
    modifier: Modifier = Modifier,
    onToggle: (Boolean) -> Unit,
    onPlay: (String) -> Unit,
    onDelete: () -> Unit,
) {
    val tone = when (c.verdict) {
        "good" -> Tone.Ok
        "fair" -> Tone.Warn
        "poor" -> Tone.Err
        else -> Tone.Idle
    }
    val color = toneColor(tone)
    val idx = Regex("\\d+").find(c.name)?.value ?: c.name
    val m = c.metrics

    Column(
        modifier
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
            .padding(8.dp)
            .alpha(if (c.selected) 1f else 0.5f),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Checkbox(checked = c.selected, onCheckedChange = onToggle)
            Text(
                "#$idx",
                style = MaterialTheme.typography.labelMedium.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                ),
                color = Domovoi.colors.fg,
            )
            Pill(c.verdict.ifBlank { "?" }, tone)
            c.score?.let { s ->
                Text(
                    "%.2f".format(s),
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = if (s >= 0.5) Domovoi.colors.ok else Domovoi.colors.err,
                )
            }
            Spacer(Modifier.weight(1f))
            IconButton(onClick = onDelete, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.Delete, "delete clip",
                    modifier = Modifier.size(16.dp),
                    tint = Domovoi.colors.fgMuted,
                )
            }
        }

        ClipSparkline(c.envelope, color)

        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            MetricText("SNR ${fmtNum(m?.snr_db)}dB", Domovoi.colors.fgMuted)
            MetricText(
                "${c.raw_duration_ms ?: 0}ms" +
                    if (c.has_trimmed) " → ${c.trimmed_duration_ms ?: 0}" else "",
                Domovoi.colors.fgMuted,
            )
            if ((m?.clipping_pct ?: 0.0) > 0.0) {
                MetricText("clip ${fmtNum(m?.clipping_pct)}%", Domovoi.colors.err)
            }
            c.issues.filter { it != "clipping" }.forEach {
                MetricText(it.replace('_', ' '), Domovoi.colors.warn)
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val rawPlaying = playing == c.name to "raw"
            val trimmedPlaying = playing == c.name to "trimmed"
            OutlinedButton(onClick = { onPlay("raw") }) {
                Icon(
                    if (rawPlaying) Icons.Filled.Stop else Icons.Filled.PlayArrow,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(4.dp))
                Text("raw")
            }
            OutlinedButton(enabled = c.has_trimmed, onClick = { onPlay("trimmed") }) {
                Icon(
                    if (trimmedPlaying) Icons.Filled.Stop else Icons.Filled.PlayArrow,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(4.dp))
                Text("trimmed")
            }
        }
    }
}

@Composable
private fun MetricText(text: String, color: Color) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
        color = color,
    )
}

/** Compact energy sparkline from the clip's downsampled RMS envelope — a
 *  clean phrase has a clear hump; silence/gated clips look flat or spiky. */
@Composable
private fun ClipSparkline(env: List<Double>, color: Color) {
    Row(
        Modifier.fillMaxWidth().height(26.dp),
        horizontalArrangement = Arrangement.spacedBy(1.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        env.forEach { v0 ->
            val v = v0.toFloat().coerceIn(0f, 1f)
            Box(
                Modifier
                    .weight(1f)
                    .fillMaxHeight(v.coerceAtLeast(0.06f))
                    .background(color.copy(alpha = 0.45f + 0.55f * v), RoundedCornerShape(1.dp)),
            )
        }
    }
}
