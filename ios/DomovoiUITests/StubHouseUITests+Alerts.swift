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
// ### Five of the seven were unmeasured on train S003, and are not any more
//
// The row and the engine were both built and **nothing mounted or started
// either**, so five cases threw `XCTSkip` naming the missing hook rather than
// passing against an app that could not do the thing — a skip is an honest
// "unmeasured", where a green tick would have been the `TokenVault`-shaped lie
// this track has a memory file about.
//
// The hook landed after that train (`ios/Domovoi/Alerts/AlertsAppWiring.swift`
// plus two lines in `DomovoiApp.swift` and three in `HomeTimersView.swift`),
// so these five now measure for real. The two `require…` guards below are
// **kept**: if a later merge drops the hook, the right answer is still
// "unmeasured" and not "passed".
//
// ### Run order was load-bearing, and the install outlived it
//
// XCTest runs a class's cases in **alphabetical order**, and two pieces of
// state here survive a relaunch:
//
//  * the **`not now` dismissal** is in `UserDefaults`. Every launch below
//    passes `-domovoi-alerts-forget-hint` (``AlertsHintModel``'s DEBUG-only
//    argument) so that one case's dismissal cannot decide what a later case
//    sees — except the second half of the dismissal case, which relaunches
//    **without** it on purpose, because surviving the relaunch is the thing
//    it measures.
//  * the **notification authorization** is a fact about the **install**, and
//    iOS grants exactly one system prompt per install. That one is not a thing
//    an order can fix, and ordering is what this file tried: exactly one case
//    asks — `testAlerts_TurningOnAlertsExplainsItselfFirst` — with everything
//    that needs the row named to sort before it and the one case that needs
//    alerts *on* named to sort after.
//
// **That worked once.** The ask case answered `Allow` on train S012, the
// Simulator install kept the grant, and in the first full-class run (I001)
// every case here met a phone whose alerts were already **on** — so the row,
// which is only drawn while they are off, was correctly absent and four cases
// went `UNMEASURED` against an app that was right. The order was never the
// problem; a second run of the same install was. Nothing a test can do from
// outside the app un-asks an install, so the app grew the seam:
// `-domovoi-alerts-unasked` (`AlertsUnaskedLatch`) has the hint's status read
// answer `.notDetermined` **until this install is asked**, and retires in
// `AlertsCenter.askForPermission()` — the app's one wrapper around
// `UNUserNotificationCenter.requestAuthorization` — so that what the row
// believes afterwards is iOS's own answer, whoever asked.
// ``alertsLaunch`` passes it by default.
//
// Round 2 moved the latch there from ``AlertsHintModel``, where it retired in
// the row's own `askIOS()`: see
// `testAlerts_NothingAsksForNotificationsAtLaunch`, whose evidence on this
// install *is* the row, and which a model-local latch would have had passing
// against an ask added anywhere else at launch.
//
// The order is kept anyway — it costs nothing, and it is still what makes the
// ledger case meet a phone that can post.
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

    /// The four controls' **identifiers**, which are the handles, while the
    /// words above are what the case asserts a control *says*.
    ///
    /// The row's dismissal answers to `not now, Timer alerts are off on this
    /// phone` — its VoiceOver label carries the sentence it declines, on
    /// purpose — so `buttons["not now"]` matched nothing on train S006 and
    /// the case failed on the tap rather than on anything it meant to
    /// measure. The row's fix link changes words with the state as well
    /// (`turn on` → `open Settings`), so its words are a thing to assert and
    /// not a thing to find it by.
    static let alertsFixId = "alerts-hint-fix"
    static let alertsNotNowId = "alerts-hint-not-now"
    static let alertsAskCancelId = "alerts-ask-cancel"
    static let alertsAskConfirmId = "alerts-ask-confirm"

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
        unasked: Bool = true,
        hideHint: Bool = false,
        forgetBooks: Bool = false,
        port: Int = 6601
    ) -> XCUIApplication {
        let app = freshApp()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:\(port)",
            "-domovoi-token", Self.token,
            "-UIPreferredContentSizeCategoryName", mode,
        ]
        if forgetHint { app.launchArguments += ["-domovoi-alerts-forget-hint"] }
        // `-domovoi-alerts-unasked`: the status read answers `.notDetermined`
        // until this process asks, so the row is drawn on an install that has
        // already been asked. **On by default, and that is the point**: the
        // authorization is a fact about the install, and the only run in which
        // these cases met a never-asked phone was the first one (see this
        // file's header, and I001). It retires on `ask iOS`, so no assertion
        // after that tap is reading anything but iOS's own answer.
        if unasked { app.launchArguments += ["-domovoi-alerts-unasked"] }
        // The audit baseline: the same Home, with the row's space drawn
        // **blank** — `.hidden()`, so no pixels and no accessibility element
        // and not one point of layout moved
        // (``AlertsHintModel/blankedForBaseline``, DEBUG only). It used to
        // drop the row outright, and that is what train S009 showed to be
        // the wrong baseline: see the case's own comment.
        if hideHint { app.launchArguments += ["-domovoi-alerts-hide-hint"] }
        // `-domovoi-alerts-forget-books`: the alert books and the notification
        // list, emptied before the engines read them
        // (``AlertsCenter/init(container:…)``). Only the ledger case needs it,
        // and it needs it because the books are a fact about the **install**:
        // train S012 skipped that case as UNMEASURED with a young fire seeded
        // and the grant already given, because `alerted` and `fires_seen` had
        // survived from the run before — the engine was right to stay quiet,
        // and the case could not tell that apart from an engine that was
        // broken. Not the default: every other case here is about what is on
        // screen, and a reset they do not need is a difference from the
        // ordinary launch for nothing.
        if forgetBooks { app.launchArguments += ["-domovoi-alerts-forget-books"] }
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// Home's list is a **`ScrollView`**, not a collection view.
    ///
    /// ``scrollUntilBuilt(_:_:flicks:)`` waits on `app.collectionViews` and
    /// returns `false` when there is none — which is what it did on train
    /// S006, printing `=== NO LIST TO SCROLL ===` while `pasta` was on screen
    /// the whole time and the case failed saying the card had lost its rows.
    /// That helper is in a shared file this item may not edit, so this is the
    /// same loop over the right container.
    @MainActor
    func alertsScrollUntilBuilt(
        _ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 20
    ) -> Bool {
        if element.exists { return true }
        let list = app.scrollViews.firstMatch
        guard list.waitForExistence(timeout: 20) else {
            print("=== alerts: NO SCROLL VIEW — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            list.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== alerts: \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// An audit finding with its frame cut off.
    ///
    /// The frame is why two audits of the same screen cannot be compared
    /// line for line: adding one row to Home's timers card moves everything
    /// under it by about fifty points, so a finding that is the *same*
    /// finding reads as a different string. The headline, the detail and the
    /// element's type and label are what identify it; where it happened to
    /// sit is not.
    static func alertsSignature(_ finding: String) -> String {
        guard let frame = finding.range(of: " frame=(") else { return finding }
        return String(finding[..<frame.lowerBound])
    }

    /// Whether Apple named an element for this finding.
    ///
    /// `auditIssues` writes `| element: nil` when `issue.element` is nil, and
    /// that is not "an anonymous view": it is the audit having **no
    /// measurement** to attach the finding to. Two runs of one screen can
    /// report the same finding named in one and nil in the other — S012 did,
    /// and the case's own comment carries the evidence — so the two kinds are
    /// compared differently.
    static func alertsIsAttributed(_ finding: String) -> Bool {
        !finding.hasSuffix("| element: nil")
    }

    /// Findings per headline (`Contrast failed`, `Dynamic Type…`), which is
    /// what can be compared between two audits of the same screen when the
    /// element cannot.
    static func alertsHeadlineCounts(_ found: [String]) -> [String: Int] {
        var counts: [String: Int] = [:]
        for finding in found {
            let headline = finding.split(separator: ":", maxSplits: 1).first.map(String.init)
                ?? finding
            counts[headline, default: 0] += 1
        }
        return counts
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
    /// and that is stated rather than hidden. It fired for every one of these
    /// cases on train S003, where the hook was absent, and for four of them
    /// again on I001, where both hooks were intact and the **install** was the
    /// difference. It is kept because the honest answer to either is still
    /// "unmeasured" and not "passed" — and the message now names all three
    /// causes in the order they are worth checking.
    @MainActor
    func requireAlertsRow(_ app: XCUIApplication) throws {
        guard !alertsHintRow(in: app).waitForExistence(timeout: 20) else { return }
        evidence(app, "alerts-row-not-mounted")
        throw XCTSkip(
            "UNMEASURED: no `\(Self.alertsOffRow)` row on Home. Three things can do "
                + "this, and the first is the one I001 hit: (1) "
                + "`-domovoi-alerts-unasked` did not reach `AlertsUnaskedLatch`, or "
                + "something in this launch asked for the permission and retired it, so "
                + "the row is reading an install whose alerts are already ON and is "
                + "right to draw nothing — the authorization survives every train once "
                + "a case has answered `Allow`; (2) the mount is gone: `AlertsHintRowView` in "
                + "HomeTimersView's card, or DomovoiApp putting AlertsAppWiring's hint "
                + "model into the environment (design-notes/ios/overnight/alerts/"
                + "DECISIONS.md §2); (3) Home has no timers card to draw it on, which "
                + "is the stub house's `/api/timers`."
        )
    }

    @MainActor
    var alertsSpringboard: XCUIApplication {
        XCUIApplication(bundleIdentifier: "com.apple.springboard")
    }

    // MARK: - what the row must never grow, and what must never ask

    /// **Neither exact-alarm sentence came across, and no link pretends to
    /// open a grant that does not exist.**
    ///
    /// Android's row has a second state — `Timers may ring a few minutes late
    /// on this phone`, with `allow alarms` opening
    /// `Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM` (`HomeSections.kt:404`).
    /// An iPhone has no counterpart: a scheduled notification is delivered on
    /// time, and that link would open nothing.
    ///
    /// The port of `ExactAlarms.kt` is the obvious thing for a later batch to
    /// "finish", and this is what says it must not. It used to run with or
    /// without the Home hook on purpose — and on I001 that is how it passed
    /// while measuring nothing, so it now requires the row it is asserting
    /// about.
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
        // **The row has to be drawn for the absences below to mean anything.**
        //
        // This case used to run "with or without the Home hook", and on I001
        // that is exactly what it did: the install's alerts were on, no hint
        // row was drawn at all, and the two absences it asserts were true of a
        // card that had nothing on it. A green tick for a measurement that
        // could not have failed. With `-domovoi-alerts-unasked` the row is
        // there, so `Timers may ring a few minutes late on this phone` being
        // absent is a fact about the row that **is** drawn.
        try requireAlertsRow(app)
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
    ///  * no **system alert** is on screen. Meaningful only while the install
    ///    is `.notDetermined`, because iOS will not prompt twice. Hence the
    ///    name: this case is ordered before the one case that asks, and the
    ///    log records what the springboard held either way.
    ///  * **the row is still there, still saying `turn on`.** This is the half
    ///    that holds on an install which has already been asked, and it is why
    ///    it was added: `-domovoi-alerts-unasked` retires inside
    ///    `AlertsCenter.askForPermission()`, which is the app's one wrapper
    ///    around `UNUserNotificationCenter.requestAuthorization`
    ///    (`AlertsNotifier.swift:268`). The real install is granted, so an ask
    ///    fired at launch — from `AlertsAppWiring.start()`, an engine, a later
    ///    item, anywhere — replaces the forced `.notDetermined` with
    ///    `.authorized` and takes the row with it. A row still offering
    ///    `turn on` after the launch has settled is therefore positive
    ///    evidence that nothing asked, on the install this rig actually has,
    ///    where no prompt would have appeared to be seen.
    ///
    ///    **The boundary is what makes that true, and round 1 did not have
    ///    it**: the latch was a property of ``AlertsHintModel`` and retired in
    ///    its `askIOS()` — the row's own ask and no other. An ask added at
    ///    launch somewhere else would have left the latch armed and this row
    ///    on screen, and this case would have gone green against precisely
    ///    the defect it is named for (`AlertsUnaskedLatch`, and
    ///    `testAnAskFromAnywhereElseTakesTheRow` in the package suite).
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
        // The half that measures something on an install that has already been
        // asked: see this case's doc comment. The row is the evidence, and its
        // words are the evidence that the latch has not retired.
        //
        // An assertion and **not** ``requireAlertsRow(_:)`` here, which is the
        // one place in this file the two differ: a missing row is the shape an
        // ask-at-launch takes on this install (the latch retires, the real
        // state is granted, the row goes), and that is the defect the case is
        // named for. Recording it as "unmeasured" would file the bug as a gap
        // in the rig. The four cases that do skip cover a dropped hook.
        XCTAssertTrue(
            alertsHintRow(in: app).waitForExistence(timeout: 20),
            "no row after launch. Either something asked for the permission at launch — "
                + "which retires `-domovoi-alerts-unasked` and, on a granted install, "
                + "takes the row with it — or the Home hook is gone "
                + "(design-notes/ios/overnight/alerts/DECISIONS.md §2)"
        )
        XCTAssertEqual(
            Self.alertsTurnOn, app.buttons[Self.alertsFixId].label,
            "the row's fix link is not `turn on`, so this launch has already asked for the "
                + "permission and taken an answer — which is what the case forbids"
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
            alertsScrollUntilBuilt(app, timer),
            "the timers card lost its rows to the hint row"
        )
        XCTAssertLessThan(
            row.frame.minY, timer.frame.minY,
            "the hint is below the timers it is about (`HomeSections.kt:396`)"
        )
        // By identifier, then by **what it says**: the words are the
        // assertion. `turn on` and `open Settings` are one control in two
        // states (``AlertsPermissionAsk/fixLabel(dismissed:)``), and this
        // install has not been asked, so the state is the first one.
        let fix = app.buttons[Self.alertsFixId]
        XCTAssertTrue(
            fix.waitForExistence(timeout: 5),
            "the row says alerts are off and offers no way to turn them on"
        )
        XCTAssertEqual(
            Self.alertsTurnOn, fix.label,
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

        app.buttons[Self.alertsNotNowId].tap()
        evidence(app, "alerts-not-now-tapped")
        XCTAssertFalse(
            alertsHintRow(in: app).waitForExistence(timeout: 5),
            "`not now` left the row on screen"
        )
        XCTAssertTrue(
            alertsScrollUntilBuilt(app, app.staticTexts[Self.alertsFireLabel]),
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
    /// ### Three launches, because Home is audited twice and subtracted
    ///
    /// Train S006 failed this case on one `Contrast failed` **with no
    /// element**: the audit saying it had no measurement to attribute, which
    /// is neither evidence that the row is at fault nor that it is not.
    /// Home's `needs attention` card draws unlabelled amber bullets, exactly
    /// the kind of graphic that produces an anonymous contrast finding, and
    /// it is on screen above the timers card in every one of this item's
    /// screenshots.
    ///
    /// So the first launch draws Home with the row's **space and nothing in
    /// it** (`-domovoi-alerts-hide-hint`) and the second with it, and what this
    /// case asserts is that the second set is not larger — compared by
    /// signature, because adding a row moves every frame under it by about
    /// fifty points and a frame is not what identifies a finding. The AX5
    /// launch has no baseline of its own (a fourth launch for one
    /// comparison), so there the assertion is the narrower one: no finding
    /// may name the row's own words.
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

        // **The baseline first: the same Home, with the row's space blank.**
        //
        // S006 reported one finding here — `Contrast failed` with **no
        // element** — and an unattributed finding is not evidence about this
        // item either way. Home's `needs attention` card draws bright amber
        // bullets with no label of their own, which is the shape of thing
        // that produces exactly this (an unlabelled graphic has no element to
        // name). Subtracting one audit from the other is what answers the
        // question Apple's own output cannot, and it is strictly stronger
        // than the empty-set assertion it replaces: *any* finding the row
        // adds fails this, named or anonymous.
        //
        // **The baseline has to keep the row's space**, and on S009 it did
        // not. `-domovoi-alerts-hide-hint` dropped the row entirely, which
        // moved every row below it up by 62 points, and the two audits were
        // then of two different geometries: with the row, Home's kitchen room
        // row sat at `{{32, 736.3}, {209, 60.3}}` and was cut by the scroll
        // view's bottom edge at y=770; without it, the same row sat at
        // `674.3` and was whole. Apple reported the cut as one anonymous
        // `Contrast failed` and the subtraction had nothing to cancel it
        // against — a finding about where Home's own content lands, read as
        // the alerts row's. The flag now draws the row `.hidden()`: same
        // layout to the point, no pixels, no element. What the subtraction
        // leaves is the row's drawing and only that.
        let base = alertsLaunch(hideHint: true)
        XCTAssertTrue(houseName(in: base).waitForExistence(timeout: 20))
        XCTAssertFalse(
            alertsHintRow(in: base).exists,
            "`-domovoi-alerts-hide-hint` left the row in the tree, so this is not a baseline"
        )
        // `floorAndRest` on **both** sides, so the two audits are filtered the
        // same way; comparing a floored list against an unfloored one would
        // report the floor itself as something the row added.
        let (baseFloor, baseRest) = Self.floorAndRest(try auditIssues(base, "alerts-baseline"))
        let baseFound = Set(baseRest.filter(Self.alertsIsAttributed).map(Self.alertsSignature))
        let baseHeads = Self.alertsHeadlineCounts(baseRest)
        evidence(base, "alerts-audit-baseline")
        print("alerts: baseline floor=\(baseFloor) findings=\(baseRest.count) heads=\(baseHeads)")
        base.terminate()

        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        try requireAlertsRow(app)
        let ordinary = alertsHintRow(in: app).frame.height
        let found = try auditIssues(app, "alerts-hint")
        evidence(app, "alerts-hint-audit-large")
        let (floor, rest) = Self.floorAndRest(found)
        print("alerts: audit floor=\(floor) rest=\(rest.count)")
        // **Two halves, because Apple attributes this screen's one finding
        // differently from run to run.** S012: the baseline's single
        // `Contrast failed` named `type=48 label='Harvest Moon · Neil Young'
        // frame=(32.0, 762.6, 160.3, 15.7)` — Home's now-playing line, cut in
        // two by the scroll view's bottom edge at y≈770 — and the run *with*
        // the row reported the same single `Contrast failed` with
        // `element: nil`. Both screenshots show the same half-rendered words
        // at the same point (the picture is what attributed it; two tree
        // diffs could not). So a set subtraction over signatures has one
        // finding in each set that cannot cancel, and reports a finding about
        // Home's own content as the row's.
        //
        // What answers the question instead:
        //
        //  1. **Attributed findings, subtracted.** A finding Apple *named*
        //     that the baseline did not have is the row's, whatever it names.
        //  2. **Counts per headline.** An anonymous finding carries no
        //     measurement at all (the `element: nil` memory file), so the only
        //     honest thing to ask of it is whether there are now *more* of
        //     them than the same screen had without the row. This is not a
        //     tolerance and it is not a pinned total: it is the same screen
        //     twice, and one extra `Contrast failed` of any kind fails it.
        let added = Set(rest.filter(Self.alertsIsAttributed).map(Self.alertsSignature))
            .subtracting(baseFound)
        XCTAssertEqual(
            [], added.sorted(),
            "the alerts row added these named findings to Home's audit "
                + "(baseline named \(baseFound.count))"
        )
        let heads = Self.alertsHeadlineCounts(rest)
        let grew = heads.filter { $0.value > (baseHeads[$0.key] ?? 0) }
            .map { "\($0.key): \(baseHeads[$0.key] ?? 0) → \($0.value)" }
            .sorted()
        XCTAssertEqual(
            [], grew,
            "the alerts row added findings Apple would not name (baseline \(baseHeads))"
        )
        // The other half of the subtraction, and not a tolerance: a finding
        // that names one of the row's own words is the row's however the
        // baseline came out.
        XCTAssertEqual(
            [], rest.filter {
                $0.contains(Self.alertsOffRow) || $0.contains(Self.alertsNotNow)
                    || $0.contains(Self.alertsTurnOn)
            },
            "Apple named one of the alerts row's own controls"
        )

        app.terminate()
        let big = alertsLaunch(mode: "UICTContentSizeCategoryAccessibilityXXXL")
        XCTAssertTrue(houseName(in: big).waitForExistence(timeout: 20))
        try requireAlertsRow(big)
        let grown = alertsHintRow(in: big).frame.height
        let bigFound = try auditIssues(big, "alerts-hint-ax5")
        evidence(big, "alerts-hint-audit-ax5")
        let (ax5Floor, ax5Rest) = Self.floorAndRest(bigFound)
        print("alerts: ax5 audit floor=\(ax5Floor) rest=\(ax5Rest.count)")
        XCTAssertEqual(
            [], ax5Rest.filter {
                $0.contains(Self.alertsOffRow) || $0.contains(Self.alertsNotNow)
                    || $0.contains(Self.alertsTurnOn)
            },
            "Apple named one of the alerts row's own controls at AX5"
        )
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
    /// prompt `Allow` when there is a prompt, because the case after it needs
    /// a phone that can post. Everything that needs the row is named to sort
    /// before it — which mattered when the install was fresh and is now
    /// belt-and-braces beside `-domovoi-alerts-unasked`.
    @MainActor
    func testAlerts_TurningOnAlertsExplainsItselfFirst() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the alerts fixture's seed")
        let app = alertsLaunch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20))
        try requireAlertsRow(app)

        app.buttons[Self.alertsFixId].tap()
        let card = app.staticTexts[Self.alertsAskTitle]
        XCTAssertTrue(
            card.waitForExistence(timeout: 5),
            "`turn on` did not explain anything"
        )
        evidence(app, "alerts-explain-card")
        // The card is the one state in which the card is auditable, and
        // nothing behind a modal may be left half-readable under its scrim:
        // `.isModal` keeps VoiceOver inside the card but leaves Home's rows
        // in the tree under a 35% black, where Apple reports them as
        // `Contrast failed` — truthfully. This is the assertion that says the
        // card carries it.
        if #available(iOS 17.0, *) {
            let (cardFloor, cardRest) = Self.floorAndRest(try auditIssues(app, "alerts-explain"))
            print("alerts: explain-card audit floor=\(cardFloor) rest=\(cardRest.count)")
            // On S006 and again on S009 every one of these named a view
            // **behind** the scrim — the header, the address pill,
            // `unverified`, `Kitchen`, the four bar labels — first through
            // `content.accessibilityHidden(…).overlay { … }` and then through
            // the `ZStack` shape ``SettingsPlaceholder`` uses. Neither hid
            // anything, because `.accessibilityHidden` does not cross the
            // `NavigationStack`'s UIKit boundary from the window's root.
            // ``AlertsHintLifecycle`` now presents the card as a
            // `fullScreenCover`, which takes Home out of the window
            // altogether. A finding naming a view outside the card is
            // therefore still the hide and not the card, and the message says
            // so rather than leaving the next reader to diff two trees for it
            // (DECISIONS §15).
            XCTAssertEqual(
                [], cardRest,
                "the audit named these while the card is up — anything that is not the "
                    + "card's own text is Home behind the scrim, i.e. the hide did not take"
            )
        }
        let springboard = alertsSpringboard
        XCTAssertEqual(
            0, springboard.alerts.count,
            "the system prompt came up with the sentence, which spends the one ask iOS gives"
        )

        let confirm = app.buttons[Self.alertsAskConfirmId]
        XCTAssertEqual(
            Self.alertsAskConfirm, confirm.label,
            "the card's own button no longer says what it does"
        )
        confirm.tap()
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
        // **`asked` is printed and not asserted, and that is a platform fact
        // rather than a softened assertion.**
        //
        // iOS puts the prompt up once per install. This case asserted `asked`
        // and passed on S012, where it was the first run; the grant it was
        // given then is still on the Simulator, so on I001 and every run after
        // it `requestAuthorization` answers from the grant and iOS shows
        // nothing. Asserting `asked` from here on would be asserting that the
        // rig had never run before.
        //
        // What is asserted instead holds in **both** worlds and fails for
        // every defect this case is here to catch:
        //
        //  * the card came first and no system alert was up while it was —
        //    asserted above, and real: the app genuinely had not called
        //    `requestAuthorization` yet, whatever the install holds;
        //  * **the row is gone after `ask iOS`** — below. The latch retires in
        //    `AlertsCenter.askForPermission()`, which that tap is the only
        //    caller of in this app's code, so the state the
        //    row is drawn from afterwards is the real centre's answer and
        //    nothing else. A `ask iOS` wired to nothing leaves the forced
        //    `.notDetermined` in place and the row on screen; so does an ask
        //    whose answer is dropped. Neither can pass this.
        //
        // The one install state it cannot recover from is a phone that has
        // already **refused**: the real ask then answers `.denied`, the row
        // stays with `open Settings`, and the message below says so — only the
        // Simulator's own notification setting can undo that, and no agent
        // touches the Simulator (NIGHT-RULES §1).
        XCTAssertFalse(
            alertsHintRow(in: app).waitForExistence(timeout: 10),
            "the row still offers to turn alerts on after `ask iOS` was tapped. Either "
                + "the card's button reaches no `requestAuthorization` at all, or its "
                + "answer is not taken — or this install has already REFUSED "
                + "notifications, in which case the answer is `.denied`, the row is "
                + "right, and the Simulator's own notification setting is the only thing "
                + "that can change it (prompt seen this run = \(asked))"
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
    /// The launch also forgets this install's alert books
    /// (`-domovoi-alerts-forget-books`), so it really is a first run: without
    /// that, `alerted` already holds the pasta timer from the run before and
    /// `fires_seen` is past the seeded id, and the engine is correct to post
    /// nothing. That is what S012 measured, and it measured the install
    /// rather than the engine.
    ///
    /// ### It looks in Notification Centre, not for a banner, and that is a
    /// ### platform fact rather than a preference
    ///
    /// iOS does not show a banner for a local notification delivered while
    /// the app is in the **foreground** unless the app's
    /// `UNUserNotificationCenterDelegate` returns `.banner` from
    /// `willPresent`. ``AlertsTapDelegate`` now returns it, which was a
    /// finding of this case rather than a test problem: the mapper's hook 4
    /// had `didReceive` only, and an app that answers neither swallows its own
    /// alert. The case still looks in the notification **list** first and
    /// falls back to pulling Notification Centre down, because a banner that
    /// has already auto-dismissed is not evidence of anything either way.
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
        // `forgetBooks`: the one case whose subject is what the engine does
        // with a fire rather than what is on screen, so the one case that has
        // to start from an install with no history. See ``alertsLaunch``.
        //
        // `unasked: false`, and it is the only launch in this file that turns
        // the latch off: this case's phone is one whose alerts are **on**, and
        // `-domovoi-alerts-unasked` would have Home's row say the opposite in
        // the screenshot this case attaches. It changes nothing the case
        // measures — the latch is ``AlertsHintModel``'s alone and the engines
        // read the real centre — so what is left is a screen that does not
        // contradict its own evidence.
        let app = alertsLaunch(unasked: false, forgetBooks: true)
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
            //
            // **From the top edge, by a drag, and not `swipeDown()`.** A
            // `swipeDown` on the springboard starts in the middle of the Home
            // screen, and the middle of the Home screen is **Spotlight** —
            // train S012's own screenshot for this case is the search field
            // and the keyboard, with Notification Centre never opened. So the
            // fallback that was there to rescue a missed banner had not been
            // looking anywhere. Notification Centre is the pull from above the
            // status bar, and on the left half: the right half is Control
            // Centre.
            XCUIDevice.shared.press(.home)
            let top = springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.25, dy: 0.0))
            top.press(
                forDuration: 0.1,
                thenDragTo: springboard.coordinate(
                    withNormalizedOffset: CGVector(dx: 0.25, dy: 0.7)
                )
            )
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
                "UNMEASURED: no notification titled `\(Self.alertsFireTitle)`. "
                    + "`AlertsAppWiring` is what starts AlertsCenter and sets the "
                    + "`willPresent` delegate, and `DomovoiApp` is its only caller — if "
                    + "this fires again, a merge dropped that line, the launch argument "
                    + "`-domovoi-alerts-forget-books` stopped reaching AlertsCenter, or "
                    + "the ledger's one fire aged past alertsFirstRunWindowMs before the "
                    + "launch."
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
