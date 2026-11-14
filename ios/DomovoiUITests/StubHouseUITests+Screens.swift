import XCTest

/// The cases this batch's **Screens** unit added, across the five screens it
/// touched.
///
/// Its other work is asserted where it belongs — the Images sentence on the
/// card's own case (`testImages_AHistoryCardShowsThreeTilesOfFour`), the
/// container check in the package (`ScreensHealthGuardTests`), the follow
/// state machine in `ScreensLyricsFollowTests`, the Images shapes in
/// `ScreensImagesShapeTests`. What is here is what needed a **new** case and
/// a running app: the drag that pauses Music's lyrics and the timer that
/// un-pauses it, the `warn again` announcement, the News chip row with no
/// bubble in it, and the three Images controls this app does not draw.
///
/// ### Three of these are about Images, and they are here rather than there
///
/// `StubHouseUITests+Images.swift` lost five cases when the composer, the
/// lane's cancel and the history delete came off the screen — all three
/// routes are admin-only on the plugin and 401 from every phone (the owner's
/// answer 41; `ImagesView.swift`'s header has the evidence). The cases that
/// replace them are **new**, so they are named `testScreens_…` and their
/// evidence is `screens-…`, which is how a train hands a result back to the
/// branch that wrote it. They call that file's own internal helpers
/// (`openImages`, `imagesPromptField`, `imagesTile`, `imagesScrollUntilBuilt`,
/// `StubImages`, `ImagesWords`) and edit none of them.
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
    /// being asked** (the owner's answer 35).
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
    /// `warn again, <the kind>` (the owner's answer 2).
    ///
    /// Two silenced kinds, so the case can say the two announcements differ:
    /// a label built from a constant would be the same string on both rows,
    /// which is the defect this answers. The **drawn** word did not change —
    /// Android's wording stays on screen and only the announcement grows —
    /// and that half is not assertable from a tree, for the reason written
    /// out at the foot of this case.
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

        // **The drawn word cannot be asserted from here, and this is where
        // that is written down.**
        //
        // This asked for `app.staticTexts["warn again"]` for one round, which
        // cannot pass: a SwiftUI `Button` whose label is a `Text` is **one**
        // accessibility element with no `StaticText` child. Train I001's tree
        // for this very panel shows it —
        // `att/testAiSettings_WarnAgainClearsOneKindAndLeavesTheOther/`
        // `aisettings-two-silenced-tree_…txt:35,37`:
        // `Button, …, identifier: 'aisettings-warn-again-calc',`
        // `label: 'warn again'`, with nothing under it. And after this change
        // the Button's own label is `warn again, <kind>`, so the bare three
        // words are on no element in any form (review 1, finding 2).
        //
        // The drawn word *is* untouched — `AiSettingsChatPanel.swift:134` is
        // still `Button(AiSettingsStrings.warnAgain)` — and the wording
        // itself is pinned against the Kotlin in the package, where it needs
        // no tree:
        // `AiSettingsStringsTests.testTheChatPanelSaysWhatTheKotlinSays`.
        //
        // What a tree *can* say is that **no row was left behind**: a kind
        // whose button never got its `accessibilityLabel` would announce the
        // bare three words, which is the defect answer 2 is about and the one
        // thing the two equalities above cannot see (they name their two
        // rows). On this seed both rows are silenced, so the count is zero.
        XCTAssertEqual(
            0,
            app.buttons.matching(
                NSPredicate(format: "label == %@", ScreensWords.warnAgain)
            ).count,
            "a `warn again` button still announces the bare three words, so one "
                + "row was left without its kind"
        )
    }

    // MARK: - Images draws nothing only an admin can use

    /// **The three admin-only controls are not on the screen** — and the
    /// screen is there, which is the half that makes the other half mean
    /// something.
    ///
    /// `POST …/generate` (`web.py:360`), `POST …/jobs/{id}/cancel` (`:339`)
    /// and `DELETE …/history/{id}` (`:651`) all carry
    /// `dependencies=ADMIN_ONLY` (`:149`), which classifies through
    /// `check_admin_request` (`domovoi/admin_auth.py:1245-1274`) — a Bearer
    /// session and the dashboard's cookie, never `X-Device-Token`. This app
    /// sends only the device token, so all three answered 401 from every
    /// phone, every time. The owner's answer 41: "admin-only things stay off
    /// both phones". `ImagesView.swift`'s header carries the whole argument
    /// and the one plugin change that would bring them back.
    ///
    /// ### Why the order of the assertions is the test
    ///
    /// An absence check on a screen that did not draw passes for the wrong
    /// reason, and so does one against a row a lazy list has not built. So
    /// this walks the page the way a person would:
    ///
    /// 1. the engine line, which only a completed `…/models` read can draw —
    ///    `openImages` already waits for it;
    /// 2. the **composer's** three words denied while the top of the page is
    ///    the window, because the composer was at the top and a page already
    ///    scrolled past it would deny them vacuously;
    /// 3. the history card scrolled until it is built, then the `delete`, the
    ///    confirmation's title and the confirmation's own sentence denied —
    ///    with the card's tiles and its `4 images, showing 3` asserted
    ///    present, so the denial is of a card that is really there.
    ///
    /// `testAudiobooks_NoControlPromisesWhatThisAppCannotDo` is the same
    /// shape, and this is the Images half of it.
    @MainActor
    func testScreens_ImagesOffersNothingOnlyAnAdminCanDo() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never drew `engine running`")
        evidence(app, "screens-40-images-readonly")

        // 1. The composer, at the top of the page where it used to be.
        XCTAssertFalse(
            imagesPromptField(app).exists,
            "the prompt field is drawn, and `POST …/generate` 401s from this phone"
        )
        for (what, label) in [
            ("generate", ImagesWords.generate),
            ("the model picker", ImagesWords.pickAModel),
        ] {
            XCTAssertEqual(
                0,
                app.buttons.matching(NSPredicate(format: "label == %@", label)).count,
                "\(what) is drawn, and the route behind it needs an admin session"
            )
        }
        // The picker's own menu wording, which nothing else on the screen
        // spells with two spaces either side of the separator: a picker drawn
        // without its `accessibilityLabel` would slip past the query above
        // and not past this one.
        XCTAssertFalse(
            containing(app, StubImages.recommended).exists,
            "the model menu's `recommended` wording is on screen"
        )
        // And no model is offered as a choice at all. The three catalog
        // labels, cased as the catalog cases them — the history card's meta
        // line says `sdxl-turbo`, the job's own id, so these three strings
        // belong to the picker and to nothing else on this screen
        // (`StubImages`' own note).
        for model in [
            StubImages.installedModel,
            StubImages.otherInstalledModel,
            StubImages.uninstalledModel,
        ] {
            XCTAssertEqual(
                0,
                app.buttons.matching(NSPredicate(format: "label == %@", model)).count,
                "`\(model)` is offered as a model to generate with"
            )
        }

        // 2. The lane's cancel. The running job is asserted present first —
        // the cancel was drawn only for an active job (`:347`), so a lane
        // with nothing in it would deny the button for the wrong reason.
        XCTAssertTrue(
            containing(app, StubImages.runningPrompt).exists,
            "the running job is not in the lane, so a missing cancel proves nothing"
        )
        XCTAssertEqual(
            0,
            app.buttons.matching(
                NSPredicate(format: "label == %@", ImagesWords.cancel)
            ).count,
            "the lane draws a cancel, and `…/jobs/{id}/cancel` 401s from this phone"
        )

        // 3. The history card's delete, on a card scrolled until it is built.
        let tile = imagesTile(app, showing: StubImages.tileOne)
        XCTAssertTrue(
            imagesScrollUntilBuilt(app, tile),
            "the history card never built, so nothing below was really checked"
        )
        XCTAssertTrue(
            app.staticTexts[ImagesWords.fourImagesShowingThree].exists,
            "the card that should carry the count does not, so this is the wrong card"
        )
        evidence(app, "screens-41-images-history-no-delete")
        XCTAssertEqual(
            0,
            app.buttons.matching(
                NSPredicate(format: "label == %@", ImagesWords.delete)
            ).count,
            "a history card draws a delete, and `DELETE …/history/{id}` 401s here"
        )
        // And nothing opened the confirmation either — a dialog on a screen
        // with no button to open it would be a scrim nobody asked for.
        XCTAssertFalse(
            app.staticTexts[ImagesWords.deleteTitle].exists,
            "the delete confirmation is on screen"
        )
        XCTAssertFalse(
            app.staticTexts[StubImages.deleteFourImages].exists,
            "the confirmation's sentence is on screen"
        )
    }

    /// A running job shows `status_text · pct`, and has **no cancel**.
    ///
    /// The progress half is
    /// `testImages_ARunningJobShowsItsProgressAndCanBeCancelled`'s, which
    /// went with the control its name promised: the composed line, not either
    /// half of it, because `:362-366` joins them and a screen drawing only
    /// `status_text` would pass a check for either one alone.
    ///
    /// The cancel is denied here as well as in the case above, and it is not
    /// a duplicate: that one walks the whole screen and this one is the lane
    /// row's own case, so a lane that grew a cancel back fails the case named
    /// after the row it is on.
    @MainActor
    func testScreens_ImagesARunningJobShowsItsProgressAndNoCancel() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        XCTAssertTrue(
            containing(app, StubImages.runningProgress).waitForExistence(timeout: 20),
            "the progress line is not composed as `status_text · pct%`"
        )
        evidence(app, "screens-42-images-progress")
        XCTAssertEqual(
            0,
            app.buttons.matching(
                NSPredicate(format: "label == %@", ImagesWords.cancel)
            ).count,
            "the running job's row draws a cancel this phone cannot send"
        )
        // The failed row is still beside it: a lane that drew only the
        // running job would pass the two assertions above.
        XCTAssertTrue(
            containing(app, StubImages.failedPrompt).exists,
            "the failed job left the lane — a dead generation nobody can see"
        )
    }

    /// A house that has generated nothing: the engine line, and **nothing
    /// else**.
    ///
    /// `testImages_WithNothingGeneratedYetTheComposerIsStillThere`'s, with
    /// its one premise inverted — the composer is not there on any house now.
    /// What survives unchanged is the part that was always the point: the
    /// `history` label exists only when there is history (`:247`,
    /// ``ImagesModel/showsHistory``), so a screen that drew the label over
    /// nothing would be a section that is usually empty.
    ///
    /// The guard is one `if` over the label **and** the cards
    /// (`ImagesScreen.kt:247-268`), so a widened guard shows both: a heading,
    /// and whatever rows the house did not send. Asserting the label alone
    /// leaves a screen that drew the section's contents from a stale read
    /// unmentioned.
    @MainActor
    func testScreens_ImagesWithNothingGeneratedYetIsTheEngineLineOnly() throws {
        let (app, landed) = openImages(how: "empty")
        evidence(app, "screens-43-images-empty")
        XCTAssertTrue(landed, "the engine line is missing on an empty house")
        XCTAssertFalse(
            imagesPromptField(app).exists,
            "an empty house draws a prompt field, and nothing can send it"
        )
        XCTAssertFalse(
            app.staticTexts[ImagesWords.history].exists,
            "the history label is drawn over no history"
        )
        XCTAssertFalse(
            containing(app, StubImages.historyPromptWord).exists,
            "a history card is on a house that has generated nothing"
        )
        XCTAssertFalse(
            containing(app, StubImages.runningPrompt).exists,
            "the lane has a job on a house with none"
        )
    }

    // MARK: - D39: the News chip row has no avatar bubble

    /// The person chips are plain pills: **no initial, anywhere in the row**
    /// (the owner's answer 39).
    ///
    /// The bubble was the row's only colour and the last thing in it an
    /// auditor could sample and misattribute — five `Contrast failed` over
    /// four trains, written up in `StubHouseUITests+News.swift`.
    ///
    /// **This is a divergence from Android, not a port of it.** Answer 39's
    /// question said dropping the bubble "is Android's shape
    /// (`NewsScreen.kt`)"; it is not. `NewsScreen.kt:139` draws
    /// `AvatarBubble(p.name, 20)` inside the chip's `Row` and `:185` draws
    /// the rail's `AvatarBubble(p.name, 32)`. The owner's answer is still the
    /// spec, and the Android half of it is logged in this unit's
    /// DECISIONS.md — the same way answers 2 and 24 got one.
    ///
    /// Asserted three ways, because each catches a different way of getting
    /// it wrong, and the first two cannot see what the third does:
    ///
    /// 1. the chips expose no text **leaf** at all
    ///    (``newsChipRowIsOneElement(_:)``, which the audit case also pins) —
    ///    a bubble back **inside** the button's label;
    /// 2. no **text or image** element is drawn over a chip: none of their
    ///    frames lies inside a chip's frame. That is the exact shape round 3
    ///    shipped and this one deleted — `StaticText 'K' {{28,225.3},{28,28}}`
    ///    inside `Button 'Jordan' {{16,217.3},{113,44}}` — and within those
    ///    two element types it is **letter-blind**: a bubble drawing a glyph,
    ///    a digit, a word or a symbol image fails it whatever the character
    ///    is.
    ///
    ///    **What it cannot see**, because XCUITest cannot: a bubble that
    ///    draws nothing nameable — a bare `Circle().fill(…)`, or an `Image`
    ///    with `accessibilityHidden(true)` — is in no query's results, so it
    ///    contributes no element to this reading and no text leaf to reading
    ///    1 either. Nothing on this PC can close that, and it is **not** the
    ///    shape that was removed: ``NewsAvatarBubble`` drew
    ///    ``NewsPerson/initial`` as a `Text`, which is the `StaticText 'K'`
    ///    above — round 3 drew that bubble as a decoration over the pill and
    ///    it was still in the tree as that leaf, which is how this reading
    ///    came to exist. An unlabelled fill would be caught by a screenshot
    ///    diff and by nothing in this file (fix-pass review should 2);
    /// 3. no initial of the roster exists as a standalone element.
    ///    ``ScreensWords/newsInitials`` is the **whole** roster's, because
    ///    the chip row is driven by the People seed's four people and not by
    ///    `fixtures/news.py`'s two (review 2, finding 5): a bubble left on
    ///    either `Alex` chip used to pass both halves of this case.
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

        // 2. Nothing drawn over a chip. The chips' own frames come from the
        //    identifiers `newsChipRowIsOneElement` just proved exist, so a
        //    missing chip cannot make this half vacuous.
        //
        //    `allElementsBoundByIndex` once over each element type, not a
        //    query per chip: one snapshot each, with `exists` asked of every
        //    element because one resolved from a stale snapshot answers a
        //    zero frame.
        //
        //    **Text leaves and images both.** The bubble that was removed
        //    drew the initial as a `Text`, so `staticTexts` is what catches
        //    the shape this case is about coming back — but a bubble redrawn
        //    as an `Image` (an SF Symbol `person.circle.fill`, say) is an
        //    image element and no text leaf at all, and reading 1 counts
        //    `chip.staticTexts` only (`StubHouseUITests+News.swift`). One
        //    query more is the difference between "no initial is drawn" and
        //    "nothing is drawn on the pill". The chips themselves contain
        //    neither: their label is a single `Text` and that is the whole
        //    one-element fix (``NewsPeopleChipRow``), so an image inside a
        //    chip's frame is by itself the defect.
        //
        //    An explicit result type and explicit labels on the tuple: a
        //    multi-statement closure whose branches are a tuple and `nil` is
        //    one Swift will not always infer, and an unlabelled tuple is not
        //    always convertible to a labelled one in a return position.
        let chips: [(name: String, box: CGRect)] = ScreensWords.newsChipPeople
            .compactMap { (name: String) -> (name: String, box: CGRect)? in
                let chip = app.buttons["news-chip-\(name)"]
                guard chip.exists else { return nil }
                return (name: name, box: chip.frame)
            }
        XCTAssertEqual(
            ScreensWords.newsChipPeople.count, chips.count,
            "a chip is missing, so the overlay half of this case checked nothing"
        )
        let drawn = app.staticTexts.allElementsBoundByIndex
            + app.images.allElementsBoundByIndex
        for text in drawn where text.exists {
            let box = text.frame
            // A zero-area frame is in no rectangle in any useful sense, and
            // `CGRect.contains` says a zero-height rect is contained by
            // anything that spans its origin.
            guard box.width > 0, box.height > 0 else { continue }
            for chip in chips where chip.box.contains(box) {
                XCTFail(
                    "something is drawn over the \(chip.name) chip — "
                    + "`\(text.label)` at \(box), which is the shape an "
                    + "avatar bubble drawn outside the button's label leaves "
                    + "behind"
                )
            }
        }

        // 3. And no initial of the roster stands on its own anywhere.
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
        let app = freshApp()
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
    /// half of what each row now announces.
    ///
    /// **Not assertable as drawn text.** A `Button` with a `Text` label has
    /// no `StaticText` child, so the word is only ever read off the Button's
    /// own label — which now ends with the kind. It is used here as the
    /// prefix the announcements are built from, and as the bare label that
    /// must appear on **no** button (a row whose `accessibilityLabel` was
    /// forgotten).
    static let warnAgain = "warn again"

    /// The people the chip row draws, in `last_seen_at DESC` order — the
    /// **People** seed's roster (`fixtures/people.py:95,105,115,128`), which
    /// is what News defers to. Not `fixtures/news.py`'s, and not a sample:
    /// `newsChipRowIsOneElement(_:)` has iterated these same four since it
    /// was written, and the leaf that cost the fifth `Contrast failed`
    /// belonged to the last of them.
    ///
    /// The fourth name's trailing space is the seed's, kept exactly: the
    /// chip's identifier is built from `NewsPerson.displayName`, so a name
    /// trimmed here would look for a chip that is not there.
    static let newsChipPeople = ["Jordan", "Chevy", "Alex", "alex "]

    /// The initials of that whole roster — what an avatar bubble would have
    /// drawn (``NewsPerson/initial``: trimmed, first character, uppercased,
    /// so both `Alex` and `alex ` give `A`). Deduplicated, and all of them
    /// rather than a sample: round 2 of this unit denied `K` and `C` only,
    /// described as "the two people in `fixtures/news.py`", so a bubble left
    /// on either `Alex` chip passed (review 2, finding 5).
    static let newsInitials = ["J", "C", "A"]
}
