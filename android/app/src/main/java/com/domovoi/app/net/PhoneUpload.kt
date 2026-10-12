package com.domovoi.app.net

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.runtime.mutableStateMapOf
import com.domovoi.app.AppContainer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.FileNotFoundException
import java.io.InputStream

/**
 * One file from the phone to the connected server: the "upload" on the
 * workspace's "Your device" pages (ui/screens/local/YourDevice.kt).
 *
 * The bytes are STREAMED from the content uri into the multipart body rather
 * than read into memory first, as [uploadFiles] does for a handful of picked
 * documents: a phone video is easily bigger than the app's heap. The stream
 * reports progress as it goes, in whole percent, so a row can show it.
 *
 * Two doors, both on the device tier and both behind the per-device Files
 * block: `/api/music/library/upload` (the Music page's own upload, which
 * indexes the new track at once — `/api/files/upload` into core:music would
 * ask the core's admin-only sweep and index nothing) and `/api/files/upload`
 * into a library folder for everything else.
 */

/** The Music page's upload door: lands in MUSIC_DIR/uploads and is indexed at once. */
internal const val MUSIC_UPLOAD_PATH = "/api/music/library/upload"

/** The Files upload door: a library id + folder. */
internal const val FILES_UPLOAD_PATH = "/api/files/upload"

/**
 * Whole-percent progress of a stream of known length. [step] answers the
 * percent to report after [sent] bytes, or null when it has not moved since
 * the last report (so a 2 GB video reports ~100 times, not once per chunk).
 * A stream of unknown length reports nothing.
 */
internal class ProgressMeter(private val total: Long) {
    private var last = -1

    fun step(sent: Long): Int? {
        if (total <= 0) return null
        val pct = ((sent.coerceAtLeast(0) * 100) / total).toInt().coerceIn(0, 100)
        if (pct == last) return null
        last = pct
        return pct
    }
}

/** A multipart file part that streams from [open] and reports progress. */
internal class StreamingBody(
    private val mime: MediaType?,
    private val length: Long,
    private val open: () -> InputStream,
    private val onPercent: (Int) -> Unit,
) : RequestBody() {
    override fun contentType(): MediaType? = mime

    override fun contentLength(): Long = length

    override fun writeTo(sink: BufferedSink) {
        // Reopened on every write, so a retry resends the whole file.
        val meter = ProgressMeter(length)
        open().use { input ->
            val buf = ByteArray(64 * 1024)
            var sent = 0L
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                sink.write(buf, 0, n)
                sent += n
                meter.step(sent)?.let(onPercent)
            }
        }
    }
}

/**
 * POST one streamed file to [path] as the part `files`, after [fields].
 * Separate from the content-uri plumbing so a test can drive it with a byte
 * stream and a mock server.
 */
internal suspend fun uploadStream(
    api: ApiClient,
    path: String,
    fields: List<Pair<String, String>>,
    name: String,
    mime: String,
    length: Long,
    open: () -> InputStream,
    onPercent: (Int) -> Unit,
): JsonElement {
    val form = MultipartBody.Builder().setType(MultipartBody.FORM).apply {
        fields.forEach { (k, v) -> addFormDataPart(k, v) }
        addFormDataPart("files", name, StreamingBody(mime.toMediaTypeOrNull(), length, open, onPercent))
    }.build()
    return api.upload(path, form)
}

/**
 * Both doors' replies as one [UploadResult]: `/api/files/upload` lists the
 * saved names, `/api/music/library/upload` counts them (and lists them under
 * `files`). Skipped is a list on both.
 */
internal fun parseUploadReply(reply: JsonElement): UploadResult {
    val obj = reply as? JsonObject ?: JsonObject(emptyMap())
    val saved = when (val s = obj["saved"]) {
        is JsonArray -> s.size
        is JsonPrimitive -> s.intOrNull ?: 0
        else -> 0
    }
    return UploadResult(
        saved = saved,
        skipped = (obj["skipped"] as? JsonArray)?.size ?: 0,
        reindexTriggered = obj["reindex_triggered"]?.jsonPrimitive?.booleanOrNull ?: false,
    )
}

/**
 * Upload the file at [uri] (a MediaStore or Storage Access Framework uri) to
 * [path] on the active server. The name sent is the file's own display name
 * (the server keeps it, deduped), or [fallbackName] when the provider has
 * none. Throws [FileNotFoundException] when the phone can't read the file,
 * before anything is sent.
 */
internal suspend fun uploadContentUri(
    context: Context,
    app: AppContainer,
    path: String,
    fields: List<Pair<String, String>>,
    uri: Uri,
    fallbackName: String,
    onPercent: (Int) -> Unit,
): UploadResult = withContext(Dispatchers.IO) {
    val resolver = context.contentResolver
    var name: String? = null
    var size = -1L
    runCatching {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                name = c.getString(0)
                if (!c.isNull(1)) size = c.getLong(1)
            }
        }
    }
    // Fail here, with the file named, rather than mid-request as a transport error.
    resolver.openInputStream(uri)?.close() ?: throw FileNotFoundException("can't read \"$fallbackName\" on this phone")
    val mime = resolver.getType(uri) ?: "application/octet-stream"
    val reply = uploadStream(
        api = app.api,
        path = path,
        fields = fields,
        name = name?.takeIf { it.isNotBlank() } ?: fallbackName,
        mime = mime,
        length = size,
        open = {
            resolver.openInputStream(uri) ?: throw FileNotFoundException("can't read \"$fallbackName\" on this phone")
        },
        onPercent = onPercent,
    )
    parseUploadReply(reply)
}

/**
 * Which phone uploads are running, and how far each has got: a whole percent,
 * or -1 while the length is unknown. Keyed by the row that started it, so the
 * row shows the progress wherever it is drawn — and the upload, which runs in
 * the app's scope, outlives the page it was started from.
 */
class PhoneUploads {
    private val running = mutableStateMapOf<String, Int>()

    /** Start [key]; false when it is already running (a second tap does nothing). */
    fun begin(key: String): Boolean {
        if (key in running) return false
        running[key] = -1
        return true
    }

    /** Record progress; ignored once [end] has run (a late report must not resurrect it). */
    fun update(key: String, percent: Int) {
        if (key in running) running[key] = percent.coerceIn(0, 100)
    }

    fun end(key: String) {
        running.remove(key)
    }

    fun inFlight(key: String): Boolean = key in running

    /** The percent sent, or null while unknown or not running. */
    fun percent(key: String): Int? = running[key]?.takeIf { it >= 0 }
}
