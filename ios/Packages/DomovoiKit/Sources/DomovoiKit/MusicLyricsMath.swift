import Foundation

/// Pure timing math, shared by the lyrics panel, the track sheet and the room
/// cards — `android/.../player/Lyrics.kt:131`'s `LyricsMath`, function for
/// function.
///
/// Every one of these is a function of its arguments, which is why this is
/// where the cheapest and most valuable tests on this item are: Android's
/// `LyricsMathTest` is the only thing that specifies this behaviour anywhere,
/// and a lyrics panel whose arithmetic is a line out is a panel that is
/// wrong in a way nobody can describe.
public enum MusicLyricsMath {

    /// A line is shown this much before its time (`Lyrics.kt:133`). Words that
    /// land exactly on the beat read late.
    public static let leadMs: Int64 = 150

    /// Timed lines kept per song — the server stores at most as many
    /// (`Lyrics.kt:136`).
    public static let maxLines = 4_000

    /// A backward correction smaller than this, while playing, is held. See
    /// ``steady(previousMs:nextMs:playing:)``.
    public static let steadyWindowMs: Int64 = 300

    /// The last line whose time is at or before `positionMs + leadMs`; `-1`
    /// before the first (`Lyrics.kt:139`).
    ///
    /// A binary search rather than a scan, because it runs on every frame of
    /// a four-thousand-line document.
    public static func activeIndex(_ lines: [MusicLyricLine], positionMs: Int64) -> Int {
        guard !lines.isEmpty else { return -1 }
        // The clamp is Android's and it is not theatre: `positionMs` comes
        // from a clock difference, and `Int64.max + 150` traps in Swift
        // rather than wrapping as it would in Kotlin.
        let at = positionMs > Int64.max - leadMs ? Int64.max : positionMs + leadMs
        var low = 0
        var high = lines.count - 1
        var found = -1
        while low <= high {
            let mid = low + (high - low) / 2
            if lines[mid].t <= at {
                found = mid
                low = mid + 1
            } else {
                high = mid - 1
            }
        }
        return found
    }

    /// A room's position now: `elapsed * 1000 + (playing ? now − readAt : 0)
    /// − nudge`, clamped to `[0, duration]` (`Lyrics.kt:151`).
    ///
    /// `readAtMs` is **when this phone received the reading**, never the
    /// server's clock — see ``MusicRoomLyrics/receivedAtMs``. A clock read
    /// before the anchor (it never should be) adds nothing rather than going
    /// backwards.
    public static func roomPositionMs(
        elapsedSec: Double,
        readAtMs: Int64,
        nowMs: Int64,
        playing: Bool,
        durationSec: Double?,
        nudgeMs: Int64
    ) -> Int64 {
        let base = elapsedSec.isFinite ? Int64((elapsedSec * 1000).rounded()) : 0
        let run = playing ? max(0, nowMs - readAtMs) : 0
        return clampToTrack(base + run - nudgeMs, durationSec: durationSec)
    }

    /// This phone's own playback between the engine's 500 ms position ticks:
    /// the tick's position, plus the time since it was seen **at the playback
    /// speed**, clamped to `[0, duration]` (`Lyrics.kt:165`).
    ///
    /// The speed matters: at 1.5× a panel that interpolated at wall-clock rate
    /// falls a line behind every twenty seconds.
    public static func localPositionMs(
        tickSec: Double,
        tickAtMs: Int64,
        nowMs: Int64,
        playing: Bool,
        speed: Double,
        durationSec: Double?
    ) -> Int64 {
        let base = tickSec.isFinite ? Int64((tickSec * 1000).rounded()) : 0
        let rate = (speed.isFinite && speed > 0) ? speed : 1
        let run = playing ? Int64((Double(max(0, nowMs - tickAtMs)) * rate).rounded()) : 0
        return clampToTrack(base + run, durationSec: durationSec)
    }

    /// The position to show after `previousMs` when the clock now says
    /// `nextMs` (`Lyrics.kt:182`).
    ///
    /// While playing, a step **back** smaller than ``steadyWindowMs`` is a
    /// re-anchoring jitter — a tick or a poll seen a little late — not a seek,
    /// and is held, so a line never flickers back and forth at its boundary.
    /// A bigger step back is a seek and is followed at once. Paused,
    /// everything is followed: there is no jitter to smooth.
    public static func steady(previousMs: Int64?, nextMs: Int64, playing: Bool) -> Int64 {
        guard
            playing,
            let previousMs,
            nextMs < previousMs,
            previousMs - nextMs < steadyWindowMs
        else { return nextMs }
        return previousMs
    }

    /// `[0, duration]`. An unknown or nonsense duration clamps at zero only —
    /// a position past the end of a track whose length nobody knows is not
    /// something this can correct.
    static func clampToTrack(_ ms: Int64, durationSec: Double?) -> Int64 {
        let atLeastZero = max(0, ms)
        guard let durationSec, durationSec.isFinite, durationSec > 0 else { return atLeastZero }
        return min(atLeastZero, Int64((durationSec * 1000).rounded()))
    }
}

/// The per-room timing nudge — `android/.../player/Lyrics.kt:203`'s
/// `LyricsNudge`.
///
/// A room's speaker plays a little behind MPD's clock (its stream buffer), so
/// each room keeps an offset this phone applies to that room's lyrics. **A
/// positive value shows the lyrics later.**
///
/// The encode/decode is ported rather than replaced by `Codable` because the
/// clamping is the point: a value written by hand, or carried over from
/// another client, must not be able to put a room ten minutes out.
public enum MusicLyricsNudge {

    /// One tap of the `±¼ s` chips (`Lyrics.kt:205`).
    public static let stepMs: Int64 = 250

    /// As far as a room can be nudged either way (`Lyrics.kt:206`). Beyond
    /// ten seconds the person is not correcting a buffer, they are looking at
    /// the wrong song.
    public static let maxMs: Int64 = 10_000

    public static func clamp(_ ms: Int64) -> Int64 { min(max(ms, -maxMs), maxMs) }

    /// The stored map. Anything unreadable is **dropped** rather than failing
    /// the read, every value is clamped, and a zero is not kept — a room at
    /// no offset is a room with no entry (`Lyrics.kt:213`).
    public static func decode(_ json: String?) -> [String: Int64] {
        guard
            let json,
            !json.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            let data = json.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        var out: [String: Int64] = [:]
        for (room, value) in object {
            guard !room.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
            // A number, and **not a string that looks like one**: Android's
            // `takeIf { !it.isString }` refuses `{"kitchen": "250"}`, because
            // a client that writes a string there has written something this
            // app did not, and guessing at it is how a nudge of 250 becomes
            // one of 250,000.
            guard let number = value as? NSNumber, !(value is String) else { continue }
            let ms = clamp(number.int64Value)
            if ms != 0 { out[room] = ms }
        }
        return out
    }

    /// The map as it is stored. Keys in order, so the stored string is
    /// stable and a test can state it (`Lyrics.kt:222`).
    public static func encode(_ map: [String: Int64]) -> String {
        let body = map.keys.sorted()
            .map { "\($0.jsonLiteral):\(map[$0] ?? 0)" }
            .joined(separator: ",")
        return "{\(body)}"
    }

    /// `map` with `room` set to `ms` (clamped); **zero removes the room**
    /// (`Lyrics.kt:227`).
    public static func with(_ map: [String: Int64], room: String, ms: Int64) -> [String: Int64] {
        var out = map
        let value = clamp(ms)
        if value == 0 {
            out.removeValue(forKey: room)
        } else {
            out[room] = value
        }
        return out
    }

    /// `"timing"`, or `"timing +0.75 s"` / `"timing −0.5 s"` while nudged
    /// (`Lyrics.kt:231`).
    ///
    /// The minus is **U+2212**, the real minus sign, not a hyphen: at label
    /// size a hyphen beside a digit reads as part of the number.
    public static func label(_ ms: Int64) -> String {
        guard ms != 0 else { return "timing" }
        let sign = ms > 0 ? "+" : "\u{2212}"
        let seconds = Double(abs(ms)) / 1000
        let text: String
        if seconds == seconds.rounded(.down) {
            text = "\(Int64(seconds))"
        } else {
            // `"0.750"` → `"0.75"`, as Kotlin's `trimEnd('0')` does. Swift's
            // default description of 0.75 has no trailing zero, so the trim
            // is here for the values that do.
            var trimmed = "\(seconds)"
            while trimmed.hasSuffix("0") { trimmed.removeLast() }
            text = trimmed
        }
        return "timing \(sign)\(text) s"
    }
}
