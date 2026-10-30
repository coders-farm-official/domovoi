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
    /// `counting` is ``homeCounting(timers:rooms:)``, and the screen now
    /// passes it: `HomeView` calls ``HomeModel/setCounting(_:)`` from the same
    /// values it draws. Part 2 shipped with nothing setting it and said so;
    /// 3b is the batch that drew the first countdown, so it is also the batch
    /// that had to make the clock tick for it.
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

/// Whether anything on Home is counting, which is the only input to
/// ``HomeClock/tickPeriodMs(counting:)`` — `HomeScreen.kt:261`:
///
/// ```kotlin
/// val counting = tv.active.isNotEmpty() || tv.done.isNotEmpty() || playing > 0
/// ```
///
/// **Added in 3b**: the rule is one line and 3a did not write it, because the
/// three things it reads are the timers and rooms sections'. It is here rather
/// than in the view for the reason every other one-line rule is — a view that
/// forgot the `done` term would leave a `done · kitchen` line sitting for its
/// whole sixty seconds while the clock ticked at thirty, so the line would
/// vanish up to thirty seconds late.
///
/// `playing` is `roomRank == 0`, which needs a **song** as well as a state, so
/// a stopped room whose player still names a track does not make the screen
/// tick every second for nothing.
public func homeCounting(timers: TimerView, rooms: [HomeRoom]) -> Bool {
    !timers.active.isEmpty || !timers.done.isEmpty || rooms.contains { roomRank($0) == 0 }
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
