package com.domovoi.app.ui.screens.documents

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.TableChart
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import com.domovoi.app.net.decode

// ---------------------------------------------------------------------------
// Models — /api/documents/sheet grid (web sheet_editor.jsx).
// ---------------------------------------------------------------------------

@Serializable
private data class SheetCell(val v: String? = null, val f: String? = null)

@Serializable
private data class SheetGrid(val rows: List<List<SheetCell?>> = emptyList())

/**
 * Minimal spreadsheet editor for .xlsx/.csv — the mobile analog of the web
 * sheet editor, deliberately simpler: an editable value grid (formulas show
 * and save as their `=...` strings; evaluation is the web editor's job).
 * Explicit Save, dirty guard, 415 → download hint.
 */
@Composable
internal fun SheetEditorOverlay(relPath: String, onClose: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    // Mutable padded grid: rows × cols of plain display strings.
    val grid = remember { mutableStateListOf<MutableList<String>>() }
    var cols by remember { mutableStateOf(0) }
    var dirty by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var confirmDiscard by remember { mutableStateOf(false) }

    LaunchedEffect(relPath) {
        loading = true
        runCatching {
            app.api.get("/api/documents/sheet/${java.net.URLEncoder.encode(relPath, "UTF-8")}")
                .decode<SheetGrid>()
        }.onSuccess { g ->
            grid.clear()
            cols = maxOf(4, g.rows.maxOfOrNull { it.size } ?: 0)
            val rows = maxOf(8, g.rows.size)
            for (r in 0 until rows) {
                val src = g.rows.getOrNull(r).orEmpty()
                grid.add(MutableList(cols) { c ->
                    val cell = src.getOrNull(c)
                    cell?.f ?: cell?.v ?: ""
                })
            }
            error = null
        }.onFailure {
            error = if ((it.message ?: "").startsWith("415")) "unsupported" else (it.message ?: "load failed")
        }
        loading = false
    }

    fun save() {
        if (saving || !dirty) return
        saving = true
        scope.launch {
            runCatching {
                app.api.put(
                    "/api/documents/sheet/${java.net.URLEncoder.encode(relPath, "UTF-8")}",
                    buildJsonObject {
                        put("rows", buildJsonArray {
                            grid.forEach { row ->
                                add(buildJsonArray {
                                    row.forEach { t ->
                                        add(buildJsonObject {
                                            if (t.startsWith("=")) put("f", t) else put("v", t)
                                        })
                                    }
                                })
                            }
                        })
                    },
                )
            }
                .onSuccess { dirty = false; toast("saved") }
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
                Icon(Icons.Outlined.TableChart, contentDescription = null, tint = Domovoi.colors.fgMuted)
                Text(
                    relPath,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Pill("sheet", Tone.Idle)
                if (dirty) {
                    Text("unsaved", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.warn)
                }
                if (!loading && error == null) {
                    Button(onClick = { save() }, enabled = dirty && !saving) {
                        Text(if (saving) "saving…" else "save")
                    }
                }
                IconButton(onClick = { requestClose() }) {
                    Icon(Icons.Outlined.Close, "close", tint = Domovoi.colors.fgMuted)
                }
            }
            HorizontalDivider(color = Domovoi.colors.border)

            when {
                loading -> LoadingState()
                error == "unsupported" -> Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        "This format can't be edited here (only .xlsx and .csv can) — download it instead.",
                        style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted,
                    )
                    OutlinedButton(onClick = onClose) { Text("close") }
                }
                error != null -> Text(
                    "Couldn't load: $error",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.err,
                    modifier = Modifier.padding(20.dp),
                )
                else -> {
                    val hScroll = rememberScrollState()
                    LazyColumn(Modifier.fillMaxSize().horizontalScroll(hScroll)) {
                        itemsIndexed(grid) { r, row ->
                            Row {
                                Text(
                                    "${r + 1}",
                                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                                    color = Domovoi.colors.fgFaint,
                                    modifier = Modifier.width(34.dp).padding(top = 8.dp, start = 6.dp),
                                )
                                row.forEachIndexed { c, value ->
                                    var local by remember(r, c, value) { mutableStateOf(value) }
                                    BasicTextField(
                                        value = local,
                                        onValueChange = {
                                            local = it
                                            grid[r][c] = it
                                            dirty = true
                                        },
                                        textStyle = MaterialTheme.typography.bodySmall.copy(
                                            fontFamily = MonoFamily, color = Domovoi.colors.fg,
                                        ),
                                        cursorBrush = SolidColor(Domovoi.colors.brand),
                                        singleLine = true,
                                        modifier = Modifier
                                            .width(110.dp)
                                            .border(0.5.dp, Domovoi.colors.borderSoft)
                                            .background(Domovoi.colors.canvas)
                                            .padding(horizontal = 6.dp, vertical = 8.dp),
                                    )
                                }
                            }
                        }
                    }
                }
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
