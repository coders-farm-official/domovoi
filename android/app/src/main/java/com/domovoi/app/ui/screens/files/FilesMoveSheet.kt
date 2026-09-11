package com.domovoi.app.ui.screens.files

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.DriveFileMove
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.net.decode
import com.domovoi.app.net.filesBrowsePath
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi

/**
 * "Move to…" destination picker — the phone's answer to the web's drag and
 * drop, which has no sensible touch equivalent inside a scrolling list.
 *
 * Pick an editable library, walk into the folder you want, tap "move here".
 * Only editable libraries are offered: a move DELETES from the source, so a
 * read-only root (a removable drive, a read-only plugin library) can only ever
 * be a destination — and for removables, /import already covers copying.
 *
 * The server still enforces everything (containment, folder-into-itself, name
 * collisions). This sheet only keeps the obvious mistakes off the wire: the
 * folder being moved is not offered as its own destination, and neither is the
 * folder the selection already sits in.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FilesMoveSheet(
    entry: FileEntry,
    sourceLibraryId: String,
    sourcePath: String,
    libraries: List<FileLibrary>,
    onMove: (targetLibraryId: String, targetPath: String) -> Unit,
    onDismiss: () -> Unit,
) {
    val app = LocalApp.current
    val targets = libraries.filter { it.editable }

    var libId by remember { mutableStateOf(sourceLibraryId.takeIf { id -> targets.any { it.id == id } } ?: targets.firstOrNull()?.id) }
    var path by remember { mutableStateOf(if (libId == sourceLibraryId) sourcePath else "") }
    LaunchedEffect(libId) { if (libId != sourceLibraryId) path = "" }

    var entries by remember { mutableStateOf<List<FileEntry>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(libId, path) {
        val id = libId ?: return@LaunchedEffect
        loading = true
        error = null
        runCatching { app.api.get(filesBrowsePath(id, path)).decode<FileBrowse>() }
            .onSuccess { entries = it.entries.filter { e -> e.isDir } }
            .onFailure { error = it.message ?: "couldn't open folder"; entries = emptyList() }
        loading = false
    }

    val sameLibrary = libId == sourceLibraryId
    // Moving into the folder it's already in is a no-op the server reports as
    // "skipped"; don't offer it as the action.
    val alreadyHere = sameLibrary && path == sourcePath
    // A folder can't contain itself. The server refuses this too.
    val intoItself = sameLibrary && entry.isDir &&
        (path == entry.rel || path.startsWith(entry.rel + "/"))
    val canMoveHere = libId != null && !alreadyHere && !intoItself

    val segments = path.split('/').filter { it.isNotBlank() }

    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Domovoi.colors.raised) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "move \"${entry.name}\" to…",
                style = MaterialTheme.typography.titleMedium,
                color = Domovoi.colors.fg,
            )

            if (targets.isEmpty()) {
                Text(
                    "no editable libraries — nothing writable to move into.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                )
                return@Column
            }

            SectionLabel("library")
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                targets.forEach { lib ->
                    FilterChip(
                        selected = libId == lib.id,
                        onClick = { libId = lib.id },
                        label = { Text(lib.label, style = MaterialTheme.typography.labelMedium) },
                    )
                }
            }

            // Current destination + a way back up.
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (segments.isNotEmpty()) {
                    OutlinedButton(onClick = { path = segments.dropLast(1).joinToString("/") }) {
                        Icon(
                            Icons.Filled.ArrowUpward, contentDescription = "up one level",
                            modifier = Modifier.size(14.dp),
                        )
                    }
                }
                Text(
                    (targets.firstOrNull { it.id == libId }?.label ?: "—") +
                        if (segments.isEmpty()) "" else " / " + segments.joinToString(" / "),
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
            }

            HorizontalDivider(color = Domovoi.colors.borderSoft)

            when {
                loading && entries.isEmpty() -> LoadingState()
                error != null -> Text(
                    error!!,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.err,
                )
                entries.isEmpty() -> EmptyState(
                    "no subfolders here",
                    "move into this folder, or pick another library",
                )
                else -> LazyColumn(Modifier.fillMaxWidth().heightIn(max = 260.dp)) {
                    items(entries, key = { it.rel }) { dir ->
                        // The folder being moved is not a place to move it to.
                        val isSelf = sameLibrary && entry.isDir && dir.rel == entry.rel
                        Row(
                            Modifier.fillMaxWidth()
                                .clickable(enabled = !isSelf) { path = dir.rel }
                                .padding(vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            Icon(
                                Icons.Filled.Folder, contentDescription = null,
                                tint = if (isSelf) Domovoi.colors.fgFaint else Domovoi.colors.brand,
                                modifier = Modifier.size(20.dp),
                            )
                            Text(
                                dir.name + if (isSelf) "  (this one)" else "",
                                style = MaterialTheme.typography.titleSmall,
                                color = if (isSelf) Domovoi.colors.fgFaint else Domovoi.colors.fg,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.size(2.dp))
            Button(
                onClick = { libId?.let { onMove(it, path) } },
                enabled = canMoveHere,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(
                    Icons.AutoMirrored.Filled.DriveFileMove, contentDescription = null,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.size(6.dp))
                Text(
                    when {
                        alreadyHere -> "already in this folder"
                        intoItself -> "can't move a folder into itself"
                        else -> "move here"
                    },
                )
            }
        }
    }
}
