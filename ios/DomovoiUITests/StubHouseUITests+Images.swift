import XCTest

/// The Images screen, against the stub house.
///
/// Every case is `testImages_<What>` and every piece of evidence is
/// `images-…`, which is how a train hands these results back to the branch
/// that wrote them (NIGHT-RULES §2).
///
/// ### The one thing to know before reading a failure here
///
/// **Images is the first route in this app that is gated on a capability.**
/// `Route.requiredCapability` for `.images` is `capImagegen`, and
/// `Capabilities.has` resolves a capability from
/// `plugins[].android_capabilities` (`Capabilities.swift:79-81`). The stub
/// house answers `/api/capabilities` with a **flat** `{"capabilities": [...]}`
/// list (`stubhouse.py:706-712`) that iOS has no key for and does not decode,
/// so until that route also publishes `plugins`, `has("imagegen")` is false,
/// the route is invisible, and a pushed Images lands back on Home.
///
/// Every case except ``testImages_WithoutThePluginTheRouteIsNotOffered()``
/// therefore fails on an unfixed house, and it fails in one recognisable way:
/// `openImages` never finds its marker and prints the tree, which will show
/// Home. That is the *house* being wrong and not the app — the fix is about
/// six lines in `stubhouse.py`, written out in
/// `design-notes/ios/overnight/images/DECISIONS.md` under "Needs a shared
/// change", and `fixtures/images.py` already carries the `PLUGIN` dict for
/// the collector to pick up. Nothing in these cases should be weakened to
/// make them pass before then.
///
/// ### What is not tested here, and where it is tested instead
///
/// **A server switch inside the generate round trip.** The only way to
/// change the address from a UI case is the app's connection sheet, which
/// NIGHT-RULES §1 forbids on a Simulator — it starts a LAN discovery sweep
/// across Kamron's real home network. The case exists and switches **inside**
/// the round trip in the package, where the whole thing is driveable:
/// `ImagesTests.testAServerSwitchMidGenerateLandsNowhere`, with a parked
/// request. [[read-after-await-wrong-key]].
extension StubHouseUITests {

    // MARK: - the house, by the words on its screen

    /// The stub's own seed (`fixtures/images.py`), spelt out here rather than
    /// imported, as every other string in this file is: a query that silently
    /// followed a renamed constant would stop testing what it claims to.
    enum StubImages {

        /// Two models are installed…
        static let installedModel = "SDXL Turbo"
        static let otherInstalledModel = "Flux Schnell"
        /// …and this one is **not**. Its label must appear nowhere on screen:
        /// the picker filters on `installed` (`ImagesScreen.kt:148`) and
        /// nothing on this screen can install a model.
        static let uninstalledModel = "HiDream E1"
        static let recommended = "SDXL Turbo  ·  recommended"

        /// The lane: a running job and a failed one.
        static let runningPrompt = "a brass orrery on a velvet desk"
        static let runningProgress = "sampling step 7 of 20 · 42%"
        static let failedPrompt = "a cathedral of folded paper"
        static let failedError = "the engine ran out of memory"

        /// **The discriminating row.** `completed` is not `pending`,
        /// `running` or `failed`, so `laneJobs` (`:180-183`) must not hold
        /// it, and `quicksilver` is in no other row in the fixture — so the
        /// word being anywhere on screen means the lane filter was widened.
        static let completedNotInLane = "quicksilver"

        /// History row 21: four images, full params, a prompt that wraps.
        static let historyPromptWord = "kingfisher"
        static let historyMeta = "sdxl-turbo · 1024 × 1024 · 28 steps · seed 773451"
        /// The three tiles that are drawn, and the fourth that is not. The
        /// first name carries a **space**, which is what proves the path
        /// escape end to end.
        static let tileOne = "kingfisher 01.png"
        static let tileTwo = "kingfisher-02.png"
        static let tileThree = "kingfisher-03.png"
        static let fourthImage = "kingfisher-04.png"
        /// All four are counted by the confirmation, which is what is being
        /// deleted (`:275`), not the three on the card.
        static let deleteFourImages =
            "Delete this generation and its 4 images? This can't be undone."

        /// History row 22: a `width` with **no `height`**, so the meta line
        /// has the steps and **no `×`** (`:387`).
        static let widthOnlyMeta = "flux-schnell · 4 steps"
        static let widthOnlyPrompt = "a tin robot watering a window box"

        /// What `…/models` says when the fixture is set to `unavailable`.
        static let pluginSilent = "the plugin is not answering"

        /// The band (`PluginFrame.kt:124`, `:144`, `:151`).
        static let pluginName = "Image Generation"
        static let pluginBy = "by Coders Farm"
        static let pluginBadge = "plugin"
    }

    /// The app's own words, spelt out for the same reason.
    enum ImagesWords {
        static let engineRunning = "engine running"
        static let notInstalledTitle = "engine not installed"
        static let offlineTitle = "engine offline"
        static let unavailableTitle = "image generation unavailable"
        static let promptHint = "describe the image…"
        static let generate = "generate"
        static let pickAModel = "pick a model"
        static let history = "history"
        static let cancel = "cancel"
        static let delete = "delete"
        static let deleteTitle = "delete generation"
        static let deleted = "deleted"
        static let close = "close"
    }

    // MARK: - getting there

    /// Reset the fixtures, choose what the reads answer, and push Images.
    ///
    /// **Reset first, always.** The cases share one house for the whole run
    /// and the generate and delete cases mutate it, so a case that relied on
    /// the seed without resetting would pass or fail on which case ran before
    /// it.
    ///
    /// **What it waits for.** `engine running` at the ordinary size, because
    /// that line cannot exist until `…/models` has answered and been decoded
    /// — a marker that only a *completed read* can produce, which is the
    /// lesson `houseName(in:)` carries. For the three states where that line
    /// is a card instead, the caller passes `waitingFor:`. At AX5 the header
    /// and the engine line fill the window, so the marker is the same one:
    /// it is above everything else on the page.
    @MainActor
    func openImages(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        how: String = "normal",
        plugin: Bool = true,
        waitingFor marker: String? = nil,
        delay: Double? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        control("reset-fixtures", "{}", port: 6601, what: "the images fixtures")
        if how != "normal" {
            control("images-state", "{\"how\":\"\(how)\"}", port: 6601,
                    what: "what the imagegen reads answer")
        }
        if !plugin {
            control("images-plugin", "{\"present\":false}", port: 6601,
                    what: "whether the manifest has the imagegen plugin")
        }
        let app = launch(mode: mode, push: "Images", pushDelay: delay ?? Self.pushDelay)
        let wanted = marker ?? ImagesWords.engineRunning
        let found = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", wanted))
            .firstMatch
        let landed = found.waitForExistence(timeout: 40)
        if !landed {
            // The tree, not a sentence about the element: "Images did not
            // open" is a claim about something that is not there, and the
            // only useful answer is what *is* — which on an unfixed stub
            // house is Home. [[ios-stub-house]].
            print("=== IMAGES DID NOT OPEN (waited for \(wanted)) — tree follows ===")
            print(app.debugDescription)
        }
        return (app, landed)
    }

    /// Type into the prompt field.
    ///
    /// Matched by label across every element type, as `composerField` is: it
    /// is a `TextField` with a vertical axis so that it grows with the type
    /// size, and a vertically-growing SwiftUI field does not always report as
    /// `.textField` in the query tree.
    @MainActor
    func imagesPromptField(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", ImagesWords.promptHint))
            .firstMatch
    }

    /// A button **inside the delete confirmation**, when the screen behind it
    /// has one by the same name.
    ///
    /// Both of the dialog's words collide with the screen's: the lane's
    /// cancel is an icon button labelled `cancel` (Android's own
    /// `contentDescription`, `:353`) and every history card has a `delete`
    /// (`:431`). `firstMatch` takes whichever came first in the tree, and a
    /// tap on the row's `delete` while the dialog is up would **open a second
    /// confirmation** rather than answer the one on screen — which reads
    /// exactly like a dialog whose buttons do nothing.
    ///
    /// `inSheet` cannot tell them apart here, and that is worth saying:
    /// ``DialogCard`` is a `ZStack` overlay and not a presented sheet, so the
    /// screen behind it stays hittable. What does tell them apart is the
    /// dialog's own title — the card's buttons are the ones **below** it.
    @MainActor
    func imagesDialogButton(_ app: XCUIApplication, _ label: String) -> XCUIElement? {
        let title = app.staticTexts[ImagesWords.deleteTitle].firstMatch
        guard title.exists else { return nil }
        let floor = title.frame.maxY
        let all = app.buttons.matching(NSPredicate(format: "label == %@", label))
        var best: XCUIElement?
        var top = CGFloat.greatestFiniteMagnitude
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists, candidate.frame.minY >= floor else { continue }
            // The nearest one under the title, so a history card further down
            // the page cannot be mistaken for the card's own button.
            if candidate.frame.minY < top {
                top = candidate.frame.minY
                best = candidate
            }
        }
        if best == nil {
            print("=== NO DIALOG `\(label)` under the title — tree follows ===")
            print(app.debugDescription)
        }
        return best
    }

    /// The `generate` button, and **not the keyboard key** that may share its
    /// label.
    ///
    /// `firstMatch` takes a keyboard key when one has the same word, and a
    /// key is hittable, so hittability cannot tell them apart. The keyboard's
    /// top edge can: anything above it is this screen's.
    /// (`StubHouseUITests.swift`'s own note on `firstMatch`.)
    @MainActor
    func imagesGenerateButton(_ app: XCUIApplication) -> XCUIElement? {
        let ceiling = app.keyboards.firstMatch.exists
            ? app.keyboards.firstMatch.frame.minY
            : CGFloat.greatestFiniteMagnitude
        let all = app.buttons.matching(NSPredicate(format: "label == %@", ImagesWords.generate))
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists, candidate.frame.minY < ceiling else { continue }
            return candidate
        }
        return nil
    }

    // MARK: - the screen draws the house

    /// The engine line, the band, and the plugin's name — the three things
    /// that can only be on screen if the manifest decoded **and** `…/models`
    /// answered.
    ///
    /// A failure means one of: the band is missing, `plugins` in the manifest
    /// is not decoded, or the engine `when` fell to the wrong arm
    /// (`ImagesScreen.kt:190-212`).
    @MainActor
    func testImages_DrawsTheEngineStateAndThePluginBand() throws {
        let (app, landed) = openImages()
        evidence(app, "images-band")
        XCTAssertTrue(landed, "Images never drew `engine running`")
        XCTAssertTrue(
            containing(app, StubImages.pluginName).exists,
            "the band does not name the plugin"
        )
        XCTAssertTrue(
            containing(app, StubImages.pluginBy).exists,
            "the band does not say who publishes it"
        )
        XCTAssertTrue(
            app.staticTexts[StubImages.pluginBadge].exists,
            "the `plugin` badge is missing, so nothing says this screen is a plugin's"
        )
    }

    /// With the plugin absent from the manifest, Images is **not offered**.
    ///
    /// `Route.visibleWith` hides a route whose capability the house does not
    /// declare (`Route.swift:203-209`), and
    /// `NavigationState.capabilitiesChanged` empties the path
    /// (`ShellNavigation.swift:138-142`), so a pushed Images ends up back at
    /// Home. A failure here means a plugin screen is rendering against a
    /// house that has no plugin.
    ///
    /// The assertion is on the **quiet note or Home**, not on an absence
    /// alone: "the screen is not there" passes on a house that answered
    /// nothing at all, which is why the house's own name is asserted too.
    @MainActor
    func testImages_WithoutThePluginTheRouteIsNotOffered() throws {
        let (app, landed) = openImages(plugin: false, waitingFor: "stub house")
        evidence(app, "images-no-plugin")
        XCTAssertTrue(landed, "the app never reached a house at all")
        XCTAssertFalse(
            containing(app, ImagesWords.engineRunning).exists,
            "the Images screen drew itself on a house with no imagegen plugin"
        )
        XCTAssertFalse(
            containing(app, StubImages.runningPrompt).exists,
            "the plugin's jobs are on screen although the house declares no plugin"
        )
    }

    // MARK: - choosing a model

    /// The picker offers the two installed models and **not** the third.
    ///
    /// A failure means the `installed` filter at `ImagesScreen.kt:148` was
    /// dropped, and the picker would be offering a model this house cannot
    /// run — a control that cannot do what it says.
    @MainActor
    func testImages_ThePickerOffersOnlyInstalledModels() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        let picker = app.buttons[ImagesWords.pickAModel].firstMatch
        XCTAssertTrue(picker.waitForExistence(timeout: 20), "there is no model picker")
        picker.tap()

        // The recommended one reads with its suffix (`:318`), both spaces
        // either side of the separator.
        let recommended = app.buttons[StubImages.recommended].firstMatch
        XCTAssertTrue(
            recommended.waitForExistence(timeout: 10),
            "the recommended model is not marked"
        )
        XCTAssertTrue(
            app.buttons[StubImages.otherInstalledModel].firstMatch.exists,
            "the second installed model is not offered"
        )
        evidence(app, "images-picker")
        XCTAssertFalse(
            containing(app, StubImages.uninstalledModel).exists,
            "a model this house has not installed is on offer"
        )

        // Choosing it is what the picker is for, and the field has to say so
        // afterwards: a menu that opens and changes nothing is the half of
        // this control a presence check would miss.
        recommended.tap()
        XCTAssertTrue(
            app.buttons[ImagesWords.pickAModel].firstMatch
                .waitForExistence(timeout: 10),
            "the picker vanished after a choice"
        )
    }

    // MARK: - generating

    /// `generate` is off with no prompt and on with one — `:231`'s four
    /// conjuncts, through the two a UI case can move.
    @MainActor
    func testImages_GenerateIsOffWithoutAPromptAndOnWithOne() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        guard let button = imagesGenerateButton(app) else {
            evidence(app, "images-no-generate")
            return XCTFail("there is no generate button")
        }
        XCTAssertFalse(button.isEnabled, "generate is live with an empty prompt")

        let field = imagesPromptField(app)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "there is no prompt field")
        field.tap()
        // Three spaces first: `prompt.isNotBlank()` is Kotlin's, and **blank**
        // rather than empty, so whitespace must not enable it. This is the
        // conjunct a test of "empty vs typed" alone would never reach.
        app.typeText("   ")
        XCTAssertFalse(
            imagesGenerateButton(app)?.isEnabled ?? true,
            "generate is live for a prompt of nothing but spaces"
        )
        app.typeText("a lantern in fog")
        evidence(app, "images-generate-enabled")
        XCTAssertTrue(
            imagesGenerateButton(app)?.isEnabled ?? false,
            "generate is still off with a prompt typed"
        )
    }

    /// Typing a prompt and tapping `generate` puts a new `pending` job in the
    /// lane, **with that prompt on it**.
    ///
    /// The prompt is the discriminating part: the fixture records the body as
    /// it arrived, so a POST with the wrong keys queues a job with an empty
    /// prompt and this fails. A failure also covers `jobs.refresh()` after
    /// the write being lost.
    @MainActor
    func testImages_GeneratingAddsAJobToTheLane() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        let field = imagesPromptField(app)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "there is no prompt field")
        field.tap()
        let asked = "a paper submarine in a bathtub"
        app.typeText(asked)
        guard let button = imagesGenerateButton(app) else {
            evidence(app, "images-no-generate-2")
            return XCTFail("there is no generate button")
        }
        button.tap()

        let queued = containing(app, asked)
        let appeared = queued.waitForExistence(timeout: 30)
        evidence(app, "images-generated")
        XCTAssertTrue(appeared, "the new job never reached the lane")
        XCTAssertTrue(
            containing(app, "pending").exists,
            "the new job is on screen without its status word"
        )
        // **The prompt is not cleared** — `ImagesScreen.kt:156-170` clears
        // neither it nor the model choice, and a second go at a prompt one
        // word different is the ordinary next thing somebody does.
        XCTAssertTrue(
            containing(app, asked).exists,
            "something cleared the prompt that Android keeps"
        )
    }

    // MARK: - the lane

    /// The lane holds what is still happening **and what failed**, and not
    /// what finished.
    ///
    /// One of the two cases a sabotage of the lane filter must fail.
    @MainActor
    func testImages_TheLaneHoldsOnlyWhatIsStillHappening() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        XCTAssertTrue(
            containing(app, StubImages.runningPrompt).exists,
            "the running job is not in the lane"
        )
        XCTAssertTrue(
            containing(app, StubImages.failedPrompt).exists,
            "the failed job left the lane — a dead generation nobody can see"
        )
        evidence(app, "images-lane")
        // Seeded so that this word exists nowhere else in the fixture: if the
        // filter were widened to every job, the completed one would be here.
        XCTAssertFalse(
            containing(app, StubImages.completedNotInLane).exists,
            "a completed job is in the live lane"
        )
    }

    /// A running job shows `status_text · pct` and can be cancelled.
    ///
    /// The composed line, not either half: `:362-366` joins them, and a
    /// screen drawing only `status_text` would pass a check for either one
    /// alone.
    @MainActor
    func testImages_ARunningJobShowsItsProgressAndCanBeCancelled() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        XCTAssertTrue(
            containing(app, StubImages.runningProgress).waitForExistence(timeout: 20),
            "the progress line is not composed as `status_text · pct%`"
        )
        let cancel = app.buttons[ImagesWords.cancel].firstMatch
        XCTAssertTrue(cancel.waitForExistence(timeout: 20), "the running job has no cancel")
        evidence(app, "images-progress")
        cancel.tap()

        XCTAssertTrue(
            waitUntilGone(containing(app, StubImages.runningPrompt), timeout: 30),
            "the cancelled job is still in the lane"
        )
        // The failed row is **still there**: a cancel that removed the wrong
        // id, or cleared the lane, would pass an assertion about the running
        // row alone.
        XCTAssertTrue(
            containing(app, StubImages.failedPrompt).exists,
            "cancelling one job took another with it"
        )
    }

    /// A failed job shows the engine's own words.
    ///
    /// The failure mode this catches is `error` being decoded and silently
    /// not drawn, which makes a dead job look idle.
    @MainActor
    func testImages_AFailedJobShowsTheEnginesOwnWords() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        evidence(app, "images-failed")
        XCTAssertTrue(
            containing(app, StubImages.failedError).waitForExistence(timeout: 20),
            "the engine said why it failed and the screen does not"
        )
        XCTAssertTrue(
            containing(app, "failed").exists,
            "the failed job has no status word, so nothing marks it as failed"
        )
        // A failed job offers **no cancel** (`:347`): a control that cannot
        // do what it says is not drawn, and `ImagesModel.cancel` refuses it
        // as well. One cancel is on screen — the running job's — and not two.
        XCTAssertEqual(
            1,
            app.buttons.matching(NSPredicate(format: "label == %@", ImagesWords.cancel)).count,
            "a failed job is offering a cancel"
        )
    }

    // MARK: - the two wrappers

    /// `jobs` and `history` are read from **their own keys**.
    ///
    /// `GET …/jobs` answers `{"jobs": […]}` and `GET …/history` answers
    /// `{"history": […]}` — two wrappers, two keys, one row type
    /// (`ImagesScreen.kt:120`, `:123`). Reading one with the other's key
    /// decodes to an empty list, which on screen looks exactly like "nothing
    /// has happened yet". Three of Home's four reads did that for a week.
    ///
    /// The other of the two cases a sabotage must fail.
    @MainActor
    func testImages_HistoryAndTheJobLaneAreReadFromTheirOwnKeys() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        // A word only a lane row has…
        XCTAssertTrue(
            containing(app, StubImages.runningPrompt).exists,
            "the lane decoded to nothing — `jobs` was read with the wrong key"
        )
        XCTAssertTrue(
            app.staticTexts[ImagesWords.history].waitForExistence(timeout: 20),
            "there is no history section, so `history` decoded to nothing"
        )
        // …and a word only a history row has. The foot of a lazy list is
        // **unbuilt**, not off screen, so it is scrolled to rather than
        // looked for.
        let historyRow = containing(app, StubImages.historyPromptWord)
        XCTAssertTrue(
            scrollUntilBuilt(app, historyRow),
            "no history row is on screen — `history` was read with the wrong key"
        )
        evidence(app, "images-two-keys")
    }

    // MARK: - a history card

    /// The meta line, and the branch where half a size is not a size.
    ///
    /// Row 21 has both a width and a height; row 22 has a width and **no**
    /// height, so its line has the steps and no `×`. A failure means the
    /// "both or neither" guard at `:387` was lost — which would print
    /// `512 × 0`, or a dangling separator.
    @MainActor
    func testImages_AHistoryCardShowsItsModelAndSettings() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        let full = app.staticTexts[StubImages.historyMeta]
        XCTAssertTrue(
            scrollUntilBuilt(app, full),
            "the full meta line is not `model · w × h · steps · seed`"
        )
        let widthOnly = app.staticTexts[StubImages.widthOnlyMeta]
        XCTAssertTrue(
            scrollUntilBuilt(app, widthOnly),
            "the width-only row's meta line is not `model · steps`"
        )
        evidence(app, "images-meta")
        // The discriminating negative: with the guard gone, the width-only
        // row would carry a `×` of its own.
        let stray = app.descendants(matching: .any).matching(
            NSPredicate(format: "label CONTAINS %@ AND label CONTAINS %@", "flux-schnell", "×")
        )
        XCTAssertEqual(
            0,
            stray.count,
            "a row with a width and no height drew a size anyway"
        )
    }

    /// Three tiles of four, and no affordance for the fourth.
    ///
    /// A failure means `take(3)` became `take(4)`, or a phantom "+1 more"
    /// appeared — a control that opens nothing.
    @MainActor
    func testImages_AHistoryCardShowsThreeTilesOfFour() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        let first = app.buttons[StubImages.tileOne]
        XCTAssertTrue(scrollUntilBuilt(app, first), "the first tile is not on the card")
        XCTAssertTrue(app.buttons[StubImages.tileTwo].exists, "the second tile is missing")
        XCTAssertTrue(app.buttons[StubImages.tileThree].exists, "the third tile is missing")
        evidence(app, "images-tiles")
        XCTAssertFalse(
            containing(app, StubImages.fourthImage).exists,
            "a fourth picture is drawn, which Android does not draw"
        )
    }

    /// Tapping a tile opens the picture.
    ///
    /// **The file name carries a space**, so this is also the end-to-end
    /// proof of the one escape this screen depends on: `Uri.encode` escapes
    /// `/` and a space, the fixture 404s any name no generation produced, and
    /// the bytes only arrive if the path was escaped the way Android escapes
    /// it (`ImagesPaths.file`). A failure means the tile is not a button, or
    /// the bytes were fetched without the token and 401'd.
    @MainActor
    func testImages_TappingATileOpensThePicture() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        let tile = app.buttons[StubImages.tileOne]
        XCTAssertTrue(scrollUntilBuilt(app, tile), "the first tile is not on the card")
        tile.tap()

        // The sheet's title is the file's own name; `close` is its way out,
        // and together they are something only the sheet draws.
        let closer = app.buttons[ImagesWords.close].firstMatch
        XCTAssertTrue(closer.waitForExistence(timeout: 30), "no picture sheet opened")
        evidence(app, "images-picture")
        XCTAssertTrue(
            containing(app, StubImages.tileOne).exists,
            "the sheet does not name the picture it is showing"
        )
        // The picture itself: the fixture serves a real PNG, so a sheet that
        // could not fetch or decode it would be showing a sentence instead.
        // `inSheet` is not needed — the screen behind is not drawing images.
        XCTAssertTrue(
            app.images.firstMatch.waitForExistence(timeout: 30),
            "the sheet opened and there is no picture in it"
        )
        closer.tap()
        XCTAssertTrue(waitUntilGone(closer, timeout: 20), "the sheet would not close")
    }

    // MARK: - deleting

    /// Deleting asks first, cancelling keeps the row, confirming removes it.
    ///
    /// The sentence is asserted in full because the **count** in it is the
    /// part that has been wrong on both platforms: it is every image in the
    /// generation (`:275`), four here, not the three tiles on the card.
    @MainActor
    func testImages_DeletingAGenerationAsksFirst() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        let row = containing(app, StubImages.historyPromptWord)
        XCTAssertTrue(scrollUntilBuilt(app, row), "the history row is not on screen")

        let bin = app.buttons[ImagesWords.delete].firstMatch
        XCTAssertTrue(bin.waitForExistence(timeout: 20), "the history card has no delete")
        bin.tap()

        let asked = app.staticTexts[StubImages.deleteFourImages]
        XCTAssertTrue(
            asked.waitForExistence(timeout: 20),
            "the confirmation does not say what is being deleted, or counts it wrong"
        )
        evidence(app, "images-delete-ask")
        XCTAssertTrue(
            containing(app, ImagesWords.deleteTitle).exists,
            "the dialog has no title"
        )

        // Cancelling keeps the row. A destructive dialog whose cancel also
        // deletes is the whole reason to test both arms.
        guard let noThanks = imagesDialogButton(app, ImagesWords.cancel) else {
            evidence(app, "images-delete-no-cancel")
            return XCTFail("the confirmation has no cancel")
        }
        noThanks.tap()
        XCTAssertTrue(waitUntilGone(asked, timeout: 20), "the dialog stayed up after cancel")
        XCTAssertTrue(
            containing(app, StubImages.historyPromptWord).exists,
            "cancelling the confirmation deleted the generation anyway"
        )

        // Now confirm.
        app.buttons[ImagesWords.delete].firstMatch.tap()
        XCTAssertTrue(asked.waitForExistence(timeout: 20), "the dialog did not come back")
        // The dialog's own `delete`, which is the one **below the dialog's
        // title**: the row's button is behind the scrim and still in the tree,
        // and tapping it would open a second confirmation instead of
        // answering this one.
        guard let confirm = imagesDialogButton(app, ImagesWords.delete) else {
            evidence(app, "images-delete-no-confirm")
            return XCTFail("the confirmation has no destructive button")
        }
        confirm.tap()

        XCTAssertTrue(
            waitUntilGone(containing(app, StubImages.historyPromptWord), timeout: 30),
            "the generation is still in history after a confirmed delete"
        )
        evidence(app, "images-deleted")
        XCTAssertTrue(
            containing(app, ImagesWords.deleted).exists
                || containing(app, StubImages.widthOnlyPrompt).exists,
            "neither the toast nor the remaining row is on screen, so nothing was re-read"
        )
    }

    // MARK: - nothing yet, and nothing answering

    /// An engine that is running and a house that has generated nothing.
    ///
    /// The empty state is **not** an empty screen: the header, the engine
    /// line and the composer are all there, and `history` is not — the label
    /// exists only when there is history (`:247`). A screen that drew the
    /// label over nothing would be a section that is usually empty.
    @MainActor
    func testImages_WithNothingGeneratedYetTheComposerIsStillThere() throws {
        let (app, landed) = openImages(how: "empty")
        evidence(app, "images-empty")
        XCTAssertTrue(landed, "the engine line is missing on an empty house")
        XCTAssertTrue(
            imagesPromptField(app).waitForExistence(timeout: 20),
            "an empty house has no prompt field, so nothing can be generated"
        )
        XCTAssertFalse(
            app.staticTexts[ImagesWords.history].exists,
            "the history label is drawn over no history"
        )
        XCTAssertFalse(
            containing(app, StubImages.runningPrompt).exists,
            "the lane has a job on a house with none"
        )
    }

    /// The three states where the engine cannot do anything, and the **one**
    /// thing each of them must not have: a control.
    ///
    /// Android's `when` order matters: "no data **and** loading" is tested
    /// before "no data", or a screen that is still reading announces that the
    /// plugin did not answer (`:190-195`). The ordering itself is a package
    /// test (`ImagesTests.testTheEngineLineFollowsAndroidsOrder`); this is the
    /// three sentences on a real screen, and the absence of the composer with
    /// them.
    @MainActor
    func testImages_AnEngineThatCannotRunSaysSoAndOffersNothing() throws {
        for (how, title, composer) in [
            ("not-installed", ImagesWords.notInstalledTitle, false),
            // Installed but not running: Android still draws the composer,
            // because `:215` guards on `installed` and not on `ready`. The
            // button is what is disabled (`:231`), which is faithful: the
            // prompt can be typed while the engine is being started.
            ("offline", ImagesWords.offlineTitle, true),
            ("unavailable", ImagesWords.unavailableTitle, false),
        ] {
            let (app, landed) = openImages(how: how, waitingFor: title)
            evidence(app, "images-engine-\(how)")
            XCTAssertTrue(landed, "the `\(how)` state does not say `\(title)`")
            XCTAssertEqual(
                composer,
                imagesPromptField(app).exists,
                "the composer's presence is wrong for `\(how)`"
            )
            if how == "unavailable" {
                // The read's own message, which is what Android puts in the
                // sub when it has one (`:194`). A screen that fell back to
                // the generic sentence would hide which house or plugin is
                // broken.
                XCTAssertTrue(
                    containing(app, StubImages.pluginSilent).exists,
                    "the plugin said why it is not answering and the screen does not"
                )
            }
            if composer {
                XCTAssertFalse(
                    imagesGenerateButton(app)?.isEnabled ?? true,
                    "generate is live with the engine offline"
                )
            }
            // **No engine control, in any of the three.** The dashboard owns
            // installing and starting it (`ImagesScreen.kt:70-74`), Android
            // draws no button for either, and one here would be a control
            // that cannot do what it says.
            for forbidden in ["install", "start engine", "start the engine", "download"] {
                XCTAssertEqual(
                    0,
                    app.buttons.matching(
                        NSPredicate(format: "label CONTAINS[c] %@", forbidden)
                    ).count,
                    "`\(how)` offers an engine control (`\(forbidden)`) that has no route"
                )
            }
            app.terminate()
        }
    }

    // MARK: - Dynamic Type

    /// The history card at the largest type size.
    ///
    /// **Measured, not believed.** A named Dynamic Type audit finding has
    /// twice been a false positive on this track, so this compares the same
    /// leaves at `large` and at AX5 and fails only on something that really
    /// does not grow. The history card is the layout most likely to straddle
    /// the viewport edge — three squares beside three lines of prompt — so
    /// the evidence is kept at both sizes and the **picture** is the thing to
    /// read when a failure will not attribute itself
    /// ([[audit-reports-one-issue]]).
    @MainActor
    func testImages_AtTheLargestTypeSize() throws {
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            for (leaf, label) in [
                ("engine", ImagesWords.engineRunning),
                ("history-label", ImagesWords.history),
                ("progress", StubImages.runningProgress),
                ("lane-title", StubImages.runningPrompt),
                ("error", StubImages.failedError),
                ("meta", StubImages.historyMeta),
                ("band", StubImages.pluginName),
            ] {
                let text = app.descendants(matching: .any)
                    .matching(NSPredicate(format: "label == %@", label))
                    .firstMatch
                if text.exists { out[leaf] = text.frame.height }
            }
            return out
        }

        var measured: [String: [String: CGFloat]] = [:]
        var tileSides: [String: CGFloat] = [:]
        for (label, mode) in [
            ("large", Self.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let (app, landed) = openImages(mode)
            XCTAssertTrue(landed, "Images never opened at \(label)")
            usleep(2_000_000)
            // The tiles are below the fold at AX5 and **unbuilt**, not merely
            // off screen, so they are scrolled to before being measured.
            let tile = app.buttons[StubImages.tileOne]
            if scrollUntilBuilt(app, tile) {
                tileSides[label] = tile.frame.width
            }
            evidence(app, "images-ax5-\(label)")
            measured[label] = heights(app)
            for (leaf, height) in heights(app).sorted(by: { $0.key < $1.key }) {
                print("SCALE [images \(label)] \(leaf) height=\(height)")
            }
            print("SCALE [images \(label)] tile side=\(tileSides[label] ?? -1)")
            app.terminate()
        }

        let small = measured["large"] ?? [:]
        let big = measured["AX5"] ?? [:]
        var compared = 0
        var stuck: [String] = []
        for (leaf, height) in small.sorted(by: { $0.key < $1.key }) {
            guard let grown = big[leaf] else { continue }
            compared += 1
            if grown < height * 2 { stuck.append("\(leaf) \(height)->\(grown)") }
        }
        XCTAssertEqual([], stuck, "a leaf on this screen does not scale")
        XCTAssertGreaterThanOrEqual(
            compared,
            3,
            "nothing could be compared, so this proved nothing: \(small) / \(big)"
        )
        // **The tile.** 84 points through `@ScaledMetric`, so it grows with
        // the type size; a fixed `frame(width: 84)` would be identical at both
        // sizes, which is rule §3's own example and is the thing a height
        // comparison of text cannot see.
        if let narrow = tileSides["large"], let wide = tileSides["AX5"] {
            XCTAssertGreaterThan(
                wide,
                narrow * 1.4,
                "the tile's side is fixed rather than @ScaledMetric (\(narrow) -> \(wide))"
            )
        }
    }

    // MARK: - Apple's audit

    /// One audit over the screen, classified **by signature**.
    ///
    /// What it is looking for, in order of likelihood: contrast on the muted
    /// meta line, which sits on a tinted card while `Color.fgMuted` was
    /// measured against the **white** canvas; an unlabelled icon button
    /// (cancel, delete); and a tile with no label. Never a pinned total — a
    /// count passes for the wrong reason the moment a row is added.
    @MainActor
    @available(iOS 17.0, *)
    func testImages_PassesApplesAccessibilityAudit() throws {
        let (app, landed) = openImages()
        XCTAssertTrue(landed, "Images never opened")
        // The history card, which is where every finding this expects would
        // be, is at the foot of a lazy list: an audit of a screen whose cards
        // are unbuilt audits the header.
        _ = scrollUntilBuilt(app, app.buttons[StubImages.tileOne])
        let found = try auditIssues(app, "images-audit")
        evidence(app, "images-audited")
        let (floor, rest) = Self.floorAndRest(found)

        let bottom = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })?
            .frame.maxY ?? 0
        XCTAssertGreaterThan(bottom, 0, "no scroll view to take a bottom edge from")
        let unexplained = rest.filter { line in
            // A Dynamic Type headline **with** an element is not a defect
            // here: ``testImages_AtTheLargestTypeSize()`` measures these very
            // leaves and fails if one of them is stuck. The measurement is the
            // test; this is the classification that makes room for it.
            if Self.knownAnonymousHeadlines.contains(where: { line.hasPrefix($0) }) {
                return false
            }
            // A contrast finding against an element the scroll view's own
            // bottom edge cuts in half. Half an element has no pixels to read,
            // every list on this phone has a row straddling that edge, and
            // scrolling one row makes it a different row's turn. The frame is
            // what tells this from a real colour defect — and it is why the
            // element is in the recorded string at all.
            if line.hasPrefix("Contrast failed"), let box = Self.frame(in: line),
               box.minY < bottom, box.maxY > bottom {
                return false
            }
            return true
        }
        XCTAssertEqual(
            [],
            unexplained,
            "Images has findings neither the measurement nor the viewport edge explains"
        )
        // Zero, not ``knownPushedBarFloor``: that figure is Settings' and
        // Music's. Asserting 8 here would be asserting another screen's
        // shape; asserting nothing would let an anonymous finding of an
        // unrecognised kind through, which is the thing the floor exists to
        // catch.
        XCTAssertEqual(0, floor, "an anonymous finding appeared on Images")
    }
}
