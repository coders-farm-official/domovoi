import Foundation
import XCTest
@testable import DomovoiKit

/// The three things Kamron answered for Calendar on 2026-11-03, where they are
/// cheapest to pin: the week view's groups (answer 14), the bounded read and
/// its reuse (answer 16), and the hour an open-ended event keeps (answer 17).
///
/// Everything here is either a pure function over a fixed clock or the model
/// against a server that is not a server — `CalendarModelTests`' rig with one
/// difference, and the difference is the point: **this house honours
/// `?start=&end=`**, so "the window is being asked for" is a thing a case can
/// observe rather than a thing it has to trust.
@MainActor
final class CalendarWindowTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let chicago = TimeZone(identifier: "America/Chicago")!

    /// 2026-11-04 14:30 in Chicago — a Wednesday, three days after the clocks
    /// went back. `CalendarModelTests`' instant, so the two files' fixtures
    /// talk about the same week.
    private let nowMs: Int64 = 1_793_824_200_000

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func day(_ year: Int, _ month: Int, _ dayOfMonth: Int) -> CalendarDay {
        CalendarDay(year: year, month: month, day: dayOfMonth)
    }

    private func entry(
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

    // MARK: - answer 14: the week's seven groups

    /// Seven groups, in date order, from the **Sunday** on or before the
    /// anchor — and the empty days are in there.
    ///
    /// The last assertion is the one that distinguishes this from an agenda
    /// over a shorter window: three of these seven days have nothing on them,
    /// and a week that dropped them would still look right in the other two
    /// assertions.
    func testTheWeekIsSevenGroupsSundayFirstIncludingTheEmptyDays() {
        let events = [
            entry(1, "2026-11-04T11:00:00-06:00", to: "2026-11-04T12:30:00-06:00"),
            entry(2, "2026-11-04T07:00:00-06:00", to: "2026-11-04T08:00:00-06:00"),
            entry(3, "2026-11-07T19:00:00-06:00"),
            // Outside the week: the following Sunday, which is the 8th.
            entry(4, "2026-11-08T09:00:00-06:00"),
        ]
        let visible = calendarVisibleEvents(
            mode: .week, anchor: day(2026, 11, 4), events: events, in: chicago
        )
        XCTAssertEqual(
            [2, 1, 3],
            visible.map(\.id),
            "the week is [sun 1, sun 8) and its rows are start-ordered"
        )

        let sections = calendarWeekSections(
            visible,
            anchor: day(2026, 11, 4),
            todayMs: nowMs,
            in: chicago
        )
        XCTAssertEqual(
            ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04",
             "2026-11-05", "2026-11-06", "2026-11-07"],
            sections.map(\.key)
        )
        XCTAssertEqual(
            [[], [], [], [2, 1], [], [], [3]],
            sections.map { $0.rows.map(\.id) },
            "an empty day still has a group — a week that drew only its busy "
                + "days would be an agenda with a shorter window"
        )
    }

    /// Today's group is the literal `today` and no other is — the agenda's own
    /// branch (`CalendarScreen.kt:348`), which a week built by copying the
    /// grouping and forgetting the header would lose silently.
    func testTheWeeksHeadersNameTheDayAndTodayIsTheWord() {
        let sections = calendarWeekSections(
            [], anchor: day(2026, 11, 4), todayMs: nowMs, in: chicago
        )
        XCTAssertEqual(
            ["sun, nov 1", "mon, nov 2", "tue, nov 3", CalendarStrings.today,
             "thu, nov 5", "fri, nov 6", "sat, nov 7"],
            sections.map(\.header)
        )
        XCTAssertEqual(
            1,
            sections.filter { $0.header == CalendarStrings.today }.count,
            "more than one group is headed `today`"
        )
    }

    /// The week steps seven days, and its label is the one that was already
    /// ported — including the two-month spelling.
    func testTheWeekStepsSevenDaysAndKeepsItsOwnLabel() {
        let anchor = day(2026, 11, 4)
        XCTAssertEqual("nov 1 – 7, 2026", calendarRangeLabel(mode: .week, anchor: anchor))

        let back = calendarStep(anchor, mode: .week, by: -1)
        XCTAssertEqual(day(2026, 10, 28), back)
        XCTAssertEqual(
            "oct 25 – 31, 2026",
            calendarRangeLabel(mode: .week, anchor: back),
            "the label is the week's, not the anchor's day"
        )

        let crossing = day(2026, 12, 2)
        XCTAssertEqual(
            "nov 29 – dec 5, 2026",
            calendarRangeLabel(mode: .week, anchor: crossing),
            "a week across a month boundary keeps the month on both sides"
        )
    }

    /// The toggle offers four views, in the order they are drawn.
    ///
    /// Pinned because the order is `CaseIterable`'s and nothing else states
    /// it: a case added in the wrong place moves the capsules under the
    /// finger with nothing failing.
    func testTheToggleOffersFourViewsInWideningSpans() {
        XCTAssertEqual(
            ["agenda", "day", "week", "month"],
            CalendarViewMode.allCases.map(\.label)
        )
    }

    // MARK: - answer 17: the hour an open-ended event keeps

    /// Fifty minutes after it started it is still there; seventy minutes
    /// after, it is gone.
    ///
    /// The two instants Kamron's "keep it for an hour" means, measured through
    /// the thing that actually decides it — the **agenda's window**, not
    /// `calendarEndMs` on its own. The anchor has to move with the clock here
    /// because the agenda's floor is the anchor's midnight: the discriminating
    /// pair is the same event read from the next day's agenda, 50 and 70
    /// minutes after a 23:40 start.
    func testAnOpenEndedEventIsOnScreenFiftyMinutesInAndGoneAtSeventy() {
        let lateNight = entry(1, "2026-11-03T23:40:00-06:00", title: "night walk")
        // 00:30 on the 4th is fifty minutes in; the 4th's agenda floor is its
        // own midnight, which the invented end (00:40) is past.
        XCTAssertEqual(
            [1],
            calendarVisibleEvents(
                mode: .agenda, anchor: day(2026, 11, 4), events: [lateNight], in: chicago
            ).map(\.id),
            "answer 17: fifty minutes after it started, an open-ended event is still on"
        )

        // Seventy minutes in, the floor has to move past the invented end to
        // say anything, and a day's floor is the next thing there is: the 5th.
        // The hour itself is pinned exactly, in milliseconds, right here.
        XCTAssertEqual(
            isoMs("2026-11-03T23:40:00-06:00").map { $0 + 3_600_000 },
            calendarEndMs(lateNight),
            "the invented end is an hour after the start, to the millisecond"
        )
        XCTAssertTrue(
            calendarVisibleEvents(
                mode: .agenda, anchor: day(2026, 11, 5), events: [lateNight], in: chicago
            ).isEmpty,
            "the hour is an hour: a day later it is gone"
        )
    }

    /// Home's rule and this one are now **the same function's answer**, not
    /// two numbers that happen to match.
    ///
    /// Asserted against ``eventEndMs(_:)`` itself so the two cannot drift
    /// apart again: the divergence this replaced (DECISIONS §8) was two
    /// fallbacks written independently, and a literal `3_600_000` here would
    /// let Home change its mind without anything failing.
    func testTheCalendarsWindowEndIsHomesOwnRule() {
        for startsAt in [
            "2026-11-04T15:00:00-06:00",
            "2026-07-04T00:00:00-05:00",
            "2026-03-08T01:30:00-06:00",   // the morning the clocks go forward
        ] {
            XCTAssertEqual(
                eventEndMs(HomeEvent(id: 9, title: "x", startsAt: startsAt)),
                calendarEndMs(CalendarEntry(id: 9, title: "x", startsAt: startsAt)),
                "the two rules disagree about \(startsAt)"
            )
        }
    }

    // MARK: - answer 16: the window, as arithmetic

    /// Each view's window, as local midnights in the device's zone.
    func testEachViewAsksForItsOwnRange() {
        let anchor = day(2026, 11, 4)
        let oneDay = calendarWindow(mode: .day, anchor: anchor, in: chicago)
        XCTAssertEqual(midnight(2026, 11, 4), oneDay.startMs)
        XCTAssertEqual(midnight(2026, 11, 5), oneDay.endMs)

        let week = calendarWindow(mode: .week, anchor: anchor, in: chicago)
        XCTAssertEqual(midnight(2026, 11, 1), week.startMs)
        XCTAssertEqual(midnight(2026, 11, 8), week.endMs)

        // The 42-cell grid, not november. November 2026 begins on a sunday,
        // so the grid starts on the 1st itself and runs six full weeks to
        // sunday 13 december — a fortnight of december that carries marks.
        let month = calendarWindow(mode: .month, anchor: anchor, in: chicago)
        XCTAssertEqual(midnight(2026, 11, 1), month.startMs)
        XCTAssertEqual(midnight(2026, 12, 13), month.endMs)

        // A day of lead-in, and **no ceiling** — the agenda is "the next 25
        // things", so there is no honest date to stop at and `limit` is what
        // bounds it.
        let agenda = calendarWindow(mode: .agenda, anchor: anchor, in: chicago)
        XCTAssertEqual(midnight(2026, 11, 3), agenda.startMs)
        XCTAssertTrue(agenda.isOpenEnded)
    }

    /// The window becomes the route's own query string.
    ///
    /// Pinned character for character, like
    /// `HomeModelsTest.calendarReadStartsAtLocalMidnightForAWeek`: the colons
    /// are `%3A`, the three parameters are in the route's own order, and an
    /// open-ended window sends **no `end`** rather than a sentinel the server
    /// would try to parse as a date.
    func testTheWindowIsSpeltOutAsTheRoutesQueryString() {
        let anchor = day(2026, 11, 4)
        XCTAssertEqual(
            "/api/calendar/events?start=2026-11-04T06%3A00%3A00Z"
                + "&end=2026-11-05T06%3A00%3A00Z&limit=500",
            calendarEventsPath(calendarWindow(mode: .day, anchor: anchor, in: chicago))
        )
        XCTAssertEqual(
            "/api/calendar/events?start=2026-11-03T06%3A00%3A00Z&limit=500",
            calendarEventsPath(calendarWindow(mode: .agenda, anchor: anchor, in: chicago)),
            "an open-ended window must send no `end` at all"
        )
    }

    /// `contains`, `touches`, `union` — and the three pairs that must **not**
    /// widen.
    ///
    /// ``CalendarWindow/touches(_:)`` is geometry and nothing else since
    /// REVIEW-1: it is asserted here as geometry, and every decision is
    /// asserted through ``CalendarWindow/widens(to:)``.
    func testAnOpenEndedWindowNeverWidensABoundedOne() {
        let agenda = CalendarWindow(startMs: 300, endMs: CalendarWindow.noCeiling)
        let grid = CalendarWindow(startMs: 100, endMs: 900)

        XCTAssertTrue(agenda.contains(CalendarWindow(startMs: 400, endMs: 500)))
        XCTAssertFalse(agenda.contains(grid), "the grid begins before the agenda's floor")

        XCTAssertTrue(
            agenda.touches(grid),
            "geometrically they do overlap — 300 to 900 is in both, and a "
                + "predicate that denied it would be lying about the shapes"
        )
        XCTAssertFalse(
            agenda.widens(to: grid),
            "their union would be an open-ended read from 100, whose limit is "
                + "spent before it reaches 300 — answer 16 undone in one tap"
        )
        XCTAssertFalse(grid.widens(to: agenda))

        let next = CalendarWindow(startMs: 800, endMs: 1_600)
        XCTAssertTrue(grid.touches(next))
        XCTAssertTrue(grid.widens(to: next))
        XCTAssertEqual(CalendarWindow(startMs: 100, endMs: 1_600), grid.union(next))

        let far = CalendarWindow(startMs: 5_000, endMs: 6_000)
        XCTAssertFalse(grid.touches(far))
        XCTAssertFalse(grid.widens(to: far), "`today` from six months out reads its own range")
    }

    /// **The agenda's floor comes home when `today` does.**
    ///
    /// Twenty-six chevrons back — the agenda steps seven days
    /// (`CalendarModels.kt:62`) — and then `today`, as arithmetic over the
    /// windows alone. This is REVIEW-1's must 1 at the size of one predicate:
    /// both windows are open-ended, so they share the same ∞ and *touch*
    /// however far apart they are. While `touches` was what decided a
    /// widening, every step unioned, `union` takes the lower floor, and the
    /// asked floor was therefore monotonically non-increasing for the life of
    /// the model: six months out, `today` could not bring it back, and the
    /// next read asked `start=may&limit=500` for a screen showing november.
    func testAnAgendaWalkedIntoThePastComesHomeWhenTodayDoes() {
        let today = day(2026, 11, 4)
        let home = calendarWindow(mode: .agenda, anchor: today, in: chicago)
        var asked = home

        for step in 1...26 {
            let wanted = calendarWindow(
                mode: .agenda, anchor: today.adding(days: -7 * step), in: chicago
            )
            XCTAssertTrue(
                asked.widens(to: wanted),
                "a chevron's own step must widen: it asks for exactly the range "
                    + "it wants, since the new floor is the union's"
            )
            asked = asked.union(wanted)
            XCTAssertEqual(
                wanted.startMs, asked.startMs,
                "stepping back must not leave the floor above the range in view"
            )
        }
        // 26 × 7 = 182 days, plus the agenda's day of lead-in: 5 may 2026,
        // which is what the probe measured on this branch (REVIEW-1, must 1).
        XCTAssertEqual(today.adding(days: -183).startMs(in: chicago), asked.startMs)
        XCTAssertEqual(
            182, asked.daysBelow(home),
            "the walk back is half a year below today's own floor"
        )
        XCTAssertFalse(
            asked.widens(to: home),
            "`today` from six months out widened the window instead of replacing "
                + "it, so the floor can never come home (REVIEW-1, must 1)"
        )
        let oneStepBack = calendarWindow(
            mode: .agenda, anchor: today.adding(days: -7), in: chicago
        )
        XCTAssertTrue(
            home.widens(to: oneStepBack),
            "one step back is still a widening — the bound must not cost the "
                + "thing it bounds"
        )
    }

    /// **A widening is bounded in either direction**, so the asked window
    /// cannot grow for the life of the model (REVIEW-1, should 5).
    ///
    /// Thirty forward day-chevrons used to ask for a thirty-day window and
    /// re-read all of it on every tap; past ``calendarWindowLimit`` the page
    /// is cut, ``CalendarPage/serves(_:)`` goes false for every sub-range,
    /// and each further step became a full re-read of an ever-larger range.
    /// Both directions are asserted because the measure has a floor term and
    /// a ceiling term, and only the floor one can starve a view.
    func testAWideningIsBoundedInEitherDirection() {
        let start = day(2026, 11, 4)

        func walk(_ direction: Int) -> CalendarWindow {
            var asked = calendarWindow(mode: .day, anchor: start, in: chicago)
            for step in 1...200 {
                let wanted = calendarWindow(
                    mode: .day, anchor: start.adding(days: direction * step), in: chicago
                )
                asked = asked.widens(to: wanted) ? asked.union(wanted) : wanted
            }
            return asked
        }

        let forwards = walk(1)
        let lastDay = calendarWindow(mode: .day, anchor: start.adding(days: 200), in: chicago)
        XCTAssertLessThanOrEqual(
            forwards.daysBelow(lastDay),
            calendarWidenLimitDays,
            "two hundred forward chevrons left the floor further behind the day "
                + "on screen than the budget allows"
        )
        let backwards = walk(-1)
        XCTAssertLessThanOrEqual(
            (backwards.endMs - backwards.startMs) / calendarNominalDayMs,
            Int64(calendarWidenLimitDays) + 2,
            "two hundred chevrons back asked for a window of "
                + "\((backwards.endMs - backwards.startMs) / calendarNominalDayMs) days"
        )
    }

    /// A page cut short by `LIMIT` is this range's answer and **nobody
    /// else's**.
    ///
    /// The distinction the screen depends on, and the whole of it since
    /// REVIEW-2's must 1: a cut page is its **own** range's answer — so
    /// nothing spins waiting for an uncut one — and is no answer at all about
    /// a range inside it.
    ///
    /// There used to be a second, weaker predicate (`landed(for:)`) saying a
    /// cut page had answered any range its window covered, and
    /// ``CalendarModel/rangePending`` asked that one. The last two assertions
    /// here are why it is gone: a screen that believed a cut page about a
    /// range inside it said `nothing on the calendar` when the read had
    /// failed, and flashed the same sentence on the way to a successful one.
    func testACutPageAnswersItsOwnRangeAndNoOther() {
        let window = CalendarWindow(startMs: 100, endMs: 900)
        let inside = CalendarWindow(startMs: 200, endMs: 300)

        let whole = CalendarPage(window: window, rows: [], truncated: false)
        XCTAssertTrue(whole.serves(inside))
        XCTAssertTrue(whole.serves(window))

        let cut = CalendarPage(window: window, rows: [], truncated: true)
        XCTAssertTrue(cut.serves(window), "re-reading it would return the same cut")
        XCTAssertFalse(
            cut.serves(inside),
            "the first 500 rows of a window are not every row of a range inside it"
        )
        XCTAssertFalse(
            cut.serves(CalendarWindow(startMs: 50, endMs: 900)),
            "a page is never an answer about a range it does not even cover"
        )
    }


    // MARK: - answer 16: the model, against a house that honours the window

    /// A house that filters on `starts_at` the way `calendar.py:52-56` does,
    /// and that can hold one window's request open.
    private final class WindowHouse: Sendable {

        /// Every GET's query string, in order.
        let asked = Shared<[String]>([])

        /// Hold any GET whose query contains this, until ``release`` is set —
        /// so a case can move the range **inside** a round trip.
        let hold = Shared<String?>(nil)
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)

        /// Set when a park ran to its bound instead of being released, which
        /// means the case that depended on it asserted nothing.
        let ranOut = Shared<Bool>(false)

        /// While set, every read of the events route answers 503 — a house
        /// that has gone away with a page already in hand, which is the state
        /// REVIEW-2's must 1 was measured in. The request is still recorded in
        /// ``asked``: it was asked.
        let failing = Shared<Bool>(false)

        let rows: [(id: Int64, title: String, startsAt: String)]

        init(rows: [(id: Int64, title: String, startsAt: String)]) {
            self.rows = rows
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                if parts.path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                let query = url.query ?? ""
                self.asked.mutate { $0.append(query) }
                if self.failing.current {
                    return .json(#"{"detail":"the calendar is unwell"}"#, status: 503)
                }
                if let holding = self.hold.current, query.contains(holding) {
                    self.arrived.set(true)
                    // Bounded at 0.8 s, under the rig's own
                    // `timeoutIntervalForRequest` of 1 s: a park that outlived
                    // the timeout would fail the request instead of holding
                    // it, and the case would be asserting over a read that
                    // never happened.
                    for step in 0..<400 {
                        if self.release.current { break }
                        if step == 399 { self.ranOut.set(true) }
                        Thread.sleep(forTimeInterval: 0.002)
                    }
                }
                let items = parts.queryItems ?? []
                func bound(_ name: String) -> Int64? {
                    guard let raw = items.first(where: { $0.name == name })?.value else {
                        return nil
                    }
                    // `URLComponents` decodes `%3A` for us; the fallback is
                    // for a value it left alone.
                    return isoMs(raw.removingPercentEncoding ?? raw)
                }
                // `starts_at >= :start` and `starts_at < :end`, on the parsed
                // instant — the route's own SQL. Parsed and not compared as
                // strings, which is stricter than the stub house's fixture
                // can be: these rows carry a `-06:00` or `-05:00` offset
                // where the window's bounds are written in `Z`.
                let from = bound("start")
                let until = bound("end")
                // **`ORDER BY starts_at ASC LIMIT :limit`** (`calendar.py:65-67`),
                // and not only the two bounds. Honoured since REVIEW-1: the
                // defect answer 16 was asked for is a *cut* read — the oldest
                // 500 from a floor below the range on screen — and a house
                // that answered with everything could not reproduce it at
                // all. Every case here but
                // `testTodayComesHomeInAHouseWith...` has a handful of rows,
                // for which this is the order they were written in anyway.
                let limit = (items.first { $0.name == "limit" }?.value).flatMap { Int($0) }
                    ?? calendarWindowLimit
                let body = self.rows
                    .filter { row in
                        guard let at = isoMs(row.startsAt) else { return false }
                        if let from, at < from { return false }
                        if let until, at >= until { return false }
                        return true
                    }
                    .sorted { (isoMs($0.startsAt) ?? 0) < (isoMs($1.startsAt) ?? 0) }
                    .prefix(limit)
                    .map {
                        """
                        {"id":\($0.id),"title":"\($0.title)","starts_at":"\($0.startsAt)",\
                        "ends_at":null,"location":null,"description":null,\
                        "source":"local","external_id":null,"last_synced_at":null}
                        """
                    }
                return .json("[\(body.joined(separator: ","))]")
            }
        }
    }

    /// Rows only the right window reaches: three in november 2026, one at the
    /// end of september, and one a year back whose title is a word no other
    /// row carries.
    private func spreadOut() -> WindowHouse {
        WindowHouse(rows: [
            (5, "autumn fete", "2026-09-29T10:00:00-05:00"),
            (1, "pottery class", "2026-11-04T11:00:00-06:00"),
            (2, "swim lesson", "2026-11-05T07:00:00-06:00"),
            (3, "choir practice", "2026-11-20T19:00:00-06:00"),
            (4, "flu jab", "2025-10-01T10:00:00-05:00"),
        ])
    }

    /// A house with more history than one read can carry: 600 events in the
    /// 182 days behind the clock, plus `pottery class` today.
    ///
    /// The crowd is dense on purpose — about three and a third a day — so
    /// that a read whose floor is six months back is **cut** before it
    /// reaches today. That is the whole defect answer 16 was asked for, and
    /// the UI case `testCal_ABoundedReadFindsTodayInAHouseWithYearsBehindIt`
    /// seeds the same shape in the stub house.
    private func crowdedHouse() -> WindowHouse {
        let floor = midnight(2026, 5, 5)
        let ceiling = midnight(2026, 11, 3)
        let gap = (ceiling - floor) / 600
        var rows: [(id: Int64, title: String, startsAt: String)] = (0..<600).map { index in
            (
                id: Int64(1_000 + index),
                title: "backfill \(index)",
                startsAt: calendarInstant(
                    Date(timeIntervalSince1970: Double(floor + gap * Int64(index)) / 1000)
                )
            )
        }
        rows.append((1, "pottery class", "2026-11-04T11:00:00-06:00"))
        return WindowHouse(rows: rows)
    }

    /// A house with more events in **one day** than one read can carry: 500
    /// on the 4th, and `swim lesson` alone on the 5th.
    ///
    /// The shape the cut-page bar is about (REVIEW-2, must 2). Every window
    /// that includes the 4th comes back cut, so a read widened from the 4th
    /// to the 5th spends all 500 of its rows on the 4th — `ORDER BY starts_at
    /// ASC LIMIT 500` — and the 5th draws nothing. `crowdedHouse` cannot see
    /// this: there the excess is 182 days, so ``CalendarWindow/widens(to:)``
    /// says no on its own and the bar never decides.
    private func busyDay() -> WindowHouse {
        let floor = midnight(2026, 11, 4)
        var rows: [(id: Int64, title: String, startsAt: String)] = (0..<500).map { index in
            (
                id: Int64(2_000 + index),
                title: "shift \(index)",
                startsAt: calendarInstant(
                    Date(timeIntervalSince1970: Double(floor + Int64(index) * 150_000) / 1000)
                )
            )
        }
        rows.append((1, "swim lesson", "2026-11-05T07:00:00-06:00"))
        return WindowHouse(rows: rows)
    }

    /// One window as the query string the route is asked for.
    ///
    /// Composed with the app's own ``calendarInstantEncoded(ms:)`` on
    /// purpose: what the spelling is, character for character, is
    /// ``testTheWindowIsSpeltOutAsTheRoutesQueryString()``'s job, and the
    /// cases below are about *which window* was asked for.
    private func query(_ window: CalendarWindow) -> String {
        var text = "start=\(calendarInstantEncoded(ms: window.startMs))"
        if !window.isOpenEnded {
            text += "&end=\(calendarInstantEncoded(ms: window.endMs))"
        }
        return text + "&limit=\(calendarWindowLimit)"
    }

    private func rig(_ house: WindowHouse) -> (CalendarModel, InMemoryShellStore) {
        let (model, store, _) = rigged(house)
        return (model, store)
    }

    /// ``rig(_:)`` with the container, for the one case that has to fire a
    /// push — the read a push makes is what says the box is pointed at the
    /// range on screen.
    private func rigged(
        _ house: WindowHouse
    ) -> (CalendarModel, InMemoryShellStore, AppContainer) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = CalendarModel(
            container: container,
            nowMs: { [nowMs] in nowMs },
            timeZone: { [chicago] in chicago }
        )
        return (model, store, container)
    }

    private func settleUntil(_ done: @MainActor () -> Bool, _ what: String) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields")
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    /// Local midnight of a date, in the zone the model formats in.
    private func midnight(_ year: Int, _ month: Int, _ dayOfMonth: Int) -> Int64 {
        day(year, month, dayOfMonth).startMs(in: chicago)
    }

    private func window(
        _ from: (Int, Int, Int),
        _ until: (Int, Int, Int)
    ) -> CalendarWindow {
        CalendarWindow(
            startMs: midnight(from.0, from.1, from.2),
            endMs: midnight(until.0, until.1, until.2)
        )
    }

    /// **The read is bounded, and what is outside the window never arrives.**
    ///
    /// `flu jab` is a year back. Under the unparameterised read it was in
    /// ``CalendarModel/all`` and dropped by the view's own filter; under
    /// answer 16's read it is not in the rows at all, and that is the
    /// difference — a case that only looked at the screen could not tell the
    /// two apart, because both end with it not drawn.
    ///
    /// The old read was `ORDER BY starts_at ASC LIMIT 500` **from the dawn of
    /// time**, so in a house whose oldest 500 events are all in the past the
    /// agenda was empty. This is the case that says the floor exists.
    func testTheReadIsBoundedAndWhatIsOutsideItNeverArrives() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")

        XCTAssertEqual(1, house.asked.current.count)
        XCTAssertEqual(
            "start=2026-11-03T06%3A00%3A00Z&limit=500",
            house.asked.current[0],
            "the agenda's own window: midnight on the 3rd, no ceiling, the route's limit"
        )
        XCTAssertEqual(
            [1, 2, 3],
            model.all.map(\.id),
            "september and last october are outside the window and must not be in the rows"
        )
        XCTAssertFalse(
            model.all.contains { $0.title == "flu jab" },
            "the read is unbounded again"
        )
    }

    /// A range already read costs **no request**; a range next door is read as
    /// the **union**; a far jump is read on its own.
    ///
    /// The three branches of the model's `rangeMoved` in one case, because the
    /// sequence is what matters: the second branch is what keeps a chevron
    /// from being a round trip apiece, and the third is what keeps `today`
    /// from reading half a year to get home.
    func testSteppingReusesWidensAndThenStartsAfresh() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        XCTAssertEqual(1, model.events.fetches)

        // 1. **Inside.** The 4th is inside the agenda's open-ended window, so
        //    the toggle asks the server for nothing at all.
        model.show(.day)
        await everythingLands()
        XCTAssertEqual(1, model.events.fetches, "a range already read was asked for again")
        XCTAssertFalse(model.rangePending)
        XCTAssertEqual([1], model.visible.map(\.id))

        // The month grid is not inside it — the grid starts before the
        // agenda's floor — and an open-ended window never widens a bounded
        // one, so this is a fresh read of the grid alone.
        model.show(.month)
        await settleUntil({ !model.rangePending }, "the month's read")
        XCTAssertEqual(2, model.events.fetches)
        XCTAssertEqual(window((2026, 11, 1), (2026, 12, 13)), model.askedWindow)

        // 2. **Next door.** December's grid overlaps november's, so their
        //    union is read rather than december alone: a fortnight of
        //    stepping back and forth settles into one window.
        model.step(1)
        await settleUntil({ !model.rangePending }, "the widened read")
        XCTAssertEqual(3, model.events.fetches)
        XCTAssertEqual(
            window((2026, 11, 1), (2027, 1, 10)),
            model.askedWindow,
            "a chevron must widen the window, not replace it"
        )

        // 3. **Far.** Six months on touches nothing read, so the window is
        //    the new range alone and not half a year of everything between.
        model.step(6)
        await settleUntil({ !model.rangePending }, "the fresh read")
        XCTAssertEqual(4, model.events.fetches)
        XCTAssertEqual(
            window((2027, 5, 30), (2027, 7, 11)),
            model.askedWindow,
            "a jump read everything in between"
        )
    }

    /// **`today` comes home in a house with more history than one read can
    /// carry** — REVIEW-1's must 1, at the size of the model.
    ///
    /// Twenty-six agenda chevrons back (seven days a step) and then `today`,
    /// against 600 events in the six months behind us. The walk back is
    /// legitimate and every step of it asks for what it wants; what must not
    /// happen is that the asked **floor** stays in may when the screen comes
    /// home to november. The route is `ORDER BY starts_at ASC LIMIT 500`, so
    /// `start=may&limit=500` in this house is 500 rows of summer and today is
    /// not among them: the agenda drew `nothing on the calendar` in a house
    /// with a full calendar, which is the exact defect answer 16 was asked
    /// for, one `today` later.
    ///
    /// `pottery class` is what discriminates: a read that came home has it,
    /// and a read still pointed at may cannot — the cut took the oldest 500
    /// and today is the newest row in the house.
    func testTodayComesHomeInAHouseWithMoreHistoryThanOneReadCanCarry() async {
        let house = crowdedHouse()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        XCTAssertTrue(
            model.visible.contains { $0.title == "pottery class" },
            "the opening read cannot see today"
        )

        for step in 1...26 {
            model.step(-1)
            await settleUntil({ !model.rangePending }, "the read for chevron \(step)")
        }
        XCTAssertEqual(
            day(2026, 11, 4).adding(days: -183).startMs(in: chicago),
            model.askedWindow.startMs,
            "the walk back asked for something other than the range it walked to"
        )
        XCTAssertTrue(
            model.events.data?.truncated == true,
            "the read from may is not cut, so this case cannot see the starving: "
                + "the crowd is too thin or the house stopped honouring `limit`"
        )

        let fetchesBefore = model.events.fetches
        model.goToToday()
        // **Not `!rangePending`**, even though since REVIEW-2's must 1 that
        // predicate is true here: what this case is about is the *request*,
        // and the assertion below has to be made before any settle at all,
        // because a settle is where the request is allowed to happen. What is
        // then waited for is the page in hand being the one that was asked
        // for. (While `rangePending` asked only whether the page's window
        // covered the range, it was false here — the cut page from may covers
        // november — and a settle on it returned before the request had even
        // gone out.)
        XCTAssertEqual(
            fetchesBefore + 1, model.events.fetches,
            "`today` asked for nothing: a cut page from may was taken as the "
                + "answer for november"
        )
        await settleUntil({ model.events.data?.window == model.askedWindow }, "the read home")

        XCTAssertEqual(
            calendarWindow(mode: .agenda, anchor: day(2026, 11, 4), in: chicago),
            model.askedWindow,
            "the asked floor is still six months back: the window only ever "
                + "widened and `today` could not bring it home (REVIEW-1, must 1)"
        )
        XCTAssertEqual(
            "start=2026-11-03T06%3A00%3A00Z&limit=500",
            house.asked.current.last,
            "the read home asked for the oldest 500 from the walk's own floor"
        )
        XCTAssertTrue(
            model.visible.contains { $0.title == "pottery class" },
            "`nothing on the calendar` in a house with a full calendar — the "
                + "read home spent its limit on the summer"
        )
        XCTAssertNil(model.emptyText)
    }

    /// **A reply is filed under the range its own request asked for, and not
    /// under whatever the box holds when it lands** — the range key is
    /// captured *before* the `await` (REVIEW-1, must 2).
    ///
    /// The one live path where the box moves and the read in flight is **not**
    /// cancelled: ``CalendarModel/serverChanged()`` sets the window and then
    /// calls ``ApiRead/serverChanged()``, whose `guard lastBase != base()`
    /// returns without cancelling anything when the address has not actually
    /// changed — which is the case `ApiRead` documents, a caller that "shouts
    /// the server changed on every redraw". So: december's union is held open
    /// at the server, the box is narrowed back to december's own grid inside
    /// that round trip, and only then is the reply let go.
    ///
    /// ``CalendarWindowTests/testAHeldReplyForTheRangeJustLeftIsDropped()``
    /// looks like this case and is not: there the move *cancels*, so
    /// ``ApiRead``'s own `guard !Task.isCancelled` drops the reply and the
    /// fetch closure's capture is never exercised. A review moved
    /// `let asked = window.current` below the `await` and the whole suite
    /// stayed green. This is the case that fails.
    func testAReplyIsFiledUnderTheRangeItAskedForAndNotTheBoxsNow() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        model.show(.month)
        await settleUntil({ !model.rangePending }, "november's grid")

        // December's grid overlaps november's, so the step widens: the union
        // `[nov 1, jan 10)` is what goes out, and the `end` is what tells it
        // from november's own read.
        house.hold.set("end=\(calendarInstantEncoded(ms: midnight(2027, 1, 10)))")
        let fetchesBefore = model.events.fetches
        model.step(1)
        await settle(for: house.arrived, what: "the union's read")
        XCTAssertEqual(
            window((2026, 11, 1), (2027, 1, 10)), model.askedWindow,
            "the held request is not the union, so the move below asserts nothing"
        )

        // **Inside the round trip.** Nothing here cancels: the address has
        // not changed, so `ApiRead.serverChanged()` returns at its guard and
        // the held request is still the live one.
        model.serverChanged()
        XCTAssertEqual(
            window((2026, 11, 29), (2027, 1, 10)), model.askedWindow,
            "the box did not move, so there is nothing for the reply to be "
                + "misfiled under"
        )
        XCTAssertEqual(
            fetchesBefore + 1, model.events.fetches,
            "something re-read: the reply this case is about was cancelled"
        )

        house.release.set(true)
        await settleUntil({ !model.rangePending }, "the held reply")
        XCTAssertFalse(house.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual(
            fetchesBefore + 1, model.events.fetches,
            "the reply in hand is not the held one"
        )
        XCTAssertEqual(
            window((2026, 11, 1), (2027, 1, 10)),
            model.events.data?.window,
            "the page is filed under the range the box holds now, not the one "
                + "its own request asked for — the window was read after the "
                + "`await` (REVIEW-1, must 2)"
        )
    }

    /// A week with nothing on it says `nothing in this range` — it does
    /// **not** draw seven empty day headers.
    ///
    /// Android's own shape: `if (visible.isEmpty())` wraps the whole view
    /// switch, so the week's grid is not drawn at all for an empty week
    /// (`CalendarScreen.kt:170-200`). ``CalendarScreenView`` tests
    /// ``CalendarModel/emptyText`` before its own switch for the same reason,
    /// and REVIEW-1's should 6 is right that nothing covered it: answer 14's
    /// "seven, always" is a property of
    /// ``calendarWeekSections(_:anchor:todayMs:in:)``, and this is where the
    /// screen's own answer is pinned instead of left to be rediscovered.
    func testAnEmptyWeekSaysSoRatherThanDrawingSevenEmptyHeaders() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        model.show(.week)
        await settleUntil({ !model.rangePending }, "this week's read")
        XCTAssertNil(model.emptyText, "this week has two of these events")

        // The week before: `autumn fete` is 29 september, so nothing is in it.
        model.step(-1)
        await settleUntil({ !model.rangePending }, "last week's read")

        XCTAssertTrue(model.visible.isEmpty)
        XCTAssertEqual(CalendarStrings.emptyRangeTitle, model.emptyText?.title)
        XCTAssertEqual(
            CalendarStrings.emptyRangeBody(CalendarViewMode.week.label),
            model.emptyText?.body,
            "an empty week must use the range wording and its own word"
        )
        XCTAssertEqual(
            7, model.weekSections.count,
            "the seven groups are still what the week is made of — what the "
                + "screen does with an empty one is the decision, not the shape"
        )
        XCTAssertTrue(model.weekSections.allSatisfy { $0.rows.isEmpty })
    }

    /// **The range key is captured before the `await`, so a reply that lands
    /// after the range moved is dropped.**
    ///
    /// The house holds november's grid read open, the range moves to
    /// october's *inside* that round trip, and only then is the held reply let
    /// go. What must not happen is that november's rows end up as the answer
    /// for october — the read-after-`await`-wrong-key defect with the house
    /// swapped for the range, and the one shape of it no view could detect,
    /// since another fortnight's rows look exactly like a server answering
    /// wrongly.
    ///
    /// `autumn fete` on 29 september is what discriminates: it is inside
    /// october's grid (which starts on sunday the 27th) and outside
    /// november's, so a screen showing the held reply has three rows and no
    /// fete, and a screen showing the right one has the fete.
    func testAHeldReplyForTheRangeJustLeftIsDropped() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")

        house.hold.set("start=\(calendarInstantEncoded(ms: midnight(2026, 11, 1)))")
        model.show(.month)
        await settle(for: house.arrived, what: "november's grid read")
        XCTAssertTrue(model.rangePending, "the held read must still be in flight")

        // **Inside the round trip.** October's grid overlaps november's, so
        // this widens: the union is read and the held request is cancelled.
        model.step(-1)
        house.release.set(true)
        await settleUntil({ !model.rangePending }, "october's read")

        XCTAssertFalse(house.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual(
            window((2026, 9, 27), (2026, 12, 13)),
            model.events.data?.window,
            "the page in hand is not the window that was asked for last"
        )
        XCTAssertTrue(
            model.all.contains { $0.title == "autumn fete" },
            "november's held reply won: october's rows were never drawn"
        )
        // October's 42-cell grid runs sunday 27 september to sunday 8
        // november, so it spills into november and draws its first week too.
        // `choir practice` on the 20th is the row outside it.
        XCTAssertEqual(
            [5, 1, 2],
            model.visible.map(\.id),
            "october's grid is drawing the wrong range"
        )
    }

    /// Stepping into an unread range says it is reading — **not** `nothing in
    /// this range`.
    ///
    /// Without ``CalendarModel/rangePending`` this is a flash of the empty
    /// wording on every chevron into unread territory: a screen telling the
    /// person their calendar is empty while it is still asking. The old code
    /// could not have had this bug and cannot have the fix either — it had
    /// every event in memory, so `visible.isEmpty` really did mean empty.
    func testAnUnreadRangeIsLoadingRatherThanEmpty() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        model.show(.month)
        await settleUntil({ !model.rangePending }, "the month's read")
        XCTAssertNil(model.emptyText, "november has three of these events")

        model.step(6)
        XCTAssertTrue(model.rangePending)
        XCTAssertTrue(model.isLoading, "an unread range must say it is reading")
        XCTAssertNil(model.emptyText, "a range nothing was read for is not an empty range")
        XCTAssertNil(model.errorText)

        await settleUntil({ !model.rangePending }, "may 2027's read")
        XCTAssertEqual("may 2027", model.rangeLabel)
        XCTAssertEqual(CalendarStrings.emptyRangeTitle, model.emptyText?.title)
    }

    /// A page bigger than the visible range still draws **only** that range.
    ///
    /// The agenda's page has no ceiling, so it serves every day view near it
    /// with no request at all — and the view's own filter is what cuts it
    /// back down (``calendarVisibleEvents(mode:anchor:events:in:)``).
    /// `swim lesson` on the 5th is in the page and must not be on the 4th;
    /// this is also where the subtitle's `in view` is pinned, since the page
    /// holds three rows and the day holds one.
    func testAPageBiggerThanTheRangeDrawsOnlyTheRange() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")

        model.show(.day)
        await everythingLands()
        XCTAssertEqual(1, model.events.fetches, "the day asked for a window it already had")
        XCTAssertEqual(3, model.all.count, "the page holds the whole agenda window")
        XCTAssertEqual([1], model.visible.map(\.id))
        XCTAssertEqual(
            "1 event in view · the household's calendar",
            model.subtitle,
            "the subtitle counts what is drawn, and says so (answer 16)"
        )

        model.step(1)
        await everythingLands()
        XCTAssertEqual(1, model.events.fetches)
        XCTAssertEqual([2], model.visible.map(\.id), "the 5th is drawing the 4th's row")
    }

    /// Going back to a range whose read was abandoned mid-flight does not
    /// leave the screen spinning for ever.
    ///
    /// Agenda → month → straight back to the agenda. The month's reply lands
    /// into ``ApiRead``'s one value slot **after** the person is back on the
    /// agenda, and the agenda's page is gone with it: with nothing left to
    /// replace it, ``CalendarModel/rangePending`` stayed true and the agenda
    /// showed a spinner that never stopped.
    ///
    /// This is ``CalendarModel``'s `pageLanded()`, and it is the case that
    /// says why that exists. `rangeMoved` used to guess at this instead —
    /// going back to the agenda re-pointed the read because the window a read
    /// *might* be in flight for did not cover it — and the guess cost a round
    /// trip on every backwards chevron that needed none (REVIEW-2, should 5).
    /// Here the month's reply is not cancelled at all: it lands, it cannot
    /// draw the agenda, and the agenda's own range is read again because of
    /// that and not because of a guess.
    func testComingStraightBackFromAViewWhoseReadWasAbandonedStillSettles() async {
        let house = spreadOut()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")

        house.hold.set("start=\(calendarInstantEncoded(ms: midnight(2026, 11, 1)))")
        model.show(.month)
        await settle(for: house.arrived, what: "november's grid read")

        model.show(.agenda)
        house.release.set(true)
        await settleUntil({ !model.rangePending }, "the agenda's second read")

        XCTAssertFalse(house.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertFalse(model.isLoading, "the agenda is still spinning")
        XCTAssertEqual([1, 2, 3], model.all.map(\.id))
        XCTAssertEqual(CalendarViewMode.agenda, model.mode)
        XCTAssertEqual(
            calendarWindow(mode: .agenda, anchor: day(2026, 11, 4), in: chicago),
            model.events.data?.window,
            "the page in hand is still the month grid's: nothing re-read the "
                + "range on screen after a page landed that could not draw it"
        )
    }

    // MARK: - REVIEW-2: what the page in hand is, and is not, evidence of

    /// **A failed read says why, with a cut page in hand.**
    ///
    /// REVIEW-2's must 1, measured on the Mac at head 40e56588: twenty-six
    /// agenda chevrons back into a house with more history than one read can
    /// carry, the house then unwell, `today` — and the screen said
    /// `nothing on the calendar`, permanently, with no retry link, because
    /// the error branch is the only one that has one.
    ///
    /// The readings are the whole bug: the page in hand *covers* november
    /// (its window is open-ended from may) and carries no row of it, because
    /// `LIMIT` cut it in the summer. A screen that took that for an answer
    /// had `rangePending == false`, which gates ``CalendarModel/errorText``
    /// off and ``CalendarModel/emptyText`` on. `visible.isEmpty` is asserted
    /// first for exactly that reason: it is what makes the empty wording
    /// reachable, so without it the case could pass for the wrong reason.
    func testAFailedReadWithACutPageInHandSaysWhyRatherThanLookingEmpty() async {
        let house = crowdedHouse()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")

        for step in 1...26 {
            model.step(-1)
            await settleUntil({ !model.rangePending }, "the read for chevron \(step)")
        }
        XCTAssertTrue(
            model.events.data?.truncated == true,
            "the read from may is not cut, so this case cannot see the bug"
        )

        house.failing.set(true)
        model.goToToday()
        await settleUntil({ model.events.error != nil }, "the refusal")

        XCTAssertTrue(
            model.visible.isEmpty,
            "the cut page from may carries a november row, so the empty "
                + "wording was never reachable and this case asserts nothing"
        )
        XCTAssertNotNil(
            model.errorText,
            "the read failed and the screen says nothing about it: a cut page "
                + "from may was taken as the answer for november (REVIEW-2, must 1)"
        )
        XCTAssertNil(
            model.emptyText,
            "`nothing on the calendar` in a house with a full calendar, "
                + "because the read failed — and no retry, which lives in the "
                + "error branch alone"
        )
        XCTAssertFalse(
            model.isLoading,
            "a failure must not read as a spinner: the sentence is what the "
                + "screen has to show"
        )

        // And the retry link's own action gets there: ``CalendarScreenView``'s
        // error branch calls `events.refresh()`, and the box is pointed at
        // november, so one tap is enough.
        house.failing.set(false)
        model.events.refresh()
        await settleUntil({ !model.rangePending }, "the retry")
        XCTAssertNil(model.errorText)
        XCTAssertTrue(model.visible.contains { $0.title == "pottery class" })
    }

    /// **A cut page never widens a read below the range on screen.**
    ///
    /// REVIEW-2's must 2: the bar on widening while the page in hand is cut
    /// had no test at all, and deleting it left the suite green. This is the
    /// shape it is for — five hundred events on the 4th and `swim lesson`
    /// alone on the 5th — and `crowdedHouse` cannot stand in for it: there a
    /// step's excess is 182 days, so ``CalendarWindow/widens(to:)`` says no
    /// on its own and the bar never decides. Here the excess is **one day**,
    /// the widening is allowed on the arithmetic, and only the cut bars it.
    ///
    /// What the counts are for: without the bar the union `[4, 6)` goes out
    /// with `limit=500` and `ORDER BY starts_at ASC`, which is five hundred
    /// rows of the 4th, so the 5th draws nothing — and then
    /// ``CalendarModel`` `pageLanded()` notices and re-reads the 5th alone.
    /// The screen recovers; the round trip was still spent, and the window
    /// asked for was still the union. So this case counts the requests and
    /// names the union it must not see, rather than only looking at the rows.
    func testACutPageNeverWidensAReadBelowTheRangeOnScreen() async {
        let house = busyDay()
        let (model, _) = rig(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        XCTAssertTrue(
            model.events.data?.truncated == true,
            "five hundred events on the 4th did not cut the agenda's read"
        )

        model.show(.day)
        await settleUntil({ !model.rangePending }, "the 4th's own read")
        let fourth = window((2026, 11, 4), (2026, 11, 5))
        XCTAssertEqual(fourth, model.askedWindow)
        XCTAssertTrue(
            model.events.data?.truncated == true,
            "the 4th's own read is not cut either, so there is no cut page in hand"
        )

        let asksBefore = house.asked.current.count
        let fifth = window((2026, 11, 5), (2026, 11, 6))
        model.step(1)
        await settleUntil({ !model.rangePending }, "the 5th's read")

        XCTAssertEqual(
            asksBefore + 1,
            house.asked.current.count,
            "the step asked twice: the cut page was widened and the union came "
                + "back with nothing the 5th could draw (REVIEW-2, must 2)"
        )
        XCTAssertFalse(
            house.asked.current.dropFirst(asksBefore)
                .contains(query(window((2026, 11, 4), (2026, 11, 6)))),
            "the union of the 4th and the 5th went out with `limit=500`, which "
                + "in this house is five hundred rows of the 4th"
        )
        XCTAssertEqual(fifth, model.askedWindow)
        XCTAssertEqual(query(fifth), house.asked.current.last)
        XCTAssertTrue(
            model.visible.contains { $0.title == "swim lesson" },
            "the 5th is empty on a day that has one event on it"
        )
        XCTAssertNil(model.emptyText)
    }

    /// Four agenda chevrons back and then `today`, which asks for **nothing**
    /// — the page in hand is a month wider than the range on screen and
    /// uncut, so it serves it.
    ///
    /// The setup for the two cases below, which are the two halves of that
    /// branch: where the box is left pointing, and what the next chevron
    /// costs.
    private func walkedBackThenHome(
        _ house: WindowHouse
    ) async -> (CalendarModel, AppContainer) {
        let (model, _, container) = rigged(house)
        model.start()
        await settleUntil({ model.events.answered }, "the opening read")
        for step in 1...4 {
            model.step(-1)
            await settleUntil({ !model.rangePending }, "the read for chevron \(step)")
        }
        XCTAssertEqual(
            day(2026, 10, 6).startMs(in: chicago),
            model.askedWindow.startMs,
            "four chevrons back is four weeks and the agenda's day of lead-in"
        )
        XCTAssertFalse(model.events.data?.truncated ?? true, "this house cuts nothing")

        let fetchesBefore = model.events.fetches
        model.goToToday()
        await everythingLands()
        XCTAssertEqual(
            fetchesBefore,
            model.events.fetches,
            "`today` re-read a range whose rows were already in hand"
        )
        XCTAssertFalse(model.rangePending)
        return (model, container)
    }

    /// **The box is left pointing at the range on screen**, so the next read
    /// anybody makes is of that range.
    ///
    /// REVIEW-2's must 3: the box coming home on the branch that asks for
    /// nothing had no test, and deleting the line left the suite green. The
    /// push is what makes it observable — nothing else reads the box without
    /// first setting it — and a push is the ordinary way this screen re-reads
    /// (`pg_notify` inside every write's own transaction).
    ///
    /// The walk is **four** weeks and not twenty-six on purpose. The line
    /// used to be `if asked.daysBelow(wanted) > calendarWidenLimitDays`, 62
    /// days, so a four-week walk left the box a month in the past and
    /// qualified as "close enough": this case therefore fails both for the
    /// line being deleted and for that bound coming back.
    func testTheBoxIsLeftPointingAtTheRangeOnScreenSoTheNextReadIsOfIt() async {
        let house = spreadOut()
        let (model, container) = await walkedBackThenHome(house)
        let home = calendarWindow(mode: .agenda, anchor: day(2026, 11, 4), in: chicago)
        XCTAssertEqual(
            home,
            model.askedWindow,
            "`today` asked for nothing and left the box pointed a month back"
        )

        // Waited for at the **house** and not on ``ApiRead/fetches``, which
        // moves inside `refresh()` before the task it starts has run: a
        // settle on it returns before the request has been dispatched, and
        // the query read back is then the walk's last one.
        let asksBefore = house.asked.current.count
        container.stateEventArrived(StateEvent(type: "calendar.events.changed", frame: "{}"))
        await settleUntil(
            { house.asked.current.count > asksBefore }, "the push's re-read"
        )
        XCTAssertEqual(
            asksBefore + 1,
            house.asked.current.count,
            "the push made more than one read"
        )
        XCTAssertEqual(
            query(home),
            house.asked.current.last,
            "the push's read asked from a floor nobody is looking at: in a "
                + "house with more rows than `limit` below it, that read comes "
                + "home with nothing to draw (REVIEW-2, must 3)"
        )
    }

    /// **A step back into rows already on screen costs no request.**
    ///
    /// REVIEW-2's should 5. The branch that asks for nothing used to need the
    /// box to cover the new range as well, and the box has just been narrowed
    /// to the range on screen — so the very next backwards chevron failed
    /// that test and re-read a fortnight that was already drawn. What decides
    /// it now is the page (``CalendarPage/serves(_:)``) and nothing else;
    /// what a read still in flight might land is ``CalendarModel``
    /// `pageLanded()`'s business, and
    /// ``testComingStraightBackFromAViewWhoseReadWasAbandonedStillSettles()``
    /// is the case for that.
    func testAStepBackIntoThePageInHandCostsNoRequest() async {
        let house = spreadOut()
        let (model, _) = await walkedBackThenHome(house)

        let fetchesBefore = model.events.fetches
        let asksBefore = house.asked.current.count
        model.step(-1)
        await everythingLands()

        XCTAssertEqual(
            fetchesBefore,
            model.events.fetches,
            "the step back re-read rows that were already on screen "
                + "(REVIEW-2, should 5)"
        )
        XCTAssertEqual(asksBefore, house.asked.current.count)
        XCTAssertFalse(model.rangePending, "the rows are here: nothing is pending")
        XCTAssertEqual(
            [1, 2, 3],
            model.visible.map(\.id),
            "the step back drew something other than the page in hand"
        )
    }
}
