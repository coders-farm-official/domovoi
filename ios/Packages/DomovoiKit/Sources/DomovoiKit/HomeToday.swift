import Foundation

// ---------------------------------------------------------------------------
// Home's today / next section, as rules — the port of `HomeModels.kt:233`,
// `:611-716` (1.3.0, merge `bf05916b`), map §3.6 and its strings §6.6.
// Since 1.3.0 it reads every calendar the house shows (its own, linked ones,
// phones'), and an event from one of those carries its calendar's colour.
//
// This is the one section that is **always drawn**, even empty: the card
// never disappears, and ``HomeTodayStrings/calendarEmpty`` is what it says
// when the coming week is empty. Everything here runs on the
// **phone's** clock, unlike the timers; a calendar entry is a time of day in
// the house, not a countdown against the database.
// ---------------------------------------------------------------------------

/// `HOME_NEXT_ROWS` (`HomeModels.kt:616`) — today / next rows on a wider
/// screen. A phone shows ``homePhoneRows``, which the attention card shares.
public let homeNextRows = 5

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

/// The date an all-day event's ISO string names, **as written**
/// (`writtenDay`, `HomeModels.kt:613`): `2026-10-01` of
/// `2026-10-01T00:00:00-05:00`, never moved into another zone. The server
/// writes an all-day bound as the house's local midnight with the house's own
/// offset, so its first ten characters are the date it is.
public func homeWrittenDay(_ iso: String?) -> CalendarDay? {
    guard let text = iso?.trimmingCharacters(in: .whitespacesAndNewlines), text.count >= 10 else { return nil }
    let parts = text.prefix(10).split(separator: "-", omittingEmptySubsequences: false)
    guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2,
          let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
          (1...12).contains(month), (1...31).contains(day)
    else { return nil }
    return CalendarDay(year: year, month: month, day: day)
}

/// `eventStartMs(e, zone)` (`HomeModels.kt:617`).
///
/// An all-day event starts at **this phone's** midnight of its written date.
/// Otherwise the instant; an event whose `starts_at` will not parse sorts
/// **last** and is filtered out by every window, because `Int64.max` is not
/// less than any day's midnight. That is Kotlin's `Long.MAX_VALUE` and it is
/// the safe direction: an unreadable time is dropped rather than drawn at the
/// epoch.
public func eventStartMs(_ event: HomeEvent, timeZone: TimeZone = .current) -> Int64 {
    if event.allDay, let day = homeWrittenDay(event.startsAt) { return day.startMs(in: timeZone) }
    return isoMs(event.startsAt) ?? Int64.max
}

/// `eventEndMs(e, zone)` (`HomeModels.kt:623`) — **an event with no end runs
/// an hour**; an all-day one runs to the start of its (exclusive) end date,
/// or to the end of its one day when it names none.
///
/// The hour matters more than it sounds: the window keeps events "ending
/// after now", so without it an entry with no `ends_at` would vanish from the
/// card the instant it started.
public func eventEndMs(_ event: HomeEvent, timeZone: TimeZone = .current) -> Int64 {
    if event.allDay {
        if let end = homeWrittenDay(event.endsAt) { return end.startMs(in: timeZone) }
        if let start = homeWrittenDay(event.startsAt) { return start.adding(days: 1).startMs(in: timeZone) }
    }
    if let end = isoMs(event.endsAt) { return end }
    let start = eventStartMs(event, timeZone: timeZone)
    return start == Int64.max ? Int64.max : start + 3_600_000
}

/// `homeHexColor` (`HomeModels.kt:653`): `#33b679` → `0xFF33B679`; anything
/// that is not six hex digits (with or without the `#`) is nil.
public func homeHexColor(_ hex: String?) -> Int64? {
    guard let trimmed = hex?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
    let text: String = trimmed.hasPrefix("#") ? String(trimmed.dropFirst()) : trimmed
    guard text.count == 6, text.allSatisfy({ (c: Character) -> Bool in c.isASCII && c.isHexDigit }),
          let value = Int64(text, radix: 16)
    else { return nil }
    return 0xFF00_0000 | value
}

// MARK: - the rows

/// One row of the today / next card (`TodayRow`, `HomeModels.kt:636`).
public struct TodayRow: Equatable, Sendable {

    public let event: HomeEvent
    public let startMs: Int64

    /// **Nil when the event named no end**, and for an all-day one, which is
    /// not the same as ``eventEndMs(_:timeZone:)``'s invented hour: the row
    /// draws a second time only when the server gave one, and the hour exists
    /// for the filtering alone.
    public let endMs: Int64?

    /// The literal `busy` on a shared screen; `(no title)` for a blank one.
    public let title: String

    /// **Never on a shared screen**, and nil for an empty one.
    public let location: String?

    /// Under way now — the brand `now` pill. Never for an all-day event.
    public let running: Bool

    /// Drawn as `all day` in the time column.
    public let allDay: Bool

    /// The calendar's own colour (`0xAARRGGBB`) for the row's dot; nil for
    /// the household's own events, which get none — the one accent stays
    /// amber.
    public let dot: Int64?

    public init(
        event: HomeEvent,
        startMs: Int64,
        endMs: Int64?,
        title: String,
        location: String?,
        running: Bool,
        allDay: Bool = false,
        dot: Int64? = nil
    ) {
        self.event = event
        self.startMs = startMs
        self.endMs = endMs
        self.title = title
        self.location = location
        self.running = running
        self.allDay = allDay
        self.dot = dot
    }
}

/// `today`, `tomorrow` or a day (`thu 1 oct`) and its rows (`TodayDay`,
/// `HomeModels.kt:650`).
public struct TodayDay: Equatable, Sendable {
    public let label: String
    public let rows: [TodayRow]

    public init(label: String, rows: [TodayRow]) {
        self.label = label
        self.rows = rows
    }
}

/// `todayDays(events, nowMs, zone, shared, limit)` (`HomeModels.kt:669`).
///
/// The events still to come or under way in **the coming week** (from now to
/// local midnight seven days on), across the household's calendar and every
/// linked and phone calendar, soonest first, **capped**, and only then
/// grouped under `today`, `tomorrow` or the day (`thu 1 oct`). An event under
/// way since before today sits under today.
///
/// ### The cap comes before the grouping, and that is the rule
///
/// "The cap is applied before the grouping, so a day whose every row was cut
/// leaves no bare label behind" — pinned on Android by
/// `aPhonesThreeRowsNeverLeaveABareDayLabel` and here by the test of the same
/// name. Cap each day instead and a phone with three events today shows
/// `today` with its three rows and then the word `tomorrow` with nothing
/// under it.
///
/// - Parameters:
///   - shared: a shared screen gets the times and the literal `busy` for
///     every calendar, and the location is dropped **entirely** rather than
///     blanked.
///   - limit: ``homePhoneRows`` on a phone, ``homeNextRows`` otherwise.
public func todayDays(
    events: [HomeEvent],
    nowMs: Int64,
    timeZone: TimeZone,
    shared: Bool,
    limit: Int = homeNextRows
) -> [TodayDay] {
    let d1 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 1)
    let d2 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 2)
    let d7 = dayStartMs(ms: nowMs, timeZone: timeZone, plusDays: 7)
    let shown: [HomeEvent] = Array(
        stableAscendingByStart(events, timeZone: timeZone)
            .filter { (e: HomeEvent) -> Bool in
                eventStartMs(e, timeZone: timeZone) < d7 && eventEndMs(e, timeZone: timeZone) > nowMs
            }
            .prefix(limit)
    )

    func row(_ event: HomeEvent) -> TodayRow {
        let start = eventStartMs(event, timeZone: timeZone)
        let blank = event.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let place: String? = event.location.flatMap { (p: String) -> String? in p.isEmpty ? nil : p }
        return TodayRow(
            event: event,
            startMs: start,
            endMs: event.allDay ? nil : isoMs(event.endsAt),
            title: shared ? HomeTodayStrings.busy : (blank ? HomeTodayStrings.noTitle : event.title),
            location: shared ? nil : place,
            running: !event.allDay && start <= nowMs && nowMs < eventEndMs(event, timeZone: timeZone),
            allDay: event.allDay,
            dot: homeHexColor(event.calendarColor)
        )
    }

    // An event under way since before today sits under today.
    func label(_ start: Int64) -> String {
        if start < d1 { return HomeTodayStrings.today }
        if start < d2 { return HomeTodayStrings.tomorrow }
        return homeDate(ms: start, timeZone: timeZone)
    }

    // Kotlin's `groupBy`, which keeps the order each key first appears in —
    // start order, since the rows are sorted. A `Dictionary(grouping:)` would
    // not.
    var order: [String] = []
    var grouped: [String: [TodayRow]] = [:]
    for event in shown {
        let key = label(eventStartMs(event, timeZone: timeZone))
        if grouped[key] == nil { order.append(key) }
        grouped[key, default: []].append(row(event))
    }
    return order.map { (key: String) -> TodayDay in TodayDay(label: key, rows: grouped[key] ?? []) }
}

/// Kotlin's `sortedBy`, which is **stable**; Swift's `sorted(by:)` is not
/// guaranteed to be.
///
/// Two events at the same minute is the ordinary case, not an edge one — a
/// calendar full of 9:00 entries — and an unstable sort would reorder them
/// between redraws, and could change *which* of them the cap keeps.
func stableAscendingByStart(_ events: [HomeEvent], timeZone: TimeZone) -> [HomeEvent] {
    events.enumerated()
        .sorted { left, right in
            let a = eventStartMs(left.element, timeZone: timeZone)
            let b = eventStartMs(right.element, timeZone: timeZone)
            return a == b ? left.offset < right.offset : a < b
        }
        .map { $0.element }
}

// MARK: - the strings

/// Today / next's strings, verbatim from `ui/screens/home/` (map §6.6).
/// Pinned against the Kotlin on disk by `ShellStringsTests`.
public enum HomeTodayStrings {

    /// The card title (`HomeSections.kt:918`).
    public static let title = "today / next"

    /// The first day label (`HomeModels.kt:705`).
    public static let today = "today"

    /// The header link (`HomeSections.kt:918`).
    public static let calendar = "calendar"

    /// `HomeModels.kt:706`.
    public static let tomorrow = "tomorrow"

    /// Every event's title on a shared screen (`HomeModels.kt:690`).
    public static let busy = "busy"

    /// A blank title, everywhere else (`HomeModels.kt:690`).
    public static let noTitle = "(no title)"

    /// An all-day row's time column (`HomeSections.kt:954`).
    public static let allDay = "all day"

    /// The running-event pill (`HomeSections.kt:983`).
    public static let now = "now"

    /// `HomeSections.kt:925`.
    public static let calendarUnavailable = "calendar unavailable"

    /// `HOME_CAL_EMPTY` (`HomeModels.kt:619`) — the coming week is empty.
    public static let calendarEmpty = "nothing on the calendar this week"
}
