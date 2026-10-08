package com.domovoi.app.ui.screens.files

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import android.provider.MediaStore
import android.provider.OpenableColumns
import com.domovoi.app.data.PhoneFolder
import com.domovoi.app.data.PhoneFolders
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.DeleteResult
import com.domovoi.app.net.MoveResult
import com.domovoi.app.net.UploadResult
import com.domovoi.app.ui.screens.documents.CsvSheet
import com.domovoi.app.ui.screens.documents.SheetCell
import com.domovoi.app.ui.screens.documents.SheetUnsupported
import com.domovoi.app.ui.screens.documents.XlsxSheet
import com.domovoi.app.ui.screens.documents.sheetChanges
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.FileNotFoundException
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.util.concurrent.ConcurrentHashMap

/** The text editor's read bound, the server's `_TEXT_MAX_BYTES`. */
internal const val TEXT_MAX_BYTES = 2 * 1024 * 1024

/**
 * Strict UTF-8, as the server's `data.decode("utf-8")`: null for anything
 * that is not valid UTF-8 (the text editor then says "binary").
 */
internal fun strictUtf8(bytes: ByteArray): String? = try {
    Charsets.UTF_8.newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
        .decode(ByteBuffer.wrap(bytes)).toString()
} catch (_: CharacterCodingException) {
    null
}

/**
 * The phone's own files: folders the person added through the system folder
 * picker (Storage Access Framework, a persisted read+write grant per folder)
 * and the photo library (MediaStore, READ_MEDIA_IMAGES). No all-files access.
 *
 * Paths are folder NAMES joined by "/" under the picked folder, the same
 * shape the server uses, resolved to document ids by walking the tree (and
 * remembered, so a listing does not re-walk). Photos are listed by album:
 * the root holds one folder per album (path = the album's bucket id), an
 * album holds its images (rel = "bucket/imageId").
 */
internal class PhoneFileSource(
    context: Context,
    private val prefs: Prefs,
) : FileSource {
    private val ctx = context.applicationContext
    private val resolver = ctx.contentResolver

    override val onPhone = true
    override val subtitle = "on this phone — folders you add, and your photos"
    override val viewsImagesInApp = true
    override val rawLabel = "open in another app"

    // ── libraries ───────────────────────────────────────────────────────

    /** The listed folders Android still holds a grant for; revoked ones drop off the list. */
    private fun grantedFolders(): Pair<List<PhoneFolder>, Set<String>> {
        val perms = resolver.persistedUriPermissions
        val read = perms.filter { it.isReadPermission }.map { it.uri.toString() }.toSet()
        val write = perms.filter { it.isWritePermission }.map { it.uri.toString() }.toSet()
        val listed = prefs.phoneFolders.value
        val kept = PhoneFolders.stillGranted(listed, read)
        if (kept != listed) prefs.setPhoneFolders(kept)
        return kept to write
    }

    override suspend fun libraries(): List<FileLibrary> = withContext(Dispatchers.IO) {
        val (folders, writable) = grantedFolders()
        folders.map { f ->
            FileLibrary(
                id = PhoneFolders.libraryId(f),
                label = f.name,
                kind = "folder",
                icon = "folder",
                editable = f.uri in writable,
                docEditing = true,
            )
        } + FileLibrary(
            id = PhoneFolders.PHOTOS_ID,
            label = "photos",
            kind = "photos",
            icon = "image",
            editable = false,
        )
    }

    override fun permissionFor(libraryId: String): String? =
        if (libraryId != PhoneFolders.PHOTOS_ID) null
        else if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES
        else Manifest.permission.READ_EXTERNAL_STORAGE

    override fun editsInApp(lib: FileLibrary?): Boolean = lib?.id?.startsWith(PhoneFolders.FOLDER_PREFIX) == true

    // ── tree walking ────────────────────────────────────────────────────

    private class Child(val id: String, val name: String, val mime: String, val size: Long?, val mtimeMs: Long?, val flags: Int) {
        val isDir: Boolean get() = mime == Document.MIME_TYPE_DIR
    }

    /** rel → document id, per tree. */
    private val ids = ConcurrentHashMap<String, String>()
    private fun key(tree: String, rel: String) = "$tree\u0000$rel"

    private fun tree(libraryId: String): Uri =
        Uri.parse(PhoneFolders.uriOf(libraryId) ?: throw FileNotFoundException("not a phone folder"))

    private fun children(tree: Uri, dirId: String): List<Child> {
        val uri = DocumentsContract.buildChildDocumentsUriUsingTree(tree, dirId)
        val proj = arrayOf(
            Document.COLUMN_DOCUMENT_ID, Document.COLUMN_DISPLAY_NAME, Document.COLUMN_MIME_TYPE,
            Document.COLUMN_SIZE, Document.COLUMN_LAST_MODIFIED, Document.COLUMN_FLAGS,
        )
        val out = mutableListOf<Child>()
        resolver.query(uri, proj, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                out += Child(
                    id = c.getString(0),
                    name = c.getString(1) ?: c.getString(0),
                    mime = c.getString(2) ?: "application/octet-stream",
                    size = if (c.isNull(3)) null else c.getLong(3),
                    mtimeMs = if (c.isNull(4)) null else c.getLong(4),
                    flags = if (c.isNull(5)) 0 else c.getInt(5),
                )
            }
        } ?: throw FileNotFoundException("this folder can't be read any more")
        return out
    }

    /** The document id at [rel] under [tree] ("" is the picked folder itself). */
    private fun docId(tree: Uri, rel: String): String {
        val t = tree.toString()
        ids[key(t, rel)]?.let { return it }
        if (rel.isEmpty()) {
            return DocumentsContract.getTreeDocumentId(tree).also { ids[key(t, "")] = it }
        }
        val parentRel = rel.substringBeforeLast('/', "")
        val name = rel.substringAfterLast('/')
        val parent = docId(tree, parentRel)
        val kids = children(tree, parent)
        kids.forEach { ids[key(t, join(parentRel, it.name))] = it.id }
        return kids.firstOrNull { it.name == name }?.id ?: throw FileNotFoundException("$rel is gone")
    }

    private fun docUri(tree: Uri, rel: String): Uri = DocumentsContract.buildDocumentUriUsingTree(tree, docId(tree, rel))

    private fun join(dir: String, name: String) = if (dir.isEmpty()) name else "$dir/$name"

    private fun forget(tree: Uri, relPrefix: String) {
        val t = tree.toString()
        ids.keys.removeAll { k ->
            val (kt, kr) = k.split('\u0000', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
            kt == t && (kr == relPrefix || kr.startsWith("$relPrefix/"))
        }
    }

    // ── browse ──────────────────────────────────────────────────────────

    override suspend fun browse(libraryId: String, path: String): FileBrowse = withContext(Dispatchers.IO) {
        if (libraryId == PhoneFolders.PHOTOS_ID) return@withContext browsePhotos(path)
        val tree = tree(libraryId)
        val dirId = docId(tree, path)
        val kids = children(tree, dirId)
        val t = tree.toString()
        kids.forEach { ids[key(t, join(path, it.name))] = it.id }
        val writable = resolver.persistedUriPermissions.any { it.uri.toString() == t && it.isWritePermission }
        FileBrowse(
            libraryId = libraryId,
            path = path,
            editable = writable,
            docEditing = true,
            breadcrumb = path.split('/').filter { it.isNotEmpty() },
            entries = kids
                .sortedWith(compareBy<Child>({ !it.isDir }, { it.name.lowercase() }))
                .map { k ->
                    FileEntry(
                        name = k.name,
                        rel = join(path, k.name),
                        isDir = k.isDir,
                        size = if (k.isDir) null else k.size,
                        mtime = k.mtimeMs?.div(1000.0),
                        kind = fileKind(k.name, k.isDir),
                    )
                },
            writable = writable,
        )
    }

    private fun browsePhotos(path: String): FileBrowse {
        val images = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        if (path.isEmpty()) {
            class Album(val id: String, var name: String, var count: Int, var latest: Long)
            val albums = LinkedHashMap<String, Album>()
            resolver.query(
                images,
                arrayOf(
                    MediaStore.Images.Media.BUCKET_ID,
                    MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
                    MediaStore.Images.Media.DATE_MODIFIED,
                ),
                null, null, "${MediaStore.Images.Media.DATE_MODIFIED} DESC",
            )?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getString(0) ?: continue
                    val a = albums.getOrPut(id) { Album(id, c.getString(1) ?: "photos", 0, c.getLong(2)) }
                    a.count++
                }
            }
            return FileBrowse(
                libraryId = PhoneFolders.PHOTOS_ID,
                path = "",
                entries = albums.values.map { a ->
                    FileEntry(name = a.name, rel = a.id, isDir = true, mtime = a.latest.toDouble(), kind = "folder")
                },
            )
        }
        val bucket = path.substringBefore('/')
        var albumName = "photos"
        val entries = mutableListOf<FileEntry>()
        resolver.query(
            images,
            arrayOf(
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.SIZE,
                MediaStore.Images.Media.DATE_MODIFIED,
                MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
            ),
            "${MediaStore.Images.Media.BUCKET_ID} = ?", arrayOf(bucket),
            "${MediaStore.Images.Media.DATE_MODIFIED} DESC",
        )?.use { c ->
            while (c.moveToNext()) {
                albumName = c.getString(4) ?: albumName
                val name = c.getString(1) ?: "image"
                entries += FileEntry(
                    name = name,
                    rel = "$bucket/${c.getLong(0)}",
                    size = c.getLong(2),
                    mtime = c.getLong(3).toDouble(),
                    kind = "image",
                )
            }
        }
        return FileBrowse(
            libraryId = PhoneFolders.PHOTOS_ID,
            path = bucket,
            breadcrumb = listOf(albumName),
            crumbPaths = listOf(bucket),
            grid = true,
            entries = entries,
        )
    }

    // ── content ─────────────────────────────────────────────────────────

    override fun contentUri(libraryId: String, rel: String): Uri? = runCatching {
        if (libraryId == PhoneFolders.PHOTOS_ID) {
            ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, rel.substringAfterLast('/').toLong())
        } else {
            docUri(tree(libraryId), rel)
        }
    }.getOrNull()

    override fun imageModel(libraryId: String, entry: FileEntry): Any? = contentUri(libraryId, entry.rel)

    override fun openFile(context: Context, libraryId: String, entry: FileEntry): Boolean {
        val uri = contentUri(libraryId, entry.rel) ?: return false
        val type = resolver.getType(uri)?.takeIf { it != "application/octet-stream" } ?: mimeFor(entry.name)
        var flags = Intent.FLAG_GRANT_READ_URI_PERMISSION
        // A folder file can be edited in the app it opens in.
        if (libraryId != PhoneFolders.PHOTOS_ID) flags = flags or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, type).addFlags(flags)
        return try {
            context.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        }
    }

    override fun openRaw(context: Context, libraryId: String, rel: String) {
        openFile(context, libraryId, FileEntry(name = rel.substringAfterLast('/'), rel = rel, kind = fileKind(rel, false)))
    }

    private fun readBytes(uri: Uri, limit: Long = Long.MAX_VALUE): ByteArray =
        resolver.openInputStream(uri)?.use { input ->
            val out = java.io.ByteArrayOutputStream()
            val buf = ByteArray(64 * 1024)
            var total = 0L
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                total += n
                if (total > limit) throw TooLarge()
                out.write(buf, 0, n)
            }
            out.toByteArray()
        } ?: throw FileNotFoundException("can't open the file")

    private class TooLarge : IOException("too large")

    /** Replace the file's content; "wt" truncates, and a provider that refuses it gets "rwt". */
    private fun writeBytes(uri: Uri, bytes: ByteArray) {
        val out = runCatching { resolver.openOutputStream(uri, "wt") }.getOrNull()
            ?: resolver.openOutputStream(uri, "rwt")
            ?: throw IOException("can't write the file")
        out.use { it.write(bytes) }
    }

    private fun fileUri(libraryId: String, rel: String): Uri {
        if (libraryId == PhoneFolders.PHOTOS_ID) throw IOException("photos are read-only here")
        return docUri(tree(libraryId), rel)
    }

    override suspend fun readText(libraryId: String, rel: String): TextLoad = withContext(Dispatchers.IO) {
        val bytes = try {
            readBytes(fileUri(libraryId, rel), TEXT_MAX_BYTES.toLong())
        } catch (_: TooLarge) {
            return@withContext TextLoad.Unpreviewable("too_large")
        }
        strictUtf8(bytes)?.let { TextLoad.Ready(it) } ?: TextLoad.Unpreviewable("binary")
    }

    override suspend fun writeText(libraryId: String, rel: String, text: String) = withContext(Dispatchers.IO) {
        writeBytes(fileUri(libraryId, rel), text.toByteArray(Charsets.UTF_8))
    }

    override suspend fun readSheet(libraryId: String, rel: String): List<List<SheetCell?>> = withContext(Dispatchers.IO) {
        when (extOf(rel)) {
            "xlsx" -> XlsxSheet.read(readBytes(fileUri(libraryId, rel)))
            "csv" -> CsvSheet.read(readBytes(fileUri(libraryId, rel)))
            else -> throw SheetUnsupported()
        }
    }

    override suspend fun writeSheet(
        libraryId: String,
        rel: String,
        loaded: List<List<String>>,
        edited: List<List<String>>,
    ) = withContext(Dispatchers.IO) {
        val uri = fileUri(libraryId, rel)
        when (extOf(rel)) {
            "xlsx" -> {
                val changes = sheetChanges(loaded, edited)
                if (changes.isNotEmpty()) writeBytes(uri, XlsxSheet.patch(readBytes(uri), changes))
            }
            "csv" -> {
                sheetChanges(loaded, edited) // the same bounds check as xlsx
                val before = CsvSheet.decode(readBytes(uri))
                writeBytes(
                    uri,
                    CsvSheet.write(edited, CsvSheet.parse(before), CsvSheet.newlineOf(before)).toByteArray(Charsets.UTF_8),
                )
            }
            else -> throw SheetUnsupported()
        }
    }

    // ── mutations ───────────────────────────────────────────────────────

    private fun displayName(uri: Uri): String? = runCatching {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    }.getOrNull()

    override suspend fun upload(context: Context, libraryId: String, path: String, uris: List<Uri>): UploadResult =
        withContext(Dispatchers.IO) {
            val tree = tree(libraryId)
            val parent = docUri(tree, path)
            var saved = 0
            var skipped = 0
            for (src in uris) {
                val name = displayName(src) ?: src.lastPathSegment ?: "file"
                val ok = runCatching { copyInto(parent, src, name) }.isSuccess
                if (ok) saved++ else skipped++
            }
            forget(tree, path)
            if (saved == 0 && skipped > 0) throw IOException("couldn't copy into this folder")
            UploadResult(saved, skipped, reindexTriggered = false)
        }

    /** A new file [name] in [parent] with [src]'s bytes. The provider picks "name (1)" on a clash. */
    private fun copyInto(parent: Uri, src: Uri, name: String): Uri {
        // octet-stream: a typed MIME makes some providers append their own
        // extension ("notes.md" + text/plain → "notes.md.txt").
        val made = DocumentsContract.createDocument(resolver, parent, "application/octet-stream", name)
            ?: throw IOException("couldn't create $name")
        try {
            resolver.openInputStream(src)?.use { input ->
                resolver.openOutputStream(made, "w")?.use { input.copyTo(it) } ?: throw IOException("can't write $name")
            } ?: throw IOException("can't read $name")
        } catch (e: Exception) {
            runCatching { DocumentsContract.deleteDocument(resolver, made) }
            throw e
        }
        return made
    }

    override suspend fun delete(libraryId: String, entry: FileEntry): DeleteResult = withContext(Dispatchers.IO) {
        val tree = tree(libraryId)
        val uri = docUri(tree, entry.rel)
        if (!DocumentsContract.deleteDocument(resolver, uri)) throw IOException("the phone refused to delete it")
        forget(tree, entry.rel)
        DeleteResult(deleted = 1, failed = 0, reindexTriggered = false)
    }

    override suspend fun move(
        sourceLibraryId: String,
        entry: FileEntry,
        targetLibraryId: String,
        targetPath: String,
    ): MoveResult = withContext(Dispatchers.IO) {
        val srcTree = tree(sourceLibraryId)
        val dstTree = tree(targetLibraryId)
        val srcParentRel = entry.rel.substringBeforeLast('/', "")
        if (sourceLibraryId == targetLibraryId && srcParentRel == targetPath) {
            return@withContext MoveResult(0, 1, 0, null, false)
        }
        val dstParentId = docId(dstTree, targetPath)
        if (children(dstTree, dstParentId).any { it.name == entry.name }) {
            return@withContext MoveResult(0, 0, 1, "\"${entry.name}\" already exists there", false)
        }
        val src = docUri(srcTree, entry.rel)
        val srcParent = docUri(srcTree, srcParentRel)
        val dstParent = DocumentsContract.buildDocumentUriUsingTree(dstTree, dstParentId)
        val moved = if (src.authority == dstParent.authority) {
            runCatching { DocumentsContract.moveDocument(resolver, src, srcParent, dstParent) }.getOrNull()
        } else {
            null
        }
        if (moved == null) {
            // No native move (or across providers): copy, then delete the original.
            copyTree(src, entry.isDir, entry.name, dstParent)
            if (!DocumentsContract.deleteDocument(resolver, src)) {
                throw IOException("copied, but the original could not be removed")
            }
        }
        forget(srcTree, entry.rel)
        forget(dstTree, targetPath)
        MoveResult(moved = 1, skipped = 0, failed = 0, firstFailure = null, reindexTriggered = false)
    }

    private fun copyTree(src: Uri, isDir: Boolean, name: String, dstParent: Uri) {
        if (!isDir) {
            copyInto(dstParent, src, name)
            return
        }
        val made = DocumentsContract.createDocument(resolver, dstParent, Document.MIME_TYPE_DIR, name)
            ?: throw IOException("couldn't create folder $name")
        val tree = src // a tree-based document uri: children are listed under the same tree
        val kids = children(tree, DocumentsContract.getDocumentId(src))
        for (k in kids) {
            copyTree(DocumentsContract.buildDocumentUriUsingTree(tree, k.id), k.isDir, k.name, made)
        }
    }
}
