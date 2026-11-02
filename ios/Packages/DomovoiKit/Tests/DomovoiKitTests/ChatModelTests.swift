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
        // The archived view is a second read, and not made until it is asked
        // for: the list on screen is one route's answer.
        XCTAssertTrue(model.archivedRows.isEmpty)
        model.showArchived(true)
        await settleUntil({ !model.archivedRows.isEmpty }, "the archived list")
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
        await settleUntil(
            { house.seen.current.contains("GET /api/chat/threads?archived=true") },
            "the archived list read again"
        )
        XCTAssertTrue(house.seen.current.contains("GET /api/chat/threads"))

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
}
