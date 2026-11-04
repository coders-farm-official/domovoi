import Foundation

/// What can be in this phone's own play queue — `android/.../player/PlayItem.kt:5`'s
/// `PlayKind`.
///
/// All five, including the one this app cannot have: ``device`` is a song in
/// the phone's own storage, and this app has no on-device music list (it is
/// the `phonemedia` item's). The case is here anyway because
/// ``MusicCastPlanner`` counts it — its arithmetic has to stay honest about a
/// queue entry it cannot send, and the refusal wording for it is the one
/// sentence a person most needs.
public enum MusicPlayKind: String, CaseIterable, Sendable {
    case library
    case radio
    case podcast
    case audiobook
    case device

    /// Whether a room could play this at all. Only ``library``: a room plays
    /// from the domovoi's own library, by track id
    /// (`CastPlan.kt:12-25`).
    public var castable: Bool { self == .library }
}

/// One chapter of a long item (`PlayItem.kt:8`).
public struct MusicChapter: Equatable, Sendable {
    public let title: String
    public let startSec: Double

    public init(title: String, startSec: Double) {
        self.title = title
        self.startSec = startSec
    }
}

/// One entry in this phone's queue — `PlayItem.kt:23`, narrowed to what an
/// iPhone's player and ``MusicCastPlanner`` actually read.
///
/// Left out of the port, each because nothing on this side can use it:
/// `parts` and `savedKey` (a folder audiobook saved on the phone — the
/// audiobooks item's), and `origin` (which server a saved book came from).
public struct MusicPlayerItem: Equatable, Sendable, Identifiable {
    /// Unique within the queue — a track can be queued twice, so the id is
    /// not enough (`PlayerTab.kt:157`).
    public let uid: String
    public let kind: MusicPlayKind
    /// Track, station, episode or book id.
    public let trackID: Int64
    public let title: String
    public let artist: String?
    public let album: String?
    /// Server-relative, e.g. `/api/music/library/12/audio`.
    public let src: String
    public let durationSec: Double?
    public let seekable: Bool
    public let chapters: [MusicChapter]

    public init(
        uid: String,
        kind: MusicPlayKind,
        trackID: Int64,
        title: String,
        artist: String? = nil,
        album: String? = nil,
        src: String,
        durationSec: Double? = nil,
        seekable: Bool = true,
        chapters: [MusicChapter] = []
    ) {
        self.uid = uid
        self.kind = kind
        self.trackID = trackID
        self.title = title
        self.artist = artist
        self.album = album
        self.src = src
        self.durationSec = durationSec
        self.seekable = seekable
        self.chapters = chapters
    }

    public var id: String { uid }

    /// One library track, queued — `PlayItem.fromTrack` (`PlayItem.kt:45`).
    public static func library(_ track: LibraryTrack) -> MusicPlayerItem {
        MusicPlayerItem(
            uid: "lib-\(track.id)",
            kind: .library,
            trackID: track.id,
            title: track.displayTitle,
            artist: track.artist,
            album: track.album,
            src: "/api/music/library/\(track.id)/audio",
            durationSec: track.durationSec
        )
    }

    /// The cover this entry's art is read from, or nil for one that has none
    /// this app can fetch.
    ///
    /// **Library only.** `PlayItem.coverPath` also carries a podcast's stored
    /// artwork and a `content://` URI for a phone song; the first is the
    /// podcasts item's route and the second is art for a song this app has no
    /// list of (``MusicCoverStore``'s doc comment says why neither is here).
    public var coverTrackID: Int64? { kind == .library ? trackID : nil }
}

/// Where transport controls are pointed (`PlayerController.kt:48`).
public enum MusicPlayTarget: Equatable, Sendable {
    case local
    case room(String)

    public var roomID: String? {
        if case .room(let id) = self { return id }
        return nil
    }
}

/// What a room being cast to last reported (`PlayerController.kt:53`).
///
/// ``readAtMs`` is **this phone's** monotonic clock when the reading came in,
/// for ``MusicLyricsMath/roomPositionMs(elapsedSec:readAtMs:nowMs:playing:durationSec:nudgeMs:)``'s
/// reason: the server's clock cannot be subtracted from this phone's `now`.
public struct MusicRemoteNowPlaying: Equatable, Sendable {
    public let roomID: String
    public let state: String
    public let title: String?
    public let artist: String?
    public let elapsedSec: Double
    public let durationSec: Double?
    /// The library track the room plays; nil for a stream or a file outside
    /// the library.
    public let trackID: Int64?
    public let readAtMs: Int64

    public init(
        roomID: String,
        state: String,
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

    public var isPlaying: Bool { state == "play" }
}

/// The audio engine this phone's player tab sits on, as a protocol — **and
/// nothing on this branch conforms to it.**
///
/// `PlayerTab.kt` is bound to `PlayerController`'s state flows. The engine
/// itself is a different overnight item ("player"), its branch is not merged
/// here, and its notes folder was empty when this was written because it was
/// running in parallel. So the shape here is: the fourteen members
/// `PlayerTab.kt`'s own table needs, everything *pure* behind it ported and
/// package-tested (``MusicCastPlanner``, ``MusicPlayerMath``,
/// ``MusicLocalQueueModel``), and **the tab drawn only when an engine is
/// registered** — with none, `player` is not in the tab bar at all: not
/// greyed, not empty.
///
/// A transport row over no engine is the control
/// [[no-misleading-controls]] forbids, and that is the whole reason this is a
/// protocol rather than a half-built player. When the Player item's engine
/// lands it is one conformance and nothing else moves.
///
/// Members this deliberately does not have: anything about on-device music
/// (``MusicPlayKind/device``), and anything about saving a track to the phone
/// — both other items'.
@MainActor
public protocol MusicMorePlayerEngine: AnyObject {

    // ---- what is queued --------------------------------------------------

    var queue: [MusicPlayerItem] { get }
    /// Which entry is current. May be out of range for an empty queue; every
    /// reader clamps.
    var index: Int { get }
    var target: MusicPlayTarget { get }
    /// What the room being cast to last said, or nil.
    var remote: MusicRemoteNowPlaying? { get }

    // ---- where it is -----------------------------------------------------

    var positionSec: Double { get }
    var isPlaying: Bool { get }
    /// Playback rate: 1.0 is normal. Local only — a room plays at its own.
    var speed: Double { get }
    /// Seconds left on the sleep timer, or nil when none is set.
    var sleepRemainingSec: Int? { get }

    // ---- what a control does ---------------------------------------------

    func toggle()
    func previous()
    func next()
    func stop()
    func seek(toSec seconds: Double)
    func jump(to index: Int)
    func move(from index: Int, to destination: Int)
    func remove(at index: Int)
    func clearQueue()
    func setSpeed(_ speed: Double)
    func setSleep(minutes: Int)
    func setSleepEndOfTrack()
    func cancelSleep()
    /// Hand the queue to a room from ``index`` on. The engine owns the
    /// request, because it is also what moves this phone's own target.
    func cast(to room: String) async -> MusicCastOutcome
    /// Start the room this queue is already cast to on `index` instead.
    func castFrom(_ index: Int) async -> MusicCastOutcome
    /// Come back to this phone.
    func playHere() async -> MusicCastOutcome
}

/// The pure arithmetic the player tab needs, with no engine in sight.
///
/// Separated out for the reason `PlayerTab.kt:94`'s `PlayerTabModel` is
/// hoisted out of its lazy items: a position tick must not rebuild a row, and
/// a function of its arguments is the only shape a test can state.
public enum MusicPlayerMath {

    /// The chapter playing at `positionSec`: the last one whose start has
    /// passed, and **the first one before any has** — not `-1`
    /// (`PlayerTab.kt:151`). A book opened at nought is in chapter one.
    public static func currentChapterIndex(
        _ chapters: [MusicChapter],
        positionSec: Double
    ) -> Int {
        var current = 0
        for (index, chapter) in chapters.enumerated() where positionSec >= chapter.startSec {
            current = index
        }
        return current
    }

    /// A queue entry's lyrics track: **library tracks only**
    /// (`PlayerTab.kt:147`, `[D10]`). Radio, podcasts, audiobooks and songs on
    /// the phone have no library id, so no lyrics document exists for them and
    /// the panel is not drawn.
    public static func lyricsTrackID(_ item: MusicPlayerItem?) -> Int64? {
        guard let item, item.kind == .library else { return nil }
        return item.trackID
    }

    /// The lazy key for one queue row. It carries the **position** as well as
    /// the uid, because a track can be queued twice (`PlayerTab.kt:157`).
    public static func queueKey(index: Int, item: MusicPlayerItem) -> String {
        "player-q-\(index)-\(item.uid)"
    }

    public static func chapterKey(index: Int) -> String { "player-chapter-\(index)" }

    /// The speed chips, in the order they are drawn (`PlayerTab.kt:477`).
    public static let speeds: [Double] = [0.75, 1, 1.25, 1.5, 2]

    /// The sleep chips, in minutes (`PlayerTab.kt:505`).
    public static let sleepMinutes = [15, 30, 45, 60]

    /// Which speed chip is lit. By tolerance, not equality: the engine's own
    /// rate is a float that has been through an `AVAudioEngine` and back, and
    /// `0.75 == 0.75` is not something to bet a highlight on
    /// (`PlayerTab.kt:479`'s `abs(speed - v) < 0.01f`).
    public static func isSpeedSelected(_ chip: Double, speed: Double) -> Bool {
        abs(speed - chip) < 0.01
    }
}
