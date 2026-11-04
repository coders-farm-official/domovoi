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

    /// Whether the composer offers a send **of its own** — never the
    /// keyboard's key, which shares the word.
    ///
    /// The positive form is ``StubHouseUITests/composerSend(_:)``; this is the
    /// question D8 asks, which is whether there is one at all. It does not
    /// wait: a control that is supposed to be absent must not be waited for,
    /// or the case spends ten seconds proving nothing.
    @MainActor
    func chatComposerSends(_ app: XCUIApplication) -> Bool {
        let keyboard = app.keyboards.firstMatch
        let keyboardTop = keyboard.exists ? keyboard.frame.minY : .greatestFiniteMagnitude
        let all = app.buttons.matching(
            NSPredicate(format: "label == %@", ChatStringsUnderTest.send)
        )
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists else { continue }
            if candidate.frame.maxY <= keyboardTop { return true }
        }
        return false
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
    @MainActor
    func chatShowEarlier(_ app: XCUIApplication) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", ChatBatchUnderTest.showEarlier))
            .firstMatch
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

        XCTAssertFalse(
            chatComposerSends(app),
            "a brand-new chat with a blank box drew a send — the control that cannot do what "
                + "it says, on a blank box"
        )
        XCTAssertFalse(
            app.buttons[ChatBatchUnderTest.talk].exists,
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
            app.buttons[ChatBatchUnderTest.talk].waitForExistence(timeout: 10),
            "a thread with a blank box drew no mic — the mic-or-send rule has changed"
        )
        XCTAssertFalse(
            chatComposerSends(app),
            "the mic and a send are both on screen — Android's one rule here is that they "
                + "never are (ChatScreen.kt:1157, :1165)"
        )

        app.typeText("and the back door?")
        XCTAssertTrue(
            chatComposerSends(app),
            "a typed message was offered nothing to send it with"
        )
        XCTAssertFalse(
            app.buttons[ChatBatchUnderTest.talk].exists,
            "the mic stayed beside the send on a box with words in it"
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
        XCTAssertTrue(rest.isEmpty, "Apple's audit over the composer: \(rest)")
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
        XCTAssertFalse(
            startingWith(app, StubChatLong.wayBack).exists,
            "message 900 was built on open, 100 messages above the window"
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
        XCTAssertTrue(
            startingWith(app, StubChatLong.wayBack).waitForExistence(timeout: 25),
            "show earlier did not bring message 900 in"
        )
        let after = top.frame.minY
        print("CHAT show earlier: the window's old top was at \(before), now \(after)")
        evidence(app, "chat-show-earlier")
        XCTAssertLessThan(
            abs(after - before), 60,
            "show earlier moved the message that was at the top from \(before) to \(after): "
                + "the scroll is not pinned to it"
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
        let ending = startingWith(app, StubChat.replyEnding)
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
        // And the place is still said — at AX5 the header is inside the
        // scrolling half, so it is reached by scrolling back to the top.
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

    /// Message 900, one page above the window: absent on open, present after
    /// one "show earlier".
    static let wayBack = "Chandelier: nine hundred messages in"

    /// Message 1, which no number of taps in these cases reaches.
    static let firstEver = "Antiquarian: the very first thing"

    /// What a case asks the long thread, which no seeded message contains.
    static let asked = "Vermilion, and how does this one end"
}

/// The words this batch added or needs, which the shared
/// ``ChatStringsUnderTest`` does not have.
///
/// Spelt out here and not added there: `StubHouseUITests.swift` is the
/// transfers unit's file in this batch and this one may not edit it. Each of
/// these is the app string's exact value, and a query that silently followed a
/// renamed constant would stop testing what it claims to.
enum ChatBatchUnderTest {

    /// ``ChatStrings/showEarlier`` — the transcript's cap (D64).
    static let showEarlier = "show earlier"

    /// ``ChatMoreStrings/talk``, the mic's accessibility label. The ChatMore
    /// unit's own file calls this `ChatMoreStringsUnderTest.talk`; that file
    /// is another unit's too.
    static let talk = "talk"
}
