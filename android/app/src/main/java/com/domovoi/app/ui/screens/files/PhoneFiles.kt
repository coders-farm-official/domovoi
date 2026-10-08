package com.domovoi.app.ui.screens.files

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import com.domovoi.app.LocalApp
import com.domovoi.app.data.PhoneFolder
import com.domovoi.app.data.PhoneFolders
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.decode
import com.domovoi.app.net.filesBrowsePath
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.theme.Domovoi

/**
 * The local shell's Files tab: the same browser as the server's Files
 * screen, over the phone's own folders and photos ([PhoneFileSource]).
 */
@Composable
fun LocalFilesScreen() {
    val app = LocalApp.current
    val ctx = LocalContext.current
    val source = remember(app) { PhoneFileSource(ctx, app.prefs) }
    FilesBrowser(source)
}

// ---------------------------------------------------------------------------
// The folder list: a persisted grant per folder, and the list in Prefs.
// ---------------------------------------------------------------------------

private const val RW = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION

/** Keep access to a folder the picker returned and add it to the list. Null when Android won't persist it. */
internal fun addPhoneFolder(context: Context, prefs: Prefs, tree: Uri): PhoneFolder? {
    val resolver = context.contentResolver
    val kept = runCatching { resolver.takePersistableUriPermission(tree, RW) }.isSuccess ||
        runCatching { resolver.takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION) }.isSuccess
    if (!kept) return null
    val name = runCatching {
        val doc = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        resolver.query(doc, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    }.getOrNull() ?: tree.lastPathSegment?.substringAfterLast(':')?.substringAfterLast('/')?.ifBlank { null } ?: "folder"
    val folder = PhoneFolder(tree.toString(), name)
    prefs.setPhoneFolders(PhoneFolders.withFolder(prefs.phoneFolders.value, folder))
    return folder
}

/** Take a folder off the list and give its grant back. The files are not touched. */
internal fun removePhoneFolder(context: Context, prefs: Prefs, treeUri: String) {
    val uri = Uri.parse(treeUri)
    val resolver = context.contentResolver
    runCatching { resolver.releasePersistableUriPermission(uri, RW) }
        .onFailure { runCatching { resolver.releasePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) } }
    prefs.setPhoneFolders(PhoneFolders.without(prefs.phoneFolders.value, treeUri))
}

// ---------------------------------------------------------------------------
// Photos: an album as a grid, and the in-app viewer.
// ---------------------------------------------------------------------------

@Composable
internal fun PhotoGrid(
    entries: List<FileEntry>,
    model: (FileEntry) -> Any?,
    onOpen: (FileEntry) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(104.dp),
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        items(entries, key = { it.rel }) { e ->
            Box(
                Modifier.aspectRatio(1f)
                    .clip(RoundedCornerShape(6.dp))
                    .background(Domovoi.colors.sunken)
                    .clickable { onOpen(e) },
                contentAlignment = Alignment.Center,
            ) {
                SubcomposeAsyncImage(
                    model = model(e),
                    contentDescription = e.name,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                    error = { Icon(Icons.Outlined.Image, null, tint = Domovoi.colors.fgFaint) },
                )
            }
        }
    }
}

/**
 * Full-screen image viewer: pinch to zoom, drag to pan, double-tap to zoom
 * in or back out. "Open in another app" and, while the server can be used,
 * "send to home" sit in its bar.
 */
@Composable
internal fun ImageViewerOverlay(
    name: String,
    model: Any?,
    onSendHome: (() -> Unit)?,
    onOpenWith: () -> Unit,
    onClose: () -> Unit,
) {
    BackHandler { onClose() }
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth()
                    .background(Domovoi.colors.card)
                    .padding(horizontal = 14.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Icon(Icons.Outlined.Image, contentDescription = null, tint = Domovoi.colors.fgMuted)
                Text(
                    name,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                onSendHome?.let {
                    IconButton(onClick = it) {
                        Icon(Icons.Outlined.CloudUpload, "send to home", tint = Domovoi.colors.fgMuted)
                    }
                }
                IconButton(onClick = onOpenWith) {
                    Icon(Icons.AutoMirrored.Outlined.OpenInNew, "open in another app", tint = Domovoi.colors.fgMuted)
                }
                IconButton(onClick = onClose) {
                    Icon(Icons.Outlined.Close, "close", tint = Domovoi.colors.fgMuted)
                }
            }
            HorizontalDivider(color = Domovoi.colors.border)
            Box(
                Modifier.fillMaxSize()
                    .pointerInput(Unit) {
                        detectTransformGestures { _, pan, zoom, _ ->
                            scale = (scale * zoom).coerceIn(1f, 6f)
                            offset = if (scale == 1f) Offset.Zero else offset + pan
                        }
                    }
                    .pointerInput(Unit) {
                        detectTapGestures(onDoubleTap = {
                            if (scale > 1f) { scale = 1f; offset = Offset.Zero } else scale = 2.5f
                        })
                    },
                contentAlignment = Alignment.Center,
            ) {
                SubcomposeAsyncImage(
                    model = model,
                    contentDescription = name,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize().graphicsLayer {
                        scaleX = scale
                        scaleY = scale
                        translationX = offset.x
                        translationY = offset.y
                    },
                    loading = { LoadingState() },
                    error = {
                        Text(
                            "This image can't be shown here.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(24.dp),
                        )
                    },
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Send to home: pick a library and folder on the server, then one upload.
// ---------------------------------------------------------------------------

/**
 * Where a phone file lands on the server by default: Pictures for an image,
 * Music for audio, Documents for the rest — whichever of those the server
 * offers as editable, else its first editable library. The sheet lets the
 * person pick any editable library and folder.
 */
internal fun defaultHomeLibrary(entry: FileEntry, libraries: List<FileLibrary>): String? {
    val editable = libraries.filter { it.editable }
    val wanted = when (entry.kind) {
        "image" -> "core:pictures"
        "audio" -> "core:music"
        else -> DOCUMENTS_LIBRARY_ID
    }
    return editable.firstOrNull { it.id == wanted }?.id
        ?: editable.firstOrNull { it.id == DOCUMENTS_LIBRARY_ID }?.id
        ?: editable.firstOrNull()?.id
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun SendHomeSheet(
    entry: FileEntry,
    onSend: (target: FileLibrary, targetPath: String) -> Unit,
    onDismiss: () -> Unit,
) {
    val libs = rememberApi("send-home-libraries") {
        it.api.get("/api/files/libraries").decode<LibrariesResponse>().libraries
    }
    val app = LocalApp.current
    val libraries = libs.data
    if (libraries == null) {
        ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Domovoi.colors.raised) {
            Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp)) {
                Text("send \"${entry.name}\" to…", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                if (libs.error != null) ErrorState(libs.error, libs.refresh) else LoadingState()
            }
        }
        return
    }
    FilesMoveSheet(
        entry = entry,
        sourceLibraryId = "",
        sourcePath = "",
        libraries = libraries,
        browse = { id, p -> app.api.get(filesBrowsePath(id, p, app.prefs.deviceId)).decode<FileBrowse>() },
        onMove = { libId, p -> libraries.firstOrNull { it.id == libId }?.let { onSend(it, p) } },
        onDismiss = onDismiss,
        title = "send \"${entry.name}\" to…",
        actionLabel = "send here",
        verb = "send",
        initialLibraryId = defaultHomeLibrary(entry, libraries),
    )
}
