package com.domovoi.app.net

import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.content.FileProvider
import com.domovoi.app.AppContainer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.util.UUID

/**
 * Files from the home server, fetched by the APP (its own HTTP client, which
 * adds the household token for the active server only) and never handed to
 * another app as a server address.
 *
 * Until 2026-10-28 a Files download or "open" passed the bare
 * `http://<server>:6369/api/files/download?...` URL to ACTION_VIEW. The
 * phone's browser took it, could not send the device token (the routes are
 * on the device tier), and showed `{"detail":"X-Device-Token or admin
 * session required"}`. Now:
 *
 *  * Download streams the file (a folder: the server's zip) into
 *    Downloads/Domovoi, and says so when it lands;
 *  * Open fetches the file into the app's cache and hands the other app a
 *    read-only content:// grant (the FileProvider's `open_files` path) —
 *    the file, not an address.
 *
 * The token never goes into a URL, and never into the system's
 * DownloadManager database (unlike the video stream's save; DeviceDownloads).
 */
internal object ServerFileTransfer {
    /** Where an opened file is written before another app reads it (res/xml/shared_files.xml). */
    const val OPEN_DIR = "open-files"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    /** The name a download is saved under: the entry's own name, scrubbed;
     *  a folder comes back from the server as a zip. Pure. */
    fun saveName(rel: String, isDir: Boolean): String {
        val base = rel.trimEnd('/').substringAfterLast('/').ifBlank { "download" }
        val name = DeviceDownloads.safeName(base, "download")
        return if (isDir && !name.endsWith(".zip", ignoreCase = true)) "$name.zip" else name
    }

    /** Save [path] (a server route) as Downloads/Domovoi/[fileName], reporting progress through [report]. */
    fun download(context: Context, app: AppContainer, path: String, fileName: String, report: (String) -> Unit) {
        val ctx = context.applicationContext
        report("downloading \"$fileName\"…")
        scope.launch {
            runCatching { saveToDownloads(ctx, app, path, fileName) }
                .onSuccess { where -> report("saved \"$fileName\" to $where") }
                .onFailure { report(failureText("download", it)) }
        }
    }

    /** Fetch [path] into the cache and open it with whatever app views [mime]. */
    fun open(context: Context, app: AppContainer, path: String, fileName: String, mime: String, report: (String) -> Unit) {
        report("opening \"$fileName\"…")
        scope.launch {
            runCatching { fetchToCache(context.applicationContext, app, path, fileName) }
                .onSuccess { file ->
                    val uri = FileProvider.getUriForFile(context, context.packageName + ".files", file)
                    val intent = Intent(Intent.ACTION_VIEW)
                        .setDataAndType(uri, mime)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    try {
                        context.startActivity(intent)
                    } catch (_: ActivityNotFoundException) {
                        report("no app on this phone opens \"$fileName\" — use Download instead")
                    }
                }
                .onFailure { report(failureText("open", it)) }
        }
    }

    private fun <T> get(app: AppContainer, path: String, body: (InputStream) -> T): T {
        val req = Request.Builder()
            .url(app.api.absolute(path))
            // Same preflight-forcing header ApiClient.raw() sends (WEB-6).
            .header("X-Requested-With", "DomovoiApp")
            .get()
            .build()
        return app.api.http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) {
                val detail = runCatching { resp.body?.string()?.take(400) }.getOrNull().orEmpty()
                throw ApiException(resp.code, "${resp.code} ${resp.message}: $detail", body = detail)
            }
            val stream = resp.body?.byteStream() ?: throw IOException("empty response")
            body(stream)
        }
    }

    /** Returns where it landed, as the person would look for it. */
    suspend fun saveToDownloads(context: Context, app: AppContainer, path: String, fileName: String): String =
        withContext(Dispatchers.IO) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val resolver = context.contentResolver
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, fileName)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Domovoi")
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IOException("couldn't create the download")
                try {
                    get(app, path) { input ->
                        (resolver.openOutputStream(uri) ?: throw IOException("couldn't write the download"))
                            .use { input.copyTo(it) }
                    }
                    resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
                } catch (e: Exception) {
                    runCatching { resolver.delete(uri, null, null) }
                    throw e
                }
                // MediaStore may have renamed it ("name (1).ext") when one existed.
                val saved = resolver.query(uri, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME), null, null, null)
                    ?.use { c -> if (c.moveToFirst()) c.getString(0) else null } ?: fileName
                "Downloads/Domovoi/$saved"
            } else {
                // Android 8-9: shared storage needs a runtime grant this app
                // doesn't ask for; the app's own Downloads folder needs none.
                val dir = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: context.filesDir, "Domovoi")
                dir.mkdirs()
                val out = uniqueFile(dir, fileName)
                val part = File(dir, out.name + ".part")
                try {
                    get(app, path) { input -> part.outputStream().use { input.copyTo(it) } }
                    if (!part.renameTo(out)) throw IOException("couldn't save ${out.name}")
                } finally {
                    part.delete()
                }
                out.absolutePath
            }
        }

    private suspend fun fetchToCache(context: Context, app: AppContainer, path: String, fileName: String): File =
        withContext(Dispatchers.IO) {
            val root = File(context.cacheDir, OPEN_DIR)
            // One file at a time is ever being looked at: the last ones go.
            root.listFiles()?.forEach { it.deleteRecursively() }
            val dir = File(root, UUID.randomUUID().toString()).apply { mkdirs() }
            val out = File(dir, fileName)
            get(app, path) { input -> out.outputStream().use { input.copyTo(it) } }
            out
        }

    private fun uniqueFile(dir: File, name: String): File {
        var f = File(dir, name)
        if (!f.exists()) return f
        val dot = name.lastIndexOf('.').takeIf { it > 0 } ?: name.length
        var n = 1
        while (f.exists()) {
            f = File(dir, name.substring(0, dot) + " ($n)" + name.substring(dot))
            n++
        }
        return f
    }
}

