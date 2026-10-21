package com.domovoi.app.audiobooks

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/*
 * Saving a book's files to the phone, resumably (owner decision 2026-10-20:
 * leave home mid-download and it finishes by itself next time the server is
 * reachable). Each file is written to `<file>.part`; a `.part` left by an
 * interrupted run resumes with a Range request, and the server's Range
 * support (web/backend/api/audio_serve.py) answers 206 from there. Only a
 * whole file is renamed into place.
 *
 * The download runs over the app's own HTTP client (the same rules as every
 * other request: cleartext policy, redirects, the household token only to the
 * active server once it proved itself), never the system DownloadManager, so
 * no header is kept in a system database.
 */

/** What a server's answer to one file request means for the `.part`. */
sealed interface RangeStep {
    /** Append the body to the `.part`. */
    data object Append : RangeStep

    /** The body is the whole file: write the `.part` afresh. */
    data object Rewrite : RangeStep

    /** The `.part` already holds the whole file: nothing to fetch. */
    data object Complete : RangeStep

    /** The `.part` is wrong (the file changed, or the range did): delete it
     *  and ask again from the start. */
    data object Restart : RangeStep

    /** Not something to write: the book went (404), or another refusal. */
    data class Refused(val status: Int) : RangeStep

    /** The server is there but not answering properly now: try later. */
    data class TryLater(val status: Int) : RangeStep
}

object RangeRules {
    /**
     * Pure. [have] bytes are in the `.part`; [expected] is the file's size
     * as the book's record says (null: unknown). [status] and
     * [contentRange] are the server's answer to a request that asked for
     * `bytes=<have>-` when [have] > 0.
     */
    fun step(have: Long, expected: Long?, status: Int, contentRange: String?): RangeStep {
        val range = parseContentRange(contentRange)
        return when {
            status == 206 && have > 0 && range != null && range.start == have &&
                (expected == null || range.total == null || range.total == expected) -> RangeStep.Append
            // A 206 that is not the range asked for, or a file of another
            // size now: what is in the .part may not be this file's start.
            status == 206 -> RangeStep.Restart
            status == 200 -> if (expected != null && have > 0 && have == expected) RangeStep.Complete else RangeStep.Rewrite
            status == 416 -> {
                // Asked past the end: the .part is the whole file if its
                // size is the file's; otherwise the file changed.
                val total = range?.total ?: expected
                if (total != null && have == total) RangeStep.Complete else RangeStep.Restart
            }
            status == 404 || status == 410 || status == 400 || status == 401 || status == 403 -> RangeStep.Refused(status)
            else -> RangeStep.TryLater(status)
        }
    }

    data class ContentRange(val start: Long, val end: Long?, val total: Long?)

    // "bytes <start>-<end>/<total>" (the total may be a star), or for a 416
    // "bytes" then a star, a slash and the total.
    fun parseContentRange(header: String?): ContentRange? {
        val h = header?.trim()?.removePrefix("bytes")?.trim() ?: return null
        val total = h.substringAfter('/', "").trim().toLongOrNull()
        val span = h.substringBefore('/').trim()
        if (span == "*") return ContentRange(-1, null, total)
        val start = span.substringBefore('-').trim().toLongOrNull() ?: return null
        val end = span.substringAfter('-', "").trim().toLongOrNull()
        return ContentRange(start, end, total)
    }
}

/** How a book's download ended this run. */
sealed interface BookFetchOutcome {
    data object Done : BookFetchOutcome

    /** The server went out of reach or stumbled: run again later; the
     *  `.part` files stay and resume. */
    data class Retry(val reason: String) : BookFetchOutcome

    /** Retrying would not help (the book is gone, or the phone is full). */
    data class Failed(val reason: String) : BookFetchOutcome
}

/** Thrown inside the fetch for an answer that means "stop". */
class BookRefused(val status: Int, message: String) : IOException(message)

class BookNotEnoughSpace(val needed: Long, val available: Long) :
    IOException("not enough free space: needs $needed bytes, $available available")

/**
 * Fetches the files of one saved book into [store]. [url] turns a server
 * path into an address on the active server.
 */
class BookFetcher(private val http: OkHttpClient, private val store: BookStore) {

    /**
     * Downloads what is missing of [book]: the cover (best-effort), then each
     * file. [onProgress] hears (bytes done, bytes total) across the files;
     * total is the record's size when it has one. Cancelling the coroutine
     * keeps the `.part` files for the next run.
     */
    suspend fun fetch(
        book: SavedBook,
        url: (String) -> String,
        onProgress: (done: Long, total: Long?) -> Unit = { _, _ -> },
    ): BookFetchOutcome = withContext(Dispatchers.IO) {
        val meta = book.meta
        val total = meta.files.takeIf { fs -> fs.all { (it.sizeBytes ?: 0) > 0 } }?.sumOf { it.sizeBytes!! }
            ?: meta.sizeBytes
        try {
            val missing = meta.files.filterNot { store.isComplete(book.key, it) }
            val doneBefore = meta.files.filter { store.isComplete(book.key, it) }.sumOf { store.fileOf(book.key, it.local).length() }
            if (total != null) {
                val partial = missing.sumOf { store.partOf(book.key, it.local).takeIf(File::isFile)?.length() ?: 0L }
                val needed = total - doneBefore - partial + SPACE_MARGIN
                val available = store.usableBytes()
                if (available < needed) throw BookNotEnoughSpace(needed, available)
            }
            var before = doneBefore
            onProgress(before, total)
            for (f in missing) {
                val path = BookModels.audioPath(meta.bookId, BookPart(f.name, f.startSec, f.durationSec, f.sizeBytes))
                val got = fetchFile(url(path), store.fileOf(book.key, f.local), f.sizeBytes) { have ->
                    onProgress(before + have, total)
                }
                before += got
            }
            if (meta.cover == null && meta.coverPath != null) fetchCover(book, url(meta.coverPath))
            onProgress(before, total ?: before)
            BookFetchOutcome.Done
        } catch (e: BookRefused) {
            BookFetchOutcome.Failed(
                if (e.status == 404 || e.status == 410) "this book is no longer on the server" else (e.message ?: "refused"),
            )
        } catch (e: BookNotEnoughSpace) {
            BookFetchOutcome.Failed("not enough free space on this phone")
        } catch (e: IOException) {
            val why = e.message ?: e.javaClass.simpleName
            if (why.contains("ENOSPC") || why.contains("No space left", ignoreCase = true)) {
                BookFetchOutcome.Failed("the phone ran out of space")
            } else {
                BookFetchOutcome.Retry(why)
            }
        }
    }

    /** One file into [target]: resume, write, rename. Returns its size. */
    internal suspend fun fetchFile(url: String, target: File, expected: Long?, onBytes: (Long) -> Unit = {}): Long {
        // Never made here: a book removed mid-download has no folder, and
        // its job must not leave a stray .part behind it.
        if (target.parentFile?.isDirectory != true) throw BookRefused(410, "the book was removed from this phone")
        val part = File(target.path + ".part")
        var have = if (part.isFile) part.length() else 0L
        if (expected != null && have > expected) {
            part.delete()
            have = 0
        }
        var restarts = 0
        while (true) {
            val request = Request.Builder().url(url)
                .header("X-Requested-With", "DomovoiApp")
                .apply { if (have > 0) header("Range", "bytes=$have-") }
                .build()
            val finished = http.newCall(request).execute().use { response ->
                when (val step = RangeRules.step(have, expected, response.code, response.header("Content-Range"))) {
                    RangeStep.Complete -> true
                    RangeStep.Restart -> {
                        if (++restarts > 2) throw IOException("the server's ranges don't line up")
                        part.delete()
                        have = 0
                        false
                    }
                    is RangeStep.Refused -> throw BookRefused(step.status, "the server refused the file (HTTP ${step.status})")
                    is RangeStep.TryLater -> throw IOException("the server answered HTTP ${step.status}")
                    RangeStep.Append, RangeStep.Rewrite -> {
                        val append = step == RangeStep.Append
                        if (!append) have = 0
                        val body = response.body ?: throw IOException("empty response")
                        FileOutputStream(part, append).use { out ->
                            body.byteStream().use { input ->
                                val buffer = ByteArray(BUFFER)
                                while (true) {
                                    currentCoroutineContext().ensureActive()
                                    val n = input.read(buffer)
                                    if (n < 0) break
                                    out.write(buffer, 0, n)
                                    have += n
                                    if (expected != null && have > expected) throw IOException("more bytes than the file's size")
                                    onBytes(have)
                                }
                            }
                        }
                        // A body that ended early is a dropped connection:
                        // the .part stays, and the next run resumes it.
                        if (expected != null && have < expected) {
                            throw IOException("connection ended at $have of $expected bytes")
                        }
                        val length = response.body?.contentLength() ?: -1
                        if (expected == null && length > 0 && !append && have < length) {
                            throw IOException("connection ended at $have of $length bytes")
                        }
                        true
                    }
                }
            }
            if (finished) break
        }
        target.delete()
        if (!part.renameTo(target)) throw IOException("could not move the file into place")
        return target.length()
    }

    /** The cover beside the audio. Best-effort: a book without one, or a
     *  cover that fails, still saves. */
    private fun fetchCover(book: SavedBook, url: String) {
        runCatching {
            val request = Request.Builder().url(url).header("X-Requested-With", "DomovoiApp").build()
            http.newCall(request).execute().use { r ->
                if (!r.isSuccessful) return
                val type = r.header("Content-Type").orEmpty()
                val ext = when {
                    "png" in type -> ".png"
                    "webp" in type -> ".webp"
                    else -> ".jpg"
                }
                val bytes = r.body?.bytes() ?: return
                if (bytes.isEmpty() || bytes.size > MAX_COVER) return
                val name = "cover$ext"
                File(store.dirOf(book.key), name).writeBytes(bytes)
                val now = store.read(book.key) ?: return
                store.write(book.key, now.meta.copy(cover = name))
            }
        }
    }

    companion object {
        /** Headroom kept free beyond the book itself. */
        const val SPACE_MARGIN: Long = 50L * 1024 * 1024
        private const val BUFFER = 128 * 1024
        private const val MAX_COVER = 8 * 1024 * 1024
    }
}
