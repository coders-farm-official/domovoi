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

    /// Reset the seed, launch onto Calendar, wait for the **push** to land,
    /// and then for a row only a completed read can draw.
    ///
    /// The marker is two things, and the first of them is why: **Home draws
    /// this fixture's events itself.** Its `today` section reads the same
    /// `/api/calendar/events` (`HomeReads.swift`, `HomeEvent`), so the moment
    /// the house is up the root carries a `pottery class` leaf of its own —
    /// and `-domovoi-audit-push` does not push for ``StubHouseUITests/pushDelay``
    /// seconds. In train S010 that made `landed` true on the **root**, twelve
    /// seconds before Calendar existed: every case that asserted something
    /// immediately after it failed against Home's tree (`new event`, `month`
    /// and `day` are not on Home; its row label begins with the clock, not the
    /// title), while the cases that happened to burn twelve seconds flicking
    /// first passed. The screen was never broken.
    ///
    /// So the arrival marker is `new event`, which this screen's header draws
    /// in all four of its states and no other screen in the app draws at all,
    /// and the root's own leaves are gone from the tree once it is covered.
    /// Only then is the row waited for — the page title and the subtitle still
    /// cannot be the read's marker, since `Calendar` is a launcher tile and
    /// the subtitle counts zero before the answer comes.
    @MainActor
    func openCalendar(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        port: Int = 6601
    ) -> (app: XCUIApplication, landed: Bool) {
        control("reset-fixtures", "{}", port: port, what: "the calendar seed")
        let app = launch(mode: mode, port: port, push: "Calendar", pushDelay: Self.pushDelay)
        // The seeded push is held `pushDelay` seconds on purpose, so this
        // waits that out and then some.
        let arrived = app.buttons[CalendarWords.newEvent]
            .waitForExistence(timeout: Self.pushDelay + 40)
        if !arrived {
            print("=== THE CALENDAR PUSH NEVER LANDED — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
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

    /// `sat, nov 7` — a month cell's accessibility label
    /// (`calendarDayLabel(_:)`), computed with Foundation rather than read
    /// off the screen for ``calendarISODay(_:)``'s reason.
    @MainActor
    func calendarCellLabel(_ offset: Int) -> String {
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = .current
        let day = gregorian.date(byAdding: .day, value: offset, to: Date()) ?? Date()
        let shape = DateFormatter()
        shape.calendar = gregorian
        shape.timeZone = .current
        shape.locale = Locale(identifier: "en_US")
        shape.dateFormat = "EEE, MMM d"
        return shape.string(from: day).lowercased()
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
        let synced = app.staticTexts[CalendarWords.syncedTwoHours]
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
    /// The subtitle is the witness that no request went: it counts the rows
    /// in view (`CalendarScreen.kt:145`, and
    /// ``CalendarStrings/subtitle(count:)``), so a POST that slipped past the
    /// validation would make it count one more after the refresh. The editor
    /// must also still be open — a dialog that closed on a refusal would
    /// have thrown the draft away.
    ///
    /// **The count is read off the screen rather than pinned.** It used to be
    /// the literal `13 events · the household's calendar`, which worked while
    /// the read was the whole house. Since answer 16 it is the visible
    /// range's count, and the agenda's own window depends on the hour the
    /// train runs at (its floor is midnight, and the seed's `bin collection`
    /// is two hours back) — a pinned number would have been a number for one
    /// time of day. What this case is actually about is that the count does
    /// **not change**, which is what is asserted.
    @MainActor
    func testCalendar_BlankTitleIsRefused() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        let subtitle = app.staticTexts
            .matching(NSPredicate(format: "label ENDSWITH %@", CalendarWords.subtitleTail))
            .firstMatch
        XCTAssertTrue(
            subtitle.exists,
            "the header does not count what is in view — this is what any later "
                + "count is measured against"
        )
        let counted = subtitle.label

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
            app.staticTexts[counted].waitForExistence(timeout: 15),
            "the list grew although the save was refused: it was \(counted)"
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
        //
        // `dy: 0.25` and not the middle: the title field wraps now
        // (`CalendarEventEditorView.swift`, "The title and the location
        // wrap"), so its element is two lines tall with the text on the
        // first, and a vertical field hangs a horizontal scroll indicator
        // across its lower part — the note field's tree shows one over the
        // bottom 30 of its 64 points. A quarter of the way down is the line
        // the words are actually on.
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.25)).tap()
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
    /// Four events are seeded on one day five days out, and the cell is
    /// picked **by its date** with the count asserted on it — not by
    /// matching `4 events` and taking the first.
    ///
    /// That is what this case did in train S002, and it failed: the fixture's
    /// relative rows land on today or tomorrow depending on the wall clock,
    /// and at 23:21 today held exactly four of them too. `firstMatch` took
    /// today's cell, the tap opened today, and the case reported "the cell's
    /// tap did not open that day" against an app that had done exactly what
    /// it was asked. Data that discriminates means the handle must name the
    /// day, since two cells may legitimately share a count.
    ///
    /// The dot cap itself is not queryable (a `Circle` is not an element); it
    /// is ``CalendarMonthCell/overflow``, pinned by the package tests, and
    /// what this case proves is the count the cell publishes and the date its
    /// tap lands on.
    @MainActor
    func testCalendar_MonthShowsDotsAndOpensADay() throws {
        let (app, landed) = openCalendar()
        XCTAssertTrue(landed, "the agenda never drew a row")
        app.buttons[CalendarWords.month].tap()

        // The four-event day, by name: `sat, nov 7` for a run on `mon, nov 2`.
        let fourthDay = calendarCellLabel(5)
        let cell = app.buttons[fourthDay]
        let found = cell.waitForExistence(timeout: 20)
        evidence(app, "calendar-11-month")
        XCTAssertTrue(found, "the month grid has no cell for \(fourthDay)")
        XCTAssertEqual(
            cell.value as? String, CalendarWords.fourEvents,
            "the \(fourthDay) cell does not publish the four events seeded on it"
        )
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

        // **And the other edge.** `night walk` is 23:15 with no `ends_at`
        // (`calendar.py:138`), past the grid's last hour, so it is pinned to
        // the bottom — twenty minutes up from it, which is Android's
        // `coerceIn(0, GRID_HOURS * 60 - 20)`. Clamped to the full height
        // instead it was laid out at exactly the grid's own height: inside the
        // last hour row's label by one pixel of nothing, below the hours,
        // below the scroll view's content, drawn nowhere and tappable nowhere.
        // The last label is `9pm` — `calendarGridEndHour` is one past the last
        // row, so no `10pm` is drawn — and the pinned block starts inside its
        // row, not under it.
        //
        // **Against the row, not against the label's own box.** Train S002
        // read `nineLabel.frame.maxY` as the bottom of the last hour, and it
        // is not: an hour label is one line of `.caption` at the **top** of a
        // 60-point row, so `maxY` is 14 points down a row that is 60 deep.
        // The block was at offset 940 of 960 — exactly the clamp, exactly
        // inside the last row — and the case called it off the grid. The row
        // height is measured here from two consecutive labels, because the
        // hour height is `@ScaledMetric` and a constant would be a constant
        // for one type size.
        let eightLabel = app.staticTexts["8pm"]
        let nineLabel = app.staticTexts["9pm"]
        let night = calendarRow(app, StubCalendar.night)
        XCTAssertTrue(
            nineLabel.exists && eightLabel.exists && night.exists,
            "the 23:15 event is missing — after 10pm is clamped to the bottom, not dropped"
        )
        let rowHeight = nineLabel.frame.minY - eightLabel.frame.minY
        XCTAssertGreaterThan(rowHeight, 0, "the hour labels are not in order")
        XCTAssertGreaterThan(
            night.frame.minY, nineLabel.frame.minY,
            "the clamped 23:15 event is not in the grid's last hour row"
        )
        XCTAssertLessThan(
            night.frame.minY, nineLabel.frame.minY + rowHeight,
            "the clamped 23:15 event starts below the last hour row — off the grid"
        )

        // **And the block is its own height, not the grid's.** Every block
        // in train S002 measured 309 x 960 — the whole grid — because the
        // `ZStack` proposed its size to a child asking only for a
        // `minHeight`. Four blocks the size of the view, stacked: the last
        // one drawn took every tap and the other three were unreachable. A
        // block is never taller than the hours it spans; `pottery class` is
        // 90 minutes of a sixteen-hour grid.
        XCTAssertLessThan(
            eleven.frame.height, rowHeight * 4,
            "the 11:00 block is half the grid tall or more — the blocks are "
                + "taking the ZStack's whole proposal and covering each other"
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
        // The push is waited out explicitly, for ``openCalendar(_:port:)``'s
        // reason: Home reads the same route, so a reason drawn on the root
        // would read as this screen's.
        XCTAssertTrue(
            app.buttons[CalendarWords.newEvent].waitForExistence(timeout: Self.pushDelay + 40),
            "the calendar push never landed"
        )
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
    /// **Every leaf Apple named** is measured here, not only the row. Train
    /// S002 reported "Dynamic Type font sizes are partially unsupported"
    /// against the page title, the subtitle, the `new event` button, the day
    /// header and its date, the month's weekday heads and `+1` — nine
    /// elements, none of which carries a point size. The Files list settled
    /// how this track answers that: a named Dynamic Type finding is not a
    /// defect for being named, and the honest answer is a measurement, which
    /// is why ``testCalendar_AuditHasNothingNew()`` classifies that headline
    /// as explained and this case is what explains it. Anything really stuck
    /// fails here.
    @MainActor
    func testCalendar_RowsGrowAtAccessibilitySize() throws {
        /// The named leaves that are on the agenda, by shape: the day header
        /// carries a relative word (`today`) and the row's clock moves with
        /// the fixture's wall clock, so neither can be matched exactly.
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            // Built with arguments rather than as format strings with the
            // value inside quotes: the subtitle's tail carries an apostrophe
            // (`household's`) and a single-quoted `NSPredicate` literal
            // containing one does not parse.
            for (leaf, predicate) in [
                ("title", NSPredicate(format: "label == %@", "Calendar")),
                // Matched by its **tail**, not by `13 events`: since answer
                // 16 the number is the visible range's count, and the
                // agenda's window depends on the hour the train runs at.
                ("subtitle", NSPredicate(format: "label ENDSWITH %@", CalendarWords.subtitleTail)),
                ("new-event", NSPredicate(format: "label == %@", "new event")),
                ("day-head", NSPredicate(format: "label == %@", "today")),
                ("day-date", NSPredicate(format: "label == %@", calendarISODay(0))),
            ] {
                let found = app.descendants(matching: .any)
                    .matching(predicate)
                    .firstMatch
                if found.exists { out[leaf] = found.frame.height }
            }
            return out
        }

        let ordinary = openCalendar()
        XCTAssertTrue(ordinary.landed, "the agenda never drew a row at the ordinary size")
        let smallRow = calendarRow(ordinary.app, StubCalendar.pottery)
        XCTAssertTrue(calendarScroll(ordinary.app, to: smallRow), "no row to measure")
        let small = smallRow.frame.height
        let smallLeaves = heights(ordinary.app)
        evidence(ordinary.app, "calendar-16-row-large")
        ordinary.app.terminate()

        let big = openCalendar("UICTContentSizeCategoryAccessibilityXXXL")
        let bigLeaves = heights(big.app)
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

        // The leaves are measured **before** the scroll, so they are the
        // header's own, and a leaf missing at one size is a failure of its
        // own rather than a silently skipped comparison.
        XCTAssertFalse(smallLeaves.isEmpty, "no header leaf was found at the ordinary size")
        for (leaf, was) in smallLeaves {
            guard let now = bigLeaves[leaf] else {
                XCTFail("`\(leaf)` is on screen at the ordinary size and gone at AX5")
                continue
            }
            print("MEASURED \(leaf): ordinary \(was) -> AX5 \(now)")
            XCTAssertGreaterThan(
                // 1.25 and not the row's 1.5: a leaf that is one line of text
                // inside a 44-point minimum — `new event` — starts at the
                // minimum and only grows once the line passes it, so its
                // honest ratio is smaller than a row's. An element Apple is
                // right about does not move at all, which any factor over 1
                // catches.
                now, was * 1.25,
                "`\(leaf)` barely grew: \(was) -> \(now) — Apple's Dynamic Type "
                    + "finding against it is a real one after all"
            )
        }
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

        // Measured before anything is tapped — see ``calendarEdges(_:)``.
        let edges = calendarEdges(app)
        XCTAssertFalse(
            edges.isEmpty,
            "neither the page's scroll view nor the phone bar is on screen, so "
                + "there is no measured edge to classify a finding against"
        )

        // **Four separate audits, each classified against its own state.**
        // Whether an edge is cutting a leaf in half is a fact about the state
        // and not about the screen: the agenda had `Lido` on the line in
        // train S007 and the editor's sheet cuts nothing at all.
        let agenda = try calendarAudit(app, "calendar-agenda", edges: edges)
        evidence(app, "calendar-17-audit-agenda")

        app.buttons[CalendarWords.month].tap()
        XCTAssertTrue(
            app.staticTexts[CalendarWords.monthHint].waitForExistence(timeout: 20),
            "the month grid did not draw"
        )
        let month = try calendarAudit(app, "calendar-month", edges: edges)
        evidence(app, "calendar-17-audit-month")

        app.buttons[CalendarWords.day].tap()
        XCTAssertTrue(
            app.staticTexts["10am"].waitForExistence(timeout: 20),
            "the day grid did not draw"
        )
        let day = try calendarAudit(app, "calendar-day", edges: edges)
        evidence(app, "calendar-17-audit-day")

        app.buttons[CalendarWords.newEvent].tap()
        XCTAssertTrue(
            app.textFields[CalendarWords.titleLabel].waitForExistence(timeout: 20),
            "the editor did not open"
        )
        let editor = try calendarAudit(app, "calendar-editor", edges: edges)
        evidence(app, "calendar-17-audit-editor")

        let floor = agenda.floor + month.floor + day.floor + editor.floor
        let unexplained =
            agenda.unexplained + month.unexplained + day.unexplained + editor.unexplained
        print("AUDIT calendar: floor \(floor), unexplained \(unexplained.count)")
        XCTAssertEqual(
            [], unexplained,
            "Apple's audit has findings against Calendar beyond the measured "
                + "floor: " + unexplained.joined(separator: " / ")
        )
    }

    /// One state's audit: its half-drawn leaf measured **first**, then
    /// Apple's findings split into the measured floor and the lines no
    /// measurement explains.
    ///
    /// A method rather than a closure inside the case, so that
    /// `@available(iOS 17.0, *)` is written where the compiler can see it:
    /// ``auditIssues(_:_:)`` needs iOS 17 and a nested function does not
    /// reliably inherit the `#available` guard above it.
    @MainActor
    @available(iOS 17.0, *)
    func calendarAudit(
        _ app: XCUIApplication,
        _ name: String,
        edges: [CGFloat]
    ) throws -> (floor: Int, unexplained: [String]) {
        let cut = calendarCutLeaf(app, edges)
        print("AUDIT calendar [\(name)] an edge cuts: \(cut ?? "nothing")")
        let split = Self.floorAndRest(try auditIssues(app, name))
        var unexplained: [String] = []
        for line in split.rest {
            print("AUDIT calendar REST :: [\(name)] \(line)")
            if !calendarFindingExplained(line, edges: edges, cutLeaf: cut) {
                unexplained.append("[\(name)] " + line)
            }
        }
        print("AUDIT calendar [\(name)] floor \(split.floor), unexplained \(unexplained.count)")
        return (split.floor, unexplained)
    }

    /// Whether one audit line is one this screen has **measured** an answer
    /// to, rather than one it is choosing to ignore. Three kinds, each with a
    /// measurement behind it:
    ///
    /// * **"Dynamic Type font sizes are partially unsupported"** — the Files
    ///   list settled this on this track: a named Dynamic Type finding is not
    ///   a defect for being named, and the answer is to measure the leaf
    ///   (``testTheFilesListAudits()``).
    ///   ``testCalendar_RowsGrowAtAccessibilitySize()`` measures every leaf
    ///   this screen was named for, at `large` and at AX5, and fails if one
    ///   of them really is stuck.
    /// * **A contrast finding on a named element an edge cuts in half.** A
    ///   half-drawn element has no pixels Apple can read, which the shell's
    ///   own bottom bar taught this suite once already
    ///   (`WorkspaceView.swift:54-74`). The month grid's last row and the day
    ///   grid's last hour label are below the fold by construction — a month
    ///   is six rows of 74 points and a day sixteen rows of 60 — and
    ///   scrolling to them is what a scroll view is. So the frame is parsed
    ///   out of the finding and compared with the **measured** edges: a
    ///   finding on an element wholly above them is never explained by this,
    ///   which is how a real contrast defect still fails this case.
    /// * **A contrast finding Apple could not attribute, in a state where an
    ///   edge is measurably cutting a leaf in half.** Train S007: one
    ///   `Contrast failed | element: nil` against the agenda, and the picture
    ///   beside it is the answer two tree diffs were not — `Lido`, the place
    ///   line of the `swim lesson` row, drawn 759.0–773.3 against a scroll
    ///   viewport that ends at 770.0, so the audit read **half a row of
    ///   glyphs**. The month grid produces the same finding with the element
    ///   attached (`'6'…'12'`, 717.7–791.7), which is why this is that
    ///   finding and not a new kind.
    ///
    ///   This is deliberately **not** "ignore every anonymous contrast
    ///   finding". ``calendarCutLeaf(_:_:)`` walks the state's own static
    ///   texts and returns the leaf an edge is cutting, so the excuse exists
    ///   only while a half-drawn leaf does: the editor sheet cuts nothing, and
    ///   an anonymous contrast finding there still fails this case. Which
    ///   agenda row lands on the edge moves with the fixture's wall clock —
    ///   S005 ran at an hour that put a gap there and reported no contrast
    ///   finding at all — so pinning this to one row would have pinned the
    ///   hour the train ran.
    @MainActor
    func calendarFindingExplained(
        _ line: String,
        edges: [CGFloat],
        cutLeaf: String?
    ) -> Bool {
        if line.hasPrefix("Dynamic Type font sizes") { return true }
        guard line.hasPrefix("Contrast"), !edges.isEmpty else { return false }
        guard let frame = calendarFindingFrame(line) else { return cutLeaf != nil }
        return edges.contains { frame.minY < $0 && frame.maxY > $0 }
    }

    /// The horizontal lines that cut this screen's content in half, measured
    /// **once**, on the agenda: by the time the four states have been audited
    /// the editor's sheet is up and neither is where it was.
    ///
    /// Two of them, because they are four points apart and each clips
    /// something:
    ///
    ///  - the page's own scroll view's bottom (`770.0` on a 393×852 phone),
    ///    which is what actually clips a row — the `Lido` line that cost
    ///    train S007 straddles this one and **not** the other, which is why
    ///    the phone bar alone was not enough;
    ///  - the top of the phone bar (`774.0`), which draws over whatever the
    ///    scroll view did finish.
    ///
    /// The page's scroll view is the **tallest** one on screen; the other is
    /// the agenda/day/month mode strip, lying on its side. The bar is matched
    /// case-insensitively because its accessibility label is the route's name
    /// and the screen's is the tab's word.
    @MainActor
    func calendarEdges(_ app: XCUIApplication) -> [CGFloat] {
        var out: [CGFloat] = []
        let page = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })
        if let page, page.frame.height > 0 { out.append(page.frame.maxY) }
        let bar = app.buttons
            .matching(NSPredicate(format: "label LIKE[c] 'home'"))
            .firstMatch
        if bar.exists { out.append(bar.frame.minY) }
        return out
    }

    /// The first static text one of ``calendarEdges(_:)`` is drawing through,
    /// described for the log, or nil when every leaf on screen is whole.
    ///
    /// Static texts only: a contrast finding is about rendered glyphs, and a
    /// row *button* straddling an edge whose own text is entirely above it
    /// has nothing half-drawn to report.
    @MainActor
    func calendarCutLeaf(_ app: XCUIApplication, _ edges: [CGFloat]) -> String? {
        for leaf in app.staticTexts.allElementsBoundByIndex {
            let box = leaf.frame
            guard box.height > 0 else { continue }
            if edges.contains(where: { box.minY < $0 && box.maxY > $0 }) {
                return "'\(leaf.label)' \(box)"
            }
        }
        return nil
    }

    /// `frame=(16.0, 735.6, 46.0, 74.0)` out of an audit line, or nil when
    /// the finding named no element (``floorAndRest(_:)``'s anonymous kind)
    /// or spelt its frame some other way.
    @MainActor
    func calendarFindingFrame(_ line: String) -> CGRect? {
        guard let open = line.range(of: "frame=("),
              let close = line.range(of: ")", range: open.upperBound..<line.endIndex)
        else { return nil }
        let numbers = line[open.upperBound..<close.lowerBound]
            .split(separator: ",")
            .compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        guard numbers.count == 4 else { return nil }
        return CGRect(x: numbers[0], y: numbers[1], width: numbers[2], height: numbers[3])
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
    /// Event 4's `last_synced_at`, two hours back (`calendar.py:124`). Two
    /// hours and not fifteen minutes because `relTimeMs` rounds to the hour
    /// there: `2h ago` holds across a half-hour, where `15m ago` was a
    /// sixty-second bucket this case could swipe straight past.
    static let syncedTwoHours = "synced 2h ago"

    /// The tail of the subtitle, which is all of it that does not depend on
    /// what is in view.
    ///
    /// It used to be the whole sentence with the seeded `13` in it. Since
    /// Kamron's answer 16 the read is a window per view, so the number is the
    /// visible range's and the sentence says `in view`
    /// (``CalendarStrings/subtitle(count:)``) — and the agenda's window
    /// depends on the hour the train runs at.
    static let subtitleTail = "in view · the household's calendar"

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
