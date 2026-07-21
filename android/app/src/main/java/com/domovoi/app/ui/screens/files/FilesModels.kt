package com.domovoi.app.ui.screens.files

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Shapes returned by the web backend's generic Files surface
 * (web/backend/api/files.py). The client only ever sends a [FileLibrary.id]
 * plus a RELATIVE path; the absolute root is held server-side and never
 * serialized — see files_security.MediaLibrary.public().
 *
 * Everything is nullable-with-defaults: the backend evolves, and unknown keys
 * are ignored by DomovoiJson.
 */

/** One browsable root (core media dir, plugin library, or removable drive).
 *  Mirrors MediaLibrary.public(). */
@Serializable
data class FileLibrary(
    val id: String = "",
    val label: String = "",
    val kind: String = "",                  // "core" | "plugin" | "removable"
    val icon: String = "",                  // lucide glyph name (per-library)
    @SerialName("kind_icon") val kindIcon: String = "",
    val owner: String? = null,              // null (core) | plugin slug | drive device
    val importable: Boolean = false,        // may be an import DESTINATION
    val editable: Boolean = false,          // upload/delete allowed inside
    @SerialName("doc_editing") val docEditing: Boolean = false,
    @SerialName("reindex_kind") val reindexKind: String? = null,
    val present: Boolean = true,            // removable: false = ejected/absent
)

@Serializable
data class LibrariesResponse(
    val libraries: List<FileLibrary> = emptyList(),
)

/** One directory entry. `kind` ∈ folder|audio|doc-office|doc-text|image|pdf|other.
 *  `mtime` is unix SECONDS (a float), not an ISO string. `locked_by` is only
 *  meaningful for core:documents rows (editor lock). */
@Serializable
data class FileEntry(
    val name: String = "",
    val rel: String = "",
    @SerialName("is_dir") val isDir: Boolean = false,
    val size: Long? = null,
    val mtime: Double? = null,
    val kind: String = "other",
    @SerialName("locked_by") val lockedBy: String? = null,
)

@Serializable
data class FileBrowse(
    @SerialName("library_id") val libraryId: String = "",
    val path: String = "",                  // normalized rel of the listed dir ("" at root)
    val editable: Boolean = false,
    val importable: Boolean = false,
    @SerialName("doc_editing") val docEditing: Boolean = false,
    val breadcrumb: List<String> = emptyList(),
    val entries: List<FileEntry> = emptyList(),
)
