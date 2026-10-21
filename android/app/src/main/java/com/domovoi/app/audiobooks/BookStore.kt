package com.domovoi.app.audiobooks

import android.content.Context
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.ServerScope
import kotlinx.serialization.Serializable
import java.io.File
import java.io.IOException

/*
 * Books saved to the phone (owner decision 2026-10-20): in the app's own
 * storage, `filesDir/audiobooks/<folder>/`, so no storage permission on any
 * Android version, nothing in the phone's Music, and gone with the app. Each
 * folder holds the audio, the cover and `book.json` — the book's title,
 * chapters and files as the server described them — so the book plays with
 * its chapters with no server at all.
 *
 * A book id is a server's: id 12 on another Domovoi is another book. The
 * folder is named once, at save (`b<id>-<random>`), and `book.json` records
 * which server it came from (its pinned identity, else its address,
 * [ServerScope]); lookups go by the record, never the folder name.
 */

/** One audio file of a saved book. [name] is the server's (`?file=`), null
 *  for a single-file book; [local] the file's name in the book's folder. */
@Serializable
data class SavedFile(
    val name: String? = null,
    val local: String,
    val startSec: Double = 0.0,
    val durationSec: Double? = null,
    val sizeBytes: Long? = null,
)

@Serializable
data class SavedChapter(val title: String = "", val startSec: Double = 0.0)

@Serializable
data class SavedBookMeta(
    val version: Int = 1,
    val bookId: Long,
    val serverAddress: String? = null,
    val serverFingerprint: String? = null,
    /** The server's name as the app showed it ("Domovoi at 192.168.1.20"). */
    val serverName: String? = null,
    val title: String,
    val author: String? = null,
    val narrator: String? = null,
    val durationSec: Double? = null,
    val isFolder: Boolean = false,
    val chapters: List<SavedChapter> = emptyList(),
    val files: List<SavedFile> = emptyList(),
    /** The cover's file name in the folder, once it is saved. */
    val cover: String? = null,
    /** The server's path for the cover, fetched with the audio. */
    val coverPath: String? = null,
    /** The book's size as the server gave it before saving; null if unknown. */
    val sizeBytes: Long? = null,
    val savedAtMs: Long = 0,
    /** Every file is here in full. */
    val complete: Boolean = false,
) {
    val server: ServerScope get() = ServerScope(serverAddress, serverFingerprint)

    val playerChapters: List<Chapter> get() = chapters.map { Chapter(it.title, it.startSec) }

    companion object {
        /** A file extension the app will name a saved file with. */
        private val EXT = Regex("^\\.[A-Za-z0-9]{1,5}$")
        private val UNSAFE = Regex("[\\\\/:*?\"<>|\\x00-\\x1f]+")

        /** A file name the app's folder can hold: no separators, no control
         *  bytes, not a dot name, at most 120 characters. */
        fun safeLocal(name: String, fallback: String): String {
            val cleaned = UNSAFE.replace(name, "_").trim(' ', '.')
            return cleaned.take(120).ifBlank { fallback }
        }

        /** What gets saved for [book] from [server]: the files to fetch,
         *  each named in the folder by its position (so two chapter files
         *  can never collide), and the chapters to play with. */
        fun fromServer(book: ServerBook, server: ServerScope, serverName: String?, nowMs: Long): SavedBookMeta {
            val parts = BookModels.parts(book)
            val ext = book.file_ext?.takeIf { EXT.matches(it) } ?: ".m4b"
            val files = parts.mapIndexed { i, p ->
                val local = if (p.file == null) "book$ext" else {
                    "%03d-".format(i + 1) + safeLocal(p.file, "part${i + 1}")
                }
                SavedFile(p.file, local, p.startSec, p.durationSec, p.sizeBytes)
            }
            return SavedBookMeta(
                bookId = book.id,
                serverAddress = server.address,
                serverFingerprint = server.fingerprint,
                serverName = serverName,
                title = book.displayTitle,
                author = book.author,
                narrator = book.narrator,
                durationSec = book.duration_sec,
                isFolder = book.is_folder,
                chapters = BookModels.chapters(book).map { SavedChapter(it.title, it.startSec) },
                files = files,
                coverPath = BookModels.coverPath(book),
                sizeBytes = BookModels.sizeBytes(book),
                savedAtMs = nowMs,
            )
        }
    }
}

/** A saved book: its folder's name ([key]) and what `book.json` says. */
data class SavedBook(val key: String, val meta: SavedBookMeta)

/**
 * The saved books on disk under [root]. Plain files and JSON, so the JVM
 * tests drive it against a temp folder. Writes are atomic per file (a temp
 * file renamed into place): a book.json is whole or the old one.
 */
class BookStore(val root: File) {

    fun dirOf(key: String): File = File(root, key)

    fun fileOf(key: String, local: String): File = File(dirOf(key), local)

    fun partOf(key: String, local: String): File = File(dirOf(key), "$local.part")

    /** Every saved book (finished or not), newest save first. A folder
     *  without a readable book.json is not a book and is skipped. */
    fun list(): List<SavedBook> {
        val dirs = root.listFiles { f -> f.isDirectory && f.name.startsWith(PREFIX) } ?: return emptyList()
        return dirs.mapNotNull { read(it.name) }.sortedByDescending { it.meta.savedAtMs }
    }

    fun read(key: String): SavedBook? {
        val f = File(dirOf(key), META)
        if (!f.isFile) return null
        return runCatching {
            SavedBook(key, DomovoiJson.decodeFromString(SavedBookMeta.serializer(), f.readText()))
        }.getOrNull()
    }

    /** The saved copy of book [bookId] from [server], if there is one. */
    fun find(bookId: Long, server: ServerScope): SavedBook? =
        list().firstOrNull { it.meta.bookId == bookId && it.meta.server.isSameServer(server) }

    /** A new folder for [meta]; returns its key. */
    fun create(meta: SavedBookMeta, random: () -> String = { java.util.UUID.randomUUID().toString().take(8) }): SavedBook {
        root.mkdirs()
        var key: String
        do { key = "$PREFIX${meta.bookId}-${random()}" } while (dirOf(key).exists())
        if (!dirOf(key).mkdirs()) throw IOException("could not make a folder for the book")
        write(key, meta)
        return SavedBook(key, meta)
    }

    fun write(key: String, meta: SavedBookMeta) {
        val dir = dirOf(key)
        if (!dir.isDirectory) throw IOException("the book's folder is gone")
        val tmp = File(dir, "$META.tmp")
        tmp.writeText(DomovoiJson.encodeToString(SavedBookMeta.serializer(), meta))
        val target = File(dir, META)
        if (!tmp.renameTo(target)) {
            target.delete()
            if (!tmp.renameTo(target)) throw IOException("could not save the book's details")
        }
    }

    /** Removes the book's folder and everything in it. */
    fun remove(key: String): Boolean {
        require(key.startsWith(PREFIX) && '/' !in key && '\\' !in key && ".." !in key) { "not a book folder: $key" }
        return dirOf(key).deleteRecursively()
    }

    /** Bytes the book takes on the phone now (finished and partial files). */
    fun bytesOf(key: String): Long = dirOf(key).walkTopDown().filter { it.isFile }.sumOf { it.length() }

    /** Bytes every saved book takes. */
    fun totalBytes(): Long = if (!root.isDirectory) 0 else root.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    /** Free space where the books are kept. */
    fun usableBytes(): Long = (root.takeIf { it.exists() } ?: root.parentFile ?: root).usableSpace

    /** Which of [meta]'s files are here in full: finished (renamed from
     *  `.part`) and, where the server gave a size, that size. */
    fun isComplete(key: String, file: SavedFile): Boolean {
        val f = fileOf(key, file.local)
        if (!f.isFile) return false
        return file.sizeBytes == null || file.sizeBytes <= 0 || f.length() == file.sizeBytes
    }

    companion object {
        const val PREFIX = "b"
        const val META = "book.json"

        fun forApp(context: Context): BookStore = BookStore(File(context.applicationContext.filesDir, "audiobooks"))
    }
}
