import XCTest

/// The cases this batch's **Screens** unit added, across the five screens it
/// touched.
///
/// Its other work is asserted where it belongs — the Images sentence on the
/// card's own case (`testImages_AHistoryCardShowsThreeTilesOfFour`), the
/// container check in the package (`HealthChecksTests`, `HealthGuardTests`),
/// the follow state machine in `ScreensLyricsFollowTests`. What is here is
/// what needed a **new** case and a running app: the drag that pauses Music's
/// lyrics and the timer that un-pauses it, the `warn again` announcement, and
/// the News chip row with no bubble in it.
///
/// Every case is `testScreens_…` because that prefix is how a train hands
/// results back to this unit (NIGHT-RULES §2).
///
/// ### What this file calls, and what it had to bring itself
///
/// Music's and News's entry helpers (`musicMoreReset`, `openMusicMore`,
/// `openTrackSheet`, `resetNewsHouse`, `openNews`, `newsChipRowIsOneElement`)
/// are `internal` on the shared class, so they are read and called here and
/// never edited — this file conflicts with nothing a train merges beside it.
///
/// Settings' are **not**: `aiSettingsLaunch(seed:)` and
/// `reachAiSettingsSection(_:group:section:)` are `fileprivate` to
/// `StubHouseUITests+AiSettings.swift`, deliberately, so that twenty
/// overnight branches can each name a `launch` helper on one class without an
/// `invalid redeclaration` dropping a whole branch from a train. A
/// `fileprivate` member is unreachable from another file, so the `warn again`
/// case below carries its own launch and its own walk to the panel, under
/// `screens…` names, rather than widening another unit's helpers to internal
/// and taking that risk on its behalf. The duplication is three short
/// functions and is the cheaper of the two.
extension StubHouseUITests {

    // MARK: - D35: the lyrics panel comes back to the song by itself

    /// A drag pauses following; **four seconds later it comes back without
    /// being asked** (Kamron's answer 35).
    ///
    /// Android pauses on `DragInteraction.Start` and resumes
    /// `FOLLOW_PAUSE_MS` after `Stop` (`LyricsPanel.kt:468-485`, `:118`), and
    /// iOS came back only from the chip until today.
    ///
    /// ### What is observed, and why it is the chip
    ///
    /// The `follow` chip is drawn **only** while following is off and there
    /// is a timed list (`MusicLyricsPanelView.swift:267`), so its appearance
    /// and disappearance is the flag itself, in the app, with nothing in
    /// between. Watching the highlighted line instead would mean knowing
    /// which line the clock is on four seconds from now — a race against the
    /// fixture's own timings, in a case that is about a boolean.
    ///
    /// It is queried by ``ScreensWords/followChip`` and not by the word it
    /// draws: the chip reads `follow` but carries
    /// `MusicMoreStrings.followTheSongAgain` as its accessibility label
    /// (`MusicLyricsPanelView.swift:271-272`), and a label, once set, is what
    /// a query sees.
    ///
    /// ### Track 506, and why the document is fifteen lines
    ///
    /// 506 is synced and **no room plays it** (`fixtures/musicmore.py`), so
    /// its track sheet is a timed panel with no room behind it: the timing
    /// chips are not drawn there (`testMusicMore_NoRoomNoTimingChips` pins
    /// exactly that), which leaves the `follow` chip the only button in that
    /// footer and nothing else to mistake it for. Its document was three
    /// lines, which **fit** the panel's 240-point frame — and SwiftUI reports
    /// no `.interacting` phase for a scroll view whose content fits, so a
    /// drag on it would have proved nothing. The seed is fifteen lines for
    /// that reason, and this case **measures** that the content moved — the
    /// first line's `frame.minY` before and after — so that a drag the panel
    /// ignored cannot be read as a pause it performed.
    ///
    /// ### The gesture
    ///
    /// `withVelocity: .slow` and a hold at the end, not a bare
    /// `press(forDuration:thenDragTo:)`: train R002 measured that the short
    /// form is a press followed by a **flick** at 500 pt/s, and a list still
    /// decelerating keeps reporting `.interacting` after the finger is gone.
    /// This case needs the lift to be a lift — the four seconds are counted
    /// from it.
    ///
    /// The wait is the one place this file spends real time. `followResumeMs`
    /// is 4,000 and the panel republishes on a 100 ms loop; twelve seconds is
    /// that with room for a Simulator under load, and the **pair** of
    /// assertions is what makes it mean something — the chip has to be there
    /// first, or a case that only looked for its absence would pass against a
    /// panel that never paused at all.
    @MainActor
    func testScreens_TheLyricsPanelComesBackToTheSongByItself() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(
            openTrackSheet(app, MusicMoreUnderTest.harrowgate),
            "506's sheet never opened"
        )
        let firstLine = startingWith(app, MusicMoreUnderTest.harrowgateLine)
        XCTAssertTrue(
            firstLine.waitForExistence(timeout: 25),
            "506's synced document drew no lines, so there is nothing to drag"
        )

        // Following is on, so there is no chip yet. Asserted before the drag
        // because the whole case turns on the chip's two states.
        let chip = app.buttons[ScreensWords.followChip]
        XCTAssertFalse(
            chip.exists,
            "a `follow` chip is drawn on a panel that is already following"
        )
        evidence(app, "screens-10-lyrics-following")

        // **The drag.** From the middle of the first line, 120 points up:
        // begun on a lyric line so that it is the panel's own scroll view
        // that moves and not the sheet's, and short enough to stay inside the
        // panel's 240-point frame at either end.
        let wasAt = firstLine.frame.minY
        let from = firstLine.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        from.press(
            forDuration: 0.3,
            thenDragTo: from.withOffset(CGVector(dx: 0, dy: -120)),
            withVelocity: .slow,
            thenHoldForDuration: 0.5
        )
        // That the content moved, measured — not assumed from the chip, which
        // is the thing under test.
        XCTAssertLessThan(
            firstLine.frame.minY, wasAt,
            "the lyrics did not scroll, so whatever the chip does next is not about a drag"
        )
        XCTAssertTrue(
            chip.waitForExistence(timeout: 10),
            "a drag on the lyrics did not pause following — no `follow` chip appeared"
        )
        evidence(app, "screens-11-lyrics-paused")

        // And now nothing is touched.
        XCTAssertTrue(
            waitUntilGone(chip, timeout: 12),
            "following never came back on its own: the chip is still there "
                + "twelve seconds after the drag ended (answer 35)"
        )
        evidence(app, "screens-12-lyrics-resumed")
    }

    // MARK: - D2: `warn again` announces its kind

    /// Each `warn again` button **reads** `warn again` and **announces**
    /// `warn again, <the kind>` (Kamron's answer 2).
    ///
    /// Two silenced kinds, so the case can say the two announcements differ:
    /// a label built from a constant would be the same string on both rows,
    /// which is the defect this answers. The drawn word is asserted
    /// separately, because the point of the change is that it did **not**
    /// change — Android's wording stays on screen and only the announcement
    /// grows.
    ///
    /// The buttons are found by their identifiers, which is how
    /// `testAiSettings_WarnAgainClearsOneKindAndLeavesTheOther` finds them: a
    /// label query cannot be used here, since the labels are the thing under
    /// test.
    @MainActor
    func testScreens_EachWarnAgainAnnouncesItsKind() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = screensAiSettingsLaunch(seed: "on:calc,scanned")
        XCTAssertTrue(
            screensReachChatPanel(app),
            "the Chat & AI / Chat panel never drew"
        )

        let heading = app.staticTexts[AiSettingsUnderTest.notWarnedHeading]
        XCTAssertTrue(heading.waitForExistence(timeout: 15), "no silenced-kinds heading")
        let calc = app.buttons[AiSettingsUnderTest.warnAgainButton("calc")]
        let scanned = app.buttons[AiSettingsUnderTest.warnAgainButton("scanned")]
        XCTAssertTrue(calc.waitForExistence(timeout: 20), "no `warn again` for `calc`")
        XCTAssertTrue(scanned.exists, "no `warn again` for `scanned`")
        evidence(app, "screens-20-warn-again")

        XCTAssertEqual(
            "\(ScreensWords.warnAgain), \(AiSettingsUnderTest.calcWord)", calc.label,
            "the `calc` button announces the same three words as every other row"
        )
        XCTAssertEqual(
            "\(ScreensWords.warnAgain), \(AiSettingsUnderTest.scannedWord)", scanned.label,
            "his words, verbatim (answer 2)"
        )
        XCTAssertNotEqual(
            calc.label, scanned.label,
            "both rows announce the same thing, which is the defect this fixes"
        )

        // **And the drawn word is untouched.** The kind is in the
        // announcement only; a button that now *reads* its kind would be a
        // rewording of Android's control, which this answer did not ask for.
        XCTAssertTrue(
            app.staticTexts[ScreensWords.warnAgain].exists,
            "the visible `warn again` is gone from the panel"
        )
    }

    // MARK: - D39: the News chip row has no avatar bubble

    /// The person chips are plain pills: **no initial, anywhere in the row**
    /// (Kamron's answer 39).
    ///
    /// The bubble was the row's only colour and the last thing in it an
    /// auditor could sample and misattribute — five `Contrast failed` over
    /// four trains, written up in `StubHouseUITests+News.swift`. Dropping it
    /// is Android's shape for a compact chip row once its gradient avatars
    /// are not ported (answer 37).
    ///
    /// Asserted two ways, because each catches a different way of getting it
    /// wrong: the chips expose no text leaf at all
    /// (``newsChipRowIsOneElement(_:)``, which the audit case also pins), and
    /// no single-letter element exists on the page — which is what a bubble
    /// drawn *outside* a button's label would leave behind, and which the
    /// leaf count on its own would miss.
    @MainActor
    func testScreens_TheNewsPersonChipsHaveNoAvatarBubble() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")
        evidence(app, "screens-30-news-chips")

        let complaints = newsChipRowIsOneElement(app)
        XCTAssertEqual(
            [], complaints,
            "the chip row is not one element per chip: \(complaints.joined(separator: "; "))"
        )

        // A one-character element is what a bubble is whether it is inside a
        // button's label or drawn over the pill.
        for initial in ScreensWords.newsInitials {
            XCTAssertFalse(
                app.staticTexts[initial].exists,
                "an avatar bubble is still drawn: `\(initial)` is on the page"
            )
        }
    }

    // MARK: - this file's own way in to Settings

    /// `aiSettingsLaunch(seed:)`'s arguments, because that helper is
    /// `fileprivate` to another unit's file — see this extension's own note.
    ///
    /// Named `screens…` for the same reason it duplicates: one shared class,
    /// twenty branches, and `aiSettingsLaunch` is a name another of them
    /// already holds.
    @MainActor
    fileprivate func screensAiSettingsLaunch(seed: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:6601",
            "-domovoi-token", Self.token,
            "-domovoi-aisettings-headsup", seed,
            "-domovoi-audit-push", "Settings",
            "-domovoi-audit-push-delay", "\(Self.pushDelay)",
            "-UIPreferredContentSizeCategoryName", StubHouseUITests.ordinaryTypeSize,
        ]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// Settings, then **Chat & AI**, then **Chat**, in two taps.
    ///
    /// The wait between them is a marker only the group list draws
    /// (`all settings`), not the section's own label: the phone bar's `Chat`
    /// tab exists from launch, so waiting for `Chat` is satisfied by the
    /// wrong element and the tap lands on the conversation. That trap, and
    /// the topmost-match choice below, are the other unit's findings — read
    /// out of `reachAiSettingsSection(_:group:section:)`, which this cannot
    /// call.
    @MainActor
    fileprivate func screensReachChatPanel(_ app: XCUIApplication) -> Bool {
        guard screensTapTopmost(
            app, AiSettingsUnderTest.chatAndAiGroup,
            orFail: "Settings never drew the Chat & AI group row", timeout: 30
        ) else { return false }
        guard app.buttons[ShellStringsUnderTest.allSettings].waitForExistence(timeout: 15) else {
            XCTFail("the Chat & AI group list never drew — no `all settings` button")
            return false
        }
        return screensTapTopmost(
            app, AiSettingsUnderTest.chatSection,
            orFail: "the Chat & AI group list has no Chat row"
        )
    }

    /// Wait for a button with this label, then tap the **topmost** one.
    ///
    /// `firstMatch` for the existence check and `allElementsBoundByIndex`
    /// for the choice: a subscript element raises "multiple matching
    /// elements" on an ambiguous label even to be asked whether it exists,
    /// and a query resolved before the screen has drawn answers "none".
    @MainActor
    fileprivate func screensTapTopmost(
        _ app: XCUIApplication, _ label: String, orFail why: String,
        timeout: TimeInterval = 15
    ) -> Bool {
        let present = app.buttons.matching(identifier: label).firstMatch
        guard present.waitForExistence(timeout: timeout) else {
            XCTFail(why)
            return false
        }
        let all = app.buttons.matching(identifier: label).allElementsBoundByIndex
        guard let top = all.filter({ $0.exists }).min(by: { $0.frame.minY < $1.frame.minY }) else {
            XCTFail(why)
            return false
        }
        top.tap()
        return true
    }
}

/// The strings these cases match on, kept together so that a change in the
/// app that breaks a query is a one-line fix here.
///
/// `Screens`-prefixed, like every type this unit adds: one shared test
/// target, and a bare `ScreenWords` is a name another branch would plausibly
/// take.
enum ScreensWords {

    /// What the follow chip **announces** —
    /// `MusicMoreStrings.followTheSongAgain`
    /// (`MusicLyricsPanelView.swift:272`). It *reads* `follow`, but an
    /// accessibility label, once set, is what a query sees.
    static let followChip = "follow the song again"

    /// `ChatSettingsPanel.kt:71` — the word every row draws, and the first
    /// half of what each row now announces. Asserted as the drawn text as
    /// well: answer 2 grew the announcement and left the wording alone.
    static let warnAgain = "warn again"

    /// The initials of the People seed's whole roster — Kamron and Chevy,
    /// the two people in `fixtures/news.py` — which is what an avatar bubble
    /// would have drawn. Both, not a sample: a bubble dropped from one chip
    /// and left on the other is a defect this would otherwise miss.
    static let newsInitials = ["K", "C"]
}
