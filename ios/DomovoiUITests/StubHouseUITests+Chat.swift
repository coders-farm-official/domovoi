import XCTest

/// The answers batch's three jobs in the two conversation screens, against the
/// stub house: **D8** (the house composer's send), **D64** (both transcripts
/// capped, with "show earlier") and **D63** (the place inside each
/// conversation's header row).
///
/// Everything here drives the house's conversation except the two place cases,
/// which need a second place and therefore the phone's engine —
/// ``StubHouseUITests/assistantLaunch(model:script:wipe:chats:mode:port:)``.
///
/// The harness lessons these cases are written against, each of which cost a
/// train:
///
///  * **`firstMatch` takes a keyboard key that shares a label.** A key *is*
///    hittable, so `isHittable` chooses the wrong one just as happily;
///    ``StubHouseUITests/composerSend(_:)`` bounds by
///    `keyboards.firstMatch.frame.minY` and ``chatComposerSends(_:)`` below
///    asks the same question in the negative.
///  * **A query string over 128 characters throws**
///    (NSInternalInconsistencyException, train S001), so every long sentence
///    is matched with ``StubHouseUITests/startingWith(_:_:)``.
///  * **Reset first**: cases share one house for the whole run.
extension StubHouseUITests {

    // MARK: - the long thread the cap is about

    /// Seed ``StubChatLong``'s thread and land on the Chat list.
    ///
    /// `openChat` cannot do this: it resets the house and launches in one
    /// step, and the thread has to be in the house **before** the list is
    /// read. So this is `openChat`'s own three calls with the seeding in the
    /// middle — the same reset, the same two switches, the same launch.
    ///
    /// The thread is left for `/api/_test/chat-reset` to clear, which every
    /// Chat case calls at its top: the fixture puts it in the house's own
    /// dictionaries exactly so that nothing has to remember to clean up
    /// (`fixtures/chat.py`).
    @MainActor
    func openLongChat(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        messages: Int = 1000,
        fail: String? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        resetChatHouse()
        setChatAI(on: true)
        setChatFail(fail)
        control(
            "chat-long", "{\"messages\":\(messages)}", port: 6601,
            what: "a \(messages)-message thread"
        )
        let app = launch(mode: mode, push: "Chat", pushDelay: Self.pushDelay)
        // The seeded thread is the newest, so its row is at the head of the
        // list whatever the type size.
        let row = app.buttons[StubChatLong.title].firstMatch
        if row.waitForExistence(timeout: 40) { return (app, true) }
        print("=== THE LONG THREAD IS NOT IN THE LIST — tree follows ===")
        print(app.debugDescription)
        return (app, false)
    }

    /// Whether the composer offers a send **that can send now** — never the
    /// keyboard's key, which shares the word, and never the greyed-out one.
    ///
    /// The positive form is ``StubHouseUITests/composerSend(_:)``; this is the
    /// question D8 asks, which is whether there is a usable one. It does not
    /// wait: a control that is supposed to be absent must not be waited for,
    /// or the case spends ten seconds proving nothing.
    ///
    /// **`isEnabled` is part of the question since Kamron's answer 70.** The
    /// send is now drawn and disabled while a reply is being written
    /// (Android's shape), so existence alone no longer answers "can this
    /// message go". ``chatComposerSendButton(_:)`` is the one that asks only
    /// whether it is drawn, and the two together are what the streaming case
    /// reads.
    ///
    /// **So this is not the helper for an absence assertion.** A case that
    /// means "nothing is drawn in that spot" has to ask
    /// ``chatComposerSendButton(_:)`` for nil, or a disabled send drawn where
    /// none belongs passes it — the two blank-box cases below ask that way.
    @MainActor
    func chatComposerSends(_ app: XCUIApplication) -> Bool {
        guard let send = chatComposerSendButton(app) else { return false }
        return send.isEnabled
    }

    /// The composer's own send **however it is drawn**, enabled or greyed out,
    /// or nil where none is on screen above the keyboard.
    ///
    /// Split out of ``chatComposerSends(_:)`` for answer 70: the composer
    /// draws a disabled send while a reply is being written, and the case for
    /// that state has to tell "drawn and dimmed" from "not drawn at all" —
    /// which is the whole of what the answer changed. It does not wait, for
    /// the same reason the other one does not.
    @MainActor
    func chatComposerSendButton(_ app: XCUIApplication) -> XCUIElement? {
        let keyboard = app.keyboards.firstMatch
        let keyboardTop = keyboard.exists ? keyboard.frame.minY : .greatestFiniteMagnitude
        let all = app.buttons.matching(
            NSPredicate(format: "label == %@", ChatStringsUnderTest.send)
        )
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists else { continue }
            if candidate.frame.maxY <= keyboardTop { return candidate }
        }
        return nil
    }

    /// The frame of the **highest** element on screen whose label is exactly
    /// `words`, or nil where there is none.
    ///
    /// For the header's title, which cannot be asked for by `firstMatch`: a
    /// thread named after what was asked carries the same words in its first
    /// bubble, and `firstMatch` is hierarchy order rather than screen order —
    /// so under a chrome that moved into the transcript it would take the
    /// bubble. The topmost one is the header's, in both the shape this batch
    /// built and the band it replaced, which is what makes the comparison it
    /// is used for discriminate.
    @MainActor
    func chatTopmostFrame(_ app: XCUIApplication, _ words: String) -> CGRect? {
        let all = app.staticTexts.matching(NSPredicate(format: "label == %@", words))
        var best: CGRect?
        for index in 0..<all.count {
            let one = all.element(boundBy: index)
            guard one.exists else { continue }
            let frame = one.frame
            // A zero-height element is a view that is not laid out; its minY
            // is not a position on screen.
            guard frame.height > 0 else { continue }
            if let found = best, found.minY <= frame.minY { continue }
            best = frame
        }
        return best
    }

    /// The transcript's "show earlier" control.
    ///
    /// **By prefix, because its accessibility label is not its words.** The
    /// control draws ``ChatStrings/showEarlier`` and is *read out* as
    /// ``ChatStrings/showEarlierNote(hidden:)`` — "show earlier, 950 more
    /// above" — because "show earlier" on its own does not say whether it is
    /// one message above or nine hundred. `app.buttons["show earlier"]`
    /// therefore matches nothing at all, which is a case that would pass
    /// against an app that had lost the control.
    ///
    /// (This block documented ``chatTopmostFrame(_:_:)`` after round 2's edit
    /// moved that function in above it — review round 2, note 7.)
    @MainActor
    func chatShowEarlier(_ app: XCUIApplication) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", ChatStringsUnderTest.showEarlier))
            .firstMatch
    }

    /// The size of the attribution leaf under a reply — the model's name —
    /// or nil where the transcript never drew it.
    ///
    /// Scrolled for, because a leaf below the fold is **unbuilt** and not
    /// merely out of sight: at AX5 one bubble fills the transcript, so the
    /// line under it needs the foot. The transcript opens *at* its foot and
    /// the attribution is the last thing in it, so every flick is downwards
    /// (`swipeUp` moves the content up), and it is dragged by its
    /// **identifier** rather than `scrollViews.firstMatch` (train R002).
    ///
    /// A zero-height match is a view that is not laid out, which is not a
    /// size; it is skipped the same way ``chatTopmostFrame(_:_:)`` skips it.
    @MainActor
    func chatAttributionSize(
        _ app: XCUIApplication, _ words: String, flicks: Int = 6
    ) -> CGSize? {
        let leaf = app.staticTexts.matching(NSPredicate(format: "label == %@", words)).firstMatch
        let transcript = app.scrollViews[ChatStringsUnderTest.transcriptID].firstMatch
        for _ in 0...flicks {
            if leaf.exists, leaf.frame.height > 0 { return leaf.frame.size }
            guard transcript.exists else { break }
            transcript.swipeUp(velocity: .slow)
        }
        if leaf.exists, leaf.frame.height > 0 { return leaf.frame.size }
        print("=== NO ATTRIBUTION LEAF '\(words)' — tree follows ===")
        print(app.debugDescription)
        return nil
    }

    // MARK: - D8: the house composer's send

    /// **Nothing at all in the send's place with a blank box and no thread.**
    ///
    /// That state is the brand-new chat, and it is where this screen drew a
    /// dimmed `.borderedProminent` — grey glyphs on a grey fill, which is
    /// what Apple's audit failed the Assistant's identical control on in
    /// train S005, and which Android does not draw either (with a blank
    /// draft its spot holds the mic, `AssistantChat.kt:379-390`,
    /// `ChatScreen.kt:1157`). Here the mic needs a thread
    /// (`POST /threads/{id}/voice` wants an id, and a new chat has none), so
    /// with neither there is nothing this spot can honestly offer.
    ///
    /// **And the mic-or-send rule is unchanged**, which is the other half:
    /// with a thread open and nothing typed the mic is there, and one word
    /// typed replaces it with the send. A case that only asserted the
    /// absence would pass against a composer that had lost its send
    /// altogether.
    @MainActor
    func testChat_ABlankComposerOffersNoSendAndNoMicOnANewChat() throws {
        let (app, landed) = openChat()
        XCTAssertTrue(landed, "the thread list never answered")

        guard let start = newChatControl(app) else {
            return XCTFail("the header has no new chat control")
        }
        start.tap()
        let field = composerField(app)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "a new chat drew no composer")
        field.tap()
        evidence(app, "chat-new-blank-composer")

        // Existence, not `isEnabled`: the question here is whether the spot is
        // empty on a blank box. Since answer 70 a *disabled* send is a real
        // thing this composer draws, so `chatComposerSends` — which reads
        // `isEnabled` — would pass against one drawn on a blank box, which is
        // exactly the control this case exists to forbid.
        XCTAssertNil(
            chatComposerSendButton(app),
            "a brand-new chat with a blank box drew a send — the control that cannot do what "
                + "it says, on a blank box"
        )
        XCTAssertFalse(
            app.buttons[ChatMoreStringsUnderTest.talk].exists,
            "a mic on a chat with no thread yet: POST /threads/{id}/voice has no id to use"
        )

        // One word, and the send is there — and it works, which is what says
        // the absence above was the blank box and not a lost button.
        app.typeText("hello")
        XCTAssertTrue(
            chatComposerSends(app),
            "nothing was offered to send a message that had been typed"
        )
        guard let send = composerSend(app) else { return XCTFail("no send above the keyboard") }
        send.tap()
        XCTAssertTrue(
            startingWith(app, "hello").waitForExistence(timeout: 25),
            "the send did not reach the transcript"
        )
    }

    /// The same rule on a thread the house already has: blank box, **mic**;
    /// one word, **send**; and never both.
    @MainActor
    func testChat_AThreadWithABlankBoxOffersTheMicAndNotASend() throws {
        let (app, landed) = openChat()
        XCTAssertTrue(landed, "the thread list never answered")
        XCTAssertTrue(
            openThread(app, StubChat.garage, waitingFor: StubChat.garageSnippet),
            "the garage thread drew no transcript"
        )
        let field = composerField(app)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the thread drew no composer")
        field.tap()
        evidence(app, "chat-thread-blank-composer")

        XCTAssertTrue(
            app.buttons[ChatMoreStringsUnderTest.talk].waitForExistence(timeout: 10),
            "a thread with a blank box drew no mic — the mic-or-send rule has changed"
        )
        // Existence again, for the reason spelt out in the new-chat case: the
        // rule named below is that the two are never both *on screen*, and a
        // greyed-out send is on screen.
        XCTAssertNil(
            chatComposerSendButton(app),
            "the mic and a send are both on screen — Android's one rule here is that they "
                + "never are (ChatScreen.kt:1157, :1165)"
        )

        app.typeText("and the back door?")
        XCTAssertTrue(
            chatComposerSends(app),
            "a typed message was offered nothing to send it with"
        )
        XCTAssertFalse(
            app.buttons[ChatMoreStringsUnderTest.talk].exists,
            "the mic stayed beside the send on a box with words in it"
        )
    }

    /// **The send is drawn and greyed out while a reply is being written** —
    /// Kamron's answer 70 (2026-11-04), which took Android's shape over the
    /// empty spot this screen shipped.
    ///
    /// Android draws the same button `enabled = canSend` with a faint tint
    /// (`ChatScreen.kt:1166-1171`). So the thing to assert is no longer an
    /// absence: the button is there, in the same place, and it is **not
    /// enabled** — VoiceOver reads it and says *dimmed*, and it is not
    /// hittable. ``ChatModel/send(_:)`` still queues nothing, so a tap would
    /// be refused; that is why it is disabled rather than live.
    ///
    /// The field stays live while a reply streams, so a follow-up can be
    /// typed, and the words stay in the box.
    ///
    /// ### Why the window is real, and how this case says so if it is not
    ///
    /// `fail: "slow"` makes the stub's five-piece reply take 1.5s a piece
    /// (`stubhouse.py`, `chat_send`), so there are some seven seconds in which
    /// a reply is being written. ``StubChat/replyEnding`` is the reply's last
    /// words, so its absence is the proof that the stream was still open when
    /// the assertion was read — asserted on **both** sides of the read, rather
    /// than trusting the clock. A window that closed early fails this case
    /// with its own message instead of passing it for the wrong reason.
    ///
    /// And the send **comes back enabled**: it can only be drawn at all by
    /// ``ChatRules/sendOffered(typed:)``, which asks whether there is anything
    /// in the box, so a usable send on screen afterwards is also the proof
    /// that the typed words survived the stream.
    ///
    /// ### The greyed send's own contrast, measured off its pixels
    ///
    /// Unit mx-ink (2026-11-05). The disabled look used to be `.bordered` +
    /// ``Color/brandInk`` — a label on an 18% wash of itself, the pairing train
    /// I005 measured at **4.38:1** on Rooms' drop-in (`#975800` on `#ECE1D1`)
    /// — and it had been chosen *because* it was believed audited. It is now
    /// ``BrandFillButtonStyle``'s disabled state: ``Color/fgMuted`` on the
    /// composer's own surface with no fill, 7.43:1 on white by the tokens.
    ///
    /// The reading is ``inkContrast(_:in:)``, the hint cases' helper: the
    /// darkest and the lightest pixel in a box **inset into the middle of the
    /// button**, so the box is inside the old chip on every side — under the
    /// old look the lightest pixel there is the wash and the reading is about
    /// 4.4, under the floor; under Android's literal `fgFaint` (`#B9B7B4`) it
    /// is about 2.0. Both fail ``StubShell/contrastFloor``. Antialiasing makes
    /// the darkest pixel lighter than the ink, so a pass has room.
    ///
    /// **Not proven by a sabotage run.** The rule is that a case must fail on
    /// the defect it names; unit mx-ink cannot boot a Simulator, so the
    /// discrimination above is token arithmetic (4.38 and 2.00 under 4.5,
    /// 7.43 over it), not an observed red. The train is where it is seen.
    ///
    /// ### What this case does not cover
    ///
    /// **Whether VoiceOver's focus survives the re-enable.** Since mx-ink the
    /// send is one unconditional `ButtonStyle` reading `isEnabled`, so SwiftUI
    /// updates one button rather than swapping two (the old `ChatSendSkin`
    /// branched, and that was the limit recorded here). That is a fact about
    /// the view tree, not a measurement: this case reads the send again
    /// *after* the reply lands, which is a fresh query and passes either way,
    /// and the suite has no focus-before-and-after helper. Recorded, not
    /// measured.
    ///
    /// **Apple's audit over the greyed send.** `testChat_TheComposerAuditsWithItsSendDrawn`
    /// audits the composer with the send **live** on an idle thread; no case
    /// runs Apple's audit over the disabled state. The pixel reading above is
    /// the measurement of that state's label contrast; the audit's other
    /// headlines (hit area, Dynamic Type) are not taken on it.
    @MainActor
    func testChat_TheSendIsGreyedOutWhileAReplyIsBeingWritten() throws {
        let (app, landed) = openChat(fail: "slow")
        XCTAssertTrue(landed, "the thread list never answered")
        XCTAssertTrue(
            // `secondMessage` and not the list's snippet: `openThread` waits
            // for a label that STARTS WITH these words, and the snippet starts
            // with an ellipsis no message in the transcript does.
            openThread(app, StubChat.fridge, waitingFor: StubChat.secondMessage),
            "the fridge thread drew no transcript"
        )
        XCTAssertTrue(say(app, "slowly now"), "the composer would not send")
        let live = startingWith(app, StubChat.replyOpening)
        XCTAssertTrue(
            live.waitForExistence(timeout: 25),
            "the first words of the reply never arrived, so no reply was being written"
        )

        // Short on purpose: every keystroke is spent out of the stream's own
        // seven seconds.
        let field = composerField(app)
        field.tap()
        app.typeText("and one more")
        let finished = containing(app, StubChat.replyEnding)
        XCTAssertFalse(
            finished.exists,
            "the reply had already finished before the follow-up was typed: this case needs a "
                + "reply still being written, so give the stub a longer one"
        )
        evidence(app, "chat-send-greyed-mid-reply")

        let send = chatComposerSendButton(app)
        XCTAssertNotNil(
            send,
            "no send is drawn while a reply is being written — answer 70 asked for Android's "
                + "greyed-out button in the same place, not an empty spot"
        )
        XCTAssertFalse(
            send?.isEnabled ?? true,
            "the send is live while a reply is being written: ChatModel.send refuses it and "
                + "queues nothing, so a tappable button there cannot do what it says"
        )
        XCTAssertFalse(
            send?.isHittable ?? true,
            "the greyed-out send is still hittable, so a tap reaches a control that is refused"
        )
        // **The greyed send's label on its own surface** (mx-ink). Read
        // before the reply can land — the `finished` check below brackets it
        // with the rest of the composer reads. The box is the middle of the
        // button: a quarter of its height in from top and bottom and a fifth of
        // its width in from each side, which is inside the old `.bordered`
        // chip on every side, so that chip's wash would be the lightest pixel.
        if let send {
            let frame = send.frame
            let box = frame.insetBy(dx: frame.width * 0.2, dy: frame.height * 0.25)
            if let reading = inkContrast(app, in: box) {
                print(
                    "CHAT [chat-send-greyed-mid-reply] label \(reading.ink) on \(reading.paper) in "
                        + "\(box) of \(frame) — " + String(format: "%.2f", reading.ratio) + ":1"
                )
                XCTAssertGreaterThan(
                    reading.ratio,
                    StubShell.contrastFloor,
                    "the greyed-out send measures " + String(format: "%.2f", reading.ratio)
                        + ":1 (label \(reading.ink) on \(reading.paper)) inside \(box), under "
                        + "\(StubShell.contrastFloor):1. About 4.4 is `.bordered` + brandInk "
                        + "again (a label on a wash of itself, 4.38:1 in I005); about 2.0 is "
                        + "Android's fgFaint taken literally. BrandFillButtonStyle draws "
                        + "fgMuted on the bare surface, 7.43:1 by the tokens"
                )
            } else {
                print(app.debugDescription)
                XCTFail(
                    "could not read the greyed-out send's pixels in \(box) of \(frame) — no "
                        + "screenshot to measure, or the box left it (tree printed above)"
                )
            }
        }
        XCTAssertFalse(
            chatComposerSends(app),
            "chatComposerSends read the dimmed send as a usable one, so every other case in "
                + "this file that asks it is now asking a weaker question"
        )
        XCTAssertFalse(
            app.buttons[ChatMoreStringsUnderTest.talk].exists,
            "the mic took the spot while there were words in the box: the mic-or-send rule is "
                + "about an EMPTY box (ChatScreen.kt:1157)"
        )
        XCTAssertFalse(
            finished.exists,
            "the reply finished while the composer was being read, so the assertions above "
                + "were not read against a streaming reply"
        )

        // And when the reply lands, the send is usable again — with the words
        // still there, which is what being drawn at all says.
        XCTAssertTrue(
            finished.waitForExistence(timeout: 40),
            "the reply never finished, so nothing was waiting for the send to come back"
        )
        var back = false
        for _ in 0..<40 {
            if chatComposerSends(app) { back = true; break }
            usleep(250_000)
        }
        evidence(app, "chat-send-back-after-reply")
        XCTAssertTrue(
            back,
            "the send never came back after the reply landed: a typed message with nothing on "
                + "screen that can send it"
        )
    }

    /// Apple's audit over the composer **with its send drawn**, which is the
    /// state D8's first half is about.
    ///
    /// The label's colour is named rather than inherited
    /// (``Color/brandFg`` on ``Color/brand``, 8.49:1 light and 9.87:1 dark,
    /// against the 2.05:1 of `.borderedProminent`'s own white label on the
    /// amber), and a ratio is not a thing a UI case can read — but it is
    /// exactly what the audit measures. Train S005 reported the Assistant's
    /// then-identical control by name, so this is the finding to be absent.
    ///
    /// Classified by ``StubHouseUITests/floorAndRest(_:)`` like every audit in
    /// this file, never a pinned total.
    ///
    /// ### The two things on this screen that are not this screen's
    ///
    /// Train I004 failed this case with four findings, and **neither pair is
    /// a defect of the composer**. Both are classified here, by a
    /// measurement taken in this case rather than by a wider allowance.
    ///
    ///  1. **Three anonymous `Element has no description`** at
    ///     `(0, 516, 131, 45)`, `(131, …)` and `(262, …)` — the three cells
    ///     of **iOS's own QuickType bar**. The same train's trees name them:
    ///     `I004/ALL/testAssistant_ComposerHasNoLyingSendKey.log:158-169` has
    ///     `Other … identifier: 'SystemInputAssistantView'` →
    ///     `'CenterPageView'` → `label: 'Typing Predictions'` with exactly
    ///     those three frames, carrying the candidates `'“M”'`, `'My'`,
    ///     `'Me'`, and `Keyboard` itself begins below them at y=561. Here
    ///     "is the side gate shut" offered iOS no candidates, so the three
    ///     cells drew **empty** and Apple's audit asked them for a
    ///     description. The classification below is anchored three ways: the
    ///     region is read live off Apple's own identifier (no region, no
    ///     exclusion), only an empty-labelled finding inside it is excluded,
    ///     and the case **measures** that the composer's own field and send
    ///     are above that region — so nothing of this screen can be in it.
    ///  2. **`Dynamic Type font sizes are partially unsupported` naming
    ///     `label='qwen3:8b'`** — the attribution under the assistant's
    ///     reply (`ChatConversationView.attribution`, `.font(.caption2)`).
    ///     It scales: the same train measured that leaf
    ///     `52.33 x 13.33` at `large` and `173.67 x 48.0` at AX5, a 3.6x
    ///     growth (`I004/ALL/`
    ///     `testChatMore_TheConversationsTextGrowsWithTheTypeSize.log:373`
    ///     and `:1127`). ``testChat_TheModelNameUnderAReplyGrowsWithTheTypeSize()``
    ///     takes that measurement **in this file**, on this screen, so the
    ///     classification here is held by a case of its own rather than by
    ///     another unit's: if the attribution ever stops scaling, that case
    ///     says so and this one keeps classifying. It is the same
    ///     false-positive class as the audit's anonymous Dynamic Type
    ///     findings (``StubHouseUITests/knownAnonymousHeadlines``) — the
    ///     difference is only that Apple attributed this one — so it is
    ///     admitted for **that one leaf** and nothing else: a Dynamic Type
    ///     finding naming any other element still fails.
    @MainActor
    @available(iOS 17.0, *)
    func testChat_TheComposerAuditsWithItsSendDrawn() throws {
        let (app, landed) = openChat()
        XCTAssertTrue(landed, "the thread list never answered")
        XCTAssertTrue(
            openThread(app, StubChat.garage, waitingFor: StubChat.garageSnippet),
            "the garage thread drew no transcript"
        )
        let field = composerField(app)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the thread drew no composer")
        field.tap()
        app.typeText("is the side gate shut")
        XCTAssertTrue(chatComposerSends(app), "the send is not drawn, so nothing was audited")

        let found = try auditIssues(app, "chat-composer")
        let (floor, rest) = Self.floorAndRest(found)
        print("CHAT composer audit floor=\(floor) rest=\(rest)")

        // **iOS's half of the screen, read off the live hierarchy** by Apple's
        // own identifier for it rather than by a rectangle written down here.
        let assistant = app.otherElements[StubChatComposer.systemInputAssistant].firstMatch
        XCTAssertTrue(
            assistant.exists,
            "iOS's input-assistant view is not on screen, so this audit was not taken with the "
                + "keyboard up — which is the state the send is drawn in"
        )
        let keyboard = app.keyboards.firstMatch
        // Optional on purpose: no assistant view, no region, and
        // ``StubHouseUITests/isInside(_:_:)`` then excludes nothing at all —
        // which is the direction an unmeasured classifier must fail in.
        let region: CGRect? = assistant.exists
            ? (keyboard.exists ? assistant.frame.union(keyboard.frame) : assistant.frame)
            : nil
        print("CHAT composer audit iOS's own region \(region.map { "\($0)" } ?? "none")")

        // The measurement that makes the exclusion safe: this screen's own
        // composer is **above** that region, so a finding inside it cannot be
        // the composer's. Read here and not assumed, because the only reason
        // the exclusion is sound is that it is true.
        if let region {
            XCTAssertLessThanOrEqual(
                field.frame.maxY, region.minY,
                "the composer's field at \(field.frame) reaches into iOS's own region at "
                    + "\(region): the exclusion below would swallow findings about this screen"
            )
            if let send = composerSend(app), send.exists {
                XCTAssertLessThanOrEqual(
                    send.frame.maxY, region.minY,
                    "the composer's send at \(send.frame) reaches into iOS's own region at "
                        + "\(region): the exclusion below would swallow findings about this "
                        + "screen"
                )
            }
        }

        let keyboardsOwn = rest.filter { $0.contains("label=''") && Self.isInside(region, $0) }
        for line in keyboardsOwn { print("CHAT composer audit iOS's own cell :: \(line)") }

        // The one leaf a Dynamic Type finding may name here, held by
        // ``testChat_TheModelNameUnderAReplyGrowsWithTheTypeSize()``.
        XCTAssertTrue(
            app.staticTexts[StubChatComposer.model].firstMatch.exists,
            "the model's name is not under the reply, so a finding naming it would be about a "
                + "leaf this screen is not drawing"
        )
        let scaled = rest.filter {
            $0.hasPrefix("Dynamic Type font sizes are")
                && $0.contains("label='\(StubChatComposer.model)'")
        }
        for line in scaled { print("CHAT composer audit a leaf measured growing :: \(line)") }

        let mine = rest.filter { !keyboardsOwn.contains($0) && !scaled.contains($0) }
        XCTAssertEqual([], mine, "Apple's audit over the composer: \(mine)")
    }

    /// The model's name under a reply **grows with the type size**, which is
    /// what lets ``testChat_TheComposerAuditsWithItsSendDrawn()`` classify the
    /// one Dynamic Type finding Apple attributes to it.
    ///
    /// `ChatConversationView.attribution` draws it `.font(.caption2)`, a text
    /// style, so it should scale — and Apple's audit says it is "partially
    /// unsupported" anyway, on this screen in I004 and on the ChatMore
    /// threads in I001, I002 and I004. The audit is the headline; this is the
    /// measurement, and the measurement is what a classification may rest on.
    ///
    /// Two launches, because the type size is a launch argument
    /// (`launch(mode:)`). The garage thread is the subject: two messages, the
    /// assistant's carrying `model="qwen3:8b"` and no device
    /// (`stubhouse.py`'s `CHAT_MESSAGES[12]`), so the attribution is the
    /// model's name alone and the leaf's label is exactly that word.
    ///
    /// The floor is a growth factor and not a pinned size: I004 measured
    /// 13.33 → 48.0 points (3.6x) and a Simulator's rounding is in the third
    /// decimal, but 1.5x is far below anything "it scales" could mean and far
    /// above anything a stuck font could reach.
    @MainActor
    func testChat_TheModelNameUnderAReplyGrowsWithTheTypeSize() throws {
        var measured: [String: CGSize] = [:]
        for (label, mode) in [
            ("large", StubHouseUITests.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let (app, landed) = openChat(mode)
            XCTAssertTrue(landed, "the thread list never answered at \(label)")
            // At AX5 a row is **unbuilt** rather than off screen, which is why
            // this is not `openThread` — the same shape as
            // ``testChat_TheWayOutOfAConversationIsPinnedAtTheLargestTypeSize()``.
            let row = app.buttons[StubChat.garage].firstMatch
            XCTAssertTrue(
                scrollUntilBuilt(app, row), "the garage row was never built at \(label)"
            )
            row.tap()
            XCTAssertTrue(
                startingWith(app, StubChat.garageSnippet).waitForExistence(timeout: 30),
                "the garage thread drew no transcript at \(label)"
            )
            let size = chatAttributionSize(app, StubChatComposer.model)
            print("CHAT attribution [\(label)] \(StubChatComposer.model) size=\(size ?? .zero)")
            evidence(app, "chat-attribution-\(label)")
            XCTAssertNotNil(
                size,
                "the model's name is not drawn under the reply at \(label), so there is nothing "
                    + "to measure"
            )
            if let size { measured[label] = size }
            app.terminate()
        }

        guard let small = measured["large"], let big = measured["AX5"] else {
            return XCTFail("one of the two type sizes gave no measurement: \(measured)")
        }
        XCTAssertGreaterThan(
            big.height, small.height * 1.5,
            "the model's name under a reply is \(small.height) points at large and "
                + "\(big.height) at AX5, so it does not scale — which makes Apple's Dynamic "
                + "Type finding on it a real defect, and "
                + "testChat_TheComposerAuditsWithItsSendDrawn must stop classifying it"
        )
    }

    // MARK: - D64: the cap, and what it costs to open a long thread

    /// A thousand-message thread opens **promptly**, with the newest page
    /// drawn and "show earlier" above it.
    ///
    /// This is entry 64's own measurement — "the server sends a whole thread
    /// with no limit, so a 1,000-message thread builds 1,000 bubbles on open
    /// and nothing has ever timed it" — and it is the case that fails without
    /// the cap: a thousand eager bubbles is a thousand `ChatBubbleView`s laid
    /// out before the first frame.
    ///
    /// The number is printed whatever happens, because the number is the
    /// point. The ceiling is generous: this is a Simulator sharing a Mac with
    /// a build, and what the case is for is the difference between "a few
    /// seconds" and "a thread that cannot be opened", not a millisecond
    /// budget.
    @MainActor
    func testChat_AThousandMessageThreadOpensWindowed() throws {
        let (app, seeded) = openLongChat()
        XCTAssertTrue(seeded, "the thousand-message thread is not in the list")

        let row = app.buttons[StubChatLong.title].firstMatch
        let started = Date()
        row.tap()
        let last = startingWith(app, StubChatLong.lastPrefix)
        let drew = last.waitForExistence(timeout: 60)
        let took = Date().timeIntervalSince(started)
        print("CHAT a 1000-message thread opened in \(took)s")
        evidence(app, "chat-long-thread")
        XCTAssertTrue(drew, "a thousand-message thread never drew its newest message")
        XCTAssertLessThan(
            took, 25,
            "a thousand-message thread took \(took)s to open: the cap is not being applied, "
                + "or it is not the thing that costs the time"
        )

        // The window: the newest page is drawn, the first message is not, and
        // the control says so. Each marker contains a word none of the others
        // has (`fixtures/chat.py`), so none of these can pass by matching a
        // prefix two of them share.
        XCTAssertTrue(
            startingWith(app, StubChatLong.insideFirstPage).exists,
            "message 960 is not drawn, and it is inside the newest page of fifty"
        )
        XCTAssertFalse(
            startingWith(app, StubChatLong.firstEver).exists,
            "the first of a thousand messages was built on open — the transcript is not capped"
        )
        // The window's own edge, both sides of it. On open the oldest drawn
        // message is 951, so 901 is 50 rows above the first drawn one and 900
        // is 51 — neither is built, and neither is "100 messages above the
        // window", which is what this said before the arithmetic was checked.
        XCTAssertFalse(
            startingWith(app, StubChatLong.firstOfSecondPage).exists,
            "message 901 was built on open, 50 rows above the oldest drawn message"
        )
        XCTAssertFalse(
            startingWith(app, StubChatLong.stillAboveAfterOnePage).exists,
            "message 900 was built on open, 51 rows above the oldest drawn message"
        )
        XCTAssertTrue(
            chatShowEarlier(app).exists,
            "a thread with 950 messages above the window offers no show earlier"
        )
    }

    /// "Show earlier" adds a page **and leaves the message that was at the
    /// top where it was**.
    ///
    /// Growing the window puts fifty bubbles *above* what is on screen, and a
    /// scroll view whose content grows at the top keeps its offset — so
    /// without the pin the line somebody had just read would jump down the
    /// screen by the height of a page. Measured: the first drawn bubble's own
    /// `minY` before the tap and after it.
    @MainActor
    func testChat_ShowEarlierAddsAPageWithoutMovingWhatYouWereReading() throws {
        let (app, seeded) = openLongChat()
        XCTAssertTrue(seeded, "the thousand-message thread is not in the list")
        app.buttons[StubChatLong.title].firstMatch.tap()
        XCTAssertTrue(
            startingWith(app, StubChatLong.lastPrefix).waitForExistence(timeout: 60),
            "the long thread never drew"
        )

        // The oldest drawn message, which is the one the pin is about. The
        // transcript is scrolled to its foot by the open, so this is reached
        // by scrolling the transcript up — by its **identifier**, never
        // `scrollViews.firstMatch`, which train R002 watched resolve to the
        // shell's phone bar and walk the app off the screen under test.
        let transcript = app.scrollViews[ChatStringsUnderTest.transcriptID].firstMatch
        XCTAssertTrue(transcript.waitForExistence(timeout: 20), "the transcript is not named")
        let control = chatShowEarlier(app)
        XCTAssertTrue(control.exists, "show earlier is not offered on a thousand-message thread")
        // **Up to the head of the window, which is fifty bubbles away.** The
        // open leaves the transcript at its foot (the follow-scroll), and the
        // control is at the very top of the content: fifty short bubbles is
        // about 2,500 points over a viewport of about 600. A flick at the
        // default velocity is worth roughly a viewport, so this is five or six
        // of them with room to spare, and overshooting is free — a scroll
        // view clamps at its top. `.slow` is deliberately *not* used here:
        // that is for stepping onto a row without passing it, and nothing
        // here needs to be stepped onto.
        for _ in 0..<25 {
            if control.isHittable { break }
            transcript.swipeDown()
        }
        guard control.isHittable else {
            print("=== SHOW EARLIER CANNOT BE REACHED — tree follows ===")
            print(app.debugDescription)
            return XCTFail("show earlier is drawn at \(control.frame) and cannot be tapped")
        }

        let top = startingWith(app, StubChatLong.firstOfTheWindow)
        XCTAssertTrue(top.exists, "message 951 is not drawn, so the window is not fifty long")
        let before = top.frame.minY
        control.tap()
        // **One page exactly, and the edge is asserted on both sides.**
        // Message 901 is the oldest drawn message after one tap and message
        // 900 is the row above it; a window that grew by the wrong amount
        // fails one of these two whichever way it was wrong.
        XCTAssertTrue(
            startingWith(app, StubChatLong.firstOfSecondPage).waitForExistence(timeout: 25),
            "show earlier did not bring message 901 in — it is the oldest message of the "
                + "second page, which is what one tap of a fifty-message page adds"
        )
        XCTAssertFalse(
            startingWith(app, StubChatLong.stillAboveAfterOnePage).exists,
            "message 900 is drawn after one tap: the window grew by more than its page"
        )
        let after = top.frame.minY
        print("CHAT show earlier: the window's old top was at \(before), now \(after)")
        evidence(app, "chat-show-earlier")
        // **200 points, and the reason it is not tighter.** `before` is read
        // with the control scrolled into view, so the transcript is clamped at
        // its top: `HomeLink` carries a 44-point hit target
        // (`HomeCardView.swift:122`) and sits inside the transcript's 16-point
        // padding, which puts the pinned row some sixty points below where
        // `scrollTo(_, anchor: .top)` lands it afterwards. That is a working
        // pin, and a 60-point bar would have failed it. What the case has to
        // tell apart is a pin from no pin at all, and no pin is **a page**:
        // fifty bubbles inserted above this row is of the order of 2,500
        // points, which 200 is nowhere near.
        XCTAssertLessThan(
            abs(after - before), 200,
            "show earlier moved the message that was at the top from \(before) to \(after): "
                + "a page of fifty bubbles is about 2,500 points, so the scroll is not "
                + "pinned to it"
        )
        // And it is still the only control of its kind, now saying 900.
        XCTAssertTrue(
            chatShowEarlier(app).exists,
            "show earlier left after one page, with 900 messages still above the window"
        )
    }

    /// A reply streaming into a capped thread is still followed to the foot.
    ///
    /// The cap windows the DATA and not the container, which is the whole
    /// shape of D64's answer — so the eager `VStack`, the truthful content box
    /// and the follow-scroll keyed on ``ChatConversationView/tail`` all have
    /// to go on working over a thread that is only showing its last fifty.
    /// What is asserted is the thing MS001 measured in points: the reply's own
    /// words end **above the keyboard**, not 277 points behind it.
    @MainActor
    func testChat_AReplyIntoACappedThreadIsStillFollowedToTheFoot() throws {
        let (app, seeded) = openLongChat()
        XCTAssertTrue(seeded, "the thousand-message thread is not in the list")
        app.buttons[StubChatLong.title].firstMatch.tap()
        XCTAssertTrue(
            startingWith(app, StubChatLong.lastPrefix).waitForExistence(timeout: 60),
            "the long thread never drew"
        )

        XCTAssertTrue(say(app, StubChatLong.asked), "the composer would not send")
        // `CONTAINS`, not `BEGINSWITH`. ``StubChat/replyEnding`` is the reply's
        // LAST words ("once the oven is free.") and the bubble's label is the
        // whole 1,400-character paragraph, which begins with
        // ``StubChat/replyOpening`` — so `startingWith` matched nothing and
        // this case failed on train I003 with "no reply arrived" while the
        // reply was on screen and the follow-scroll had done its job (the
        // triage tree had the paragraph ending at y=355.3 with the keyboard's
        // top at 516). Every other reader of `replyEnding` already uses
        // `containing`; this one was the odd one out.
        let ending = containing(app, StubChat.replyEnding)
        XCTAssertTrue(
            ending.waitForExistence(timeout: 40),
            "no reply arrived in a thread of a thousand messages"
        )
        evidence(app, "chat-long-thread-reply")

        let keyboard = app.keyboards.firstMatch
        let keyboardTop = keyboard.exists ? keyboard.frame.minY : .greatestFiniteMagnitude
        print("CHAT capped-thread reply ends at \(ending.frame.maxY), keyboard top \(keyboardTop)")
        XCTAssertLessThanOrEqual(
            ending.frame.maxY, keyboardTop,
            "the end of the reply is \(ending.frame.maxY - keyboardTop) points behind the "
                + "keyboard: the follow-scroll stopped working over a capped thread"
        )
        // The person's own question is still in the hierarchy, which is R002's
        // other complaint and the reason this transcript is not lazy.
        XCTAssertTrue(
            startingWith(app, StubChatLong.asked).exists,
            "the question the reply answers is not in the hierarchy at all"
        )
    }

    // MARK: - D63: the place inside each conversation's header row

    /// The house's conversation says its place **in its own header row**, and
    /// the band above the screen is gone.
    ///
    /// Android's shape (`ChatHeader`, `ChatHome.kt:700`): a column holding the
    /// title with the place chip under it, inside the row that carries back
    /// and share. So the chip is above the first bubble and below nothing but
    /// the shell, and the picker's `runs` label — which belongs to the band
    /// over a **list** — is not on a conversation at all.
    ///
    /// ### What discriminates, and what review round 1 found did not
    ///
    /// "Above the first bubble", "less than 400 points down" and "no `runs`
    /// label" were **all three true before this change**. The old
    /// `AssistantPlaceSwitch` drew the band over a conversation at the
    /// ordinary sizes with `choices = [place]`, which is
    /// `AssistantPlacePickerView`'s one-choice branch: a chip in a band
    /// directly above the conversation, with no `runs` label anywhere (that
    /// label only exists in the `choices.count > 1` branch). A case that
    /// passes against the shape it was written to replace is a case that was
    /// never testing anything.
    ///
    /// So the two assertions that say **this** shape and not that one are
    /// geometric, and each fails on its own against the band:
    ///
    ///  1. **The chip is under the title**, which is the column
    ///     (`ChatHome.kt:721-724`, ``ChatConversationView/titleAndPlace``). In
    ///     a band above the conversation the chip is *above* the title, so
    ///     `chip.minY >= title.maxY` is false by the height of the row.
    ///  2. **The chip is right of the chevron**, because the column sits
    ///     beside it in the header row. The band was full width with a
    ///     12-point inset, which puts its chip at about x=24 against a chevron
    ///     ending at about x=56 — `chip.minX > back.maxX` is false there.
    ///
    /// The title is taken as the **topmost** element carrying the thread's
    /// name, because the thread's first message carries it too (thread 12 is
    /// named after what was asked); in the band's shape that is still the
    /// header's title, with the chip above it.
    @MainActor
    func testChat_TheHousesConversationSaysItsPlaceInItsHeader() throws {
        resetChatHouse()
        setChatAI(on: true)
        setChatFail(nil)
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the place case")
        let app = assistantLaunch(model: "fake", wipe: true)

        // The house's place is the one with the threads on it. Which place
        // opens first is remembered across launches
        // (``reachPhonePlace`` says why), so the house's chip is tapped to be
        // sure of where this case is.
        let houseChip = assistantChip(app, AssistantUnderTest.chipServerShared)
        XCTAssertTrue(houseChip.waitForExistence(timeout: 40), "the house's chip never drew")
        if houseChip.isHittable { houseChip.tap() }
        XCTAssertTrue(
            openThread(app, StubChat.garage, waitingFor: StubChat.garageSnippet),
            "the garage thread drew no transcript"
        )
        evidence(app, "chat-place-in-header")

        let chip = startingWith(app, AssistantUnderTest.chipServerShared)
        XCTAssertTrue(
            chip.waitForExistence(timeout: 15),
            "a conversation on a two-place phone does not say which place it is in"
        )
        let bubble = startingWith(app, StubChat.garageSnippet)
        XCTAssertTrue(bubble.exists, "the transcript is not on screen, so nothing was placed")
        print("CHAT header chip \(chip.frame) first bubble \(bubble.frame)")
        XCTAssertLessThan(
            chip.frame.maxY, bubble.frame.minY,
            "the place is at \(chip.frame) and the first bubble at \(bubble.frame): the chip is "
                + "not in the header row above the transcript"
        )
        XCTAssertLessThan(
            chip.frame.minY, 400,
            "the place chip is \(chip.frame.minY) points down the screen — that is not a header"
        )

        // **Android's shape, asserted.** The two geometric facts the band
        // cannot satisfy; this case's doc comment has the arithmetic.
        guard let titleFrame = chatTopmostFrame(app, StubChat.garage) else {
            print("=== NO TITLE ON SCREEN — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the conversation draws no title, so the chip has nothing to be under")
        }
        let back = app.buttons[ChatStringsUnderTest.backToChats].firstMatch
        XCTAssertTrue(back.exists, "the conversation has no way out of it")
        print("CHAT header title \(titleFrame) back \(back.frame)")
        XCTAssertGreaterThanOrEqual(
            chip.frame.minY, titleFrame.maxY,
            "the chip at \(chip.frame) is not under the title at \(titleFrame): that is a band "
                + "above the conversation, not Android's header column"
        )
        XCTAssertGreaterThan(
            chip.frame.minX, back.frame.maxX,
            "the chip at \(chip.frame.minX) starts left of the back chevron's right edge at "
                + "\(back.frame.maxX): it is not inside the header row's own column"
        )

        // The band's own label belongs to a list, never a conversation.
        XCTAssertFalse(
            startingWith(app, AssistantUnderTest.runs).exists,
            "the place picker's band is drawn over a conversation, where it offers to move a "
                + "chat that is already in a place"
        )
    }

    /// The same at the largest accessibility size, which is **the hole entry
    /// 63 names**.
    ///
    /// Train R001 measured what the band cost there: the chip is one word in a
    /// band 80 points tall at AX5, on a header row that is already 107, of the
    /// 401.7 the shell leaves Chat — and the transcript was left a
    /// `ScrollView` **6.7 points tall** holding 860 points of content. The
    /// band was therefore dropped at accessibility sizes, which is what entry
    /// 63 asked about and what Kamron answered: the place goes in the header,
    /// at every size.
    ///
    /// Moving it alone would have swapped 80 points for 80, so the chrome goes
    /// with the scroll at accessibility sizes
    /// (``AssistantPhoneConversationView/chromeScrolls``). This measures what
    /// that bought — the transcript's own height — and prints it, because the
    /// number is what the next round argues from.
    @MainActor
    func testChat_ThePlaceIsInTheHeaderAtTheLargestTypeSize() throws {
        resetChatHouse()
        setChatAI(on: true)
        setChatFail(nil)
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the AX5 place case")
        let app = assistantLaunch(
            model: "fake", wipe: true, mode: "UICTContentSizeCategoryAccessibilityXXXL"
        )
        let houseChip = assistantChip(app, AssistantUnderTest.chipServerShared)
        XCTAssertTrue(houseChip.waitForExistence(timeout: 40), "the house's chip never drew at AX5")
        if houseChip.isHittable { houseChip.tap() }

        let row = app.buttons[StubChat.garage].firstMatch
        XCTAssertTrue(
            scrollUntilBuilt(app, row),
            "the garage row was never built at AX5"
        )
        row.tap()
        XCTAssertTrue(
            startingWith(app, StubChat.garageSnippet).waitForExistence(timeout: 30),
            "the garage thread drew no transcript at AX5"
        )
        evidence(app, "chat-place-in-header-ax5")

        let transcript = app.scrollViews[ChatStringsUnderTest.transcriptID].firstMatch
        XCTAssertTrue(transcript.waitForExistence(timeout: 20), "the transcript is not named")
        print("CHAT AX5 transcript \(transcript.frame)")
        // R001's number is 6.7. The floor is one line of a bubble at AX5,
        // which is about 44 points: below that the conversation cannot show
        // any of itself, which is the defect entry 63 is about.
        XCTAssertGreaterThan(
            transcript.frame.height, 120,
            "the transcript is \(transcript.frame.height) points tall at AX5 (train R001 "
                + "measured 6.7 with the band drawn): the chrome is not giving the "
                + "conversation its height back"
        )
        // **The way out is pinned, and it is checked before anything is
        // scrolled.** The chrome that goes with the scroll is the name, the
        // place and the internet switch; the chevron is not in it
        // (``ChatConversationView/exitRow``), and the full case for that is
        // `testChat_TheWayOutOfAConversationIsPinnedAtTheLargestTypeSize`.
        let back = app.buttons[ChatStringsUnderTest.backToChats].firstMatch
        XCTAssertTrue(
            back.exists && back.isHittable,
            "the way out of the conversation is at \(back.frame) and cannot be tapped at AX5, "
                + "before anything has been scrolled"
        )

        // And the place is still said — at AX5 the name and the chip are
        // inside the scrolling half, so they are reached by scrolling back to
        // the top.
        let chip = startingWith(app, AssistantUnderTest.chipServerShared)
        if !chip.exists || chip.frame.height == 0 {
            for _ in 0..<8 where !chip.exists { transcript.swipeDown(velocity: .slow) }
        }
        XCTAssertTrue(
            chip.exists,
            "the conversation does not say its place at AX5 — which is entry 63's own complaint, "
                + "the other way round"
        )
    }

    /// **The only way out of a conversation is reachable at AX5**, and it is
    /// still reachable after the transcript has been read to its foot.
    ///
    /// This app has no system back and no swipe-back — answer 32's is not
    /// built — so the chevron in the header row is the one control that leaves
    /// a conversation. Round 1 of this branch put the whole header row inside
    /// the transcript at accessibility sizes, which put that chevron behind a
    /// scroll that opens at its foot: a reader at the newest message had
    /// nothing on screen that left the chat. ``ChatConversationView/exitRow``
    /// is the answer and this is the case for it.
    ///
    /// Three things, in the order a person would hit them: the chevron is on
    /// screen and hittable **without scrolling**, it is still hittable after
    /// the transcript has been scrolled to its foot, and tapping it actually
    /// leaves. The third is what makes the first two more than a frame check.
    @MainActor
    func testChat_TheWayOutOfAConversationIsPinnedAtTheLargestTypeSize() throws {
        let (app, landed) = openChat("UICTContentSizeCategoryAccessibilityXXXL")
        XCTAssertTrue(landed, "the thread list never answered at AX5")

        let row = app.buttons[StubChat.garage].firstMatch
        XCTAssertTrue(scrollUntilBuilt(app, row), "the garage row was never built at AX5")
        row.tap()
        XCTAssertTrue(
            startingWith(app, StubChat.garageSnippet).waitForExistence(timeout: 30),
            "the garage thread drew no transcript at AX5"
        )
        evidence(app, "chat-way-out-ax5")

        let back = app.buttons[ChatStringsUnderTest.backToChats].firstMatch
        XCTAssertTrue(back.waitForExistence(timeout: 20), "the conversation draws no way out")
        print("CHAT AX5 way out \(back.frame) hittable=\(back.isHittable)")
        XCTAssertTrue(
            back.isHittable,
            "the way out is at \(back.frame) and cannot be tapped on a freshly-opened "
                + "conversation at AX5"
        )

        // Read to the foot, which is where the follow-scroll leaves a reader
        // and where a chevron inside the transcript would have gone off
        // screen. The transcript is dragged by its **identifier**, never
        // `scrollViews.firstMatch` (train R002).
        let transcript = app.scrollViews[ChatStringsUnderTest.transcriptID].firstMatch
        if transcript.exists {
            for _ in 0..<4 { transcript.swipeUp(velocity: .slow) }
        }
        XCTAssertTrue(
            back.isHittable,
            "the way out is at \(back.frame) after the transcript was read to its foot: it "
                + "scrolled away with the chrome, and nothing else leaves a conversation"
        )

        back.tap()
        // The composer is the conversation's own, so its absence is the
        // conversation's: `new chat` belongs to the list it goes back to.
        XCTAssertTrue(
            app.buttons[ChatStringsUnderTest.newChat].firstMatch.waitForExistence(timeout: 25),
            "the chevron was tapped and the thread list did not come back"
        )
        XCTAssertFalse(
            composerField(app).exists,
            "the conversation's composer is still drawn after its way out was tapped"
        )
    }
}

/// The thousand-message thread `fixtures/chat.py` seeds, by the words that
/// appear on screen.
///
/// Spelt out here rather than imported, like every other string this file
/// matches on. These are the fixture's own.
enum StubChatLong {

    /// `fixtures/chat.py`'s `CHAT_THREADS[900]` — the newest row in the list.
    static let title = "Zeppelin: the thread that never ends"

    /// Message 1000, the thread's last. A **prefix**: a query string over 128
    /// characters throws, and matching the whole sentence buys nothing.
    static let lastPrefix = "Everlasting: the last thing said"

    /// Message 951 — the oldest message of the newest page of fifty, which is
    /// what "show earlier" pins the scroll to.
    static let firstOfTheWindow = "Message 951 of"

    /// Message 960, inside the newest page.
    static let insideFirstPage = "Dulcimer: nine hundred and sixty"

    /// Message **901** — the oldest message drawn after exactly one "show
    /// earlier", which is the window's new edge.
    ///
    /// The arithmetic, which review round 1 of this file had one row wrong:
    /// the page is fifty, so a 1,000-message thread opens on messages
    /// 951-1000 (indices 950-999) and one tap pins the window at index 900,
    /// which is message **901**. The branch's own package test says the same
    /// thing (`ChatTranscriptWindowTests.testShowEarlierOnTheHousesTranscript`
    /// asserts `drawn.first == "message 901"`), and the case that waited for
    /// message 900 after one tap would have timed out on the train.
    static let firstOfSecondPage = "Bellwether: nine hundred and one"

    /// Message **900** — the row directly above ``firstOfSecondPage``, which
    /// is still outside the window after one tap. Asserted absent beside 901's
    /// presence, which together pin the window's edge rather than merely
    /// saying something older arrived.
    static let stillAboveAfterOnePage = "Chandelier: nine hundred messages in"

    /// Message 1, which no number of taps in these cases reaches.
    static let firstEver = "Antiquarian: the very first thing"

    /// What a case asks the long thread, which no seeded message contains.
    static let asked = "Vermilion, and how does this one end"
}

/// What the composer's audit case has to name that is not a string of this
/// app's.
///
/// Both of these are read rather than asserted-about, and both earn their
/// place by being spelt out where the case that uses them is: a classification
/// keyed on a constant somebody else can rename is a classification that
/// stops meaning what it says.
enum StubChatComposer {

    /// The model every seeded reply carries (`stubhouse.py`'s `_msg`), drawn
    /// under the words by `ChatConversationView.attribution`.
    ///
    /// The same word as `StubChatMore.model`, deliberately not shared with
    /// it: that enum belongs to another unit's file, and this file's two
    /// readers of it (the composer audit's classification and
    /// ``StubHouseUITests/testChat_TheModelNameUnderAReplyGrowsWithTheTypeSize()``'s
    /// measurement) must move together or not at all.
    static let model = "qwen3:8b"

    /// iOS's own accessory bar above the keyboard, by **Apple's** identifier
    /// for it.
    ///
    /// Its three cells are the QuickType candidates. With no candidates to
    /// show they draw empty, and Apple's audit then asks each of them for a
    /// description — three findings on a view no part of this app owns
    /// (train I004; the trees that name it are cited in
    /// ``StubHouseUITests/testChat_TheComposerAuditsWithItsSendDrawn()``).
    static let systemInputAssistant = "SystemInputAssistantView"
}

// `ChatBatchUnderTest` stood here. It held `showEarlier` and `talk` because
// this unit could not add them to the files they belonged in:
// `ChatStringsUnderTest` is in `StubHouseUITests.swift` (the transfers unit's
// this batch) and `ChatMoreStringsUnderTest` is in
// `StubHouseUITests+ChatMore.swift`. Both now have them — the transfers unit
// added `ChatStringsUnderTest.showEarlier` in the same batch, and
// `ChatMoreStringsUnderTest.talk` was already there — so the enum is deleted
// on the integration, where all three files are in one tree. On either branch
// alone this deletion does not compile, which is why neither unit made it.
