import XCTest

/// Calendar, against the stub house — the screen drawing the fixture's data,
/// every control's effect, both empty wordings, a failed read, Dynamic Type
/// measured at two sizes, and one audit over four states.
///
/// The fixture is `design-notes/ios/stubhouse/fixtures/calendar.py`, which
/// owns `/api/calendar/events` and all five of its methods. **Every case
/// resets it first**: the create, edit and delete cases mutate that module
/// and the whole run shares one house.
///
/// The words matched on are spelt out in ``CalendarWords`` and
/// ``StubCalendar`` below rather than imported from `CalendarStrings`, which
/// is this file's convention everywhere: a query that silently followed a
/// renamed constant would stop testing what it claims to.
///
/// **The connection sheet is never opened** (NIGHT-RULES §1): every case
/// launches with the server already saved and the route seeded through
/// `-domovoi-audit-push`, so nothing here starts a LAN sweep.
extension StubHouseUITests {

    // MARK: - getting there

    /// Reset the seed, launch onto Calendar, and wait for a row only a
    /// completed read can draw.
    ///
    /// The marker is the `pottery class` row and not the page title: `Calendar`
    /// is on screen from the first frame as a launcher tile, and the subtitle
    /// is drawn with a count of zero before the read lands — both are true of
    /// a screen that never got an answer. A row with a clock in it is not.
    @MainActor
    func openCalendar(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        port: Int = 6601
    ) -> (app: XCUIApplication, landed: Bool) {
        control("reset-fixtures", "{}", port: port, what: "the calendar seed")
        let app = launch(mode: mode, port: port, push: "Calendar", pushDelay: Self.pushDelay)
        return (app, calendarRow(app, StubCalendar.pottery).waitForExistence(timeout: 40))
    }

    /// Anything whose label starts with `title` — a row, a month cell or a
    /// day-grid block, all of which label themselves with the event's title
    /// first.
    ///
    /// `.any` rather than `.buttons`: a row is a `Button` and a day header is
    /// not, and a query that guessed wrong would report a screen as empty.
    @MainActor
    func calendarRow(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label BEGINSWITH %@", title))
            .firstMatch
    }

    /// Flick the page until `element` is built.
    ///
    /// **Not ``scrollUntilBuilt(_:_:flicks:)``**, which scrolls
    /// `app.collectionViews.firstMatch`: this screen's agenda is a
    /// `LazyVStack` inside a `ScrollView`, so there is no collection view to
    /// find and that helper would report "no list to scroll" for every row
    /// below the fold. Swiping the application swipes its key window, which
    /// cannot pick the view toggle's own horizontal scroll view by mistake.
    /// `.slow` for the harness's reason: a plain `swipeUp()` steps over rows.
    @MainActor
    func calendarScroll(_ app: XCUIApplication, to element: XCUIElement, flicks: Int = 12) -> Bool {
        for _ in 0..<flicks {
            if element.exists { return true }
            app.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// One of the editor's four date/time controls, by its section word and
    /// what it currently holds.
    ///
    /// By **both**, because the two buttons in a row share the section word:
    /// `starts at` names a date button and a time button, and the value is
    /// what tells them apart. Picking by label alone is the `firstMatch`
    /// trap this harness was written around.
    @MainActor
    func calendarStepButton(
        _ app: XCUIApplication,
        _ label: String,
        holding value: String
    ) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(format: "label == %@ AND value == %@", label, value))
            .firstMatch
    }

    /// Open the detail sheet on a row, and wait for the sheet's own line.
    @MainActor
    func openCalendarEvent(_ app: XCUIApplication, _ title: String, number: String) -> Bool {
        let row = calendarRow(app, title)
        guard calendarScroll(app, to: row) else { return false }
        row.tap()
        let marker = app.staticTexts[number].firstMatch
        let open = marker.waitForExistence(timeout: 15)
        if !open {
            print("=== NO DETAIL SHEET for \(title) — tree follows ===")
            print(app.debugDescription)
        }
        return open
    }

    /// Today, tomorrow or any other day as `2026-11-02`, the agenda header's
    /// right-hand half.
    ///
    /// Computed here, with Foundation, rather than read off the screen: the
    /// point of ``testCalendar_TodaysGroupSaysToday()`` is that the app's own
    /// header branch chose `today` for one date and a day label for another,
    /// and a date the app produced could not say that.
    @MainActor
    func calendarISODay(_ offset: Int) -> String {
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = .current
        let day = gregorian.date(byAdding: .day, value: offset, to: Date()) ?? Date()
        let shape = DateFormatter()
        shape.calendar = gregorian
        shape.timeZone = .current
        shape.locale = Locale(identifier: "en_US_POSIX")
        shape.dateFormat = "yyyy-MM-dd"
        return shape.string(from: day)
    }

    /// `november 2026` — the month view's range label, independently of the
    /// app's own month table.
    @MainActor
    func calendarMonthLabel(_ offset: Int) -> String {
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = .current
        let day = gregorian.date(byAdding: .month, value: offset, to: Date()) ?? Date()
        let shape = DateFormatter()
        shape.calendar = gregorian
        shape.timeZone = .current
        shape.locale = Locale(identifier: "en_US")
        shape.dateFormat = "LLLL yyyy"
        return shape.string(from: day).lowercased()
    }

    // MARK: - the agenda

    /// The window, the grouping and the clock.
    ///
    /// Three things fail it: a row under the wrong day header, a 24-hour or
    /// `11:00 AM`-spelled clock, and an open-ended event drawn with a range.
    /// `pottery class` is the one seeded row with a **fixed wall clock** —
    /// the other today rows are relative to the run's own time and have no
    /// stable string — and `night walk` is the one with no `ends_at`, so
    /// `11:15pm – 11:15pm` means the end fell back to the start on screen as
    /// well as in the rule.
    @MainActor
    func testCalendar_AgendaGroupsDaysAndShowsClocks() throws {
        let (app, landed) = openCalendar()
        evidence(app, "calendar-01-agenda")
        XCTAssertTrue(landed, "the agenda never drew a row — the calendar read did not land")

        let withRange = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label BEGINSWITH %@ AND label CONTAINS %@ AND label CONTAINS %@",
                StubCalendar.pottery, "11:00am", "12:30pm"
            )
        ).firstMatch
        XCTAssertTrue(
            withRange.exists,
            "the 11:00-12:30 row did not read `11:00am` and `12:30pm` — a 24-hour "
                + "clock, a capitalised meridiem or a lost end"
        )

        let openEnded = calendarRow(app, StubCalendar.night)
        XCTAssertTrue(
            calendarScroll(app, to: openEnded),
            "the open-ended event at 23:15 is not in the agenda at all"
        )
        XCTAssertTrue(
            openEnded.label.contains("11:15pm"),
            "the open-ended row's clock is \(openEnded.label)"
        )
        XCTAssertFalse(
            openEnded.label.contains(" – "),
            "an event with no `ends_at` was drawn as a range: \(openEnded.label)"
        )

        XCTAssertFalse(
            calendarRow(app, StubCalendar.fluJab).exists,
            "the agenda drew an event six weeks past — its floor is the anchor's midnight"
        )
    }

    /// `CalendarScreen.kt:348`'s special case, both halves.
    ///
    /// Today's header must be the literal `today` **and** tomorrow's must not
    /// be: a header that lost the branch fails the first, and one that always
    /// says `today` fails the second. The ISO date beside it is what pins
    /// each header to a date without reading the app's own month table.
    @MainActor
    func testCalendar_TodaysGroupSaysToday() throws {
        let (app, landed) = openCalendar()
        let todayHeader = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", "today \(calendarISODay(0))"))
            .firstMatch
        evidence(app, "calendar-02-today-header")
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            todayHeader.exists,
            "today's group is not headed `today` beside \(calendarISODay(0))"
        )

        let tomorrowISO = calendarISODay(1)
        let tomorrowHeader = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label ENDSWITH %@", tomorrowISO))
            .firstMatch
        XCTAssertTrue(
            calendarScroll(app, to: tomorrowHeader),
            "there is no group for tomorrow, though `swim lesson` is seeded at 07:00"
        )
        XCTAssertFalse(
            tomorrowHeader.label.hasPrefix("today"),
            "tomorrow's group is also headed `today`: \(tomorrowHeader.label)"
        )
    }

    // MARK: - the detail sheet

    /// The four fields `HomeEvent` cannot carry.
    ///
    /// `brought over from google` is seeded as event 4's **description** and
    /// appears in no title, no location and no other row, so a decoder that
    /// dropped `description` fails this even though the sheet still opens —
    /// which is exactly what a second decoder of one server shape costs if it
    /// is written short. The pill proves `source` decoded and `event #4`
    /// proves it is the right row.
    @MainActor
    func testCalendar_DetailSheetShowsPlaceNoteAndSource() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        let open = openCalendarEvent(app, StubCalendar.offsite, number: "event #4")
        evidence(app, "calendar-03-detail")
        XCTAssertTrue(open, "the detail sheet did not open on the google event")

        XCTAssertTrue(
            containing(app, StubCalendar.googleNote).exists,
            "the sheet drew no description — `description` is one of the four keys "
                + "this screen decodes and Home's model does not"
        )
        XCTAssertTrue(
            containing(app, StubCalendar.offsitePlace).exists,
            "the sheet drew no location"
        )
        XCTAssertTrue(
            app.staticTexts[StubCalendar.googleSource].exists,
            "the source pill does not say `google` — `source` did not decode"
        )
    }

    /// `relTime` wired to `last_synced_at`, and the line's absence on a local
    /// row.
    ///
    /// Event 4's `last_synced_at` is 15 minutes old, which `relTime` writes
    /// as `15m ago`; event 13 has none, and a sheet that drew `synced —` or
    /// `synced 56y ago` there would be reporting a null as a time.
    @MainActor
    func testCalendar_GoogleRowSaysWhenItSynced() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            openCalendarEvent(app, StubCalendar.offsite, number: "event #4"),
            "the detail sheet did not open on the google event"
        )
        let synced = app.staticTexts[CalendarWords.synced15m]
        evidence(app, "calendar-04-synced")
        XCTAssertTrue(synced.exists, "the google row does not say when it was synced")

        app.buttons[CalendarWords.close].tap()
        XCTAssertTrue(
            openCalendarEvent(app, StubCalendar.pottery, number: "event #13"),
            "the detail sheet did not open on the local event"
        )
        XCTAssertFalse(
            startingWith(app, "synced").exists,
            "a local row with no `last_synced_at` still claims to have been synced"
        )
    }

    // MARK: - creating

    /// The POST, the 201's `id`, and the selection that follows it.
    ///
    /// The toast is asserted **first**: it is up for 2.4 seconds
    /// (`ShellStrings.toastMs`). `event #14` is the discriminating half —
    /// the id comes out of the 201's own body (`calendar.py:110`) and not
    /// from a re-read, so a screen that selected the row by guessing would
    /// open the wrong sheet or none.
    @MainActor
    func testCalendar_NewEventCreatesAndSelectsIt() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")

        app.buttons[CalendarWords.newEvent].tap()
        let field = app.textFields[CalendarWords.titleLabel]
        XCTAssertTrue(field.waitForExistence(timeout: 15), "the editor did not open")
        field.tap()
        app.typeText(StubCalendar.created)
        app.buttons[CalendarWords.createEvent].tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", CalendarWords.eventCreated))
            .firstMatch
        let said = toast.waitForExistence(timeout: 20)
        let numbered = app.staticTexts["event #14"].firstMatch.waitForExistence(timeout: 20)
        evidence(app, "calendar-05-created")
        XCTAssertTrue(said, "nothing was said about the create")
        XCTAssertTrue(
            numbered,
            "the new event was not selected as `event #14` — the 201's `id` was not read"
        )
        XCTAssertTrue(
            containing(app, StubCalendar.created).exists,
            "the sheet that opened is not the event just created"
        )

        app.buttons[CalendarWords.close].tap()
        XCTAssertTrue(
            calendarScroll(app, to: calendarRow(app, StubCalendar.created)),
            "the created event is not in the agenda after the refresh"
        )
    }

    /// `title is required`, and that nothing was sent.
    ///
    /// The subtitle is the witness that no request went: it counts the whole
    /// list (`CalendarScreen.kt:145`), so a POST that slipped past the
    /// validation would make it read `14 events` after the refresh. The
    /// editor must also still be open — a dialog that closed on a refusal
    /// would have thrown the draft away.
    @MainActor
    func testCalendar_BlankTitleIsRefused() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            app.staticTexts[CalendarWords.thirteenEvents].exists,
            "the header does not count the seeded thirteen — the subtitle counts "
                + "the whole list, so this is what any later count is measured against"
        )

        app.buttons[CalendarWords.newEvent].tap()
        XCTAssertTrue(
            app.textFields[CalendarWords.titleLabel].waitForExistence(timeout: 15),
            "the editor did not open"
        )
        app.buttons[CalendarWords.createEvent].tap()

        let refusal = app.staticTexts[CalendarWords.titleRequired]
        let refused = refusal.waitForExistence(timeout: 10)
        evidence(app, "calendar-06-blank-title")
        XCTAssertTrue(refused, "a blank title was not refused")
        XCTAssertTrue(
            app.textFields[CalendarWords.titleLabel].exists,
            "the editor closed on a refusal, taking the draft with it"
        )
        XCTAssertFalse(
            app.staticTexts[CalendarWords.eventCreated].exists,
            "something was created from a blank title"
        )

        app.buttons[CalendarWords.cancel].tap()
        XCTAssertTrue(
            app.staticTexts[CalendarWords.thirteenEvents].waitForExistence(timeout: 15),
            "the list grew although the save was refused"
        )
    }

    /// `end must be after start` (`EventEditor.kt:266`).
    ///
    /// Driven on event 13, the one row with a fixed wall clock: its end is
    /// moved from `12:30pm` back to `9:30am`, which is before its 11:00 start
    /// whatever time the run happens at. A wheel and not a typed time —
    /// `adjust(toPickerWheelValue:)` is exact, where a tap into a compact
    /// field puts the caret wherever it lands.
    @MainActor
    func testCalendar_EndBeforeStartIsRefused() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            openCalendarEvent(app, StubCalendar.pottery, number: "event #13"),
            "the detail sheet did not open"
        )
        app.buttons[CalendarWords.edit].tap()

        let endTime = calendarStepButton(app, CalendarWords.endsAt, holding: "12:30pm")
        XCTAssertTrue(endTime.waitForExistence(timeout: 15), "the editor has no `ends at` time")
        endTime.tap()
        XCTAssertTrue(
            app.pickerWheels.element(boundBy: 0).waitForExistence(timeout: 10),
            "the time picker did not expand"
        )
        app.pickerWheels.element(boundBy: 0).adjust(toPickerWheelValue: "9")
        app.pickerWheels.element(boundBy: 2).adjust(toPickerWheelValue: "AM")
        app.buttons[CalendarWords.save].tap()

        let refusal = app.staticTexts[CalendarWords.endAfterStart]
        let refused = refusal.waitForExistence(timeout: 10)
        evidence(app, "calendar-07-end-before-start")
        XCTAssertTrue(refused, "an end before the start was not refused")
        XCTAssertFalse(
            app.staticTexts[CalendarWords.eventUpdated].exists,
            "the PATCH went anyway"
        )
    }

    /// `EventEditor.kt:224-231` — the end moves with the start.
    ///
    /// Event 13 is 11:00-12:30, an hour and a half. Moving the start to
    /// 8:00am must leave the end reading `9:30am`; a rule that dropped the
    /// duration would leave `12:30pm`, and that is asserted too, because
    /// "9:30am exists" would also be true of an editor that had simply
    /// grown a third control.
    @MainActor
    func testCalendar_MovingTheStartKeepsTheDuration() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            openCalendarEvent(app, StubCalendar.pottery, number: "event #13"),
            "the detail sheet did not open"
        )
        app.buttons[CalendarWords.edit].tap()

        let startTime = calendarStepButton(app, CalendarWords.startsAt, holding: "11:00am")
        XCTAssertTrue(startTime.waitForExistence(timeout: 15), "the editor has no `starts at` time")
        startTime.tap()
        XCTAssertTrue(
            app.pickerWheels.element(boundBy: 0).waitForExistence(timeout: 10),
            "the time picker did not expand"
        )
        app.pickerWheels.element(boundBy: 0).adjust(toPickerWheelValue: "8")

        let movedEnd = calendarStepButton(app, CalendarWords.endsAt, holding: "9:30am")
        let moved = movedEnd.waitForExistence(timeout: 10)
        evidence(app, "calendar-08-duration-kept")
        XCTAssertTrue(
            moved,
            "the end did not follow the start by the same hour and a half"
        )
        XCTAssertFalse(
            calendarStepButton(app, CalendarWords.endsAt, holding: "12:30pm").exists,
            "the end stayed where it was while the start moved"
        )
    }

    // MARK: - editing and deleting

    /// The PATCH path and `event updated`, with the row still open.
    ///
    /// The new title shares no prefix with the old one on purpose: asserting
    /// `pottery class moved` is there says nothing about whether
    /// `pottery class` went, because one begins with the other.
    @MainActor
    func testCalendar_EditSavesAndKeepsTheRowSelected() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            openCalendarEvent(app, StubCalendar.pottery, number: "event #13"),
            "the detail sheet did not open"
        )
        app.buttons[CalendarWords.edit].tap()

        let field = app.textFields[CalendarWords.titleLabel]
        XCTAssertTrue(field.waitForExistence(timeout: 15), "the editor did not open")
        // The caret goes where the tap lands, so it is put at the far right
        // and the field emptied from there.
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        clearField(app)
        app.typeText(StubCalendar.renamed)
        app.buttons[CalendarWords.save].tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", CalendarWords.eventUpdated))
            .firstMatch
        let said = toast.waitForExistence(timeout: 20)
        let renamed = containing(app, StubCalendar.renamed).waitForExistence(timeout: 20)
        evidence(app, "calendar-09-edited")
        XCTAssertTrue(said, "nothing was said about the edit")
        XCTAssertTrue(renamed, "the sheet still shows the old title after the PATCH")
        XCTAssertTrue(
            app.staticTexts["event #13"].firstMatch.exists,
            "the edited row is no longer the open one"
        )

        app.buttons[CalendarWords.close].tap()
        XCTAssertTrue(
            calendarScroll(app, to: calendarRow(app, StubCalendar.renamed)),
            "the renamed row is not in the agenda"
        )
        XCTAssertFalse(
            calendarRow(app, StubCalendar.pottery).exists,
            "the old title is still on a row — the PATCH did not replace it"
        )
    }

    /// The confirm, the 204, and the row going.
    ///
    /// The 204 is the half worth naming: the route answers it with **no
    /// body** (`calendar.py:172`), so a delete written with
    /// `DomovoiClient.delete(_:as:)` would throw on the empty reply and this
    /// case would see `delete failed:` instead of `deleted "swim lesson"` —
    /// a delete that worked and said it had not.
    @MainActor
    func testCalendar_DeleteAsksFirstThenRemovesTheRow() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        XCTAssertTrue(
            openCalendarEvent(app, StubCalendar.swim, number: "event #5"),
            "the detail sheet did not open on tomorrow's event"
        )
        app.buttons[CalendarWords.delete].tap()

        let alert = app.alerts.firstMatch
        let asked = alert.waitForExistence(timeout: 15)
        evidence(app, "calendar-10-delete-confirm")
        XCTAssertTrue(asked, "the delete was not confirmed first")
        XCTAssertTrue(
            alert.staticTexts[StubCalendar.swimDeleteBody].exists,
            "the confirmation does not name the event: \(alert.debugDescription)"
        )
        // Scoped to the alert: the sheet behind it has a `delete` of its own.
        alert.buttons[CalendarWords.delete].tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", StubCalendar.swimDeletedToast))
            .firstMatch
        let said = toast.waitForExistence(timeout: 20)
        evidence(app, "calendar-10-deleted")
        XCTAssertTrue(said, "nothing was said about the delete")
        XCTAssertFalse(
            calendarRow(app, StubCalendar.swim).exists,
            "the deleted row is still in the agenda"
        )
    }

    // MARK: - the two grids

    /// The month cell's marks, its count, and the tap that opens a day.
    ///
    /// Four events are seeded on one day five days out — the only day in the
    /// fixture with exactly four — so `4 events` is that cell's
    /// accessibility value and nothing else's. The dot cap itself is not
    /// queryable (a `Circle` is not an element); it is
    /// ``CalendarMonthCell/overflow``, pinned by the package tests, and what
    /// this case proves is the count the cell publishes and the date its tap
    /// lands on.
    @MainActor
    func testCalendar_MonthShowsDotsAndOpensADay() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalendarWords.month].tap()

        let cell = app.buttons
            .matching(NSPredicate(format: "value == %@", CalendarWords.fourEvents))
            .firstMatch
        let found = cell.waitForExistence(timeout: 20)
        evidence(app, "calendar-11-month")
        XCTAssertTrue(found, "no month cell says it holds four events")
        XCTAssertTrue(
            app.staticTexts[CalendarWords.monthHint].exists,
            "the dot grid draws no hint saying a day can be tapped"
        )

        cell.tap()
        XCTAssertTrue(
            calendarRow(app, StubCalendar.hedge).waitForExistence(timeout: 20),
            "the cell's tap did not open that day"
        )
        for title in [StubCalendar.parcel, StubCalendar.vet, StubCalendar.choir] {
            XCTAssertTrue(
                calendarRow(app, title).exists,
                "the day the cell opened is missing \(title)"
            )
        }
        XCTAssertFalse(
            calendarRow(app, StubCalendar.dentist).exists,
            "the cell's tap landed on the wrong date — today's events are on screen"
        )
    }

    /// The time grid: where an event sits, and the clamp that keeps an early
    /// one visible.
    ///
    /// Measured against the hour labels rather than against a point offset:
    /// the hour height is `@ScaledMetric`, so a number here would be a number
    /// for one type size. `pottery class` starts at 11:00, which is below the
    /// `10am` label and above `12pm`; `early train` starts at 05:30, before
    /// `START_HOUR`, and Android pins it to the top rather than dropping it —
    /// a clamp that was lost would put it above the grid or lose the row
    /// entirely, and both are caught here.
    @MainActor
    func testCalendar_DayGridPlacesAnEventAgainstTheHours() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalendarWords.day].tap()

        let eleven = calendarRow(app, StubCalendar.pottery)
        XCTAssertTrue(eleven.waitForExistence(timeout: 20), "the day grid drew no 11:00 event")
        let tenLabel = app.staticTexts["10am"]
        let noonLabel = app.staticTexts["12pm"]
        let sevenLabel = app.staticTexts["7am"]
        evidence(app, "calendar-12-day-grid")
        XCTAssertTrue(
            tenLabel.exists && noonLabel.exists && sevenLabel.exists,
            "the grid is missing an hour label — `10am`, `12pm` and `7am` are all "
                + "inside the sixteen rows it draws"
        )
        XCTAssertGreaterThan(
            eleven.frame.minY, tenLabel.frame.minY,
            "the 11:00 event is above the `10am` row"
        )
        XCTAssertLessThan(
            eleven.frame.minY, noonLabel.frame.minY,
            "the 11:00 event is below the `12pm` row"
        )

        let early = calendarRow(app, StubCalendar.earlyTrain)
        XCTAssertTrue(
            early.exists,
            "the 05:30 event is missing — before 6am is clamped to the top, not dropped"
        )
        XCTAssertLessThan(
            early.frame.minY, sevenLabel.frame.minY,
            "the clamped 05:30 event is not in the grid's first hour row"
        )
    }

    // MARK: - moving the range, and the two emptinesses

    /// `stepAnchor` and `rangeLabel`, in the one view whose label a test can
    /// compute for itself.
    ///
    /// Month, because `november 2026` is a month name and a year and nothing
    /// else — the agenda's `from wed, nov 4` and the day's
    /// `wednesday, november 4` would have this case reimplementing the app's
    /// own tables to know what to expect. Each step asserts the label that
    /// should be gone as well as the one that should be there.
    @MainActor
    func testCalendar_ChevronsAndTodayMoveTheRange() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalendarWords.month].tap()

        let thisMonth = calendarMonthLabel(0)
        let nextMonth = calendarMonthLabel(1)
        let lastMonth = calendarMonthLabel(-1)
        XCTAssertTrue(
            app.staticTexts[thisMonth].waitForExistence(timeout: 20),
            "the month view's range label is not \(thisMonth)"
        )

        app.buttons[CalendarWords.next].tap()
        XCTAssertTrue(
            app.staticTexts[nextMonth].waitForExistence(timeout: 15),
            "`next` did not step to \(nextMonth)"
        )
        XCTAssertFalse(app.staticTexts[thisMonth].exists, "both months' labels are on screen")

        app.buttons[CalendarWords.today].tap()
        XCTAssertTrue(
            app.staticTexts[thisMonth].waitForExistence(timeout: 15),
            "`today` did not come back to \(thisMonth)"
        )

        app.buttons[CalendarWords.previous].tap()
        let stepped = app.staticTexts[lastMonth].waitForExistence(timeout: 15)
        evidence(app, "calendar-13-range")
        XCTAssertTrue(stepped, "`previous` did not step to \(lastMonth)")
    }

    /// The **range** wording, which is not the agenda's.
    ///
    /// Four months on, where the fixture's furthest event is five days out,
    /// so the 42-cell grid is empty. Asserting the agenda's
    /// `nothing on the calendar` is absent is the half that matters: one
    /// empty state for both views would pass a case that only looked for
    /// "something that says nothing is here".
    @MainActor
    func testCalendar_AnEmptyRangeSaysNothingInThisRange() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalendarWords.month].tap()
        for _ in 0..<4 {
            app.buttons[CalendarWords.next].tap()
        }

        let said = app.staticTexts[CalendarWords.emptyRangeTitle].waitForExistence(timeout: 20)
        evidence(app, "calendar-14-empty-range")
        XCTAssertTrue(said, "an empty month does not say `nothing in this range`")
        XCTAssertTrue(
            app.staticTexts[CalendarWords.emptyRangeBodyMonth].exists,
            "the empty-range body does not name the view"
        )
        XCTAssertFalse(
            app.staticTexts[CalendarWords.emptyAgendaTitle].exists,
            "an empty month is using the agenda's empty wording"
        )
    }

    /// A read that failed says why, and offers the way back.
    ///
    /// **A 500 and not a 401.** Both of the route's tiers are device-level,
    /// so an unpaired phone's refusal is a *shell* state on this app: a 401
    /// that matches `isDeviceTokenRefusal` sets `pairingRequired` and the
    /// whole workspace becomes the pairing screen, and the throttled house's
    /// 429 raises the token-waiting card the same way
    /// (``testTheStatesTheAuditHadNeverSeen()``). Neither leaves this screen on screen to say
    /// anything, so the failure it can actually draw is a server error — and
    /// the fixture's own control route produces one. The flag is cleared by
    /// `reset-fixtures`, which every case in this file calls first, so it
    /// cannot leak into the next case the way the files block once did.
    @MainActor
    func testCalendar_AFailedReadSaysWhyRatherThanLookingEmpty() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the calendar seed")
        control(
            "calendar-fail", "{\"how\":\"500\"}", port: 6601,
            what: "the calendar read's failure"
        )
        let app = launch(port: 6601, push: "Calendar", pushDelay: Self.pushDelay)
        let why = containing(app, StubCalendar.failDetail)
        let explained = why.waitForExistence(timeout: 40)
        evidence(app, "calendar-15-failed-read")
        XCTAssertTrue(
            explained,
            "a failed read drew no reason — the screen looks like an empty calendar"
        )
        XCTAssertFalse(
            app.staticTexts[CalendarWords.emptyAgendaTitle].exists,
            "a failed read is being drawn as an empty calendar"
        )
        XCTAssertTrue(
            app.buttons[CalendarWords.retry].exists,
            "there is no way back from a failed read"
        )
        control("calendar-fail", "{\"how\":null}", port: 6601, what: "the calendar read's failure")
    }

    // MARK: - Dynamic Type, measured

    /// The agenda row at both sizes, **measured**.
    ///
    /// Android's clock column is a fixed 64dp (`CalendarScreen.kt:382`) and
    /// this port's is `@ScaledMetric`; a row that had kept the fixed width
    /// would hold its height while its text grew, or clip. A named Dynamic
    /// Type audit finding has twice been a false positive on this track
    /// (``knownScalingFalsePositive``), so this compares the element itself
    /// rather than believing a headline.
    @MainActor
    func testCalendar_RowsGrowAtAccessibilitySize() throws {
        let ordinary = openCalendar()
        XCTAssertTrue(ordinary.landed, "the agenda never drew a row at the ordinary size")
        let smallRow = calendarRow(ordinary.app, StubCalendar.pottery)
        XCTAssertTrue(calendarScroll(ordinary.app, to: smallRow), "no row to measure")
        let small = smallRow.frame.height
        evidence(ordinary.app, "calendar-16-row-large")
        ordinary.app.terminate()

        let big = openCalendar("UICTContentSizeCategoryAccessibilityXXXL")
        let bigRow = calendarRow(big.app, StubCalendar.pottery)
        XCTAssertTrue(
            calendarScroll(big.app, to: bigRow, flicks: 20),
            "the row is not reachable at AX5"
        )
        let grown = bigRow.frame.height
        evidence(big.app, "calendar-16-row-ax5")
        print("MEASURED agenda row: ordinary \(small) -> AX5 \(grown)")
        XCTAssertGreaterThan(
            grown, small * 1.5,
            "the agenda row barely grew: \(small) -> \(grown). The clock column or the "
                + "title is holding a fixed size."
        )
    }

    // MARK: - the audit

    /// Apple's audit over **four** states, because an audit only covers the
    /// states it is launched in: the agenda, the month grid, the day grid and
    /// the editor sheet.
    ///
    /// Classified by signature with ``floorAndRest(_:)``, never by a pinned
    /// total: a finding Apple can attribute to an element is a defect, and an
    /// anonymous finding of an unrecognised kind is an unknown rather than a
    /// floor.
    @MainActor
    func testCalendar_AuditHasNothingNew() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")

        var found: [String] = []
        found += try auditIssues(app, "calendar-agenda")
        evidence(app, "calendar-17-audit-agenda")

        app.buttons[CalendarWords.month].tap()
        XCTAssertTrue(
            app.staticTexts[CalendarWords.monthHint].waitForExistence(timeout: 20),
            "the month grid did not draw"
        )
        found += try auditIssues(app, "calendar-month")
        evidence(app, "calendar-17-audit-month")

        app.buttons[CalendarWords.day].tap()
        XCTAssertTrue(
            app.staticTexts["10am"].waitForExistence(timeout: 20),
            "the day grid did not draw"
        )
        found += try auditIssues(app, "calendar-day")
        evidence(app, "calendar-17-audit-day")

        app.buttons[CalendarWords.newEvent].tap()
        XCTAssertTrue(
            app.textFields[CalendarWords.titleLabel].waitForExistence(timeout: 20),
            "the editor did not open"
        )
        found += try auditIssues(app, "calendar-editor")
        evidence(app, "calendar-17-audit-editor")

        let split = Self.floorAndRest(found)
        print("AUDIT calendar: floor \(split.floor), rest \(split.rest.count)")
        XCTAssertTrue(
            split.rest.isEmpty,
            "Apple's audit has findings against Calendar beyond the measured "
                + "anonymous floor: " + split.rest.joined(separator: " / ")
        )
    }
}

/// What the Calendar screen says, by the words that appear on screen.
///
/// Spelt out rather than imported from `CalendarStrings`, like every other
/// string this file matches on: a query that silently followed a renamed
/// constant would stop testing what it claims to.
enum CalendarWords {
    static let newEvent = "new event"
    static let today = "today"
    static let previous = "previous"
    static let next = "next"
    static let close = "close"
    static let cancel = "cancel"
    static let edit = "edit"
    static let save = "save"
    static let createEvent = "create event"
    static let delete = "delete"
    static let retry = "retry"

    static let month = "month"
    static let day = "day"

    static let titleLabel = "title"
    static let startsAt = "starts at"
    static let endsAt = "ends at"

    static let titleRequired = "title is required"
    static let endAfterStart = "end must be after start"
    static let eventCreated = "event created"
    static let eventUpdated = "event updated"

    static let emptyAgendaTitle = "nothing on the calendar"
    static let emptyRangeTitle = "nothing in this range"
    /// The view's own word is concatenated, article and all
    /// (`CalendarScreen.kt:178`).
    static let emptyRangeBodyMonth = "tap new event to add one for month"

    static let monthHint = "tap a day to see what is on it"

    /// Event 4's `last_synced_at` is 900 seconds old, which `relTime` writes
    /// as `15m ago`.
    static let synced15m = "synced 15m ago"

    /// The seeded list's own size, which the header counts in full.
    static let thirteenEvents = "13 events · the household's calendar"

    /// The accessibility value of the one cell with four events on it.
    static let fourEvents = "4 events"
}

/// What the stub house's calendar half holds, by the words it serves.
///
/// These are the fixture's own (`design-notes/ios/stubhouse/fixtures/calendar.py`),
/// not the app's, and each one is chosen so that only one row can produce it.
enum StubCalendar {
    /// Event 13, 11:00-12:30 local today — the only row with a wall clock
    /// that does not depend on when the run happens.
    static let pottery = "pottery class"
    /// What the edit case renames it to. It shares **no prefix** with the old
    /// title, so "the new one is there" and "the old one is gone" are two
    /// different assertions.
    static let renamed = "ceramics evening"
    /// Event 11, 23:15 local with no `ends_at`.
    static let night = "night walk"
    /// Event 10, 05:30 local — before the grid's first hour.
    static let earlyTrain = "early train"
    /// Event 12, six weeks back.
    static let fluJab = "flu jab"
    /// Event 5, tomorrow at 07:00.
    static let swim = "swim lesson"
    static let swimDeleteBody = "Delete \"swim lesson\"? This can't be undone."
    static let swimDeletedToast = "deleted \"swim lesson\""
    /// Event 2, whose time is relative to the run.
    static let dentist = "dentist"

    /// Event 4, the google row.
    static let offsite = "work offsite"
    static let offsitePlace = "Bradford office"
    static let googleSource = "google"
    /// Event 4's **description**, which appears in no title and no other
    /// field — the word that fails a decoder which dropped `description`.
    static let googleNote = "brought over from google"

    /// The four on one day, five days out.
    static let hedge = "hedge trimming"
    static let parcel = "parcel drop"
    static let vet = "vet booster"
    static let choir = "choir practice"

    /// What the create case types.
    static let created = "garden waste pickup"

    /// The fixture's own 500 detail.
    static let failDetail = "the calendar table is unavailable"
}
