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
import okhttp3.RequestBody.Companion.toRequestBody
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
 *
 * Long documents (documents plan D2, web/backend/api/chat_doc_jobs.py): a
 * summary read in parts by a job, its `job`/`progress` events and the
 * thread's `jobs` ([ServerJobView]); stop and carry on; the home server's
 * measured speed (GET /api/chat/doc-jobs/speed, for the phone chat's "use
 * home server" offer); and a phone chat's private job (POST
 * /api/chat/doc-jobs: the server keeps nothing, and the summary is written
 * into the phone's conversation).
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
    /** A summary a long-document job wrote: which job, and how far it read. */
    val job: ServerJobRef? = null,
    /** mode "heads_up": the card this reply is (documents plan D3). */
    val heads_up: ServerHeadsUp? = null,
)

/** A heads-up card's option, as the server gives it. */
@Serializable
internal data class ServerHeadsUpOption(val id: String = "", val label: String = "")

/** A reply that is a heads-up card (documents plan D3): the weakness in a
 *  sentence and the options that work on the home server; `chosen` once
 *  picked. */
@Serializable
internal data class ServerHeadsUp(
    val kind: String = "",
    val text: String = "",
    val options: List<ServerHeadsUpOption> = emptyList(),
    val never_label: String? = null,
    val chosen: String? = null,
) {
    fun view() = HeadsUpView(kind, text, options.map { it.id to it.label }, never_label ?: NEVER_WARN, chosen)
}

/** This phone's heads-up switch and silenced kinds, as a send carries them. */
internal fun headsUpBody(p: com.domovoi.app.assistant.docs.HeadsUpPrefs): JsonObject = buildJsonObject {
    put("off", p.off)
    put("skip", kotlinx.serialization.json.buildJsonArray { p.skip.sorted().forEach { add(JsonPrimitive(it)) } })
}

@Serializable
internal data class ServerJobRef(val id: String = "", val state: String = "", val parts: Int = 0, val read: Int = 0)

/** A job's result: the reply's text and `doc` (and, in a home-server chat,
 *  the persisted message row). */
@Serializable
internal data class ServerJobResult(val content: String = "", val doc: ServerDocReply? = null)

@Serializable
internal data class ServerJobError(val detail: String = "", val code: String? = null)

/** A long-document job as the server shows it (chat_doc_jobs.Job.view). */
@Serializable
internal data class ServerJobView(
    val id: String = "",
    /** running | reducing | stopped | failed | done */
    val state: String = "",
    val waiting_for_voice: Boolean = false,
    val part: Int = 0,
    val parts: Int = 0,
    val read: Int = 0,
    val eta_s: Double? = null,
    val estimate_s: Double? = null,
    val line: String = "",
    val private: Boolean = false,
    val thread_id: Long? = null,
    val covers: String = "",
    val result: ServerJobResult? = null,
    val error: ServerJobError? = null,
) {
    val live: Boolean get() = state == "running" || state == "reducing"
}

/** `GET /api/chat/doc-jobs/speed`'s answer. */
@Serializable
internal data class ServerDocSpeed(val ai: Boolean = false, val estimate_s: Double? = null, val parts: Int? = null)

internal suspend fun fetchDocSpeed(app: AppContainer, chars: Int): ServerDocSpeed = withContext(Dispatchers.IO) {
    app.api.get("/api/chat/doc-jobs/speed?chars=$chars").decode<ServerDocSpeed>()
}

internal suspend fun fetchJob(app: AppContainer, id: String): ServerJobView = withContext(Dispatchers.IO) {
    app.api.get("/api/chat/doc-jobs/$id").decode<ServerJobView>()
}

internal suspend fun stopServerJob(app: AppContainer, id: String): ServerJobView = withContext(Dispatchers.IO) {
    app.api.post("/api/chat/doc-jobs/$id/stop").decode<ServerJobView>()
}

internal suspend fun resumeServerJob(app: AppContainer, id: String): ServerJobView = withContext(Dispatchers.IO) {
    app.api.post("/api/chat/doc-jobs/$id/resume").decode<ServerJobView>()
}

internal suspend fun deleteServerJob(app: AppContainer, id: String) {
    withContext(Dispatchers.IO) { runCatching { app.api.delete("/api/chat/doc-jobs/$id") } }
}

/** Reads an SSE body, calling [onEvent] (event, data) per frame. */
internal fun readSseFrames(source: okio.BufferedSource, onEvent: (String, String) -> Unit) {
    var event = "message"
    val data = StringBuilder()
    while (true) {
        val line = source.readUtf8Line() ?: break
        when {
            line.startsWith("event: ") -> event = line.removePrefix("event: ").trim()
            line.startsWith("data: ") -> data.append(line.removePrefix("data: "))
            line.isEmpty() && data.isNotEmpty() -> {
                onEvent(event, data.toString())
                event = "message"
                data.setLength(0)
            }
        }
    }
}

/** A job event's view, or null for an event that isn't one. */
internal fun jobEventView(event: String, data: String): ServerJobView? =
    if (event in setOf("job", "progress", "done")) runCatching { DomovoiJson.decodeFromString(ServerJobView.serializer(), data) }.getOrNull() else null

private fun streamJob(app: AppContainer, req: Request, onView: (String, ServerJobView) -> Unit): ServerJobView? {
    var last: ServerJobView? = null
    app.api.http.newCall(req).execute().use { resp ->
        if (!resp.isSuccessful) throw ApiException(resp.code, "${resp.code} ${resp.message}", body = resp.body?.string().orEmpty().take(4096))
        val source = resp.body?.source() ?: return null
        readSseFrames(source) { event, data ->
            if (event == "error") return@readSseFrames
            jobEventView(event, data)?.let { v ->
                last = v
                onView(event, v)
            }
        }
    }
    return last
}

/**
 * "Use home server": [file] goes to the home server's private job with the
 * request, and its progress comes back to [onView] ("job", "progress",
 * "done"). Returns the last view (the result on it), or throws.
 */
internal suspend fun runPrivateJob(app: AppContainer, file: File, name: String, question: String,
                                   onView: (String, ServerJobView) -> Unit): ServerJobView? = withContext(Dispatchers.IO) {
    val body = MultipartBody.Builder().setType(MultipartBody.FORM)
        .addFormDataPart("file", name, file.asRequestBody("application/octet-stream".toMediaType()))
        .addFormDataPart("question", question)
        .build()
    val req = Request.Builder().url(app.api.absolute("/api/chat/doc-jobs"))
        .header("X-Requested-With", "DomovoiApp").post(body).build()
    streamJob(app, req, onView)
}

/** A job's progress from now on (coming back to one). */
internal suspend fun followServerJob(app: AppContainer, id: String, onView: (String, ServerJobView) -> Unit): ServerJobView? =
    withContext(Dispatchers.IO) {
        val req = Request.Builder().url(app.api.absolute("/api/chat/doc-jobs/$id/events"))
            .header("X-Requested-With", "DomovoiApp").get().build()
        streamJob(app, req, onView)
    }

/** A server job's sources as the phone keeps them (the document's index in
 *  the phone's conversation is the caller's to set). */
internal fun phoneSourcesOf(doc: ServerDocReply?): List<com.domovoi.app.assistant.docs.DocSource> =
    doc?.sources.orEmpty().map { s ->
        com.domovoi.app.assistant.docs.DocSource(label = s.label, partLabel = s.label, text = s.text, match = s.match,
            cited = s.cited, page = s.page)
    }

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

/** `POST /api/chat/letters/read`'s answer (documents plan D6). */
@Serializable
internal data class ServerLetterRead(
    val blur: Double? = null,
    val kind: String? = null,
    val text: String? = null,
    val stopped: Boolean = false,
    val seconds: Double? = null,
)

/** How long the home server may take to read one letter photo: a CPU-only
 *  box takes a few minutes (the D6 eval: about 2 minutes on contended
 *  E-cores), with nothing sent meanwhile. */
private const val LETTER_READ_TIMEOUT_MIN = 8L

/**
 * A phone chat's letter photo ([jpeg]), read by the home server: it keeps
 * nothing, and the explanation is written on this phone. With
 * [stopIfHandwritten], a handwritten letter comes back with no text, so
 * its card can show first. Throws [ApiException] (its body names why:
 * AI features off, no picture model, unreadable).
 */
internal suspend fun readLetterOnServer(app: AppContainer, jpeg: ByteArray, stopIfHandwritten: Boolean):
    com.domovoi.app.assistant.LetterServerRead = withContext(Dispatchers.IO) {
    val body = MultipartBody.Builder().setType(MultipartBody.FORM)
        .addFormDataPart("file", "letter.jpg", jpeg.toRequestBody("image/jpeg".toMediaType()))
        .addFormDataPart("stop_if_handwritten", if (stopIfHandwritten) "true" else "false")
        .build()
    val req = Request.Builder().url(app.api.absolute("/api/chat/letters/read"))
        .header("X-Requested-With", "DomovoiApp").post(body).build()
    val client = app.api.http.newBuilder()
        .readTimeout(LETTER_READ_TIMEOUT_MIN, java.util.concurrent.TimeUnit.MINUTES)
        .callTimeout(LETTER_READ_TIMEOUT_MIN + 1, java.util.concurrent.TimeUnit.MINUTES)
        .build()
    client.newCall(req).execute().use { resp ->
        val text = resp.body?.string().orEmpty()
        if (!resp.isSuccessful) {
            throw ApiException(resp.code, docFailureText("read", ApiException(resp.code, "${resp.code} ${resp.message}", body = text.take(4096))),
                body = text.take(4096), refusal = resp.header("X-Domovoi-Refusal"))
        }
        val r = DomovoiJson.decodeFromString(ServerLetterRead.serializer(), text)
        com.domovoi.app.assistant.LetterServerRead(r.blur, r.kind, r.text, r.stopped)
    }
}
