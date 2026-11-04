import Foundation

/// One browsable root: a core media folder, an enabled plugin's library, or a
/// removable drive the server can see.
///
/// `FilesModels.kt:22`'s `FileLibrary`, field for field, which is
/// `MediaLibrary.public()` in `web/backend/api/files_security.py`. **The
/// absolute root is never sent** — the client only ever names a library by
/// `id` and a file by a path relative to it, which is the whole shape of that
/// module's security posture and the reason this type has no `path`.
public struct FileLibrary: Equatable, Sendable, Decodable, Identifiable {

    public let id: String
    public let label: String
    /// `"core"`, `"plugin"` or `"removable"`.
    public let kind: String
    /// The lucide glyph name the dashboard draws. Mapped to an SF Symbol by
    /// the view (`libraryGlyph`), not stored as one: the server's vocabulary
    /// is lucide's and translating it here would hide which end chose.
    public let icon: String
    public let kindIcon: String
    /// Nil for a core library; a plugin slug, or a drive's device name.
    public let owner: String?
    /// May be an import **destination**.
    public let importable: Bool
    /// The library's own permission: files may be added to and removed from
    /// it. Not the device's — see ``FileBrowse/writable``.
    public let editable: Bool
    public let docEditing: Bool
    public let reindexKind: String?
    /// A removable drive that is no longer there reads `false`.
    public let present: Bool
    /// `"other"` for the Documents folder's non-documents, empty otherwise.
    /// Decoded because the server sends it; nothing here branches on it, and
    /// the id (``FileLibrary/other``) is what the rules key on.
    public let view: String

    /// The household's Documents folder — the one library whose text files
    /// this app can read in place (`/api/documents/text` resolves inside it
    /// and nowhere else).
    ///
    /// `FileSource.kt:148`'s `DOCUMENTS_LIBRARY_ID`.
    public static let documents = "core:documents"

    /// The same folder's files that are **not** documents: installers,
    /// archives, an APK somebody dropped in. `FileSource.kt:152`.
    public static let other = "core:other"

    public init(
        id: String = "",
        label: String = "",
        kind: String = "",
        icon: String = "",
        kindIcon: String = "",
        owner: String? = nil,
        importable: Bool = false,
        editable: Bool = false,
        docEditing: Bool = false,
        reindexKind: String? = nil,
        present: Bool = true,
        view: String = ""
    ) {
        self.id = id
        self.label = label
        self.kind = kind
        self.icon = icon
        self.kindIcon = kindIcon
        self.owner = owner
        self.importable = importable
        self.editable = editable
        self.docEditing = docEditing
        self.reindexKind = reindexKind
        self.present = present
        self.view = view
    }

    private enum CodingKeys: String, CodingKey {
        case id, label, kind, icon, owner, importable, editable, present, view
        case kindIcon = "kind_icon"
        case docEditing = "doc_editing"
        case reindexKind = "reindex_kind"
    }

    /// Every field is optional-with-a-default, as Android's is: the backend
    /// grows fields and an older server leaves some out, and a library that
    /// fails to decode is a library the person cannot open at all.
    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? ""
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        icon = try values.decodeIfPresent(String.self, forKey: .icon) ?? ""
        kindIcon = try values.decodeIfPresent(String.self, forKey: .kindIcon) ?? ""
        owner = try values.decodeIfPresent(String.self, forKey: .owner)
        importable = try values.decodeIfPresent(Bool.self, forKey: .importable) ?? false
        editable = try values.decodeIfPresent(Bool.self, forKey: .editable) ?? false
        docEditing = try values.decodeIfPresent(Bool.self, forKey: .docEditing) ?? false
        reindexKind = try values.decodeIfPresent(String.self, forKey: .reindexKind)
        present = try values.decodeIfPresent(Bool.self, forKey: .present) ?? true
        view = try values.decodeIfPresent(String.self, forKey: .view) ?? ""
    }

    /// A stick plugged into the server, which is the one kind of library a
    /// file can be **imported from**.
    public var isRemovable: Bool { kind == "removable" }

    /// The line under a library's name in the picker: `"core · read-only"`.
    /// `FilesScreen.kt:1010`.
    public var pickerDetail: String {
        kind + (editable ? "" : " · read-only")
    }
}

/// `/api/files/libraries`: the libraries, and what the Documents view takes.
///
/// `document_exts` is decoded because the server sends it as the authority on
/// which files belong to Documents rather than Other, and this app's own copy
/// of that set (``FileKinds/documentExtensions``) is checked against it by
/// ``FileLibraries/divergence(from:)`` — the same pinning
/// `domovoi/tests/test_files_other_android.py` does for the Kotlin copy.
/// Nothing in the screen branches on it: an upload that would need it is a
/// later batch's.
public struct FileLibraries: Equatable, Sendable, Decodable {

    public let libraries: [FileLibrary]
    public let documentExtensions: Set<String>

    public init(libraries: [FileLibrary] = [], documentExtensions: Set<String> = []) {
        self.libraries = libraries
        self.documentExtensions = documentExtensions
    }

    private enum CodingKeys: String, CodingKey {
        case libraries
        case documentExtensions = "document_exts"
    }

    public init(from decoder: any Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init()
            return
        }
        self.init(
            libraries: try values.decodeIfPresent([FileLibrary].self, forKey: .libraries) ?? [],
            documentExtensions: Set(
                (try values.decodeIfPresent([String].self, forKey: .documentExtensions) ?? [])
                    .map { $0.lowercased() }
            )
        )
    }

    /// Extensions this server counts as documents that the app's own table
    /// does not, and the other way round — empty when the two agree.
    ///
    /// It is **not** used to change what the screen does. It exists because
    /// the app's table is a copy of the server's and a copy drifts: a test
    /// can hold the server's real answer against it, and a later batch that
    /// sorts a mixed upload into Documents and Other has the comparison
    /// already written rather than discovering the drift on a person's files.
    public func divergence(from ours: Set<String>) -> (appOnly: Set<String>, serverOnly: Set<String>) {
        guard !documentExtensions.isEmpty else { return ([], []) }
        return (ours.subtracting(documentExtensions), documentExtensions.subtracting(ours))
    }
}

/// One entry of a directory listing.
///
/// `FilesModels.kt:48`. ``PagedRow/itemsKey`` is `"entries"`, which is the
/// key `/api/files/browse` answers under.
public struct FileEntry: PagedRow, Equatable, Identifiable {

    public static var itemsKey: String { "entries" }

    public let name: String
    /// The path inside the library, which is the row's identity: unique
    /// within a folder, and what every verb takes.
    public let rel: String
    public let isDir: Bool
    public let size: Int64?
    /// Last modified, in **seconds** since the epoch, as a `Double` — the
    /// server sends `st_mtime`, which has a fraction. Not an ISO string, so
    /// ``relFromEpochSec(_:nowMs:)`` is what formats it and ``relTime(_:nowMs:)``
    /// is not.
    public let mtime: Double?
    /// `folder` · `audio` · `video` · `doc-office` · `doc-text` · `image` ·
    /// `pdf` · `other`, as the **server** classified it (`files.py`'s
    /// `_entry_kind`). ``FileKinds/kind(of:isDir:)`` is the same rules, for a
    /// name the server has not classified.
    public let kind: String

    /// Who has this file open in the dashboard's editor.
    ///
    /// **Nothing sends this any more.** `BrowseEntry.locked_by` is still in
    /// the server's schema (`files.py:266`) but the join that filled it went
    /// with the office engines — `browse`'s own docstring says so: "the
    /// per-file editor-lock join is gone with the office engines". Android
    /// still draws an `editing in …` pill from it (`FilesScreen.kt:1130`),
    /// which is a control that can no longer appear on a current server.
    ///
    /// It is decoded and **not drawn**: the field costs nothing and an older
    /// house may still send it, but a pill this app can never show is exactly
    /// what the owner's standing rule says not to build
    /// ([[no-misleading-controls]]). Recorded in the Android defect list.
    public let lockedBy: String?

    public var id: String { rel }

    public init(
        name: String = "",
        rel: String = "",
        isDir: Bool = false,
        size: Int64? = nil,
        mtime: Double? = nil,
        kind: String = "other",
        lockedBy: String? = nil
    ) {
        self.name = name
        self.rel = rel
        self.isDir = isDir
        self.size = size
        self.mtime = mtime
        self.kind = kind
        self.lockedBy = lockedBy
    }

    private enum CodingKeys: String, CodingKey {
        case name, rel, size, mtime, kind
        case isDir = "is_dir"
        case lockedBy = "locked_by"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        rel = try values.decodeIfPresent(String.self, forKey: .rel) ?? ""
        isDir = try values.decodeIfPresent(Bool.self, forKey: .isDir) ?? false
        size = try values.decodeIfPresent(Int64.self, forKey: .size)
        mtime = try values.decodeIfPresent(Double.self, forKey: .mtime)
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "other"
        lockedBy = try values.decodeIfPresent(String.self, forKey: .lockedBy)
    }

    /// The name a list shows: a formatted document without its extension.
    /// ``FileKinds/displayName(of:isDir:)``.
    public var displayTitle: String {
        let shown = FileKinds.displayName(of: name, isDir: isDir)
        return shown.isEmpty ? (rel.isEmpty ? FileStrings.unnamedFile : rel) : shown
    }

    /// `"folder · 3d ago"`, or `"Document · 12 KB · just now"`.
    ///
    /// `FilesScreen.kt:1113`. The `Document · ` prefix is there for the
    /// reason the extension is hidden: the editor's own files are `.html` and
    /// people are never shown "HTML".
    public func metaLine(nowMs: Int64) -> String {
        let when = relFromEpochSec(mtime, nowMs: nowMs)
        if isDir { return "\(FileStrings.folder) · \(when)" }
        let prefix = FileKinds.isFormattedDocument(name, isDir: isDir) ? "\(FileStrings.document) · " : ""
        return "\(prefix)\(fmtBytes(size)) · \(when)"
    }
}

/// A listed folder's own fields: everything `/api/files/browse` answers
/// **apart from** the page of entries.
///
/// `FilesModels.kt:58`'s `FileBrowse` with `entries` dropped, which is what
/// Android keeps too (`FilesScreen.kt:214` stores `fb.copy(entries =
/// emptyList())`): the rows belong to the pager, and holding them twice is
/// how the two come to disagree about which page is on screen.
///
/// The body is decoded **twice** — once as a ``Page`` of entries, once as
/// this — for the reason ``VideoLibraryTotals`` is: ``Page`` deliberately has
/// no untyped `extra`, and two passes over one page of JSON is the price of
/// keeping a `[String: Any]` out of this package.
public struct FileBrowse: Equatable, Sendable, Decodable {

    public let libraryID: String
    /// The normalised path of the listed directory; empty at the root.
    public let path: String
    public let editable: Bool
    public let importable: Bool
    public let docEditing: Bool
    /// The folders above this one, in order. The path a segment leads to is
    /// the segments up to and including it, joined by `/`.
    public let breadcrumb: [String]

    /// Whether **this device** may change things here.
    ///
    /// ``FileLibrary/editable`` is the library's property; this is the
    /// device's, and an admin can block a named device from writing while
    /// leaving every read open (`files.py`'s `_block_status`). Reads are
    /// never blocked, so a blocked phone still sees the folder — and is told
    /// why, once, above the list.
    ///
    /// `true` for an older server that does not send it, which is the only
    /// safe default: the alternative is a phone that hides every write
    /// against a house that would have allowed them.
    public let writable: Bool
    public let blockedReason: String?
    public let total: Int?
    /// The whole folder's count, where ``total`` counts what matches the
    /// filter.
    public let folderTotal: Int?

    public init(
        libraryID: String = "",
        path: String = "",
        editable: Bool = false,
        importable: Bool = false,
        docEditing: Bool = false,
        breadcrumb: [String] = [],
        writable: Bool = true,
        blockedReason: String? = nil,
        total: Int? = nil,
        folderTotal: Int? = nil
    ) {
        self.libraryID = libraryID
        self.path = path
        self.editable = editable
        self.importable = importable
        self.docEditing = docEditing
        self.breadcrumb = breadcrumb
        self.writable = writable
        self.blockedReason = blockedReason
        self.total = total
        self.folderTotal = folderTotal
    }

    private enum CodingKeys: String, CodingKey {
        case path, editable, importable, breadcrumb, writable, total
        case libraryID = "library_id"
        case docEditing = "doc_editing"
        case blockedReason = "blocked_reason"
        case folderTotal = "folder_total"
    }

    public init(from decoder: any Decoder) throws {
        // A bare array is not a shape this endpoint answers in, but ``Page``
        // tolerates one, so the second decode of the same body must not throw
        // on it either.
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init()
            return
        }
        self.init(
            libraryID: try values.decodeIfPresent(String.self, forKey: .libraryID) ?? "",
            path: try values.decodeIfPresent(String.self, forKey: .path) ?? "",
            editable: try values.decodeIfPresent(Bool.self, forKey: .editable) ?? false,
            importable: try values.decodeIfPresent(Bool.self, forKey: .importable) ?? false,
            docEditing: try values.decodeIfPresent(Bool.self, forKey: .docEditing) ?? false,
            breadcrumb: try values.decodeIfPresent([String].self, forKey: .breadcrumb) ?? [],
            writable: try values.decodeIfPresent(Bool.self, forKey: .writable) ?? true,
            blockedReason: try values.decodeIfPresent(String.self, forKey: .blockedReason),
            total: try values.decodeIfPresent(Int.self, forKey: .total),
            folderTotal: try values.decodeIfPresent(Int.self, forKey: .folderTotal)
        )
    }

    /// Where a breadcrumb segment leads: the segments up to and including
    /// `index`, joined by `/`. Nil for an index that is not in the trail,
    /// which is a tap on a crumb a reload has removed.
    public func crumbPath(at index: Int) -> String? {
        guard breadcrumb.indices.contains(index) else { return nil }
        return breadcrumb[0...index].joined(separator: "/")
    }

    /// The folder one level up, or nil at the library's root.
    public var parentPath: String? {
        guard !breadcrumb.isEmpty else { return nil }
        return breadcrumb.dropLast().joined(separator: "/")
    }
}

/// What a write to `/api/files` answered.
///
/// One type for `delete`, `move` and `import`, where Android has three
/// (`FilesIo.kt`'s `DeleteResult`, `MoveResult`, `ImportResult`). They are
/// the same answer in three vocabularies — a per-path outcome, never
/// all-or-nothing — and the counts a screen reports are the same three
/// sentences, so one decoder with the union of the keys says it once. The
/// names are kept as the server's, not unified into "changed", because
/// `moved`/`copied`/`deleted` is what a reader will grep for in `files.py`.
public struct FileChange: Equatable, Sendable, Decodable {

    public let deleted: [String]
    public let moved: [String]
    public let copied: [String]
    /// Harmless no-ops, kept apart from ``failed`` so a file dropped into the
    /// folder it is already in does not read as an error (`files.py:351`).
    public let skipped: [String]
    public let failed: [String]
    public let reindexTriggered: Bool

    public init(
        deleted: [String] = [],
        moved: [String] = [],
        copied: [String] = [],
        skipped: [String] = [],
        failed: [String] = [],
        reindexTriggered: Bool = false
    ) {
        self.deleted = deleted
        self.moved = moved
        self.copied = copied
        self.skipped = skipped
        self.failed = failed
        self.reindexTriggered = reindexTriggered
    }

    private enum CodingKeys: String, CodingKey {
        case deleted, moved, copied, skipped, failed
        case reindexTriggered = "reindex_triggered"
    }

    public init(from decoder: any Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init()
            return
        }
        self.init(
            deleted: try values.decodeIfPresent([String].self, forKey: .deleted) ?? [],
            moved: try values.decodeIfPresent([String].self, forKey: .moved) ?? [],
            copied: try values.decodeIfPresent([String].self, forKey: .copied) ?? [],
            skipped: try values.decodeIfPresent([String].self, forKey: .skipped) ?? [],
            failed: try values.decodeIfPresent([String].self, forKey: .failed) ?? [],
            reindexTriggered: try values.decodeIfPresent(Bool.self, forKey: .reindexTriggered) ?? false
        )
    }

    /// The first failure **verbatim**, which is the whole point of keeping
    /// the array: `"1 failed"` with no reason is useless when the cause is a
    /// name collision the person can do something about (`FilesIo.kt:88`).
    public var firstFailure: String? {
        failed.first.map { String($0.prefix(120)) }
    }

    /// `"moved 2 items · 1 already there · indexing…"` — a move's result.
    ///
    /// `FilesScreen.kt:449`, and the empty case is Android's "nothing to
    /// move" rather than a blank toast.
    public var movedLine: String {
        var parts: [String] = []
        if !moved.isEmpty { parts.append("moved \(moved.count) \(FileStrings.items(moved.count))") }
        if !skipped.isEmpty { parts.append("\(skipped.count) already there") }
        if !failed.isEmpty { parts.append("\(failed.count) failed") }
        if parts.isEmpty { return FileStrings.nothingToMove }
        if reindexTriggered { parts.append(FileStrings.indexing) }
        return parts.joined(separator: " · ")
    }

    /// `"imported 3 items · 1 skipped · indexing…"`. `FilesScreen.kt:480`.
    public var importedLine: String {
        var parts = ["imported \(copied.count) \(FileStrings.items(copied.count))"]
        if !skipped.isEmpty { parts.append("\(skipped.count) skipped") }
        if reindexTriggered { parts.append(FileStrings.indexing) }
        return parts.joined(separator: " · ")
    }
}

/// `relTime` for a time the server sent as epoch **seconds**.
///
/// `Format.kt`'s `relFromEpochSec`. It is a line over ``relTimeMs(_:nowMs:)``
/// rather than its own arithmetic, because Files is the first screen whose
/// timestamps are not ISO strings and a second copy of the wording is how two
/// screens come to disagree about what "3d ago" means.
///
/// A missing or non-finite time is the same em dash ``relTime(_:nowMs:)``
/// gives for a missing string: a `mtime` the server could not read is not a
/// file modified at the epoch.
public func relFromEpochSec(_ seconds: Double?, nowMs: Int64) -> String {
    guard let seconds, seconds.isFinite else { return "—" }
    return relTimeMs(Int64((seconds * 1000).rounded()), nowMs: nowMs)
}
