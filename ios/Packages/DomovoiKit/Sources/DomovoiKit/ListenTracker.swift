import Foundation

/// The song now playing on the phone's own player, as listening history
/// counts it (V051; the web's ListenLog in web/static/player.jsx, the
/// server's domovoi/music_listens.py `outcome_for`). A port of
/// `android/.../listens/ListenTracker.kt` (a1e611b6):
///
///  * `played_sec` is the time it was actually PLAYING (a monotonic clock
///    that runs only between "playing" and "not playing"): a pause adds
///    nothing, and a seek forward isn't listening;
///  * `outcome` is `finished` on a natural end, or once 90 % or more
///    played whatever ended it; `played_sec` is always the time HEARD, a
///    natural end included (never padded to the length: "count real time",
///    2026-11-14);
///    else `skipped` when next, a queue row or removing the current song
///    ended it; else `stopped` (stop, a new play, casting, the app closing);
///  * a song that played under a second is not a listen.
///
/// Pure (two clocks injected), so every rule runs in `swift test`. Not
/// thread safe: ``ListenLog`` calls it on the main actor.
public struct ListenTracker {

    private struct Open {
        let trackID: Int64
        let server: String
        let startedAtMs: Int64
        /// The monotonic clock at the begin: nothing can have played longer
        /// than has passed since.
        let beganMono: Int64
        var durationSec: Double?
        var playedMs: Int64 = 0
        var sinceMono: Int64?
        var skipped = false
    }

    /// Wall clock, epoch ms: when a song began (`started_at`).
    private let wallMs: () -> Int64
    /// Monotonic ms: how long it played.
    private let monoMs: () -> Int64

    private var cur: Open?

    public init(
        wallMs: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        monoMs: @escaping () -> Int64
    ) {
        self.wallMs = wallMs
        self.monoMs = monoMs
    }

    /// The library track being followed, or nil.
    public var openTrack: Int64? { cur?.trackID }

    /// The server the open song came from, or nil.
    public var openServer: String? { cur?.server }

    /// Drop the open song without a row (its server was forgotten).
    @discardableResult
    public mutating func discard() -> Bool {
        defer { cur = nil }
        return cur != nil
    }

    /// A library song `trackID` from `server` starts now. Whatever was open
    /// is closed first, as stopped (the caller closes it with the right word
    /// before, when it knows one).
    @discardableResult
    public mutating func begin(trackID: Int64, server: String, durationSec: Double?, playing: Bool) -> ListenRow? {
        let closed = close(natural: false)
        let positive = durationSec.flatMap { $0 > 0 && $0.isFinite ? $0 : nil }
        cur = Open(trackID: trackID, server: server, startedAtMs: wallMs(), beganMono: monoMs(), durationSec: positive)
        if playing { cur?.sinceMono = monoMs() }
        return closed
    }

    /// The player started or stopped producing sound.
    public mutating func playing(_ isPlaying: Bool) {
        guard var c = cur else { return }
        if isPlaying, c.sinceMono == nil { c.sinceMono = monoMs() }
        if !isPlaying, let since = c.sinceMono {
            c.playedMs += monoMs() - since
            c.sinceMono = nil
        }
        cur = c
    }

    /// The player learnt the song's real length.
    public mutating func duration(_ sec: Double) {
        guard sec > 0, sec.isFinite else { return }
        cur?.durationSec = sec
    }

    /// The song now playing was skipped (next, a queue row, removed).
    public mutating func skip() {
        cur?.skipped = true
    }

    /// The song now playing has ended, however. `natural`: it ran to its end.
    /// The row to log, or nil (nothing open, or under a second).
    public mutating func close(natural: Bool) -> ListenRow? {
        guard let c = cur else { return nil }
        cur = nil
        let now = monoMs()
        var playedMs = c.playedMs + (c.sinceMono.map { now - $0 } ?? 0)
        // Never more than has passed since the begin, plus a little for the
        // clock reads in between (security review, finding 4).
        playedMs = min(playedMs, now - c.beganMono + Self.clockSlackMs)
        let duration = c.durationSec
        // `played` is the time actually HEARD, a natural end included (owner,
        // 2026-11-14: "count real time"). It used to be padded up to the
        // song's length on a natural end (player.jsx's rule), so a seek from
        // 0:10 to 5:50 of a 6:00 song logged 360 s. Only the seconds change:
        // a natural end is still `finished` (below).
        if let duration {
            playedMs = min(playedMs, Int64(duration * 1000))
        }
        let played = Double(playedMs) / 1000.0
        guard played >= 1.0 else { return nil }
        return ListenRow(
            trackID: c.trackID,
            server: c.server,
            startedAtMs: c.startedAtMs,
            playedSec: Int(played.rounded()),
            durationSec: duration.map { Int($0.rounded()) },
            outcome: natural
                ? "finished"
                : Self.outcome(playedSec: played, durationSec: duration, skipped: c.skipped)
        )
    }

    /// The slack on the elapsed-time clamp, in ms.
    public static let clockSlackMs: Int64 = 1_000

    /// `finished` from this share of the song on (music_listens.py FINISHED_SHARE).
    public static let finishedShare = 0.9

    /// domovoi/music_listens.py `outcome_for`, word for word.
    public static func outcome(playedSec: Double, durationSec: Double?, skipped: Bool) -> String {
        if let durationSec, durationSec > 0, playedSec >= finishedShare * durationSec { return "finished" }
        return skipped ? "skipped" : "stopped"
    }
}

/// One closed listen, before it is an outbox row.
public struct ListenRow: Equatable, Sendable {
    public let trackID: Int64
    public let server: String
    public let startedAtMs: Int64
    public let playedSec: Int
    public let durationSec: Int?
    public let outcome: String
}
