import Foundation

/// What a file **is**, and what tapping its row does.
///
/// `android/.../ui/screens/files/FileKinds.kt`, which is itself a copy of
/// `web/backend/api/files.py`'s `_entry_kind` and the extension sets it
/// borrows. The server sends `kind` with every entry and this screen uses the
/// server's answer; these rules are here for the places there is no server
/// answer to use — a name from a scan, a row built by a test, and the
/// `.html`-is-a-document rule, which is the dashboard's and not the
/// backend's.
///
/// ### Where this deliberately differs from the Kotlin
///
/// Android's `openWith` routes a file to one of four destinations: the sheet
/// editor, the text editor, the image viewer or another app. This app has no
/// editors — editing documents is the dashboard's job and no iOS screen in
/// this batch writes a file — so the question it answers is a different one
/// (``FileKinds/action(for:in:saveCap:)``), with four answers of its own, and
/// one of them is "there is no control for this". A port that kept the
/// Kotlin's four names would have had to make two of them lie.
public enum FileKinds {

    // MARK: - the extension sets, as the server has them

    static let audio: Set<String> = [
        "mp3", "m4a", "m4b", "mp4", "aac", "flac", "ogg", "oga", "opus", "wav",
    ]
    static let video: Set<String> = ["mp4", "m4v", "mov", "webm", "mkv"]
    static let officeWordProcessor: Set<String> = ["docx", "doc", "odt", "rtf"]
    static let sheets: Set<String> = ["xlsx", "xls", "ods", "csv"]
    static let images: Set<String> = [
        "png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg",
    ]
    static let drawings: Set<String> = ["excalidraw", "svg"]
    static let text: Set<String> = [
        "txt", "md", "markdown", "rst",
        "json", "csv", "tsv", "log",
        "yaml", "yml", "ini", "toml", "conf", "cfg",
        "xml", "html", "htm", "css",
        "js", "jsx", "ts", "tsx", "py", "sh", "bash", "ps1",
        "sql", "c", "h", "cpp", "java", "go", "rs",
    ]

    /// What the server's Documents view lists and accepts
    /// (`web/backend/api/doc_types.py`'s `DOCUMENT_EXTS`); everything else in
    /// that one folder is Other.
    ///
    /// **Its only reader is the pin.** Nothing this screen draws branches on
    /// it: the server already split the folder into two libraries before the
    /// app saw it. It is here because the server *sends* its own copy
    /// (`document_exts`) and ``FileLibraries/divergence(from:)`` can then
    /// hold the two against each other — the same pinning
    /// `domovoi/tests/test_files_other_android.py` does for the Kotlin table.
    /// The batch that adds uploading is the one that will need it to decide
    /// where a mixed selection goes, and it will find it already checked.
    public static let documentExtensions: Set<String> =
        text
        .union(officeWordProcessor)
        .union(sheets)
        .union(drawings)
        .union(["doc", "docx", "odt", "rtf", "txt", "md", "markdown", "pdf", "html", "htm"])

    /// The document editor's own files: `.html`, and `.htm`, the same format.
    /// Lists show them by name with no extension, as the dashboard does —
    /// people are never shown "HTML".
    static let formattedDocuments: Set<String> = ["html", "htm"]

    // MARK: - the rules

    /// The lower-cased extension without its dot, or `""`.
    ///
    /// **Python's `Path.suffix`, not the last dot.** `.bashrc` has no
    /// extension and neither does `notes.` — the leading-dot case is the one
    /// that matters, because a dotfile classified by its name would be
    /// `doc-text` for `.bashrc` and the server calls it `other`.
    public static func ext(of name: String) -> String {
        guard let dot = name.lastIndex(of: ".") else { return "" }
        guard dot != name.startIndex, dot != name.index(before: name.endIndex) else { return "" }
        return String(name[name.index(after: dot)...]).lowercased()
    }

    /// Whether a file of this name belongs to Documents rather than Other.
    /// Read only by the pin — see ``documentExtensions``.
    public static func isDocument(_ name: String) -> Bool {
        documentExtensions.contains(ext(of: name))
    }

    public static func isFormattedDocument(_ name: String, isDir: Bool = false) -> Bool {
        !isDir && formattedDocuments.contains(ext(of: name))
    }

    /// The name a list shows: a formatted document without its extension.
    public static func displayName(of name: String, isDir: Bool = false) -> String {
        guard isFormattedDocument(name, isDir: isDir), let dot = name.lastIndex(of: ".") else {
            return name
        }
        return String(name[name.startIndex..<dot])
    }

    /// `folder` · `audio` · `video` · `pdf` · `doc-office` · `image` ·
    /// `doc-text` · `other`, in the server's own order of precedence.
    ///
    /// The order is the part that matters and the reason this is not a
    /// dictionary lookup: `.mp4` is in both the audio and the video sets and
    /// the server calls it `audio` (audio first), `.svg` is in both the image
    /// and the drawing sets and the server calls it `image`, and `.csv` is in
    /// both the sheet and the text sets and the server calls it `doc-office`.
    /// Three overlaps, three answers, one `if`-chain copied in order.
    public static func kind(of name: String, isDir: Bool) -> String {
        if isDir { return "folder" }
        let extension_ = ext(of: name)
        if audio.contains(extension_) { return "audio" }
        if video.contains(extension_) { return "video" }
        if extension_ == "pdf" { return "pdf" }
        if officeWordProcessor.contains(extension_) || sheets.contains(extension_) { return "doc-office" }
        if images.contains(extension_) { return "image" }
        if text.contains(extension_) || drawings.contains(extension_) { return "doc-text" }
        return "other"
    }

    // MARK: - what a row's tap does

    /// What tapping a row does **in this app**.
    ///
    /// Android's equivalent is `primaryVerb`'s six strings over `openWith`'s
    /// four destinations. The shapes differ because the capabilities do: this
    /// app has no editors, hands nothing to another app, and reads a file's
    /// bytes through its own client, so there are four outcomes and each one
    /// is something a screen can actually do.
    public enum FileAction: Equatable, Sendable {

        /// A folder: list it.
        case open

        /// Read it in the app, as text. **Documents only** — the route that
        /// serves a file as text (`/api/documents/text/{rel}`) resolves its
        /// path inside the Documents folder and nowhere else, so a `.txt` in
        /// the Music library is not readable this way and does not offer it.
        case read

        /// Look at it in the app, as a picture. Any library: `/api/images/raw`
        /// takes a `library_id`.
        case view

        /// Fetch the bytes and hand the person a file (the share sheet).
        ///
        /// **Any size.** There used to be a fifth case, `tooBig`, for a file
        /// over ``FileSaveModel``'s 64 MiB ceiling; the save streams to disk
        /// now (D47, ``SaveToDeviceModel``) so there is no ceiling and no
        /// row that refuses.
        case save
    }

    /// The action for `entry` in `library`.
    ///
    /// **No size argument.** It took a `saveCap` until D47: a file over
    /// ``FileSaveModel``'s 64 MiB ceiling answered `tooBig` and the row drew
    /// a line instead of a control. The save streams to disk now
    /// (``SaveToDeviceModel``), so a row's size decides nothing here and the
    /// four answers are about what the file *is*.
    ///
    /// A folder is always ``FileAction/open`` — including in a library this
    /// app may not write to, because listing is a read and reads are never
    /// blocked. It is never ``FileAction/save``, which is the one thing
    /// Android's row can do that this one cannot: there a folder downloads as
    /// a server-built zip, and this app has nothing to unpack one with.
    public static func action(
        for entry: FileEntry,
        in library: FileLibrary?
    ) -> FileAction {
        if entry.isDir { return .open }
        if entry.kind == "image" { return .view }
        if library?.id == FileLibrary.documents, entry.kind == "doc-text" { return .read }
        return .save
    }

    /// The word for what the tap does, which is the row's accessibility
    /// action and the first line of its menu.
    ///
    /// `FilesScreen.kt:1095`'s rule, which this keeps: **the label never
    /// promises something the tap does not do**. Optional still, although
    /// every case now has a word: the one that had none was `tooBig`, and a
    /// non-optional return here would mean a fifth action could not be added
    /// without one.
    public static func verb(_ action: FileAction) -> String? {
        switch action {
        case .open: return FileStrings.verbOpen
        case .read: return FileStrings.verbRead
        case .view: return FileStrings.verbView
        case .save: return FileStrings.verbSave
        }
    }
}
