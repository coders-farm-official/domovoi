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
/// and wipes the **phone's own** chats with `-domovoi-phone-chats wipe` (or,
/// where a case needs a chat to measure rather than to drive, replaces them
/// with one seeded exchange — `seed`),
/// because those are files in the app's container and would otherwise be the
/// previous case's data (`AssistantStoreSeam` says so where it is written).
extension StubHouseUITests {

    // MARK: - the audit findings this item has measured

    /// One audit finding this item has **measured and found to be wrong**,
    /// pinned by its headline and the element it names.
    ///
    /// Both halves, because the headline alone would forgive the same
    /// complaint about any element on the screen, and the element alone would
    /// forgive a contrast defect on a label that happens to also be here.
    struct AssistantAuditExemption {
        let headline: String
        let label: String

        func matches(_ line: String) -> Bool {
            line.hasPrefix(headline) && line.contains("label='\(label)'")
        }
    }

    /// Apple's Dynamic Type audit, wrong about three of this item's labels.
    ///
    /// Train S003 reported "Dynamic Type font sizes are partially unsupported"
    /// against three leaves of the phone's empty list — `no chats yet`,
    /// `start one — …` and `new chat`. Each is a plain `Text` with a semantic
    /// font and `.fixedSize(horizontal: false, vertical: true)`, which is the
    /// shape that scales; the fourth element on the same screen with exactly
    /// that shape, the two-place subtitle, was measured in the same run at
    /// **33.67 → 208.67 points** between the ordinary size and AX5. The audit
    /// reported nothing against it.
    ///
    /// So this is the third time on this track that a *named* Dynamic Type
    /// finding was a false positive (``knownScalingFalsePositive`` is the
    /// second), and it is forgiven the same way: by signature, never by
    /// removing a modifier to quiet it, and never by a pinned total.
    ///
    /// **This list is not a promise, it is a pairing.** Every label in it is
    /// measured at both type sizes by
    /// `testAssistant_DynamicTypeHoldsAtAccessibilityXXXL`, which fails if one
    /// of them stops growing. Forgiving a finding here while that case holds
    /// is forgiving it on evidence; forgiving one without adding it there
    /// would be asserting less, which NIGHT-RULES §5 forbids.
    ///
    /// Nothing else is forgiven. The contrast finding S003 raised against the
    /// selected `on this phone` chip was **real** and was fixed in
    /// `AssistantPlacePickerView.swift`, which has the arithmetic.
    ///
    /// ### S005 named seven more, on two screens the audit had not reached
    ///
    /// S003 only ever swept the empty list. S005 swept a conversation and a
    /// list with a row in it as well, and the same headline came back against
    /// the row's title, its `on this phone` caption, its age, the privacy
    /// footer, `clear all`, and the two bubbles. Every one of them is a plain
    /// `Text` with a semantic font — `.subheadline`, `.caption`, `.caption2`,
    /// `.footnote`, `.body` — and there is **no fixed point size, no
    /// `minimumScaleFactor` and no fixed `frame(width:)` anywhere in
    /// `ios/Domovoi/Assistant/`; a grep of the four view files is the check.
    /// So they are forgiven on the same terms as the first three, and all ten
    /// labels are now measured at both type sizes by
    /// `testAssistant_DynamicTypeHoldsAtAccessibilityXXXL`.
    ///
    /// What S005 found that was **not** forgiven: the contrast finding against
    /// the composer's `send`. That one was real — a dimmed
    /// `.borderedProminent` on a blank composer — and the control is gone,
    /// which is also what Android draws there (`AssistantChat.kt:379-390`).
    static let assistantScalingFalsePositives: [AssistantAuditExemption] = {
        let headline = "Dynamic Type font sizes are partially unsupported"
        return [
            // the empty list (S003)
            AssistantUnderTest.noChatsYet,
            AssistantUnderTest.emptyHintPhone,
            ChatStringsUnderTest.newChat,
            // a list with a row in it (S005)
            AssistantUnderTest.arborio,
            AssistantUnderTest.chipPhone,
            AssistantUnderTest.justNow,
            AssistantUnderTest.privacyLineOne,
            AssistantUnderTest.clearAll,
            // a conversation (S005). `arborio` is the question's bubble as
            // well as the row's title and is already above.
            AssistantUnderTest.paperBag,
        ].map { AssistantAuditExemption(headline: headline, label: $0) }
    }()

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
        chats: String? = nil,
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
        // `chats` wins over `wipe` where a case asked for a seeded store:
        // `seed` empties it and writes one finished exchange, which is the
        // same `clearAll` plus one `save` (``AssistantStoreSeam``).
        if let chats {
            app.launchArguments += ["-domovoi-phone-chats", chats]
        } else if wipe {
            app.launchArguments += ["-domovoi-phone-chats", "wipe"]
        }
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
        // **Nothing in the send's place with a blank composer**, which is what
        // Android draws there (`AssistantChat.kt:379-390`: a blank draft gets
        // the mic, never a greyed send) and what train S005's audit asked for
        // — the dimmed `.borderedProminent` that used to stand here was the
        // one contrast finding that run attributed to a control of this item.
        // Before the field is tapped, so there is no keyboard whose own key
        // could be the match.
        XCTAssertEqual(
            app.buttons.matching(NSPredicate(format: "label ==[c] %@", ChatStringsUnderTest.send)).count,
            0,
            "a send button on a composer with nothing in it — it cannot send, so it is not drawn"
        )
        field.tap()
        app.typeText("Mushrooms")
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 20), "the keyboard never came up")
        evidence(app, "assistant-composer")
        // **Caseless**, and the keyboard asked directly. iOS labels the
        // Return key with a capitalised word, so a case-sensitive `label ==
        // "send"` matched nothing but the app's own button — which is always
        // above the keyboard, so the frame loop below could not fail for the
        // regression this case is named after.
        let saysSend = NSPredicate(format: "label ==[c] %@", ChatStringsUnderTest.send)
        XCTAssertEqual(
            keyboard.buttons.matching(saysSend).count, 0,
            "the Return key of a vertical field is labelled send — it makes a newline, it cannot send"
        )
        let sends = app.buttons.matching(saysSend)
        XCTAssertGreaterThan(sends.count, 0, "the composer has no send button at all")
        for index in 0..<sends.count {
            let candidate = sends.element(boundBy: index)
            guard candidate.exists else { continue }
            XCTAssertLessThanOrEqual(
                candidate.frame.maxY, keyboard.frame.minY,
                "a send is on the keyboard — the Return key of a vertical field cannot send"
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
        // **Tap at the far right, not in the middle.** `field.tap()` put the
        // caret where it landed — in the middle of the title the sheet comes
        // pre-filled with — and `clearField` is sixty backspaces, which delete
        // only what is BEFORE the caret. Train S003 therefore renamed the chat
        // to "Fennel notes arborio rice": the head was deleted, the new words
        // were inserted, and the tail after the caret survived. The app had
        // renamed it correctly; the case had asked for the wrong name.
        // NIGHT-RULES §5 says this is how a field is appended to, and it is
        // equally how one is emptied.
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        clearField(app)
        app.typeText(AssistantUnderTest.renamed)
        app.buttons[ChatStringsUnderTest.renameSave].firstMatch.tap()
        XCTAssertTrue(
            app.buttons[AssistantUnderTest.renamed].waitForExistence(timeout: 20),
            "the rename did not reach the list"
        )
        // And the old title is **gone**. The assert above only says the new
        // words arrived somewhere; this says they replaced something, which is
        // what a rename is. S003's half-rename happened to fail the exact
        // match too, but only because the surviving tail was at the end of the
        // label — a tail left anywhere else would have read as a pass.
        XCTAssertFalse(
            app.buttons[AssistantUnderTest.arborio].exists,
            "the renamed row still answers to the title it was renamed from"
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

    /// Scroll the phone's list back to its head, so that the next element is
    /// looked for from the top rather than from wherever the last one left it.
    ///
    /// **Train S005 paid for this.** At AX5 the place picker wraps to two
    /// rows and the list is left a 178-point window; one `swipeUp` in a window
    /// that small steps over a whole cell. ``StubHouseUITests/scrollUntilBuilt(_:_:flicks:)``
    /// only ever scrolls **down**, so once a measurement had run to the foot
    /// of the list the `new chat` cell above it was unbuilt and unreachable,
    /// and the case reported "new-chat was not on screen at AX5" about a
    /// button that was drawing perfectly well.
    ///
    /// The head is the two-place subtitle, which is in the list's own header
    /// cell — the one thing that is at the top of this list and nowhere else.
    @MainActor
    func assistantScrollListToTop(_ app: XCUIApplication, flicks: Int = 20) {
        let list = app.collectionViews.firstMatch
        guard list.exists else { return }
        let head = startingWith(app, AssistantUnderTest.subtitleBoth)
        for _ in 0..<flicks {
            if head.exists { return }
            list.swipeDown(velocity: .slow)
        }
    }

    /// The height of the **shortest** static text carrying exactly this label.
    ///
    /// Shortest, and every match rather than `firstMatch`, because two of the
    /// labels the audit named are drawn twice on the screen they were named
    /// on: `on this phone` is the picker's chip and the row's caption, and the
    /// chat's title is the row and the conversation's own heading. A
    /// measurement of whichever one the tree happened to hold first would
    /// forgive a finding against the other; the shortest growing means both
    /// grew.
    ///
    /// - Parameter inList: scroll the phone's list for it. False for the
    ///   conversation, whose transcript is a `ScrollView` and not the
    ///   `collectionViews.firstMatch` those helpers drive.
    @MainActor
    func assistantTextHeight(
        _ app: XCUIApplication,
        _ label: String,
        inList: Bool = true
    ) -> CGFloat? {
        let all = app.staticTexts.matching(NSPredicate(format: "label == %@", label))
        if inList {
            assistantScrollListToTop(app)
            guard scrollUntilBuilt(app, all.firstMatch) else { return nil }
        } else {
            guard all.firstMatch.waitForExistence(timeout: 20) else {
                print("=== NO TEXT '\(label)' — tree follows ===")
                print(app.debugDescription)
                return nil
            }
        }
        var shortest: CGFloat?
        for index in 0..<all.count {
            let one = all.element(boundBy: index)
            guard one.exists else { continue }
            if let best = shortest {
                shortest = min(best, one.frame.height)
            } else {
                shortest = one.frame.height
            }
        }
        return shortest
    }

    /// The place's own text grows with the type size.
    ///
    /// **Measured, not read off a headline**: a named Dynamic Type audit
    /// finding has now been a false positive four times on this track, so this
    /// compares each element at the ordinary size and at AX5. It measures
    /// everything this item drew that `testAssistant_Audit` is asked to
    /// forgive, and that pairing is the point: the audit's exemptions
    /// (``assistantScalingFalsePositives``) name these very labels, so if one
    /// of them ever stops scaling this case fails and the exemption cannot
    /// hide it.
    ///
    /// ### Two corrections train S003 paid for
    ///
    /// **The chip is measured by WIDTH.** Its height was compared and the case
    /// failed at 46.67 against 44.00 — but the chip carries `.frame(minHeight:
    /// 44)` for the hit-target rule, so at the ordinary size its height *is*
    /// the floor and not the text. The glyphs were growing the whole time, in
    /// the dimension that has no floor: 97.0 → 267.3 points wide. Height is
    /// kept for the elements that wrap, where height is the thing that grows.
    /// `clear all` is measured by width for the same reason — S005 reported it
    /// at exactly 44 points tall, which is a `HomeLink`'s hit target.
    ///
    /// **The hint is scrolled to.** At AX5 the header cell alone is 301 points
    /// tall, so the empty-state hint is below the fold and a lazy list leaves
    /// it *unbuilt*, not merely off screen (NIGHT-RULES §5). `if hint.exists`
    /// silently recorded nothing and the case reported only the chip, because
    /// `continueAfterFailure` is false and the chip failed first.
    ///
    /// ### Two more train S005 paid for
    ///
    /// **Every element is looked for from the top of the list**
    /// (``assistantScrollListToTop(_:flicks:)``), because the scroll that
    /// reaches the hint leaves `new chat` behind it and unbuilt.
    ///
    /// **A second pass, with a chat already on the phone.** S005's audit swept
    /// a list with a row in it and a conversation, and named seven labels that
    /// only exist there. They are reached by seeding the store at launch
    /// (`-domovoi-phone-chats seed`, ``AssistantStoreSeam``) and tapping the
    /// row — not by typing a question at AX5 over a 178-point list with the
    /// keyboard up, which is the kind of driving that loses a cell. Nothing
    /// here asserts behaviour against seeded data: this case measures frames.
    @MainActor
    func testAssistant_DynamicTypeHoldsAtAccessibilityXXXL() throws {

        /// The empty list: the picker, the header and the empty state.
        func onEmptyList(_ mode: String, _ tag: String) -> [String: CGFloat] {
            control("reset-fixtures", "{}", port: 6601, what: "the fixtures before \(tag)")
            let app = assistantLaunch(model: "fake", mode: mode)
            XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew at \(tag)")
            var out: [String: CGFloat] = [:]

            // The picker's chip: a scaled glyph beside text, and the
            // row-to-column swap. By width — see the doc comment.
            let chip = assistantChip(app, AssistantUnderTest.chipPhone)
            if chip.exists { out["chip"] = chip.frame.width }

            // The sentences, by height, each of them `.fixedSize(horizontal:
            // false, vertical: true)` so that it wraps rather than squeezes.
            // `staticTexts`, not `buttons`: the audit's findings are against
            // the leaf text (`type=48`), and `new chat`'s button shares its
            // label while carrying the same 44-point floor the chip does.
            for (key, label) in [
                ("subtitle", AssistantUnderTest.subtitleBoth),
                ("new-chat", ChatStringsUnderTest.newChat),
                ("no-chats-yet", AssistantUnderTest.noChatsYet),
                ("hint", AssistantUnderTest.emptyHintPhone),
            ] {
                if let height = assistantTextHeight(app, label) { out[key] = height }
            }

            evidence(app, "assistant-type-\(tag)")
            app.terminate()
            return out
        }

        /// A list with one seeded chat on it, and that chat opened.
        func onSeededChat(_ mode: String, _ tag: String) -> [String: CGFloat] {
            control("reset-fixtures", "{}", port: 6601, what: "the fixtures before \(tag)")
            let app = assistantLaunch(model: "fake", chats: "seed", mode: mode)
            XCTAssertTrue(reachPhonePlace(app), "the phone's place never drew at \(tag)")
            var out: [String: CGFloat] = [:]

            // The row, its caption and its age; then the footer, which is
            // below the row and needs the scroll of its own that
            // ``assistantTextHeight(_:_:inList:)`` makes for every key.
            for (key, label) in [
                ("row-title", AssistantUnderTest.arborio),
                ("row-place", AssistantUnderTest.chipPhone),
                ("row-age", AssistantUnderTest.justNow),
                ("privacy", AssistantUnderTest.privacyLineOne),
            ] {
                if let height = assistantTextHeight(app, label) { out[key] = height }
            }
            // `clear all` by width, for the chip's reason: it is a `HomeLink`
            // with a 44-point hit target and S005 measured it at exactly 44.
            let clear = app.buttons[AssistantUnderTest.clearAll].firstMatch
            if scrollUntilBuilt(app, clear) { out["clear-all"] = clear.frame.width }

            // The conversation. The row's own button carries the title as its
            // label (`AssistantPhoneRowView`), which is how every other case
            // here opens one.
            assistantScrollListToTop(app)
            let row = app.buttons[AssistantUnderTest.arborio].firstMatch
            if scrollUntilBuilt(app, row) {
                row.tap()
                for (key, label) in [
                    ("bubble-asked", AssistantUnderTest.arborio),
                    ("bubble-reply", AssistantUnderTest.paperBag),
                ] {
                    if let height = assistantTextHeight(app, label, inList: false) {
                        out[key] = height
                    }
                }
            } else {
                XCTFail("the seeded chat has no row to open at \(tag)")
            }

            evidence(app, "assistant-type-\(tag)")
            app.terminate()
            return out
        }

        var ordinary = onEmptyList(StubHouseUITests.ordinaryTypeSize, "large")
        var big = onEmptyList("UICTContentSizeCategoryAccessibilityXXXL", "ax5")
        ordinary.merge(onSeededChat(StubHouseUITests.ordinaryTypeSize, "large-row")) { a, _ in a }
        big.merge(onSeededChat("UICTContentSizeCategoryAccessibilityXXXL", "ax5-row")) { a, _ in a }
        print("ASSISTANT TYPE large=\(ordinary) ax5=\(big)")

        // **Every verdict, not the first one.** `continueAfterFailure` is
        // false for this class, and on S003 that turned four measurements into
        // one line: the chip failed and the hint's own absence at AX5 was
        // never reported. Twelve elements are twelve findings.
        let wasContinuing = continueAfterFailure
        continueAfterFailure = true
        defer { continueAfterFailure = wasContinuing }
        let keys = [
            "chip", "subtitle", "new-chat", "no-chats-yet", "hint",
            "row-title", "row-place", "row-age", "privacy", "clear-all",
            "bubble-asked", "bubble-reply",
        ]
        for key in keys {
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
                "\(key) is \(grown) at AX5 against \(small) at large — a fixed frame, not "
                    + "scaled text (chip and clear-all measured by width, every other key by height)"
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
    /// is how the modal-dialog contrast bug was found, and how S003 found this
    /// item's one real one (the selected chip's fill, fixed in
    /// `AssistantPlacePickerView.swift`).
    ///
    /// The one exception is ``assistantScalingFalsePositives``, three Dynamic
    /// Type findings measured at both type sizes and found to be wrong. Read
    /// that list before adding to it: every label in it must also be measured
    /// by `testAssistant_DynamicTypeHoldsAtAccessibilityXXXL`.
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
            let real = split.rest.filter { line in
                !Self.assistantScalingFalsePositives.contains { $0.matches(line) }
            }
            let forgiven = split.rest.count - real.count
            floors[name] = split.floor + forgiven
            findings[name] = real
            print(
                "ASSISTANT AUDIT [\(name)] floor=\(split.floor) measured-scaling=\(forgiven)"
                    + " other=\(real.count)"
            )
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

        // **All three screens' verdicts, in a fixed order.** `findings` is a
        // dictionary and `continueAfterFailure` is false, so S003 reported
        // whichever screen the iteration happened to reach first and said
        // nothing about the other two — a queue where there is a set.
        let wasContinuing = continueAfterFailure
        continueAfterFailure = true
        defer { continueAfterFailure = wasContinuing }
        for name in findings.keys.sorted() {
            let rest = findings[name] ?? []
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

    /// ``AssistantStrings/privacyLine(_:)`` with the one seeded chat, **whole**.
    ///
    /// Whole and not a prefix because ``StubHouseUITests/AssistantAuditExemption``
    /// matches on `label='<this>'` with its closing quote: a prefix would
    /// forgive nothing. Its first words are ``privacyHead``, which is what a
    /// query uses — a query string over 128 characters throws (NIGHT-RULES §5).
    static let privacyLineOne =
        "1 kept on this phone, in the app's private storage and outside backups. The model sees "
        + "the chat you are in and nothing else on the phone: not your other files, contacts or location."
    static let privacyHead = "1 kept on this phone, in the app's private storage"

    /// `relTime`'s word for a chat written a moment ago (`Format.swift:149`),
    /// which is every seeded one. It is also the row's accessibility
    /// **value**, so a row and the header's `new chat` button can be told
    /// apart.
    static let justNow = "just now"

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
