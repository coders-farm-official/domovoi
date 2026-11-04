import Foundation

/// How the server's Documents view opens a file: the port of
/// `web/backend/api/documents.py`'s `_doc_category` (`:207`).
///
/// It is the **routing table for every editor in this item**, and it is the
/// server's table rather than Android's because the save routes ask it the
/// mirror question — `_assert_text_editor_may_write` (`:447`) and
/// `_assert_sheet_editor_may_write` (`:521`) both refuse a target this
/// answers differently for. An app that opened an editor the server would
/// refuse the save from would be a Save button that 415s every time.
public enum DocumentsCategory: String, Equatable, Sendable {
    /// The formatted-document/markdown/Word editor: `GET/PUT /api/documents/doc`.
    case doc
    /// The grid editor: `GET/PUT /api/documents/sheet`.
    case sheet
    /// Excalidraw. No editor on either phone.
    case drawing
    /// A PDF or an image — the dashboard opens these in a tab.
    case newtab
    /// Legacy office formats: stored and served, never edited.
    case download
    /// The plain-text editor: `GET/PUT /api/documents/text`. The catch-all —
    /// an unrecognised extension lands here and `/text` decides for itself
    /// whether the bytes are text.
    case text
}

/// Which of this app's four editors opens a file, or none.
///
/// Android's `openWith` (`FileKinds.kt:88`) has the same four destinations:
/// a `.md`, an `.html` and a `.docx` all go to the dashboard's own editor
/// inside a `WebView`, and that host is now ported too
/// (``case web``, owner answer 21), so this table and Android's answer the
/// same question the same way for every extension. A `.md` is the one place
/// they differ on purpose, and ``case markdown`` says why.
public enum DocumentsEditorKind: Equatable, Sendable {
    /// Plain UTF-8 over `/api/documents/text`.
    case text
    /// Markdown over `/api/documents/doc` — `{markdown: …}` both ways.
    ///
    /// Android sends a `.md` to the WebView host as well. Here it keeps the
    /// native editor it already has, with its formatting row and its caret
    /// rules, because markdown is text: there is nothing in a `.md` the
    /// native editor can drop, which is the whole of entry 21's argument and
    /// the reason it does **not** apply to this one extension.
    case markdown
    /// The grid over `/api/documents/sheet`.
    case sheet
    /// The dashboard's own editor page in a `WKWebView` —
    /// ``DocumentsWebEditorHost``, for a formatted document (`.html`,
    /// `.htm`) and a Word file (`.docx`).
    ///
    /// Not a native round trip: a `.docx` and a sanitised `.html` hold
    /// tables, pictures, fonts and comments that nothing on this phone can
    /// represent, and an `AttributedString` round trip would drop them
    /// silently — invisible until somebody reopened the file on a laptop
    /// ([[no-misleading-controls]], and entry 21 word for word).
    case web

    /// The pill beside the file's name.
    public var pill: String {
        switch self {
        case .text: return DocumentsStrings.pillText
        case .markdown: return DocumentsStrings.pillMarkdown
        case .sheet: return DocumentsStrings.pillSheet
        case .web: return DocumentsStrings.pillDocument
        }
    }
}

/// What every write in this item answers with: `documents.py`'s
/// `DocumentRow` (`:601`).
///
/// It lives beside the category table because its own `category` field is
/// that table's answer for the file that was just written, and because all
/// four writers (`PUT /text`, `PUT /doc`, `PUT /sheet`, `POST /create`)
/// answer in this one shape.
///
/// Decode-only, and every field optional-with-a-default as the rest of this
/// package has it: a row that fails to decode is a save the person is told
/// failed when it landed.
public struct DocumentsRow: Equatable, Sendable, Decodable {

    public let relPath: String
    /// The file's **stem** — `documents.py:712` sends `target.stem`, so a
    /// `Budget.xlsx` answers `name: "Budget"`. The row a list shows is built
    /// from ``relPath``'s last component for that reason
    /// (``DocumentsNewModel/entry(from:)``).
    public let name: String
    public let ext: String
    public let size: Int64
    /// Epoch **seconds**, with a fraction (`st_mtime`) — not an ISO string.
    public let modifiedAt: Double?
    public let category: String

    public init(
        relPath: String = "",
        name: String = "",
        ext: String = "",
        size: Int64 = 0,
        modifiedAt: Double? = nil,
        category: String = "text"
    ) {
        self.relPath = relPath
        self.name = name
        self.ext = ext
        self.size = size
        self.modifiedAt = modifiedAt
        self.category = category
    }

    private enum CodingKeys: String, CodingKey {
        case name, ext, size, category
        case relPath = "rel_path"
        case modifiedAt = "modified_at"
    }

    public init(from decoder: any Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init()
            return
        }
        self.init(
            relPath: try values.decodeIfPresent(String.self, forKey: .relPath) ?? "",
            name: try values.decodeIfPresent(String.self, forKey: .name) ?? "",
            ext: try values.decodeIfPresent(String.self, forKey: .ext) ?? "",
            size: try values.decodeIfPresent(Int64.self, forKey: .size) ?? 0,
            modifiedAt: try values.decodeIfPresent(Double.self, forKey: .modifiedAt),
            category: try values.decodeIfPresent(String.self, forKey: .category) ?? "text"
        )
    }
}

/// The category table, and the question a Files row asks it.
public enum DocumentsKinds {

    // MARK: - the extension sets, as `documents.py` has them

    /// `_FORMATTED_EXTS` (`:155`) — the dashboard editor's own files.
    static let formatted: Set<String> = ["html", "htm"]

    /// `_MARKDOWN_EXTS` (`:165`).
    static let markdown: Set<String> = ["md", "markdown"]

    /// `_RICH_EDITOR_EXTS` (`:159`): everything `GET/PUT /doc` opens.
    static let richEditor: Set<String> = formatted.union(markdown).union(["docx"])

    /// `_EDITABLE_SHEET_EXTS` (`:162`) — and **not** `_SHEET_EXTS`: `.xls`
    /// and `.ods` are sheets the server will not round-trip, so they are
    /// `download` and no editor opens them.
    static let editableSheets: Set<String> = ["xlsx", "csv"]

    /// `_OFFICE_WP_EXTS` and `_IMAGE_EXTS`, which this module does **not**
    /// copy: ``FileKinds`` already carries both, transcribed from the same
    /// server file, and two copies of one table is how two screens come to
    /// disagree about a `.rtf`.
    static var officeWordProcessor: Set<String> { FileKinds.officeWordProcessor }
    static var newtab: Set<String> { FileKinds.images.union(["pdf"]) }

    // MARK: - the table

    /// `_doc_category(ext)` (`documents.py:207`), for an extension
    /// **without** its dot.
    ///
    /// The order is the whole content of it, exactly as the `if`-chain has
    /// it: `.docx` is in both `richEditor` and `officeWordProcessor` and the
    /// server calls it `doc`; `.csv` is in both `editableSheets` and the text
    /// set and the server calls it `sheet`; and `.svg`, which the Documents
    /// **view** counts as a drawing (`_DRAWING_EXTS`), is `newtab` here
    /// because this table asks only `== ".excalidraw"` — the one place where
    /// reading the chain in order matters more than reading the sets.
    public static func category(ofExt ext: String) -> DocumentsCategory {
        let bare = ext.lowercased()
        if richEditor.contains(bare) { return .doc }
        if editableSheets.contains(bare) { return .sheet }
        if officeWordProcessor.contains(bare) || bare == "xls" || bare == "ods" { return .download }
        if bare == "excalidraw" { return .drawing }
        if newtab.contains(bare) { return .newtab }
        return .text
    }

    /// The same, for a file name — `.bashrc` has no extension, which
    /// ``FileKinds/ext(of:)`` is Python's `Path.suffix` about.
    public static func category(of name: String) -> DocumentsCategory {
        category(ofExt: FileKinds.ext(of: name))
    }

    // MARK: - what a Files row opens

    /// The editor that opens `entry` in `libraryID`, or nil when nothing in
    /// this app does.
    ///
    /// Three gates, and each one is a refusal Android does not have to make:
    ///
    /// * **Documents only.** Every `/api/documents/*` route resolves inside
    ///   the household's documents folder (`_readable_target`), so the same
    ///   `.txt` in the Music library is a 404 there — and so is a `.docx`,
    ///   because the WebView host's page reads and writes through
    ///   `/api/documents/doc` like everything else in that module. Android's
    ///   gate is `ServerFileSource.editsInApp` (`FileSource.kt:174`), which
    ///   is the same library for the same reason.
    /// * **A folder never opens an editor.**
    /// * The category decides, and `doc` splits in two: a **markdown** file
    ///   gets the native text editor, and a `.html`, `.htm` or `.docx` gets
    ///   the WebView host (``DocumentsEditorKind/web``) — which is the
    ///   dashboard's own editor page, so it can save exactly the files
    ///   `PUT /doc` takes.
    ///
    /// This function is the **one** dispatch point for all four editors, and
    /// the only thing that had to change for owner answer 21: the two call
    /// sites in `FilesView.swift` (`:204`, `:487`) ask
    /// ``DocumentsEditorCover/init(for:in:)``, which asks this, so a formatted
    /// document became editable without a line changing in Files.
    public static func editor(for entry: FileEntry, in libraryID: String?) -> DocumentsEditorKind? {
        guard libraryID == FileLibrary.documents, !entry.isDir else { return nil }
        let named = entry.name.isEmpty ? entry.rel : entry.name
        switch category(of: named) {
        case .text: return .text
        case .sheet: return .sheet
        case .doc: return markdown.contains(FileKinds.ext(of: named)) ? .markdown : .web
        case .drawing, .newtab, .download: return nil
        }
    }
}
