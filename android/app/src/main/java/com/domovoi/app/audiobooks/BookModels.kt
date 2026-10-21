package com.domovoi.app.audiobooks

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.Chapter
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/*
 * Audiobooks as the server lists them (`GET /api/audiobooks`, `GET
 * /api/audiobooks/{id}`; design-notes AUDIOBOOKS-ANDROID-BRIEF, "API
 * contract"). Every field past the old ones is optional: a server from before
 * the contract has no size, no cover route and no `files`, and a folder book
 * there still names each chapter's file in its chapters.
 */

/** One audio file of a book: a folder book has one per chapter file, a
 *  single-file book exactly one. */
@Serializable
data class BookFileRow(
    val file: String? = null,
    val title: String? = null,
    val start_sec: Double = 0.0,
    val duration_sec: Double? = null,
    val size_bytes: Long? = null,
)

@Serializable
data class BookChapterRow(
    val title: String? = null,
    val start_sec: Double = 0.0,
    /** Folder books from before `files`: the chapter's file. */
    val file: String? = null,
)

@Serializable
data class ServerBook(
    val id: Long = 0,
    val title: String? = null,
    val author: String? = null,
    val narrator: String? = null,
    val artwork: String? = null,
    val duration_sec: Double? = null,
    val is_folder: Boolean = false,
    val file_ext: String? = null,
    val chapters: List<BookChapterRow> = emptyList(),
    val size_bytes: Long? = null,
    val cover_url: String? = null,
    val files: List<BookFileRow> = emptyList(),
    /** Where the book sits in the server's Audiobooks library (posix,
     *  relative): what "remove from server" deletes. Absent on an older
     *  server, and then the app offers no remove. */
    val library_path: String? = null,
    val added_at: String? = null,
) {
    val displayTitle: String get() = title?.takeIf { it.isNotBlank() } ?: "audiobook"
}

/** One playable piece of a book: a file, where it starts in the book, and
 *  how long it is (null when the server did not say). */
data class BookPart(
    val file: String?,
    val startSec: Double,
    val durationSec: Double?,
    val sizeBytes: Long?,
)

object BookModels {
    /** The book's audio files, in order. A single-file book is one part with
     *  no file name; a folder book one per file, from `files`, or from the
     *  chapters' file names on a server from before `files`. Empty for a
     *  folder book that names no file at all (nothing could be played). */
    fun parts(book: ServerBook): List<BookPart> {
        if (!book.is_folder) {
            val f = book.files.firstOrNull()
            return listOf(BookPart(null, 0.0, f?.duration_sec ?: book.duration_sec, f?.size_bytes ?: book.size_bytes))
        }
        val named = book.files.filter { !it.file.isNullOrBlank() }
        if (named.isNotEmpty()) {
            return named.sortedBy { it.start_sec }.map { BookPart(it.file, it.start_sec, it.duration_sec, it.size_bytes) }
        }
        val fromChapters = book.chapters.filter { !it.file.isNullOrBlank() }.sortedBy { it.start_sec }
        return fromChapters.mapIndexed { i, c ->
            val next = fromChapters.getOrNull(i + 1)?.start_sec ?: book.duration_sec
            BookPart(c.file, c.start_sec, next?.let { (it - c.start_sec).takeIf { d -> d > 0 } }, null)
        }
    }

    /** The server path of one part's audio: `?file=` for a folder book
     *  (the server 400s a folder book without it). */
    fun audioPath(bookId: Long, part: BookPart): String =
        "/api/audiobooks/$bookId/audio" + (part.file?.let { "?file=" + encodeQuery(it) } ?: "")

    /** The book's chapters for the player. A folder book without chapters
     *  of its own gets one per file. */
    fun chapters(book: ServerBook): List<Chapter> {
        val listed = book.chapters.map { Chapter(it.title?.takeIf { t -> t.isNotBlank() } ?: "", it.start_sec) }
        if (listed.isNotEmpty() || !book.is_folder) return listed
        return book.files.mapIndexed { i, f -> Chapter(f.title ?: f.file ?: "part ${i + 1}", f.start_sec) }
    }

    /** The whole book's size: the server's figure, else the files' sum when
     *  every file has one; null when unknown (the app then says so rather
     *  than guess). */
    fun sizeBytes(book: ServerBook): Long? {
        book.size_bytes?.takeIf { it > 0 }?.let { return it }
        if (book.files.isEmpty() || book.files.any { (it.size_bytes ?: 0) <= 0 }) return null
        return book.files.sumOf { it.size_bytes ?: 0 }
    }

    /** The cover's server path: the cover route the server named, else the
     *  legacy `artwork` path when it is one of the server's own. Never an
     *  address elsewhere (the phone fetches nothing from outside). */
    fun coverPath(book: ServerBook): String? =
        book.cover_url?.takeIf { it.startsWith("/") } ?: book.artwork?.takeIf { it.startsWith("/") }

    /** The list answer, paged (`{items: [...]}`) or bare, as books. */
    fun parseList(json: JsonElement): List<ServerBook> = when (json) {
        is JsonArray -> json.map { DomovoiJson.decodeFromJsonElement(ServerBook.serializer(), it) }
        is JsonObject -> (json["items"] as? JsonArray)?.map {
            DomovoiJson.decodeFromJsonElement(ServerBook.serializer(), it)
        }.orEmpty()
        else -> emptyList()
    }

    fun parse(json: JsonElement): ServerBook = DomovoiJson.decodeFromJsonElement(ServerBook.serializer(), json)

    private fun encodeQuery(s: String): String =
        java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")
}

/** Another device or room's place in a book, for this person
 *  (`GET /api/audiobooks/{id}/positions`). */
@Serializable
data class OtherPosition(
    val device_label: String? = null,
    val room: String? = null,
    val position_sec: Double = 0.0,
    val updated_at: String? = null,
)
