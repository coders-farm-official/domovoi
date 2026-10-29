import Foundation

/// Home's two clocks — map §2.2, `HomeScreen.kt:130-147`, read at `df658c2b`.
///
/// There are two, they are different facts, and collapsing them is the
/// mistake this type exists to prevent:
///
/// - **the phone's**, which drives the date and today; and
/// - **the server's**, which is what timers count against. A phone whose
///   clock is minutes off still counts down to the second, because
///   ``serverOffsetMs(serverNow:receivedAtMs:)`` is applied to every timer
///   figure on the screen and to nothing else.
public enum HomeClock {

    /// How often the screen's clock advances (`HomeScreen.kt:262-267`):
    /// **1 s** while something is counting, **30 s** otherwise.
    ///
    /// `counting` is "there is an active timer, a done line, or a playing
    /// room" (`HomeScreen.kt:261`). The three things it is computed from —
    /// the timer view's active and done lists, and the playing-room count —
    /// belong to sections this batch does not build, so ``HomeModel`` takes
    /// `counting` as an input that is `false` until something sets it. That
    /// is a gap with a name, in `design-notes/ios/I1D2-REPORT.md`: until the
    /// timers and rooms sections land, Home's clock ticks at 30 s, which is
    /// right for a screen with no countdown on it and wrong the moment one
    /// appears.
    public static func tickPeriodMs(counting: Bool) -> Int64 {
        counting ? 1_000 : 30_000
    }

    /// `val nowMs = maxOf(tick, System.currentTimeMillis())`
    /// (`HomeScreen.kt:131`).
    ///
    /// The `max` is the whole trick and it is worth keeping: `tick` only
    /// *schedules* a redraw, so a redraw that happens for any other reason —
    /// a read landing, a push arriving — still sees a current clock. Without
    /// it "a fresh read renders against a stale second", which is a countdown
    /// that jumps backwards.
    public static func nowMs(tick: Int64, wall: Int64) -> Int64 {
        max(tick, wall)
    }
}

/// `dayStartMs` (`HomeModels.kt:237`) — local midnight of the day `ms` falls
/// in, `plusDays` later.
///
/// This is the calendar read's key, and that is why it matters more than it
/// looks: the key changes by itself at local midnight, because the screen's
/// clock advances at least every 30 s, so the window rolls over with no timer
/// watching for midnight (map §2.3 item 10). Keep that property — a
/// midnight-watching timer is a thing that can be suspended.
public func dayStartMs(ms: Int64, timeZone: TimeZone, plusDays: Int = 0) -> Int64 {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = timeZone
    let date = Date(timeIntervalSince1970: Double(ms) / 1000)
    let midnight = calendar.startOfDay(for: date)
    // `plusDays` is added as a **calendar day**, not as 86 400 s: across a
    // daylight-saving change a week is not seven times twenty-four hours, and
    // `toLocalDate().plusDays(n).atStartOfDay(zone)` is what the Kotlin does.
    guard plusDays != 0 else { return Int64((midnight.timeIntervalSince1970 * 1000).rounded()) }
    guard let shifted = calendar.date(byAdding: .day, value: plusDays, to: midnight) else {
        return Int64((midnight.timeIntervalSince1970 * 1000).rounded())
    }
    return Int64((calendar.startOfDay(for: shifted).timeIntervalSince1970 * 1000).rounded())
}

/// `serverOffsetMs` (`HomeModels.kt:300`): the server's clock minus this
/// phone's, from a read received at `receivedAtMs`.
///
/// **Zero when the server did not say**, which is the Kotlin's `?: 0L` and is
/// the right default: a missing `server_now` means count against the phone,
/// not count against nothing.
public func serverOffsetMs(serverNow: String?, receivedAtMs: Int64) -> Int64 {
    guard let server = isoMs(serverNow) else { return 0 }
    return server - receivedAtMs
}
