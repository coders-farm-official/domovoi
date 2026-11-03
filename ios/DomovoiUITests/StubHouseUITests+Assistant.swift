import XCTest

/// The phone as a second Chat place, driven in the Simulator (assistant M1).
///
/// ### What these cases run against, and why it is not Apple's model
///
/// Apple Intelligence is not on a Simulator at all, and a model that answers
/// differently every time cannot be asserted on — a case that needed it would
/// be a case about Apple. So the app takes the engine from a launch argument
/// (`AssistantEngineChoice`, `-domovoi-phone-model`): `fake` runs
/// `FakeAssistantEngine` with one of the named scripts in
/// `ios/Domovoi/Assistant/AssistantEngineHost.swift`, and `off` is every
/// iPhone without the system model — the state in which **nothing about this
/// milestone is drawn**, which has a case of its own because it is the
/// [[no-misleading-controls]] rule's own case.
///
/// Everything a case asserts about a reply is therefore the app's decision
/// around the model — a stop that keeps its mark, an overflow drawn as a
/// divider and not a failure, a reply landing in the conversation that asked
/// it — which is the half worth a Simulator.
///
/// ### These cases cost a shared train only their own run time
///
/// `fixtures/assistant.py` claims **no path** and adds no capability:
/// milestone 1 calls no server route, so there is nothing for this item to
/// serve and nothing another item's fixture can collide with. Only two cases
/// need a house that is not the primary one — the audit's and the
/// no-house one — and both use houses `stub_run.sh` already stands up on
/// their own ports. Every case resets the fixtures as NIGHT-RULES §5 says to,
/// and wipes the **phone's own** chats with `-domovoi-phone-chats wipe`,
/// because those are files in the app's container and would otherwise be the
/// previous case's data (`AssistantStoreSeam` says so where it is written).
extension StubHouseUITests {

    // MARK: - getting to the place

    /// A launch with the phone place's engine chosen.
    ///
    /// ``StubHouseUITests/launch(mode:paired:server:port:strip:push:pushDelay:)``
    /// cannot take these arguments and is shared infrastructure this item may
    /// not edit, so this is its shape with three arguments added and nothing
    /// else changed — the same server, token, push and type-size arguments, in
    /// the same order.
    @MainActor
    func assistantLaunch(
        model: String,
        script: String? = nil,
        wipe: Bool = true,
        mode: String = StubHouseUITests.ordinaryTypeSize,
        port: Int = 6601
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:\(port)",
            "-domovoi-token", Self.token,
            // Chat is pushed by the shell's own audit seam, which is how every
            // Chat case gets there without driving the connection sheet (which
            // would start a LAN sweep across Kamron's real network).
            "-domovoi-audit-push", "Chat",
            "-domovoi-audit-push-delay", "\(Self.pushDelay)",
            "-UIPreferredContentSizeCategoryName", mode,
            "-domovoi-phone-model", model,
        ]
        if let script { app.launchArguments += ["-domovoi-phone-script", script] }
        if wipe { app.launchArguments += ["-domovoi-phone-chats", "wipe"] }
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// The picker's chip for a place, which is a **button** only while there
    /// are two places to choose between.
    @MainActor
    func assistantChip(_ app: XCUIApplication, _ words: String) -> XCUIElement {
        app.buttons[words].firstMatch
    }

    /// Tap through to the chats kept on this phone, and say whether they drew.
    ///
    /// The tap is made on every case rather than only where the house's place
    /// is showing, because **which place opens first is remembered across
    /// launches**: `AssistantPhoneModel.lastUsedPlace` is a `UserDefaults` key
    /// and the Simulator keeps it for the whole run, so the place a case finds
    /// depends on what the case before it did. Tapping the chip is the one
    /// action that is right in both states.
    ///
    /// The marker is the **two-place subtitle**
    /// (``AssistantUnderTest/subtitleBoth``), which is the one thing on screen
    /// that only the phone's list draws: the house's own header keeps the
    /// sentence Chat has said since it shipped ("runs on your own hardware").
    /// A row would have been the wrong marker — a wiped phone has none.
    @MainActor
    func reachPhonePlace(_ app: XCUIApplication) -> Bool {
        let chip = assistantChip(app, AssistantUnderTest.chipPhone)
        guard chip.waitForExistence(timeout: 40) else {
            print("=== NO PHONE CHIP — tree follows ===")
            print(app.debugDescription)
            return false
        }
        chip.tap()
        let drew = startingWith(app, AssistantUnderTest.subtitleBoth).waitForExistence(timeout: 25)
        if !drew {
            print("=== PHONE PLACE DREW NOTHING — tree follows ===")
            print(app.debugDescription)
        }
        return drew
    }

    /// A control of the phone conversation, told apart from a **keyboard key**
    /// that shares its word.
    ///
    /// ``StubHouseUITests/composerSend(_:)`` says what this is for: a key is
    /// hittable and `firstMatch` took it, and five Chat cases once reported
    /// "the composer would not send" about a composer nothing had asked to
    /// send. Above the keyboard, by frame, not by hittability.
    @MainActor
    func phoneControl(_ app: XCUIApplication, _ label: String) -> XCUIElement? {
        let keyboard = app.keyboards.firstMatch
        let keyboardTop = keyboard.exists ? keyboard.frame.minY : .greatestFiniteMagnitude
        let all = app.buttons.matching(NSPredicate(format: "label == %@", label))
        guard all.firstMatch.waitForExistence(timeout: 15) else {
            print("=== NO '\(label)' — tree follows ===")
            print(app.debugDescription)
            return nil
        }
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists else { continue }
            if candidate.frame.maxY <= keyboardTop { return candidate }
        }
        print("=== EVERY '\(label)' IS INSIDE THE KEYBOARD (top \(keyboardTop)) — tree follows ===")
        print(app.debugDescription)
        return nil
    }

    /// Start a chat on the phone and ask it something. Answers whether the
    /// question reached the transcript.
    ///
    /// The question is **saved before the model is asked**
    /// (``AssistantPhoneModel/send(_:)``), so its own words appearing is the
    /// signal that the send was taken rather than refused.
    @MainActor
    func phoneAsk(_ app: XCUIApplication, _ words: String, newChat: Bool = true) -> Bool {
        if newChat {
            guard let start = newChatControl(app) else { return false }
            start.tap()
        }
        let field = composerField(app)
        guard field.waitForExistence(timeout: 20) else {
            print("=== NO PHONE COMPOSER — tree follows ===")
            print(app.debugDescription)
            return false
        }
        field.tap()
        app.typeText(words)
        guard let send = phoneControl(app, ChatStringsUnderTest.send) else { return false }
        send.tap()
        return startingWith(app, words).waitForExistence(timeout: 20)
    }

    /// Back out of a phone conversation to its list.
    @MainActor
    func leavePhoneChat(_ app: XCUIApplication) {
        let back = app.buttons[ChatStringsUnderTest.backToChats].firstMatch
        if back.waitForExistence(timeout: 15) { back.tap() }
    }

    // MARK: - the picker

    /// Both places are offered, with the `runs` label and two chips.
    ///
    /// Catches the branch this screen gets wrong most easily: `PlacePicker`
    /// draws **the chip alone** for one choice (`ChatHome.kt:741`), and that
    /// test inverted would leave a phone that can chat with no way to pick the
    /// place — or a one-place phone with a picker that does nothing.
    @MainActor
    func testAssistant_PlacePickerOffersBothPlaces() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the picker case")
        let app = assistantLaunch(model: "fake")
        let runs = startingWith(app, AssistantUnderTest.runs)
        XCTAssertTrue(runs.waitForExistence(timeout: 40), "the picker's label never drew")
        evidence(app, "assistant-picker")
        XCTAssertTrue(
            assistantChip(app, AssistantUnderTest.chipPhone).exists,
            "the phone's chip is not offered on a phone that can chat"
        )
        XCTAssertTrue(
            assistantChip(app, AssistantUnderTest.chipServerShared).exists,
            "the house's chip is not offered, or does not say the chats are the household's"
        )
        // And the picker really switches: the phone's list draws the sentence
        // only it has.
        XCTAssertTrue(reachPhonePlace(app), "the phone's chip did not open the phone's chats")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.emptyHintPhone).waitForExistence(timeout: 20),
            "an empty phone place does not say how to start one"
        )
        evidence(app, "assistant-phone-empty")
    }

    /// A phone whose system model is unavailable is **the app as it was**.
    ///
    /// The [[no-misleading-controls]] rule's own case: no chip, no `runs`
    /// label, no second list — and Chat still drawing the house's threads
    /// under its own subtitle, so this cannot pass by Chat being broken.
    @MainActor
    func testAssistant_NoPlaceWithoutTheModel() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the no-model case")
        let app = assistantLaunch(model: "off")
        let house = app.buttons[StubChat.fridge].firstMatch
        XCTAssertTrue(house.waitForExistence(timeout: 40), "Chat itself did not draw the house's threads")
        evidence(app, "assistant-no-model")
        XCTAssertFalse(
            assistantChip(app, AssistantUnderTest.chipPhone).exists,
            "a phone chip on a phone that cannot answer — the control that cannot do what it says"
        )
        XCTAssertFalse(
            assistantChip(app, AssistantUnderTest.chipServerShared).exists,
            "a place chip with only one place"
        )
        XCTAssertFalse(
            startingWith(app, AssistantUnderTest.runs).exists,
            "the place picker drew with nothing to pick"
        )
        // The house's own sentence, which is what Chat says when it is the
        // only place. The two-place subtitle must be absent.
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.houseSubtitle).exists,
            "Chat is not drawing its own one-place subtitle"
        )
        XCTAssertFalse(
            startingWith(app, AssistantUnderTest.subtitleBoth).exists,
            "the two-place subtitle on a phone with one place"
        )
    }

    // MARK: - a turn

    /// The reply comes from this phone, with the house's live socket dead.
    ///
    /// The strong half is **the words**: the scripted on-phone engine answers
    /// ``AssistantUnderTest/paperBag``, which the stub house has never heard
    /// of — its own chat replies are entirely different text — so a reply
    /// quietly fetched from the house could not produce this sentence. The
    /// `silent-socket` house is the second half: no state socket, no
    /// `chat.changed` events, nothing pushed, and the place still answers.
    @MainActor
    func testAssistant_PhoneChatAnswersWithNoHouse() throws {
        let app = assistantLaunch(model: "fake", port: Self.silentSocketPort)
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew against the silent house")
        XCTAssertTrue(phoneAsk(app, "How do I keep mushrooms"), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 30),
            "the phone wrote no reply with the house's socket dead"
        )
        evidence(app, "assistant-no-house-reply")
    }

    /// A reply streams in and a stop keeps the words so far, with its mark.
    ///
    /// Catches a stop that leaves the reply running (the whole sentence would
    /// arrive anyway) and a stopped reply saved without its mark — which is
    /// the difference between a reply the person stopped and one that failed,
    /// and the only thing on screen that says which.
    @MainActor
    func testAssistant_ReplyStreamsAndStops() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the stop case")
        let app = assistantLaunch(model: "fake", script: "slow")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, "How do I keep mushrooms"), "the composer would not send")
        // The reply has started but cannot have finished: nine hundred
        // milliseconds a word.
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBagHead).waitForExistence(timeout: 30),
            "no reply began"
        )
        guard let stop = phoneControl(app, AssistantUnderTest.stop) else {
            return XCTFail("the composer offered no stop while a reply was being written")
        }
        stop.tap()
        XCTAssertTrue(
            app.staticTexts[AssistantUnderTest.stopped].waitForExistence(timeout: 20),
            "a stopped reply was saved without its mark"
        )
        evidence(app, "assistant-stopped")
        // The tail of the scripted sentence must never arrive. Written as a
        // wait for its absence rather than one `exists`: the stop has to hold,
        // not merely be true for the instant after the tap.
        XCTAssertTrue(
            waitUntilGone(containing(app, AssistantUnderTest.paperBagTail), timeout: 12),
            "the reply kept writing after the stop"
        )
    }

    /// Every failure has a sentence.
    ///
    /// Catches the one a reader cannot do anything about: an empty bubble.
    /// The reason is the engine's own words inside Android's sentence
    /// (``AssistantStrings/modelStopped(_:)``), and "try again" has to be
    /// offered on it — a failed reply is exactly the one that should be.
    @MainActor
    func testAssistant_ReplyFailureSaysWhy() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the failure case")
        let app = assistantLaunch(model: "fake", script: "fail")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, "How do I keep mushrooms"), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.failureSentence).waitForExistence(timeout: 30),
            "a reply that failed drew no sentence saying why"
        )
        evidence(app, "assistant-failure")
        XCTAssertTrue(
            app.buttons[ChatStringsUnderTest.actionTryAgain].waitForExistence(timeout: 15),
            "a failed reply is not offered try again"
        )
    }

    /// A context overflow is the **divider**, not a failure.
    ///
    /// The scripted engine throws `exceededContextWindowSize` on the second
    /// turn; the model writes down where the session's view begins again and
    /// asks once more with the shorter history. Catches an overflow shown as
    /// an error (the honest answer is that the model forgot the beginning, not
    /// that it broke) and a `windowFrom` that was never saved, which would
    /// leave the divider off the screen that earned it.
    @MainActor
    func testAssistant_WindowDividerShowsWhenTheModelForgets() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the overflow case")
        let app = assistantLaunch(model: "fake", script: "overflow")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, "How do I keep mushrooms"), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.bagIt).waitForExistence(timeout: 30),
            "the first reply never arrived"
        )
        XCTAssertTrue(
            phoneAsk(app, "And what about parsley", newChat: false),
            "the second question would not send"
        )
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.afterOverflow).waitForExistence(timeout: 30),
            "the rebuilt session wrote no reply — an overflow became a dead end"
        )
        evidence(app, "assistant-window-divider")
        XCTAssertTrue(
            containing(app, ChatStringsUnderTest.windowNote).exists,
            "the model's view of the chat began again and nothing said so"
        )
        XCTAssertFalse(
            containing(app, AssistantUnderTest.couldNotStart).exists,
            "a context overflow was drawn as a failure"
        )
    }

    /// A reply lands in the conversation that asked it.
    ///
    /// [[read-after-await-wrong-key]] is the top defect class on this track,
    /// and the switch is made **inside the round trip**: the slow reply is
    /// still being written when the person walks back to the list and opens
    /// the other chat. Both halves are asserted — the words must not appear in
    /// the chat that did not ask, and must be there in the one that did when
    /// it is opened again.
    @MainActor
    func testAssistant_ReplyLandsInTheChatThatAskedIt() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the switching case")
        let app = assistantLaunch(model: "fake", script: "switcher")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        // The first chat, answered at once, so that exactly one reply is in
        // flight later on.
        XCTAssertTrue(phoneAsk(app, "Parsley keeping advice please"), "the first question would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.bagIt).waitForExistence(timeout: 30),
            "the first chat got no reply"
        )
        leavePhoneChat(app)

        // The second chat, answered slowly — and left inside the round trip.
        XCTAssertTrue(phoneAsk(app, "Mushroom keeping advice please"), "the second question would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBagHead).waitForExistence(timeout: 30),
            "the slow reply never began"
        )
        leavePhoneChat(app)
        let other = app.buttons[AssistantUnderTest.parsleyTitle].firstMatch
        XCTAssertTrue(other.waitForExistence(timeout: 20), "the first chat is not in the list")
        other.tap()
        // Long enough for the whole slow reply to have finished somewhere.
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.bagIt).waitForExistence(timeout: 20),
            "the first chat does not show its own reply"
        )
        usleep(12_000_000)
        evidence(app, "assistant-other-chat")
        XCTAssertFalse(
            containing(app, AssistantUnderTest.paperBagHead).exists,
            "a reply landed in the conversation that was open, not the one that asked"
        )
        leavePhoneChat(app)
        let asked = app.buttons[AssistantUnderTest.mushroomTitle].firstMatch
        XCTAssertTrue(asked.waitForExistence(timeout: 20), "the chat that asked is not in the list")
        asked.tap()
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 25),
            "the reply was lost: the chat that asked it does not have it"
        )
        evidence(app, "assistant-asking-chat")
    }

    /// The composer's Return key does not say `send`.
    ///
    /// A multi-line `TextField(axis: .vertical)` puts a newline in on Return
    /// and `onSubmit` never fires, so a key labelled `send` says one thing and
    /// does another — three screens shipped that, and a Simulator run watched
    /// one swallow a whole message. Catches a `submitLabel`/`onSubmit` added
    /// to this composer later.
    @MainActor
    func testAssistant_ComposerHasNoLyingSendKey() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the composer case")
        let app = assistantLaunch(model: "fake")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        guard let start = newChatControl(app) else { return XCTFail("no new chat control") }
        start.tap()
        let field = composerField(app)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no composer")
        field.tap()
        app.typeText("Mushrooms")
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 20), "the keyboard never came up")
        evidence(app, "assistant-composer")
        let sends = app.buttons.matching(NSPredicate(format: "label == %@", ChatStringsUnderTest.send))
        XCTAssertGreaterThan(sends.count, 0, "the composer has no send button at all")
        for index in 0..<sends.count {
            let candidate = sends.element(boundBy: index)
            guard candidate.exists else { continue }
            XCTAssertLessThanOrEqual(
                candidate.frame.maxY, keyboard.frame.minY,
                "a keyboard key is labelled send — the Return key of a vertical field cannot send"
            )
        }
    }

    // MARK: - the chats themselves

    /// The chats are kept on the phone, under their own ids, across a
    /// relaunch.
    ///
    /// Catches a store that does not persist and one that persists under the
    /// wrong id. The title is the question's first five words
    /// (``ChatTitles/firstWords(_:)``) and the question carries a word nothing
    /// else in this app or the stub house says, so a row with this title is
    /// this chat and no other.
    @MainActor
    func testAssistant_ChatsAreKeptOnThePhone() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the kept case")
        let app = assistantLaunch(model: "fake")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, AssistantUnderTest.arborio), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 30),
            "no reply, so there is nothing to have been kept"
        )
        leavePhoneChat(app)
        XCTAssertTrue(
            app.buttons[AssistantUnderTest.arborio].waitForExistence(timeout: 20),
            "the chat is not in the list under its first words"
        )
        evidence(app, "assistant-kept-before")
        app.terminate()

        // **No wipe this time**, which is the whole case: the same app, a new
        // process, reading the files the last one wrote.
        let again = assistantLaunch(model: "fake", wipe: false)
        XCTAssertTrue(reachPhonePlace(again), "the phone's place never drew on the second launch")
        XCTAssertTrue(
            again.buttons[AssistantUnderTest.arborio].waitForExistence(timeout: 25),
            "the chat did not survive the relaunch"
        )
        evidence(again, "assistant-kept-after")
        // And it is the chat, not just a row with a name: its words are in it.
        again.buttons[AssistantUnderTest.arborio].firstMatch.tap()
        XCTAssertTrue(
            startingWith(again, AssistantUnderTest.paperBag).waitForExistence(timeout: 25),
            "the row survived and the messages did not"
        )
    }

    /// The search reads **message bodies**, not only titles.
    ///
    /// Seeded so the title cannot match: the first message is the title's five
    /// words and the searched word is only in the second message, so a search
    /// over titles alone finds nothing. The match itself has to be drawn too —
    /// ``assistantMatchSnippet(_:_:)``'s forty characters either side — which
    /// is what tells a row that found the word from a row that merely exists.
    @MainActor
    func testAssistant_SearchFindsTheMessageNotTheTitle() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the search case")
        let app = assistantLaunch(model: "fake")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, AssistantUnderTest.arborio), "the first question would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 30),
            "no reply to the first question"
        )
        XCTAssertTrue(
            phoneAsk(app, AssistantUnderTest.saffron, newChat: false),
            "the second question would not send"
        )
        leavePhoneChat(app)
        let search = app.textFields[ChatStringsUnderTest.searchHint].firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 20), "no search field")
        search.tap()
        app.typeText("saffron")
        XCTAssertTrue(
            containing(app, "saffron").waitForExistence(timeout: 20),
            "a word in a message body found nothing — the search reads titles only"
        )
        evidence(app, "assistant-search-hit")
        // The row is the chat whose title does not contain the word, which is
        // what makes the hit a body match and not a title match.
        XCTAssertTrue(
            app.buttons[AssistantUnderTest.arborio].exists,
            "the hit is not the chat whose body held the word"
        )
        clearField(app)
        app.typeText("zzzznothing")
        XCTAssertTrue(
            startingWith(app, ChatStringsUnderTest.noChatsMatch).waitForExistence(timeout: 20),
            "a search that found nothing does not say so"
        )
        evidence(app, "assistant-search-empty")
    }

    /// The row menu: rename, archive, share, delete — and **no pin**.
    ///
    /// Catches the pin being offered on a chat the shared pin book cannot hold
    /// (``assistantPinKey(place:id:server:)`` says why it would come back
    /// under whichever house happened to be saved), and the delete
    /// confirmation's words — the one sentence that says this chat is not the
    /// household's, which is the opposite of what the house's delete says.
    @MainActor
    func testAssistant_RowMenuRenamesArchivesDeletes() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the row menu case")
        let app = assistantLaunch(model: "fake")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, AssistantUnderTest.arborio), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 30),
            "no reply, so no chat to work on"
        )
        leavePhoneChat(app)

        XCTAssertTrue(openChatRowMenu(app, AssistantUnderTest.arborio), "the row has no menu")
        evidence(app, "assistant-row-menu")
        for word in [
            ChatStringsUnderTest.actionRename, ChatStringsUnderTest.actionArchive,
            ChatStringsUnderTest.actionShare, ChatStringsUnderTest.actionDelete,
        ] {
            XCTAssertTrue(app.buttons[word].exists, "the row menu is missing \(word)")
        }
        XCTAssertFalse(
            app.buttons[ChatStringsUnderTest.actionPin].exists,
            "a pin on a phone chat: the shared pin book keys on the house's address"
        )

        // Rename.
        app.buttons[ChatStringsUnderTest.actionRename].tap()
        let field = app.textFields[ChatStringsUnderTest.renameField].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the rename sheet has no field")
        field.tap()
        clearField(app)
        app.typeText(AssistantUnderTest.renamed)
        app.buttons[ChatStringsUnderTest.renameSave].firstMatch.tap()
        XCTAssertTrue(
            app.buttons[AssistantUnderTest.renamed].waitForExistence(timeout: 20),
            "the rename did not reach the list"
        )

        // Archive, and back.
        XCTAssertTrue(openChatRowMenu(app, AssistantUnderTest.renamed), "the renamed row has no menu")
        app.buttons[ChatStringsUnderTest.actionArchive].tap()
        XCTAssertTrue(
            waitUntilGone(app.buttons[AssistantUnderTest.renamed]),
            "an archived chat stayed in the main list"
        )
        let archivedEntry = startingWith(app, ChatStringsUnderTest.archivedPrefix)
        XCTAssertTrue(archivedEntry.waitForExistence(timeout: 20), "no archived entry after archiving one")
        archivedEntry.tap()
        XCTAssertTrue(
            app.buttons[AssistantUnderTest.renamed].waitForExistence(timeout: 20),
            "the archived view does not hold the chat that was archived"
        )
        evidence(app, "assistant-archived")

        // Delete, from the archived view, with its own sentence.
        XCTAssertTrue(openChatRowMenu(app, AssistantUnderTest.renamed), "the archived row has no menu")
        app.buttons[ChatStringsUnderTest.actionDelete].tap()
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 20), "delete asked nothing")
        evidence(app, "assistant-delete-confirm")
        XCTAssertTrue(
            containing(app, AssistantUnderTest.deleteTail).exists,
            "the delete confirmation does not say the chat is on this phone and nowhere else"
        )
        XCTAssertFalse(
            containing(app, ChatStringsUnderTest.serverDeleteNote).exists,
            "a phone chat's delete claims it deletes for everyone in the house"
        )
        alert.buttons[ChatStringsUnderTest.actionDelete].tap()
        XCTAssertTrue(
            waitUntilGone(app.buttons[AssistantUnderTest.renamed]),
            "the chat was not deleted"
        )
    }

    /// `clear all` empties every conversation kept on this phone, after saying
    /// how many.
    ///
    /// Catches the control doing nothing, and the count in the sentence being
    /// wrong — it is the one number that tells somebody what they are about to
    /// lose. The confirm is taken from **inside the alert**: the entry link
    /// and the alert's button share Android's word.
    @MainActor
    func testAssistant_ClearAllEmptiesThePhone() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the clear-all case")
        let app = assistantLaunch(model: "fake")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")
        XCTAssertTrue(phoneAsk(app, AssistantUnderTest.arborio), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 30),
            "no reply, so no chat to clear"
        )
        leavePhoneChat(app)
        let clear = app.buttons[AssistantUnderTest.clearAll].firstMatch
        XCTAssertTrue(
            scrollUntilBuilt(app, clear),
            "the list offers no clear all with a chat on the phone"
        )
        clear.tap()
        let alert = app.alerts[AssistantUnderTest.clearAllTitle].firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 20), "clear all asked nothing")
        evidence(app, "assistant-clear-all")
        XCTAssertTrue(
            containing(app, AssistantUnderTest.clearAllCount).exists,
            "the clear-all sentence does not say how many are kept"
        )
        alert.buttons[AssistantUnderTest.clearAll].tap()
        XCTAssertTrue(
            waitUntilGone(app.buttons[AssistantUnderTest.arborio]),
            "clear all left the chats where they were"
        )
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.noChatsYet).waitForExistence(timeout: 20),
            "an emptied phone place does not say it is empty"
        )
    }

    // MARK: - Dynamic Type, and the audit

    /// The place's own text grows with the type size.
    ///
    /// **Measured, not read off a headline**: a named Dynamic Type audit
    /// finding has twice been a false positive on this track, so this compares
    /// each element's height at the ordinary size and at AX5. The two measured
    /// are the ones this item drew: the picker's chip (a scaled glyph beside
    /// text, and the row-to-column swap) and the two-place subtitle (a
    /// sentence that must wrap rather than be squeezed — `minimumScaleFactor`
    /// is rejected everywhere in this app).
    @MainActor
    func testAssistant_DynamicTypeHoldsAtAccessibilityXXXL() throws {
        func heights(_ mode: String, _ tag: String) -> [String: CGFloat] {
            control("reset-fixtures", "{}", port: 6601, what: "the fixtures before \(tag)")
            let app = assistantLaunch(model: "fake", mode: mode)
            XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew at \(tag)")
            var out: [String: CGFloat] = [:]
            let chip = assistantChip(app, AssistantUnderTest.chipPhone)
            if chip.exists { out["chip"] = chip.frame.height }
            let subtitle = startingWith(app, AssistantUnderTest.subtitleBoth)
            if subtitle.exists { out["subtitle"] = subtitle.frame.height }
            let hint = startingWith(app, AssistantUnderTest.emptyHintPhone)
            if hint.exists { out["hint"] = hint.frame.height }
            evidence(app, "assistant-type-\(tag)")
            app.terminate()
            return out
        }

        let ordinary = heights(StubHouseUITests.ordinaryTypeSize, "large")
        let big = heights("UICTContentSizeCategoryAccessibilityXXXL", "ax5")
        print("ASSISTANT TYPE large=\(ordinary) ax5=\(big)")
        for key in ["chip", "subtitle", "hint"] {
            guard let small = ordinary[key] else {
                XCTFail("\(key) was not on screen at the ordinary size — nothing was measured")
                continue
            }
            guard let grown = big[key] else {
                XCTFail("\(key) was not on screen at AX5: clipped, or never built")
                continue
            }
            XCTAssertGreaterThan(
                grown, small * 1.3,
                "\(key) is \(grown) at AX5 against \(small) at large — a fixed frame, not scaled text"
            )
        }
    }

    /// Apple's own audit, over the phone's list and over a conversation,
    /// classified by **signature**.
    ///
    /// Never a pinned total: a floor pinned by a number goes stale the moment
    /// anything else on the screen changes, and
    /// ``StubHouseUITests/floorAndRest(_:)`` is what the whole file classifies
    /// by. Anything the audit can attribute to an element is a defect — that
    /// is how the modal-dialog contrast bug was found.
    @MainActor
    func testAssistant_Audit() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the audit case")
        let app = assistantLaunch(model: "fake")
        XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew")

        var findings: [String: [String]] = [:]
        var floors: [String: Int] = [:]
        func sweep(_ name: String) throws {
            evidence(app, "assistant-audit-\(name)")
            // Inside the nested function, which is where the shared file's own
            // `sweep` has it: a `guard #available` in the enclosing method
            // does not reliably widen availability into a declaration.
            guard #available(iOS 17.0, *) else { return }
            let split = Self.floorAndRest(try auditIssues(app, "assistant-\(name)"))
            floors[name] = split.floor
            findings[name] = split.rest
            print("ASSISTANT AUDIT [\(name)] floor=\(split.floor) other=\(split.rest.count)")
            for line in split.rest { print("ASSISTANT AUDIT [\(name)]   \(line)") }
        }

        // The empty list, which is the one with the picker and both chips on
        // it — the contrast pair this item added.
        try sweep("empty-list")

        XCTAssertTrue(phoneAsk(app, AssistantUnderTest.arborio), "the composer would not send")
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.paperBag).waitForExistence(timeout: 30),
            "no reply to audit a conversation around"
        )
        try sweep("conversation")

        leavePhoneChat(app)
        XCTAssertTrue(
            app.buttons[AssistantUnderTest.arborio].waitForExistence(timeout: 20),
            "the list has no row to audit"
        )
        try sweep("list-with-a-row")

        for (name, rest) in findings {
            XCTAssertTrue(
                rest.isEmpty,
                "the audit attributed \(rest.count) finding(s) on \(name) to an element: \(rest)"
            )
        }
        print("ASSISTANT AUDIT floors=\(floors)")
    }
}

/// The words these cases match on, spelled out.
///
/// Spelled rather than imported, as ``ChatStringsUnderTest`` is and for its
/// reason: a query that silently followed a renamed constant would stop
/// testing what it claims to. Every one of these is also held to the Kotlin by
/// `AssistantStringsTests`, so a change here that is not a change there fails
/// the package suite first.
enum AssistantUnderTest {

    // the picker (`ChatPlace.kt:38-43`, `ChatHome.kt:752`)
    static let chipPhone = "on this phone"
    static let chipServerShared = "on home server · shared with the household"
    static let runs = "runs"

    // the list (`ChatHome.kt:172-181`)
    static let subtitleBoth = "text chat with Domovoi — on this phone, or on your home server"
    static let emptyHintPhone = "start one — it runs on this phone, with no internet"

    /// ``ChatStrings/subtitle`` — the sentence Chat has said since it shipped,
    /// which the **house's** header keeps. It is here rather than in
    /// ``ChatStringsUnderTest`` (which is in a file this item may not edit)
    /// and it is the discriminator of the no-model case: one place means this
    /// sentence and not ``subtitleBoth``.
    static let houseSubtitle = "text chat with Domovoi — runs on your own hardware"

    /// ``ChatStrings/noChatsYet``, for the same reason.
    static let noChatsYet = "no chats yet"

    // a turn (`AssistantChat.kt:595`, `:830-832`)
    static let stop = "stop"
    static let stopped = "stopped"
    static let clearAll = "clear all"
    static let clearAllTitle = "clear all conversations?"
    /// ``AssistantStrings/clearAllBody(_:)`` with the one chat these cases
    /// seed. The count is the point of the sentence, so it is in the match.
    static let clearAllCount = "kept on this phone (1)"

    /// ``AssistantStrings/deleteText(_:)``'s tail — the one sentence that says
    /// a phone chat is not the household's.
    static let deleteTail = "is deleted from this phone. This can't be undone."

    /// ``AssistantStrings/modelCouldNotStart(_:)``'s opening, which a context
    /// overflow must **not** produce.
    static let couldNotStart = "The model could not start:"

    /// ``AssistantStrings/modelStopped(_:)`` around
    /// `AssistantFakeScripts.failureWhy`.
    static let failureSentence = "The model stopped: the phone ran out of room for this one"

    // what the scripted engine answers (`AssistantFakeScripts`)
    static let paperBag = "Mushrooms keep best in a paper bag in the fridge door."
    /// The first words of ``paperBag``, which exist as soon as the reply
    /// begins: the slow script writes one word every nine hundred ms.
    static let paperBagHead = "Mushrooms keep"
    /// The last words, which a stop must keep from ever arriving.
    static let paperBagTail = "fridge door."
    static let bagIt = "Bag it loosely."
    static let afterOverflow = "Shorter answer: a paper bag, never a sealed box."

    // what the cases type
    /// Five words, so ``ChatTitles/firstWords(_:)`` makes them the title whole
    /// — no `…` — and `arborio` is a word nothing else in this app or the stub
    /// house says.
    static let arborio = "Mushroom risotto needs arborio rice"
    /// Sent **second**, so the searched word is in a message body and not in
    /// the title.
    static let saffron = "and also a pinch of saffron"
    static let renamed = "Fennel notes"
    static let parsleyTitle = "Parsley keeping advice please"
    static let mushroomTitle = "Mushroom keeping advice please"
}
