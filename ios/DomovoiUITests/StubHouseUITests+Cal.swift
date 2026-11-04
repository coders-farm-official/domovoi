import XCTest

/// Calendar, the three things Kamron answered on 2026-11-03: the week view
/// (14), the window per read (16) and the hour an open-ended event keeps (17).
///
/// The screen's own cases are `StubHouseUITests+Calendar.swift` and its
/// helpers — `openCalendar`, `calendarRow`, `calendarScroll`, `calendarEdges`,
/// `calendarAudit`, `CalendarWords`, `StubCalendar` — are used here rather
/// than copied. What is here is what those cases could not have asserted
/// before the answers.
///
/// Two of these cases need rows the others must not see, and the fixture
/// keeps them behind controls for that reason
/// (`fixtures/calendar.py`): two extra rows above `pottery class` would push
/// it out of the agenda's first `LazyVStack` build, and `openCalendar` waits
/// for that row without scrolling. **Each case turns its control off again on
/// the way out**, because the whole run shares one house.
///
/// **The connection sheet is never opened** (NIGHT-RULES §1).
extension StubHouseUITests {

    // MARK: - getting there, with a control set first

    /// Reset the seed, set something up, then launch onto Calendar and wait
    /// for the header.
    ///
    /// ``openCalendar(_:port:)`` resets and launches in one step, so there is
    /// no moment in it at which a control can be set. The arrival marker is
    /// the same one and for the same reason: `new event` is drawn by this
    /// screen in all four of its states and by no other screen in the app,
    /// where Home draws this very fixture's events itself.
    @MainActor
    func openCalAfter(
        _ setup: @MainActor () -> Void,
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        port: Int = 6601
    ) -> (app: XCUIApplication, landed: Bool) {
        control("reset-fixtures", "{}", port: port, what: "the calendar seed")
        setup()
        let app = launch(mode: mode, port: port, push: "Calendar", pushDelay: Self.pushDelay)
        let arrived = app.buttons[CalendarWords.newEvent]
            .waitForExistence(timeout: Self.pushDelay + 40)
        if !arrived {
            print("=== THE CALENDAR PUSH NEVER LANDED — tree follows ===")
            print(app.debugDescription)
        }
        return (app, arrived)
    }

    /// The seven ISO dates of the Sunday-first week `offset` weeks from the
    /// one today is in.
    ///
    /// Computed with Foundation, for ``calendarISODay(_:)``'s reason: the
    /// point of the week case is that the **app** cut a week out of its own
    /// arithmetic, and a date read off the screen could not say that. Sunday
    /// first and not `Calendar.firstWeekday`, which is the device locale's
    /// answer — this screen's week starts Sunday like the dashboard it ports
    /// (``calendarWeekStart(_:)``).
    @MainActor
    func calWeekISODays(_ offset: Int = 0) -> [String] {
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = .current
        let weekday = gregorian.component(.weekday, from: Date())   // 1 = sunday
        let toSunday = -(weekday - 1) + 7 * offset
        return (0..<7).map { calendarISODay(toSunday + $0) }
    }

    /// `nov 1 – 7, 2026`, or `nov 29 – dec 5, 2026` across a month boundary —
    /// the week view's range label, computed independently of the app's own
    /// month table (``calendarMonthLabel(_:)``'s reason).
    ///
    /// Both spellings, because the collapsed one drops the month from the
    /// right-hand side and a week that crossed a boundary and used it would
    /// read `nov 29 – 5, 2026`. En dash, U+2013, in both.
    @MainActor
    func calWeekRangeLabel(_ offset: Int = 0) -> String {
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = .current
        let days = calWeekISODays(offset)

        func parts(_ iso: String) -> (month: String, day: Int, year: Int) {
            let reader = DateFormatter()
            reader.calendar = gregorian
            reader.timeZone = .current
            reader.locale = Locale(identifier: "en_US_POSIX")
            reader.dateFormat = "yyyy-MM-dd"
            let date = reader.date(from: iso) ?? Date()
            let shape = DateFormatter()
            shape.calendar = gregorian
            shape.timeZone = .current
            shape.locale = Locale(identifier: "en_US")
            shape.dateFormat = "MMM"
            let bits = gregorian.dateComponents([.day, .year], from: date)
            return (shape.string(from: date).lowercased(), bits.day ?? 1, bits.year ?? 1970)
        }

        let first = parts(days[0])
        let last = parts(days[6])
        if first.month == last.month {
            return "\(first.month) \(first.day) – \(last.day), \(last.year)"
        }
        return "\(first.month) \(first.day) – \(last.month) \(last.day), \(last.year)"
    }

    /// Set the fixture's crowd of past events, or clear it.
    @MainActor
    func setCalCrowd(_ count: Int, port: Int = 6601) {
        control(
            "calendar-crowd", "{\"count\":\(count)}", port: port,
            what: "the calendar's backfill of past events"
        )
    }

    /// Add or remove the two open-ended rows that straddle the agenda's
    /// floor.
    @MainActor
    func setCalOpenEnded(_ on: Bool, port: Int = 6601) {
        control(
            "calendar-open-ended", "{\"on\":\(on)}", port: port,
            what: "the calendar's open-ended rows"
        )
    }

    // MARK: - answer 14: the week

    /// **Seven day headers, with the agenda's rows under them.**
    ///
    /// Kamron's answer 14, as a screen. Four things fail it: a week that is
    /// Android's seven-column grid (there are no day headers in one), a week
    /// that drops its empty days (which would be an agenda with a shorter
    /// window and nothing a person could tell from the agenda), a week that
    /// shows a day outside it, and a week whose rows are a second kind of row
    /// — they must be the agenda's, so `pottery class` reads with its clock
    /// exactly as it does there.
    ///
    /// `nothing on` is the discriminating leaf: no other view in this app
    /// draws it, so it is also how this case knows it is looking at the week
    /// and not at the agenda, whose headers carry ISO dates too. At least
    /// three of the seven days are always empty — the seed puts events on
    /// today, tomorrow, today + 3 and today + 5, which is four distinct days
    /// at most.
    @MainActor
    func testCal_TheWeekShowsSevenDayHeadersWithTheAgendasRowsUnderThem() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalWords.week].tap()

        let label = calWeekRangeLabel()
        XCTAssertTrue(
            app.staticTexts[label].waitForExistence(timeout: 20),
            "the week's range label is not \(label)"
        )

        // **One pass down the week**, checking everything against whatever
        // is built at each step. Which day of the seven today falls on moves
        // with the date the train runs on, so there is no order in which
        // "scroll to the header" and "and the row is there" could both be
        // single assertions: `calendarScroll` only flicks downwards, and an
        // element it has scrolled past may have been recycled.
        var sawAnEmptyDay = false
        var sawTheAgendasRow = false

        // The one seeded row with a fixed wall clock, read exactly as the
        // agenda reads it — the week must draw the agenda's row and not a
        // second kind.
        let withRange = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label BEGINSWITH %@ AND label CONTAINS %@ AND label CONTAINS %@",
                StubCalendar.pottery, "11:00am", "12:30pm"
            )
        ).firstMatch

        for iso in calWeekISODays() {
            let header = app.descendants(matching: .any)
                .matching(NSPredicate(format: "label ENDSWITH %@", iso))
                .firstMatch
            XCTAssertTrue(
                calendarScroll(app, to: header, flicks: 20),
                "the week has no day header for \(iso)"
            )
            if app.staticTexts[CalWords.nothingOn].exists { sawAnEmptyDay = true }
            if withRange.exists { sawTheAgendasRow = true }
            // Six weeks back, so it is in no week this case ever draws.
            XCTAssertFalse(
                calendarRow(app, StubCalendar.fluJab).exists,
                "the week is drawing an event six weeks past"
            )
        }
        evidence(app, "cal-01-week")

        XCTAssertTrue(
            sawAnEmptyDay,
            "no day of the week says `nothing on` — either the empty days were "
                + "dropped, which makes this an agenda with a shorter window, or "
                + "this is still the agenda"
        )
        XCTAssertTrue(
            sawTheAgendasRow,
            "the week's row for the 11:00-12:30 event is not the agenda's row: no "
                + "row read `11:00am` and `12:30pm`"
        )
    }

    /// The week's chevron steps **seven days**, and `today` comes back.
    ///
    /// Asserted on the headers as well as on the label: a view that moved its
    /// label and not its groups would pass on the label alone, and that is
    /// exactly the shape of a week whose sections were keyed on the wrong
    /// anchor.
    @MainActor
    func testCal_TheWeeksChevronStepsSevenDaysAndTodayComesBack() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalWords.week].tap()

        let thisWeek = calWeekRangeLabel(0)
        let nextWeek = calWeekRangeLabel(1)
        XCTAssertTrue(
            app.staticTexts[thisWeek].waitForExistence(timeout: 20),
            "the week's range label is not \(thisWeek)"
        )

        app.buttons[CalendarWords.next].tap()
        XCTAssertTrue(
            app.staticTexts[nextWeek].waitForExistence(timeout: 20),
            "`next` did not step to \(nextWeek)"
        )
        XCTAssertFalse(app.staticTexts[thisWeek].exists, "both weeks' labels are on screen")

        // And the groups moved with it: next week's sunday has a header and
        // this week's sunday does not.
        let movedTo = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label ENDSWITH %@", calWeekISODays(1)[0]))
            .firstMatch
        XCTAssertTrue(
            calendarScroll(app, to: movedTo, flicks: 20),
            "the label stepped but the day headers did not"
        )
        XCTAssertFalse(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label ENDSWITH %@", calWeekISODays(0)[0]))
                .firstMatch.exists,
            "this week's sunday is still on screen in next week's view"
        )
        evidence(app, "cal-02-week-stepped")

        app.buttons[CalendarWords.today].tap()
        XCTAssertTrue(
            app.staticTexts[thisWeek].waitForExistence(timeout: 20),
            "`today` did not come back to \(thisWeek)"
        )
    }

    /// The `nothing on` line grows with the type size.
    ///
    /// The week's other leaves — the day header, its date, the row — are
    /// measured by ``testCalendar_RowsGrowAtAccessibilitySize()``, because
    /// they are the agenda's. This one is the week's own, and a `.caption`
    /// that had been given a fixed frame or a `minimumScaleFactor` would be
    /// the one thing the week added that nothing measured.
    @MainActor
    func testCal_TheWeeksEmptyDayLineGrowsAtAccessibilitySize() throws {
        func lineHeight(_ app: XCUIApplication) -> CGFloat? {
            app.buttons[CalWords.week].tap()
            let line = app.staticTexts[CalWords.nothingOn]
            guard calendarScroll(app, to: line, flicks: 20) else { return nil }
            return line.frame.height
        }

        let ordinary = openCalendar()
        XCTAssertTrue(ordinary.landed, "the agenda never drew a row at the ordinary size")
        guard let small = lineHeight(ordinary.app) else {
            return XCTFail("no `nothing on` line to measure at the ordinary size")
        }
        evidence(ordinary.app, "cal-03-empty-day-large")
        ordinary.app.terminate()

        // `landed` is deliberately not asserted at AX5, as
        // ``testCalendar_RowsGrowAtAccessibilitySize()`` does not assert it
        // either: at that size the row it waits for is below the first build,
        // and the scroll inside `lineHeight` is what reaches anything.
        let big = openCalendar("UICTContentSizeCategoryAccessibilityXXXL")
        guard let grown = lineHeight(big.app) else {
            return XCTFail("the `nothing on` line is not reachable at AX5")
        }
        evidence(big.app, "cal-03-empty-day-ax5")
        print("MEASURED week empty-day line: ordinary \(small) -> AX5 \(grown)")
        XCTAssertGreaterThan(
            grown, small * 1.5,
            "the week's `nothing on` line barely grew: \(small) -> \(grown)"
        )
    }

    /// Apple's audit over the **week**, which the screen's own audit case
    /// cannot cover: an audit only covers the states it is launched in, and
    /// that one was written when there were four.
    ///
    /// Classified by signature against the measured edges, through the same
    /// ``calendarAudit(_:_:edges:)`` the other four states use.
    @MainActor
    func testCal_TheWeekAudits() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")

        // Measured on the agenda, before anything is tapped, for the reason
        // ``calendarEdges(_:)`` gives.
        let edges = calendarEdges(app)
        XCTAssertFalse(edges.isEmpty, "there is no measured edge to classify a finding against")

        app.buttons[CalWords.week].tap()
        XCTAssertTrue(
            app.staticTexts[CalWords.nothingOn].waitForExistence(timeout: 20),
            "the week did not draw"
        )
        let week = try calendarAudit(app, "cal-week", edges: edges)
        evidence(app, "cal-04-audit-week")
        print("AUDIT cal week: floor \(week.floor), unexplained \(week.unexplained.count)")
        XCTAssertEqual(
            [], week.unexplained,
            "Apple's audit has findings against the week beyond the measured "
                + "floor: " + week.unexplained.joined(separator: " / ")
        )
    }

    // MARK: - answer 16: the window is really asked for

    /// **A house with more history than one read can carry still shows
    /// today.**
    ///
    /// This is the defect answer 16 fixes, and the only shape of it a UI case
    /// can see. The route is `ORDER BY starts_at ASC LIMIT 500`, so the read
    /// that sent **no parameters** got the *oldest* 500 events in the house —
    /// and with 600 events behind us, today was not among them: the agenda
    /// drew `nothing on the calendar` in a house with a full calendar.
    ///
    /// Both halves are needed. `pottery class` present says the window found
    /// today; no `backfill` anywhere says the window has a floor rather than
    /// the client throwing the past away after reading it, which is what the
    /// old code did and which looks identical on screen.
    @MainActor
    func testCal_ABoundedReadFindsTodayInAHouseWithYearsBehindIt() throws {
        let (app, landed) = openCalAfter { setCalCrowd(600) }
        defer { setCalCrowd(0) }
        XCTAssertTrue(landed, "the calendar push never landed")

        let today = calendarRow(app, StubCalendar.pottery)
        let drew = today.waitForExistence(timeout: 40)
        evidence(app, "cal-05-crowded-house")
        XCTAssertTrue(
            drew,
            "the agenda has no row for today in a house with 600 past events — the "
                + "read is unparameterised again, so the server answered with the "
                + "oldest 500 and today was not in them"
        )
        XCTAssertFalse(
            app.staticTexts[CalendarWords.emptyAgendaTitle].exists,
            "a house with a full calendar is being drawn as an empty one"
        )
        XCTAssertEqual(
            0,
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label CONTAINS %@", CalWords.backfill))
                .count,
            "a `backfill` row is on screen: the past arrived and was filtered "
                + "client-side rather than never being asked for"
        )
    }

    // MARK: - answer 17: the hour

    /// **An open-ended event is still on screen fifty minutes after it
    /// started, and gone after seventy.**
    ///
    /// Kamron's answer 17, as a screen. The agenda's floor is **today's
    /// midnight** and not now, so an open-ended event from this morning is
    /// drawn all day under either rule — the hour only shows at the day
    /// boundary, which is where the fixture's two rows sit: `porch light`
    /// starts 23:10 yesterday (fifty minutes before the floor, invented end
    /// 00:10 today, so it stays) and `attic hatch` starts 22:50 (seventy
    /// minutes before it, invented end 23:50 yesterday, so it goes).
    ///
    /// Under the rule this replaced — the end falls back to the start —
    /// **both** were dropped, so the pair is what tells the two apart. The
    /// rows group under yesterday's date, which is the one header the agenda
    /// never had before.
    @MainActor
    func testCal_AnOpenEndedEventStaysAnHourAndThenGoes() throws {
        let (app, landed) = openCalAfter { setCalOpenEnded(true) }
        defer { setCalOpenEnded(false) }
        XCTAssertTrue(landed, "the calendar push never landed")

        // The read's marker is **today's group header**, not a row: `porch
        // light` is the row under test, so waiting for it would turn "the
        // feature is broken" into "the read never landed". Today's header is
        // third in the list whatever happens, so it is always built.
        let todayHeader = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", "today \(calendarISODay(0))"))
            .firstMatch
        XCTAssertTrue(
            todayHeader.waitForExistence(timeout: 40),
            "the agenda never drew a group — the calendar read did not land"
        )

        let fifty = calendarRow(app, CalWords.porchLight)
        let stillOn = fifty.exists
        evidence(app, "cal-06-open-ended")
        XCTAssertTrue(
            stillOn,
            "`porch light` started fifty minutes before the agenda's floor with no "
                + "`ends_at` and is not on screen — answer 17's hour is not being kept"
        )
        XCTAssertFalse(
            fifty.label.contains(" – "),
            "the invented hour reached the clock: \(fifty.label)"
        )
        XCTAssertFalse(
            calendarRow(app, CalWords.atticHatch).exists,
            "`attic hatch` started seventy minutes before the floor and is still on "
                + "screen — the hour has become for ever"
        )

        // And it is under **yesterday's** header, not moved to today: "on
        // screen for an hour" is about being drawn at all.
        let yesterday = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label ENDSWITH %@", calendarISODay(-1)))
            .firstMatch
        XCTAssertTrue(
            yesterday.exists,
            "there is no group for yesterday, though a row from it is on screen"
        )
        XCTAssertLessThan(
            yesterday.frame.minY, fifty.frame.minY,
            "`porch light` is not under yesterday's header"
        )
    }
}

/// The words this file matches on that `CalendarWords` does not have.
///
/// Spelt out rather than imported from `CalendarStrings`, which is this
/// suite's convention everywhere: a query that silently followed a renamed
/// constant would stop testing what it claims to.
enum CalWords {
    /// The toggle's fourth word, added by answer 14.
    static let week = "week"

    /// What a day of the week with nothing on it says
    /// (``CalendarStrings/nothingOnThisDay``) — iOS-only, and drawn by no
    /// other view in the app.
    static let nothingOn = "nothing on"

    /// The fixture's crowd of past events, whose titles are `backfill 0`
    /// upwards. The word is in no other row's title, location or
    /// description.
    static let backfill = "backfill"

    /// 23:10 yesterday with no `ends_at` — fifty minutes before the agenda's
    /// floor.
    static let porchLight = "porch light"

    /// 22:50 yesterday with no `ends_at` — seventy minutes before it.
    static let atticHatch = "attic hatch"
}
