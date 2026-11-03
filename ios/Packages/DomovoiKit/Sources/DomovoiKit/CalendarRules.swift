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
// All of this runs on **one unparameterised read** of the whole list
// (`CalendarScreen.kt:84`, DECISIONS §7): the server's default is the first
// 500 events by `starts_at`, and every view's window is cut out of that list
// client-side. So stepping a month costs no round trip — and a house with
// more than 500 events shows only the oldest 500, which is the cost and is
// written down.
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
/// A month, a day, or **seven days for the agenda** — the agenda steps a week
/// at a time, not a day, which is Android's `CalView.Week, CalView.Agenda ->
/// anchor.plusDays(7 * dir)`. Week's branch folds into agenda's here because
/// the two did the same thing and there is no week view (``CalendarViewMode``).
public func calendarStep(
    _ anchor: CalendarDay,
    mode: CalendarViewMode,
    by direction: Int
) -> CalendarDay {
    switch mode {
    case .month: return anchor.adding(months: direction)
    case .agenda: return anchor.adding(days: 7 * direction)
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
/// * **agenda** — every event whose **end** is not before the anchor's
///   midnight, sorted by start, and the **first 25**. The cap is Android's
///   `.take(25)`, not a page: the list is already in memory and the cap is
///   what keeps the agenda from being five hundred rows.
///
/// The agenda's floor is ``calendarEndMs(_:)``, which falls back to the
/// **start** — so an event with no `ends_at` drops out the moment it starts,
/// where Home's card keeps it an hour. Android's rule, raised in DECISIONS §8.
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
    case .agenda:
        let floor = anchor.startMs(in: timeZone)
        return events
            .filter { calendarEndMs($0) >= floor }
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
///   the offset is clamped into `[0, GRID_HOURS * 60]`;
/// * a block is at least **20 minutes** tall, so an event with no `ends_at`
///   (whose end is its start, ``calendarEndMs(_:)``) is still a visible block
///   rather than a hairline.
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
    let rawDuration = Int((calendarEndMs(entry) - calendarStartMs(entry)) / 60_000)

    let offset = min(max(startMinutes, 0), gridMinutes)
    let height = min(max(rawDuration, 20), gridMinutes - offset)
    return (offset, max(height, 20))
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
