package com.domovoi.app.ui.screens.documents

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import com.domovoi.app.AppContainer
import com.domovoi.app.ui.components.relTime
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.time.Instant

// ---------------------------------------------------------------------------
// URL helpers — rel_path segments must stay %-encoded ("/" preserved).
// ---------------------------------------------------------------------------

internal fun docRawPath(rel: String): String = "/api/documents/raw/" + Uri.encode(rel, "/")

internal fun docTextPath(rel: String): String = "/api/documents/text/" + Uri.encode(rel, "/")

/** A document as Word: `GET /api/documents/export/doc/{rel_path:path}?fmt=docx`
 *  (web/backend/api/documents.py `export_doc`, daily-read tier). Each folder
 *  and the name are one %-encoded segment ("/" kept between them), so
 *  "Letters/To Gran.html" asks for `Letters/To%20Gran.html`. Pure, unlike
 *  android.net.Uri, so it is pinned by a JVM test. */
internal fun docWordExportPath(rel: String): String =
    "/api/documents/export/doc/" +
        rel.trim('/').split('/').joinToString("/") { com.domovoi.app.net.encodePathSegment(it) } +
        "?fmt=docx"

/** modified_at arrives as unix seconds — bridge it into the shared relTime. */
internal fun relFromEpochSec(sec: Double?): String {
    val s = sec ?: return "—"
    return relTime(Instant.ofEpochMilli((s * 1000).toLong()).toString())
}

// ---------------------------------------------------------------------------
// Upload — content Uris → multipart "files" parts with their display names.
// ---------------------------------------------------------------------------

/** Returns (saved, skipped) counts from the upload response. */
internal suspend fun uploadDocs(
    context: Context,
    app: AppContainer,
    uris: List<Uri>,
): Pair<Int, Int> = withContext(Dispatchers.IO) {
    val resolver = context.contentResolver
    val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
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
    val res = app.api.upload("/api/documents/upload", builder.build())
    val obj = res as? JsonObject ?: JsonObject(emptyMap())
    val saved = (obj["saved"] as? JsonArray)?.size ?: 0
    val skipped = (obj["skipped"] as? JsonArray)?.size ?: 0
    saved to skipped
}

private fun displayName(resolver: ContentResolver, uri: Uri): String? = runCatching {
    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
    }
}.getOrNull()
