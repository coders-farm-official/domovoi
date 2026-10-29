package com.domovoi.app.ui.screens.files

import androidx.compose.material.icons.outlined.QuestionAnswer
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.assistant.DeviceTier
import com.domovoi.app.assistant.docs.DocExtract
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.alerts.CHAT_ROUTE
import com.domovoi.app.ui.screens.chat.ChatOpenRequest
import com.domovoi.app.ui.screens.chat.chatFromDocument
import com.domovoi.app.ui.screens.chat.docFailureText
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
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import com.domovoi.app.ui.components.PromptDialog
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material.icons.outlined.TableChart
import androidx.compose.material.icons.filled.Add
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Upload
import androidx.compose.material.icons.automirrored.filled.DriveFileMove
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.outlined.Article
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.outlined.Print
import androidx.compose.foundation.layout.widthIn
import com.domovoi.app.ui.screens.printing.PrintIo
import com.domovoi.app.ui.screens.printing.PrintOffer
import com.domovoi.app.ui.screens.printing.PrintSheet
import com.domovoi.app.ui.screens.printing.ScanSheet
import com.domovoi.app.ui.screens.printing.printOffer
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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
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
import com.domovoi.app.net.Page
import com.domovoi.app.net.PageRequest
import com.domovoi.app.net.SERVER_MAX_PAGE
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.LibraryRefreshBox
import com.domovoi.app.ui.components.LibraryRefreshButton
import com.domovoi.app.ui.components.rememberLibraryRefresh
import com.domovoi.app.ui.components.LoadMoreNearEnd
import com.domovoi.app.ui.components.PagedFooter
import com.domovoi.app.ui.components.rememberPager
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
import com.domovoi.app.ui.screens.documents.WebEditorHost
import com.domovoi.app.ui.screens.documents.WebEditorKind
import com.domovoi.app.ui.screens.documents.opensInDocEditor
import com.domovoi.app.ui.screens.documents.relFromEpochSec
import com.domovoi.app.ui.screens.local.UploadButtonState
import com.domovoi.app.ui.screens.local.UploadDest
import com.domovoi.app.ui.screens.local.UploadKind
import com.domovoi.app.ui.screens.local.UploadRefusedCard
import com.domovoi.app.ui.screens.local.UploadRowButton
import com.domovoi.app.ui.screens.local.fileUploadKey
import com.domovoi.app.ui.screens.local.rememberDeviceUploader
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
 * In the workspace it serves them again under the page's "your device"
 * switch (ui/screens/local/YourDevice.kt), where [uploadMode] turns each
 * row's send-to-home into Upload: progress on the row, a refusal said up
 * front when the server blocks this device.
 */
@Composable
fun FilesScreen() {
    val app = LocalApp.current
    val source = remember(app) { ServerFileSource(app) }
    FilesBrowser(source)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FilesBrowser(source: FileSource, uploadMode: Boolean = false) {
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

    // Saveable, so the folder survives the workspace going away and coming
    // back (a spell in the background, the system folder picker).
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    var path by rememberSaveable { mutableStateOf("") }

    // Auto-select the first library once loaded; re-select if the current one
    // vanishes (e.g. a removable drive was ejected between refreshes). Not
    // before the list has loaded: a restored folder must outlive the wait.
    LaunchedEffect(libs.data) {
        val loaded = libs.data ?: return@LaunchedEffect
        if (loaded.isEmpty()) {
            selectedId = null
        } else if (selectedId == null || loaded.none { it.id == selectedId }) {
            selectedId = loaded.first().id
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

    // The folder, a page at a time as it scrolls (net/Paging.kt). The name
    // filter is applied to the whole folder (the server's, or the phone
    // source's); a new folder, library or filter is a new list.
    var filter by rememberSaveable { mutableStateOf("") }
    var needle by remember { mutableStateOf(filter.trim()) }
    LaunchedEffect(filter) {
        kotlinx.coroutines.delay(250)
        needle = filter.trim()
    }
    /** The folder's own fields (breadcrumb, writable, grid…) from its latest page. */
    var meta by remember { mutableStateOf<FileBrowse?>(null) }
    val pager = rememberPager<FileEntry>(
        selectedId, path, source, permitted, needle,
        keyOf = { it.rel }, eventTypes = source.browseEvents,
    ) { _, req ->
        val id = selectedId
        if (id.isNullOrBlank() || !permitted) {
            Page(emptyList(), 0, false, null)
        } else {
            val fb = source.browsePage(id, path, req, needle)
            meta = fb.copy(entries = emptyList())
            Page(
                fb.entries,
                fb.total ?: if (fb.hasMore) null else req.offset + fb.entries.size,
                fb.hasMore,
                fb.nextCursor,
            )
        }
    }
    val listing by pager.state.collectAsState()
    // The header's rescan and the list's pull-to-refresh: the libraries, and
    // the folder on screen re-walked past the server's cached listing.
    val refresh = rememberLibraryRefresh {
        libs.refresh()
        pager.reload(fresh = true)
        pager.idle()
    }
    val data = meta?.takeIf { (it.libraryId == selectedId || !source.onPhone) && listing.loaded }
    val listState = rememberLazyListState()
    val gridState = rememberLazyGridState()
    LoadMoreNearEnd(listState, pager)
    LoadMoreNearEnd(gridState, pager)
    // Two different "no": the LIBRARY is read-only (editable=false), or THIS
    // DEVICE has been blocked by an admin (writable=false). Upload / move /
    // import need both. Delete is admin-gated server-side and this app can't
    // sign in as admin, so its button stays but the failure says so.
    val blocked = data?.writable == false
    val canWrite = editable && !blocked && libraryTakesFiles(selectedId)
    // "Send to home" is offered only while the server can actually be used.
    val serverUsable = LocalServerChoice.current.enabled
    val canSendHome = source.onPhone && serverUsable
    // "Your device" in the workspace: send-to-home is the row's Upload.
    val uploader = if (uploadMode) rememberDeviceUploader(UploadKind.File) else null

    var textEditorRel by remember { mutableStateOf<String?>(null) }
    var sheetEditorRel by remember { mutableStateOf<String?>(null) }
    var docEditorRel by remember { mutableStateOf<String?>(null) }
    var imageEntry by remember { mutableStateOf<FileEntry?>(null) }
    var confirmDelete by remember { mutableStateOf<FileEntry?>(null) }
    var moveEntry by remember { mutableStateOf<FileEntry?>(null) }
    var importEntry by remember { mutableStateOf<FileEntry?>(null) }
    var sendHomeEntry by remember { mutableStateOf<FileEntry?>(null) }
    var confirmForget by remember { mutableStateOf<FileLibrary?>(null) }
    var busy by remember { mutableStateOf<String?>(null) } // uploading | deleting | importing | sending
    // Documents → New (the dashboard's "+ New"): the kind being named, and
    // the dialog's refusal / in-flight state.
    var newKind by remember { mutableStateOf<NewDocKind?>(null) }
    var newError by remember { mutableStateOf<String?>(null) }
    var newWorking by remember { mutableStateOf(false) }
    var newMenu by remember { mutableStateOf(false) }

    // Printing and scanning through the home server: server files only. A
    // phone file keeps Android's own print path; nothing here touches it.
    val printers = if (source.onPhone || !serverUsable) {
        null
    } else {
        rememberApi("printers-for-files", source) { PrintIo(it.api).printers() }.data
    }
    var printEntry by remember { mutableStateOf<FileEntry?>(null) }
    var scanning by remember { mutableStateOf(false) }
    // A scan WRITES into Documents: not offered where this device may not write.
    val canScanHere = printers?.hasScanner == true && selectedId == DOCUMENTS_LIBRARY_ID && canWrite

    // "Ask about this document" (documents plan, D1): a phone file starts a
    // phone chat with it attached, a file in the household's Documents a
    // home-server chat. The places never blend, so each is offered only
    // where its own chat can run.
    val caps = LocalCapabilities.current
    val sharedScreen = LocalSharedScreen.current
    val phoneChats = source.onPhone && AssistantSupport.available && !sharedScreen &&
        app.assistant.state.collectAsState().value.tier != DeviceTier.Unsupported
    val serverChats = !source.onPhone && caps.aiEnabled && selectedId == DOCUMENTS_LIBRARY_ID
    fun canAsk(e: FileEntry): Boolean = askable(e, phoneChats, serverChats)
    fun ask(e: FileEntry) {
        val id = selectedId ?: return
        if (source.onPhone) {
            val uri = source.contentUri(id, e.rel)
            if (uri == null) {
                toast("can't read \"${e.name}\"")
                return
            }
            val conversation = app.assistant.startWithDocument(e.name) {
                ctx.contentResolver.openInputStream(uri) ?: throw java.io.IOException("can't read ${e.name}")
            }
            app.pendingChat.value = ChatOpenRequest("phone", conversation)
            app.pendingRoute.value = CHAT_ROUTE
        } else {
            busy = "asking"
            toast("reading \"${e.name}\" on the home server…")
            scope.launch {
                runCatching { chatFromDocument(app, e.rel) }
                    .onSuccess {
                        app.pendingChat.value = ChatOpenRequest("server", it.thread.id.toString())
                        app.pendingRoute.value = CHAT_ROUTE
                    }
                    .onFailure { toast(docFailureText("ask", it)) }
                busy = null
            }
        }
    }

    fun askSendHome(e: FileEntry) {
        if (uploader == null || uploader.gate()) sendHomeEntry = e
    }

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
                    // Why one was skipped, in the server's words: "Documents
                    // shows documents, spreadsheets and PDFs. Upload this in Other…"
                    r.firstSkipped?.let { parts.append(" · ").append(it) }
                    toast(parts.toString())
                    pager.reload()
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
            filter = ""
            path = e.rel
            return
        }
        val id = selectedId ?: return
        // In-app editing libraries (the server's Documents, every phone
        // folder): text/markdown in the text editor, .xlsx/.csv in the sheet
        // editor; everything else opens with the system viewer.
        val how = openWith(e.name, e.kind)
        // Documents on the server (formatted documents, markdown, Word) open
        // in the server's own document editor, in the app; a phone folder
        // has no server to edit on, so its files keep the on-phone editors.
        if (inAppEditing && !source.onPhone && id == DOCUMENTS_LIBRARY_ID && opensInDocEditor(e.name)) {
            docEditorRel = e.rel
            return
        }
        when {
            inAppEditing && how == OpenWith.SheetEditor -> sheetEditorRel = e.rel
            inAppEditing && how == OpenWith.TextEditor -> textEditorRel = e.rel
            source.viewsImagesInApp && e.kind == "image" -> imageEntry = e
            else -> if (!source.openFile(ctx, id, e, toast)) {
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
                    pager.reload()
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
                    pager.reload()
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
            toast("${if (uploader != null) "upload" else "send"} failed: can't read \"${e.name}\"")
            return
        }
        if (uploader != null) {
            // Streamed, with progress on the row, and it carries on if the
            // page is left (DeviceUpload.kt).
            uploader.upload(fileUploadKey(id, e.rel), uri, e.name, UploadDest.Library(target.id, target.label, targetPath))
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
            val headerActions: @Composable () -> Unit = {
                LibraryRefreshButton(refresh, "rescan this library")
                if (source.onPhone) {
                    if (canWrite) {
                        IconButton(onClick = { filePicker.launch("*/*") }, enabled = busy == null) {
                            Icon(Icons.Outlined.UploadFile, "add files to this folder", tint = Domovoi.colors.fgMuted)
                        }
                    }
                    if (libraryTakesFiles(selectedId)) {
                        OutlinedButton(onClick = { folderPicker.launch(null) }) { Text("add folder") }
                    }
                } else {
                    if (canScanHere) {
                        OutlinedButton(onClick = { scanning = true }) { Text("scan") }
                    }
                    // New, beside Upload, in the household's Documents only:
                    // shown wherever the library takes files; a blocked
                    // device is told why when it picks a kind.
                    if (selectedId == DOCUMENTS_LIBRARY_ID && inAppEditing && editable &&
                        libraryTakesFiles(selectedId)
                    ) {
                        Box {
                            OutlinedButton(
                                onClick = { newMenu = true },
                                enabled = busy == null,
                                contentPadding = PaddingValues(horizontal = 14.dp),
                            ) {
                                Icon(Icons.Filled.Add, null, Modifier.size(18.dp), tint = Domovoi.colors.brand)
                                Spacer(Modifier.width(4.dp))
                                Text("new")
                            }
                            DropdownMenu(expanded = newMenu, onDismissRequest = { newMenu = false }) {
                                NewDocKind.entries.forEach { k ->
                                    DropdownMenuItem(
                                        leadingIcon = {
                                            Icon(
                                                when (k) {
                                                    NewDocKind.Document -> Icons.Outlined.Description
                                                    NewDocKind.Spreadsheet -> Icons.Outlined.TableChart
                                                    NewDocKind.Text -> Icons.AutoMirrored.Outlined.Article
                                                },
                                                null, tint = Domovoi.colors.fgMuted,
                                            )
                                        },
                                        text = {
                                            Column {
                                                Text(k.label)
                                                Text(
                                                    k.hint, style = MaterialTheme.typography.bodySmall,
                                                    color = Domovoi.colors.fgFaint,
                                                )
                                            }
                                        },
                                        onClick = {
                                            newMenu = false
                                            if (blocked) {
                                                toast(data?.blockedReason ?: "this device is blocked from changing files")
                                            } else {
                                                newError = null
                                                newKind = k
                                            }
                                        },
                                    )
                                }
                            }
                        }
                    }
                    if (canWrite) {
                        OutlinedButton(
                            onClick = { filePicker.launch("*/*") },
                            enabled = busy == null,
                        ) {
                            Text(if (busy == "uploading") "uploading…" else "upload")
                        }
                    }
                }
            }
            // On a phone the buttons get their own row under the title: beside
            // it they squeezed the subtitle into a word-per-line column at
            // 320 dp (a Fold's cover screen), and New + Upload need the room.
            androidx.compose.foundation.layout.BoxWithConstraints(Modifier.fillMaxWidth()) {
                if (maxWidth < 480.dp) {
                    Column {
                        PageHeader("Files", source.subtitle)
                        if (!com.domovoi.app.ui.shell.keyboardCrowdsTheWindow()) {
                            Spacer(Modifier.height(8.dp))
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                                verticalAlignment = Alignment.CenterVertically,
                            ) { headerActions() }
                        }
                    }
                } else {
                    PageHeader("Files", source.subtitle, actions = headerActions)
                }
            }
            Spacer(Modifier.height(12.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.weight(1f)) {
                    LibrarySelector(
                        libraries = libraries,
                        selectedId = selectedId,
                        // Picking the library already open re-lists it: the
                        // phone has no change events to say a folder moved on.
                        onSelect = {
                            // ...and past the server's cached listing too.
                            if (it == selectedId && path.isEmpty()) pager.reload(fresh = true)
                            if (it != selectedId) filter = ""
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
                            "An admin lifts the block in the dashboard under Settings → Phones & browsers.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
                Spacer(Modifier.height(10.dp))
            }
            uploader?.refused?.let {
                UploadRefusedCard(it)
                Spacer(Modifier.height(10.dp))
            }

            if (selectedId != null) {
                Breadcrumb(
                    label = currentLib?.label ?: "root",
                    segments = data?.breadcrumb ?: emptyList(),
                    onHome = { filter = ""; path = "" },
                    onSegment = { i ->
                        val crumbs = data?.breadcrumb ?: emptyList()
                        filter = ""
                        path = data?.crumbPaths?.getOrNull(i) ?: crumbs.take(i + 1).joinToString("/")
                    },
                )
                Spacer(Modifier.height(8.dp))
                if (permitted) {
                    OutlinedTextField(
                        value = filter,
                        onValueChange = { filter = it },
                        placeholder = { Text("filter this folder…", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(8.dp))
                }
            }

            val errorTitle = if (source.onPhone) "couldn't open this folder" else "couldn't reach the server"
            LibraryRefreshBox(refresh, Modifier.fillMaxWidth().weight(1f)) { when {
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
                listing.items.isEmpty() && listing.loading -> LoadingState()
                listing.items.isEmpty() && listing.error != null ->
                    ErrorState(listing.error ?: "request failed", pager::retry, title = errorTitle)
                data != null && listing.items.isEmpty() && needle.isNotEmpty() -> EmptyState(
                    "nothing matches",
                    "filter = \"$needle\"",
                    action = {
                        androidx.compose.material3.TextButton(onClick = { filter = "" }) {
                            Text("clear filter", color = Domovoi.colors.brand)
                        }
                    },
                )
                data != null && listing.items.isEmpty() -> EmptyState(
                    "empty folder",
                    when {
                        source.onPhone && canWrite -> "add files, or pick another folder"
                        source.onPhone -> "nothing here yet"
                        canWrite -> "upload files, or pick another library"
                        else -> "nothing here yet"
                    },
                )
                data != null && data.grid -> PhotoGrid(
                    entries = listing.items,
                    model = { e -> source.imageModel(selectedId ?: "", e) },
                    onOpen = { e -> onEntryPrimary(e) },
                    modifier = Modifier.fillMaxSize(),
                    state = gridState,
                    footer = { PagedFooter(listing, "photos", onRetry = pager::retry, onLoadMore = pager::loadMore) },
                )
                data != null -> DomovoiCard(
                    modifier = Modifier.fillMaxSize(),
                    padding = 0,
                ) {
                    LazyColumn(Modifier.fillMaxWidth(), state = listState) {
                        items(listing.items, key = { it.rel }) { e ->
                            FileRow(
                                entry = e,
                                primaryLabel = primaryVerb(
                                    e.name, e.kind, e.isDir,
                                    editsInApp = inAppEditing,
                                    docEditor = inAppEditing && !source.onPhone &&
                                        selectedId == DOCUMENTS_LIBRARY_ID && opensInDocEditor(e.name),
                                    downloadOnly = !source.onPhone && selectedId == OTHER_LIBRARY_ID,
                                    opensByDownload = !source.onPhone && selectedId != DOCUMENTS_LIBRARY_ID,
                                ),
                                onOpen = { onEntryPrimary(e) },
                                onImport = if (isRemovable && !blocked) ({ importEntry = e }) else null,
                                onDownload = if (source.canDownload) ({ source.download(ctx, selectedId ?: "", e, toast) }) else null,
                                onSendHome = if (canSendHome && !e.isDir) ({ askSendHome(e) }) else null,
                                onAsk = if (canAsk(e)) ({ ask(e) }) else null,
                                upload = uploader?.state(fileUploadKey(selectedId ?: "", e.rel)),
                                // The web moves things by drag and drop; a phone
                                // gets a destination picker instead (FilesMoveSheet).
                                onMove = if (canWrite) ({ moveEntry = e }) else null,
                                onDelete = if (editable) ({ confirmDelete = e }) else null,
                                // Other only downloads: no printing from it.
                                print = if (selectedId == OTHER_LIBRARY_ID) PrintOffer.None
                                else printOffer(e.name, e.isDir, printers),
                                onPrint = { printEntry = e },
                            )
                        }
                        item(key = "files-footer") {
                            PagedFooter(listing, "items", onRetry = pager::retry, onLoadMore = pager::loadMore)
                        }
                    }
                }
                else -> LoadingState()
            } }
        }

        textEditorRel?.let { rel ->
            TextEditorOverlay(source, selectedId ?: "", rel) {
                textEditorRel = null
                pager.reload()
            }
        }
        docEditorRel?.let { rel ->
            WebEditorHost(app, rel, WebEditorKind.Document) {
                docEditorRel = null
                pager.reload()
            }
        }
        sheetEditorRel?.let { rel ->
            val close = {
                sheetEditorRel = null
                pager.reload()
            }
            // The server's Documents: the dashboard's own sheet editor
            // (formulas, formatting, sheets, autosave) in a WebView. A
            // phone folder has no server behind it: the plain grid editor.
            if (!source.onPhone && selectedId == DOCUMENTS_LIBRARY_ID) {
                WebEditorHost(app, rel, WebEditorKind.Sheet, close)
            } else {
                SheetEditorOverlay(source, selectedId ?: "", rel, close)
            }
        }
        imageEntry?.let { e ->
            val id = selectedId ?: ""
            ImageViewerOverlay(
                name = e.name,
                model = source.imageModel(id, e),
                onSendHome = if (canSendHome) ({ askSendHome(e) }) else null,
                sendLabel = if (uploader != null) "upload" else "send to home",
                onOpenWith = {
                    if (!source.openFile(ctx, id, e, toast)) toast("no app on this phone opens \"${e.name}\"")
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
                "Delete \"${displayName(e.name)}\"? This can't be undone."
            },
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { doDelete(e) },
            onDismiss = { confirmDelete = null },
        )
    }

    newKind?.let { k ->
        PromptDialog(
            title = k.prompt,
            placeholder = k.placeholder,
            note = if (path.isNotEmpty()) "It goes at the top of Documents, as on the dashboard." else null,
            error = newError,
            working = newWorking,
            dismissOnConfirm = false,
            onConfirm = { name ->
                newWorking = true
                newError = null
                scope.launch {
                    runCatching { createDocument(app, name, k) }
                        .onSuccess { row ->
                            newKind = null
                            pager.reload()
                            // Opens as tapping its row would: the document
                            // editor, the sheet editor, the text editor.
                            onEntryPrimary(row.asEntry())
                        }
                        .onFailure { newError = createFailureText(it) }
                    newWorking = false
                }
            },
            onDismiss = { newKind = null; newError = null },
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
            // A destination's folders: one page of up to 200 (the sheet says when there are more).
            browse = { id, p -> source.browsePage(id, p, PageRequest(null, 0, SERVER_MAX_PAGE), foldersOnly = true) },
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
            asUpload = uploader != null,
        )
    }

    val printList = printers
    printEntry?.let { e ->
        if (printList != null && selectedId != null) {
            PrintSheet(
                libraryId = selectedId ?: "",
                path = e.rel,
                fileName = e.name,
                printers = printList,
                onDismiss = { printEntry = null },
            )
        }
    }
    if (scanning && printList != null) {
        ScanSheet(
            printers = printList,
            onOpen = { s ->
                val rel = s.path ?: return@ScanSheet
                val name = s.name ?: rel.substringAfterLast('/')
                scanning = false
                if (!source.openFile(ctx, s.libraryId ?: DOCUMENTS_LIBRARY_ID, FileEntry(name = name, rel = rel, kind = fileKind(name, false)), toast)) {
                    toast("no app on this phone opens \"$name\"")
                }
            },
            onSaved = { pager.reload() },
            onDismiss = { scanning = false },
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
    /** What tapping the row does ("Open", "Edit", "View", "Play", "Download"): [primaryVerb]. */
    primaryLabel: String,
    onOpen: () -> Unit,
    /** Each action is offered (in the ⋮ menu) only when it is non-null. */
    onImport: (() -> Unit)?,
    onDownload: (() -> Unit)?,
    onSendHome: (() -> Unit)?,
    onMove: (() -> Unit)?,
    onDelete: (() -> Unit)?,
    /** Set in "your device" mode: [onSendHome] is the Upload item, in this state. */
    upload: UploadButtonState? = null,
    /** The server's Print for this file: a menu item, a "save as PDF first" line, or nothing. */
    print: PrintOffer = PrintOffer.None,
    onPrint: () -> Unit = {},
    /** "Ask about this document": a chat with it attached. */
    onAsk: (() -> Unit)? = null,
) {
    // The row is the primary action (as Music and Videos rows play on a
    // tap); every other verb lives in the ⋮ menu, so the name gets the
    // width even on a 320 dp cover screen. Delete stays last, behind its
    // confirmation dialog.
    val name = displayName(entry.name, entry.isDir)
    Column {
        Row(
            Modifier.fillMaxWidth()
                .heightIn(min = 56.dp)
                .clickable(onClickLabel = "${primaryLabel.lowercase()} $name", onClick = onOpen)
                .padding(start = 14.dp, end = 2.dp, top = 6.dp, bottom = 6.dp),
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
                    name,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    if (entry.isDir) {
                        "folder · ${relFromEpochSec(entry.mtime)}"
                    } else {
                        (if (isFormattedDocument(entry.name)) "Document · " else "") +
                            "${fmtBytes(entry.size)} · ${relFromEpochSec(entry.mtime)}"
                    },
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgFaint,
                    maxLines = 1,
                    softWrap = false,
                    overflow = TextOverflow.Ellipsis,
                )
                entry.lockedBy?.let {
                    Spacer(Modifier.height(2.dp))
                    Pill("editing in $it", Tone.Warn)
                }
            }
            // An upload in flight shows its progress in the row (a status,
            // not a button); starting one is in the menu.
            if (upload is UploadButtonState.Busy && onSendHome != null) UploadRowButton(upload, onSendHome)
            var menu by remember { mutableStateOf(false) }
            Box {
                IconButton(onClick = { menu = true }) {
                    Icon(Icons.Filled.MoreVert, "more for $name", tint = Domovoi.colors.fgMuted)
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    @Composable
                    fun item(label: String, icon: ImageVector, enabled: Boolean = true, danger: Boolean = false, onClick: () -> Unit) =
                        DropdownMenuItem(
                            leadingIcon = {
                                Icon(icon, null, tint = if (danger) Domovoi.colors.err else Domovoi.colors.fgMuted)
                            },
                            text = { Text(label, color = if (danger) Domovoi.colors.err else Color.Unspecified) },
                            enabled = enabled,
                            onClick = { menu = false; onClick() },
                        )
                    item(primaryLabel, primaryIcon(primaryLabel), onClick = onOpen)
                    onAsk?.let { item("Ask in chat", Icons.Outlined.QuestionAnswer, onClick = it) }
                    if (primaryLabel != "Download") onDownload?.let { item("Download", Icons.Outlined.Download, onClick = it) }
                    onSendHome?.let {
                        if (upload != null) {
                            item(
                                if (upload is UploadButtonState.Busy) "Uploading…" else "Upload",
                                Icons.Outlined.Upload,
                                enabled = upload is UploadButtonState.Ready || upload is UploadButtonState.Refused,
                                onClick = it,
                            )
                        } else {
                            item("Send to home", Icons.Outlined.CloudUpload, onClick = it)
                        }
                    }
                    onImport?.let { item("Import into a library", Icons.Filled.ContentCopy, onClick = it) }
                    onMove?.let { item("Move", Icons.AutoMirrored.Filled.DriveFileMove, onClick = it) }
                    when (print) {
                        PrintOffer.Print -> item("Print", Icons.Outlined.Print, onClick = onPrint)
                        is PrintOffer.PdfFirst -> Text(
                            print.message,
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp).widthIn(max = 280.dp),
                        )
                        PrintOffer.None -> Unit
                    }
                    onDelete?.let { item("Delete…", Icons.Outlined.Delete, danger = true, onClick = it) }
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
    }
}

private fun primaryIcon(verb: String): ImageVector = when (verb) {
    "Edit" -> Icons.Outlined.Edit
    "Play" -> Icons.Filled.PlayArrow
    "Download" -> Icons.Outlined.Download
    "View" -> Icons.Outlined.Image
    else -> Icons.AutoMirrored.Outlined.OpenInNew
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
private fun entryIcon(entry: FileEntry): ImageVector = if (isFormattedDocument(entry.name, entry.isDir)) {
    Icons.Outlined.Description
} else when (entry.kind) {
    "folder" -> Icons.Filled.Folder
    "audio" -> Icons.Filled.MusicNote
    "doc-office" -> Icons.Outlined.Description
    "doc-text" -> Icons.AutoMirrored.Outlined.Article
    "image" -> Icons.Outlined.Image
    "video" -> Icons.Filled.Movie
    "pdf" -> Icons.Outlined.PictureAsPdf
    else -> Icons.Outlined.Description
}

/** Whether a file row offers "Ask about this document": a file of a kind
 *  Chat reads, and a chat in its own place can run ([phoneChats] for a
 *  phone file, [serverChats] for one in the household's Documents). */
internal fun askable(e: FileEntry, phoneChats: Boolean, serverChats: Boolean): Boolean =
    !e.isDir && DocExtract.kindFor(e.name) != null && (phoneChats || serverChats)
