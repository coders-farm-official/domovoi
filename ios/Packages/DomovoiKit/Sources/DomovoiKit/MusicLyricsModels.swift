import Foundation

/*
 * Lyrics for what is playing — `android/.../player/Lyrics.kt`'s shapes, and
 * `web/backend/api/lyrics.py`'s wire format.
 *
 * The server does all of the LRC work: `.lrc` sidecars, the songs' own tags
 * and LRCLIB are read, parsed and stored there, the file's `[offset:]` is
 * already applied, and a doc arrives as time-ordered `{t, text}` lines in
 * milliseconds — an empty text marks a gap — plus the plain text. What is
 * left for a client is to keep that order, follow a position through it
 * (``MusicLyricsMath``) and show the right state (``MusicLyricsState``).
 *
 * **A lyric is never logged.** No print, no error message built out of one,
 * nothing in a notification and nothing in a failure's text — the routes are
 * device-tier and `Cache-Control: no-store` for the same reason
 * (`lyrics.py:17-25`): this is the household's own copy of copyrighted text.
 * That is why ``MusicLyricsResult/failed`` carries no message.
 */

/// One timed line: ``t`` milliseconds from the start, the file's offset
/// already applied. An empty ``text`` is an instrumental gap
/// (`Lyrics.kt:47`).
public struct MusicLyricLine: Equatable, Sendable, Decodable {
    public let t: Int64
    public let text: String

    public init(t: Int64 = 0, text: String = "") {
        self.t = t
        self.text = text
    }

    private enum CodingKeys: String, CodingKey { case t, text }

    /// Hand-written, like every model in ``MusicModels``: the synthesised
    /// decoder throws on a missing key and `= 0` in the declaration does not
    /// make it optional to it.
    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        t = try values.decodeIfPresent(Int64.self, forKey: .t) ?? 0
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }

    /// A gap: nothing to read out, so a surface describes it rather than
    /// showing an empty row (`LyricsPanel.kt:124`).
    public var isGap: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    /// The same line at a different time — ``MusicLyricsDoc/timedLines()``
    /// clamps a negative `t`, and every field is a `let`.
    func at(_ time: Int64) -> MusicLyricLine { MusicLyricLine(t: time, text: text) }
}

/// `GET /api/music/library/{track_id}/lyrics` (`lyrics.py:323`).
///
/// `updated_at` is on the wire and is **not decoded**: Android does not read
/// it either, and a field nothing shows is a field that goes stale silently.
public struct MusicLyricsDoc: Equatable, Sendable, Decodable {
    public let trackID: Int64
    /// `synced` | `plain` | `instrumental` | `none`. An **open** vocabulary as
    /// far as this app is concerned: an unknown word falls through to
    /// ``MusicLyricsState/noLyrics`` rather than being drawn as something it
    /// is not.
    public let status: String
    /// Domovoi is still looking for this song's words.
    public let checking: Bool
    /// `sidecar` | `embedded` | `lrclib`.
    public let source: String?
    public let sourceLabel: String?
    public let lines: [MusicLyricLine]?
    public let text: String?

    public init(
        trackID: Int64 = 0,
        status: String = "none",
        checking: Bool = false,
        source: String? = nil,
        sourceLabel: String? = nil,
        lines: [MusicLyricLine]? = nil,
        text: String? = nil
    ) {
        self.trackID = trackID
        self.status = status
        self.checking = checking
        self.source = source
        self.sourceLabel = sourceLabel
        self.lines = lines
        self.text = text
    }

    private enum CodingKeys: String, CodingKey {
        case status, checking, source, lines, text
        case trackID = "track_id"
        case sourceLabel = "source_label"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        trackID = try values.decodeIfPresent(Int64.self, forKey: .trackID) ?? 0
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "none"
        checking = try values.decodeIfPresent(Bool.self, forKey: .checking) ?? false
        source = try values.decodeIfPresent(String.self, forKey: .source)
        sourceLabel = try values.decodeIfPresent(String.self, forKey: .sourceLabel)
        lines = try values.decodeIfPresent([MusicLyricLine].self, forKey: .lines)
        text = try values.decodeIfPresent(String.self, forKey: .text)
    }

    /// The timed lines the way a surface uses them (`Lyrics.kt:65`).
    ///
    /// Time order — the server sends it, and it is kept **stable** here so
    /// equal times stay in file order, which is what a chorus repeated on one
    /// timestamp depends on. No negative times, at most
    /// ``MusicLyricsMath/maxLines``. Empty unless the doc is `synced` **and**
    /// at least one line has words: a synced doc of nothing but gaps is not
    /// something to scroll through, and ``state()`` falls back to its plain
    /// text.
    public func timedLines() -> [MusicLyricLine] {
        guard status == "synced" else { return [] }
        let raw = lines ?? []
        guard raw.contains(where: { !$0.isGap }) else { return [] }
        let clean = raw.prefix(MusicLyricsMath.maxLines).map { $0.t < 0 ? $0.at(0) : $0 }
        // Already ordered is the common case and the server's promise; the
        // sort is the fallback. `enumerated` keeps it stable: Swift's `sort`
        // is not, so equal times would otherwise come back in an order that
        // depends on the input's length.
        guard
            zip(clean, clean.dropFirst()).contains(where: { $0.t > $1.t })
        else { return Array(clean) }
        return clean.enumerated()
            .sorted { ($0.element.t, $0.offset) < ($1.element.t, $1.offset) }
            .map(\.element)
    }

    /// What a lyrics surface shows for this doc — `Lyrics.kt:77`'s `when`, in
    /// its order, and **the order is the whole of it**.
    ///
    /// The branch that breaks silently if the order is collapsed is the
    /// second one: a doc the server called `synced` whose lines turn out to be
    /// unusable still has its plain text, and showing "no lyrics" over a
    /// song whose words arrived is the bug `Lyrics.kt:83` has a comment for.
    public func state() -> MusicLyricsState {
        let timed = timedLines()
        if !timed.isEmpty { return .timed(timed) }
        let plain = text.flatMap { body -> String? in
            body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : body
        }
        if status == "synced" || status == "plain", let plain { return .plain(plain) }
        if status == "instrumental" { return .instrumental }
        if checking { return .looking }
        return .noLyrics
    }
}

/// The states a lyrics surface can be in once a doc is here (`Lyrics.kt:95`,
/// the web's `LyricsView`).
///
/// Named ``MusicLyricsState`` and not `LyricsView`: twenty branches are adding
/// files to this module tonight and `LyricsView` is both Android's name and
/// the name a SwiftUI view of this would take, so it is the one most likely to
/// be declared twice (NIGHT-RULES §2).
public enum MusicLyricsState: Equatable, Sendable {
    case timed([MusicLyricLine])
    case plain(String)
    case instrumental
    /// No lyrics yet, and Domovoi is still looking (the doc's `checking`).
    case looking
    case noLyrics
}

/// `GET /api/music/now-playing/{room_id}/lyrics` (`lyrics.py:339`): what a
/// room plays, where it has got to, and that song's lyrics in one call.
///
/// ``receivedAtMs`` is **this phone's** monotonic clock when the answer
/// landed, and the room's position is anchored on it. The server does send a
/// `read_at`, and `schemas.py:1151` says in words that it is informational —
/// "a client anchors its own interpolation on when IT received the answer" —
/// so it is not decoded here at all. A clock that is not this phone's cannot
/// be subtracted from this phone's `now`.
public struct MusicRoomLyrics: Equatable, Sendable, Decodable {
    public let roomID: String
    public let state: String
    public let trackID: Int64?
    public let elapsedSec: Double?
    /// `int` on the wire, `Double?` in Android's model — decoded leniently as
    /// a `Double` so neither shape is a decode failure.
    public let durationSec: Double?
    public let lineIndex: Int?
    public let lyrics: MusicLyricsDoc?

    /// Set by ``MusicLyricsStore/room(_:)``, never by the decoder.
    public var receivedAtMs: Int64 = 0

    public init(
        roomID: String = "",
        state: String = "stop",
        trackID: Int64? = nil,
        elapsedSec: Double? = nil,
        durationSec: Double? = nil,
        lineIndex: Int? = nil,
        lyrics: MusicLyricsDoc? = nil,
        receivedAtMs: Int64 = 0
    ) {
        self.roomID = roomID
        self.state = state
        self.trackID = trackID
        self.elapsedSec = elapsedSec
        self.durationSec = durationSec
        self.lineIndex = lineIndex
        self.lyrics = lyrics
        self.receivedAtMs = receivedAtMs
    }

    private enum CodingKeys: String, CodingKey {
        case state, lyrics
        case roomID = "room_id"
        case trackID = "track_id"
        case elapsedSec = "elapsed_sec"
        case durationSec = "duration_sec"
        case lineIndex = "line_index"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? "stop"
        trackID = try values.decodeIfPresent(Int64.self, forKey: .trackID)
        elapsedSec = try values.decodeIfPresent(Double.self, forKey: .elapsedSec)
        durationSec = try values.decodeIfPresent(Double.self, forKey: .durationSec)
        lineIndex = try values.decodeIfPresent(Int.self, forKey: .lineIndex)
        lyrics = try values.decodeIfPresent(MusicLyricsDoc.self, forKey: .lyrics)
        receivedAtMs = 0
    }

    public var isPlaying: Bool { state == "play" }

    /// The same reading stamped with when it arrived here.
    public func received(at ms: Int64) -> MusicRoomLyrics {
        var copy = self
        copy.receivedAtMs = ms
        return copy
    }
}

/// Where a room was when a reading of it came in; its lyrics run on from here
/// (`LyricsPanel.kt:153`'s `LyricsAnchor`).
public struct MusicLyricsAnchor: Equatable, Sendable {
    public let positionSec: Double
    /// This phone's monotonic clock at ``MusicRoomLyrics/receivedAtMs``.
    public let atMs: Int64
    public let playing: Bool
    public let durationSec: Double?

    public init(positionSec: Double, atMs: Int64, playing: Bool, durationSec: Double?) {
        self.positionSec = positionSec
        self.atMs = atMs
        self.playing = playing
        self.durationSec = durationSec
    }

    /// The anchor a room reading carries.
    public init(_ reading: MusicRoomLyrics) {
        self.init(
            positionSec: reading.elapsedSec ?? 0,
            atMs: reading.receivedAtMs,
            playing: reading.isPlaying,
            durationSec: reading.durationSec
        )
    }
}
