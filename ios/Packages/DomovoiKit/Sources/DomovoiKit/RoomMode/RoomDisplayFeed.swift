import Foundation

// What the room display shows beyond the room's own status: this room's
// music, its timers and alarms, and a timer going off right now
// (`roommode/RoomDisplayFeed.kt`). All of it comes from the server, over the
// room's own socket. Until a core sends it the feed is nil and the display
// hides those sections: nothing is guessed, nothing is a placeholder.
//
// Why the socket and not the household app's routes: a room phone holds only
// the room's pairing key, which the core accepts on `/v1/stream/{room}` and
// nowhere else (and room mode seals the household token, ``HouseholdLock``).

/// `now_playing.state`.
public enum RoomPlayState: String, Equatable, Sendable {
    case play, pause
}

/// The transport verbs, named as the core's `/v1/admin/music/{action}`
/// (Android's `Transport`).
public enum RoomDisplayTransport: String, Equatable, Sendable, CaseIterable {
    case pause, resume, skip, previous

    public var wire: String { rawValue }
}

public enum RoomTimerKind: String, Equatable, Sendable {
    case timer, alarm
}

/// What kind of thing a room is playing (`now_playing.source`; Android's
/// `MediaSource`).
public enum RoomMediaSource: String, Equatable, Sendable, CaseIterable {
    case library, radio, sleep, podcast, audiobook, other

    public var wire: String { rawValue }

    /// A value this app does not know yet is "other"; none at all is nil.
    public static func of(_ wire: String?) -> RoomMediaSource? {
        guard let wire else { return nil }
        return RoomMediaSource(rawValue: wire) ?? .other
    }
}

public struct RoomFeedNowPlaying: Equatable, Sendable {
    public var state: RoomPlayState
    public var title: String?
    public var artist: String?
    public var album: String?
    public var source: RoomMediaSource?
    /// A path on the room's server for the cover (`/v1/music/cover/{id}`,
    /// library tracks only); nil: none, and no artwork tile is drawn.
    public var art: String?
    public var durationSec: Double?
    public var elapsedSec: Double?
    /// What `room_control` will do in this room right now.
    public var controls: Set<RoomDisplayTransport>

    public init(
        state: RoomPlayState,
        title: String?,
        artist: String?,
        album: String?,
        source: RoomMediaSource?,
        art: String?,
        durationSec: Double?,
        elapsedSec: Double?,
        controls: Set<RoomDisplayTransport>
    ) {
        self.state = state
        self.title = title
        self.artist = artist
        self.album = album
        self.source = source
        self.art = art
        self.durationSec = durationSec
        self.elapsedSec = elapsedSec
        self.controls = controls
    }
}

public struct RoomFeedTimer: Equatable, Sendable {
    public var id: Int64
    public var kind: RoomTimerKind
    public var label: String?
    /// Left when the frame was sent.
    public var remainingMS: Int64
    /// The whole length, when known.
    public var totalMS: Int64?

    public init(id: Int64, kind: RoomTimerKind, label: String?, remainingMS: Int64, totalMS: Int64?) {
        self.id = id
        self.kind = kind
        self.label = label
        self.remainingMS = remainingMS
        self.totalMS = totalMS
    }
}

public struct RoomFeedRinging: Equatable, Sendable {
    public var fireID: Int64
    public var kind: RoomTimerKind
    public var label: String?

    public init(fireID: Int64, kind: RoomTimerKind, label: String?) {
        self.fireID = fireID
        self.kind = kind
        self.label = label
    }
}

/// A photo or a video result's kind (Android's `MediaKind`).
public enum RoomMediaKind: String, Equatable, Sendable {
    case image, video
}

/// One photo or video result on the room's screen (`media.items`; Android's
/// `FeedMediaItem`).
public struct RoomFeedMediaItem: Equatable, Sendable {
    public var kind: RoomMediaKind
    /// Its thumbnail on the room's server (`/v1/room-media/{token}`, good for
    /// 10 minutes, no credential: ``RoomDisplayFeed/isMediaThumbPath(_:)``);
    /// nil: none, a blank tile.
    public var thumb: String?
    /// What it MIGHT show (a picture model's guess, untrusted): drawn as plain
    /// text, "Might show: …"; nil: none.
    public var caption: String?
    /// When it was taken, `YYYY-MM-DD`; nil: unknown.
    public var date: String?
    /// A video's best moment, seconds; nil: none (and never for a photo).
    public var atS: Double?

    public init(kind: RoomMediaKind, thumb: String?, caption: String?, date: String?, atS: Double?) {
        self.kind = kind
        self.thumb = thumb
        self.caption = caption
        self.date = date
        self.atS = atS
    }
}

/// Photo and video results asked for by voice in this room (1.5.0, the
/// `media` part of `room_display`, domovoi/room_media.py; Android's
/// `FeedMedia`): shown for ``expiresInMS`` from when the frame arrived,
/// replaced by newer results (a new ``id``), gone with a frame whose `media`
/// is null.
public struct RoomFeedMedia: Equatable, Sendable {
    public var id: Int64
    /// The spoken answer ("I found 12 photos that might show the beach…").
    public var said: String
    public var count: Int
    /// More results than the server sent (they are on the Photos page).
    public var more: Bool
    public var expiresInMS: Int64
    /// At most ``RoomDisplayFeed/maxMediaItems``.
    public var items: [RoomFeedMediaItem]

    public init(id: Int64, said: String, count: Int, more: Bool, expiresInMS: Int64, items: [RoomFeedMediaItem]) {
        self.id = id
        self.said = said
        self.count = count
        self.more = more
        self.expiresInMS = expiresInMS
        self.items = items
    }
}

/// One `room_display` frame, as the room keeps it (Android's `RoomFeed`).
public struct RoomFeed: Equatable, Sendable {
    public var nowPlaying: RoomFeedNowPlaying?
    public var timers: [RoomFeedTimer]
    public var ringing: RoomFeedRinging?
    /// Monotonic ms when it arrived (the engine stamps it, on
    /// ``RoomClock/monotonicMS``): every remaining time counts down from
    /// here, so the phone's wall clock being off never moves a countdown.
    public var receivedAt: Int64
    /// The server's monotonic ms when it sent this frame (`sent_at_ms`): for
    /// ordering only, never shown. Nil: an older shape.
    public var sentAtMS: Int64?
    /// Photo and video results (1.5.0); nil: none, or an older core.
    public var media: RoomFeedMedia?

    public init(
        nowPlaying: RoomFeedNowPlaying?,
        timers: [RoomFeedTimer],
        ringing: RoomFeedRinging?,
        receivedAt: Int64 = 0,
        sentAtMS: Int64? = nil,
        media: RoomFeedMedia? = nil
    ) {
        self.nowPlaying = nowPlaying
        self.timers = timers
        self.ringing = ringing
        self.receivedAt = receivedAt
        self.sentAtMS = sentAtMS
        self.media = media
    }
}

/// What the room asks the server to do (`room_control`).
public enum RoomControl: Equatable, Sendable {
    case music(RoomDisplayTransport)
    case stopRinging(fireID: Int64)
}

/// The room display's half of a running room: the server's latest
/// `room_display`, the controls, the room's volume and its artwork
/// (Android's `RoomDisplaySource`). ``RoomEngine`` is one; the kiosk reads it
/// by a cast, so an engine without it (a test's fake) simply shows no cards.
public protocol RoomDisplaySource: AnyObject, Sendable {
    /// The latest `room_display`, now and on every change; nil: none (an
    /// older core, or no session right now). Finishes when the engine stops.
    func displayUpdates() -> AsyncStream<RoomFeed?>
    /// Asks the server to do `action` in this room. False when it could not
    /// be sent (no session, or a core that does not take it).
    @discardableResult func roomControl(_ action: RoomControl) -> Bool
    /// This room's output level, 0-100 (``RoomGain``).
    var volumeLevel: Int { get }
    /// Sets this room's output level and tells the server (`volume_status`,
    /// the frame a Pi reports its level with). False with no session.
    @discardableResult func setVolumeLevel(_ level: Int) -> Bool
    /// The bytes of an image path from the feed (a cover, or a photo or
    /// video result's thumbnail: ``RoomDisplayFeed/isImagePath(_:)``), from
    /// the room's own server, with no credential; nil when it cannot be had.
    func art(_ path: String) async -> Data?
}

/// The frames, both on `/v1/stream/{room}` (the core's
/// `domovoi/room_display.py`, docs/uml/satellite-protocol.md "The room
/// display"; core 1.4.0). `title`, `artist`, `album`, `duration_sec`,
/// `elapsed_sec`, `total_ms` and every `label` may be null. A timer's label
/// is the bare name ("pasta", the server drops "the"/"my") or null; an
/// alarm's is the handler's ("7 AM alarm") or null. A stream's title is the
/// station's name, with no artist, album or duration and only
/// pause/resume. A music action is taken only if the last frame offered it.
///
/// Server → room, once after `ready` and then on change only (at most about
/// once a second); only to a room whose `ready.features` lists ``ability``:
///
///     {"type":"room_display","sent_at_ms":N,
///      "now_playing": null | {"state":"play"|"pause","title":..,"artist":..,"album":..,
///                             "source":"library"|"radio"|"sleep"|"podcast"|"audiobook"|"other",
///                             "art":"/v1/music/cover/{id}"|null,"duration_sec":N,"elapsed_sec":N,
///                             "controls":["pause","resume","skip","previous"]},
///      "timers": [{"id":N,"kind":"timer"|"alarm","label":..,"remaining_ms":N,"total_ms":N}],
///      "ringing": null | {"fire_id":N,"kind":"timer"|"alarm","label":..},
///      "media": null | {"id":N,"said":..,"count":N,"more":bool,"expires_in_ms":N,   (1.5.0)
///                       "items":[{"kind":"image"|"video","thumb":"/v1/room-media/{token}",
///                                 "caption":..,"date":"YYYY-MM-DD","at_s":N}]}}
///
/// `media` (``RoomFeedMedia``): the results of "show me photos of the beach"
/// said here, at most six, for `expires_in_ms`; null otherwise, and gone with
/// the next frame sent for any other reason. A caption is a picture model's
/// guess: plain text, "Might show: …". `thumb` is fetched from the room's own
/// server with no credential (``isMediaThumbPath(_:)``). Read leniently: a
/// `media` this app cannot use is none, and the rest of the frame applies.
///
/// Between frames the phone moves things on itself: `elapsed_sec` while the
/// state is "play", each `remaining_ms` always (``RoomDisplayModel``). A
/// frame older than the last one applied (`sent_at_ms`) is dropped
/// (``newer(_:_:)``). In a group every member gets the group's `now_playing`
/// and its controls act on the group. Reminders are never sent.
///
/// Room → server, only when `ready.features` lists ``ability``:
///
///     {"type":"room_control","action":"pause"|"resume"|"skip"|"previous"}
///     {"type":"room_control","action":"stop_ringing","fire_id":N}   (a timer or an alarm)
public enum RoomDisplayFeed {
    /// The ability name: the hello's `supports` declares it
    /// (``RoomProtocol/supports(echoCanceller:)``), and the room shows and
    /// sends nothing unless the core's `ready.features` lists it back (a
    /// paired room on a 1.4.0 core or later).
    public static let ability = "room_display"

    /// Longest text kept from the server (a title, a label).
    static let maxText = 200
    static let maxTimers = 20

    public static func parse(_ o: [String: RoomJSON]) -> RoomFeed {
        var timers: [RoomFeedTimer] = []
        if case .array(let items)? = o["timers"] {
            for item in items {
                guard case .object(let t) = item, let parsed = Self.timer(t) else { continue }
                timers.append(parsed)
                if timers.count == maxTimers { break }
            }
        }
        var nowPlaying: RoomFeedNowPlaying?
        if case .object(let np)? = o["now_playing"] { nowPlaying = Self.nowPlaying(np) }
        var ringing: RoomFeedRinging?
        if case .object(let r)? = o["ringing"] { ringing = Self.ringing(r) }
        // Read leniently: a `media` this app cannot use is no media, never a
        // frame that fails.
        var media: RoomFeedMedia?
        if case .object(let m)? = o["media"] { media = Self.media(m) }
        return RoomFeed(
            nowPlaying: nowPlaying,
            timers: timers,
            ringing: ringing,
            sentAtMS: long(o, "sent_at_ms"),
            media: media
        )
    }

    /// Most results the server sends a room (domovoi/room_media.py MAX_ITEMS).
    public static let maxMediaItems = 6
    /// The spoken answer's longest (room_media.py SAID_MAX).
    static let maxSaid = 300
    /// The longest results stay: the server says 90 s; a value past this is
    /// a mistake, and the screen still clears.
    static let maxMediaMS: Int64 = 300_000

    private static func media(_ o: [String: RoomJSON]) -> RoomFeedMedia? {
        guard let id = long(o, "id"), case .array(let raw)? = o["items"] else { return nil }
        var items: [RoomFeedMediaItem] = []
        for entry in raw {
            guard case .object(let i) = entry, let item = mediaItem(i) else { continue }
            items.append(item)
            if items.count == maxMediaItems { break }
        }
        guard !items.isEmpty else { return nil }
        let said = str(o, "said")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        var more = false
        if case .bool(let b)? = o["more"] { more = b }
        let count = long(o, "count").map { WireNumber.clamp($0, to: Int64(items.count)...Int64(Int32.max)) } ?? Int64(items.count)
        return RoomFeedMedia(
            id: id,
            said: String(said.prefix(maxSaid)),
            count: Int(count),
            more: more,
            expiresInMS: WireNumber.clamp(long(o, "expires_in_ms") ?? 90_000, to: 0...maxMediaMS),
            items: items
        )
    }

    private static func mediaItem(_ o: [String: RoomJSON]) -> RoomFeedMediaItem? {
        guard let kind = str(o, "kind").flatMap(RoomMediaKind.init(rawValue:)) else { return nil }
        return RoomFeedMediaItem(
            kind: kind,
            thumb: str(o, "thumb").flatMap { isMediaThumbPath($0) ? $0 : nil },
            caption: text(o, "caption"),
            date: str(o, "date").flatMap { isDate($0) ? $0 : nil },
            atS: num(o, "at_s").flatMap { kind == .video && $0 >= 0 ? min($0, WireNumber.maxMediaSeconds) : nil }
        )
    }

    /// `YYYY-MM-DD`, ASCII digits.
    static func isDate(_ s: String) -> Bool {
        let b = Array(s.utf8)
        guard b.count == 10, b[4] == UInt8(ascii: "-"), b[7] == UInt8(ascii: "-") else { return false }
        return b.enumerated().allSatisfy { i, c in i == 4 || i == 7 || (c >= UInt8(ascii: "0") && c <= UInt8(ascii: "9")) }
    }

    private static func nowPlaying(_ o: [String: RoomJSON]) -> RoomFeedNowPlaying? {
        guard let w = str(o, "state"), let state = RoomPlayState(rawValue: w) else { return nil }
        var controls: Set<RoomDisplayTransport> = []
        if case .array(let items)? = o["controls"] {
            for item in items {
                if case .string(let w) = item, let t = RoomDisplayTransport(rawValue: w) { controls.insert(t) }
            }
        }
        return RoomFeedNowPlaying(
            state: state,
            title: text(o, "title"),
            artist: text(o, "artist"),
            album: text(o, "album"),
            source: RoomMediaSource.of(str(o, "source")),
            art: str(o, "art").flatMap { isArtPath($0) ? $0 : nil },
            durationSec: num(o, "duration_sec").flatMap { $0 > 0 ? min($0, WireNumber.maxMediaSeconds) : nil },
            elapsedSec: num(o, "elapsed_sec").flatMap { $0 >= 0 ? min($0, WireNumber.maxMediaSeconds) : nil },
            controls: controls
        )
    }

    private static func timer(_ o: [String: RoomJSON]) -> RoomFeedTimer? {
        guard let id = long(o, "id"), let remaining = long(o, "remaining_ms"),
              let timerKind = Self.kind(str(o, "kind"))
        else { return nil }
        return RoomFeedTimer(
            id: id,
            kind: timerKind,
            label: text(o, "label"),
            // Held to 0...a year as it is read: the display counts these down
            // every second, and a number past that is a server's mistake that
            // must not reach the arithmetic (security audit round 6, IOS-1).
            remainingMS: WireNumber.clamp(remaining, to: 0...WireNumber.maxCountdownMs),
            totalMS: long(o, "total_ms").flatMap { $0 > 0 ? min($0, WireNumber.maxCountdownMs) : nil }
        )
    }

    private static func ringing(_ o: [String: RoomJSON]) -> RoomFeedRinging? {
        guard let id = long(o, "fire_id") else { return nil }
        return RoomFeedRinging(fireID: id, kind: Self.kind(str(o, "kind")) ?? .timer, label: text(o, "label"))
    }

    private static func kind(_ s: String?) -> RoomTimerKind? {
        s.flatMap { RoomTimerKind(rawValue: $0) }
    }

    /// Whether `incoming` may replace `current`: frames can overtake each
    /// other, so one sent before the frame on screen is dropped. A frame (or
    /// a screen) without `sent_at_ms` is applied as it comes.
    public static func newer(_ current: RoomFeed?, _ incoming: RoomFeed) -> Bool {
        guard let was = current?.sentAtMS, let now = incoming.sentAtMS else { return true }
        return now >= was
    }

    /// The one artwork path the core sends, `/v1/music/cover/{track_id}`
    /// (`domovoi/room_display.py`), and nothing else: exactly
    /// `^/v1/music/cover/[0-9]{1,10}$`, ASCII digits only. The room never
    /// follows a link to another host, and no other route on its server is
    /// fetched for a picture (security audit round 6, IOS-4: it used to take
    /// any `/v1/` path).
    public static func isArtPath(_ p: String) -> Bool {
        let prefix = "/v1/music/cover/"
        guard p.hasPrefix(prefix) else { return false }
        let id = p.utf8.dropFirst(prefix.utf8.count)
        return (1...10).contains(id.count) && id.allSatisfy { $0 >= UInt8(ascii: "0") && $0 <= UInt8(ascii: "9") }
    }

    /// Only the core's room-media route with one of its tokens, exactly as
    /// the core writes it (`/v1/room-media/{token}`, domovoi/room_media.py:
    /// `secrets.token_urlsafe(24)`, held to `TOKEN_RE` there): exactly
    /// `^/v1/room-media/[A-Za-z0-9_-]{20,64}$`, ASCII only. Nothing else, no
    /// other host, no percent-encoding, `..`, query or fragment. Fetched with
    /// no credential, like a cover (Android's `isMediaThumbPath`).
    public static func isMediaThumbPath(_ p: String) -> Bool {
        let prefix = "/v1/room-media/"
        guard p.hasPrefix(prefix) else { return false }
        let token = p.utf8.dropFirst(prefix.utf8.count)
        return (20...64).contains(token.count) && token.allSatisfy { c in
            (c >= UInt8(ascii: "A") && c <= UInt8(ascii: "Z")) || (c >= UInt8(ascii: "a") && c <= UInt8(ascii: "z"))
                || (c >= UInt8(ascii: "0") && c <= UInt8(ascii: "9")) || c == UInt8(ascii: "_") || c == UInt8(ascii: "-")
        }
    }

    /// What ``RoomDisplaySource/art(_:)`` fetches: a cover or a result's thumbnail.
    public static func isImagePath(_ p: String) -> Bool { isArtPath(p) || isMediaThumbPath(p) }

    /// The `room_control` frame for `c`. It names the action, never the room:
    /// the socket is the room.
    public static func control(_ c: RoomControl) -> RoomProtocol.Frame {
        switch c {
        case .music(let verb):
            return ["type": .string("room_control"), "action": .string(verb.wire)]
        case .stopRinging(let fireID):
            return ["type": .string("room_control"), "action": .string("stop_ringing"), "fire_id": .int(fireID)]
        }
    }

    // MARK: - reading the frame

    private static func str(_ o: [String: RoomJSON], _ k: String) -> String? {
        if case .string(let s)? = o[k] { return s }
        return nil
    }

    private static func text(_ o: [String: RoomJSON], _ k: String) -> String? {
        guard let s = str(o, k)?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return String(s.prefix(maxText))
    }

    private static func long(_ o: [String: RoomJSON], _ k: String) -> Int64? {
        switch o[k] {
        case .int(let n)?: return n
        case .double(let d)? where d.isFinite && abs(d) < 9e15: return Int64(d)
        default: return nil
        }
    }

    private static func num(_ o: [String: RoomJSON], _ k: String) -> Double? {
        switch o[k] {
        case .int(let n)?: return Double(n)
        case .double(let d)? where d.isFinite: return d
        default: return nil
        }
    }
}

/// The engine's side of ``RoomDisplaySource/displayUpdates()``: the latest
/// feed and everyone watching it, behind one lock (``RoomEngine``'s status
/// watchers, for the display).
final class RoomDisplayChannel: @unchecked Sendable {
    private struct State {
        var feed: RoomFeed?
        var conts: [Int: AsyncStream<RoomFeed?>.Continuation] = [:]
        var next = 0
    }

    private let state = RoomLock(State())

    var feed: RoomFeed? { state.withLock { $0.feed } }

    func updates() -> AsyncStream<RoomFeed?> {
        AsyncStream { continuation in
            let id: Int = state.withLock { s in
                s.next += 1
                s.conts[s.next] = continuation
                continuation.yield(s.feed)
                return s.next
            }
            continuation.onTermination = { [weak self] _ in
                _ = self?.state.withLock { $0.conts.removeValue(forKey: id) }
            }
        }
    }

    /// Replaces the feed with what `change` makes of it (only when that
    /// changes it) and tells every watcher.
    func update(_ change: (RoomFeed?) -> RoomFeed?) {
        state.withLock { s in
            let next = change(s.feed)
            guard next != s.feed else { return }
            s.feed = next
            for c in s.conts.values { c.yield(next) }
        }
    }

    /// The room stopped: nothing to show, and every stream ends.
    func finish() {
        let conts = state.withLock { s -> [AsyncStream<RoomFeed?>.Continuation] in
            s.feed = nil
            let c = Array(s.conts.values)
            s.conts = [:]
            return c
        }
        for c in conts {
            c.yield(nil)
            c.finish()
        }
    }
}
