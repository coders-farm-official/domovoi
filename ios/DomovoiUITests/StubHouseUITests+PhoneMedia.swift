import XCTest

/// "On this phone": the server-or-device switch, the phone's own lists, and
/// the upload.
///
/// Every case is named `testPhoneMedia_…` so a train can hand its result back
/// to this item, and every piece of evidence is named `phonemedia-…`.
///
/// ### Why every case launches with `-domovoi-phone-media`
///
/// PHPicker, the document picker and the Photos save alert are all
/// **out-of-process** UI that XCUITest cannot hold steady, and a Simulator's
/// photo library is whatever the last person left in it. So the phone's own
/// library is a `#if DEBUG` fake behind that argument (``PhoneMediaFake``),
/// and the real pickers are covered by a hand pass on Kamron's phone, which
/// DECISIONS says in as many words.
///
/// Every fake name carries a nonsense word the stub house never uses —
/// `quenlo`, `zarbis`, `grendalf`, `marsden`, `brevik` — so a case asserting
/// "the phone's row is here and the server's is not" cannot pass by matching
/// a word both sides happen to share.
///
/// ### One case `MAP.md` §4.5 planned is deliberately not here
///
/// `AnswerForTheOldServerDoesNotLandUnderTheNew`. The only way to change
/// servers from inside the app is the connection sheet, and NIGHT-RULES §1
/// forbids opening one on a Simulator — it starts a LAN discovery sweep
/// across Kamron's real home network. The property is covered where it can
/// be: `PhoneUploadModelTests` switches the house **inside** the round trip,
/// which is the shape [[read-after-await-wrong-key]] asks for. In its place
/// is `AFailureNeverShowsTheServersFieldList`, which tests the other half of
/// the same reply path and is reachable.
extension StubHouseUITests {

    // MARK: - getting there

    /// Put the house back where every case below expects it.
    ///
    /// **Two controls, not one.** `reset-fixtures` resets the fixture
    /// modules, and `phonemedia.py` is one of them — but the files **block**
    /// is not a fixture: it is `FILES_BLOCKED` inside `stubhouse.py` itself
    /// (`:352`), flipped through its own control, and `reset-fixtures` does
    /// not touch it. Without the second line, every case that ran after
    /// `ABlockedDeviceSeesTheCardAndNoUploadButton` would see a blocked
    /// device and no upload button, and the failure would read as this
    /// item's bug.
    @MainActor
    func phoneReset(port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the phone-media upload seed")
        control("files-blocked", "{\"blocked\":false}", port: port, what: "the files block, off")
    }

    /// How the next upload answers.
    @MainActor
    func phoneUpload(_ body: String, port: Int = 6601) {
        control("phonemedia-upload", body, port: port, what: "how the next upload answers")
    }

    /// Launch with the phone's own library faked.
    ///
    /// ``StubHouseUITests/launch(mode:paired:server:port:strip:push:pushDelay:)``
    /// cannot take an extra argument and is do-not-edit, so this assembles
    /// the same list plus `-domovoi-phone-media`. It stays in step with that
    /// one by holding the same four flags in the same order.
    @MainActor
    func phoneLaunch(
        push route: String?,
        media: String = "seeded",
        mode: String = StubHouseUITests.ordinaryTypeSize,
        server: Bool = true,
        port: Int = 6601
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = server ? ["-domovoi-server", "127.0.0.1:\(port)"] : []
        if server { app.launchArguments += ["-domovoi-token", Self.token] }
        app.launchArguments += ["-domovoi-phone-media", media]
        if let route {
            app.launchArguments += [
                "-domovoi-audit-push", route,
                "-domovoi-audit-push-delay", "\(Self.pushDelay)",
            ]
        }
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", mode]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// Choose one side of the switch, whichever shape the switch is in.
    ///
    /// **Two shapes, one helper.** Below an accessibility type size the
    /// switch is the two-segment pill and the side is a button on screen.
    /// At an accessibility size it is ``PhoneSourceCollapsedSwitch`` — one row
    /// naming the current side, with both sides in its menu — because the
    /// column it replaced cost the page 164 of the window's 852 points
    /// (`PhoneSourceSwitchPill`'s own doc comment has the measurement, and
    /// `integration/sourceswitch-r4.md` the band breakdown). Every case in
    /// this file that switches sides runs at one size or the other, so the
    /// question "which shape is drawn" is asked here and nowhere else.
    ///
    /// ### One waited query over both shapes, and why round 1's poll was the
    /// wrong instrument
    ///
    /// Round 1 polled `.exists` on the two shapes in turn every 200 ms, to
    /// avoid spending a whole `waitForExistence` timeout on the shape that is
    /// **not** drawn. That cost is avoided here too — both shapes carry this
    /// side's own identifier, so **one** predicate query finds either in one
    /// snapshot — and the poll then cost R004 a round for a second reason,
    /// which is the one worth writing down:
    ///
    /// `XCUIElement.exists` **returns `false` when the snapshot itself
    /// failed.** In R004's
    /// `testPhoneMedia_TheDeviceSideHasNoAuditFindings` the run's automation
    /// channel went bad at `t = 9.94s` — every query from there to the end of
    /// the case logs `(retry 1)`, `(retry 2)` and costs 2.1 s, and
    /// `app.debugDescription` came back with no tree at all — and this loop
    /// turned twenty-five seconds of channel failure into `NO SWITCH OF
    /// EITHER SHAPE`: a diagnosis about this unit's control for a failure that
    /// was neither the control's nor the app's. Three other cases in the same
    /// twelve minutes of that run named what had really happened (`Lost
    /// connection to the application`), and the difference between them and
    /// this one is only that they were inside a `waitForExistence`, which
    /// **throws** rather than shrugging. It also polls once a second instead
    /// of five times, so a case that waits through the 12-second seeded push
    /// (``StubHouseUITests/pushDelay``) asks for thirty snapshots of the whole
    /// hierarchy rather than three hundred.
    ///
    /// Both shapes are matched by **identifier**: a segment carries
    /// `PhoneSourceSwitchPill.serverSegmentID`/`deviceSegmentID` and the
    /// collapsed row carries `PhoneSourceSwitchPill.collapsedID`, and
    /// `PhoneSourceCollapsedSwitch.identifier(of:)` puts the same two on the
    /// menu's rows — which cannot be confused with these, since the menu is
    /// shut until the row below is tapped.
    @MainActor
    func phoneSourcePickSide(_ app: XCUIApplication, _ side: PhoneSideUnderTest) -> Bool {
        let either = app.buttons.matching(
            NSPredicate(
                format: "identifier == %@ OR identifier == %@",
                StubPhoneMedia.collapsedSwitch, side.identifier
            )
        ).firstMatch
        guard either.waitForExistence(timeout: 40) else {
            // **`app.state` needs no snapshot**, so it answers even when the
            // channel that would print a tree cannot: `runningForeground`
            // here means the switch really was not drawn and this unit is
            // what the next round should read, and anything else means the
            // run lost the app and it is not.
            print(
                "=== NO SWITCH OF EITHER SHAPE — app is "
                    + "\(Self.stateName(app.state)) — tree follows ==="
            )
            print(app.debugDescription)
            return false
        }
        guard either.identifier == StubPhoneMedia.collapsedSwitch else {
            either.tap()
            return true
        }
        either.tap()
        guard let row = phoneSourceMenuRow(app, side) else {
            print("=== THE COLLAPSED SWITCH'S MENU HAS NO `\(side.words)` — tree follows ===")
            print(app.debugDescription)
            return false
        }
        row.tap()
        return true
    }

    /// One row of the open collapsed menu.
    ///
    /// The identifier first, because that is what
    /// ``PhoneSourceCollapsedSwitch`` puts on each row and it is the handle
    /// that cannot be confused with anything. SwiftUI hands a `Menu`'s buttons
    /// to UIKit as menu elements, and an identifier does not reliably survive
    /// that trip, so the words are the fallback — under two exclusions, both
    /// of which are a real element on this screen and not a precaution:
    ///
    /// * **`.buttons`, never `.any`.** The shell's own server chip is a
    ///   `Button` labelled `switch server` whose *child* `StaticText` is the
    ///   house's address, so an `.any` query for the address would find it —
    ///   and tapping it opens the connection sheet, which NIGHT-RULES §1
    ///   forbids on a Simulator because it sweeps Kamron's real home network.
    ///   Matching a button's own label leaves it out, and the explicit skip
    ///   below is the second lock on the same door.
    /// * **the collapsed row itself.** Its label is
    ///   `PhoneMediaStrings.showingSide(_:)` over the *current* side, so it
    ///   contains the current side's words; asking for the side the page is
    ///   already on would otherwise find the row and tap the menu shut.
    @MainActor
    func phoneSourceMenuRow(_ app: XCUIApplication, _ side: PhoneSideUnderTest) -> XCUIElement? {
        let byID = app.descendants(matching: .any).matching(identifier: side.identifier).firstMatch
        if byID.waitForExistence(timeout: 5) { return byID }
        let byWords = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", side.words))
        for index in 0..<byWords.count {
            let candidate = byWords.element(boundBy: index)
            guard candidate.exists,
                  candidate.identifier != StubPhoneMedia.collapsedSwitch,
                  !(candidate.label.contains(StubPhoneMedia.switchServer))
            else { continue }
            return candidate
        }
        return nil
    }

    /// Tap `your device` and wait for the phone's side to have drawn.
    ///
    /// It waits for a **row**, not for the segment to look selected: the
    /// segment changes the instant it is tapped, and a case that carried on
    /// from there would assert against a side whose first page had not
    /// landed.
    @MainActor
    func phoneSwitchToDevice(_ app: XCUIApplication, waitingFor row: String) -> Bool {
        guard phoneSourcePickSide(app, .device) else { return false }
        let landed = containing(app, row).waitForExistence(timeout: 30)
        if !landed {
            // The app's state beside the complaint, for the same reason
            // ``phoneSourcePickSide(_:_:)`` prints it: a tree that comes back
            // empty says the run lost the app, not that the side drew
            // nothing, and R004 spent a case on that distinction.
            print(
                "=== THE DEVICE SIDE DREW NO `\(row)` — app is "
                    + "\(Self.stateName(app.state)) — tree follows ==="
            )
            print(app.debugDescription)
        }
        return landed
    }

    /// Tap `your device` and wait for the phone page's **header**.
    ///
    /// The looser sibling of ``phoneSwitchToDevice(_:waitingFor:)``, for the
    /// one case that must carry on before a row has been built: at AX5 the
    /// device list's rows are all below the fold and unbuilt, so a case that
    /// scrolls to find them cannot first wait for one. Everything else here
    /// keeps the stricter gate — a case that asserted against a side whose
    /// first page had not landed is exactly what that one was written for.
    ///
    /// The header is not proof the rows arrived, which is why this is not
    /// the default: it is proof the **device page** is what the switch drew.
    @MainActor
    func phoneSwitchToDeviceHeader(_ app: XCUIApplication, waitingFor header: String) -> Bool {
        guard phoneSourcePickSide(app, .device) else { return false }
        let landed = app.staticTexts[header].firstMatch.waitForExistence(timeout: 30)
        if !landed {
            print(
                "=== THE DEVICE SIDE DREW NO `\(header)` — app is "
                    + "\(Self.stateName(app.state)) — tree follows ==="
            )
            print(app.debugDescription)
        }
        return landed
    }

    /// Every **button** whose label is exactly `upload`: the row controls,
    /// and nothing else.
    ///
    /// `.buttons` and not ``containing(_:_:)``'s `.any`, which is this file's
    /// habit everywhere else, for one reason found by reading
    /// ``PhonePickToSendView``: the two picker doors sit on the same page and
    /// their words begin with the same one. They are now `upload a photo` and
    /// `upload a file` (the review's low finding: `open in another app` is
    /// Android's open-with on a file you already have, the opposite
    /// direction), so an **exact** match on `upload` is what separates the
    /// row controls from the doors — a `BEGINSWITH` or an `.any` query would
    /// turn every count below into "two more than it should be".
    @MainActor
    func phoneUploadControls(_ app: XCUIApplication) -> XCUIElementQuery {
        app.buttons.matching(NSPredicate(format: "label == %@", StubPhoneMedia.upload))
    }

    // MARK: - the page under the switch

    /// Scroll the page **under the source switch** until `element` is built,
    /// and say whether it is.
    ///
    /// ### Why the server side's own cases need this (train I001)
    ///
    /// The switch is pinned above the page: ``PhoneSourceSwitchView`` is a
    /// `VStack` of the pill and the two sides, so the side that is shown
    /// scrolls in whatever the pill leaves. At `large` the pill is 45 points
    /// and nothing notices. At AX5 it **was 164**, and the folder's
    /// `ScrollView` on a 393×852 phone was left 178.7 points (the tree beside
    /// `overnight-trains/R001/FilesVideos/testFilesAtTheLargestTypeSize.log`).
    /// A `LazyVStack` builds about a viewport deep, Files' header alone is
    /// 383.7 points, and so the `choose library` button — the second lazy
    /// item — was **unbuilt**, not off screen. Five of the Files and Videos
    /// cases this item wrapped were written before there was a pill and
    /// waited for a marker that can no longer arrive on its own.
    ///
    /// ``PhoneSourceCollapsedSwitch`` has since taken the switch at
    /// accessibility sizes down to one row of about 65 points, which hands
    /// the page roughly 99 of those 164 back. That makes the scroll shorter;
    /// it does not make it unnecessary — Files' AX5 header is still 383.7
    /// points against a viewport under 280, so the picker is still the second
    /// lazy item below the fold. The number is printed on every run that has
    /// to scroll, so the next train reads the page's real height rather than
    /// this comment's arithmetic.
    ///
    /// ### It is ``documentsScrollTo(_:_:flicks:big:)``, deliberately
    ///
    /// Round 1 wrote a second mechanism for this — a press-then-drag between
    /// two corners of the page's measured frame — and no train ever ran it
    /// (R001's five failures all cite `wf/ios-n-all`'s line numbers, so the
    /// Mac's checkout was a commit behind). Meanwhile the Documents item had
    /// already solved the same problem and **passed** at AX5 twice in that
    /// same run: a slow `swipeUp` on the full-width scroll view with the
    /// smallest `minY`, re-resolved every flick. Trains S011 and S013 are
    /// what taught it to check width before position and not to trust
    /// `scrollViews.firstMatch`; its own doc comment has both stories.
    ///
    /// So this is a name, not a mechanism: the Files and Videos cases say
    /// what they mean by it and there is one copy of how it is done. A second
    /// copy is how two cases come to disagree about one screen's scroll.
    ///
    /// `true` the moment `element` exists — so at an ordinary type size,
    /// where the pill costs 45 points and everything near the top is built
    /// already, this does nothing and costs one query. A screen that really
    /// never answered still fails, one flick budget later, with its tree
    /// printed.
    @MainActor
    @discardableResult
    func phoneSourceScrollTo(_ app: XCUIApplication, _ element: XCUIElement) -> Bool {
        if element.exists { return true }
        // **The number this scroll exists because of, in every log.** The
        // window left to the page is what makes the difference between a
        // marker that is off screen and one that was never built, and it was
        // the open finding on this screen: 178.7 of 852 points at AX5,
        // against the 260 dp of content Android's own shell treats as the
        // floor (`AppShell.kt:481`'s `CONTENT_FLOOR`). The band this unit
        // owns has been cut from 164 points to one row
        // (``PhoneSourceCollapsedSwitch``) and
        // ``testPhoneMedia_TheSwitchSegmentsGrowWithTheTypeSize`` now asserts
        // that floor; the two bars still above the floor's other side are the
        // Shell's. Printed here as well, so every train's log carries the
        // measurement next to the scroll it explains.
        // `integration/sourceswitch-r4.md` has the breakdown.
        if let page = documentsFolderScroll(app) {
            print("PAGE UNDER THE SWITCH height=\(page.frame.height) minY=\(page.frame.minY)")
        }
        // `big: true` whatever the type size: it buys the slow swipe and the
        // forty-flick budget, and at `large` the short-circuit above means
        // neither is spent. The flag names the window that has to be crossed,
        // not the launch mode.
        return documentsScrollTo(app, element, big: true)
    }

    /// One flick of that page, for a case that measures as it goes.
    ///
    /// The scroll view is re-resolved on every call — ``documentsScrollTo``'s
    /// own reason: a query held across a swipe goes stale on this screen, and
    /// the frame taken before the first flick is not the frame after it.
    /// `.slow` because a full-velocity flick's momentum carries the page over
    /// rows, and at AX5 a row stepped over is a leaf that was never built and
    /// so never measured.
    ///
    /// Never `app.swipeUp()`, whose gesture starts in the middle of the
    /// window — which at AX5 **is the pill**, so it scrolls nothing at all.
    /// That is half of why these cases found nothing.
    @MainActor
    func phoneSourceFlick(_ app: XCUIApplication) {
        documentsFolderScroll(app)?.swipeUp(velocity: .slow)
    }

    // MARK: - the switch

    /// Every page starts on the server's side (`YourDevice.kt:30`).
    ///
    /// A default of `.device` would hide the house's whole library behind a
    /// phone screen on every fresh launch, which is the thing Android's
    /// comment on that line is about.
    @MainActor
    func testPhoneMedia_TheSwitchIsOnTheServerSideFirst() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music")
        let segment = app.buttons[StubPhoneMedia.yourDevice].firstMatch
        XCTAssertTrue(segment.waitForExistence(timeout: 40), "the switch never drew")
        evidence(app, "phonemedia-switch-default")
        XCTAssertFalse(
            segment.isSelected,
            "`your device` is the chosen side on a fresh launch — every page starts on the server's"
        )
        // And the phone's side is not merely unselected but **not on screen**:
        // the fake's track is the discriminator, and the stub house has no
        // such word anywhere.
        XCTAssertFalse(
            containing(app, StubPhoneMedia.phoneTrackWord).exists,
            "the phone's rows are readable with the server's side chosen"
        )
    }

    /// The side that is shown is the only side that can be read.
    ///
    /// The exact defect `ShownOrParked` exists to prevent
    /// (`YourDevice.kt:181`): both sides stay composed, so without
    /// `accessibilityHidden` and `allowsHitTesting(false)` the hidden one is
    /// still in the tree and still takes taps.
    @MainActor
    func testPhoneMedia_SwitchingToTheDeviceShowsThePhoneAndNotTheServer() throws {
        phoneReset()
        let app = phoneLaunch(push: "Videos")
        // The server's side first, so the absence asserted afterwards is a
        // thing that went away rather than a thing that never drew.
        let serverTile = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", StubPhoneMedia.serverVideo))
            .firstMatch
        XCTAssertTrue(serverTile.waitForExistence(timeout: 40), "the server's videos never drew")

        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneVideo))
        evidence(app, "phonemedia-device-videos")
        XCTAssertFalse(
            serverTile.exists,
            "the server's grid is still in the tree behind the phone's — the parked side is not hidden"
        )
    }

    /// Switching to the phone and back finds the server's page where it was
    /// left — the folder, not the root (`YourDevice.kt:134`).
    @MainActor
    func testPhoneMedia_TheServerSideKeepsItsPlaceAcrossASwitch() throws {
        phoneReset()
        let app = phoneLaunch(push: "Files")
        let folder = app.buttons[StubPhoneMedia.serverFolder].firstMatch
        XCTAssertTrue(folder.waitForExistence(timeout: 40), "the server's Files never drew")
        folder.tap()
        // A file that exists **only inside** that folder, so "we are still in
        // Albums" cannot be satisfied by the root listing.
        let inside = containing(app, StubPhoneMedia.serverFileInsideFolder)
        XCTAssertTrue(inside.waitForExistence(timeout: 30), "the folder never opened")

        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))
        // **The SERVER's segment**, by its identifier. Tapping `your device`
        // a second time is what this case used to do, and `onPick(.device)`
        // is idempotent — so it never left the phone's side, and the
        // assertion below could only pass if the parked server side leaked
        // into the accessibility tree, which is the very defect
        // `ShownOrParked` exists to prevent. The server's segment cannot be
        // found by label: its label is the house's own name, which is why
        // ``PhoneSideUnderTest/identifier`` is what
        // ``phoneSourcePickSide(_:_:)`` matches on.
        XCTAssertTrue(phoneSourcePickSide(app, .server), "the switch has no server side")
        evidence(app, "phonemedia-server-kept-its-place")
        // Back on the server's side, and still in the folder. A container
        // that rebuilt the server side would be at its root, asking the
        // server again.
        XCTAssertTrue(
            inside.waitForExistence(timeout: 20),
            "the server's side went back to its root — the parked composition was thrown away"
        )
    }

    /// The choice is per page, not one shared flag (`YourDevice.kt:27`).
    @MainActor
    func testPhoneMedia_EachPageRemembersItsOwnSide() throws {
        phoneReset()
        let app = phoneLaunch(push: "Videos")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneVideo))

        // The phone bar, whose buttons are labelled with the route's own name
        // (`ShellChromeViews.swift:304`).
        let toMusic = app.buttons[StubPhoneMedia.musicTab].firstMatch
        guard toMusic.waitForExistence(timeout: 20) else {
            print("=== NO PHONE BAR — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the phone bar has no Music tab to navigate by")
        }
        toMusic.tap()

        let segment = app.buttons[StubPhoneMedia.yourDevice].firstMatch
        XCTAssertTrue(segment.waitForExistence(timeout: 30), "Music drew no switch")
        evidence(app, "phonemedia-per-page-side")
        XCTAssertFalse(
            segment.isSelected,
            "Music opened on the phone's side because Videos was set to it — one flag for three pages"
        )

        app.buttons[StubPhoneMedia.videosTab].firstMatch.tap()
        XCTAssertTrue(
            containing(app, StubPhoneMedia.phoneVideo).waitForExistence(timeout: 30),
            "Videos forgot that it was on the phone's side"
        )
    }

    // MARK: - the local shell, where there is no server

    /// The hook into ``LocalShellView`` landed: a phone with no server shows
    /// its own media and not the "not built yet" placeholder.
    @MainActor
    func testPhoneMedia_TheLocalShellShowsThePhoneNotAPlaceholder() throws {
        let app = phoneLaunch(push: nil, server: false)
        let track = containing(app, StubPhoneMedia.phoneTrackWord)
        XCTAssertTrue(track.waitForExistence(timeout: 40), "the local shell drew no phone media")
        evidence(app, "phonemedia-local-shell")
        XCTAssertFalse(
            app.staticTexts[StubPhoneMedia.notBuiltYet].exists,
            "the local shell is still drawing the placeholder this item replaced"
        )
    }

    /// No server, so no upload control anywhere — Android's `upload = null`
    /// (`LocalMediaScreens.kt:118`), and the owner's rule: a button that
    /// would fail on every tap is not drawn.
    @MainActor
    func testPhoneMedia_TheLocalShellOffersNoUploadWithNoServer() throws {
        let app = phoneLaunch(push: nil, server: false)
        XCTAssertTrue(
            containing(app, StubPhoneMedia.phoneTrackWord).waitForExistence(timeout: 40),
            "the local shell drew no phone media, so an absent upload proves nothing"
        )
        evidence(app, "phonemedia-local-no-upload")
        XCTAssertEqual(
            phoneUploadControls(app).count, 0,
            "an uploader reached the local shell, where there is no server to upload to"
        )
    }

    // MARK: - the upload

    /// A track goes through the **Music** door and the toast says so.
    ///
    /// Two things at once, both of which have been got wrong: the wrong door
    /// (`/api/files/upload` into `core:music` asks the core's admin-only
    /// sweep and indexes nothing — `PhoneUpload.kt:36`), and the music
    /// door's `saved` being an **int** where the files door's is a list. A
    /// decoder that took only the list shape would report "nothing was
    /// uploaded" here.
    @MainActor
    func testPhoneMedia_UploadingATrackSaysWhereItWent() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))

        // **Exactly one** upload control on this page, which is itself the
        // assertion: the folder row has none, and the 200 MB row has none
        // because it is over the ceiling.
        XCTAssertEqual(
            phoneUploadControls(app).count, 1,
            "the device side of Music should offer one upload — the one row under the cap"
        )
        phoneUploadControls(app).element(boundBy: 0).tap()

        let toast = app.staticTexts[StubPhoneMedia.uploadedTrack].firstMatch
        let said = toast.waitForExistence(timeout: 40)
        evidence(app, "phonemedia-upload-track")
        XCTAssertTrue(said, "the upload never said where the track went")
    }

    /// A video names the library it landed in — and on this house that is
    /// **Documents**, which no other case produces.
    ///
    /// The stub's `/api/files/libraries` has `core:music` and
    /// `core:documents` and **no** `core:videos` (`stubhouse.py:251`), so
    /// ``phoneDefaultHomeLibrary(_:_:)``'s chain falls past Videos, past
    /// Other, and lands on Documents. A house whose Videos folder is not a
    /// Files library is a real configuration, and a row that uploaded to the
    /// wrong library cannot pass this.
    @MainActor
    func testPhoneMedia_UploadingAVideoNamesTheLibraryItLandedIn() throws {
        phoneReset()
        let app = phoneLaunch(push: "Videos")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneVideo))
        XCTAssertEqual(
            phoneUploadControls(app).count, 1,
            "the folder tile should offer no upload, and the one video should"
        )
        phoneUploadControls(app).element(boundBy: 0).tap()

        let toast = app.staticTexts[StubPhoneMedia.uploadedVideoToDocuments].firstMatch
        let said = toast.waitForExistence(timeout: 40)
        evidence(app, "phonemedia-upload-video")
        XCTAssertTrue(
            said,
            "the video's toast did not name Documents — the default-home-library chain is wrong"
        )
    }

    /// The ring runs while the upload does, and the done toast still arrives.
    ///
    /// The ring is what catches a `Task` left on a lazy row: a row recycled
    /// while it scrolls cancels one, and the upload would stop mid-file with
    /// nothing said.
    @MainActor
    func testPhoneMedia_AProgressRingShowsWhileAnUploadRuns() throws {
        phoneReset()
        phoneUpload("{\"delay_ms\":6000}")
        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))
        phoneUploadControls(app).element(boundBy: 0).tap()

        let busy = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", StubPhoneMedia.uploading))
            .firstMatch
        let ringed = busy.waitForExistence(timeout: 15)
        evidence(app, "phonemedia-upload-ring")
        XCTAssertTrue(ringed, "no row said it was uploading while a six-second upload ran")
        XCTAssertTrue(
            app.staticTexts[StubPhoneMedia.uploadedTrack].firstMatch.waitForExistence(timeout: 60),
            "the upload started and never finished — the task did not outlive the row"
        )
    }

    /// A second tap does not post the file twice — it says so
    /// (`DeviceUpload.kt:257`).
    @MainActor
    func testPhoneMedia_ASecondTapDoesNotStartASecondUpload() throws {
        phoneReset()
        phoneUpload("{\"delay_ms\":6000}")
        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))
        phoneUploadControls(app).element(boundBy: 0).tap()

        // The same control, under its busy name: the button stays tappable
        // while it is busy on purpose, because the tap is how the refusal and
        // this message get said at all.
        let busy = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", StubPhoneMedia.uploading))
            .firstMatch
        XCTAssertTrue(busy.waitForExistence(timeout: 15), "the first upload never showed as busy")
        busy.tap()

        let toast = app.staticTexts[StubPhoneMedia.alreadyUploading].firstMatch
        let said = toast.waitForExistence(timeout: 20)
        evidence(app, "phonemedia-second-tap")
        XCTAssertTrue(said, "a second tap on a running upload posted the file again instead of saying so")
    }

    // MARK: - the refusals

    /// A blocked device gets the card, and the button says why **without a
    /// tap**.
    ///
    /// ### The assertion this case was first written with, and why it was
    /// wrong
    ///
    /// "and no upload button". It is in ``PhoneUploadRowButton`` in as many
    /// words, and it is Android's rule (`DeviceUpload.kt:338`): a refused
    /// button stays drawn and stays tappable, because the tap is the only
    /// place the reason gets said out loud and the only thing that re-reads
    /// the status. Hiding it would leave somebody with a refusal and nowhere
    /// to read it. The owner's no-misleading-controls rule is satisfied the
    /// other way round — the card above the list says uploads are off before
    /// anything is tapped, and the button carries the same sentence as its
    /// accessibility **value**, which is what this case measures. The row
    /// over the ceiling is the one that genuinely has no control
    /// (``testPhoneMedia_AFileOverTheCapHasNoUploadControl``), because
    /// nothing could ever make that tap work.
    @MainActor
    func testPhoneMedia_ABlockedDeviceSeesTheCardAndTheButtonSaysWhy() throws {
        phoneReset()
        control("files-blocked", "{\"blocked\":true}", port: 6601, what: "an admin's files block")
        defer { control("files-blocked", "{\"blocked\":false}", port: 6601, what: "the block, off again") }

        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))

        // The card is a paragraph, so it is matched by a fragment of its
        // tail: a query string over 128 characters throws
        // (NSInternalInconsistencyException, train S001).
        let card = containing(app, StubPhoneMedia.adminLiftsTheBlock)
        let drawn = card.waitForExistence(timeout: 30)
        evidence(app, "phonemedia-blocked")
        XCTAssertTrue(drawn, "a blocked device was not told who lifts the block")

        let button = phoneUploadControls(app).element(boundBy: 0)
        XCTAssertTrue(button.waitForExistence(timeout: 20), "the refused row has no button to read")
        // The server's own sentence, which names **this** device — not a
        // generic "uploads are off". A value that came back empty would mean
        // VoiceOver reads the word `upload` and stops, and somebody would tap
        // it to find out why.
        XCTAssertEqual(
            button.value as? String, StubPhoneMedia.blockedReason,
            "the refused button does not say why it is refused"
        )
    }

    /// A refusal that arrives **after** the page opened is said, and then
    /// shown: the 403 asks the server again, and the card appears
    /// (`DeviceUpload.kt:283`).
    ///
    /// Without that re-read the button would keep saying `upload` for the
    /// rest of the visit.
    @MainActor
    func testPhoneMedia_ARefusalAfterThePageOpenedIsSaidAndShown() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))
        XCTAssertEqual(
            phoneUploadControls(app).count, 1,
            "the page has to open unblocked, or the refusal below is not a change"
        )

        // The door refuses, and the admin's block goes on at the same moment
        // — which is what a real block is: the 403 and the `writable: false`
        // the re-read then sees are one event on the server.
        phoneUpload("{\"fail\":\"403\"}")
        control("files-blocked", "{\"blocked\":true}", port: 6601, what: "a block that lands mid-visit")
        defer { control("files-blocked", "{\"blocked\":false}", port: 6601, what: "the block, off again") }

        phoneUploadControls(app).element(boundBy: 0).tap()
        let toast = app.staticTexts[StubPhoneMedia.uploadRefused].firstMatch
        let said = toast.waitForExistence(timeout: 40)
        evidence(app, "phonemedia-late-refusal")
        XCTAssertTrue(said, "a 403 was not reported in the server's own words")
        XCTAssertTrue(
            containing(app, StubPhoneMedia.adminLiftsTheBlock).waitForExistence(timeout: 30),
            "the 403 did not make the page ask again, so the button is still lying"
        )
    }

    /// A 422 is reported as a failure, in the `upload failed:` vocabulary —
    /// never as the `upload refused:` one, and never as an upload.
    ///
    /// Here in place of the server-switch case NIGHT-RULES §1 forbids (see
    /// this file's note), because it tests the other half of the same reply
    /// path: ``phoneUploadFailureText`` splits on the **status**, and a 403 is
    /// a refusal while everything else is a failure. A branch that reported
    /// every non-2xx as a refusal would tell somebody an admin had blocked
    /// their phone when the door had simply fallen over.
    ///
    /// ### What this case does **not** assert, and the open call it left
    ///
    /// That FastAPI's field list never reaches the person. It does:
    /// ``phoneServerDetail(_:)`` correctly declines a `detail` that is a
    /// list, but the fall-through is ``failureText(_:_:)``, whose message is
    /// the shared `"{status}: {body first 200 chars}"` convention — so a 422
    /// does show `string_too_short` and `loc` in the toast. That is the
    /// convention every screen in both apps uses (CONVENTIONS rule 4), not
    /// this item's wiring, so it is written up in DECISIONS for the owner
    /// rather than asserted against here.
    @MainActor
    func testPhoneMedia_AFailureIsNotReportedAsARefusalOrAnUpload() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))

        phoneUpload("{\"fail\":\"422\"}")
        phoneUploadControls(app).element(boundBy: 0).tap()

        let failed = startingWith(app, StubPhoneMedia.uploadFailedPrefix)
        let said = failed.waitForExistence(timeout: 40)
        evidence(app, "phonemedia-422")
        XCTAssertTrue(said, "a 422 was never reported as a failure")
        XCTAssertFalse(
            startingWith(app, StubPhoneMedia.uploadRefusedPrefix).exists,
            "a 422 was reported in the 403's words — somebody is told their phone is blocked"
        )
        XCTAssertFalse(
            app.staticTexts[StubPhoneMedia.uploadedTrack].firstMatch.exists,
            "a 422 was reported as a successful upload"
        )
    }

    /// A row over the ceiling has **no upload control** and a line saying how
    /// big it is (``PhoneUploadModel/cap``, and ``FileSaveModel``'s own
    /// pattern the other way round).
    ///
    /// A cap enforced only inside `upload()` would be a button that always
    /// fails, which is what this asserts is not there.
    @MainActor
    func testPhoneMedia_AFileOverTheCapHasNoUploadControl() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord))

        let note = startingWith(app, StubPhoneMedia.tooBigPrefix)
        let drawn = note.waitForExistence(timeout: 30)
        evidence(app, "phonemedia-over-the-cap")
        XCTAssertTrue(drawn, "the 200 MB row did not say why it has no upload")
        // The row under the cap keeps its control; the one over it has none.
        // One, not zero and not two: that is the whole claim.
        XCTAssertEqual(
            phoneUploadControls(app).count, 1,
            "the row over the ceiling was given an upload button that would always fail"
        )
        XCTAssertTrue(
            containing(app, StubPhoneMedia.phoneBigFileWord).exists,
            "the oversized row is not even listed, so the note above belongs to nothing"
        )
    }

    // MARK: - the phone's own states

    /// A folder with nothing in it is an empty folder, not a failure and not
    /// a filter that matched nothing.
    @MainActor
    func testPhoneMedia_AnEmptyFolderSaysSoAndIsNotAFailure() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music", media: "empty")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.noMusicOnDevice))
        evidence(app, "phonemedia-empty-folder")
        XCTAssertTrue(
            containing(app, StubPhoneMedia.downloadsLandHere).exists,
            "the empty state is missing its second line"
        )
        XCTAssertFalse(
            containing(app, StubPhoneMedia.noLongerReadable).exists,
            "an empty folder was reported as unreadable"
        )
    }

    /// A folder whose bookmark stopped resolving says so, where an empty list
    /// would read as "you have no music".
    @MainActor
    func testPhoneMedia_AFolderThatStoppedResolvingSaysSoRatherThanLookingEmpty() throws {
        phoneReset()
        let app = phoneLaunch(push: "Music", media: "unreadable")
        XCTAssertTrue(phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.noLongerReadable))
        evidence(app, "phonemedia-unreadable-folder")
        XCTAssertFalse(
            containing(app, StubPhoneMedia.noMusicOnDevice).exists,
            "an unreadable folder was drawn as an empty one"
        )
    }

    // MARK: - Dynamic Type, measured

    /// The switch's words grow with the type size — **and** the switch leaves
    /// the page enough of the window to be a page.
    ///
    /// ### What this case used to assert, and why that became the wrong
    /// question
    ///
    /// It compared the `your device` **segment's own height** at `large` and
    /// at AX5 and required the second to be bigger. That caught the defect it
    /// was written for — Android's pill is 40 dp (`YourDevice.kt:196`) and a
    /// straight port would be `frame(height: 40)`, which does not clip, so
    /// nothing but a measurement finds it — and then it rewarded the cure
    /// that was worse: the taller the switch grew, the better it scored.
    /// R003 measured where that ended. On a 393×852 phone at AX5 the switch
    /// was a 164-point column and the page under it 178.7 points, against the
    /// 260 dp that Android's own shell treats as the floor below which it
    /// stops drawing chrome at all (`AppShell.kt:481`'s `CONTENT_FLOOR`,
    /// read by `YourDevice.kt:198` and `Kit.kt:212`).
    ///
    /// So ``PhoneSourceCollapsedSwitch`` collapses the switch to one row at
    /// accessibility sizes, and this case now measures the four properties
    /// that the one it replaced was standing in for:
    ///
    /// 1. **The collapse happens where it should and nowhere else** — the
    ///    two-segment pill at an ordinary size, the one row at AX5, never
    ///    both and never the wrong one.
    /// 2. **The row's text still scales**, measured against the 44-point
    ///    floor rather than against the old size (see below). A fixed font
    ///    size or a `minimumScaleFactor` anywhere in the switch fails it.
    /// 3. **Both sides are still offered and the house is still named** — the
    ///    row says which side, the menu says which server, so the name the
    ///    column used to show in full is still shown in full.
    /// 4. **The page gets its window back.** A future switch that grows back
    ///    into a column fails on Android's floor rather than scoring well on
    ///    height, which is the trap the case this replaced fell into.
    ///
    /// ### And why the AX5 half is `> 44` rather than a ratio
    ///
    /// Because the accessibility tree cannot report a `Text`'s own height
    /// inside a `Button`. R003's `large` tree has the segment at
    /// `{{15.5, 144.8}, {178.7, 45}}` and its `StaticText` at
    /// `{{16, 145.3}, {177.7, 44}}` — the **button's** 44-point `minHeight`,
    /// not the 18-point line in the middle of it. So "the words grew" is not
    /// a measurable quantity here, and the measurable thing that means the
    /// same is this: the collapsed row is one line of `.subheadline` against a
    /// 44-point floor, so a row **taller than 44** is a row whose text
    /// outgrew the floor, and a row of exactly 44 is a row whose text did not
    /// scale. R003's AX5 tree gives that line as 49 points (the server
    /// segment's two lines measured 98), which is the five points of margin
    /// this assertion lives on; the number is printed either way.
    @MainActor
    func testPhoneMedia_TheSwitchSegmentsGrowWithTheTypeSize() throws {
        phoneReset()
        // At `large` the pill is intact: two segments, side by side, which is
        // Android's control to the point. If this ever collapses at an
        // ordinary size the AX5 half below would still pass, so it is asserted
        // here.
        let small = phoneLaunch(push: "Music")
        let ordinary = small.buttons[StubPhoneMedia.serverSegment].firstMatch
        XCTAssertTrue(ordinary.waitForExistence(timeout: 40), "the switch never drew at L")
        let boxAtL = ordinary.frame
        evidence(small, "phonemedia-segment-L")
        print("SWITCH AT L segment=\(boxAtL)")
        XCTAssertTrue(
            small.buttons[StubPhoneMedia.yourDevice].exists,
            "the pill drew one segment at an ordinary size — both sides are on screen there"
        )
        XCTAssertFalse(
            small.buttons[StubPhoneMedia.collapsedSwitch].exists,
            "the switch collapsed at an ordinary size, where there is room for the pill"
        )
        small.terminate()

        let big = phoneLaunch(push: "Music", mode: "UICTContentSizeCategoryAccessibilityXXXL")
        let collapsed = big.buttons[StubPhoneMedia.collapsedSwitch].firstMatch
        XCTAssertTrue(
            collapsed.waitForExistence(timeout: 40),
            "the switch drew no collapsed row at AX5"
        )
        let boxAtAX5 = collapsed.frame
        // The page under the switch, by the same helper the Files and Videos
        // cases scroll with, so the number this asserts is the number they
        // print.
        let page = documentsFolderScroll(big)?.frame ?? .null
        evidence(big, "phonemedia-segment-AX5")
        print("SWITCH AT AX5 row=\(boxAtAX5) page=\(page)")

        // 1. The collapse happened, rather than both shapes drawing.
        XCTAssertFalse(
            big.buttons[StubPhoneMedia.serverSegment].exists,
            "the two-segment pill is still on screen at AX5 as well as the collapsed row"
        )
        // 2. The row's one line of text outgrew the 44-point floor, which is
        //    what a scaling font does and a fixed one does not.
        XCTAssertGreaterThan(
            boxAtAX5.height, 44,
            "the collapsed row is \(boxAtAX5.height) points at AX5 — exactly the platform "
                + "minimum, so its text is not scaling"
        )
        // 3. Both sides are still offered, in full. The row names the side;
        //    the menu names the house, and this is what says the name did not
        //    simply go away.
        collapsed.tap()
        let serverRow = phoneSourceMenuRow(big, .server)
        let deviceRow = phoneSourceMenuRow(big, .device)
        evidence(big, "phonemedia-segment-AX5-menu")
        XCTAssertNotNil(deviceRow, "the collapsed switch does not offer the phone's side")
        guard let serverRow else {
            print("=== THE MENU DOES NOT NAME THE HOUSE — tree follows ===")
            print(big.debugDescription)
            return XCTFail("the collapsed switch's menu does not carry the server's own name")
        }
        XCTAssertTrue(
            serverRow.label.contains(StubPhoneMedia.serverAddressStem),
            "the menu's server row says `\(serverRow.label)`, which is not the house's name"
        )
        // Picking the side that is already on is idempotent
        // (`PhoneSourceChoices.choose`) and is how this closes the menu
        // without tapping anything it did not mean to.
        serverRow.tap()

        // 4. And the page is above the floor Android's own shell keeps.
        XCTAssertGreaterThanOrEqual(
            page.height, 260,
            "the switch leaves the page \(page.height) of 852 points, under Android's own "
                + "CONTENT_FLOOR of 260 (AppShell.kt:481). switch=\(boxAtAX5) page=\(page)"
        )
    }

    // MARK: - the audit

    /// Apple's own audit of the device side of Files.
    ///
    /// One audit case per screen, classified by **signature** with
    /// ``floorAndRest(_:)`` and never a pinned total: the floor is the
    /// anonymous Dynamic Type finding every pushed screen in this app
    /// carries, and anything the audit can attribute to an element is a
    /// defect to fix. Files is the device page with the most on it — the
    /// library picker, the two add doors, the breadcrumb, the filter, the
    /// rows and their icon-only upload buttons.
    ///
    /// ### What train S006 reported, and which half of it was real
    ///
    /// Fourteen findings Apple could attribute. **One was a defect**: `Label
    /// not human-readable` against the server's segment, whose whole label is
    /// the house's address — fixed where it belongs, in
    /// ``PhoneSourceSegment`` (`PhoneMediaStrings.serverSideLabel(_:)`).
    ///
    /// The other thirteen were `Dynamic Type font sizes are partially
    /// unsupported` against the device rows' titles, their meta lines, their
    /// durations, the count line and the too-big note — every one of them
    /// `.subheadline` or `.caption` with no fixed size anywhere near it. A
    /// named Dynamic Type finding is **not a defect by virtue of being
    /// named**: it has now been a false positive three times on this track
    /// (the `favorites only` switch, the Videos grid, the Files list), and
    /// the only honest answer is a measurement, which
    /// ``testPhoneMedia_TheDeviceSideTextGrowsAtAX5`` is. So this case drops
    /// that one headline from `rest` and keeps every other kind — the same
    /// split `testTheFilesListAudits` makes over the server side of the very
    /// same rows. A leaf that really is stuck fails the measurement.
    ///
    /// The floor is printed rather than pinned: the device page carries both
    /// anonymous and named findings of that headline, and a pinned total is
    /// what let a new contrast finding in unnoticed once already.
    @MainActor
    @available(iOS 17.0, *)
    func testPhoneMedia_TheDeviceSideHasNoAuditFindings() throws {
        phoneReset()
        let app = phoneLaunch(push: "Files")
        // The message is not decoration. R004 failed this line as a bare
        // `XCTAssertTrue failed`, and the helper's own print was the only
        // thing that said the switch was never found — which it also got
        // wrong, because the run had lost the app
        // (``phoneSourcePickSide(_:_:)``).
        XCTAssertTrue(
            phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneTrackWord),
            "the device side of Files never drew — the print above says whether the app was still there"
        )
        evidence(app, "phonemedia-files-audit")

        let found = try auditIssues(app, "phonemedia-files")
        let split = Self.floorAndRest(found)
        print("PHONEMEDIA AUDIT floor=\(split.floor) rest=\(split.rest.count)")
        for line in split.rest { print("PHONEMEDIA AUDIT REST :: \(line)") }

        let unexplained = split.rest.filter { !$0.hasPrefix("Dynamic Type font sizes") }
        XCTAssertEqual(
            [], unexplained,
            "the device side of Files has a finding of its own"
        )
    }

    /// The leaves Apple's audit names on the device side of Files, measured
    /// at `large` and at AX5.
    ///
    /// The companion to the classification above, and the test that actually
    /// decides whether those thirteen findings were defects. Five code sites
    /// cover all thirteen: ``PhoneMediaRowView``'s title (`.subheadline`) and
    /// its meta and duration (`.caption`), ``PhoneDeviceChrome``'s count line
    /// and ``PhoneUploadTooBigNote``. A folder's name is the sixth, and it is
    /// the row title drawn inside a `Button` rather than a combined row —
    /// worth its own measurement because the wrapper differs.
    ///
    /// Matched on `.staticTexts` rather than `.any`, which is this file's
    /// habit elsewhere: every one of the thirteen was reported as `type=48`,
    /// and an `.any` query would take the enclosing row or button — a
    /// different element from the one the audit flagged, and one that grows
    /// for its own reasons.
    ///
    /// A leaf that cannot be found is skipped, not failed, because the fake
    /// library's foot is unbuilt at AX5; the case fails instead if fewer than
    /// two leaves could be compared, so it can never pass by measuring
    /// nothing.
    ///
    /// ### Why it no longer waits for a row, and no longer swipes the app
    ///
    /// Train S009 failed this case at AX5 with "the device side never drew",
    /// and the tree it printed said the opposite: `phone-source-device` was
    /// `Selected`, the device header and both upload doors were drawn, and
    /// the device list's content was 2995 points of unbuilt rows. Two
    /// mistakes of this case, both about AX5 and neither about the app:
    ///
    /// 1. ``phoneSwitchToDevice(_:waitingFor:)`` gates on a **row**, which
    ///    is right for every other case here and wrong for this one — the
    ///    thing this case then scrolls to find cannot be the thing it waits
    ///    for. It gates on the device header instead, which is the first
    ///    line of the device page at any type size.
    /// 2. `app.swipeUp()` swipes the centre of the window. At AX5 the stub
    ///    launch chrome takes the top 300 points and the switch becomes a
    ///    column, so the window's centre lands **on the switch** and the
    ///    list never moved. The swipe goes to the device page's own
    ///    `ScrollView`, picked by the header it contains (the server side's
    ///    `ScrollView` is still in the tree at the same frame, holding its
    ///    place across the switch, so `.firstMatch` alone would take either).
    ///
    /// Each leaf's height is recorded the **first flick it is built on** and
    /// never overwritten, rather than reading all six once at the end. At
    /// AX5 the viewport is a little over one row tall, so no single scroll
    /// position holds two leaves; measuring as they appear is what makes the
    /// comparison possible at all, and it measures four more leaves at
    /// `large` than the single read did.
    @MainActor
    func testPhoneMedia_TheDeviceSideTextGrowsAtAX5() throws {
        let leaves = [
            ("row-title", "label == 'quenlo lullaby.mp3'"),
            ("row-meta", "label BEGINSWITH '3.0 MB · '"),
            ("row-duration", "label == '3:34'"),
            ("folder-name", "label == 'Trips'"),
            ("count", "label BEGINSWITH 'showing 5 of'"),
            ("too-big", "label BEGINSWITH 'too big to upload from a phone'"),
        ]

        /// Flick the device list to its foot, taking each leaf's height the
        /// first time it exists.
        ///
        /// The drag is two **coordinates**, not `list.swipeUp()`, and they
        /// are worked out once from the list's frame before the first flick.
        /// An `XCUIElement` is re-resolved on every use, and the only handle
        /// on this `ScrollView` is the header it contains — a header that is
        /// in a `LazyVStack` and unbuilds the moment it scrolls off, so the
        /// second flick would have had nothing to send a swipe to.
        func heights(_ app: XCUIApplication, _ frame: CGRect) -> [String: CGFloat] {
            let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            let from = origin.withOffset(
                CGVector(dx: frame.midX, dy: frame.maxY - 16)
            )
            let to = origin.withOffset(
                CGVector(dx: frame.midX, dy: frame.minY + 16)
            )
            var out: [String: CGFloat] = [:]
            // A press-then-drag, not a flick: a flick's momentum carries it
            // over rows, and at AX5 a row stepped over is a leaf that was
            // never built and so never measured. It also moves only the drag
            // distance, hence a count this high — at AX5 the viewport is
            // under 180 points and the device list's content is 2995.
            let flicks = 24
            for flick in 0...flicks {
                for (leaf, predicate) in leaves where out[leaf] == nil {
                    let found = app.staticTexts
                        .matching(NSPredicate(format: predicate))
                        .firstMatch
                    if found.exists { out[leaf] = found.frame.height }
                }
                if out.count == leaves.count { break }
                if flick < flicks { from.press(forDuration: 0.1, thenDragTo: to) }
            }
            return out
        }

        phoneReset()
        var measured: [String: [String: CGFloat]] = [:]
        for (label, mode) in [
            ("large", Self.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let app = phoneLaunch(push: "Files", mode: mode)
            XCTAssertTrue(
                phoneSwitchToDeviceHeader(app, waitingFor: StubPhoneMedia.phoneFilesSubtitle),
                "the device side never drew at \(label)"
            )
            let list = app.scrollViews
                .containing(
                    NSPredicate(format: "label == %@", StubPhoneMedia.phoneFilesSubtitle)
                )
                .firstMatch
            XCTAssertTrue(
                list.waitForExistence(timeout: 15),
                "the device page's list is not a scroll view at \(label)"
            )
            let here = heights(app, list.frame)
            evidence(app, "phonemedia-scale-\(label)")
            measured[label] = here
            for (leaf, height) in here.sorted(by: { $0.key < $1.key }) {
                print("PHONEMEDIA SCALE [\(label)] \(leaf) height=\(height)")
            }
            app.terminate()
        }

        let small = measured["large"] ?? [:]
        let big = measured["AX5"] ?? [:]
        var compared = 0
        var stuck: [String] = []
        for (leaf, height) in small.sorted(by: { $0.key < $1.key }) {
            guard let grown = big[leaf] else { continue }
            compared += 1
            // 1.8 is the factor `testTheFilesListTextGrowsAtAX5` settled on
            // over the same two text styles: `large` to AX5 is about three
            // times the line height, and a leaf that wrapped already at
            // `large` grows by less than its font does.
            if grown < height * 1.8 { stuck.append("\(leaf) \(height)->\(grown)") }
        }
        XCTAssertEqual([], stuck, "a leaf Apple flagged really does not scale")
        XCTAssertGreaterThanOrEqual(
            compared,
            2,
            "nothing could be compared, so this proved nothing: \(small) / \(big)"
        )
    }
}

/// What the phone's faked library and the stub house hold, **by the words
/// that appear on screen**.
///
/// Spelt out here rather than imported, like every other string this file
/// matches on: a query that silently followed a renamed constant would stop
/// testing what it claims to. The fake's are ``PhoneMediaFake``'s
/// (`ios/Domovoi/PhoneMedia/PhoneDeviceSources.swift`); the server's are the
/// stub's own (`design-notes/ios/stubhouse/stubhouse.py`).
enum StubPhoneMedia {

    // ---- the switch ----------------------------------------------------

    /// `PhoneMediaStrings.yourDevice` (`YourDevice.kt:215`).
    static let yourDevice = "your device"
    /// The server's segment, by `PhoneSourceSwitchPill.serverSegmentID`. Its
    /// LABEL is the house's own name, so an identifier is the only stable
    /// handle on it.
    static let serverSegment = "phone-source-server"
    /// `PhoneSourceSwitchPill.deviceSegmentID`. The device's side has a
    /// readable label as well; this is what finds its row inside the
    /// collapsed menu, where a label may be the one VoiceOver was given.
    static let deviceSegment = "phone-source-device"
    /// `PhoneSourceSwitchPill.collapsedID` — the one-row switch
    /// ``PhoneSourceCollapsedSwitch`` draws at an accessibility type size.
    /// Labelled with whichever side is current, so an identifier is the only
    /// stable handle on it too.
    static let collapsedSwitch = "phone-source-switch"
    /// Enough of the stub house's address to find it and not enough to depend
    /// on a truncated label (`phoneLaunch` passes `127.0.0.1:6601`).
    static let serverAddressStem = "127.0.0.1"
    /// The SHELL's server chip (`ShellChromeViews.swift`), which must never be
    /// tapped: it opens the connection sheet, and NIGHT-RULES §1 forbids one
    /// on a Simulator because it sweeps Kamron's real home network. Spelt out
    /// here so ``StubHouseUITests/phoneSourceMenuRow(_:_:)`` can skip it by
    /// name as well as by query shape.
    static let switchServer = "switch server"
    /// The phone bar's buttons carry the route's own name
    /// (`ShellChromeViews.swift:304`), which is `Route`'s raw value.
    static let musicTab = "Music"
    static let videosTab = "Videos"

    // ---- the fake's rows, each with a word the stub never uses ----------

    /// The 3 MB track: the one row on the device side of Music with an
    /// upload control.
    static let phoneTrackWord = "quenlo"
    /// The 8 MB video, which is the one video tile.
    static let phoneVideo = "zarbis"
    /// The 200 MB row, which must be listed and must have **no** control.
    static let phoneBigFileWord = "grendalf"

    // ---- the server's rows, which the phone's side must not show --------

    /// The stub's first video, whose tile label is its name
    /// (`stubhouse.py:397`, index 0).
    static let serverVideo = "a long one.mp4"
    /// A folder of the stub's first Files library, `core:music`
    /// (`stubhouse.py:285`).
    static let serverFolder = "Albums"
    /// A file that exists **only inside** `Albums`, so "still in the folder"
    /// cannot be satisfied by the root listing.
    static let serverFileInsideFolder = "a song.mp3"

    // ---- what the app says ---------------------------------------------

    static let upload = "upload"
    /// `PhoneMediaStrings.uploadingLabel(nil)` — there is no percentage,
    /// because `DomovoiClient.raw` reports none.
    static let uploading = "uploading"
    static let uploadedTrack = "uploaded \"quenlo lullaby.mp3\" to Music · indexing…"
    /// Documents, not Videos: the stub has no `core:videos` **Files**
    /// library.
    static let uploadedVideoToDocuments = "uploaded \"zarbis dune ride.mp4\" to Documents · indexing…"
    static let alreadyUploading = "\"quenlo lullaby.mp3\" is already uploading"
    static let uploadRefused = "upload refused: \"a simulator\" may not upload here"
    static let uploadFailedPrefix = "upload failed"
    /// ``PhoneMediaStrings/uploadRefused(_:)``'s own opening, which only a
    /// 403 may use.
    static let uploadRefusedPrefix = "upload refused"
    /// A prefix of `PhoneMediaStrings.tooBigToUpload(_:)`, matched as a
    /// prefix so the byte formatting is not this case's business.
    static let tooBigPrefix = "too big to upload from a phone"
    /// A fragment of the refused card's blocked tail
    /// (`PhoneMediaStrings.blockedTail`), short enough to be a legal query.
    static let adminLiftsTheBlock = "An admin lifts the block"
    /// `FILES_BLOCKED_REASON` (`stubhouse.py:354`), which is what
    /// `/api/files/browse` returns as `blocked_reason` and what the button
    /// reads out as its value.
    static let blockedReason = "\"a simulator\" is blocked from changing files by an admin"
    /// `PhoneMediaStrings.phoneFilesSubtitle` — the device page's own second
    /// line, and the first thing on it that is drawn at every type size.
    /// Spelt out here, like every other string this file matches on.
    static let phoneFilesSubtitle = "on this phone — folders you add, and your photos"
    static let noMusicOnDevice = "no music on this device"
    static let downloadsLandHere = "downloads from a domovoi land in Downloads/Domovoi"
    static let noLongerReadable = "no longer readable"
    /// What ``ScreenPlaceholder`` drew in the local shell before this item.
    static let notBuiltYet = "on this phone — not built yet"
}

/// One side of the source switch, as a case finds it.
///
/// Two handles rather than one, because the switch has two shapes and the
/// server's side has no readable label in either: the identifier that the
/// pill's segment and the collapsed menu's row both carry, and the words that
/// survive in a menu whose identifiers SwiftUI may not have carried into
/// UIKit.
enum PhoneSideUnderTest {

    case server
    case device

    // `segmentLabel` — "an identifier for the server, the words for the
    // phone", for `app.buttons[_:]`, which matches either — is gone with the
    // poll that used it: ``StubHouseUITests/phoneSourcePickSide(_:_:)`` now
    // matches both shapes on `identifier` alone, which every one of the four
    // elements carries. A second way to name the same side is how two cases
    // come to disagree about which control they found.

    /// The identifier this side carries in both shapes
    /// (`PhoneSourceCollapsedSwitch.identifier(of:)` is the same two).
    var identifier: String {
        switch self {
        case .server: return StubPhoneMedia.serverSegment
        case .device: return StubPhoneMedia.deviceSegment
        }
    }

    /// Words of this side that appear on its row of the collapsed menu —
    /// whether that row kept the title it was given or the spoken label over
    /// it (`PhoneMediaStrings.serverSideLabel(_:)` contains the address, and
    /// `yourDevice` is both).
    var words: String {
        switch self {
        case .server: return StubPhoneMedia.serverAddressStem
        case .device: return StubPhoneMedia.yourDevice
        }
    }
}
