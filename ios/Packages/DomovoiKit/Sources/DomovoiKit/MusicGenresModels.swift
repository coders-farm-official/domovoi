import Foundation

/// Music → Genres: the library by genre (owner, 2026-11-12).
///
/// `android/.../ui/screens/music/GenresTab.kt` (3cdd261b), whose web twin is
/// `music.jsx` `GenresTab` / `GenreDrawer`. The server is
/// `web/backend/api/music_genres.py` (V050): every shape here is read from
/// its response models, not guessed.
///
/// Genres come from the files' own tags. For tracks that were in the library
/// before genres were read, the server reads them in the background; until it
/// is done (`reading`) the tab says "reading genres from your files… N of M"
/// and re-reads itself every ``MusicGenres/readPollMs``, rather than passing a
/// partial list off as the whole one. Read tracks whose tags name no genre
/// are one last row, "no genre", so nothing is hidden.

/// One row of `GET /api/music/genres` (`music_genres.py:57` `GenreRow`).
///
/// Key `""` is the no-genre bucket (``MusicGenres/noGenreKey``) — which the
/// server never lists; this app adds it last (``MusicGenres/listRows(_:progress:search:hasMore:)``).
public struct MusicGenreRow: Equatable, Hashable, Sendable, PagedRow, Identifiable {
    public let key: String
    public let name: String
    public let trackCount: Int
    public let coverTrackID: Int64?

    public var id: String { key }

    public init(key: String, name: String, trackCount: Int, coverTrackID: Int64? = nil) {
        self.key = key
        self.name = name
        self.trackCount = trackCount
        self.coverTrackID = coverTrackID
    }

    private enum CodingKeys: String, CodingKey {
        case key, name
        case trackCount = "track_count"
        case coverTrackID = "cover_track_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        key = try values.decodeIfPresent(String.self, forKey: .key) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        trackCount = try values.decodeIfPresent(Int.self, forKey: .trackCount) ?? 0
        coverTrackID = try values.decodeIfPresent(Int64.self, forKey: .coverTrackID)
    }
}

/// The rest of a `GET /api/music/genres` answer: how far the genre read has
/// got (`music_genres.py:64` `GenrePage`, `tags_read` … `untagged_count`).
///
/// **Library-wide, not about the page**: the counts come from one query over
/// every track (`_progress`), whatever the search or sort was. So a newer
/// answer for a different search still says the true thing about the read,
/// and the model keeps it across a query change.
public struct MusicGenreProgress: Equatable, Sendable, Decodable {
    public let tagsRead: Int
    public let tagsTotal: Int
    public let reading: Bool
    public let untaggedCount: Int

    public init(tagsRead: Int = 0, tagsTotal: Int = 0, reading: Bool = false, untaggedCount: Int = 0) {
        self.tagsRead = tagsRead
        self.tagsTotal = tagsTotal
        self.reading = reading
        self.untaggedCount = untaggedCount
    }

    private enum CodingKeys: String, CodingKey {
        case tagsRead = "tags_read"
        case tagsTotal = "tags_total"
        case reading
        case untaggedCount = "untagged_count"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        tagsRead = try values.decodeIfPresent(Int.self, forKey: .tagsRead) ?? 0
        tagsTotal = try values.decodeIfPresent(Int.self, forKey: .tagsTotal) ?? 0
        reading = try values.decodeIfPresent(Bool.self, forKey: .reading) ?? false
        untaggedCount = try values.decodeIfPresent(Int.self, forKey: .untaggedCount) ?? 0
    }
}

/// One whole `GET /api/music/genres` answer: the page **and** the progress,
/// decoded from the one object.
///
/// Two decodes over one decoder rather than a `Page` with an untyped `extra`
/// — ``Page``'s own doc says a typed second value is what a screen needing
/// the rest of the answer should add, and this is that, without widening
/// ``Page`` for every other list.
public struct MusicGenreAnswer: Sendable, Decodable {
    public let page: Page<MusicGenreRow>
    public let progress: MusicGenreProgress

    public init(from decoder: any Decoder) throws {
        page = try Page<MusicGenreRow>(from: decoder)
        progress = try MusicGenreProgress(from: decoder)
    }
}

/// `GET /api/music/genre?key=` (`music_genres.py:83` `GenreDetail`): one
/// genre and every track id, artist order, for a room. At most 2,000 ids
/// (`MAX_TRACK_IDS`), with `track_ids_truncated` saying so.
public struct MusicGenreDetail: Equatable, Sendable, Decodable {
    public let key: String
    public let name: String
    public let trackCount: Int
    public let trackIDs: [Int64]
    public let trackIDsTruncated: Bool

    public init(key: String, name: String, trackCount: Int, trackIDs: [Int64], trackIDsTruncated: Bool = false) {
        self.key = key
        self.name = name
        self.trackCount = trackCount
        self.trackIDs = trackIDs
        self.trackIDsTruncated = trackIDsTruncated
    }

    private enum CodingKeys: String, CodingKey {
        case key, name
        case trackCount = "track_count"
        case trackIDs = "track_ids"
        case trackIDsTruncated = "track_ids_truncated"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        key = try values.decodeIfPresent(String.self, forKey: .key) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        trackCount = try values.decodeIfPresent(Int.self, forKey: .trackCount) ?? 0
        trackIDs = try values.decodeIfPresent([Int64].self, forKey: .trackIDs) ?? []
        trackIDsTruncated = try values.decodeIfPresent(Bool.self, forKey: .trackIDsTruncated) ?? false
    }
}

/// A ``MusicGenreDetail`` **tagged with the server and genre it was read
/// for** (`GenresTab.kt:357-365`): an answer that lands after either changed
/// is never sent on to a room.
public struct MusicGenreDetailRead: Equatable, Sendable {
    public let server: String
    public let detail: MusicGenreDetail
}

/// The genre list's sort (`GenresTab.kt:251`): the server's
/// `Literal["name", "tracks"]`, with Android's words.
public enum MusicGenreSort: String, CaseIterable, Sendable {
    case name
    case tracks

    public var label: String {
        switch self {
        case .name: return "name"
        case .tracks: return "most tracks"
        }
    }
}

/// The Genres tab's rules, every one pure — `GenresTab.kt`'s top-level
/// functions and `TrackSets.kt`'s.
public enum MusicGenres {

    /// The genre key of the tracks whose tags name no genre.
    public static let noGenreKey = ""

    /// How often the list re-reads itself while the server is still reading
    /// tags (`GENRE_READ_POLL_MS`).
    public static let readPollMs: Int64 = 10_000

    /// How long typing settles before the list is re-read (`GenresTab.kt:174`).
    public static let searchDebounceMs: Int64 = 250

    /// How many songs a genre's list asks for at a time (`GenresTab.kt:372`).
    public static let songsPageSize = 100

    /// Most track ids a room takes in one go — play-tracks' own cap, which
    /// is ``MusicCastPlanner/maxIDs`` (`TrackSets.kt:25`).
    public static let roomTrackCap = MusicCastPlanner.maxIDs

    /// Most entries this phone's queue holds (``PlayerQueueWindow/maximum``,
    /// Android's `QueueWindow.MAX`).
    public static let phoneTrackCap = PlayerQueueWindow.maximum

    private static let queryAllowed = CharacterSet(charactersIn: "-._~").union(.alphanumerics)

    private static func encoded(_ text: String) -> String {
        text.addingPercentEncoding(withAllowedCharacters: queryAllowed) ?? text
    }

    /// `GET /api/music/genres` for a search and sort, **no paging** (the pager
    /// adds it). A blank search is left out, as the library's is.
    public static func path(search: String, sort: MusicGenreSort) -> String {
        var out = "/api/music/genres?sort=\(sort.rawValue)"
        let typed = search.trimmingCharacters(in: .whitespacesAndNewlines)
        if !typed.isEmpty { out += "&q=\(encoded(typed))" }
        return out
    }

    /// One genre's songs as library rows, by artist (the web's
    /// `?genre=&sort=artist`). The empty key is **sent** as `genre=`: present
    /// and empty is the no-genre bucket (`music.py:146`), absent is the whole
    /// library.
    public static func songsPath(key: String) -> String {
        "/api/music/library?genre=\(encoded(key))&sort=artist"
    }

    /// `GET /api/music/genre?key=` — every id of one genre, for a room.
    public static func detailPath(key: String) -> String {
        "/api/music/genre?key=\(encoded(key))"
    }

    /// "reading genres from your files… 1,200 of 5,232" while the server
    /// reads, else nil.
    public static func readingLine(_ progress: MusicGenreProgress?) -> String? {
        guard let progress, progress.reading else { return nil }
        let style = IntegerFormatStyle<Int>().locale(Locale(identifier: "en_US"))
        return "reading genres from your files… \(progress.tagsRead.formatted(style)) "
            + "of \(progress.tagsTotal.formatted(style))"
    }

    /// The rows the tab draws: the server's genres, then — once the whole
    /// list is loaded and no search narrows it — "no genre" with the read
    /// tracks that have none, last, as the web does (`genreListRows`).
    ///
    /// Mid-list it would sit between two pages' genres; in a search it would
    /// be a result that does not match.
    public static func listRows(
        _ items: [MusicGenreRow],
        progress: MusicGenreProgress?,
        search: String,
        hasMore: Bool
    ) -> [MusicGenreRow] {
        let untagged = progress?.untaggedCount ?? 0
        let blank = search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        guard blank, !hasMore, untagged > 0 else { return items }
        return items + [MusicGenreRow(key: noGenreKey, name: MusicGenresStrings.noGenre, trackCount: untagged)]
    }

    /// The ids a room gets: in order, or `shuffler`'s order; at most
    /// ``roomTrackCap`` (`roomTrackIds`).
    public static func roomTrackIDs(
        _ ids: [Int64],
        shuffle: Bool,
        shuffler: ([Int64]) -> [Int64] = { $0.shuffled() }
    ) -> [Int64] {
        Array((shuffle ? shuffler(ids) : ids).prefix(roomTrackCap))
    }

    /// Said under the room buttons when a room can't take the whole set.
    public static func roomCapNote(_ count: Int) -> String? {
        guard count > roomTrackCap else { return nil }
        return "a room takes \(roomTrackCap) at a time — the first \(roomTrackCap) "
            + "(or a random \(roomTrackCap) when shuffled)"
    }

    /// Said under "play on this device" when the phone's queue can't hold the
    /// whole set (`phoneCapNote`).
    public static func phoneCapNote(_ count: Int) -> String? {
        guard count > phoneTrackCap else { return nil }
        return "this device takes \(phoneTrackCap) at a time — the first \(phoneTrackCap) "
            + "(or a random \(phoneTrackCap) when shuffled)"
    }

    /// The sentence a set played on this phone says (`playTracksHereAndSay`):
    /// **the set's** name, "playing Favorites on this device", rather than its
    /// first song's.
    ///
    /// - Parameter took: what ``MusicLocalQueueModel/takeToast()`` handed
    ///   back straight after ``MusicLocalQueueModel/playHere(_:startAt:)``.
    ///   That model only sets one when no room was left; when one was, the
    ///   player says the combined `… · paused kitchen` sentence itself, so
    ///   nil here means "say nothing more", not "say this".
    public static func saidAfterPlayingHere(took: String?, label: String) -> String? {
        guard took != nil else { return nil }
        return MusicGenresStrings.playingHere(label)
    }
}

/// Every word the Genres tab adds. Android's (`GenresTab.kt`) unless noted.
public enum MusicGenresStrings {
    public static let tab = "genres"
    public static let noGenre = "no genre"
    public static let searchPlaceholder = "search genres…"
    public static let sortLabel = "sort"
    public static let fillsIn = "the list fills in as it goes"
    public static let noGenresMatch = "no genres match"
    public static let clearSearch = "clear search"
    public static let noGenresFoundYet = "no genres found yet"
    public static let genresAppearAsRead = "genres show up here as your files are read"
    public static let noGenresYet = "no genres yet"
    public static let genresAppearFromTags = "songs whose tags name a genre show up here"
    public static let eyebrow = "genre"
    public static let tagsNameNoGenre = "their tags name no genre"
    public static let noSongs = "no songs"
    public static let noSongsNote = "nothing in the library for this choice"
    /// The phone's own single-song play, the row's icon (`GenresTab.kt:575`).
    public static let playSongHere = "play on this device"

    public static func searchedFor(_ search: String) -> String { "q = “\(search)”" }

    public static func genres(_ count: Int) -> String { "\(count) genre" + (count == 1 ? "" : "s") }

    public static func tracks(_ count: Int) -> String { "\(count) track" + (count == 1 ? "" : "s") }

    /// The head of a genre: its count, and for the no-genre bucket why.
    public static func detailSubtitle(count: Int, key: String) -> String {
        tracks(count) + (key == MusicGenres.noGenreKey ? " · \(tagsNameNoGenre)" : "")
    }

    public static func readingLine(_ line: String) -> String { "\(line) · \(fillsIn)" }

    public static func playingInRoom(_ name: String, room: String, shuffle: Bool) -> String {
        (shuffle ? "shuffling" : "playing") + " \(name) in \(room)…"
    }

    public static func playInRoomFailed(_ message: String, shuffle: Bool) -> String {
        (shuffle ? "shuffle" : "play") + " failed: \(message)"
    }

    /// `"playing Favorites on this device"` — a whole set, no quotes
    /// (`TrackSets.kt:66`).
    public static func playingHere(_ label: String) -> String { "playing \(label) on this device" }

    /// Under the phone buttons while the list has more to load: the phone
    /// takes what is loaded (`GenresTab.kt:483`).
    public static func loadedSoFar(_ count: Int) -> String {
        "this device takes the " + (count == 1 ? "1 song" : "\(count) songs")
            + " loaded so far — scroll the list for more"
    }

    // ---- the queue (integration of the music and queues units, 21b5ccfb / cb780e90)

    /// The room's `add to queue` and this phone's `queue here`
    /// (`GenresTab.kt:453`, `:490`).
    public static let addToQueue = "add to queue"
    public static let queueHere = "queue here"

    private static func songsWord(_ n: Int) -> String { "\(n) song" + (n == 1 ? "" : "s") }

    /// `"added 12 songs · Jazz to parlour's queue"` (`GenresTab.kt:397`).
    public static func addedToRoomQueue(_ count: Int, genre: String, room: String) -> String {
        "added \(songsWord(count)) · \(genre) to \(room)'s queue"
    }

    /// `"add failed: …"` (`GenresTab.kt:400`).
    public static func addFailed(_ message: String) -> String { "add failed: \(message)" }

    /// `"queued 12 songs · Jazz"` (`TrackSets.kt:80`, `queueTracksHereAndSay`).
    public static func queuedHere(_ count: Int, label: String) -> String {
        "queued \(songsWord(count)) · \(label)"
    }
}
