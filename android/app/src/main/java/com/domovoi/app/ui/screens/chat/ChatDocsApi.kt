package com.domovoi.app.ui.screens.chat

import com.domovoi.app.AppContainer
import com.domovoi.app.assistant.docs.DocExtraction
import com.domovoi.app.assistant.docs.DocNote
import com.domovoi.app.assistant.docs.DocPart
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File

/*
 * The home server's side of documents in Chat (web/backend/api/chat.py,
 * documents plan D1), as the app speaks it: attach a document to a
 * home-server chat (POST /api/chat/documents, then the send's "documents"),
 * the "documents" and "doc" fields of its messages, a thread started from a
 * file in the household's Documents (POST /api/chat/threads/from-document),
 * and "Ask the home server" for a phone chat's PDF (POST
 * /api/chat/documents/extract: the text comes back, nothing is kept there,
 * and the answer is still written on the phone).
 */

/** A document attached to a home-server chat (the upload's answer, and the
 *  user row's `documents`). */
@Serializable
internal data class ServerDocument(
    val token: String = "",
    val name: String = "",
    val kind: String = "",
    val pages: Int? = null,
    val words: Int = 0,
    val parts: Int = 0,
    val scanned_pages: List<Int> = emptyList(),
    val garbled_pages: List<Int> = emptyList(),
    val summary: String = "",
    val notes: List<DocNote> = emptyList(),
    val size: Long = 0,
)

/** One source under a home-server document reply. */
@Serializable
internal data class ServerSource(
    val token: String = "",
    val name: String = "",
    val label: String = "",
    val page: Int? = null,
    val text: String = "",
    val match: String = "",
    val cited: Boolean = false,
)

/** An assistant row's `doc`: how it answered about the chat's documents. */
@Serializable
internal data class ServerDocReply(
    val mode: String = "",
    val found: Boolean = true,
    val sources: List<ServerSource> = emptyList(),
    val notes: List<DocNote> = emptyList(),
)

/** `POST /api/chat/documents/extract`'s answer. */
@Serializable
internal data class ServerExtract(
    val kind: String = "",
    val pages: Int? = null,
    val parts: List<DocPart> = emptyList(),
    val scanned_pages: List<Int> = emptyList(),
    val garbled_pages: List<Int> = emptyList(),
    val words: Int = 0,
    val chars: Int = 0,
    val notes: List<DocNote> = emptyList(),
) {
    fun toExtraction() = DocExtraction(
        kind = kind.ifEmpty { "pdf" }, pages = pages, parts = parts, scannedPages = scanned_pages,
        garbledPages = garbled_pages, words = words, chars = chars, notes = notes, readByServer = true,
    )
}

/** The server's sentence for a refused document ({"detail": ...}), or a
 *  plain "<action> failed" line. */
internal fun docFailureText(action: String, e: Throwable): String {
    if (e is ApiException && e.body.isNotBlank()) {
        val detail = runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content
        }.getOrNull()
        if (!detail.isNullOrBlank()) return detail
    }
    return failureText(action, e)
}

private fun form(file: File, name: String): MultipartBody = MultipartBody.Builder().setType(MultipartBody.FORM)
    .addFormDataPart("file", name, file.asRequestBody("application/octet-stream".toMediaType()))
    .build()

/** A phone chat's PDF, read by the home server (nothing is kept there). */
internal suspend fun extractOnServer(app: AppContainer, file: File?, name: String): Result<DocExtraction> = runCatching {
    requireNotNull(file) { "the document is no longer on the phone" }
    withContext(Dispatchers.IO) { app.api.upload("/api/chat/documents/extract", form(file, name)).decode<ServerExtract>() }
        .toExtraction()
}

/** A document for a home-server chat: uploaded and read there. */
internal suspend fun uploadServerDocument(app: AppContainer, file: File, name: String): ServerDocument =
    withContext(Dispatchers.IO) { app.api.upload("/api/chat/documents", form(file, name)).decode<ServerDocument>() }

/** `POST /api/chat/threads/from-document`'s answer. */
@Serializable
internal data class FromDocument(val thread: ThreadRow = ThreadRow(), val document: ServerDocument? = null)

/** A home-server chat about [relPath] in the household's Documents. */
internal suspend fun chatFromDocument(app: AppContainer, relPath: String): FromDocument = withContext(Dispatchers.IO) {
    app.api.post("/api/chat/threads/from-document", buildJsonObject { put("rel_path", relPath) }).decode<FromDocument>()
}

/** A home-server document's file, downloaded once into the app's cache (the
 *  page sheet draws a PDF page from it). Null when it can't be had. */
internal suspend fun serverDocumentFile(app: AppContainer, cacheDir: File, token: String): File? = withContext(Dispatchers.IO) {
    if (!Regex("^[0-9a-f]{32}$").matches(token)) return@withContext null
    val out = File(cacheDir, "chat-docs/$token.pdf")
    if (out.isFile && out.length() > 0) return@withContext out
    runCatching {
        val req = Request.Builder().url(app.api.absolute("/api/chat/documents/$token/file"))
            .header("X-Requested-With", "DomovoiApp").get().build()
        app.api.http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return@runCatching null
            out.parentFile?.mkdirs()
            val tmp = File(out.parentFile, "$token.part")
            resp.body?.byteStream()?.use { input -> tmp.outputStream().use { input.copyTo(it) } } ?: return@runCatching null
            tmp.renameTo(out)
            out
        }
    }.getOrNull()
}

/** A home-server reply's sources as the shared chips show them. */
internal fun serverSources(doc: ServerDocReply?, pdf: (String) -> (suspend () -> File?)?): List<SourceView> =
    doc?.sources.orEmpty().map { s ->
        SourceView(s.label, s.text, s.match, s.cited, s.page, pdf = if (s.page != null && s.name.lowercase().endsWith(".pdf")) pdf(s.token) else null)
    }
