package com.domovoi.app.ui.screens.documents

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.Request

private sealed class EditorStatus {
    data object Loading : EditorStatus()
    data object Ready : EditorStatus()
    data class Unpreviewable(val reason: String) : EditorStatus()
    data class Error(val message: String) : EditorStatus()
}

/** One markdown toolbar action: wrap the selection or prefix its lines. */
private data class MdTool(val label: String, val before: String, val after: String = "", val linePrefix: Boolean = false)

private val MD_TOOLS = listOf(
    MdTool("B", "**", "**"),
    MdTool("I", "*", "*"),
    MdTool("H1", "# ", linePrefix = true),
    MdTool("H2", "## ", linePrefix = true),
    MdTool("•", "- ", linePrefix = true),
    MdTool("1.", "1. ", linePrefix = true),
    MdTool("`", "`", "`"),
    MdTool(">", "> ", linePrefix = true),
)

private fun applyMdTool(value: androidx.compose.ui.text.input.TextFieldValue, tool: MdTool): androidx.compose.ui.text.input.TextFieldValue {
    val s = value.selection.min
    val e = value.selection.max
    val text = value.text
    return if (tool.linePrefix) {
        val lineStart = text.lastIndexOf('\n', (s - 1).coerceAtLeast(0)).let { if (it < 0) 0 else it + 1 }
        val next = text.substring(0, lineStart) + tool.before + text.substring(lineStart)
        value.copy(text = next, selection = androidx.compose.ui.text.TextRange(e + tool.before.length))
    } else {
        val sel = text.substring(s, e)
        val next = text.substring(0, s) + tool.before + sel + tool.after + text.substring(e)
        value.copy(
            text = next,
            selection = androidx.compose.ui.text.TextRange(s + tool.before.length, s + tool.before.length + sel.length),
        )
    }
}

/**
 * Full-screen in-app text editor (web TextEditorOverlay / DocEditor):
 * GET /text → edit in a monospace field → Save PUTs {text}. Markdown files
 * additionally get a formatting toolbar (the mobile analog of the web
 * markdown editor's). Saves on the button only, so closing with unsaved
 * edits asks first. A 415 means the file isn't previewable as text — offer
 * the raw download instead.
 */
@Composable
internal fun TextEditorOverlay(relPath: String, onClose: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val isMarkdown = relPath.lowercase().endsWith(".md") || relPath.lowercase().endsWith(".markdown")

    var status by remember { mutableStateOf<EditorStatus>(EditorStatus.Loading) }
    var field by remember { mutableStateOf(androidx.compose.ui.text.input.TextFieldValue("")) }
    var dirty by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var confirmDiscard by remember { mutableStateOf(false) }

    LaunchedEffect(relPath) {
        status = EditorStatus.Loading
        withContext(Dispatchers.IO) {
            runCatching {
                val req = Request.Builder().url(app.api.absolute(docTextPath(relPath))).build()
                app.api.http.newCall(req).execute().use { resp ->
                    val body = resp.body?.string().orEmpty()
                    status = when {
                        resp.code == 415 -> {
                            val reason = runCatching {
                                DomovoiJson.parseToJsonElement(body)
                                    .jsonObject["reason"]?.jsonPrimitive?.contentOrNull
                            }.getOrNull()
                            EditorStatus.Unpreviewable(reason ?: "binary")
                        }
                        !resp.isSuccessful -> EditorStatus.Error("${resp.code} ${resp.message}")
                        else -> {
                            val loaded = runCatching {
                                DomovoiJson.parseToJsonElement(body)
                                    .jsonObject["text"]?.jsonPrimitive?.contentOrNull
                            }.getOrNull() ?: ""
                            field = androidx.compose.ui.text.input.TextFieldValue(loaded)
                            EditorStatus.Ready
                        }
                    }
                }
            }.onFailure { status = EditorStatus.Error(it.message ?: "load failed") }
        }
    }

    fun save() {
        if (saving || !dirty) return
        saving = true
        scope.launch {
            runCatching {
                app.api.put(docTextPath(relPath), buildJsonObject { put("text", field.text) })
            }
                .onSuccess {
                    dirty = false
                    toast("saved")
                }
                .onFailure { toast("save failed: ${it.message}") }
            saving = false
        }
    }

    fun requestClose() {
        if (dirty) confirmDiscard = true else onClose()
    }

    BackHandler { requestClose() }

    Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth()
                    .background(Domovoi.colors.card)
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Icon(
                    Icons.Outlined.Description,
                    contentDescription = null,
                    tint = Domovoi.colors.fgMuted,
                )
                Text(
                    relPath,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Pill(if (isMarkdown) "markdown" else "text", Tone.Idle)
                if (dirty) {
                    Text(
                        "unsaved",
                        style = MaterialTheme.typography.labelMedium,
                        color = Domovoi.colors.warn,
                    )
                }
                if (status == EditorStatus.Ready) {
                    Button(onClick = { save() }, enabled = dirty && !saving) {
                        Text(if (saving) "saving…" else "save")
                    }
                }
                IconButton(onClick = { requestClose() }) {
                    Icon(Icons.Outlined.Close, "close", tint = Domovoi.colors.fgMuted)
                }
            }
            HorizontalDivider(color = Domovoi.colors.border)

            if (isMarkdown && status == EditorStatus.Ready) {
                Row(
                    Modifier.fillMaxWidth()
                        .background(Domovoi.colors.card)
                        .padding(horizontal = 8.dp, vertical = 2.dp),
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    MD_TOOLS.forEach { tool ->
                        androidx.compose.material3.TextButton(onClick = {
                            field = applyMdTool(field, tool)
                            dirty = true
                        }) {
                            Text(
                                tool.label,
                                style = MaterialTheme.typography.labelMedium.copy(fontFamily = MonoFamily),
                                color = Domovoi.colors.fgMuted,
                            )
                        }
                    }
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }

            when (val st = status) {
                EditorStatus.Loading -> LoadingState()
                is EditorStatus.Error -> Text(
                    "Couldn't load: ${st.message}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.err,
                    modifier = Modifier.padding(20.dp),
                )
                is EditorStatus.Unpreviewable -> Column(
                    Modifier.padding(24.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(
                        "Can't preview $relPath as text" + if (st.reason == "too_large") {
                            " — it's too large."
                        } else {
                            " — it looks binary."
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { openRawDoc(ctx, app, relPath) }) {
                            Text("download")
                        }
                    }
                }
                EditorStatus.Ready -> TextField(
                    value = field,
                    onValueChange = {
                        if (it.text != field.text) dirty = true
                        field = it
                    },
                    modifier = Modifier.fillMaxSize(),
                    textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = MonoFamily),
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Domovoi.colors.canvas,
                        unfocusedContainerColor = Domovoi.colors.canvas,
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                        cursorColor = Domovoi.colors.brand,
                        focusedTextColor = Domovoi.colors.fg,
                        unfocusedTextColor = Domovoi.colors.fg,
                    ),
                )
            }
        }
    }

    if (confirmDiscard) {
        ConfirmDialog(
            title = "unsaved changes",
            body = "You have unsaved changes. Discard them and close?",
            confirmLabel = "discard",
            destructive = true,
            onConfirm = onClose,
            onDismiss = { confirmDiscard = false },
        )
    }
}
