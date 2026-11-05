import XCTest

// ---------------------------------------------------------------------------
// The Manual screen, against the stub house.
//
// Every case is named `testManual_<What>`, which is how a train hands its
// results back to this item, and every piece of evidence is named `manual-…`.
//
// **The data these cases assert on is written to discriminate.** The fixture
// (`design-notes/ios/stubhouse/fixtures/manual.py`) seeds a word that only one
// thing on the screen can produce for each claim a case makes: `fencepost` for
// the server's own document, `trebuchet` for a keyword search, `marmalade` for
// a plugin's page, `aurora` for a phrase the server cannot answer,
// `Pomegranate Waltz` for what a room is playing, `obsidian` for the one
// inline `code` span in the document. A case that asserted on a word two
// things could produce would pass against a screen that drew the other one.
//
// **Three house routes are claimed by the fixture only on request.**
// `/api/satellites`, `/api/plugins` and `/api/music/library/stats` belong to
// the house until a `manual-…` control route sets an override, so a case that
// needs two wake words, a plugin row or a track count asks for it first and
// `reset-fixtures` takes it away again.
//
// **The cards do not float while these cases run.** Answer 31 gives every
// map node a `repeatForever` animation and XCUITest waits for the app to go
// idle before every query, so `manualLaunch(…)` — which every case here
// launches through — passes `-domovoi-manual-float-off`. One case,
// `testManual_TheMapWorksWhileItsNodesFloat`, turns it on and drives the map
// with the cards moving, so a quiescence problem costs that case and not the
// screen.
//
// **Every level is on one screen** (Kamron's answer 73). The top bar is drawn
// once and the level under it is swapped — a cut to Start here or a piece,
// and the camera's 280 ms glide between the map and a cluster — so the tree
// holds one level and one bar at a time. If a query here ever matches two of
// the same label, that premise is what broke
// (``testManual_ASwipeFromAPieceGoesUpToItsTopic()`` counts it).
//
//  * the swipe back goes up one level from the map, a cluster and a piece, and
//    leaves the manual from Start here — **at every text size** (answer 74),
//    because the manual's own recogniser sits in front of the navigation
//    stack's pop (`ManualBackSwipe.swift`);
//  * "this page is gone" is still a wait (``manualGone(_:timeout:)``) rather
//    than a read on the next line, because a node fades out over 180 ms.
//
// **`@SceneStorage` outlives a launch.** The screen keeps which level it was
// on, as Android's `rememberSaveable` promises to (Android's shell then drops
// it on every route change — `ManualView`'s `act(_:in:)`), and on iOS that survives a
// cold start of the same install — so every case here puts the screen on a
// known level with the `start here` or `map` pill rather than assuming one.
// ---------------------------------------------------------------------------

extension StubHouseUITests {

    // MARK: - the document

    /// The fetch, the parse, the schema guard and the `only` filter.
    ///
    /// `fencepost` exists in the seeded document and nowhere else — not in an
    /// app string, not in another fixture — so a port that bundled the manual
    /// with the app, or that drew a copy from disk when the server had a newer
    /// one, fails this.
    @MainActor
    func testManual_StartHereDrawsTheServersDocument() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        evidence(app, "manual-start-here")

        XCTAssertTrue(
            containing(app, StubManual.fencepost).waitForExistence(timeout: 10),
            "Start here did not draw the server's own paragraph — the document "
                + "was not fetched, or was not parsed"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.eyebrow].firstMatch.exists,
            "the eyebrow is a `labels` string of the document and it is missing"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.startTitle].firstMatch.exists,
            "`start.title` did not draw"
        )
        // `shownInApp`: the app shows its own and the unmarked lines, never
        // the dashboard's.
        XCTAssertTrue(
            containing(app, StubManual.appOnlyItem).exists,
            "the `only: app` list item is missing"
        )
        XCTAssertFalse(
            containing(app, StubManual.dashboardOnlyItem).exists,
            "the `only: dashboard` list item was drawn in the app"
        )
    }

    /// `appChecklist` and `done`: which lines the app shows, and which it can
    /// tick.
    ///
    /// Two legs over one signal this item owns. With no track count served the
    /// `music` line must read "can't tell from here"; with 412 tracks it must
    /// not, and something must be ticked. A checklist that rendered every line
    /// fails on the two dashboard-only lines, and one that ticked on a read
    /// that never answered fails the first leg.
    @MainActor
    func testManual_ChecklistShowsTheAppLinesAndTicksWhatIsDone() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        // **Armed, not assumed.** The seed leaves `/api/music/library/stats`
        // to the house, and `musicmore.py` sorts after the manual's fixture
        // and answers it with a library of its own — so in train S006 the
        // music line read `done` in the very case asserting it could not be
        // told. This arms the state the case is about: the route 503s, the
        // read fails, and `tracks` is nil.
        control(
            "manual-tracks", "{\"unreadable\": true}",
            port: 6601, what: "a library whose stats cannot be read"
        )
        var app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(
            containing(app, StubManual.musicLine).waitForExistence(timeout: 10),
            "the checklist did not draw"
        )
        evidence(app, "manual-checklist-unknown")

        let lines = [
            StubManual.firstRoomLine, StubManual.wakeLine,
            StubManual.introduceLine, StubManual.musicLine, StubManual.pairLine,
        ]
        for line in lines {
            XCTAssertTrue(
                containing(app, line).exists,
                "the checklist is missing its \(line) line"
            )
        }
        // The two the app must drop, each named by a word only it contains.
        XCTAssertFalse(
            containing(app, StubManual.adminLine).exists,
            "a dashboard-only checklist line was drawn in the app"
        )
        XCTAssertFalse(
            containing(app, StubManual.internetLine).exists,
            "a dashboard-only checklist line was drawn in the app"
        )
        // The stats route 503s, so `music` cannot be told from here — and
        // that is a third state, not "not done".
        XCTAssertTrue(
            app.images[StubManual.unknownTick].firstMatch.exists,
            "a line the app cannot judge was drawn as judged"
        )

        control(
            "manual-tracks", "{\"total_tracks\": 412}",
            port: 6601, what: "the library's track count"
        )
        app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here again")
        XCTAssertTrue(
            containing(app, StubManual.musicLine).waitForExistence(timeout: 10),
            "the checklist did not draw after the relaunch"
        )
        evidence(app, "manual-checklist-ticked")
        XCTAssertTrue(
            app.images[StubManual.doneTick].firstMatch.exists,
            "nothing was ticked although the server answered every signal"
        )
        XCTAssertFalse(
            app.images[StubManual.unknownTick].firstMatch.exists,
            "a line still read `can't tell from here` with every signal answered "
                + "— the track count landed and the checklist did not read it"
        )
    }

    // MARK: - the map and the search

    /// The three levels, `go` and `up`.
    ///
    /// The footer `next: …` is the discriminator for "this is a piece's page":
    /// it exists on no other level, so a camera that zoomed and never changed
    /// level cannot pass.
    @MainActor
    func testManual_MapOpensATopicThenAPiece() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()
        XCTAssertTrue(manualMap(app), "the map pill did not open the map")
        evidence(app, "manual-map-level-one")

        let topic = manualNode(app, StubManual.roomsTopic)
        XCTAssertTrue(
            topic.waitForExistence(timeout: 10),
            "the map drew no node for a topic in the document's ring"
        )
        topic.tap()

        let piece = manualNode(app, StubManual.piRoom)
        XCTAssertTrue(
            piece.waitForExistence(timeout: 10),
            "tapping a topic did not open its cluster"
        )
        evidence(app, "manual-map-level-two")
        piece.tap()

        XCTAssertTrue(
            app.buttons[StubManual.nextPlugins].firstMatch.waitForExistence(timeout: 10),
            "tapping a piece's node did not open its page"
        )
        evidence(app, "manual-piece-page")

        app.buttons[StubManual.zoomOut].firstMatch.tap()
        XCTAssertTrue(
            manualNode(app, StubManual.plugins).waitForExistence(timeout: 10),
            "`zoom out` from a piece did not go back to its topic's cluster"
        )
        // **Waited for, not read once.** The swap between two levels
        // animates, so the piece's page is still in the tree for a moment
        // after `zoom out` has done its work. The claim is unchanged — the
        // page must be gone, and a `zoom out` that did nothing still fails
        // this — it is just given the transition's time to happen in.
        XCTAssertTrue(
            manualGone(app.buttons[StubManual.nextPlugins].firstMatch),
            "the piece's page was still drawn after `zoom out`"
        )
    }

    /// **Answer 30**: the name scrambles, and nothing but the drawn glyphs
    /// changes.
    ///
    /// A node is reached by its **exact** accessibility label — the Kotlin's
    /// `"<title>: <subtitle>"` — within a second of its level opening, while
    /// the glyphs are still landing, and again once they have. The effect is
    /// 216 ms in a shipping build, which no case could be inside, so this one
    /// asks for 600 ms a frame (`-domovoi-manual-scramble`, DEBUG-only) and
    /// has 5.4 seconds of scramble to query during.
    ///
    /// It fails against the obvious wrong implementation: a scramble that is
    /// the node's own text with no label over it makes the button's merged
    /// label the glyph run, and `label == "Domovoi: your home"` then matches
    /// nothing for as long as the effect runs.
    ///
    /// ### And it fails if the scramble is deleted
    ///
    /// Which it did not, before the review: the label this case reaches a node
    /// by predates the effect, so the whole of `ManualScrambleName` could be
    /// removed and this passed. The drawn glyphs are not the card's label and
    /// not its value — that is the point of the effect — so nothing VoiceOver
    /// stops on can carry *what* was drawn. (A leaf under the card does carry
    /// it: train I005 measured that, twice, and the leaf-level reading below
    /// is what it cost — it allows a frame of this name's own glyph run and
    /// forbids every other word. See the comment on that line.)
    ///
    /// What it can carry is **whether** a scramble is in flight, and that is
    /// now published as the node's accessibility value
    /// (``ManualMapNode/scrambleState``) for as long as a case has asked for
    /// a slow scramble, and only then: `"scrambling"`, then `"settled"`. A
    /// node drawn straight is `"settled"` from the first frame and the wait
    /// below times out. The two screenshots stay, because they are what says
    /// the glyphs *look* right: `manual-scramble-running` has them in it and
    /// `manual-scramble-settled` has the names.
    ///
    /// The settle arithmetic itself — which characters are glyphs at which
    /// frame — is `ManualAnswersTests.testAScramblingNameSettlesFromTheLeftAndKeepsItsShape`
    /// in the package suite, where a deterministic glyph picker can be handed
    /// in. A UI case cannot assert on `Random`.
    @MainActor
    func testManual_ANodeIsNamedWhileItsNameScrambles() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        // 600 ms a frame: nine frames is 5.4 s, which is the slack this case
        // needs. The assertions below are taken after a tap, an element wait
        // and (for the second pair) a screenshot, and at the shipping 24 ms
        // the effect would be over before the first of them.
        let app = manualLaunch(scrambleMS: 600)

        // **Start here first, and it is not tidiness.** `@SceneStorage` keeps
        // the level across a launch, so a run that came in already on the map
        // would have spent its scramble before this case tapped anything, and
        // `go("map")` from the map is no move and restarts no `.task`. From
        // Start here the map's nodes are torn down, so the tap below builds
        // them — and the scramble starts with them.
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")

        // The pill is tapped here rather than through ``manualMap(_:)``:
        // that helper proves the map opened *by finding a node by its name*,
        // which is the very thing this case is about, so it would hide the
        // failure it exists to catch behind "the map pill did not open the
        // map".
        let pill = app.buttons[StubManual.mapPill].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 30), "the manual never drew its top bar")
        XCTAssertTrue(manualToTop(app, until: pill), "the `map` pill was not reachable")
        pill.tap()

        let centre = manualExact(app, StubManual.centreNodeLabel)
        XCTAssertTrue(
            centre.waitForExistence(timeout: 5),
            "the map's centre node could not be reached by its real name while "
                + "its name was scrambling — the drawn glyphs have become "
                + "somebody's accessibility label"
        )

        // **The effect is running.** The only thing about it the tree can
        // carry, and the assertion a deleted scramble fails: nine 600 ms
        // frames is 5.4 s of `"scrambling"`, and a name drawn straight is
        // `"settled"` from the first frame. Asserted before the screenshot,
        // which costs the better part of a second.
        //
        // The value is read off the **button** and not off `centre`, which is
        // an `.any` match: a value belongs to the control, and `.any` can
        // land on a container that merged the same label.
        let card = app.buttons[StubManual.centreNodeLabel].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 5), "the centre node is not a control")
        XCTAssertTrue(
            manualValue(card, is: "scrambling"),
            "the centre node's name never scrambled: its value went straight "
                + "to \"\(card.value as? String ?? "")\" — the effect is not "
                + "running at all"
        )

        // **No foreign word under this card**, asserted while the glyphs are
        // on screen.
        //
        // What this line is *not*, twice over. It first required the card to
        // contribute **nothing**, and train I005 measured that it cannot: the
        // card came back holding `["Domovoi", "Domovo9", "your home"]` with
        // both `.accessibilityHidden(true)`s in place, and the same trees
        // hold the node's icon, hidden since long before answer 30
        // (`manual-audit-map-tree…txt`: `Image … label: 'Radio'`). Inside a
        // `Button`'s label SwiftUI keeps the leaves and drops what was said
        // about them — `NewsPeopleChipRow.swift:94` has the app-wide
        // measurement, down to `accessibilityElement(children: .ignore)` only
        // adding a wrapper.
        //
        // It then required the card's own two words, which is the **same
        // measurement refused**: `"Domovo9"` is the glyph overlay's leaf, so
        // with no app change that set is `["Domovoi", "Domovo9", "your home"]`
        // again in the next train. There is no modifier that takes a leaf out
        // of a `Button`'s label (DECISIONS D-HX2), so a card whose name is
        // drawn through an overlay effect carries a third leaf mid-scramble,
        // full stop. **A scramble frame is allowed here and said so**, and
        // what is left of answer 30's half ("leaving the accessibility label
        // alone") is the ``manualExact(_:_:)`` wait above, on the real name,
        // mid-scramble: VoiceOver stops once, on the card, and hears
        // ``centreNodeLabel``. There is no leaf-level belt on that promise.
        //
        // What is still worth asserting, and what a glyph frame is told apart
        // from it by: a leaf under this card carrying a word that is neither
        // the card's own nor a frame of its own name — another node's name, a
        // crumb, a tag, a cluster's heading. ``manualIsAScrambleFrame(_:of:)``
        // is `manualScrambleFrame`'s own contract (same character count,
        // spaces kept, every unsettled character from the glyph alphabet),
        // which no other word on this screen satisfies.
        let inside = Set(card.staticTexts.allElementsBoundByIndex.map(\.label))
        let own: Set<String> = [StubManual.centreNode, StubManual.centreSubtitle]
        let foreign = inside.subtracting(own).filter {
            !manualIsAScrambleFrame($0, of: StubManual.centreNode)
        }
        XCTAssertTrue(
            foreign.isEmpty,
            "the centre node's card carries \(foreign) in the tree while its "
                + "name is scrambling, and a card may carry only its own words "
                + "\(own) and a frame of \"\(StubManual.centreNode)\" "
                + "scrambling (same length, spaces kept, the rest from "
                + "the glyph alphabet). Everything in that list is a word "
                + "this card does not draw — a level below this one still in "
                + "the tree, or somebody else's name under this control"
        )
        // The control for that reading: a button whose children are in the
        // tree, on the same screen, in the same run. If SwiftUI ever did stop
        // exposing a merged button's children, every card would be empty and
        // the line above would pass while asserting nothing.
        XCTAssertGreaterThan(
            app.buttons[StubManual.homeTabControl].firstMatch.staticTexts.count, 0,
            "no button on this screen exposes a static text of its own, so "
                + "the card's leaves above say nothing — SwiftUI has changed "
                + "what a merged button contributes and this file's counting "
                + "assertions are what to rewrite"
        )
        XCTAssertFalse(
            inside.isEmpty,
            "the centre node's card has no leaves at all while a button on "
                + "the same screen does, so this card is drawing neither its "
                + "name nor its second line"
        )
        evidence(app, "manual-scramble-running")

        // Then it finishes, which is the other half: an effect that never
        // settles leaves a node showing glyphs for good.
        XCTAssertTrue(
            manualValue(card, is: "settled", timeout: 15),
            "the centre node's name never finished scrambling"
        )
        XCTAssertTrue(
            centre.exists,
            "the centre node lost its name once the scramble had finished"
        )
        XCTAssertTrue(
            manualExact(app, StubManual.roomsTopicLabel).exists,
            "a ring topic lost its name: \(StubManual.roomsTopicLabel)"
        )
        evidence(app, "manual-scramble-settled")
    }

    /// A map card is **one** accessibility element, and it is the element
    /// Apple's audit measures.
    ///
    /// This is the case train I004's two unattributable `Contrast failed`
    /// findings should have had and did not. The audit on that screen reports
    /// `element: nil`, so a red there says "something on the map", and the
    /// thing that was wrong was a rectangle: the node's second line claimed
    /// the whole `{177.7 × 104}` card instead of its own `{85.3 × 14.3}`
    /// from the batch answer 34 made it an `AttributedString` in. A
    /// card-sized rectangle's two most-covered colours are the card and its
    /// own border — 1.35:1, 2.46:1 on the hub — where a tight one's are the
    /// card and `fgMuted`, 6.66:1. That this is what the audit measures is a
    /// model that fits every rectangle sampled, not a documented rule;
    /// ``testManual_AuditsClean`` has the sampling, the model and what it
    /// does not explain.
    ///
    /// So this counts what the audit cannot name: **one control per card, and
    /// no leaf under it carrying anything but that card's own two words.**
    ///
    /// It required the card to contribute *nothing*, and train I005 measured
    /// that it cannot: both of a card's texts are
    /// `.accessibilityHidden(true)` and both came back in the tree, beside
    /// the node icon, which has been hidden since long before any of this
    /// (`I005/ALL/att/testManual_AuditsClean/manual-audit-map-tree…txt`:
    /// `Image … label: 'Radio'`, `StaticText … 'Rooms & devices'`,
    /// `StaticText … 'where it listens'`). Inside a `Button`'s label SwiftUI
    /// keeps the leaves and drops what was said about them
    /// (`NewsPeopleChipRow.swift:94`, app-wide, including that
    /// `accessibilityElement(children: .ignore)` only adds a wrapper). The
    /// map's wires are absent from the same tree, but that measures nothing
    /// about the modifier: they are a bare `Canvas` with no accessibility
    /// content of their own, absent with `.accessibilityHidden(true)` and
    /// without it (I005's verifier). The hide is kept — ``ManualMapNode``'s second
    /// line says why, and what the frame did between the two trains — and
    /// nothing asserts that it works, because it does not.
    ///
    /// What is still worth an assertion is the thing that **can** be wrong:
    /// **one control per card, and no word under it but that card's own
    /// two.**
    ///
    /// **At rest, and the subset here is exact because of it.** Train I005
    /// measured this card's leaves at this point in this case as
    /// `["Domovoi", "your home"]` — nothing else
    /// (`overnight-trains/I005/ALL/testManual_ANodeCardIsOneElementAndNotFour.log:66`).
    /// So a glyph run among them here is not the overlay doing its job: it is
    /// a name that never settled, which at the shipping 24 ms is a card
    /// showing glyphs to a reader for the rest of the screen's life. That is
    /// the one thing this case catches that
    /// `testManual_ANodeIsNamedWhileItsNameScrambles` cannot, and it is why
    /// this one does **not** allow a scramble frame where that one has to:
    /// mid-scramble the overlay's leaf is in the tree by measurement
    /// (`["Domovoi", "Domovo9", "your home"]`), and at rest it is not.
    ///
    /// The float is off (`manualLaunch`'s default) so that `frame` is read off
    /// a card that is holding position — a bobbing card's frame is a point in
    /// an animation, which is the one thing a geometry assertion in this file
    /// must not be.
    @MainActor
    func testManual_ANodeCardIsOneElementAndNotFour() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()

        XCTAssertTrue(manualMap(app), "the map pill did not open the map")
        let centre = app.buttons[StubManual.centreNodeLabel].firstMatch
        XCTAssertTrue(centre.waitForExistence(timeout: 20), "the map drew no centre node")
        // The scramble is over by now at the shipping 24 ms — the pill tap,
        // the wait and the queries above cost more than its nine 24 ms
        // frames — and that is the state this case asserts on:
        // I005 measured the centre card's leaves here as its own two words
        // (`["Domovoi", "your home"]`), so the subset below is exact, and a
        // glyph run among them is a name that never settled.
        let cards: [(label: String, words: Set<String>)] = [
            (StubManual.centreNodeLabel, [StubManual.centreNode, StubManual.centreSubtitle]),
            (StubManual.roomsTopicLabel, [StubManual.roomsTopic, StubManual.roomsTopicSubtitle]),
        ]
        for (label, words) in cards {
            let matches = app.buttons.matching(NSPredicate(format: "label == %@", label))
            XCTAssertEqual(
                matches.count, 1,
                "\(label) is \(matches.count) controls on the map, not one — a "
                    + "card drawn twice is a node of the level left behind still "
                    + "in the tree after its 180 ms fade "
                    + "(`testManual_ASwipeFromAPieceGoesUpToItsTopic` has the "
                    + "one-screen premise)"
            )
            let card = matches.firstMatch
            let inside = Set(card.staticTexts.allElementsBoundByIndex.map(\.label))
            XCTAssertTrue(
                inside.isSubset(of: words),
                "the card \"\(label)\" carries \(inside.subtracting(words)) in "
                    + "the tree, and at rest a card carries only its own words "
                    + "\(words) (I005's measurement). A run of glyphs from "
                    + "\(StubManual.scrambleGlyphs.count) characters of upper "
                    + "case, digits and symbols is `ManualScrambleName` never "
                    + "having settled; anything else is a word this card does "
                    + "not draw — a level below this one still in the tree, or "
                    + "somebody else's name under this control"
            )
            XCTAssertFalse(
                inside.isEmpty,
                "the card \"\(label)\" has no leaves at all, while the control "
                    + "below says a merged button still exposes its children — "
                    + "so this card is drawing neither its name nor its second "
                    + "line"
            )
        }
        // The control for those subsets, so a card with no leaves cannot be
        // SwiftUI having stopped exposing a merged button's children at all.
        XCTAssertGreaterThan(
            app.buttons[StubManual.homeTabControl].firstMatch.staticTexts.count, 0,
            "no button on this screen exposes a static text of its own, so the "
                + "cards' leaves above say nothing"
        )
        evidence(app, "manual-node-card-is-one-element")
    }

    /// **Answer 31**: the map still works while its cards are bobbing.
    ///
    /// The one case in this file that launches with the float **on** (see
    /// `manualLaunch(mode:scrambleMS:float:)`). Ten `repeatForever`
    /// animations are ten animations that never finish, and every XCUITest
    /// query waits for the app to go idle first; the rest of the suite turns
    /// the float off so that a quiescence problem cannot cost every manual
    /// case, and this is the case that would pay for it instead.
    ///
    /// So what it asserts is deliberately ordinary — the map draws, a topic
    /// opens, `zoom out` comes back — because the claim is not about the
    /// animation's looks. It is "the screen is still driveable with it on".
    /// If this case idle-times out and the rest pass, the float's
    /// `repeatForever` is the suspect and
    /// `-domovoi-manual-float-off` is already the answer.
    @MainActor
    func testManual_TheMapWorksWhileItsNodesFloat() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch(float: true)

        XCTAssertTrue(manualMap(app), "the map did not open with the cards floating")
        evidence(app, "manual-float-on")
        let topic = manualNode(app, StubManual.roomsTopic)
        XCTAssertTrue(topic.waitForExistence(timeout: 15), "a floating ring topic was not drawn")
        // A moving target is still a target: `.offset` moves the hit area
        // with the card, so a tap lands.
        topic.tap()
        XCTAssertTrue(
            manualNode(app, StubManual.plugins).waitForExistence(timeout: 15),
            "tapping a floating node did not open its cluster — the hit area "
                + "does not move with the card"
        )
        let out = app.buttons[StubManual.zoomOut].firstMatch
        XCTAssertTrue(
            out.waitForExistence(timeout: 15),
            "`zoom out` was not reachable with the cards floating"
        )
        out.tap()
        XCTAssertTrue(
            manualNode(app, StubManual.centreNode).waitForExistence(timeout: 15),
            "`zoom out` did not come back to the centre"
        )
        evidence(app, "manual-float-still-driveable")
    }

    /// **Answer 32, kept by answer 74**: a swipe back from a piece goes up to
    /// its topic, and does not leave the manual.
    ///
    /// Three things say it, and they say different halves:
    ///
    ///  * the piece's page is **gone** — so the swipe did something. Two
    ///    page-only controls say that: the `next:` footer and the page's own
    ///    lowercased link back to its topic. Not the piece's **name**, which
    ///    the cluster this swipe lands on draws as a card (train I005);
    ///  * the `map` pill is **still there** — so what it did was not pop the
    ///    route. Since answer 73 every level is on one screen, so the system's
    ///    own pop would leave the manual from here; only `ManualBackSwipe`
    ///    taking the swipe first keeps this green;
    ///  * a **sibling piece's** node is there — so the level it landed on is
    ///    the piece's own cluster and not the map or Start here. `the-plugins`
    ///    is a piece of `rooms-and-devices` beside `pi-room`
    ///    (`design-notes/ios/stubhouse/fixtures/manual.py:240`), on screen at
    ///    the cluster and at no other level.
    ///
    /// It also carries the **one-screen premise** the rest of this file's
    /// queries rest on — one top bar, so one `map` pill and one `zoom out` —
    /// at the deepest level, where a second copy would most likely be.
    ///
    /// If it fails, read which level the tree holds: Home means the system's
    /// pop got the swipe (the recogniser never began, or never installed —
    /// `ManualBackSwipe.Probe` found no navigation controller); a piece page
    /// still drawn means the recogniser began and `goUp` never ran (the drag
    /// was not "committed").
    @MainActor
    func testManual_ASwipeFromAPieceGoesUpToItsTopic() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()

        XCTAssertTrue(
            manualOpenTopic(app, topic: StubManual.roomsTopic),
            "the map never opened the topic's cluster"
        )
        let piece = manualNode(app, StubManual.piRoom)
        XCTAssertTrue(piece.waitForExistence(timeout: 15), "the cluster drew no piece node")
        piece.tap()
        XCTAssertTrue(
            app.buttons[StubManual.nextPlugins].firstMatch.waitForExistence(timeout: 15),
            "the piece's page never opened"
        )
        evidence(app, "manual-swipe-on-the-piece")

        // ---- the premise: one screen, one bar.
        XCTAssertEqual(
            app.buttons.matching(
                NSPredicate(format: "label == %@", StubManual.mapPill)
            ).count,
            1,
            "more than one `map` pill is in the tree on a piece's page, so more "
                + "than one top bar is mounted — the manual is not one screen "
                + "any more, and every `firstMatch` in this file may be taking "
                + "an element of a screen nobody can see"
        )
        XCTAssertEqual(
            app.buttons.matching(
                NSPredicate(format: "label == %@", StubManual.zoomOut)
            ).count,
            1,
            "two `zoom out` buttons are in the tree on a piece's page — a "
                + "second level's bar is mounted"
        )

        manualSwipeBack(app)

        XCTAssertTrue(
            manualGone(app.buttons[StubManual.nextPlugins].firstMatch),
            "a swipe back from a piece left its page on screen"
        )
        XCTAssertTrue(
            manualGone(app.buttons[StubManual.piecePageTopicLink].firstMatch),
            "a swipe back from a piece left the piece's page on screen: its "
                + "own `\(StubManual.piecePageTopicLink)` link back to the "
                + "topic is still drawn, and that control exists on a piece's "
                + "page and nowhere else"
        )
        evidence(app, "manual-swipe-went-up-a-level")
        XCTAssertTrue(
            app.buttons[StubManual.mapPill].firstMatch.exists,
            "a swipe back from a piece left the manual altogether instead of "
                + "going up a level — the system's pop got the swipe, which is "
                + "what it does on a screen whose levels are not pushed"
        )
        XCTAssertTrue(
            manualNode(app, StubManual.plugins).exists,
            "the topic's cluster is not what the swipe landed on"
        )
    }

    /// **Answer 32, one level higher**: a swipe back from the map lands on
    /// Start here, and still does not leave the manual.
    ///
    /// Android's `up()` reads `else -> "start"` (`ManualScreen.kt:308`), so
    /// its back from the map stays on the screen too. This is the level where
    /// getting it wrong looks like getting it right: a swipe that popped the
    /// route would also take the map off screen, so "the map is gone" is not
    /// the claim. The claim is **what is there instead** — Start here's own
    /// title, which Home does not draw.
    ///
    /// The second assertion reads the centre **node**: the centre crumb
    /// carries the same words and is a `Text`, not a button, at this level
    /// (``ManualCrumbs`` draws `link: topic != nil`).
    @MainActor
    func testManual_ASwipeFromTheMapGoesBackToStartHere() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()

        XCTAssertTrue(manualMap(app), "the map pill did not open the map")
        let centre = manualNode(app, StubManual.centreNode)
        XCTAssertTrue(centre.waitForExistence(timeout: 15), "the map drew no centre node")
        evidence(app, "manual-swipe-on-the-map")

        manualSwipeBack(app)

        XCTAssertTrue(
            app.staticTexts[StubManual.startTitle].firstMatch.waitForExistence(timeout: 15),
            "a swipe back from the map did not land on Start here — if the "
                + "house's name is on screen instead it popped the route and "
                + "left the manual"
        )
        XCTAssertTrue(
            manualGone(centre),
            "the map's centre node is still drawn, so the swipe moved nothing"
        )
        XCTAssertTrue(
            app.buttons[StubManual.mapPill].firstMatch.exists,
            "the manual's top bar is gone, so the swipe left the screen"
        )
        evidence(app, "manual-swipe-back-on-start-here")
    }

    /// **Kamron's answer 74**: at an accessibility text size the swipe back
    /// goes up a level too — from a cluster to the map, from the map to Start
    /// here — and only from Start here does it leave the manual.
    ///
    /// At those sizes the top bar and the page share **one** scroll view
    /// (``ManualView/ready(_:cached:_:)``: pinned, the bar alone was 310 of
    /// this screen's 400 points), and before this the swipe left the manual
    /// from every level there. `ManualBackSwipe` sits on the navigation
    /// controller's view, outside that scroll view, so the layout is no longer
    /// part of what the swipe does. This case fails on that defect: the first
    /// swipe would land on Home and the `map` pill would be gone.
    ///
    /// The swipe is the same drag as everywhere else in this file — at the
    /// window's leading edge, half way down, which at AX5 is over the scroll
    /// view's content.
    @MainActor
    func testManual_ASwipeAtAnAccessibilitySizeGoesUpALevel() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch(mode: StubManual.ax5)

        XCTAssertTrue(
            manualOpenTopic(app, topic: StubManual.roomsTopic),
            "the map never opened the topic's cluster at AX5"
        )
        let sibling = manualNode(app, StubManual.plugins)
        XCTAssertTrue(sibling.waitForExistence(timeout: 15), "the cluster drew no piece node at AX5")
        evidence(app, "manual-swipe-ax5-on-the-cluster")

        manualSwipeBack(app)

        XCTAssertTrue(
            manualGone(sibling, timeout: 15),
            "at AX5 a swipe back from a cluster left the cluster's pieces "
                + "drawn, so it moved nothing"
        )
        XCTAssertTrue(
            manualNode(app, StubManual.centreNode).waitForExistence(timeout: 15),
            "at AX5 a swipe back from a cluster did not land on the map — if "
                + "Home is on screen, the swipe still leaves the manual at the "
                + "accessibility sizes, which is what answer 74 asked to change"
        )
        XCTAssertTrue(
            app.buttons[StubManual.mapPill].firstMatch.exists,
            "at AX5 a swipe back from a cluster left the manual"
        )
        evidence(app, "manual-swipe-ax5-went-up-to-the-map")

        manualSwipeBack(app)
        XCTAssertTrue(
            app.staticTexts[StubManual.startTitle].firstMatch.waitForExistence(timeout: 15),
            "at AX5 a swipe back from the map did not land on Start here"
        )
        let pill = app.buttons[StubManual.mapPill].firstMatch
        XCTAssertTrue(pill.exists, "at AX5 a swipe back from the map left the manual")
        evidence(app, "manual-swipe-ax5-on-start-here")

        manualSwipeBack(app)
        XCTAssertTrue(
            manualGone(pill, timeout: 15),
            "at AX5 a swipe back on Start here did not leave the manual"
        )
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "the swipe left the manual but Home is not what it landed on"
        )
        evidence(app, "manual-swipe-ax5-left-the-manual")
    }

    /// The phone bar still works from a deep level, and the manual is where
    /// it was left when you come back — **which Android's is not**.
    ///
    /// The phone bar is drawn outside the shell's stack (`WorkspaceView.swift:82`)
    /// and its tabs append a `Route` to the same path. While a level was a
    /// pushed screen that was a route pushed underneath live presentations,
    /// and the manual collapsed itself to Start here to avoid it (DECISIONS
    /// D32B). With one screen there is nothing to collapse, so coming back
    /// lands on the piece.
    ///
    /// Android declares `at` a `rememberSaveable` (`ManualScreen.kt:290`) and
    /// then loses it: `ScreenRouter` composes only the current route and the
    /// shell's `SaveableStateProvider` is keyed on the server
    /// (`AppShell.kt:231`), so opening Music disposes `ManualScreen` and the
    /// manual restarts at Start here. That is an Android defect, read and not
    /// copied (``ManualView``'s `act(_:in:)` has the reading).
    ///
    /// The swipe back from Music is also the other half of answer 74's
    /// recogniser: it stays on the navigation controller's view while Music is
    /// on top, and it must **not** take that swipe. If it did, Music would
    /// stay and the manual underneath would move up a level.
    @MainActor
    func testManual_ThePhoneBarWorksFromADeepLevel() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()

        XCTAssertTrue(
            manualOpenTopic(app, topic: StubManual.roomsTopic),
            "the map never opened the topic's cluster"
        )
        let piece = manualNode(app, StubManual.piRoom)
        XCTAssertTrue(piece.waitForExistence(timeout: 15), "the cluster drew no piece node")
        piece.tap()
        let footer = app.buttons[StubManual.nextPlugins].firstMatch
        XCTAssertTrue(footer.waitForExistence(timeout: 15), "the piece's page never opened")
        evidence(app, "manual-phone-bar-on-the-piece")

        let tab = app.buttons[ShellStringsUnderTest.musicTab].firstMatch
        XCTAssertTrue(
            tab.waitForExistence(timeout: 15),
            "the phone bar has no Music tab on a manual piece — if the bar is "
                + "gone from the screen, that is the finding, not this tab"
        )
        tab.tap()

        let crumb = app.staticTexts[ShellStringsUnderTest.musicCrumb]
        XCTAssertTrue(
            crumb.waitForExistence(timeout: 15),
            "the phone bar's Music tab did nothing from a manual piece"
        )
        evidence(app, "manual-phone-bar-opened-music")
        XCTAssertTrue(
            manualGone(footer),
            "the piece's page is still on screen over Music"
        )

        manualSwipeBack(app)
        XCTAssertTrue(
            footer.waitForExistence(timeout: 15),
            "a swipe back from Music did not come back to the manual's piece. "
                + "If Music is still on screen, the manual's recogniser took a "
                + "swipe that was Music's; if the manual is on Start here, it "
                + "lost its level on the way out — Android's defect (its "
                + "`ManualScreen` is disposed with the route), not this port's "
                + "design"
        )
        evidence(app, "manual-phone-bar-back-to-the-piece")
    }

    /// A swipe back from **Start here** leaves the manual.
    ///
    /// Android's `BackHandler(enabled = at != "start")`: from Start here the
    /// back is the screen's own. ``manualBack(at:manualIsOnTop:)`` answers
    /// `.leaveTheManual`, so `ManualBackSwipe` fails at once and the system's
    /// pop gets the touch. A manual nobody can swipe out of is the regression
    /// a recogniser in front of the pop could cause, and this is the case
    /// that catches it.
    ///
    /// Two assertions, each a different failure:
    ///
    ///  * the `map` pill is **gone** — the manual left. A swallowed gesture
    ///    leaves it there and fails here;
    ///  * the house's name is on screen — what it left *to* is Home.
    ///
    /// Its history, kept short: it hung in I003 (an inner `NavigationStack`
    /// stopped the app going idle) and failed on its own assertion in I004
    /// (the drag did nothing); ``manualSwipeBack(_:)`` carries what was
    /// learned about the drag.
    @MainActor
    func testManual_ASwipeFromStartHereLeavesTheManual() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()

        // `@SceneStorage` keeps the level across a launch, so this is not
        // tidiness: a run that came in on the map would be swiping a level
        // that is *meant* to stay inside the manual.
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        let pill = app.buttons[StubManual.mapPill].firstMatch
        XCTAssertTrue(pill.exists, "the manual's top bar is not on screen")
        evidence(app, "manual-swipe-on-start-here")

        manualSwipeBack(app)

        XCTAssertTrue(
            manualGone(pill),
            "a swipe back on Start here did not leave the manual — the top "
                + "bar is still there, so the system's pop never got the swipe"
        )
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "the swipe left the manual but Home is not what it landed on"
        )
        evidence(app, "manual-swipe-left-the-manual")
    }

    /// **Answer 76**: the names retype on every move — down into a cluster,
    /// back up to the map, and up from a piece to its cluster by the swipe.
    ///
    /// Android retypes every node on every level change (`levelKey`,
    /// `ManualScreen.kt:593`; `LaunchedEffect(key, text)`, `:686`). The drawn
    /// glyphs are nobody's label (``ManualScrambleName``), so the only thing
    /// the tree can carry is *whether* a scramble is in flight —
    /// ``ManualMapNode/scrambleState``, published only to a launch that asked
    /// for a slow scramble. Each arrival below is asserted as **scrambling**
    /// on a card of the level arrived at, and that is the claim: a level that
    /// came back with its names already settled fails on that wait.
    @MainActor
    func testManual_GoingBackUpALevelScramblesTheNamesAgain() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        // 600 ms a frame: 5.4 s of retyping, which is the slack a case needs
        // to be inside an effect that ships at 216 ms.
        let app = manualLaunch(scrambleMS: 600)

        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        let pill = app.buttons[StubManual.mapPill].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 30), "the manual never drew its top bar")
        XCTAssertTrue(manualToTop(app, until: pill), "the `map` pill was not reachable")
        pill.tap()

        // The centre node is drawn on the **map** level and on no other.
        let centre = app.buttons[StubManual.centreNodeLabel].firstMatch
        XCTAssertTrue(centre.waitForExistence(timeout: 20), "the map drew no centre node")
        XCTAssertTrue(
            manualValue(centre, is: "settled", timeout: 25),
            "the map's first scramble never finished"
        )

        // ---- down: the cluster's cards retype as they arrive.
        let topic = manualNode(app, StubManual.roomsTopic)
        XCTAssertTrue(topic.waitForExistence(timeout: 15), "the map drew no ring topic")
        topic.tap()
        let sibling = manualNode(app, StubManual.plugins)
        XCTAssertTrue(sibling.waitForExistence(timeout: 15), "the topic's cluster never opened")
        XCTAssertTrue(
            manualValue(sibling, is: "scrambling", timeout: 15),
            "going down into a cluster did not retype its names"
        )
        XCTAssertTrue(
            manualValue(sibling, is: "settled", timeout: 25),
            "the cluster's scramble never finished"
        )

        // ---- up, by `zoom out`: the map retypes.
        app.buttons[StubManual.zoomOut].firstMatch.tap()
        XCTAssertTrue(
            manualValue(centre, is: "scrambling", timeout: 15),
            "`zoom out` back to the map did not retype its names: the centre "
                + "node came back already settled"
        )
        evidence(app, "manual-up-a-level-scrambling-again")
        XCTAssertTrue(
            manualValue(centre, is: "settled", timeout: 25),
            "the second scramble never finished"
        )
        XCTAssertTrue(
            manualExact(app, StubManual.centreNodeLabel).exists,
            "the centre node lost its name after the second scramble"
        )

        // ---- up, by the swipe from a piece: the cluster retypes.
        manualNode(app, StubManual.roomsTopic).tap()
        let piece = manualNode(app, StubManual.piRoom)
        XCTAssertTrue(piece.waitForExistence(timeout: 15), "the cluster never opened again")
        XCTAssertTrue(manualValue(piece, is: "settled", timeout: 25), "the cluster never settled")
        piece.tap()
        XCTAssertTrue(
            app.buttons[StubManual.nextPlugins].firstMatch.waitForExistence(timeout: 15),
            "the piece's page never opened"
        )
        manualSwipeBack(app)
        XCTAssertTrue(
            manualValue(manualNode(app, StubManual.plugins), is: "scrambling", timeout: 15),
            "a swipe back from a piece to its cluster did not retype the "
                + "cluster's names"
        )
        evidence(app, "manual-up-from-a-piece-scrambling")
    }

    /// **Answer 73**: the glide lands on the right level — the cluster is
    /// framed where the map was, at full size, and the map comes back the
    /// same way.
    ///
    /// The camera is three numbers (``ManualCamera``) and at the end of the
    /// glide it looks at the cluster's hub at `1 / 0.34`, so the hub card is
    /// in the **middle** of the stage and the size of any other card. That is
    /// what the frames below read. A camera that did not move — the defect
    /// this case is for — leaves the cluster drawn at its world scale of
    /// 0.34 around its ring topic's place: cards about a third the width,
    /// off the middle.
    ///
    /// What it cannot read is the glide **between**: a query waits for the
    /// app to go idle, which is after the animation. The 280 ms is
    /// `ManualAnswersTests.testTheGlideIsAndroids`, and the motion itself is
    /// the screen recording's.
    @MainActor
    func testManual_TheGlideLandsOnTheCluster() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()

        XCTAssertTrue(
            manualOpenTopic(app, topic: StubManual.roomsTopic),
            "the map never opened the topic's cluster"
        )
        let piece = manualNode(app, StubManual.piRoom)
        XCTAssertTrue(piece.waitForExistence(timeout: 15), "the cluster drew no piece node")
        XCTAssertTrue(
            manualGone(app.buttons[StubManual.centreNodeLabel].firstMatch),
            "the map's centre node is still drawn over the cluster"
        )
        let hubs = app.buttons.matching(NSPredicate(format: "label == %@", StubManual.roomsTopicLabel))
        XCTAssertEqual(hubs.count, 1, "the cluster's hub is not one card")
        let hub = hubs.firstMatch
        let window = app.windows.firstMatch.frame
        print("MANUAL glide landed: hub \(hub.frame) piece \(piece.frame) window \(window)")
        evidence(app, "manual-glide-landed-on-the-cluster")
        XCTAssertGreaterThan(
            hub.frame.width, window.width * 0.35,
            "the cluster's hub is \(hub.frame.width) points wide in a "
                + "\(window.width)-point window — drawn at its world scale, so "
                + "the camera never zoomed in"
        )
        XCTAssertEqual(
            hub.frame.midX, window.midX, accuracy: 12,
            "the cluster's hub is not in the middle of the stage, so the camera "
                + "is not looking at it"
        )
        XCTAssertGreaterThan(
            piece.frame.width, window.width * 0.35,
            "a piece card in the cluster is drawn at a third of its size"
        )
        XCTAssertTrue(piece.isHittable, "a piece card the glide landed on cannot be tapped")

        // And back out: the map, centred, at 1:1.
        app.buttons[StubManual.zoomOut].firstMatch.tap()
        let centre = app.buttons[StubManual.centreNodeLabel].firstMatch
        XCTAssertTrue(centre.waitForExistence(timeout: 15), "`zoom out` did not come back to the map")
        XCTAssertTrue(manualGone(piece), "the cluster's piece is still drawn over the map")
        print("MANUAL glide back: centre \(centre.frame) window \(window)")
        evidence(app, "manual-glide-landed-on-the-map")
        XCTAssertGreaterThan(
            centre.frame.width, window.width * 0.35,
            "the map's centre card is drawn small — the camera stayed zoomed in"
        )
        XCTAssertEqual(
            centre.frame.midX, window.midX, accuracy: 12,
            "the map's centre card is off the middle — the camera stayed on the hub"
        )
    }

    /// **Answer 75**: `admin` sits on amber, `preview` and `plugin` on grey,
    /// and a stand-alone badge on the **same** grey as the inline words —
    /// Android's `warnSoft` and `idleSoft` (`ManualScreen.kt:878-880`,
    /// `Kit.kt:53`), composited onto the page (``ManualTone``).
    ///
    /// Read off the screen's pixels, because a colour is not in the tree. The
    /// light theme's washes are `#FFF4E8` (amber) and `#F2F1F1` (grey); the
    /// three it replaced were `#F5F3F0` for every inline word and
    /// `#F2F2F7` (`secondarySystemBackground`) for every badge. A tolerance of
    /// 2 per channel tells all four apart, and every reading is printed.
    ///
    ///  * the `admin` badge on Start here's label block — its most common
    ///    colour is the amber;
    ///  * the `preview` badge on a piece's heading — its most common colour is
    ///    the grey;
    ///  * the one paragraph with all three inline words — it holds a **block**
    ///    of amber and a block of grey: a square two points on a side, every
    ///    pixel of it within 2 of the wash. Before answer 75 it held neither.
    ///
    /// **A block, not a count** (revision 1, the verifier's M2). The first
    /// shape of this case counted matching pixels over the paragraph's
    /// frame, and the grey half could not fail: black prose anti-aliased on
    /// white puts light greys on every glyph edge, and the fixture sentence
    /// rendered with **no** grey wash at all held 179 pixels within 2 of
    /// `#F2F1F1`. An edge is a pixel or two wide; a wash behind a caption
    /// word is a patch — its leading space alone is a column about 3 × 13
    /// points, and the strip under the baseline of `review` or `admin` is
    /// about 30 × 2.7 — so a two-point square of the wash's colour is in
    /// every washed word and in no glyph edge. The grey must also be nearer
    /// `#F2F1F1` than the code run's `#F5F3F0`, which is three away on red:
    /// the `obsidian` run's wash shares the paragraph, and without that it
    /// could stand in for a missing grey.
    @MainActor
    func testManual_TheLittleWordsSitOnAndroidsWashes() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()
        let amber = (255, 244, 232)
        let grey = (242, 241, 241)

        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        let admin = app.staticTexts[StubManual.adminBadge].firstMatch
        XCTAssertTrue(manualScroll(app, to: admin), "Start here's `admin` badge was never built")
        XCTAssertTrue(manualScrollOnScreen(app, admin), "the `admin` badge could not be brought on screen")
        let adminBack = manualMostCommon(app, in: admin.frame)
        print("MANUAL wash admin badge \(admin.frame): \(String(describing: adminBack))")
        XCTAssertTrue(
            adminBack.map { manualNear($0, amber) } ?? false,
            "the `admin` badge sits on \(String(describing: adminBack)), not "
                + "on Android's amber \(amber)"
        )

        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordPiRoom, piece: StubManual.piRoom),
            "the manual never opened the preview piece"
        )
        let preview = app.staticTexts[StubManual.previewBadge].firstMatch
        XCTAssertTrue(preview.waitForExistence(timeout: 15), "the piece's `preview` badge did not draw")
        let previewBack = manualMostCommon(app, in: preview.frame)
        print("MANUAL wash preview badge \(preview.frame): \(String(describing: previewBack))")
        XCTAssertTrue(
            previewBack.map { manualNear($0, grey) } ?? false,
            "the `preview` badge sits on \(String(describing: previewBack)), "
                + "not on the inline words' grey \(grey) — `(242, 242, 247)` is "
                + "Apple's `secondarySystemBackground`, the grey it replaced"
        )

        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordYourMusic, piece: StubManual.yourMusic),
            "the manual never opened the piece with the inline words"
        )
        // By predicate, never by subscript: the paragraph's label is 133
        // characters and XCUITest refuses a subscript identifier over 128
        // with an exception that ends the case (train I007). The whole label
        // is still compared, so the match is as exact as the subscript was.
        let paragraph = manualExact(app.staticTexts, StubManual.codeSpanParagraph)
        guard paragraph.waitForExistence(timeout: 15) else {
            print("=== THE INLINE-WORDS PARAGRAPH NEVER DREW — tree follows ===")
            print(app.debugDescription)
            XCTFail("the paragraph with the inline words did not draw")
            return
        }
        XCTAssertTrue(manualScrollOnScreen(app, paragraph), "the paragraph could not be brought on screen")
        let codeWash = (245, 243, 240)
        let amberBlock = manualWashBlock(app, in: paragraph.frame, near: amber, unlike: [])
        let greyBlock = manualWashBlock(app, in: paragraph.frame, near: grey, unlike: [codeWash])
        print(
            "MANUAL wash inline paragraph \(paragraph.frame): "
                + "amber=\(String(describing: amberBlock)) grey=\(String(describing: greyBlock)) "
                + "(side = the block's side in pixels, widest = the widest run of "
                + "columns that hold one)"
        )
        evidence(app, "manual-washes")
        XCTAssertTrue(
            amberBlock.map { $0.widest >= $0.side } ?? false,
            "the paragraph holds no amber block (\(String(describing: amberBlock))): "
                + "inline `admin` is not on Android's amber"
        )
        XCTAssertTrue(
            greyBlock.map { $0.widest >= $0.side } ?? false,
            "the paragraph holds no grey block (\(String(describing: greyBlock))): "
                + "inline `preview` and `plugin` are not on Android's grey — "
                + "light greys on glyph edges do not count, and neither does "
                + "the code run's wash"
        )
    }

    /// A `route:` link works at an **accessibility** size — the layout where
    /// the top bar and the page share one scroll view.
    ///
    /// `testManual_AnUnopenableRouteLinkIsNotALink` is the same tap at the
    /// ordinary size. The two layouts are two branches of
    /// ``ManualView/ready(_:cached:_:)`` and both draw the level inside the
    /// view that installs the link action (`ManualLinkHostTests` reads that
    /// off the source); this is the runtime half for the branch I005's
    /// verifier said had no case.
    @MainActor
    func testManual_ALinkWorksAtAnAccessibilitySize() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch(mode: StubManual.ax5)
        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordWhatItIs, piece: StubManual.whatItIs),
            "the manual never opened the piece with the links at AX5"
        )
        let link = manualControl(app, StubManual.roomsLink)
        let words = link.exists ? link : app.staticTexts[StubManual.roomsLink].firstMatch
        XCTAssertTrue(manualScroll(app, to: words), "the `route:` link's words were not built at AX5")
        XCTAssertTrue(manualScrollOnScreen(app, words), "the link could not be brought on screen at AX5")
        words.tap()
        XCTAssertTrue(
            startingWith(app, StubManual.roomsSectionSub).waitForExistence(timeout: 15),
            "tapping the openable `route:` link at AX5 did not land on "
                + "Settings → Rooms — the accessibility-size layout's links are dead"
        )
        evidence(app, "manual-link-ax5-landed-on-rooms")
    }

    /// **Answer 33**: a server whose document predates `checklist_unknown`
    /// still says "can't tell from here".
    ///
    /// The fixture serves the document with that one label removed, which is
    /// the only state the app's own fallback is ever read in — the generator
    /// has shipped the key since before v1
    /// (`scripts/manual/build_manual.py:361`). The circle is drawn either
    /// way; what the fallback decides is whether it has a name at all, and
    /// `ManualStrings.Fallback.checklistUnknown` was the empty string, which
    /// is an image VoiceOver reads as nothing.
    @MainActor
    func testManual_AnOlderServersTickStillSaysItCannotTell() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control(
            "manual-doc", "{\"how\": \"no-unknown-label\"}",
            port: 6601, what: "a document with no `checklist_unknown` label"
        )
        // The same arming ``testManual_ChecklistShowsTheAppLinesAndTicksWhatIsDone``
        // needs: the stats route 503s, so the music line is the third state.
        control(
            "manual-tracks", "{\"unreadable\": true}",
            port: 6601, what: "a library whose stats cannot be read"
        )
        let app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(
            containing(app, StubManual.musicLine).waitForExistence(timeout: 10),
            "the checklist did not draw"
        )
        evidence(app, "manual-unknown-tick-old-server")
        XCTAssertTrue(
            app.images[StubManual.unknownTick].firstMatch.exists,
            "a checklist circle the app cannot judge had no words on a server "
                + "whose document has no `checklist_unknown`: the app's own "
                + "fallback is what has to word it"
        )
        // Handed back, so a case that forgets to reset does not read this
        // document.
        control("manual-doc", "{}", port: 6601, what: "the seeded document again")
    }

    /// **Answer 34**: an inline `code` run and the inline pills are drawn on
    /// an opaque wash, and Apple's audit reads them.
    ///
    /// **Since answer 75 the run and the pills sit on three different
    /// washes** (revision 1 restated this; the earlier text described every
    /// inline word on `--sunken`). The ratios are arithmetic, not a guess,
    /// from ``manualContrastRatio(_:_:)`` over the same sRGB values the view
    /// builds its colours from:
    ///
    ///  * the `code` run, `Color.primary` on `--sunken` `#F5F3F0`
    ///    (``Color/manualCodeWash``): 18.96:1 light, 19.27:1 dark
    ///    (`ManualAnswersTests.testEveryRatioTheWashCommentsState`);
    ///  * `admin`, `warnInk` on Android's `warnSoft` composited onto the page,
    ///    `#FFF4E8` / `#291B0C`: **5.27:1** light, 8.74:1 dark;
    ///  * `preview` and `plugin`, `fgMuted` on `idleSoft` composited,
    ///    `#F2F1F1` / `#151414`: 6.61:1 light, 7.40:1 dark
    ///    (`ManualAnswersTests.testEveryWordOnItsWashReads`).
    ///
    /// What a case can add to that is Apple's own measurement of the drawn
    /// page, which is what this does. `Your music` is the one piece with an
    /// inline `code` span (`obsidian`) and all three pills in it, so this
    /// audit covers every ink-on-wash pairing at once — including `admin`,
    /// the weakest at 5.27:1. A translucent wash would make those numbers
    /// untrue and this is where it would show.
    ///
    /// ### What this case does **not** guard, said plainly
    ///
    /// It passes with `run.backgroundColor` removed from both arms, and the
    /// review was right to say so. A wash is three pixel values inside a
    /// `Text`'s attributed string: it is not an element, it has no label and
    /// no value, and the accessibility tree a UI case queries cannot see it.
    ///
    /// So the washes are guarded elsewhere: the numbers by
    /// `ManualAnswersTests.testEveryRatioTheWashCommentsState` and
    /// `testEveryWordOnItsWashReads`, and the pills' washes being on the
    /// screen at all by ``testManual_TheLittleWordsSitOnAndroidsWashes``,
    /// which samples this paragraph's frame for a two-point **block** of each
    /// pill wash (a count of matching pixels could not fail: glyph edges hold
    /// light greys). The `code` run's own wash is still guarded by review and
    /// the evidence screenshot only. What this case is for is the other half:
    /// that the page a reviewer looked at still passes Apple's audit, at the
    /// ordinary type size, with the keyboard down.
    @MainActor
    func testManual_ACodeSpanIsWashedAndStillAudits() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()
        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordYourMusic, piece: StubManual.yourMusic),
            "the manual never opened the piece with the code span in it"
        )
        XCTAssertTrue(
            containing(app, StubManual.codeWord).waitForExistence(timeout: 15),
            "the paragraph with the inline `code` span did not draw"
        )
        // The keyboard has to be down before an audit: text under it has no
        // pixels the audit can read, which is what train S009's nine findings
        // were. ``manualOpen(_:search:piece:)`` opens from the search field
        // and the app clears focus on that tap.
        let keyboard = app.keyboards.firstMatch
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: keyboard)
        XCTAssertEqual(
            XCTWaiter().wait(for: [gone], timeout: 10), .completed,
            "the keyboard was still up over the piece: \(keyboard.frame)"
        )
        // The one named exception in this file, with its measurement in
        // ``manualAudit(_:_:washedElement:washes:)``: Apple's audit reports
        // `Contrast failed` against this paragraph because its frame holds
        // the page and wash patches behind part of its words. I004 measured
        // every ink off the audit's own screenshot at 18.96 / 6.71 / 5.17 to 1
        // when every inline word sat on `--sunken`; since answer 75 the
        // arithmetic is 18.96 (code) / 6.61 (grey pills) / 5.27 (`admin` on
        // amber). Train I007 measured them off pixels — 18.961 / 6.589 /
        // 5.273 — and returned the same one finding **with no element**;
        // that route is allowed once, and only after this screen's own
        // pixels are read and clear 4.5 (``manualAudit(_:_:washedElement:washes:)``).
        // Any other finding on this page still fails this case, including a
        // `Contrast failed` against any other element.
        try manualAudit(
            app, "manual-audit-code-span",
            washedElement: StubManual.codeSpanParagraph,
            washes: [
                ("code", StubManual.codeWash),
                ("amber", StubManual.amberWash),
                ("grey", StubManual.greyWash),
            ]
        )
    }

    /// `searchManual` over **keywords**, and a result that navigates.
    ///
    /// `trebuchet` is one piece's keyword and in no title and no subtitle, so
    /// a search over titles only finds nothing; the other piece's title is
    /// asserted absent, which is what makes the hit mean something.
    @MainActor
    func testManual_SearchOpensThePieceItNames() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(manualType(app, StubManual.keywordPiRoom), "the search field never drew")
        evidence(app, "manual-search-keyword")

        let hit = app.buttons[StubManual.piRoom].firstMatch
        XCTAssertTrue(
            hit.waitForExistence(timeout: 10),
            "a search for a word in one piece's keywords found nothing — the "
                + "search is over titles only"
        )
        XCTAssertFalse(
            app.buttons[StubManual.yourMusic].firstMatch.exists,
            "the search matched a piece whose keywords and title hold none of "
                + "the query's words"
        )
        hit.tap()
        XCTAssertTrue(
            app.buttons[StubManual.nextPlugins].firstMatch.waitForExistence(timeout: 10),
            "picking a search result did not open that piece's page"
        )
    }

    /// The plugin gate (`visiblePieces`): a piece of a plugin this house does
    /// not run is not on the map and cannot be searched for.
    ///
    /// The house serves no plugins until this item's control route says so, so
    /// the hidden leg is the ordinary house and the shown leg is one request
    /// away. A map that listed every piece fails the first leg.
    @MainActor
    func testManual_APluginsPieceIsHiddenWhileThePluginIsNot() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        var app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(manualType(app, StubManual.keywordRadio), "the search field never drew")
        XCTAssertTrue(
            app.staticTexts[StubManual.searchEmpty].firstMatch.waitForExistence(timeout: 10),
            "the search found something with no plugin installed"
        )
        XCTAssertFalse(
            app.buttons[StubManual.radioPiece].firstMatch.exists,
            "a piece belonging to a plugin this house does not run was offered"
        )
        evidence(app, "manual-plugin-piece-hidden")

        control(
            "manual-plugins",
            "{\"plugins\": [{\"slug\": \"radio\", \"name\": \"Radio\","
                + " \"version\": \"0.4.1\", \"enabled\": true, \"status\": \"ok\"}]}",
            port: 6601, what: "a radio plugin that is installed and healthy"
        )
        app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here again")
        XCTAssertTrue(manualType(app, StubManual.keywordRadio), "the search field never drew")
        XCTAssertTrue(
            app.buttons[StubManual.radioPiece].firstMatch.waitForExistence(timeout: 10),
            "the plugin's page stayed hidden with the plugin installed, enabled "
                + "and healthy"
        )
        evidence(app, "manual-plugin-piece-shown")
    }

    // MARK: - what this server can answer

    /// `shownManualPhrases`: a phrase whose feature the server has switched
    /// off is not offered, **and neither is the reply it carries**.
    ///
    /// `aurora` is in that phrase and `clear skies` in its `then`, and both
    /// carry `needs: ai` in the document — which is what the shipped document
    /// does, so that a dropped phrase cannot leave its answer captioning the
    /// phrase above it. The sibling phrase stays, which is what tells this
    /// case from one that simply hid the block.
    @MainActor
    func testManual_APhraseTheServerCannotAnswerIsNotShown() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control("chat-ai", "{\"on\": false}", port: 6601, what: "the server's AI features off")
        let app = manualLaunch()
        let reached = manualOpen(app, search: StubManual.keywordTalking, piece: StubManual.talking)
        let sibling = containing(app, StubManual.timePhrase).waitForExistence(timeout: 10)
        let asked = containing(app, StubManual.aiPhrase).exists
        let answered = containing(app, StubManual.aiReply).exists
        evidence(app, "manual-ai-off-phrases")
        // Put the house back **before** asserting: a failure with
        // `continueAfterFailure` off stops the case where it stands, and the
        // next item's cases would then run against a server with its AI off.
        control("chat-ai", "{\"on\": true}", port: 6601, what: "the server's AI features on")

        XCTAssertTrue(reached, "the manual never opened the piece with the say block")
        XCTAssertTrue(sibling, "a phrase that needs no feature was dropped too")
        XCTAssertFalse(
            asked,
            "a phrase whose feature the server says is off was still offered"
        )
        XCTAssertFalse(
            answered,
            "the reply to a dropped phrase was still drawn — it is now captioning "
                + "whichever phrase came before it"
        )
    }

    /// `manualRouteOpens`: a link the app cannot open does nothing and reads
    /// as the words it is; the one beside it opens its screen.
    ///
    /// With the server's AI features off, `Route.chat` is not visible, so the
    /// `route:chat` link in the seeded paragraph must not be a link — while
    /// `route:settings/rooms` in the paragraph under it stays one and lands
    /// on Settings → Rooms. That pairing is the point: a port that drew
    /// neither as a link would pass the first half alone.
    ///
    /// **This taps, where the first version of this case queried.** A live
    /// link is an `AttributedString` run inside one `Text`
    /// (`ManualPageView.swift:481`), and SwiftUI gives such a run no element
    /// of its own: in train S006 the whole sentence came back as a single
    /// `StaticText` with no link and no button beneath it, so "is it a link"
    /// was unanswerable from the tree and the case failed a screen that was
    /// right. What a person can actually tell apart is what a tap does, and
    /// that is the stronger claim anyway — it is the behaviour the rule
    /// exists for, not its styling. The fixture puts the live link alone in
    /// its paragraph so a tap lands on its own words (`fixtures/manual.py`),
    /// and the query for a control is kept: if a future SwiftUI does expose
    /// these runs, a dead one drawn as a link still fails here.
    @MainActor
    func testManual_AnUnopenableRouteLinkIsNotALink() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control("chat-ai", "{\"on\": false}", port: 6601, what: "the server's AI features off")
        let app = manualLaunch()
        let reached = manualOpen(app, search: StubManual.keywordWhatItIs, piece: StubManual.whatItIs)
        // A control of **either** kind. `Text`'s link runs and a `Button` are
        // both controls — a query for one alone would call a port that drew
        // the other a failure.
        let chatIsALink = manualControl(app, StubManual.chatLink).exists
        // Static texts only, not ``StubHouseUITests/containing(_:_:)``: this
        // one is tapped, and an `.any` first match can be the container the
        // sentence sits in — whose centre is some other row entirely.
        let chatSentence = app.staticTexts
            .matching(NSPredicate(format: "label CONTAINS %@", StubManual.chatLink))
            .firstMatch
        let chatIsStillSaid = chatSentence.waitForExistence(timeout: 10)
        evidence(app, "manual-links-ai-off")

        // ---- the dead leg: tapping its words must do nothing.
        var deadLinkWentSomewhere = false
        if chatIsStillSaid {
            chatSentence.tap()
            // Not an absence check on its own: `waitForExistence` on the
            // Rooms panel would wait out its timeout on every healthy run.
            // The piece's own heading is the discriminator — it is still
            // here if nothing was opened.
            deadLinkWentSomewhere =
                !app.staticTexts[StubManual.whatItIs].firstMatch.exists
                || startingWith(app, StubManual.roomsSectionSub).exists
            evidence(app, "manual-dead-link-tapped")
        }

        // ---- the live leg: its own paragraph, so a tap lands on the link.
        let roomsControl = manualControl(app, StubManual.roomsLink)
        let roomsWords = roomsControl.exists
            ? roomsControl
            : app.staticTexts[StubManual.roomsLink].firstMatch
        let roomsIsThere = roomsWords.waitForExistence(timeout: 10)
        var landed = false
        if roomsIsThere {
            roomsWords.tap()
            landed = startingWith(app, StubManual.roomsSectionSub).waitForExistence(timeout: 15)
            evidence(app, "manual-link-landed-on-rooms")
        }
        control("chat-ai", "{\"on\": true}", port: 6601, what: "the server's AI features on")

        XCTAssertTrue(reached, "the manual never opened the piece with the two links")
        XCTAssertFalse(
            chatIsALink,
            "a link to a screen this server does not offer was drawn as a link — "
                + "tapping it lands back on Home"
        )
        XCTAssertTrue(
            chatIsStillSaid,
            "the words of the unopenable link vanished; it should read as plain text"
        )
        XCTAssertFalse(
            deadLinkWentSomewhere,
            "tapping the words of a link this server cannot open took the screen "
                + "somewhere — an unopenable link must be inert, not just unstyled"
        )
        XCTAssertTrue(roomsIsThere, "the openable `route:` link's words were not drawn at all")
        XCTAssertTrue(landed, "tapping the openable link did not land on Settings → Rooms")
    }

    // MARK: - the live slots

    /// The `rooms` slot, and the shape Android reads wrongly.
    ///
    /// The seeded room plays `Pomegranate Waltz`, which appears nowhere else in
    /// this house. Android decodes `now_playing.title`; the server carries the
    /// track under `now_playing.song` (`schemas.py:236`), so a port that kept
    /// Android's decoder draws the status and the wake word and nothing else,
    /// and fails here.
    @MainActor
    func testManual_TheRoomsSlotNamesTheRoomAndWhatItPlays() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control("manual-rooms", StubManual.oneRoomPlaying, port: 6601, what: "a room with a song")
        let app = manualLaunch()
        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordPiRoom, piece: StubManual.piRoom),
            "the manual never opened the piece with the rooms slot"
        )
        evidence(app, "manual-rooms-slot")

        // The row is a `Button` with an explicit `accessibilityLabel`
        // (`ManualLiveView.swift:120`) whenever the document defines the
        // route it links to — which this fixture's `settings/rooms` does — so
        // the inner `Text(room.name)` is NOT in the tree: a button is one
        // element and an explicit label replaces its merged children. The
        // room's name is asserted below off that one label, with everything
        // else the line has to carry.
        let line = containing(app, StubManual.song)
        XCTAssertTrue(
            line.waitForExistence(timeout: 15),
            "the rooms slot did not say what the room is playing — the decoder is "
                + "reading `now_playing.title` and the server sends "
                + "`now_playing.song.title`"
        )
        XCTAssertTrue(
            line.label.contains(StubManual.kitchen),
            "the rooms slot did not name the room: \(line.label)"
        )
        XCTAssertTrue(
            line.label.contains(StubManual.online),
            "the room's line lost its status: \(line.label)"
        )
        XCTAssertTrue(
            line.label.contains(StubManual.jarvis),
            "the room's line lost the word it answers to: \(line.label)"
        )
    }

    /// `listedHandlers`, the phrase fallback, and the two pills.
    ///
    /// One seeded handler has examples of its own; one has none and the
    /// document does, so the fallback has to run; one is in
    /// `handler_needs_feature` on a feature this server never publishes and
    /// must be absent altogether. The plugins slot on the same page says
    /// `no plugins installed`, which is a house with none — not a read that
    /// never answered.
    @MainActor
    func testManual_ThingsToSayListsTheServersPhrasesAndFallsBack() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch()
        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordPlugins, piece: StubManual.plugins),
            "the manual never opened the piece with the live slots"
        )
        evidence(app, "manual-things-to-say")

        XCTAssertTrue(
            app.staticTexts[StubManual.timeHandler].firstMatch.waitForExistence(timeout: 15),
            "the handler list did not draw"
        )
        XCTAssertTrue(
            containing(app, StubManual.timePhrase).exists,
            "a handler the server lists with no example phrases drew none — the "
                + "manual's own phrase for it was not used"
        )
        XCTAssertTrue(
            containing(app, StubManual.musicExample).exists,
            "a handler's own example phrase was not drawn"
        )
        XCTAssertFalse(
            app.staticTexts[StubManual.chatModeHandler].firstMatch.exists,
            "a handler needing a feature this server does not publish was listed"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.pluginPill].firstMatch.exists,
            "a handler from a plugin was not marked with its origin"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.needsInternet].firstMatch.exists,
            "`requires_network` was not worded from the document's `labels.net`"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.noPlugins].firstMatch.exists,
            "the plugins slot did not say the house has none"
        )
    }

    /// `wakeInfo`/`wakeNote`: the note names the **other** words, and there is
    /// no note at all while every room answers to one.
    ///
    /// Two legs over the same slot. A note that always drew fails the first;
    /// one that never drew fails the second. The note's own text is read and
    /// checked for the default word, because the default is on the page
    /// elsewhere — in `{wake}` and in the phrase chips — and asserting its
    /// absence from the *screen* would be asserting the wrong thing.
    @MainActor
    func testManual_TheWakeWordNoteNamesOnlyTheOtherWords() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control("manual-rooms", StubManual.twoRoomsOneWord, port: 6601, what: "one wake word")
        var app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(
            containing(app, StubManual.fencepost).waitForExistence(timeout: 15),
            "Start here did not draw"
        )
        evidence(app, "manual-wake-one-word")
        XCTAssertFalse(
            startingWith(app, StubManual.wakeNoteOpening).exists,
            "the wake-word slot drew a note about other words in a house where "
                + "every room answers to the same one"
        )

        control("manual-rooms", StubManual.twoWords, port: 6601, what: "two wake words")
        app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here again")
        let note = startingWith(app, StubManual.wakeNoteOpening)
        XCTAssertTrue(
            note.waitForExistence(timeout: 15),
            "no note was drawn although one room answers to another word"
        )
        evidence(app, "manual-wake-two-words")
        XCTAssertTrue(
            note.label.contains(StubManual.alexa),
            "the note did not name the other word: \(note.label)"
        )
        XCTAssertTrue(
            note.label.contains(StubManual.study),
            "the note did not name the room that answers to it: \(note.label)"
        )
        XCTAssertFalse(
            note.label.contains(StubManual.jarvis),
            "the note named the word the manual is already using: \(note.label)"
        )
    }

    // MARK: - the load states

    /// The `NoManual` branch, at a 404 **and** at a 200 of the dashboard's own
    /// `index.html`.
    ///
    /// The second leg is the one the schema guard exists for: the static mount
    /// is `html=True`, so a server with no manual file answers this path with
    /// the single-page app. A parse that accepted it would draw a manual with
    /// no topics in it.
    @MainActor
    func testManual_AServerWithNoManualOffersTheDashboard() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control("manual-doc", "{\"how\": \"missing\"}", port: 6601, what: "no manual file")
        var app = manualLaunch()
        XCTAssertTrue(
            app.staticTexts[StubManual.noManualTitle].firstMatch.waitForExistence(timeout: 20),
            "a server that answers 404 for the manual did not say so"
        )
        evidence(app, "manual-no-manual-404")
        XCTAssertTrue(
            app.buttons[StubManual.dashboardButton].firstMatch.exists,
            "there was no way to the dashboard's own manual"
        )

        control("manual-doc", "{\"how\": \"html\"}", port: 6601, what: "the SPA's index instead")
        app = manualLaunch()
        XCTAssertTrue(
            app.staticTexts[StubManual.noManualTitle].firstMatch.waitForExistence(timeout: 20),
            "a 200 that is the dashboard's `index.html` was accepted as a manual"
        )
        evidence(app, "manual-no-manual-html")
        XCTAssertFalse(
            containing(app, StubManual.fencepost).exists,
            "a manual was drawn from a body that is not one"
        )
    }

    /// The per-server copy on disk, and the line that marks it.
    ///
    /// The relaunch is the whole point: a cache that lives only in memory
    /// passes a second read in the same process and fails this. The warn line
    /// is the other half — a copy from last time drawn with no mark on it is a
    /// manual claiming to describe a server nobody can reach.
    @MainActor
    func testManual_AnUnreachableServerShowsTheCopyFromLastTime() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        var app = manualLaunch()
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(
            containing(app, StubManual.fencepost).waitForExistence(timeout: 15),
            "the manual did not load at all, so there is no copy to keep"
        )

        control(
            "manual-fail", "{\"how\": \"unreachable\"}",
            port: 6601, what: "a manual read that fails without a 404"
        )
        app = manualLaunch()
        let drawn = containing(app, StubManual.fencepost).waitForExistence(timeout: 20)
        let marked = app.staticTexts[StubManual.cachedLine].firstMatch.exists
        evidence(app, "manual-copy-from-last-time")
        control("manual-fail", "{}", port: 6601, what: "the manual read working again")

        XCTAssertTrue(
            drawn,
            "a fresh launch against an unreachable server drew nothing — the copy "
                + "is kept in memory only and does not survive a launch"
        )
        XCTAssertTrue(
            marked,
            "the copy from last time was drawn with nothing saying so"
        )
    }

    // MARK: - accessibility

    /// Apple's audit on the three levels this screen has, plus the topic and
    /// a second piece, which are here to say where an unattributable finding
    /// on the piece page lives.
    ///
    /// Classified by **signature** with ``StubHouseUITests/floorAndRest(_:)``
    /// and never by a pinned total: `rest` is every finding Apple could
    /// attribute to an element, and this screen is where contrast on a card, a
    /// map node under 44 points and an icon-only control with no label would
    /// show up.
    ///
    /// ### Why the piece is reached from the map and not from the search
    ///
    /// The piece used to be opened by typing a keyword, and that audit
    /// reported anonymous `Contrast failed` — nine of them in train S009, one
    /// left in S012 after ``ManualTopBar`` started clearing focus.
    ///
    /// Eight of the nine were the keyboard standing over the page, and the
    /// focus clear took those away. The ninth was read as a second window —
    /// iOS keeps its full-screen keyboard host for the app's lifetime once a
    /// field has been focused — so the piece is now opened map → topic →
    /// node, before anything is typed, and `app.windows.count == 1` is
    /// asserted before the audit runs.
    ///
    /// **Train I001 ran that and the finding did not move**: one window, one
    /// anonymous `Contrast failed`, and I001's `manual-audit-piece` tree is
    /// byte-identical to S012's apart from the keyboard window that is now
    /// gone. So the window was not the cause, and the map route stays for a
    /// smaller reason — it is the shorter path to the same page and it leaves
    /// the search path free to assert the one thing that is its own, that the
    /// keyboard is down.
    ///
    /// ### The finding that was left, and what it turned out to be
    ///
    /// It was never a colour. Sampled off the audit's own screenshot, every
    /// text leaf on the piece page clears 4.5:1 — the weakest are `ok` at
    /// 5.05:1 and `needs internet` at 5.13:1, the `fgMuted` pills are 6.66:1
    /// and the phrase chips 18.8:1 — and a window scan of the whole page finds
    /// no region at all where ink reads under 4.5:1 against what it is drawn
    /// on.
    ///
    /// **Train R001 named the element**, which S009, S012 and I001 had all
    /// reported as `element: nil`: the piece page's eyebrow,
    /// `type=9 label='rooms & devices' frame=(16.0, 321.3, 94.3, 44.0)`.
    /// Nothing about the app had changed between I001 and R001, so the
    /// attribution is the audit being inconsistent about what it can resolve,
    /// not a new defect — but it is the name, and it is enough.
    ///
    /// The ink there measures `(151, 88, 0)` on white, **5.66:1**, the same
    /// pixel value to the unit as the three ``ManualCrumb`` buttons and
    /// `zoom out` eleven points above it, which the same audit of the same
    /// page leaves unnamed. What was unique to it was its **shape**: a row
    /// profile of the screenshot puts its ink in points 353.3–362.3 of a
    /// 44-point frame running 321.3–365.3 — three points off the bottom edge,
    /// 32 points of blank above — because ``ManualPiecePage`` hung the text at
    /// `alignment: .bottomLeading`, the only `.bottomLeading` in
    /// `ios/Domovoi/`. Every crumb's ink sits near its own frame's middle.
    /// That frame is now `alignment: .leading` like the rest of the app, which
    /// also stops a tap in the white 25 points above the word from navigating.
    ///
    /// Nothing is excused to get there: the classifier is untouched, no colour
    /// is repainted, and a `Contrast failed` naming this element again fails
    /// this case exactly as it did.
    ///
    /// ### `manual-audit-map`'s two findings, and why no wait went in front
    /// of them
    ///
    /// Train **I004** returned two `Contrast failed … element: nil` on the map
    /// (`testManual_AuditsClean.log:257-261`) where **I002**, auditing the
    /// same screen, returned none. The first reading of that was the
    /// scramble, and the first fix put a 1.5 s wait here. The pixels say
    /// otherwise, and they are what settled it.
    ///
    /// Both trains' screenshots were sampled in the same rectangles, at
    /// 1178 × 2556 for a 393 × 852 window
    /// (`I004/ALL/att/testManual_AuditsClean/manual-audit-map_0_115C….png`
    /// against `I002/…/manual-audit-map_0_0B78….png`):
    ///
    ///  - **Each node's card rectangle is the same picture in both, outside
    ///    the name's own frame.** `(242,242,247)` over about 93% of it and
    ///    the `.quaternary` border `(210,210,215)` over about 2.5% (rooms
    ///    2.7%, music 2.5% clustering within RGB distance 14; VERIFY-2 got
    ///    2.46% / 2.33% with its own rounding). The hub's border is
    ///    `(187,150,99)` — `brandInk` at 60% — over about 2% (2.1% here,
    ///    2.04% in VERIFY-2). The coverages depend on how the rectangle is
    ///    rounded to pixels; the colours and ratios do not. Nothing was
    ///    repainted between the two trains.
    ///  - **The glyphs are the same ink as the name.** In the name's own
    ///    frame, I004's gibberish measures `(0,0,0)` on the card at
    ///    **18.82:1** and I002's real name measures 18.82:1, the same to the
    ///    second decimal; the black pixel counts differ by 8% (2305 against
    ///    2130), which is the gibberish being a little wider. A re-lettering
    ///    has no contrast finding in it.
    ///  - **One rectangle changed hands.** The node's second line is an
    ///    element whose frame went from `{85.3 × 14.3}` to the whole
    ///    `{177.7 × 104}` card between the two trains; answer 34 making it
    ///    an `AttributedString` is the only edit it took in between, and
    ///    ``ManualMapNode``'s second line says why that is a correlation and
    ///    not a proven mechanism. The tight frame's two most-covered colours
    ///    were the card and `fgMuted`, 6.66:1. The card-sized one's are the
    ///    card and its own border: **1.35:1**, and 2.46:1 on the hub.
    ///
    /// **Why those frames, when sub-4.5 pairs are all over this picture.**
    /// They are not the only ones: in the same screenshot, in both trains, the
    /// `start here` pill holds the identical 1.35:1 border pair at 5.5% of
    /// its rectangle, the search field 1.72:1, `switch server` 1.36:1, and the
    /// `unverified` chip's fill sits 1.18:1 on white — none flagged. The model
    /// that fits every rectangle sampled (a model, not Apple's documented
    /// rule) is that the audit compares the **two most-covered colours inside
    /// a static text's frame**:
    ///
    ///  - every static text on I002's map and on I004's has a top-two pair of
    ///    5.07:1 or better, except I004's three card-sized second lines
    ///    (1.35, 1.35, 2.46) — I002 clean, I004 not;
    ///  - the pill and the field are a `Button` and a `TextField`, so they
    ///    are not measured; `unverified`'s top two are white and its ink;
    ///  - the code span's one finding, the only one that *was* attributed, is
    ///    its paragraph's top two, white and the wash, at exactly 1.108:1;
    ///  - Start here's fencepost paragraph, the same `361 × 64.3` shape, is
    ///    white and black at 21:1, and that audit returned nothing.
    ///
    /// What it does **not** explain is the count: three frames under the
    /// model, two findings. Two of the three share one pair, so an audit that
    /// de-duplicates by pair would report two — a guess, untested. Under the
    /// model the fix works because the card's only remaining text is its
    /// `Button` label, and the pill is the measured control that a button
    /// holding the same pair is not flagged; the next train is the test.
    ///
    /// So the fix is the second line leaving the tree, not a wait, and the
    /// audit runs on the state the findings came back in. A wait would have
    /// made this case blind to the one thing it has ever caught here, with
    /// nothing gained: the scrambling and settled trees are identical in
    /// accessibility terms now that both of a card's texts are hidden, so the
    /// earlier state is the same audit plus the guarantee that the hide holds
    /// while glyphs are on screen. That the hide holds at all is counted, not
    /// inferred, in ``testManual_ANodeCardIsOneElementAndNotFour()`` and
    /// ``testManual_ANodeIsNamedWhileItsNameScrambles()``.
    ///
    /// ### The two states that bisect it, which are kept
    ///
    /// R001 ran the three-state version of this case — S012's commit, not the
    /// five-state one — so the two below have still never run. They stay: they
    /// cost one tap each, and between them the five answers say which third of
    /// the page a future finding is in:
    ///
    ///  - **`manual-audit-topic`** — the topic's cluster, one tap short of a
    ///    piece. It draws this page's chrome and nothing else of it: a crumb
    ///    trail with a chevron in it and the `zoom out` control, neither of
    ///    which exists on Start here or on the map. A finding here is in the
    ///    chrome.
    ///  - **`manual-audit-room-piece`** — `A Raspberry Pi room`, a piece page
    ///    of exactly the same shape as the audited one (eyebrow, title, a
    ///    three-deep crumb trail, a live box) with different content inside
    ///    it. A finding here *and* on the piece is in the page's own frame.
    ///  - **`manual-audit-piece`** — the page as before. A finding only here
    ///    is in what only it draws: the plugin rows, the phrase chips and the
    ///    four pills.
    ///
    /// Nothing is audited less: all five states take the same strict
    /// classifier, and a colour defect in any of them still fails this case.
    @MainActor
    func testManual_AuditsClean() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        control("manual-rooms", StubManual.oneRoomPlaying, port: 6601, what: "a room with a song")
        control(
            "manual-plugins",
            "{\"plugins\": [{\"slug\": \"radio\", \"name\": \"Radio\","
                + " \"version\": \"0.4.1\", \"enabled\": true, \"status\": \"ok\"}]}",
            port: 6601, what: "a plugin row to audit"
        )
        let app = manualLaunch()

        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(
            containing(app, StubManual.fencepost).waitForExistence(timeout: 15),
            "Start here did not draw, so there was nothing to audit"
        )
        try manualAudit(app, "manual-audit-start")

        XCTAssertTrue(manualMap(app), "the map pill did not open the map")
        XCTAssertTrue(
            manualNode(app, StubManual.roomsTopic).waitForExistence(timeout: 10),
            "the map drew no nodes, so there was nothing to audit"
        )
        // **Audited the moment the nodes exist, which is mid-scramble, which
        // is the state the two findings came back on.** There was a 1.5 s
        // wait here for one commit, and it was wrong: it removed the only
        // state this screen has ever returned a finding in, so the audit
        // could not tell the fix from its absence. The audit's note has why
        // the wait is unnecessary: with both of a card's texts hidden the
        // scrambling and settled trees are the same elements, and what the
        // earlier state adds is a different picture (glyphs drawn) and the
        // guarantee that the hides hold while they are.
        try manualAudit(app, "manual-audit-map")

        // ---- the topic, which is the piece page's chrome without its page.
        //
        // Audited in its own right, and as the bisect this case's doc comment
        // describes: the crumb trail, its chevron and `zoom out` are drawn
        // here and on the piece, and nowhere else on this screen.
        XCTAssertTrue(
            manualOpenTopic(app, topic: StubManual.roomsTopic),
            "the map never opened the topic's cluster"
        )
        let roomPiece = manualNode(app, StubManual.piRoom)
        XCTAssertTrue(
            roomPiece.waitForExistence(timeout: 15),
            "the topic's cluster drew no nodes, so there was nothing to audit"
        )
        // Mid-scramble here too, and for the same reason: the cluster's cards
        // are the same ``ManualMapNode``, so auditing it settled would audit
        // one fewer state of the same view.
        //
        // **The one named exception on this level**, and it is answer 34's
        // wash in a node's second line, measured exactly as the code span's
        // is — `manualAudit(_:_:washedElement:washes:)` has the numbers and the
        // control that sits beside it in this very picture. Any other
        // element, and any other headline, still fails this case.
        try manualAudit(
            app, "manual-audit-topic",
            washedElement: StubManual.piRoomNodeSecondLine,
            washes: [("node", StubManual.nodeWash)]
        )

        // ---- a second page of the same shape, with other content in it.
        //
        // `A Raspberry Pi room` and `What plugins add` are both piece pages:
        // same eyebrow, same title, same three-deep crumb trail. What differs
        // is what the blocks draw — one live rooms box against the plugin
        // rows, the phrase chips and the four pills. A finding both report is
        // in the page's frame; one only the second reports is in the content
        // only it has.
        roomPiece.tap()
        XCTAssertTrue(
            app.staticTexts[StubManual.piRoom].firstMatch.waitForExistence(timeout: 15),
            "the map never opened the room piece's page"
        )
        // The room's line is one `Button` with a merged label, so the room's
        // name is not a static text of its own — the song is the word that
        // says the live slot answered, which is what
        // `testManual_TheRoomsSlotNamesTheRoomAndWhatItPlays` asserts on too.
        XCTAssertTrue(
            containing(app, StubManual.song).waitForExistence(timeout: 15),
            "the room piece's live slot did not draw, so there was nothing to audit"
        )
        try manualAudit(app, "manual-audit-room-piece")

        app.buttons[StubManual.zoomOut].firstMatch.tap()
        let node = manualNode(app, StubManual.plugins)
        XCTAssertTrue(
            node.waitForExistence(timeout: 15),
            "`zoom out` did not go back to the topic's cluster"
        )

        // The piece itself — still no field focused, so the app has the one
        // window every audit above saw.
        //
        // A **static text** with the piece's words is the page and nothing
        // else: the title is a `StaticText` on the piece's page (its heading
        // and its last crumb) and a `Button` at every level of the map.
        node.tap()
        XCTAssertTrue(
            app.staticTexts[StubManual.plugins].firstMatch.waitForExistence(timeout: 15),
            "the map never opened the piece's page"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.timeHandler].firstMatch.waitForExistence(timeout: 15),
            "the live slots did not draw, so there was nothing to audit"
        )
        XCTAssertEqual(
            app.windows.count, 1,
            "the piece was audited with more than the app's own window in the "
                + "hierarchy — the keyboard host iOS keeps once a field has "
                + "been focused, which train S009's nine findings were drawn "
                + "under. It is not what I001's one finding was, but an audit "
                + "of a page with a second window over it means something "
                + "other than this page"
        )
        try manualAudit(app, "manual-audit-piece")

        // ---- and the same piece opened from the search, for the keyboard.
        //
        // **The keyboard has to be gone once a search opens a piece, and that
        // is the app's job, not a tidy-up here.** In train S009 it was still
        // standing over the piece the search had just opened, and nine of that
        // run's findings are elements whose `minY` is at or below the
        // keyboard's top. Android clears focus on the same tap
        // (`ManualScreen.kt:409`) and ``ManualTopBar`` now does too, so this
        // asserts the keyboard is down rather than dismissing it itself: a
        // revert of that focus clear fails here.
        //
        // ``XCTWaiter`` and not `wait(for:timeout:)`: this file's `setUp`
        // sets `continueAfterFailure = false`, and the built-in wait records
        // its own failure, which would end the case before the line that
        // says what went wrong.
        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordPlugins, piece: StubManual.plugins),
            "the manual never opened a piece's page from the search field"
        )
        let keyboard = app.keyboards.firstMatch
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: keyboard)
        let settled = XCTWaiter().wait(for: [gone], timeout: 10)
        evidence(app, "manual-audit-piece-searched")
        XCTAssertEqual(
            settled, .completed,
            "opening a piece from the search field left the keyboard up, over "
                + "the piece it had just opened: \(keyboard.frame)"
        )
    }

    /// The map node is where this screen breaks at a big type size, so this
    /// **measures** it.
    ///
    /// Android's node is a fixed 104 dp and holds four lines of text; the port
    /// makes that height `@ScaledMetric`, and this is the case that says so. A
    /// named Dynamic Type finding has twice been a false positive on this
    /// track, so nothing here reads a headline: it compares the element's own
    /// height at the ordinary size and at AX5.
    @MainActor
    func testManual_GrowsAtAnAccessibilitySize() throws {
        let ordinary = try manualSizes(Self.ordinaryTypeSize, "manual-size-ordinary")
        let huge = try manualSizes("UICTContentSizeCategoryAccessibilityXXXL", "manual-size-ax5")

        print("MANUAL SIZES node \(ordinary.node) -> \(huge.node)")
        print("MANUAL SIZES heading \(ordinary.heading) -> \(huge.heading)")
        XCTAssertGreaterThan(
            huge.node, ordinary.node * 1.2,
            "a map node did not grow with the type size: \(ordinary.node) -> "
                + "\(huge.node). Android's node is a fixed height and clips all "
                + "four of its lines at this size."
        )
        XCTAssertGreaterThan(
            huge.heading, ordinary.heading * 1.2,
            "a piece's heading did not grow with the type size: "
                + "\(ordinary.heading) -> \(huge.heading)"
        )

        // ---- and the two leaves the audit names.
        //
        // These are not here for the screen's sake; they are the measurement
        // ``manualAudit(_:_:)`` leans on. Apple's audit says of both "User
        // will not be able to change the font size of this element", and if
        // that were true of either one the classifier that drops that finding
        // would be hiding a real defect. So the claim is checked directly: a
        // bullet drawn at `.body` and a list item drawn through
        // ``ManualRichText`` — whose `AttributedString` runs carry fonts of
        // their own, which is the one thing on this screen that could
        // genuinely pin a size — both have to grow.
        print("MANUAL SIZES bullet \(ordinary.bullet) -> \(huge.bullet)")
        print("MANUAL SIZES item \(ordinary.item) -> \(huge.item)")
        XCTAssertGreaterThan(
            huge.bullet, ordinary.bullet * 1.2,
            "the list's bullet did not grow with the type size: "
                + "\(ordinary.bullet) -> \(huge.bullet) — Apple's audit names "
                + "this leaf, and the audit case drops that finding"
        )
        XCTAssertGreaterThan(
            huge.item, ordinary.item * 1.2,
            "a list item's text did not grow with the type size: "
                + "\(ordinary.item) -> \(huge.item) — Apple's audit names this "
                + "leaf, and the audit case drops that finding"
        )
    }

    // MARK: - helpers this item owns

    /// The ordinary type size and AX5, measured the same way.
    @MainActor
    private func manualSizes(
        _ mode: String,
        _ name: String
    ) throws -> (node: CGFloat, heading: CGFloat, bullet: CGFloat, item: CGFloat) {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch(mode: mode)

        // ---- the two leaves Apple's audit names on Start here, measured
        // before anything else so that ``manualAudit(_:_:)``'s classification
        // rests on a number rather than on the headline it drops.
        XCTAssertTrue(manualStart(app), "the manual never drew Start here at \(mode)")
        // Static texts only, and not ``StubHouseUITests/containing(_:_:)``: a
        // measurement must come off the leaf that draws the words, and a
        // `.any` first match can be a container whose merged label holds them.
        let item = app.staticTexts
            .matching(NSPredicate(format: "label BEGINSWITH %@", StubManual.appOnlyItem))
            .firstMatch
        XCTAssertTrue(
            manualScroll(app, to: item),
            "the `only: app` list item never built at \(mode)"
        )
        let itemHeight = item.frame.height
        // Any of the list's bullets: they are one `Text` drawn once per item,
        // so whichever is built first measures the same glyph. At AX5 the
        // scroll above may have left a later one at the top of the tree.
        let bullet = app.staticTexts["\u{2022}"].firstMatch
        XCTAssertTrue(bullet.exists, "the list's bullet never drew at \(mode)")
        let bulletHeight = bullet.frame.height

        XCTAssertTrue(manualMap(app), "the map pill did not open the map at \(mode)")
        let centre = manualNode(app, StubManual.centreNode)
        XCTAssertTrue(
            centre.waitForExistence(timeout: 15),
            "the map's centre node never drew at \(mode)"
        )
        let node = centre.frame.height
        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordWhatItIs, piece: StubManual.whatItIs),
            "the manual never opened a piece at \(mode)"
        )
        let title = app.staticTexts[StubManual.whatItIs].firstMatch
        XCTAssertTrue(
            title.waitForExistence(timeout: 15),
            "the piece's heading never drew at \(mode)"
        )
        let heading = title.frame.height
        evidence(app, name)
        return (node, heading, bulletHeight, itemHeight)
    }

    /// One audit, classified by signature.
    ///
    /// ### Why the Dynamic Type finding is floor here even when it names a leaf
    ///
    /// ``StubHouseUITests/floorAndRest(_:)`` takes the two Dynamic Type
    /// headlines as floor only while they carry `element: nil`, which is the
    /// shape every other screen on this track has seen. Start here reports
    /// them **attributed**: train S003 named fourteen leaves — `•`,
    /// `how to start`, `what will not work`, `admin`, `ask whoever set it up`
    /// and the checklist's and list's sentences — each with the same detail,
    /// "User will not be able to change the font size of this element".
    ///
    /// That set is the reason this is not read as a defect. It is *almost
    /// every text leaf on the page*, and it does not sort by how the text is
    /// drawn: `Text("\u{2022}").font(.body)` and
    /// `Text(block.text).font(.subheadline.weight(.semibold))` are plain
    /// text-style fonts with nothing fixed about them, and they are flagged
    /// beside the ``ManualRichText`` sentences. What those leaves have in
    /// common is that they are standalone elements — Start here merges almost
    /// nothing — so this is the same finding the rest of the file sees with no
    /// element, on a screen where the audit had an element to name.
    ///
    /// The detail's claim is **measured false** rather than assumed false:
    /// ``testManual_GrowsAtAnAccessibilitySize()`` takes the height of two of
    /// these very leaves — the bullet and the `only: app` list item — at
    /// `large` and at AX5 and requires both to grow. So the signature below
    /// is narrow on purpose: this headline, this detail, and an element that
    /// is a static text (`type=48`) or none. A hit area, a contrast failure or
    /// an unlabelled control still lands in `rest` and still fails the case,
    /// and so does a Dynamic Type finding against anything that is not text.
    /// ### The `Contrast failed` this takes, and the measurement that says it
    /// is not a colour
    ///
    /// `washedElement` is a single element's **whole** label, passed by the
    /// caller that knows which element on its screen draws answer 34's wash.
    /// A `Contrast failed` naming a static text with exactly that label is
    /// printed and not failed; everything else — any other headline, any
    /// other element, the same headline against anything else on the same
    /// screen — still fails, and nothing about ``floorAndRest(_:)`` changes.
    ///
    /// **Two screens pass one**, and they are the same finding twice: a
    /// `Text` whose frame holds an opaque wash patch behind part of its
    /// words. `manual-audit-code-span` is the piece page's paragraph (below);
    /// `manual-audit-topic` is the piece **node's** second line, which train
    /// I005 named — `element: type=48 label='one room, end to end   preview '
    /// frame=(25.0, 461.3, 99.7, 30.3)`. Sampled off that train's own
    /// screenshot (`manual-audit-topic_0_*.png`, 1178 × 2556 at 3×), in that
    /// rectangle, with no clustering:
    ///
    ///  - the card `(242,242,247)` over **59.65%** and the wash
    ///    `(230,230,234)` — `manualNodeWashLight`, `0xE6E6EA`, composited
    ///    opaque — over **16.73%**: the two most-covered colours, **1.115:1**,
    ///    and the pair carries no text;
    ///  - `fgMuted` `(88,85,79)` over 3.30% of it, which on that wash is
    ///    **5.967:1** — the number `ManualRules`' token arithmetic states as
    ///    5.98, and body text needs 4.5;
    ///  - **the control sits beside it in the same picture.** The sibling
    ///    node's second line, same font, same ink, same card, no tag and so
    ///    no wash (`'and what they say'`, `{102.3 × 14.3}`) has top-two
    ///    `(242,242,247)` and `(88,85,79)` — **6.656:1** — and was not
    ///    flagged; nor was the third (`'where it listens'`, 6.656:1), nor the
    ///    card's title (18.819:1). One rectangle in three differs by one
    ///    thing, and it is the wash.
    ///
    /// So the failing pair is a tint against the card it sits on, and every
    /// pair that carries a glyph passes. Dropping the wash is the only other
    /// answer and it is the one Kamron declined (answer 34); the cost of the
    /// allowance — that element, on that screen, blind to a future colour
    /// regression — is in `hx-manual/DECISIONS.md`.
    ///
    /// It exists because train I004 reported it against the one paragraph in
    /// the manual that mixes answer 34's washed runs into prose
    /// (`testManual_ACodeSpanIsWashedAndStillAudits`,
    /// `element: type=48 label='Copy files into the obsidian folder…'
    /// frame=(16.0, 407.67, 361.0, 64.33)`). Four things say it is the
    /// audit's heuristic and not this app's ink:
    ///
    /// **Re-measurement due (revision 1).** All five readings below were
    /// taken **before answer 75** (train I004; reading 4 is I002's), when every inline word sat
    /// on `--sunken` `#F5F3F0`. Since answer 75 the paragraph's frame holds
    /// the page and three wash colours: the `code` run's `#F5F3F0` (1.108:1
    /// against the page), `admin`'s amber `#FFF4E8` (1.087:1) and the grey
    /// `#F2F1F1` under `preview` and `plugin` (1.126:1). The inks on them are
    /// arithmetic until a train samples them again: 18.96:1 for the `code`
    /// run, **5.27:1** for `warnInk` on amber, **6.61:1** for `fgMuted` on the
    /// grey (`ManualAnswersTests.testEveryWordOnItsWashReads`), every one over
    /// 4.5:1, and the strongest wash-against-page pair is still under 1.2:1
    /// and carries no text. The allowance is matched by **signature** — this
    /// element's whole label, `Contrast failed` only — so it stands; the
    /// next train's `manual-audit-code-span` screenshot is where the new
    /// numbers get read off pixels, and if the audit's finding moves to
    /// another element or headline the case reds, which is the safe
    /// direction. **Read in I007** (the section after this list): 18.961 /
    /// 5.273 / 6.589 off the patches themselves, the arithmetic to within
    /// 0.03 — and that train is the one where the finding lost its element.
    ///
    ///  1. **Every pairing the element draws was sampled off the audit's own
    ///     screenshot** (pre-answer-75; see above) (`att/.../manual-audit-code-span_0_*.png`, 1178×2556
    ///     at 3× for a 393-point screen). The wash is `(245, 243, 240)`,
    ///     opaque as answer 34 asked. On it: the `code` run's ink `(0, 0, 0)`
    ///     at **18.96:1**, `preview` and `plugin` at `(88, 85, 79)` —
    ///     `fgMuted` — at **6.71:1**, and `admin` at `(157, 83, 0)` —
    ///     `warnInk` — at **5.17:1**. The prose outside the washes is black
    ///     on white. Body text needs 4.5:1 and the weakest is 5.17:1.
    ///  2. Those were the numbers `ManualAnswersTests.testEveryRatioTheWashCommentsState`
    ///     computes from the `--sunken` tokens (18.96 / 6.72 / 5.17), so the
    ///     pixels and the arithmetic agreed to the second decimal. The pills'
    ///     arithmetic is now `testEveryWordOnItsWashReads`' (5.27 / 6.61).
    ///  3. **The same inks and the same wash pass this same audit, in this
    ///     same train, when they are their own elements.** Start here draws
    ///     an `{admin}` pill and a `code` run with tight frames of their own
    ///     (`manual-audit-start`'s tree: `label: 'admin'` at
    ///     `{34.7 × 14.3}`), and `manual-audit-start` came back
    ///     `floor=24 text-scaling=0` with no contrast finding at all. What
    ///     differs here is the element's **shape**, not its colours: one
    ///     361 × 64.3 static text whose frame holds two backgrounds, the page
    ///     and three wash patches. The strongest failing pair inside that
    ///     frame is wash against page — 1.11:1 — and it carries no text.
    ///  4. Train **I002** audited this screen before answer 34 and reported
    ///     nothing; the paragraph, the fixture and the type size are
    ///     otherwise unchanged.
    ///  5. **A paragraph of this exact shape, unwashed, audits clean in the
    ///     same train.** Start here's fencepost is a `361 × 64.3` static text
    ///     too (`manual-audit-start`'s tree, `{{16.0, 316.0}, {361.0, 64.3}}`),
    ///     and sampled in that rectangle it holds two colours worth counting:
    ///     white 84.4% and `(0,0,0)` 9.0%, so its **lowest** pair is 21:1 and
    ///     there is nothing under 4.5:1 in it anywhere. `manual-audit-start`
    ///     came back with no contrast finding. Same shape, same width, same
    ///     prose, one difference — the wash patches — and the finding follows
    ///     the wash, not the frame's size. (Re-sampled here: wash
    ///     `(245,243,240)` over 31,360 px of the failing element, wash against
    ///     page 1.108:1.)
    ///
    /// This is the R001 shape again (`testManual_AuditsClean`'s note: "It was
    /// never a colour") with the opposite ending — there the audit's
    /// attribution was inconsistent and the cause turned out to be a frame;
    /// here the frame is a **paragraph's own** four lines and what the
    /// heuristic objects to is a background inside it, so there is nothing to
    /// repaint. Reading 5 is also what separates this from the map's two
    /// findings, which looked like the same story and were not: there the
    /// element's frame was twelve times its own content and the fix was to
    /// take that element out of the tree (``ManualMapNode``'s second line).
    /// Here the element is honest and the allowance is the only answer short
    /// of dropping the wash, which is the answer Kamron gave. It is in
    /// `gx-manual/DECISIONS.md` for him, with the cost.
    ///
    /// ### The same finding with no element on it (train I007)
    ///
    /// I007 returned **one** `Contrast failed` on each of the two washed
    /// screens, as I005 had, but with `element: nil` — so the signature above
    /// could not match it and both cases redded. Attributed off the pictures
    /// (`I007/SEL/att/…/manual-audit-code-span_0_*.png` and
    /// `…/manual-audit-topic_0_*.png`, 1178 × 2556 at 3×), every text run and
    /// glyph on both screens sampled against its own background:
    ///
    ///  - **the washed runs**, each read inside its own patch: the `code` run
    ///    `(0,0,0)` on `#F5F3F0` **18.961:1**, `admin` `(157,83,0)` on amber
    ///    `#FFF4E8` **5.273:1**, `preview`/`plugin` `(88,85,79)` on grey
    ///    `#F2F1F1` **6.589:1**, the node's `preview` `(88,85,79)` on
    ///    `#E6E6EA` **5.967:1** — the numbers `ManualAnswersTests` states;
    ///  - **every leaf** on the two screens (35 and 37 static texts, buttons and images) at
    ///    **5.07:1 or better**: the weakest are the phone bar's `home`
    ///    `(151,88,0)` on `(242,242,247)` 5.07, the `map` pill 5.14, the
    ///    crumbs and `zoom out` 5.66, `unverified` 5.72;
    ///  - nothing cut by the viewport's edge on either screen (the topic's
    ///    cards end at 659.3 against a viewport ending at 770.0), and the
    ///    cards under the glide's transform read as above;
    ///  - the one sub-4.5 ink in either picture is the search field's hint,
    ///    `(197,197,199)` on white 1.72:1 — I007 ran `ebdc8351`, before
    ///    `c4476be1` put the hint ink back (7.43:1) — and it is in the same
    ///    field, same pixels, on `manual-audit-start` and `manual-audit-map`
    ///    of the same train, which returned **no** contrast finding.
    ///
    /// And against I005, which named this finding: the topic screen differs
    /// in no pixel between the two trains except the back control and the
    /// hint (the cards moved 0.1 point); the piece page differs in those and
    /// in the inline washes' tints only, inside
    /// `(80.7, 429.7)–(313.7, 472.0)`, by at most 10 per channel. Same
    /// picture, one finding, attributed once and not the next time — the
    /// shape `assertTheChromeAuditsAtAX5` met on Home in I006.
    ///
    /// So the unattributed finding is taken on the **same** signature and no
    /// wider: only where the caller names a washed element, only
    /// `Contrast failed` with exactly ``manualAnonymousContrast``'s line, only
    /// **one**, and only while the audit has not already named the washed
    /// element (one finding, whichever way Apple reports it). Because an
    /// anonymous finding could in principle be any element's, letting one
    /// through costs a measurement every time:
    /// ``manualAssertTheUnattributedContrastIsNotAColour(_:_:washedElement:washes:)``
    /// reads the washed element's own patches and every leaf on the screen
    /// off the app's own screenshot, prints each reading, and fails on any
    /// under 4.5:1 or on a wash that is not there.
    @MainActor
    private func manualAudit(
        _ app: XCUIApplication,
        _ name: String,
        washedElement: String? = nil,
        washes: [(name: String, rgb: (Int, Int, Int))] = []
    ) throws {
        let found = try auditIssues(app, name)
        let split = Self.floorAndRest(found)
        let scaling = split.rest.filter { Self.isManualDynamicTypeOnText($0) }
        let notScaling = split.rest.filter { !Self.isManualDynamicTypeOnText($0) }
        let washed = washedElement.map { element in
            notScaling.filter { Self.isContrastOnTheWashedElement($0, element) }
        } ?? []
        // At most one line, and only when the named route took nothing: a
        // second identical anonymous line stays in `rest`.
        var unattributed = 0
        let rest = notScaling.filter { line in
            if washed.contains(line) { return false }
            if washedElement != nil, washed.isEmpty, unattributed == 0,
               line == Self.manualAnonymousContrast {
                unattributed += 1
                return false
            }
            return true
        }
        evidence(app, name)
        print(
            "AUDIT [\(name)] floor=\(split.floor) text-scaling=\(scaling.count) "
                + "washed-element=\(washed.count) washed-element-unattributed=\(unattributed)"
        )
        for line in washed { print("AUDIT [\(name)] washed element, measured: \(line)") }
        XCTAssertTrue(
            rest.isEmpty,
            "\(name): Apple's audit named \(rest.count) element(s): "
                + rest.joined(separator: " || ")
        )
        if unattributed > 0, let washedElement {
            manualAssertTheUnattributedContrastIsNotAColour(
                app, name, washedElement: washedElement, washes: washes
            )
        }
    }

    /// `Contrast failed` as ``auditIssues`` writes it when Apple attached no
    /// element. Matched whole, so another headline or detail does not match.
    static let manualAnonymousContrast = "Contrast failed: Contrast failed for element | element: nil"

    /// WCAG's floor for body text, which every reading below is held to
    /// whatever the text's size.
    private static let manualContrastFloor = 4.5

    /// What an unattributed `Contrast failed` costs: the screen measured off
    /// the app's own screenshot, so the finding that was let through cannot
    /// be a colour.
    ///
    /// Two readings, both printed:
    ///
    ///  1. **Each wash the caller names, inside the washed element**, with
    ///     ``manualInkOnWash(_:in:scale:wash:)`` — the ink over that patch against
    ///     the patch. A wash that is not there fails too: the allowance's
    ///     reason would be gone.
    ///  2. **Every static text, button and image on the window**, with
    ///     ``manualLeafInk(_:in:)`` — the most-contrasting colour that covers
    ///     at least 0.5% of the leaf's frame against its most common colour.
    ///     A leaf whose frame holds nothing but its paper is printed and
    ///     skipped; nothing else is.
    ///
    /// The search field's hint is not a leaf and is not read here; it is
    /// `testShell_TheHintInkIsReadable`'s, and this train's own start and map
    /// audits show the audit does not report it.
    @MainActor
    private func manualAssertTheUnattributedContrastIsNotAColour(
        _ app: XCUIApplication,
        _ name: String,
        washedElement: String,
        washes: [(name: String, rgb: (Int, Int, Int))]
    ) {
        let floor = Self.manualContrastFloor
        guard let whole = app.screenshot().image.cgImage, app.frame.width > 0 else {
            XCTFail(
                "\(name): an unattributed `Contrast failed` was let through on the "
                    + "washed element's signature and there is no screenshot to measure"
            )
            return
        }
        let window = app.frame
        let element = manualExact(app.staticTexts, washedElement)
        guard element.exists, window.contains(element.frame), element.frame.width > 2 else {
            print("=== \(name): THE WASHED ELEMENT IS NOT WHOLLY ON SCREEN — tree follows ===")
            print(app.debugDescription)
            XCTFail(
                "\(name): an unattributed `Contrast failed` was let through because "
                    + "this screen draws the washed element '\(washedElement)', and it "
                    + "is not wholly on screen (\(element.exists ? "\(element.frame)" : "absent"))"
            )
            return
        }
        XCTAssertFalse(
            washes.isEmpty,
            "\(name): the caller named a washed element and no wash to read in it"
        )
        for wash in washes {
            let reading = manualInkOnWash(
                whole,
                in: manualPixelBox(whole, window, element.frame),
                scale: CGFloat(whole.width) / window.width,
                wash: wash.rgb
            )
            guard let reading else {
                evidence(app, "\(name)-no-\(wash.name)-wash")
                XCTFail(
                    "\(name): the \(wash.name) wash \(wash.rgb) is not in the washed "
                        + "element at \(element.frame), or carries no ink — the "
                        + "allowance's reason is gone"
                )
                continue
            }
            print(
                "AUDIT [\(name)] measured: \(wash.name) wash \(wash.rgb) carries ink "
                    + "\(reading.ink) at " + String(format: "%.3f", reading.ratio)
                    + ":1 (\(reading.rows) rows of it)"
            )
            XCTAssertGreaterThanOrEqual(
                reading.ratio, floor,
                "\(name): the ink on the \(wash.name) wash measures "
                    + String(format: "%.3f", reading.ratio) + ":1 (\(reading.ink) on "
                    + "\(wash.rgb)) — under \(floor):1, so the unattributed contrast "
                    + "finding is this colour. Picture attached."
            )
        }
        var read = 0
        for kind in [XCUIElement.ElementType.staticText, .button, .image] {
            for leaf in app.descendants(matching: kind).allElementsBoundByIndex {
                guard leaf.exists else { continue }
                let box = leaf.frame.intersection(window)
                guard !box.isNull, box.width >= 2, box.height >= 2 else { continue }
                guard let reading = manualLeafInk(whole, in: manualPixelBox(whole, window, box)) else {
                    print("AUDIT [\(name)] measured: '\(leaf.label)' \(box) holds only its paper")
                    continue
                }
                read += 1
                print(
                    "AUDIT [\(name)] measured: '\(leaf.label)' \(box) ink \(reading.ink) on "
                        + "\(reading.paper) " + String(format: "%.3f", reading.ratio) + ":1"
                )
                XCTAssertGreaterThanOrEqual(
                    reading.ratio, floor,
                    "\(name): '\(leaf.label)' at \(box) measures "
                        + String(format: "%.3f", reading.ratio) + ":1 (\(reading.ink) on "
                        + "\(reading.paper)) while Apple returned an unattributed "
                        + "`Contrast failed` this case let through on the washed "
                        + "element's signature — the finding may be this leaf's. "
                        + "Picture attached."
                )
            }
        }
        XCTAssertGreaterThan(read, 0, "\(name): no leaf on the screen could be measured")
    }

    /// `Contrast failed` against one named static text, and nothing else —
    /// the signature ``manualAudit(_:_:washedElement:washes:)`` explains.
    ///
    /// Written against the exact text ``StubHouseUITests/oneLine(_:)``
    /// produces. A finding with no element, or against a different element,
    /// or with a different headline, does not match and still fails.
    private static func isContrastOnTheWashedElement(
        _ line: String,
        _ element: String
    ) -> Bool {
        line.hasPrefix("Contrast failed: ")
            && line.contains("| element: type=48 label='\(element)' frame=(")
    }

    /// The one signature above, as a test of a line ``auditIssues`` wrote:
    /// `"<compact>: <detail> | element: <oneLine>"`.
    private static func isManualDynamicTypeOnText(_ line: String) -> Bool {
        let detail = "User will not be able to change the font size of this element"
        let headlines = [
            "Dynamic Type font sizes are partially unsupported",
            "Dynamic Type font sizes are unsupported",
        ]
        guard headlines.contains(where: { line.hasPrefix("\($0): \(detail)") }) else {
            return false
        }
        // `type=48` is `XCUIElement.ElementType.staticText`, as
        // ``StubHouseUITests/oneLine(_:)`` writes it.
        return line.hasSuffix("| element: nil") || line.contains("| element: type=48 ")
    }

    /// Scroll a `LazyVStack`'s unbuilt foot into existence.
    ///
    /// Not ``StubHouseUITests/scrollUntilBuilt(_:_:flicks:)``: that one drives
    /// `app.collectionViews`, and the manual's pages are a `ScrollView` over a
    /// `LazyVStack` with no collection view anywhere in them. The foot is
    /// unbuilt in exactly the same way, so the loop is the same one.
    @MainActor
    private func manualScroll(
        _ app: XCUIApplication,
        to element: XCUIElement,
        flicks: Int = 25
    ) -> Bool {
        guard let scroller = manualScroller(app) else {
            print("=== NO MANUAL SCROLL VIEW — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            scroller.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// The scroll view the manual's page is in.
    ///
    /// **Not `app.scrollViews.firstMatch`.** The phone's bottom tab strip is
    /// a scroll view too, and in train S006 the first match was that strip
    /// (`Find the ScrollView at {{0.0, 706.0}, {393.0, 146.0}}`):
    /// twenty-five flicks went into the tab bar and the manual never moved.
    /// The manual's scroller is the one above the strip, so this takes the
    /// top-most of them — the keyboard rule
    /// (`keyboards.firstMatch.frame.minY`) applied to a scroll view.
    @MainActor
    private func manualScroller(_ app: XCUIApplication) -> XCUIElement? {
        guard app.scrollViews.firstMatch.waitForExistence(timeout: 20) else { return nil }
        return app.scrollViews.allElementsBoundByIndex
            .filter { $0.exists && $0.frame.height > 1 }
            .min(by: { $0.frame.minY < $1.frame.minY })
    }

    /// Flick back up until a control of the top bar can be tapped.
    ///
    /// At an accessibility size the bar scrolls **with** the page
    /// (``ManualView``'s `ready`, which is why: pinned, the bar alone was 310
    /// of this screen's 400 points), so a case that has flicked to the foot
    /// of a page has pushed the pills and the search field off the top. Below
    /// those sizes the bar is pinned, the control is hittable already and
    /// this returns without touching anything.
    @MainActor
    private func manualToTop(
        _ app: XCUIApplication,
        until control: XCUIElement,
        flicks: Int = 25
    ) -> Bool {
        if control.exists && control.isHittable { return true }
        guard let scroller = manualScroller(app) else { return false }
        for _ in 0..<flicks {
            if control.exists && control.isHittable { return true }
            scroller.swipeDown(velocity: .slow)
        }
        return control.exists && control.isHittable
    }

    /// Put the screen on Start here, whatever level the last launch left in
    /// `@SceneStorage`.
    @MainActor
    private func manualStart(_ app: XCUIApplication) -> Bool {
        let pill = app.buttons[StubManual.startPill].firstMatch
        guard pill.waitForExistence(timeout: 30), manualToTop(app, until: pill) else {
            print("=== NO MANUAL TOP BAR — tree follows ===")
            print(app.debugDescription)
            return false
        }
        pill.tap()
        return app.staticTexts[StubManual.startTitle].firstMatch.waitForExistence(timeout: 15)
    }

    /// Put the screen on the map.
    @MainActor
    private func manualMap(_ app: XCUIApplication) -> Bool {
        let pill = app.buttons[StubManual.mapPill].firstMatch
        guard pill.waitForExistence(timeout: 30), manualToTop(app, until: pill) else {
            print("=== NO MANUAL TOP BAR — tree follows ===")
            print(app.debugDescription)
            return false
        }
        pill.tap()
        return manualNode(app, StubManual.centreNode).waitForExistence(timeout: 15)
            || manualNode(app, StubManual.roomsTopic).waitForExistence(timeout: 5)
    }

    /// The words of a link, as whatever kind of control they were drawn as.
    ///
    /// A span's link inside a `Text` reports as `.link`; the same words drawn
    /// as a `Button` report as `.button`. The rule under test is whether the
    /// words are tappable **at all** (``ManualModel/linkIsLive(kind:target:)``),
    /// so this accepts either and a port is not failed for the mechanism it
    /// picked.
    @MainActor
    private func manualControl(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(
                NSPredicate(
                    format: "label == %@ AND (elementType == %@ OR elementType == %@)",
                    label,
                    NSNumber(value: XCUIElement.ElementType.link.rawValue),
                    NSNumber(value: XCUIElement.ElementType.button.rawValue)
                )
            )
            .firstMatch
    }

    /// **Every case in this file launches through here**, and the reason is
    /// the float.
    ///
    /// Answer 31 gives each map card a `repeatForever` animation, and
    /// XCUITest waits for the app under test to go idle before every query.
    /// Ten animations that never finish are a quiescence risk for the whole
    /// screen — not a hypothetical one: the cost of being wrong is every
    /// `testManual_*` case idle-timing-out on a shared train, which is a
    /// worse outcome than not exercising an ornament. So this passes
    /// `-domovoi-manual-float-off` (``ManualFloatClock``, DEBUG-only and read
    /// once at launch) and one case of its own,
    /// ``testManual_TheMapWorksWhileItsNodesFloat()``, passes `-on` and drives
    /// the map with the cards moving. If forever-animating nodes do hang
    /// XCUITest, that is the one case that hangs.
    ///
    /// This is
    /// ``StubHouseUITests/launch(mode:paired:server:port:strip:push:pushDelay:args:)``
    /// with the manual's own knobs on it. It used to be that function's body
    /// written out again, because `launchArguments` has to be set before
    /// `launch()` and the shared helper took no extra arguments; the `args:`
    /// parameter this unit's DECISIONS asked for landed in the same batch, so
    /// only the knobs are left here.
    @MainActor
    private func manualLaunch(
        mode: String = StubHouseUITests.ordinaryTypeSize,
        scrambleMS: Int? = nil,
        float: Bool = false
    ) -> XCUIApplication {
        // **`launch(args:)` and not a copy of its body.** This wrote the whole
        // launcher out again because `launchArguments` must be set before
        // `launch()` and there was no way to add one argument — a duplicate
        // every case in this file goes through, so load-bearing and silently
        // drifting from the real one. The `args:` parameter landed on the
        // transfers unit's branch in this same batch (this unit's REPORT asked
        // for it), so the duplicate is gone on the integration.
        //
        // Both seams are DEBUG-only on the app's side and read once at launch,
        // so neither can leak into the next case.
        var seams = [float ? "-domovoi-manual-float-on" : "-domovoi-manual-float-off"]
        if let scrambleMS {
            seams += ["-domovoi-manual-scramble", "\(scrambleMS)"]
        }
        return launch(
            mode: mode,
            push: "Manual",
            pushDelay: Self.pushDelay,
            args: seams
        )
    }

    /// The system's leading-edge swipe back, driven for real.
    ///
    /// One helper and not a drag written out in every case: it is the gesture
    /// answers 32 and 74 are *about*, and copies of it would be chances for one
    /// of them to stop being a left-edge drag. Since answer 74 the drag is
    /// met first by the manual's own recogniser (`ManualBackSwipe`), which
    /// goes up a level on a drag lifted past a quarter of the width — this
    /// one travels from 0.004 to 0.95 of it.
    ///
    /// ### The numbers are copied from the one drag in this suite that is
    /// measured to pop a screen
    ///
    /// What **ran** in train I004 was this item's own drag at
    /// `app.coordinate`, `dx: 0.01` out to `0.9` (`3f6ca5f7:459,462`), and
    /// that train is why the numbers are not this item's any more. (An
    /// intermediate `0.02`/`0.8` sat here between `84f60193` and this commit
    /// and has never run anywhere — an earlier version of this comment
    /// credited I004 with it.) Two edge drags ran in that train and only one
    /// worked:
    ///
    ///  * ``StubHouseUITests/roomsLeaveRoom(_:_:swipe:)`` —
    ///    `app.windows.firstMatch.coordinate`, `dx: 0.004`, out to `0.95`,
    ///    pressed 80 ms — popped the room and
    ///    `testRooms_OpeningARoomShowsItsOverview` **passed**;
    ///  * this item's, at `(3.93, 426)` out to `353.7` —
    ///    `testManual_ASwipeFromStartHereLeavesTheManual` failed, and the
    ///    train's screen recording says the gesture did nothing at all: the
    ///    last frame, 0.74 s into a 350-point drag, is the manual drawn
    ///    exactly as the frame before the touch
    ///    (`att/testManual_ASwipeFromStartHereLeavesTheManual/Screen_Recording_*.mp4`,
    ///    last frame at 18.158 s against a press beginning at 17.42 s). No
    ///    transition started; nothing slid.
    ///
    /// The point itself was delivered where this file asked for it — the
    /// synthesised event in the same folder carries `coordinate.x 3.93`
    /// (`0.01 × 393`, exactly), `coordinate.y 426.0` (`0.5 × 852`, exactly)
    /// and `destination.x 353.7` (`0.9 × 393`) — so the numbers are **not**
    /// known to be the cause, and two things that sound like reasons are not
    /// reasons either:
    ///
    ///  * **`app` versus the window was not it in that run.** `y = 426.0` is
    ///    half of the 852-point window to the decimal and `x` is `0.01 × 393`
    ///    to the decimal, so the application element's frame and the window's
    ///    were the same rectangle. Taking the window is still right — the
    ///    application's frame is the union of its windows, and the audit
    ///    cases' trees do carry a second one — but it is insurance, not a
    ///    diagnosis. What is left between the two drags is `x 3.93 → 1.57`
    ///    and `353.7 → 373.35`.
    ///  * **Nothing in the app competes for the gesture.** There is no
    ///    `DragGesture`, `simultaneousGesture`, `highPriorityGesture`,
    ///    `interactiveDismissDisabled` or `navigationBarBackButtonHidden`
    ///    anywhere under `ios/Domovoi/Manual/` or `ios/Domovoi/Shell/`;
    ///    `ShellModel.path`'s setter is a plain assignment
    ///    (`ShellNavigation.swift:72-74`), so a pop is not written back over;
    ///    and the harness's seeded push is behind a `seededPush` guard
    ///    (`WorkspaceView.swift:48-56`), so it cannot re-push what a pop took
    ///    off. I004's own tree has a standard `Back` button in the stack's bar
    ///    (`manual-swipe-on-start-here-tree…txt:17-18`).
    ///
    /// What is **not** the same about the two drags is what they have to pop,
    /// and that is the difference worth reading first if this fails again:
    /// rooms' working drag pops a destination-based `NavigationLink`
    /// (`RoomsView.swift:62`, `:299`), while a swipe from Start here has to
    /// pop a `Route` element off ``ShellModel/path``
    /// (`WorkspaceView.swift:161`) — which no case in this suite has ever
    /// done, by a gesture or by anything else. That is the shell's seam, not
    /// this item's, and it is in `gx-manual/REPORT.md` under "Needs a shared
    /// change".
    ///
    /// `dx: 0.004` and not 0, so the press lands inside the window's frame
    /// where the leading-edge recogniser can see it, and `0.95` so the drag
    /// is long enough to be committed rather than cancelled back. The press
    /// is held for 80 ms first: a drag with no press is a flick, and
    /// `UIScreenEdgePanGestureRecognizer` wants the touch to settle on the
    /// edge before it moves.
    ///
    /// It returns nothing: what the gesture *did* is the caller's claim, and
    /// every caller here reads it off the screen rather than off a return
    /// value.
    @MainActor
    private func manualSwipeBack(_ app: XCUIApplication) {
        let window = app.windows.firstMatch
        window.coordinate(withNormalizedOffset: CGVector(dx: 0.004, dy: 0.5))
            .press(
                forDuration: 0.08,
                thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5))
            )
    }

    /// Flick the manual's page until an element is wholly on screen and
    /// hittable — what a pixel reading needs, where a tap would scroll by
    /// itself.
    @MainActor
    private func manualScrollOnScreen(
        _ app: XCUIApplication,
        _ element: XCUIElement,
        flicks: Int = 12
    ) -> Bool {
        let window = app.windows.firstMatch.frame
        func onScreen() -> Bool {
            element.exists && element.isHittable && window.contains(element.frame)
        }
        guard let scroller = manualScroller(app) else { return onScreen() }
        for _ in 0..<flicks {
            if onScreen() { return true }
            if element.frame.midY > window.midY {
                scroller.swipeUp(velocity: .slow)
            } else {
                scroller.swipeDown(velocity: .slow)
            }
        }
        if !onScreen() {
            print("=== \(element) NOT ON SCREEN after \(flicks) flicks: \(element.frame) — tree follows ===")
            print(app.debugDescription)
        }
        return onScreen()
    }

    /// Every pixel of the screen inside a rectangle, as 8-bit RGB.
    ///
    /// The screenshot is in pixels and the frame in points, and the factor is
    /// read rather than assumed — the Shell cases' `inkContrast` reads it the
    /// same way. Nil when there is no screenshot or the rectangle is off it.
    @MainActor
    private func manualPixels(_ app: XCUIApplication, in box: CGRect) -> [(Int, Int, Int)]? {
        manualCrop(app, in: box)?.pixels
    }

    /// ``manualPixels(_:in:)`` with the crop's width in pixels and the
    /// screenshot's scale, for a reading that needs rows.
    @MainActor
    private func manualCrop(
        _ app: XCUIApplication,
        in box: CGRect
    ) -> (width: Int, scale: CGFloat, pixels: [(Int, Int, Int)])? {
        guard let whole = app.screenshot().image.cgImage, app.frame.width > 0 else { return nil }
        let scaleX = CGFloat(whole.width) / app.frame.width
        guard let read = manualRead(whole, manualPixelBox(whole, app.frame, box)) else { return nil }
        return (read.width, scaleX, read.pixels)
    }

    /// A rectangle in points as the rectangle of pixels it covers in a
    /// screenshot of the window, rounded outwards and clipped to the image.
    private func manualPixelBox(_ whole: CGImage, _ window: CGRect, _ box: CGRect) -> CGRect {
        guard window.width > 0, window.height > 0 else { return .null }
        let scaleX = CGFloat(whole.width) / window.width
        let scaleY = CGFloat(whole.height) / window.height
        return CGRect(
            x: box.minX * scaleX, y: box.minY * scaleY,
            width: box.width * scaleX, height: box.height * scaleY
        ).integral.intersection(CGRect(x: 0, y: 0, width: whole.width, height: whole.height))
    }

    /// The pixels of one rectangle of an image already taken, row by row,
    /// with the crop's width — so a case that reads many rectangles of one
    /// screen reads them all off the **same** picture.
    private func manualRead(
        _ whole: CGImage,
        _ wanted: CGRect
    ) -> (width: Int, pixels: [(Int, Int, Int)])? {
        guard !wanted.isNull, let crop = whole.cropping(to: wanted),
              crop.width > 2, crop.height > 2
        else { return nil }
        let width = crop.width
        let height = crop.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drew = bytes.withUnsafeMutableBytes { raw -> Bool in
            guard let context = CGContext(
                data: raw.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(crop, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drew else { return nil }
        // Walked by stride, never by an index into a collection that could
        // be shorter than the loop thinks: a trap here ends the whole run.
        var out: [(Int, Int, Int)] = []
        out.reserveCapacity(width * height)
        var it = bytes.makeIterator()
        while let r = it.next(), let g = it.next(), let b = it.next(), it.next() != nil {
            out.append((Int(r), Int(g), Int(b)))
        }
        guard out.count == width * height else { return nil }
        return (width, out)
    }

    /// The most common colour inside a rectangle — the surface a short word
    /// sits on, since its glyphs are the minority of the pixels.
    @MainActor
    private func manualMostCommon(_ app: XCUIApplication, in box: CGRect) -> (Int, Int, Int)? {
        guard let pixels = manualPixels(app, in: box) else { return nil }
        var counts: [Int: Int] = [:]
        for (r, g, b) in pixels { counts[(r << 16) | (g << 8) | b, default: 0] += 1 }
        guard let top = counts.max(by: { $0.value < $1.value }) else { return nil }
        return ((top.key >> 16) & 0xFF, (top.key >> 8) & 0xFF, top.key & 0xFF)
    }

    /// Whether a rectangle holds a **patch** of one colour, rather than
    /// pixels of it: the widest run of columns, in any row, over which the
    /// colour has stood at least `side` pixels tall — so `widest >= side`
    /// means a `side × side` square of it exists. `side` is two points at the
    /// screenshot's own scale (six pixels at 3×).
    ///
    /// A pixel counts when it is within 2 of `colour` on every channel
    /// **and** nearer `colour` than every colour in `unlike` (summed over the
    /// channels), so a neighbouring wash a few units away cannot stand in for
    /// it. Built row by row from an iterator, never by indexing a pixel
    /// array: a trap here ends the whole run.
    @MainActor
    private func manualWashBlock(
        _ app: XCUIApplication,
        in box: CGRect,
        near colour: (Int, Int, Int),
        unlike others: [(Int, Int, Int)]
    ) -> (side: Int, widest: Int)? {
        guard let crop = manualCrop(app, in: box), crop.width > 0 else { return nil }
        func distance(_ a: (Int, Int, Int), _ b: (Int, Int, Int)) -> Int {
            abs(a.0 - b.0) + abs(a.1 - b.1) + abs(a.2 - b.2)
        }
        func counts(_ pixel: (Int, Int, Int)) -> Bool {
            guard manualNear(pixel, colour) else { return false }
            let mine = distance(pixel, colour)
            return others.allSatisfy { distance(pixel, $0) > mine }
        }
        let side = max(3, Int((2 * crop.scale).rounded(.up)))
        var heights = [Int](repeating: 0, count: crop.width)
        var row: [(Int, Int, Int)] = []
        row.reserveCapacity(crop.width)
        var widest = 0
        var it = crop.pixels.makeIterator()
        while let pixel = it.next() {
            row.append(pixel)
            guard row.count == crop.width else { continue }
            heights = zip(heights, row).map { counts($1) ? $0 + 1 : 0 }
            var run = 0
            for height in heights {
                run = height >= side ? run + 1 : 0
                widest = max(widest, run)
            }
            row.removeAll(keepingCapacity: true)
        }
        return (side, widest)
    }

    /// Within 2 on every channel — tight enough that the four washes this
    /// file tells apart (`#FFF4E8`, `#F2F1F1`, `#F5F3F0`, `#F2F2F7`) are four.
    private func manualNear(_ a: (Int, Int, Int), _ b: (Int, Int, Int)) -> Bool {
        abs(a.0 - b.0) <= 2 && abs(a.1 - b.1) <= 2 && abs(a.2 - b.2) <= 2
    }

    /// WCAG 2's contrast between two sRGB colours.
    private func manualRatio(_ a: (Int, Int, Int), _ b: (Int, Int, Int)) -> Double {
        func channel(_ value: Int) -> Double {
            let v = Double(value) / 255
            return v <= 0.03928 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4)
        }
        func luminance(_ c: (Int, Int, Int)) -> Double {
            0.2126 * channel(c.0) + 0.7152 * channel(c.1) + 0.0722 * channel(c.2)
        }
        let (x, y) = (luminance(a), luminance(b))
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)
    }

    private func manualKey(_ c: (Int, Int, Int)) -> Int { (c.0 << 16) | (c.1 << 8) | c.2 }
    private func manualRGB(_ key: Int) -> (Int, Int, Int) {
        ((key >> 16) & 0xFF, (key >> 8) & 0xFF, key & 0xFF)
    }

    /// One leaf's ink against its paper: the paper is the most common colour
    /// in the leaf's pixels, the ink is whichever colour covering at least
    /// 0.5% of them contrasts most with it. Nil when nothing but the paper
    /// covers that much — a frame with nothing drawn in it.
    ///
    /// The 0.5% is what keeps a glyph's anti-aliased edge (a light grey over
    /// a handful of pixels) from being read as the leaf's ink. On the I007
    /// pictures every text leaf's ink covers 3% or more of it, and the
    /// thinnest reading is a node card's whole button, whose title ink is
    /// 0.90% of its 177 × 104 points.
    private func manualLeafInk(
        _ whole: CGImage,
        in pixels: CGRect
    ) -> (ink: (Int, Int, Int), paper: (Int, Int, Int), ratio: Double)? {
        guard let read = manualRead(whole, pixels) else { return nil }
        var counts: [Int: Int] = [:]
        for pixel in read.pixels { counts[manualKey(pixel), default: 0] += 1 }
        guard let top = counts.max(by: { $0.value < $1.value }) else { return nil }
        let paper = manualRGB(top.key)
        let enough = Double(read.pixels.count) * 0.005
        let ink = counts
            .filter { $0.key != top.key && Double($0.value) >= enough }
            .map { (rgb: manualRGB($0.key), ratio: manualRatio(manualRGB($0.key), paper)) }
            .max(by: { $0.ratio < $1.ratio })
        guard let ink else { return nil }
        return (ink.rgb, paper, ink.ratio)
    }

    /// The ink drawn **over one wash patch** inside a rectangle, against the
    /// wash — the pair a glyph on that patch is read on.
    ///
    /// Row by row, with the paper (the rectangle's most common colour) as
    /// the edge of every patch:
    ///
    ///  - a pixel is **wash** when it is within 2 of `wash` on every channel
    ///    and the column above it has held wash for `side` rows (two points
    ///    at the screenshot's scale), so the one-pixel light-grey edge of a
    ///    prose glyph is never a patch;
    ///  - a row is **inside** a patch from a run of `side` such pixels until
    ///    the next paper pixel; every pixel in between that is neither wash
    ///    nor paper is the patch's ink or its anti-aliasing;
    ///  - the ink is the colour among those covering at least 1% of the
    ///    patch's pixels that contrasts most with the wash.
    ///
    /// Nil when fewer than `side` rows hold a patch — the wash is not there —
    /// or when nothing over the patch covers 1% of it.
    /// The same reading, run over the I007 pictures, gives `admin` 5.273,
    /// `preview`/`plugin` 6.589, the `code` run 18.961 and the node's
    /// `preview` 5.967, which are the per-patch numbers taken by hand.
    /// Built from iterators over rows, never by indexing the pixel array.
    private func manualInkOnWash(
        _ whole: CGImage,
        in pixels: CGRect,
        scale: CGFloat,
        wash: (Int, Int, Int)
    ) -> (ink: (Int, Int, Int), ratio: Double, rows: Int)? {
        guard let read = manualRead(whole, pixels), read.width > 0 else { return nil }
        var counts: [Int: Int] = [:]
        for pixel in read.pixels { counts[manualKey(pixel), default: 0] += 1 }
        guard let top = counts.max(by: { $0.value < $1.value }) else { return nil }
        let paper = manualRGB(top.key)
        let side = max(3, Int((2 * scale).rounded(.up)))
        var heights = [Int](repeating: 0, count: read.width)
        var row: [(Int, Int, Int)] = []
        row.reserveCapacity(read.width)
        var over: [Int: Int] = [:]
        var washPixels = 0
        var rows = 0
        var it = read.pixels.makeIterator()
        while let pixel = it.next() {
            row.append(pixel)
            guard row.count == read.width else { continue }
            heights = zip(heights, row).map { manualNear($1, wash) ? $0 + 1 : 0 }
            var inside = false
            var entered = false
            var run = 0
            for (height, colour) in zip(heights, row) {
                if manualNear(colour, wash) {
                    run = height >= side ? run + 1 : 0
                    if height >= side { washPixels += 1 }
                    if run >= side { inside = true; entered = true }
                    continue
                }
                run = 0
                if manualNear(colour, paper) {
                    inside = false
                } else if inside {
                    over[manualKey(colour), default: 0] += 1
                }
            }
            if entered { rows += 1 }
            row.removeAll(keepingCapacity: true)
        }
        guard rows >= side else { return nil }
        let enough = Double(washPixels + over.values.reduce(0, +)) * 0.01
        let ink = over
            .filter { Double($0.value) >= enough }
            .map { (rgb: manualRGB($0.key), ratio: manualRatio(manualRGB($0.key), wash)) }
            .max(by: { $0.ratio < $1.ratio })
        guard let ink else { return nil }
        return (ink.rgb, ink.ratio, rows)
    }

    /// An element by its **whole** label, of any kind.
    ///
    /// ``manualNode(_:_:)`` matches a node by the start of its label, which
    /// is what every other case wants — a node reads as
    /// `"<title>: <subtitle>"`. A case about the scramble needs the opposite:
    /// the label has to be the real name *entire*, because a prefix match
    /// would be satisfied by a name with glyphs stuck on the end of it.
    @MainActor
    private func manualExact(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        manualExact(app.descendants(matching: .any), label)
    }

    /// The same whole-label match inside one kind of element. A predicate
    /// rather than a subscript for any label, because a subscript identifier
    /// over 128 characters raises instead of matching (I007's
    /// `testManual_TheLittleWordsSitOnAndroidsWashes`).
    @MainActor
    private func manualExact(_ query: XCUIElementQuery, _ label: String) -> XCUIElement {
        query.matching(NSPredicate(format: "label == %@", label)).firstMatch
    }

    /// Whether a drawn string is **one frame of this name scrambling**
    /// rather than a word of its own.
    ///
    /// Train I005 measured that a node's card carries the glyph overlay's
    /// leaf while its name is scrambling: mid-scramble the centre node held
    /// `["Domovoi", "Domovo9", "your home"]` with both
    /// `.accessibilityHidden(true)`s in place, and there is no modifier that
    /// takes a leaf out of a `Button`'s label (DECISIONS D-HX2). So a case
    /// that reads the leaves under a card has to tell that frame apart from a
    /// word the card does not draw, and this is how.
    ///
    /// It is `manualScrambleFrame(of:frame:steps:glyph:)`'s own contract
    /// (`ManualRules.swift:744`), which holds at every frame:
    ///
    ///  * the character **count** never changes;
    ///  * a **space** is never replaced;
    ///  * a character that has not settled yet is one of the glyph alphabet
    ///    (``StubManual/scrambleGlyphs``).
    ///
    /// No other word on the map satisfies all three against this name: the
    /// alphabet is upper case, digits and symbols, and a sibling node's name,
    /// a crumb or a tag is a different length or carries lower-case letters
    /// in places this name does not.
    ///
    /// The glyph alphabet is **spelled out** here rather than imported, this
    /// file's standing reason: the UI test target does not import DomovoiKit,
    /// so a renamed or re-spelled constant in the app must not silently
    /// follow. `ManualAnswersTests` pins the alphabet itself in the package.
    @MainActor
    private func manualIsAScrambleFrame(_ drawn: String, of name: String) -> Bool {
        let drawn = Array(drawn)
        let name = Array(name)
        guard drawn.count == name.count else { return false }
        for (at, character) in drawn.enumerated() {
            if character == name[at] { continue }
            // A space that came back as something else is not this effect.
            if name[at] == " " { return false }
            if !StubManual.scrambleGlyphs.contains(character) { return false }
        }
        return true
    }

    /// Wait for an element's accessibility **value** to become this string.
    ///
    /// Only one thing in this item publishes a value and only while a case
    /// has asked for a slow scramble: ``ManualMapNode/scrambleState``, which
    /// is `"scrambling"` then `"settled"`. It is a value and not a label
    /// because the label is the node's real name, and that claim is the other
    /// half of the same case.
    ///
    /// ``XCTWaiter`` and not `wait(for:timeout:)`, for this file's standing
    /// reason: `setUp` sets `continueAfterFailure = false` and the built-in
    /// wait records a failure of its own, which would end the case before the
    /// line that says what went wrong.
    @MainActor
    private func manualValue(
        _ element: XCUIElement,
        is value: String,
        timeout: TimeInterval = 10
    ) -> Bool {
        let became = expectation(
            for: NSPredicate(format: "value == %@", value),
            evaluatedWith: element
        )
        return XCTWaiter().wait(for: [became], timeout: timeout) == .completed
    }

    /// Wait for an element to stop existing.
    ///
    /// A level change animates, so "the piece's page is gone" is a claim
    /// with a wait in it rather than a read taken on the next line — the
    /// page that is leaving is still in the tree for a few hundred
    /// milliseconds after the control that replaced it was tapped.
    ///
    /// ``XCTWaiter`` and not `wait(for:timeout:)`, for this file's standing
    /// reason: `setUp` sets `continueAfterFailure = false` and the built-in
    /// wait records a failure of its own, which would end the case before the
    /// line that says what went wrong.
    @MainActor
    private func manualGone(_ element: XCUIElement, timeout: TimeInterval = 10) -> Bool {
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: element)
        return XCTWaiter().wait(for: [gone], timeout: timeout) == .completed
    }

    /// A node on the map, by the start of its label.
    ///
    /// A node's label is the Kotlin's `"<title>: <subtitle>"`
    /// (`ManualScreen.kt:655`), so an exact subscript cannot reach one — and
    /// `firstMatch` over `.any` would take the container the node sits in.
    /// Buttons only, and by prefix.
    @MainActor
    private func manualNode(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", title))
            .firstMatch
    }

    /// Type into the search field.
    @MainActor
    private func manualType(_ app: XCUIApplication, _ query: String) -> Bool {
        let field = app.textFields[StubManual.searchPlaceholder].firstMatch
        guard field.waitForExistence(timeout: 30), manualToTop(app, until: field) else {
            print("=== NO MANUAL SEARCH FIELD — tree follows ===")
            print(app.debugDescription)
            return false
        }
        field.tap()
        clearField(app)
        app.typeText(query)
        return true
    }

    /// Open a topic's cluster by tapping its node on the map.
    ///
    /// ``testManual_MapOpensATopicThenAPiece()``'s path, stopped one level
    /// short, because ``testManual_AuditsClean()`` audits the topic as well as
    /// the piece and needs both with **no field ever focused** — once one has
    /// been, iOS keeps a second, full-screen keyboard window for the app's
    /// lifetime and the audit reports a region of it it cannot read.
    ///
    /// `zoom out` and not a node is what says the cluster is up: it is drawn
    /// whenever a topic is in play (`ManualCrumbs`) and at no other level, so
    /// a map that never zoomed in cannot satisfy it. Which nodes a cluster
    /// draws is the caller's business.
    @MainActor
    private func manualOpenTopic(_ app: XCUIApplication, topic topicTitle: String) -> Bool {
        guard manualMap(app) else { return false }
        let topic = manualNode(app, topicTitle)
        guard topic.waitForExistence(timeout: 15) else {
            print("=== NO MAP NODE FOR \(topicTitle) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        topic.tap()
        guard app.buttons[StubManual.zoomOut].firstMatch.waitForExistence(timeout: 15) else {
            print("=== NO CLUSTER FOR \(topicTitle) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// Search for a piece and open it.
    @MainActor
    private func manualOpen(
        _ app: XCUIApplication,
        search query: String,
        piece title: String
    ) -> Bool {
        guard manualStart(app), manualType(app, query) else { return false }
        let hit = app.buttons[title].firstMatch
        guard hit.waitForExistence(timeout: 15) else {
            print("=== NO SEARCH HIT FOR \(query) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        hit.tap()
        // The heading, which is on the piece's page and in no result row once
        // the query has been cleared.
        return app.staticTexts[title].firstMatch.waitForExistence(timeout: 15)
    }
}

/// What the stub house's manual fixture holds, by the words that appear on
/// screen.
///
/// Spelt out here rather than imported, like every other string this rig
/// matches on: a query that silently followed a renamed constant would stop
/// testing what it claims to. Most of these are the **fixture's**
/// (`design-notes/ios/stubhouse/fixtures/manual.py`) — the document is the
/// server's, so its wording is the server's — and the four marked below are
/// the app's own (`ManualStrings`).
enum StubManual {

    // ---- the document's chrome (`labels`), which the stub copies from the
    // shipped `web/static/manual-content.json`.
    static let startPill = "start here"
    static let mapPill = "map"
    static let zoomOut = "zoom out"
    static let eyebrow = "user manual"
    static let searchPlaceholder = "search the manual"
    static let searchEmpty = "nothing matches"
    static let noPlugins = "no plugins installed"
    static let unknownTick = "can't tell from here"
    static let needsInternet = "needs internet"
    static let pluginPill = "plugin: radio"
    /// The stand-alone badges answer 75 is about, by their words: the `admin`
    /// badge on Start here's `what will not work` label block, and the
    /// `preview` badge on `A Raspberry Pi room`'s heading.
    static let adminBadge = "admin"
    static let previewBadge = "preview"
    /// The biggest accessibility text size, as a launch argument.
    static let ax5 = "UICTContentSizeCategoryAccessibilityXXXL"

    // ---- the app's own (`ManualStrings`), which a document cannot supply.
    static let noManualTitle = "no manual on this server"
    static let dashboardButton = "open the dashboard"
    static let cachedLine =
        "this server can't be reached right now: the manual from last time"
    static let doneTick = "done"

    // ---- Start here.
    static let startTitle = "Start here"
    /// The sentence that exists in this fixture and nowhere else.
    static let fencepost = "fencepost paragraph"
    static let appOnlyItem = "Only in the app"
    static let dashboardOnlyItem = "Only on the dashboard"

    // ---- the checklist, by a word only each line contains.
    static let adminLine = "Claim the admin account"
    static let internetLine = "Answer the internet question"
    static let firstRoomLine = "Add your first room"
    static let wakeLine = "Say the wake word"
    static let introduceLine = "Introduce yourself"
    static let musicLine = "Add your music"
    static let pairLine = "Pair this phone"

    // ---- the map and the pieces.
    static let centreNode = "Domovoi"
    static let roomsTopic = "Rooms & devices"
    static let piRoom = "A Raspberry Pi room"
    static let plugins = "What plugins add"
    static let yourMusic = "Your music"
    static let radioPiece = "Radio stations"
    static let whatItIs = "What it is"
    static let talking = "Talking to it"
    /// A node's **whole** label: the Kotlin's `"<title>: <subtitle>"`
    /// (`ManualScreen.kt:655`), which is what the scramble must not change.
    static let centreNodeLabel = "Domovoi: your home"
    /// The centre node's second line — a leaf of its card in the tree, with
    /// its `.accessibilityHidden(true)` applied.
    ///
    /// Train I005 measured that the hide is a no-op inside a `Button`'s
    /// label: the card came back holding `["Domovoi", "Domovo9", "your
    /// home"]`, and the same trees hold the node icon, hidden since long
    /// before any of this. What a reader hears is the card's own label
    /// (``centreNodeLabel``), because a `Button` is one VoiceOver stop. So
    /// this is one of the two words a leaf of the centre card is **allowed**
    /// to carry, beside a frame of the name scrambling
    /// (``StubHouseUITests/manualIsAScrambleFrame(_:of:)``); anything else is
    /// a word this card does not draw.
    static let centreSubtitle = "your home"
    static let roomsTopicLabel = "Rooms & devices: where it listens"
    /// The rooms topic card's second line, the other allowed leaf of that
    /// card. See ``centreSubtitle``.
    static let roomsTopicSubtitle = "where it listens"
    /// The piece node's second line **as the accessibility tree spells it**:
    /// the subtitle, the `"  "` join and the `" preview "` run
    /// ``ManualMapNode``'s `secondLine` builds, which is the only washed
    /// second line in this document.
    ///
    /// Copied verbatim from the finding in
    /// `overnight-trains/I005/ALL/testManual_AuditsClean.log` so that it is
    /// the string Apple's audit actually names
    /// (``manualAudit(_:_:washedElement:washes:)``). A reworded fixture stops
    /// matching and the case reds, which is the safe direction.
    static let piRoomNodeSecondLine = "one room, end to end   preview "
    /// The piece page's own way back to its topic: the topic's title,
    /// **lowercased** (`ManualPageView.swift:173`), drawn on a piece's page
    /// and nowhere else. The crumb trail's copy of the same words is
    /// capitalised, and XCUITest matches labels case-sensitively, so this
    /// reaches the page and only the page.
    static let piecePageTopicLink = "rooms & devices"

    /// The alphabet a scrambling name is drawn from — `GLYPHS`,
    /// `ManualScreen.kt:169`, which DomovoiKit holds as
    /// `manualScrambleGlyphs` (`ManualRules.swift:703`).
    ///
    /// Spelled out rather than imported, this file's standing reason: the UI
    /// test target does not import DomovoiKit, so a query that silently
    /// followed a renamed or re-spelled constant would stop testing what it
    /// claims to. Used by
    /// ``StubHouseUITests/manualIsAScrambleFrame(_:of:)``.
    static let scrambleGlyphs = Set("ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>")

    /// The phone bar's Home tab, by its accessibility label.
    ///
    /// The bare word, as `StubHouseUITests.swift:2521` reaches the same four
    /// tabs — they are the suite's one control that is a button with a static
    /// text of its own on every screen, which is what makes it the positive
    /// control for the leaves a node card is allowed to carry: if SwiftUI
    /// ever stopped exposing a merged button's children, every card would
    /// come back empty and those assertions would pass on nothing.
    static let homeTabControl = "Home"
    /// `ManualStrings.next("next", piece: "What plugins add")` — the footer
    /// that exists on a piece's page and on no other level.
    static let nextPlugins = "next: What plugins add"

    // ---- the keywords, each in one piece and in no title.
    static let keywordPiRoom = "trebuchet"
    static let keywordRadio = "marmalade"
    static let keywordWhatItIs = "escarpment"
    static let keywordTalking = "cantilever"
    static let keywordPlugins = "sundial"
    /// `your-music`'s keyword. The alias `The music library` carries it too
    /// and `searchManual` drops an alias, so the one hit is `Your music`.
    static let keywordYourMusic = "gramophone"

    // ---- the inline markup, which lives in `your-music` and nowhere else.
    /// Inside the document's only inline `code` span, so it is the word that
    /// says the washed run drew (answer 34).
    static let codeWord = "obsidian"

    /// The one paragraph in this document that mixes washed runs into prose,
    /// **as the accessibility tree spells it** — the double spaces are the
    /// `" \(word) "` padding a `{tag}` run carries (`ManualRichText`).
    ///
    /// Copied verbatim from
    /// `overnight-trains/I004/ALL/testManual_ACodeSpanIsWashedAndStillAudits.log`
    /// so that it is the string the audit actually names, and used by
    /// ``manualAudit(_:_:washedElement:washes:)``. If the fixture's sentence is
    /// ever reworded this stops matching and the case reds, which is the safe
    /// direction.
    static let codeSpanParagraph =
        "Copy files into the obsidian folder. Renaming it needs  admin , "
        + "a second folder is  preview , and the radio's own folder is  plugin ."

    // ---- the four washes, light theme, as the screenshot draws them.
    //
    // Spelled out, this file's standing reason (the UI target does not import
    // DomovoiKit): `manualCodeWashLight` `#F5F3F0` (`--sunken`, `Color.kt:49`),
    // the `warn` pill's `#FFF4E8` and the `idle` pill's `#F2F1F1`
    // (`ManualRules.swift` — `warnSoft`/`idleSoft` over the page,
    // `Color.kt:60`, `:64`), and `manualNodeWashLight` `#E6E6EA` (`idleSoft`
    // over the card). I007's pictures hold exactly these four.
    static let codeWash = (245, 243, 240)
    static let amberWash = (255, 244, 232)
    static let greyWash = (242, 241, 241)
    static let nodeWash = (230, 230, 234)

    // ---- the phrases.
    static let timePhrase = "what time is it"
    static let musicExample = "play something by marmalade"
    static let aiPhrase = "will I see the aurora tonight"
    static let aiReply = "clear skies after ten"

    // ---- the links in `what-it-is`.
    static let chatLink = "the chat screen"
    static let roomsLink = "the rooms list"
    /// ``SettingsSection``'s own `sub` for Rooms, which is what says the link
    /// landed there. Deliberately not the panel's contents: the Rooms section
    /// is another item's and may be built by the time this runs.
    static let roomsSectionSub = "Each room's device"

    // ---- the handlers.
    static let timeHandler = "Time"
    static let chatModeHandler = "Chat mode"

    // ---- the rooms.
    static let kitchen = "Kitchen"
    static let study = "Study"
    static let online = "online"
    static let jarvis = "Hey Jarvis"
    static let alexa = "Alexa"
    /// Played by the seeded room and by nothing else in this house.
    static let song = "Pomegranate Waltz"
    static let wakeNoteOpening = "Some rooms answer to another word"

    /// One room, online, playing, and heard from.
    static let oneRoomPlaying = """
        {"rooms": [{"room_id": "kitchen", "room_label": "Kitchen",
                    "status": "online", "wake_word": "hey_jarvis",
                    "playing": "Pomegranate Waltz", "heard": true}]}
        """

    /// Two rooms, both on the word the manual names: there is nothing to say
    /// about other words, so the slot must draw nothing.
    static let twoRoomsOneWord = """
        {"rooms": [{"room_id": "kitchen", "room_label": "Kitchen",
                    "status": "online", "wake_word": "hey_jarvis"},
                   {"room_id": "hall", "room_label": "Hall",
                    "status": "offline", "wake_word": "hey_jarvis"}]}
        """

    /// Two rooms on the default word and one on another, which is the only
    /// shape that makes the note say something.
    static let twoWords = """
        {"rooms": [{"room_id": "kitchen", "room_label": "Kitchen",
                    "status": "online", "wake_word": "hey_jarvis"},
                   {"room_id": "hall", "room_label": "Hall",
                    "status": "online", "wake_word": "hey_jarvis"},
                   {"room_id": "study", "room_label": "Study",
                    "status": "offline", "wake_word": "alexa"}]}
        """
}
