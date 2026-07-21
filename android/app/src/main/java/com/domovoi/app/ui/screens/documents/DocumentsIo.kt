package com.domovoi.app.ui.screens.documents

import android.content.ContentResolver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import com.domovoi.app.AppContainer
import com.domovoi.app.ui.components.relTime
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException
import java.time.Instant

// ---------------------------------------------------------------------------
// URL helpers — rel_path segments must stay %-encoded ("/" preserved).
// ---------------------------------------------------------------------------

internal fun docRawPath(rel: String): String = "/api/documents/raw/" + Uri.encode(rel, "/")

internal fun docTextPath(rel: String): String = "/api/documents/text/" + Uri.encode(rel, "/")

/** Open the /raw endpoint with the system viewer/downloader. */
internal fun openRawDoc(context: Context, app: AppContainer, rel: String) {
    runCatching {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(app.api.absolute(docRawPath(rel)))))
    }
}

/** modified_at arrives as unix seconds — bridge it into the shared relTime. */
internal fun relFromEpochSec(sec: Double?): String {
    val s = sec ?: return "—"
    return relTime(Instant.ofEpochMilli((s * 1000).toLong()).toString())
}

// ---------------------------------------------------------------------------
// Bulk zip download — streamed straight into Downloads (or the app dir).
// ---------------------------------------------------------------------------

/** POST /api/documents/download-zip and stream the body to storage. Returns the shown path. */
internal suspend fun downloadDocsZip(
    context: Context,
    app: AppContainer,
    rels: List<String>,
): String = withContext(Dispatchers.IO) {
    val payload = buildJsonObject {
        put("rel_paths", buildJsonArray { rels.forEach { add(it) } })
    }.toString().toRequestBody("application/json".toMediaType())
    val req = Request.Builder()
        .url(app.api.absolute("/api/documents/download-zip"))
        .post(payload)
        .build()
    app.api.http.newCall(req).execute().use { resp ->
        if (!resp.isSuccessful) throw IOException("${resp.code} ${resp.message}")
        val stream = resp.body?.byteStream() ?: throw IOException("empty response")
        val fileName = "domovoi-documents-${System.currentTimeMillis()}.zip"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val resolver = context.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, fileName)
                put(MediaStore.MediaColumns.MIME_TYPE, "application/zip")
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IOException("couldn't create the download entry")
            val out = resolver.openOutputStream(uri)
                ?: throw IOException("couldn't open the download for writing")
            out.use { stream.copyTo(it) }
            "Downloads/$fileName"
        } else {
            val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: context.filesDir
            val file = File(dir, fileName)
            file.outputStream().use { stream.copyTo(it) }
            file.absolutePath
        }
    }
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
