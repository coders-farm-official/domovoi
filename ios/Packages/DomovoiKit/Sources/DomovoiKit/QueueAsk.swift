import Foundation

/// The queue's search box: search the library from a queue and add what is
/// found.
///
/// `QueueAsk.kt` (Android 21b5ccfb, batch 2 §2), which is itself the web's
/// `PlayerQueueAdd` (player.jsx): the prompts, the two paths and every word
/// here are the web's, so the three clients read the same. Plain values, so a
/// package test can hold each against Android's.
public enum QueueAsk {

    /// What the box asks, one picked at random each time a queue opens and
    /// kept while typing. The web's `_QUEUE_ASK_PROMPTS`, in order.
    public static let prompts: [String] = [
        "What's next? Name a song, artist or vibe…",
        "Feed me a song…",
        "Who should we hear from next?",
        "Something for the mood…",
        "Got a song stuck in your head?",
        "What are we in the mood for?",
        "Add a little something…",
        "Name a tune, an album, a feeling…",
        "Who's up next on the stage?",
        "Whisper a song title…",
        "Hungry for a genre? Say which…",
        "Keep it going: what else?",
        "An old favorite, or something new?",
        "Tell the house what to play next…",
        "Dig up a song, any song…",
    ]

    /// One prompt, at random (the web's `_queueAskPrompt`).
    public static func prompt<G: RandomNumberGenerator>(using generator: inout G) -> String {
        prompts[Int.random(in: 0..<prompts.count, using: &generator)]
    }

    public static func prompt() -> String {
        var any = SystemRandomNumberGenerator()
        return prompt(using: &any)
    }

    /// Fewer characters than this ask nothing (`_QUEUE_SEARCH_MIN`).
    public static let searchMin = 2

    /// Songs a genre row adds, shuffled (`_QUEUE_GENRE_TAKE`).
    public static let genreTake = 25

    /// The web's debounce on the box, `useDebouncedValue(q, 250)`.
    public static let debounceMs: UInt64 = 250

    /// The text the box asks about, or nil while it is too short to ask.
    public static func term(_ typed: String) -> String? {
        let tidy = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        return tidy.count >= searchMin ? tidy : nil
    }

    /// `encodeURIComponent`, exactly: the unreserved set and `!*'()`, ASCII
    /// only. Not `.alphanumerics`, which is every script's letters — an `é`
    /// would go out bare where the web and Android send `%C3%A9`.
    static let component = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.!~*'()"
    )

    public static func encode(_ text: String) -> String {
        text.addingPercentEncoding(withAllowedCharacters: component) ?? text
    }

    /// The two reads one search makes, as the web makes them: songs by
    /// title, artist or album (the library search, 8 of them) and genres (2).
    public static func songsPath(_ term: String) -> String {
        "/api/music/library?q=\(encode(term))&limit=8"
    }

    public static func genresPath(_ term: String) -> String {
        "/api/music/genres?q=\(encode(term))&limit=2"
    }

    /// A genre row's songs: up to 200, shuffled here, the first
    /// ``genreTake`` added.
    public static func genreSongsPath(_ key: String) -> String {
        "/api/music/library?genre=\(encode(key))&limit=200"
    }

    /// A genre's songs as they are added: shuffled, the first ``genreTake``.
    public static func genreTake(
        _ all: [LibraryTrack],
        shuffler: ([LibraryTrack]) -> [LibraryTrack] = { $0.shuffled() }
    ) -> [LibraryTrack] {
        Array(shuffler(all).prefix(genreTake))
    }
}

/// One `GET /api/music/genres` row, as the search box reads it.
///
/// `music_genres.py`'s `GenreRow`, narrowed to the three fields the box
/// draws. Its own name rather than `GenreRow`, which is the genres screen's
/// word on Android and may be this app's one day with more fields on it.
public struct QueueGenre: Equatable, Sendable, Decodable {
    public let key: String
    public let name: String
    public let trackCount: Int

    public init(key: String, name: String, trackCount: Int = 0) {
        self.key = key
        self.name = name
        self.trackCount = trackCount
    }

    private enum CodingKeys: String, CodingKey {
        case key, name
        case trackCount = "track_count"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        key = try values.decodeIfPresent(String.self, forKey: .key) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        trackCount = try values.decodeIfPresent(Int.self, forKey: .trackCount) ?? 0
    }
}

/// `GET /api/music/genres`'s rows (the paged answer's items).
struct QueueGenrePage: Decodable {
    let items: [QueueGenre]

    private enum CodingKeys: String, CodingKey { case items }

    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        items = try values.decodeIfPresent([QueueGenre].self, forKey: .items) ?? []
    }
}

/// One row under the box: a song, or a genre that adds a shuffled handful.
public enum QueueFind: Equatable, Sendable, Identifiable {
    case song(LibraryTrack)
    case genre(QueueGenre)

    public var id: String {
        switch self {
        case .song(let track): return "s\(track.id)"
        case .genre(let genre): return "g\(genre.key)"
        }
    }

    /// The row in words: the song's title, or "<genre> songs".
    public var label: String {
        switch self {
        case .song(let track): return track.title ?? "that song"
        case .genre(let genre): return "\(genre.name) songs"
        }
    }

    /// The first line: "title · artist".
    public var title: String {
        switch self {
        case .song(let track):
            return (track.title ?? "unknown") + (track.artist.map { " · \($0)" } ?? "")
        case .genre(let genre):
            return genre.name
        }
    }

    /// The second line: the album, or "genre · N songs, shuffled".
    public var sub: String {
        switch self {
        case .song(let track):
            return track.album ?? "—"
        case .genre(let genre):
            return "genre · \(genre.trackCount) song" + (genre.trackCount == 1 ? "" : "s") + ", shuffled"
        }
    }
}

/// The rows, songs first, then genres; a genre without a key (the no-genre
/// bucket) is not offered, as on the web.
public func queueFinds(songs: [LibraryTrack], genres: [QueueGenre]?) -> [QueueFind] {
    songs.map(QueueFind.song) + (genres ?? []).filter { !$0.key.isEmpty }.map(QueueFind.genre)
}

/// Where Play next / Add to queue go: this phone's own queue, or a room's.
public enum QueueTarget: Equatable, Hashable, Sendable {
    case phone
    case room(String)

    public var roomID: String? {
        if case .room(let id) = self { return id }
        return nil
    }

    /// A song row follows the player: the room it casts to, else this phone
    /// (`queueTargetOf`).
    public static func following(castsTo room: String?) -> QueueTarget {
        room.map(QueueTarget.room) ?? .phone
    }
}

/// The queue box's words and the queue's new ones — the web's, unchanged
/// (`QueueAsk.kt:117-150`, `QueueAdder.kt`, `RoomQueueSheet.kt`).
public enum QueueStrings {
    public static let searchLabel = "search the library to add to the queue"
    public static let searching = "searching…"
    public static let clearSearch = "clear the search"

    public static func nothingMatches(_ term: String) -> String {
        "Nothing matches \"\(term)\". Try an artist, an album or a genre."
    }

    public static func searchFailed(_ why: String) -> String { "Couldn't search the library: \(why)" }
    public static func nothingToAdd(_ label: String) -> String { "Nothing to add from \(label)." }
    public static func addFailed(_ label: String, _ why: String) -> String { "Couldn't add \(label): \(why)" }

    /// "Playing next: X" / "Added: X", with "(n songs)" for more than one;
    /// "… in <room>: X" for a room's queue.
    public static func added(_ label: String, count: Int, next: Bool, room: String? = nil) -> String {
        (next ? "Playing next" : "Added") + (room.map { " in \($0)" } ?? "") + ": \(label)"
            + (count > 1 ? " (\(count) songs)" : "")
    }

    /// The names of a row's two buttons (the web's aria-labels).
    public static func playNextLabel(_ label: String) -> String { "play \(label) next" }
    public static func addToQueueLabel(_ label: String) -> String { "add \(label) to the queue" }

    /// A song row's toast after Play next / Add to queue.
    public static func songQueued(_ target: QueueTarget, title: String, next: Bool) -> String {
        switch target {
        case .phone:
            return next ? "playing next: \"\(title)\"" : "queued \"\(title)\""
        case .room(let room):
            return next ? "playing next in \(room): \"\(title)\"" : "added \"\(title)\" to \(room)'s queue"
        }
    }

    // ---- save as playlist ----------------------------------------------

    public static let saveAsPlaylist = "save as playlist"
    public static let saveAsPlaylistHint = "save this queue as a playlist"
    public static let saveAsPlaylistNone = "no library songs in the queue to save"
    public static let playlistNamePrompt = "name your playlist…"
    public static let playlistNameLabel = "playlist name"
    public static let save = "save"
    public static let cancel = "cancel"
    /// `savedAsNote` (`QueueAsk.kt`): the web's line, with the count of songs
    /// that went in.
    public static func savedAs(_ name: String, songs: Int) -> String {
        "Saved as \"\(name)\" (\(songs) song\(songs == 1 ? "" : "s"))."
    }
    public static func saveFailed(_ why: String) -> String { "Couldn't save the playlist: \(why)" }

    /// The name of a room queue's save button.
    public static func roomSaveHint(_ room: String) -> String { "save \(room)'s queue as a playlist" }

    // ---- the track sheet's queue buttons --------------------------------

    public static let playNext = "play next"
    public static let addToQueue = "add to queue"
    public static let thisDevice = "this device"
    public static func playNextIn(_ room: String) -> String { "play next in \(room)" }
    public static func addToRoomQueue(_ room: String) -> String { "add to \(room)'s queue" }

    /// The name of a song row's Play next / Add to queue menu.
    public static func songQueueMenu(_ title: String) -> String { "queue \(title)" }

    // ---- a room's queue, one tap away -----------------------------------

    /// The name of a room's queue button, and its sheet's title.
    public static func roomQueueLabel(_ room: String) -> String { "\(room)'s queue" }

    /// The room queue's empty state, now that a search box sits above it.
    public static let queueEmpty = "queue is empty"
    public static func queueEmptyNote(room: String) -> String {
        "search above to add a song, or say \"play something\" in \(room)"
    }

    public static let playNow = "play now"
}
