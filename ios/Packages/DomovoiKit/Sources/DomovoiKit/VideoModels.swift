import Foundation

/// One video, as `/api/videos/list` and `/api/videos/recent` answer it.
///
/// `VideosScreen.kt:90`'s `VideoRow`, field for field. Identity is
/// `(library_id, rel)` — there is **no index and no id**: videos are
/// discovered by walking every Files library on every request
/// (`web/backend/api/videos.py`), so the pair of strings is all there is to
/// key a row, a resume position or a poster by.
///
/// The last three fields come back only from `/recent`; a `/list` row leaves
/// them out, which is why every one of them is optional and why nothing here
/// treats a missing `duration_sec` as zero.
public struct VideoRow: Equatable, Sendable, PagedRow, Identifiable {

    /// `"videos"`, not `"items"` — the key this endpoint answers under
    /// (``PagedRow/itemsKey``).
    public static var itemsKey: String { "videos" }

    public let libraryID: String
    /// What the library calls itself, for the section header. Nil on an older
    /// server, which is why ``VideoGridEntry/Header`` falls back to the id.
    public let libraryLabel: String?
    /// The path inside the library, which with ``libraryID`` is the identity.
    public let rel: String
    /// The file's own basename, as the server computed it.
    public let name: String
    public let size: Int64?
    /// Last-modified, in **seconds** since the epoch, as a `Double` — the
    /// server sends `st_mtime`, which has a fraction.
    public let mtime: Double?

    // ---- /recent only ------------------------------------------------------

    public let positionSec: Int?
    public let durationSec: Int?
    /// The title the resume row was saved under, which is what the person
    /// last saw this video called.
    public let title: String?

    public init(
        libraryID: String = "",
        libraryLabel: String? = nil,
        rel: String = "",
        name: String = "",
        size: Int64? = nil,
        mtime: Double? = nil,
        positionSec: Int? = nil,
        durationSec: Int? = nil,
        title: String? = nil
    ) {
        self.libraryID = libraryID
        self.libraryLabel = libraryLabel
        self.rel = rel
        self.name = name
        self.size = size
        self.mtime = mtime
        self.positionSec = positionSec
        self.durationSec = durationSec
        self.title = title
    }

    private enum CodingKeys: String, CodingKey {
        case rel, name, size, mtime, title
        case libraryID = "library_id"
        case libraryLabel = "library_label"
        case positionSec = "position_sec"
        case durationSec = "duration_sec"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        libraryID = try values.decodeIfPresent(String.self, forKey: .libraryID) ?? ""
        libraryLabel = try values.decodeIfPresent(String.self, forKey: .libraryLabel)
        rel = try values.decodeIfPresent(String.self, forKey: .rel) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        size = try values.decodeIfPresent(Int64.self, forKey: .size)
        mtime = try values.decodeIfPresent(Double.self, forKey: .mtime)
        positionSec = try values.decodeIfPresent(Int.self, forKey: .positionSec)
        durationSec = try values.decodeIfPresent(Int.self, forKey: .durationSec)
        title = try values.decodeIfPresent(String.self, forKey: .title)
    }

    /// `"library:rel"` — the row's identity, the grid item's key, and what a
    /// closing feed hands back so the grid can find the tile again.
    ///
    /// `VideosScreen.kt:107`'s `videoKey`. A colon rather than anything
    /// cleverer because the server's own library ids already contain one
    /// (`core:videos`), so a reader has to split from the **left** at the
    /// first colon; nothing here ever parses it back, which is why that is
    /// safe and worth saying.
    public var id: String { "\(libraryID):\(rel)" }

    /// The name a row shows.
    ///
    /// The server already sends the basename in `name`, so unlike Music's
    /// rows this needs no path stripping — the title, when there is one, is
    /// only ever on a `/recent` row.
    public var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        if !name.isEmpty { return name }
        return rel.isEmpty ? VideoStrings.unnamedVideo : rel
    }

    /// `"Movies/clips/holiday.mp4"` for the details sheet: the library's own
    /// name, or its id, and then the path inside it.
    public var location: String {
        "\(libraryLabel ?? libraryID)/\(rel)"
    }

    /// How far through this video the person is, 0…1, or nil when the resume
    /// row carries no usable duration.
    ///
    /// Nil rather than zero for a `duration_sec` of 0 or less: a bar at zero
    /// says "not started", and "the server never said how long this is" is a
    /// different fact. `VideosScreen.kt:286` writes the same guard inline.
    public var watchedFraction: Double? {
        guard let durationSec, durationSec > 0 else { return nil }
        let at = Double(max(0, positionSec ?? 0))
        return min(1, at / Double(durationSec))
    }

    /// `"1:12 / 48:30"`, or just the position when the length is unknown.
    public var resumeLine: String {
        let at = fmtDur(Double(max(0, positionSec ?? 0)))
        guard let durationSec, durationSec > 0 else { return at }
        return "\(at) / \(fmtDur(Double(durationSec)))"
    }
}

/// The per-library totals a paged `/list` answer carries alongside its rows.
///
/// `VideosScreen.kt:132`'s `LibraryTotal`. It is a separate type rather than
/// a field on ``Page`` because ``Page`` deliberately has no `extra` (its own
/// doc says so): the fetch decodes the answer twice, once as a page of rows
/// and once as this, which costs one pass over a small JSON body and keeps
/// the untyped dictionary out of the package.
public struct VideoLibraryTotals: Equatable, Sendable, Decodable {

    /// `library_id` → how many videos the server says that library holds.
    public let byLibrary: [String: Int]

    public init(byLibrary: [String: Int] = [:]) {
        self.byLibrary = byLibrary
    }

    private struct Entry: Decodable {
        let libraryID: String
        let total: Int

        private enum CodingKeys: String, CodingKey {
            case libraryID = "library_id"
            case total
        }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            libraryID = try values.decodeIfPresent(String.self, forKey: .libraryID) ?? ""
            total = try values.decodeIfPresent(Int.self, forKey: .total) ?? 0
        }
    }

    private enum CodingKeys: String, CodingKey {
        case libraries
    }

    public init(from decoder: any Decoder) throws {
        // A bare array is not an answer shape this endpoint uses, but the
        // list tolerates one (``Page``), so this must not throw on it.
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init()
            return
        }
        let entries = try values.decodeIfPresent([Entry].self, forKey: .libraries) ?? []
        var out: [String: Int] = [:]
        for entry in entries where !entry.libraryID.isEmpty { out[entry.libraryID] = entry.total }
        self.init(byLibrary: out)
    }
}

/// One item of the Videos grid, in render order.
///
/// `FeedModels.kt:90`. The grid renders **straight from the list of these**,
/// so an entry's index in the list *is* its index in the grid — which is the
/// whole mechanism behind "close the feed and the video you ended on is back
/// in the middle of the screen".
public enum VideoGridEntry: Equatable, Sendable, Identifiable {

    /// The horizontal "recently played" strip: one full-width grid item.
    case recent
    case header(libraryID: String, label: String, count: Int)
    case tile(VideoRow)

    public var id: String {
        switch self {
        case .recent: return "recent"
        case .header(let libraryID, _, _): return "hdr:\(libraryID)"
        case .tile(let video): return video.id
        }
    }

    /// Whether this entry spans the whole grid width. Both of the non-tile
    /// cases do; it is asked here rather than by a `switch` in the view.
    public var isFullWidth: Bool {
        if case .tile = self { return false }
        return true
    }

    public var video: VideoRow? {
        if case .tile(let video) = self { return video }
        return nil
    }
}

/// The grid's own arithmetic, with no view and no server.
public enum VideoGrid {

    /// Rows grouped by library, **in the order the rows arrived**.
    ///
    /// Android writes `list.items.groupBy { it.library_id }`
    /// (`VideosScreen.kt:227`) and relies on Kotlin's `groupBy` returning a
    /// `LinkedHashMap`, which preserves first-encounter order. Swift's
    /// `Dictionary(grouping:by:)` has **no order at all** and a different one
    /// between runs, so the same call here would reshuffle the library
    /// sections between two reads of the same list. Hence pairs, not a
    /// dictionary — the same trap as the stats tile's tie-break one batch
    /// ago.
    ///
    /// The server sorts by `library` then path, so first-encounter order is
    /// the server's own grouping and this never splits a library in two.
    public static func grouped(_ rows: [VideoRow]) -> [(libraryID: String, videos: [VideoRow])] {
        var order: [String] = []
        var byLibrary: [String: [VideoRow]] = [:]
        for row in rows {
            if byLibrary[row.libraryID] == nil { order.append(row.libraryID) }
            byLibrary[row.libraryID, default: []].append(row)
        }
        return order.map { (libraryID: $0, videos: byLibrary[$0] ?? []) }
    }

    /// The grid's entries: the recent strip when it is shown, then each
    /// library's header followed by its tiles.
    ///
    /// - Parameters:
    ///   - showRecent: whether the strip has anything in it **and** the
    ///     filter is empty — a filtered grid is a search result and the strip
    ///     is not part of it (`VideosScreen.kt:229`).
    ///   - totals: the server's per-library counts. A library it did not
    ///     total (an older server) is counted by what is loaded, which is the
    ///     only honest number available then.
    ///
    /// A library whose list is empty gets **no header**: a heading over
    /// nothing reads as a failed section.
    ///
    /// The label is read with `first?` and not `videos[0]`, even though the
    /// `where` clause above has just established that there is one. This is
    /// the principle ``sections(_:)`` states at its own impossible case, and
    /// it was arrived at the same way: with `videos[0]` there, deleting the
    /// `where` clause did not make a test fail — it made the whole test
    /// process die on `Index out of range`, taking every later result with
    /// it. A grid that traps is the worst possible answer to a shape that
    /// should not exist, and it is also the one that tells you least.
    public static func entries(
        showRecent: Bool,
        grouped: [(libraryID: String, videos: [VideoRow])],
        totals: [String: Int] = [:]
    ) -> [VideoGridEntry] {
        var out: [VideoGridEntry] = []
        if showRecent { out.append(.recent) }
        for group in grouped where !group.videos.isEmpty {
            out.append(
                .header(
                    libraryID: group.libraryID,
                    label: group.videos.first?.libraryLabel ?? group.libraryID,
                    count: totals[group.libraryID] ?? group.videos.count
                )
            )
            for video in group.videos { out.append(.tile(video)) }
        }
        return out
    }

    /// The same entries as **sections**, which is what a SwiftUI grid needs.
    ///
    /// ### Why both shapes exist
    ///
    /// Android draws one `LazyVerticalGrid` and makes a header span the whole
    /// width with `GridItemSpan(maxLineSpan)`. SwiftUI's `LazyVGrid` cannot:
    /// `gridCellColumns(_:)` needs a **fixed** column count, and these
    /// columns are `.adaptive(minimum: 160)` precisely so a wide phone shows
    /// more of them. So the page is a stack of (header, grid-of-that-
    /// library's-tiles) instead, which renders the same and is the ordinary
    /// SwiftUI shape for it.
    ///
    /// It is **derived from ``entries(showRecent:grouped:totals:)``** rather
    /// than built from the rows again, so the two cannot drift: the flat list
    /// stays the thing that defines the order (and the thing
    /// ``index(of:in:)`` searches when a feed closes), and this is a reading
    /// of it.
    public static func sections(_ entries: [VideoGridEntry]) -> [VideoSection] {
        var out: [VideoSection] = []
        var tileIndex = 0
        for entry in entries {
            switch entry {
            case .recent:
                continue
            case .header(let libraryID, let label, let count):
                out.append(VideoSection(libraryID: libraryID, label: label, count: count, tiles: []))
            case .tile(let video):
                let tile = VideoTile(gridIndex: tileIndex, video: video)
                tileIndex += 1
                guard var last = out.popLast() else {
                    // A tile with no header above it cannot come out of
                    // `entries`, which always writes the header first. It is
                    // handled rather than trapped because a `popLast` on
                    // empty would, and a crash in a grid is the worst
                    // possible answer to an impossible input.
                    out.append(
                        VideoSection(
                            libraryID: video.libraryID,
                            label: video.libraryLabel ?? video.libraryID,
                            count: 1,
                            tiles: [tile]
                        )
                    )
                    continue
                }
                last.tiles.append(tile)
                out.append(last)
            }
        }
        return out
    }

    /// The viewing order for a tap in the grid: every tile, in the order the
    /// grid shows them. `FeedModels.kt:135`.
    public static func feedOrder(_ entries: [VideoGridEntry]) -> [VideoRow] {
        entries.compactMap(\.video)
    }

    /// Where `key` sits in `entries`, or nil when the video is no longer
    /// there — a page reloaded under the feed, or a file deleted while it was
    /// open.
    ///
    /// Nil rather than Android's `indexOfFirst` returning `-1` and
    /// `centerOn` quietly refusing a negative index: the caller here has to
    /// say what it does when the video has gone, and what it does is nothing.
    public static func index(of key: String, in entries: [VideoGridEntry]) -> Int? {
        entries.firstIndex { $0.id == key }
    }
}

/// Every `/api/videos` path this screen asks for, escaped once, here.
///
/// `VideosScreen.kt:109-121` builds these inline with `Uri.encode`. They are
/// a type for the reason ``MusicQueuePath`` is: a library id and a relative
/// path both go in a **query** (not a path segment), both can hold a space, a
/// `&`, a `#` or a Windows backslash, and a screen assembling that string by
/// hand gets it wrong once and then silently reads the wrong video.
public enum VideoPath {

    /// Unreserved characters only, so `&`, `=`, `#`, `+`, `/`, `\` and a
    /// space are all escaped. Stricter than Android's `Uri.encode`, which
    /// leaves `!'()*` alone — both are correct in a query; this one is
    /// correct everywhere.
    static let safe = CharacterSet(charactersIn: "-._~").union(.alphanumerics)

    static func esc(_ raw: String) -> String {
        raw.addingPercentEncoding(withAllowedCharacters: safe) ?? raw
    }

    /// The grid's list, with the sort and the filter but **no paging** — the
    /// pager adds that (``Paging/path(_:_:)``).
    ///
    /// `sort=library` keeps each library's videos together under its header,
    /// which is what makes ``VideoGrid/grouped(_:)`` a grouping and not a
    /// shuffle. A blank filter is left out rather than sent as `q=`.
    public static func list(filter: String = "") -> String {
        var out = "/api/videos/list?sort=library"
        let needle = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        if !needle.isEmpty { out += "&q=\(esc(needle))" }
        return out
    }

    /// The identity pair every other route takes.
    static func identity(_ video: VideoRow) -> String {
        "library_id=\(esc(video.libraryID))&path=\(esc(video.rel))"
    }

    /// The video's bytes. `download=1` asks the server for a
    /// `Content-Disposition` attachment instead of inline playback.
    public static func stream(_ video: VideoRow, download: Bool = false) -> String {
        "/api/videos/stream?\(identity(video))" + (download ? "&download=1" : "")
    }

    /// One extracted frame, or a 204 when ffmpeg got none.
    public static func poster(_ video: VideoRow) -> String {
        "/api/videos/poster?\(identity(video))"
    }

    /// Who is watching, for the per-(device × person) resume store.
    ///
    /// `VideosScreen.kt:118`'s `videoIdentityQuery`. The person is left out
    /// entirely when nobody is signed in on this phone, because the server
    /// keys a row on `(device, person)` and `person_id=` empty is a different
    /// key from absent.
    public static func watcher(deviceID: String, personID: String? = nil) -> String {
        var out = "device_id=\(esc(deviceID))"
        if let personID, !personID.isEmpty { out += "&person_id=\(esc(personID))" }
        return out
    }

    /// Where this watcher left off in this video.
    public static func position(
        _ video: VideoRow,
        deviceID: String,
        personID: String? = nil
    ) -> String {
        "/api/videos/position?\(identity(video))&\(watcher(deviceID: deviceID, personID: personID))"
    }

    /// The resume save, which is a POST with a body.
    public static let savePosition = "/api/videos/position"

    /// The "recently played" strip: this watcher's newest resume rows.
    public static func recent(deviceID: String, personID: String? = nil) -> String {
        "/api/videos/recent?\(watcher(deviceID: deviceID, personID: personID))"
    }

    /// The body of a resume save.
    ///
    /// - Parameters:
    ///   - positionSec: clamped at zero. The server would too, but a negative
    ///     position here means the player reported one, and sending it is how
    ///     a resume row ends up unusable.
    ///   - durationSec: sent only when the player actually knows it; a live
    ///     or still-loading stream reports nothing, and `duration_sec: 0`
    ///     would overwrite a good value with a useless one.
    ///   - title: what the person saw it called, so the strip can label the
    ///     row without re-walking the library.
    ///
    /// `nonisolated static` so a test off the main actor can ask for the
    /// bytes without a model.
    public nonisolated static func saveBody(
        _ video: VideoRow,
        deviceID: String,
        personID: String? = nil,
        positionSec: Int64,
        durationSec: Int64?
    ) -> Data {
        var out = #"{"library_id":"# + quoted(video.libraryID)
        out += #","path":"# + quoted(video.rel)
        out += #","device_id":"# + quoted(deviceID)
        if let personID, let number = Int64(personID) {
            // The server types `person_id` as an integer; Android sends it
            // only when it parses as one (`VideosScreen.kt:129`) and so does
            // this, rather than sending a string the row would refuse.
            out += #","person_id":\#(number)"#
        }
        out += #","position_sec":\#(max(0, positionSec))"#
        if let durationSec, durationSec > 0 { out += #","duration_sec":\#(durationSec)"# }
        out += #","title":"# + quoted(video.displayTitle)
        out += "}"
        return Data(out.utf8)
    }

    /// A JSON string literal. A library label or a filename can hold a quote
    /// or a backslash — a Windows path holds backslashes by definition — and
    /// this is the one place that matters.
    nonisolated static func quoted(_ raw: String) -> String {
        var out = "\""
        for character in raw.unicodeScalars {
            switch character {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if character.value < 0x20 {
                    out += String(format: "\\u%04x", character.value)
                } else {
                    out.unicodeScalars.append(character)
                }
            }
        }
        return out + "\""
    }
}

/// Every word the Videos screen says, transcribed from the Kotlin.
public enum VideoStrings {

    // ---- the screen --------------------------------------------------------

    /// `VideosScreen.kt:246`.
    public static let title = "Videos"
    public static let subtitle = "videos across your files libraries · resume where you left off"
    public static let rescan = "rescan videos"
    public static let filterPlaceholder = "filter…"
    public static let recentlyPlayed = "recently played"

    public static let noMatch = "no videos match"
    public static func filterIs(_ needle: String) -> String { "filter = \"\(needle)\"" }
    public static let noneFound = "no videos found"
    public static let noneFoundSub = "drop video files (mp4 · webm · mkv · mov) into any files library"

    /// A grid section's heading: the library and how many videos it holds.
    public static func section(_ label: String, _ count: Int) -> String { "\(label) · \(count)" }

    /// A row the server named nothing at all. Android would draw an empty
    /// line; this is iOS-only wording, recorded in `IOS-ONLY-STRINGS.md`.
    public static let unnamedVideo = "video"

    public static let play = "play"

    // ---- the feed ----------------------------------------------------------

    public static let cantPlay = "can't play this video"
    public static let cantPlaySub = "swipe for the next one"
    public static let paused = "paused"
    public static let close = "close"
    public static let forward15 = "forward 15 seconds"
    public static let back15 = "back 15 seconds"
    public static let seekFlash = "15 s"

    /// `"3 / 48"` under the title.
    public static func counter(_ page: Int, of total: Int) -> String { "\(page) / \(total)" }

    // ---- the long-press sheets ---------------------------------------------

    public static let playlist = "playlist"
    public static let details = "details"
    public static let playFromBeginning = "play from beginning"
    public static let saveToDevice = "save to device"
    public static let upload = "upload"
    public static let share = "share"
    public static let removeFromPlaylist = "remove from playlist"

    /// `"playlist · 1 video"` / `"playlist · 7 videos"`.
    public static func playlistCount(_ count: Int) -> String {
        "\(playlist) · \(count) video\(count == 1 ? "" : "s")"
    }

    public static let metaSize = "size"
    public static let metaModified = "modified"
    public static let metaDuration = "duration"
    public static let metaResolution = "resolution"
    public static let metaBitrate = "bitrate"
    public static let metaFrameRate = "frame rate"
    public static let metaCodec = "codec"
    public static let metaLocation = "location"
    public static let unknown = "unknown"

    // ---- what the screen says when something goes wrong --------------------

    /// The feed refuses to start because the token may not be handed over —
    /// the server has not proved itself on this network **right now**.
    ///
    /// iOS-only: Android's player carries the token in a header through its
    /// own HTTP client, so this case cannot arise there. See
    /// ``VideoStream/url(for:in:)``.
    public static func cannotPlayYet(_ why: String) -> String {
        "can't play this video yet — \(why)"
    }

    /// The phone is not paired, so the device tier is closed to it.
    public static let pairToPlay = "pair this phone with your Domovoi to play videos"

    // ---- the words this screen shares with Music ---------------------------
    //
    // Said again here rather than reached for across screens: the same two
    // words, and ``MusicStrings`` is Music's vocabulary, not a common one.
    // If a third screen wants them they become a shared type, which is a
    // decision to make with three call sites rather than two.

    public static let retry = "try again"
    public static let loadMore = "load more"
}

/// One tile in the grid, with **its index among the tiles**.
///
/// The index is what ``VideosModel/shouldLoadMore(lastVisible:)`` compares
/// against the loaded row count, so it counts tiles and not headers: an
/// index that counted the section headings too would ask for the next page
/// one heading early per library, which on a house with six libraries is six
/// tiles early.
public struct VideoTile: Identifiable, Sendable, Equatable {
    public let gridIndex: Int
    public let video: VideoRow

    public var id: String { video.id }

    public init(gridIndex: Int, video: VideoRow) {
        self.gridIndex = gridIndex
        self.video = video
    }
}

/// One library's heading and its loaded tiles.
public struct VideoSection: Identifiable, Sendable, Equatable {
    public let libraryID: String
    public let label: String
    /// The server's total for this library, or the loaded count — whichever
    /// ``VideoGrid/entries(showRecent:grouped:totals:)`` put in the header.
    public let count: Int
    public var tiles: [VideoTile]

    public var id: String { libraryID }

    public init(libraryID: String, label: String, count: Int, tiles: [VideoTile]) {
        self.libraryID = libraryID
        self.label = label
        self.count = count
        self.tiles = tiles
    }
}
