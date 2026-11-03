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
// `Pomegranate Waltz` for what a room is playing. A case that asserted on a
// word two things could produce would pass against a screen that drew the
// other one.
//
// **Three house routes are claimed by the fixture only on request.**
// `/api/satellites`, `/api/plugins` and `/api/music/library/stats` belong to
// the house until a `manual-…` control route sets an override, so a case that
// needs two wake words, a plugin row or a track count asks for it first and
// `reset-fixtures` takes it away again.
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        var app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        XCTAssertFalse(
            app.buttons[StubManual.nextPlugins].firstMatch.exists,
            "the piece's page was still drawn after `zoom out`"
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        var app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        var app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        var app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        app = launch(push: "Manual", pushDelay: Self.pushDelay)
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
        var app = launch(push: "Manual", pushDelay: Self.pushDelay)
        XCTAssertTrue(manualStart(app), "the manual never drew Start here")
        XCTAssertTrue(
            containing(app, StubManual.fencepost).waitForExistence(timeout: 15),
            "the manual did not load at all, so there is no copy to keep"
        )

        control(
            "manual-fail", "{\"how\": \"unreachable\"}",
            port: 6601, what: "a manual read that fails without a 404"
        )
        app = launch(push: "Manual", pushDelay: Self.pushDelay)
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

    /// Apple's audit on the three levels this screen has.
    ///
    /// Classified by **signature** with ``StubHouseUITests/floorAndRest(_:)``
    /// and never by a pinned total: `rest` is every finding Apple could
    /// attribute to an element, and this screen is where contrast on a card, a
    /// map node under 44 points and an icon-only control with no label would
    /// show up.
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
        let app = launch(push: "Manual", pushDelay: Self.pushDelay)

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
        try manualAudit(app, "manual-audit-map")

        XCTAssertTrue(
            manualOpen(app, search: StubManual.keywordPlugins, piece: StubManual.plugins),
            "the manual never opened a piece's page"
        )
        XCTAssertTrue(
            app.staticTexts[StubManual.timeHandler].firstMatch.waitForExistence(timeout: 15),
            "the live slots did not draw, so there was nothing to audit"
        )
        // **The keyboard has to be gone before this screen is audited, and
        // that is the app's job, not a tidy-up here.**
        //
        // The two audits above run after a pill tap, with no keyboard. This
        // one runs after a search, and in train S009 the keyboard was still
        // standing over the piece the search had just opened: the audit
        // reported nine anonymous `Contrast failed`, and all nine are
        // elements whose `minY` is at or below the keyboard's top of 561 —
        // text with no pixels an audit can read. Android clears focus on the
        // same tap (`ManualScreen.kt:409`), and ``ManualTopBar`` now does
        // too, so this asserts the keyboard is down rather than dismissing it
        // and auditing something the app would never show.
        //
        // ``XCTWaiter`` and not `wait(for:timeout:)`: this file's `setUp`
        // sets `continueAfterFailure = false`, and the built-in wait records
        // its own failure, which would end the case before the line that
        // says what went wrong.
        let keyboard = app.keyboards.firstMatch
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: keyboard)
        let settled = XCTWaiter().wait(for: [gone], timeout: 10)
        XCTAssertEqual(
            settled, .completed,
            "opening a piece from the search field left the keyboard up, over "
                + "the piece it had just opened: \(keyboard.frame)"
        )
        try manualAudit(app, "manual-audit-piece")
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
        let app = launch(mode: mode, push: "Manual", pushDelay: Self.pushDelay)

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
    @MainActor
    private func manualAudit(_ app: XCUIApplication, _ name: String) throws {
        let found = try auditIssues(app, name)
        let split = Self.floorAndRest(found)
        let scaling = split.rest.filter { Self.isManualDynamicTypeOnText($0) }
        let rest = split.rest.filter { !Self.isManualDynamicTypeOnText($0) }
        evidence(app, name)
        print("AUDIT [\(name)] floor=\(split.floor) text-scaling=\(scaling.count)")
        XCTAssertTrue(
            rest.isEmpty,
            "\(name): Apple's audit named \(rest.count) element(s): "
                + rest.joined(separator: " || ")
        )
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

    /// A node on the map, by the start of its label.
    ///
    /// A node's label is the Kotlin's `"<title>: <subtitle>"`
    /// (`ManualScreen.kt:650`), so an exact subscript cannot reach one — and
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
    /// `ManualStrings.next("next", piece: "What plugins add")` — the footer
    /// that exists on a piece's page and on no other level.
    static let nextPlugins = "next: What plugins add"

    // ---- the keywords, each in one piece and in no title.
    static let keywordPiRoom = "trebuchet"
    static let keywordRadio = "marmalade"
    static let keywordWhatItIs = "escarpment"
    static let keywordTalking = "cantilever"
    static let keywordPlugins = "sundial"

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
