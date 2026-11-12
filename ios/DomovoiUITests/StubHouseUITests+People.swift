import XCTest

/// The People screen, driven against the stub house.
///
/// Every case here resets the fixture module first (`fixtures/people.py`),
/// because the cases share one house for a whole run and three of them write
/// to it — a memory added by one would otherwise be in the roster of every
/// case that ran after it, which is how a video left a recently-played strip
/// on four screens that never asked for one.
///
/// The screen is reached by **seeding the push** (`launch(push: "People")`),
/// not by tapping. Since the nav regroup People is **Settings → Household →
/// People**: the seeded `navigate(.people)` opens Settings on that section
/// (`settingsTabFor`), where the screen is drawn embedded — Settings' own
/// heading and scroll, the counts line, the roster, and the section's
/// `back to Household` foot. It is on no default bar and no longer on Home's
/// grid. Nothing here opens the connection sheet.
///
/// ### What the fixture was written to make provable
///
/// Four people, one of them live, two of them namesakes, one with no voice
/// profiles; three memories whose bodies share no word (`peanuts`,
/// `sourdough`, `unicycle`); three favourites over two kinds; two sessions,
/// the second deliberately outside the conversations window; four turns, one
/// with an answer over 140 characters whose **last** word is
/// `sourdough-starter`. Every assertion below leans on one of those, so a
/// case cannot pass by accident — the discrimination rule in
/// `fixtures/README.md`.
extension StubHouseUITests {

    // MARK: - what the stub serves, by the words on screen

    /// Spelled out rather than imported, like every other string this file
    /// matches on: a query that silently followed a renamed constant would
    /// stop testing what it claims to. These are ``PeopleStrings``' and the
    /// fixture's own words.
    enum PeopleUnderTest {
        static let title = "People"
        /// Only a **completed** roster read can produce this line: four rows
        /// and exactly one of them heard in the last five minutes.
        static let subtitle = "4 enrolled · 1 heard in the last 5 min"
        static let filter = "filter by name…"
        static let noMatch = "no match"
        static let nobodyEnrolled = "nobody enrolled yet"
        static let back = "back"

        static let jordan = "Jordan"
        static let chevy = "Chevy"
        static let alex = "Alex"
        /// Person 4's name carries a trailing space — the server counts
        /// namesakes on `lower(btrim(name))`, so these two clash although
        /// the strings differ.
        static let alexLower = "alex "
        static let namesakeTag = "#3 · same name ·"
        static let sameName = "same name"

        static let notes = "prefers the kitchen satellite"
        static let noNotes = "no notes yet"
        static let voiceProfiles = "voice profiles"
        static let personTwoLine = "person · #2 ·"
        static let namesakeNote = "2 people are called Alex"
        static let whereRenamingIs = "renaming and forgetting someone are done on the dashboard"

        static let pendingBand = "pending — extracted from conversation"
        static let activeMemory = "allergic to peanuts"
        static let pendingMemory = "bakes sourdough on Sundays"
        static let rejectedMemory = "wants to learn the unicycle"
        static let rejectedDisclosure = "1 rejected (hidden)"
        static let noMemories = "no memories yet"
        /// The memories header before anything is added, and after — the
        /// line the add case waits on, because it moves only when the POST
        /// landed *and* the re-read came back.
        static let memoryCountsAfter = "2 active · 1 pending"
        static let addMemory = "add a memory…  e.g. allergic to peanuts"
        static let saveMemory = "save memory"
        static let saveFavorite = "save favorite"
        static let savedMemory = "saved memory"
        static let newMemory = "allergic to shellfish"
        /// memories (3) + favourites (3), which the tab title counts
        /// together (`PersonDetail.kt:113`).
        static let memoryTabSix = "memory (6)"
        static let memoryTabUnknown = "memory (?)"
        static let couldNotReach = "couldn't reach the server"
        static let retry = "retry"

        static let favoriteCounts = "3 saved · 2 kinds"
        /// After `tea`/`oolong` is added — a third kind, a fourth value.
        static let favoriteCountsAfter = "4 saved · 3 kinds"
        static let coffee = "coffee"
        static let team = "team"
        static let mariners = "Mariners"
        static let sounders = "Sounders"
        static let favoriteKind = "kind"
        static let favoriteValue = "value  e.g. Mariners"

        static let kitchenSession = "s-aaaa11"
        static let studySession = "s-bbbb22"
        static let noRowsInSession = "no conversation rows in this session"
        static let timerTurn = "set a timer for the bread"
        static let quietTurn = "play something quiet"

        static let search = "search what they said or what domovoi said…"
        static let oneOfFour = "1 of 4"
        static let longAnswerTail = "sourdough-starter"
        static let more = "more"
        static let less = "less"

        static let denylistCard = "Voice denylist"
        static let optedOut = "2 opted out"
        static let hashesOnFile = "hashes on file"

        static let failToast = "couldn't load sessions, memories:"
    }

    // MARK: - getting there

    /// Reset the fixture, launch into People, and wait for the one line only
    /// a completed read can draw.
    ///
    /// The subtitle rather than a row: a roster that drew four rows and
    /// counted the wrong number of them live is a screen this suite must be
    /// able to fail, and the header is where that shows.
    @MainActor
    func peopleLaunch(mode: String = StubHouseUITests.ordinaryTypeSize) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        let app = launch(mode: mode, push: PeopleUnderTest.title)
        if !app.staticTexts[PeopleUnderTest.subtitle].waitForExistence(timeout: 30) {
            // The tree, never a sentence about what is missing: this file's
            // own header has paid six runs for that lesson.
            print("=== PEOPLE DID NOT DRAW — tree follows ===")
            print(app.debugDescription)
        }
        return app
    }

    /// Arm or disarm one of the five sub-reads (`fixtures/people.py`'s
    /// `/api/_test/people-fail`).
    @MainActor
    func peopleFail(_ which: String?, status: Int = 500, delayMs: Int = 0) {
        let body = which.map {
            "{\"which\":\"\($0)\",\"status\":\(status),\"delay_ms\":\(delayMs)}"
        } ?? "{}"
        control("people-fail", body, port: 6601, what: "a broken people read")
    }

    /// A roster row, by the name it draws. The subtitle is the row's
    /// **value**, as ``PersonRosterRow`` sets it, so a row is one VoiceOver
    /// stop rather than four.
    @MainActor
    func peopleRow(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label == %@", name)).firstMatch
    }

    /// Open a person and wait for their detail.
    ///
    /// The detail is a **branch** of this route, not a push, so arriving is
    /// the back link appearing — the one control the roster does not have.
    @MainActor
    func peopleOpen(_ app: XCUIApplication, _ name: String) -> Bool {
        let row = peopleRow(app, name)
        guard row.waitForExistence(timeout: 25) else {
            print("=== NO ROSTER ROW \(name) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        row.tap()
        let arrived = app.buttons[PeopleUnderTest.back].waitForExistence(timeout: 20)
        if !arrived {
            print("=== DETAIL FOR \(name) DID NOT OPEN — tree follows ===")
            print(app.debugDescription)
        }
        return arrived
    }

    /// Tap one of the four tabs, whose titles carry a count that moves
    /// (`memory (6)`), so they are matched by prefix.
    @MainActor
    func peopleTab(_ app: XCUIApplication, _ word: String) -> Bool {
        let tab = app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", word))
            .firstMatch
        guard tab.waitForExistence(timeout: 20) else {
            print("=== NO \(word) TAB — tree follows ===")
            print(app.debugDescription)
            return false
        }
        tab.tap()
        return true
    }

    /// The screen's own scroll view — **not the keyboard's**.
    ///
    /// `app.scrollViews.firstMatch` is not reliably it. With the keyboard up,
    /// the typing-predictions bar (`SystemInputAssistantView`, its own
    /// window) contains a `ScrollView` too, and on train S008 `firstMatch`
    /// resolved to *that* in three cases. The log says it in as many words —
    /// `Swipe up ScrollView at {{0.0, 516.0}, {393.0, 45.0}}`, twelve times
    /// over — so nothing scrolled, `peopleReveal` measured the field against
    /// a 45-point strip no field can fit inside, and three cases failed with
    /// "the field never came into the viewport" and "the saved memory never
    /// appeared in the list" against an app that was right.
    ///
    /// Told apart by height: the content scroll view is 676.7 points, or
    /// 374.7 with the keyboard up, against the strip's 45. Resolved freshly
    /// on every call, because the keyboard comes and goes and an index taken
    /// before it appeared does not keep.
    @MainActor
    func peopleSheet(_ app: XCUIApplication) -> XCUIElement {
        let views = app.scrollViews
        let heights = views.allElementsBoundByIndex.map { $0.frame.height }
        guard let tallest = heights.enumerated().max(by: { $0.element < $1.element }),
            tallest.element > 120
        else { return views.firstMatch }
        return views.element(boundBy: tallest.offset)
    }

    /// Flick the screen slowly until `element` is built.
    ///
    /// ``scrollUntilBuilt(_:_:flicks:)`` waits for a `collectionView` and
    /// these screens are a `ScrollView` of `LazyVStack`, which reports as a
    /// `scrollView` — so this is that helper over the right container, with
    /// the same slow flick for the same measured reason (a default
    /// `swipeUp()` is worth a viewport and steps straight over a row).
    @MainActor
    func peopleScroll(_ app: XCUIApplication, to element: XCUIElement, flicks: Int = 12) -> Bool {
        if element.exists { return true }
        guard app.scrollViews.firstMatch.waitForExistence(timeout: 20) else {
            print("=== NOTHING TO SCROLL — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            // Re-resolved per flick, never bound once: see ``peopleSheet(_:)``.
            peopleSheet(app).swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// The part of ``peopleSheet(_:)`` that nothing else is drawn over.
    ///
    /// The screen sits in a `NavigationStack`, so its scroll view's frame
    /// *starts at the navigation bar's own top* and the content scrolls under
    /// that bar — the scroll view says nothing about the 44 points of it a
    /// tap can never reach. Train S011 paid for the difference: the `value`
    /// field reported `{{16, 97.7}, {294, 34}}` against a sheet whose frame
    /// began at 93.3, so the reveal loop called it visible, and the tap at
    /// `y = 114.7` landed on the `NavigationBar` at `{{0, 93.3}, {393, 44}}`.
    /// Focus stayed on `kind`, and `typeText` failed all three of its retries
    /// with "neither element nor any descendant has keyboard focus" — a
    /// failure that reads as a dead text field on an app that was right.
    @MainActor
    func peopleViewport(_ app: XCUIApplication) -> CGRect {
        var viewport = peopleSheet(app).frame
        for bar in app.navigationBars.allElementsBoundByIndex {
            let frame = bar.frame
            // Only a bar sitting ON the sheet's top edge: one that does not
            // reach it covers nothing, and one below it is not a thing this
            // screen has.
            guard frame.height > 0, frame.minY <= viewport.minY, frame.maxY > viewport.minY
            else { continue }
            viewport = CGRect(
                x: viewport.minX, y: frame.maxY,
                width: viewport.width,
                height: max(0, viewport.maxY - frame.maxY)
            )
        }
        return viewport
    }

    /// Scroll the sheet by an exact number of points, with no flick momentum.
    ///
    /// A `swipeUp(velocity: .slow)` here moves the content by about a whole
    /// viewport — 383 points, measured on train S011 — and the `value`
    /// field's two reachable positions either side of one such step were
    /// *both* outside the viewport (97.7 under the navigation bar, 480.7
    /// under the phone bar), so a loop that can only flick oscillates between
    /// them until it runs out of tries. A press long enough to register as a
    /// drag rather than a flick moves the content by the drag's own distance,
    /// which converges in one or two passes.
    ///
    /// `dy` is how far the **content** should move: positive brings something
    /// above the viewport down into it.
    @MainActor
    func peopleNudge(_ app: XCUIApplication, _ dy: CGFloat) {
        let viewport = peopleViewport(app)
        guard viewport.height > 32 else { return }
        let room = viewport.height - 16
        let step = max(-room, min(room, dy))
        // x = 8 is inside the scroll view and left of every field, button and
        // row on this screen, so the drag is never taken for a control's own
        // gesture.
        let x = viewport.minX + 8
        let from = step > 0 ? viewport.minY + 8 : viewport.maxY - 8
        let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
        origin.withOffset(CGVector(dx: x, dy: from))
            .press(
                forDuration: 0.5,
                thenDragTo: origin.withOffset(CGVector(dx: x, dy: from + step))
            )
    }

    /// Scroll until `element` is built **and inside the viewport**.
    ///
    /// `exists` is not `visible`. XCUITest reports an element's frame in the
    /// scroll view's *content*, un-clipped, so a field one point below the
    /// viewport is `exists == true` with a frame that lies over whatever is
    /// drawn under the scroll view — on this screen, the phone bar. Train
    /// S005 paid for the difference: the `value` field reported
    /// `{{16, 481}, {294, 34}}` while the keyboard had shrunk the viewport to
    /// end at 468, the tap at the field's own centre-right landed on the
    /// phone bar's `chat` button at `{{294.7, 472}, {98.3, 44}}`, and the app
    /// left People entirely. The failure read as "the text field vanished
    /// while typing", and the tree attached to it was a Chat screen.
    ///
    /// The viewport is ``peopleViewport(_:)``'s: ``peopleSheet(_:)``'s frame
    /// less the navigation bar drawn over its top (train S011), and not
    /// `scrollViews.firstMatch`'s — the keyboard has a scroll view of its
    /// own, and comparing a field's frame against *it* is what failed three
    /// cases on train S008.
    ///
    /// Each pass moves the content by the measured shortfall rather than by a
    /// flick, for the oscillation ``peopleNudge(_:_:)`` describes.
    @MainActor
    func peopleReveal(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 10) -> Bool {
        guard peopleScroll(app, to: element, flicks: flicks) else { return false }
        for _ in 0..<flicks {
            let viewport = peopleViewport(app)
            let frame = element.frame
            if frame.minY >= viewport.minY && frame.maxY <= viewport.maxY { return true }
            // Eight points clear of the edge it was over, so that a rounding
            // difference between the measurement and the drag cannot leave it
            // straddling the edge it was just moved off.
            let dy =
                frame.maxY > viewport.maxY
                ? viewport.maxY - frame.maxY - 8
                : viewport.minY - frame.minY + 8
            peopleNudge(app, dy)
        }
        print(
            "=== BUILT BUT NEVER INSIDE THE VIEWPORT: element \(element.frame) "
                + "viewport \(peopleViewport(app)) — tree follows ==="
        )
        print(app.debugDescription)
        return false
    }

    /// Type into a field, having put the caret at its end.
    ///
    /// A tap puts the caret where it lands, which is why the tap is at 0.95
    /// of the width: a tap in the middle of a pre-filled field appends in the
    /// wrong place, and three Chat cases were written twice before that was
    /// understood.
    ///
    /// The field is brought inside the viewport first — see
    /// ``peopleReveal(_:_:flicks:)`` for the tap that went to the phone bar.
    @MainActor
    func peopleType(_ app: XCUIApplication, _ field: XCUIElement, _ text: String) {
        XCTAssertTrue(peopleReveal(app, field), "the field never came into the viewport")
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        field.typeText(text)
    }

    // MARK: - the roster

    /// The household, and the one line only a completed read can produce.
    @MainActor
    func testPeople_RosterListsTheHousehold() throws {
        let app = peopleLaunch()
        evidence(app, "people-roster")
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.subtitle].exists,
            "the header never read `\(PeopleUnderTest.subtitle)` — either the roster read "
                + "did not land, the decode wanted a wrapper the route does not send, or the "
                + "live count counted everybody"
        )
        for name in [
            PeopleUnderTest.jordan, PeopleUnderTest.chevy,
            PeopleUnderTest.alex, PeopleUnderTest.alexLower,
        ] {
            XCTAssertTrue(peopleRow(app, name).exists, "no roster row for \(name)")
        }
    }

    /// Two people with one name are marked, and nobody else is.
    ///
    /// The negative half is what makes it discriminate: a `rosterNameTag`
    /// applied to every row would pass the first assertion on its own.
    @MainActor
    func testPeople_NamesakesAreMarked() throws {
        let app = peopleLaunch()
        let alex = app.buttons
            .matching(
                NSPredicate(
                    format: "label == %@ AND value BEGINSWITH %@",
                    PeopleUnderTest.alex, PeopleUnderTest.namesakeTag
                )
            )
            .firstMatch
        let marked = alex.waitForExistence(timeout: 20)
        evidence(app, "people-namesakes")
        XCTAssertTrue(
            marked,
            "Alex's subtitle does not begin `\(PeopleUnderTest.namesakeTag)` — the roster's "
                + "name tag is missing, or it is not keyed on `namesakes`"
        )
        let jordan = peopleRow(app, PeopleUnderTest.jordan)
        XCTAssertTrue(jordan.exists, "no Jordan row to check")
        XCTAssertFalse(
            (jordan.value as? String ?? "").contains(PeopleUnderTest.sameName),
            "Jordan shares a name with nobody and the row says they do: "
                + "\(jordan.value as? String ?? "nothing")"
        )
    }

    /// The filter narrows the roster, and the two empty notes are not
    /// interchangeable.
    @MainActor
    func testPeople_FilterNarrowsAndSaysSo() throws {
        let app = peopleLaunch()
        let field = app.textFields[PeopleUnderTest.filter]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no filter field")
        peopleType(app, field, "chev")
        XCTAssertTrue(
            peopleRow(app, PeopleUnderTest.chevy).waitForExistence(timeout: 10),
            "`chev` does not match Chevy — the filter is case-sensitive, or over another field"
        )
        XCTAssertTrue(
            waitUntilGone(peopleRow(app, PeopleUnderTest.jordan)),
            "`chev` left Jordan in the roster — the filter narrowed nothing"
        )

        clearField(app)
        XCTAssertTrue(
            peopleRow(app, PeopleUnderTest.jordan).waitForExistence(timeout: 10),
            "clearing the filter did not bring the household back"
        )

        peopleType(app, field, "zzz")
        let noMatch = app.staticTexts[PeopleUnderTest.noMatch]
        let said = noMatch.waitForExistence(timeout: 10)
        evidence(app, "people-filter-no-match")
        XCTAssertTrue(said, "a filter that matched nobody drew no `no match` note")
        XCTAssertFalse(
            app.staticTexts[PeopleUnderTest.nobodyEnrolled].exists,
            "a filter that matched nobody claimed the house has nobody enrolled — the two "
                + "notes are swapped, and one of them is a lie about the household"
        )
    }

    // MARK: - one person

    /// The detail opens on the row that was tapped, not on the first one.
    @MainActor
    func testPeople_DetailOpensOnTheTappedPerson() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.chevy), "Chevy's detail did not open")
        let line = containing(app, PeopleUnderTest.personTwoLine)
        let onPersonTwo = line.waitForExistence(timeout: 15)
        evidence(app, "people-detail-chevy")
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.chevy].exists,
            "the detail header does not say Chevy"
        )
        XCTAssertTrue(
            onPersonTwo,
            "the profile line does not read `\(PeopleUnderTest.personTwoLine)` — the detail "
                + "opened on the wrong person, which is the row-index-for-id confusion a lazy "
                + "list invites"
        )
    }

    /// The stats and the notes are **this** person's.
    @MainActor
    func testPeople_ProfileShowsTheirOwnStats() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        let tile = app.descendants(matching: .any)
            .matching(
                NSPredicate(
                    format: "label CONTAINS %@ AND label CONTAINS %@",
                    PeopleUnderTest.voiceProfiles, "3"
                )
            )
            .firstMatch
        let counted = tile.waitForExistence(timeout: 15)
        let noted = app.staticTexts[PeopleUnderTest.notes].exists
        evidence(app, "people-profile-jordan")
        XCTAssertTrue(counted, "the voice-profiles tile does not read 3 for a person with three")
        XCTAssertTrue(noted, "Jordan's notes are not on the profile")

        app.buttons[PeopleUnderTest.back].tap()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.chevy), "Chevy's detail did not open")
        let placeholder = app.staticTexts[PeopleUnderTest.noNotes]
        let saidNone = placeholder.waitForExistence(timeout: 15)
        evidence(app, "people-profile-chevy")
        XCTAssertTrue(saidNone, "a person with no notes does not get the `no notes yet` line")
        XCTAssertFalse(
            app.staticTexts[PeopleUnderTest.notes].exists,
            "Chevy's profile is showing Jordan's notes — the tiles and the notes are reading a "
                + "shared value rather than the open person's"
        )
    }

    /// The namesake note explains the clash, in words this app can act on.
    @MainActor
    func testPeople_NamesakeNoteExplainsTheClash() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.alex), "Alex's detail did not open")
        let note = containing(app, PeopleUnderTest.namesakeNote)
        let explained = note.waitForExistence(timeout: 15)
        evidence(app, "people-namesake-note")
        XCTAssertTrue(
            explained,
            "the profile of a person who shares a name says nothing about it"
        )
        // Android's note ends "If they are different people, rename one of
        // them." — advice about a control that is not on this screen and
        // cannot be (`PATCH /api/people/{id}` is admin-only). The iOS-only
        // replacement keeps the advice and says **where**:
        // ``PeopleStrings/namesakeFixIsOnTheDashboard``.
        //
        // This assertion read `!contains("rename one of them")` on train
        // S002, which both sentences contain — it could not tell the ported
        // Android wording from the replacement, so it failed against a screen
        // that was right. It is the clause that differs that is asserted
        // here: Android's bare full stop after `them`, and the dashboard the
        // replacement names. Reverting the string to Android's still fails
        // both halves.
        XCTAssertFalse(
            note.label.contains("rename one of them."),
            "the note tells the reader to rename somebody and does not say where, which is "
                + "advice a phone cannot act on: " + note.label
        )
        XCTAssertTrue(
            note.label.contains("on the dashboard"),
            "the note raises the clash and never says where it can be settled: " + note.label
        )
    }

    /// **The no-misleading-controls case.** Neither admin control is drawn,
    /// and the screen says where that work is done.
    ///
    /// This is the one a careless "port it faithfully" fix would break, which
    /// is why it asserts the absence of both controls *and* the presence of
    /// the sentence that replaced them.
    @MainActor
    func testPeople_NoRenameOrForgetControl() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        let sentence = containing(app, PeopleUnderTest.whereRenamingIs)
        let explained = sentence.waitForExistence(timeout: 15)
        evidence(app, "people-no-admin-controls")
        XCTAssertFalse(
            app.buttons["rename \(PeopleUnderTest.jordan)"].exists,
            "the rename control is back — `PATCH /api/people/{id}` is admin-only and 401s from "
                + "every phone (people.py:145)"
        )
        XCTAssertFalse(
            app.buttons["rename"].exists,
            "a bare `rename` button is on the profile"
        )
        XCTAssertFalse(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label CONTAINS %@", "forget person"))
                .firstMatch
                .exists,
            "the `forget person` control is back — `DELETE /api/people/{id}` is admin-only, and "
                + "it cascades across the household's whole history"
        )
        XCTAssertTrue(
            explained,
            "neither control is drawn and nothing says where renaming and forgetting are done"
        )
    }

    // MARK: - the memory tab

    /// Three statuses, three places — and the rejected ones stay hidden until
    /// they are asked for.
    @MainActor
    func testPeople_MemoryTabSeparatesPendingFromActive() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let band = app.staticTexts[PeopleUnderTest.pendingBand]
        XCTAssertTrue(band.waitForExistence(timeout: 20), "no pending band over the extracted rows")
        let pending = app.staticTexts[PeopleUnderTest.pendingMemory]
        let active = app.staticTexts[PeopleUnderTest.activeMemory]
        evidence(app, "people-memory-tab")
        XCTAssertTrue(pending.exists, "the pending memory is not on screen")
        XCTAssertTrue(active.exists, "the active memory is not on screen")
        // **The structural half**: only a pending row carries `approve`, so a
        // single list of all three would give the active memory one too.
        XCTAssertTrue(
            app.buttons["approve \(PeopleUnderTest.pendingMemory)"].exists,
            "the pending memory has no `approve` control"
        )
        XCTAssertFalse(
            app.buttons["approve \(PeopleUnderTest.activeMemory)"].exists,
            "an **active** memory is offering `approve` — the three statuses have been "
                + "collapsed into one list"
        )
        // **The positional half**: the pending row is under the band and the
        // active row is below both.
        XCTAssertLessThan(
            band.frame.minY, pending.frame.minY,
            "the pending memory is drawn above its own band"
        )
        XCTAssertLessThan(
            pending.frame.minY, active.frame.minY,
            "the active memory is drawn inside the pending band"
        )
        XCTAssertFalse(
            app.staticTexts[PeopleUnderTest.rejectedMemory].exists,
            "a rejected memory is in the list before the disclosure was opened"
        )

        let disclosure = app.buttons[PeopleUnderTest.rejectedDisclosure]
        XCTAssertTrue(
            peopleScroll(app, to: disclosure),
            "no `\(PeopleUnderTest.rejectedDisclosure)` disclosure"
        )
        disclosure.tap()
        // **Scrolled to, not waited for.** The rows behind the disclosure are
        // a `LazyVStack`, and since the nav regroup People is drawn inside
        // Settings' page, under its heading — so the disclosure sits at the
        // viewport's foot and the row it opens is below it, unbuilt. Train
        // I014's picture shows exactly that: the chevron turned down (the
        // tap worked) and the row not in the tree. A lazy row is built by
        // scrolling to it.
        let rejected = app.staticTexts[PeopleUnderTest.rejectedMemory]
        let shown = rejected.waitForExistence(timeout: 3) || peopleScroll(app, to: rejected)
        evidence(app, "people-memory-rejected")
        XCTAssertTrue(shown, "opening the disclosure did not reveal the rejected memory")
    }

    /// Adding a memory sends it, clears the field and re-reads the list —
    /// three separate real bugs in one round trip.
    @MainActor
    func testPeople_AddingAMemorySavesIt() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let field = app.textFields[PeopleUnderTest.addMemory]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no add-a-memory field")
        peopleType(app, field, PeopleUnderTest.newMemory)
        let save = app.buttons[PeopleUnderTest.saveMemory]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "no save control beside the field")
        XCTAssertTrue(save.isEnabled, "`save` is disabled with a memory typed into the field")
        save.tap()
        // The toast lives 2.4 seconds (``ShellStrings/toastMs``), so it is
        // queried immediately and waited on rather than asserted after the
        // list.
        let toast = app.staticTexts[PeopleUnderTest.savedMemory]
        let said = toast.waitForExistence(timeout: 15)
        // **The counts line first, then the row.** The re-read landing and
        // the row being *drawn* are two different things, and train S005
        // proved it: the header already read `2 active · 1 pending` and the
        // tab `memory (7)` while the row itself was still unbuilt, because
        // the active memories are a `LazyVStack` whose foot sits below a
        // viewport the keyboard had shrunk to 468 points. Waiting on the
        // count says the POST and the re-read were fine; scrolling says the
        // list drew it. Asserting only the first would have stopped testing
        // the second.
        let counted = app.staticTexts[PeopleUnderTest.memoryCountsAfter]
            .waitForExistence(timeout: 20)
        let row = app.staticTexts[PeopleUnderTest.newMemory]
        let arrived = peopleScroll(app, to: row)
        evidence(app, "people-memory-added")
        XCTAssertTrue(
            counted,
            "the memories header never reached `\(PeopleUnderTest.memoryCountsAfter)` — the "
                + "POST or the re-read"
        )
        XCTAssertTrue(arrived, "the saved memory never appeared in the list")
        XCTAssertTrue(said, "nothing said the memory was saved")
        XCTAssertEqual(
            field.value as? String, PeopleUnderTest.addMemory,
            "the field still holds what was typed — a second tap would POST it twice, and the "
                + "memories route does not upsert"
        )
    }

    /// Approving an extracted memory moves it out of the pending band, and
    /// the tab's count does not move with it.
    @MainActor
    func testPeople_ApprovingAPendingMemoryMovesIt() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let approve = app.buttons["approve \(PeopleUnderTest.pendingMemory)"]
        XCTAssertTrue(approve.waitForExistence(timeout: 20), "the pending memory has no approve")
        // Waits, like the post-condition below does: `reload()` runs its five
        // reads in order (sessions, conversations, memories, favourites,
        // preferences), so `approve` existing only proves the MEMORIES read
        // landed — the tab still reads `memory (3)` until favourites answers
        // one round trip later, and a non-waiting `.exists` here failed a
        // correct app on a loaded Simulator.
        XCTAssertTrue(
            app.buttons[PeopleUnderTest.memoryTabSix].waitForExistence(timeout: 20),
            "the memory tab does not count three memories and three favourites before the patch"
        )
        approve.tap()
        let moved = waitUntilGone(approve)
        let stillCounted = app.buttons[PeopleUnderTest.memoryTabSix].waitForExistence(timeout: 20)
        evidence(app, "people-memory-approved")
        XCTAssertTrue(
            moved,
            "the row still offers `approve` — the PATCH did not land, sent the wrong status, or "
                + "the list was never re-read"
        )
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.pendingMemory].exists,
            "the approved memory vanished instead of moving into the active list"
        )
        XCTAssertTrue(
            stillCounted,
            "the tab count changed on an approve, which moves a memory rather than adding one"
        )
    }

    /// A destructive write asks first, and a cancel really cancels.
    @MainActor
    func testPeople_ForgettingAMemoryAsksFirst() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let forget = app.buttons["forget \(PeopleUnderTest.activeMemory)"]
        XCTAssertTrue(forget.waitForExistence(timeout: 20), "the active memory has no forget")
        forget.tap()
        let asked = containing(app, "Forget \"\(PeopleUnderTest.activeMemory)\"?")
        let quoted = asked.waitForExistence(timeout: 15)
        evidence(app, "people-forget-memory-ask")
        XCTAssertTrue(
            quoted,
            "the confirmation does not quote the memory it is about to forget — two memories "
                + "cannot be told apart in a dialog that does not"
        )
        app.buttons["cancel"].tap()
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.activeMemory].waitForExistence(timeout: 15),
            "cancelling the confirmation deleted the memory anyway"
        )

        forget.tap()
        XCTAssertTrue(asked.waitForExistence(timeout: 15), "the confirmation did not come back")
        app.buttons["forget"].tap()
        let gone = waitUntilGone(app.staticTexts[PeopleUnderTest.activeMemory])
        evidence(app, "people-forget-memory-done")
        XCTAssertTrue(gone, "confirming the forget did not delete the memory")
    }

    /// Favourites are grouped by kind, and the header counts both.
    @MainActor
    func testPeople_FavoritesGroupByKind() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let counts = app.staticTexts[PeopleUnderTest.favoriteCounts]
        XCTAssertTrue(
            peopleScroll(app, to: counts),
            "the favourites header never read `\(PeopleUnderTest.favoriteCounts)` — the count, "
                + "the group count, or `plural()` printing `2 kind`"
        )
        // **The groups are a `LazyVStack` under the header, so reaching the
        // header does not build them.** On train S005 the counts line read
        // `3 saved · 2 kinds` at y=685 with the groups' 150 points of
        // reserved space still empty below the fold, and this case failed
        // with "no `team` group" against a screen whose grouping was right.
        // Each group is scrolled to in turn.
        let team = app.staticTexts[PeopleUnderTest.team]
        XCTAssertTrue(peopleScroll(app, to: team), "no `team` group")
        let coffee = app.staticTexts[PeopleUnderTest.coffee]
        XCTAssertTrue(peopleScroll(app, to: coffee), "no `coffee` group")
        evidence(app, "people-favorites")
        // Each pill's control names the kind **and** the value, so a grouping
        // that flattened or mixed the two kinds shows up here rather than in
        // a frame comparison.
        XCTAssertTrue(
            app.buttons["forget team \(PeopleUnderTest.mariners)"].exists,
            "Mariners is not under `team`"
        )
        XCTAssertTrue(
            app.buttons["forget team \(PeopleUnderTest.sounders)"].exists,
            "Sounders is not under `team`"
        )
        XCTAssertFalse(
            app.buttons["forget coffee \(PeopleUnderTest.mariners)"].exists,
            "Mariners is listed under `coffee` — the grouping is not keyed on the kind"
        )
    }

    /// A `save` that could only ever 422 is not enabled.
    @MainActor
    func testPeople_AddingAFavoriteNeedsBothFields() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let kind = app.textFields[PeopleUnderTest.favoriteKind]
        XCTAssertTrue(peopleReveal(app, kind), "no favourite `kind` field")
        peopleType(app, kind, "tea")
        let save = app.buttons[PeopleUnderTest.saveFavorite]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "no favourite save control")
        evidence(app, "people-favorite-half-typed")
        XCTAssertFalse(
            save.isEnabled,
            "`save` is enabled with a kind and no value — it would POST a 422"
        )
        let value = app.textFields[PeopleUnderTest.favoriteValue]
        XCTAssertTrue(value.waitForExistence(timeout: 10), "no favourite `value` field")
        peopleType(app, value, "oolong")
        XCTAssertTrue(save.isEnabled, "`save` stayed disabled with both fields filled in")
        save.tap()
        // The counts line moves only when the POST landed and the re-read
        // came back; the pill itself is in the groups' `LazyVStack`, which
        // needs scrolling to — the two halves this case kept confusing.
        let counts = app.staticTexts[PeopleUnderTest.favoriteCountsAfter]
        let counted = counts.waitForExistence(timeout: 20)
        let pill = app.staticTexts["oolong"]
        let saved = peopleScroll(app, to: pill)
        evidence(app, "people-favorite-added")
        XCTAssertTrue(
            counted,
            "the favourites header never reached "
                + "`\(PeopleUnderTest.favoriteCountsAfter)` — the POST or the re-read"
        )
        XCTAssertTrue(saved, "the new favourite never appeared in its group")
    }

    // MARK: - sessions and conversations

    /// A session expands into **its own** turns, and says so when it has none
    /// in hand.
    ///
    /// The second half is the only way the `no conversation rows` branch is
    /// ever seen: the fixture's second session is deliberately outside the
    /// conversations window, which is the real condition the branch exists
    /// for (`limit=200`, filtered client-side).
    @MainActor
    func testPeople_SessionsExpandToTheirTurns() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "sessions"), "no sessions tab")
        let kitchen = app.buttons
            .matching(NSPredicate(format: "value BEGINSWITH %@", PeopleUnderTest.kitchenSession))
            .firstMatch
        XCTAssertTrue(kitchen.waitForExistence(timeout: 20), "no kitchen session row")
        kitchen.tap()
        let turn = containing(app, PeopleUnderTest.timerTurn)
        let expanded = turn.waitForExistence(timeout: 15)
        evidence(app, "people-session-expanded")
        XCTAssertTrue(
            expanded,
            "the expanded session shows none of its turns — the client-side `session_id` filter "
                + "matched nothing"
        )

        let study = app.buttons
            .matching(NSPredicate(format: "value BEGINSWITH %@", PeopleUnderTest.studySession))
            .firstMatch
        XCTAssertTrue(peopleScroll(app, to: study), "no study session row")
        study.tap()
        let none = app.staticTexts[PeopleUnderTest.noRowsInSession]
        let saidSo = none.waitForExistence(timeout: 15)
        evidence(app, "people-session-no-rows")
        XCTAssertTrue(
            saidSo,
            "a session whose turns fell outside the conversations window drew nothing instead of "
                + "saying so — which means the filter matched everything"
        )
    }

    /// The search reads what was said, and the count reports what it found
    /// over the whole log.
    @MainActor
    func testPeople_ConversationSearchCountsWhatItFound() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "conversations"), "no conversations tab")
        let field = app.textFields[PeopleUnderTest.search]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no conversation search field")
        peopleType(app, field, "sourdough")
        let count = app.staticTexts[PeopleUnderTest.oneOfFour]
        let counted = count.waitForExistence(timeout: 15)
        evidence(app, "people-conversation-search")
        XCTAssertTrue(
            counted,
            "the count beside the search does not read `\(PeopleUnderTest.oneOfFour)` — either "
                + "the search read the wrong fields or the count reports the unfiltered total"
        )
        XCTAssertFalse(
            containing(app, PeopleUnderTest.quietTurn).exists,
            "a turn the word is not in survived the filter"
        )
    }

    /// The long answer is cut, and `more` is the only way to see its tail.
    @MainActor
    func testPeople_LongAnswerExpands() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "conversations"), "no conversations tab")
        let more = app.buttons[PeopleUnderTest.more]
        XCTAssertTrue(more.waitForExistence(timeout: 20), "no `more` on the long answer")
        evidence(app, "people-answer-collapsed")
        XCTAssertFalse(
            containing(app, PeopleUnderTest.longAnswerTail).exists,
            "the tail of a 186-character answer is on screen before `more` was tapped — the "
                + "130/140 truncation is missing or inverted"
        )
        more.tap()
        let tail = containing(app, PeopleUnderTest.longAnswerTail)
        let shown = tail.waitForExistence(timeout: 15)
        let toggled = app.buttons[PeopleUnderTest.less].exists
        evidence(app, "people-answer-expanded")
        XCTAssertTrue(shown, "`more` did not reveal the rest of the answer")
        XCTAssertTrue(toggled, "the control still says `more` after expanding")
    }

    // MARK: - what failure looks like

    /// **F-A008.** A read that failed says so and offers a retry; it never
    /// reads as "nothing here yet", and the tab counts it as `?` rather than
    /// as 0.
    @MainActor
    func testPeople_AFailedListSaysSoAndRetries() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        peopleFail("memories")
        defer { peopleFail(nil) }
        let app = launch(push: PeopleUnderTest.title)
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.subtitle].waitForExistence(timeout: 30),
            "the roster did not draw"
        )
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let card = app.staticTexts[PeopleUnderTest.couldNotReach]
        let saidSo = card.waitForExistence(timeout: 20)
        evidence(app, "people-memories-failed")
        XCTAssertTrue(saidSo, "a 500 on the memories read drew no error card")
        XCTAssertFalse(
            containing(app, PeopleUnderTest.noMemories).exists,
            "a failed read is claiming the person has no memories — which is F-A008, the bug "
                + "this screen's whole per-source failure design exists for"
        )
        XCTAssertTrue(
            app.buttons[PeopleUnderTest.memoryTabUnknown].exists,
            "the tab reads a number for a list that failed to load, not `?`"
        )

        peopleFail(nil)
        let retry = app.buttons[PeopleUnderTest.retry]
        XCTAssertTrue(retry.waitForExistence(timeout: 10), "the error card has no retry")
        retry.tap()
        let row = app.staticTexts[PeopleUnderTest.activeMemory]
        let arrived = row.waitForExistence(timeout: 25)
        evidence(app, "people-memories-retried")
        XCTAssertTrue(arrived, "retry did not re-read the memories")
    }

    /// One toast names every list that failed, in tab order, and quotes the
    /// first failure.
    @MainActor
    func testPeople_FailedListsAreNamedInOneToast() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        peopleFail("sessions")
        peopleFail("memories")
        defer { peopleFail(nil) }
        let app = launch(push: PeopleUnderTest.title)
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.subtitle].waitForExistence(timeout: 30),
            "the roster did not draw"
        )
        let row = peopleRow(app, PeopleUnderTest.jordan)
        XCTAssertTrue(row.waitForExistence(timeout: 20), "no Jordan row")
        row.tap()
        // Queried before the reads finish, because the toast is raised when
        // the fifth one lands and lives 2.4 seconds.
        let toast = startingWith(app, PeopleUnderTest.failToast)
        let said = toast.waitForExistence(timeout: 30)
        evidence(app, "people-failure-toast")
        XCTAssertTrue(
            said,
            "no toast named the two lists that failed in tab order — `personDetailFailureToast` "
                + "lost a name, the order, or the quoted reason"
        )
    }

    // MARK: - the denylist

    /// The denylist explains itself, and names nobody.
    @MainActor
    func testPeople_DenylistExplainsItself() throws {
        let app = peopleLaunch()
        let card = app.buttons[PeopleUnderTest.denylistCard]
        XCTAssertTrue(peopleScroll(app, to: card), "no denylist card at the foot of the roster")
        card.tap()
        let count = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", PeopleUnderTest.optedOut))
            .firstMatch
        let opened = count.waitForExistence(timeout: 20)
        evidence(app, "people-denylist")
        XCTAssertTrue(opened, "the denylist view does not say how many voices opted out")
        XCTAssertTrue(
            containing(app, PeopleUnderTest.hashesOnFile).exists,
            "the denylist view does not say the count is hashes"
        )
        // The one privacy claim this screen makes out loud.
        for name in [PeopleUnderTest.jordan, PeopleUnderTest.chevy, PeopleUnderTest.alex] {
            XCTAssertFalse(
                containing(app, name).exists,
                "\(name) is named on the denylist view, which claims nothing identifying is kept"
            )
        }
    }

    /// **[[read-after-await-wrong-key]].** Person A's answers must not land
    /// under person B.
    ///
    /// The switch happens **inside** the round trip — the fixture holds
    /// person 1's memories for two and a half seconds and the case goes back
    /// and opens person 2 while that request is outstanding — which is the
    /// only arrangement that tests it. Person 2 has no memories at all, so
    /// the word `peanuts` appearing anywhere afterwards is person 1's answer
    /// published under person 2.
    @MainActor
    func testPeople_SwitchingPersonMidFlightKeepsTheAnswersStraight() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        peopleFail("memories", status: 0, delayMs: 2500)
        defer { peopleFail(nil) }
        let app = launch(push: PeopleUnderTest.title)
        XCTAssertTrue(
            app.staticTexts[PeopleUnderTest.subtitle].waitForExistence(timeout: 30),
            "the roster did not draw"
        )
        let jordan = peopleRow(app, PeopleUnderTest.jordan)
        XCTAssertTrue(jordan.waitForExistence(timeout: 20), "no Jordan row")
        jordan.tap()
        let back = app.buttons[PeopleUnderTest.back]
        XCTAssertTrue(back.waitForExistence(timeout: 20), "Jordan's detail did not open")
        // Inside the held request: the sessions and conversations reads are
        // immediate, the memories read is the one being delayed.
        usleep(700_000)
        back.tap()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.chevy), "Chevy's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")

        // Watch for the whole of the held request's remaining life, and fail
        // the moment person 1's memory shows up under person 2.
        let empty = containing(app, PeopleUnderTest.noMemories)
        let leaked = app.staticTexts[PeopleUnderTest.activeMemory]
        let deadline = Date().addingTimeInterval(25)
        var sawEmpty = false
        while Date() < deadline {
            if leaked.exists { break }
            if empty.exists { sawEmpty = true; break }
            usleep(200_000)
        }
        evidence(app, "people-switched-mid-flight")
        XCTAssertFalse(
            leaked.exists,
            "`\(PeopleUnderTest.activeMemory)` is Jordan's and it is on Chevy's memory tab — a "
                + "read published after the person was switched"
        )
        XCTAssertTrue(
            sawEmpty,
            "Chevy's memory tab never drew its empty note, so the case proved nothing about "
                + "whose answer landed"
        )
    }

    // MARK: - type size and the audit

    /// The roster row **grows**, measured at both sizes rather than trusted.
    ///
    /// A named Dynamic Type audit finding has twice been a false positive on
    /// this track, so this compares the row's own height at the ordinary size
    /// and at AX5: a fixed `frame(width:)` on the avatar or a
    /// `minimumScaleFactor` anywhere in the row shows up as a height that
    /// barely moves.
    @MainActor
    func testPeople_GrowsAtTheLargestTypeSize() throws {
        let ordinary = peopleLaunch()
        let small = peopleRow(ordinary, PeopleUnderTest.jordan)
        XCTAssertTrue(small.waitForExistence(timeout: 25), "no Jordan row at the ordinary size")
        let smallHeight = small.frame.height
        evidence(ordinary, "people-row-ordinary")
        ordinary.terminate()

        let large = peopleLaunch(mode: "UICTContentSizeCategoryAccessibilityXXXL")
        let big = peopleRow(large, PeopleUnderTest.jordan)
        XCTAssertTrue(big.waitForExistence(timeout: 25), "no Jordan row at AX5")
        let bigHeight = big.frame.height
        evidence(large, "people-row-ax5")

        print("PEOPLE ROW HEIGHT ordinary=\(smallHeight) ax5=\(bigHeight)")
        XCTAssertGreaterThan(
            bigHeight, smallHeight * 1.5,
            "the roster row is \(Int(smallHeight)) points at the ordinary size and "
                + "\(Int(bigHeight)) at AX5 — text that was asked to grow did not, which is a "
                + "fixed frame or a `minimumScaleFactor`"
        )
    }

    /// One finding's signature: a `Contrast failed` the audit could not
    /// attribute to any element.
    ///
    /// It is the signature of **pixels the audit could not read**, not of a
    /// colour — `StubHouseUITests.knownAnonymousHeadlines`' own doc comment
    /// says so for Home's five, four of which were "elements the bar was
    /// drawn over or that straddled a scroll edge". It is spelled out here
    /// rather than added to that set, because it is not a floor: it is a
    /// *retry* condition, and a finding that survives the retry still fails.
    static func peopleAnonymousContrast(_ line: String) -> Bool {
        line.hasPrefix("Contrast failed") && line.hasSuffix("| element: nil")
    }

    /// The title of the notification another item posts during a full-class
    /// run, as a literal.
    ///
    /// `AlertsStrings.incidentTamperTitle`
    /// (`DomovoiKit/AlertsStrings.swift:77`) is the source of truth and is
    /// package-tested against Android's string; this target links no
    /// DomovoiKit, so the literal is copied here the way
    /// `StubHouseUITests+Alerts.swift` copies `alertsFireTitle`. It is only
    /// ever used to *recognise* a banner, never to assert one.
    static let peopleOverlayTitle = "Signs of tampering"

    /// SpringBoard — the app a notification banner belongs to.
    ///
    /// A banner is drawn over Domovoi by a different process, so it is in no
    /// Domovoi tree. That is the whole reason Apple's audit can attribute
    /// nothing it measures under one: `app.debugDescription` for the I001
    /// failure does not contain the banner at all.
    @MainActor
    var peopleSpringboard: XCUIApplication {
        XCUIApplication(bundleIdentifier: "com.apple.springboard")
    }

    /// Whether SpringBoard has a notification banner over the app right now.
    ///
    /// Two queries because each misses the other's case: the short-look
    /// view's identifier is Apple's and has moved between releases, and the
    /// title only catches the one banner this run is known to post — but a
    /// banner of any other kind is still something drawn over the app.
    @MainActor
    func peopleBannerIsOverTheApp() -> Bool {
        let springboard = peopleSpringboard
        return springboard.otherElements["NotificationShortLookView"].exists
            || springboard.staticTexts[Self.peopleOverlayTitle].exists
    }

    /// Wait until nothing SpringBoard owns is drawn over the app, and say
    /// whether anything was.
    ///
    /// This replaces a blind eight-second sleep. The sleep was a guess at how
    /// long a banner lives; this is the banner itself, read out of the
    /// process that owns it, so the audit runs on a screen that is **measured
    /// to be uncovered** rather than one that probably is by now.
    ///
    /// The return value is carried into the failure message on purpose: a run
    /// with anonymous contrast and *no* banner is a different problem from
    /// this one, and whoever reads the next train's log should not have to
    /// re-derive which it was.
    ///
    /// It never dismisses the banner. A swipe aimed at something that has
    /// just gone lands on the app, and on this screen the thing under the bar
    /// is a lazy roster: a stray scroll would leave a row straddling the
    /// viewport edge, which is itself a source of contrast findings the audit
    /// cannot attribute.
    @MainActor
    @discardableResult
    func peopleWaitForAnUncoveredScreen(_ what: String) -> Bool {
        var sawOne = false
        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline {
            guard peopleBannerIsOverTheApp() else {
                if sawOne { print("PEOPLE AUDIT [\(what)] the banner has cleared") }
                return sawOne
            }
            if !sawOne {
                print(
                    "PEOPLE AUDIT [\(what)] SpringBoard has a notification banner over "
                        + "the app — waiting for it to clear before auditing"
                )
            }
            sawOne = true
            usleep(500_000)
        }
        print("PEOPLE AUDIT [\(what)] a banner is STILL over the app after 15s")
        return true
    }

    /// Apple's audit on the roster, classified by signature.
    ///
    /// `rest` is what this asserts: a finding Apple can attribute to an
    /// element is a defect, and the anonymous Dynamic Type findings of
    /// ``knownPushedBarFloor`` are a measured platform floor. A total pinned
    /// here would hide a new contrast finding the moment one of the floor's
    /// went away.
    ///
    /// ### Why it audits more than once
    ///
    /// On train I001 — the first run of the whole class together — this case
    /// failed with three `Contrast failed | element: nil` against a People
    /// screen whose colours are **measured to pass**: every text leaf in the
    /// run's own screenshot sits at 5.0:1 or better against what is behind
    /// it (the roster avatars, the nearest things to the line, measure
    /// 5.04–5.25:1 white-on-fill). Nothing on People had changed since train
    /// S011, where the same screen audited 0.
    ///
    /// What *had* changed is that the class now runs together. The run's
    /// screenshot shows an iOS notification banner — `Signs of tampering ·
    /// The web search helper is running a different image than the one
    /// Domovoi ex…` — sitting over the top of the app, posted by another
    /// item once an alerts case granted notification authorisation for the
    /// rest of the run. The banner belongs to SpringBoard, so it is in no
    /// app tree and the audit can attribute nothing it finds under it: three
    /// anonymous findings, one per covered top-bar leaf. Home proves it is
    /// not a People defect — `knownHomeIssues` is 0, Home's code did not
    /// change, and in that same run Home reported the same three anonymous
    /// contrast findings with the same banner in its screenshot.
    ///
    /// So when everything left is that one signature, the screen is given
    /// time to clear and audited again. **This cannot hide a colour defect**:
    /// a colour is still there eight seconds later, and the one real contrast
    /// defect this screen has had — the first avatar palette, white on a
    /// green at 4.10:1 — was reported by every single run it was in (S002,
    /// S005, S008) and by none after it was darkened. Both audits are
    /// printed, and a finding that survives fails the case exactly as before.
    ///
    /// ### Round 2: the pixels, and why the wait is no longer a guess
    ///
    /// The three findings were measured, not reasoned about. Sampling R001's
    /// own screenshots at the frames in their tree attachments:
    ///
    /// | leaf (same frame in both shots) | under the banner | banner-free |
    /// |---|---|---|
    /// | `unverified` | **4.12:1** | 5.72:1 |
    /// | `  live` | **4.15:1** | 7.43:1 |
    /// | `domovoi / people` (now `domovoi / settings`: People is a Settings section) | — | 7.43:1 |
    /// | `127.0.0.1:6601` | — | 15.47:1 |
    ///
    /// The two that fail fail at the banner's own grey timestamp — `now`,
    /// `#787575` on `#F5F3F1` — which sits across those two frames. The
    /// banner-free column is from `testPeople_AddingAFavoriteNeedsBothFields`
    /// in the **same run**, so it is the same build, the same palette and the
    /// same frames: the pixels the audit read were SpringBoard's, and the
    /// bar's own are nowhere near the line. People's own leaves measure
    /// 5.07:1 (`home` tab) to 21:1, and the four avatars 5.12–5.64:1.
    ///
    /// So the retry no longer sleeps and hopes. It asks SpringBoard whether a
    /// banner is there (``peopleBannerIsOverTheApp()``) and audits once there
    /// is not — before the first audit as well, because in I001 the banner
    /// was already up by the time the roster had drawn.
    @MainActor
    func testPeople_PassesTheAccessibilityAudit() throws {
        let app = peopleLaunch()
        // Before the first audit, not only between audits: `peopleLaunch`
        // returns about four seconds in, and that is when the banner lands.
        var sawABanner = peopleWaitForAnUncoveredScreen("first")
        evidence(app, "people-audit")
        var split = Self.floorAndRest(try auditIssues(app, "people-audit"))
        print(
            "PEOPLE AUDIT floor=\(split.floor) rest=\(split.rest.count) "
                + "banner-seen=\(sawABanner)"
        )

        // Up to four audits. One banner is waited out by the gate above; this
        // is for one that arrives *during* an audit, which is the only way a
        // screen measured uncovered can still report the signature.
        var attempt = 1
        while attempt < 4,
            !split.rest.isEmpty,
            split.rest.allSatisfy({ Self.peopleAnonymousContrast($0) })
        {
            print(
                "PEOPLE AUDIT attempt \(attempt) was all anonymous contrast — "
                    + "something is drawn over the app; waiting it out"
            )
            if peopleWaitForAnUncoveredScreen("attempt \(attempt + 1)") { sawABanner = true }
            // The dismissal animation outlives the element: the banner is
            // still sliding off the bar for a moment after SpringBoard stops
            // reporting it.
            let until = Date().addingTimeInterval(2)
            while Date() < until { usleep(250_000) }
            attempt += 1
            let name = "people-audit-\(attempt)"
            evidence(app, name)
            split = Self.floorAndRest(try auditIssues(app, name))
            print("PEOPLE AUDIT attempt \(attempt) floor=\(split.floor) rest=\(split.rest.count)")
        }

        XCTAssertTrue(
            split.rest.isEmpty,
            "Apple's audit has findings against People beyond the anonymous floor, in "
                + "\(attempt) audit(s) of a screen SpringBoard reported nothing over "
                + "(a banner was over the app at some point in this case: \(sawABanner)): "
                + split.rest.joined(separator: " / ")
        )
    }
}
