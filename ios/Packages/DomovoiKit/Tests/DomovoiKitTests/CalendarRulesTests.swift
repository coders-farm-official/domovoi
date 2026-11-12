import Foundation
import XCTest
@testable import DomovoiKit

/// The window each view cuts out of the one read, the agenda's groups, and
/// where a block sits in the day grid.
///
/// Nothing over here reads a clock, which is the whole point: "the agenda
/// floor is the anchor's midnight" and "a month is 42 cells from the Sunday
/// before the 1st" are facts that can be pinned at a month end and across the
/// fall-back night, which a function calling `LocalDate.now()` cannot be.
final class CalendarRulesTests: XCTestCase {

    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let utc = TimeZone(secondsFromGMT: 0)!

    private func day(_ year: Int, _ month: Int, _ dayOfMonth: Int) -> CalendarDay {
        CalendarDay(year: year, month: month, day: dayOfMonth)
    }

    /// An event at a **local** wall-clock time in Chicago, written as the
    /// server writes it: an absolute instant with an offset.
    private func local(
        _ id: Int64,
        _ isoWithOffset: String,
        to end: String? = nil,
        title: String? = nil
    ) -> CalendarEntry {
        CalendarEntry(
            id: id,
            title: title ?? "event \(id)",
            startsAt: isoWithOffset,
            endsAt: end
        )
    }

    // MARK: - the week and the grid

    /// The week starts **Sunday**, not the device locale's first weekday.
    func testTheWeekStartsOnSundayWhateverDayTheAnchorIs() {
        // 2026-11-01 is a Sunday; every day of that week collapses onto it.
        for offset in 0..<7 {
            XCTAssertEqual(
                day(2026, 11, 1),
                calendarWeekStart(day(2026, 11, 1).adding(days: offset)),
                "day \(offset) of the week"
            )
        }
        XCTAssertEqual(
            day(2026, 11, 1),
            calendarWeekStart(day(2026, 11, 7)),
            "the saturday is the last day of the same week, not the first of the next"
        )
        XCTAssertEqual(day(2026, 11, 8), calendarWeekStart(day(2026, 11, 8)))
    }

    /// The grid starts at the Sunday **on or before** the 1st, so a month
    /// beginning mid-week shows the tail of the previous one.
    func testTheMonthGridStartsAtTheSundayOnOrBeforeTheFirst() {
        // november 2026 begins on a Sunday, so the grid starts on the 1st.
        XCTAssertEqual(day(2026, 11, 1), calendarMonthGridStart(day(2026, 11, 20)))
        // december 2026 begins on a Tuesday, so the grid starts 29 november.
        XCTAssertEqual(day(2026, 11, 29), calendarMonthGridStart(day(2026, 12, 1)))
        // february 2027 begins on a Monday, so the grid starts 31 january.
        XCTAssertEqual(day(2027, 1, 31), calendarMonthGridStart(day(2027, 2, 14)))
    }

    // MARK: - stepping

    func testEachViewStepsByItsOwnUnit() {
        let anchor = day(2026, 11, 4)
        XCTAssertEqual(day(2026, 12, 4), calendarStep(anchor, mode: .month, by: 1))
        XCTAssertEqual(day(2026, 10, 4), calendarStep(anchor, mode: .month, by: -1))
        XCTAssertEqual(day(2026, 11, 5), calendarStep(anchor, mode: .day, by: 1))
        XCTAssertEqual(
            day(2026, 11, 11),
            calendarStep(anchor, mode: .agenda, by: 1),
            "the agenda steps a week, not a day (`CalendarModels.kt:63`)"
        )
        XCTAssertEqual(day(2026, 10, 28), calendarStep(anchor, mode: .agenda, by: -1))
    }

    /// A month step off the 31st clamps rather than overflowing into the
    /// month after next, and a day step across the fall-back night is one
    /// date — both of which `+ 86_400_000` arithmetic gets wrong.
    func testSteppingOverAMonthEndAndADstBoundary() {
        XCTAssertEqual(day(2026, 2, 28), calendarStep(day(2026, 1, 31), mode: .month, by: 1))
        XCTAssertEqual(day(2026, 11, 1), calendarStep(day(2026, 10, 31), mode: .day, by: 1))
        XCTAssertEqual(
            day(2026, 11, 2),
            calendarStep(day(2026, 11, 1), mode: .day, by: 1),
            "the 25-hour night is one calendar day"
        )
        XCTAssertEqual(day(2026, 12, 6), calendarStep(day(2026, 11, 29), mode: .agenda, by: 1))
    }

    // MARK: - the windows

    /// The 42-cell window, at both ends. The grid's last row spills into
    /// december, so the boundary is `gridStart + 42` and not the month's end.
    func testTheMonthWindowIsTheFortyTwoCellGridAndNotTheMonth() {
        let events = [
            local(1, "2026-10-31T10:00:00-05:00"),   // the day before the grid
            local(2, "2026-11-01T10:00:00-05:00"),   // the grid's first cell
            local(3, "2026-12-12T10:00:00-06:00"),   // the grid's last cell
            local(4, "2026-12-13T10:00:00-06:00"),   // one past it
        ]
        let shown = calendarVisibleEvents(
            mode: .month, anchor: day(2026, 11, 20), events: events, in: chicago
        )
        XCTAssertEqual([2, 3], shown.map(\.id))
    }

    func testTheDayWindowIsThatDateExactly() {
        let events = [
            local(1, "2026-11-03T23:30:00-06:00"),
            local(2, "2026-11-04T00:15:00-06:00"),
            local(3, "2026-11-04T23:45:00-06:00"),
            local(4, "2026-11-05T00:05:00-06:00"),
        ]
        XCTAssertEqual(
            [2, 3],
            calendarVisibleEvents(
                mode: .day, anchor: day(2026, 11, 4), events: events, in: chicago
            ).map(\.id)
        )
    }

    /// The window is on the event's **local** date, so the same list cuts
    /// differently in two zones — which is why the zone is a parameter and not
    /// `.current` read inside.
    func testTheWindowFollowsTheReadersZone() {
        let events = [local(1, "2026-11-05T02:00:00+00:00")]
        XCTAssertEqual(
            [1],
            calendarVisibleEvents(
                mode: .day, anchor: day(2026, 11, 4), events: events, in: chicago
            ).map(\.id)
        )
        XCTAssertTrue(
            calendarVisibleEvents(
                mode: .day, anchor: day(2026, 11, 4), events: events, in: utc
            ).isEmpty
        )
    }

    /// The agenda's floor is the anchor's midnight measured against the
    /// event's **end**, and an open-ended event's end is **an hour after it
    /// starts**.
    ///
    /// ### Why this case turned over
    ///
    /// It used to assert `[1, 3]`: the 23:30 row with no `ends_at` ended at
    /// 23:30 under Android's `endLocal()` and dropped out of the 4th's
    /// agenda. **The owner's answer 17 (2026-11-03): "yes: match Home, keep an
    /// open-ended event on screen for an hour."** Under Home's `eventEndMs`
    /// that row runs to 00:30 on the 4th, so it stays — and row 2 is the
    /// discriminating one in both directions: it is the only row here whose
    /// verdict the rule change moves.
    ///
    /// Row 2 keeps its 23:30 start, so it still **groups under the 3rd**
    /// (``calendarAgendaSections(_:todayMs:in:)`` keys on the local start
    /// date): "on screen for an hour" is about being drawn at all, not about
    /// being moved to another day.
    func testTheAgendaFloorGivesAnOpenEndedEventTheHourHomeGivesIt() {
        let events = [
            local(1, "2026-11-03T22:00:00-06:00", to: "2026-11-04T01:00:00-06:00"),
            local(2, "2026-11-03T23:30:00-06:00"),
            local(3, "2026-11-04T09:00:00-06:00"),
        ]
        let shown = calendarVisibleEvents(
            mode: .agenda, anchor: day(2026, 11, 4), events: events, in: chicago
        )
        XCTAssertEqual(
            [1, 2, 3],
            shown.map(\.id),
            "answer 17: an open-ended 23:30 runs to 00:30 and is still on the 4th's agenda"
        )
        XCTAssertEqual(
            CalendarDay(year: 2026, month: 11, day: 3),
            calendarAgendaSections(shown, todayMs: 0, in: chicago).first?.day,
            "it is kept, not moved: its group is still the 3rd"
        )

        // **And an hour is all it gets.** Row 2's invented end is 00:30 on
        // the 4th, so the 5th's agenda keeps nothing here — a rule that had
        // quietly become "keep an open-ended event for ever" fails this, and
        // so would one that gave it a day.
        XCTAssertTrue(
            calendarVisibleEvents(
                mode: .agenda, anchor: day(2026, 11, 5), events: events, in: chicago
            ).isEmpty,
            "an hour is an hour: nothing from the 3rd or the 4th reaches the 5th"
        )
    }

    /// The agenda is start-ordered and capped at **25**, which is Android's
    /// `.take(25)` and not a page of anything.
    func testTheAgendaIsStartOrderedAndCappedAtTwentyFive() {
        // Thirty events a minute apart, handed over newest-first so the sort
        // has work to do.
        let events: [CalendarEntry] = (1...30).reversed().map { index in
            local(Int64(index), String(format: "2026-11-04T09:%02d:00-06:00", index))
        }
        let shown = calendarVisibleEvents(
            mode: .agenda, anchor: day(2026, 11, 4), events: events, in: chicago
        )
        XCTAssertEqual(25, shown.count)
        XCTAssertEqual(Array(1...25).map(Int64.init), shown.map(\.id))
    }

    /// Two events at the same minute keep a stable order between reads, which
    /// sorting on the start alone does not promise.
    func testEventsAtTheSameMinuteAreOrderedByIdAndNotByArrival() {
        let events = [
            local(9, "2026-11-04T09:00:00-06:00"),
            local(2, "2026-11-04T09:00:00-06:00"),
            local(5, "2026-11-04T09:00:00-06:00"),
        ]
        XCTAssertEqual(
            [2, 5, 9],
            calendarVisibleEvents(
                mode: .agenda, anchor: day(2026, 11, 4), events: events, in: chicago
            ).map(\.id)
        )
    }

    // MARK: - the agenda's groups

    /// Groups come out in **row order**, and today's header is the literal
    /// word while every other day gets its label. A `Dictionary(grouping:)`
    /// would pass the second assertion and fail the first on one run in three.
    func testTheGroupsKeepRowOrderAndOnlyTodaySaysToday() {
        let events = [
            local(1, "2026-11-04T09:00:00-06:00"),
            local(2, "2026-11-04T17:00:00-06:00"),
            local(3, "2026-11-05T07:00:00-06:00"),
            local(4, "2026-11-06T07:00:00-06:00"),
        ]
        let sections = calendarAgendaSections(
            events,
            todayMs: isoMs("2026-11-04T14:30:00-06:00") ?? 0,
            in: chicago
        )
        XCTAssertEqual(["2026-11-04", "2026-11-05", "2026-11-06"], sections.map(\.key))
        XCTAssertEqual([CalendarStrings.today, "thu, nov 5", "fri, nov 6"], sections.map(\.header))
        XCTAssertEqual([[1, 2], [3], [4]], sections.map { $0.rows.map(\.id) })
    }

    /// Today's header is a date comparison, not a "within 24 hours" one: an
    /// event nine hours from now is tomorrow when now is late in the evening.
    func testTomorrowIsNotTodayEvenNineHoursFromNow() {
        let sections = calendarAgendaSections(
            [local(1, "2026-11-05T07:00:00-06:00")],
            todayMs: isoMs("2026-11-04T22:00:00-06:00") ?? 0,
            in: chicago
        )
        XCTAssertEqual(["thu, nov 5"], sections.map(\.header))
    }

    // MARK: - the day grid

    /// The grid is sixteen hour rows, 6am through 10pm
    /// (`CalendarGrids.kt:206-208`).
    func testTheGridIsSixteenHoursFromSixToTwentyTwo() {
        XCTAssertEqual(6, calendarGridStartHour)
        XCTAssertEqual(22, calendarGridEndHour)
        XCTAssertEqual(16, calendarGridHours)
    }

    func testAnEventInsideTheHoursIsPlacedByItsMinutes() {
        let entry = local(1, "2026-11-04T11:00:00-06:00", to: "2026-11-04T12:00:00-06:00")
        let placed = calendarDayGridPlacement(entry, on: day(2026, 11, 4), in: chicago)
        XCTAssertEqual(300, placed.offsetMinutes, "five hours below the 6am line")
        XCTAssertEqual(60, placed.heightMinutes)

        let halfPast = local(2, "2026-11-04T11:30:00-06:00", to: "2026-11-04T11:45:00-06:00")
        XCTAssertEqual(
            330,
            calendarDayGridPlacement(halfPast, on: day(2026, 11, 4), in: chicago).offsetMinutes
        )
    }

    /// **An event before 6am is clamped to the top, not dropped.** A 05:30
    /// event that simply vanished would be the one thing on this view nobody
    /// could explain.
    func testAnEventBeforeSixIsPinnedToTheTopAndNotMissing() {
        let early = local(10, "2026-11-04T05:30:00-06:00", to: "2026-11-04T06:30:00-06:00")
        let placed = calendarDayGridPlacement(early, on: day(2026, 11, 4), in: chicago)
        XCTAssertEqual(0, placed.offsetMinutes)
        XCTAssertEqual(60, placed.heightMinutes)
    }

    /// After 10pm it is pinned to the bottom, and an event with no `ends_at`
    /// still gets the **20-minute minimum** rather than a zero-height block.
    func testAnEventAfterTenAndOneWithNoEndStillGetATwentyMinuteBlock() {
        let late = local(11, "2026-11-04T23:15:00-06:00")
        let placed = calendarDayGridPlacement(late, on: day(2026, 11, 4), in: chicago)
        // `GRID_HOURS * 60 - 20`, Android's cap (`CalendarGrids.kt:313`) —
        // twenty minutes up from the bottom, which is where the block's own
        // minimum height fits. Pinned at `GRID_HOURS * 60` the block was laid
        // out at exactly the grid's height: off the bottom of the hours and
        // out of the scroll view's content, invisible and untappable.
        XCTAssertEqual(calendarGridHours * 60 - 20, placed.offsetMinutes)
        XCTAssertEqual(20, placed.heightMinutes)

        // Through ``calendarBlockEndMs(_:)``, which still falls back to the
        // start. ``calendarEndMs(_:)`` gives this row an hour since answer
        // 17, and the hour is the AGENDA's floor: a grid that measured a
        // block with it would draw `night walk` three times the height the
        // house asked for.
        let openInside = local(12, "2026-11-04T14:00:00-06:00")
        XCTAssertEqual(
            20,
            calendarDayGridPlacement(openInside, on: day(2026, 11, 4), in: chicago).heightMinutes
        )
    }

    /// **A `starts_at` nothing can parse is drawn by no view.**
    ///
    /// ``calendarStartMs(_:)`` answers `Int64.max` for it. Month and day view
    /// compare that date against the anchor and drop it; the agenda's window
    /// has a floor and **no ceiling**, so the sentinel passed it, sorted last,
    /// and grouped under `CalendarDay(ms: Int64.max, …)` — outside the
    /// Gregorian component range, which falls back to 1970 and grew the agenda
    /// a trailing `1970-01-01` header with a junk row under it.
    func testAnUnreadableStartIsInNoViewAndInNoAgendaGroup() {
        let junk = CalendarEntry(id: 99, title: "unreadable", startsAt: "not a date", endsAt: nil)
        let real = local(1, "2026-11-04T11:00:00-06:00", to: "2026-11-04T12:00:00-06:00")
        let anchor = day(2026, 11, 4)
        let todayMs = anchor.startMs(in: chicago)

        // **Every** mode, from `allCases`, so a view added later cannot be
        // left out of this one: the week was, until answer 14 added it and
        // this loop was still a hand-written list of three.
        for mode in CalendarViewMode.allCases {
            let visible = calendarVisibleEvents(
                mode: mode, anchor: anchor, events: [real, junk], in: chicago
            )
            XCTAssertEqual([1], visible.map(\.id), "\(mode) kept the unreadable row")
        }

        let sections = calendarAgendaSections(
            calendarVisibleEvents(mode: .agenda, anchor: anchor, events: [real, junk], in: chicago),
            todayMs: todayMs,
            in: chicago
        )
        XCTAssertEqual([anchor], sections.map(\.day), "the agenda grew a 1970 day group")
    }

    /// The `now` rule is nil off the day and nil outside the hours — a line
    /// clamped to the top would claim it is 6am, which is the difference
    /// between it and an event.
    func testTheNowRuleOnlyExistsOnTodayAndInsideTheHours() {
        let today = day(2026, 11, 4)
        XCTAssertEqual(
            510,
            calendarNowOffsetMinutes(
                nowMs: isoMs("2026-11-04T14:30:00-06:00") ?? 0, on: today, in: chicago
            )
        )
        XCTAssertNil(
            calendarNowOffsetMinutes(
                nowMs: isoMs("2026-11-05T14:30:00-06:00") ?? 0, on: today, in: chicago
            ),
            "tomorrow's grid has no now line"
        )
        XCTAssertNil(
            calendarNowOffsetMinutes(
                nowMs: isoMs("2026-11-04T05:00:00-06:00") ?? 0, on: today, in: chicago
            ),
            "before the grid's first hour"
        )
        XCTAssertNil(
            calendarNowOffsetMinutes(
                nowMs: isoMs("2026-11-04T23:00:00-06:00") ?? 0, on: today, in: chicago
            ),
            "after the grid's last hour"
        )
    }

    // MARK: - the month grid

    /// Six rows of seven, always — a grid whose row count varied with the
    /// month would move every cell under the finger.
    func testTheMonthGridIsAlwaysFortyTwoCellsInReadingOrder() {
        let cells = calendarMonthCells(anchor: day(2026, 12, 1), events: [], in: chicago)
        XCTAssertEqual(42, cells.count)
        XCTAssertEqual(day(2026, 11, 29), cells.first?.day)
        XCTAssertEqual(day(2027, 1, 9), cells.last?.day)
        XCTAssertFalse(cells.first?.inMonth ?? true, "29 november is not in december")
        XCTAssertTrue(cells[2].inMonth, "1 december is")
        XCTAssertFalse(cells.last?.inMonth ?? true)
    }

    /// Every day of the anchor's month is `inMonth` and nothing else is — the
    /// grid's two spill rows are the cells a month-only test would never
    /// notice were mislabelled, because 42 cells can never hold the same month
    /// in two years.
    func testEveryDayOfTheAnchorsMonthIsInMonthAndTheSpillIsNot() {
        let cells = calendarMonthCells(anchor: day(2027, 1, 15), events: [], in: chicago)
        XCTAssertEqual(31, cells.filter(\.inMonth).count)
        XCTAssertTrue(
            cells.filter(\.inMonth).allSatisfy { $0.day.month == 1 && $0.day.year == 2027 },
            "a spill cell was labelled as january's"
        )
        XCTAssertTrue(
            cells.filter { !$0.inMonth }.allSatisfy { $0.day.month == 12 || $0.day.month == 2 },
            "the spill is december 2026 and february 2027 and nothing else"
        )
    }

    /// Three dots and a count — the cap and the overflow, on a day with four
    /// events (the fixture's 6/7/8/9).
    func testACellDrawsThreeMarksAndCountsTheRest() {
        let sameDay = (0..<4).map { index in
            local(Int64(6 + index), String(format: "2026-11-15T%02d:00:00-06:00", 9 + index))
        }
        let cells = calendarMonthCells(anchor: day(2026, 11, 1), events: sameDay, in: chicago)
        guard let cell = cells.first(where: { $0.day == day(2026, 11, 15) }) else {
            return XCTFail("the 15th is not in november's grid")
        }
        XCTAssertEqual(3, cell.marks.count)
        XCTAssertEqual(1, cell.overflow)
        XCTAssertEqual("+1", CalendarStrings.moreCount(cell.overflow))
        // What VoiceOver gets for the cell is the full count, not the
        // overflow: the `+1` chip is inside the cell's button and carries no
        // label of its own.
        XCTAssertEqual("4 events", CalendarStrings.dayEvents(cell.events.count))
        XCTAssertEqual([6, 7, 8], cell.marks.map(\.id), "the marks are the earliest three")

        let quiet = cells.first { $0.day == day(2026, 11, 16) }
        XCTAssertEqual(0, quiet?.overflow)
        XCTAssertEqual(0, quiet?.marks.count)
    }

    func testAMonthCellsEventsAreTheOnesStartingOnThatDate() {
        let events = [
            local(1, "2026-11-15T09:00:00-06:00"),
            local(2, "2026-11-16T09:00:00-06:00"),
        ]
        let cells = calendarMonthCells(anchor: day(2026, 11, 1), events: events, in: chicago)
        XCTAssertEqual([1], cells.first { $0.day == day(2026, 11, 15) }?.events.map(\.id))
        XCTAssertEqual([2], cells.first { $0.day == day(2026, 11, 16) }?.events.map(\.id))
    }
}
