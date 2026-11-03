import XCTest

/// The rest of a conversation, against the stub house: pictures, documents,
/// drafts, long-document jobs, the mid-answer lookup card, heads-up picks,
/// report-a-message and a spoken turn.
///
/// ### The two things every case here does first
///
///  1. **`POST /api/_test/reset-fixtures`**, which puts `fixtures/chatmore.py`
///     back to its seed. Four of its pieces are stateful on purpose — the
///     jobs' progress, draft 43's one-off 409, the household's internet policy
///     and the voice mode — and a case that inherited any of them from the
///     case before would pass or fail on the alphabet.
///  2. **A search**, because threads 18 to 28 are *below the fold*. Their
///     `updated_at` is older than every thread the earlier Chat cases were
///     written against, so adding eleven of them moved nothing on the list;
///     the price is that none of them is on screen at launch. The stub's
///     search is a case-insensitive substring over the title and every
///     message (`chat_threads_body`), and each of the eleven has a word no
///     other thread holds.
///
/// ### What makes these cases discriminating
///
/// Every assertion below is against fixture data chosen so that the obvious
/// wrong answer is a *different* string, not a missing one. The places where
/// that mattered most:
///
/// * thread 18's sources arrive **uncited first**, so a chip list that does
///   not put the cited page first comes out backwards rather than empty;
/// * page 2's passage holds "service the boiler annually" and page 7's holds
///   "gutters", so the sheet can be shown to have opened the chip that was
///   tapped;
/// * the stopped job has `read: 4` of `parts: 11`, so "7 parts left" is right
///   and "11" and "6" are the two ways of getting it wrong;
/// * the live job's bar is asserted against the job's **own** line — "reading
///   part 6 of 11" with a bar at 5 — which is the read-versus-part confusion
///   and nothing else;
/// * the lookup answers stream words that never contain "Tagus", while the
///   row the server keeps does: a model that drew its own stream instead of
///   re-reading the thread cannot pass;
/// * the heads-up reply **echoes the `skip` list it received**, so silencing a
///   kind is observable even though no route stores it;
/// * a picture that loaded is an `.image` element, one still coming is a
///   spinner and one that failed is a line of text — three element kinds, so
///   "it drew something" is never mistaken for "it drew the picture".
extension StubHouseUITests {

    // MARK: - getting there

    /// The fixture module back to its seed.
    @MainActor
    func chatMoreReset(port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the ChatMore fixture back to its seed")
    }

    /// The states a real house will not make on demand: the household's
    /// internet policy, and the core's two voice refusals.
    @MainActor
    func chatMoreControl(_ body: String, port: Int = 6601) {
        control("chatmore", body, port: port, what: "the ChatMore fixture's own state")
    }

    /// Anything on screen whose label is exactly `text`, of any element type.
    ///
    /// ``containing(_:_:)``'s sibling, and the one to reach for when a
    /// *substring* would also match the thing being told apart: "internet off"
    /// is a substring of no other chip wording, and "v1" is a substring of
    /// "v12" the day a draft has twelve versions.
    @MainActor
    func exactly(_ app: XCUIApplication, _ text: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", text))
            .firstMatch
    }

    /// How many elements carry exactly this label.
    ///
    /// Used where an *absence* cannot be asserted because a sheet does not
    /// hide the transcript behind it from a query (NIGHT-RULES section 6): the
    /// report sheet's question is told from the wrong question by which of the
    /// two gained a second element when the sheet opened.
    @MainActor
    func countOf(_ app: XCUIApplication, _ text: String) -> Int {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", text))
            .count
    }

    /// Back to the top of the thread list.
    ///
    /// ``scrollUntilBuilt(_:_:flicks:)`` only ever scrolls down, and at an
    /// accessibility size `openChat` has already scrolled the list to reach
    /// the archived entry — so the search field, which is above every row, is
    /// off the top before a case has done anything.
    @MainActor
    func chatMoreScrollToTop(_ app: XCUIApplication) {
        let list = app.collectionViews.firstMatch
        guard list.waitForExistence(timeout: 20) else { return }
        for _ in 0..<30 { list.swipeDown(velocity: .slow) }
    }

    /// Search the thread list for `search` and open the thread called `title`,
    /// waiting for a transcript that starts with `words`.
    ///
    /// Answers false and prints the tree when any step of that fails, so a
    /// case reports *where* it stopped rather than asserting about a screen
    /// that was never reached.
    @MainActor
    func openChatMoreThread(
        _ app: XCUIApplication,
        search: String,
        title: String,
        waitingFor words: String
    ) -> Bool {
        chatMoreScrollToTop(app)
        let field = app.textFields[ChatStringsUnderTest.searchHint].firstMatch
        guard field.waitForExistence(timeout: 20) else {
            print("=== NO SEARCH FIELD — tree follows ===")
            print(app.debugDescription)
            return false
        }
        field.tap()
        app.typeText(search)
        return openThread(app, title, waitingFor: words)
    }

    /// Launch, reset the fixture, and open one of the eleven threads.
    @MainActor
    func openChatMore(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        search: String,
        title: String,
        waitingFor words: String
    ) -> XCUIApplication? {
        chatMoreReset()
        let (app, landed) = openChat(mode)
        guard landed else {
            XCTFail("the thread list never answered")
            return nil
        }
        guard openChatMoreThread(app, search: search, title: title, waitingFor: words) else {
            XCTFail("\(title) drew no transcript")
            return nil
        }
        return app
    }

    /// A launch with the talk seam armed.
    ///
    /// **A Simulator has no microphone**, so `-domovoi-chatmore-talk` hands
    /// ``ChatMoreTalker`` a fixed stretch of silence instead of recording one
    /// (DEBUG only, the ``AuditStrip`` idiom). Everything after that is real:
    /// the phase machine, the multipart body, the core's refusals and the
    /// re-read of the thread. This cannot go through ``launch(mode:paired:server:port:strip:push:pushDelay:)``,
    /// which has no argument for it.
    @MainActor
    func launchChatMoreTalk(seconds: String = "1.0", voice: String? = nil) -> XCUIApplication {
        resetChatHouse()
        setChatAI(on: true)
        setChatFail(nil)
        chatMoreReset()
        if let voice { chatMoreControl("{\"voice\":\"\(voice)\"}") }
        let app = XCUIApplication()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:6601",
            "-domovoi-token", StubHouseUITests.token,
            "-domovoi-audit-push", "Chat",
            "-domovoi-audit-push-delay", "\(StubHouseUITests.pushDelay)",
            "-domovoi-chatmore-talk", seconds,
            "-UIPreferredContentSizeCategoryName", StubHouseUITests.ordinaryTypeSize,
        ]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// The big hold-to-talk ring, told apart from the hint line under it.
    ///
    /// Both carry "hold to talk" — the hint is ``ChatMoreModel/talkHint``'s
    /// idle wording and the ring's label is the same words — so this picks by
    /// size. The ring is a `@ScaledMetric` 196 points square; a line of
    /// `.subheadline` is nothing like that wide and tall.
    @MainActor
    func talkRing(_ app: XCUIApplication) -> XCUIElement? {
        let all = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", ChatMoreStringsUnderTest.holdToTalk))
        guard all.firstMatch.waitForExistence(timeout: 20) else {
            print("=== NO TALK SCREEN — tree follows ===")
            print(app.debugDescription)
            return nil
        }
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists else { continue }
            let frame = candidate.frame
            if frame.width > 120, frame.height > 120 { return candidate }
        }
        print("=== NO RING AMONG \(all.count) 'hold to talk' ELEMENTS — tree follows ===")
        print(app.debugDescription)
        return nil
    }

    /// Open the talk screen from the composer's mic.
    @MainActor
    func openTalk(_ app: XCUIApplication) -> XCUIElement? {
        let mic = app.buttons[ChatMoreStringsUnderTest.talk].firstMatch
        guard mic.waitForExistence(timeout: 20) else {
            print("=== NO MIC IN THE COMPOSER — tree follows ===")
            print(app.debugDescription)
            return nil
        }
        mic.tap()
        return talkRing(app)
    }

    /// Open one draft card out of four by **which draft it is**.
    ///
    /// Four cards in one transcript each carry an "open", so the label alone
    /// cannot tell them apart. The button's accessibility *value* is the
    /// card's own sentence, which is what VoiceOver reads and what this picks
    /// by — the same label-and-value idiom the thread list's row menus use.
    @MainActor
    func openDraftCard(_ app: XCUIApplication, _ card: String) -> Bool {
        let open = app.buttons
            .matching(
                NSPredicate(
                    format: "label == %@ AND value == %@", ChatMoreStringsUnderTest.open, card
                )
            )
            .firstMatch
        guard open.waitForExistence(timeout: 20) else {
            print("=== NO OPEN FOR \(card) — tree follows ===")
            print(app.debugDescription)
            XCTFail("the transcript has no draft card for \(card)")
            return false
        }
        open.tap()
        let canvas = app.buttons[ChatMoreStringsUnderTest.backToTheChat].firstMatch
        guard canvas.waitForExistence(timeout: 20) else {
            print("=== THE CANVAS DID NOT OPEN FOR \(card) — tree follows ===")
            print(app.debugDescription)
            XCTFail("open drew no canvas for \(card)")
            return false
        }
        return true
    }

    /// Turn the canvas's changes switch off if it is on.
    ///
    /// A version this phone has not seen opens with the changes shown, so a
    /// case about the *words* has to put them back. The switch not being
    /// there is fine — a version with no changes draws none.
    @MainActor
    func turnChangesOff(_ app: XCUIApplication) {
        let changes = app.switches[ChatMoreStringsUnderTest.changes].firstMatch
        guard changes.waitForExistence(timeout: 15) else { return }
        if changes.value as? String == "1" { changes.tap() }
    }

    // MARK: - documents and pictures

    /// A reply over a document: its source chips, cited page first, and the
    /// page sheet behind the one that was tapped.
    ///
    /// The two chips are `p. 2` (cited) and `p. 7 · read` (not), and the
    /// server sends **page 7 first**. So the ordering assertion is not
    /// decoration: a chip list that drew the sources in the order they
    /// arrived would put the uncited page on the left.
    @MainActor
    func testChatMore_ADocumentReplyShowsItsSourcesAndOpensThePage() throws {
        guard let app = openChatMore(
            search: "Fernbrook",
            title: StubChatMore.lease,
            waitingFor: StubChatMore.leaseReply
        ) else { return }
        evidence(app, "chatmore-01-sources")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.sources).exists,
            "a reply with sources draws no heading for them"
        )
        let cited = exactly(app, StubChatMore.citedChip)
        let read = exactly(app, StubChatMore.uncitedChip)
        XCTAssertTrue(cited.exists, "the cited page has no chip")
        XCTAssertTrue(read.exists, "the page that was read but not cited has no chip")
        print("CHATMORE chips cited=\(cited.frame) read=\(read.frame)")
        // Cited first, and the chips are a row at this type size.
        XCTAssertLessThan(
            cited.frame.minX,
            read.frame.minX,
            "the chips are in the order the server sent them, not cited first"
        )
        XCTAssertTrue(
            containing(app, StubChatMore.replyNote).exists,
            "the reply does not say that two of its pages were scans"
        )

        cited.tap()
        let passage = containing(app, StubChatMore.citedPassage)
        let opened = passage.waitForExistence(timeout: 20)
        evidence(app, "chatmore-02-page-sheet")
        XCTAssertTrue(opened, "tapping the cited chip did not open its passage")
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.theSentenceTheReplyUsed).exists,
            "the sheet does not say which sentence the reply used"
        )
        // The other page's passage is the only "gutters" anywhere in this
        // thread — the reply's own words are about the boiler — so this is
        // "the sheet opened the chip that was tapped" and nothing else.
        XCTAssertFalse(
            containing(app, StubChatMore.uncitedPassageWord).exists,
            "the sheet opened a page nobody tapped"
        )
    }

    /// A document attached to a message: the marker, the summary the server
    /// wrote for it, and its note.
    ///
    /// The summary is the discriminator. ``ChatMoreDocument/markerSummary``
    /// falls back to the file name when the summary is blank, and this
    /// document has both — so a marker reading only "attached lease.pdf" is
    /// the fallback firing when it should not.
    @MainActor
    func testChatMore_AnAttachedDocumentLeavesItsMarker() throws {
        guard let app = openChatMore(
            search: "Fernbrook",
            title: StubChatMore.lease,
            waitingFor: StubChatMore.leaseReply
        ) else { return }
        evidence(app, "chatmore-03-doc-marker")

        XCTAssertTrue(
            startingWith(app, StubChatMore.markerPrefix).exists,
            "the attached document leaves no marker on the message"
        )
        XCTAssertTrue(
            containing(app, StubChatMore.leaseSummary).exists,
            "the marker shows the file name instead of the summary the server wrote"
        )
        XCTAssertTrue(
            containing(app, StubChatMore.leaseNote).exists,
            "the marker does not pass on the note about the scanned pages"
        )
    }

    /// Two pictures on one message, each drawn and each named.
    ///
    /// The two tokens are different and so are the two names, because a store
    /// keyed on the wrong token draws one picture twice. And a loaded picture
    /// is an **`.image`** element: a spinner that never stops is a progress
    /// indicator and a refused fetch is a line of text, so this cannot pass on
    /// "something was drawn there".
    @MainActor
    func testChatMore_APictureOnAMessageIsDrawn() throws {
        guard let app = openChatMore(
            search: "photographs",
            title: StubChatMore.photographs,
            waitingFor: StubChatMore.photographsAsked
        ) else { return }

        let ladder = app.images[ChatMoreStringsUnderTest.pictureNamed(StubChatMore.ladder)]
            .firstMatch
        let drawn = ladder.waitForExistence(timeout: 25)
        let gutter = app.images[ChatMoreStringsUnderTest.pictureNamed(StubChatMore.gutter)]
            .firstMatch
        evidence(app, "chatmore-04-pictures")
        XCTAssertTrue(drawn, "the first picture never became a picture")
        XCTAssertTrue(gutter.exists, "the second picture never became a picture")
        XCTAssertFalse(
            containing(app, ChatMoreStringsUnderTest.pictureDidntLoad).exists,
            "a picture on the message could not be fetched"
        )
    }

    // MARK: - drafts

    /// A draft card opens the canvas **at the version the card named**.
    ///
    /// The card under this reply is v2 and the draft has three versions. Each
    /// version's closing name is a word no other version holds — Hawthorn,
    /// Marchmont, Pemberley — so "it opened at the latest" and "it opened at
    /// the first" are each a different wrong word on screen rather than an
    /// empty canvas.
    ///
    /// The changes switch is turned **off** on the way, because a version this
    /// phone has not seen opens with it on and the words are the thing being
    /// asserted. That it is on at all is the next case.
    @MainActor
    func testChatMore_ADraftCardOpensTheCanvasAtItsVersion() throws {
        guard let app = openChatMore(
            search: "Four letters",
            title: StubChatMore.letters,
            waitingFor: StubChatMore.lettersAsked
        ) else { return }
        evidence(app, "chatmore-05-draft-cards")

        XCTAssertTrue(
            containing(app, StubChatMore.boilerCard).exists,
            "the draft card does not say which draft and which version it is"
        )
        guard openDraftCard(app, StubChatMore.boilerCard) else { return }
        turnChangesOff(app)
        evidence(app, "chatmore-06-draft-canvas")

        XCTAssertTrue(
            containing(app, StubChatMore.v2Word).exists,
            "the canvas did not open at the version the card named"
        )
        XCTAssertFalse(
            containing(app, StubChatMore.v3Word).exists,
            "the canvas opened at the latest version instead of the card's"
        )
        XCTAssertFalse(
            containing(app, StubChatMore.v1Word).exists,
            "the canvas opened at the first version instead of the card's"
        )
    }

    /// The changes switch: on for a version this phone has not seen, off for
    /// one with no changes at all, and the words follow it.
    ///
    /// This draft's v2 removed "Yours faithfully" and added "Yours sincerely".
    /// The removed phrase is in **no body on screen** — so it is on screen
    /// exactly when the changes are, and gone when the switch is off. That is
    /// the switch doing something, rather than the switch existing.
    @MainActor
    func testChatMore_TheCanvasShowsChangesForAVersionNotSeenHere() throws {
        guard let app = openChatMore(
            search: "Four letters",
            title: StubChatMore.letters,
            waitingFor: StubChatMore.lettersAsked
        ) else { return }
        guard openDraftCard(app, StubChatMore.guttersCard) else { return }

        let changes = app.switches[ChatMoreStringsUnderTest.changes].firstMatch
        let live = changes.waitForExistence(timeout: 20)
        evidence(app, "chatmore-07-changes-on")
        XCTAssertTrue(live, "a version this phone has not seen draws no changes switch")
        XCTAssertEqual("1", changes.value as? String, "the changes switch did not start on")
        XCTAssertTrue(
            containing(app, StubChatMore.removedPhrase).exists,
            "the changes do not show the phrase this version removed"
        )

        changes.tap()
        XCTAssertTrue(
            waitUntilGone(containing(app, StubChatMore.removedPhrase)),
            "the removed phrase is still on screen with the changes switched off"
        )
        XCTAssertTrue(
            containing(app, StubChatMore.addedName).exists,
            "the version's own words are not on screen with the changes off"
        )

        // v1 has no changes at all, so the switch is not drawn for it — a
        // switch that could not do anything is not drawn
        // ([[no-misleading-controls]]).
        let first = exactly(app, ChatMoreStringsUnderTest.version(1))
        guard first.waitForExistence(timeout: 15) else {
            evidence(app, "chatmore-08-no-v1-chip")
            XCTFail("the canvas has no chip for v1")
            return
        }
        first.tap()
        evidence(app, "chatmore-09-v1")
        XCTAssertTrue(
            waitUntilGone(app.switches[ChatMoreStringsUnderTest.changes].firstMatch),
            "v1 has no changes and still draws a changes switch"
        )
        XCTAssertFalse(
            containing(app, StubChatMore.addedName).exists,
            "v1 shows the name a later version added"
        )
    }

    /// A hand edit is saved as the next version, and a 409 says so without
    /// taking the typed words away.
    ///
    /// The fixture refuses the **first** save of this draft once and takes the
    /// second, which is the only way to see both halves in one case. The typed
    /// words surviving the refusal is the assertion that matters: the sentence
    /// promises it, and the reload a 409 triggers is what used to clear the
    /// editor.
    @MainActor
    func testChatMore_AHandEditSavesAsTheNextVersionAndA409SaysSo() throws {
        guard let app = openChatMore(
            search: "Four letters",
            title: StubChatMore.letters,
            waitingFor: StubChatMore.lettersAsked
        ) else { return }
        guard openDraftCard(app, StubChatMore.fenceCard) else { return }

        let edit = app.buttons[ChatMoreStringsUnderTest.edit].firstMatch
        guard edit.waitForExistence(timeout: 20) else {
            evidence(app, "chatmore-10-no-edit")
            XCTFail("the canvas offers no edit")
            return
        }
        edit.tap()
        let editor = exactly(app, ChatMoreStringsUnderTest.edit)
        guard editor.waitForExistence(timeout: 15) else {
            evidence(app, "chatmore-11-no-editor")
            XCTFail("edit drew no editor")
            return
        }
        editor.tap()
        app.typeText(StubChatMore.typed)

        // v1 is the newest, so the save asks for v2 both times.
        let save = app.buttons[ChatMoreStringsUnderTest.saveAsVersion(2)].firstMatch
        guard save.waitForExistence(timeout: 15) else {
            evidence(app, "chatmore-12-no-save")
            XCTFail("the editor offers no save")
            return
        }
        save.tap()
        let refused = containing(app, ChatMoreStringsUnderTest.newerVersionCameIn)
        let said = refused.waitForExistence(timeout: 25)
        evidence(app, "chatmore-13-draft-409")
        XCTAssertTrue(said, "a refused save does not say a newer version came in")
        XCTAssertTrue(
            containing(app, StubChatMore.typed).waitForExistence(timeout: 15),
            "the sentence promises the typed words are still here and they are not"
        )

        let again = app.buttons[ChatMoreStringsUnderTest.saveAsVersion(2)].firstMatch
        guard again.waitForExistence(timeout: 20) else {
            evidence(app, "chatmore-14-no-second-save")
            XCTFail("the save is gone after the refusal")
            return
        }
        again.tap()
        let kept = containing(app, ChatMoreStringsUnderTest.savedAsVersion(2))
        let landed = kept.waitForExistence(timeout: 30)
        evidence(app, "chatmore-15-draft-saved")
        XCTAssertTrue(landed, "the second save does not say which version it wrote")
        XCTAssertTrue(
            containing(app, StubChatMore.typed).exists,
            "the version that was just written does not hold the words that were typed"
        )
    }

    /// A draft the house will not hand over: the canvas says so, in words.
    ///
    /// Draft 44 is never served, so this is the canvas's error state and not a
    /// blank sheet with a spinner on it.
    @MainActor
    func testChatMore_ADraftThatCannotBeReadSaysSo() throws {
        guard let app = openChatMore(
            search: "Four letters",
            title: StubChatMore.letters,
            waitingFor: StubChatMore.lettersAsked
        ) else { return }
        guard openDraftCard(app, StubChatMore.shedCard) else { return }

        let why = startingWith(app, ChatMoreStringsUnderTest.couldNotReadTheDraftPrefix)
        let shown = why.waitForExistence(timeout: 25)
        evidence(app, "chatmore-16-draft-unreadable")
        XCTAssertTrue(shown, "a draft that cannot be read leaves the canvas silent")
        XCTAssertFalse(
            containing(app, "500").exists,
            "the canvas shows a status code instead of a sentence"
        )
    }

    // MARK: - long-document jobs

    /// A live job: its own progress line, a bar that agrees with it, and a
    /// stop that reaches the server.
    ///
    /// The bar is asserted **against the line**, settled. The job streams
    /// three progress frames and stops at `read: 5`, `part: 6`, so the line
    /// reads "reading part 6 of 11" and the bar must read "5 of 11 parts
    /// read". A bar drawn from `part` instead of `read` says "6 of 11" —
    /// which is the one confusion this card can have, and it is pinned here by
    /// two numbers that differ by one.
    @MainActor
    func testChatMore_ALongDocumentJobShowsItsProgressAndStops() throws {
        guard let app = openChatMore(
            search: "tenancy pack",
            title: StubChatMore.pack,
            waitingFor: StubChatMore.packAsked
        ) else { return }

        let line = containing(app, StubChatMore.jobSettledLine)
        let settled = line.waitForExistence(timeout: 40)
        evidence(app, "chatmore-17-job-live")
        XCTAssertTrue(settled, "the live job's progress line never reached its last part")
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.partsRead(read: 5, parts: 11)).exists,
            "the progress bar is drawn from the part being read, not from the parts read"
        )

        let stop = app.buttons[ChatMoreStringsUnderTest.stop].firstMatch
        guard stop.waitForExistence(timeout: 15) else {
            evidence(app, "chatmore-18-no-stop")
            XCTFail("a live job offers no stop")
            return
        }
        stop.tap()
        // A stopped job has no card — its offer is the "carry on" under its
        // own reply — so the card leaving **is** the stop having reached the
        // server. A stop that went nowhere leaves the card where it was.
        let gone = waitUntilGone(containing(app, StubChatMore.jobLinePrefix), timeout: 30)
        evidence(app, "chatmore-19-job-stopped")
        XCTAssertTrue(gone, "the job's card is still there after stop")
    }

    /// A summary that was interrupted offers to carry on, and says how much is
    /// left.
    ///
    /// `read: 4` of `parts: 11` is seven left. Eleven is the count a button
    /// reading `parts` would show and six is what `parts - part` would give,
    /// so the number in the label is the assertion.
    @MainActor
    func testChatMore_AStoppedSummaryOffersCarryOnWithThePartsLeft() throws {
        guard let app = openChatMore(
            search: "tenancy pack",
            title: StubChatMore.pack,
            waitingFor: StubChatMore.packAsked
        ) else { return }

        let carry = app.buttons[ChatMoreStringsUnderTest.carryOn(7)].firstMatch
        let offered = carry.waitForExistence(timeout: 30)
        evidence(app, "chatmore-20-carry-on")
        XCTAssertTrue(
            offered,
            "the interrupted summary does not offer to carry on with 7 parts left"
        )
        XCTAssertFalse(
            app.buttons[ChatMoreStringsUnderTest.carryOn(11)].exists,
            "the offer counts the whole document instead of what is left"
        )

        carry.tap()
        // Once it is running again it is no longer stopped, so the offer goes.
        // A resume that went nowhere leaves the button exactly where it was.
        let gone = waitUntilGone(carry, timeout: 30)
        evidence(app, "chatmore-21-carried-on")
        XCTAssertTrue(gone, "the carry-on is still offered after it was taken")
    }

    // MARK: - the mid-answer lookup card

    /// The card that stops an answer to ask: the question, the words, where
    /// they would go, and three answers.
    ///
    /// "goes to duckduckgo.com" is the one line that cannot be left out: the
    /// card exists so a person decides whether their words leave the house,
    /// and without the destination they are deciding that with the fact
    /// missing. It is asserted as an element of its own, which a row drawing
    /// the server's whole sentence as ordinary words could not produce.
    @MainActor
    func testChatMore_ALookupCardAsksBeforeItGoesOnline() throws {
        guard let app = openChatMore(
            search: "Lisbon",
            title: StubChatMore.lisbon,
            waitingFor: StubChatMore.lisbonAsked
        ) else { return }
        evidence(app, "chatmore-22-lookup-card")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.mayILookThisUp).exists,
            "the lookup card has no title"
        )
        XCTAssertTrue(
            exactly(app, StubChatMore.lisbonQueryQuoted).exists,
            "a search's words are not quoted on the card"
        )
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.goesTo(StubChatMore.duck)).exists,
            "the card does not say where the words would go"
        )
        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.allowOnce].exists,
            "the card offers no allow-once"
        )
        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.allowInThisChat].exists,
            "the card offers no allow-for-this-chat"
        )
        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.notNow].exists,
            "the card offers no refusal"
        )
    }

    /// Allow once: the answer streams, the thread is re-read, and the card
    /// says it was answered.
    ///
    /// The streamed words and the row the server keeps are **different text**.
    /// The stream never says "Tagus" and the kept row does, so a screen built
    /// from the stream alone cannot pass this — which is the whole reason
    /// ``ChatMoreModel`` re-opens the thread after a decision.
    @MainActor
    func testChatMore_AllowOnceAnswersTheCardAndTheReplyArrives() throws {
        guard let app = openChatMore(
            search: "Lisbon",
            title: StubChatMore.lisbon,
            waitingFor: StubChatMore.lisbonAsked
        ) else { return }

        app.buttons[ChatMoreStringsUnderTest.allowOnce].firstMatch.tap()
        let answer = containing(app, StubChatMore.lisbonKeptWord)
        let arrived = answer.waitForExistence(timeout: 40)
        evidence(app, "chatmore-23-lookup-allowed")
        XCTAssertTrue(arrived, "the answer the server kept never reached the thread")
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.cardStateAllowedOnce).exists,
            "the card does not say it was allowed once"
        )
    }

    /// A card that has already been answered offers no second answer.
    ///
    /// The server 409s one (`already_answered`), so a live button there is a
    /// control that cannot do what it says.
    @MainActor
    func testChatMore_AnAnsweredCardOffersNoSecondAnswer() throws {
        guard let app = openChatMore(
            search: "41 bus",
            title: StubChatMore.bus,
            waitingFor: StubChatMore.busAsked
        ) else { return }
        evidence(app, "chatmore-24-card-answered")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.cardStateAllowedOnce).exists,
            "an answered card does not say what it was answered with"
        )
        XCTAssertFalse(
            app.buttons[ChatMoreStringsUnderTest.allowOnce].exists,
            "an answered card still offers allow-once, which the server refuses"
        )
        XCTAssertFalse(
            app.buttons[ChatMoreStringsUnderTest.notNow].exists,
            "an answered card still offers a refusal"
        )
    }

    /// Allow in this chat: the answer arrives and the thread's own chip turns
    /// on.
    ///
    /// This is a **page** card, so its address is bare rather than quoted — a
    /// URL in quotation marks reads as a phrase to search for — and that is
    /// asserted here because nothing else in this file covers it.
    ///
    /// The chip turning on is the half that matters: `internet off` and
    /// `internet on for this chat` are two wordings of the same chip, so a
    /// chip reading the household's policy instead of the thread's flag would
    /// stay on "off" through all of this.
    @MainActor
    func testChatMore_AllowInThisChatTurnsTheChipOn() throws {
        guard let app = openChatMore(
            search: "ferry",
            title: StubChatMore.ferry,
            waitingFor: StubChatMore.ferryAsked
        ) else { return }
        evidence(app, "chatmore-25-page-card")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.mayIReadThisPage).exists,
            "a page card is titled as a search"
        )
        XCTAssertTrue(
            exactly(app, StubChatMore.ferryQuery).exists,
            "a page's address is quoted like a search's words"
        )
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.internetOff).exists,
            "a thread that may not go online says otherwise"
        )

        app.buttons[ChatMoreStringsUnderTest.allowInThisChat].firstMatch.tap()
        let answer = containing(app, StubChatMore.ferryKeptWord)
        let arrived = answer.waitForExistence(timeout: 40)
        evidence(app, "chatmore-26-chip-on")
        XCTAssertTrue(arrived, "the answer the server kept never reached the thread")
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.internetOnForThisChat)
                .waitForExistence(timeout: 20),
            "the thread's chip did not follow the decision"
        )
    }

    /// A household that never goes online: the chip is a fact and not a
    /// switch.
    ///
    /// The thread's own flag changes nothing a person would see under that
    /// policy, so the chip is drawn — "internet off" is worth knowing before
    /// typing — and is **not a button**. The same wording either way is the
    /// point: what is asserted is the element's kind, not its words.
    @MainActor
    func testChatMore_TheChipIsNotTappableWhenTheHouseSaysNever() throws {
        chatMoreReset()
        chatMoreControl("{\"internet\":\"never\"}")
        let (app, landed) = openChat()
        guard landed else {
            XCTFail("the thread list never answered")
            return
        }
        guard openChatMoreThread(
            app,
            search: "Lisbon",
            title: StubChatMore.lisbon,
            waitingFor: StubChatMore.lisbonAsked
        ) else {
            XCTFail("the Lisbon thread drew no transcript")
            return
        }
        evidence(app, "chatmore-27-chip-never")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.internetOff).exists,
            "a house that never goes online does not say so above the composer"
        )
        XCTAssertEqual(
            0,
            app.buttons
                .matching(
                    NSPredicate(format: "label == %@", ChatMoreStringsUnderTest.internetOff)
                )
                .count,
            "the chip is a button under a policy where tapping it changes nothing"
        )
    }

    // MARK: - heads-up picks

    /// A heads-up card **is** the reply until an option is picked.
    ///
    /// And `choose_another` is not among the options: it re-opens the picture
    /// picker, there is no picker here, so the card says nothing about it. Its
    /// label is a sentence no other option shares, which is how a card that
    /// drew it anyway is caught.
    @MainActor
    func testChatMore_AHeadsUpCardIsTheReplyUntilAnOptionIsPicked() throws {
        guard let app = openChatMore(
            search: "Rewrite the Fernbrook",
            title: StubChatMore.rewrite,
            waitingFor: StubChatMore.rewriteAsked
        ) else { return }
        evidence(app, "chatmore-28-heads-up")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.headsUp).exists,
            "the heads-up card is not marked as one"
        )
        XCTAssertTrue(
            startingWith(app, StubChatMore.rewriteWarning).exists,
            "the card does not say what it is warning about"
        )
        XCTAssertTrue(
            app.buttons[StubChatMore.rewriteAnyway].exists,
            "the card offers neither of its two ways on"
        )
        XCTAssertTrue(
            app.buttons[StubChatMore.summariseInstead].exists,
            "the card offers only one way on"
        )
        XCTAssertFalse(
            containing(app, StubChatMore.chooseAnotherLabel).exists,
            "the card offers an option that re-opens a picture picker this app has not got"
        )
        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.neverWarn].exists,
            "the card offers no way to stop being warned"
        )
    }

    /// "don't warn me about this again" is this phone's, and the **next** pick
    /// carries it to the server.
    ///
    /// No route stores the list, so the only way to see it leave is the
    /// fixture echoing what arrived: the kept reply says "skip: calc". Without
    /// the silencing it says "skip: nothing".
    ///
    /// The silencing is kept in `UserDefaults` and so outlives a launch, which
    /// is why the tap is conditional: on a Simulator that has run this case
    /// before, the kind is already silenced and the button is already gone.
    /// What is asserted is the sentence and the echo, both of which hold
    /// either way.
    @MainActor
    func testChatMore_NeverWarnMeSilencesThatKindOnThisPhone() throws {
        guard let app = openChatMore(
            search: "Averages",
            title: StubChatMore.averages,
            waitingFor: StubChatMore.averagesAsked
        ) else { return }

        let never = app.buttons[ChatMoreStringsUnderTest.neverWarn].firstMatch
        if never.waitForExistence(timeout: 20) {
            never.tap()
        } else {
            print("CHATMORE the calc heads-up was already silenced on this Simulator")
        }
        let silenced = containing(app, ChatMoreStringsUnderTest.silencedCalc)
        let said = silenced.waitForExistence(timeout: 20)
        evidence(app, "chatmore-29-silenced")
        XCTAssertTrue(said, "the card does not say this phone will not be warned again")

        app.buttons[StubChatMore.roughFigure].firstMatch.tap()
        let echoed = containing(app, StubChatMore.skipEcho)
        let arrived = echoed.waitForExistence(timeout: 40)
        evidence(app, "chatmore-30-heads-up-picked")
        XCTAssertTrue(arrived, "the pick did not carry this phone's silenced kinds to the server")
    }

    // MARK: - report a message

    /// Reporting a reply shows exactly what would be sent, and nothing else.
    ///
    /// The thread has **two** questions, and the report must carry the one
    /// above the reply being reported rather than the thread's first. Neither
    /// absence can be asserted — a sheet does not hide the transcript behind
    /// it from a query — so this counts: the sheet adds one element to
    /// whichever question it used, and the right question ends up with more.
    @MainActor
    func testChatMore_ReportingAReplyShowsExactlyWhatWouldBeSent() throws {
        guard let app = openChatMore(
            search: "out loud",
            title: StubChatMore.spoken,
            waitingFor: StubChatMore.spokenFirstAsked
        ) else { return }

        let beforeRight = countOf(app, StubChatMore.spokenSecondAsked)
        let beforeWrong = countOf(app, StubChatMore.spokenFirstAsked)
        let menu = app.buttons
            .matching(
                NSPredicate(
                    format: "label == %@ AND value == %@",
                    ChatMoreStringsUnderTest.more, StubChatMore.reportedMessageID
                )
            )
            .firstMatch
        guard menu.waitForExistence(timeout: 20) else {
            evidence(app, "chatmore-31-no-row-menu")
            XCTFail("the reply has no menu of its own")
            return
        }
        menu.tap()
        let item = app.buttons[ChatMoreStringsUnderTest.reportThisReply].firstMatch
        guard item.waitForExistence(timeout: 15) else {
            evidence(app, "chatmore-32-no-report-item")
            XCTFail("the row's menu does not offer to report the reply")
            return
        }
        item.tap()

        let title = exactly(app, ChatMoreStringsUnderTest.reportTitle)
        let opened = title.waitForExistence(timeout: 20)
        evidence(app, "chatmore-33-report")
        XCTAssertTrue(opened, "report-a-reply opened no sheet")
        XCTAssertTrue(
            containing(app, ChatMoreStringsUnderTest.reportExplainsOpening).exists,
            "the sheet does not say what reporting does"
        )
        // The question was spoken, so its label says so — a report that read
        // `source` off the reply instead of the question would label it
        // "your question".
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.reportYourQuestionSpoken).exists,
            "the report does not say the question was spoken"
        )
        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.reportTheReply).exists,
            "the report does not label the reply"
        )
        XCTAssertTrue(
            containing(app, ChatMoreStringsUnderTest.reportOnTheHomeServer).exists,
            "the report does not say which model answered, or where"
        )

        let afterRight = countOf(app, StubChatMore.spokenSecondAsked)
        let afterWrong = countOf(app, StubChatMore.spokenFirstAsked)
        print(
            "CHATMORE report question counts right \(beforeRight)->\(afterRight)"
                + " wrong \(beforeWrong)->\(afterWrong)"
        )
        XCTAssertGreaterThan(
            afterRight,
            beforeRight,
            "the report does not carry the question the reply answered"
        )
        XCTAssertEqual(
            beforeWrong,
            afterWrong,
            "the report carries the thread's first question instead of the reply's"
        )
        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.reportShareLabel].exists,
            "the sheet offers nowhere to send the report"
        )
    }

    // MARK: - voice

    /// The mic **or** the send button, never both.
    ///
    /// Android's one rule here, and it is the whole of what either control
    /// means: a mic beside a half-typed message would record over it.
    @MainActor
    func testChatMore_TheMicIsOfferedOnlyWithAnEmptyComposer() throws {
        guard let app = openChatMore(
            search: "Say something",
            title: StubChatMore.saySomething,
            waitingFor: StubChatMore.saySomethingAsked
        ) else { return }
        evidence(app, "chatmore-34-mic")

        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.talk].waitForExistence(timeout: 20),
            "an empty composer offers no mic"
        )
        XCTAssertEqual(
            0,
            app.buttons
                .matching(NSPredicate(format: "label == %@", ChatStringsUnderTest.send))
                .count,
            "the mic and the send button are drawn at the same time"
        )

        let field = composerField(app)
        guard field.waitForExistence(timeout: 20) else {
            XCTFail("there is no composer to type into")
            return
        }
        field.tap()
        app.typeText("a word")
        evidence(app, "chatmore-35-send")
        XCTAssertTrue(
            waitUntilGone(app.buttons[ChatMoreStringsUnderTest.talk].firstMatch),
            "the mic is still there with words in the composer"
        )
        XCTAssertNotNil(composerSend(app), "a composer with words in it offers no send")
    }

    /// A spoken turn puts **both** rows in the thread.
    ///
    /// The core does the speech-to-text and writes the question and the answer
    /// itself, and the app re-reads the thread — so the words on screen are
    /// the server's and not this phone's guess at them. Neither sentence is
    /// anywhere in this thread beforehand.
    @MainActor
    func testChatMore_ATalkTurnPutsBothRowsInTheThread() throws {
        let app = launchChatMoreTalk()
        guard openChatMoreThread(
            app,
            search: "Say something",
            title: StubChatMore.saySomething,
            waitingFor: StubChatMore.saySomethingAsked
        ) else {
            XCTFail("the thread a spoken turn goes into drew no transcript")
            return
        }
        guard let ring = openTalk(app) else {
            XCTFail("the mic opened no talk screen")
            return
        }
        evidence(app, "chatmore-36-talk")

        // A hold, which is what the gesture is: `press(forDuration:)` is a
        // touch down, a wait and a touch up, and
        // `DragGesture(minimumDistance: 0)` answers both ends of it. The
        // length does not decide the clip — the seam's canned silence is a
        // fixed stretch — only that the press began and ended.
        ring.press(forDuration: 0.8)

        let answered = containing(app, StubChatMore.spokenAnswer)
        let arrived = answered.waitForExistence(timeout: 60)
        evidence(app, "chatmore-37-talk-answered")
        XCTAssertTrue(arrived, "a spoken turn produced no answer")
        XCTAssertTrue(
            containing(app, StubChatMore.spokenHeard).exists,
            "the turn's own words are not on screen"
        )

        app.buttons[ChatMoreStringsUnderTest.backToTheChat].firstMatch.tap()
        let inThread = containing(app, StubChatMore.spokenHeard)
        XCTAssertTrue(
            inThread.waitForExistence(timeout: 30),
            "the question the house heard is not in the thread"
        )
        evidence(app, "chatmore-38-talk-in-thread")
        XCTAssertTrue(
            containing(app, StubChatMore.spokenAnswer).exists,
            "the answer is not in the thread"
        )
    }

    /// A spoken row says who Domovoi heard, and how sure it is.
    ///
    /// One row is confident about Ana and one is not confident about Chevy.
    /// The marker's screen-reader sentence is the assertion, because that is
    /// what the element carries: a weak match ends "but isn't sure", and a
    /// marker drawn at full strength would read "spoken by Chevy" — which is
    /// asserted absent.
    @MainActor
    func testChatMore_ASpokenRowSaysWhoDomovoiHeard() throws {
        guard let app = openChatMore(
            search: "out loud",
            title: StubChatMore.spoken,
            waitingFor: StubChatMore.spokenFirstAsked
        ) else { return }
        evidence(app, "chatmore-39-speaker-marks")

        XCTAssertTrue(
            containing(app, ChatMoreStringsUnderTest.spokenBy(StubChatMore.ana)).exists,
            "a row Domovoi was sure about does not say who said it"
        )
        XCTAssertTrue(
            containing(app, ChatMoreStringsUnderTest.spokenMaybeBy(StubChatMore.chevy)).exists,
            "a row Domovoi was unsure about does not say so"
        )
        XCTAssertEqual(
            0,
            countOf(app, ChatMoreStringsUnderTest.spokenBy(StubChatMore.chevy)),
            "a weak match is drawn as a certain one"
        )
    }

    /// A turn the core refuses: **the server's own sentence**, not the app's
    /// fallback.
    ///
    /// Both say a clip is at most thirty seconds, in different words, which is
    /// the only way to tell which one is on screen. The app's wording exists
    /// for a refusal that arrives without a sentence, and this refusal has
    /// one.
    @MainActor
    func testChatMore_ATurnTooLongSaysTheServersSentence() throws {
        let app = launchChatMoreTalk(voice: "too_long")
        guard openChatMoreThread(
            app,
            search: "Say something",
            title: StubChatMore.saySomething,
            waitingFor: StubChatMore.saySomethingAsked
        ) else {
            XCTFail("the thread a spoken turn goes into drew no transcript")
            return
        }
        guard let ring = openTalk(app) else {
            XCTFail("the mic opened no talk screen")
            return
        }
        ring.press(forDuration: 0.8)

        let refusal = containing(app, StubChatMore.tooLongSentence)
        let said = refusal.waitForExistence(timeout: 40)
        evidence(app, "chatmore-40-turn-too-long")
        XCTAssertTrue(said, "a refused turn does not say what the server said")
        XCTAssertFalse(
            containing(app, ChatMoreStringsUnderTest.voiceTooLongFallbackWords).exists,
            "the app's own wording is on screen instead of the server's"
        )
        XCTAssertFalse(
            containing(app, "clip_too_long").exists,
            "the screen shows the server's code instead of its sentence"
        )
    }

    // MARK: - the ordinary thread, and growth

    /// A thread with none of this in it draws none of it.
    ///
    /// The case that stops the extras becoming furniture. Thread 11 has six
    /// plain messages: no chips, no cards, no job, no marker — and the two
    /// things an ordinary thread **does** have, the chip above the composer
    /// and the mic beside it, are asserted present so that "nothing is drawn"
    /// cannot pass by the screen having failed to load.
    @MainActor
    func testChatMore_AnOrdinaryThreadDrawsNoneOfTheExtras() throws {
        chatMoreReset()
        let (app, landed) = openChat()
        guard landed else {
            XCTFail("the thread list never answered")
            return
        }
        guard openThread(app, StubChat.fridge, waitingFor: StubChat.secondMessage) else {
            XCTFail("the fridge thread drew no transcript")
            return
        }
        evidence(app, "chatmore-41-ordinary")

        XCTAssertTrue(
            exactly(app, ChatMoreStringsUnderTest.internetOff).exists,
            "an ordinary thread does not say whether it may go online"
        )
        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.talk].exists,
            "an ordinary thread offers no mic"
        )
        XCTAssertFalse(
            exactly(app, ChatMoreStringsUnderTest.sources).exists,
            "a thread with no documents in it draws a sources heading"
        )
        XCTAssertFalse(
            exactly(app, ChatMoreStringsUnderTest.headsUp).exists,
            "a thread with no heads-up in it draws one"
        )
        XCTAssertFalse(
            startingWith(app, StubChatMore.carryOnPrefix).exists,
            "a thread with no job in it offers to carry one on"
        )
        XCTAssertFalse(
            startingWith(app, StubChatMore.jobLinePrefix).exists,
            "a thread with no job in it draws a job card"
        )
        XCTAssertFalse(
            containing(app, StubChatMore.spokenPrefix).exists,
            "a thread nobody spoke into draws a speaker marker"
        )
        XCTAssertFalse(
            startingWith(app, StubChatMore.markerPrefix).exists,
            "a thread with nothing attached draws an attachment marker"
        )
    }

    /// The cards' own leaves, measured at `large` and at AX5.
    ///
    /// Growth and never a total (the testing playbook): each leaf is compared
    /// with itself at the other size, and a leaf that could not be built is
    /// skipped rather than failed. The lookup card is the subject because it
    /// is the densest new thing on the screen — a title, a line of words that
    /// may be long, a destination and three buttons — and the one where a
    /// fixed height would be easiest to get away with.
    @MainActor
    func testChatMore_TheCardsGrowWithTheTypeSize() throws {
        let leaves: [(String, String)] = [
            ("card-title", ChatMoreStringsUnderTest.mayILookThisUp),
            ("card-query", StubChatMore.lisbonQueryQuoted),
            ("card-destination", ChatMoreStringsUnderTest.goesTo(StubChatMore.duck)),
            ("chip", ChatMoreStringsUnderTest.internetOff),
        ]

        var measured: [String: [String: CGFloat]] = [:]
        for (label, mode) in [
            ("large", StubHouseUITests.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            guard let app = openChatMore(
                mode,
                search: "Lisbon",
                title: StubChatMore.lisbon,
                waitingFor: StubChatMore.lisbonAsked
            ) else { return }
            usleep(1_000_000)
            evidence(app, "chatmore-42-scale-\(label)")
            var seen: [String: CGFloat] = [:]
            for (leaf, words) in leaves {
                // `staticTexts`, not `.any`: a card's words sit inside views
                // that carry the same label, and `.any` measures a 44-point
                // control with padding rather than a line of text.
                let found = app.staticTexts
                    .matching(NSPredicate(format: "label == %@", words))
                    .firstMatch
                if found.exists { seen[leaf] = found.frame.height }
                print("CHATMORE SCALE [\(label)] \(leaf) height=\(seen[leaf] ?? -1)")
            }
            measured[label] = seen
            app.terminate()
        }

        let small = measured["large"] ?? [:]
        let big = measured["AX5"] ?? [:]
        var compared = 0
        var stuck: [String] = []
        for (leaf, height) in small.sorted(by: { $0.key < $1.key }) {
            guard let grown = big[leaf] else { continue }
            compared += 1
            if grown < height * 1.8 { stuck.append("\(leaf) \(height)->\(grown)") }
        }
        XCTAssertEqual([], stuck, "a leaf of the lookup card does not scale")
        XCTAssertGreaterThanOrEqual(
            compared,
            2,
            "nothing could be compared, so this proved nothing: \(small) / \(big)"
        )
    }

    /// A reply that arrives a word at a time stays **above the keyboard** all
    /// the way to its last word.
    ///
    /// ### The blind spot this closes
    ///
    /// `I1H2-SIM-REPORT.md`'s first open item. `testAReplyArrivesAWordAtATime`
    /// asserted that the live reply was clear of the keyboard, and it was —
    /// but the reply it measured was seven lines long. Sabotaging the
    /// composer's inset moved its bottom from 389.7 to 477.7 and the
    /// keyboard's top was at 561, so **both** numbers passed and the assertion
    /// could not fail. The stub's reply is eight pieces and about 1,400
    /// characters now, which is nearer thirty wrapped lines than seven, and
    /// this case measures the same thing against it.
    ///
    /// Thread 11 is the subject because its transcript already overflows
    /// before a word is sent: a reply that fits proves nothing about scrolling
    /// at all. If the transcript somehow does not overflow, this says so
    /// rather than passing quietly.
    @MainActor
    func testChatMore_ReplyFollowsItsOwnGrowth() throws {
        chatMoreReset()
        let (app, landed) = openChat(fail: "slow")
        guard landed else {
            XCTFail("the thread list never answered")
            return
        }
        guard openThread(app, StubChat.fridge, waitingFor: StubChat.secondMessage) else {
            XCTFail("the fridge thread drew no transcript")
            return
        }
        XCTAssertTrue(say(app, "tell me the long version"), "the composer would not send")

        let ending = containing(app, StubChat.replyEnding)
        let finished = ending.waitForExistence(timeout: 90)
        // Let the scroll settle: the last delta lands and the list animates
        // after it.
        usleep(2_000_000)
        evidence(app, "chatmore-43-long-reply")
        XCTAssertTrue(finished, "the long reply never reached its last words")

        let keyboard = app.keyboards.firstMatch
        guard keyboard.exists else {
            XCTFail("the keyboard is not up, so this measured nothing")
            return
        }
        let keyboardTop = keyboard.frame.minY
        let live = containing(app, StubChat.replyEnding)
        let bottom = live.frame.maxY
        print("CHATMORE long reply bottom=\(bottom) keyboardTop=\(keyboardTop)")
        print("CHATMORE long reply frame=\(live.frame)")
        // The thing the earlier case could not tell apart: with a reply this
        // long, a transcript that does not scroll puts its last words well
        // below 561.
        XCTAssertLessThanOrEqual(
            bottom,
            keyboardTop,
            "the end of a long reply is behind the keyboard"
        )
        // And the warning the earlier case needed: if the transcript fits,
        // nothing here was a test of scrolling.
        let first = containing(app, StubChat.secondMessage)
        if first.exists, first.frame.minY > 0 {
            print(
                "CHATMORE WARNING the transcript did not overflow"
                    + " (first message still at \(first.frame.minY)) — this case proved little"
            )
        }
    }

    // MARK: - the audit

    /// Apple's audit over a conversation with the new things in it.
    ///
    /// Two screens, one case: thread 18 is the chips, the attachment marker
    /// and the notes, and thread 21 is the lookup card and the three answers.
    /// Both are the same screen with different data in it, so one case is
    /// enough and two audits cover twice as many leaves.
    ///
    /// Classified **by signature and never by a total** — the floor on this
    /// screen came back 6, 5 and 5 across three runs of one commit — and a
    /// finding that names an element is a defect until measured otherwise,
    /// which is how the modal bug on this track was found.
    @MainActor
    @available(iOS 17.0, *)
    func testChatMore_Audits() throws {
        var rest: [String] = []
        var floors: [Int] = []

        let screens: [(String, String, String, String)] = [
            ("chatmore-doc", "Fernbrook", StubChatMore.lease, StubChatMore.leaseReply),
            ("chatmore-card", "Lisbon", StubChatMore.lisbon, StubChatMore.lisbonAsked),
        ]
        for (name, search, title, words) in screens {
            guard let app = openChatMore(search: search, title: title, waitingFor: words)
            else { return }
            let found = try auditIssues(app, name)
            evidence(app, "chatmore-44-audit-\(name)")
            let (floor, theRest) = StubHouseUITests.floorAndRest(found)
            floors.append(floor)
            rest.append(contentsOf: theRest)
            print("CHATMORE AUDIT [\(name)] floor=\(floor) rest=\(theRest.count)")
            for line in theRest { print("CHATMORE AUDIT REST :: \(line)") }
            app.terminate()
        }

        // The signature the Chat list already measured to be false: `Text
        // clipped` with **no element**, which is the audit saying a leaf *may*
        // clip at a larger size and having nothing to measure. Every leaf it
        // could name on this screen grows instead, which
        // ``testChatMore_TheCardsGrowWithTheTypeSize()`` measures.
        let clipped = rest.filter {
            $0.hasPrefix("Text clipped") && $0.hasSuffix("| element: nil")
        }
        let unexplained = rest.filter { !clipped.contains($0) }
        XCTAssertEqual(
            [],
            unexplained,
            "a conversation with the new things in it has an audit finding of its own"
        )
        print("CHATMORE AUDIT floors \(floors), unpinned by design — see testTheChatListAudits")
    }
}

/// The ChatMore half of the stub house, by the words that appear on screen.
///
/// Spelt out here rather than imported, like ``StubChat``: a query that
/// silently followed a renamed constant would stop testing what it claims to.
/// These are the stub's own (`stubhouse.py`'s `CHAT_THREADS` and
/// `CHAT_MESSAGES`, and `fixtures/chatmore.py`).
enum StubChatMore {

    // Thread 18: a document and a reply over it.
    static let lease = "The lease and the boiler at Fernbrook"
    static let leaseReply = "The boiler is the landlord's"
    static let leaseSummary = "a tenancy agreement at Fernbrook"
    static let leaseNote = "pages 3 and 4 are scans"
    static let markerPrefix = "attached lease.pdf"
    static let replyNote = "two pages were scans and were read by eye"
    /// The cited page's chip, and the one that was read but not cited. The
    /// server sends the **uncited** one first.
    static let citedChip = "p. 2"
    static let uncitedChip = "p. 7 · read"
    /// A phrase from page 2's passage, and a word from page 7's that appears
    /// nowhere else in the thread.
    static let citedPassage = "service the boiler annually"
    static let uncitedPassageWord = "gutters"

    // Thread 19: four draft cards, one draft each.
    static let letters = "Four letters about Fernbrook"
    static let lettersAsked = "Write the landlord a letter about the boiler"
    /// The four cards' sentences, which are what the canvas is opened by.
    static let boilerCard = "draft: Boiler service at Fernbrook, version 2"
    static let guttersCard = "draft: Gutters at Fernbrook, version 2"
    static let fenceCard = "draft: Fence at Fernbrook, version 1"
    static let shedCard = "draft: Shed at Fernbrook, version 1"
    /// Draft 41's three versions, each closing with a name no other holds.
    static let v1Word = "Hawthorn"
    static let v2Word = "Marchmont"
    static let v3Word = "Pemberley"
    /// Draft 42's v2 **removed** this and added that; the removed phrase is in
    /// no body on screen, so it is visible exactly when the changes are.
    static let removedPhrase = "Yours faithfully"
    static let addedName = "Mr Alderney"
    /// What the hand-edit case types into draft 43.
    static let typed = " and the gate"

    // Thread 20: the long-document jobs.
    static let pack = "Summarise the Fernbrook tenancy pack"
    static let packAsked = "Summarise the whole Fernbrook tenancy pack"
    /// Where the live job's progress line settles: three frames from `part 3`.
    static let jobSettledLine = "reading part 6 of 11"
    static let jobLinePrefix = "reading part"
    static let carryOnPrefix = "carry on ("

    // Threads 21, 22, 23: the lookup card.
    static let lisbon = "The weather in Lisbon"
    static let lisbonAsked = "What is the weather doing in Lisbon?"
    /// Quoted, because it is a search.
    static let lisbonQueryQuoted = "\u{201C}lisbon weather today\u{201D}"
    static let duck = "duckduckgo.com"
    /// A word the **kept row** holds and the streamed deltas never do.
    static let lisbonKeptWord = "Tagus"
    static let ferry = "The ferry timetable"
    static let ferryAsked = "Read me the ferry timetable from this page"
    /// Bare, because it is a page.
    static let ferryQuery = "cp.pt/ferry"
    static let ferryKeptWord = "Sesimbra"
    static let bus = "Where the 41 bus goes"
    static let busAsked = "Where does the 41 bus go?"

    // Threads 24 and 25: the heads-up cards.
    static let averages = "Averages over the Fernbrook pack"
    static let averagesAsked = "What is the average rent across the Fernbrook pack?"
    static let roughFigure = "give me a rough figure"
    /// What the fixture's kept reply echoes back: the `skip` list it received.
    static let skipEcho = "skip: calc"
    static let rewrite = "Rewrite the Fernbrook summary"
    static let rewriteAsked = "Rewrite the whole Fernbrook summary in plain English"
    static let rewriteWarning = "Rewriting a whole document takes a while and I"
    static let rewriteAnyway = "rewrite it anyway"
    static let summariseInstead = "summarise it instead"
    /// The option this app does not draw, worded so that nothing else matches.
    static let chooseAnotherLabel = "let me pick another picture instead"

    // Thread 26: two pictures on one row.
    static let photographs = "Two photographs from the Fernbrook shed"
    static let photographsAsked = "What are these two?"
    static let ladder = "ladder.jpg"
    static let gutter = "gutter.png"

    // Thread 27: two spoken rows, and two questions.
    static let spoken = "Questions asked out loud"
    static let spokenFirstAsked = "How long under the grill?"
    static let spokenSecondAsked = "And the oven?"
    /// Message 274, the reply the report case reports.
    static let reportedMessageID = "274"
    static let ana = "Ana"
    static let chevy = "Chevy"
    /// Any speaker marker's sentence begins with this word.
    static let spokenPrefix = "spoken"

    // Thread 28, and the turn that goes into it.
    static let saySomething = "Say something to the house"
    static let saySomethingAsked = "ready when you are"
    /// What the core writes for a turn: the question it heard and its answer.
    static let spokenHeard = "How long does the grill take for a frittata"
    static let spokenAnswer = "Four minutes under a hot grill"
    /// The core's own 413 sentence, which is **not** the app's fallback.
    static let tooLongSentence = "a voice turn is at most 30 seconds"
}

/// The ChatMore views' own words, from ``ChatMoreStrings``.
///
/// Spelt out, as ``ChatStringsUnderTest`` is, with the computed ones rebuilt
/// here rather than called: a case that called the app's own function would
/// pass whatever that function was changed to say.
enum ChatMoreStringsUnderTest {
    static let sources = "sources"
    static let theSentenceTheReplyUsed = "the sentence the reply used"
    static let pictureDidntLoad = "this picture didn't load"
    static func pictureNamed(_ name: String) -> String { "picture \(name)" }
    static func partsRead(read: Int, parts: Int) -> String {
        "\(read) of \(parts) part\(parts == 1 ? "" : "s") read"
    }

    static let stop = "stop"
    static func carryOn(_ left: Int) -> String {
        "carry on (\(left) part\(left == 1 ? "" : "s") left)"
    }

    static let mayILookThisUp = "May I look this up online?"
    static let mayIReadThisPage = "May I read this page?"
    static func goesTo(_ destination: String) -> String { "goes to \(destination)" }
    static let allowOnce = "Allow once"
    static let allowInThisChat = "Allow in this chat"
    static let notNow = "Not now"
    static let cardStateAllowedOnce = "allowed once"

    static let internetOff = "internet off"
    static let internetOnForThisChat = "internet on for this chat"

    static let headsUp = "heads-up"
    static let neverWarn = "don't warn me about this again"
    /// ``ChatMoreStrings/silenced(_:)`` for `calc`, with its own `kindWords`.
    static let silencedCalc =
        "no more heads-ups about exact sums, averages and dates on this phone"

    static let open = "open"
    static let edit = "edit"
    static let changes = "changes"
    static let backToTheChat = "back to the chat"
    static func version(_ version: Int) -> String { "v\(version)" }
    static func saveAsVersion(_ version: Int) -> String { "save as v\(version)" }
    static func savedAsVersion(_ version: Int) -> String { "saved as v\(version) · edited by you" }
    static let newerVersionCameIn = "a newer version came in meanwhile; your text is still here"
    static let couldNotReadTheDraftPrefix = "couldn't read the draft"

    static let more = "more"
    static let reportThisReply = "report this reply"
    static let reportTitle = "Report this reply"
    /// The opening of ``ChatMoreStrings/reportExplains``, which is a paragraph.
    static let reportExplainsOpening = "If this answer is offensive"
    static let reportYourQuestionSpoken = "your question (spoken)"
    static let reportTheReply = "the reply"
    static let reportOnTheHomeServer = "on the home server"
    static let reportShareLabel = "share as text"

    static let talk = "talk"
    static let holdToTalk = "hold to talk"
    static func spokenBy(_ who: String) -> String { "spoken by \(who)" }
    static func spokenMaybeBy(_ who: String) -> String {
        "spoken; Domovoi thinks it was \(who), but isn't sure"
    }

    /// Words from ``ChatMoreStrings/voiceTooLongFallback``, which the server's
    /// own sentence must be shown **instead of**.
    static let voiceTooLongFallbackWords = "a voice message is at most"
}
