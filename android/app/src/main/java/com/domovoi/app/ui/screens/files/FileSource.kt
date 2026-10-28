package com.domovoi.app.ui.screens.files

import android.content.Context
import android.net.Uri
import com.domovoi.app.AppContainer
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DeleteResult
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.ImportResult
import com.domovoi.app.net.MoveResult
import com.domovoi.app.net.UploadResult
import com.domovoi.app.net.decode
import com.domovoi.app.net.deleteFiles
import com.domovoi.app.net.PageRequest
import com.domovoi.app.net.Paging
import com.domovoi.app.net.filesBrowseQueryPath
import com.domovoi.app.net.importFile
import com.domovoi.app.net.moveFiles
import com.domovoi.app.net.ServerFileTransfer
import com.domovoi.app.net.filesDownloadPath
import com.domovoi.app.net.imagesRawPath
import com.domovoi.app.net.uploadFiles
import com.domovoi.app.ui.components.FILES_CHANGED
import com.domovoi.app.ui.screens.documents.SheetCell
import com.domovoi.app.ui.screens.documents.SheetUnsupported
import com.domovoi.app.ui.screens.documents.docTextPath
import com.domovoi.app.ui.screens.documents.docRawPath
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.Request
import java.io.IOException

/** What the text editor gets for a file. */
internal sealed interface TextLoad {
    data class Ready(val text: String) : TextLoad

    /** Not shown as text: [reason] is "too_large" or "binary", as the server says it. */
    data class Unpreviewable(val reason: String) : TextLoad
}

/**
 * Where the Files screen and its editors get their files: the server's
 * `/api/files` + `/api/documents` ([ServerFileSource]) or the phone's own
 * folders and photos ([PhoneFileSource]). One screen
 * ([FilesBrowser]), one text editor, one sheet editor serve both; each verb a
 * source cannot do is switched off by its flags rather than special-cased in
 * the UI.
 *
 * Libraries and paths keep the server's shape: a [FileLibrary.id] plus a
 * RELATIVE path of folder names joined by "/".
 */
internal interface FileSource {
    /** The phone's own files (no server involved). */
    val onPhone: Boolean

    /** The page header's subtitle. */
    val subtitle: String

    /** WS events that should refresh a listing. */
    val browseEvents: Set<String> get() = emptySet()

    suspend fun libraries(): List<FileLibrary>

    /**
     * One page of a folder (net/Paging.kt): [FileBrowse.entries] is the page,
     * folders first. [q] keeps names containing it and [foldersOnly] keeps
     * folders (a destination picker); both apply to the whole folder.
     */
    suspend fun browsePage(
        libraryId: String,
        path: String,
        req: PageRequest,
        q: String = "",
        foldersOnly: Boolean = false,
    ): FileBrowse

    /** A runtime permission [libraryId] needs before it can be listed (photos). */
    fun permissionFor(libraryId: String): String? = null

    /** Whether files in [lib] open in the in-app text / sheet editors. */
    fun editsInApp(lib: FileLibrary?): Boolean

    /** Images open in the in-app viewer rather than another app. */
    val viewsImagesInApp: Boolean get() = false

    /** Open a file that has no in-app editor, in whatever the source uses for it. */
    fun openFile(context: Context, libraryId: String, entry: FileEntry, report: (String) -> Unit = {}): Boolean

    /** Save a copy to the device (the server's download verb). */
    val canDownload: Boolean get() = false
    fun download(context: Context, libraryId: String, entry: FileEntry, report: (String) -> Unit = {}) {}

    /** Removable-drive import into another library (server only). */
    suspend fun import(sourceLibraryId: String, entry: FileEntry, target: FileLibrary): ImportResult =
        throw UnsupportedOperationException("import")

    /** Copy picked files ([uris]) into a folder. */
    suspend fun upload(context: Context, libraryId: String, path: String, uris: List<Uri>): UploadResult

    suspend fun delete(libraryId: String, entry: FileEntry): DeleteResult

    suspend fun move(sourceLibraryId: String, entry: FileEntry, targetLibraryId: String, targetPath: String): MoveResult

    suspend fun readText(libraryId: String, rel: String): TextLoad

    suspend fun writeText(libraryId: String, rel: String, text: String)

    /** The sheet grid; throws [SheetUnsupported] for a type the editor can't round-trip. */
    suspend fun readSheet(libraryId: String, rel: String): List<List<SheetCell?>>

    /**
     * Save the sheet. [loaded] is the grid as the editor first showed it,
     * [edited] as it is now (both padded string grids; "=..." is a formula).
     */
    suspend fun writeSheet(libraryId: String, rel: String, loaded: List<List<String>>, edited: List<List<String>>)

    /** The text editor's way out for a file it can't show ("download", "open in another app"). */
    val rawLabel: String
    fun openRaw(context: Context, libraryId: String, rel: String, report: (String) -> Unit = {})

    /** Image data for the in-app viewer / photo grid (a content Uri on the phone). */
    fun imageModel(libraryId: String, entry: FileEntry): Any? = null

    /** A content Uri the bytes can be read from (send to home). */
    fun contentUri(libraryId: String, rel: String): Uri? = null
}

// ---------------------------------------------------------------------------
// The server: exactly the calls FilesScreen, DocumentsEditor and SheetEditor
// made before the abstraction existed.
// ---------------------------------------------------------------------------

@Serializable
private data class SheetGrid(val rows: List<List<SheetCell?>> = emptyList())

internal const val DOCUMENTS_LIBRARY_ID = "core:documents"

/** Other: the shared Documents folder's files that are not documents
 *  (installers, APKs, archives). Kept as they are: download only. */
internal const val OTHER_LIBRARY_ID = "core:other"

internal fun sheetApiPath(rel: String): String = "/api/documents/sheet/" + Uri.encode(rel, "/")

internal class ServerFileSource(private val app: AppContainer) : FileSource {
    override val onPhone = false
    override val subtitle = "browse every library — music, docs, plugins & removable drives"
    // library.indexer: the music index moved; files.changed: any library's
    // folder changed (an upload from another device, a delete, a file a
    // plugin wrote; web/backend/api/listing_cache.py).
    override val browseEvents = setOf("library.indexer.changed", FILES_CHANGED)

    override suspend fun libraries(): List<FileLibrary> =
        app.api.get("/api/files/libraries").decode<LibrariesResponse>().libraries

    override suspend fun browsePage(
        libraryId: String,
        path: String,
        req: PageRequest,
        q: String,
        foldersOnly: Boolean,
    ): FileBrowse = app.api.get(
        Paging.path(filesBrowseQueryPath(libraryId, path, app.prefs.deviceId, q, foldersOnly), req),
    ).decode<FileBrowse>()

    // The Documents library edits in-app; every other library opens files
    // with the system viewer.
    override fun editsInApp(lib: FileLibrary?): Boolean = lib?.id == DOCUMENTS_LIBRARY_ID

    // Every server file is fetched by the app, which carries the device
    // token, and handed on as a file (ServerFileTransfer), never as a
    // server address another app would open without the token.
    override fun openFile(context: Context, libraryId: String, entry: FileEntry, report: (String) -> Unit): Boolean {
        val name = ServerFileTransfer.saveName(entry.rel, entry.isDir)
        when {
            // A Documents file the editors don't take (PDF, .doc...): opened
            // in the app that views it.
            libraryId == DOCUMENTS_LIBRARY_ID && !entry.isDir ->
                ServerFileTransfer.open(context, app, docRawPath(entry.rel), name, mimeFor(entry.name), report)
            // Other's files are only ever downloaded; nothing opens them.
            libraryId == OTHER_LIBRARY_ID -> download(context, libraryId, entry, report)
            // Images in ANY library open in the phone's viewer, from the
            // generic library-image serve (web-parity with the Files tab's
            // "Open" action).
            entry.kind == "image" ->
                ServerFileTransfer.open(context, app, imagesRawPath(libraryId, entry.rel), name, mimeFor(entry.name), report)
            else -> download(context, libraryId, entry, report)
        }
        return true
    }

    override val canDownload = true

    // Documents files reuse the /api/documents/raw attachment serve; every
    // other library (and any directory → server zip) uses /api/files/download.
    override fun download(context: Context, libraryId: String, entry: FileEntry, report: (String) -> Unit) {
        val path = if (libraryId == DOCUMENTS_LIBRARY_ID && !entry.isDir) docRawPath(entry.rel)
        else filesDownloadPath(libraryId, entry.rel)
        ServerFileTransfer.download(context, app, path, ServerFileTransfer.saveName(entry.rel, entry.isDir), report)
    }

    override suspend fun import(sourceLibraryId: String, entry: FileEntry, target: FileLibrary): ImportResult =
        importFile(app, sourceLibraryId, entry.rel, target.id, "")

    override suspend fun upload(context: Context, libraryId: String, path: String, uris: List<Uri>): UploadResult =
        uploadFiles(context, app, libraryId, path, uris)

    override suspend fun delete(libraryId: String, entry: FileEntry): DeleteResult =
        deleteFiles(app, libraryId, listOf(entry.rel), recursive = entry.isDir)

    override suspend fun move(
        sourceLibraryId: String,
        entry: FileEntry,
        targetLibraryId: String,
        targetPath: String,
    ): MoveResult = moveFiles(app, sourceLibraryId, listOf(entry.rel), targetLibraryId, targetPath)

    override suspend fun readText(libraryId: String, rel: String): TextLoad = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(app.api.absolute(docTextPath(rel))).build()
        app.api.http.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            when {
                resp.code == 415 -> {
                    val reason = runCatching {
                        DomovoiJson.parseToJsonElement(body).jsonObject["reason"]?.jsonPrimitive?.contentOrNull
                    }.getOrNull()
                    TextLoad.Unpreviewable(reason ?: "binary")
                }
                !resp.isSuccessful -> throw IOException("${resp.code} ${resp.message}")
                else -> TextLoad.Ready(
                    runCatching {
                        DomovoiJson.parseToJsonElement(body).jsonObject["text"]?.jsonPrimitive?.contentOrNull
                    }.getOrNull() ?: "",
                )
            }
        }
    }

    override suspend fun writeText(libraryId: String, rel: String, text: String) {
        app.api.put(docTextPath(rel), buildJsonObject { put("text", text) })
    }

    override suspend fun readSheet(libraryId: String, rel: String): List<List<SheetCell?>> =
        try {
            app.api.get(sheetApiPath(rel)).decode<SheetGrid>().rows
        } catch (e: ApiException) {
            if (e.status == 415) throw SheetUnsupported() else throw e
        }

    override suspend fun writeSheet(
        libraryId: String,
        rel: String,
        loaded: List<List<String>>,
        edited: List<List<String>>,
    ) {
        app.api.put(
            sheetApiPath(rel),
            buildJsonObject {
                put("rows", buildJsonArray {
                    edited.forEach { row ->
                        add(buildJsonArray {
                            row.forEach { t ->
                                add(buildJsonObject {
                                    if (t.startsWith("=")) put("f", t) else put("v", t)
                                })
                            }
                        })
                    }
                })
            },
        )
    }

    override val rawLabel = "download"

    override fun openRaw(context: Context, libraryId: String, rel: String, report: (String) -> Unit) =
        ServerFileTransfer.download(context, app, docRawPath(rel), ServerFileTransfer.saveName(rel, false), report)
}
