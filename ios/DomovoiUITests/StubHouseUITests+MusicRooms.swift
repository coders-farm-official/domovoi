import XCTest

/// Music's rooms folded behind `Show rooms`, and the player's `Stop and
/// close` — the port of Android d19efd9c (mobile hand-off §2 and §2b).
///
/// Every case is named `testMusicRooms_…` and every piece of evidence
/// `musicrooms-…`.
///
/// ### Which phone each case starts from
///
/// The choice is kept in `UserDefaults`, which outlives a launch — that is
/// the feature — so a case that needs a phone that **never chose** launches
/// with `-domovoi-music-rooms-forget` (DEBUG only, once per process;
/// ``MusicRoomsOpenStore``). Every case that opens the strip shuts it again
/// before it ends, so the cases after it in this shared Simulator meet the
/// default too; the ones in `+MusicMore` that read a room card ask for the
/// strip open on their own launch line instead.
///
/// The house is the MusicMore seed (`fixtures/musicmore.py`): parlour plays a
/// library track, cellar a stream, attic is idle — **two playing**, which is
/// the count the folded link carries.
extension StubHouseUITests {

    /// Music, pushed, from a phone that has not chosen (or, with
    /// `forget: false`, from whatever this phone chose last).
    @MainActor
    func openMusicRooms(forget: Bool = true) -> (app: XCUIApplication, landed: Bool) {
        musicMoreReset()
        let app = launch(
            push: "Music", pushDelay: Self.pushDelay,
            args: forget ? ["-domovoi-music-rooms-forget"] : []
        )
        let crumb = app.staticTexts[ShellStringsUnderTest.musicCrumb]
        guard crumb.waitForExistence(timeout: 40) else {
            print("=== MUSIC NEVER PUSHED — tree follows ===\n" + app.debugDescription)
            return (app, false)
        }
        // The link is the first thing under the header, and it is drawn once
        // the page's models are built — the same moment the strip would be.
        let link = musicRoomsLink(app)
        guard link.waitForExistence(timeout: 30) else {
            print("=== NO ROOMS LINK ON MUSIC — tree follows ===\n" + app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    /// The link, whichever of its three wordings it has.
    @MainActor
    func musicRoomsLink(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH %@ OR label == %@", MusicRoomsUnderTest.show, MusicRoomsUnderTest.hide)
        ).firstMatch
    }

    /// Whether any room card is on the page. The card's heading is the room's
    /// id, which nothing else on Music says.
    @MainActor
    func musicRoomsCardsShown(_ app: XCUIApplication) -> Bool {
        app.staticTexts[MusicRoomsUnderTest.parlour].exists
            || app.staticTexts[MusicRoomsUnderTest.attic].exists
    }

    @MainActor
    func musicRoomsAssertFolded(_ app: XCUIApplication, _ when: String) {
        // Waited for by its whole wording: the link reads plain `Show rooms`
        // until the page's now-playing read has answered, and the count
        // arrives with it.
        let link = app.buttons[MusicRoomsUnderTest.showTwoPlaying]
        XCTAssertTrue(
            link.waitForExistence(timeout: 20),
            "\(when): the folded link does not carry the count the page already read — tree follows\n"
                + app.debugDescription
        )
        XCTAssertEqual(
            link.value as? String, MusicRoomsUnderTest.collapsed,
            "\(when): the link does not say the rooms are collapsed"
        )
        XCTAssertFalse(
            musicRoomsCardsShown(app),
            "\(when): the room cards are drawn under a folded link — tree follows\n" + app.debugDescription
        )
    }

    @MainActor
    func musicRoomsAssertOpen(_ app: XCUIApplication, _ when: String) {
        let hide = app.buttons[MusicRoomsUnderTest.hide]
        XCTAssertTrue(
            hide.waitForExistence(timeout: 20),
            "\(when): the link does not read `Hide rooms` — tree follows\n" + app.debugDescription
        )
        XCTAssertEqual(
            hide.value as? String, MusicRoomsUnderTest.expanded,
            "\(when): the link does not say the rooms are expanded"
        )
        XCTAssertTrue(
            app.staticTexts[MusicRoomsUnderTest.parlour].waitForExistence(timeout: 20),
            "\(when): the strip is open and no room card is drawn — tree follows\n" + app.debugDescription
        )
    }

    // MARK: - folded by default

    /// A phone that never chose meets the rooms folded away, behind a link
    /// that says how many are playing and that it is collapsed — and the
    /// link opens them (the positive control: a page that simply never drew
    /// a card would pass the first half).
    @MainActor
    func testMusicRooms_FoldedByDefault() throws {
        let (app, landed) = openMusicRooms()
        XCTAssertTrue(landed, "Music never drew its rooms link")
        evidence(app, "musicrooms-10-folded")
        musicRoomsAssertFolded(app, "a phone that never chose")

        musicRoomsLink(app).tap()
        evidence(app, "musicrooms-11-opened")
        musicRoomsAssertOpen(app, "after `Show rooms`")

        // Shut again, so the cases after this one meet the default.
        app.buttons[MusicRoomsUnderTest.hide].tap()
        XCTAssertTrue(
            waitUntilGone(app.staticTexts[MusicRoomsUnderTest.parlour], timeout: 20),
            "`Hide rooms` left the cards up — tree follows\n" + app.debugDescription
        )
        musicRoomsAssertFolded(app, "after `Hide rooms`")
    }

    // MARK: - the choice is kept

    /// Open, relaunch: still open. Shut, relaunch: still shut. The second
    /// half is what tells a kept choice from a strip that simply starts open.
    @MainActor
    func testMusicRooms_TheChoiceIsRememberedAcrossARelaunch() throws {
        let (first, landed) = openMusicRooms()
        XCTAssertTrue(landed, "Music never drew its rooms link")
        musicRoomsAssertFolded(first, "a phone that never chose")
        musicRoomsLink(first).tap()
        musicRoomsAssertOpen(first, "after `Show rooms`")
        first.terminate()

        let (second, landedAgain) = openMusicRooms(forget: false)
        XCTAssertTrue(landedAgain, "Music never drew its rooms link on the second launch")
        evidence(second, "musicrooms-20-open-after-relaunch")
        musicRoomsAssertOpen(second, "relaunched after opening the rooms")
        second.buttons[MusicRoomsUnderTest.hide].tap()
        XCTAssertTrue(
            waitUntilGone(second.staticTexts[MusicRoomsUnderTest.parlour], timeout: 20),
            "`Hide rooms` left the cards up — tree follows\n" + second.debugDescription
        )
        second.terminate()

        let (third, landedThird) = openMusicRooms(forget: false)
        XCTAssertTrue(landedThird, "Music never drew its rooms link on the third launch")
        evidence(third, "musicrooms-21-shut-after-relaunch")
        musicRoomsAssertFolded(third, "relaunched after hiding the rooms")
    }

    // MARK: - Stop and close

    /// A playlist plays on this phone, the player tab holds it, and `Stop and
    /// close` empties it: the tab goes back to `nothing queued` and the
    /// control goes with what it closed.
    ///
    /// The phone half only. The room half — stopped first, a refusal keeping
    /// everything — is `PlayerModelStopAndCloseTests`, against a house that
    /// can park the stop inside its round trip, which this stub cannot.
    @MainActor
    func testMusicRooms_StopAndCloseClosesThePlayer() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let here = app.buttons[MusicMoreUnderTest.playHere]
        XCTAssertTrue(
            here.waitForExistence(timeout: 20),
            "the sheet offers no `play on this device` — tree follows\n" + app.debugDescription
        )
        here.tap()
        app.buttons[MusicMoreUnderTest.close].firstMatch.tap()
        XCTAssertTrue(
            musicMoreTapTab(app, MusicMoreUnderTest.tabPlayer),
            "the player tab is not offered after a play on this device"
        )

        let close = app.buttons[MusicRoomsUnderTest.stopAndClose]
        XCTAssertTrue(
            close.waitForExistence(timeout: 20),
            "the player tab holding a queue offers no `Stop and close` — tree follows\n" + app.debugDescription
        )
        XCTAssertFalse(
            containing(app, MusicMoreUnderTest.nothingQueued).exists,
            "the tab says nothing is queued before the close, so the close proves nothing"
        )
        evidence(app, "musicrooms-30-before-stop-and-close")
        XCTAssertTrue(musicMoreReveal(app, close), "`Stop and close` could not be brought into view")
        close.tap()

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.nothingQueued).waitForExistence(timeout: 20),
            "`Stop and close` did not close the player — tree follows\n" + app.debugDescription
        )
        evidence(app, "musicrooms-31-closed")
        XCTAssertTrue(
            waitUntilGone(close, timeout: 10),
            "`Stop and close` is still offered over nothing queued — tree follows\n" + app.debugDescription
        )
        XCTAssertEqual(
            XCUIApplication.State.runningForeground, app.state,
            "the app went away on `Stop and close` — " + StubHouseUITests.stateName(app.state)
        )
    }

    // MARK: - the audit

    /// Apple's audit on the folded page — the default every phone now meets —
    /// judged as `testNav_TheGroupedGridAudits` judges Home (the 2026-11-09
    /// helpers): the anonymous floor by signature (``floorAndRest(_:)``); if
    /// the audit moved the page, every pixel-sampled finding's element is
    /// read off the picture where it was drawn (fails under 4.5:1,
    /// ``roomsAssertOwnInk``) and those findings are retaken at the settled
    /// position, which must not move again; a retaken finding on an element
    /// the page clips is measured whole (``navClippedMeasured(_:_:on:anonymousForCutLeaves:)``).
    /// The one named Dynamic Type finding it meets, against the `favorites
    /// only` text, is allowed only after that text is measured growing at AX5.
    /// Anything else attributed fails.
    @MainActor
    func testMusicRooms_TheFoldedPageAudits() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        let (app, landed) = openMusicRooms()
        XCTAssertTrue(landed, "Music never drew its rooms link")
        musicRoomsAssertFolded(app, "before the audit")
        evidence(app, "musicrooms-40-audit")

        let before = roomsWindowShot(app)
        let picture = app.screenshot().image.cgImage
        let first = try auditIssues(app, "musicrooms-folded")
        let firstSplit = Self.floorAndRest(first)
        let settled = roomsWindowShot(app)
        let moves = Self.roomsPageMoves(from: before, to: settled)
        let other = firstSplit.rest.filter { !Self.roomsSamplesPixels($0) }
        var pixelLines = firstSplit.rest.filter(Self.roomsSamplesPixels)
        var judgedOn = before
        if moves.isEmpty {
            print("MUSICROOMS AUDIT: the audit left the page where it was — one pass is the measurement")
        } else {
            print(
                "MUSICROOMS AUDIT: the audit moved the page — "
                    + moves.prefix(6).map { "'\($0.label)' by \($0.dy)" }.joined(separator: ", ")
                    + " — so its \(pixelLines.count) pixel-sampled finding(s) are read where drawn and retaken"
            )
            roomsAssertOwnInk(
                pixelLines,
                on: before,
                picture: picture,
                window: app.frame,
                name: "musicrooms-folded"
            )
            evidence(app, "musicrooms-41-audit-settled")
            let retake = try auditIssues(app, "musicrooms-folded-settled")
            let movedAgain = Self.roomsPageMoves(from: settled, to: roomsWindowShot(app))
            XCTAssertTrue(
                movedAgain.isEmpty,
                "the retaken audit moved the page as well — "
                    + movedAgain.prefix(6).map { "'\($0.label)' by \($0.dy)" }.joined(separator: ", ")
                    + " — so its contrast findings are not a measurement either"
            )
            let retakeSplit = Self.floorAndRest(retake)
            for line in retakeSplit.rest where !Self.roomsSamplesPixels(line) {
                print("MUSICROOMS AUDIT retake (judged in the first pass) :: \(line)")
            }
            pixelLines = retakeSplit.rest.filter(Self.roomsSamplesPixels)
            judgedOn = settled
        }
        let clipped = navClippedMeasured(app, pixelLines, on: judgedOn)
        // Read off the audited launch **before** any other launch replaces it:
        // the ordinary half of the favourites switch's measurement.
        let switchText = app.staticTexts[MusicRoomsUnderTest.favoritesOnly]
        let ordinaryHeight: CGFloat? = switchText.exists ? switchText.frame.height : nil
        let scaling = other.filter(Self.musicRoomsIsTheSwitchScalingFinding)
        var rest = other.filter { !Self.musicRoomsIsTheSwitchScalingFinding($0) } + clipped.kept
        if !scaling.isEmpty {
            // **Allowed only for a label this case has just watched grow.**
            // I022 named `favorites only` (type=48, 101.7 x 20.3) for "User
            // will not be able to change the font size of this element" —
            // ``knownScalingFalsePositive``, which was measured once by hand
            // (20.33 -> 63.33 points at AX5) but never by a running case.
            // This is that measurement: the same leaf at AX5 on a launch of
            // its own, and the finding stays in `rest` unless it is at least
            // twice as tall there. A switch whose font were pinned to one
            // size would measure the same at both and fail here.
            app.terminate()
            let axHeight = musicRoomsFavouritesSwitchHeightAtAX5()
            let ordinary = ordinaryHeight.map { "\($0)" } ?? "not on screen"
            let ax5 = axHeight.map { "\($0)" } ?? "not on screen"
            print("SCALE [musicrooms] '\(MusicRoomsUnderTest.favoritesOnly)' large=\(ordinary) ax5=\(ax5)")
            if let small = ordinaryHeight, let big = axHeight, small > 0, big >= small * 2 {
                for line in scaling {
                    print("MUSICROOMS AUDIT measured scaling false positive (\(small) -> \(big)) :: \(line)")
                }
            } else {
                rest += scaling
            }
        }
        print(
            "MUSICROOMS AUDIT floor=\(firstSplit.floor) rest=\(firstSplit.rest.count) "
                + "moved=\(!moves.isEmpty) clipped-measured=\(clipped.allowed.count) "
                + "scaling-named=\(scaling.count) ours=\(rest.count)"
        )
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) attributed finding(s) on the folded Music page: "
                + rest.joined(separator: " || ")
        )
    }

    /// The one named Dynamic Type finding this case measures rather than
    /// believes: Apple's "font size" headline against the `favorites only`
    /// **text** (type 48, a `StaticText`) and nothing else. The same words on
    /// any other element, or any other headline on this one, stays in `rest`.
    static func musicRoomsIsTheSwitchScalingFinding(_ line: String) -> Bool {
        line.hasPrefix("Dynamic Type font sizes are partially unsupported")
            && line.contains("| element: type=48 \(knownScalingFalsePositive) frame=")
    }

    /// The `favorites only` text's height on Music at AX5, on a launch of its
    /// own; `nil` (with the tree printed) if it never drew. The filters sit
    /// under the tab strip in a `LazyVStack`, so at AX5 they are unbuilt until
    /// the page is scrolled to them (``musicMoreScroll(_:to:flicks:)``).
    @MainActor
    func musicRoomsFavouritesSwitchHeightAtAX5() -> CGFloat? {
        let (app, landed) = openMusicMore("UICTContentSizeCategoryAccessibilityXXXL")
        defer { app.terminate() }
        guard landed else {
            print("=== MUSIC NEVER LANDED AT AX5 — tree follows ===\n" + app.debugDescription)
            return nil
        }
        let text = app.staticTexts[MusicRoomsUnderTest.favoritesOnly]
        guard musicMoreScroll(app, to: text) else { return nil }
        evidence(app, "musicrooms-42-favourites-ax5")
        return text.frame.height
    }
}

/// The words these cases look for, spelled out rather than imported
/// (``MusicRoomsStrings``, ``MusicMoreStrings``), so a rename fails here.
enum MusicRoomsUnderTest {
    static let show = "Show rooms"
    static let hide = "Hide rooms"
    /// The MusicMore seed: parlour and cellar playing, attic idle.
    static let showTwoPlaying = "Show rooms (2 playing)"
    static let expanded = "expanded"
    static let collapsed = "collapsed"
    static let parlour = "parlour"
    static let attic = "attic"
    static let stopAndClose = "Stop and close"
    /// ``MusicStrings/favoritesOnly``.
    static let favoritesOnly = "favorites only"
}
