import Foundation
#if canImport(UniformTypeIdentifiers)
import UniformTypeIdentifiers
#endif

/// What is on this phone, read a page at a time.
///
/// Android reads `MediaStore` for music and videos (`data/LocalMedia.kt`) and
/// the Storage Access Framework for the folders the person added
/// (`ui/screens/files/PhoneFileSource.kt`). This is the seam both of those
/// sit behind here, and it exists because of what the owner's standing
/// pagination decision says: **no files-like list ever holds everything at
/// once** (``Paging``), the phone's own lists included. A folder of 40,000
/// photos is the case that makes it matter.
///
/// Three implementations are expected, and only the first is in this package:
///
/// * ``PhoneFolderSource`` — the folders the person picked, read with
///   `FileManager`. Pure Foundation, so it is tested here against a real
///   temporary directory rather than mocked.
/// * the photo library — PhotoKit, so it lives in the app target beside the
///   views that need `PHImageManager` thumbnails.
/// * a fake behind `-domovoi-phone-media`, DEBUG only, which is the only way
///   a UI case can reach a row at all: PHPicker and the document picker are
///   out-of-process (DECISIONS).

/// What a phone row holds, in the server's own vocabulary.
///
/// The raw values are the strings `/api/files/browse` answers under `kind`
/// (`files.py`'s `_entry_kind`) and ``FileKinds/kind(of:isDir:)`` produces,
/// deliberately: ``phoneDefaultHomeLibrary(_:_:)`` branches on them, and a
/// second vocabulary here would mean translating at the one point where
/// getting it wrong sends a file to the wrong library.
public enum PhoneMediaKind: String, Codable, Equatable, Sendable, CaseIterable {
    case folder
    case audio
    case video
    case image
    case pdf
    case docText = "doc-text"
    case docOffice = "doc-office"
    case other

    /// What a name on this phone is — the server's rules, with **video
    /// before audio**.
    ///
    /// ``FileKinds/kind(of:isDir:)`` checks audio first because that is the
    /// order `files.py`'s `_entry_kind` has, and `mp4` is in both sets
    /// (`FileKinds.swift:104` says so outright). That order is right for a
    /// *library listing*, where the server's answer is the one the row must
    /// agree with. It is wrong here, because the thing this answer is read
    /// by is a page **narrowed by kind**: `PhoneDevicePage.kinds(of: .videos)`
    /// is `[.video]` and `kinds(of: .music)` is `[.audio]`, so an `mp4`
    /// classified as audio is filtered out of the device side of Videos and
    /// listed as a track on the device side of Music — with an Upload control
    /// that posts it into MUSIC_DIR. `mp4` is what a phone camera and nearly
    /// every download writes, so that is the common case.
    ///
    /// ``phoneDefaultHomeLibrary(_:_:)`` already carries the same rule for
    /// the same reason (`PhoneUploadRules.swift:134`); this is it in the one
    /// other place a phone name is classified.
    public init(fileName: String, isDir: Bool) {
        if !isDir, FileKinds.video.contains(FileKinds.ext(of: fileName)) {
            self = .video
            return
        }
        self = PhoneMediaKind(rawValue: FileKinds.kind(of: fileName, isDir: isDir)) ?? (isDir ? .folder : .other)
    }

    /// The kinds the device side of Music lists. Audio only — and **no play
    /// control on any of them**, for the reason
    /// ``PhoneMediaStrings/noPhonePlayerYet`` gives.
    public static let audioOnly: Set<PhoneMediaKind> = [.audio]

    /// The kinds the device side of Videos lists.
    public static let videoOnly: Set<PhoneMediaKind> = [.video]
}

/// Where the bytes of a phone row actually are.
///
/// Two cases and no third: a file inside a folder the person picked, or an
/// asset in the photo library. A phone row is never a server path, which is
/// the whole distinction this item exists to draw.
public enum PhoneMediaOrigin: Codable, Equatable, Sendable {
    case file(URL)
    /// A `PHAsset.localIdentifier`. Opaque to this package, which has no
    /// PhotoKit in it.
    case asset(String)
}

/// One row of the phone's own library.
///
/// ``PagedRow`` because ``Pager`` is what pages it, and ``Pager`` is what
/// pages every other list in this app — a second paging mechanism for the
/// phone's lists would be a second place for the near-end guard
/// (``Paging/nearEnd(totalItems:lastVisible:threshold:)``) to be got wrong.
/// It is `Codable` only to satisfy that protocol; nothing decodes one from a
/// server, and nothing should.
public struct PhoneMediaItem: PagedRow, Equatable, Identifiable, Sendable, Codable {

    /// Stable within its library: a path relative to the picked folder, or an
    /// asset's local identifier. ``PhoneUploadKey/of(_:_:)`` keys an upload
    /// by it.
    public let id: String

    /// Which phone library it came from (``PhoneFolderList/libraryID(of:)``
    /// or ``PhoneFolderList/photosID``), so a row knows its own origin after
    /// the person has switched library.
    public let libraryID: String

    /// What a list shows, and the name the server is asked to save it under.
    public let name: String

    public let kind: PhoneMediaKind

    public let isDir: Bool

    /// Nil when the phone did not say — an asset whose resources are not
    /// local, a directory.
    public let sizeBytes: Int64?

    /// Seconds since the epoch, as ``FileEntry/mtime`` is, so the one
    /// relative-time formatter serves both sides.
    public let modified: Double?

    /// Seconds, `nil` unless the phone reported a positive duration — the
    /// `ms/1000.0 only when > 0` rule of `LocalMedia.kt:82`.
    public let durationSec: Double?

    public let origin: PhoneMediaOrigin

    public init(
        id: String,
        libraryID: String,
        name: String,
        kind: PhoneMediaKind,
        isDir: Bool = false,
        sizeBytes: Int64? = nil,
        modified: Double? = nil,
        durationSec: Double? = nil,
        origin: PhoneMediaOrigin
    ) {
        self.id = id
        self.libraryID = libraryID
        self.name = name
        self.kind = kind
        self.isDir = isDir
        self.sizeBytes = sizeBytes
        self.modified = modified
        self.durationSec = durationSec
        self.origin = origin
    }

    /// The row's second line on the device side of Music: `artist · album` is
    /// what Android shows there (`LocalMediaScreens.kt:242`) and this port
    /// has no metadata reader, so it is the size and the age instead — the
    /// shape ``FileEntry/metaLine(nowMs:)`` uses, and the honest one.
    public func metaLine(nowMs: Int64) -> String {
        if isDir { return "\(FileStrings.folder) · \(relFromEpochSec(modified, nowMs: nowMs))" }
        return "\(fmtBytes(sizeBytes)) · \(relFromEpochSec(modified, nowMs: nowMs))"
    }

    /// `fmtDur`, or Android's own dash for a row with no duration
    /// (`LocalMediaScreens.kt:249`).
    public var durationText: String {
        durationSec == nil ? PhoneMediaStrings.noDuration : fmtDur(durationSec)
    }

    /// The media type the upload's file part declares.
    public var mimeType: String { phoneMimeType(forFileName: name) }
}

/// One page of one phone library, with everything a source needs to answer
/// it.
public struct PhoneMediaRequest: Equatable, Sendable {
    public let libraryID: String
    /// The folder inside that library, as folder names joined by `/` — **the
    /// same shape the server uses** for a path inside a library
    /// (`PhoneFileSource.kt:152`), so one breadcrumb type serves both sides.
    public let path: String
    /// A case-insensitive substring of the name, applied to the whole folder
    /// rather than to the page, as `MediaStore`'s LIKE match is
    /// (`LocalMedia.kt:59`).
    public let filter: String
    /// Which kinds to list. Empty means every kind, which is what the Files
    /// side asks for.
    public let kinds: Set<PhoneMediaKind>
    public let page: PageRequest

    public init(
        libraryID: String,
        path: String = "",
        filter: String = "",
        kinds: Set<PhoneMediaKind> = [],
        page: PageRequest
    ) {
        self.libraryID = libraryID
        self.path = path
        self.filter = filter
        self.kinds = kinds
        self.page = page
    }
}

public typealias PhoneMediaPage = Page<PhoneMediaItem>

/// Something that can list what is on this phone.
public protocol PhoneMediaReading: Sendable {

    /// The phone's own libraries, in the shape the Files screen's picker
    /// already takes (`PhoneFileSource.kt:98` builds `FileLibrary`s too).
    func libraries() async -> [FileLibrary]

    /// One page. Throws when the library cannot be read at all — a bookmark
    /// that no longer resolves, a revoked permission — because that is a
    /// state the screen says out loud rather than drawing as an empty folder.
    func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage

    /// The bytes, for an upload. Throws ``PhoneMediaError/unreadable(name:)``
    /// when the phone will not hand them over, which is Android's
    /// `FileNotFoundException` **before anything is sent**
    /// (`PhoneUpload.kt:157`).
    func bytes(of item: PhoneMediaItem) async throws -> Data
}

/// `LocalMedia.kt:162`'s `pageOf`, which is the half of it that is not
/// `MediaStore`: take this request's window out of a list that is already in
/// hand, and say how many there were.
///
/// Not a method on ``Page`` — that type is shared infrastructure this item
/// does not add API to — and not generic, because the one caller is a phone
/// source.
public func phoneMediaSlice(_ all: [PhoneMediaItem], _ request: PageRequest) -> PhoneMediaPage {
    let total = all.count
    let start = max(0, min(request.offset, total))
    let end = max(start, min(start + max(0, request.limit), total))
    return Page(
        items: Array(all[start..<end]),
        total: total,
        serverHasMore: end < total,
        nextCursor: nil
    )
}

/// `s` lowercased and trimmed, which is the whole of what a phone-side filter
/// is here.
///
/// Android has to escape the needle's own `%`, `_` and `\` because it hands
/// it to SQLite as a LIKE pattern (`LocalMedia.kt:186`'s `likeEscape`). There
/// is no SQL on this side: the comparison is a Swift substring check, so
/// those three characters are already literal and an escaper would make a
/// search for `my_file` match nothing. Named here so nobody ports
/// `likeEscape` and breaks it.
public func phoneMediaNeedle(_ raw: String) -> String {
    raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
}

/// The media type for a name, for the upload's file part.
///
/// Android asks the content resolver (`PhoneUpload.kt:159`), which knows the
/// type the provider declared. Here there is no provider, so it is the
/// extension — and the same fallback the resolver's `?:` gives.
public func phoneMimeType(forFileName name: String) -> String {
    let ext = FileKinds.ext(of: name)
    guard !ext.isEmpty else { return "application/octet-stream" }
    #if canImport(UniformTypeIdentifiers)
    if let type = UTType(filenameExtension: ext)?.preferredMIMEType { return type }
    #endif
    return "application/octet-stream"
}

/// The folders the person handed this app through the document picker, read
/// with `FileManager`.
///
/// `PhoneFileSource.kt:73`, the folder half. The photo half is PhotoKit's and
/// lives in the app target.
///
/// ### What is not, and why there is no control for it
///
/// **Nothing is written, moved or deleted here.** Android gets delete and
/// move for free from its `FileSource` interface; the iOS `FilesModel` has no
/// such seam (DECISIONS), so this source lists and reads, and no screen over
/// it draws a control it could not honour. Writing into a security-scoped
/// folder is a later step, and the absence is stated rather than stubbed.
public struct PhoneFolderSource: PhoneMediaReading {

    /// The folders, newest-picked last, exactly as ``PhoneFolderList`` keeps
    /// them.
    public let folders: [PhoneFolder]

    /// Turn a folder into a URL this process may open — a bookmark
    /// resolution, which is UIKit-free but not pure, so it is injected.
    /// **Nil means the bookmark no longer resolves**, which is the state
    /// ``PhoneMediaStrings/folderNoLongerReadable`` describes.
    private let resolve: @Sendable (PhoneFolder) -> URL?

    public init(folders: [PhoneFolder], resolve: @Sendable @escaping (PhoneFolder) -> URL?) {
        self.folders = folders
        self.resolve = resolve
    }

    public func libraries() async -> [FileLibrary] {
        folders.map { folder in
            FileLibrary(
                id: PhoneFolderList.libraryID(of: folder),
                label: folder.name,
                kind: "folder",
                icon: "folder",
                // Not editable: see "What is not" above. Android sets this
                // from whether the WRITE grant survived
                // (`PhoneFileSource.kt:110`); here nothing writes, so a true
                // would draw an upload-into-the-phone control that does
                // nothing.
                editable: false,
                present: resolve(folder) != nil
            )
        }
    }

    public func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        guard let folder = folders.first(where: { PhoneFolderList.libraryID(of: $0) == request.libraryID }),
              let root = resolve(folder)
        else { throw PhoneMediaError.unreadable(name: request.libraryID) }

        let scoped = root.startAccessingSecurityScopedResource()
        defer { if scoped { root.stopAccessingSecurityScopedResource() } }

        // The path is folder names joined by `/`, so it is appended a
        // component at a time: one `appendingPathComponent` with the whole
        // string would escape the folder on any name holding a separator.
        var directory = root
        for segment in request.path.split(separator: "/") where !segment.isEmpty {
            guard segment != "..", segment != "." else {
                throw PhoneMediaError.unreadable(name: request.path)
            }
            directory.appendPathComponent(String(segment), isDirectory: true)
        }

        let keys: [URLResourceKey] = [.isDirectoryKey, .fileSizeKey, .contentModificationDateKey, .nameKey]
        guard let listed = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { throw PhoneMediaError.unreadable(name: folder.name) }

        let needle = phoneMediaNeedle(request.filter)
        var rows: [PhoneMediaItem] = []
        for url in listed {
            let values = try? url.resourceValues(forKeys: Set(keys))
            let isDir = values?.isDirectory ?? false
            let name = values?.name ?? url.lastPathComponent
            let kind = PhoneMediaKind(fileName: name, isDir: isDir)
            // A folder always stays: narrowing to one kind must not hide the
            // way down to the files of that kind.
            if !request.kinds.isEmpty, !isDir, !request.kinds.contains(kind) { continue }
            if !needle.isEmpty, !name.lowercased().contains(needle) { continue }
            let rel = request.path.isEmpty ? name : "\(request.path)/\(name)"
            rows.append(PhoneMediaItem(
                id: rel,
                libraryID: request.libraryID,
                name: name,
                kind: kind,
                isDir: isDir,
                sizeBytes: isDir ? nil : values?.fileSize.map(Int64.init),
                modified: values?.contentModificationDate?.timeIntervalSince1970,
                origin: .file(url)
            ))
        }
        // Folders first, then by name, case-insensitively — `TITLE COLLATE
        // NOCASE, _ID` (`LocalMedia.kt:59`) is the same intent on a listing
        // that has no `_ID`, so the name breaks its own ties.
        rows.sort { left, right in
            if left.isDir != right.isDir { return left.isDir }
            let compared = left.name.localizedCaseInsensitiveCompare(right.name)
            return compared == .orderedSame ? left.id < right.id : compared == .orderedAscending
        }
        return phoneMediaSlice(rows, request.page)
    }

    public func bytes(of item: PhoneMediaItem) async throws -> Data {
        guard case .file(let url) = item.origin else {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        // Scope the **folder**, not the file: the grant is on the tree, and a
        // file inside it has no bookmark of its own.
        let root = folders.first(where: { PhoneFolderList.libraryID(of: $0) == item.libraryID }).flatMap(resolve)
        let scoped = root?.startAccessingSecurityScopedResource() ?? false
        defer { if scoped, let root { root.stopAccessingSecurityScopedResource() } }
        guard let bytes = try? Data(contentsOf: url, options: .mappedIfSafe) else {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        return bytes
    }
}
