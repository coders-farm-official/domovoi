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

    /// Flick slowly until `element` is hittable. A tap on one that exists
    /// and is not hittable throws `Activation point invalid`.
    @MainActor
    func navReach(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 12) -> Bool {
        var done = 0
        while !(element.exists && element.isHittable), done < flicks {
            app.swipeUp(velocity: .slow)
            done += 1
        }
        if !(element.exists && element.isHittable) {
            print("=== \(element) NOT REACHED after \(done) flicks — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// Tap a button by its exact label once it is reachable; print the tree
    /// on a miss.
    @MainActor
    func navTap(_ app: XCUIApplication, _ label: String, timeout: Double = 20) -> Bool {
        let button = app.buttons[label]
        guard button.waitForExistence(timeout: timeout) else {
            print("=== NO `\(label)` BUTTON — tree follows ===")
            print(app.debugDescription)
            return false
        }
        guard navReach(app, button) else { return false }
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
        let podcasts = try XCTUnwrap(top("Podcasts"))
        let audiobooks = try XCTUnwrap(top("Audiobooks"))
        let newsTop = try XCTUnwrap(top("News"))
        let calendar = try XCTUnwrap(top("Calendar"))
        let files = try XCTUnwrap(top("Files"))
        let settings = try XCTUnwrap(top("Settings"))
        let manual = try XCTUnwrap(top("Manual"))
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
        let (floor, rest) = Self.floorAndRest(try auditIssues(app, "nav-grid"))
        print("NAV GRID AUDIT floor=\(floor) rest=\(rest.count)")
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) attributed finding(s) on the grouped grid: "
                + rest.joined(separator: " | ")
        )
    }

    // MARK: - Settings → Household

    /// The index's first group is Household; it holds the four sections, in
    /// order; App layout is in System.
    @MainActor
    func testNav_SettingsHouseholdHoldsItsFourSections() throws {
        let app = launch(push: "Settings", pushDelay: 2)
        XCTAssertFalse(
            app.buttons["Rooms & devices"].waitForExistence(timeout: 5),
            "the index still offers Rooms & devices"
        )
        XCTAssertTrue(navTap(app, NavUnderTest.household, timeout: 30), "no Household group on the index")
        XCTAssertTrue(navHouseholdIsUp(app))
        evidence(app, "nav-household")
        XCTAssertTrue(navTap(app, NavUnderTest.allSettings))
        XCTAssertTrue(navTap(app, NavUnderTest.system))
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
        XCTAssertTrue(navHouseholdIsUp(app))
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
    /// fresh Settings) and from inside Settings (the request taken by the open
    /// page). The bar is put back with `default` before the case ends.
    @MainActor
    func testNav_ASavedPeopleTabOpensSettingsPeople() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the people fixture")
        let app = launch(push: "Settings", pushDelay: 2)
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .appLayout))
        guard systemSettingsSettledBar(app) != nil else {
            evidence(app, "nav-people-tab-never-settled")
            return XCTFail("the bar never settled with chat on it")
        }
        XCTAssertTrue(navTap(app, NavUnderTest.addPeople), "App layout offers no People tab")
        let line = systemSettingsBarLine(app)
        XCTAssertTrue(line.hasSuffix("people"), "the bar did not take People: “\(line)”")

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
        XCTAssertTrue(navTap(app, NavUnderTest.backToSystem))
        XCTAssertTrue(navTap(app, NavUnderTest.allSettings))
        XCTAssertTrue(app.buttons[NavUnderTest.household].waitForExistence(timeout: 10))
        XCTAssertTrue(navTap(app, "People"), "no People tab on the bar")
        XCTAssertTrue(onPeople("from the Settings index"), "the People tab did not open Settings → People")
        evidence(app, "nav-people-tab-from-settings")

        // From Home: a fresh Settings, opened on People.
        XCTAssertTrue(navTap(app, "Home"))
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20), "the Home tab did not go home")
        XCTAssertTrue(navTap(app, "People"))
        XCTAssertTrue(onPeople("from Home"), "the People tab did not open Settings → People from Home")
        evidence(app, "nav-people-tab-from-home")

        // Put the bar back.
        XCTAssertTrue(navTap(app, NavUnderTest.backToHousehold))
        XCTAssertTrue(navTap(app, NavUnderTest.allSettings))
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .appLayout))
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
        XCTAssertTrue(navTap(app, NavUnderTest.backToHousehold), "the section has no foot back to Household")
        XCTAssertTrue(navHouseholdIsUp(app))
    }
}
