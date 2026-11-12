import Foundation

/// A phone library held in memory: the libraries, the rows per folder, and
/// the bytes.
///
/// It exists for one reason, and the reason is in DECISIONS: **PHPicker, the
/// document picker and the Photos save alert are all out-of-process**, so
/// without a seam like this not one row, upload button, progress ring or
/// toast of this whole item is reachable from an XCUITest. The app selects
/// one of these behind `-domovoi-phone-media <name>`, `#if DEBUG` only, which
/// is the idiom `AuditStrip.active` already uses (`WorkspaceView.swift:238`).
///
/// It is in the package rather than beside the fake data in the app target so
/// that the paging, the filter and the kind narrowing it does are the **same
/// code** every phone source is tested against, and so that `pkgtest`
/// compiles it. Android's equivalent is Espresso driving a real `MediaStore`
/// on an emulator with seeded files; there is no such thing on a Simulator.
///
/// ### What it deliberately does not simulate
///
/// Permission. A real ``PhonePhotosSource`` answers an empty library list
/// while the person has not allowed Photos, and the screen draws the gate;
/// this source always answers. A UI case about the gate would therefore be a
/// case about this type rather than about the app, so there is none — the
/// gate is covered by the hand pass on the owner's phone, which DECISIONS says.
public struct PhoneMemorySource: PhoneMediaReading {

    /// The libraries, **in the order a screen's picker will show them**.
    public let shelves: [FileLibrary]

    /// Rows by `(libraryID, path)`. A folder with no entry here is empty, not
    /// unreadable.
    private let rows: [PhoneMemorySource.Place: [PhoneMediaItem]]

    /// Libraries whose every read throws — a bookmark that stopped
    /// resolving.
    private let unreadable: Set<String>

    /// What a row's bytes are, by row id. A row with no entry answers
    /// ``PhoneMediaError/unreadable(name:)``, which is the state a file
    /// deleted since the page was listed is in.
    private let bytes: [String: Data]

    /// Row ids whose ``fileURL(of:)`` materialises a file of the row's **own
    /// stated size** instead of writing ``bytes``.
    ///
    /// This is how a UI case uploads something over the old 64 MiB ceiling
    /// without a 200 MiB literal in the app binary or 200 MiB of a
    /// Simulator's memory: the file is created by setting its length, so it
    /// is sparse on APFS and costs no disk until something writes into it,
    /// and `URLSession` then streams its (zero) bytes at full speed. A case
    /// that asserts the house received the right NUMBER of bytes is the point
    /// of it; nothing asserts what they are.
    private let sized: Set<String>

    /// A library and a folder inside it.
    public struct Place: Hashable, Sendable {
        public let libraryID: String
        public let path: String
        public init(_ libraryID: String, _ path: String = "") {
            self.libraryID = libraryID
            self.path = path
        }
    }

    public init(
        shelves: [FileLibrary],
        rows: [Place: [PhoneMediaItem]],
        bytes: [String: Data] = [:],
        sized: Set<String> = [],
        unreadable: Set<String> = []
    ) {
        self.shelves = shelves
        self.rows = rows
        self.bytes = bytes
        self.sized = sized
        self.unreadable = unreadable
    }

    public func libraries() async -> [FileLibrary] { shelves }

    public func page(_ request: PhoneMediaRequest) async throws -> PhoneMediaPage {
        guard shelves.contains(where: { $0.id == request.libraryID }),
              !unreadable.contains(request.libraryID)
        else { throw PhoneMediaError.unreadable(name: request.libraryID) }

        var kept = rows[Place(request.libraryID, request.path)] ?? []
        // A folder always stays when the list is narrowed to one kind: hiding
        // it would hide the way down to the files of that kind.
        // ``PhoneFolderSource/page(_:)``'s rule, and the same line of it.
        if !request.kinds.isEmpty {
            kept = kept.filter { $0.isDir || request.kinds.contains($0.kind) }
        }
        let needle = phoneMediaNeedle(request.filter)
        if !needle.isEmpty { kept = kept.filter { $0.name.lowercased().contains(needle) } }
        return phoneMediaSlice(kept, request.page)
    }

    public func bytes(of item: PhoneMediaItem) async throws -> Data {
        guard let found = bytes[item.id] else {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        return found
    }

    /// A real file, so a UI case can drive a real streamed upload.
    ///
    /// A row named in ``sized`` gets a file of exactly its stated size, made
    /// by setting the length rather than by writing bytes; anything else
    /// falls through to the default, which spools ``bytes``.
    public func fileURL(of item: PhoneMediaItem) async throws -> PhoneUploadFile {
        guard sized.contains(item.id), let size = item.sizeBytes, size >= 0 else {
            let bytes = try await self.bytes(of: item)
            let url = PhoneUploadSpool.fileURL(in: try PhoneUploadSpool.directory(), for: item.name)
            try bytes.write(to: url, options: .atomic)
            return .spooled(url)
        }
        let url = PhoneUploadSpool.fileURL(in: try PhoneUploadSpool.directory(), for: item.name)
        guard FileManager.default.createFile(atPath: url.path, contents: nil),
              let handle = try? FileHandle(forWritingTo: url)
        else { throw PhoneMediaError.unreadable(name: item.name) }
        defer { try? handle.close() }
        do {
            try handle.truncate(atOffset: UInt64(size))
        } catch {
            throw PhoneMediaError.unreadable(name: item.name)
        }
        return .spooled(url)
    }
}
