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
/// not by tapping: `Route.people` is on no phone bar
/// (`defaultPhoneBar`, `Route.swift:118`) and is in `sharedScreenHidden`, so
/// Home's everything grid is the only tap route and it needs a scroll on a
/// phone. Nothing here opens the connection sheet.
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

        static let kamron = "Kamron"
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
        let sheet = app.scrollViews.firstMatch
        guard sheet.waitForExistence(timeout: 20) else {
            print("=== NOTHING TO SCROLL — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            sheet.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// Type into a field, having put the caret at its end.
    ///
    /// A tap puts the caret where it lands, which is why the tap is at 0.95
    /// of the width: a tap in the middle of a pre-filled field appends in the
    /// wrong place, and three Chat cases were written twice before that was
    /// understood.
    @MainActor
    func peopleType(_ field: XCUIElement, _ text: String) {
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
            PeopleUnderTest.kamron, PeopleUnderTest.chevy,
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
        let kamron = peopleRow(app, PeopleUnderTest.kamron)
        XCTAssertTrue(kamron.exists, "no Kamron row to check")
        XCTAssertFalse(
            (kamron.value as? String ?? "").contains(PeopleUnderTest.sameName),
            "Kamron shares a name with nobody and the row says they do: "
                + "\(kamron.value as? String ?? "nothing")"
        )
    }

    /// The filter narrows the roster, and the two empty notes are not
    /// interchangeable.
    @MainActor
    func testPeople_FilterNarrowsAndSaysSo() throws {
        let app = peopleLaunch()
        let field = app.textFields[PeopleUnderTest.filter]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no filter field")
        peopleType(field, "chev")
        XCTAssertTrue(
            peopleRow(app, PeopleUnderTest.chevy).waitForExistence(timeout: 10),
            "`chev` does not match Chevy — the filter is case-sensitive, or over another field"
        )
        XCTAssertTrue(
            waitUntilGone(peopleRow(app, PeopleUnderTest.kamron)),
            "`chev` left Kamron in the roster — the filter narrowed nothing"
        )

        clearField(app)
        XCTAssertTrue(
            peopleRow(app, PeopleUnderTest.kamron).waitForExistence(timeout: 10),
            "clearing the filter did not bring the household back"
        )

        peopleType(field, "zzz")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        evidence(app, "people-profile-kamron")
        XCTAssertTrue(counted, "the voice-profiles tile does not read 3 for a person with three")
        XCTAssertTrue(noted, "Kamron's notes are not on the profile")

        app.buttons[PeopleUnderTest.back].tap()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.chevy), "Chevy's detail did not open")
        let placeholder = app.staticTexts[PeopleUnderTest.noNotes]
        let saidNone = placeholder.waitForExistence(timeout: 15)
        evidence(app, "people-profile-chevy")
        XCTAssertTrue(saidNone, "a person with no notes does not get the `no notes yet` line")
        XCTAssertFalse(
            app.staticTexts[PeopleUnderTest.notes].exists,
            "Chevy's profile is showing Kamron's notes — the tiles and the notes are reading a "
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
        // them" — advice about a control this screen does not have. The
        // replacement is one of this screen's two iOS-only sentences.
        XCTAssertFalse(
            note.label.contains("rename one of them"),
            "the note still tells the reader to rename somebody, which a phone cannot do: "
                + note.label
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
        let sentence = containing(app, PeopleUnderTest.whereRenamingIs)
        let explained = sentence.waitForExistence(timeout: 15)
        evidence(app, "people-no-admin-controls")
        XCTAssertFalse(
            app.buttons["rename \(PeopleUnderTest.kamron)"].exists,
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        let rejected = app.staticTexts[PeopleUnderTest.rejectedMemory]
        let shown = rejected.waitForExistence(timeout: 15)
        evidence(app, "people-memory-rejected")
        XCTAssertTrue(shown, "opening the disclosure did not reveal the rejected memory")
    }

    /// Adding a memory sends it, clears the field and re-reads the list —
    /// three separate real bugs in one round trip.
    @MainActor
    func testPeople_AddingAMemorySavesIt() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let field = app.textFields[PeopleUnderTest.addMemory]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no add-a-memory field")
        peopleType(field, PeopleUnderTest.newMemory)
        let save = app.buttons[PeopleUnderTest.saveMemory]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "no save control beside the field")
        XCTAssertTrue(save.isEnabled, "`save` is disabled with a memory typed into the field")
        save.tap()
        // The toast lives 2.4 seconds (``ShellStrings/toastMs``), so it is
        // queried immediately and waited on rather than asserted after the
        // list.
        let toast = app.staticTexts[PeopleUnderTest.savedMemory]
        let said = toast.waitForExistence(timeout: 15)
        let row = app.staticTexts[PeopleUnderTest.newMemory]
        let arrived = row.waitForExistence(timeout: 20)
        evidence(app, "people-memory-added")
        XCTAssertTrue(arrived, "the saved memory never appeared — the POST or the re-read")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let counts = app.staticTexts[PeopleUnderTest.favoriteCounts]
        XCTAssertTrue(
            peopleScroll(app, to: counts),
            "the favourites header never read `\(PeopleUnderTest.favoriteCounts)` — the count, "
                + "the group count, or `plural()` printing `2 kind`"
        )
        evidence(app, "people-favorites")
        XCTAssertTrue(app.staticTexts[PeopleUnderTest.team].exists, "no `team` group")
        XCTAssertTrue(app.staticTexts[PeopleUnderTest.coffee].exists, "no `coffee` group")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
        XCTAssertTrue(peopleTab(app, "memory"), "no memory tab")
        let kind = app.textFields[PeopleUnderTest.favoriteKind]
        XCTAssertTrue(peopleScroll(app, to: kind), "no favourite `kind` field")
        peopleType(kind, "tea")
        let save = app.buttons[PeopleUnderTest.saveFavorite]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "no favourite save control")
        evidence(app, "people-favorite-half-typed")
        XCTAssertFalse(
            save.isEnabled,
            "`save` is enabled with a kind and no value — it would POST a 422"
        )
        let value = app.textFields[PeopleUnderTest.favoriteValue]
        XCTAssertTrue(value.waitForExistence(timeout: 10), "no favourite `value` field")
        peopleType(value, "oolong")
        XCTAssertTrue(save.isEnabled, "`save` stayed disabled with both fields filled in")
        save.tap()
        let pill = app.staticTexts["oolong"]
        let saved = pill.waitForExistence(timeout: 20)
        evidence(app, "people-favorite-added")
        XCTAssertTrue(saved, "the new favourite never appeared — the POST or the re-read")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
        XCTAssertTrue(peopleTab(app, "conversations"), "no conversations tab")
        let field = app.textFields[PeopleUnderTest.search]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no conversation search field")
        peopleType(field, "sourdough")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.kamron), "Kamron's detail did not open")
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
        let row = peopleRow(app, PeopleUnderTest.kamron)
        XCTAssertTrue(row.waitForExistence(timeout: 20), "no Kamron row")
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
        for name in [PeopleUnderTest.kamron, PeopleUnderTest.chevy, PeopleUnderTest.alex] {
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
        let kamron = peopleRow(app, PeopleUnderTest.kamron)
        XCTAssertTrue(kamron.waitForExistence(timeout: 20), "no Kamron row")
        kamron.tap()
        let back = app.buttons[PeopleUnderTest.back]
        XCTAssertTrue(back.waitForExistence(timeout: 20), "Kamron's detail did not open")
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
            "`\(PeopleUnderTest.activeMemory)` is Kamron's and it is on Chevy's memory tab — a "
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
        let small = peopleRow(ordinary, PeopleUnderTest.kamron)
        XCTAssertTrue(small.waitForExistence(timeout: 25), "no Kamron row at the ordinary size")
        let smallHeight = small.frame.height
        evidence(ordinary, "people-row-ordinary")
        ordinary.terminate()

        let large = peopleLaunch(mode: "UICTContentSizeCategoryAccessibilityXXXL")
        let big = peopleRow(large, PeopleUnderTest.kamron)
        XCTAssertTrue(big.waitForExistence(timeout: 25), "no Kamron row at AX5")
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

    /// Apple's audit on the roster, classified by signature.
    ///
    /// `rest` is what this asserts: a finding Apple can attribute to an
    /// element is a defect, and the anonymous Dynamic Type findings of
    /// ``knownPushedBarFloor`` are a measured platform floor. A total pinned
    /// here would hide a new contrast finding the moment one of the floor's
    /// went away.
    @MainActor
    func testPeople_PassesTheAccessibilityAudit() throws {
        let app = peopleLaunch()
        evidence(app, "people-audit")
        let found = try auditIssues(app, "people-audit")
        let split = Self.floorAndRest(found)
        print("PEOPLE AUDIT floor=\(split.floor) rest=\(split.rest.count)")
        XCTAssertTrue(
            split.rest.isEmpty,
            "Apple's audit has findings against People beyond the anonymous floor: "
                + split.rest.joined(separator: " / ")
        )
    }
}
