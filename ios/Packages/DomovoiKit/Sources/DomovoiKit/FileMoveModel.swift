import Foundation
import Observation

/// The move destination picker: pick a library, walk into a folder, move
/// here.
///
/// `FilesMoveSheet.kt:66`, which is the phone's answer to the dashboard's
/// drag and drop — there is no sensible touch equivalent of dragging a row
/// out of a scrolling list.
///
/// ### What it decides, and what the server decides
///
/// The server enforces everything that matters: containment inside the
/// library root, a folder into itself, name collisions, and whether this
/// device may write at all. This model keeps the two **obvious** mistakes off
/// the wire — the folder being moved offered as its own destination, and the
/// folder the file already sits in offered as a move — because both of them
/// come back as a refusal or a silent "skipped", and a button that reports
/// "nothing happened" is a worse answer than a button that is not offered.
///
/// It lists **one page** of folders per level, up to the server's maximum,
/// and says so when there are more (``moreFolders``). A sheet that paged
/// would be a second scrolling list inside a sheet over a scrolling list.
@MainActor
@Observable
public final class FileMoveModel {

    public let entry: FileEntry
    public let sourceLibraryID: String
    public let sourcePath: String

    /// The libraries a move may go into (``FilesModel/moveTargets``).
    public let targets: [FileLibrary]

    public private(set) var targetID: String?

    /// The folder inside the chosen library, relative to its root.
    public private(set) var targetPath = ""

    /// The subfolders of ``targetPath``, one page of them.
    public private(set) var folders: [FileEntry] = []

    /// How many folders this level really has, when the page did not hold
    /// them all.
    public private(set) var moreFolders: Int?

    public private(set) var loading = false
    public private(set) var error: String?

    private let browse: @Sendable (String, String) async throws -> (folders: [FileEntry], total: Int?, hasMore: Bool)
    private var generation = 0

    public init(
        entry: FileEntry,
        sourceLibraryID: String,
        sourcePath: String,
        targets: [FileLibrary],
        browse: @Sendable @escaping (String, String) async throws -> (folders: [FileEntry], total: Int?, hasMore: Bool)
    ) {
        self.entry = entry
        self.sourceLibraryID = sourceLibraryID
        self.sourcePath = sourcePath
        self.targets = targets
        self.browse = browse
        // The library the file is already in, when that is a place a move may
        // go; otherwise the first one offered. Starting in the source library
        // is what makes "into the subfolder next to it" — the common move —
        // two taps.
        let start = targets.first { $0.id == sourceLibraryID } ?? targets.first
        self.targetID = start?.id
        self.targetPath = start?.id == sourceLibraryID ? sourcePath : ""
    }

    /// The picker the screen builds: destinations listed through the same
    /// browse the folder list uses, asked for **folders only**.
    public convenience init(
        entry: FileEntry,
        sourceLibraryID: String,
        sourcePath: String,
        targets: [FileLibrary],
        deviceID: String?,
        in container: AppContainer
    ) {
        let client = container.client
        self.init(
            entry: entry,
            sourceLibraryID: sourceLibraryID,
            sourcePath: sourcePath,
            targets: targets,
            browse: { libraryID, path in
                let base = FilePath.browse(
                    libraryID: libraryID,
                    path: path,
                    deviceID: deviceID,
                    foldersOnly: true
                )
                let request = PageRequest(cursor: nil, offset: 0, limit: Paging.serverMaxPage)
                let body = try await client.raw(method: "GET", path: Paging.path(base, request))
                let page = try DomovoiClient.decode(body, as: Page<FileEntry>.self)
                // `kind=folder` is the server's filter and this is not a
                // second one "to be safe": the sheet must not draw a file as
                // a folder if the parameter is ever dropped, and a list that
                // quietly contained files would be a tap that navigates into
                // a `.mp3`.
                return (
                    folders: page.items.filter(\.isDir),
                    total: page.total,
                    hasMore: page.hasMore(loadedBefore: 0)
                )
            }
        )
    }

    // MARK: - what the sheet shows

    public var targetLibrary: FileLibrary? {
        targets.first { $0.id == targetID }
    }

    /// The path as segments, which is what the "up one level" button walks.
    public var trail: [String] {
        targetPath.split(separator: "/").map(String.init).filter { !$0.isEmpty }
    }

    /// `"Music / Albums / 2026"` — where a move would land.
    public var here: String {
        let label = targetLibrary?.label ?? "—"
        return trail.isEmpty ? label : ([label] + trail).joined(separator: " / ")
    }

    public var isSameLibrary: Bool { targetID == sourceLibraryID }

    /// Already where it is: the server calls this `skipped` rather than an
    /// error, and offering it would be a button that does nothing.
    public var alreadyHere: Bool { isSameLibrary && targetPath == sourcePath }

    /// A folder cannot contain itself — nor can it be moved into something
    /// inside itself, which is the `hasPrefix` half.
    public var intoItself: Bool {
        guard isSameLibrary, entry.isDir, !entry.rel.isEmpty else { return false }
        return targetPath == entry.rel || targetPath.hasPrefix(entry.rel + "/")
    }

    public var canMoveHere: Bool {
        targetID != nil && !alreadyHere && !intoItself
    }

    /// What the button says — the refusal in its place when there is one, so
    /// a disabled button is never unexplained.
    public var actionLabel: String {
        if alreadyHere { return FileStrings.alreadyHere }
        if intoItself { return FileStrings.intoItself }
        return FileStrings.moveHere
    }

    public var hasNoTargets: Bool { targets.isEmpty }

    /// The folder being moved, listed inside its own parent: it is shown,
    /// greyed, with `(this one)` after it, rather than left out — a folder
    /// that vanished from the list of its own siblings reads as a bug.
    public func isSelf(_ folder: FileEntry) -> Bool {
        isSameLibrary && entry.isDir && folder.rel == entry.rel
    }

    public var isEmptyHere: Bool {
        folders.isEmpty && !loading && error == nil
    }

    // MARK: - walking about

    public func pick(_ libraryID: String) {
        guard libraryID != targetID else { return }
        targetID = libraryID
        // Back to the root of the library just picked — except the source's,
        // where the folder the file is in is the useful place to start.
        targetPath = libraryID == sourceLibraryID ? sourcePath : ""
        list()
    }

    public func into(_ folder: FileEntry) {
        guard folder.isDir, !isSelf(folder) else { return }
        targetPath = folder.rel
        list()
    }

    public func up() {
        guard !trail.isEmpty else { return }
        targetPath = trail.dropLast().joined(separator: "/")
        list()
    }

    /// List this level.
    ///
    /// The generation counter is the same guard ``Pager`` has: picking a
    /// library and then another before the first answers would otherwise
    /// show the first one's folders under the second one's name.
    public func list() {
        guard let libraryID = targetID else { return }
        generation += 1
        let mine = generation
        let path = targetPath
        loading = true
        error = nil
        let read = browse
        Task { @MainActor [weak self] in
            do {
                let answer = try await read(libraryID, path)
                guard let self, mine == self.generation else { return }
                self.folders = answer.folders
                self.moreFolders = answer.hasMore ? answer.total : nil
                self.loading = false
            } catch {
                guard let self, mine == self.generation else { return }
                self.folders = []
                self.moreFolders = nil
                self.error = FilesModel.why(error)
                self.loading = false
            }
        }
    }
}
