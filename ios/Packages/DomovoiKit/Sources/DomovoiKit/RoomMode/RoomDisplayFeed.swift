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

    public init(
        nowPlaying: RoomFeedNowPlaying?,
        timers: [RoomFeedTimer],
        ringing: RoomFeedRinging?,
        receivedAt: Int64 = 0,
        sentAtMS: Int64? = nil
    ) {
        self.nowPlaying = nowPlaying
        self.timers = timers
        self.ringing = ringing
        self.receivedAt = receivedAt
        self.sentAtMS = sentAtMS
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
    /// The bytes of an artwork path from the feed, from the room's own
    /// server; nil when it cannot be had.
    func art(_ path: String) async -> Data?
}

/// The frames, both on `/v1/stream/{room}` (shape accepted by the core team
/// 2026-11-16; no released core sends or takes them yet):
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
///      "ringing": null | {"fire_id":N,"kind":"timer"|"alarm","label":..}}
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
    /// The ability name (hello `supports`, and the core's `ready.features`).
    ///
    /// Not in ``RoomProtocol/supports(echoCanceller:)`` yet: the hello is
    /// pinned to docs/uml/satellite-fixtures.json (`RoomProtocolTests`). When
    /// the fixture's room hello lists `room_display`, append it there. Until
    /// then the room shows and sends nothing unless the core's `ready.features`
    /// lists it.
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
        return RoomFeed(
            nowPlaying: nowPlaying,
            timers: timers,
            ringing: ringing,
            sentAtMS: long(o, "sent_at_ms")
        )
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
            durationSec: num(o, "duration_sec").flatMap { $0 > 0 ? $0 : nil },
            elapsedSec: num(o, "elapsed_sec").flatMap { $0 >= 0 ? $0 : nil },
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
            remainingMS: max(0, remaining),
            totalMS: long(o, "total_ms").flatMap { $0 > 0 ? $0 : nil }
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

    /// Only a plain path on the room's own server: the room never follows a
    /// link to another host, and nothing climbs out of `/v1/`.
    public static func isArtPath(_ p: String) -> Bool {
        guard p.hasPrefix("/v1/"), p.utf16.count <= 300 else { return false }
        if p.contains("..") || p.contains("\\") { return false }
        if p.contains("/" + "/") { return false }
        return !p.unicodeScalars.contains { s in
            s.properties.isWhitespace || s.value < 0x20 || s.value == 0x7F
        }
    }

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
