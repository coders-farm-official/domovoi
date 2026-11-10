import XCTest

/// A Domovoi nobody has claimed yet: the one screen drawn in place of the
/// workspace (`UnclaimedServerView`, a port of Android's
/// `UnclaimedServerScreen`, 70ce74be + cfe9b0dd).
///
/// Every case is named `testUnclaimed_…` and every piece of evidence
/// `unclaimed-…`.
///
/// ### The house
///
/// The companion stub on **6606**, `--mode unclaimed`: the same house as
/// 6601 in every route but one — the open `GET /api/auth/status` answers
/// `setup_complete: false` there and `true` everywhere else, which is the
/// only thing the app reads to decide. `stub_run.sh` starts it beside
/// 6602-6605 (`design-notes/ios/batch-2026-11-09/unclaimed/stub_run.patch`).
///
/// ### One button is never tapped
///
/// `use a different server` opens the connection sheet, and the sheet starts
/// a LAN discovery sweep across Kamron's real network (RULES "Safety"). It
/// is asserted **present and hittable**, never pressed. `open the dashboard`
/// is not pressed either: it hands the address to Safari, which takes the
/// Simulator out of the app. `use this phone's media` is pressed, because it
/// cannot reach the sheet — it writes the "this phone" choice
/// (`ShellModel.chooseThisPhone()`) into a launch-seeded in-memory store,
/// and the local shell opens nothing by itself.
extension StubHouseUITests {

    /// The unclaimed companion house.
    static let unclaimedPort = 6606

    /// The words, spelled out rather than imported — this file's standing
    /// rule: a query that followed a renamed constant would stop testing what
    /// it claims to. `UnclaimedServer.kt` word for word, and
    /// `UnclaimedServerTests.testTheWordsSayWhatToDoAndWhere` pins the
    /// package's copy to the same letters.
    enum UnclaimedUnderTest {
        /// `UNCLAIMED_TITLE`, `UnclaimedServer.kt:63`.
        static let title = "This Domovoi isn't set up yet"
        /// `UnclaimedServer.kt:112`.
        static let checkAgain = "Check again"
        /// `UnclaimedServer.kt:118`.
        static let openTheDashboard = "open the dashboard"
        /// `UnclaimedServer.kt:120`.
        static let useThisPhonesMedia = "use this phone's media"
        /// `UnclaimedServer.kt:121`. Asserted, never tapped.
        static let useADifferentServer = "use a different server"
        /// `UNCLAIMED_STILL`, `UnclaimedServer.kt:72`.
        static let still = "still not set up — claim it on the dashboard first"
        /// The opening of `unclaimedText`, `UnclaimedServer.kt:67`.
        static let textOpening = "Open its dashboard in a web browser and claim it, then come back."
        /// `ShellStrings.connectionTitle`: the sheet that must never open.
        static let connectionSheet = "connection"
    }

    // MARK: - getting there

    /// Launch against the unclaimed house and wait for the blocker. Prints
    /// the tree when it does not come, and says whether the shell fell back
    /// to local media instead — a different failure with a different cause.
    @MainActor
    func unclaimedLaunch(mode: String = StubHouseUITests.ordinaryTypeSize) -> (XCUIApplication, Bool) {
        let app = launch(mode: mode, port: Self.unclaimedPort)
        let drawn = app.staticTexts[UnclaimedUnderTest.title].waitForExistence(timeout: 30)
        if !drawn {
            print(
                "UNCLAIMED BLOCKER NOT DRAWN — fell back to local media: \(fellBackToLocalMedia(in: app)); "
                    + "is the 6606 house running --mode unclaimed? Tree follows"
            )
            print(app.debugDescription)
        }
        return (app, drawn)
    }

    /// One element by its exact label, whatever its type. The toast is a
    /// `Text` in an overlay, and a button's label is not a static text.
    @MainActor
    func unclaimedElement(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
    }

    // MARK: - the cases

    /// The blocker draws with its words and its four buttons — and
    /// `use a different server` is offered but never pressed.
    @MainActor
    func testUnclaimed_TheBlockerDrawsWithItsFourButtons() {
        let (app, drawn) = unclaimedLaunch()
        evidence(app, "unclaimed-10-blocker")
        XCTAssertTrue(drawn, "the unclaimed house never got its blocker")

        let opening = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", UnclaimedUnderTest.textOpening))
            .firstMatch
        XCTAssertTrue(opening.exists, "the explanation is drawn")
        XCTAssertTrue(
            app.staticTexts["127.0.0.1:\(Self.unclaimedPort)"].exists,
            "the address to open is drawn"
        )

        // `Check again` is waited for, not read once: while the launch's own
        // read is out the button is labelled `checking…`, so an instant
        // `exists` can miss it on a slow first frame.
        var missing: [String] = []
        if !app.buttons[UnclaimedUnderTest.checkAgain].waitForExistence(timeout: 10) {
            missing.append(UnclaimedUnderTest.checkAgain)
        }
        for label in [
            UnclaimedUnderTest.openTheDashboard,
            UnclaimedUnderTest.useThisPhonesMedia,
            UnclaimedUnderTest.useADifferentServer,
        ] where !app.buttons[label].exists {
            missing.append(label)
        }
        if !missing.isEmpty {
            print("UNCLAIMED BUTTONS MISSING: \(missing) — tree follows")
            print(app.debugDescription)
        }
        XCTAssertEqual([], missing, "every one of Android's four buttons is offered")

        // Offered, and reachable by a finger — and NOT pressed: it opens the
        // connection sheet, which sweeps the LAN.
        let different = app.buttons[UnclaimedUnderTest.useADifferentServer]
        XCTAssertTrue(different.exists && different.isHittable, "use a different server is offered")
        XCTAssertFalse(
            app.staticTexts[UnclaimedUnderTest.connectionSheet].exists,
            "nothing opened the connection sheet"
        )
        // The blocker is in place of the workspace: no Home behind it.
        XCTAssertFalse(houseName(in: app).exists, "the workspace is not drawn under the blocker")
    }

    /// `Check again` on a house that is still unclaimed says so — the words
    /// only the read the button started may say (cfe9b0dd).
    ///
    /// **Up to three presses.** The socket coming up starts a read of its
    /// own, and while any read is out the button reads `checking…` and is
    /// off, so a press can land on a disabled button and do nothing. Each
    /// attempt waits for the button to be back first.
    @MainActor
    func testUnclaimed_CheckAgainSaysStillNotSetUp() {
        let (app, drawn) = unclaimedLaunch()
        XCTAssertTrue(drawn, "the unclaimed house never got its blocker")
        let toast = unclaimedElement(app, UnclaimedUnderTest.still)
        XCTAssertFalse(toast.exists, "the launch's own read is quiet: nobody asked")

        var said = false
        for attempt in 1...3 where !said {
            let button = app.buttons[UnclaimedUnderTest.checkAgain]
            guard button.waitForExistence(timeout: 10) else {
                print("CHECK AGAIN NOT FOUND on attempt \(attempt) — tree follows")
                print(app.debugDescription)
                break
            }
            guard button.isEnabled else {
                usleep(500_000)
                continue
            }
            button.tap()
            said = toast.waitForExistence(timeout: 5)
        }
        evidence(app, "unclaimed-20-check-again")
        if !said {
            print("NO 'still not set up' AFTER CHECK AGAIN — tree follows")
            print(app.debugDescription)
        }
        XCTAssertTrue(said, "Check again on a still-unclaimed house says '\(UnclaimedUnderTest.still)'")
        XCTAssertTrue(
            app.staticTexts[UnclaimedUnderTest.title].exists,
            "and the blocker stays: the house is still unclaimed"
        )
    }

    /// The two ways out that leave the house: the dashboard is offered (not
    /// pressed — it leaves the app for Safari), and `use this phone's media`
    /// is pressed, and goes to the phone's own media without the sheet.
    @MainActor
    func testUnclaimed_TheDashboardAndThisPhonesMediaAreOffered() {
        let (app, drawn) = unclaimedLaunch()
        XCTAssertTrue(drawn, "the unclaimed house never got its blocker")

        // The evidence (picture and tree) first: this file's `setUp` stops a
        // case at its first failed assertion, so a miss below would
        // otherwise end the case with nothing printed.
        let dashboard = app.buttons[UnclaimedUnderTest.openTheDashboard]
        evidence(app, "unclaimed-25-ways-out")
        XCTAssertTrue(dashboard.exists && dashboard.isHittable, "open the dashboard is offered")

        let local = app.buttons[UnclaimedUnderTest.useThisPhonesMedia]
        guard local.exists else {
            print("USE THIS PHONE'S MEDIA NOT FOUND — tree follows")
            print(app.debugDescription)
            XCTFail("use this phone's media is not offered")
            return
        }
        local.tap()
        // The pattern `+Manual` uses, which the app check has compiled.
        let left = expectation(
            for: NSPredicate(format: "exists == false"),
            evaluatedWith: app.staticTexts[UnclaimedUnderTest.title]
        )
        let waited = XCTWaiter().wait(for: [left], timeout: 10)
        evidence(app, "unclaimed-30-this-phone")
        XCTAssertEqual(.completed, waited, "the blocker gave way to the phone's own media")
        XCTAssertFalse(
            app.staticTexts[UnclaimedUnderTest.connectionSheet].exists,
            "the local shell opened nothing by itself"
        )
        XCTAssertFalse(fellBackToLocalMedia(in: app), "this is the person's choice, not an outage banner")
        XCTAssertEqual(.runningForeground, app.state)
    }

    /// A claimed house never shows it: 6601's status says `true`.
    @MainActor
    func testUnclaimed_AClaimedHouseNeverShowsTheBlocker() {
        let app = launch()
        let named = houseName(in: app).waitForExistence(timeout: 20)
        if !named {
            print("HOME NOT DRAWN ON 6601 — tree follows")
            print(app.debugDescription)
        }
        XCTAssertTrue(named, "Home drew against the claimed house")
        // Long enough for the launch read to have landed several times over:
        // the stub answers on loopback in milliseconds.
        XCTAssertFalse(
            app.staticTexts[UnclaimedUnderTest.title].waitForExistence(timeout: 6),
            "a claimed house is never blocked"
        )
        evidence(app, "unclaimed-40-claimed-house")
        XCTAssertTrue(houseName(in: app).exists, "and Home is still there")
    }

    /// Apple's accessibility audit, on the blocker itself. A root screen, not
    /// a pushed one, so no anonymous floor is expected: zero findings.
    @MainActor
    func testUnclaimed_TheBlockerPassesTheAudit() throws {
        let (app, drawn) = unclaimedLaunch()
        evidence(app, "unclaimed-50-audited")
        XCTAssertTrue(drawn, "the unclaimed house never got its blocker")
        if #available(iOS 17.0, *) {
            let found = try auditIssues(app, "unclaimed")
            XCTAssertTrue(
                found.isEmpty,
                "Apple's audit has \(found.count) against the unclaimed blocker: "
                    + found.joined(separator: " | ")
            )
        }
    }
}
