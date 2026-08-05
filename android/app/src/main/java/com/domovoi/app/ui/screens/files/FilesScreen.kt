package com.domovoi.app.ui.screens.files

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.outlined.Article
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Podcasts
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.PictureAsPdf
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.deleteFiles
import com.domovoi.app.net.filesBrowsePath
import com.domovoi.app.net.importFile
import com.domovoi.app.net.openFileDownload
import com.domovoi.app.net.rememberApi
import com.domovoi.app.net.uploadFiles
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.screens.documents.SheetEditorOverlay
import com.domovoi.app.ui.screens.documents.TextEditorOverlay
import com.domovoi.app.ui.screens.documents.openRawDoc
import com.domovoi.app.ui.screens.documents.relFromEpochSec
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch

/**
 * Files — a multi-library browser over the web dashboard's generic `/api/files`
 * surface (design §6). A library selector (core media dirs, enabled-plugin
 * media libraries, present removable drives), a breadcrumb trail, and a
 * one-level folder listing. Per-row Download; Delete (with a recursive-folder
 * confirm) when the library is editable; an Import affordance on removable
 * drives (copy into an importable library). The Documents library keeps opening
 * the existing in-app text editor / raw-view flows for office/text/image/pdf.
 *
 * Plugin libraries are gated by `/api/capabilities` server-side (the endpoint
 * already omits disabled plugins), so the client renders whatever
 * `/api/files/libraries` returns.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FilesScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    val libs = rememberApi("files-libraries") {
        it.api.get("/api/files/libraries").decode<LibrariesResponse>().libraries
    }
    val libraries = libs.data ?: emptyList()

    var selectedId by remember { mutableStateOf<String?>(null) }
    var path by remember { mutableStateOf("") }

    // Auto-select the first library once loaded; re-select if the current one
    // vanishes (e.g. a removable drive was ejected between refreshes).
    LaunchedEffect(libraries) {
        if (libraries.isEmpty()) {
            selectedId = null
        } else if (selectedId == null || libraries.none { it.id == selectedId }) {
            selectedId = libraries.first().id
            path = ""
        }
    }

    val currentLib = libraries.firstOrNull { it.id == selectedId }
    val editable = currentLib?.editable == true
    val isRemovable = currentLib?.kind == "removable"
    val isDocuments = selectedId == "core:documents"

    val browse = rememberApi(selectedId, path, eventTypes = setOf("library.indexer.changed")) {
        val id = selectedId
        if (id.isNullOrBlank()) null
        else it.api.get(filesBrowsePath(id, path)).decode<FileBrowse>()
    }
    val data = browse.data

    var textEditorRel by remember { mutableStateOf<String?>(null) }
    var sheetEditorRel by remember { mutableStateOf<String?>(null) }
    var confirmDelete by remember { mutableStateOf<FileEntry?>(null) }
    var importEntry by remember { mutableStateOf<FileEntry?>(null) }
    var busy by remember { mutableStateOf<String?>(null) } // uploading | deleting | importing

    val filePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents(),
    ) { uris ->
        val id = selectedId
        if (id == null || uris.isEmpty()) return@rememberLauncherForActivityResult
        busy = "uploading"
        toast("uploading ${uris.size} file${if (uris.size == 1) "" else "s"}…")
        scope.launch {
            runCatching { uploadFiles(ctx, app, id, path, uris) }
                .onSuccess { r ->
                    val parts = StringBuilder("uploaded ${r.saved} file${if (r.saved == 1) "" else "s"}")
                    if (r.skipped > 0) parts.append(" · ${r.skipped} skipped")
                    if (r.reindexTriggered) parts.append(" · indexing…")
                    toast(parts.toString())
                    browse.refresh()
                }
                .onFailure { toast("upload failed: ${it.message}") }
            busy = null
        }
    }

    fun onEntryPrimary(e: FileEntry) {
        if (e.isDir) {
            path = e.rel
            return
        }
        val id = selectedId ?: return
        if (isDocuments) {
            // Documents library edits in-app: text/markdown in the text
            // editor, .xlsx/.csv in the sheet editor; everything else opens
            // with the system viewer via /raw.
            val ext = e.name.substringAfterLast('.', "").lowercase()
            when {
                ext == "xlsx" || ext == "csv" -> sheetEditorRel = e.rel
                e.kind == "doc-text" -> textEditorRel = e.rel
                else -> openRawDoc(ctx, app, e.rel)
            }
        } else if (e.kind == "image") {
            // Images in ANY library open inline (system viewer) via the
            // generic library-image serve — web-parity with the Files
            // tab's "Open" action.
            ctx.startActivity(
                android.content.Intent(
                    android.content.Intent.ACTION_VIEW,
                    android.net.Uri.parse(
                        app.api.absolute(
                            "/api/images/raw?library_id=${android.net.Uri.encode(id)}" +
                                "&path=${android.net.Uri.encode(e.rel)}",
                        ),
                    ),
                ),
            )
        } else {
            openFileDownload(ctx, app, id, e.rel)
        }
    }

    fun onEntryDownload(e: FileEntry) {
        val id = selectedId ?: return
        // Documents files reuse the /api/documents/raw attachment serve; every
        // other library (and any directory → server zip) uses /api/files/download.
        if (isDocuments && !e.isDir) openRawDoc(ctx, app, e.rel)
        else openFileDownload(ctx, app, id, e.rel)
    }

    fun doDelete(e: FileEntry) {
        val id = selectedId ?: return
        busy = "deleting"
        scope.launch {
            runCatching { deleteFiles(app, id, listOf(e.rel), recursive = e.isDir) }
                .onSuccess { r ->
                    toast(
                        "deleted ${r.deleted}" +
                            if (r.failed > 0) " · ${r.failed} failed" else "",
                    )
                    browse.refresh()
                }
                .onFailure { toast("delete failed: ${it.message}") }
            busy = null
        }
    }

    fun doImport(e: FileEntry, target: FileLibrary) {
        val id = selectedId ?: return
        busy = "importing"
        toast("importing \"${e.name}\" → ${target.label}…")
        scope.launch {
            runCatching { importFile(app, id, e.rel, target.id, "") }
                .onSuccess { r ->
                    val parts = StringBuilder("imported ${r.copied} item${if (r.copied == 1) "" else "s"}")
                    if (r.skipped > 0) parts.append(" · ${r.skipped} skipped")
                    if (r.reindexTriggered) parts.append(" · indexing…")
                    toast(parts.toString())
                }
                .onFailure { toast("import failed: ${it.message}") }
            busy = null
        }
    }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            PageHeader(
                "Files",
                "browse every library — music, docs, plugins & removable drives",
                actions = {
                    if (editable) {
                        OutlinedButton(
                            onClick = { filePicker.launch("*/*") },
                            enabled = busy == null,
                        ) {
                            Text(if (busy == "uploading") "uploading…" else "upload")
                        }
                    }
                },
            )
            Spacer(Modifier.height(12.dp))

            LibrarySelector(
                libraries = libraries,
                selectedId = selectedId,
                onSelect = { selectedId = it; path = "" },
            )
            Spacer(Modifier.height(10.dp))

            if (selectedId != null) {
                Breadcrumb(
                    label = currentLib?.label ?: "root",
                    segments = data?.breadcrumb ?: emptyList(),
                    onHome = { path = "" },
                    onSegment = { i -> path = (data?.breadcrumb ?: emptyList()).take(i + 1).joinToString("/") },
                )
                Spacer(Modifier.height(10.dp))
            }

            when {
                libs.data == null && libs.loading -> LoadingState()
                libs.data == null && libs.error != null ->
                    ErrorState(libs.error ?: "request failed", libs.refresh)
                libraries.isEmpty() -> EmptyState(
                    "no libraries",
                    "no browsable libraries are configured on this server",
                )
                selectedId == null -> LoadingState()
                browse.data == null && browse.loading -> LoadingState()
                browse.data == null && browse.error != null ->
                    ErrorState(browse.error ?: "request failed", browse.refresh)
                data != null && data.entries.isEmpty() -> EmptyState(
                    "empty folder",
                    if (editable) "upload files, or pick another library" else "nothing here yet",
                )
                data != null -> DomovoiCard(
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    padding = 0,
                ) {
                    LazyColumn(Modifier.fillMaxWidth()) {
                        items(data.entries, key = { it.rel }) { e ->
                            FileRow(
                                entry = e,
                                editable = editable,
                                removable = isRemovable,
                                onOpen = { onEntryPrimary(e) },
                                onDownload = { onEntryDownload(e) },
                                onDelete = { confirmDelete = e },
                                onImport = { importEntry = e },
                            )
                        }
                    }
                }
                else -> LoadingState()
            }
        }

        textEditorRel?.let { rel ->
            TextEditorOverlay(rel) {
                textEditorRel = null
                browse.refresh()
            }
        }
        sheetEditorRel?.let { rel ->
            SheetEditorOverlay(rel) {
                sheetEditorRel = null
                browse.refresh()
            }
        }
    }

    confirmDelete?.let { e ->
        ConfirmDialog(
            title = if (e.isDir) "delete folder" else "delete file",
            body = if (e.isDir) {
                "Delete \"${e.name}\" and everything inside it? This can't be undone."
            } else {
                "Delete \"${e.name}\"? This can't be undone."
            },
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { doDelete(e) },
            onDismiss = { confirmDelete = null },
        )
    }

    importEntry?.let { e ->
        ImportTargetSheet(
            targets = libraries.filter { it.importable },
            onPick = { target ->
                importEntry = null
                doImport(e, target)
            },
            onDismiss = { importEntry = null },
        )
    }
}

@Composable
private fun LibrarySelector(
    libraries: List<FileLibrary>,
    selectedId: String?,
    onSelect: (String) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    val current = libraries.firstOrNull { it.id == selectedId }
    Box {
        Row(
            Modifier
                .fillMaxWidth()
                .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
                .clickable(enabled = libraries.isNotEmpty()) { open = true }
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(
                libIcon(current?.icon ?: "folder"),
                contentDescription = null,
                tint = Domovoi.colors.brand,
                modifier = Modifier.size(20.dp),
            )
            Text(
                current?.label ?: "select a library",
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Icon(
                Icons.Filled.ArrowDropDown,
                contentDescription = "choose library",
                tint = Domovoi.colors.fgMuted,
            )
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            libraries.forEach { lib ->
                DropdownMenuItem(
                    leadingIcon = {
                        Icon(libIcon(lib.icon), null, tint = Domovoi.colors.fgMuted)
                    },
                    text = {
                        Column {
                            Text(lib.label, style = MaterialTheme.typography.titleSmall)
                            Text(
                                lib.kind + (if (!lib.editable) " · read-only" else ""),
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fgFaint,
                            )
                        }
                    },
                    onClick = {
                        open = false
                        onSelect(lib.id)
                    },
                )
            }
        }
    }
}

@Composable
private fun Breadcrumb(
    label: String,
    segments: List<String>,
    onHome: () -> Unit,
    onSegment: (Int) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            Modifier.clickable(onClick = onHome).padding(vertical = 4.dp, horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(
                Icons.Filled.Home,
                contentDescription = "library root",
                tint = if (segments.isEmpty()) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                modifier = Modifier.size(16.dp),
            )
            Text(
                label,
                style = MaterialTheme.typography.labelMedium,
                color = if (segments.isEmpty()) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                maxLines = 1,
            )
        }
        segments.forEachIndexed { i, seg ->
            Text("/", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgFaint)
            Text(
                seg,
                style = MaterialTheme.typography.labelMedium,
                color = if (i == segments.lastIndex) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                maxLines = 1,
                modifier = Modifier
                    .clickable { onSegment(i) }
                    .padding(vertical = 4.dp, horizontal = 2.dp),
            )
        }
    }
}

@Composable
private fun FileRow(
    entry: FileEntry,
    editable: Boolean,
    removable: Boolean,
    onOpen: () -> Unit,
    onDownload: () -> Unit,
    onDelete: () -> Unit,
    onImport: () -> Unit,
) {
    Column {
        Row(
            Modifier.fillMaxWidth()
                .clickable(onClick = onOpen)
                .padding(horizontal = 14.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(
                entryIcon(entry),
                contentDescription = null,
                tint = if (entry.isDir) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                modifier = Modifier.size(20.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(
                    entry.name,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        if (entry.isDir) {
                            "folder · ${relFromEpochSec(entry.mtime)}"
                        } else {
                            "${fmtBytes(entry.size)} · ${relFromEpochSec(entry.mtime)}"
                        },
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                        color = Domovoi.colors.fgFaint,
                    )
                    entry.lockedBy?.let { Pill("editing in $it", Tone.Warn) }
                }
            }
            if (removable) {
                IconButton(onClick = onImport) {
                    Icon(Icons.Filled.ContentCopy, "import into a library", tint = Domovoi.colors.fgMuted)
                }
            }
            IconButton(onClick = onDownload) {
                Icon(Icons.Outlined.Download, "download", tint = Domovoi.colors.fgMuted)
            }
            if (editable) {
                IconButton(onClick = onDelete) {
                    Icon(Icons.Outlined.Delete, "delete", tint = Domovoi.colors.fgMuted)
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ImportTargetSheet(
    targets: List<FileLibrary>,
    onPick: (FileLibrary) -> Unit,
    onDismiss: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Domovoi.colors.raised) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "import into…",
                style = MaterialTheme.typography.titleMedium,
                color = Domovoi.colors.fg,
            )
            if (targets.isEmpty()) {
                Text(
                    "no importable libraries — nothing writable to copy into.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                )
            } else {
                targets.forEach { lib ->
                    Row(
                        Modifier.fillMaxWidth()
                            .clickable { onPick(lib) }
                            .padding(vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(libIcon(lib.icon), null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(20.dp))
                        Text(lib.label, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
                    }
                }
            }
        }
    }
}

// Lucide library-icon name → Material glyph (per-library selector icon).
private fun libIcon(name: String): ImageVector = when (name) {
    "music" -> Icons.Filled.MusicNote
    "book-open" -> Icons.AutoMirrored.Filled.MenuBook
    "podcast" -> Icons.Filled.Podcasts
    "file-text" -> Icons.Outlined.Description
    "hard-drive" -> Icons.Filled.Storage
    "disc" -> Icons.Filled.Album
    "clapperboard" -> Icons.Filled.Movie
    "puzzle" -> Icons.Filled.Extension
    else -> Icons.Filled.Folder
}

// Entry `kind` → row glyph.
private fun entryIcon(entry: FileEntry): ImageVector = when (entry.kind) {
    "folder" -> Icons.Filled.Folder
    "audio" -> Icons.Filled.MusicNote
    "doc-office" -> Icons.Outlined.Description
    "doc-text" -> Icons.AutoMirrored.Outlined.Article
    "image" -> Icons.Outlined.Image
    "pdf" -> Icons.Outlined.PictureAsPdf
    else -> Icons.Outlined.Description
}
