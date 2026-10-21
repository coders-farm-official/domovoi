package com.domovoi.app.audiobooks

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import com.domovoi.app.AppContainer
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.FILES_UPLOAD_PATH
import com.domovoi.app.net.UploadResult
import com.domovoi.app.net.uploadContentUri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/*
 * "Add to server": audiobook files or a folder picked on the phone, sent to
 * the server's Audiobooks library (`core:audiobooks`) through the Files
 * upload, which re-reads the library when it is done. A single file is a
 * book of its own; a folder is ONE book, a file per chapter, and lands in a
 * folder of its name (`create_dir=true`, asked for alongside the files; a
 * server from before it answers 404 and the app says folders need an
 * update rather than scatter the chapters as separate books).
 */

/** One file to send: where it is on the phone, and its name. */
data class PickedFile(val uri: Uri, val name: String, val sizeBytes: Long?)

/** A picked folder: its name and its audio files, in name order. */
data class PickedFolder(val name: String, val files: List<PickedFile>)

object BookUploadRules {
    private val AUDIO_EXT = setOf("mp3", "m4a", "m4b", "aac", "flac", "ogg", "oga", "opus", "wav", "wma", "mka")

    /** Whether a picked file looks like audio: its type, else its extension. */
    fun isAudio(name: String, mime: String?): Boolean =
        mime?.startsWith("audio/") == true || name.substringAfterLast('.', "").lowercase() in AUDIO_EXT

    /** The order a folder's files go up in: by name, numbers by value
     *  ("2 Chapter" before "10 Chapter"), as a person numbered them. */
    val naturalOrder: Comparator<String> = Comparator { a, b ->
        val ra = Regex("\\d+|\\D+").findAll(a.lowercase()).map { it.value }.toList()
        val rb = Regex("\\d+|\\D+").findAll(b.lowercase()).map { it.value }.toList()
        for (i in 0 until minOf(ra.size, rb.size)) {
            val x = ra[i]
            val y = rb[i]
            val c = if (x[0].isDigit() && y[0].isDigit()) {
                x.trimStart('0').length.compareTo(y.trimStart('0').length).takeIf { it != 0 }
                    ?: x.trimStart('0').compareTo(y.trimStart('0'))
            } else x.compareTo(y)
            if (c != 0) return@Comparator c
        }
        ra.size.compareTo(rb.size)
    }

    /** The folder's name on the server: no separators or characters a
     *  Windows server can't hold, so the server's own cleaning leaves it as
     *  it is and the later files find the folder the first one made. */
    fun folderName(name: String): String =
        SavedBookMeta.safeLocal(name.replace(Regex("\\s+"), " "), "audiobook").trimEnd(' ', '.').take(100)

    /** The upload's form fields for file [index] of a folder named [folder]
     *  (null: a single-file book into the library's top). */
    fun fields(deviceId: String, folder: String?, index: Int): List<Pair<String, String>> = buildList {
        add("library_id" to ServerRemove.LIBRARY_ID)
        add("path" to (folder ?: ""))
        add("device_id" to deviceId)
        // Only the first file makes the folder; the rest go into it.
        if (folder != null && index == 0) add("create_dir" to "true")
    }

    /** The server could not make the folder: one from before `create_dir`. */
    fun isOldServerRefusal(e: Throwable): Boolean =
        e is ApiException && e.status == 404 && "destination directory not found" in e.body
}

object BookUpload {
    /** The audio files in the folder [tree] (a Storage Access Framework
     *  tree grant), in order, with the folder's name. */
    suspend fun readFolder(context: Context, tree: Uri): PickedFolder = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        val rootId = DocumentsContract.getTreeDocumentId(tree)
        var folderName = "audiobook"
        resolver.query(
            DocumentsContract.buildDocumentUriUsingTree(tree, rootId),
            arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null,
        )?.use { c -> if (c.moveToFirst()) c.getString(0)?.let { folderName = it } }
        val files = ArrayList<PickedFile>()
        resolver.query(
            DocumentsContract.buildChildDocumentsUriUsingTree(tree, rootId),
            arrayOf(
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
                DocumentsContract.Document.COLUMN_SIZE,
            ),
            null, null, null,
        )?.use { c ->
            while (c.moveToNext()) {
                val id = c.getString(0) ?: continue
                val name = c.getString(1) ?: continue
                val mime = c.getString(2)
                if (mime == DocumentsContract.Document.MIME_TYPE_DIR || !BookUploadRules.isAudio(name, mime)) continue
                val size = if (c.isNull(3)) null else c.getLong(3)
                files += PickedFile(DocumentsContract.buildDocumentUriUsingTree(tree, id), name, size)
            }
        }
        PickedFolder(folderName, files.sortedWith(compareBy(BookUploadRules.naturalOrder) { it.name }))
    }

    /**
     * Sends [files] one at a time into [folder] (null: each its own book).
     * [onProgress] hears (file index, its percent). Stops at the first
     * refusal, which it throws.
     */
    internal suspend fun send(
        context: Context,
        app: AppContainer,
        files: List<PickedFile>,
        folder: String?,
        onProgress: (index: Int, percent: Int) -> Unit,
    ): UploadResult {
        var saved = 0
        var skipped = 0
        var reindexed = false
        files.forEachIndexed { i, f ->
            val r = uploadContentUri(
                context, app, FILES_UPLOAD_PATH, BookUploadRules.fields(app.prefs.deviceId, folder, i), f.uri, f.name,
            ) { pct -> onProgress(i, pct) }
            saved += r.saved
            skipped += r.skipped
            reindexed = reindexed || r.reindexTriggered
        }
        return UploadResult(saved, skipped, reindexed)
    }
}
