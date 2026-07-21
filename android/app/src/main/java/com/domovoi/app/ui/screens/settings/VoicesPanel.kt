package com.domovoi.app.ui.screens.settings

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
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
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLDecoder

/**
 * Voices tab — the TTS voice registry (settings.jsx VoicesPanel).
 * Register a cloud (Edge) voice by id, upload a local Piper model
 * (.onnx + .onnx.json), play freshly-synthesized samples, set the default,
 * rename, delete.
 */
@Composable
internal fun VoicesPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val state = rememberApi(fetch = { it.api.get("/api/voices").decode<List<Voice>>() })
    val voices = state.data ?: emptyList()

    val player = remember { TempAudioPlayer(context) }
    DisposableEffect(Unit) { onDispose { player.stop() } }

    var samplingId by remember { mutableStateOf<Long?>(null) }
    var deleting by remember { mutableStateOf<Voice?>(null) }

    // Register-Edge form state (hoisted above the LazyColumn).
    var edgeName by remember { mutableStateOf("") }
    var edgeVoiceId by remember { mutableStateOf("") }

    // Upload-Piper form state.
    var piperName by remember { mutableStateOf("") }
    var onnxUri by remember { mutableStateOf<Uri?>(null) }
    var onnxName by remember { mutableStateOf("") }
    var cfgUri by remember { mutableStateOf<Uri?>(null) }
    var cfgName by remember { mutableStateOf("") }
    var uploading by remember { mutableStateOf(false) }

    val onnxPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            onnxUri = uri
            onnxName = displayName(context, uri)
        }
    }
    val cfgPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            cfgUri = uri
            cfgName = displayName(context, uri)
        }
    }

    // Fetch a freshly-synthesized sample and play it. The button stays busy
    // through synthesis + playback, cleared on end (mirrors the web).
    fun playSample(v: Voice) {
        scope.launch {
            player.stop()
            samplingId = v.id
            try {
                val (bytes, headers) = app.api.bytes("/api/voices/${v.id}/sample")
                val said = headers.entries
                    .firstOrNull { it.key.equals("X-Sample-Text", ignoreCase = true) }
                    ?.value
                if (!said.isNullOrBlank()) {
                    toast(runCatching { URLDecoder.decode(said, "UTF-8") }.getOrDefault(said))
                }
                player.play(bytes) { if (samplingId == v.id) samplingId = null }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("sample failed: ${e.message ?: "error"}")
                samplingId = null
            }
        }
    }

    fun uploadPiper() {
        val n = piperName.trim()
        val onnx = onnxUri
        val cfg = cfgUri
        if (n.isBlank() || onnx == null || cfg == null) return
        scope.launch {
            uploading = true
            try {
                val form = withContext(Dispatchers.IO) {
                    val onnxBytes = context.contentResolver.openInputStream(onnx)
                        ?.use { it.readBytes() } ?: error("couldn't read the .onnx file")
                    val cfgBytes = context.contentResolver.openInputStream(cfg)
                        ?.use { it.readBytes() } ?: error("couldn't read the config file")
                    MultipartBody.Builder().setType(MultipartBody.FORM)
                        .addFormDataPart("name", n)
                        .addFormDataPart(
                            "onnx",
                            onnxName.ifBlank { "model.onnx" },
                            onnxBytes.toRequestBody("application/octet-stream".toMediaType()),
                        )
                        .addFormDataPart(
                            "config",
                            cfgName.ifBlank { "model.onnx.json" },
                            cfgBytes.toRequestBody("application/json".toMediaType()),
                        )
                        .build()
                }
                app.api.upload("/api/voices/piper", form)
                toast("voice uploaded")
                piperName = ""
                onnxUri = null
                onnxName = ""
                cfgUri = null
                cfgName = ""
                state.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                toast("failed: ${e.message ?: "upload failed"}")
            } finally {
                uploading = false
            }
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Add a cloud voice",
                "Register a Microsoft Edge neural voice by its id. Needs network to speak.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = edgeName,
                        onValueChange = { edgeName = it },
                        placeholder = { Text("Name (e.g. Aria)", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = edgeVoiceId,
                        onValueChange = { edgeVoiceId = it },
                        placeholder = {
                            Text("Edge voice id (e.g. en-US-AriaNeural)", color = Domovoi.colors.fgSubtle)
                        },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Button(
                        enabled = edgeName.isNotBlank() && edgeVoiceId.isNotBlank(),
                        onClick = {
                            val n = edgeName.trim()
                            val id = edgeVoiceId.trim()
                            scope.settingsMutation(toast, "voice registered", state.refresh) {
                                app.api.post(
                                    "/api/voices/edge",
                                    buildJsonObject {
                                        put("name", n)
                                        put("voice_id", id)
                                    },
                                )
                            }
                            edgeName = ""
                            edgeVoiceId = ""
                        },
                    ) {
                        Icon(Icons.Filled.Cloud, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text("Register")
                    }
                }
            }
        }

        item {
            PanelCard(
                "Upload a local voice",
                "A Piper model (.onnx) and its config (.onnx.json). Fully offline once uploaded.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = piperName,
                        onValueChange = { piperName = it },
                        placeholder = { Text("Name (e.g. Ryan)", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedButton(onClick = { onnxPicker.launch("*/*") }) {
                        Icon(Icons.Filled.Upload, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text(
                            onnxName.ifBlank { "Pick the model (.onnx)" },
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    OutlinedButton(onClick = { cfgPicker.launch("*/*") }) {
                        Icon(Icons.Filled.Upload, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text(
                            cfgName.ifBlank { "Pick the config (.onnx.json)" },
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Button(
                        enabled = !uploading && piperName.isNotBlank() && onnxUri != null && cfgUri != null,
                        onClick = { uploadPiper() },
                    ) {
                        Text(if (uploading) "Uploading…" else "Upload voice")
                    }
                }
            }
        }

        val err = state.error
        when {
            err != null && state.data == null -> item { ErrorState(err, state.refresh) }
            state.loading && state.data == null -> item { LoadingState() }
            voices.isEmpty() -> item {
                EmptyState("No voices yet", "Add a cloud voice or upload a Piper model above.")
            }
            else -> item {
                PanelCard(
                    "Registered (${voices.size})",
                    "The default is used by any satellite that hasn't picked its own.",
                ) {
                    voices.forEachIndexed { i, v ->
                        if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
                        VoiceRow(
                            v = v,
                            sampling = samplingId == v.id,
                            onPlay = { playSample(v) },
                            onRename = { name ->
                                scope.settingsMutation(toast, "voice renamed", state.refresh) {
                                    app.api.patch(
                                        "/api/voices/${v.id}",
                                        buildJsonObject { put("name", name) },
                                    )
                                }
                            },
                            onSetDefault = {
                                scope.settingsMutation(
                                    toast, "${v.name} is now the default", state.refresh,
                                ) {
                                    app.api.patch(
                                        "/api/voices/${v.id}",
                                        buildJsonObject { put("set_default", true) },
                                    )
                                }
                            },
                            onDelete = { deleting = v },
                        )
                    }
                }
            }
        }
    }

    deleting?.let { v ->
        ConfirmDialog(
            title = "Delete ${v.name}?",
            body = "Removes the voice from the registry. Satellites using it fall back to the default.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = {
                scope.settingsMutation(toast, "voice removed", state.refresh) {
                    app.api.delete("/api/voices/${v.id}")
                }
            },
            onDismiss = { deleting = null },
        )
    }
}

@Composable
private fun VoiceRow(
    v: Voice,
    sampling: Boolean,
    onPlay: () -> Unit,
    onRename: (String) -> Unit,
    onSetDefault: () -> Unit,
    onDelete: () -> Unit,
) {
    var editing by remember(v.id) { mutableStateOf(false) }
    var name by remember(v.id, v.name) { mutableStateOf(v.name) }

    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            IconButton(onClick = onPlay, enabled = !sampling) {
                if (sampling) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                        color = Domovoi.colors.brand,
                    )
                } else {
                    Icon(Icons.Filled.PlayArrow, "play a sample", tint = Domovoi.colors.fg)
                }
            }
            if (editing) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
            } else {
                Column(Modifier.weight(1f)) {
                    Text(
                        v.name,
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                        color = Domovoi.colors.fg,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        v.model_ref ?: "",
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Pill(
                if (v.engine == "piper") "local" else "cloud",
                if (v.engine == "piper") Tone.Idle else Tone.Brand,
            )
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (v.is_default) {
                Pill("default", Tone.Brand)
            } else {
                TextButton(onClick = onSetDefault) { Text("make default") }
            }
            Spacer(Modifier.weight(1f))
            if (editing) {
                TextButton(onClick = {
                    editing = false
                    name = v.name
                }) { Text("cancel", color = Domovoi.colors.fgMuted) }
                Button(onClick = {
                    val t = name.trim()
                    if (t.isNotBlank() && t != v.name) onRename(t)
                    editing = false
                }) { Text("Save") }
            } else {
                IconButton(onClick = { editing = true }) {
                    Icon(Icons.Filled.Edit, "rename", tint = Domovoi.colors.fgMuted)
                }
                if (!v.is_default) {
                    IconButton(onClick = onDelete) {
                        Icon(Icons.Filled.Delete, "delete", tint = Domovoi.colors.fgMuted)
                    }
                }
            }
        }
    }
}

/** Resolve a content Uri's display name for the multipart filename. */
private fun displayName(context: Context, uri: Uri): String {
    var name: String? = null
    runCatching {
        context.contentResolver.query(
            uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null,
        )?.use { c ->
            if (c.moveToFirst()) name = c.getString(0)
        }
    }
    return name ?: uri.lastPathSegment ?: "file"
}
