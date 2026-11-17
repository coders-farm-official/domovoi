import Foundation
import XCTest
@testable import DomovoiKit

/// The Friend houses screen's state against a fake dashboard: the three
/// reads and their problems, the tabs, "message" and "open in lists"; and one
/// conversation — read and marked read on open, marked again when a new one
/// arrives, "Load earlier", the counter, send and its refusals, and that
/// nothing for a house the phone has left lands on, or goes to, another.
@MainActor
final class FriendHousesModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.99:6369"
    private let smiths = String(repeating: "ab", count: 32)
    private let stranger = String(repeating: "ef", count: 32)

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func page(_ rows: [String]) -> String { #"{"messages":[\#(rows.joined(separator: ","))]}"# }

    private func conversation(
        _ fh: FhHouse,
        friendId: String? = "fa1b2c3d4e5f6"
    ) -> (FhConversationModel, InMemoryShellStore) {
        let (container, store) = fhContainer(fh, server: server)
        let api = FriendHousesApi(client: container.client)
        let model = FhConversationModel(
            conversation: FhConversation(house: smiths, name: "The Smiths", friendId: friendId),
            container: container,
            api: api
        )
        return (model, store)
    }

    // MARK: - one conversation

    func testOpeningReadsItThenMarksItRead() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations/\(smiths)?limit=50",
                  page([FhHouse.message(1, state: "read"), FhHouse.message(2, state: "unread")]))
        fh.answer("POST", "/api/friend-houses/conversations/\(smiths)/read", #"{"marked":1}"#)
        let (model, _) = conversation(fh)
        await model.opened()
        XCTAssertEqual([Int64(1), 2], model.thread.messages.map(\.id))
        XCTAssertTrue(model.thread.loaded)
        XCTAssertFalse(model.thread.more, "a short page: nothing earlier")
        XCTAssertEqual(1, model.marks)
        XCTAssertEqual(
            ["GET /api/friend-houses/conversations/\(smiths)?limit=50", "POST /api/friend-houses/conversations/\(smiths)/read"],
            fh.seen.current
        )
    }

    func testANewUnreadWhileOpenMarksItReadAgainAndNothingNewDoesNot() async {
        let fh = FhHouse()
        let url = "/api/friend-houses/conversations/\(smiths)?limit=50"
        fh.answer("GET", url, page([FhHouse.message(1)]))
        fh.answer("POST", "/api/friend-houses/conversations/\(smiths)/read", #"{"marked":0}"#)
        let (model, _) = conversation(fh)
        await model.opened()
        XCTAssertEqual(1, model.marks)
        await model.latest(first: false)
        XCTAssertEqual(1, model.marks, "nothing new, and it marked again")
        fh.answer("GET", url, page([FhHouse.message(1), FhHouse.message(2, state: "unread", body: "Dinner at six?")]))
        await model.latest(first: false)
        XCTAssertEqual(2, model.marks)
        XCTAssertEqual("Dinner at six?", model.thread.messages.last?.body)
        XCTAssertEqual(10_000, FhConversationModel.pollMs)
    }

    func testLoadEarlierAsksByTheOldestHeld() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations/\(smiths)?limit=50",
                  page((51...100).map { FhHouse.message(Int64($0)) }))
        fh.answer("GET", "/api/friend-houses/conversations/\(smiths)?limit=50&before=51",
                  page((41...50).map { FhHouse.message(Int64($0)) }))
        fh.answer("POST", "/api/friend-houses/conversations/\(smiths)/read", #"{"marked":0}"#)
        let (model, _) = conversation(fh)
        await model.opened()
        XCTAssertTrue(model.thread.more, "a full page offers Load earlier")
        await model.loadEarlier()
        XCTAssertEqual((41...100).map { Int64($0) }, model.thread.messages.map(\.id))
        XCTAssertFalse(model.thread.more)
        XCTAssertEqual("Load earlier", FhWords.loadEarlier)
    }

    func testTheCounterAndWhatMaySend() {
        let fh = FhHouse()
        let (model, _) = conversation(fh)
        XCTAssertEqual("0 / 2000", model.counter)
        XCTAssertFalse(model.canSend, "an empty box sends nothing")
        model.edit("   \n")
        XCTAssertFalse(model.canSend, "spaces send nothing")
        model.edit("See you at six")
        XCTAssertEqual("14 / 2000", model.counter)
        XCTAssertTrue(model.canSend)
        model.edit(String(repeating: "a", count: 2001))
        XCTAssertTrue(model.over)
        XCTAssertFalse(model.canSend)
        XCTAssertEqual("2001 / 2000", model.counter)
        XCTAssertEqual("Message The Smiths…", model.placeholder)

        let (former, _) = conversation(fh, friendId: nil)
        former.edit("hello")
        XCTAssertTrue(former.former)
        XCTAssertFalse(former.canSend, "a former friend has no composer to send from")
        XCTAssertEqual("No longer a friend: messages can't be sent to this house.", FhWords.formerFriend)
    }

    func testASendEmptiesTheBoxAndReadsAgain() async throws {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations/\(smiths)?limit=50", page([]))
        fh.answer("POST", "/api/friend-houses/messages", #"{"id":9,"state":"queued","created_at":"2026-11-17T18:25:33+00:00"}"#)
        let (model, _) = conversation(fh)
        model.edit("See you at six")
        await model.send()
        XCTAssertEqual("", model.text)
        XCTAssertNil(model.sendProblem)
        XCTAssertFalse(model.busy)
        XCTAssertEqual(
            ["POST /api/friend-houses/messages", "GET /api/friend-houses/conversations/\(smiths)?limit=50"],
            fh.seen.current
        )
        let body = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.httpMethod == "POST" }?.httpBody
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
        XCTAssertEqual(["friend_id": "fa1b2c3d4e5f6", "text": "See you at six"], object)
    }

    func testARefusedSendSaysTheServersWordsAndKeepsTheText() async {
        let fh = FhHouse()
        fh.answer("POST", "/api/friend-houses/messages", #"{"detail":"200 messages are already waiting for that house."}"#, status: 400)
        let (model, _) = conversation(fh)
        model.edit("one more")
        await model.send()
        XCTAssertEqual("200 messages are already waiting for that house.", model.sendProblem)
        XCTAssertEqual("one more", model.text, "the words are not lost")
        fh.answer("POST", "/api/friend-houses/messages", #"{"detail":"nope"}"#, status: 403)
        await model.send()
        XCTAssertEqual("Only an admin can do that, on the dashboard.", model.sendProblem)
        // Typing again clears it, as Android's `onValueChange` does.
        model.edit("one more!")
        XCTAssertNil(model.sendProblem)
    }

    func testNothingForAHouseThePhoneLeftLandsOrIsSent() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations/\(smiths)?limit=50", page([FhHouse.message(1, state: "unread")]))
        fh.answer("POST", "/api/friend-houses/conversations/\(smiths)/read", #"{"marked":1}"#)
        fh.answer("POST", "/api/friend-houses/messages", #"{"id":9,"state":"queued"}"#)
        let (model, store) = conversation(fh)
        store.trustServer(other, identity: TestIdentity.real.pin)
        store.setToken("household-token-9", for: other)
        XCTAssertTrue(store.setServerURL(other))
        await model.opened()
        XCTAssertTrue(model.thread.messages.isEmpty, "another house's answer landed here")
        XCTAssertEqual(0, model.marks)
        model.edit("for the Smiths")
        await model.send()
        XCTAssertNil(model.sendProblem)
        XCTAssertTrue(fh.seen.current.isEmpty, "a call for the house the phone left was sent: \(fh.seen.current)")
    }

    /// Re-reads never grow the thread past 200: a house writing all day
    /// keeps the newest, and Load earlier is offered again.
    func testReReadsKeepTheNewestTwoHundred() async {
        let fh = FhHouse()
        let url = "/api/friend-houses/conversations/\(smiths)?limit=50"
        fh.answer("POST", "/api/friend-houses/conversations/\(smiths)/read", #"{"marked":0}"#)
        let (model, _) = conversation(fh)
        var top = 0
        for round in 0..<6 {
            let rows = (1...50).map { FhHouse.message(Int64(round * 50 + $0)) }
            top = round * 50 + 50
            fh.answer("GET", url, page(rows))
            await model.latest(first: round == 0)
        }
        XCTAssertEqual(200, model.thread.messages.count)
        XCTAssertEqual(Int64(top), model.thread.messages.last?.id)
        XCTAssertTrue(model.thread.more)
    }

    // MARK: - the screen

    func testTheScreensReadsAndItsMoves() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations", """
            {"conversations":[{"house":"\(smiths)","name":"The Smiths","friend_id":"fa1b2c3d4e5f6","unread":2,"last_id":5,"not_delivered":0}],"unread":2}
            """)
        fh.answer("GET", "/api/friend-houses/friends", """
            {"friends":[{"id":"fa1b2c3d4e5f6","name":"The Smiths","house":"\(smiths)","alert":"read","verified":true},
            {"id":"f2","name":"The Patels","house":"\(stranger)","alert":"inbox","verified":false}]}
            """)
        fh.answer("GET", "/api/friend-houses/shared-lists", """
            {"shared_lists":[{"id":"a","state":"invited","origin":"them","title":"Camping","friend":"The Smiths"},
            {"id":"b","state":"active","origin":"us","title":"Groceries","list_id":7,"list_name":"Groceries","friend":"The Smiths"}]}
            """)
        let (container, _) = fhContainer(fh, server: server)
        let gate = FriendHousesGate(container: container)
        let model = FriendHousesModel(container: container, gate: gate)
        await model.readAll()
        XCTAssertEqual([smiths], model.inbox?.value?.map(\.house))
        XCTAssertEqual(2, model.friends?.value?.count)
        XCTAssertEqual(["b", "a"], fhSharedOrder(model.shared?.value ?? []).map(\.id), "active first, then the offers")
        XCTAssertEqual(15_000, FriendHousesModel.inboxPollMs)

        // A friend never written to opens an empty conversation with them.
        let patels = FhFriend(id: "f2", name: "The Patels", house: stranger)
        model.tab = .friends
        model.message(patels)
        XCTAssertEqual(.messages, model.tab)
        XCTAssertEqual(stranger, model.conversation?.house)
        XCTAssertEqual("f2", model.conversation?.friendId)
        model.closeConversation()
        XCTAssertNil(model.conversation)

        // "open in lists": the list Lists opens next, for this server.
        let selection = ListsSelection(place: SavedStateBox())
        let active = model.shared?.value?.first { $0.state == "active" }
        let invited = model.shared?.value?.first { $0.state == "invited" }
        XCTAssertTrue(model.openInLists(active!, selection: selection))
        XCTAssertEqual(Int64(7), selection.read(server: server))
        XCTAssertFalse(model.openInLists(invited!, selection: selection), "an offer is words, never a way in")

        // The server moves: nothing of the last house's stays on screen.
        model.openConversation(smiths)
        model.serverChanged()
        XCTAssertNil(model.inbox)
        XCTAssertNil(model.friends)
        XCTAssertNil(model.shared)
        XCTAssertNil(model.open)
    }

    func testAReadTheHouseRefusesSaysWhoCan() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations", #"{"detail":"admin only"}"#, status: 403)
        fh.answer("GET", "/api/friend-houses/friends", #"{"detail":"core unreachable"}"#, status: 502)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"shared_lists":[]}"#)
        let (container, _) = fhContainer(fh, server: server)
        let model = FriendHousesModel(container: container, gate: FriendHousesGate(container: container))
        await model.readAll()
        XCTAssertEqual(FhRead<[FhConversation]>.problem("Only an admin can do that, on the dashboard."), model.inbox)
        XCTAssertEqual(FhRead<[FhFriend]>.problem("load friends failed (502)"), model.friends)
        XCTAssertEqual(FhRead<[FhSharedList]>.ok([]), model.shared)
        // Not a house key: nothing opens.
        model.openConversation("../etc")
        XCTAssertNil(model.open)
    }
}
