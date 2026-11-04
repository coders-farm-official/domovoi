import Foundation

/// One entry in the phone's own play queue.
///
/// `android/.../player/PlayItem.kt:1-109`, ported field for field. The one
/// queue holds library tracks, live radio, podcast episodes, audiobooks and
/// songs that live on the phone, exactly as the web dashboard's `player.jsx`
/// item shape does — so every transport control, the cast planner and the
/// position saves have one type to read.
///
/// `src` and `coverPath` are **server-relative paths** for everything that
/// came from a Domovoi (`/api/music/library/12/audio`); a phone song carries
/// a full file URL instead, which is why ``PlayerArtwork/model(coverPath:absolute:)``
/// and the engine both check for a leading `/` rather than prefixing blindly
/// (`CoverArt.kt:30` — prefixing a `content://` URI with the server turned a
/// phone song's art into a dead URL on Android).
public enum PlayerKind: String, Equatable, Sendable, Codable, CaseIterable {
    case library, radio, podcast, audiobook, device
}

/// One chapter mark: `PlayItem.kt:8`.
public struct PlayerChapter: Equatable, Sendable, Codable {
    public let title: String
    public let startSec: Double

    public init(title: String, startSec: Double) {
        self.title = title
        self.startSec = startSec
    }

    private enum CodingKeys: String, CodingKey {
        case title
        case startSec = "start_sec"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        startSec = try values.decodeIfPresent(Double.self, forKey: .startSec) ?? 0
    }
}

/// One file of a book that is several — a folder book, one file per chapter.
///
/// `PlayItem.kt:16`. Android joins the parts into one media source so the book
/// has one position; this port maps the timeline instead
/// (``PlayerBookTimeline``, and the reason is in
/// `design-notes/ios/overnight/player/DECISIONS.md`), so a part whose
/// `durationSec` is nil is the thing that makes a whole-book seek bar
/// impossible rather than merely imprecise.
public struct PlayerMediaPart: Equatable, Sendable {
    public let src: String
    public let durationSec: Double?

    public init(src: String, durationSec: Double? = nil) {
        self.src = src
        self.durationSec = durationSec
    }
}

/// `PlayItem.kt:23-43`.
public struct PlayerItem: Equatable, Sendable, Identifiable {
    /// Stable within a queue: `"lib-12"`, `"pod-9"`, `"book-3"`. The engine
    /// is keyed by it, and two copies of one track in a queue share it — the
    /// model's `index` is what tells those apart, as Android's `mediaId` does.
    public let uid: String
    public let kind: PlayerKind
    /// The track / station / episode / book id.
    public let itemID: Int64
    public let title: String
    public let artist: String?
    public let album: String?
    /// e.g. `/api/music/library/12/audio`.
    public let src: String
    /// e.g. `/api/music/library/12/cover`.
    public let coverPath: String?
    public let durationSec: Double?
    /// False for a live stream: the seek is refused in the model and the
    /// lock-screen scrubber is disabled (MAP §5.5).
    public let seekable: Bool
    public let chapters: [PlayerChapter]
    /// A book of several files, in order; `src` is the first. Kept only when
    /// there is more than one (`PlayItem.kt:105`).
    public let parts: [PlayerMediaPart]
    /// A book saved on this phone: its folder. Its files belong to no server.
    public let savedKey: String?
    /// The server a saved book came from: where its place goes.
    public let origin: PlayerServerScope?

    public var id: String { uid }

    public init(
        uid: String,
        kind: PlayerKind,
        itemID: Int64,
        title: String,
        artist: String? = nil,
        album: String? = nil,
        src: String,
        coverPath: String? = nil,
        durationSec: Double? = nil,
        seekable: Bool = true,
        chapters: [PlayerChapter] = [],
        parts: [PlayerMediaPart] = [],
        savedKey: String? = nil,
        origin: PlayerServerScope? = nil
    ) {
        self.uid = uid
        self.kind = kind
        self.itemID = itemID
        self.title = title
        self.artist = artist
        self.album = album
        self.src = src
        self.coverPath = coverPath
        self.durationSec = durationSec
        self.seekable = seekable
        self.chapters = chapters
        self.parts = parts
        self.savedKey = savedKey
        self.origin = origin
    }

    /// Podcast or audiobook — the flag that decides whether a position is
    /// saved at all (`PlayItem.kt:108`).
    public var isSpoken: Bool { kind == .podcast || kind == .audiobook }

    // MARK: - the five factories, each pinning a path

    /// `PlayItem.kt:45`.
    public static func fromTrack(
        id: Int64,
        title: String,
        artist: String? = nil,
        album: String? = nil,
        durationSec: Double? = nil
    ) -> PlayerItem {
        PlayerItem(
            uid: "lib-\(id)", kind: .library, itemID: id,
            title: title, artist: artist, album: album,
            src: "/api/music/library/\(id)/audio",
            coverPath: PlayerArtwork.libraryPath(trackID: id),
            durationSec: durationSec
        )
    }

    /// `PlayItem.kt:57`. The artist line is the literal `"live radio"`, and
    /// `seekable` is false: a live stream has no place to seek to.
    ///
    /// Only reachable when the `stations` capability is present — the radio
    /// plugin's own router serves the stream.
    public static func fromStation(id: Int64, name: String) -> PlayerItem {
        PlayerItem(
            uid: "radio-\(id)", kind: .radio, itemID: id,
            title: name, artist: PlayerStrings.liveRadio,
            src: "/api/plugins/radio/stations/\(id)/stream",
            seekable: false
        )
    }

    /// `PlayItem.kt:68`. `artwork` goes through ``PlayerArtwork/podcastPath(_:)``
    /// so a publisher's URL is dropped and the phone never contacts one.
    public static func fromEpisode(
        id: Int64,
        title: String,
        show: String? = nil,
        durationSec: Double? = nil,
        artwork: String? = nil,
        chapters: [PlayerChapter] = []
    ) -> PlayerItem {
        PlayerItem(
            uid: "pod-\(id)", kind: .podcast, itemID: id,
            title: title, artist: show,
            src: "/api/podcasts/episodes/\(id)/audio",
            coverPath: PlayerArtwork.podcastPath(artwork),
            durationSec: durationSec,
            chapters: chapters
        )
    }

    /// `PlayItem.kt:79`. A song that lives on the phone: `uri` is played
    /// directly, with no server in it.
    ///
    /// **Nothing in this item ever calls this.** It is here because
    /// ``PlayerCastPlanner``'s refusals count phone songs and those sentences
    /// are ported word for word; the PhoneMedia item is what reads the phone's
    /// own library (MAP §5.2).
    public static func fromDeviceAudio(
        id: Int64,
        title: String,
        artist: String? = nil,
        album: String? = nil,
        durationSec: Double? = nil,
        uri: String,
        artURI: String? = nil
    ) -> PlayerItem {
        PlayerItem(
            uid: "dev-\(id)", kind: .device, itemID: id,
            title: title, artist: artist, album: album,
            src: uri, coverPath: artURI,
            durationSec: durationSec
        )
    }

    /// `PlayItem.kt:88-105`. A folder book passes its `parts`
    /// (`/api/audiobooks/{id}/audio?file=…` each); `parts` is kept only when
    /// there is more than one, so a single-file book is an ordinary item.
    public static func fromBook(
        id: Int64,
        title: String,
        author: String? = nil,
        durationSec: Double? = nil,
        artwork: String? = nil,
        chapters: [PlayerChapter] = [],
        parts: [PlayerMediaPart] = [],
        savedKey: String? = nil,
        origin: PlayerServerScope? = nil
    ) -> PlayerItem {
        PlayerItem(
            uid: "book-\(id)", kind: .audiobook, itemID: id,
            title: title, artist: author,
            src: parts.first?.src ?? "/api/audiobooks/\(id)/audio",
            coverPath: artwork,
            durationSec: durationSec,
            chapters: chapters,
            parts: parts.count > 1 ? parts : [],
            savedKey: savedKey,
            origin: origin
        )
    }
}

/// Where the transport controls are pointed: this phone, or a satellite room.
///
/// `PlayerController.kt:47-50` (`PlayTarget`).
public enum PlayerTarget: Equatable, Sendable {
    case thisPhone
    case room(String)

    /// The room id while casting, nil on this phone.
    public var roomID: String? {
        if case .room(let id) = self { return id }
        return nil
    }

    public var isCasting: Bool { roomID != nil }
}

/// One reading of a room's now-playing row, with **this phone's** clock at
/// the moment it landed.
///
/// `PlayerController.kt:53-68` (`RemoteNowPlaying`). The receipt clock is the
/// field that matters: a room's lyrics are followed from when the phone saw
/// the reading, never from the server's clock (``PlayerLyricsMath``,
/// `Lyrics.kt:150-158`), because the two clocks are not the same clock.
///
/// Kept separate from ``NowPlayingRoom`` — which is the server's wire shape
/// and decodes a whole array of rooms — so that the cast planner and the
/// lyrics math stay pure over four fields and need no decoder at all.
public struct PlayerRoomReading: Equatable, Sendable {
    public let roomID: String
    /// MPD's word: `play`, `pause`, `stop`.
    public let state: String
    public let title: String?
    public let artist: String?
    public let elapsedSec: Double
    public let durationSec: Double?
    /// The library track the room plays (now-playing's `track_id`); nil for a
    /// stream or a file that is not in the library.
    public let trackID: Int64?
    /// This phone's monotonic clock in milliseconds when the reading came in.
    public let readAtMs: Int64

    public init(
        roomID: String,
        state: String = "stop",
        title: String? = nil,
        artist: String? = nil,
        elapsedSec: Double = 0,
        durationSec: Double? = nil,
        trackID: Int64? = nil,
        readAtMs: Int64 = 0
    ) {
        self.roomID = roomID
        self.state = state
        self.title = title
        self.artist = artist
        self.elapsedSec = elapsedSec
        self.durationSec = durationSec
        self.trackID = trackID
        self.readAtMs = readAtMs
    }

    /// From the server's own row (`music.py:840-864`), with the phone's clock
    /// stamped on it here — the one place the stamp can honestly be taken.
    public init(_ row: NowPlayingRoom, readAtMs: Int64) {
        self.init(
            roomID: row.roomID,
            state: row.state ?? "stop",
            title: row.song?.title,
            artist: row.song?.artist,
            elapsedSec: row.elapsedSec ?? 0,
            durationSec: row.song?.durationSec,
            trackID: row.trackID,
            readAtMs: readAtMs
        )
    }

    public var isPlaying: Bool { state == "play" }
}
