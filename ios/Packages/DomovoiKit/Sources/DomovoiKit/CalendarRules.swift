import Foundation

// ---------------------------------------------------------------------------
// The screen's date arithmetic — `CalendarModels.kt:54-96` and
// `CalendarScreen.kt:338-341`, as pure functions.
//
// Every one of them takes its `TimeZone` and, where it needs one, its `nowMs`,
// rather than reading the clock: the rule `relTime(_:nowMs:)` follows, and the
// reason is that "the agenda floor is the anchor's midnight" cannot be tested
// at a DST boundary or at the turn of a month by a function that asks
// Foundation what time it is.
//
// It used to run on **one unparameterised read** of the whole list
// (`CalendarScreen.kt:84`, DECISIONS §7) — the server's default first 500
// events by `starts_at`, every view's window cut out of that list
// client-side. **Kamron's answer 16 (2026-11-03): "yes: ask the server for a
// window per view (`?start=&end=`)."** So the read is now bounded to the
// visible range (``calendarWindow(mode:anchor:in:)``) and the filters below
// still run over whatever arrived — they are the window the *view* draws,
// which is not the same thing as the window the *read* asks for, and keeping
// them separate is what lets a widened read serve two ranges.
// ---------------------------------------------------------------------------

// MARK: - the week, and the month's grid

/// `startOfWeekSun(d)` (`CalendarModels.kt:55`) — **the week starts Sunday**,
/// like the dashboard page this screen ports.
///
/// Not `Calendar.firstWeekday`, which is the device locale's answer and is
/// Monday in most of Europe: the house's dashboard draws a Sunday-first grid
/// and a phone that drew a Monday-first one would disagree with it about which
/// cell an event is in.
public func calendarWeekStart(_ day: CalendarDay) -> CalendarDay {
    day.adding(days: -day.weekdayIndex)
}

/// `monthGridStart(anchor)` (`CalendarModels.kt:58`) — the Sunday on or before
/// the 1st, which is the first of the grid's 42 cells.
public func calendarMonthGridStart(_ anchor: CalendarDay) -> CalendarDay {
    calendarWeekStart(anchor.firstOfMonth)
}

/// `stepAnchor(view, anchor, dir)` (`CalendarModels.kt:61-65`) — what a
/// chevron does, per view.
///
/// A month, a day, or **seven days for the week and the agenda** — which is
/// Android's own `CalView.Week, CalView.Agenda -> anchor.plusDays(7 * dir)`,
/// one branch for the two of them over there and one here.
public func calendarStep(
    _ anchor: CalendarDay,
    mode: CalendarViewMode,
    by direction: Int
) -> CalendarDay {
    switch mode {
    case .month: return anchor.adding(months: direction)
    case .agenda, .week: return anchor.adding(days: 7 * direction)
    case .day: return anchor.adding(days: direction)
    }
}

// MARK: - the window

/// `visibleEvents(view, anchor, events)` (`CalendarModels.kt:68-96`) — the
/// events one view shows, cut out of the whole list.
///
/// The window is on `starts_at`'s **local date**, which is why the zone is a
/// parameter: an event at 23:30 is on a different day in two zones, and the
/// grid has to agree with the clock the row shows.
///
/// * **month** — the 42-cell grid, `[gridStart, gridStart + 42)`. Forty-two
///   and not "the month": the grid's first and last rows spill into the
///   neighbouring months and those cells carry marks too.
/// * **day** — that date exactly.
/// * **week** — `[weekStart, weekStart + 7)`, the Sunday-first week
///   (``calendarWeekStart(_:)``). Android's own `CalView.Week` branch
///   (`CalendarModels.kt:82-89`), which it feeds to a seven-column grid and
///   this port feeds to seven day groups (answer 14).
/// * **agenda** — every event whose `starts_at` parses and whose **end** is
///   not before the anchor's midnight, sorted by start, and the **first 25**. The cap is Android's
///   `.take(25)`, not a page: the list is already in memory and the cap is
///   what keeps the agenda from being five hundred rows.
///
/// The agenda's floor is measured against ``calendarEndMs(_:)``, which gives
/// an event with no `ends_at` **an hour** — Home's `eventEndMs` rule, adopted
/// here by Kamron's answer 17. It used to fall back to the start, which is
/// Android's rule and dropped such an event the instant it began while Home's
/// card beside it kept it.
///
/// Sorted by the parsed instant and then by `id`, where Android sorts by the
/// `starts_at` **string** (`:94`). Lexical order over ISO strings is only the
/// instant order while every row carries the same offset, which is true of
/// this route today and is not a property of ISO 8601; and the id tiebreak
/// makes the order total, so two events at the same minute do not swap places
/// between reads.
public func calendarVisibleEvents(
    mode: CalendarViewMode,
    anchor: CalendarDay,
    events: [CalendarEntry],
    in timeZone: TimeZone
) -> [CalendarEntry] {
    switch mode {
    case .month:
        let gridStart = calendarMonthGridStart(anchor)
        let gridEnd = gridStart.adding(days: 42)
        return events.filter { entry in
            let day = calendarLocalDay(entry, in: timeZone)
            return day >= gridStart && day < gridEnd
        }
    case .day:
        return events.filter { calendarLocalDay($0, in: timeZone) == anchor }
    case .week:
        let weekStart = calendarWeekStart(anchor)
        let weekEnd = weekStart.adding(days: 7)
        return events
            .filter { entry in
                let day = calendarLocalDay(entry, in: timeZone)
                return day >= weekStart && day < weekEnd
            }
            // Sorted, where Android's week branch is not: it hands its rows to
            // a grid that positions each one by its own clock, so their order
            // in the list never shows. These become seven groups of rows read
            // top to bottom, so the order is the thing on screen — the same
            // sort the agenda and the month cells use, and total for the same
            // reason.
            .sorted { ($0.startMsSort, $0.id) < ($1.startMsSort, $1.id) }
    case .agenda:
        let floor = anchor.startMs(in: timeZone)
        return events
            // The sentinel is excluded explicitly: this window has a floor
            // and no ceiling, so `Int64.max >= floor` is true and an
            // unreadable `starts_at` would otherwise be kept, sort last, and
            // group under 1970-01-01 — a junk trailing day header. Month and
            // day view drop it through their own predicates.
            .filter { calendarStartMs($0) != Int64.max && calendarEndMs($0) >= floor }
            .sorted { ($0.startMsSort, $0.id) < ($1.startMsSort, $1.id) }
            .prefix(25)
            .map { $0 }
    }
}

/// The local date an event starts on — `e.startLocal().toLocalDate()`, which
/// every window above and the agenda's grouping are keyed on.
public func calendarLocalDay(_ entry: CalendarEntry, in timeZone: TimeZone) -> CalendarDay {
    CalendarDay(ms: calendarStartMs(entry), in: timeZone)
}

private extension CalendarEntry {
    /// ``calendarStartMs(_:)``, named so the sort above reads as a sort.
    var startMsSort: Int64 { calendarStartMs(self) }
}

// MARK: - the agenda's day groups

/// `AgendaList`'s `groupBy { startLocal().toLocalDate() }` plus its header
/// branch (`CalendarScreen.kt:338-357`), as values.
///
/// Groups come out in the **order the rows do**, which is start order after
/// ``calendarVisibleEvents(mode:anchor:events:in:)``, because Kotlin's
/// `groupBy` returns a `LinkedHashMap` and the list is already sorted. A
/// `Dictionary(grouping:)` here would be unordered, which is exactly the bug
/// that puts tomorrow's header above today's on one run in three.
///
/// `todayMs` rather than a clock read, so a test can say "and tomorrow's group
/// is not called today" without owning the calendar.
public func calendarAgendaSections(
    _ events: [CalendarEntry],
    todayMs: Int64,
    in timeZone: TimeZone
) -> [CalendarAgendaSection] {
    let today = CalendarDay(ms: todayMs, in: timeZone)
    var order: [CalendarDay] = []
    var rows: [CalendarDay: [CalendarEntry]] = [:]
    for entry in events {
        let day = calendarLocalDay(entry, in: timeZone)
        if rows[day] == nil { order.append(day) }
        rows[day, default: []].append(entry)
    }
    return order.map { day in
        CalendarAgendaSection(
            day: day,
            rows: rows[day] ?? [],
            header: day == today ? CalendarStrings.today : calendarDayLabel(day)
        )
    }
}

// MARK: - the week's seven groups

/// The week view's groups — **seven, always**, one per day of the
/// Sunday-first week, in date order, each carrying the rows that start on it.
///
/// Kamron's answer 14 is "agenda rows under seven day headers", and *seven* is
/// the part that makes this its own function rather than
/// ``calendarAgendaSections(_:todayMs:in:)`` over a week-filtered list: the
/// agenda's groups come from the rows, so a day with nothing on it has no
/// header at all. A week that skipped its empty days would be an agenda with a
/// shorter window — nothing a person could tell apart from the agenda, and not
/// a week. The empty groups carry an empty `rows`, and the view says so in
/// words (``CalendarStrings/nothingOnThisDay``).
///
/// Today's group is headed with the literal `today`, exactly as the agenda's
/// is (`CalendarScreen.kt:348`), and `todayMs` is a parameter for the same
/// reason: a test has to be able to say "and the 5th's group is not called
/// today" without owning the clock.
///
/// The rows are grouped on ``calendarLocalDay(_:in:)`` — `starts_at`'s local
/// date — so an event is under the header whose clock it shows, which is the
/// property the month grid and the agenda are also keyed on.
public func calendarWeekSections(
    _ events: [CalendarEntry],
    anchor: CalendarDay,
    todayMs: Int64,
    in timeZone: TimeZone
) -> [CalendarAgendaSection] {
    let today = CalendarDay(ms: todayMs, in: timeZone)
    let weekStart = calendarWeekStart(anchor)
    var rows: [CalendarDay: [CalendarEntry]] = [:]
    for entry in events {
        rows[calendarLocalDay(entry, in: timeZone), default: []].append(entry)
    }
    return (0..<7).map { index in
        let day = weekStart.adding(days: index)
        return CalendarAgendaSection(
            day: day,
            rows: rows[day] ?? [],
            header: day == today ? CalendarStrings.today : calendarDayLabel(day)
        )
    }
}

// MARK: - the window the READ asks for

/// A half-open span of instants, which is what
/// `GET /api/calendar/events?start=&end=` filters on — `starts_at >= :start`
/// and `starts_at < :end` (`web/backend/api/calendar.py:52-56`).
///
/// Instants and not ``CalendarDay``s, because the route takes instants and
/// because the bounds are local midnights: the same civil date is a different
/// instant in two zones, and the window has to agree with the clock the row
/// shows.
///
/// ``noCeiling`` is `Int64.max` and means "omit `end`", which the route's own
/// docstring offers ("omit ``end`` for everything from ``start`` onwards").
/// Only the agenda uses it, and ``calendarWindow(mode:anchor:in:)`` says why.
public struct CalendarWindow: Equatable, Sendable {

    /// Inclusive, as the route's `>=` is.
    public let startMs: Int64

    /// Exclusive, as the route's `<` is, or ``noCeiling``.
    public let endMs: Int64

    /// What an open-ended window's ``endMs`` is: no `end` parameter is sent.
    public static let noCeiling = Int64.max

    public init(startMs: Int64, endMs: Int64) {
        self.startMs = startMs
        self.endMs = endMs
    }

    public var isOpenEnded: Bool { endMs == Self.noCeiling }

    /// Every instant of `other` is an instant of this one.
    public func contains(_ other: CalendarWindow) -> Bool {
        startMs <= other.startMs && other.endMs <= endMs
    }

    /// The two overlap, or one begins exactly where the other ends.
    ///
    /// This is what decides whether a range change **widens** the read or
    /// replaces it. Stepping a chevron always touches — the ranges are
    /// adjacent or overlapping by construction — and `today` from six months
    /// out does not, which is the difference between "read a little more" and
    /// "read half a year nobody asked for".
    ///
    /// **An open-ended window never touches a bounded one**, which is not a
    /// detail. The union of `[nov 3, ∞)` and `[oct 25, dec 6)` is
    /// `[oct 25, ∞)` — an open-ended read starting two months back, whose
    /// `limit` is then spent on october before it reaches today. One switch
    /// from the agenda to month would have turned every later range into
    /// "already covered" and undone answer 16 in a single tap. A mixed pair
    /// replaces instead: the new range is read on its own.
    public func touches(_ other: CalendarWindow) -> Bool {
        guard isOpenEnded == other.isOpenEnded else { return false }
        return startMs <= other.endMs && other.startMs <= endMs
    }

    /// The smallest window holding both — the span, not the set, so a gap
    /// between two touching windows cannot arise.
    public func union(_ other: CalendarWindow) -> CalendarWindow {
        CalendarWindow(
            startMs: min(startMs, other.startMs),
            endMs: max(endMs, other.endMs)
        )
    }
}

/// How many rows one windowed read asks for — the route's own default
/// (`calendar.py:41`, `ge=1, le=2000`).
///
/// Named rather than left at the server's default so that
/// ``CalendarPage/truncated`` can be worked out at all: a read that came back
/// with exactly this many rows may have been cut, and a cut read must not be
/// reused for a different range.
public let calendarWindowLimit = 500

/// One day of lead-in on the agenda's window — see
/// ``calendarWindow(mode:anchor:in:)``.
public let calendarAgendaLeadInDays = 1

/// The instants one view needs read — Kamron's answer 16, as a function.
///
/// Each view already has a range, moved by the chevrons, and this is that
/// range as the route's two bounds:
///
/// * **day** — `[anchor, anchor + 1 day)`.
/// * **week** — `[weekStart, weekStart + 7 days)`.
/// * **month** — `[gridStart, gridStart + 42 days)`, the 42 cells, not the
///   month: the grid's first and last rows carry marks of their own.
/// * **agenda** — `[anchor midnight − 1 day, no ceiling)`.
///
/// ### The agenda's two oddities, both deliberate
///
/// **No ceiling.** The agenda's own window has a floor and no end
/// (``calendarVisibleEvents(mode:anchor:events:in:)``) — it is "the next 25
/// things" — so there is no honest date to stop at. A horizon of n days would
/// be a number nobody chose, and an agenda that showed 9 rows because the
/// 10th was n+1 days out would look like a bug. `limit` is what bounds this
/// read, and the route is built for exactly it. What the bound buys is the
/// thing that was wrong before: the unparameterised read returned
/// `ORDER BY starts_at ASC LIMIT 500` **from the dawn of time**, so in a house
/// whose oldest 500 events are all in the past the agenda was empty.
///
/// **A day of lead-in.** The agenda keeps an event whose *end* is not before
/// the anchor's midnight, and the route filters on `starts_at`, so an event
/// that began last night and runs past midnight would be read by neither if
/// the window began at midnight. One day back is enough for every event short
/// of a multi-day one, and the rows it over-reads are dropped by the view's
/// own filter. Home takes the opposite choice on the same route and says so
/// (`calendarPath(dayStartMs:timeZone:)`: "an event already under way would
/// drop out of a window that began now") — it has a week of ceiling to spend
/// and no need of a floor below midnight. The cost kept here is the same one
/// named there, pushed back by a day: an event longer than a day that started
/// more than a day before the anchor is not read.
public func calendarWindow(
    mode: CalendarViewMode,
    anchor: CalendarDay,
    in timeZone: TimeZone
) -> CalendarWindow {
    func span(from first: CalendarDay, days: Int) -> CalendarWindow {
        CalendarWindow(
            startMs: first.startMs(in: timeZone),
            endMs: first.adding(days: days).startMs(in: timeZone)
        )
    }
    switch mode {
    case .day:
        return span(from: anchor, days: 1)
    case .week:
        return span(from: calendarWeekStart(anchor), days: 7)
    case .month:
        return span(from: calendarMonthGridStart(anchor), days: 42)
    case .agenda:
        return CalendarWindow(
            startMs: anchor.adding(days: -calendarAgendaLeadInDays).startMs(in: timeZone),
            endMs: CalendarWindow.noCeiling
        )
    }
}

// MARK: - the day grid

/// `START_HOUR` (`CalendarGrids.kt:206`) — the time grid's first hour row.
public let calendarGridStartHour = 6

/// `END_HOUR` (`CalendarGrids.kt:207`) — one past the last row, so the grid
/// is sixteen rows: 6am through 10pm.
public let calendarGridEndHour = 22

/// How many hour rows the day grid draws — `GRID_HOURS` (`:208`).
public let calendarGridHours = calendarGridEndHour - calendarGridStartHour

/// Where one event sits in the day grid, in **minutes from the top** and
/// minutes tall — `CalendarGrids.kt:312-316`.
///
/// Two clamps, both Android's and both worth naming because they are the
/// reason an event is never missing from this view:
///
/// * an event before 6am or after 10pm is **pinned to the edge**, not hidden —
///   the offset is clamped into `[0, GRID_HOURS * 60 - 20]`, which leaves
///   room for the twenty the next clamp asks for;
/// * a block is at least **20 minutes** tall, so an event with no `ends_at`
///   (whose end is its start, ``calendarBlockEndMs(_:)``) is still a visible
///   block rather than a hairline.
///
/// A view multiplies these by its `@ScaledMetric` hour height; the rule
/// itself is in minutes so it has no point size in it to go stale.
public func calendarDayGridPlacement(
    _ entry: CalendarEntry,
    on day: CalendarDay,
    in timeZone: TimeZone
) -> (offsetMinutes: Int, heightMinutes: Int) {
    let dayStart = day.startMs(in: timeZone)
    let gridTop = dayStart + Int64(calendarGridStartHour) * 3_600_000
    let gridMinutes = calendarGridHours * 60

    let startMinutes = Int((calendarStartMs(entry) - gridTop) / 60_000)
    // ``calendarBlockEndMs(_:)`` and not ``calendarEndMs(_:)``: the latter
    // now invents an hour for an open-ended event (answer 17), which is the
    // AGENDA's floor and not a block height. Through it, `night walk` at
    // 23:15 with no `ends_at` would be drawn an hour tall instead of the
    // twenty minutes Android's own minimum gives it.
    let rawDuration = Int((calendarBlockEndMs(entry) - calendarStartMs(entry)) / 60_000)

    // `gridMinutes - 20`, not `gridMinutes`: Android's own cap is
    // `coerceIn(0, GRID_HOURS * 60 - 20)` (`CalendarGrids.kt:313`), and the
    // twenty it leaves is what the block's own minimum needs. Capped at
    // `gridMinutes` instead, an event at 22:00 or later landed at offset 960
    // — a block positioned at exactly the grid's full height, so below the
    // hours, below the scroll view's measured content, and neither visible
    // nor tappable. With this cap `gridMinutes - offset` is never under 20,
    // which is why the height needs no rescue of its own.
    let offset = min(max(startMinutes, 0), gridMinutes - 20)
    let height = min(max(rawDuration, 20), gridMinutes - offset)
    return (offset, height)
}

/// Where the `now` rule goes, in minutes from the grid's top, or nil when now
/// is not on this day or is outside the grid's hours
/// (`CalendarGrids.kt:359-376`).
///
/// Nil rather than a clamped edge, which is the difference between this and
/// ``calendarDayGridPlacement(_:on:in:)``: an event clamped to the top is
/// still that event, but a `now` line clamped to the top would say it is 6am.
public func calendarNowOffsetMinutes(
    nowMs: Int64,
    on day: CalendarDay,
    in timeZone: TimeZone
) -> Int? {
    guard CalendarDay(ms: nowMs, in: timeZone) == day else { return nil }
    let gridTop = day.startMs(in: timeZone) + Int64(calendarGridStartHour) * 3_600_000
    let minutes = Int((nowMs - gridTop) / 60_000)
    guard minutes >= 0, minutes <= calendarGridHours * 60 else { return nil }
    return minutes
}

// MARK: - the month grid

/// How many marks one month cell draws before it starts counting —
/// Android's three chips (`CalendarGrids.kt:139`).
public let calendarMonthCellMarks = 3

/// One cell of the month grid: which date, whether it is in the anchor's
/// month, and the events that start on it.
///
/// **Dots and a count, not title chips.** Android draws up to three chips of
/// a dot plus the title at 10sp and `+N more` at 9sp (`:148-199`). Seven
/// cells across a 390pt phone is ~53pt each, which cannot hold a word that
/// scales, and a chip is not a 44pt target — so this port's cell is the day
/// number, up to ``calendarMonthCellMarks`` dots, and
/// ``CalendarStrings/moreCount(_:)``. The cell's own tap opens that day, which
/// is what Android's cell already does (`:120`), and selecting an event
/// happens in agenda and day where a row is full width. DECISIONS §4.
public struct CalendarMonthCell: Equatable, Sendable, Identifiable {

    public let day: CalendarDay
    public let inMonth: Bool
    public let events: [CalendarEntry]

    public var id: String { day.iso }

    /// The dots drawn, at most ``calendarMonthCellMarks``.
    public var marks: [CalendarEntry] { Array(events.prefix(calendarMonthCellMarks)) }

    /// How many are not drawn — 0 when they all are.
    public var overflow: Int { max(0, events.count - calendarMonthCellMarks) }

    public init(day: CalendarDay, inMonth: Bool, events: [CalendarEntry]) {
        self.day = day
        self.inMonth = inMonth
        self.events = events
    }
}

/// The 42 cells of the month grid, in reading order — `CalendarGrids.kt:80-98`.
///
/// Six rows of seven, always, starting at ``calendarMonthGridStart(_:)``: a
/// grid whose row count varied with the month would move every cell under the
/// finger between one month and the next.
///
/// Each cell's events are the ones starting on that date, in start order —
/// `byDay` at `:63-67`, whose `sortedBy { it.starts_at }` is
/// ``calendarVisibleEvents(mode:anchor:events:in:)``'s sort for the reason
/// given there.
public func calendarMonthCells(
    anchor: CalendarDay,
    events: [CalendarEntry],
    in timeZone: TimeZone
) -> [CalendarMonthCell] {
    let gridStart = calendarMonthGridStart(anchor)
    var byDay: [CalendarDay: [CalendarEntry]] = [:]
    for entry in events {
        byDay[calendarLocalDay(entry, in: timeZone), default: []].append(entry)
    }
    for key in byDay.keys {
        byDay[key] = byDay[key]?.sorted { ($0.startMsSort, $0.id) < ($1.startMsSort, $1.id) }
    }
    return (0..<42).map { index in
        let day = gridStart.adding(days: index)
        return CalendarMonthCell(
            day: day,
            inMonth: day.month == anchor.month && day.year == anchor.year,
            events: byDay[day] ?? []
        )
    }
}
