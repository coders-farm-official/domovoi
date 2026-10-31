import Foundation

/// The shapes the web backend answers Music with.
///
/// `android/.../ui/screens/music/MusicModels.kt`, whose own heading says the
/// rule this file keeps: **everything optional with a default, because the
/// backend evolves.** A field the server stops sending, or starts sending,
/// must not turn a screenful of tracks into a decode failure — and on this
/// track it already has, twice, in the stub house's Home reads
/// ([[ios-stub-house]]).
///
/// So every one of these has a hand-written `init(from:)` using
/// `decodeIfPresent`. The synthesised decoder throws on a missing
/// non-optional key, and `Int = 0` in the declaration does **not** make the
/// key optional to it — which is the exact trap `SavedShellStore.Saved` and
/// ``TrustBook`` document, met again here for a different reason.

/// What a room is playing, inside ``NowPlayingRoom``.
public struct NowPlayingSong: Equatable, Sendable, Decodable {
    public let title: String?
    public let artist: String?
    public let file: String?
    public let durationSec: Double?

    public init(title: String? = nil, artist: String? = nil, file: String? = nil, durationSec: Double? = nil) {
        self.title = title
        self.artist = artist
        self.file = file
        self.durationSec = durationSec
    }

    private enum CodingKeys: String, CodingKey {
        case title, artist, file
        case durationSec = "duration_sec"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = try values.decodeIfPresent(String.self, forKey: .title)
        artist = try values.decodeIfPresent(String.self, forKey: .artist)
        file = try values.decodeIfPresent(String.self, forKey: .file)
        durationSec = try values.decodeIfPresent(Double.self, forKey: .durationSec)
    }

    /// What a card shows: the tag title, or the filename when there is no
    /// tag, or the generic word.
    ///
    /// The filename is taken off **both** separators. The server may be
    /// Windows — Kamron's house is — so a path can arrive as
    /// `C:\music\artist\song.mp3`, and splitting on `/` alone would show the
    /// whole thing (`MusicIo.kt` strips both, and says why).
    public var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        if let leaf = MusicFile.basename(file), !leaf.isEmpty { return leaf }
        return MusicStrings.unnamedTrack
    }
}

/// One room's now-playing row — `GET /api/music/now-playing` answers an array
/// of these.
public struct NowPlayingRoom: Equatable, Sendable, Decodable {
    public let roomID: String
    /// MPD's word: `play`, `pause`, `stop`, or nothing at all.
    public let state: String?
    public let song: NowPlayingSong?
    public let elapsedSec: Double?
    public let favorited: Bool

    /// The registered source slug — `library`, `playlist`, or a plugin's own
    /// — and an optional external link its matcher supplied.
    ///
    /// Provider-agnostic on purpose (design §4.7/§10.2): the vocabulary is
    /// **open**, so nothing here switches on a known set, and an unknown slug
    /// gets the generic wording rather than being hidden.
    public let source: String?
    public let sourceURL: String?

    /// The library row this song resolves to, or nil for a stream or a file
    /// outside the library.
    public let trackID: Int64?

    public init(
        roomID: String = "",
        state: String? = nil,
        song: NowPlayingSong? = nil,
        elapsedSec: Double? = nil,
        favorited: Bool = false,
        source: String? = nil,
        sourceURL: String? = nil,
        trackID: Int64? = nil
    ) {
        self.roomID = roomID
        self.state = state
        self.song = song
        self.elapsedSec = elapsedSec
        self.favorited = favorited
        self.source = source
        self.sourceURL = sourceURL
        self.trackID = trackID
    }

    private enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case state, song, favorited, source
        case elapsedSec = "elapsed_sec"
        case sourceURL = "source_url"
        case trackID = "track_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        state = try values.decodeIfPresent(String.self, forKey: .state)
        song = try values.decodeIfPresent(NowPlayingSong.self, forKey: .song)
        elapsedSec = try values.decodeIfPresent(Double.self, forKey: .elapsedSec)
        favorited = try values.decodeIfPresent(Bool.self, forKey: .favorited) ?? false
        source = try values.decodeIfPresent(String.self, forKey: .source)
        sourceURL = try values.decodeIfPresent(String.self, forKey: .sourceURL)
        trackID = try values.decodeIfPresent(Int64.self, forKey: .trackID)
    }

    /// Sound is coming out of this room now.
    public var isPlaying: Bool { state == "play" }

    /// Stopped with something still loaded — `NowPlayingCards.kt:110`'s
    /// `paused`, which needs the song as well as the word: a room that is
    /// `pause` with nothing loaded is an idle room, not a paused one.
    public var isPaused: Bool { state == "pause" && song != nil }

    /// Where the song is now: the server's `elapsed_sec` plus however many
    /// seconds have passed locally since that answer landed.
    ///
    /// The server's figure is canonical and the local tick is **additive on
    /// top of it** (`MusicScreen.kt:143`), so a card counts up between pushes
    /// instead of freezing, and a push puts it back where the server says.
    /// A room that is not playing does not advance.
    public func elapsed(tick: Int) -> Double? {
        guard let elapsedSec else { return nil }
        guard isPlaying else { return elapsedSec }
        let end = song?.durationSec
        let moved = elapsedSec + Double(max(0, tick))
        return end.map { min(moved, $0) } ?? moved
    }
}

/// A row of the library — `GET /api/music/library`.
public struct LibraryTrack: Equatable, Sendable, PagedRow, Identifiable {
    public let id: Int64
    public let title: String?
    public let artist: String?
    public let album: String?
    public let durationSec: Double?
    /// The provider plugin's slug that stamped this row, or nil for a file
    /// the library found on disk.
    public let source: String?
    public let sourceID: String?
    public let addedAt: String?
    /// How it got here: `manual`, a plugin's own word, or nothing.
    public let addedVia: String?
    public let enrichedAt: String?
    public let filePath: String?
    public let favorited: Bool

    public init(
        id: Int64 = 0,
        title: String? = nil,
        artist: String? = nil,
        album: String? = nil,
        durationSec: Double? = nil,
        source: String? = nil,
        sourceID: String? = nil,
        addedAt: String? = nil,
        addedVia: String? = nil,
        enrichedAt: String? = nil,
        filePath: String? = nil,
        favorited: Bool = false
    ) {
        self.id = id
        self.title = title
        self.artist = artist
        self.album = album
        self.durationSec = durationSec
        self.source = source
        self.sourceID = sourceID
        self.addedAt = addedAt
        self.addedVia = addedVia
        self.enrichedAt = enrichedAt
        self.filePath = filePath
        self.favorited = favorited
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, artist, album, source, favorited
        case durationSec = "duration_sec"
        case sourceID = "source_id"
        case addedAt = "added_at"
        case addedVia = "added_via"
        case enrichedAt = "enriched_at"
        case filePath = "file_path"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title)
        artist = try values.decodeIfPresent(String.self, forKey: .artist)
        album = try values.decodeIfPresent(String.self, forKey: .album)
        durationSec = try values.decodeIfPresent(Double.self, forKey: .durationSec)
        source = try values.decodeIfPresent(String.self, forKey: .source)
        sourceID = try values.decodeIfPresent(String.self, forKey: .sourceID)
        addedAt = try values.decodeIfPresent(String.self, forKey: .addedAt)
        addedVia = try values.decodeIfPresent(String.self, forKey: .addedVia)
        enrichedAt = try values.decodeIfPresent(String.self, forKey: .enrichedAt)
        filePath = try values.decodeIfPresent(String.self, forKey: .filePath)
        favorited = try values.decodeIfPresent(Bool.self, forKey: .favorited) ?? false
    }

    /// The name a row shows: the tag title, the file's own basename, or the
    /// generic word. `MusicScreen.kt`'s `toItem`, in that order.
    public var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        if let leaf = MusicFile.basename(filePath), !leaf.isEmpty { return leaf }
        return MusicStrings.unnamedTrack
    }

    /// The same row with its favourite flag moved.
    ///
    /// Every field is a `let`, so the toggle has to rebuild the row; doing it
    /// here rather than at the call site means a field added later cannot be
    /// silently dropped, which is what a twelve-argument initialiser call
    /// inside a model would do the first time somebody missed a line.
    public func favorited(_ value: Bool) -> LibraryTrack {
        LibraryTrack(
            id: id,
            title: title,
            artist: artist,
            album: album,
            durationSec: durationSec,
            source: source,
            sourceID: sourceID,
            addedAt: addedAt,
            addedVia: addedVia,
            enrichedAt: enrichedAt,
            filePath: filePath,
            favorited: value
        )
    }

    /// The source pill, or nil for none.
    ///
    /// **Nil, not `"manual"`.** A row with no source used to show the same
    /// word as `added_via`'s "manual" right beside it, so every file the
    /// library found on disk read "manual manual" (`MusicBits.kt:112`). A
    /// pill with nothing to say is not drawn.
    public var sourcePill: String? {
        guard let source, !source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return source
    }
}

/// `GET /api/music/library/stats`.
public struct LibraryStats: Equatable, Sendable, Decodable {
    public let totalTracks: Int
    public let totalDurationSec: Double?
    public let byAddedVia: [String: Int]
    public let enrichedCount: Int

    public init(
        totalTracks: Int = 0,
        totalDurationSec: Double? = nil,
        byAddedVia: [String: Int] = [:],
        enrichedCount: Int = 0
    ) {
        self.totalTracks = totalTracks
        self.totalDurationSec = totalDurationSec
        self.byAddedVia = byAddedVia
        self.enrichedCount = enrichedCount
    }

    private enum CodingKeys: String, CodingKey {
        case totalTracks = "total_tracks"
        case totalDurationSec = "total_duration_sec"
        case byAddedVia = "by_added_via"
        case enrichedCount = "enriched_count"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        totalTracks = try values.decodeIfPresent(Int.self, forKey: .totalTracks) ?? 0
        totalDurationSec = try values.decodeIfPresent(Double.self, forKey: .totalDurationSec)
        byAddedVia = try values.decodeIfPresent([String: Int].self, forKey: .byAddedVia) ?? [:]
        enrichedCount = try values.decodeIfPresent(Int.self, forKey: .enrichedCount) ?? 0
    }
}

/// `GET /api/playlists`. `isVirtual` marks the ones the server composes —
/// "recently added", a favourites view — which cannot be edited and are
/// filtered out of every "add to playlist" offer (`MusicScreen.kt:155`).
public struct Playlist: Equatable, Sendable, Decodable, Identifiable {
    public let id: Int64
    public let name: String
    public let description: String?
    public let trackCount: Int
    public let createdAt: String?
    public let isVirtual: Bool
    public let coverEmoji: String?
    public let coverColor: String?

    public init(
        id: Int64 = 0,
        name: String = "",
        description: String? = nil,
        trackCount: Int = 0,
        createdAt: String? = nil,
        isVirtual: Bool = false,
        coverEmoji: String? = nil,
        coverColor: String? = nil
    ) {
        self.id = id
        self.name = name
        self.description = description
        self.trackCount = trackCount
        self.createdAt = createdAt
        self.isVirtual = isVirtual
        self.coverEmoji = coverEmoji
        self.coverColor = coverColor
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, description
        case trackCount = "track_count"
        case createdAt = "created_at"
        case isVirtual = "is_virtual"
        case coverEmoji = "cover_emoji"
        case coverColor = "cover_color"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        description = try values.decodeIfPresent(String.self, forKey: .description)
        trackCount = try values.decodeIfPresent(Int.self, forKey: .trackCount) ?? 0
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        isVirtual = try values.decodeIfPresent(Bool.self, forKey: .isVirtual) ?? false
        coverEmoji = try values.decodeIfPresent(String.self, forKey: .coverEmoji)
        coverColor = try values.decodeIfPresent(String.self, forKey: .coverColor)
    }
}

/// What `POST /api/music/now-playing/{room}/favorite` answers.
public struct NpFavoriteResponse: Equatable, Sendable, Decodable {
    /// Open vocabulary: `library` and `radio` are core-known; a provider
    /// plugin reports its own slug and gets the generic copy.
    public let kind: String?
    public let alreadyFavorited: Bool
    public let title: String?
    /// Server-supplied copy, **preferred over anything this app composes**.
    public let message: String?

    public init(kind: String? = nil, alreadyFavorited: Bool = false, title: String? = nil, message: String? = nil) {
        self.kind = kind
        self.alreadyFavorited = alreadyFavorited
        self.title = title
        self.message = message
    }

    private enum CodingKeys: String, CodingKey {
        case kind, title, message
        case alreadyFavorited = "already_favorited"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        kind = try values.decodeIfPresent(String.self, forKey: .kind)
        alreadyFavorited = try values.decodeIfPresent(Bool.self, forKey: .alreadyFavorited) ?? false
        title = try values.decodeIfPresent(String.self, forKey: .title)
        message = try values.decodeIfPresent(String.self, forKey: .message)
    }

    /// The toast.
    ///
    /// `MusicScreen.kt:210`'s `when`, in its order, and the order is the
    /// point: **the server's own sentence wins**, because the vocabulary of
    /// `kind` is open and a plugin knows what its own thing is called. Only
    /// when the server said nothing does this app guess, and then it guesses
    /// generically for every slug it does not know.
    public var toast: String {
        if let message, !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return message
        }
        let label = title ?? MusicStrings.unnamedTrack
        if alreadyFavorited {
            return kind == "radio"
                ? "\(label) is already a favorite station"
                : "\(label) is already favorited"
        }
        return kind == "radio" ? "favorited station \(label)" : "favorited \(label)"
    }
}

/// A file path, as the two apps have to read one.
public enum MusicFile {

    /// The leaf of a path, off **either** separator.
    ///
    /// The server may be Windows, so a stored path can be
    /// `C:\music\Artist\Song.mp3`; Android strips both
    /// (`substringAfterLast('/')` then `substringAfterLast('\\')`) and so does
    /// this. Nil for nil or for a path that is nothing but separators.
    public static func basename(_ path: String?) -> String? {
        guard let path else { return nil }
        let leaf = path.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last
        guard let leaf, !leaf.isEmpty else { return nil }
        return String(leaf)
    }

    /// The extension of a path with its dot, or `".mp3"` when there is none
    /// to read — `MusicIo.kt`'s fallback, which exists because a saved file
    /// with no extension is a file the phone will not open.
    public static func dottedExtension(_ path: String?) -> String {
        guard let leaf = basename(path) else { return ".mp3" }
        guard let dot = leaf.lastIndex(of: "."), dot != leaf.startIndex else { return ".mp3" }
        let ext = leaf[leaf.index(after: dot)...]
        return ext.isEmpty ? ".mp3" : "." + ext
    }
}

/// Music's own words.
///
/// Android's are literals at their call sites. They are gathered here for the
/// reason ``ShellStrings`` is: one place to reword, and `grep` finds every
/// use.
public enum MusicStrings {
    public static let title = "Music"
    public static let unnamedTrack = "track"
    public static let unknownArtist = "unknown artist"

    /// `"5,232 tracks · 2 rooms playing"` — the page header's second line
    /// (`MusicScreen.kt:382`), with the em dash it does not have: Android
    /// uses `·`, and so does this.
    ///
    /// The track count is `—` while the stats read has not answered, rather
    /// than `0`: a library of unknown size is not an empty library, and this
    /// is the line a person reads while the first request is in flight.
    public static func header(tracks: Int?, roomsPlaying: Int) -> String {
        let count = tracks.map { "\($0)" } ?? "—"
        let rooms = roomsPlaying == 1 ? "1 room playing" : "\(roomsPlaying) rooms playing"
        return "\(count) tracks · \(rooms)"
    }

    /// `LibraryTab.kt:147`, with its ellipsis character.
    public static let searchPlaceholder = "search title, artist, album, path…"
    public static let favoritesOnly = "favorites only"
    public static let sourceLabel = "source"
    public static let sortLabel = "sort"
    public static let allSources = "all"

    // The empty states, which are Android's three narrowed cases plus one of
    // this app's own. `LibraryTab.kt:91-95` says "no tracks match" for a
    // search, "no favorited tracks yet" for the switch and "no X tracks" for
    // a source filter — three different true sentences rather than one vague
    // one — and this app adds the fourth, for a library that really is empty,
    // and keeps ``noTracks`` for a library whose size it does not know yet.
    public static let noTracksForSearch = "no tracks match"
    public static let noFavoritesYet = "no favorited tracks yet"
    public static func noTracksFromSource(_ source: String) -> String {
        "no \(source) tracks"
    }
    public static let noTracks = "no tracks here"
    public static let libraryEmpty = "the library is empty — add music on the dashboard"

    public static let retry = "try again"
    public static let loadMore = "load more"
    public static let nothingPlaying = "nothing playing"

    /// `TrackDrawer.kt:176` and `:192`.
    public static let playInRoom = "play in room"
    public static func playIn(room: String) -> String { "play in \(room)" }

    /// `LibraryTab.kt:359`'s pair, which is the control's label **and** says
    /// what it will do rather than what the row is.
    public static let favorite = "favorite"
    public static let unfavorite = "unfavorite"

    public static func playingIn(room: String) -> String { "playing in \(room)" }
    public static func pausedIn(room: String) -> String { "paused in \(room)" }
    public static func playFailed(_ reason: String) -> String { "play failed: \(reason)" }
    public static func favoriteFailed(_ reason: String) -> String { "favorite failed: \(reason)" }

    /// `TrackDrawer.kt:90`, ellipsis included: the request has gone and the
    /// room has not answered yet, which is what the three dots say.
    public static func queuedInRoom(_ title: String, room: String) -> String {
        "playing \"\(title)\" in \(room)…"
    }
}
