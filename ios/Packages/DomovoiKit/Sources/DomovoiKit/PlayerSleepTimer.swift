import Foundation

/// The sleep timer's rules.
///
/// `PlayerController.kt:528-564`. `setSleepMinutes` counts down a second at a
/// time and **pauses** at zero; `setSleepEndOfTrack` seeds the remaining
/// seconds from `duration - position`, at least one; `cancelSleep` clears it.
///
/// ## The Android race that is not ported
///
/// `setSleepEndOfTrack` over there calls `setSleepMinutes(0)` — which starts a
/// countdown job — and then overwrites the value and starts a **second** job
/// (`:546-559`), so two jobs decrement one value for a moment. The behaviour
/// is what is being ported, not the race: one countdown here, seeded once.
/// Flagged rather than silently fixed, in
/// `design-notes/ios/overnight/player/DECISIONS.md` and in
/// `ANDROID-DEFECTS-FOUND-BY-IOS.md`, because it is a live defect in the
/// shipping Android app.
public enum PlayerSleepTimer {

    /// Seconds to count down from for `minutes` (`:530`).
    public static func seconds(forMinutes minutes: Int) -> Int {
        max(minutes, 0) * 60
    }

    /// Seconds to count down from for "at the end of this track" (`:547`).
    ///
    /// At least one second, so a track that has already finished still pauses
    /// through the countdown rather than never firing.
    public static func secondsForEndOfTrack(durationSec: Double, positionSec: Double) -> Int {
        let left = durationSec - positionSec
        guard left.isFinite else { return 1 }
        return max(Int(left), 1)
    }

    /// One tick of the countdown: what `remaining` becomes, and whether the
    /// player pauses now.
    ///
    /// `.pause` is returned **once** — at the step that reaches zero — and the
    /// value becomes nil, so a consumer cannot pause twice off one timer. A
    /// nil `remaining` is a cancelled timer and nothing happens.
    public struct Tick: Equatable, Sendable {
        public let remaining: Int?
        public let pause: Bool

        public init(remaining: Int?, pause: Bool) {
            self.remaining = remaining
            self.pause = pause
        }
    }

    public static func tick(remaining: Int?) -> Tick {
        guard let remaining else { return Tick(remaining: nil, pause: false) }
        let left = remaining - 1
        if left <= 0 { return Tick(remaining: nil, pause: true) }
        return Tick(remaining: left, pause: false)
    }
}
