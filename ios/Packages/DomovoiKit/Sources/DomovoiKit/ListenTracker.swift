import Foundation

/// The song now playing on the phone's own player, as listening history
/// counts it (V051; the web's ListenLog in web/static/player.jsx, the
/// server's domovoi/music_listens.py `outcome_for`). A port of
/// `android/.../listens/ListenTracker.kt` (a1e611b6):
///
///  * `played_sec` is the time it was actually PLAYING (a monotonic clock
///    that runs only between "playing" and "not playing"): a pause adds
///    nothing, and a seek forward isn't listening;
///  * `outcome` is `finished` once 90 % or more played, whatever ended it;
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

    /// A library song `trackID` from `server` starts now. Whatever was open
    /// is closed first, as stopped (the caller closes it with the right word
    /// before, when it knows one).
    @discardableResult
    public mutating func begin(trackID: Int64, server: String, durationSec: Double?, playing: Bool) -> ListenRow? {
        let closed = close(natural: false)
        let positive = durationSec.flatMap { $0 > 0 && $0.isFinite ? $0 : nil }
        cur = Open(trackID: trackID, server: server, startedAtMs: wallMs(), durationSec: positive)
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
        var playedMs = c.playedMs + (c.sinceMono.map { monoMs() - $0 } ?? 0)
        let duration = c.durationSec
        if let duration {
            let durMs = Int64(duration * 1000)
            if natural { playedMs = max(playedMs, durMs) }
            playedMs = min(playedMs, durMs)
        }
        let played = Double(playedMs) / 1000.0
        guard played >= 1.0 else { return nil }
        return ListenRow(
            trackID: c.trackID,
            server: c.server,
            startedAtMs: c.startedAtMs,
            playedSec: Int(played.rounded()),
            durationSec: duration.map { Int($0.rounded()) },
            outcome: Self.outcome(playedSec: played, durationSec: duration, skipped: c.skipped && !natural)
        )
    }

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
