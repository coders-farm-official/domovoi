package com.domovoi.app.ui.screens.files

/**
 * File-kind rules shared by both file sources, so a file on the phone is
 * classified and opened exactly as the same file would be on the server.
 *
 * [fileKind] mirrors web/backend/api/files.py `_entry_kind` (and the
 * extension sets it borrows from documents.py and audio_serve.py); the
 * server sends `kind` itself, the phone computes it here.
 */

private val AUDIO_EXTS = setOf("mp3", "m4a", "m4b", "mp4", "aac", "flac", "ogg", "oga", "opus", "wav")
private val VIDEO_EXTS = setOf("mp4", "m4v", "mov", "webm", "mkv")
private val OFFICE_WP_EXTS = setOf("docx", "doc", "odt", "rtf")
private val SHEET_EXTS = setOf("xlsx", "xls", "ods", "csv")
private val IMAGE_EXTS = setOf("png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg")
private val DRAWING_EXTS = setOf("excalidraw", "svg")
private val TEXT_EXTS = setOf(
    "txt", "md", "markdown", "rst",
    "json", "csv", "tsv", "log",
    "yaml", "yml", "ini", "toml", "conf", "cfg",
    "xml", "html", "htm", "css",
    "js", "jsx", "ts", "tsx", "py", "sh", "bash", "ps1",
    "sql", "c", "h", "cpp", "java", "go", "rs",
)

/** Lower-case extension without the dot ("" when there is none). */
internal fun extOf(name: String): String {
    // Python's Path.suffix: ".bashrc" and "notes." have none.
    val i = name.lastIndexOf('.')
    if (i <= 0 || i == name.length - 1) return ""
    return name.substring(i + 1).lowercase()
}

/** kind ∈ folder | audio | video | doc-office | doc-text | image | pdf | other. */
internal fun fileKind(name: String, isDir: Boolean): String {
    if (isDir) return "folder"
    val ext = extOf(name)
    return when {
        ext in AUDIO_EXTS -> "audio"
        ext in VIDEO_EXTS -> "video"
        ext == "pdf" -> "pdf"
        ext in OFFICE_WP_EXTS || ext in SHEET_EXTS -> "doc-office"
        ext in IMAGE_EXTS -> "image"
        ext in TEXT_EXTS || ext in DRAWING_EXTS -> "doc-text"
        else -> "other"
    }
}

/** What tapping a file does. */
internal enum class OpenWith { TextEditor, SheetEditor, ImageViewer, System }

/**
 * The open action for a file in an in-app-editing library (the server's
 * Documents library, every phone folder): .xlsx/.csv in the sheet editor,
 * text and markdown in the text editor, images in the viewer, everything
 * else (PDF, office documents, media) in another app — the routing
 * FilesScreen has always used for Documents.
 */
internal fun openWith(name: String, kind: String): OpenWith {
    val ext = extOf(name)
    return when {
        ext == "xlsx" || ext == "csv" -> OpenWith.SheetEditor
        kind == "doc-text" -> OpenWith.TextEditor
        kind == "image" -> OpenWith.ImageViewer
        else -> OpenWith.System
    }
}

/** A MIME type for handing a file to another app, from its extension. */
internal fun mimeFor(name: String): String = when (extOf(name)) {
    "pdf" -> "application/pdf"
    "txt", "log" -> "text/plain"
    "md", "markdown" -> "text/markdown"
    "csv" -> "text/csv"
    "html", "htm" -> "text/html"
    "json" -> "application/json"
    "xml" -> "text/xml"
    "png" -> "image/png"
    "jpg", "jpeg" -> "image/jpeg"
    "gif" -> "image/gif"
    "webp" -> "image/webp"
    "bmp" -> "image/bmp"
    "svg" -> "image/svg+xml"
    "avif" -> "image/avif"
    "mp3" -> "audio/mpeg"
    "m4a", "m4b" -> "audio/mp4"
    "flac" -> "audio/flac"
    "ogg", "oga", "opus" -> "audio/ogg"
    "wav" -> "audio/wav"
    "mp4", "m4v" -> "video/mp4"
    "mov" -> "video/quicktime"
    "webm" -> "video/webm"
    "mkv" -> "video/x-matroska"
    "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    "doc" -> "application/msword"
    "odt" -> "application/vnd.oasis.opendocument.text"
    "rtf" -> "application/rtf"
    "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    "xls" -> "application/vnd.ms-excel"
    "ods" -> "application/vnd.oasis.opendocument.spreadsheet"
    "zip" -> "application/zip"
    else -> "application/octet-stream"
}
