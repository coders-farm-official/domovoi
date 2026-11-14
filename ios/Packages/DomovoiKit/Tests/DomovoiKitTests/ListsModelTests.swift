import Foundation
import XCTest
@testable import DomovoiKit

/// The Lists screen's data layer against a server that is not a server: the
/// live re-read (§7.6, `ListsLiveTest.kt`), the add box (§7.7), the
/// paired-only state, a list deleted elsewhere, and what an answer that lands
/// after the phone moved house is allowed to touch.
///
/// The house answers in the §2 shapes (design-notes/LISTS-MOBILE-HANDOFF.md),
/// built from the hand-off's own field list, and every list it serves is
/// decoded by the model's real decoders. No test here indexes an array.
@MainActor
final class ListsModelTests: XCTestCase {

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

    /// To-do (1) and Shopping (2); Shopping holds Milk, said in the kitchen.
    private final class House: Sendable {

        let lists = Shared<[String]>([
            House.list(1, "To-do", position: 1, open: 0),
            House.list(2, "Shopping", position: 2, open: 1),
        ])
        let items = Shared<[Int64: [String]]>([
            1: [],
            2: [House.item(41, list: 2, "Milk", position: 1, name: "Alex", room: "kitchen")],
        ])
        /// What the next add answers with, and its status.
        let addStatus = Shared<Int>(201)
        let addOutcome = Shared<String>("added")
        let readStatus = Shared<Int>(200)
        /// Lists whose own read 404s while the lists read still names them —
        /// deleted between the two reads.
        let goneDetail = Shared<Set<Int64>>([])

        /// Every request, as `METHOD path`.
        let seen = Shared<[String]>([])

        static func list(_ id: Int64, _ name: String, position: Int, open: Int, done: Int = 0) -> String {
            """
            {"id": \(id), "name": "\(name)", "position": \(position), "open_count": \(open), "done_count": \(done),
             "created_at": "2026-11-11T20:55:21.123+00:00", "updated_at": "2026-11-11T21:03:00+00:00"}
            """
        }

        static func item(
            _ id: Int64,
            list: Int64,
            _ text: String,
            position: Int,
            done: Bool = false,
            name: String? = nil,
            room: String? = nil
        ) -> String {
            func value(_ s: String?) -> String { s.map { "\"\($0)\"" } ?? "null" }
            let voice = name != nil || room != nil
            return """
            {"id": \(id), "list_id": \(list), "text": "\(text)", "done": \(done), "done_at": null, "position": \(position),
             "added_by_person_id": \(name == nil ? "null" : "1"), "added_by_name": \(value(name)),
             "added_by_room": \(value(room)), "added_via": "\(voice ? "voice" : "screen")",
             "created_at": "...", "updated_at": "..."}
            """
        }

        func count(_ what: String) -> Int { seen.current.filter { $0 == what }.count }

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard let url = request.url,
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            let method = request.httpMethod ?? "GET"
            seen.mutate { $0.append("\(method) \(parts.path)") }
            if method == "GET", readStatus.current != 200 {
                return .json(#"{"detail":"this device is not paired with the house"}"#, status: readStatus.current)
            }
            if method == "GET", parts.path == "/api/lists" {
                return .json("[\(lists.current.joined(separator: ","))]")
            }
            if method == "GET", parts.path.hasPrefix("/api/lists/"),
               let id = Int64(parts.path.dropFirst("/api/lists/".count)) {
                guard !goneDetail.current.contains(id) else {
                    return .json(#"{"detail": "list \#(id) not found"}"#, status: 404)
                }
                guard let row = lists.current.first(where: { $0.contains("\"id\": \(id),") }) else {
                    return .json(#"{"detail": "list \#(id) not found"}"#, status: 404)
                }
                let rows = items.current[id] ?? []
                return .json(#"{"list": \#(row), "items": [\#(rows.joined(separator: ","))]}"#)
            }
            if method == "POST", parts.path.hasSuffix("/items") {
                guard addStatus.current == 201 else {
                    return .json(#"{"detail":"the house fell over"}"#, status: addStatus.current)
                }
                return .json(
                    #"{"item": \#(House.item(41, list: 2, "Milk", position: 1)), "outcome": "\#(addOutcome.current)"}"#,
                    status: 201
                )
            }
            return .json("{}", status: 405)
        }
    }

    private func container(
        _ house: House,
        token: String? = "household-abc"
    ) -> (AppContainer, InMemoryShellStore) {
        StubURLProtocol.dispatch { request in house.answer(request) }
        let store = InMemoryShellStore(serverURL: server, token: token)
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (container, store)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    /// Started, with the lists and the open list both read.
    private func opened(_ model: ListsModel) async {
        model.start()
        await settleUntil({ model.lists.answered }, "the lists read")
        await settleUntil({ model.openDetail != nil }, "the open list's read")
    }

    // MARK: - §7.6 the live re-read

    /// `aListsChangedEventReReadsTheListsAndTheOpenList`: a `lists.changed`
    /// on /ws/state re-reads the lists AND the open list. Another feature's
    /// event is not a reason to re-read, and the event's payload is never
    /// read: one that is not even JSON still re-reads both.
    func testAListsChangedEventReReadsTheListsAndTheOpenList() async {
        let house = House()
        let (container, _) = container(house)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        await opened(model)
        await everythingLands()
        XCTAssertEqual(1, house.count("GET /api/lists"))
        XCTAssertEqual(1, house.count("GET /api/lists/1"), "the first list is the one opened")
        XCTAssertEqual(1, model.selectedID)
        XCTAssertEqual(["lists.changed"], Array(ListsModel.eventTypes))

        container.stateEventArrived(StateEvent(type: "calendar.events.changed", frame: "{}"))
        await everythingLands()
        XCTAssertEqual(1, house.count("GET /api/lists"), "another feature's push re-read the lists")
        XCTAssertEqual(1, house.count("GET /api/lists/1"), "another feature's push re-read the open list")

        container.stateEventArrived(StateEvent(type: "lists.changed", frame: "x"))
        await settleUntil(
            { house.count("GET /api/lists") >= 2 && house.count("GET /api/lists/1") >= 2 },
            "both re-reads"
        )
        await everythingLands()
        XCTAssertEqual(2, house.count("GET /api/lists"))
        XCTAssertEqual(2, house.count("GET /api/lists/1"))
    }

    /// A voice add elsewhere, then the push: the new item is on the board.
    func testAVoiceAddArrivesOnTheBoardWithItsWhoLine() async {
        let house = House()
        let (container, _) = container(house)
        let selection = ListsSelection(place: SavedStateBox())
        selection.save(server: server, id: 2)
        let model = ListsModel(container: container, selection: selection)
        await opened(model)
        XCTAssertEqual(2, model.selectedID, "the list this phone last had open, for this server")
        XCTAssertEqual(["Milk"], model.board.open.map(\.text))

        house.items.mutate {
            $0[2, default: []].append(House.item(42, list: 2, "Eggs", position: 2, room: "garage"))
        }
        container.stateEventArrived(StateEvent(type: "lists.changed", frame: "{}"))
        await settleUntil({ model.board.open.count == 2 }, "the pushed item")
        XCTAssertEqual(["Milk", "Eggs"], model.board.open.map(\.text))
        XCTAssertEqual(["Alex · kitchen", "by voice · garage"], model.board.open.map { listsWhoLine($0) ?? "-" })
    }

    // MARK: - §7.7 the add box

    /// `theAddFieldEmptiesOnSendAndGetsItsWordsBackAfterAFailedAdd`, on the
    /// field itself: sent and empty while the add travels; a failure puts the
    /// words back; what was typed meanwhile is never overwritten.
    func testTheAddFieldEmptiesOnSendAndGetsItsWordsBackAfterAFailedAdd() async {
        let field = ListAddField("  Milk ")
        let sent = Shared<[String]>([])
        let release = Shared<Bool>(false)

        let first = Task { @MainActor in
            await field.submit { (words: String) async throws(DomovoiError) -> String in
                sent.mutate { $0.append(words) }
                for _ in 0..<settleYieldLimit where !release.current { await Task.yield() }
                throw DomovoiError.unreachable("offline")
            }
        }
        await settleUntil({ sent.current == ["Milk"] }, "the send")
        XCTAssertEqual("", field.text, "the box is not empty while the add travels")
        release.set(true)
        let failed = await first.value
        XCTAssertNotNil(failed, "something was sent")
        XCTAssertEqual("Milk", field.text, "the failed add's words did not come back")

        // Typed again while the add was out: that is never overwritten.
        release.set(false)
        let second = Task { @MainActor in
            await field.submit { (_: String) async throws(DomovoiError) -> String in
                for _ in 0..<settleYieldLimit where !release.current { await Task.yield() }
                throw DomovoiError.unreachable("offline")
            }
        }
        await settleUntil({ field.text.isEmpty }, "the second send")
        field.text = "Eggs"
        release.set(true)
        _ = await second.value
        XCTAssertEqual("Eggs", field.text, "a failed add overwrote what was typed since")

        // A good add leaves the box empty; nothing to send sends nothing.
        let fine = await field.submit { (_: String) async throws(DomovoiError) -> String in "fine" }
        XCTAssertEqual("fine", try? fine?.get())
        XCTAssertEqual("", field.text)
        field.text = "   "
        let nothing = await field.submit { (_: String) async throws(DomovoiError) -> String in
            XCTFail("a blank box sent something")
            return "no"
        }
        XCTAssertNil(nothing)
    }

    /// Through the model: a failed add says why and gives the words back; an
    /// `already` says so.
    func testAFailedAddSaysWhyAndGivesTheWordsBack() async {
        let house = House()
        let (container, _) = container(house)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        await opened(model)

        house.addStatus.set(500)
        model.field.text = "Bread"
        model.add()
        XCTAssertEqual("", model.field.text, "the box did not empty as the add was sent")
        await settleUntil({ model.toast != nil }, "the failed add")
        XCTAssertEqual("add failed: 500: {\"detail\":\"the house fell over\"}", model.takeToast())
        XCTAssertEqual("Bread", model.field.text)

        house.addStatus.set(201)
        house.addOutcome.set("already")
        model.add()
        await settleUntil({ model.toast != nil }, "the add")
        XCTAssertEqual("\"Milk\" is already on the list", model.takeToast())
        XCTAssertEqual("", model.field.text)
    }

    // MARK: - the paired-only state

    /// No household token: nothing is asked, and the screen is told to show
    /// the paired-only state (`rememberListsData(paired = false)`).
    func testAnUnpairedPhoneAsksNothingAndIsPairedOnly() async {
        let house = House()
        let (container, _) = container(house, token: nil)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        model.start()
        await settleUntil({ model.lists.answered }, "the lists read")
        await everythingLands()
        XCTAssertTrue(model.refused)
        XCTAssertNil(model.selectedID)
        XCTAssertEqual([], house.seen.current.filter { $0.contains("/api/lists") }, "an unpaired phone asked anyway")
    }

    /// A token the house refuses is the same state, not an error.
    func testARefusedReadIsPairedOnly() async {
        let house = House()
        house.readStatus.set(401)
        let (container, _) = container(house)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        model.start()
        await settleUntil({ model.lists.answered }, "the lists read")
        XCTAssertTrue(model.refused)
        XCTAssertNil(model.lists.error)
    }

    // MARK: - a list deleted elsewhere

    /// The open list deleted on another phone: its read 404s, and the screen
    /// drops back to the first list and re-reads (hand-off §6).
    func testAListDeletedElsewhereDropsBackToTheFirst() async {
        let house = House()
        let (container, _) = container(house)
        let selection = ListsSelection(place: SavedStateBox())
        selection.save(server: server, id: 2)
        let model = ListsModel(container: container, selection: selection)
        await opened(model)
        XCTAssertEqual(2, model.selectedID)

        // Deleted between the two reads: the lists still name it, its own
        // read 404s.
        house.goneDetail.set([2])
        container.stateEventArrived(StateEvent(type: "lists.changed", frame: "{}"))
        await settleUntil({ model.selectedID == 1 && model.openDetail?.list.id == 1 }, "the drop back")
        XCTAssertNil(selection.read(server: server), "the gone list is still this phone's choice")
        XCTAssertGreaterThanOrEqual(house.count("GET /api/lists"), 3, "the lists were not read again after the 404")
    }

    /// Gone from the lists read itself: the first list is shown.
    func testAListGoneFromTheListsShowsTheFirst() async {
        let house = House()
        let (container, _) = container(house)
        let selection = ListsSelection(place: SavedStateBox())
        selection.save(server: server, id: 2)
        let model = ListsModel(container: container, selection: selection)
        await opened(model)
        house.lists.set([House.list(1, "To-do", position: 1, open: 0)])
        container.stateEventArrived(StateEvent(type: "lists.changed", frame: "{}"))
        await settleUntil({ model.selectedID == 1 && model.openDetail?.list.id == 1 }, "the first list")
        XCTAssertEqual([], model.board.items.map(\.id), "Shopping's rows are on To-do's board")
    }

    // MARK: - read-after-await: every answer keyed by its server

    /// The open list's read is in flight when the phone moves house; the old
    /// house's answer lands after. It must not be drawn: list 2 exists in
    /// both houses, and the rows are the other house's.
    func testADetailReadThatLandsAfterMovingHouseIsDropped() async {
        let house = House()
        let parked = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let ranOut = Shared<Bool>(false)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/lists/2" {
                parked.set(true)
                for step in 0..<400 {
                    if release.current { break }
                    if step == 399 { ranOut.set(true) }
                    Thread.sleep(forTimeInterval: 0.002)
                }
            }
            return house.answer(request)
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        // Shopping, so the rows that must not be drawn are Milk's.
        let selection = ListsSelection(place: SavedStateBox())
        selection.save(server: server, id: 2)
        let model = ListsModel(container: container, selection: selection)
        model.start()
        await settleUntil({ parked.current }, "the open list's read")

        // The phone moves house **while the read is in flight**, and nothing
        // has told the reads yet — the window the key check is for.
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        release.set(true)
        await everythingLands()
        XCTAssertFalse(ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertNil(model.openDetail, "the first house's list was drawn in the second house")
        XCTAssertEqual([], model.board.items.map(\.text), "the first house's rows are on the board")
    }

    /// A make that lands after moving house selects nothing and says nothing:
    /// the 201's id is the first house's.
    func testAMakeThatLandsAfterMovingHouseTouchesNothing() async {
        let house = House()
        let parked = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let ranOut = Shared<Bool>(false)
        StubURLProtocol.dispatch { request in
            if request.httpMethod == "POST", request.url?.path == "/api/lists" {
                parked.set(true)
                for step in 0..<400 {
                    if release.current { break }
                    if step == 399 { ranOut.set(true) }
                    Thread.sleep(forTimeInterval: 0.002)
                }
                return .json(House.list(7, "Camping", position: 3, open: 0), status: 201)
            }
            return house.answer(request)
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let selection = ListsSelection(place: SavedStateBox())
        let model = ListsModel(container: container, selection: selection)
        await opened(model)

        let closed = Shared<Bool?>(nil)
        model.makeList("Camping") { ok in closed.set(ok) }
        await settleUntil({ parked.current }, "the make")
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.serverChanged()

        release.set(true)
        await everythingLands()
        XCTAssertFalse(ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertNil(selection.read(server: otherServer), "the first house's new id became the second house's choice")
        XCTAssertNil(selection.read(server: server), "an answer after the move still wrote the first house's choice")
        XCTAssertNotEqual(7, model.selectedID)
        XCTAssertNil(model.toast)
        XCTAssertNil(closed.current, "the name field was told how a make in another house went")
    }

    /// Moving house drops the board, the pending delete and the choice.
    func testMovingHouseClearsTheBoardAndThePendingDelete() async {
        let house = House()
        let (container, store) = container(house)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        await opened(model)
        guard let open = model.openDetail?.list else { return XCTFail("no open list") }
        model.askToDelete(open)
        XCTAssertNotNil(model.deleteTarget)

        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.serverChanged()
        XCTAssertNil(model.deleteTarget)
        XCTAssertNil(model.selectedID)
        XCTAssertEqual([], model.board.items.map(\.id))
    }

    /// A confirm raised in one house and tapped in another sends no DELETE.
    func testAConfirmAfterMovingHouseSendsNoDelete() async {
        let house = House()
        let (container, store) = container(house)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        await opened(model)
        guard let open = model.openDetail?.list else { return XCTFail("no open list") }
        model.askToDelete(open)
        guard let ask = model.deleteTarget else { return XCTFail("no confirm") }

        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.confirmDelete(ask)
        await everythingLands()
        XCTAssertEqual(0, house.seen.current.filter { $0.hasPrefix("DELETE") }.count)
    }

    // MARK: - lifecycle

    /// `stop()` leaves the push registry: a screen on its way out does not
    /// re-read on every push.
    func testAStoppedScreenLeavesTheFanOut() async {
        let house = House()
        let (container, _) = container(house)
        let model = ListsModel(container: container, selection: ListsSelection(place: SavedStateBox()))
        XCTAssertEqual(0, container.stateEventSinkCount)
        await opened(model)
        XCTAssertEqual(1, container.stateEventSinkCount)
        model.stop()
        XCTAssertEqual(0, container.stateEventSinkCount)
        let before = model.lists.fetches
        container.stateEventArrived(StateEvent(type: "lists.changed", frame: "{}"))
        await everythingLands()
        XCTAssertEqual(before, model.lists.fetches)
    }
}
