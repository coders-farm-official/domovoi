import Foundation

// ---------------------------------------------------------------------------
// Home's today section, as rules — the port of `HomeModels.kt:233`, `:604-672`
// (read at `6859b1f7`), map §3.6 and its strings §6.6.
//
// This is the one section that is **always drawn**, even empty: the card
// never disappears, and ``todayEmptyText(events:nowMs:timeZone:shared:)`` is
// what it says when there is nothing in it. Everything here runs on the
// **phone's** clock, unlike the timers; a calendar entry is a time of day in
// the house, not a countdown against the database.
// ---------------------------------------------------------------------------

/// `HOME_TODAY_ROWS` (`HomeModels.kt:36`) — today rows on a wider screen.
/// A phone shows ``homePhoneRows``, which the attention card shares.
public let homeTodayRows = 6

// MARK: - the clock

/// `homeClock(ms, zone)` (`HomeModels.kt:233`) — `9:00am`.
///
/// **Not a locale formatter**, on purpose and on both platforms: the Kotlin
/// formats `h:mm a` in `Locale.US`, lower-cases it and removes the space, so a
/// phone set to a 24-hour locale still reads `9:00am` here — the same string
/// the house's dashboard writes. A `DateFormatter` with
/// `.setLocalizedDateFormatFromTemplate` would give `09:00` on that phone and
/// `9:00 AM` on another, and the two would disagree about the same event.
///
/// ``HomeClock`` next door is a different thing — the screen's tick and the
/// server offset — and this is deliberately not in it: one is a clock, the
/// other is a way of writing a time down.
///
/// The hour is the 12-hour hour with no padding (`9`, `12`), the minute is
/// always two digits, and midnight and noon are `12:00am` and `12:00pm` —
/// which is what `h` in `Locale.US` does, where `K` would give `0`.
public func homeClock(ms: Int64, timeZone: TimeZone) -> String {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = timeZone
    let parts = calendar.dateComponents(
        [.hour, .minute],
        from: Date(timeIntervalSince1970: Double(ms) / 1000)
    )
    let hour24 = parts.hour ?? 0
    let hour = hour24 % 12 == 0 ? 12 : hour24 % 12
    let suffix = hour24 < 12 ? "am" : "pm"
    return "\(hour):\(String(format: "%02d", parts.minute ?? 0))\(suffix)"
}

// MARK: - an event's span

/// `eventStartMs(e)` (`HomeModels.kt:604`).
///
/// An event whose `starts_at` will not parse sorts **last** and is filtered
/// out by every window, because `Int64.max` is not less than any day's
/// midnight. That is Kotlin's `Long.MAX_VALUE` and it is the safe direction:
/// an unreadable time is dropped rather than drawn at the epoch.
public func eventStartMs(_ event: HomeEvent) -> Int64 {
    isoMs(event.startsAt) ?? Int64.max
}

/// `eventEndMs(e)` (`HomeModels.kt:606`) — **an event with no end runs an
/// hour.**
///
/// Which matters more than it sounds: the today window keeps events "ending at
/// or after now", so without the hour an all-day-ish entry with no `ends_at`
/// would vanish from the card the instant it started.
public func eventEndMs(_ event: HomeEvent) -> Int64 {
    if let end = isoMs(event.endsAt) { return end }
    let start = eventStartMs(event)
    return start == Int64.max ? Int64.max : start + 3_600_000
}

// MARK: - the rows

/// One row of the today card (`TodayRow`, `HomeModels.kt:610`).
public struct TodayRow: Equatable, Sendable {

    public let event: HomeEvent
    public let startMs: Int64

    /// **Nil when the event named no end**, which is not the same as
    /// ``eventEndMs(_:)``'s invented hour: the row draws a second time only
    /// when the server gave one, and the hour exists for the filtering alone.
    public let endMs: Int64?

    /// The literal `busy` on a shared screen.
    public let title: String

    /// **Never on a shared screen**, and nil for an empty one.
    public let location: String?

    /// Under way now — the brand `now` pill.
    public let running: Bool

    public init(
        event: HomeEvent,
        startMs: Int64,
        endMs: Int64?,
        title: String,
        location: String?,
        running: Bool
    ) {
        self.event = event
        self.startMs = startMs
        self.endMs = endMs
        self.title = title
        self.location = location
        self.running = running
    }
}

/// `today` or `tomorrow` and its rows (`TodayDay`, `HomeModels.kt:622`).
public struct TodayDay: Equatable, Sendable {
    public let label: String
    public let rows: [TodayRow]

    public init(label: String, rows: [TodayRow]) {
        self.label = label
        self.rows = rows
    }
}

/// `todayDays(events, nowMs, zone, shared, limit)` (`HomeModels.kt:630`).
///
/// Sorted by start, filtered to events starting **before the day after
/// tomorrow's midnight** and ending at or after now, **capped**, and only
/// then split into `today` / `tomorrow` with empty days dropped.
///
/// ### The cap comes before the grouping, and that is the rule
///
/// "The cap is applied before the grouping, so a day whose every row was cut
/// leaves no bare label behind" — pinned on Android by
/// `aPhonesThreeRowsNeverLeaveABareDayLabel` and here by the test of the same
/// name. Cap each day instead and a phone with three events today shows
/// `today` with its three rows and then the word `tomorrow` with nothing
/// under it: a heading that promises rows the cap has already taken. The
/// order of two lines of code, and it is the difference between a card that
/// is honest about being short and one that looks broken.
///
/// - Parameters:
///   - shared: a shared screen gets the times and the literal `busy`, and the
///     location is dropped **entirely** rather than blanked.
///   - limit: ``homePhoneRows`` on a phone, ``homeTodayRows`` otherwise.
public func todayDays(
    events: [HomeEvent],
    nowMs: Int64,
    timeZone: TimeZone,
    shared: Bool,
    limit: Int = homeTodayRows
) -> [TodayDay] {
    let d1 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 1)
    let d2 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 2)
    let shown = stableAscendingByStart(events)
        .filter { eventStartMs($0) < d2 && eventEndMs($0) >= nowMs }
        .prefix(limit)

    func row(_ event: HomeEvent) -> TodayRow {
        let start = eventStartMs(event)
        return TodayRow(
            event: event,
            startMs: start,
            endMs: isoMs(event.endsAt),
            title: shared ? HomeTodayStrings.busy : event.title,
            location: shared ? nil : event.location.flatMap { $0.isEmpty ? nil : $0 },
            running: start <= nowMs && nowMs < eventEndMs(event)
        )
    }

    return [
        TodayDay(label: HomeTodayStrings.today, rows: shown.filter { eventStartMs($0) < d1 }.map(row)),
        TodayDay(label: HomeTodayStrings.tomorrow, rows: shown.filter { eventStartMs($0) >= d1 }.map(row)),
    ].filter { !$0.rows.isEmpty }
}

/// `todayEmptyText(events, nowMs, zone, shared)` (`HomeModels.kt:660`).
///
/// `nothing on today`, or **`nothing more today`** when something earlier
/// today has already ended — the distinction is the point of the line: a day
/// that is over reads differently from a day with nothing in it.
///
/// Then, when there is one, `· next: <date> <clock> <title>` naming the first
/// event from **the day after tomorrow onward**. The calendar read asks for a
/// whole week precisely so this clause can exist (`calendarPath`), and the
/// title is `busy` on a shared screen like every other title here.
///
/// Note "earlier today" means started before tomorrow's midnight *and*
/// already ended — an event still under way is not what makes the day over,
/// and it would not be in the empty branch anyway.
public func todayEmptyText(
    events: [HomeEvent],
    nowMs: Int64,
    timeZone: TimeZone,
    shared: Bool
) -> String {
    let d1 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 1)
    let d2 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 2)
    let sorted = stableAscendingByStart(events)
    let next = sorted.first { eventStartMs($0) >= d2 && eventStartMs($0) != Int64.max }
    let earlier = sorted.contains { eventStartMs($0) < d1 && eventEndMs($0) < nowMs }
    let lead = earlier ? HomeTodayStrings.nothingMoreToday : HomeTodayStrings.nothingOnToday
    guard let next else { return lead }
    let at = eventStartMs(next)
    let title = shared ? HomeTodayStrings.busy : next.title
    return lead + HomeTodayStrings.middot + HomeTodayStrings.nextPrefix
        + "\(homeDate(ms: at, timeZone: timeZone)) \(homeClock(ms: at, timeZone: timeZone)) \(title)"
}

/// Kotlin's `sortedBy`, which is **stable**; Swift's `sorted(by:)` is not
/// guaranteed to be.
///
/// Two events at the same minute is the ordinary case, not an edge one — a
/// calendar full of 9:00 entries — and an unstable sort would reorder them
/// between redraws, and could change *which* of them the cap keeps.
func stableAscendingByStart(_ events: [HomeEvent]) -> [HomeEvent] {
    events.enumerated()
        .sorted { left, right in
            let a = eventStartMs(left.element), b = eventStartMs(right.element)
            return a == b ? left.offset < right.offset : a < b
        }
        .map(\.element)
}

// MARK: - the strings

/// Today's strings, verbatim from `ui/screens/home/` (map §6.6). Pinned
/// against the Kotlin on disk by `ShellStringsTests`.
public enum HomeTodayStrings {

    /// The card title, and the first day label (`HomeSections.kt:909`,
    /// `HomeModels.kt:654`).
    public static let today = "today"

    /// The header link (`HomeSections.kt:909`).
    public static let calendar = "calendar"

    /// `HomeModels.kt:655`.
    public static let tomorrow = "tomorrow"

    /// Every event's title on a shared screen (`HomeModels.kt:648`, `:669`).
    public static let busy = "busy"

    /// The running-event pill (`HomeSections.kt:978`).
    public static let now = "now"

    /// `HomeSections.kt:916`.
    public static let calendarUnavailable = "calendar unavailable"

    /// `HomeModels.kt:666`.
    public static let nothingOnToday = "nothing on today"

    /// …and when something earlier today has already ended.
    public static let nothingMoreToday = "nothing more today"

    /// Between the lead and the next event (`HomeModels.kt:669`).
    /// **U+00B7, one space each side.**
    public static let middot = " · "

    /// `next: ` — the head of the clause, before the date.
    public static let nextPrefix = "next: "
}
