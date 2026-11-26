import Foundation
import XCTest
@testable import DomovoiKit

/// The Friend houses screen's state against a fake dashboard: the two reads
/// and their problems, the tabs, "open in Lists", and Accept, Decline and
/// Stop sharing (1.4.1); and one
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
        fh.answer("GET", "/api/friend-houses/shared-lists", """
            {"shared_lists":[{"id":"\(offerId)","state":"invited","origin":"them","title":"Camping","friend":"The Smiths"},
            {"id":"\(activeId)","state":"active","origin":"us","title":"Groceries","list_id":7,"list_name":"Groceries","friend":"The Smiths"}]}
            """)
        let (container, _) = fhContainer(fh, server: server)
        let gate = FriendHousesGate(container: container)
        let model = FriendHousesModel(container: container, gate: gate)
        await model.readAll()
        XCTAssertEqual([smiths], model.inbox?.value?.map(\.house))
        let sections = fhSharedSections(model.shared?.value ?? [])
        XCTAssertEqual([offerId], sections.offers.map(\.id), "the offers to this house first")
        XCTAssertEqual([activeId], sections.current.map(\.id), "then what is shared now")
        XCTAssertEqual(15_000, FriendHousesModel.inboxPollMs)
        // 1.4.1: Messages, then Shared lists; the friends are the admin
        // zone's, hidden from a phone without admin, and not even read.
        let out = fhTabsFor(.signedOut, sharedScreen: false)
        XCTAssertEqual([.messages, .shared], out)
        XCTAssertEqual(["messages", "shared lists"], out.map(\.label))
        XCTAssertEqual(
            ["GET /api/friend-houses/conversations", "GET /api/friend-houses/shared-lists"],
            fh.seen.current
        )

        // A conversation opens from the inbox; a house not in it doesn't.
        model.openConversation(smiths)
        XCTAssertEqual("fa1b2c3d4e5f6", model.conversation?.friendId)
        model.closeConversation()
        XCTAssertNil(model.conversation)
        model.openConversation(stranger)
        XCTAssertNil(model.conversation, "a friend never written to has no way in on the phone")
        model.closeConversation()

        // "open in Lists": the list Lists opens next, for this server.
        let selection = ListsSelection(place: SavedStateBox())
        let active = model.shared?.value?.first { $0.state == "active" }
        let invited = model.shared?.value?.first { $0.state == "invited" }
        XCTAssertTrue(model.openInLists(active!, selection: selection))
        XCTAssertEqual(Int64(7), selection.read(server: server))
        XCTAssertFalse(model.openInLists(invited!, selection: selection), "an offer has no list here yet")
        // Ours, waiting for them to accept, is a list here already.
        let ours = FhSharedList(id: activeId, state: "invited", origin: "us", listId: 9)
        XCTAssertTrue(model.openInLists(ours, selection: selection))

        // The server moves: nothing of the last house's stays on screen.
        model.openConversation(smiths)
        model.serverChanged()
        XCTAssertNil(model.inbox)
        XCTAssertNil(model.shared)
        XCTAssertNil(model.open)
    }

    func testAReadTheHouseRefusesSaysWhoCan() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses/conversations", #"{"detail":"admin only"}"#, status: 403)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"detail":"core unreachable"}"#, status: 502)
        let (container, _) = fhContainer(fh, server: server)
        let model = FriendHousesModel(container: container, gate: FriendHousesGate(container: container))
        await model.readAll()
        XCTAssertEqual(FhRead<[FhConversation]>.problem("Only an admin can do that, on the dashboard."), model.inbox)
        XCTAssertEqual(FhRead<[FhSharedList]>.problem("load shared lists failed (502)"), model.shared)
        // Not a house key: nothing opens.
        model.openConversation("../etc")
        XCTAssertNil(model.open)
    }

    // MARK: - the shared lists' actions (household tier, 1.4.1)

    private let offerId = String(repeating: "0f", count: 16)
    private let activeId = String(repeating: "1e", count: 16)

    private func offer(title: String = "Camping") -> FhSharedList {
        FhSharedList(id: offerId, state: "invited", origin: "them", title: title, friend: "The Smiths")
    }

    /// The screen with its lists read (an action goes only to the server
    /// the lists came from), and the house's log emptied after the read.
    private func screen(_ fh: FhHouse) async -> (FriendHousesModel, InMemoryShellStore) {
        let (container, store) = fhContainer(fh, server: server)
        let model = FriendHousesModel(container: container, gate: FriendHousesGate(container: container))
        fh.answer("GET", "/api/friend-houses/conversations", #"{"conversations":[],"unread":0}"#)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"shared_lists":[]}"#)
        await model.readAll()
        fh.seen.mutate { $0 = [] }
        return (model, store)
    }

    func testNoListActionBeforeTheListsWereRead() async {
        let fh = FhHouse()
        let (container, _) = fhContainer(fh, server: server)
        let model = FriendHousesModel(container: container, gate: FriendHousesGate(container: container))
        let outcome = await model.accept(offer())
        XCTAssertEqual(.dropped, outcome)
        model.askDecline(offer())
        XCTAssertNil(model.ask)
        XCTAssertTrue(fh.seen.current.isEmpty)
    }

    func testAcceptTakesTheListAndSaysItsNameHere() async throws {
        let fh = FhHouse()
        fh.answer("POST", "/api/friend-houses/shared-lists/\(offerId)/accept", #"{"list_id":12,"name":"Camping"}"#)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"shared_lists":[]}"#)
        let (model, _) = await screen(fh)
        let outcome = await model.accept(offer())
        XCTAssertEqual(.accepted, outcome)
        XCTAssertEqual("added to Lists as Camping", model.takeToast())
        XCTAssertNil(model.toast, "a toast is said once")
        XCTAssertTrue(model.busyShared.isEmpty)
        XCTAssertEqual(
            ["POST /api/friend-houses/shared-lists/\(offerId)/accept", "GET /api/friend-houses/shared-lists"],
            fh.seen.current,
            "accepted, then the lists read again"
        )
        let body = try XCTUnwrap(StubURLProtocol.recorded.last { $0.httpMethod == "POST" }?.httpBody)
        XCTAssertEqual("{}", String(decoding: body, as: UTF8.self), "no name: their title")
    }

    func testANameTakenHereAsksForAnotherAndSendsIt() async throws {
        let fh = FhHouse()
        let path = "/api/friend-houses/shared-lists/\(offerId)/accept"
        fh.answer("POST", path, #"{"detail":"There's already a list called Camping here."}"#, status: 400)
        let (model, _) = await screen(fh)
        let first = await model.accept(offer())
        XCTAssertEqual(.nameTaken("There's already a list called Camping here."), first)
        XCTAssertNil(model.toast, "the row says it, not a toast")
        fh.answer("POST", path, #"{"list_id":13,"name":"Camping 2"}"#)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"shared_lists":[]}"#)
        let second = await model.accept(offer(), name: "  Camping 2 ")
        XCTAssertEqual(.accepted, second)
        XCTAssertEqual("added to Lists as Camping 2", model.takeToast())
        let body = try XCTUnwrap(StubURLProtocol.recorded.last { $0.httpMethod == "POST" }?.httpBody)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
        XCTAssertEqual(["name": "Camping 2"], object)
    }

    func testDeclineAndStopSharingAskFirstThenAct() async {
        let fh = FhHouse()
        fh.answer("POST", "/api/friend-houses/shared-lists/\(offerId)/decline", #"{"ok":true}"#)
        fh.answer("DELETE", "/api/friend-houses/shared-lists/\(activeId)", #"{"ok":true}"#)
        fh.answer("GET", "/api/friend-houses/shared-lists", #"{"shared_lists":[]}"#)
        let (model, _) = await screen(fh)
        let active = FhSharedList(
            id: activeId, state: "active", origin: "us", title: "Groceries", listId: 7, listName: "Groceries", friend: "The Smiths"
        )

        // Decline is for an offer only, and asks first.
        model.askDecline(active)
        XCTAssertNil(model.ask, "Decline on a list shared now")
        model.askDecline(offer())
        let decline = try? XCTUnwrap(model.ask)
        XCTAssertEqual("Decline this list?", decline?.title)
        XCTAssertEqual("Decline", decline?.confirm)
        XCTAssertEqual("\u{201C}Camping\u{201D} won\u{2019}t be added here. The Smiths isn\u{2019}t told.", decline?.body)
        model.cancelAsk()
        XCTAssertNil(model.ask)
        XCTAssertTrue(fh.seen.current.isEmpty, "a cancel sent something")
        model.askDecline(offer())
        await model.confirmAsk(model.ask!)
        XCTAssertEqual("list declined", model.takeToast())

        // Stop sharing is for a list shared now only.
        model.askStop(offer())
        XCTAssertNil(model.ask, "Stop sharing on an offer")
        model.askStop(active)
        XCTAssertEqual("Stop sharing?", model.ask?.title)
        XCTAssertEqual("Stop sharing Groceries with The Smiths? You both keep your own copy.", model.ask?.body)
        await model.confirmAsk(model.ask!)
        XCTAssertEqual("stopped sharing \u{2014} you both keep your own copy", model.takeToast())
        XCTAssertEqual(
            [
                "POST /api/friend-houses/shared-lists/\(offerId)/decline", "GET /api/friend-houses/shared-lists",
                "DELETE /api/friend-houses/shared-lists/\(activeId)", "GET /api/friend-houses/shared-lists",
            ],
            fh.seen.current
        )
    }

    func testARefusedListActionSaysWhyInTheToast() async {
        let fh = FhHouse()
        // An older server keeps these an admin's: the admin sentence.
        fh.answer("DELETE", "/api/friend-houses/shared-lists/\(activeId)", #"{"detail":"admin"}"#, status: 403)
        fh.answer("POST", "/api/friend-houses/shared-lists/\(offerId)/accept", #"{"detail":"x"}"#, status: 502)
        let (model, _) = await screen(fh)
        model.askStop(FhSharedList(id: activeId, state: "active", origin: "us"))
        await model.confirmAsk(model.ask!)
        XCTAssertEqual("Only an admin can do that, on the dashboard.", model.takeToast())
        let outcome = await model.accept(offer())
        XCTAssertEqual(.failed, outcome)
        XCTAssertEqual("accept failed (502)", model.takeToast())
        XCTAssertTrue(model.busyShared.isEmpty)
    }

    func testNothingForAHouseThePhoneLeftIsAcceptedOrSaid() async {
        let fh = FhHouse()
        fh.answer("POST", "/api/friend-houses/shared-lists/\(offerId)/accept", #"{"list_id":12,"name":"Camping"}"#)
        let (model, store) = await screen(fh)
        store.trustServer(other, identity: TestIdentity.real.pin)
        store.setToken("household-token-9", for: other)
        // The confirmation was asked on this house; the phone moves first.
        model.askDecline(offer())
        let asked = model.ask!
        XCTAssertTrue(store.setServerURL(other))
        let outcome = await model.accept(offer())
        XCTAssertEqual(.dropped, outcome)
        await model.confirmAsk(asked)
        XCTAssertNil(model.toast)
        XCTAssertTrue(fh.seen.current.isEmpty, "a list action for the house the phone left was sent: \(fh.seen.current)")
    }

    func testTheSharedListsWords() {
        XCTAssertEqual("The Smiths shared a list: \u{201C}Camping\u{201D}", FhWords.invited("The Smiths", "Camping"))
        XCTAssertEqual("Groceries \u{2014} shared with The Smiths", FhWords.sharedLine("Groceries", origin: "us", friend: "The Smiths"))
        XCTAssertEqual("Camping \u{2014} from The Smiths", FhWords.sharedLine("Camping", origin: "them", friend: "The Smiths"))
        XCTAssertEqual("added to Lists as a new list", FhWords.accepted(nil))
        // Their title, only where it differs from the name here.
        let theirs = FhSharedList(id: offerId, state: "active", origin: "them", title: "Camping", listName: "Trip")
        XCTAssertEqual("they call it \u{201C}Camping\u{201D}", fhTheirTitle(theirs))
        XCTAssertNil(fhTheirTitle(FhSharedList(id: offerId, state: "active", origin: "them", title: "Trip", listName: "Trip")))
        XCTAssertNil(fhTheirTitle(FhSharedList(id: offerId, state: "active", origin: "us", title: "Camping", listName: "Trip")))
        XCTAssertEqual("this list", fhListName(FhSharedList(id: offerId)))
        // A list a URL can't name is not drawn at all.
        XCTAssertTrue(fhSharedSections([FhSharedList(id: "nope", state: "active", origin: "us")]).current.isEmpty)
        // The note left is the one thing still the admin's: starting a share.
        XCTAssertFalse(FhWords.listsAdmin.contains("accept"))
        XCTAssertFalse(FhWords.listsAdmin.contains("stop"))
        XCTAssertFalse(FhWords.noMessagesSub.lowercased().contains("press message"), "no Message button on a phone")
    }
}
