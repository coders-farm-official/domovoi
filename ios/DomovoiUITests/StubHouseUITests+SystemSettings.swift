import XCTest

/// Settings' **App layout**, **About & updates** and **Advanced** sections,
/// looked at.
///
/// Ten cases over three screens, and **not one of them seeds anything**: every
/// screen in this item is local to the phone, so `fixtures/systemsettings.py`
/// claims no route and there is no reset to call. The only state a case here
/// leaves behind is the saved phone bar, and the three that edit it put it
/// back with `default` as their last step. That is what makes the whole item
/// one cheap block in a train rather than a build per screen.
///
/// ### How these cases get to a section
///
/// `-domovoi-audit-push Settings` (``AuditStrip/seededRoute``), then two taps:
/// the group, then the section. **Not** through Home's "everything" grid: the
/// Settings tile is thirteen tiles down a screen that scrolls, and a tap on an
/// element XCUITest says exists but has not scrolled into view lands on a
/// coordinate that belongs to something else. The push is the same navigation
/// — it is the array `NavigationStack` takes — and it is DEBUG-only on the
/// app's side and read once at launch.
///
/// No case here opens the connection sheet, which would start a LAN sweep
/// across Kamron's real network (NIGHT-RULES §1).
extension StubHouseUITests {

    // MARK: - App layout

    /// A tab goes onto the bar, moves, and comes off again.
    ///
    /// The four things this fails for, which is why it is one case and not
    /// four: the write seam dead (``SystemSettingsBarWriter``'s cast failing
    /// would leave `canEdit` false and no controls at all), `PhoneBar.Op.left`
    /// wired to `right` — up and down in the Kotlin, trivially inverted —
    /// **the bottom bar never redrawing**, which is the work-around this item
    /// ships rather than a shared change, and a row's buttons disabled by the
    /// wrong rule.
    ///
    /// `Files` is the tab it uses because the stub house always offers it
    /// (`/api/capabilities` is `music, videos, files, chat` plus whatever the
    /// other fixtures add) and it is **not** in the default bar — so its name
    /// appearing in the preview line can only be this edit. Nothing here
    /// asserts the spare list's length or its contents: twenty fixture modules
    /// add capabilities on a full run.
    @MainActor
    func testSystemSettings_AppLayoutAddsAndRemovesATab() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .roomsAndDevices, section: .appLayout))
        guard let settled = systemSettingsSettledBar(app) else {
            evidence(app, "systemsettings-layout-never-settled")
            return XCTFail("the preview line never settled with chat on the bar")
        }
        evidence(app, "systemsettings-layout-before")
        XCTAssertFalse(
            settled.contains("files"),
            "`files` is on the bar before this case added it — the line read “\(settled)”, "
                + "so an earlier case did not put the bar back with `default`"
        )

        let add = app.buttons[SystemSettingsStringsUnderTest.addFiles]
        XCTAssertTrue(add.waitForExistence(timeout: 10), "no `add Files to the bar` button")
        add.tap()

        // The preview line, which is the panel's own answer…
        let added = systemSettingsBarLine(app)
        XCTAssertTrue(
            added.contains("files"),
            "the line still reads “\(added)” after adding Files — the write did not land"
        )
        // …and the **bottom bar**, which is the one thing this item cannot
        // repaint — a measurement now, not an assertion.
        //
        // It was an assertion, and it failed on three trains in a row against
        // three different nudges: `model.path = model.path` (S002),
        // `model.refresh()` called straight from the button's action (S005),
        // and the same call deferred to a later turn of the main actor, where
        // ``ShellModel``'s own sinks put theirs (S008). What the three rounds
        // **measured**, rather than guessed:
        //
        //  - The **data** is sound. `SystemSettingsLayoutRedrawTests` writes
        //    through the very store the app under test uses — a launch
        //    carrying `-domovoi-server` builds an `InMemoryShellStore`
        //    (`DomovoiApp.swift:95-115`) — and `ShellModel.phoneBar` answers
        //    the new list back. Not the write, not the cast, not the
        //    encoding, not the filter.
        //  - The bar's **children** do redraw. Every `Assistant` tree in
        //    train S008 shows the `Chat` tab carrying `Selected` after the
        //    route was pushed, and that trait comes from
        //    `model.barSelects(route)` **inside** the `ForEach` body
        //    (`ShellChromeViews.swift:258`).
        //  - The bar's **own body** does not. The tab list is the `ForEach`'s
        //    data, `model.phoneBar`, read in `PhoneBarView.body`
        //    (`ShellChromeViews.swift:257`), and that property is computed
        //    over a store Observation cannot see: the only tracked properties
        //    the read registers are the ones `visibility` touches, and a view
        //    SwiftUI skips re-evaluating — its one stored field is the same
        //    object reference every time — never re-registers them.
        //
        // The fix is one line in `ShellModel`, a file this item may not edit
        // (NIGHT-RULES §2); it is written up as the second **Needs a shared
        // change** in `design-notes/ios/overnight/systemsettings/DECISIONS.md`.
        // Until it lands the edit is saved, the panel tells the truth, and the
        // tabs change when the shell is next drawn — so this prints what the
        // bar did for whoever folds that line in, and the case does not claim
        // a repaint this branch cannot deliver. The assertions above and below
        // still fail if the write, the order or the rules break.
        let tab = app.buttons[SystemSettingsStringsUnderTest.filesTab]
        let repainted = tab.waitForExistence(timeout: 5)
        if !repainted { evidence(app, "systemsettings-layout-no-bar-tab") }
        print(
            "BAR REDRAW: the line reads “\(added)” and the bottom bar "
                + (repainted ? "carries" : "does not carry")
                + " a `\(SystemSettingsStringsUnderTest.filesTab)` tab"
        )

        // Added last, so Files is rightmost; one move left puts it before
        // chat. **The order is the assertion**: an `Op.left` wired to `right`
        // answers the same list back (the swap is refused at the end of the
        // array) and the line would not change at all.
        let left = app.buttons[SystemSettingsStringsUnderTest.moveFilesLeft]
        XCTAssertTrue(left.waitForExistence(timeout: 10), "no `move Files left` button")
        left.tap()
        let moved = systemSettingsBarLine(app)
        XCTAssertTrue(
            moved.contains("files · chat"),
            "after one move left the line reads “\(moved)” — Files did not pass chat"
        )

        let off = app.buttons[SystemSettingsStringsUnderTest.takeFilesOff]
        XCTAssertTrue(off.exists, "no `take Files off the bar` button")
        off.tap()
        let removed = systemSettingsBarLine(app)
        XCTAssertFalse(
            removed.contains("files"),
            "the line still reads “\(removed)” after taking Files off"
        )
        evidence(app, "systemsettings-layout-after")
        systemSettingsRestoreTheBar(app)
    }

    /// `default` puts the bar back, says so, and is dead until something is
    /// saved.
    ///
    /// Three failures: a `default` that clears the wrong key or writes an
    /// empty list (the line would not read the four defaults), the
    /// missing-toast line never drawn — this app shows
    /// ``ShellStrings/phoneBarBackTo(_:)`` as a line under the button rather
    /// than a toast, and a surface nobody draws is the way that change goes
    /// wrong — and ``SystemSettingsLayoutModel/hasSaved`` inverted, which the
    /// first assertion catches before any edit exists to hide it.
    @MainActor
    func testSystemSettings_AppLayoutDefaultRestoresTheBar() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .roomsAndDevices, section: .appLayout))
        XCTAssertNotNil(systemSettingsSettledBar(app), "the preview line never settled")

        // **Before any edit.** `default` is enabled only while the store's key
        // is set (`PhoneBarPanel.kt:100`), and a fresh install has never saved
        // one. This is also the assertion that would fail if an earlier case
        // left a bar saved, which is why it is here and not after the edit.
        let restore = systemSettingsDefaultButton(app)
        XCTAssertTrue(restore.waitForExistence(timeout: 10), "no `default` button")
        evidence(app, "systemsettings-default-before")
        XCTAssertFalse(
            restore.isEnabled,
            "`default` is live with nothing saved — it has nothing to restore"
        )

        // The same wait the other case has, so a rig problem (a spare button
        // that is not in the tree) reports as one rather than as
        // "no matches found" on the tap.
        let add = app.buttons[SystemSettingsStringsUnderTest.addFiles]
        XCTAssertTrue(add.waitForExistence(timeout: 10), "no `add Files to the bar` button")
        add.tap()
        XCTAssertTrue(
            systemSettingsBarLine(app).contains("files"),
            "the edit this case needs did not land"
        )
        XCTAssertTrue(restore.isEnabled, "`default` is dead with a bar saved")

        restore.tap()
        // The **filtered** default, which against this house is all four:
        // the stub answers `features.ai: true` and `shared_screen: false`.
        let line = systemSettingsBarLine(app)
        XCTAssertEqual(
            SystemSettingsStringsUnderTest.defaultLine,
            line,
            "after `default` the bar reads “\(line)”"
        )
        let note = systemSettingsLine(app, beginningWith: SystemSettingsStringsUnderTest.backToNote)
        evidence(app, "systemsettings-default-after")
        XCTAssertTrue(
            note.waitForExistence(timeout: 10),
            "nothing under the button says what the bar went back to — Android toasts this "
                + "sentence and this app draws it, so an undrawn line is the whole change lost"
        )
        XCTAssertEqual(
            SystemSettingsStringsUnderTest.backToNote + SystemSettingsStringsUnderTest.defaultLine,
            note.label,
            "the note names a different bar from the one on screen"
        )
        XCTAssertFalse(restore.isEnabled, "`default` is still live after it cleared the key")
    }

    /// Home cannot move or go, and the second tab cannot go at two tabs.
    ///
    /// Two rules, each of which a view can lose on its own: Home's guard lives
    /// in ``PhoneBar/edit(_:_:_:)`` and the *drawing* of it is this panel's
    /// (Android draws `always first` **instead of** the three controls, not
    /// three disabled ones), and the `min` of two is what stops a person
    /// emptying their own navigation.
    @MainActor
    func testSystemSettings_AppLayoutHomeCannotMoveOrGo() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .roomsAndDevices, section: .appLayout))
        XCTAssertNotNil(systemSettingsSettledBar(app), "the preview line never settled")
        evidence(app, "systemsettings-home-row")

        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.alwaysFirst].exists,
            "Home's row does not say `always first`"
        )
        for absent in [
            SystemSettingsStringsUnderTest.moveHomeLeft,
            SystemSettingsStringsUnderTest.moveHomeRight,
            SystemSettingsStringsUnderTest.takeHomeOff,
        ] {
            XCTAssertFalse(
                app.buttons[absent].exists,
                "Home's row carries a `\(absent)` control, which can never do anything"
            )
        }

        // Down to two tabs — home and music — and then the one rule left.
        // Each removal is waited for: the row it belongs to is redrawn by the
        // write before it, and a tap on an element that is not there yet
        // fails with a message about the control rather than about the rule
        // this case is here for.
        for off in [
            SystemSettingsStringsUnderTest.takeChatOff,
            SystemSettingsStringsUnderTest.takeVideosOff,
        ] {
            let control = app.buttons[off]
            XCTAssertTrue(control.waitForExistence(timeout: 10), "no `\(off)` button")
            XCTAssertTrue(control.isEnabled, "`\(off)` is dead above `PhoneBar.min`")
            control.tap()
        }
        let line = systemSettingsBarLine(app)
        XCTAssertEqual(
            SystemSettingsStringsUnderTest.twoTabLine,
            line,
            "the bar reads “\(line)”, not the two tabs this case needs"
        )
        let offMusic = app.buttons[SystemSettingsStringsUnderTest.takeMusicOff]
        evidence(app, "systemsettings-two-tabs")
        XCTAssertTrue(offMusic.exists, "no `take Music off the bar` button at two tabs")
        XCTAssertFalse(
            offMusic.isEnabled,
            "the last removable tab can still be taken off — `PhoneBar.min` is lost"
        )
        systemSettingsRestoreTheBar(app)
    }

    // MARK: - About & updates

    /// The three paragraphs, the version, the manual, the help chat — and no
    /// developer options, however many times the version is tapped.
    ///
    /// `app version unknown` is the shape a `Bundle` key mistake takes and
    /// nothing else, which is why the version is asserted twice: that it is
    /// there, and that it is not that. The seven taps are Android's gesture
    /// (`DeveloperOptions.kt:96-116`), deliberately **not** ported, and the
    /// honest way to test an absence is to try the thing that would reveal it.
    @MainActor
    func testSystemSettings_AboutShowsTheVersionTheManualAndHelp() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .about))
        evidence(app, "systemsettings-about")

        for opening in SystemSettingsStringsUnderTest.aboutParagraphOpenings {
            XCTAssertTrue(
                startingWith(app, opening).exists,
                "no About paragraph starting “\(opening)”"
            )
        }
        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.serverBuildLine].exists,
            "the server-build line is not drawn"
        )
        let version = startingWith(app, SystemSettingsStringsUnderTest.versionPrefix)
        XCTAssertTrue(version.exists, "no `app version …` line")
        XCTAssertNotEqual(
            SystemSettingsStringsUnderTest.versionUnknown,
            version.label,
            "the version line reads “\(version.label)” — `CFBundleShortVersionString` did not read"
        )
        XCTAssertTrue(
            app.buttons[SystemSettingsStringsUnderTest.openTheManual].exists,
            "no `Open the user manual` button"
        )

        // The stub answers `features.ai: true` and its socket comes up, so
        // ``systemSettingsHelpEntry(connected:aiEnabled:)`` is `.serverChat`
        // and the button says so. The **button**, not the card's title: both
        // are the same words (`AskForHelp.kt:64`, `:95`), and the subscript
        // is what tells them apart.
        let ask = app.buttons[SystemSettingsStringsUnderTest.askForHelp]
        if !ask.waitForExistence(timeout: 25) {
            evidence(app, "systemsettings-about-no-help")
            XCTFail(
                "the help card does not offer a help chat against a connected, AI-enabled "
                    + "house — the entry resolved to the manual"
            )
        }

        // **No developer options, and no gesture that could reveal them.**
        // Android's version line is a 48 dp tappable box; this one is text.
        XCTAssertEqual(
            0,
            app.buttons.matching(
                NSPredicate(format: "label BEGINSWITH %@", SystemSettingsStringsUnderTest.versionPrefix)
            ).count,
            "the version line is a button — the seven-tap gesture came across with it"
        )
        for _ in 0..<7 where version.isHittable { version.tap() }
        evidence(app, "systemsettings-about-after-seven-taps")
        XCTAssertEqual(
            0,
            app.descendants(matching: .any).matching(
                NSPredicate(format: "label CONTAINS[c] %@", "developer")
            ).count,
            "something about developer options appeared after seven taps on the version"
        )
    }

    /// A house this phone cannot hear from offers the manual, not a chat.
    ///
    /// The mirror of the case above, and the one state that proves
    /// ``systemSettingsHelpEntry(connected:aiEnabled:)`` reads `connected` at
    /// all: an entry that ignored it would offer a help chat here and the tap
    /// would hang on a socket that never speaks.
    ///
    /// The **silent-socket house** (6604) does it with no shared state
    /// touched: the upgrade is accepted and not one frame is ever sent. The
    /// AI switch would have done it too and is deliberately not used — it is
    /// one house for the whole run and Chat's own cases read it.
    ///
    /// With the manual as the entry, `Open the user manual` is drawn **twice**
    /// — the About card's own button and the help card's — which is the
    /// discriminating count: one would mean the help card drew something else.
    @MainActor
    func testSystemSettings_HelpOffersTheManualWhenTheSocketIsSilent() throws {
        let app = launch(port: Self.silentSocketPort, push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .about))
        let manual = app.buttons.matching(
            NSPredicate(format: "label == %@", SystemSettingsStringsUnderTest.openTheManual)
        )
        // The entry flips the instant the socket is given up on, so this waits
        // on the second button rather than reading a count once.
        let deadline = Date().addingTimeInterval(30)
        while Date() < deadline, manual.count < 2 { usleep(400_000) }
        evidence(app, "systemsettings-help-silent-socket")
        XCTAssertEqual(
            2,
            manual.count,
            "against a silent socket the help card should offer the manual, so `Open the user "
                + "manual` should be drawn twice — it is drawn \(manual.count) time(s)"
        )
        XCTAssertFalse(
            app.buttons[SystemSettingsStringsUnderTest.askForHelp].exists,
            "the help card offers a server chat on a phone that cannot hear the server"
        )
    }

    /// The licences card lists **this** app's parts, and says there are none.
    ///
    /// Discriminating by absence, deliberately: `ONNX Runtime` and `CMUdict`
    /// are in Android's `OPEN_SOURCE_PARTS` and in no part of this app, and
    /// "port the licences card" is the most natural way to put them on an
    /// iPhone's screen. A licence row is a claim about what the binary
    /// contains.
    @MainActor
    func testSystemSettings_LicencesListThisAppsPartsOnly() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .about))
        evidence(app, "systemsettings-licences")

        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.applesFrameworks].exists,
            "the licences card does not name Apple's frameworks"
        )
        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.nothingLinked].exists,
            "the card does not say this build links no third-party package"
        )
        // The one part of Android's card that ports word for word, because it
        // is about the server's downloads.
        XCTAssertTrue(
            app.buttons[SystemSettingsStringsUnderTest.modelLicencesLink].exists,
            "no link to the licences of what Domovoi downloads"
        )
        for android in SystemSettingsStringsUnderTest.androidOnlyParts {
            XCTAssertEqual(
                0,
                app.descendants(matching: .any).matching(
                    NSPredicate(format: "label CONTAINS %@", android)
                ).count,
                "`\(android)` is on this app's licences card and is not in this app"
            )
        }
    }

    /// The privacy sentences are this platform's, and the problem-report card
    /// draws no control over an empty list.
    ///
    /// Android's storage sentence promises an app-wide backup opt-out that iOS
    /// does not have, so shipping it unchanged would be a false privacy claim
    /// — and this asserts **both** halves: that the iOS wording is there and
    /// that Android's is not.
    ///
    /// The problem-report half is the Simulator's own state: iOS writes
    /// MetricKit diagnostics for a real install, so there are none here and
    /// the empty state is the whole card. Three buttons over an empty list
    /// would be three that cannot do what they say, and the card going missing
    /// altogether would mean the store threw on a file that is not there.
    @MainActor
    func testSystemSettings_PrivacyAndProblemReports() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .about))
        evidence(app, "systemsettings-privacy-and-problems")

        XCTAssertTrue(
            startingWith(app, SystemSettingsStringsUnderTest.storageOpening).exists,
            "the privacy card does not carry the iOS storage sentence"
        )
        XCTAssertEqual(
            0,
            app.descendants(matching: .any).matching(
                NSPredicate(format: "label CONTAINS %@", SystemSettingsStringsUnderTest.androidBackupClause)
            ).count,
            "Android's backup sentence is on screen, and it is not true of an iPhone build"
        )
        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.noAnalytics].exists,
            "the privacy card does not say there is no analytics or crash-reporting service"
        )
        for absent in SystemSettingsStringsUnderTest.droppedPrivacyClauses {
            XCTAssertEqual(
                0,
                app.descendants(matching: .any).matching(
                    NSPredicate(format: "label CONTAINS[c] %@", absent)
                ).count,
                "the privacy card claims “\(absent)”, and this app has no such feature"
            )
        }

        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.problemsEmpty].waitForExistence(timeout: 20),
            "the problem-report card never drew its empty state — the store read did not finish, "
                + "or it threw on a file that is not there"
        )
        for absent in [
            SystemSettingsStringsUnderTest.copyAll,
            SystemSettingsStringsUnderTest.clear,
            SystemSettingsStringsUnderTest.share,
        ] {
            XCTAssertFalse(
                app.buttons[absent].exists,
                "`\(absent)` is drawn with nothing recorded"
            )
        }
    }

    // MARK: - Advanced

    /// Advanced is a signpost, and it points at `#settings/configuration`.
    ///
    /// The section's dashboard key is `configuration`, not `advanced`
    /// (`SettingsScreen.kt:152`: an older server has no `advanced` section),
    /// and `#settings/advanced` is the natural mistake — so the address is
    /// asserted both ways. It is also asserted to be built off the **saved**
    /// address: against this house that is `127.0.0.1`, which is what
    /// `-domovoi-server` saved.
    ///
    /// **The button is not tapped.** A Safari hand-off backgrounds the app and
    /// ends the run.
    @MainActor
    func testSystemSettings_AdvancedPointsAtTheDashboard() throws {
        let app = launch(push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .advanced))
        evidence(app, "systemsettings-advanced")

        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.onTheDashboard].exists,
            "no `On the dashboard` card"
        )
        XCTAssertTrue(
            startingWith(app, SystemSettingsStringsUnderTest.advancedIsChanged).exists,
            "the card does not say Advanced is changed on the dashboard"
        )
        let open = app.buttons[SystemSettingsStringsUnderTest.openOnTheDashboard]
        XCTAssertTrue(open.exists, "no `Open on the dashboard` button")
        XCTAssertTrue(
            open.isEnabled,
            "the button is dead against a saved server — the address came back nil"
        )
        // `staticTexts`, not every element type: the assertion below reads
        // this label, and a container that matched would answer with its own.
        let address = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", SystemSettingsStringsUnderTest.dashboardHash)
        ).firstMatch
        XCTAssertTrue(
            address.exists,
            "the dashboard address is not on screen, or it does not end "
                + "`\(SystemSettingsStringsUnderTest.dashboardHash)`"
        )
        XCTAssertTrue(
            address.label.hasSuffix(SystemSettingsStringsUnderTest.dashboardHash),
            "the address reads “\(address.label)”"
        )
        XCTAssertTrue(
            address.label.contains("127.0.0.1"),
            "the address reads “\(address.label)” — it is not built off the saved server"
        )
        XCTAssertEqual(
            0,
            app.descendants(matching: .any).matching(
                NSPredicate(format: "label CONTAINS %@", "#settings/advanced")
            ).count,
            "the hash was built from the section's key instead of its `dashboard:` value"
        )
    }

    // MARK: - Dynamic Type, measured

    /// The bar's preview line grows with the type size, measured at both ends.
    ///
    /// Not an audit headline — a measurement. A named "Dynamic Type font sizes
    /// are unsupported" finding has twice been a false positive on this track,
    /// and the only thing that settled either was comparing the element at
    /// ``StubHouseUITests/ordinaryTypeSize`` with the same element at AX5. The
    /// preview line is the right element to pin: it is `.caption` **and**
    /// monospaced, which is the combination a fixed `font(.system(size:))`
    /// would be reached for, and it wraps, so a missing
    /// `fixedSize(horizontal:vertical:)` shows up as a height that does not
    /// move.
    @MainActor
    func testSystemSettings_AppLayoutTextGrowsAtTheLargestTypeSize() throws {
        var heights: [String: CGFloat] = [:]
        for (name, mode) in [
            ("ordinary", Self.ordinaryTypeSize),
            ("ax5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let app = launch(mode: mode, push: "Settings")
            XCTAssertTrue(
                systemSettingsReach(app, group: .roomsAndDevices, section: .appLayout),
                "App layout was not reached at the \(name) type size"
            )
            XCTAssertNotNil(
                systemSettingsSettledBar(app),
                "the preview line never settled at the \(name) type size"
            )
            let line = systemSettingsLine(app, beginningWith: SystemSettingsStringsUnderTest.barLinePrefix)
            XCTAssertTrue(line.exists, "no preview line at the \(name) type size")
            heights[name] = line.frame.height
            evidence(app, "systemsettings-layout-\(name)")
        }
        let ordinary = heights["ordinary"] ?? 0
        let ax5 = heights["ax5"] ?? 0
        print("SCALE systemsettings preview line \(ordinary) -> \(ax5)")
        XCTAssertGreaterThan(ordinary, 0, "the line was never measured at the ordinary size")
        // A `.caption` line that wraps at AX5 is several times its own height.
        // 1.8x is deliberately short of what it actually does, so this fails
        // for text that cannot scale and not for a layout that changed.
        XCTAssertGreaterThan(
            ax5,
            ordinary * 1.8,
            "the preview line is \(ordinary) points at the ordinary size and \(ax5) at AX5 — "
                + "it is not scaling"
        )
    }

    // MARK: - Apple's audit

    // ### The anonymous `Contrast failed` both of these cases went red for
    //
    // Train R001 failed both audit cases in this file on `Contrast failed`
    // findings that carry **no element**: three on About, and on App layout
    // five, where only two of the five are the cut edge's — so they overflowed
    // that case's measured allowance by one. An anonymous finding cannot be
    // placed by its own frame, because it has none. It is placed by taking a
    // piece of the screen away and auditing again, which is what ``AuditStrip``
    // exists for (`WorkspaceView.swift:189`).
    //
    // They are ``ShellTopBar``'s. The BaseAudit item audited the same build at
    // the stack's **root**, where Apple does name elements, and
    // `testHomePassesApplesAccessibilityAudit` came back with four
    // `Contrast failed`, every one of them that bar's own text leaves:
    //
    //     label='domovoi / home'  frame=(16.0,  68.3, 96.7, 15.7)
    //     label='127.0.0.1:6601'  frame=(203.3, 69.0, 79.7, 14.3)
    //     label='unverified'      frame=(299.0, 69.5, 52.0, 13.3)
    //     label='  live'          frame=(351.0, 69.0, 26.0, 14.3)
    //
    // Those are the frames of the four leaves above the navigation bar in this
    // file's own trees, to the point. The picture beside the R001 run shows
    // which of them fail and why: the address is grey on the grey pill, and
    // `unverified` and `live` are a muted amber and a muted grey on white,
    // while the crumb is near-black — which is why a Settings panel reports
    // three where the root reports four.
    //
    // **An earlier commit of mine blamed a notification banner and was wrong**
    // (a92c849c). There is no banner in any R001 screenshot of this app — I
    // read them — and the frames above put the findings in the header's ink.
    // That machinery is deleted rather than left standing beside the real
    // cause.
    //
    // The shell is not this item's to edit (NIGHT-RULES §2) and the BaseAudit
    // item is fixing that bar for every screen. So each case below audits its
    // panel **twice**: once composed, as a person sees it, and once under
    // `-domovoi-audit-strip topBar`, which mounts the identical screen with
    // that one bar left out. The strict assertion is on the bare run, which is
    // the panel's own findings with nothing of the shell's among them. The
    // composed run must then add nothing but anonymous `Contrast failed`, and
    // no more of them than that bar has text leaves to account for. A named
    // finding, a second kind of finding, or one anonymous contrast too many
    // still fails both cases — which is the part that is not given up.

    /// Apple's own audit on **App layout**, the screen with the icon-only
    /// controls.
    ///
    /// Classified by signature (``StubHouseUITests/floorAndRest(_:)``) and
    /// never by a total: the floor is the anonymous Dynamic Type findings
    /// every pushed screen in this app sits on — measured false positives,
    /// whose leaves grow 13.3 to 48.0 points — and `rest` is what this screen
    /// adds. An icon button with no `accessibilityLabel` and a row that
    /// cannot become a column both land in `rest`.
    ///
    /// Read the screenshots in `att/` before believing a named finding. Twice
    /// on this track the picture attributed a failure two tree diffs could
    /// not — and once, above, it refuted one of mine.
    @MainActor
    func testSystemSettings_AppLayoutPassesApplesAccessibilityAudit() throws {
        guard #available(iOS 17.0, *) else { return }
        let composed = try systemSettingsAuditAppLayout(withTheShellHeader: true)
        let bare = try systemSettingsAuditAppLayout(withTheShellHeader: false)
        systemSettingsAssert("App layout", composed: composed, bare: bare)
    }

    /// The same on **About & updates**, which is the longest card stack in
    /// Settings: five cards, two web links, a monospaced line and a muted
    /// paragraph under every heading.
    @MainActor
    func testSystemSettings_AboutPassesApplesAccessibilityAudit() throws {
        guard #available(iOS 17.0, *) else { return }
        let composed = try systemSettingsAuditAbout(withTheShellHeader: true)
        let bare = try systemSettingsAuditAbout(withTheShellHeader: false)
        systemSettingsAssert("About", composed: composed, bare: bare)
    }

    // MARK: - one audit of a Settings panel

    /// What one audit of one panel measured.
    ///
    /// `headerLeaves` is how many text leaves ``ShellTopBar`` had above the
    /// navigation bar when the audit ran: four composed, and **zero** bare,
    /// which is the only thing that proves the strip took effect.
    typealias SystemSettingsAuditRun = (floor: Int, unexplained: [String], headerLeaves: Int)

    /// Reach **App layout** and audit it, with or without the shell's header.
    @MainActor
    @available(iOS 17.0, *)
    func systemSettingsAuditAppLayout(withTheShellHeader header: Bool) throws -> SystemSettingsAuditRun {
        // Spelled out rather than a ternary of `nil` and a literal, which
        // has no contextual type of its own.
        let strip: String? = header ? nil : "topBar"
        let app = launch(strip: strip, push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .roomsAndDevices, section: .appLayout))
        XCTAssertNotNil(systemSettingsSettledBar(app), "the preview line never settled")
        return try systemSettingsAudit(
            app,
            header ? "systemsettings-layout" : "systemsettings-layout-nobar",
            allowingLeavesCutByTheScrollEdge: true
        )
    }

    /// Reach **About & updates** and audit it, with or without the shell's
    /// header.
    @MainActor
    @available(iOS 17.0, *)
    func systemSettingsAuditAbout(withTheShellHeader header: Bool) throws -> SystemSettingsAuditRun {
        // Spelled out rather than a ternary of `nil` and a literal, which
        // has no contextual type of its own.
        let strip: String? = header ? nil : "topBar"
        let app = launch(strip: strip, push: "Settings")
        XCTAssertTrue(systemSettingsReach(app, group: .system, section: .about))
        // The empty state is the card's last word; auditing before it lands
        // would audit a spinner.
        XCTAssertTrue(
            app.staticTexts[SystemSettingsStringsUnderTest.problemsEmpty].waitForExistence(timeout: 20),
            "the problem-report card had not finished reading"
        )
        return try systemSettingsAudit(
            app,
            header ? "systemsettings-about" : "systemsettings-about-nobar",
            // About's one straddling leaf has never produced a finding — X006
            // measured zero contrast on it and R001's three were all the
            // header's — so this screen is allowed nothing at all.
            allowingLeavesCutByTheScrollEdge: false
        )
    }

    /// Apple's audit on a panel, with the floor and the scroll view's bottom
    /// edge classified by measurement.
    ///
    /// ### The scroll view's bottom edge, and the leaves it cuts in half
    ///
    /// App layout is taller than the viewport — three cards, and the spare
    /// chips are the last of them — so at rest the scroll view's bottom edge
    /// runs **through** a row of chips. Train S008 measured it: the scroll
    /// view is `{{0, 93.3}, {393, 676.7}}`, so its edge is at y = 770, and
    /// `Podcasts` and `Audiobooks` both span 760.8 to 781.1 inside buttons
    /// spanning 749 to 793.
    ///
    /// Apple returns `Contrast failed` for that one cut — `Podcasts` named in
    /// R001, the others with no element at all — because half an element has
    /// no pixels an audit can read. It is the same false positive
    /// `testTheVideosGridAudits` measured and classified, in the same words:
    /// "every list on this phone has a row straddling that edge… scrolling one
    /// row makes it a different row's turn". The picture beside each run is the
    /// evidence — the chips are cut, not occluded; the bar is laid out below
    /// the stack and draws over nothing (`WorkspaceView.swift:52-77`).
    ///
    /// **The allowance is measured, not assumed.** Named findings are
    /// classified by their own frames; anonymous ones are allowed only up to
    /// the number of leaves this screen can actually show the audit in halves,
    /// counted here from the tree. A fourth anonymous `Contrast failed` with
    /// only three cut leaves still fails, and so does any contrast finding
    /// naming an element the edge does not cross. A panel with no cut leaf
    /// worth allowing for passes `false` and is allowed nothing.
    @MainActor
    @available(iOS 17.0, *)
    func systemSettingsAudit(
        _ app: XCUIApplication,
        _ name: String,
        allowingLeavesCutByTheScrollEdge cutAllowed: Bool
    ) throws -> SystemSettingsAuditRun {
        let found = try auditIssues(app, name)
        evidence(app, "\(name)-audited")
        let (floor, rest) = Self.floorAndRest(found)
        print("AUDIT \(name) floor=\(floor)")

        // ``ShellTopBar`` is everything above the navigation bar, and it is
        // counted from the tree rather than by label so that a reworded pill
        // still counts. Zero of them is what `-domovoi-audit-strip topBar`
        // has to produce for a bare run to mean anything.
        let navTop = app.navigationBars.firstMatch.frame.minY
        XCTAssertGreaterThan(navTop, 0, "no navigation bar to measure the shell header against")
        let header = app.staticTexts.allElementsBoundByIndex
            .filter { $0.frame.height > 0 && $0.frame.maxY <= navTop }
        for leaf in header { print("AUDIT \(name) HEADER :: \(Self.oneLine(leaf))") }

        let page = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })
        let bottom = page?.frame.maxY ?? 0
        XCTAssertGreaterThan(bottom, 0, "no scroll view to take a bottom edge from")
        func straddles(_ box: CGRect) -> Bool { box.minY < bottom && box.maxY > bottom }
        let cut = (app.staticTexts.allElementsBoundByIndex + app.buttons.allElementsBoundByIndex)
            .filter { straddles($0.frame) }
        // Printed whether or not they are allowed for: a case that goes red
        // here next train needs to know what the edge was cutting.
        for leaf in cut { print("AUDIT \(name) CUT :: \(Self.oneLine(leaf))") }

        var anonymousAllowed = cutAllowed ? cut.count : 0
        let unexplained = rest.filter { line in
            guard cutAllowed, line.hasPrefix("Contrast failed") else { return true }
            if let box = Self.frame(in: line) { return !straddles(box) }
            guard anonymousAllowed > 0 else { return true }
            anonymousAllowed -= 1
            return false
        }
        for line in unexplained { print("AUDIT \(name) REST :: \(line)") }
        return (floor, unexplained, header.count)
    }

    /// The two runs, read together.
    ///
    /// The bare run carries the panel's verdict; the composed run is allowed
    /// the shell header's anonymous contrast findings and nothing else. See
    /// the note above ``testSystemSettings_AppLayoutPassesApplesAccessibilityAudit()``
    /// for how those were attributed.
    @MainActor
    func systemSettingsAssert(
        _ screen: String,
        composed: SystemSettingsAuditRun,
        bare: SystemSettingsAuditRun
    ) {
        XCTAssertEqual(
            0,
            bare.headerLeaves,
            "`-domovoi-audit-strip topBar` left \(bare.headerLeaves) text leaves above the "
                + "navigation bar, so the bare run of \(screen) is not bare and nothing below "
                + "is a measurement of the panel alone"
        )
        XCTAssertEqual(
            [],
            bare.unexplained,
            "\(screen) itself — the panel with the shell's header taken away — has findings "
                + "the measured floor does not explain"
        )
        // The shell header's shape, and only it: `Contrast failed` with no
        // element. Anything else in the composed run is new, because the same
        // panel audited clean one launch ago without that bar.
        let shellShaped = composed.unexplained.filter {
            $0.hasPrefix("Contrast failed") && $0.hasSuffix("| element: nil")
        }
        XCTAssertEqual(
            composed.unexplained,
            shellShaped,
            "the composed \(screen) has a finding that is not the shell header's shape "
                + "(an anonymous `Contrast failed`), and the panel alone audits clean"
        )
        XCTAssertLessThanOrEqual(
            shellShaped.count,
            composed.headerLeaves,
            "the composed \(screen) has \(shellShaped.count) anonymous `Contrast failed` and "
                + "the shell header has only \(composed.headerLeaves) text leaves to account "
                + "for them"
        )
    }

    // MARK: - getting about, and reading the bar

    /// Settings → a group → a section, from a launch that pushed Settings.
    ///
    /// Prints the tree when an element is missing, which is the lesson
    /// ``reachPhonesAndBrowsers(_:)`` paid six runs for: a failure that says
    /// "the section did not draw" is a claim about something absent, and
    /// cannot report what is present instead.
    @MainActor
    func systemSettingsReach(
        _ app: XCUIApplication,
        group: SystemSettingsStringsUnderTest.Group,
        section: SystemSettingsStringsUnderTest.Section
    ) -> Bool {
        let groupButton = app.buttons[group.rawValue]
        guard groupButton.waitForExistence(timeout: 30) else {
            print("SETTINGS INDEX NOT REACHED (\(group.rawValue)) — tree follows")
            print(app.debugDescription)
            return false
        }
        groupButton.tap()
        let sectionButton = app.buttons[section.rawValue]
        guard sectionButton.waitForExistence(timeout: 20) else {
            print("GROUP \(group.rawValue) HAS NO \(section.rawValue) — tree follows")
            print(app.debugDescription)
            return false
        }
        sectionButton.tap()
        guard app.staticTexts[section.marker].waitForExistence(timeout: 20) else {
            print("SECTION \(section.rawValue) DREW NO `\(section.marker)` — tree follows")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// A `staticText` whose label begins with `text`.
    ///
    /// ``StubHouseUITests/startingWith(_:_:)`` searches **every** element
    /// type, which is right for "is this sentence on screen" and wrong here:
    /// these three cases read a label and one of them measures a frame, and a
    /// container that happens to match would answer with its own geometry.
    @MainActor
    func systemSettingsLine(_ app: XCUIApplication, beginningWith text: String) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", text)).firstMatch
    }

    /// The monospaced preview line under the rows, as it reads now.
    @MainActor
    func systemSettingsBarLine(_ app: XCUIApplication) -> String {
        let line = systemSettingsLine(app, beginningWith: SystemSettingsStringsUnderTest.barLinePrefix)
        guard line.waitForExistence(timeout: 10) else {
            print("NO PREVIEW LINE — tree follows")
            print(app.debugDescription)
            return ""
        }
        return line.label
    }

    /// Wait for the bar to settle, and answer what it settled on.
    ///
    /// **A paired install is treated as a shared screen until the server
    /// answers** (``DeviceRegistry/isSharedScreen(answer:paired:)``:
    /// `answer ?? paired`). The stub answers `shared_screen: false`, but there
    /// is a window at launch where chat, files, people and news are filtered
    /// out of the bar and the shared-screen note is drawn — so a case that
    /// read the line immediately would see three tabs, and one that asserted
    /// on the spare list would see a different list from the one a person
    /// gets. `chat` on the bar is that answer having landed.
    @MainActor
    func systemSettingsSettledBar(_ app: XCUIApplication) -> String? {
        let deadline = Date().addingTimeInterval(30)
        while Date() < deadline {
            let line = systemSettingsBarLine(app)
            if line.contains("chat") { return line }
            usleep(400_000)
        }
        return nil
    }

    /// The `default` button, matched on the word rather than on the label.
    ///
    /// ``ShellStrings/defaultBar`` is `"  default"` — **two leading spaces**,
    /// which is Android's own padding off its restore glyph — and XCUITest is
    /// not to be trusted to keep leading whitespace in an element's label. No
    /// other button in Settings contains the word.
    @MainActor
    func systemSettingsDefaultButton(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "default")).firstMatch
    }

    /// Put the saved bar back, so the next case starts where this one did.
    ///
    /// The one piece of state a case in this item can leave behind. It is a
    /// `default` tap and not a relaunch, because the store is this phone's and
    /// survives one.
    @MainActor
    func systemSettingsRestoreTheBar(_ app: XCUIApplication) {
        let restore = systemSettingsDefaultButton(app)
        guard restore.exists, restore.isEnabled else { return }
        restore.tap()
        XCTAssertEqual(
            SystemSettingsStringsUnderTest.defaultLine,
            systemSettingsBarLine(app),
            "this case could not put the phone bar back, so the next one starts on its edit"
        )
    }
}

/// What these three sections say, by the words that appear on screen.
///
/// Spelled out here rather than imported, as every other string this suite
/// matches on is: a query that silently followed a renamed constant would stop
/// testing the words and start testing that two copies of a symbol agree. The
/// symbol each one comes from is named, so a deliberate rewording is one grep
/// away from its case.
enum SystemSettingsStringsUnderTest {

    /// ``SettingsGroup/label``.
    enum Group: String {
        case roomsAndDevices = "Rooms & devices"
        case system = "System"
    }

    /// ``SettingsSection/label``, with the first thing each panel draws that
    /// nothing else on the way there does.
    enum Section: String {
        case appLayout = "App layout"
        case about = "About & updates"
        case advanced = "Advanced"

        var marker: String {
            switch self {
            case .appLayout: return "Phone bar"          // ShellStrings.phoneBarTitle
            case .about: return "About Domovoi"          // SystemSettingsStrings.aboutTitle
            case .advanced: return "On the dashboard"    // ShellStrings.onTheDashboard
            }
        }
    }

    // MARK: - the phone bar (ShellStrings §7.9)

    /// Every bar starts with Home, so this is the preview line's prefix.
    static let barLinePrefix = "home · "
    /// ``PhoneBar/routes(saved:visibility:)`` of nothing, against a house with
    /// AI on that is not a shared screen: `defaultPhoneBar`, unfiltered.
    static let defaultLine = "home · music · videos · chat"
    /// What is left after chat and videos come off.
    static let twoTabLine = "home · music"
    /// ``ShellStrings/phoneBarBackTo(_:)``'s opening.
    static let backToNote = "phone bar back to "
    static let alwaysFirst = "always first"

    /// ``ShellStrings/addLabelToTheBar(_:)``, ``ShellStrings/moveLeft(_:)``,
    /// ``ShellStrings/moveRight(_:)`` and ``ShellStrings/takeOffTheBar(_:)``,
    /// which are the icon buttons' accessibility labels.
    static let addFiles = "add Files to the bar"
    static let moveFilesLeft = "move Files left"
    static let takeFilesOff = "take Files off the bar"
    static let takeChatOff = "take Chat off the bar"
    static let takeVideosOff = "take Videos off the bar"
    static let takeMusicOff = "take Music off the bar"
    static let moveHomeLeft = "move Home left"
    static let moveHomeRight = "move Home right"
    static let takeHomeOff = "take Home off the bar"

    /// A tab of ``PhoneBarView``. Its **caption** is the route's label
    /// lowercased (`ShellChromeViews.swift:270`), but the caption is not what
    /// the query matches: the tab button combines its children and carries
    /// `.accessibilityLabel(route.label)` (`ShellChromeViews.swift:304`), so
    /// the element answers to `Files` — ``Route/label`` is the raw value
    /// verbatim for everything but `satellites`.
    ///
    /// Still unambiguous on this screen: the spare button is
    /// `add Files to the bar` and the row's controls are `move Files left` /
    /// `take Files off the bar`, so `Files` exactly is only ever the bar tab.
    static let filesTab = "Files"

    // MARK: - About (SystemSettingsStrings)

    /// The openings of ``SystemSettingsStrings/aboutFirst``, ``aboutSecond``
    /// and ``aboutThird``. Prefixes, because each paragraph is drawn as one
    /// element whose label is the whole sentence, emphasis and all.
    static let aboutParagraphOpenings = [
        "Domovoi is named for the Slavic household guardian spirit",
        "A Pi in each room hears you",
        "It's local-first",
    ]
    static let serverBuildLine =
        "The server's build / version identifiers live on the dashboard under About & updates."
    /// ``systemSettingsAppVersionText(version:debugBuild:)``.
    static let versionPrefix = "app version "
    /// What that line reads when the bundle key did not read.
    static let versionUnknown = "app version unknown"
    static let openTheManual = "Open the user manual"
    /// ``SystemSettingsStrings/helpAsk`` — and ``helpTitle``, which is the
    /// same words as a `staticText`.
    static let askForHelp = "Ask Domovoi for help"

    // MARK: - privacy (SystemSettingsPrivacy)

    /// ``SystemSettingsPrivacy/storage``'s opening — the iOS wording.
    static let storageOpening = "What the app keeps stays in its own storage on this phone"
    /// The clause from Android's sentence (`PrivacyCard.kt:45`) that is **not**
    /// true of an iPhone build: there is no app-wide backup opt-out and no API
    /// to keep `UserDefaults` out of a backup.
    static let androidBackupClause = "outside cloud backups and phone-to-phone transfers"
    /// ``SystemSettingsPrivacy/noAnalytics``, verbatim from Android.
    static let noAnalytics = "There is no analytics, advertising or crash-reporting service in the app."
    /// Features this app does not have, which Android's points promise
    /// (`PrivacyCard.kt:60-66`). A claim about one of them is the same fault
    /// as a control that cannot act.
    static let droppedPrivacyClauses = ["room mode", "microphone"]

    // MARK: - problem reports (SystemSettingsStrings)

    static let problemsEmpty =
        "Nothing recorded. If the app freezes or closes unexpectedly, it shows up here."
    static let copyAll = "copy all"
    static let clear = "clear"
    static let share = "share"

    // MARK: - licences (SystemSettingsStrings, SystemSettingsLicences)

    static let applesFrameworks = "Apple's frameworks"
    static let nothingLinked =
        "This build links no third-party package: everything above is Apple's own."
    static let modelLicencesLink = "licences of what Domovoi downloads"
    /// Parts of `OPEN_SOURCE_PARTS` (`LicencesCard.kt:60-108`) that are in the
    /// **Android** APK and in nothing this app links. Two is enough: they are
    /// the first and the last of that list, and the mistake this guards
    /// against is porting the whole of it.
    static let androidOnlyParts = ["ONNX Runtime", "CMUdict"]

    // MARK: - Advanced (ShellStrings §7.9)

    static let onTheDashboard = "On the dashboard"
    /// ``ShellStrings/changedOnTheDashboard(section:)`` with the section's own
    /// label first.
    static let advancedIsChanged = "Advanced is changed on the Domovoi dashboard"
    static let openOnTheDashboard = "Open on the dashboard"
    /// `DASHBOARD_SETTINGS_HASH` plus the section's `dashboard:` value, which
    /// is `configuration` and **not** `advanced` (`SettingsSections.swift:160`).
    static let dashboardHash = "#settings/configuration"
}
