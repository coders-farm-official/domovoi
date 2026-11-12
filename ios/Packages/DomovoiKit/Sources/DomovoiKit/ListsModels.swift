import Foundation

// The Lists screen's shapes, words, order rules and error mapping — a port of
// `android/.../ui/screens/lists/ListsModels.kt` (Android 3a3b6422), which
// mirrors web/backend/schemas.py `HouseholdList` / `HouseholdListItem` /
// `HouseholdListDetail` / `ListItemAdded` / `ListsCleared` (V048,
// web/backend/api/lists.py) and the dashboard's web/static/lists.jsx. The
// contract is design-notes/LISTS-MOBILE-HANDOFF.md §2.
//
// The fields every answer carries have no default (`id`, `name`; an item's
// `id`, `list_id`, `text`; the detail's `list`; the add answer's `item`), so
// a change to the server's shape fails the decoder tests rather than drawing
// a blank row — the Kotlin's rule (`ListsModels.kt:16-22`).

// MARK: - the shapes

/// One list, with its counts (`ListsModels.kt:24-33`).
public struct HouseholdList: Decodable, Equatable, Hashable, Identifiable, Sendable {
    public let id: Int64
    public let name: String
    public let position: Int
    public let openCount: Int
    public let doneCount: Int
    public let createdAt: String?
    public let updatedAt: String?

    public init(
        id: Int64,
        name: String,
        position: Int = 0,
        openCount: Int = 0,
        doneCount: Int = 0,
        createdAt: String? = nil,
        updatedAt: String? = nil
    ) {
        self.id = id
        self.name = name
        self.position = position
        self.openCount = openCount
        self.doneCount = doneCount
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, name, position
        case openCount = "open_count"
        case doneCount = "done_count"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        position = try c.decodeIfPresent(Int.self, forKey: .position) ?? 0
        openCount = try c.decodeIfPresent(Int.self, forKey: .openCount) ?? 0
        doneCount = try c.decodeIfPresent(Int.self, forKey: .doneCount) ?? 0
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

/// One thing on a list (`ListsModels.kt:35-50`). `addedVia` is `"voice"` or
/// `"screen"`; the three `addedBy…` are set only for a voice add.
public struct HouseholdListItem: Decodable, Equatable, Hashable, Identifiable, Sendable {
    public let id: Int64
    public let listID: Int64
    public var text: String
    public var done: Bool
    public let doneAt: String?
    public let position: Int
    public let addedByPersonID: Int64?
    public let addedByName: String?
    public let addedByRoom: String?
    public let addedVia: String
    public let createdAt: String?
    public let updatedAt: String?

    public init(
        id: Int64,
        listID: Int64,
        text: String,
        done: Bool = false,
        doneAt: String? = nil,
        position: Int = 0,
        addedByPersonID: Int64? = nil,
        addedByName: String? = nil,
        addedByRoom: String? = nil,
        addedVia: String = "screen",
        createdAt: String? = nil,
        updatedAt: String? = nil
    ) {
        self.id = id
        self.listID = listID
        self.text = text
        self.done = done
        self.doneAt = doneAt
        self.position = position
        self.addedByPersonID = addedByPersonID
        self.addedByName = addedByName
        self.addedByRoom = addedByRoom
        self.addedVia = addedVia
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, text, done, position
        case listID = "list_id"
        case doneAt = "done_at"
        case addedByPersonID = "added_by_person_id"
        case addedByName = "added_by_name"
        case addedByRoom = "added_by_room"
        case addedVia = "added_via"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        listID = try c.decode(Int64.self, forKey: .listID)
        text = try c.decode(String.self, forKey: .text)
        done = try c.decodeIfPresent(Bool.self, forKey: .done) ?? false
        doneAt = try c.decodeIfPresent(String.self, forKey: .doneAt)
        position = try c.decodeIfPresent(Int.self, forKey: .position) ?? 0
        addedByPersonID = try c.decodeIfPresent(Int64.self, forKey: .addedByPersonID)
        addedByName = try c.decodeIfPresent(String.self, forKey: .addedByName)
        addedByRoom = try c.decodeIfPresent(String.self, forKey: .addedByRoom)
        addedVia = try c.decodeIfPresent(String.self, forKey: .addedVia) ?? "screen"
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

/// `GET /api/lists/{id}`: the list and ALL its items, open and crossed off,
/// in list order (`ListsModels.kt:52-56`).
public struct HouseholdListDetail: Decodable, Equatable, Sendable {
    public let list: HouseholdList
    public let items: [HouseholdListItem]

    public init(list: HouseholdList, items: [HouseholdListItem] = []) {
        self.list = list
        self.items = items
    }

    enum CodingKeys: String, CodingKey { case list, items }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        list = try c.decode(HouseholdList.self, forKey: .list)
        items = try c.decodeIfPresent([HouseholdListItem].self, forKey: .items) ?? []
    }
}

/// `POST /api/lists/{id}/items`: the item and what happened to it —
/// `"added"`, `"reopened"` or `"already"` (`ListsModels.kt:58-65`).
public struct ListItemAdded: Decodable, Equatable, Sendable {
    public let item: HouseholdListItem
    public let outcome: String

    public init(item: HouseholdListItem, outcome: String = "added") {
        self.item = item
        self.outcome = outcome
    }

    enum CodingKeys: String, CodingKey { case item, outcome }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        item = try c.decode(HouseholdListItem.self, forKey: .item)
        outcome = try c.decodeIfPresent(String.self, forKey: .outcome) ?? "added"
    }
}

/// `POST /api/lists/{id}/clear-done`: how many went (`ListsModels.kt:67-68`).
public struct ListsCleared: Decodable, Equatable, Sendable {
    public let removed: Int

    public init(removed: Int = 0) { self.removed = removed }

    enum CodingKeys: String, CodingKey { case removed }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        removed = try c.decodeIfPresent(Int.self, forKey: .removed) ?? 0
    }
}

// MARK: - the words (web/static/lists.jsx, `ListsModels.kt:76-123`)

/// Every word the Lists screen draws, Android's exactly.
public enum ListsStrings {
    public static let title = "Lists"

    /// "1 thing", "3 things".
    public static func things(_ n: Int) -> String { n == 1 ? "1 thing" : "\(n) things" }

    /// The header's sub-line: "1 list · 3 things to go" — `1 list`, not the
    /// dashboard's `1 lists` (`ListsModels.kt:81-86`).
    public static func headerSub(_ lists: [HouseholdList]) -> String {
        if lists.isEmpty { return "to-do, shopping and your own" }
        let total = lists.reduce(0) { $0 + $1.openCount }
        let n = lists.count == 1 ? "1 list" : "\(lists.count) lists"
        return "\(n) · \(things(total)) to go"
    }

    /// The open list's sub-line: "2 things to go · 1 crossed off".
    public static func cardSub(open: Int, done: Int) -> String {
        "\(things(open)) to go" + (done > 0 ? " · \(done) crossed off" : "")
    }

    public static func addPlaceholder(_ listName: String) -> String { "add to \(listName.lowercased())" }

    public static func emptyListHint(_ listName: String) -> String {
        "type above, or say \"add milk to the \(listName.lowercased()) list\""
    }

    public static func deleteListTitle(_ name: String) -> String { "Delete \(name)?" }

    public static func deleteListBody(_ itemCount: Int) -> String {
        (itemCount > 0 ? "The list and the \(things(itemCount)) on it go for good." : "The list goes for good.")
            + " This can't be undone."
    }

    public static let deleteListConfirm = "Delete list"
    public static let cancelDialog = "Cancel"

    public static let pairedOnlyTitle = "paired devices only"
    public static let pairedOnlyBody = "pair this phone, or sign in, to read the household's lists"
    public static let noListsTitle = "no lists yet"
    public static let noListsBody = "make one here, or say \"make a list called camping\""
    public static let emptyListTitle = "nothing on this list"
    public static let allCrossedOff = "everything here is crossed off"
    public static let requestFailed = "request failed"
    public static let retry = "retry"

    public static let newList = "new list"
    public static let newListName = "new list name"
    public static let listNamePlaceholder = "list name"
    public static let listName = "list name"
    public static let make = "make"
    public static let cancel = "cancel"
    public static let save = "save"
    public static let add = "add"
    public static let renameList = "rename list"
    public static let deleteList = "delete list"
    public static let itemText = "item text"
    public static let changeTheWords = "change the words"
    public static let clearCrossedOff = "clear crossed off"
    public static let moveUp = "move up"
    public static let moveDown = "move down"
    public static let fold = "fold"
    public static let unfold = "unfold"
    public static let expanded = "expanded"
    public static let collapsed = "collapsed"
    public static let toGo = "to go"
    public static let crossedOffState = "crossed off"

    public static func crossedOffHeading(_ n: Int) -> String { "crossed off · \(n)" }
    public static func removed(_ n: Int) -> String { "removed \(things(n))" }
    public static func deleted(_ name: String) -> String { "deleted \(name)" }
    public static func addTo(_ name: String) -> String { "add to \(name)" }
    public static func move(_ text: String) -> String { "move \(text)" }
    public static func crossOff(_ text: String) -> String { "cross off \(text)" }
    public static func putBack(_ text: String) -> String { "put \(text) back on the list" }
    public static func remove(_ text: String) -> String { "remove \(text)" }

    /// The action words `failureText` is given (`ListsScreen.kt`).
    public enum Action {
        public static let makeList = "make list"
        public static let rename = "rename"
        public static let delete = "delete"
        public static let add = "add"
        public static let save = "save"
        public static let move = "move"
        public static let clear = "clear"
    }
}

/// Who added an item, when the server knows: "Alex · kitchen", "by voice ·
/// garage", "Alex"; nil for an item added on a screen (`ListsModels.kt:93-100`).
public func listsWhoLine(_ item: HouseholdListItem) -> String? {
    guard item.addedVia == "voice" else { return nil }
    let name = item.addedByName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let who = name.isEmpty ? "by voice" : (item.addedByName ?? "")
    let room = item.addedByRoom?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return room.isEmpty ? who : "\(who) · \(item.addedByRoom ?? "")"
}

/// The short note an add answers with, or nil for a plain `added`
/// (`ListsModels.kt:102-107`).
public func listsAddNote(_ added: ListItemAdded) -> String? {
    switch added.outcome {
    case "already": return "\"\(added.item.text)\" is already on the list"
    case "reopened": return "\"\(added.item.text)\" is back on the list"
    default: return nil
    }
}

// MARK: - order and selection (`ListsModels.kt:113-150`)

/// The server's order: position, then id (gaps are normal).
public func listsInOrder(_ items: [HouseholdListItem]) -> [HouseholdListItem] {
    items.sorted { ($0.position, $0.id) < ($1.position, $1.id) }
}

/// The list shown: the one this phone last picked, else the first.
public func listsSelected(_ lists: [HouseholdList], savedID: Int64?) -> HouseholdList? {
    lists.first { $0.id == savedID } ?? lists.first
}

/// The open items' ids with `id` moved to `toIndex` (among the open items):
/// the `item_ids` of `PUT …/items/order`. Crossed-off items are never in it.
/// Nil when `id` is not an open item.
public func listsOpenOrderWithMove(_ items: [HouseholdListItem], id: Int64, toIndex: Int) -> [Int64]? {
    var ids = items.filter { !$0.done }.map(\.id)
    guard let from = ids.firstIndex(of: id) else { return nil }
    ids.remove(at: from)
    ids.insert(id, at: min(max(toIndex, 0), ids.count))
    return ids
}

/// "move up" (-1) / "move down" (+1) for `id`, or nil at the end it can't
/// pass — the accessibility actions; the dashboard's arrow keys.
public func listsOpenOrderStep(_ items: [HouseholdListItem], id: Int64, delta: Int) -> [Int64]? {
    let open = items.filter { !$0.done }.map(\.id)
    guard let from = open.firstIndex(of: id) else { return nil }
    let to = from + delta
    guard to >= 0, to < open.count else { return nil }
    return listsOpenOrderWithMove(items, id: id, toIndex: to)
}

/// The open list's items as the screen draws them — `ListBoard`
/// (`ListsModels.kt:152-237`).
///
/// A re-read lands here (``serverSays(_:)``); while a drag is under way it is
/// **held back**, not applied — a voice add arriving mid-drag would otherwise
/// reorder the rows under the finger — and the drop decides: a moved order is
/// sent and the screen re-reads; an unmoved one takes what was held back.
public struct ListBoard: Equatable, Sendable {
    public private(set) var items: [HouseholdListItem]
    /// The open ids in their dragged order while a drag is under way.
    public private(set) var dragOrder: [Int64]?
    public private(set) var dragID: Int64?
    private var heldBack: [HouseholdListItem]?

    public init(_ initial: [HouseholdListItem] = []) {
        items = listsInOrder(initial)
    }

    public var dragging: Bool { dragOrder != nil }

    public var open: [HouseholdListItem] {
        let all = items.filter { !$0.done }
        guard let order = dragOrder else { return all }
        return order.compactMap { id in all.first { $0.id == id } }
    }

    public var done: [HouseholdListItem] { items.filter(\.done) }

    public mutating func serverSays(_ server: [HouseholdListItem]) {
        if dragging { heldBack = server } else { items = listsInOrder(server) }
    }

    /// An optimistic change (cross off, new words, removed).
    public mutating func change(_ f: ([HouseholdListItem]) -> [HouseholdListItem]) {
        items = f(items)
    }

    public mutating func startDrag(_ id: Int64) {
        guard items.contains(where: { $0.id == id && !$0.done }) else { return }
        dragOrder = items.filter { !$0.done }.map(\.id)
        dragID = id
    }

    /// The dragged row passes `targetID`: they swap places.
    @discardableResult
    public mutating func dragPast(_ targetID: Int64) -> Bool {
        guard var order = dragOrder, let dragged = dragID,
              let a = order.firstIndex(of: dragged), let b = order.firstIndex(of: targetID), a != b
        else { return false }
        order.remove(at: a)
        order.insert(dragged, at: b)
        dragOrder = order
        return true
    }

    /// The drop: the new open order to send, or nil when nothing moved (then
    /// what a re-read brought meanwhile is applied now).
    public mutating func endDrag() -> [Int64]? {
        guard let order = dragOrder else { return nil }
        let before = items.filter { !$0.done }.map(\.id)
        dragOrder = nil
        dragID = nil
        let held = heldBack
        heldBack = nil
        if order == before {
            if let held { items = listsInOrder(held) }
            return nil
        }
        applyOrder(order)
        return order
    }

    /// Open items in `order` (the ones it doesn't name after them, in place),
    /// then the crossed-off ones: what the server will answer.
    public mutating func applyOrder(_ order: [Int64]) {
        let open = items.filter { !$0.done }
        let named = order.compactMap { id in open.first { $0.id == id } }
        items = named + open.filter { !order.contains($0.id) } + items.filter(\.done)
    }
}

// MARK: - errors (`ListsModels.kt:264-312`; hand-off §2.3)

/// What the screen does about a failed write: `message` to show (nil: say
/// nothing), the 409's `code`, and `gone` for a 404 (re-read; the list or
/// item went elsewhere). Every write re-reads afterwards either way.
public struct ListsFailure: Equatable, Sendable {
    public let message: String?
    public let code: String?
    public let gone: Bool

    public init(message: String?, code: String? = nil, gone: Bool = false) {
        self.message = message
        self.code = code
        self.gone = gone
    }
}

/// The words of a FastAPI `detail`: a sentence; `{code, message}` (the 409s);
/// pydantic's list of `{msg}`. Nil when the body has none.
public func listsDetail(_ body: String?) -> (message: String, code: String?)? {
    guard let data = (body ?? "").data(using: .utf8),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let detail = object["detail"]
    else { return nil }
    if let sentence = detail as? String {
        return sentence.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : (sentence, nil)
    }
    if let coded = detail as? [String: Any] {
        guard let message = coded["message"] as? String,
              !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return (message, coded["code"] as? String)
    }
    if let entries = detail as? [Any] {
        for entry in entries {
            if let msg = (entry as? [String: Any])?["msg"] as? String,
               !msg.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return (msg, nil)
            }
        }
    }
    return nil
}

/// A failed write, for the screen. `404`: the list or item went elsewhere
/// (deleted on another phone, by voice) — nothing alarming is said and the
/// screen re-reads. `400`/`409`/`422`: the server's own sentence ("there's
/// already a list called Shopping"), with the 409's code. Anything else: the
/// app's usual failure line (``failureText(_:_:)``).
public func listsFailure(_ action: String, _ error: DomovoiError) -> ListsFailure {
    if let failure = error.failure {
        if failure.status == 404 { return ListsFailure(message: nil, gone: true) }
        if [400, 409, 422].contains(failure.status) {
            // Android's `e.body.ifBlank { message after ": " }`.
            let body = failure.body.isEmpty
                ? String(failure.message.drop(while: { $0 != ":" }).dropFirst(2))
                : failure.body
            guard let detail = listsDetail(body) else {
                return ListsFailure(message: failureText(action, error))
            }
            return ListsFailure(message: detail.message, code: detail.code)
        }
    }
    return ListsFailure(message: failureText(action, error))
}

/// A read refused for want of the household token: the paired-only state.
public func listsRefused(_ error: DomovoiError) -> Bool {
    guard let status = error.failure?.status else { return false }
    return status == 401 || status == 403
}

// MARK: - which list this phone last had open

/// Which list this phone last had open, **per server** (list ids are a
/// server's own) — `ListsSelection` (`ListsData.kt:76-94`). A convenience:
/// lost, it falls back to the first list.
///
/// One blob, a JSON object from the normalised server address to the id,
/// under its own key — a second key under the shell's prefix, as
/// ``ChatPinBook`` keeps its pins.
public struct ListsSelection: Sendable {

    public static let key = "domovoi.lists.selected"

    private let place: any SavedState

    public init(place: any SavedState = UserDefaultsSavedState(key: ListsSelection.key)) {
        self.place = place
    }

    private func all() -> [String: Int64] {
        guard let blob = place.read(),
              let map = try? JSONDecoder().decode([String: Int64].self, from: blob)
        else { return [:] }
        return map
    }

    public func read(server: String) -> Int64? {
        guard let id = all()[ServerCredentials.normalize(server)], id > 0 else { return nil }
        return id
    }

    public func save(server: String, id: Int64?) {
        var map = all()
        map[ServerCredentials.normalize(server)] = id
        guard let blob = try? JSONEncoder().encode(map) else { return }
        place.write(blob)
    }
}
