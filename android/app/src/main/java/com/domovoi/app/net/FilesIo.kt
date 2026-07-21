package com.domovoi.app.net

import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import com.domovoi.app.AppContainer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * Client for the generic multi-library Files surface
 * (web/backend/api/files.py, prefix `/api/files`). The Android analog of the
 * web files.jsx helpers: browse/download/upload/delete/import.
 *
 * The client only ever sends a `library_id` + a RELATIVE `path`; the absolute
 * root is server-side. Query values are %-encoded via [Uri.encode] so a
 * `library_id` like "core:music" and a `path` with "/" survive intact.
 */

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

/** GET /api/files/browse for a library + rel dir (both %-encoded as query values). */
internal fun filesBrowsePath(libraryId: String, path: String): String =
    "/api/files/browse?library_id=" + Uri.encode(libraryId) + "&path=" + Uri.encode(path)

/** Absolute /api/files/download URL for a file or directory (dir → server zip). */
internal fun filesDownloadUrl(app: AppContainer, libraryId: String, rel: String): String =
    app.api.absolute("/api/files/download?library_id=" + Uri.encode(libraryId) + "&path=" + Uri.encode(rel))

/** Open /api/files/download with the system viewer/downloader (attachment serve). */
internal fun openFileDownload(context: Context, app: AppContainer, libraryId: String, rel: String) {
    runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(filesDownloadUrl(app, libraryId, rel))),
        )
    }
}

// ---------------------------------------------------------------------------
// Result holders
// ---------------------------------------------------------------------------

internal data class UploadResult(val saved: Int, val skipped: Int, val reindexTriggered: Boolean)
internal data class DeleteResult(val deleted: Int, val failed: Int, val reindexTriggered: Boolean)
internal data class ImportResult(val copied: Int, val skipped: Int, val reindexTriggered: Boolean)

// ---------------------------------------------------------------------------
// Upload — content Uris → multipart into the current dir of an editable library.
// Form fields: library_id, path, files[] (part name "files", matching the
// FastAPI `files: list[UploadFile] = File(...)` param in files.py).
// ---------------------------------------------------------------------------

internal suspend fun uploadFiles(
    context: Context,
    app: AppContainer,
    libraryId: String,
    path: String,
    uris: List<Uri>,
): UploadResult = withContext(Dispatchers.IO) {
    val resolver = context.contentResolver
    val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
    builder.addFormDataPart("library_id", libraryId)
    builder.addFormDataPart("path", path)
    var added = 0
    uris.forEach { uri ->
        val bytes = runCatching {
            resolver.openInputStream(uri)?.use { it.readBytes() }
        }.getOrNull() ?: return@forEach
        val name = displayName(resolver, uri) ?: uri.lastPathSegment ?: "file"
        val mime = resolver.getType(uri) ?: "application/octet-stream"
        builder.addFormDataPart("files", name, bytes.toRequestBody(mime.toMediaTypeOrNull()))
        added++
    }
    if (added == 0) throw IOException("nothing readable to upload")
    val res = app.api.upload("/api/files/upload", builder.build())
    val obj = res as? JsonObject ?: JsonObject(emptyMap())
    UploadResult(
        saved = (obj["saved"] as? JsonArray)?.size ?: 0,
        skipped = (obj["skipped"] as? JsonArray)?.size ?: 0,
        reindexTriggered = obj["reindex_triggered"]?.jsonPrimitive?.booleanOrNull ?: false,
    )
}

private fun displayName(resolver: ContentResolver, uri: Uri): String? = runCatching {
    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
    }
}.getOrNull()

// ---------------------------------------------------------------------------
// Delete — files always; folders only with recursive=true (bounded server-side).
// ---------------------------------------------------------------------------

internal suspend fun deleteFiles(
    app: AppContainer,
    libraryId: String,
    paths: List<String>,
    recursive: Boolean,
): DeleteResult {
    val res = app.api.post(
        "/api/files/delete",
        buildJsonObject {
            put("library_id", libraryId)
            put("paths", buildJsonArray { paths.forEach { add(it) } })
            put("recursive", recursive)
        },
    )
    val obj = res as? JsonObject ?: JsonObject(emptyMap())
    return DeleteResult(
        deleted = (obj["deleted"] as? JsonArray)?.size ?: 0,
        failed = (obj["failed"] as? JsonArray)?.size ?: 0,
        reindexTriggered = obj["reindex_triggered"]?.jsonPrimitive?.booleanOrNull ?: false,
    )
}

// ---------------------------------------------------------------------------
// Import — copy a file/dir from a removable source into an importable library.
// Server-side copy (the phone never streams the bytes).
// ---------------------------------------------------------------------------

internal suspend fun importFile(
    app: AppContainer,
    sourceLibraryId: String,
    sourcePath: String,
    targetLibraryId: String,
    targetPath: String,
): ImportResult {
    val res = app.api.post(
        "/api/files/import",
        buildJsonObject {
            put("source_library_id", sourceLibraryId)
            put("source_path", sourcePath)
            put("target_library_id", targetLibraryId)
            put("target_path", targetPath)
        },
    )
    val obj = res as? JsonObject ?: JsonObject(emptyMap())
    return ImportResult(
        copied = (obj["copied"] as? JsonArray)?.size ?: 0,
        skipped = (obj["skipped"] as? JsonArray)?.size ?: 0,
        reindexTriggered = obj["reindex_triggered"]?.jsonPrimitive?.booleanOrNull ?: false,
    )
}
