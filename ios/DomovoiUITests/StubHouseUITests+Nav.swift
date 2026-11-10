import XCTest

/// The nav regroup (Android adb2ad06, core 5d2b1807): Home's grid under
/// **media** and **tools** headings, Settings → **Household** holding Rooms,
/// People, Phones & browsers and Printers & scanners, People reached through
/// Settings — by a People tab saved on the bar too — and the old
/// `rooms-and-devices` key still opening Household.
///
/// An iPhone has no drawer: its grouped launcher is Home's "everything" grid,
/// so that is what the group cases read. The iPad sidebar's groups are not
/// reachable from a phone Simulator and are not claimed here.
///
/// Every miss prints the tree. Nothing here opens the connection sheet.
extension StubHouseUITests {

    /// The words these cases match on, spelt out rather than imported, like
    /// every other string in this suite.
    enum NavUnderTest {
        /// ``NavGroupInfo/label``, lowercased as the grid draws it.
        static let mediaHeading = "media"
        static let toolsHeading = "tools"
        /// ``SettingsGroup/label`` and Household's four sections, in order.
        static let household = "Household"
        static let householdSections = ["Rooms", "People", "Phones & browsers", "Printers & scanners"]
        static let system = "System"
        static let appLayout = "App layout"
        /// ``ShellStrings/backTo(_:)``, ``ShellStrings/allSettings``.
        static let backToHousehold = "back to Household"
        static let backToSystem = "back to System"
        static let allSettings = "all settings"
        /// ``ShellStrings/addLabelToTheBar(_:)`` for People.
        static let addPeople = "add People to the bar"
        /// The People fixture's counts line: only a completed roster read
        /// draws it (``PeopleUnderTest/subtitle``).
        static let peopleCounts = "4 enrolled · 1 heard in the last 5 min"
        /// ``SettingsSection/sub`` for People, drawn only on its section page.
        static let peopleSectionSub =
            "Who lives here: each person's profile, what Domovoi remembers, and their conversations."
    }

    // MARK: - helpers

    /// The part of the page a tap can land on: the tallest scroll view (the
    /// page's own, not the tab strip's or the keyboard's), less a navigation
    /// bar sitting on its top edge — ``peopleViewport(_:)``'s measurement.
    @MainActor
    func navViewport(_ app: XCUIApplication) -> CGRect? {
        let views = app.scrollViews.allElementsBoundByIndex.map(\.frame)
        guard let sheet = views.max(by: { $0.height < $1.height }), sheet.height > 120 else { return nil }
        var top = sheet.minY
        for bar in app.navigationBars.allElementsBoundByIndex {
            let frame = bar.frame
            if frame.minY <= top + 1, frame.maxY > top, frame.maxY < sheet.maxY { top = frame.maxY }
        }
        return CGRect(x: sheet.minX, y: top, width: sheet.width, height: sheet.maxY - top)
    }

    /// Flick slowly until `element` is **wholly** inside the page's viewport.
    ///
    /// Not `isHittable`. Train I014 is why: `back to Household` measured
    /// `{{16, 768.3}, {143, 44}}` against a page whose scroll view ends at
    /// y = 770 with the phone bar from 774, and XCUITest called it hittable
    /// and tapped its centre, y = 790 — on the bar. The same happened to
    /// `back to System` at `{{16, 760}, {117.7, 44}}`. Neither tap did what
    /// it said, and the next wait failed with no message. A frame inside the
    /// viewport is a tap that lands on the button.
    ///
    /// `inPage: false` is for the phone bar's tabs, which are outside every
    /// scroll view and are reached by existing.
    @MainActor
    func navReach(
        _ app: XCUIApplication,
        _ element: XCUIElement,
        inPage: Bool = true,
        flicks: Int = 12
    ) -> Bool {
        func inside() -> Bool {
            guard element.exists else { return false }
            guard inPage else { return element.isHittable }
            guard let page = navViewport(app) else { return element.isHittable }
            let frame = element.frame
            return frame.minY >= page.minY - 0.5 && frame.maxY <= page.maxY + 0.5
        }
        var done = 0
        while !inside(), done < flicks {
            if inPage, element.exists, let page = navViewport(app), element.frame.minY < page.minY {
                app.swipeDown(velocity: .slow)
            } else {
                app.swipeUp(velocity: .slow)
            }
            done += 1
        }
        if !inside() {
            let page = navViewport(app).map { "\($0)" } ?? "none"
            print(
                "=== \(element) NOT WHOLLY IN THE PAGE after \(done) flicks "
                    + "(frame \(element.exists ? "\(element.frame)" : "absent"), page \(page)) — tree follows ==="
            )
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// Tap a button by its exact label once it is wholly reachable; print
    /// the tree on a miss.
    @MainActor
    func navTap(_ app: XCUIApplication, _ label: String, inPage: Bool = true, timeout: Double = 20) -> Bool {
        let button = app.buttons[label]
        guard button.waitForExistence(timeout: timeout) else {
            print("=== NO `\(label)` BUTTON — tree follows ===")
            print(app.debugDescription)
            return false
        }
        guard navReach(app, button, inPage: inPage) else { return false }
        button.tap()
        return true
    }

    /// Whether Household's group page is up: its four sections, in order,
    /// and no App layout among them.
    @MainActor
    func navHouseholdIsUp(_ app: XCUIApplication) -> Bool {
        let all = app.buttons[NavUnderTest.allSettings]
        guard all.waitForExistence(timeout: 20) else {
            print("=== NOT ON A GROUP PAGE — tree follows ===")
            print(app.debugDescription)
            return false
        }
        var tops: [CGFloat] = []
        for label in NavUnderTest.householdSections {
            let row = app.buttons[label]
            guard row.waitForExistence(timeout: 10) else {
                print("=== HOUSEHOLD HAS NO `\(label)` — tree follows ===")
                print(app.debugDescription)
                return false
            }
            tops.append(row.frame.minY)
        }
        XCTAssertEqual(tops, tops.sorted(), "Household's sections are not in Android's order: \(tops)")
        XCTAssertTrue(app.staticTexts[NavUnderTest.household].exists, "the group page has no Household heading")
        XCTAssertFalse(app.buttons[NavUnderTest.appLayout].exists, "App layout is still in Household")
        return true
    }

    // MARK: - the grid's groups

    /// Home's grid, with the default bar (home · music · videos · chat):
    /// `media` over Podcasts and Audiobooks, `tools` over News, Calendar and
    /// Files, then Settings and the manual unheaded — and no People tile.
    @MainActor
    func testNav_HomeGridHasMediaAndToolsGroups() throws {
        let app = launch()
        // News is a shared-screen-hidden tile, so its arrival is the device
        // registration having answered "not shared".
        let news = app.buttons["News"]
        guard news.waitForExistence(timeout: 30) else {
            print("=== NO NEWS TILE — tree follows ===")
            print(app.debugDescription)
            return XCTFail("Home's grid never drew News")
        }
        let media = app.staticTexts[NavUnderTest.mediaHeading]
        let tools = app.staticTexts[NavUnderTest.toolsHeading]
        guard media.waitForExistence(timeout: 10), tools.waitForExistence(timeout: 10) else {
            print("=== NO GROUP HEADINGS — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the grid has no media/tools headings")
        }
        _ = navReach(app, app.buttons["Manual"])
        evidence(app, "nav-home-grid")

        func top(_ label: String) -> CGFloat? {
            let tile = app.buttons[label]
            guard tile.exists else {
                print("=== NO `\(label)` TILE — tree follows ===")
                print(app.debugDescription)
                return nil
            }
            return tile.frame.minY
        }
        let podcasts = try XCTUnwrap(top("Podcasts"), "no `Podcasts` tile on Home's grid")
        let audiobooks = try XCTUnwrap(top("Audiobooks"), "no `Audiobooks` tile on Home's grid")
        let newsTop = try XCTUnwrap(top("News"), "no `News` tile on Home's grid")
        let calendar = try XCTUnwrap(top("Calendar"), "no `Calendar` tile on Home's grid")
        let files = try XCTUnwrap(top("Files"), "no `Files` tile on Home's grid")
        let settings = try XCTUnwrap(top("Settings"), "no `Settings` tile on Home's grid")
        let manual = try XCTUnwrap(top("Manual"), "no `Manual` tile on Home's grid")
        print(
            "NAV GRID media=\(media.frame.minY) podcasts=\(podcasts) audiobooks=\(audiobooks) "
                + "tools=\(tools.frame.minY) news=\(newsTop) calendar=\(calendar) files=\(files) "
                + "settings=\(settings) manual=\(manual)"
        )
        XCTAssertLessThan(media.frame.minY, podcasts, "media is not above its tiles")
        XCTAssertLessThan(audiobooks, tools.frame.minY, "Audiobooks is not above the tools heading")
        XCTAssertLessThan(tools.frame.minY, newsTop, "tools is not above its tiles")
        XCTAssertLessThanOrEqual(newsTop, calendar, "Calendar is not in the tools run")
        XCTAssertLessThan(files, settings, "Settings is not after the tools run")
        XCTAssertLessThanOrEqual(settings, manual, "the manual is not after Settings")
        XCTAssertFalse(app.buttons["People"].exists, "People is still a tile on Home's grid")
    }

    /// Apple's audit on the grid with its headings in view, classified by
    /// signature (``floorAndRest(_:)``): an attributed finding fails.
    ///
    /// ### The audit moves this page, so it is judged the way Rooms is
    ///
    /// Train I016 failed this case on seven `Contrast failed` findings spread
    /// over the whole screen — `10:30am`, `call Mum`, `Podcasts`, `News`,
    /// `Calendar`, `Manual`, and the Today card's `calendar` link — while
    /// `testHomePassesApplesAccessibilityAudit` passed the same inks on the
    /// unscrolled Home. Every finding's frame sat **41.3 points below** the
    /// same element in the tree attached just before the audit (`Podcasts`
    /// 422.0 → 463.3, `News` 608.3 → 649.7, `10:30am` 221.0 → 262.3,
    /// `calendar` 22.0 → 63.3): the case scrolls Home to its foot to bring
    /// the grid in, and the audit's re-layout left the page 41.3 points
    /// higher in its content. Sampled off the case's own picture, each
    /// element measures where it is drawn — `10:30am` and `call Mum`
    /// `#000000` on `#F1F1F2` 18.60:1, the four tile labels `#975800` on
    /// `#F2F2F7` 5.07:1 — and at the finding's frame the picture holds only
    /// card fill, 1.01:1 to 1.13:1. `calendar` was not on the page at all:
    /// it was scrolled away above it, and the finding put it across the
    /// page's top edge.
    ///
    /// So this is the Rooms roster's judgement (`testRooms_Audit`, I008),
    /// with its own helpers: when the audit moved the page, every
    /// pixel-sampled finding's element is read off the picture where it was
    /// drawn and **fails under 4.5:1** (``roomsAssertOwnInk``), and the
    /// pixel-sampled findings are retaken at the settled position, which must
    /// not move again. A retaken finding against an element the page clips at
    /// its top or foot is measured by bringing that element wholly into view
    /// and reading its ink (``navClippedMeasured(_:_:on:)``); everything else
    /// fails.
    @MainActor
    func testNav_TheGroupedGridAudits() throws {
        let app = launch()
        guard app.staticTexts[NavUnderTest.toolsHeading].waitForExistence(timeout: 30) else {
            print("=== NO TOOLS HEADING — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the grid has no tools heading to audit")
        }
        XCTAssertTrue(navReach(app, app.buttons["Manual"]), "the grid's foot never came into view")
        evidence(app, "nav-grid-audit")
        let before = roomsWindowShot(app)
        let picture = app.screenshot().image.cgImage
        let first = try auditIssues(app, "nav-grid")
        let firstSplit = Self.floorAndRest(first)
        let settled = roomsWindowShot(app)
        let moves = Self.roomsPageMoves(from: before, to: settled)
        let other = firstSplit.rest.filter { !Self.roomsSamplesPixels($0) }
        var pixelLines = firstSplit.rest.filter(Self.roomsSamplesPixels)
        var judgedOn = before
        if moves.isEmpty {
            print("NAV GRID AUDIT: the audit left the page where it was — one pass is the measurement")
        } else {
            print(
                "NAV GRID AUDIT: the audit moved the page — "
                    + moves.prefix(6).map { "'\($0.label)' by \($0.dy)" }.joined(separator: ", ")
                    + " — so its \(pixelLines.count) pixel-sampled finding(s) are read where drawn and retaken"
            )
            roomsAssertOwnInk(
                pixelLines,
                on: before,
                picture: picture,
                window: app.frame,
                name: "nav-grid"
            )
            evidence(app, "nav-grid-audit-settled")
            let retake = try auditIssues(app, "nav-grid-settled")
            let movedAgain = Self.roomsPageMoves(from: settled, to: roomsWindowShot(app))
            XCTAssertTrue(
                movedAgain.isEmpty,
                "the retaken grid audit moved the page as well — "
                    + movedAgain.prefix(6).map { "'\($0.label)' by \($0.dy)" }.joined(separator: ", ")
                    + " — so its contrast findings are not a measurement either"
            )
            let retakeSplit = Self.floorAndRest(retake)
            for line in retakeSplit.rest where !Self.roomsSamplesPixels(line) {
                print("NAV GRID AUDIT retake (judged in the first pass) :: \(line)")
            }
            pixelLines = retakeSplit.rest.filter(Self.roomsSamplesPixels)
            judgedOn = settled
        }
        let clipped = navClippedMeasured(app, pixelLines, on: judgedOn)
        let rest = other + clipped.kept
        print(
            "NAV GRID AUDIT floor=\(firstSplit.floor) rest=\(firstSplit.rest.count) "
                + "moved=\(!moves.isEmpty) clipped-measured=\(clipped.allowed.count) ours=\(rest.count)"
        )
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) attributed finding(s) on the grouped grid: "
                + rest.joined(separator: " | ")
        )
    }

    /// A pixel-sampled finding against an element the page **clips** — every
    /// one of its page twins in `shot` crosses the page's top or its foot —
    /// is measured rather than believed: the element is brought wholly into
    /// the page (``navReach(_:_:inPage:flicks:)``), photographed, and its ink
    /// read with ``roomsLeafInk(_:window:frame:)``. Under 4.5:1 fails, with
    /// the reading. At or over it, the line is allowed and printed with the
    /// reading. Any other line — no label, no twin, a twin wholly inside or
    /// wholly outside the page — is kept, so it fails its case.
    ///
    /// The page's edges are the measurement's, not the element's: half a
    /// control against whatever is past the clip is not a reading of the
    /// control (D57, `testRooms_Audit`). Home is a root, so there is no
    /// navigation bar over its top edge for ``roomsContrastWithNoPixels``
    /// to reason about; this asks the page frame alone.
    @MainActor
    func navClippedMeasured(
        _ app: XCUIApplication,
        _ lines: [String],
        on shot: RoomsWindowShot
    ) -> (allowed: [String], kept: [String]) {
        let page = shot.page
        var allowed: [String] = []
        var kept: [String] = []
        for line in lines {
            guard !page.isEmpty, let label = Self.roomsLabel(in: line) else {
                kept.append(line)
                continue
            }
            let twins = shot.pageElements.filter { $0.label == label }
            let clippedTwins = twins.filter {
                $0.frame.intersects(page)
                    && ($0.frame.minY < page.minY - 0.5 || $0.frame.maxY > page.maxY + 0.5)
            }
            guard !twins.isEmpty, clippedTwins.count == twins.count else {
                kept.append(line)
                continue
            }
            let element = app.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", label))
                .firstMatch
            guard navReach(app, element) else {
                print("NAV GRID AUDIT clipped '\(label)' could not be brought whole — kept :: \(line)")
                kept.append(line)
                continue
            }
            let frame = element.frame
            evidence(app, "nav-grid-clipped-\(label)")
            guard let picture = app.screenshot().image.cgImage,
                  let reading = Self.roomsLeafInk(picture, window: app.frame, frame: frame)
            else {
                print("NAV GRID AUDIT clipped '\(label)' at \(frame) has no ink to read — kept :: \(line)")
                kept.append(line)
                continue
            }
            let said = "'\(label)' clipped at " + clippedTwins.map { "\($0.frame)" }.joined(separator: ", ")
                + " by the page \(page); drawn whole at \(frame) it reads \(reading.ink) on \(reading.paper) "
                + String(format: "%.2f", reading.ratio) + ":1"
            XCTAssertGreaterThanOrEqual(
                reading.ratio,
                Self.roomsContrastFloor,
                "nav-grid: \(said) — under 4.5:1 where it is drawn whole, so the colour is the defect"
            )
            print("NAV GRID AUDIT clipped, measured :: \(said) :: \(line)")
            allowed.append(line)
        }
        return (allowed, kept)
    }

    // MARK: - Settings → Household

    /// The index's first group is Household; it holds the four sections, in
    /// order; App layout is in System.
    @MainActor
    func testNav_SettingsHouseholdHoldsItsFourSections() throws {
        let app = launch(push: "Settings", pushDelay: 2)
        // The index drawn first, so the absence below is measured on it.
        guard app.buttons[NavUnderTest.household].waitForExistence(timeout: 30) else {
            print("=== NO HOUSEHOLD ON THE INDEX — tree follows ===")
            print(app.debugDescription)
            return XCTFail("no Household group on the index")
        }
        XCTAssertFalse(app.buttons["Rooms & devices"].exists, "the index still offers Rooms & devices")
        XCTAssertTrue(navTap(app, NavUnderTest.household), "no Household group on the index")
        XCTAssertTrue(navHouseholdIsUp(app), "tapping Household did not open its four sections")
        evidence(app, "nav-household")
        XCTAssertTrue(navTap(app, NavUnderTest.allSettings), "Household's page has no reachable `all settings`")
        XCTAssertTrue(navTap(app, NavUnderTest.system), "the index has no reachable System group")
        XCTAssertTrue(
            app.buttons[NavUnderTest.appLayout].waitForExistence(timeout: 10),
            "System has no App layout"
        )
    }

    /// Apple's audit on Household's group page, by signature.
    @MainActor
    func testNav_HouseholdAudits() throws {
        let app = launch(push: "Settings", pushDelay: 2)
        XCTAssertTrue(navTap(app, NavUnderTest.household, timeout: 30), "no Household group on the index")
        XCTAssertTrue(navHouseholdIsUp(app), "tapping Household did not open its four sections")
        evidence(app, "nav-household-audit")
        let (floor, rest) = Self.floorAndRest(try auditIssues(app, "nav-household"))
        print("NAV HOUSEHOLD AUDIT floor=\(floor) rest=\(rest.count)")
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) attributed finding(s) on Household: "
                + rest.joined(separator: " | ")
        )
    }

    /// The old group key, as an older link names it, opens Household — bare
    /// and with the group prefix (`-domovoi-audit-settings`, which is
    /// `openSettingsTab`, the call every link makes).
    @MainActor
    func testNav_TheOldRoomsAndDevicesKeyOpensHousehold() throws {
        for key in ["rooms-and-devices", "group:rooms-and-devices"] {
            let app = launch(pushDelay: 2, args: ["-domovoi-audit-settings", key])
            print("NAV OLD KEY \(key)")
            XCTAssertTrue(navHouseholdIsUp(app), "`\(key)` did not open Household")
            evidence(app, "nav-old-key-\(key.replacingOccurrences(of: ":", with: "-"))")
            app.terminate()
        }
    }

    // MARK: - People, through Settings

    /// A People tab on the bar opens **Settings → People** — from Home (a
    /// second Settings pushed over Home) and from inside Settings (the request
    /// taken by the open page). The bar is put back with `default` before the
    /// case ends, and by a teardown block in a fresh launch if the case stopped
    /// before it could, so a failure here cannot leave a People tab on the bar
    /// for the cases after it.
    @MainActor
    func testNav_ASavedPeopleTabOpensSettingsPeople() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        let restored = NavRestored()
        addTeardownBlock { @MainActor [weak self] in
            guard let self, !restored.done else { return }
            self.navRestoreTheBarInAFreshLaunch()
        }
        let app = launch(push: "Settings", pushDelay: 2)
        XCTAssertTrue(
            systemSettingsReach(app, group: .system, section: .appLayout),
            "Settings → System → App layout was not reached"
        )
        guard systemSettingsSettledBar(app) != nil else {
            evidence(app, "nav-people-tab-never-settled")
            return XCTFail("the bar never settled with chat on it")
        }
        XCTAssertTrue(navTap(app, NavUnderTest.addPeople), "App layout offers no People tab")
        let line = systemSettingsBarLine(app)
        XCTAssertTrue(line.hasSuffix("people"), "the bar did not take People: “\(line)”")
        // The bottom bar itself, redrawn by the save (train I015: the line
        // said `… · people` and the bar kept four tabs, because a save
        // invalidated nothing until `ShellModel.phoneBarSaved()`).
        if !app.buttons["People"].waitForExistence(timeout: 10) {
            print("=== THE SAVE DID NOT REACH THE BOTTOM BAR — tree follows ===")
            print(app.debugDescription)
            XCTFail("the line reads “\(line)” but the bottom bar has no People tab")
        }

        func onPeople(_ how: String) -> Bool {
            let counts = app.staticTexts[NavUnderTest.peopleCounts]
            let sub = app.staticTexts[NavUnderTest.peopleSectionSub]
            guard counts.waitForExistence(timeout: 25), sub.exists else {
                print("=== PEOPLE NOT OPENED (\(how)) — tree follows ===")
                print(app.debugDescription)
                return false
            }
            return app.buttons[NavUnderTest.backToHousehold].exists
        }

        // From inside Settings: the index, where no row is called People, so
        // the only `People` button is the bar's tab.
        XCTAssertTrue(navTap(app, NavUnderTest.backToSystem), "App layout's `back to System` foot was not reached")
        XCTAssertTrue(navTap(app, NavUnderTest.allSettings), "System's page has no reachable `all settings`")
        if !app.buttons[NavUnderTest.household].waitForExistence(timeout: 10) {
            print("=== NOT ON THE SETTINGS INDEX — tree follows ===")
            print(app.debugDescription)
            XCTFail("`all settings` did not reach the index")
        }
        XCTAssertTrue(navTap(app, "People", inPage: false), "no People tab on the bar")
        XCTAssertTrue(onPeople("from the Settings index"), "the People tab did not open Settings → People")
        evidence(app, "nav-people-tab-from-settings")

        // From Home: a second Settings pushed over Home (the stack is
        // settings, home, settings), opened on People by `.onAppear`.
        XCTAssertTrue(navTap(app, "Home", inPage: false), "no Home tab on the bar")
        if !houseName(in: app).waitForExistence(timeout: 20) {
            print("=== HOME NOT REACHED — tree follows ===")
            print(app.debugDescription)
            XCTFail("the Home tab did not go home")
        }
        XCTAssertTrue(navTap(app, "People", inPage: false), "no People tab on the bar over Home")
        XCTAssertTrue(onPeople("from Home"), "the People tab did not open Settings → People from Home")
        evidence(app, "nav-people-tab-from-home")

        // Put the bar back.
        XCTAssertTrue(navTap(app, NavUnderTest.backToHousehold), "People's `back to Household` foot was not reached")
        XCTAssertTrue(navTap(app, NavUnderTest.allSettings), "Household's page has no reachable `all settings`")
        XCTAssertTrue(
            systemSettingsReach(app, group: .system, section: .appLayout),
            "App layout was not reached again to put the bar back"
        )
        systemSettingsRestoreTheBar(app)
        restored.done = systemSettingsBarLine(app) == SystemSettingsStringsUnderTest.defaultLine
    }

    /// The teardown half of the case above: a fresh launch into App layout,
    /// and `default`.
    @MainActor
    func navRestoreTheBarInAFreshLaunch() {
        print("NAV TEARDOWN putting the phone bar back in a fresh launch")
        let app = launch(push: "Settings", pushDelay: 2)
        guard systemSettingsReach(app, group: .system, section: .appLayout),
              systemSettingsSettledBar(app) != nil
        else { return print("NAV TEARDOWN could not reach App layout — the bar may still hold People") }
        systemSettingsRestoreTheBar(app)
    }

    /// A route that names People (the seeded push is `navigate(.people)`)
    /// lands on the section, under Household, not on a page of its own.
    @MainActor
    func testNav_PeopleIsASectionOfHousehold() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        let app = launch(push: "People", pushDelay: 2)
        guard app.staticTexts[NavUnderTest.peopleCounts].waitForExistence(timeout: 30) else {
            print("=== PEOPLE DID NOT DRAW — tree follows ===")
            print(app.debugDescription)
            return XCTFail("People never drew")
        }
        XCTAssertTrue(app.staticTexts[NavUnderTest.peopleSectionSub].exists, "not Settings' People section")
        XCTAssertTrue(navTap(app, NavUnderTest.backToHousehold), "the section's `back to Household` foot was not reached")
        XCTAssertTrue(navHouseholdIsUp(app), "`back to Household` did not open Household's four sections")
    }
}

/// Whether the People-tab case put the bar back itself. A class, so the
/// teardown block reads what the case body wrote.
final class NavRestored: @unchecked Sendable {
    var done = false
}
