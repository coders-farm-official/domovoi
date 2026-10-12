package com.domovoi.app.assistant

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest

/**
 * The model download: the ONLY network use of the on-device assistant, and
 * the only class in `assistant/` that touches an HTTP client (a unit test
 * holds that line). The user starts it; it fetches each catalog file from its
 * pinned URL over the app's own client (redirects followed by the app's
 * policy, never downgraded to http, no household token: see AppContainer).
 *
 * Each file is written to `<file>.part` and hashed while it is written. A
 * `.part` left by an interrupted run resumes with an HTTP Range request; a
 * host that answers 200 instead of 206 restarts that file. Only a file whose
 * SHA-256 matches the catalog is renamed into place; on a mismatch the
 * `.part` is deleted and the file fetched once more before giving up.
 *
 * M0 runs this in the foreground from the benchmark card; M1 moves it into a
 * WorkManager job with the same rules.
 */
class ModelDownloader(private val http: OkHttpClient) {

    class HashMismatch(val path: String, val expected: String, val actual: String) :
        IOException("$path failed its checksum (expected ${expected.take(12)}…, got ${actual.take(12)}…)")

    class NotEnoughSpace(val needed: Long, val available: Long) :
        IOException("not enough free space: needs $needed bytes, $available available")

    /** Bytes of [model] already on disk (verified files and partial ones). */
    fun bytesDone(model: CatalogModel, store: ModelStore): Long =
        model.files.sumOf { f ->
            if (store.isComplete(model, f)) f.size else partOf(store.fileFor(model, f)).let { if (it.isFile) it.length() else 0L }
        }

    /**
     * Downloads whatever of [model] is missing into [store] and marks it
     * installed. [onProgress] gets (bytes done, bytes total) across all of
     * the model's files. Cancelling the coroutine keeps the `.part` files,
     * so the next call resumes.
     */
    suspend fun install(
        model: CatalogModel,
        store: ModelStore,
        onProgress: (done: Long, total: Long) -> Unit = { _, _ -> },
    ) = withContext(Dispatchers.IO) {
        if (store.isInstalled(model)) return@withContext
        val total = model.totalBytes
        val missing = model.files.filterNot { store.isComplete(model, it) }
        val needed = total - bytesDone(model, store) + SPACE_MARGIN
        val available = store.usableBytes()
        if (available < needed) throw NotEnoughSpace(needed, available)

        var before = total - missing.sumOf { it.size }
        onProgress(bytesDone(model, store), total)
        for (file in missing) {
            val target = store.fileFor(model, file)
            var attempts = 0
            while (true) {
                try {
                    fetch(file, target) { have -> onProgress(before + have, total) }
                    break
                } catch (e: HashMismatch) {
                    if (++attempts > 1) throw e
                }
            }
            before += file.size
        }
        store.markInstalled(model)
        onProgress(total, total)
    }

    /** Fetches one file into [target]: resume, hash, verify, rename. */
    internal suspend fun fetch(file: ModelFile, target: File, onBytes: (Long) -> Unit = {}) {
        target.parentFile?.mkdirs()
        val part = partOf(target)
        val digest = MessageDigest.getInstance("SHA-256")
        var have = if (part.isFile) part.length() else 0L
        if (have > file.size) {
            part.delete()
            have = 0
        }
        if (have > 0) part.inputStream().use { input -> pump(input, digest, null) {} }

        var restarted = false
        while (have < file.size) {
            val request = Request.Builder().url(file.url)
                .apply { if (have > 0) header("Range", "bytes=$have-") }
                .build()
            val resumedAt = http.newCall(request).execute().use { response ->
                val append = when {
                    have > 0 && response.code == 206 && contentRangeStart(response.header("Content-Range")) == have -> true
                    response.code == 200 -> false
                    have > 0 && response.code == 206 && !restarted -> null       // wrong range: start over
                    else -> throw IOException("${file.path}: HTTP ${response.code}")
                }
                if (append == null) return@use null
                if (!append) {
                    digest.reset()
                    have = 0
                }
                val body = response.body ?: throw IOException("${file.path}: empty response")
                FileOutputStream(part, append).use { out ->
                    body.byteStream().use { input ->
                        pump(input, digest, out) { n ->
                            have += n
                            if (have > file.size) throw IOException("${file.path}: more bytes than the catalog size")
                            onBytes(have)
                        }
                    }
                }
                have
            }
            if (resumedAt == null) {
                part.delete()
                digest.reset()
                have = 0
                restarted = true
                continue
            }
            if (have < file.size) throw IOException("${file.path}: connection ended at $have of ${file.size} bytes")
        }

        val actual = digest.digest().toHex()
        if (!actual.equals(file.sha256, ignoreCase = true)) {
            part.delete()
            throw HashMismatch(file.path, file.sha256, actual)
        }
        target.delete()
        if (!part.renameTo(target)) throw IOException("${file.path}: could not move into place")
    }

    private suspend fun pump(
        input: java.io.InputStream,
        digest: MessageDigest,
        out: java.io.OutputStream?,
        onChunk: (Int) -> Unit,
    ) {
        val buffer = ByteArray(BUFFER)
        while (true) {
            currentCoroutineContext().ensureActive()
            val n = input.read(buffer)
            if (n < 0) break
            out?.write(buffer, 0, n)
            digest.update(buffer, 0, n)
            onChunk(n)
        }
    }

    companion object {
        /** Headroom kept free beyond the download itself. */
        const val SPACE_MARGIN: Long = 200L * 1024 * 1024
        private const val BUFFER = 256 * 1024

        fun partOf(target: File): File = File(target.path + ".part")

        /** The first byte of a `Content-Range: bytes <start>-<end>/<size>`. */
        internal fun contentRangeStart(header: String?): Long? =
            header?.trim()?.removePrefix("bytes")?.trim()?.substringBefore('-')?.toLongOrNull()

        internal fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
    }
}
