package com.domovoi.app.ui.screens.files

import android.net.Uri
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
import androidx.compose.material.icons.automirrored.filled.DriveFileMove
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
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.PictureAsPdf
import androidx.compose.material.icons.outlined.RemoveCircleOutline
import androidx.compose.material.icons.outlined.UploadFile
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
import androidx.compose.runtime.collectAsState
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
import com.domovoi.app.data.LocalMedia
import com.domovoi.app.data.PhoneFolders
import com.domovoi.app.net.ApiException
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
import com.domovoi.app.ui.screens.documents.relFromEpochSec
import com.domovoi.app.ui.shell.LocalServerChoice
import com.domovoi.app.ui.shell.enabled
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
 *
 * The same browser serves the phone's own files in the local shell
 * ([LocalFilesScreen]); everything that differs lives behind [FileSource].
 */
@Composable
fun FilesScreen() {
    val app = LocalApp.current
    val source = remember(app) { ServerFileSource(app) }
    FilesBrowser(source)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FilesBrowser(source: FileSource) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    // The phone's folder list is a key: adding or removing one re-lists.
    val phoneFolders by app.prefs.phoneFolders.collectAsState()
    val libs = rememberApi("files-libraries", source, if (source.onPhone) phoneFolders else null) {
        source.libraries()
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
    val inAppEditing = source.editsInApp(currentLib)

    // A library that needs a runtime permission first (the phone's photos).
    val permission = selectedId?.let { source.permissionFor(it) }
    var permissionTick by remember { mutableStateOf(0) }
    val permitted = remember(permission, permissionTick) {
        permission == null || LocalMedia.hasPermission(ctx, permission)
    }
    val permissionAsk = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { permissionTick++ }

    val browse = rememberApi(selectedId, path, source, permitted, eventTypes = source.browseEvents) {
        val id = selectedId
        if (id.isNullOrBlank() || !permitted) null
        else source.browse(id, path)
    }
    val data = browse.data?.takeIf { it.libraryId == selectedId || !source.onPhone }
    // Two different "no": the LIBRARY is read-only (editable=false), or THIS
    // DEVICE has been blocked by an admin (writable=false). Upload / move /
    // import need both. Delete is admin-gated server-side and this app can't
    // sign in as admin, so its button stays but the failure says so.
    val blocked = data?.writable == false
    val canWrite = editable && !blocked
    // "Send to home" is offered only while the server can actually be used.
    val serverUsable = LocalServerChoice.current.enabled
    val canSendHome = source.onPhone && serverUsable

    var textEditorRel by remember { mutableStateOf<String?>(null) }
    var sheetEditorRel by remember { mutableStateOf<String?>(null) }
    var imageEntry by remember { mutableStateOf<FileEntry?>(null) }
    var confirmDelete by remember { mutableStateOf<FileEntry?>(null) }
    var moveEntry by remember { mutableStateOf<FileEntry?>(null) }
    var importEntry by remember { mutableStateOf<FileEntry?>(null) }
    var sendHomeEntry by remember { mutableStateOf<FileEntry?>(null) }
    var confirmForget by remember { mutableStateOf<FileLibrary?>(null) }
    var busy by remember { mutableStateOf<String?>(null) } // uploading | deleting | importing | sending

    val filePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents(),
    ) { uris ->
        val id = selectedId
        if (id == null || uris.isEmpty()) return@rememberLauncherForActivityResult
        busy = "uploading"
        toast(
            (if (source.onPhone) "copying" else "uploading") +
                " ${uris.size} file${if (uris.size == 1) "" else "s"}…",
        )
        scope.launch {
            runCatching { source.upload(ctx, id, path, uris) }
                .onSuccess { r ->
                    val parts = StringBuilder(
                        (if (source.onPhone) "added" else "uploaded") +
                            " ${r.saved} file${if (r.saved == 1) "" else "s"}",
                    )
                    if (r.skipped > 0) parts.append(" · ${r.skipped} skipped")
                    if (r.reindexTriggered) parts.append(" · indexing…")
                    toast(parts.toString())
                    browse.refresh()
                }
                .onFailure { toast("${if (source.onPhone) "copy" else "upload"} failed: ${it.message}") }
            busy = null
        }
    }

    // Adding a folder: the system folder picker, then a persisted grant.
    val folderPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        val added = addPhoneFolder(ctx, app.prefs, uri)
        if (added == null) {
            toast("couldn't keep access to that folder")
        } else {
            toast("added \"${added.name}\"")
            selectedId = PhoneFolders.libraryId(added)
            path = ""
        }
    }

    fun onEntryPrimary(e: FileEntry) {
        if (e.isDir) {
            path = e.rel
            return
        }
        val id = selectedId ?: return
        // In-app editing libraries (the server's Documents, every phone
        // folder): text/markdown in the text editor, .xlsx/.csv in the sheet
        // editor; everything else opens with the system viewer.
        val how = openWith(e.name, e.kind)
        when {
            inAppEditing && how == OpenWith.SheetEditor -> sheetEditorRel = e.rel
            inAppEditing && how == OpenWith.TextEditor -> textEditorRel = e.rel
            source.viewsImagesInApp && e.kind == "image" -> imageEntry = e
            else -> if (!source.openFile(ctx, id, e)) {
                toast("no app on this phone opens \"${e.name}\"")
            }
        }
    }

    fun doDelete(e: FileEntry) {
        val id = selectedId ?: return
        busy = "deleting"
        scope.launch {
            runCatching { source.delete(id, e) }
                .onSuccess { r ->
                    toast(
                        "deleted ${r.deleted}" +
                            if (r.failed > 0) " · ${r.failed} failed" else "",
                    )
                    browse.refresh()
                }
                .onFailure {
                    // Delete is the one Files verb behind the admin password,
                    // and this app has no admin session to present.
                    val status = (it as? ApiException)?.status
                    toast(
                        if (status == 401 || status == 403) {
                            "delete needs admin — use the dashboard, or ask an admin"
                        } else {
                            "delete failed: ${it.message}"
                        },
                    )
                }
            busy = null
        }
    }

    fun doMove(e: FileEntry, targetLibraryId: String, targetPath: String) {
        val id = selectedId ?: return
        busy = "moving"
        scope.launch {
            runCatching { source.move(id, e, targetLibraryId, targetPath) }
                .onSuccess { r ->
                    val parts = StringBuilder()
                    if (r.moved > 0) parts.append("moved ${r.moved} item${if (r.moved == 1) "" else "s"}")
                    if (r.skipped > 0) {
                        if (parts.isNotEmpty()) parts.append(" · ")
                        parts.append("${r.skipped} already there")
                    }
                    if (r.failed > 0) {
                        if (parts.isNotEmpty()) parts.append(" · ")
                        parts.append("${r.failed} failed")
                    }
                    if (r.reindexTriggered) parts.append(" · indexing…")
                    toast(parts.ifEmpty { StringBuilder("nothing to move") }.toString())
                    // Name the reason — "1 failed" alone is useless when the
                    // cause is a collision the user can act on.
                    r.firstFailure?.let { toast(it.take(120)) }
                    browse.refresh()
                }
                .onFailure { toast("move failed: ${it.message}") }
            busy = null
        }
    }

    fun doImport(e: FileEntry, target: FileLibrary) {
        val id = selectedId ?: return
        busy = "importing"
        toast("importing \"${e.name}\" → ${target.label}…")
        scope.launch {
            runCatching { source.import(id, e, target) }
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

    // A phone file → the server, through the same upload the server's own
    // Files screen uses. One file, one explicit tap; nothing syncs.
    fun doSendHome(e: FileEntry, target: FileLibrary, targetPath: String) {
        val id = selectedId ?: return
        val uri = source.contentUri(id, e.rel)
        if (uri == null) {
            toast("send failed: can't read \"${e.name}\"")
            return
        }
        busy = "sending"
        val where = target.label + if (targetPath.isEmpty()) "" else " / $targetPath"
        toast("sending \"${e.name}\" to $where…")
        scope.launch {
            runCatching { uploadFiles(ctx, app, target.id, targetPath, listOf(uri)) }
                .onSuccess { r ->
                    val parts = StringBuilder(
                        if (r.saved > 0) "sent \"${e.name}\" to $where" else "nothing was sent",
                    )
                    if (r.skipped > 0) parts.append(" · ${r.skipped} skipped")
                    if (r.reindexTriggered) parts.append(" · indexing…")
                    toast(parts.toString())
                }
                .onFailure { toast(com.domovoi.app.net.failureText("send", it)) }
            busy = null
        }
    }

    androidx.compose.foundation.layout.Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            PageHeader(
                "Files",
                source.subtitle,
                actions = {
                    if (source.onPhone) {
                        if (canWrite) {
                            IconButton(onClick = { filePicker.launch("*/*") }, enabled = busy == null) {
                                Icon(Icons.Outlined.UploadFile, "add files to this folder", tint = Domovoi.colors.fgMuted)
                            }
                        }
                        OutlinedButton(onClick = { folderPicker.launch(null) }) { Text("add folder") }
                    } else if (canWrite) {
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

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.weight(1f)) {
                    LibrarySelector(
                        libraries = libraries,
                        selectedId = selectedId,
                        // Picking the library already open re-lists it: the
                        // phone has no change events to say a folder moved on.
                        onSelect = {
                            if (it == selectedId && path.isEmpty()) browse.refresh()
                            selectedId = it
                            path = ""
                        },
                    )
                }
                if (source.onPhone && currentLib != null && PhoneFolders.uriOf(currentLib.id) != null) {
                    IconButton(onClick = { confirmForget = currentLib }) {
                        Icon(
                            Icons.Outlined.RemoveCircleOutline, "remove folder from list",
                            tint = Domovoi.colors.fgMuted,
                        )
                    }
                }
            }
            if (source.onPhone && phoneFolders.isEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "Add a folder to browse, edit and send its files from here.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            Spacer(Modifier.height(10.dp))

            if (blocked) {
                // Said once, above the list, rather than on every row: this
                // device can look but not touch, and here's who to ask.
                DomovoiCard(modifier = Modifier.fillMaxWidth(), padding = 12) {
                    Text(
                        (data?.blockedReason ?: "this device isn't allowed to change files") +
                            ". You can browse and download, but not upload, move or import. " +
                            "An admin lifts the block in the dashboard under Settings → Devices.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
                Spacer(Modifier.height(10.dp))
            }

            if (selectedId != null) {
                Breadcrumb(
                    label = currentLib?.label ?: "root",
                    segments = data?.breadcrumb ?: emptyList(),
                    onHome = { path = "" },
                    onSegment = { i ->
                        val crumbs = data?.breadcrumb ?: emptyList()
                        path = data?.crumbPaths?.getOrNull(i) ?: crumbs.take(i + 1).joinToString("/")
                    },
                )
                Spacer(Modifier.height(10.dp))
            }

            val errorTitle = if (source.onPhone) "couldn't open this folder" else "couldn't reach the server"
            when {
                libs.data == null && libs.loading -> LoadingState()
                libs.data == null && libs.error != null ->
                    ErrorState(libs.error ?: "request failed", libs.refresh, title = errorTitle)
                libraries.isEmpty() -> EmptyState(
                    "no libraries",
                    "no browsable libraries are configured on this server",
                )
                selectedId == null -> LoadingState()
                !permitted -> EmptyState(
                    "no access to photos",
                    "domovoi needs permission to read photos on this phone",
                    action = {
                        androidx.compose.material3.Button(onClick = { permission?.let { permissionAsk.launch(it) } }) {
                            Text("allow access")
                        }
                    },
                )
                data == null && browse.loading -> LoadingState()
                data == null && browse.error != null ->
                    ErrorState(browse.error ?: "request failed", browse.refresh, title = errorTitle)
                data != null && data.entries.isEmpty() -> EmptyState(
                    "empty folder",
                    when {
                        source.onPhone && canWrite -> "add files, or pick another folder"
                        source.onPhone -> "nothing here yet"
                        canWrite -> "upload files, or pick another library"
                        else -> "nothing here yet"
                    },
                )
                data != null && data.grid -> PhotoGrid(
                    entries = data.entries,
                    model = { e -> source.imageModel(selectedId ?: "", e) },
                    onOpen = { e -> onEntryPrimary(e) },
                    modifier = Modifier.fillMaxWidth().weight(1f),
                )
                data != null -> DomovoiCard(
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    padding = 0,
                ) {
                    LazyColumn(Modifier.fillMaxWidth()) {
                        items(data.entries, key = { it.rel }) { e ->
                            FileRow(
                                entry = e,
                                onOpen = { onEntryPrimary(e) },
                                onImport = if (isRemovable && !blocked) ({ importEntry = e }) else null,
                                onDownload = if (source.canDownload) ({ source.download(ctx, selectedId ?: "", e) }) else null,
                                onSendHome = if (canSendHome && !e.isDir) ({ sendHomeEntry = e }) else null,
                                // The web moves things by drag and drop; a phone
                                // gets a destination picker instead (FilesMoveSheet).
                                onMove = if (canWrite) ({ moveEntry = e }) else null,
                                onDelete = if (editable) ({ confirmDelete = e }) else null,
                            )
                        }
                    }
                }
                else -> LoadingState()
            }
        }

        textEditorRel?.let { rel ->
            TextEditorOverlay(source, selectedId ?: "", rel) {
                textEditorRel = null
                browse.refresh()
            }
        }
        sheetEditorRel?.let { rel ->
            SheetEditorOverlay(source, selectedId ?: "", rel) {
                sheetEditorRel = null
                browse.refresh()
            }
        }
        imageEntry?.let { e ->
            val id = selectedId ?: ""
            ImageViewerOverlay(
                name = e.name,
                model = source.imageModel(id, e),
                onSendHome = if (canSendHome) ({ sendHomeEntry = e }) else null,
                onOpenWith = {
                    if (!source.openFile(ctx, id, e)) toast("no app on this phone opens \"${e.name}\"")
                },
                onClose = { imageEntry = null },
            )
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

    confirmForget?.let { lib ->
        ConfirmDialog(
            title = "remove from list",
            body = "Take \"${lib.label}\" off this list? The folder and its files stay on the phone; " +
                "add it again any time.",
            confirmLabel = "remove",
            onConfirm = {
                PhoneFolders.uriOf(lib.id)?.let { removePhoneFolder(ctx, app.prefs, it) }
                toast("removed \"${lib.label}\" from the list")
                confirmForget = null
            },
            onDismiss = { confirmForget = null },
        )
    }

    moveEntry?.let { e ->
        FilesMoveSheet(
            entry = e,
            sourceLibraryId = selectedId ?: "",
            sourcePath = path,
            libraries = libraries,
            browse = { id, p -> source.browse(id, p) },
            onMove = { targetLibraryId, targetPath ->
                moveEntry = null
                doMove(e, targetLibraryId, targetPath)
            },
            onDismiss = { moveEntry = null },
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

    sendHomeEntry?.let { e ->
        SendHomeSheet(
            entry = e,
            onSend = { target, targetPath ->
                sendHomeEntry = null
                doSendHome(e, target, targetPath)
            },
            onDismiss = { sendHomeEntry = null },
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
    onOpen: () -> Unit,
    /** Each action is shown only when it is offered (non-null). */
    onImport: (() -> Unit)?,
    onDownload: (() -> Unit)?,
    onSendHome: (() -> Unit)?,
    onMove: (() -> Unit)?,
    onDelete: (() -> Unit)?,
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
            onImport?.let {
                IconButton(onClick = it) {
                    Icon(Icons.Filled.ContentCopy, "import into a library", tint = Domovoi.colors.fgMuted)
                }
            }
            onDownload?.let {
                IconButton(onClick = it) {
                    Icon(Icons.Outlined.Download, "download", tint = Domovoi.colors.fgMuted)
                }
            }
            onSendHome?.let {
                IconButton(onClick = it) {
                    Icon(Icons.Outlined.CloudUpload, "send to home", tint = Domovoi.colors.fgMuted)
                }
            }
            onMove?.let {
                IconButton(onClick = it) {
                    Icon(
                        Icons.AutoMirrored.Filled.DriveFileMove, "move to another folder",
                        tint = Domovoi.colors.fgMuted,
                    )
                }
            }
            onDelete?.let {
                IconButton(onClick = it) {
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
    "image" -> Icons.Outlined.Image
    else -> Icons.Filled.Folder
}

// Entry `kind` → row glyph.
private fun entryIcon(entry: FileEntry): ImageVector = when (entry.kind) {
    "folder" -> Icons.Filled.Folder
    "audio" -> Icons.Filled.MusicNote
    "doc-office" -> Icons.Outlined.Description
    "doc-text" -> Icons.AutoMirrored.Outlined.Article
    "image" -> Icons.Outlined.Image
    "video" -> Icons.Filled.Movie
    "pdf" -> Icons.Outlined.PictureAsPdf
    else -> Icons.Outlined.Description
}
