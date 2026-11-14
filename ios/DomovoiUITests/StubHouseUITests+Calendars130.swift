import XCTest

/// Calendars 1.3.0 (iOS batch 2026-11-14, unit `cal`): Home's today / next
/// card, read-only events from linked and phone calendars, and Settings →
/// Phone calendars — against the stub house, whose calendar half is
/// `design-notes/ios/batch-2026-11-14/cal/stubhouse.patch` (`fixture_cal130`)
/// on top of `fixtures/calendar.py`.
///
/// The imported rows sit behind `POST /api/_test/calendar-imported` and are
/// **off by default**, so no other case sees them; every case here resets
/// the house first, and turns them off again on the way out.
///
/// **No case turns Phone calendars on.** That raises iOS's own calendar
/// prompt, and what it reads is the Simulator's calendars, not a person's:
/// both are device checks (the report lists them). What is asserted here is
/// everything before the prompt — the section, the disclosure and the words
/// about what an iPhone can't do — and that the switch starts off.
///
/// **The connection sheet is never opened.**
extension StubHouseUITests {

    private enum Cal130 {
        static let cardTitle = "today / next"
        static let standup = "team standup"       // phone calendar "Work", 30 min from now
        static let schoolRun = "school run"       // linked calendar "School"
        static let holiday = "bank holiday"       // all day tomorrow
        static let allDay = "all day"
        static let schoolNote = "from School \u{2014} change it there"
        static let workNote = "from Work on Alex's iPhone \u{2014} change it on that phone"
        static let household = "household"
        static let section = "Phone calendars"
        static let everyone = "Everyone in your household can see them, and Domovoi can read them out in any room."
        static let timing = "doesn't let apps update on a schedule in the background"
        static let privateNote = "doesn't tell apps which events are marked private"
    }

    @MainActor
    private func cal130Imported(on: Bool, alone: Bool = false) {
        control("calendar-imported", "{\"on\":\(on),\"alone\":\(alone)}", port: 6601,
                what: "the imported calendars")
    }

    @MainActor
    private func cal130Find(_ app: XCUIApplication, _ text: String, timeout: Double = 30) -> Bool {
        let found = containing(app, text).waitForExistence(timeout: timeout)
        if !found {
            print("=== NO \"\(text)\" — tree follows ===")
            print(app.debugDescription)
        }
        return found
    }

    // MARK: - Home

    /// The card is "today / next", and an event from a phone and a linked
    /// calendar reach it — with an all-day one saying `all day` in its time
    /// column. `alone` hides the household's own rows so the phone's three
    /// rows are these, whatever the hour the train runs at.
    @MainActor
    func testCalendars130_HomeCardShowsTheWeekAcrossEveryCalendar() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house")
        cal130Imported(on: true, alone: true)
        defer { cal130Imported(on: false) }
        let app = launch()
        XCTAssertTrue(cal130Find(app, Cal130.cardTitle), "Home's card is not `today / next`")
        XCTAssertTrue(cal130Find(app, Cal130.standup), "a phone calendar's event is not on Home")
        XCTAssertTrue(cal130Find(app, Cal130.holiday), "tomorrow's all-day event is not on Home")
        XCTAssertTrue(cal130Find(app, Cal130.allDay), "an all-day row does not say `all day`")
        evidence(app, "calendars130-01-home")
    }

    // MARK: - Calendar

    /// An event from a linked calendar opens read-only: its calendar's name,
    /// the note that says where it changes, and **no edit and no delete**.
    @MainActor
    func testCalendars130_ALinkedEventIsReadOnlyAndSaysWhereItChanges() throws {
        let (app, landed) = openCalAfter({ cal130Imported(on: true) })
        defer { cal130Imported(on: false) }
        XCTAssertTrue(landed, "Calendar never drew")
        let row = calendarRow(app, Cal130.schoolRun)
        XCTAssertTrue(calendarScroll(app, to: row), "the linked event is not in the agenda")
        row.tap()
        XCTAssertTrue(cal130Find(app, Cal130.schoolNote, timeout: 15), "the read-only note is missing")
        evidence(app, "calendars130-02-linked")
        XCTAssertTrue(app.staticTexts["School"].exists, "the badge does not name the calendar")
        // V057: the same event in Galaxy's phone calendar is listed once,
        // and the sheet names it quietly.
        XCTAssertTrue(containing(app, "also in Family \u{00B7} Galaxy").exists, "no `also in` line")
        XCTAssertFalse(app.buttons[CalendarWords.edit].exists, "a read-only event offers edit")
        XCTAssertFalse(app.buttons[CalendarWords.delete].exists, "a read-only event offers delete")
        XCTAssertFalse(startingWith(app, "event #").exists, "a read-only event shows the house's row number")
    }

    /// A phone's event names the phone, and also offers nothing to change.
    @MainActor
    func testCalendars130_APhoneEventNamesThePhone() throws {
        let (app, landed) = openCalAfter({ cal130Imported(on: true) })
        defer { cal130Imported(on: false) }
        XCTAssertTrue(landed, "Calendar never drew")
        let row = calendarRow(app, Cal130.standup)
        XCTAssertTrue(calendarScroll(app, to: row), "the phone's event is not in the agenda")
        row.tap()
        XCTAssertTrue(cal130Find(app, Cal130.workNote, timeout: 15), "the phone's note is missing")
        XCTAssertFalse(app.buttons[CalendarWords.edit].exists, "a phone's event offers edit")
    }

    /// The household's own event is unchanged in what it offers, and its
    /// badge now says `household` (it said `local` before 1.3.0).
    @MainActor
    func testCalendars130_TheHouseholdsOwnEventKeepsEditAndSaysHousehold() throws {
        let (app, landed) = openCalAfter({ cal130Imported(on: true) })
        defer { cal130Imported(on: false) }
        XCTAssertTrue(landed, "Calendar never drew")
        XCTAssertTrue(openCalendarEvent(app, StubCalendar.pottery, number: "event #13"),
                      "the household's own event did not open")
        XCTAssertTrue(app.staticTexts[Cal130.household].exists, "the badge does not say household")
        XCTAssertTrue(app.buttons[CalendarWords.edit].exists, "the household's own event lost edit")
    }

    // MARK: - Settings → Phone calendars

    /// The section is in Household, says what is sent and who sees it
    /// before anything is asked, says what an iPhone can't do, and starts
    /// off. The switch is NOT flipped: that is iOS's own prompt.
    @MainActor
    func testCalendars130_PhoneCalendarsSaysEverythingBeforeItAsks() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house")
        let app = launch(push: "Settings", pushDelay: 2)
        let group = app.buttons[ShellStringsUnderTest.householdGroup]
        guard group.waitForExistence(timeout: 30), printingScrollToHittable(app, group) else {
            print(app.debugDescription)
            return XCTFail("Settings never drew its Household group")
        }
        group.tap()
        let section = app.buttons[Cal130.section]
        guard section.waitForExistence(timeout: 15), printingScrollToHittable(app, section) else {
            print(app.debugDescription)
            return XCTFail("Household has no Phone calendars section")
        }
        section.tap()
        XCTAssertTrue(cal130Find(app, Cal130.everyone, timeout: 15), "the disclosure does not say who sees them")
        XCTAssertTrue(cal130Find(app, Cal130.privateNote, timeout: 5), "no word about private events")
        XCTAssertTrue(cal130Find(app, Cal130.timing, timeout: 5), "no word about what an iPhone can't do")
        let toggle = app.switches.firstMatch
        XCTAssertTrue(toggle.waitForExistence(timeout: 5), "no switch")
        XCTAssertEqual("0", toggle.value as? String, "sharing is on before anyone turned it on")
        evidence(app, "calendars130-03-panel")
    }
}
