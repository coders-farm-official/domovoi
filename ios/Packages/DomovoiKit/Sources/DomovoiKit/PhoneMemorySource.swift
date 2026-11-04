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
/// gate is covered by the hand pass on Kamron's phone, which DECISIONS says.
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
        unreadable: Set<String> = []
    ) {
        self.shelves = shelves
        self.rows = rows
        self.bytes = bytes
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
}
