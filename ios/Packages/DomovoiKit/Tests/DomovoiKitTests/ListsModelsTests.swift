import Foundation
import XCTest
@testable import DomovoiKit

/// The Lists screen's decoders, wording, order and errors, against the
/// contract in design-notes/LISTS-MOBILE-HANDOFF.md §2 — a port of
/// `ListsModelsTest.kt` (Android 3a3b6422), case for case.
///
/// **The JSON below is copied from §2 of the hand-off**, not written to match
/// the decoder (the stub-house lesson: test the decoder, not a fixture). No
/// test here indexes an array: a list is compared whole, or read with
/// `first(where:)` and unwrapped.
final class ListsModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - §7.1 decoding, the §2 shapes verbatim

    private let listJson = """
    {"id": 2, "name": "Shopping", "position": 2, "open_count": 3, "done_count": 1,
     "created_at": "2026-11-11T20:55:21.123+00:00", "updated_at": "2026-11-11T21:03:00+00:00"}
    """

    private let voiceItemJson = """
    {"id": 41, "list_id": 2, "text": "Milk", "done": false, "done_at": null, "position": 4,
     "added_by_person_id": 1, "added_by_name": "Alex", "added_by_room": "kitchen",
     "added_via": "voice",
     "created_at": "...", "updated_at": "..."}
    """

    /// An item added on a screen: "Items added from the dashboard or an app
    /// have all three null" (§2.1); crossed off, so `done_at` is set.
    private let screenItemJson = """
    {"id": 42, "list_id": 2, "text": "Bread", "done": true,
     "done_at": "2026-11-11T21:03:00+00:00", "position": 5,
     "added_by_person_id": null, "added_by_name": null, "added_by_room": null,
     "added_via": "screen",
     "created_at": "...", "updated_at": "..."}
    """

    func testAListDecodes() throws {
        let list = try decode(listJson, as: HouseholdList.self)
        XCTAssertEqual(
            HouseholdList(
                id: 2, name: "Shopping", position: 2, openCount: 3, doneCount: 1,
                createdAt: "2026-11-11T20:55:21.123+00:00", updatedAt: "2026-11-11T21:03:00+00:00"
            ),
            list
        )
        XCTAssertEqual([list], try decode("[\(listJson)]", as: [HouseholdList].self))
    }

    func testAnItemDecodesWithAndWithoutWhoAddedIt() throws {
        let voice = try decode(voiceItemJson, as: HouseholdListItem.self)
        XCTAssertEqual(41, voice.id)
        XCTAssertEqual(2, voice.listID)
        XCTAssertEqual("Milk", voice.text)
        XCTAssertFalse(voice.done)
        XCTAssertNil(voice.doneAt)
        XCTAssertEqual(4, voice.position)
        XCTAssertEqual(1, voice.addedByPersonID)
        XCTAssertEqual("Alex", voice.addedByName)
        XCTAssertEqual("kitchen", voice.addedByRoom)
        XCTAssertEqual("voice", voice.addedVia)

        let screen = try decode(screenItemJson, as: HouseholdListItem.self)
        XCTAssertTrue(screen.done)
        XCTAssertEqual("2026-11-11T21:03:00+00:00", screen.doneAt)
        XCTAssertNil(screen.addedByPersonID)
        XCTAssertNil(screen.addedByName)
        XCTAssertNil(screen.addedByRoom)
        XCTAssertEqual("screen", screen.addedVia)
    }

    func testTheDetailWrapperDecodes() throws {
        let detail = try decode(
            #"{"list": \#(listJson), "items": [\#(voiceItemJson), \#(screenItemJson)]}"#,
            as: HouseholdListDetail.self
        )
        XCTAssertEqual("Shopping", detail.list.name)
        XCTAssertEqual([41, 42], detail.items.map(\.id))
    }

    func testTheAddAnswerDecodesWithEachOutcome() throws {
        for outcome in ["added", "reopened", "already"] {
            let added = try decode(
                #"{"item": \#(voiceItemJson), "outcome": "\#(outcome)"}"#,
                as: ListItemAdded.self
            )
            XCTAssertEqual(outcome, added.outcome)
            XCTAssertEqual("Milk", added.item.text)
        }
        let milk = try decode(voiceItemJson, as: HouseholdListItem.self)
        XCTAssertNil(listsAddNote(ListItemAdded(item: milk, outcome: "added")))
        XCTAssertEqual("\"Milk\" is already on the list", listsAddNote(ListItemAdded(item: milk, outcome: "already")))
        XCTAssertEqual("\"Milk\" is back on the list", listsAddNote(ListItemAdded(item: milk, outcome: "reopened")))
        XCTAssertEqual(3, try decode(#"{"removed": 3}"#, as: ListsCleared.self).removed)
    }

    /// A required field gone from the server's answer fails the read rather
    /// than drawing a blank row.
    func testAListMissingItsNameIsNotDrawnBlank() {
        XCTAssertThrowsError(try decode(#"{"id": 2, "position": 2}"#, as: HouseholdList.self))
        XCTAssertThrowsError(try decode(#"{"id": 41, "list_id": 2}"#, as: HouseholdListItem.self))
        XCTAssertThrowsError(try decode(#"{"items": []}"#, as: HouseholdListDetail.self))
    }

    // MARK: - §7.2 errors

    /// The shape ``DomovoiClient`` throws for a non-2xx that is not 401/403,
    /// 429 or 5xx — the body as the server sent it.
    private func http(_ status: Int, _ body: String) -> DomovoiError {
        let failure = ApiFailure(status: status, message: "\(status): \(body)", body: body)
        switch status {
        case 401, 403: return .refused(failure)
        case 500...599: return .serverTrouble(failure)
        default: return .httpError(failure)
        }
    }

    func testA409SaysTheServersMessageAndKeepsItsCode() {
        let taken = listsFailure(
            "make list",
            http(409, #"{"detail": {"code": "list_name_taken", "message": "there's already a list called Shopping"}}"#)
        )
        XCTAssertEqual("there's already a list called Shopping", taken.message)
        XCTAssertEqual("list_name_taken", taken.code)
        XCTAssertFalse(taken.gone)
        XCTAssertEqual(
            "list_full",
            listsFailure("add", http(409, #"{"detail": {"code": "list_full", "message": "that list is full"}}"#)).code
        )
        // The body read off the message when the failure carries none.
        let bare = DomovoiError.httpError(ApiFailure(
            status: 409,
            message: #"409: {"detail": {"code": "too_many_lists", "message": "a household can keep up to 50 lists"}}"#
        ))
        XCTAssertEqual("a household can keep up to 50 lists", listsFailure("make list", bare).message)
        XCTAssertEqual("too_many_lists", listsFailure("make list", bare).code)
    }

    func testA404IsQuietAndMeansReRead() {
        let gone = listsFailure("save", http(404, #"{"detail": "item 9 not found on list 2"}"#))
        XCTAssertNil(gone.message)
        XCTAssertTrue(gone.gone)
    }

    func testA422GivesAMessageFromAStringOrPydanticsList() {
        XCTAssertEqual(
            "text can't be empty",
            listsFailure("add", http(422, #"{"detail": "text can't be empty"}"#)).message
        )
        let pydantic = """
        {"detail": [{"type": "string_too_long", "loc": ["body", "text"],
            "msg": "String should have at most 200 characters", "input": "x", "ctx": {"max_length": 200}}]}
        """
        XCTAssertEqual("String should have at most 200 characters", listsFailure("add", http(422, pydantic)).message)
        XCTAssertEqual("no fields provided", listsFailure("save", http(400, #"{"detail": "no fields provided"}"#)).message)
    }

    func testOtherFailuresUseTheAppsUsualLine() {
        XCTAssertEqual("add failed (offline?)", listsFailure("add", .unreachable("reset")).message)
        XCTAssertEqual("add failed: 500: oops", listsFailure("add", http(500, "oops")).message)
        // A 409 with no words of its own still says something.
        XCTAssertEqual("add failed: 409: {}", listsFailure("add", http(409, "{}")).message)
        XCTAssertTrue(listsRefused(http(401, "{}")))
        XCTAssertTrue(listsRefused(http(403, "{}")))
        XCTAssertFalse(listsRefused(http(404, "{}")))
        XCTAssertFalse(listsRefused(.unreachable("reset")))
    }

    // MARK: - §7.3 order

    private func item(_ id: Int64, _ position: Int, done: Bool = false) -> HouseholdListItem {
        HouseholdListItem(id: id, listID: 2, text: "i\(id)", done: done, position: position)
    }

    /// Server order is (position, id): 12, 9 (crossed off), 15, 20.
    private var items: [HouseholdListItem] {
        [item(15, 3), item(9, 2, done: true), item(12, 1), item(20, 3)]
    }

    func testTheServersOrderIsPositionThenId() {
        XCTAssertEqual([12, 9, 15, 20], listsInOrder(items).map(\.id))
    }

    func testMovingAnItemSendsTheOpenItemsOnlyInTheirNewOrder() {
        let sorted = listsInOrder(items)
        XCTAssertEqual([20, 12, 15], listsOpenOrderWithMove(sorted, id: 20, toIndex: 0))
        XCTAssertEqual([15, 20, 12], listsOpenOrderWithMove(sorted, id: 12, toIndex: 99))
        XCTAssertNil(listsOpenOrderWithMove(sorted, id: 9, toIndex: 0), "a crossed-off item is not movable")
        XCTAssertEqual([15, 12, 20], listsOpenOrderStep(sorted, id: 15, delta: -1))
        XCTAssertEqual([12, 20, 15], listsOpenOrderStep(sorted, id: 15, delta: 1))
        XCTAssertNil(listsOpenOrderStep(sorted, id: 12, delta: -1))
        XCTAssertNil(listsOpenOrderStep(sorted, id: 20, delta: 1))
        for order in [listsOpenOrderWithMove(sorted, id: 20, toIndex: 0), listsOpenOrderStep(sorted, id: 15, delta: -1)] {
            XCTAssertFalse(order?.contains(9) ?? true, "a crossed-off item is in the order sent")
        }
    }

    func testADragSendsTheOpenOrderAndARefreshWaitsForTheDrop() {
        var board = ListBoard(items)
        board.startDrag(20)
        XCTAssertTrue(board.dragPast(15))
        XCTAssertTrue(board.dragPast(12))
        XCTAssertEqual([20, 12, 15], board.open.map(\.id))
        // A voice add lands mid-drag: held back, the rows don't move.
        board.serverSays(items + [item(30, 9)])
        XCTAssertEqual([20, 12, 15], board.open.map(\.id))
        XCTAssertEqual([20, 12, 15], board.endDrag())
        XCTAssertFalse(board.dragging)
        XCTAssertEqual([20, 12, 15, 9], board.items.map(\.id))
        // Not moved: the held-back read is taken at the drop.
        board.startDrag(12)
        board.serverSays(items + [item(30, 9)])
        XCTAssertNil(board.endDrag())
        XCTAssertEqual([12, 15, 20, 30], board.open.map(\.id))
        // A crossed-off item can't be dragged.
        board.startDrag(9)
        XCTAssertFalse(board.dragging)
    }

    // MARK: - §7.4 who-line

    func testTheWhoLine() throws {
        let voice = HouseholdListItem(id: 1, listID: 2, text: "i1", position: 1, addedVia: "voice")
        func with(name: String? = nil, room: String? = nil, via: String = "voice") -> HouseholdListItem {
            HouseholdListItem(
                id: voice.id, listID: voice.listID, text: voice.text, position: 1,
                addedByName: name, addedByRoom: room, addedVia: via
            )
        }
        XCTAssertEqual("Alex · kitchen", listsWhoLine(with(name: "Alex", room: "kitchen")))
        XCTAssertEqual("by voice · kitchen", listsWhoLine(with(room: "kitchen")))
        XCTAssertEqual("Alex", listsWhoLine(with(name: "Alex")))
        XCTAssertEqual("by voice", listsWhoLine(voice))
        XCTAssertEqual("by voice · kitchen", listsWhoLine(with(name: " ", room: "kitchen")), "a blank name is no name")
        XCTAssertNil(listsWhoLine(with(name: "Alex", room: "kitchen", via: "screen")))
        XCTAssertEqual("Alex · kitchen", listsWhoLine(try decode(voiceItemJson, as: HouseholdListItem.self)))
        XCTAssertNil(listsWhoLine(try decode(screenItemJson, as: HouseholdListItem.self)))
    }

    func testTheWording() throws {
        XCTAssertEqual("to-do, shopping and your own", ListsStrings.headerSub([]))
        let list = try decode(listJson, as: HouseholdList.self)
        XCTAssertEqual("1 list · 3 things to go", ListsStrings.headerSub([list]), "1 list, not the dashboard's 1 lists")
        let other = HouseholdList(id: 3, name: "To-do", openCount: 1)
        XCTAssertEqual("2 lists · 4 things to go", ListsStrings.headerSub([list, other]))
        XCTAssertEqual("1 thing to go", ListsStrings.cardSub(open: 1, done: 0))
        XCTAssertEqual("2 things to go · 1 crossed off", ListsStrings.cardSub(open: 2, done: 1))
        XCTAssertEqual("add to shopping", ListsStrings.addPlaceholder("Shopping"))
        XCTAssertEqual("type above, or say \"add milk to the camping list\"", ListsStrings.emptyListHint("Camping"))
        XCTAssertEqual("Delete Camping?", ListsStrings.deleteListTitle("Camping"))
        XCTAssertEqual(
            "The list and the 3 things on it go for good. This can't be undone.",
            ListsStrings.deleteListBody(3)
        )
        XCTAssertEqual("The list goes for good. This can't be undone.", ListsStrings.deleteListBody(0))
        XCTAssertEqual("removed 1 thing", ListsStrings.removed(1))
        XCTAssertEqual("crossed off · 2", ListsStrings.crossedOffHeading(2))
        XCTAssertEqual(list, listsSelected([HouseholdList(id: 1, name: "To-do"), list], savedID: 2))
        XCTAssertEqual(1, listsSelected([HouseholdList(id: 1, name: "To-do"), list], savedID: 99)?.id)
        XCTAssertNil(listsSelected([], savedID: 2))
    }

    // MARK: - the selection, per server

    func testTheSelectionIsKeptPerServer() {
        let box = SavedStateBox()
        let selection = ListsSelection(place: box)
        selection.save(server: "http://10.0.0.42:6369", id: 7)
        selection.save(server: "http://10.0.0.99:6369", id: 2)
        XCTAssertEqual(7, selection.read(server: "http://10.0.0.42:6369"))
        XCTAssertEqual(2, ListsSelection(place: box).read(server: "http://10.0.0.99:6369"), "it survives a relaunch")
        XCTAssertNil(selection.read(server: "http://10.0.0.7:6369"))
        selection.save(server: "http://10.0.0.42:6369", id: nil)
        XCTAssertNil(selection.read(server: "http://10.0.0.42:6369"))
        XCTAssertEqual(2, selection.read(server: "http://10.0.0.99:6369"), "forgetting one house's choice kept the other's")
    }
}
