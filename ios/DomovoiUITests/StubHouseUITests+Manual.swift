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
// **A level is a pushed screen, on the shell's own `NavigationStack`.**
// Answer 32 — "swipe back goes up a level" — is built again, and these cases
// are the only thing that can read it: `pkgtest` builds the package and
// `appcheck` type-checks, so a gesture against a real navigation stack is a
// train's to run. What it means here:
//
//  * the top bar is drawn by every level, so the pills, the crumbs, the
//    search field and `zoom out` are reachable at every depth as before;
//  * one level is on screen at a time, and a covered one is **behind a
//    pushed screen** rather than unmounted. UIKit hides a covered view
//    controller from accessibility, so the tree still holds one level — if a
//    query here ever matches two of the same label, that premise is what
//    broke, and it is the first thing to check;
//  * the swipe back goes up one level from the map, a cluster and a piece,
//    and leaves the manual from Start here (which has nothing pushed);
//  * at an accessibility text size the levels are swapped in place, because
//    the bar and the page share one scroll view there, so the swipe leaves
//    the manual from every level — Kamron's open entry 74, pinned by
//    ``testManual_ASwipeAtAnAccessibilitySizeLeavesTheManual()``.
//
// The first build of answer 32 gave the manual a `NavigationStack` of its
// own and train I003 measured what that costs: all twenty cases here that
// reach a loaded document failed, because a nested stack unwound the route's
// own push (``ManualView/ready(_:cached:_:)`` has the whole measurement).
// Nothing here is a nested stack now. "This page is gone" is still a wait
// (``manualGone(_:timeout:)``) rather than a read on the next line, because
// a push and a pop animate.
//
// **`@SceneStorage` outlives a launch.** The screen keeps which level it was
// on the way Android's `rememberSaveable` does, and on iOS that survives a
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

        // ``manualTopBarButton(_:_:)`` and not `firstMatch`: this is a piece,
        // which is depth 3, and two of the four mounted bars draw `zoom out`.
        manualTopBarButton(app, StubManual.zoomOut).tap()
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
    /// `accessibilityElement(children: .ignore)` only adds a wrapper); the
    /// same modifier on the map's wires, outside a `Button`, does take them
    /// out of the same tree. The hide is kept — ``ManualMapNode``'s second
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
        // the wait and the queries above cost more than its 350 ms lead-in
        // and eight frames — and that is the state this case asserts on:
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
                    + "card drawn twice is a level below this one still in the "
                    + "tree (`testManual_ASwipeFromAPieceGoesUpToItsTopic` has "
                    + "the premise)"
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

    /// **Answer 32**: a swipe back from a piece goes up to its topic, and
    /// does not leave the manual.
    ///
    /// Three things say it, and they say different halves:
    ///
    ///  * the piece's page is **gone** — so the swipe did something. Two
    ///    page-only controls say that: the `next:` footer and the page's own
    ///    lowercased link back to its topic. Not the piece's **name**, which
    ///    the cluster this swipe lands on draws as a card (train I005, and
    ///    the comment at that assertion);
    ///  * the `map` pill is **still there** — so what it did was not pop the
    ///    route. Between `6d071463` and the commit that added this case back,
    ///    the gesture popped the route like every other screen's and the
    ///    manual was replaced by Home;
    ///  * a **sibling piece's** node is there — so the level it landed on is
    ///    the piece's own cluster and not the map or Start here. That node
    ///    discriminates: the map draws topic nodes only, and
    ///    `the-plugins` is a piece of `rooms-and-devices` beside `pi-room`
    ///    (`design-notes/ios/stubhouse/fixtures/manual.py:240`), so it is on
    ///    screen at the cluster and at no other level.
    ///
    /// It also carries the **premise** the rest of this file's queries rest
    /// on, because it is at the one depth that can settle it: see the two
    /// `XCTAssertEqual`s before the swipe, and review round 2's F1.
    ///
    /// **This case stood here before and was deleted**, with the inner
    /// `NavigationStack` train I003 measured (twenty reds,
    /// ``ManualView/ready(_:cached:_:)``). It comes back unchanged from
    /// `e27b0adf:ios/DomovoiUITests/StubHouseUITests+Manual.swift:384` bar
    /// its drag, which is now ``manualSwipeBack(_:)``. If it fails again,
    /// the thing to read is which level the tree holds: a map means the
    /// pushed chain was short (``ManualLevelHost``'s late fallback drawing
    /// the deepest level), and Home means the route was popped, which is a
    /// pushed screen that was never pushed.
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

        // ---- the premise this whole file rests on, asserted once, here.
        //
        // This is a piece: depth 3, so four ``ManualLevelHost``s are mounted
        // and each draws its own `ManualTopBar`. Four `map` pills exist as
        // views and two `zoom out` buttons do (``ManualCrumbs`` draws it
        // while `topic != nil`). The file's header says UIKit hides a covered
        // view controller from accessibility and then nothing checked it,
        // which is review round 2's F1: the wrong-premise failures are a tap
        // on a buried copy ("not hittable") and — worse — every
        // `waitForExistence` for an element of the level *underneath*
        // silently ceasing to discriminate, which is a case passing while
        // asserting nothing.
        //
        // Two counts, at the one depth where both are more than one if the
        // premise is wrong. They cost one query each and they are what makes
        // a red train readable: this case says "covered screens are in the
        // tree" in one line, instead of six cases saying six unrelated
        // things.
        XCTAssertEqual(
            app.buttons.matching(
                NSPredicate(format: "label == %@", StubManual.mapPill)
            ).count,
            1,
            "more than one `map` pill is in the tree at depth 3, so the levels "
                + "below this one are NOT hidden from accessibility while they "
                + "are covered — the premise this file's queries rest on. "
                + "Every `firstMatch` in this file is now taking an element of "
                + "a buried screen; read the other reds in that light"
        )
        XCTAssertEqual(
            app.buttons.matching(
                NSPredicate(format: "label == %@", StubManual.zoomOut)
            ).count,
            1,
            "two `zoom out` buttons are in the tree at depth 3 — the cluster's "
                + "and the piece's — so a covered host's bar is reachable. "
                + "``manualTopBarButton(_:_:)`` keeps the taps working; the "
                + "waits for an element of the level underneath do not "
                + "discriminate any more and the fix is "
                + "`.accessibilityHidden(presentsDeeper && deeperDrew)` on a "
                + "host's own content"
        )

        manualSwipeBack(app)

        XCTAssertTrue(
            manualGone(app.buttons[StubManual.nextPlugins].firstMatch),
            "a swipe back from a piece left its page on screen"
        )
        // **The page's own way back to its topic**, which is drawn on a
        // piece's page and at no other level: the topic's title lowercased
        // (`ManualPageView.swift:173`), where the crumb trail's copy of the
        // same words is capitalised.
        //
        // This line used to wait for the static text `A Raspberry Pi room` to
        // go, and train I005 measured that it never can: the cluster this
        // swipe lands on **draws that piece as a card**, and the card's name
        // is a leaf of it — `StaticText, {{25.0, 442.6}, {153.0, 15.7}},
        // label: 'A Raspberry Pi room'` in that train's own
        // `manual-audit-topic` tree. It was written when a card was expected
        // to contribute nothing to the tree, which
        // `testManual_ANodeCardIsOneElementAndNotFour` has since measured it
        // does not. So the case asked the landing level to hide the one thing
        // it has to draw, and the page's footer going (above) was the only
        // half of it that discriminated.
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
                + "going up a level — it popped the route, which is what it did "
                + "before answer 32"
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
    /// its back from the map stays on the screen too — the `BackHandler` is
    /// enabled at every level but Start here (`:315`). This is the level
    /// where the two outcomes of answer 32 meet, and the only one where
    /// getting it wrong looks like getting it right: a swipe that popped the
    /// route would also take the map off screen, so "the map is gone" is not
    /// the claim. The claim is **what is there instead** — Start here's own
    /// title, which Home does not draw.
    ///
    /// The second assertion then says the map really went, and it reads the
    /// centre **node**. That leans on one thing worth writing down: the
    /// centre crumb carries the same words and is not an element of the same
    /// kind at this level (``ManualCrumbs`` draws `link: topic != nil`, so on
    /// the map the centre is a `Text`, and ``manualNode(_:_:)`` matches a
    /// button). The day that crumb becomes a link at the map level, this line
    /// is a false red and the thing to change is the query, not the screen.
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

    /// **Kamron's entry 74**: at an accessibility text size the swipe back
    /// leaves the manual from a level too, and that is on purpose.
    ///
    /// At those sizes the top bar and the page share **one** scroll view
    /// (``ManualView/ready(_:cached:_:)``: pinned, the bar alone was 310 of
    /// this screen's 400 points), and a level cannot be a pushed screen
    /// inside that — a pushed screen brings a bar of its own that scrolls
    /// with its own page. So the levels are swapped in place there and the
    /// route's own pop gesture is the only one on the screen.
    ///
    /// It is pinned rather than left to the code, because it is the one place
    /// this screen means two different things by one gesture: a reader of the
    /// next train's results should see that this is a case with an
    /// expectation and not a case that failed to notice. `zoom out`'s
    /// VoiceOver hint says which it is
    /// (``ManualStrings/swipeHint(isAccessibilitySize:)``, pinned at the
    /// package layer — `XCUIElement` cannot read a hint).
    @MainActor
    func testManual_ASwipeAtAnAccessibilitySizeLeavesTheManual() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        let app = manualLaunch(mode: "UICTContentSizeCategoryAccessibilityXXXL")

        XCTAssertTrue(manualMap(app), "the map pill did not open the map at AX5")
        let pill = app.buttons[StubManual.mapPill].firstMatch
        XCTAssertTrue(pill.exists, "the manual's top bar is not on screen at AX5")
        evidence(app, "manual-swipe-ax5-on-the-map")

        manualSwipeBack(app)

        XCTAssertTrue(
            manualGone(pill, timeout: 15),
            "at an accessibility size a swipe back from the map was expected "
                + "to leave the manual (entry 74) and the top bar is still "
                + "there. If the levels are pushed screens at these sizes now, "
                + "this case is the thing to rewrite, not the screen"
        )
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "the swipe left the manual but Home is not what it landed on"
        )
        evidence(app, "manual-swipe-ax5-left-the-manual")
    }

    /// **Review round 1's finding 3**: the phone bar still works from a
    /// pushed level, and nothing of the manual is left on top of what it
    /// opens.
    ///
    /// A level is presented on the **shell's** stack, and the phone bar is
    /// drawn outside that stack (`WorkspaceView.swift:82`) so it is tappable
    /// from every depth. Its tabs append a `Route` to the same path
    /// (`ShellChromeViews.swift:277` → ``ShellNavigation``), which asks
    /// SwiftUI to hold a route pushed *underneath* three presentations that
    /// hang off the screen below it — a shape nobody here has measured, whose
    /// worst case is the most ordinary exit in the app visibly doing nothing.
    /// ``ManualLevelHost/isTopRoute`` is the guard, and this is the case that
    /// reads it.
    ///
    /// Music, because it is the one pushed route a case in this file can
    /// reach in one tap and recognise without any fixture of its own: the
    /// claim is ``ShellTopBar``'s crumb, which is rebuilt from the open
    /// route. Then the piece's page must be **gone**, because a level left
    /// presented over the route that was just pushed is the other half of the
    /// same bug.
    ///
    /// If it fails, the two reds mean different things: no `music` crumb is
    /// the push being swallowed, and `next: What plugins add` still on screen
    /// is a level the gate did not take down.
    @MainActor
    func testManual_ThePhoneBarWorksFromAPushedLevel() throws {
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
            "the phone bar has no Music tab while a manual level is pushed — "
                + "if the bar is gone from the screen, that is the finding, "
                + "not this tab"
        )
        tab.tap()

        XCTAssertTrue(
            app.staticTexts[ShellStringsUnderTest.musicCrumb].waitForExistence(timeout: 15),
            "the phone bar's Music tab did nothing from a pushed manual "
                + "level: the route went onto a path that still had the "
                + "manual's levels presented off it"
        )
        evidence(app, "manual-phone-bar-opened-music")
        XCTAssertTrue(
            manualGone(footer),
            "the piece's page is still on screen over Music, so a level of "
                + "the manual was left presented on top of the route that was "
                + "just pushed"
        )
    }

    /// A swipe back from **Start here** leaves the manual.
    ///
    /// Answer 32's second half — "the system swipe-back pops a level and only
    /// the top level leaves the manual". Start here has nothing pushed above
    /// it (``ManualLevelStack/presentsDeeper(at:in:)`` is false for an empty
    /// chain, which is Android's `BackHandler(enabled = at != "start")`), so
    /// the route's own `interactivePopGestureRecognizer` is the only
    /// recogniser in play and the manual is left. A manual nobody can swipe
    /// out of is the regression the levels-as-screens shape could cause, and
    /// this is the case that catches it.
    ///
    /// Two assertions, each a different failure:
    ///
    ///  * the `map` pill is **gone** — the manual left. A swallowed gesture
    ///    leaves it there and fails here;
    ///  * the house's name is on screen — what it left *to* is Home.
    ///    `stub house` is ``houseName(in:)``, the one thing only a completed
    ///    config read on Home can draw, so a swipe that unwound to a blank
    ///    root or to the pairing screen fails too.
    ///
    /// ### Its I003 failure is NOT the other nineteen's — read it on its own
    ///
    /// Do not read this case as fixed because the other nineteen were. In
    /// I003 it failed with a different symptom: the `start here` pill's
    /// `waitForExistence` *succeeded* at `t = 14.47s`, and the very next
    /// query — `control.exists` inside ``manualToTop(_:until:flicks:)``,
    /// which unlike `waitForExistence`'s `existsNoRetry` predicate waits for
    /// the app to go **idle** first — hung, retried at `45.54s` and
    /// `76.55s`, and ended at `106.57s` with "Failed to get matching
    /// snapshots: Timed out while evaluating UI query". No tree was printed
    /// (a query that throws does not come back into Swift, so the guard's
    /// `app.debugDescription` never ran).
    ///
    /// The train's screen recording is the evidence the tree is not, and it
    /// **refutes** the first reading of this case ("caught mid-unwind"):
    /// `att/testManual_ASwipeFromStartHereLeavesTheManual/Screen_Recording_*.mp4`
    /// shows the manual fully drawn — `domovoi / manual`, both pills, the
    /// whole Start here page — and then **unchanged, pixel for pixel, to the
    /// end of the recording**. It is the only one of the twenty whose last
    /// frame is the manual: in the other nineteen the last frame is Home
    /// under a `domovoi / manual` breadcrumb (measured over all twenty last
    /// frames; this one is also the only recording longer than 46 s).
    ///
    /// So in this run the route did **not** unwind. The screen stopped
    /// changing and the app stopped reporting idle at the same instant the
    /// document landed and the inner stack mounted — the same trigger, stuck
    /// rather than resolved, which is what an unsupported stack shape is
    /// entitled to do. Removing the stack removes that trigger, and that is
    /// the whole claim: if this case fails again, it is a second finding and
    /// the thing to look at is what is holding the app un-idle, not a popped
    /// route.
    ///
    /// ### And it did fail again, in I004, with a third symptom
    ///
    /// It failed on its own assertion rather than on a hang: the pill's
    /// `exists == 0` was polled once a second from `t = 18.5s` to
    /// `t = 28.5s`, 11 times, and the pill was there every time
    /// (`testManual_ASwipeFromStartHereLeavesTheManual.log:168-186`). No
    /// 106-second query, no un-idle app, and the recording's last frame is
    /// the manual drawn exactly as the frame before the touch — the gesture
    /// did nothing.
    /// ``manualSwipeBack(_:)`` carries that diagnosis and the eliminations,
    /// including the one difference that is not about coordinates: this is the
    /// only swipe in the suite that has to pop a `Route` off
    /// ``ShellModel/path``.
    ///
    /// **Answer 32 did not change this case's claim**, which is worth saying
    /// because answer 32 landed after I004 ran: Start here is depth 0, no
    /// ``ManualLevelHost`` is presented over it
    /// (`ManualLevelStack.presentsDeeper(at:in:)` is false on an empty
    /// chain), so the leading-edge gesture has nothing of the manual's to
    /// unwind and the route's own pop is still the only thing it can do. The
    /// two cases above it — from the map and from a piece — are where answer
    /// 32 changed the outcome.
    ///
    /// `manual-swipe-on-start-here` is taken **before** the drag, as the
    /// state the gesture was aimed at. The state after it is in
    /// `manual-swipe-left-the-manual`, and in a failing run in neither: both
    /// assertions below come first.
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
                + "bar is still there, so the route's own pop gesture never "
                + "fired"
        )
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "the swipe left the manual but Home is not what it landed on"
        )
        evidence(app, "manual-swipe-left-the-manual")
    }

    /// **Answer 30 on the way up**: going back up a level scrambles that
    /// level's names again.
    ///
    /// Android re-scrambles every node on every level change, `zoom out`
    /// included: `levelKey` is one value in one composition
    /// (`ManualScreen.kt:593`) and every node's `LaunchedEffect(key, text)`
    /// (`:686`) re-fires when it changes. iOS does not get that for free —
    /// the first build of this re-showed a map whose names had settled
    /// minutes ago, and the ornament Kamron called "meant to impress" ran on
    /// the way down only. That is review 2's S2, and
    /// ``ManualModel/arrivals(of:)`` read through ``ManualMapStage/levelKey``
    /// is the fix: the key counts the arrival, so coming back up restarts
    /// the effect whatever SwiftUI decided about the view's identity.
    ///
    /// **What makes this a test and not a screenshot.** The drawn glyphs are
    /// nobody's label and nobody's value, on purpose
    /// (``ManualScrambleName``), so the only thing the tree can carry is
    /// *whether* a scramble is in flight — ``ManualMapNode/scrambleState``,
    /// published as a value and only to a launch that asked for a slow
    /// scramble. The sequence asserted here is settled → **scrambling** →
    /// settled on one element, and the middle one is the claim: with the
    /// arrival left out of the key the centre node never leaves `"settled"`
    /// and this case fails on that wait.
    @MainActor
    func testManual_GoingBackUpALevelScramblesTheNamesAgain() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the manual fixture's seed")
        // 600 ms a frame: 4.8 s of retyping after the 350 ms lead-in, which
        // is the slack a case needs to be inside an effect that ships at
        // 216 ms.
        let app = manualLaunch(scrambleMS: 600)

        // From Start here, so the map's nodes are built by the tap below and
        // the first scramble is this case's to watch.
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        let pill = app.buttons[StubManual.mapPill].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 30), "the manual never drew its top bar")
        XCTAssertTrue(manualToTop(app, until: pill), "the `map` pill was not reachable")
        pill.tap()

        // The centre node is drawn on the **map** level and on no other: a
        // cluster's hub is its topic's card, so this element cannot be
        // confused with anything the level above draws.
        let centre = app.buttons[StubManual.centreNodeLabel].firstMatch
        XCTAssertTrue(centre.waitForExistence(timeout: 20), "the map drew no centre node")
        XCTAssertTrue(
            manualValue(centre, is: "settled", timeout: 25),
            "the map's first scramble never finished"
        )

        // Down a level. Going down must NOT count an arrival at the level
        // left behind: a stage that re-scrambled while something was over it
        // would spend the effect out of sight and be settled again by the
        // time anybody came back to it.
        let topic = manualNode(app, StubManual.roomsTopic)
        XCTAssertTrue(topic.waitForExistence(timeout: 15), "the map drew no ring topic")
        topic.tap()
        let out = app.buttons[StubManual.zoomOut].firstMatch
        XCTAssertTrue(out.waitForExistence(timeout: 15), "the topic's cluster never opened")
        // **Whether this is a claim at all depends on the tree, so it is
        // written as the implication and not as a wait.** One level is
        // mounted at a time now, so the map's elements are normally *absent*
        // here — and `manualValue` would then poll a non-existent element
        // and time out, failing this case for a reason that has nothing to
        // do with the claim (review 3's F4). What is true either way is the
        // implication: if the map is still in the tree, it must still read
        // `"settled"`. The negative itself — going down does not count an
        // arrival at the level left behind — is pinned where it can be
        // asserted, at the package layer, in
        // `ManualAnswersTests.testArrivingAtALevelAgainIsANewArrival`.
        if centre.exists {
            XCTAssertEqual(
                "settled", centre.value as? String,
                "pushing a cluster re-scrambled the covered map underneath it — "
                    + "the effect would be over before anybody came back to see it"
            )
        }
        evidence(app, "manual-up-a-level-before")

        // And back up, which is the half review 2 found missing.
        out.tap()
        XCTAssertTrue(
            manualValue(centre, is: "scrambling", timeout: 15),
            "`zoom out` back to the map did not scramble its names again: the "
                + "centre node's value stayed settled — nothing restarts "
                + "the effect unless the key counts the arrival"
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
    /// The ratios are arithmetic, not a guess:
    /// ``manualContrastRatio(_:_:)`` over `--sunken` gives 18.96:1 for
    /// `Color.primary`, 6.72:1 for `fgMuted` and 5.17:1 for `warnInk` in the
    /// light theme, and 19.27:1 / 7.77:1 / 10.08:1 in the dark one — the
    /// table is in `Color.manualCodeWash`'s doc comment, and the numbers come
    /// out of the same sRGB values the view builds its colour from, so the
    /// claim cannot drift from the pixels.
    ///
    /// What a case can add to that is Apple's own measurement of the drawn
    /// page, which is what this does. `Your music` is the one piece with an
    /// inline `code` span (`obsidian`) and all three pills in it, so this
    /// audit covers every ink-on-wash pairing at once — including `admin`,
    /// the weakest at 5.17:1. A translucent wash would make those numbers
    /// untrue and this is where it would show.
    ///
    /// ### What this case does **not** guard, said plainly
    ///
    /// It passes with `run.backgroundColor` removed from both arms, and the
    /// review was right to say so. A wash is three pixel values inside a
    /// `Text`'s attributed string: it is not an element, it has no label and
    /// no value, and the accessibility tree a UI case queries cannot see it.
    /// Pixel-sampling a screenshot was considered and rejected — the washed
    /// run is a substring of a paragraph, so there is no frame to sample it
    /// in, and a case that sampled the paragraph's frame would be reading
    /// mostly page background either way.
    ///
    /// So answer 34 is guarded in two places and neither is this one:
    /// `ManualAnswersTests.testEveryRatioTheWashCommentsState` holds the
    /// numbers, the two washes and the measurement that says why there are
    /// two of them; and the evidence screenshot is what says the wash is on
    /// the run at all. **One assignment per run kind is guarded by review
    /// only.** What this case is for is the other half: that the page a
    /// reviewer looked at still passes Apple's audit, at the ordinary type
    /// size, with the keyboard down.
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
        // ``manualAudit(_:_:washedElement:)``: Apple's audit reports
        // `Contrast failed` against this paragraph because its frame holds
        // two backgrounds, and every ink it draws was measured off the
        // audit's own screenshot at 18.96 / 6.71 / 5.17 to 1. Any other
        // finding on this page still fails this case, including a
        // `Contrast failed` against any other element.
        try manualAudit(app, "manual-audit-code-span", washedElement: StubManual.codeSpanParagraph)
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
        // is — `manualAudit(_:_:washedElement:)` has the numbers and the
        // control that sits beside it in this very picture. Any other
        // element, and any other headline, still fails this case.
        try manualAudit(
            app, "manual-audit-topic",
            washedElement: StubManual.piRoomNodeSecondLine
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

        // Depth 3 again (a piece's page), so the hittable copy and not the
        // first — ``manualTopBarButton(_:_:)`` has the reason.
        manualTopBarButton(app, StubManual.zoomOut).tap()
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
    ///  1. **Every pairing the element draws was sampled off the audit's own
    ///     screenshot** (`att/.../manual-audit-code-span_0_*.png`, 1178×2556
    ///     at 3× for a 393-point screen). The wash is `(245, 243, 240)`,
    ///     opaque as answer 34 asked. On it: the `code` run's ink `(0, 0, 0)`
    ///     at **18.96:1**, `preview` and `plugin` at `(88, 85, 79)` —
    ///     `fgMuted` — at **6.71:1**, and `admin` at `(157, 83, 0)` —
    ///     `warnInk` — at **5.17:1**. The prose outside the washes is black
    ///     on white. Body text needs 4.5:1 and the weakest is 5.17:1.
    ///  2. Those are the numbers `ManualAnswersTests.testEveryRatioTheWashCommentsState`
    ///     computes from the tokens (18.96 / 6.72 / 5.17), so the pixels and
    ///     the arithmetic agree to the second decimal.
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
    @MainActor
    private func manualAudit(
        _ app: XCUIApplication,
        _ name: String,
        washedElement: String? = nil
    ) throws {
        let found = try auditIssues(app, name)
        let split = Self.floorAndRest(found)
        let scaling = split.rest.filter { Self.isManualDynamicTypeOnText($0) }
        let notScaling = split.rest.filter { !Self.isManualDynamicTypeOnText($0) }
        let washed = washedElement.map { element in
            notScaling.filter { Self.isContrastOnTheWashedElement($0, element) }
        } ?? []
        let rest = notScaling.filter { !washed.contains($0) }
        evidence(app, name)
        print(
            "AUDIT [\(name)] floor=\(split.floor) text-scaling=\(scaling.count) "
                + "washed-element=\(washed.count)"
        )
        for line in washed { print("AUDIT [\(name)] washed element, measured: \(line)") }
        XCTAssertTrue(
            rest.isEmpty,
            "\(name): Apple's audit named \(rest.count) element(s): "
                + rest.joined(separator: " || ")
        )
    }

    /// `Contrast failed` against one named static text, and nothing else —
    /// the signature ``manualAudit(_:_:washedElement:)`` explains.
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
    /// One helper and not a drag written out in four cases: it is the gesture
    /// answer 32 is *about*, and four copies of it would be four chances for
    /// one of them to stop being a left-edge drag.
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

    /// A control in the manual's top bar, taken by the copy that is on
    /// screen rather than by `firstMatch`.
    ///
    /// **Review round 2's F1, half two.** Every ``ManualLevelHost`` draws its
    /// own `ManualTopBar`, so at a piece (depth 3) four bars are mounted and
    /// two of them carry `zoom out` — ``ManualCrumbs`` draws it while
    /// `topic != nil`, which is the cluster and the piece. The file's whole
    /// premise is that UIKit hides a covered view controller's view from
    /// accessibility, so only the top one is ever in the tree; if that is
    /// wrong, a pushed view's view sits *after* the covered one and
    /// `firstMatch` takes the buried copy, whose tap fails "not hittable" —
    /// this file's own standing lesson.
    ///
    /// This asks for the hittable one and falls back to the last match, which
    /// is the topmost. **It does not make a wrong premise pass**: that is
    /// ``testManual_ASwipeFromAPieceGoesUpToItsTopic``'s count assertions,
    /// which red on a second copy. This only stops six other cases failing
    /// for a reason that is not theirs, so the train names the premise once
    /// instead of six times.
    @MainActor
    private func manualTopBarButton(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        let all = app.buttons.matching(NSPredicate(format: "label == %@", label))
        let found = all.allElementsBoundByIndex
        return found.first(where: { $0.isHittable }) ?? found.last ?? all.firstMatch
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
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
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
    /// (``manualAudit(_:_:washedElement:)``). A reworded fixture stops
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
    /// ``manualAudit(_:_:washedElement:)``. If the fixture's sentence is
    /// ever reworded this stops matching and the case reds, which is the safe
    /// direction.
    static let codeSpanParagraph =
        "Copy files into the obsidian folder. Renaming it needs  admin , "
        + "a second folder is  preview , and the radio's own folder is  plugin ."

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
