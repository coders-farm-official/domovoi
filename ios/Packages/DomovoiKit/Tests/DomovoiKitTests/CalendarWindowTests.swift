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

    /// `contains`, `touches` and `union` — and the pair that must **not**
    /// touch.
    func testAnOpenEndedWindowNeverWidensABoundedOne() {
        let agenda = CalendarWindow(startMs: 300, endMs: CalendarWindow.noCeiling)
        let grid = CalendarWindow(startMs: 100, endMs: 900)

        XCTAssertTrue(agenda.contains(CalendarWindow(startMs: 400, endMs: 500)))
        XCTAssertFalse(agenda.contains(grid), "the grid begins before the agenda's floor")

        XCTAssertFalse(
            agenda.touches(grid),
            "their union would be an open-ended read from 100, whose limit is "
                + "spent before it reaches 300 — answer 16 undone in one tap"
        )
        XCTAssertFalse(grid.touches(agenda))

        let next = CalendarWindow(startMs: 800, endMs: 1_600)
        XCTAssertTrue(grid.touches(next))
        XCTAssertEqual(CalendarWindow(startMs: 100, endMs: 1_600), grid.union(next))

        let far = CalendarWindow(startMs: 5_000, endMs: 6_000)
        XCTAssertFalse(grid.touches(far), "`today` from six months out reads its own range")
    }

    /// A page cut short by `LIMIT` is this range's answer and **nobody
    /// else's**.
    ///
    /// The distinction the screen depends on: `landed` keeps a truncated page
    /// from spinning for ever, `serves` keeps it from being believed about a
    /// smaller range it may not carry every row of.
    func testACutPageAnswersItsOwnRangeAndNoOther() {
        let window = CalendarWindow(startMs: 100, endMs: 900)
        let inside = CalendarWindow(startMs: 200, endMs: 300)

        let whole = CalendarPage(window: window, rows: [], truncated: false)
        XCTAssertTrue(whole.landed(for: inside))
        XCTAssertTrue(whole.serves(inside))

        let cut = CalendarPage(window: window, rows: [], truncated: true)
        XCTAssertTrue(cut.landed(for: window), "a cut page is still its own range's answer")
        XCTAssertTrue(cut.serves(window), "re-reading it would return the same cut")
        XCTAssertTrue(cut.landed(for: inside))
        XCTAssertFalse(
            cut.serves(inside),
            "the first 500 rows of a window are not every row of a range inside it"
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
                let body = self.rows
                    .filter { row in
                        guard let at = isoMs(row.startsAt) else { return false }
                        if let from, at < from { return false }
                        if let until, at >= until { return false }
                        return true
                    }
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

    private func rig(_ house: WindowHouse) -> (CalendarModel, InMemoryShellStore) {
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
        return (model, store)
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
        XCTAssertEqual(
            [5],
            model.visible.map(\.id),
            "october's grid shows the fete and nothing else of these five"
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
    /// agenda, and the agenda's page is gone with it: without the in-flight
    /// check in the model's `rangeMoved` nothing was left to replace it, so
    /// ``CalendarModel/rangePending`` stayed true and the agenda showed a
    /// spinner that never stopped.
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
    }
}
