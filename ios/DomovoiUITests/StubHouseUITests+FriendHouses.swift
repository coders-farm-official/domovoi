import XCTest

/// Friend houses (Android 394473e2), against the stub house: hidden on a
/// house without it, in `tools` after Lists with the inbox's unread count
/// when it is on, the server's sentence when stopped, a hostile message
/// shown exactly as written with no link in it, marked read on open and
/// again when a new one arrives, a send with its counter and its waiting
/// state, a refusal in the server's words, a former friend with no composer,
/// friends and shared lists as words, "open in lists" landing in Lists, the
/// screen leaving when the server turns it off, and Apple's audit.
///
/// The house is the stub's `fixtures/friendhouses.py`
/// (`design-notes/ios/batch-2026-11-17/friend-houses/stubhouse.patch`),
/// **switched off by default**: every case here turns it on through
/// `/api/_test/friendhouses` and puts the house back with
/// `/api/_test/reset-fixtures` at the end, so no other case sees a Friend
/// houses tile. The words matched on are spelt out (this suite's convention)
/// from `FhWords` and the fixture's seed.
extension StubHouseUITests {

    enum FriendHousesUnderTest {
        static let tile = "Friend houses"
        static let toolsHeading = "tools"
        static let description = "Serverless messaging and friend system between households"
        static let smiths = "The Smiths"
        static let joneses = "The Joneses"
        static let patels = "The Patels"
        static let hostile = "Bring the **quillwort** <b>pie</b> http://example.com/x [link](http://example.com/y)\n"
            + "zephyrine on the second line"
        static let stopped = "The internet answer is \"never\", so Friend Houses has stopped."
        static let delivered = "Delivered"
        static let stillTrying = "Still trying: no route to the house"
        static let waiting = "Waiting to send"
        static let conversationAdmin = "Only an admin can save a copy of these messages or delete them, on the dashboard."
        static let former = "No longer a friend: messages can't be sent to this house."
        static let senderLevel = "New messages: the rooms say who it's from"
        static let inboxLevel = "New messages: only in the inbox"
        static let notVerified = "not verified"
        static let friendsAdmin =
            "Only an admin can add or remove friends, compare the codes, or change what a new message does, on the dashboard."
        static let invited = "The Patels shared a list: Camping trip. An admin can accept it in the dashboard."
        static let listsAdmin = "Only an admin can share a list, accept one or stop sharing, on the dashboard."
        static let refused = "That house isn't a friend any more."
        static let typed = "See you at six"
        static let typedCounter = "14 / 2000"
        static let smithsHouse = String(repeating: "ab", count: 32)

        /// **The elements the audit's Dynamic Type finding may name, by
        /// screen, every one measured growing** by
        /// `testFriendHouses_TheTextGrowsWithTheTypeSize` — the one list both
        /// that case and the audit case read, as ChatMore's
        /// `StubChatMore.auditNamedLeaves` is. Apple's checker reports these
        /// one element at a time (I066 the body, I067-I068 the state, I069
        /// the time), all text styles with nothing pinning their size; this
        /// is every text element each screen draws in the audited state, so
        /// the next run does not stop on the next one.
        static let inboxMeasured = [
            "fh.title", "fh.description",
            "fh.tab.messages", "fh.tab.friends", "fh.tab.shared",
            // The inbox row: the name, "no longer a friend" and the unread
            // and not-delivered pills are this one button's words.
            "fh.conversation",
        ]
        static let conversationMeasured = [
            "fh.conversation.name", "fh.conversation.note",
            "fh.message.body",
            // The time, and "sent … (their clock)" when the clocks disagree:
            // one text.
            "fh.message.when",
            // "Delivered", "Waiting to send", "Still trying: …", "Not
            // delivered: …": one text.
            "fh.message.state",
            "fh.counter", "fh.send",
        ]
    }

    /// The audit's lines with this one finding on exactly these measured
    /// ids taken out; everything else stays and fails its case.
    static func friendHousesUnmeasured(_ lines: [String], ids: [String]) -> (excused: [String], rest: [String]) {
        let excused = lines.filter { line in
            line.hasPrefix("Dynamic Type font sizes are partially unsupported")
                && ids.contains { line.contains("id='\($0)'") }
        }
        return (excused, lines.filter { !excused.contains($0) })
    }

    // MARK: - the house's switch

    /// Turn the house's Friend Houses to `mode` (a fresh seed), or off.
    @MainActor
    func friendHouses(_ mode: String?) {
        let body = mode.map { #"{"mode": "\#($0)", "reset": true}"# } ?? #"{"mode": null, "reset": true}"#
        control("friendhouses", body, port: 6601, what: "friend houses \(mode ?? "absent")")
    }

    /// What the house heard: mark-reads per house, and the texts sent.
    @MainActor
    func friendHousesHeard() -> (marks: [String: Int], sent: [String]) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:6601/api/_test/friendhouses")!)
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said what it heard")
        var marks: [String: Int] = [:]
        var sent: [String] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data, let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                marks = object["marks"] as? [String: Int] ?? [:]
                sent = object["sent"] as? [String] ?? []
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return (marks, sent)
    }

    /// Launch with Friend Houses `mode` and open it from Home's grid. True
    /// once the screen's description is up.
    @MainActor
    func openFriendHouses(
        _ mode: String = "on",
        typeSize: String = StubHouseUITests.ordinaryTypeSize
    ) -> (app: XCUIApplication, landed: Bool) {
        friendHouses(mode)
        let app = launch(mode: typeSize)
        let tile = app.buttons[FriendHousesUnderTest.tile]
        guard tile.waitForExistence(timeout: 40), navReach(app, tile) else {
            print("=== NO FRIEND HOUSES TILE ON HOME — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        tile.tap()
        let said = app.staticTexts["fh.description"]
        guard said.waitForExistence(timeout: 30) else {
            print("=== FRIEND HOUSES NEVER DREW — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    /// Open a conversation from the inbox by its name.
    @MainActor
    func friendHousesOpen(_ app: XCUIApplication, _ name: String) -> Bool {
        let row = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label CONTAINS %@", "fh.conversation", name))
            .firstMatch
        guard row.waitForExistence(timeout: 30) else {
            print("=== NO `\(name)` IN THE INBOX — tree follows ===")
            print(app.debugDescription)
            return false
        }
        row.tap()
        guard app.buttons["fh.back"].waitForExistence(timeout: 20) else {
            print("=== `\(name)` NEVER OPENED — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// The composer. A vertical `TextField` is drawn by a text view, so it is
    /// found by its identifier whatever element type it reports.
    @MainActor
    func friendHousesComposer(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: "fh.compose").firstMatch
    }

    @MainActor
    func friendHousesSays(_ app: XCUIApplication, _ label: String, timeout: Double = 20) -> Bool {
        let said = app.descendants(matching: .staticText).matching(NSPredicate(format: "label == %@", label)).firstMatch
        if said.waitForExistence(timeout: timeout) { return true }
        print("=== NOTHING SAYS “\(label)” — tree follows ===")
        print(app.debugDescription)
        return false
    }

    // MARK: - the cases

    /// A house with no Friend Houses (the fixture's default: the house's own
    /// 404) shows no tile anywhere — no misleading entry.
    @MainActor
    func testFriendHouses_HiddenOnAHouseWithoutIt() throws {
        friendHouses(nil)
        let app = launch()
        let lists = app.buttons["Lists"]
        guard lists.waitForExistence(timeout: 30) else {
            print(app.debugDescription)
            return XCTFail("Home's grid never drew")
        }
        evidence(app, "fh-01-hidden")
        XCTAssertFalse(app.buttons[FriendHousesUnderTest.tile].exists, "a Friend houses tile on a house without it")
        // Off is the same: a house that turned it off keeps its messages, and
        // still shows nothing.
        app.terminate()
        friendHouses("off")
        let again = launch()
        XCTAssertTrue(again.buttons["Lists"].waitForExistence(timeout: 30))
        XCTAssertFalse(again.buttons[FriendHousesUnderTest.tile].waitForExistence(timeout: 5), "shown while off")
        friendHouses(nil)
    }

    /// On: the tile is under `tools`, after Lists, and says the unread count;
    /// the screen has its title, its one description and the inbox.
    @MainActor
    func testFriendHouses_OpensFromToolsWithTheUnreadCount() throws {
        friendHouses("on")
        defer { friendHouses(nil) }
        let app = launch()
        let tile = app.buttons[FriendHousesUnderTest.tile]
        let lists = app.buttons["Lists"]
        guard tile.waitForExistence(timeout: 40), navReach(app, tile) else {
            print(app.debugDescription)
            return XCTFail("no Friend houses tile on Home with the house's Friend Houses on")
        }
        evidence(app, "fh-02-tile")
        XCTAssertEqual("1 unread", tile.value as? String, "the tile does not carry the inbox's unread count")
        let tools = app.staticTexts[FriendHousesUnderTest.toolsHeading]
        if tools.exists { XCTAssertLessThan(tools.frame.minY, tile.frame.minY, "not under tools") }
        if lists.exists {
            XCTAssertTrue(
                lists.frame.minY < tile.frame.minY
                    || (abs(lists.frame.minY - tile.frame.minY) < 1 && lists.frame.minX < tile.frame.minX),
                "Friend houses is not after Lists"
            )
        }
        tile.tap()
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.description))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.tile))
        let smiths = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label CONTAINS %@", "fh.conversation", FriendHousesUnderTest.smiths)).firstMatch
        let joneses = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label CONTAINS %@", "fh.conversation", FriendHousesUnderTest.joneses)).firstMatch
        XCTAssertTrue(smiths.waitForExistence(timeout: 20), "the Smiths are not in the inbox")
        XCTAssertTrue(joneses.exists, "the Joneses are not in the inbox")
        XCTAssertLessThan(smiths.frame.minY, joneses.frame.minY, "the inbox is not newest first")
        evidence(app, "fh-02-inbox")
    }

    /// Stopped: the server's own sentence under the header; the inbox still
    /// reads.
    @MainActor
    func testFriendHouses_StoppedSaysTheServersSentence() throws {
        let (app, landed) = openFriendHouses("stopped")
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        let line = app.descendants(matching: .any)["fh.statusLine"]
        XCTAssertTrue(line.waitForExistence(timeout: 20), "no status line while stopped")
        XCTAssertTrue(line.label.contains(FriendHousesUnderTest.stopped), "the line is not the server's: \(line.label)")
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.smiths), "the inbox did not read while stopped")
        evidence(app, "fh-03-stopped")
    }

    /// The message with Markdown, HTML and two URLs in it is drawn exactly as
    /// written — asterisks, tags and the line break — and nothing in it is a
    /// link. Opening the conversation marked it read.
    @MainActor
    func testFriendHouses_AHostileBodyIsShownAsWritten() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.smiths))
        let body = app.staticTexts.matching(NSPredicate(format: "label == %@", FriendHousesUnderTest.hostile)).firstMatch
        let shown = body.waitForExistence(timeout: 20)
        evidence(app, "fh-04-hostile")
        if !shown {
            print("=== THE HOSTILE BODY IS NOT SHOWN AS WRITTEN — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(shown, "the message was not drawn character for character")
        XCTAssertEqual(0, app.links.count, "something in a message became a link")
        XCTAssertFalse(app.staticTexts["quillwort"].exists, "the Markdown was parsed")
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.delivered))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.stillTrying))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.conversationAdmin))
        let marked = friendHousesHeard().marks[FriendHousesUnderTest.smithsHouse] ?? 0
        XCTAssertGreaterThanOrEqual(marked, 1, "opening the conversation did not mark it read")
    }

    /// A message that arrives while the conversation is open shows within the
    /// 10 s re-read and is marked read again.
    @MainActor
    func testFriendHouses_ANewArrivalWhileOpenIsMarkedRead() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.smiths))
        let before = friendHousesHeard().marks[FriendHousesUnderTest.smithsHouse] ?? 0
        XCTAssertGreaterThanOrEqual(before, 1)
        control("friendhouses", #"{"arrive": "Gravelwick says hello"}"#, port: 6601, what: "a new message")
        XCTAssertTrue(friendHousesSays(app, "Gravelwick says hello", timeout: 30), "the new message never showed")
        var after = before
        for _ in 0..<10 where after <= before {
            after = friendHousesHeard().marks[FriendHousesUnderTest.smithsHouse] ?? 0
            if after <= before { Thread.sleep(forTimeInterval: 2) }
        }
        evidence(app, "fh-05-arrival")
        XCTAssertGreaterThan(after, before, "a new unread message while open was not marked read")
    }

    /// Typing counts toward 2,000; Send queues it, the box empties, and the
    /// message shows "Waiting to send".
    @MainActor
    func testFriendHouses_SendCountsAndWaits() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.smiths))
        let field = friendHousesComposer(app)
        guard field.waitForExistence(timeout: 20) else {
            print(app.debugDescription)
            return XCTFail("no composer for a friend")
        }
        XCTAssertFalse(app.buttons["fh.send"].isEnabled, "Send is offered for an empty box")
        field.tap()
        field.typeText(FriendHousesUnderTest.typed)
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.typedCounter))
        let send = app.buttons["fh.send"]
        XCTAssertTrue(send.isEnabled)
        send.tap()
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.typed, timeout: 20), "the sent message never showed")
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.waiting), "no Waiting to send")
        XCTAssertTrue(friendHousesSays(app, "0 / 2000"), "the box did not empty")
        evidence(app, "fh-06-sent")
        XCTAssertEqual([FriendHousesUnderTest.typed], friendHousesHeard().sent)
    }

    /// A send the house refuses says the server's sentence, as written, and
    /// keeps the words in the box.
    @MainActor
    func testFriendHouses_ARefusedSendSaysTheServersWords() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.smiths))
        control("friendhouses", #"{"refuse_send": "\#(FriendHousesUnderTest.refused)"}"#, port: 6601, what: "a refused send")
        let field = friendHousesComposer(app)
        guard field.waitForExistence(timeout: 20) else { return XCTFail("no composer") }
        field.tap()
        field.typeText("one more")
        app.buttons["fh.send"].tap()
        let problem = app.descendants(matching: .any)["fh.sendProblem"]
        XCTAssertTrue(problem.waitForExistence(timeout: 20), "the refusal was not said")
        XCTAssertTrue(problem.label.contains(FriendHousesUnderTest.refused), "not the server's words: \(problem.label)")
        XCTAssertEqual("one more", field.value as? String, "the words were lost")
        evidence(app, "fh-07-refused")
    }

    /// A former friend's conversation reads, says why it can't be written to,
    /// and has no composer at all.
    @MainActor
    func testFriendHouses_AFormerFriendHasNoComposer() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.joneses))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.former))
        XCTAssertTrue(friendHousesSays(app, "Goodbye from the Joneses"))
        XCTAssertFalse(friendHousesComposer(app).exists, "a composer that cannot send")
        XCTAssertFalse(app.buttons["fh.send"].exists, "a Send that cannot send")
        evidence(app, "fh-08-former")
    }

    /// Friends: names, verified or not, the alert level in words, the admin's
    /// sentence; "message" on a friend never written to opens an empty
    /// conversation with a composer.
    @MainActor
    func testFriendHouses_FriendsAreWordsAndMessageOpensTheirConversation() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        let tab = app.buttons["fh.tab.friends"]
        XCTAssertTrue(tab.waitForExistence(timeout: 10))
        tab.tap()
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.senderLevel))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.inboxLevel))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.notVerified))
        let admin = app.staticTexts[FriendHousesUnderTest.friendsAdmin]
        XCTAssertTrue(admin.waitForExistence(timeout: 10) || navReach(app, admin), "no admin sentence")
        evidence(app, "fh-09-friends")
        let messages = app.buttons.matching(identifier: "fh.friend.message")
        XCTAssertEqual(2, messages.count, "one message button per friend, and no other control")
        messages.element(boundBy: 1).tap()
        XCTAssertTrue(app.buttons["fh.back"].waitForExistence(timeout: 20))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.patels))
        XCTAssertTrue(friendHousesComposer(app).waitForExistence(timeout: 10), "no composer for a friend")
    }

    /// Shared lists: an offer is a sentence and never a button; "open in
    /// lists" opens the shared list in Lists.
    @MainActor
    func testFriendHouses_AnOfferIsASentenceAndOpenInListsLands() throws {
        control("lists-reset", "{}", port: 6601, what: "the lists seed")
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        let tab = app.buttons["fh.tab.shared"]
        XCTAssertTrue(tab.waitForExistence(timeout: 10))
        tab.tap()
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.invited))
        XCTAssertTrue(friendHousesSays(app, FriendHousesUnderTest.listsAdmin))
        let open = app.buttons["fh.shared.open"]
        XCTAssertTrue(open.waitForExistence(timeout: 10))
        XCTAssertEqual(1, app.buttons.matching(identifier: "fh.shared.open").count, "the offer has a button")
        evidence(app, "fh-10-shared")
        open.tap()
        let title = app.staticTexts.matching(NSPredicate(format: "identifier == %@ AND label == %@", "lists.title", "Shopping"))
            .firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 30), "Lists did not open on the shared list")
        evidence(app, "fh-10-in-lists")
    }

    /// The house turns Friend Houses off while the screen is open: the next
    /// read (15 s on screen) closes it and the shell goes home; the tile goes.
    @MainActor
    func testFriendHouses_TurnedOffWhileOpenGoesHome() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        control("friendhouses", #"{"mode": "off"}"#, port: 6601, what: "friend houses off")
        let gone = app.staticTexts["fh.description"].waitForNonExistence(timeout: 45)
        evidence(app, "fh-11-off")
        XCTAssertTrue(gone, "the screen stayed after the house turned it off")
        XCTAssertTrue(app.buttons["Lists"].waitForExistence(timeout: 20), "the shell did not go home")
        XCTAssertFalse(app.buttons[FriendHousesUnderTest.tile].exists, "the tile stayed")
    }

    /// Apple's audit on the inbox and on a conversation.
    ///
    /// **One finding, on measured ids only.** "Dynamic Type font sizes are
    /// partially unsupported" is excused on exactly the elements in
    /// ``FriendHousesUnderTest/inboxMeasured`` and
    /// ``FriendHousesUnderTest/conversationMeasured``, and only because
    /// `testFriendHouses_TheTextGrowsWithTheTypeSize` measures every one of
    /// them growing at AX5 — each a text style with no point size, no
    /// `minimumScaleFactor`, no fixed frame, no `dynamicTypeSize` clamp and
    /// no horizontal `fixedSize`, as Chat's per-message lines are, which this
    /// audit names the same way and ChatMore excuses after the same
    /// measurement. Any other element, or any other finding on these, still
    /// fails.
    @MainActor
    func testFriendHouses_PassesTheAudit() throws {
        let (app, landed) = openFriendHouses()
        defer { friendHouses(nil) }
        XCTAssertTrue(landed, "Friend houses did not open")
        evidence(app, "fh-12-audit-inbox")
        let inbox = Self.floorAndRest(try auditIssues(app, "friend-houses-inbox"))
        let inboxSplit = Self.friendHousesUnmeasured(inbox.rest, ids: FriendHousesUnderTest.inboxMeasured)
        print(
            "FRIEND HOUSES AUDIT inbox floor=\(inbox.floor) rest=\(inbox.rest.count)"
                + " measured=\(inboxSplit.excused.count) ours=\(inboxSplit.rest.count)"
        )
        XCTAssertTrue(inboxSplit.rest.isEmpty, "Apple's audit on the inbox: " + inboxSplit.rest.joined(separator: " | "))
        XCTAssertTrue(friendHousesOpen(app, FriendHousesUnderTest.smiths))
        evidence(app, "fh-12-audit-conversation")
        let thread = Self.floorAndRest(try auditIssues(app, "friend-houses-conversation"))
        let threadSplit = Self.friendHousesUnmeasured(thread.rest, ids: FriendHousesUnderTest.conversationMeasured)
        print(
            "FRIEND HOUSES AUDIT conversation floor=\(thread.floor) rest=\(thread.rest.count)"
                + " measured=\(threadSplit.excused.count) ours=\(threadSplit.rest.count)"
        )
        XCTAssertTrue(
            threadSplit.rest.isEmpty,
            "Apple's audit on a conversation: " + threadSplit.rest.joined(separator: " | ")
        )
    }

    /// Every element the audit case excuses, measured at `large` and at AX5
    /// on the screen it is drawn on: each must grow by
    /// ``chatMoreGrew(_:_:)``'s bar (1.8x for a line of text, 1.25x or the
    /// area for a 44-point control). If one ever stops scaling, this fails
    /// and the audit case's excuse for it is no longer true. The state line
    /// is measured on its "Delivered" row, the one I068 named.
    @MainActor
    func testFriendHouses_TheTextGrowsWithTheTypeSize() throws {
        defer { friendHouses(nil) }
        var sizes: [String: [String: CGSize]] = [:]
        for (label, mode) in [
            ("large", StubHouseUITests.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let (app, landed) = openFriendHouses(typeSize: mode)
            guard landed else { return XCTFail("Friend houses did not open at \(label)") }
            var seen: [String: CGSize] = [:]
            for id in FriendHousesUnderTest.inboxMeasured {
                seen["inbox/" + id] = friendHousesSize(app, id)
            }
            guard friendHousesOpen(app, FriendHousesUnderTest.smiths) else {
                return XCTFail("the Smiths did not open at \(label)")
            }
            for id in FriendHousesUnderTest.conversationMeasured {
                let words = id == "fh.message.state" ? FriendHousesUnderTest.delivered : nil
                seen["conversation/" + id] = friendHousesSize(app, id, label: words)
            }
            for (key, size) in seen.sorted(by: { $0.key < $1.key }) {
                print("FRIEND HOUSES SIZE [\(label)] \(key) = \(size)")
            }
            evidence(app, "fh-13-sizes-\(label)")
            sizes[label] = seen
            app.terminate()
        }
        let small = sizes["large"] ?? [:]
        let big = sizes["AX5"] ?? [:]
        var stuck: [String] = []
        var missing: [String] = []
        let all = FriendHousesUnderTest.inboxMeasured.map { "inbox/" + $0 }
            + FriendHousesUnderTest.conversationMeasured.map { "conversation/" + $0 }
        for key in all {
            guard let was = small[key], let grown = big[key], was != .zero, grown != .zero else {
                missing.append(key)
                continue
            }
            if !chatMoreGrew(was, grown) { stuck.append("\(key) \(was) -> \(grown)") }
        }
        XCTAssertEqual([], missing, "an excused element was not found to measure")
        XCTAssertEqual([], stuck, "an excused element does not scale after all")
    }

    /// The first element with this id (and these words, when given), its
    /// size; `.zero` when absent. A conversation is a plain `VStack`, so a
    /// line scrolled out of view is still in the tree with its frame.
    @MainActor
    func friendHousesSize(_ app: XCUIApplication, _ id: String, label: String? = nil) -> CGSize {
        let predicate = label.map { NSPredicate(format: "identifier == %@ AND label == %@", id, $0) }
            ?? NSPredicate(format: "identifier == %@", id)
        let element = app.descendants(matching: .any).matching(predicate).firstMatch
        guard element.waitForExistence(timeout: 15) else {
            print("=== NO `\(id)` TO MEASURE — tree follows ===")
            print(app.debugDescription)
            return .zero
        }
        return element.frame.size
    }
}
