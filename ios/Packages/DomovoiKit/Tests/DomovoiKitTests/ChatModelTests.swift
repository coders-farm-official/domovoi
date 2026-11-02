import Foundation
import XCTest
@testable import DomovoiKit

/// Chat's data layer against a server that is not a server: which list is on
/// screen and which emptiness, what a search asks for and keeps, where a pin
/// lives, what each of the three writes sends, when a thread is made, and what
/// a stream leaves behind when it fails.
///
/// `ChatHome.kt` and `ConversationPane` are composables, so Android's JVM
/// suite specifies the rules (`ChatHomeTest.kt`) and **nothing at all**
/// specifies the screen's own decisions: the four emptinesses, the composer
/// gate, the wrong-thread guard and the failed retry are tested here for the
/// first time on either phone.
@MainActor
final class ChatModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// The search debounce, as a gate a test opens — the shape
    /// `FilesModelTests` uses, and for the same reason: a real sleep makes a
    /// test that waits on wall-clock time, and a wait with no gate makes one
    /// that cannot assert "nothing has been asked for yet".
    private final class Waits: Sendable {
        let asked = Shared<[Int64]>([])
        let gaveUp = Shared<Bool>(false)
        private let opened = Shared<Bool>(false)

        func release() { opened.set(true) }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let opened = self.opened
            let gaveUp = self.gaveUp
            return { ms in
                asked.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    if opened.current { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                gaveUp.set(true)
                throw CancellationError()
            }
        }
    }

    /// A house with four threads: two live, one archived, one with no
    /// messages — and a models answer, a transcript and a stream.
    private final class House: Sendable {

        /// What `GET /threads` answers with, as rows of
        /// (id, title, updatedAt, messages, snippet, archived).
        let threads = Shared<[String]>([
            House.thread(id: 1, title: "lentil soup", at: "2026-11-02T18:00:00+00:00", messages: 6,
                        snippet: "and then simmer"),
            House.thread(id: 2, title: "bike chain", at: "2026-11-01T09:00:00+00:00", messages: 2,
                        snippet: "a quick link"),
            House.thread(id: 4, title: "nothing said", at: "2026-10-30T09:00:00+00:00", messages: 0),
        ])
        let archived = Shared<[String]>([
            House.thread(id: 3, title: "old plan", at: "2026-09-01T09:00:00+00:00", messages: 9,
                        snippet: "never mind", archived: true),
        ])

        /// A `?q=` answer: hits come back in whatever order the server likes,
        /// **with an archived one among them** (`chat.py:235`).
        let hits = Shared<[String]>([
            House.thread(id: 3, title: "old plan", at: "2026-09-01T09:00:00+00:00", messages: 9,
                        snippet: "never mind", archived: true, match: "…the old plan…"),
            House.thread(id: 1, title: "lentil soup", at: "2026-11-02T18:00:00+00:00", messages: 6,
                        snippet: "and then simmer"),
        ])
        let searchStatus = Shared<Int>(200)

        let models = Shared<String>(
            #"{"installed":["llama3.2:latest"],"default_model":"llama3.2","vision_installed":false}"#
        )

        /// `GET /threads/{id}/messages`.
        let transcript = Shared<String>("""
        {"messages":[
          {"id":10,"role":"user","content":"how long do lentils take?","created_at":"2026-11-02T17:59:00+00:00",
           "device_id":"ios-1","device_name":"Kamron's iPhone","source":"typed"},
          {"id":11,"role":"assistant","content":"about twenty minutes","created_at":"2026-11-02T18:00:00+00:00",
           "model":"llama3.2:latest","retryable":true,"stats":{"window_from_id":10}}
        ],"jobs":[]}
        """)

        /// What a write answers with.
        let writeStatus = Shared<Int>(200)
        let writeBody = Shared<String>(#"{"ok":true}"#)

        /// What `POST /threads` answers with when a new chat needs a thread.
        let newThread = Shared<String>(
            #"{"id":77,"title":"new chat","updated_at":"2026-11-02T19:00:00+00:00"}"#
        )
        let newThreadStatus = Shared<Int>(200)

        /// The stream a send or a retry gets.
        let stream = Shared<String>("""
        event: delta
        data: {"text":"about "}

        event: delta
        data: {"text":"twenty minutes"}

        event: done
        data: {"id":11,"role":"assistant","content":"about twenty minutes","model":"llama3.2:latest","retryable":true}

        """)
        let streamStatus = Shared<Int>(200)
        let streamRefusal = Shared<String?>(nil)

        /// Every request that reached the house, as "METHOD path?query".
        let seen = Shared<[String]>([])

        static func thread(
            id: Int64,
            title: String?,
            at: String,
            messages: Int,
            snippet: String? = nil,
            archived: Bool = false,
            match: String? = nil,
            usedInternet: Bool = false
        ) -> String {
            let titleJSON = title.map { "\"\($0)\"" } ?? "null"
            let snippetJSON = snippet.map { "\"\($0)\"" } ?? "null"
            let matchJSON = match.map { "\"\($0)\"" } ?? "null"
            return """
            {"id":\(id),"title":\(titleJSON),"updated_at":"\(at)","message_count":\(messages),
             "last_snippet":\(snippetJSON),"archived":\(archived),"match_snippet":\(matchJSON),
             "used_internet":\(usedInternet)}
            """
        }

        private static func list(_ rows: [String]) -> String {
            #"{"threads":[\#(rows.joined(separator: ","))]}"#
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
                let method = request.httpMethod ?? "GET"
                let path = parts.path
                if path != "/api/health" {
                    let line = parts.query.map { "\(method) \(path)?\($0)" } ?? "\(method) \(path)"
                    self.seen.mutate { $0.append(line) }
                }

                switch (method, path) {
                case ("GET", "/api/health"):
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")
                case ("GET", "/api/chat/models"):
                    return .json(self.models.current)
                case ("GET", "/api/chat/threads"):
                    if value("q") != nil {
                        guard self.searchStatus.current == 200 else {
                            return .json(#"{"detail":"the index is rebuilding"}"#, status: self.searchStatus.current)
                        }
                        return .json(Self.list(self.hits.current))
                    }
                    return .json(Self.list(
                        value("archived") == "true" ? self.archived.current : self.threads.current
                    ))
                case ("POST", "/api/chat/threads"):
                    guard self.newThreadStatus.current == 200 else {
                        return .json(#"{"detail":"nope"}"#, status: self.newThreadStatus.current)
                    }
                    return .json(self.newThread.current)
                default:
                    break
                }
                if path.hasSuffix("/messages"), method == "GET" {
                    return .json(self.transcript.current)
                }
                if path.hasSuffix("/messages") || path.hasSuffix("/retry") {
                    guard self.streamStatus.current == 200 else {
                        return .json(
                            #"{"detail":"AI features are turned off for this home"}"#,
                            status: self.streamStatus.current,
                            headers: self.streamRefusal.current.map { ["X-Domovoi-Refusal": $0] } ?? [:]
                        )
                    }
                    return .json(self.stream.current, headers: ["Content-Type": "text/event-stream"])
                }
                if path.hasPrefix("/api/chat/threads/") {
                    guard self.writeStatus.current == 200 else {
                        return .json(
                            #"{"detail":"AI features are turned off for this home"}"#,
                            status: self.writeStatus.current
                        )
                    }
                    return .json(self.writeBody.current)
                }
                return .json("{}", status: 404)
            }
        }
    }

    private func rig(
        house: House = House(),
        server: String? = nil,
        pins: ChatPinBook = ChatPinBook(place: SavedStateBox()),
        waits: Waits = Waits()
    ) -> (ChatModel, House, Waits) {
        house.install()
        let address = server ?? self.server
        let store = InMemoryShellStore(serverURL: address, token: "household-abc")
        store.trustServer(address, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = ChatModel(
            in: container,
            deviceID: "ios-1",
            pins: pins,
            wait: waits.sleeper,
            nowMs: { 1_762_110_000_000 },
            nowISO: { "2026-11-02T19:00:00+00:00" }
        )
        return (model, house, waits)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Every task that could have started gets its chance to run — for
    /// asserting a **negative**.
    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    /// The same, with the whole ``settleYieldLimit`` behind it: for a
    /// **released round trip** whose answer has to have landed before a
    /// negative assertion means anything.
    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    private func rows(_ content: ChatListContent) -> [ChatRow] {
        if case .rows(let rows) = content { return rows }
        return []
    }

    // MARK: - the list

    func testTheListIsOneReadPinnedFirstThenNewest() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty }, "the thread list")
        XCTAssertEqual(["1", "2", "4"], model.rows.map(\.id), "newest first")
        XCTAssertEqual("and then simmer", model.rows.first?.subtitle)
        XCTAssertEqual(ChatStrings.noMessagesYet, model.rows.last?.subtitle, "a thread with no messages")
        // The archived view is a second read — and it is made on `start()`,
        // not when it is asked for. `testTheArchivedCountIsKnownWhenTheListFirstLands`
        // is why; this assertion used to be `XCTAssertTrue(archivedRows.isEmpty)`
        // with a comment arguing the laziness was fine.
        await settleUntil({ !model.archivedRows.isEmpty }, "the archived list")
        model.showArchived(true)
        XCTAssertEqual(["3"], model.archivedRows.map(\.id))
        XCTAssertEqual(1, model.archivedCount)
    }

    /// `GET /threads` carries **no** `limit`, `offset` or cursor: the route has
    /// no paging (`chat.py:219`), and client paging over a route with none
    /// would hide the server's gap rather than close it.
    func testTheListAsksForNoPage() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        XCTAssertTrue(house.seen.current.contains("GET /api/chat/threads"))
        XCTAssertFalse(
            house.seen.current.contains { $0.contains("limit=") || $0.contains("offset=") },
            "the route takes neither: \(house.seen.current)"
        )
    }

    /// **The four emptinesses**, which are four different sentences and were
    /// four `if`s in a composable.
    func testWhichNothingTheListIsShowing() async {
        let house = House()
        house.threads.set([])
        house.archived.set([])
        house.hits.set([])
        let (model, _, waits) = rig(house: house)
        model.start()
        await settleUntil({ model.threads.data != nil }, "the first read")
        XCTAssertEqual(.empty(ChatStrings.noChatsYet, hint: ChatStrings.emptyHint), model.content)

        model.showArchived(true)
        await settleUntil({ model.archivedThreads.data != nil }, "the archived read")
        XCTAssertEqual(.empty(ChatStrings.noArchivedChats, hint: nil), model.content,
                       "and no hint: there is nothing to start from here")

        model.showArchived(false)
        model.typed("soup")
        // **Nothing at all while the search is in flight**, which is not the
        // same answer as "no chats match" and would be the wrong one for the
        // few hundred milliseconds before the real one arrives.
        XCTAssertEqual(.waiting, model.content)
        waits.release()
        await settleUntil({ model.hits != nil }, "the search")
        XCTAssertEqual(.empty(ChatStrings.noChatsMatch, hint: nil), model.content)

        house.searchStatus.set(503)
        model.typed("soup again")
        waits.release()
        await settleUntil({ model.searchFailed }, "the failed search")
        XCTAssertEqual(.empty(ChatStrings.searchFailed, hint: nil), model.content,
                       "a search that could not run did not find nothing")
    }

    // MARK: - searching

    /// **The trap the coordinator named**: `?q=` searches the archived threads
    /// too, so the hits are sorted by date and **not** run through
    /// `orderChats`, which would drop every archived one.
    func testASearchKeepsItsArchivedHits() async {
        let (model, house, waits) = rig()
        model.start()
        model.typed("plan")
        XCTAssertEqual("plan", model.filter)
        XCTAssertTrue(model.searching, "the list is a search from the first keystroke")
        XCTAssertNil(model.hits, "and nothing has been asked yet")
        waits.release()
        await settleUntil({ model.hits != nil }, "the search")
        XCTAssertEqual(["1", "3"], rows(model.content).map(\.id), "newest first, archived kept")
        XCTAssertTrue(rows(model.content).contains { $0.archived })
        XCTAssertEqual("…the old plan…", rows(model.content).last?.subtitle, "the match, not the snippet")
        XCTAssertEqual([ChatRules.searchDebounceMs], waits.asked.current)
        XCTAssertTrue(
            house.seen.current.contains("GET /api/chat/threads?q=plan"),
            "\(house.seen.current)"
        )
        XCTAssertFalse(
            house.seen.current.contains { $0.contains("q=") && $0.contains("archived=") },
            "asking for one view would be asking the server to drop the other half"
        )
    }

    /// A blank field is not a search: the hits go, and nothing is asked for.
    func testClearingTheFieldAsksForNothing() async {
        let (model, house, waits) = rig()
        model.start()
        model.typed("plan")
        waits.release()
        await settleUntil({ model.hits != nil })
        let asked = house.seen.current.count
        model.typed("   ")
        XCTAssertNil(model.hits)
        XCTAssertFalse(model.searching)
        await nothingHappens()
        XCTAssertEqual(asked, house.seen.current.count, "a blank field asked the server nothing")
    }

    /// The debounce is one request per settled word, not one per keystroke.
    func testTheSearchWaitsForTheTypingToStop() async {
        let (model, house, waits) = rig()
        model.start()
        await settleUntil({ model.threads.data != nil })
        model.typed("p")
        model.typed("pl")
        model.typed("pla")
        model.typed("plan")
        // **Settled on the waits, not on a yield count.** The gate's spin runs
        // off the main actor, so a long `nothingHappens()` lets it reach its
        // own bound, give up, and fire the search it was holding — the trap
        // `FilesModelTests.Waits.gaveUp` was added for, which cost this test a
        // round. Four keystrokes have asked for four waits; the only way a
        // search could have gone out is through one of them returning.
        await settleUntil({ waits.asked.current.count == 4 }, "the four debounces")
        XCTAssertTrue(
            house.seen.current.filter { $0.contains("q=") }.isEmpty,
            "nothing while the gate is shut: \(house.seen.current)"
        )
        waits.release()
        await settleUntil({ model.hits != nil })
        XCTAssertFalse(waits.gaveUp.current, "a wait ran out, so the count below proves nothing")
        let searches = house.seen.current.filter { $0.contains("q=") }
        XCTAssertEqual(1, searches.count, "one search for four keystrokes: \(searches)")
        XCTAssertEqual("GET /api/chat/threads?q=plan", searches.first)
    }

    // MARK: - pins, which are this phone's and this house's

    func testAPinIsKeptOnThisPhoneForThisHouseAndSurvivesARestart() async {
        let place = SavedStateBox()
        let (model, _, _) = rig(pins: ChatPinBook(place: place))
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let bike = model.rows.first(where: { $0.id == "2" }) else { return XCTFail("no bike row") }
        XCTAssertEqual([ChatStrings.actionRename, ChatStrings.actionPin, ChatStrings.actionArchive,
                        ChatStrings.actionShare, ChatStrings.actionDelete], model.actions(for: bike))
        model.setPinned(bike, true)
        XCTAssertTrue(model.isPinned(bike))
        XCTAssertEqual(["2", "1", "4"], model.rows.map(\.id), "pinned first, then newest")
        XCTAssertTrue(model.actions(for: bike).contains(ChatStrings.actionUnpin))
        XCTAssertFalse(model.actions(for: bike).contains(ChatStrings.actionPin))

        // A second book over the same place is the next launch of the app.
        let second = ChatPinBook(place: place)
        XCTAssertEqual([ChatRules.pinKey(bike, server: model.server)], Array(second.all))

        // **And the other house's list is not pinned by it.** The key carries
        // the address, so the same thread id elsewhere is a different chat.
        StubURLProtocol.reset()
        let (elsewhere, _, _) = rig(server: otherServer, pins: second)
        elsewhere.start()
        await settleUntil({ !elsewhere.rows.isEmpty }, "the other house's list")
        XCTAssertEqual(["1", "2", "4"], elsewhere.rows.map(\.id), "nothing is pinned here")
        XCTAssertFalse(elsewhere.rows.contains { $0.pinned })
    }

    /// A pin while a search is on screen moves the hits too.
    func testAPinDuringASearchMovesTheHits() async {
        let (model, _, waits) = rig()
        model.start()
        model.typed("plan")
        waits.release()
        await settleUntil({ model.hits != nil })
        XCTAssertEqual(["1", "3"], rows(model.content).map(\.id))
        guard let old = model.hits?.last else { return XCTFail("no hits") }
        model.setPinned(old, true)
        XCTAssertEqual(["1", "3"], model.hits?.map(\.id),
                       "a search is still sorted by date: a pin marks a row, it does not reorder a result")
        XCTAssertTrue(model.hits?.last?.pinned ?? false, "and the row knows it is pinned")
    }

    // MARK: - the three writes

    func testRenameSendsATitleAndNothingElse() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.renameRequested(row)
        XCTAssertEqual(row, model.renaming)
        model.rename(row, to: "  lentils  ")
        XCTAssertNil(model.renaming)
        await settleUntil({ house.seen.current.contains("PATCH /api/chat/threads/1") }, "the rename")
        let body = StubURLProtocol.recorded.last { $0.httpMethod == "PATCH" }?.httpBody ?? Data()
        let sent = (try? JSONSerialization.jsonObject(with: body)) as? [String: String] ?? [:]
        XCTAssertEqual(["title": "lentils"], sent, "trimmed, and nothing else in the body")
    }

    /// A blank name is not sent: the server answers a 400 and Android does not
    /// ask either (`ServerChatPlace.kt:114`).
    func testABlankRenameIsNotSent() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        let before = house.seen.current.count
        model.rename(row, to: "   ")
        await nothingHappens()
        XCTAssertEqual(before, house.seen.current.count)
        XCTAssertNil(model.renaming, "and the dialog still closed")
    }

    /// Archiving says so, and **both** lists are read again: the row moved
    /// from one to the other, so reading only the one it came from leaves the
    /// other stale.
    func testArchivingSaysSoAndReadsBothListsAgain() async {
        let (model, house, _) = rig()
        model.start()
        model.showArchived(true)
        await settleUntil({ !model.archivedRows.isEmpty })
        model.showArchived(false)
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        house.seen.set([])
        model.setArchived(row, true)
        await settleUntil({ model.toast != nil }, "the archive")
        XCTAssertEqual(ChatStrings.archivedToast, model.toast)
        let body = StubURLProtocol.recorded.last { $0.httpMethod == "PATCH" }?.httpBody ?? Data()
        XCTAssertEqual(
            ["archived": true],
            (try? JSONSerialization.jsonObject(with: body)) as? [String: Bool] ?? [:]
        )
        // **Both, in one wait.** `refreshLists()` calls `refresh()` on two
        // separate `ApiRead`s, and each starts a `Task` of its own, so the two
        // requests reach the stub in whichever order the scheduler runs them.
        // Waiting for the archived read and then asserting the plain one had
        // already arrived failed about half the time (measured 2026-11-02,
        // 4 of 8 runs) while the app was doing exactly the right thing.
        await settleUntil(
            {
                house.seen.current.contains("GET /api/chat/threads?archived=true")
                    && house.seen.current.contains("GET /api/chat/threads")
            },
            "both lists read again"
        )

        model.setToast(nil)
        model.setArchived(row, false)
        await settleUntil({ model.toast != nil }, "the move back")
        XCTAssertEqual(ChatStrings.movedBackToast, model.toast)
    }

    /// Delete takes the pin with it and closes the conversation if it is the
    /// one open (`ChatHome.kt:477`).
    func testDeleteUnpinsAndClosesTheChatItWasShowing() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.setPinned(row, true)
        model.open(row)
        await settleUntil({ !model.transcript.isEmpty }, "the transcript")
        model.deleteRequested(row)
        XCTAssertEqual(row, model.deleting)
        XCTAssertEqual(
            "Delete \"lentil soup\"? Deletes it for everyone in the house. This can't be undone.",
            model.deleteText(row)
        )
        model.delete(row)
        XCTAssertNil(model.deleting)
        XCTAssertFalse(model.isPinned(row), "a key for a thread that is gone would outlive it")
        XCTAssertNil(model.openThread)
        XCTAssertTrue(model.transcript.isEmpty)
        await settleUntil({ house.seen.current.contains("DELETE /api/chat/threads/1") }, "the delete")
    }

    /// A refused write says the refusal's own words, not a status.
    func testARefusedWriteSaysWhyInWords() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        house.writeStatus.set(409)
        model.rename(row, to: "lentils")
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(aiChatOffText, model.toast, "the 409 carried the server's own sentence")
    }

    // MARK: - the conversation

    func testOpeningAChatReadsItsMessagesAndNamesThisPhone() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.open(row)
        await settleUntil({ model.transcript.messages.count == 2 }, "the transcript")
        XCTAssertEqual(1, model.openThread)
        XCTAssertEqual("lentil soup", model.openTitle)
        XCTAssertTrue(model.conversationOpen)
        XCTAssertEqual(ChatStrings.thisPhone, model.transcript.messages.first?.device,
                       "a message from this install is not called by the device's own name")
        XCTAssertNil(
            model.transcript.windowDividerIndex,
            "window_from_id is the FIRST message: nothing fell out of the window, so no divider"
        )
        XCTAssertTrue(model.transcript.canRetry, "the last message is a finished retryable reply")
    }

    /// **[[read-after-await-wrong-key]] in its navigation form.** A slow read
    /// landing after the person opened another chat must not draw under this
    /// title. The switch happens **inside** the round trip, which is the only
    /// honest way to test it.
    func testATranscriptThatLandsAfterAnotherChatWasOpenedIsDropped() async {
        let house = House()
        house.install()
        let park = parkRequest(
            to: "/api/chat/threads/1/messages",
            answering: { .json(house.transcript.current) },
            otherwise: { request in
                guard let url = request.url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}") }
                if parts.path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                if parts.path == "/api/chat/threads/2/messages" {
                    return .json(#"{"messages":[{"id":90,"role":"user","content":"a quick link"}]}"#)
                }
                return .json("{}")
            }
        )
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = ChatModel(in: container, deviceID: "ios-1", pins: ChatPinBook(place: SavedStateBox()))
        model.open(thread: 1, title: "lentil soup")
        await settle(for: park.arrived, what: "the first transcript read")
        // The person opens another chat while the first read is still open.
        model.open(thread: 2, title: "bike chain")
        park.release.set(true)
        await settleUntil({ model.transcript.messages.count == 1 }, "the second transcript")
        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual(2, model.openThread)
        XCTAssertEqual(["a quick link"], model.transcript.messages.map(\.content),
                       "the first thread's messages never landed under the second's title")
    }

    /// A new chat makes **no thread** until the first message needs one, so a
    /// chat opened and left leaves nothing in the household's list.
    func testANewChatMakesNoThreadUntilSomethingIsSent() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        house.seen.set([])
        model.startNewChat()
        XCTAssertTrue(model.conversationOpen)
        XCTAssertNil(model.openThread)
        XCTAssertEqual(ChatStrings.newChat, model.openTitle)
        await nothingHappens()
        XCTAssertFalse(house.seen.current.contains("POST /api/chat/threads"), "\(house.seen.current)")
        model.closeChat()
        await nothingHappens()
        XCTAssertFalse(house.seen.current.contains("POST /api/chat/threads"),
                       "and closing it left nothing behind either")
    }

    func testSendingFromANewChatMakesTheThreadThenStreamsTheReply() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.threads.data != nil })
        model.startNewChat()
        XCTAssertTrue(model.send("how long do lentils take?"))
        XCTAssertTrue(model.sending)
        await settleUntil({ !model.sending }, "the stream")
        XCTAssertEqual(77, model.openThread, "the thread the first message made")
        XCTAssertFalse(model.newChat)
        XCTAssertTrue(house.seen.current.contains("POST /api/chat/threads"))
        XCTAssertTrue(house.seen.current.contains("POST /api/chat/threads/77/messages"))
        XCTAssertEqual(
            ["how long do lentils take?", "about twenty minutes"],
            model.transcript.messages.map(\.content)
        )
        XCTAssertEqual(11, model.transcript.messages.last?.id, "the done row replaced the live guess")
        XCTAssertFalse(model.transcript.streaming)
        // The send body carries this phone's id and the words, nothing else.
        let body = StubURLProtocol.recorded.last { $0.url?.path.hasSuffix("/messages") == true }?.httpBody ?? Data()
        let sent = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
        XCTAssertEqual(Set(["content", "device_id"]), Set(sent.keys))
        XCTAssertEqual("how long do lentils take?", sent["content"] as? String)
        XCTAssertEqual("ios-1", sent["device_id"] as? String)
    }

    /// Nothing goes out for a blank message, for a second send while one is
    /// running, or with no conversation open.
    func testWhatSendRefusesToSend() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.threads.data != nil })
        XCTAssertFalse(model.send("hello"), "with no conversation open")
        model.startNewChat()
        XCTAssertFalse(model.send("   "), "a blank message")
        house.seen.set([])
        XCTAssertTrue(model.send("first"))
        XCTAssertFalse(model.send("second"), "not while a reply is being written")
        await settleUntil({ !model.sending })
        XCTAssertEqual(1, house.seen.current.filter { $0.hasSuffix("/messages") }.count)
    }

    /// A thread that could not even be made puts the refusal where the reply
    /// would have been (`ChatScreen.kt:773`).
    func testAThreadThatCouldNotBeMadeFailsTheMessageInPlace() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.threads.data != nil })
        house.newThreadStatus.set(503)
        model.startNewChat()
        XCTAssertTrue(model.send("hello?"))
        await settleUntil({ !model.sending }, "the failed send")
        XCTAssertEqual(2, model.transcript.messages.count, "the question stays on screen")
        XCTAssertEqual(
            "nope",
            model.transcript.messages.last?.error,
            "the server's own sentence, which is what this port reads in place of a reason phrase"
        )
        XCTAssertNil(model.openThread, "and no thread was left behind")
    }

    /// The house's AI switch: no composer at all, and the screen says why.
    func testTheComposerIsNotOfferedWhenTheHousesAiIsOff() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.open(row)
        XCTAssertTrue(model.composerOffered, "absent means on")
        XCTAssertNil(model.aiOffNote)
        model.capabilitiesChanged(Capabilities(features: ["ai": false]))
        XCTAssertFalse(model.composerOffered)
        XCTAssertFalse(model.canSend)
        XCTAssertFalse(model.send("anything"), "and a send that cannot work does not go out")
        XCTAssertEqual(
            ShellStrings.unavailableNote(label: ChatStrings.title, isChat: true, aiEnabled: false).title,
            model.aiOffNote?.title
        )
        model.capabilitiesChanged(Capabilities(features: [:]))
        XCTAssertTrue(model.composerOffered, "a manifest with no switch is not a switch turned off")
    }

    // MARK: - try again

    func testRetryIsOfferedOnlyOnAFinishedRetryableReply() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.open(row)
        await settleUntil({ model.transcript.messages.count == 2 })
        XCTAssertTrue(model.transcript.canRetry)
        house.stream.set("""
        event: done
        data: {"id":12,"role":"assistant","content":"about twenty-five minutes","replaces":11,"retryable":true}

        """)
        XCTAssertTrue(model.retry())
        await settleUntil({ !model.sending }, "the retry")
        XCTAssertEqual(
            ["how long do lentils take?", "about twenty-five minutes"],
            model.transcript.messages.map(\.content)
        )
        XCTAssertTrue(house.seen.current.contains("POST /api/chat/threads/1/retry"))
        XCTAssertNil(
            StubURLProtocol.recorded.last { $0.url?.path.hasSuffix("/retry") == true }?.httpBody,
            "the route takes no body (chat.py:1069)"
        )
    }

    /// **A failed retry puts the old reply back and says why once.** The
    /// thread on the server never changed, so a transcript left showing a
    /// failure where a good reply is would be showing something that is not
    /// there.
    func testAFailedRetryRestoresTheReplyAndSaysWhyAsAToast() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.open(row)
        await settleUntil({ model.transcript.messages.count == 2 })
        house.streamStatus.set(409)
        house.streamRefusal.set(nil)
        XCTAssertTrue(model.retry())
        await settleUntil({ !model.sending }, "the failed retry")
        XCTAssertEqual(
            ["how long do lentils take?", "about twenty minutes"],
            model.transcript.messages.map(\.content),
            "the reply that is still on the server is what is on screen"
        )
        XCTAssertNil(model.transcript.messages.last?.error, "and it is not marked failed")
        XCTAssertEqual(
            ChatStrings.couldNotTryAgain(aiChatOffText),
            model.toast,
            "said once, as a toast, about the attempt rather than about the reply"
        )
    }

    func testRetryIsNotOfferedWithNothingToReplace() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.threads.data != nil })
        model.startNewChat()
        XCTAssertFalse(model.transcript.canRetry)
        XCTAssertFalse(model.retry(), "no thread, nothing to try again")
    }

    // MARK: - sharing

    func testSharingReadsTheThreadAndBuildsItsText() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.share(row)
        await settleUntil({ model.sharing != nil }, "the share")
        XCTAssertEqual("lentil soup", model.sharing?.title)
        XCTAssertEqual("""
        lentil soup
        Chat with Domovoi on the home server

        You: how long do lentils take?

        Domovoi: about twenty minutes

        """, model.sharing?.text)
        model.shared()
        XCTAssertNil(model.sharing)
    }

    // MARK: - pushes, and moving house

    /// `chat.changed` from another device re-reads both lists, and the
    /// conversation on screen — but **not** while a reply is being written
    /// here, which would overwrite the live words with the server's older
    /// copy.
    func testAPushRereadsTheListsAndTheOpenChat() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.open(row)
        await settleUntil({ model.transcript.messages.count == 2 })
        house.seen.set([])
        model.received(StateEvent(type: "chat.changed", frame: "{}"))
        await settleUntil({ house.seen.current.contains("GET /api/chat/threads/1/messages") },
                          "the transcript read again")

        // And not mid-stream.
        house.stream.set("""
        event: delta
        data: {"text":"thinking"}

        """)
        XCTAssertTrue(model.send("and lentils in a pressure cooker?"))
        house.seen.set([])
        model.received(StateEvent(type: "chat.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertFalse(
            house.seen.current.contains("GET /api/chat/threads/1/messages"),
            "a reply being written here is not replaced by the server's older copy"
        )
        await settleUntil({ !model.sending }, "the stream")
    }

    /// Moving house drops everything that was the old house's — **except the
    /// pins**, which are keyed by address and so are simply not matched.
    func testMovingHouseDropsTheOldHousesChatsAndKeepsItsPins() async {
        let place = SavedStateBox()
        let (model, _, waits) = rig(pins: ChatPinBook(place: place))
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.setPinned(row, true)
        model.open(row)
        await settleUntil({ model.transcript.messages.count == 2 })
        model.typed("plan")
        waits.release()
        await settleUntil({ model.hits != nil })
        model.showArchived(true)

        model.serverChanged()
        XCTAssertNil(model.openThread)
        XCTAssertFalse(model.newChat)
        XCTAssertTrue(model.transcript.isEmpty)
        XCTAssertEqual("", model.filter)
        XCTAssertFalse(model.searching)
        XCTAssertNil(model.hits)
        XCTAssertFalse(model.showArchived)
        XCTAssertNil(model.renaming)
        XCTAssertNil(model.deleting)
        XCTAssertNil(model.sharing)
        // The two lists are **not** asserted empty: ``ApiRead/serverChanged()``
        // re-reads against the new address and keeps the last value until the
        // new one lands, which is every screen's behaviour on this phone and
        // not Chat's to change. What Chat owns is everything above.
        XCTAssertEqual(
            [ChatRules.pinKey(row, server: model.server)],
            Array(ChatPinBook(place: place).all),
            "the pin is still written down, under the house it belongs to"
        )
    }

    // MARK: - [[read-after-await-wrong-key]]: the house is half of every key
    //
    // Five of the eight tests below were throwaway probes written by the
    // reviewer of I1-H part 1 (`ZZReviewProbeTests.swift`, on
    // `wf/ios-h1-review`): each asserted the correct behaviour, all five
    // failed, and that is how these findings were established rather than
    // relayed. They are permanent here, with the assertion messages they were
    // written with — those name the symptom in the words a person would use.
    //
    // The rig is each test's own rather than ``House``: these need **two
    // houses** and a slow first one, so the dispatcher has to answer one path
    // differently on its second call, or hold one open while the app moves.

    nonisolated private static func health(_ parts: URLComponents) -> StubURLProtocol.Answer {
        let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
        return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
    }

    /// A dispatcher's usual answers: health signed, everything else empty.
    nonisolated private static func plain(_ request: URLRequest) -> StubURLProtocol.Answer {
        guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
        else { return .json("{}") }
        if parts.path == "/api/health" { return health(parts) }
        return .json("{}")
    }

    /// A model on `address` with no ``House`` behind it, and the store, so a
    /// test can move house inside a round trip.
    private func twoHouseRig(_ address: String) -> (ChatModel, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: address, token: "household-abc")
        store.trustServer(address, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = ChatModel(
            in: container,
            deviceID: "ios-1",
            pins: ChatPinBook(place: SavedStateBox()),
            nowISO: { "2026-11-02T19:00:00+00:00" }
        )
        return (model, store)
    }

    /// Move the app from the house it is in to `address`.
    private func move(_ model: ChatModel, _ store: InMemoryShellStore, to address: String) {
        store.trustServer(address, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(address))
        model.serverChanged()
    }

    /// **B2.** `share(_:)` hands a whole conversation to the share sheet, so a
    /// read from the house the person has left is the one finding here that
    /// puts another house's words into whatever they share next.
    ///
    /// Android does not have this: its `shareText` is a `suspend fun` launched
    /// from `rememberCoroutineScope()` (`ChatHome.kt:364`), which dies with the
    /// composition.
    func testAShareReadFromTheOldHouseDoesNotOpenAfterMovingHouse() async {
        let park = parkRequest(
            to: "/api/chat/threads/7/messages",
            answering: {
                .json(#"{"messages":[{"id":1,"role":"user","content":"the old house's words"}]}"#)
            },
            otherwise: { Self.plain($0) }
        )
        let (model, store) = twoHouseRig(server)
        model.share(ChatRow(id: "7", title: "old plan"))
        await settle(for: park.arrived, what: "the share read")

        move(model, store, to: otherServer)
        XCTAssertNil(model.sharing, "serverChanged cleared it")

        park.release.set(true)
        await everythingLands()
        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertNil(
            model.sharing,
            "the old house's chat text opened the share sheet after the app moved house"
        )
    }

    /// **B1.** `openThread == id` is not the key: a thread id belongs to a
    /// house, and `serverChanged()` says in its own words that two houses can
    /// both have one.
    ///
    /// The person opens thread 7 on house A; the read is slow; they move
    /// house and open **thread 7** there. House A's answer then lands, passes
    /// `openThread == 7`, and writes house A's conversation into the
    /// transcript the screen is drawing under house B's title.
    func testAnOldHousesTranscriptDoesNotLandUnderTheSameIdOnTheNewHouse() async {
        let calls = Shared<Int>(0)
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let ranOut = Shared<Bool>(false)
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            guard parts.path == "/api/chat/threads/7/messages" else { return .json("{}") }
            let mine = calls.mutate { $0 += 1; return $0 }
            // House B's own read of **its** thread 7 answers with nothing at
            // all. That is what makes this test honest: the only thing that
            // can put words in the transcript afterwards is the read that was
            // started for the house the app has left, so an empty transcript
            // is the finding and a full one is the bug. House B's read is also
            // the proof of order — a custom `URLProtocol` is served on one
            // worker, so its failure cannot be recorded until house A's answer
            // has been handed back.
            guard mine == 1 else { return .json(#"{"detail":"not this one"}"#, status: 503) }
            arrived.set(true)
            for step in 0..<400 {
                if release.current { break }
                if step == 399 { ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(#"{"messages":[{"id":10,"role":"user","content":"house A's words"}]}"#)
        }
        let (model, store) = twoHouseRig(server)
        model.open(thread: 7, title: "house A's plan")
        await settle(for: arrived, what: "house A's transcript read")

        move(model, store, to: otherServer)
        model.open(thread: 7, title: "house B's plan")

        release.set(true)
        await settleUntil({ model.toast != nil }, "house B's own read of its thread 7")
        await everythingLands()
        XCTAssertFalse(ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual(7, model.openThread, "house B's thread 7 is what is on screen")
        XCTAssertEqual(
            [],
            model.transcript.messages.map(\.content),
            "house A's conversation landed under house B's thread 7"
        )
    }

    /// **Smell 1.** `write(…)`'s task was as untracked as `share`'s, so an
    /// "archived" toast landed on the new house, naming a thread the person is
    /// no longer looking at.
    func testAnArchiveToastFromTheOldHouseDoesNotLandOnTheNewOne() async {
        let park = parkRequest(
            to: "/api/chat/threads/7",
            answering: { .json(#"{"ok":true}"#) },
            otherwise: { Self.plain($0) }
        )
        let (model, store) = twoHouseRig(server)
        model.setArchived(ChatRow(id: "7", title: "old plan"), true)
        await settle(for: park.arrived, what: "the archive write")

        move(model, store, to: otherServer)
        park.release.set(true)
        await everythingLands()
        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertNil(
            model.toast,
            "the old house's archive said \"archived\" over the new house's list"
        )
    }

    /// **B7.** `ensureThread()` wrote `openThread` after an await with no
    /// guard at all, so a thread made for a house the app has left claimed the
    /// composer.
    ///
    /// **This test pins the guard's presence, not a sequence anybody has
    /// seen fire, and it did not go red with the guard taken out.** Say so
    /// here rather than let it read as a proven catcher.
    ///
    /// Every route into this window goes through `cancelStream()` —
    /// `open(_:)`, `startNewChat()`, `closeChat()`, `serverChanged()`, all of
    /// them — and `session.data(for:delegate:)` is cancellation-aware, so the
    /// cancelled POST throws and `ensureThread` never reaches its write. The
    /// window the guard closes is "the response arrived and its main-actor hop
    /// was enqueued before the `cancel()` landed", and nothing in this suite
    /// can place a test inside it. The reviewer said the same and reported it
    /// as a bug to fix rather than one they had shown firing.
    ///
    /// What this does do is hold the end state that matters, so a change that
    /// stops cancellation from being enough on its own is caught here.
    func testAThreadMadeForTheOldHouseDoesNotClaimTheNewOnesComposer() async {
        let park = parkRequest(
            to: "/api/chat/threads",
            answering: { .json(#"{"id":77,"title":null}"#) },
            otherwise: { Self.plain($0) }
        )
        let (model, store) = twoHouseRig(server)
        model.startNewChat()
        XCTAssertTrue(model.send("how long do lentils take?"))
        await settle(for: park.arrived, what: "the new thread")

        move(model, store, to: otherServer)
        park.release.set(true)
        await everythingLands()
        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertNil(
            model.openThread,
            "a thread made on the house the app left became the open chat on the new one"
        )
        XCTAssertFalse(model.newChat)
        XCTAssertFalse(model.sending)
    }

    /// **B5.** A retry whose stream opened, said something, and closed with
    /// neither an `error` nor a `done` — a `StreamingResponse` generator
    /// ending early, a worker restart, a proxy closing after the headers —
    /// gave `finishStream` no reason, and the fallback was
    /// `ChatStrings.searchFailed`.
    ///
    /// Android has no fallback at all (`ChatScreen.kt:879` is
    /// `live.error?.let { toast(…) }`), and the old reply being back on screen
    /// is itself the answer.
    func testAFailedRetryWithNoReasonSaysNothingRatherThanBlamingTheSearch() async {
        let transcript = #"""
        {"messages":[
          {"id":10,"role":"user","content":"how long?"},
          {"id":11,"role":"assistant","content":"twenty minutes","retryable":true}
        ]}
        """#
        let stream = "event: delta\ndata: {\"text\":\"half an \"}\n\n"
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            if parts.path == "/api/chat/threads/7/retry" {
                return .json(stream, headers: ["Content-Type": "text/event-stream"])
            }
            if parts.path == "/api/chat/threads/7/messages" { return .json(transcript) }
            return .json("{}")
        }
        let (model, _) = twoHouseRig(server)
        model.open(thread: 7, title: "lentils")
        await settleUntil({ model.transcript.messages.count == 2 }, "the transcript")
        XCTAssertTrue(model.transcript.canRetry)
        XCTAssertTrue(model.retry())
        await settleUntil({ !model.sending }, "the retry")
        XCTAssertNotEqual(
            ChatStrings.couldNotTryAgain(ChatStrings.searchFailed),
            model.toast,
            "a retry that failed told the person the SEARCH could not run"
        )
        XCTAssertNil(model.toast, "and with no reason to give it says nothing, as Android does")
        XCTAssertEqual(
            ["how long?", "twenty minutes"],
            model.transcript.messages.map(\.content),
            "the reply that is still on the server is what is on screen"
        )
    }

    /// **B4.** `openTitle` was a stored value, so a new chat's header said
    /// "new chat" for ever after the server's title job had named the thread,
    /// and a rename made on the dashboard never showed while the chat was
    /// open here. Android's title begins with *the title as the list has it
    /// now* (`ChatScreen.kt:941`), and `ServerChatPlace.kt:149` writes down
    /// both reasons.
    func testTheOpenTitleFollowsTheListWhenTheServerNamesTheThread() async {
        let threads = Shared<String>(#"{"threads":[]}"#)
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            if parts.path == "/api/chat/models" { return .json(#"{"installed":[]}"#) }
            if parts.path == "/api/chat/threads" { return .json(threads.current) }
            if parts.path == "/api/chat/threads/7/messages" {
                return .json(#"{"messages":[{"id":1,"role":"user","content":"how long do lentils take?"}]}"#)
            }
            return .json("{}")
        }
        let (model, _) = twoHouseRig(server)
        model.start()
        model.open(thread: 7, title: ChatStrings.newChat)
        await settleUntil({ !model.transcript.isEmpty }, "the transcript")
        // The title job has now written one, and a push says the list moved.
        threads.set(#"""
        {"threads":[{"id":7,"title":"lentil soup","updated_at":"2026-11-02T18:00:00+00:00",
                     "message_count":2,"last_snippet":"and then simmer","archived":false}]}
        """#)
        model.received(StateEvent(type: "chat.changed", frame: "{}"))
        await settleUntil({ model.rows.first?.title == "lentil soup" }, "the list")
        XCTAssertEqual("lentil soup", model.rows.first?.title, "the list has the server's title")
        XCTAssertEqual(
            "lentil soup",
            model.openTitle,
            "the conversation's header still says \"new chat\" (ChatScreen.kt:941's titleOf)"
        )
    }

    /// The chain's other three links: the stored title for a thread the list
    /// does not know, the first words of the question for a thread the server
    /// has not named, and `newChat` for nothing at all.
    func testWhatTheHeaderSaysBeforeTheServerHasNamedTheThread() async {
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            if parts.path == "/api/chat/models" { return .json(#"{"installed":[]}"#) }
            if parts.path == "/api/chat/threads" {
                // The list has thread 7 and the server has **not** named it:
                // Android's `titleOf` reads a nullable title and falls
                // through on a null, which is why `ChatRules.row`'s own "new
                // chat" substitution must not be what this asks.
                return .json(#"""
                {"threads":[{"id":7,"title":null,"updated_at":"2026-11-02T18:00:00+00:00",
                             "message_count":2,"archived":false}]}
                """#)
            }
            if parts.path == "/api/chat/threads/7/messages" {
                return .json(#"{"messages":[{"id":1,"role":"user","content":"how long do lentils take to cook?"}]}"#)
            }
            return .json("{}")
        }
        let (model, _) = twoHouseRig(server)
        model.startNewChat()
        XCTAssertEqual(ChatStrings.newChat, model.openTitle, "nothing to go on")

        // Opened from somewhere that knew a name the list does not have yet —
        // a help notification's tap (``PendingChatThread``).
        model.open(thread: 7, title: "bike chain")
        XCTAssertEqual("bike chain", model.openTitle)

        // Opened from a list row for a thread the server has not named:
        // ``ChatRules/row(_:)`` hands over its own "new chat" placeholder,
        // which is the list saying it has no name — so the chain goes on,
        // exactly as Android's does on a null `ThreadRow.title`.
        model.open(thread: 7, title: ChatStrings.newChat)
        model.start()
        await settleUntil({ model.transcript.messages.count == 1 }, "the transcript")
        await settleUntil({ model.threads.data != nil }, "the list")
        XCTAssertEqual(
            "how long do lentils take…",
            model.openTitle,
            "the list has the thread and no name for it, so the question's first five words"
        )
    }

    /// **B3.** The archived view was invisible when Chat was first opened, and
    /// it was a closed loop: `start()` read the archived list only `if
    /// showArchived`, and the only control that sets `showArchived` is the row
    /// the list draws **when the archived count is non-zero**
    /// (`ChatHome.kt:443`). Android reads both lists on mount.
    func testTheArchivedCountIsKnownWhenTheListFirstLands() async {
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            if parts.path == "/api/chat/models" { return .json(#"{"installed":[]}"#) }
            guard parts.path == "/api/chat/threads" else { return .json("{}") }
            let archived = (parts.queryItems ?? []).first { $0.name == "archived" }?.value == "true"
            if archived {
                return .json(#"""
                {"threads":[{"id":3,"title":"old plan","updated_at":"2026-09-01T09:00:00+00:00",
                             "message_count":9,"archived":true}]}
                """#)
            }
            return .json(#"""
            {"threads":[{"id":1,"title":"lentil soup","updated_at":"2026-11-02T18:00:00+00:00",
                         "message_count":6,"archived":false}]}
            """#)
        }
        let (model, _) = twoHouseRig(server)
        model.start()
        await settleUntil({ !model.rows.isEmpty }, "the main list")
        XCTAssertEqual(["1"], model.rows.map(\.id), "the main list landed")
        await settleUntil({ model.archivedCount > 0 }, "the archived list")
        XCTAssertEqual(
            1,
            model.archivedCount,
            "the archived entry's count is 0 on first load, so the row that opens the archived view is never drawn (ChatHome.kt:443)"
        )
        XCTAssertFalse(model.showArchived, "and the main list is still what is on screen")
    }

    /// **B6.** A failed **read** of a thread was worded as a failed **send**:
    /// opening a chat the house could not answer for said "send failed
    /// (offline?)" when nothing had been sent.
    func testAChatThatCouldNotBeReadSaysSoAsAReadAndNotAsASend() async {
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            if parts.path == "/api/chat/threads/7/messages" {
                return .json(#"{"detail":"the thread is being rebuilt"}"#, status: 503)
            }
            return .json("{}")
        }
        let (model, _) = twoHouseRig(server)
        model.open(thread: 7, title: "lentil soup")
        await settleUntil({ model.toast != nil }, "the failed read")
        XCTAssertTrue(
            model.toast?.hasPrefix("open chat failed") == true,
            "the verb is the read's: \(model.toast ?? "nil")"
        )
        XCTAssertFalse(
            model.toast?.hasPrefix("send failed") == true,
            "a read that failed said a send had failed, and nothing was sent"
        )
    }

    /// **Smells 9 and 11.** A stream cancelled on the way out writes nothing
    /// into the transcript and **re-issues nothing**: `stop()` →
    /// `cancelStream()` → `threads.stop()`, and the cancelled task's
    /// continuation then ran `endStream` → `refreshLists()`, which
    /// ``ApiRead/refresh()`` re-joins to the server-change registry and
    /// re-issues — so a screen going away asked the house for both lists.
    ///
    /// This is the only test of stream cancellation there is. What it does
    /// **not** pin is ``ServerEvents``' own `onTermination` hook: the pump's
    /// cancellation has no observable effect through `StubURLProtocol`, whose
    /// `stopLoading` is a no-op.
    func testAStreamCancelledOnTheWayOutAppliesNothingAndRefreshesNothing() async {
        let listReads = Shared<Int>(0)
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let ranOut = Shared<Bool>(false)
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            if parts.path == "/api/chat/threads" { listReads.mutate { $0 += 1 } }
            guard parts.path == "/api/chat/threads/7/messages" else { return .json("{}") }
            // The GET answers at once; **the stream's POST is held open**, so
            // `stop()` happens inside the round trip.
            guard request.httpMethod == "POST" else {
                return .json(#"{"messages":[{"id":10,"role":"user","content":"how long?"}]}"#)
            }
            arrived.set(true)
            for step in 0..<400 {
                if release.current { break }
                if step == 399 { ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(
                "event: delta\ndata: {\"text\":\"about twenty\"}\n\n",
                headers: ["Content-Type": "text/event-stream"]
            )
        }
        let (model, _) = twoHouseRig(server)
        model.open(thread: 7, title: "lentil soup")
        await settleUntil({ model.transcript.messages.count == 1 }, "the transcript")
        XCTAssertTrue(model.send("how long do lentils take?"))
        await settle(for: arrived, what: "the stream")

        let before = listReads.current
        model.stop()
        release.set(true)
        await everythingLands()
        XCTAssertFalse(ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertFalse(model.sending)
        XCTAssertEqual(before, listReads.current, "a screen on its way out read both lists again")
        XCTAssertFalse(
            model.transcript.live?.content.contains("about twenty") == true,
            "and a cancelled stream wrote no words"
        )
    }

    /// **Smell 8.** A rename is a `PATCH` and was optimistic with no revert, so
    /// a refused rename left the rejected name in the header for as long as
    /// the chat was open. ``ChatModel/openTitle`` reading the list is the
    /// revert, which is where Android gets it from too.
    func testARenameTheServerRefusedLeavesTheHeaderAlone() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ !model.rows.isEmpty })
        guard let row = model.rows.first else { return XCTFail("no rows") }
        model.open(row)
        await settleUntil({ model.transcript.messages.count == 2 })
        XCTAssertEqual("lentil soup", model.openTitle)
        house.writeStatus.set(400)
        model.rename(row, to: "LENTILS")
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(
            "lentil soup",
            model.openTitle,
            "the name the server refused stayed in the header"
        )
    }
}
