import XCTest

// ---------------------------------------------------------------------------
// Alerts — the UI cases (overnight 2026-11-02, tag `alerts`).
//
// This item has **no screen**: its surfaces are one row on Home's timers card,
// the card that explains the notification permission, and the notifications
// themselves. So the cases here measure three things — what the row says and
// does, that nothing asks for the permission at launch, and that neither of
// Android's two exact-alarm sentences came across.
//
// ### Five of the seven cannot run yet, and say so rather than passing
//
// The row is built (`ios/Domovoi/Alerts/AlertsHintRowView.swift`) and so is
// the engine that posts (`DomovoiKit/AlertsEngine.swift`), and **nothing
// mounts or starts either**: both hooks are lines in
// `ios/Domovoi/DomovoiApp.swift` and `ios/Domovoi/Home/HomeTimersView.swift`,
// shared files this item may not edit (NIGHT-RULES §2). The three lines are
// written out in `design-notes/ios/overnight/alerts/DECISIONS.md` §2 for
// whoever merges.
//
// Those five cases therefore **skip**, with the hook named in the skip
// message, rather than passing against an app that cannot do the thing. A
// skip is an honest "unmeasured"; a green tick here would be the
// `TokenVault`-shaped lie this track has a memory file about. They are written
// now so the train that follows the hook measures them, instead of a later one
// having to write them from scratch.
//
// ### Run order is load-bearing, and the names carry it
//
// XCTest runs a class's cases in **alphabetical order**, and two pieces of
// state here survive a relaunch:
//
//  * the **notification authorization** is a fact about the install, and iOS
//    grants exactly one system prompt per install. So exactly one case may
//    ask — `testAlerts_TurningOnAlertsExplainsItselfFirst` — and every case
//    that needs the row (which is only drawn while alerts are *off*) is named
//    to sort **before** it. `testAlerts_WhenAlertsAreOnALedgerFireIsDelivered`
//    is named to sort after it, because it is the one case that needs them on.
//  * the **`not now` dismissal** is in `UserDefaults`. Every launch below
//    passes `-domovoi-alerts-forget-hint` (``AlertsHintModel``'s DEBUG-only
//    argument) so that one case's dismissal cannot decide what a later case
//    sees — except the second half of the dismissal case, which relaunches
//    **without** it on purpose, because surviving the relaunch is the thing
//    it measures.
// ---------------------------------------------------------------------------

extension StubHouseUITests {

    // MARK: - what the alerts say

    /// The row's sentence. Spelt out rather than imported, like every other
    /// string this file matches on: a query that silently followed a renamed
    /// constant would stop testing what it claims to. These are
    /// ``AlertsStrings``' words (`HomeSections.kt:400`, `:366`) and the four
    /// iOS-only ones from `IOS-ONLY-STRINGS.md`.
    static let alertsOffRow = "Timer alerts are off on this phone"
    static let alertsTurnOn = "turn on"
    static let alertsNotNow = "not now"
    static let alertsOpenSettings = "open Settings"
    static let alertsAskTitle = "Let Domovoi tell you when a timer goes off"
    static let alertsAskConfirm = "ask iOS"

    /// The two sentences Android has that this port **does not**, and must
    /// never grow (`ExactAlarms.kt:36-99`, `HomeSections.kt:404`). There is no
    /// exact-alarm grant on an iPhone, so the sentence would describe nothing
    /// and the link would open nothing.
    static let alertsLateRingRow = "Timers may ring a few minutes late on this phone"
    static let alertsAllowAlarms = "allow alarms"

    /// The stub house's own seed, by the words that appear in a notification
    /// (`design-notes/ios/stubhouse/fixtures/alerts.py`). Each is in exactly
    /// one field, which is what makes an assertion about one of them a
    /// measurement: `pasta` is the timer's label, `heard in garage` its
    /// summary — a room it was **not** set in — and
    /// `take the tablets at six` the reminder's own words, which appear
    /// nowhere else in the house.
    static let alertsFireTitle = "Timer done · kitchen"
    static let alertsFireLabel = "pasta"
    static let alertsFireSummary = "heard in garage"
    static let alertsReminderWords = "take the tablets at six"

    // MARK: - the launches these cases need

    /// `launch()` plus `-domovoi-alerts-forget-hint`.
    ///
    /// A near-copy of ``launch(mode:paired:server:port:strip:push:pushDelay:)``
    /// and not a call to it, because that method is in `StubHouseUITests.swift`
    /// — a shared file this item may not edit — and it launches the app before
    /// another argument could be appended. The duplication is three lines and
    /// is the smaller cost.
    @MainActor
    func alertsLaunch(
        mode: String = StubHouseUITests.ordinaryTypeSize,
        forgetHint: Bool = true,
        port: Int = 6601
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:\(port)",
            "-domovoi-token", Self.token,
            "-UIPreferredContentSizeCategoryName", mode,
        ]
        if forgetHint { app.launchArguments += ["-domovoi-alerts-forget-hint"] }
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// The row, by its sentence.
    @MainActor
    func alertsHintRow(in app: XCUIApplication) -> XCUIElement {
        app.staticTexts[Self.alertsOffRow]
    }

    /// Stop a case that cannot run until the Home hook lands.
    ///
    /// **Not a fallback and not a tolerance**: it throws `XCTSkip`, which a
    /// train records as unmeasured. The alternative — asserting less so the
    /// case goes green — is the one thing NIGHT-RULES §5 forbids outright.
    ///
    /// The skip cannot tell "the hook is missing" from "the row is broken",
    /// and that is stated rather than hidden: while the hook is absent every
    /// one of these cases is unmeasured, which is what `REPORT.md` says in as
    /// many words. The moment DECISIONS §2 is applied, the skip stops firing
    /// and all five become real measurements with no edit.
    @MainActor
    func requireAlertsRow(_ app: XCUIApplication) throws {
        guard !alertsHintRow(in: app).waitForExistence(timeout: 20) else { return }
        evidence(app, "alerts-row-not-mounted")
        throw XCTSkip(
            "UNMEASURED: no `\(Self.alertsOffRow)` row on Home. The row is built "
                + "(ios/Domovoi/Alerts/AlertsHintRowView.swift) and nothing mounts it: "
                + "HomeTimersView.swift and DomovoiApp.swift are shared files this item "
                + "may not edit. Apply the two hooks in "
                + "design-notes/ios/overnight/alerts/DECISIONS.md §2 and this case measures "
                + "for real."
        )
    }

    @MainActor
    var alertsSpringboard: XCUIApplication {
        XCUIApplication(bundleIdentifier: "com.apple.springboard")
    }

    // MARK: - the two cases that run today

    /// **Neither exact-alarm sentence came across, and no link pretends to
    /// open a grant that does not exist.**
    ///
    /// Android's row has a second state — `Timers may ring a few minutes late
    /// on this phone`, with `allow alarms` opening
    /// `Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM` (`HomeSections.kt:404`).
    /// An iPhone has no counterpart: a scheduled notification is delivered on
    /// time, and that link would open nothing.
    ///
    /// Runs **today**, with or without the Home hook, and keeps running after
    /// it: the port of `ExactAlarms.kt` is the obvious thing for a later batch
    /// to "finish", and this is what says it must not.
    @MainActor
    func testAlerts_NoLateRingRowOrAllowAlarmsLink() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        // The house's own `/api/timers` has three running timers, so the
        // timers card — the card the hint row sits on — is on screen. Waiting
        // for it is what makes the absences below mean something: a screen
        // that never drew has no sentences on it either.
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "the config read never landed, so nothing on this screen is evidence of anything"
        )
        evidence(app, "alerts-no-late-ring-row")
        XCTAssertFalse(
            app.staticTexts[Self.alertsLateRingRow].exists,
            "the exact-alarm sentence is on screen, and an iPhone has no exact-alarm grant"
        )
        XCTAssertFalse(
            app.buttons[Self.alertsAllowAlarms].exists,
            "an `allow alarms` control that opens nothing — NIGHT-RULES §3"
        )
    }

    /// **Nothing asks for notifications at launch.**
    ///
    /// The item's scope forbids it in as many words, and iOS gives one system
    /// prompt per install: an app that spends it on its first frame has spent
    /// it before the person knows what it is for.
    ///
    /// Two assertions, and they are not equally strong:
    ///
    ///  * our **explain card** is not on screen. Always meaningful — the card
    ///    is this app's own view, and only this app can put it there.
    ///  * no **system alert** is on screen. Meaningful only while the status
    ///    is `.notDetermined`, because iOS will not prompt twice. Hence the
    ///    name: this case is ordered before the one case that asks, and the
    ///    log records what the springboard held either way.
    @MainActor
    func testAlerts_NothingAsksForNotificationsAtLaunch() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        // A prompt does not arrive on the first frame: the engine's catch-up
        // is a round trip, and a prompt fired from it would land a second or
        // two in. Long enough that an ask would have shown up, short enough
        // not to pad the suite.
        let springboard = alertsSpringboard
        let appeared = springboard.alerts.firstMatch.waitForExistence(timeout: 8)
        print("alerts: springboard alerts after launch = \(springboard.alerts.count)")
        evidence(app, "alerts-nothing-asks-at-launch")
        XCTAssertFalse(
            app.staticTexts[Self.alertsAskTitle].exists,
            "the explain card put itself on screen at launch; it is only ever reached "
                + "from the hint row's `turn on`"
        )
        XCTAssertFalse(
            appeared,
            "a system alert is on screen at launch — the one thing this item's scope forbids"
        )
    }

    // MARK: - the row (needs DECISIONS §2's Home hook)

    /// **The row is on the timers card, above the timers, and the card still
    /// lists them.**
    ///
    /// `frame.minY` and not the tree's order: the position is Android's
    /// (`HomeSections.kt:396-407`, the row before the rows) and a row drawn
    /// under the timers would read as a footnote about the card rather than a
    /// thing to do about this phone.
    ///
    /// The timer assertion is the other half: a hint that hid the card it is
    /// about would be a worse bug than no hint.
    @MainActor
    func testAlerts_TheTimersCardSaysAlertsAreOffOnThisPhone() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        try requireAlertsRow(app)
        evidence(app, "alerts-row-on-the-timers-card")

        let row = alertsHintRow(in: app)
        // `pasta` is the house's own running timer (`stubhouse.py:177`) as
        // well as the fire ledger's label — here it is the card's row, and its
        // presence is what says the card survived the hint.
        let timer = app.staticTexts[Self.alertsFireLabel]
        XCTAssertTrue(
            scrollUntilBuilt(app, timer),
            "the timers card lost its rows to the hint row"
        )
        XCTAssertLessThan(
            row.frame.minY, timer.frame.minY,
            "the hint is below the timers it is about (`HomeSections.kt:396`)"
        )
        XCTAssertTrue(
            app.buttons[Self.alertsTurnOn].exists,
            "the row says alerts are off and offers no way to turn them on"
        )
        XCTAssertFalse(
            app.buttons[Self.alertsOpenSettings].exists,
            "`open Settings` belongs to `.denied` alone; this install has not been asked"
        )
    }

    /// **`not now` hides the row, keeps the timers, and is still answered
    /// after a relaunch.**
    ///
    /// The relaunch is the assertion. Android keeps the answer in its store
    /// (`AlertStore.kt:227`), and a dismissal held in memory is the
    /// "shipped, but not where it is read" defect this track has a memory
    /// file about — invisible in a single-launch test.
    ///
    /// The second launch deliberately **omits** `-domovoi-alerts-forget-hint`,
    /// which is the only launch in this file that does.
    @MainActor
    func testAlerts_NotNowHidesTheRowAndTheTimerStays() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        try requireAlertsRow(app)

        app.buttons[Self.alertsNotNow].firstMatch.tap()
        evidence(app, "alerts-not-now-tapped")
        XCTAssertFalse(
            alertsHintRow(in: app).waitForExistence(timeout: 5),
            "`not now` left the row on screen"
        )
        XCTAssertTrue(
            scrollUntilBuilt(app, app.staticTexts[Self.alertsFireLabel]),
            "the whole timers card went with the row"
        )

        app.terminate()
        let again = alertsLaunch(forgetHint: false)
        XCTAssertTrue(houseName(in: again).waitForExistence(timeout: 20))
        evidence(again, "alerts-not-now-after-relaunch")
        XCTAssertFalse(
            alertsHintRow(in: again).waitForExistence(timeout: 8),
            "the dismissal did not survive the relaunch — it is kept in memory only"
        )
    }

    /// **The audit of the row, at both type sizes, with the growth
    /// measured.**
    ///
    /// Classified by signature with ``floorAndRest(_:)`` and never by a
    /// pinned total. The measurement is the second half and the more useful
    /// one: a named Dynamic Type finding has twice been a false positive on
    /// this track, so the row's own height at `L` and at AX5 is compared
    /// rather than inferred from a headline.
    ///
    /// What it would catch: a dot left at `frame(width: 8)`, a
    /// `minimumScaleFactor` anywhere, a row that cannot grow, and the warn
    /// dot's contrast — which is why the dot is ``Color/warnInk`` and not
    /// `.orange` (2.20:1 on white).
    @MainActor
    func testAlerts_TheAlertsRowAudits() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        try requireAlertsRow(app)
        let ordinary = alertsHintRow(in: app).frame.height
        let found = try auditIssues(app, "alerts-hint")
        evidence(app, "alerts-hint-audit-large")
        let (floor, rest) = Self.floorAndRest(found)
        print("alerts: audit floor=\(floor) rest=\(rest.count)")
        XCTAssertEqual([], rest, "Apple attributed these to an element on the alerts row")

        app.terminate()
        let big = alertsLaunch(mode: "UICTContentSizeCategoryAccessibilityXXXL")
        XCTAssertTrue(houseName(in: big).waitForExistence(timeout: 20))
        try requireAlertsRow(big)
        let grown = alertsHintRow(in: big).frame.height
        let bigFound = try auditIssues(big, "alerts-hint-ax5")
        evidence(big, "alerts-hint-audit-ax5")
        let (ax5Floor, ax5Rest) = Self.floorAndRest(bigFound)
        print("alerts: ax5 audit floor=\(ax5Floor) rest=\(ax5Rest.count)")
        XCTAssertEqual([], ax5Rest, "Apple attributed these to an element on the alerts row at AX5")
        print("alerts: row height L=\(ordinary) AX5=\(grown)")
        XCTAssertGreaterThan(
            grown, ordinary,
            "the row is the same height at AX5 as at L — the sentence cannot be growing"
        )
    }

    /// **`turn on` explains itself before iOS is asked.**
    ///
    /// One tap puts **our** card on screen — its own sentence, and no system
    /// alert yet. A second tap, on `ask iOS`, is what brings the system
    /// prompt up.
    ///
    /// This is the case for the one thing the item's scope names: *asking for
    /// notification permission must happen at a moment that explains itself.*
    /// What it catches is an ask fired straight from the link with no sentence
    /// in front of it — which is also exactly what Android does, and is
    /// wrong here because iOS allows the prompt once.
    ///
    /// **It is the only case in this file that may ask**, and it answers the
    /// prompt `Allow`, because the case after it needs a phone that can post.
    /// Everything that needs the row is named to sort before it.
    @MainActor
    func testAlerts_TurningOnAlertsExplainsItselfFirst() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        try requireAlertsRow(app)

        app.buttons[Self.alertsTurnOn].firstMatch.tap()
        let card = app.staticTexts[Self.alertsAskTitle]
        XCTAssertTrue(
            card.waitForExistence(timeout: 5),
            "`turn on` did not explain anything"
        )
        evidence(app, "alerts-explain-card")
        let springboard = alertsSpringboard
        XCTAssertEqual(
            0, springboard.alerts.count,
            "the system prompt came up with the sentence, which spends the one ask iOS gives"
        )

        app.buttons[Self.alertsAskConfirm].firstMatch.tap()
        let prompt = springboard.alerts.firstMatch
        let asked = prompt.waitForExistence(timeout: 15)
        print("alerts: system prompt after `ask iOS` = \(asked)")
        if asked {
            evidence(app, "alerts-system-prompt")
            // `Allow` is the button's label on every iOS 18 build this rig
            // has run; the `firstMatch` fallback is there so a reworded
            // prompt leaves the install in a known state rather than
            // stranding every later case behind a modal.
            let allow = prompt.buttons["Allow"]
            (allow.exists ? allow : prompt.buttons.firstMatch).tap()
        }
        XCTAssertTrue(
            asked,
            "`ask iOS` did not reach iOS — the card's button is the only caller of "
                + "requestAuthorization"
        )
        XCTAssertFalse(
            alertsHintRow(in: app).waitForExistence(timeout: 8),
            "alerts are on and the row is still offering to turn them on"
        )
    }

    /// **With alerts on, a fire in the ledger is delivered — titled from the
    /// kind and the room, bodied from the label, and not from the summary.**
    ///
    /// The engine's catch-up reads `GET /api/timers/fires` at start and posts
    /// anything that went off in the last two minutes
    /// (`alertsFirstRunWindowMs`, `AlertStore.kt:133`). So the fixture's
    /// ledger is cut to **one** fire before the launch — the pasta timer —
    /// and the reminder's words must then be nowhere, which is what makes the
    /// body assertion a measurement rather than a coincidence.
    ///
    /// ### It looks in Notification Centre, not for a banner, and that is a
    /// ### platform fact rather than a preference
    ///
    /// iOS does not show a banner for a local notification delivered while
    /// the app is in the **foreground** unless the app's
    /// `UNUserNotificationCenterDelegate` returns `.banner` from
    /// `willPresent`. There is no delegate yet — it is DECISIONS §2's hook 4,
    /// a line in `DomovoiApp.swift` — and `willPresent` is not in that hook
    /// as the mapper wrote it. **That is a finding, not a test problem**, and
    /// it is written into DECISIONS §2 as the fourth hook's second half. Until
    /// then the honest place to look is where iOS really puts it: the
    /// notification list.
    ///
    /// Named to sort **last**: it is the only case that needs a phone that
    /// can post, and the grant comes from the case before it.
    @MainActor
    func testAlerts_WhenAlertsAreOnALedgerFireIsDelivered() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        // One fire, young enough for a first run to post it. Written out here
        // rather than taken from the seed so that the one thing this case
        // asserts the absence of — the reminder's words — is absent because
        // the ledger holds no reminder, which is a fact the case sets up
        // itself.
        control(
            "alerts-fires",
            """
            {"clear":true,"fire":{"id":31,"timer_id":4,"kind":"timer",\
            "is_reminder":false,"label":"pasta","message":null,\
            "room_id":"kitchen","created_at_ago":620,"due_at_ago":15,\
            "fired_at_ago":15,"summary":"heard in garage",\
            "heard_in":["garage"]}}
            """,
            port: 6601,
            what: "one young timer fire in the ledger"
        )
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        // The catch-up is a round trip and the post follows it. Nothing on
        // screen says it happened, so this is a wait rather than a
        // `waitForExistence` on an app element.
        let springboard = alertsSpringboard
        let title = springboard.staticTexts[Self.alertsFireTitle]
        var delivered = title.waitForExistence(timeout: 10)
        if !delivered {
            // Home the app and pull Notification Centre down: a notification
            // delivered while the app was foreground is in the list and on no
            // banner (see this case's doc comment).
            XCUIDevice.shared.press(.home)
            springboard.swipeDown()
            delivered = title.waitForExistence(timeout: 15)
        }
        let shot = XCTAttachment(screenshot: springboard.screenshot())
        shot.name = "alerts-ledger-fire-delivered"
        shot.lifetime = .keepAlways
        add(shot)
        let tree = XCTAttachment(string: springboard.debugDescription)
        tree.name = "alerts-ledger-fire-delivered-tree"
        tree.lifetime = .keepAlways
        add(tree)
        print("=== TREE: alerts-ledger-fire-delivered ===")
        print(springboard.debugDescription)

        guard delivered else {
            throw XCTSkip(
                "UNMEASURED: no notification titled `\(Self.alertsFireTitle)`. Nothing "
                    + "starts AlertsCenter — DomovoiApp.swift is a shared file this item "
                    + "may not edit; apply DECISIONS §2's hooks 1 and 4 (including the "
                    + "`willPresent` half) and this case measures for real."
            )
        }
        XCTAssertTrue(
            springboard.staticTexts
                .containing(NSPredicate(format: "label CONTAINS %@", Self.alertsFireLabel))
                .firstMatch.exists,
            "the notification has the right title and not the timer's label as its body"
        )
        XCTAssertFalse(
            springboard.staticTexts
                .containing(NSPredicate(format: "label CONTAINS %@", Self.alertsReminderWords))
                .firstMatch.exists,
            "a reminder's words are in a notification posted for a timer — the ledger has "
                + "no reminder in it at all for this case"
        )
    }
}
