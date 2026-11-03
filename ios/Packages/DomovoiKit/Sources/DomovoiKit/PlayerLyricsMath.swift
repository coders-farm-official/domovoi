import Foundation

/// Following a position through a song's timed lines.
///
/// `android/.../player/Lyrics.kt:120-200` (`LyricsMath`) — and **only** that
/// part of it. `Lyrics.kt` is three things: the wire shapes (`LyricLine`,
/// `LyricsDoc`, `RoomLyrics`), this timing math, and a repository with view
/// states. The timing is the part that needs the player's tick and rate, which
/// nothing but this item has, so it lives here; the doc types, the repository,
/// the view states and the room nudge preference belong to the **MusicMore**
/// item, which owns the lyrics surface. This takes plain times, so it needs no
/// doc type at all and MusicMore does not have to wait for one.
///
/// ## Lyrics text is never logged
///
/// `Lyrics.kt:38-42`: lyrics text goes in no log, no diagnostic, no
/// notification, no media session and no exception message. Everything here
/// takes and returns **times and indices**, so there is nothing to leak — and
/// the Now Playing dictionary ``PlayerModel`` writes must never carry a lyric
/// line either (``PlayerNowPlayingInfo`` has no field for one).
public enum PlayerLyricsMath {

    /// A line is shown this early, so it is on screen as it is sung
    /// (`Lyrics.kt:122`).
    public static let leadMs: Int64 = 150

    /// While playing, a backward step smaller than this is re-anchoring
    /// jitter, not a seek (`Lyrics.kt:128`).
    public static let steadyWindowMs: Int64 = 300

    /// The last line with `t <= positionMs + leadMs`; -1 before the first.
    /// Binary search, because this runs on every frame of a scrolling view
    /// (`Lyrics.kt:131`).
    public static func activeIndex(times: [Int64], positionMs: Int64) -> Int {
        guard !times.isEmpty else { return -1 }
        let at = positionMs > Int64.max - leadMs ? Int64.max : positionMs + leadMs
        var lo = 0
        var hi = times.count - 1
        var found = -1
        while lo <= hi {
            let mid = (lo + hi) / 2
            if times[mid] <= at {
                found = mid
                lo = mid + 1
            } else {
                hi = mid - 1
            }
        }
        return found
    }

    /// Where a **room** is now: its reported elapsed time, plus the time since
    /// this phone **received** that reading, less the room's nudge
    /// (`Lyrics.kt:140`).
    ///
    /// Anchored on the phone's own receipt clock, never the server's: the two
    /// clocks are not the same clock, and a position computed from the
    /// server's would drift by whatever the two disagree about. A clock read
    /// before the anchor — which it never should be — adds nothing rather than
    /// subtracting.
    public static func roomPositionMs(
        elapsedSec: Double,
        readAtMs: Int64,
        nowMs: Int64,
        playing: Bool,
        durationSec: Double?,
        nudgeMs: Int64
    ) -> Int64 {
        let base = elapsedSec.isFinite ? Int64((elapsedSec * 1000).rounded()) : 0
        let run = playing ? max(nowMs - readAtMs, 0) : 0
        return clampToTrack(base + run - nudgeMs, durationSec: durationSec)
    }

    /// This phone's own playback between the player's 500 ms position ticks:
    /// the tick's position, plus the time since it was seen **at the playback
    /// rate** while playing (`Lyrics.kt:165`).
    ///
    /// The rate is the part that is easy to miss: at 1.5× a line sung 1.5
    /// seconds after the tick arrives one wall-clock second later, and a
    /// reader that ignored `speed` would show every line late by half the gap
    /// — worse the further from the tick.
    public static func localPositionMs(
        tickSec: Double,
        tickAtMs: Int64,
        nowMs: Int64,
        playing: Bool,
        speed: Float,
        durationSec: Double?
    ) -> Int64 {
        let base = tickSec.isFinite ? Int64((tickSec * 1000).rounded()) : 0
        let rate = (speed.isFinite && speed > 0) ? Double(speed) : 1.0
        let run = playing ? Int64((Double(max(nowMs - tickAtMs, 0)) * rate).rounded()) : 0
        return clampToTrack(base + run, durationSec: durationSec)
    }

    /// The position to show after `previousMs` when the clock now says
    /// `nextMs` (`Lyrics.kt:182`).
    ///
    /// While playing, a step **back** smaller than ``steadyWindowMs`` is a
    /// tick or a poll seen a little late, not a seek, and is held — so a line
    /// never flickers back and forth at its boundary. A bigger step back is a
    /// seek and is followed at once; paused, everything is.
    public static func steady(previousMs: Int64?, nextMs: Int64, playing: Bool) -> Int64 {
        if playing, let previousMs, nextMs < previousMs, previousMs - nextMs < steadyWindowMs {
            return previousMs
        }
        return nextMs
    }

    static func clampToTrack(_ ms: Int64, durationSec: Double?) -> Int64 {
        let atLeastZero = max(ms, 0)
        guard let durationSec, durationSec.isFinite, durationSec > 0 else { return atLeastZero }
        return min(atLeastZero, Int64((durationSec * 1000).rounded()))
    }
}
