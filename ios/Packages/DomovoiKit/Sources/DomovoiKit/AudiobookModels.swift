import Foundation

/*
 * Audiobooks as the server lists them — `GET /api/audiobooks` and
 * `GET /api/audiobooks/{id}`, both answering `_book_row`
 * (`web/backend/api/audiobooks.py:220-243`). Ported from
 * `audiobooks/BookModels.kt`, read in this worktree.
 *
 * Every field past the oldest ones is optional, for the Kotlin's reason: a
 * server from before the contract has no size, no cover route and no `files`,
 * and a folder book there still names each chapter's file in its chapters.
 */

/// One audio file of a book: a folder book has one per chapter file, a
/// single-file book exactly one. `BookModels.kt:21`.
///
/// **Decode-only.** Nothing here is ever sent to the server; the only body
/// this item posts is a position (``AudiobookPath/saveBody(deviceID:positionSec:speed:)``).
public struct BookFileRow: Decodable, Sendable, Equatable {

    public let file: String?
    public let title: String?

    /// Where the file starts in the book. The server sends an **int**
    /// (`audiobooks.py:189`, `int(...)`); read as a Double because the
    /// chapter fallback computes durations from it.
    public let startSec: Double

    public let durationSec: Double?
    public let sizeBytes: Int64?

    public init(
        file: String? = nil,
        title: String? = nil,
        startSec: Double = 0,
        durationSec: Double? = nil,
        sizeBytes: Int64? = nil
    ) {
        self.file = file
        self.title = title
        self.startSec = startSec
        self.durationSec = durationSec
        self.sizeBytes = sizeBytes
    }

    private enum CodingKeys: String, CodingKey {
        case file
        case title
        case startSec = "start_sec"
        case durationSec = "duration_sec"
        case sizeBytes = "size_bytes"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        file = try values.decodeIfPresent(String.self, forKey: .file)
        title = try values.decodeIfPresent(String.self, forKey: .title)
        startSec = try values.decodeIfPresent(Double.self, forKey: .startSec) ?? 0
        durationSec = try values.decodeIfPresent(Double.self, forKey: .durationSec)
        sizeBytes = try values.decodeIfPresent(Int64.self, forKey: .sizeBytes)
    }
}

/// One chapter as the row lists it. `BookModels.kt:30`.
public struct BookChapterRow: Decodable, Sendable, Equatable {

    public let title: String?
    public let startSec: Double

    /// Folder books from before `files`: the chapter's own file. The server
    /// builds `files` back out of these when the indexer has not re-walked
    /// the folder since V032 (`audiobooks.py:196-208`), and
    /// ``BookRules/parts(_:)`` has the same fallback for the server that
    /// does not.
    public let file: String?

    public init(title: String? = nil, startSec: Double = 0, file: String? = nil) {
        self.title = title
        self.startSec = startSec
        self.file = file
    }

    private enum CodingKeys: String, CodingKey {
        case title
        case startSec = "start_sec"
        case file
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = try values.decodeIfPresent(String.self, forKey: .title)
        startSec = try values.decodeIfPresent(Double.self, forKey: .startSec) ?? 0
        file = try values.decodeIfPresent(String.self, forKey: .file)
    }
}

/// One book on the server. `BookModels.kt:38`'s `ServerBook`.
///
/// **Named `AudiobookRow`, not `ServerBook`.** DomovoiKit already has a
/// ``ServerBook`` — the client's "which server and which token" book
/// (`DomovoiClient.swift:17`) — and a second one would be an
/// `invalid redeclaration` that drops this branch from a train. The mapper's
/// plan named it `ServerBook`; `MAP.md` §8 and DECISIONS §9 record the
/// rename, and the row is named for the screen it belongs to the way
/// ``VideoRow`` is.
///
/// **Decode-only**, like every field of it.
public struct AudiobookRow: PagedRow, Sendable, Equatable, Identifiable {

    public let id: Int64
    public let title: String?
    public let author: String?
    public let narrator: String?

    /// The legacy artwork field. In the **answer** this is the cover route,
    /// not a path on disk, and it is the same string as ``coverURL``
    /// (`audiobooks.py:241-243`) — or null for a book with no cover.
    public let artwork: String?

    public let durationSec: Double?
    public let isFolder: Bool

    /// `null` for a folder book (`audiobooks.py:223`).
    public let fileExt: String?

    public let chapters: [BookChapterRow]
    public let sizeBytes: Int64?
    public let coverURL: String?
    public let files: [BookFileRow]

    /// Where the book sits in the server's audiobooks library — POSIX,
    /// relative, and `null` for a row outside the directory
    /// (`audiobooks.py:161-180`).
    ///
    /// **Nothing in this app reads it.** It is what "remove from server"
    /// would delete, and that control is left out (DECISIONS §2); the field
    /// is decoded so a later batch does not have to change the row, and
    /// ``BookRules/safeLibraryPath(_:)`` says the same of itself.
    public let libraryPath: String?

    public let addedAt: String?

    /// `added_via`: `"index"` or `"upload"`. Decoded because the row has it;
    /// nothing draws it, as Android draws nothing from it either.
    public let addedVia: String?

    public init(
        id: Int64 = 0,
        title: String? = nil,
        author: String? = nil,
        narrator: String? = nil,
        artwork: String? = nil,
        durationSec: Double? = nil,
        isFolder: Bool = false,
        fileExt: String? = nil,
        chapters: [BookChapterRow] = [],
        sizeBytes: Int64? = nil,
        coverURL: String? = nil,
        files: [BookFileRow] = [],
        libraryPath: String? = nil,
        addedAt: String? = nil,
        addedVia: String? = nil
    ) {
        self.id = id
        self.title = title
        self.author = author
        self.narrator = narrator
        self.artwork = artwork
        self.durationSec = durationSec
        self.isFolder = isFolder
        self.fileExt = fileExt
        self.chapters = chapters
        self.sizeBytes = sizeBytes
        self.coverURL = coverURL
        self.files = files
        self.libraryPath = libraryPath
        self.addedAt = addedAt
        self.addedVia = addedVia
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case title
        case author
        case narrator
        case artwork
        case durationSec = "duration_sec"
        case isFolder = "is_folder"
        case fileExt = "file_ext"
        case chapters
        case sizeBytes = "size_bytes"
        case coverURL = "cover_url"
        case files
        case libraryPath = "library_path"
        case addedAt = "added_at"
        case addedVia = "added_via"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title)
        author = try values.decodeIfPresent(String.self, forKey: .author)
        narrator = try values.decodeIfPresent(String.self, forKey: .narrator)
        artwork = try values.decodeIfPresent(String.self, forKey: .artwork)
        durationSec = try values.decodeIfPresent(Double.self, forKey: .durationSec)
        isFolder = try values.decodeIfPresent(Bool.self, forKey: .isFolder) ?? false
        fileExt = try values.decodeIfPresent(String.self, forKey: .fileExt)
        chapters = try values.decodeIfPresent([BookChapterRow].self, forKey: .chapters) ?? []
        sizeBytes = try values.decodeIfPresent(Int64.self, forKey: .sizeBytes)
        coverURL = try values.decodeIfPresent(String.self, forKey: .coverURL)
        files = try values.decodeIfPresent([BookFileRow].self, forKey: .files) ?? []
        libraryPath = try values.decodeIfPresent(String.self, forKey: .libraryPath)
        addedAt = try values.decodeIfPresent(String.self, forKey: .addedAt)
        addedVia = try values.decodeIfPresent(String.self, forKey: .addedVia)
    }

    /// `BookModels.kt:57` — the title, or the word `audiobook` for a row the
    /// server named nothing at all.
    public var displayTitle: String {
        guard let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return AudiobooksStrings.untitledBook
        }
        return title
    }
}

/// One playable piece of a book: a file, where it starts in the book, and how
/// long it is (nil when the server did not say). `BookModels.kt:62`.
public struct BookPart: Sendable, Equatable {
    public let file: String?
    public let startSec: Double
    public let durationSec: Double?
    public let sizeBytes: Int64?

    public init(file: String?, startSec: Double, durationSec: Double?, sizeBytes: Int64?) {
        self.file = file
        self.startSec = startSec
        self.durationSec = durationSec
        self.sizeBytes = sizeBytes
    }
}

/// One chapter as the player wants it: a title and where it starts.
/// Android's `player/Chapter` (`BookModels.kt:97` builds these).
public struct BookChapter: Sendable, Equatable {
    public let title: String
    public let startSec: Double

    public init(title: String, startSec: Double) {
        self.title = title
        self.startSec = startSec
    }
}

/// Another device's or room's place in a book, for this listener —
/// `GET /api/audiobooks/{id}/positions` (`BookModels.kt:152`).
///
/// `room` is the room's id when a room's player saved the place, nil for a
/// phone or a browser; `deviceLabel` is then the device's name, or the
/// generic `phone` / `browser` / `device` when the asker is the
/// **unidentified** listener — the server does not name someone else's phone
/// to an anonymous reader (`audiobooks.py:578-586`).
public struct OtherBookPosition: Decodable, Sendable, Equatable {

    public let deviceLabel: String?
    public let room: String?
    public let positionSec: Double
    public let updatedAt: String?

    public init(
        deviceLabel: String? = nil,
        room: String? = nil,
        positionSec: Double = 0,
        updatedAt: String? = nil
    ) {
        self.deviceLabel = deviceLabel
        self.room = room
        self.positionSec = positionSec
        self.updatedAt = updatedAt
    }

    private enum CodingKeys: String, CodingKey {
        case deviceLabel = "device_label"
        case room
        case positionSec = "position_sec"
        case updatedAt = "updated_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        deviceLabel = try values.decodeIfPresent(String.self, forKey: .deviceLabel)
        room = try values.decodeIfPresent(String.self, forKey: .room)
        positionSec = try values.decodeIfPresent(Double.self, forKey: .positionSec) ?? 0
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

/// The other places as the server answers them: a **bare array**
/// (`audiobooks.py:538-597`), with the tolerance Android has for the same
/// list wrapped in `items` or `positions` (`ApiPositionRemote.kt:66-72`).
///
/// A wrapper type rather than a `JsonElement` branch because this package
/// decodes with `Decodable` and has no untyped JSON: the init tries the
/// unkeyed container first, exactly as ``Page`` does for the same reason.
public struct OtherBookPositions: Decodable, Sendable {

    public let positions: [OtherBookPosition]

    public init(_ positions: [OtherBookPosition]) {
        self.positions = positions
    }

    private enum CodingKeys: String, CodingKey {
        case items
        case positions
    }

    public init(from decoder: any Decoder) throws {
        if var array = try? decoder.unkeyedContainer() {
            var rows: [OtherBookPosition] = []
            while !array.isAtEnd { rows.append(try array.decode(OtherBookPosition.self)) }
            positions = rows
            return
        }
        let values = try decoder.container(keyedBy: CodingKeys.self)
        positions = try values.decodeIfPresent([OtherBookPosition].self, forKey: .items)
            ?? values.decodeIfPresent([OtherBookPosition].self, forKey: .positions)
            ?? []
    }
}

/// The server's `AudiobookSort` literal (`audiobooks.py:86`): a fourth value
/// is a 422, so the chips are these three and nothing else.
public enum AudiobooksSort: String, CaseIterable, Sendable {
    case title
    case author
    case newest

    /// The chip's label, which is the key — `AudiobooksScreen.kt:105-109`.
    public var label: String { rawValue }
}

/// The pure rules over a book row.
///
/// **Named `BookRules`, not `BookModels`.** Two members of the Kotlin object
/// are deliberately missing — `parse` and `parseList`, which are `Decodable`
/// and ``Page``'s job here — so a name reading as the Kotlin's twin would
/// claim more than this holds (`MAP.md` §8).
///
/// Every one of these is pure and runs off the main actor, which is why the
/// package tests can drive them with no server and no Simulator.
public enum BookRules {

    /// The book's audio files, in order. `BookModels.kt:74`.
    ///
    /// A single-file book is **one part with no file name**; a folder book is
    /// one per `files` entry that names a file, by `start_sec`; failing that
    /// one per chapter that names a file, each one's length taken from the
    /// next chapter's start (or the book's own duration); and **empty** for a
    /// folder book that names no file at all, because nothing could be
    /// played.
    public static func parts(_ book: AudiobookRow) -> [BookPart] {
        guard book.isFolder else {
            let first = book.files.first
            return [
                BookPart(
                    file: nil,
                    startSec: 0,
                    durationSec: first?.durationSec ?? book.durationSec,
                    sizeBytes: first?.sizeBytes ?? book.sizeBytes
                )
            ]
        }
        let named = book.files.filter { !ServerCredentials.isBlank($0.file ?? "") }
        if !named.isEmpty {
            // `sortedBy` in Kotlin is stable, and so is this: two files that
            // start at the same second keep the server's order.
            return named.sorted { $0.startSec < $1.startSec }.map {
                BookPart(file: $0.file, startSec: $0.startSec, durationSec: $0.durationSec, sizeBytes: $0.sizeBytes)
            }
        }
        let fromChapters = book.chapters
            .filter { !ServerCredentials.isBlank($0.file ?? "") }
            .sorted { $0.startSec < $1.startSec }
        return fromChapters.enumerated().map { index, chapter in
            let next = index + 1 < fromChapters.count ? fromChapters[index + 1].startSec : book.durationSec
            let span = next.map { $0 - chapter.startSec }
            return BookPart(
                file: chapter.file,
                startSec: chapter.startSec,
                durationSec: span.flatMap { $0 > 0 ? $0 : nil },
                sizeBytes: nil
            )
        }
    }

    /// The book's chapters for the player. `BookModels.kt:97`.
    ///
    /// A folder book with no chapters of its own gets one per file, titled by
    /// the file's own title, else its name, else `part N`.
    public static func chapters(_ book: AudiobookRow) -> [BookChapter] {
        let listed = book.chapters.map {
            BookChapter(title: ServerCredentials.isBlank($0.title ?? "") ? "" : ($0.title ?? ""), startSec: $0.startSec)
        }
        if !listed.isEmpty || !book.isFolder { return listed }
        return book.files.enumerated().map { index, file in
            BookChapter(
                title: file.title ?? file.file ?? AudiobooksStrings.partNumber(index + 1),
                startSec: file.startSec
            )
        }
    }

    /// The whole book's size: the server's figure when it is positive, else
    /// the files' sum when **every** file has one, else nil — the app then
    /// says so rather than guess. `BookModels.kt:106`.
    public static func sizeBytes(_ book: AudiobookRow) -> Int64? {
        if let own = book.sizeBytes, own > 0 { return own }
        guard !book.files.isEmpty, !book.files.contains(where: { ($0.sizeBytes ?? 0) <= 0 }) else { return nil }
        return book.files.reduce(Int64(0)) { $0 + ($1.sizeBytes ?? 0) }
    }

    /// The cover's server path: the cover route the server named, else the
    /// legacy `artwork` when that is one of the server's own. **Never an
    /// address elsewhere** — the phone fetches nothing from outside.
    /// `BookModels.kt:115`.
    public static func coverPath(_ book: AudiobookRow) -> String? {
        if let url = book.coverURL, url.hasPrefix("/") { return url }
        if let art = book.artwork, art.hasPrefix("/") { return art }
        return nil
    }

    /// `path` when it is one a "remove from server" may send: a relative path
    /// inside the audiobooks library — not blank, not padded, not absolute,
    /// no drive letter, no backslash, no `.` or `..` part, no control
    /// character. Nil otherwise. `BookModels.kt:134`.
    ///
    /// ### Nothing calls this
    ///
    /// Said here because a seam with no caller that reads as shipped
    /// behaviour is a defect class this track has already paid for. **There
    /// is no "remove from server" in this app** (DECISIONS §2: the delete
    /// needs an `Authorization: Bearer` that
    /// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
    /// cannot send, and sending an admin password outside the client's
    /// policies is a worse trade than leaving the control out). The rule is
    /// ported with its tests because it is the one thing that must be right
    /// the day a remove does land, and writing it down now costs nothing —
    /// but its presence is not evidence that a remove exists.
    public static func safeLibraryPath(_ path: String?) -> String? {
        guard let path else { return nil }
        if ServerCredentials.isBlank(path) || path != path.trimmingCharacters(in: .whitespacesAndNewlines) { return nil }
        if path.hasPrefix("/") || path.contains("\\") { return nil }
        if path.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) { return nil }
        // `^[A-Za-z]:` — a Windows drive letter.
        let scalars = Array(path.unicodeScalars)
        if scalars.count >= 2, scalars[1] == ":", CharacterSet.letters.contains(scalars[0]) { return nil }
        for part in path.split(separator: "/", omittingEmptySubsequences: false) {
            if part.isEmpty || part == "." || part == ".." { return nil }
        }
        return path
    }
}
