import Foundation

// ---------------------------------------------------------------------------
// Calendar's wire shape, its civil date, its view modes and its draft — the
// port of `ui/screens/calendar/CalendarModels.kt` (178 lines, read at the
// worktree's tip) and `web/backend/schemas.py:826-852`.
//
// Everything here is a value: no clock is read inside any function, which is
// the rule `relTime(_:nowMs:)` already follows and the reason the whole of
// §2.4's date math can be pinned by a package test rather than waited for.
// ---------------------------------------------------------------------------

// MARK: - the event

/// One row of `GET /api/calendar/events` — `CalendarEvent`
/// (`schemas.py:826`), all nine fields.
///
/// **Why this is not ``HomeEvent``.** `HomeEvent` (`HomeModels.swift:680`)
/// decodes five keys, because Home shows five and "the model is the privacy
/// boundary" over there. This screen's detail sheet reads four more —
/// `description`, `source`, `external_id`, `last_synced_at` — and
/// `HomeModels.swift` belongs to Home, which this item may not edit
/// (NIGHT-RULES §2). Two decoders of one server shape is a real cost and is
/// written down rather than left to be found: DECISIONS §1 holds the shared
/// change that folds them back into one.
///
/// Decode-only, like every read model in this module: nothing here is encoded
/// back. What a write sends is ``CalendarDraft`` through
/// ``CalendarModel/save(_:)``, and the server chooses `source` and
/// `created_via` itself (`calendar.py:105`), so a client could not send them
/// if it tried.
///
/// Every key is `decodeIfPresent ?? default`, exactly as `HomeEvent` is: a row
/// missing a field the house has not written yet must not fail the whole
/// array's decode and blank the screen.
public struct CalendarEntry: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int64
    public let title: String

    /// `TIMESTAMPTZ`, so it arrives with an offset
    /// (`V001__baseline.sql:229`). Kept as the server's string and parsed
    /// through ``isoMs(_:)`` where it is needed, which is what `HomeEvent`
    /// does and what keeps one parser in the module.
    public let startsAt: String
    public let endsAt: String?
    public let location: String?
    public let description: String?

    /// `'google'` or `'local'` — the column's check constraint
    /// (`V001__baseline.sql:237`). Nil for a row older than the column.
    public let source: String?
    public let externalID: String?
    public let lastSyncedAt: String?

    public init(
        id: Int64 = 0,
        title: String = "",
        startsAt: String = "",
        endsAt: String? = nil,
        location: String? = nil,
        description: String? = nil,
        source: String? = nil,
        externalID: String? = nil,
        lastSyncedAt: String? = nil
    ) {
        self.id = id
        self.title = title
        self.startsAt = startsAt
        self.endsAt = endsAt
        self.location = location
        self.description = description
        self.source = source
        self.externalID = externalID
        self.lastSyncedAt = lastSyncedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, title, location, description, source
        case startsAt = "starts_at"
        case endsAt = "ends_at"
        case externalID = "external_id"
        case lastSyncedAt = "last_synced_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        startsAt = try c.decodeIfPresent(String.self, forKey: .startsAt) ?? ""
        endsAt = try c.decodeIfPresent(String.self, forKey: .endsAt)
        location = try c.decodeIfPresent(String.self, forKey: .location)
        description = try c.decodeIfPresent(String.self, forKey: .description)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        externalID = try c.decodeIfPresent(String.self, forKey: .externalID)
        lastSyncedAt = try c.decodeIfPresent(String.self, forKey: .lastSyncedAt)
    }

    /// `isGoogle()` (`CalendarModels.kt:28`) — what decides the colour bar,
    /// the pill's tone and whether ``CalendarStrings/synced(_:)`` is shown.
    public var isGoogle: Bool { source == "google" }

    /// The pill's word: the server's `source`, or `local` for a row that
    /// predates the column (`CalendarScreen.kt:426`).
    public var sourceLabel: String {
        guard let source, !source.isEmpty else { return CalendarStrings.localSource }
        return source
    }
}

// MARK: - when an event is

/// `startLocal()` (`CalendarModels.kt:33`) as epoch milliseconds.
///
/// **`Int64.max` for a timestamp that will not parse**, which is
/// ``eventStartMs(_:)``'s choice: an unreadable time sorts last and is drawn
/// nowhere, rather than drawn at the epoch.
///
/// Being outside the window is only *half* of why it disappears, and the half
/// that does not carry here. Month and day view compare the sentinel's date
/// against the anchor and so drop it; the agenda's window has a floor and no
/// ceiling, so `Int64.max` passes it — and `CalendarDay(ms: Int64.max, …)`
/// falls out of the Gregorian component range and groups under 1970. The
/// agenda therefore drops the sentinel **by name**
/// (``calendarVisibleEvents(mode:anchor:events:in:)``) and not by its window.
///
/// **A deliberate divergence from Android**, which falls back to
/// `LocalDateTime.now()` (`:34`) — so over there an event whose `starts_at`
/// the phone cannot read appears *today*, in whatever view is open, at
/// whatever time it happens to be. That is worse than invisible and it is not
/// copied. It also cannot be copied here without handing a clock to a pure
/// function, which is the thing this file does not do.
public func calendarStartMs(_ entry: CalendarEntry) -> Int64 {
    isoMs(entry.startsAt) ?? Int64.max
}

/// The end a **window** measures an event against — ``eventEndMs(_:)``'s rule,
/// line for line: the server's `ends_at`, or **an hour after the start** when
/// there is none.
///
/// ### Why this is now Home's rule and not Android's
///
/// It used to fall back to the *start*, which is Android's `endLocal()`
/// (`CalendarModels.kt:36`) and what its agenda floor filters on (`:91`) — so
/// an event with no `ends_at` dropped out of the agenda the moment its start
/// passed, while Home's card beside it kept the same event for an hour. Two
/// rules for one row, raised as DECISIONS §8.
///
/// **The owner's answer 17 (2026-11-03): "yes: match Home, keep an open-ended
/// event on screen for an hour."** So this is `HomeToday.swift`'s
/// ``eventEndMs(_:)``, copied rather than approximated: the server's end if it
/// parses, else `start + 3_600_000`, and the `Int64.max` sentinel stays itself
/// so an unreadable `starts_at` cannot be turned into a real instant by
/// addition.
///
/// It is the **window's** end, not the drawn one: ``calendarBlockEndMs(_:)`` is
/// what a clock or a day-grid block uses, and it still falls back to the start,
/// because Home keeps the same split (`TodayRow.endMs` is nil when the server
/// gave no end — "the hour exists for the filtering alone"). An invented hour
/// that reached the screen would draw `3:00pm – 4:00pm` on a row the house
/// never gave an end for.
public func calendarEndMs(_ entry: CalendarEntry) -> Int64 {
    if let end = isoMs(entry.endsAt) { return end }
    let start = calendarStartMs(entry)
    return start == Int64.max ? Int64.max : start + 3_600_000
}

/// `endLocal()` (`CalendarModels.kt:36`) — **the start when there is no end**,
/// for the two places that draw an end rather than filter on one.
///
/// The day grid's block height (``calendarDayGridPlacement(_:on:in:)``) and the
/// `9:00am – 10:00am` range (``calendarRange(_:in:)``) both have to say what
/// the house said. Android's 20-minute minimum block exists precisely because
/// this fallback makes an open-ended event zero minutes long
/// (`CalendarGrids.kt:314`), and answer 17 was about keeping such an event on
/// the agenda, not about growing its block to an hour — so the drawn end is
/// left exactly as Android has it.
public func calendarBlockEndMs(_ entry: CalendarEntry) -> Int64 {
    if let end = isoMs(entry.endsAt) { return end }
    return calendarStartMs(entry)
}

// MARK: - a civil date

/// A date with no time and no zone — Java's `LocalDate`, which the whole of
/// `CalendarModels.kt`'s arithmetic is written in.
///
/// It exists because the screen's anchor is a *date*, not an instant: "the
/// month of November" and "the 4th" are civil facts, and stepping a month
/// from the 31st or across a daylight-saving change is arithmetic Foundation
/// will do correctly on components and incorrectly on `86_400 * n`.
///
/// ``adding(days:)`` and ``adding(months:)`` run in a **GMT** calendar on
/// purpose: civil arithmetic has no zone, so doing it in the device's zone
/// would make "tomorrow" depend on where the phone is. The zone appears only
/// where a date meets an instant — ``init(ms:in:)`` and ``startMs(in:)``.
public struct CalendarDay: Hashable, Comparable, Sendable {

    public let year: Int
    public let month: Int
    public let day: Int

    public init(year: Int, month: Int, day: Int) {
        self.year = year
        self.month = month
        self.day = day
    }

    /// The local date an instant falls on, in `timeZone`.
    public init(ms: Int64, in timeZone: TimeZone) {
        let parts = Self.zoned(timeZone).dateComponents(
            [.year, .month, .day],
            from: Date(timeIntervalSince1970: Double(ms) / 1000)
        )
        self.init(year: parts.year ?? 1970, month: parts.month ?? 1, day: parts.day ?? 1)
    }

    /// Local midnight of this date, as epoch milliseconds — `atStartOfDay`.
    public func startMs(in timeZone: TimeZone) -> Int64 {
        var parts = DateComponents()
        parts.year = year
        parts.month = month
        parts.day = day
        let calendar = Self.zoned(timeZone)
        guard let date = calendar.date(from: parts) else { return 0 }
        return Int64((calendar.startOfDay(for: date).timeIntervalSince1970 * 1000).rounded())
    }

    /// Local `hour:minute` of this date, as epoch milliseconds — `atTime`.
    public func at(hour: Int, minute: Int, in timeZone: TimeZone) -> Int64 {
        var parts = DateComponents()
        parts.year = year
        parts.month = month
        parts.day = day
        parts.hour = hour
        parts.minute = minute
        let calendar = Self.zoned(timeZone)
        // A wall-clock time that does not exist (the hour a spring-forward
        // skips) is resolved by Foundation to the next one that does, which is
        // `ZonedDateTime`'s rule too. Nil is unreachable for a valid y/m/d and
        // falls back to midnight rather than to the epoch.
        guard let date = calendar.date(from: parts) else { return startMs(in: timeZone) }
        return Int64((date.timeIntervalSince1970 * 1000).rounded())
    }

    /// `plusDays` — civil, so a week is seven dates and not 604 800 seconds.
    public func adding(days: Int) -> CalendarDay {
        shifted(.day, by: days)
    }

    /// `plusMonths` — which clamps: a month after 31 january is 28 february,
    /// as `LocalDate.plusMonths` does and as Foundation's components do.
    public func adding(months: Int) -> CalendarDay {
        shifted(.month, by: months)
    }

    /// `withDayOfMonth(1)`.
    public var firstOfMonth: CalendarDay {
        CalendarDay(year: year, month: month, day: 1)
    }

    /// 0 for Sunday through 6 for Saturday — Java's `dayOfWeek.value % 7`
    /// (`CalendarModels.kt:56`), which is what makes the week start on Sunday.
    ///
    /// Foundation's `.weekday` is already Sunday-first (1 = Sunday), so this
    /// is a subtraction rather than the shift ``homeDate(ms:timeZone:)``
    /// needs — and the off-by-one that would hide here is walked over a whole
    /// week by `CalendarRulesTests`.
    public var weekdayIndex: Int {
        var parts = DateComponents()
        parts.year = year
        parts.month = month
        parts.day = day
        let calendar = Self.zoned(.gmt)
        guard let date = calendar.date(from: parts),
              let weekday = calendar.dateComponents([.weekday], from: date).weekday
        else { return 0 }
        return (weekday - 1) % 7
    }

    /// `LocalDate.toString()` — `2026-11-04`, which the agenda's day header
    /// shows right-aligned (`CalendarScreen.kt:355`).
    public var iso: String {
        String(format: "%04d-%02d-%02d", year, month, day)
    }

    public static func < (lhs: CalendarDay, rhs: CalendarDay) -> Bool {
        (lhs.year, lhs.month, lhs.day) < (rhs.year, rhs.month, rhs.day)
    }

    private func shifted(_ unit: Foundation.Calendar.Component, by amount: Int) -> CalendarDay {
        var parts = DateComponents()
        parts.year = year
        parts.month = month
        parts.day = day
        let calendar = Self.zoned(.gmt)
        guard let date = calendar.date(from: parts),
              let moved = calendar.date(byAdding: unit, value: amount, to: date)
        else { return self }
        let out = calendar.dateComponents([.year, .month, .day], from: moved)
        return CalendarDay(
            year: out.year ?? year,
            month: out.month ?? month,
            day: out.day ?? day
        )
    }

    private static func zoned(_ timeZone: TimeZone) -> Foundation.Calendar {
        var calendar = Foundation.Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar
    }
}

// MARK: - the views

/// The toggle's views — `CalView` (`CalendarModels.kt:43-45`), whose raw
/// values are the lower-case words drawn on it.
///
/// ### `week` is here, and it is not Android's grid
///
/// Android's week is a seven-column time grid of 9–11sp text inside
/// fixed-height blocks (`CalendarGrids.kt:212-286`). Seven columns on a 390pt
/// phone is ~49pt each, and the standing rules require `@ScaledMetric` glyph
/// widths and `fixedSize` on wrapping text and reject `minimumScaleFactor`
/// outright, so that grid has no legible layout at
/// `UICTContentSizeCategoryAccessibilityXXXL`. DECISIONS §3 asked whether the
/// week was wanted as a grouped list instead.
///
/// **The owner's answer 14 (2026-11-03): "build the week view (agenda rows under
/// seven day headers)."** So the week is a week-bounded list — the agenda's own
/// rows, under all seven day headers, from
/// ``calendarWeekSections(_:anchor:todayMs:in:)``
/// — and the arithmetic it steps on is the one that was already ported and
/// tested (``calendarWeekStart(_:)``, ``calendarWeekRangeLabel(_:)``).
///
/// ### The order
///
/// `CaseIterable`'s order is the toggle's order. Android's compact order is
/// agenda, month, week, day (`calViews`, `:51-52`); this port's was already
/// agenda, day, month — widening spans, which is how the three read as a set
/// on one line — and `week` goes where its span puts it, between day and
/// month. One divergence from Android's order, not two.
public enum CalendarViewMode: String, CaseIterable, Sendable {
    case agenda
    case day
    case week
    case month

    /// `CalView.label` (`CalendarModels.kt:43`) — the word on the toggle.
    public var label: String { rawValue }
}

// MARK: - the draft

/// What the editor is editing — `EventDraft` (`CalendarModels.kt:151-158`).
///
/// `start` and `end` are **absolute instants**, not Android's naive
/// `LocalDateTime`. That is the §2.3 decision in the type system: Android
/// formats the draft with `yyyy-MM-dd'T'HH:mm:ss` and no offset, the column is
/// `TIMESTAMPTZ`, and nothing sets a Postgres session zone — so an event
/// created on the phone is stored as though the wall-clock time were UTC and
/// read back shifted by the device's offset. The dashboard Android ports sends
/// `toISOString()` (`calendar.jsx:36`). This port sends the instant, through
/// ``calendarInstant(_:)``, and the Android defect is reported in DECISIONS §2
/// rather than copied.
public struct CalendarDraft: Equatable, Sendable {

    /// Nil for a new event, which is what chooses POST over PATCH
    /// (`CalendarScreen.kt:109-110`).
    public let id: Int64?
    public var title: String
    public var start: Date
    public var end: Date
    public var location: String
    public var description: String

    public init(
        id: Int64? = nil,
        title: String = "",
        start: Date,
        end: Date,
        location: String = "",
        description: String = ""
    ) {
        self.id = id
        self.title = title
        self.start = start
        self.end = end
        self.location = location
        self.description = description
    }

    /// `blankDraft(anchor)` (`CalendarModels.kt:160-165`).
    ///
    /// Nine in the morning, or the current hour when the anchor is today and
    /// it is already later than nine — capped at 23, so a draft opened at
    /// 23:40 starts at 23:00 and not at midnight tomorrow. One hour long.
    ///
    /// `now` and `timeZone` are parameters because `blankDraft` reads
    /// `LocalDateTime.now()` inside itself, and a function that reads the wall
    /// clock cannot have its three branches — before nine, after nine, after
    /// eleven at night — tested without waiting for them.
    public static func blank(
        anchor: CalendarDay,
        nowMs: Int64,
        timeZone: TimeZone
    ) -> CalendarDraft {
        let today = CalendarDay(ms: nowMs, in: timeZone)
        var nowCalendar = Foundation.Calendar(identifier: .gregorian)
        nowCalendar.timeZone = timeZone
        let nowHour = nowCalendar.component(
            .hour,
            from: Date(timeIntervalSince1970: Double(nowMs) / 1000)
        )
        let hour = anchor == today ? max(9, nowHour) : 9
        let startMs = anchor.at(hour: min(hour, 23), minute: 0, in: timeZone)
        let start = Date(timeIntervalSince1970: Double(startMs) / 1000)
        return CalendarDraft(start: start, end: start.addingTimeInterval(3600))
    }

    /// `draftOf(e)` (`CalendarModels.kt:167-178`) — the event, with an end an
    /// hour after the start when it has none.
    ///
    /// An entry whose `starts_at` will not parse gets ``Date`` at
    /// ``calendarStartMs(_:)``'s `Int64.max`, which would be a nonsense date
    /// in the picker, so it is refused instead: nil, and the caller draws no
    /// editor. Android would open the editor on `LocalDateTime.now()` and let
    /// a save overwrite the row's real time with today's.
    public init?(_ entry: CalendarEntry) {
        let startMs = calendarStartMs(entry)
        guard startMs != Int64.max else { return nil }
        let start = Date(timeIntervalSince1970: Double(startMs) / 1000)
        let end = isoMs(entry.endsAt)
            .map { Date(timeIntervalSince1970: Double($0) / 1000) }
            ?? start.addingTimeInterval(3600)
        self.init(
            id: entry.id,
            title: entry.title,
            start: start,
            end: end,
            location: entry.location ?? "",
            description: entry.description ?? ""
        )
    }

    /// Move the start and **keep the duration** (`EventEditor.kt:224-231`).
    ///
    /// A zero or negative duration falls back to one hour, which is the
    /// Kotlin's `if (dur.isZero || dur.isNegative)` — a draft whose end was
    /// dragged before its start does not stay inverted when the start moves.
    public func movingStart(to newStart: Date) -> CalendarDraft {
        let span = end.timeIntervalSince(start)
        var moved = self
        moved.start = newStart
        moved.end = newStart.addingTimeInterval(span > 0 ? span : 3600)
        return moved
    }

    /// `EventEditor.kt:264-266`, both validations, in the Kotlin's order.
    ///
    /// Nil when the draft may be saved. The title is trimmed before the test
    /// and **trimmed in what is sent**, as `:269` does.
    public var refusal: String? {
        if title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return CalendarStrings.titleRequired
        }
        if end <= start { return CalendarStrings.endAfterStart }
        return nil
    }

    /// The body of the POST or the PATCH — `CalendarScreen.kt:100-108`.
    ///
    /// Five keys, always all five, with a blank location or description sent
    /// as **`null`**. That is deliberate on a PATCH: the route is
    /// `exclude_unset` (`calendar.py:146`), so a key that is present and null
    /// **clears** the column, which is what Android's `ifBlank { null }`
    /// does and is the only way the editor can empty a field it filled.
    ///
    /// `source` and `created_via` are not here and could not be: the server
    /// sets them (`calendar.py:105`).
    ///
    /// Written by hand rather than through `JSONEncoder` for
    /// ``MusicModel/json(_:)``'s reason — a test asserts the bytes, and a
    /// dictionary's key order is not a fact.
    public var body: Data {
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let place = location.trimmingCharacters(in: .whitespacesAndNewlines)
        let note = description.trimmingCharacters(in: .whitespacesAndNewlines)
        let text = """
        {"title":\(trimmedTitle.jsonLiteral),\
        "starts_at":\(calendarInstant(start).jsonLiteral),\
        "ends_at":\(calendarInstant(end).jsonLiteral),\
        "location":\(place.isEmpty ? "null" : place.jsonLiteral),\
        "description":\(note.isEmpty ? "null" : note.jsonLiteral)}
        """
        return Data(text.utf8)
    }
}

// MARK: - the agenda's groups

/// One day's worth of agenda rows — `AgendaList`'s `groupBy`
/// (`CalendarScreen.kt:338-341`) as a value, so the grouping is testable
/// without a view.
public struct CalendarAgendaSection: Equatable, Sendable, Identifiable {

    public let day: CalendarDay
    public let rows: [CalendarEntry]

    /// `"today"` for today, else `fmtDayLabel` (`CalendarScreen.kt:348`) —
    /// the branch a header that lost it would silently pass without.
    public let header: String

    /// `day.toString()`, the ISO date the header shows right-aligned.
    public var key: String { day.iso }

    public var id: String { day.iso }

    public init(day: CalendarDay, rows: [CalendarEntry], header: String) {
        self.day = day
        self.rows = rows
        self.header = header
    }
}
