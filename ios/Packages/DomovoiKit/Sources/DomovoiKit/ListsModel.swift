import Foundation
import Observation
import Synchronization

/// `GET /api/lists`, or the paired-only state: no household token, or the
/// server refused it (`ListsData.kt:11-16`).
public enum ListsRead: Equatable, Sendable {
    case loaded([HouseholdList])
    case refused
}

/// `GET /api/lists/{id}` for the list on screen, that the list is gone
/// (deleted on another phone, or by voice, while it was open here), or
/// nothing to read — no list selected (`ListsData.kt:18-23`).
///
/// A loaded answer carries its own list, so which list the items belong to is
/// read off the answer and never off the selection after the `await`.
public enum ListDetailRead: Equatable, Sendable {
    case none
    case loaded(HouseholdListDetail)
    case gone(Int64)
}

/// The confirm alert's subject: the list, how many things go with it, and the
/// house it was asked in — ``CalendarModel``'s `deleteAskedServer`, carried
/// with the target.
public struct ListsDeleteAsk: Equatable, Sendable {
    public let list: HouseholdList
    public let itemCount: Int
    let server: String
}

/// The Lists screen's state, its two reads and every write — `ListsScreen.kt`
/// and `ListsData.kt` (Android 3a3b6422) as a type, in ``CalendarModel``'s
/// shape: Calendar is the other household-shared screen, with the same live
/// socket event and the same paired-only tier.
///
/// ### The reads
///
/// Two ``ApiRead``s, both re-read on `lists.changed` (``eventTypes``; its
/// payload is a digest and is never decoded) and when the screen comes back
/// to the foreground (``cameBack()``): the lists, and the open one with its
/// items. Which list is open is ``listsSelected(_:savedID:)`` of the lists and
/// the id this phone last picked for **this server** (``ListsSelection``).
/// An unpaired phone reads nothing: both reads answer the paired-only state
/// without a request, as Android's `rememberListsData(paired = false)` does.
///
/// ### Every answer is keyed by its server
///
/// The top defect class on this track is a value read after an `await` that
/// belongs to another house. Both reads run **on the main actor** and compare
/// the server they asked with the server saved after the `await`, in one
/// main-actor step with ``ApiRead``'s write (the 2026-11-09 rooms history
/// pattern); a reply from the house the phone has left is dropped. Every
/// write captures the server before it sends and compares it before it
/// touches anything (``write(_:onOk:onFail:_:)``), and the board the items are
/// drawn from is keyed by server **and** list, so list 2 of one house never
/// receives list 2 of another's rows.
@MainActor
@Observable
public final class ListsModel {

    /// The one event this screen listens for: `lists_changed` → topic
    /// `lists` → `"lists.changed"` (web/backend/realtime.py).
    public static let eventTypes: Set<String> = ["lists.changed"]

    // MARK: - the reads

    public let lists: ApiRead<ListsRead>
    public let detail: ApiRead<ListDetailRead>

    /// The list ``detail`` reads, shared with its `@Sendable` fetch closure.
    private let reading: ListsIDBox

    // MARK: - what is on screen

    /// The list on screen, or nil before the lists have come back or when
    /// there are none.
    public private(set) var selectedID: Int64?

    /// The open list's items as drawn (``ListBoard``): optimistic changes
    /// first, a re-read held back mid-drag.
    public private(set) var board = ListBoard()

    /// The add box. A fresh one per list and per server, as Android's
    /// `remember(key) { ListAddField() }`.
    public private(set) var field = ListAddField()

    /// The item being edited in place, the rename field, and whether the
    /// crossed-off section is open — each per list, reset with the board.
    public var editingID: Int64?
    public var renaming = false
    public var showDone = true

    /// What the last write said. Read and cleared by the screen
    /// (``takeToast()``).
    public private(set) var toast: String?

    /// The confirm alert's subject, or nil.
    public private(set) var deleteTarget: ListsDeleteAsk?

    // MARK: - what it was built from

    private let container: AppContainer
    private let selection: ListsSelection
    @ObservationIgnored private var savedID: Int64?
    @ObservationIgnored private var savedFor: String?
    @ObservationIgnored private var boardKey: String?
    /// What the server last said about the open list's items — the board goes
    /// back to it when an optimistic change fails.
    @ObservationIgnored private var lastRead: [HouseholdListItem] = []
    @ObservationIgnored private var eventSink: Int?

    public init(container: AppContainer, selection: ListsSelection = ListsSelection()) {
        self.container = container
        self.selection = selection
        let store = container.store
        let client = container.client
        let reading = ListsIDBox()
        self.reading = reading
        self.lists = ApiRead(in: container, eventTypes: Self.eventTypes) { @MainActor in
            let asked = store.serverURL
            guard !ServerCredentials.isBlank(store.token(for: asked) ?? "") else { return .refused }
            let rows: [HouseholdList]
            do throws(DomovoiError) {
                rows = try await client.get("/api/lists", as: [HouseholdList].self)
            } catch {
                guard store.serverURL == asked else { throw CancellationError() }
                if listsRefused(error) { return .refused }
                throw error
            }
            // **The house it was asked of**, checked on the main actor in one
            // step with ``ApiRead``'s write: a reply from the house the phone
            // has left is dropped, never filed under the new one.
            guard store.serverURL == asked else { throw CancellationError() }
            return .loaded(rows)
        }
        self.detail = ApiRead(in: container, eventTypes: Self.eventTypes) { @MainActor in
            let asked = store.serverURL
            // The list is read off the box **before** the `await`, and the
            // answer carries its own list back.
            guard let id = reading.current,
                  !ServerCredentials.isBlank(store.token(for: asked) ?? "")
            else { return .none }
            let answer: HouseholdListDetail
            do throws(DomovoiError) {
                answer = try await client.get("/api/lists/\(id)", as: HouseholdListDetail.self)
            } catch {
                guard store.serverURL == asked else { throw CancellationError() }
                if error.failure?.status == 404 { return .gone(id) }
                throw error
            }
            guard store.serverURL == asked else { throw CancellationError() }
            return .loaded(answer)
        }
        // Last, because they capture `self`.
        self.lists.onValue = { [weak self] in self?.listsLanded() }
        self.detail.onValue = { [weak self] in self?.detailLanded() }
    }

    // MARK: - lifecycle

    /// The screen appeared: join the push registry and read.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        rememberSelection()
        lists.refresh()
        if selectedID != nil { detail.refresh() }
    }

    /// The screen is gone: the reads stop and coming back re-reads.
    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        lists.stop()
        detail.stop()
    }

    /// A frame from `/ws/state`. Only its type is read: `lists.changed`
    /// re-reads the lists **and** the open list (hand-off §2.4, §7.6).
    public func received(_ event: StateEvent) {
        lists.received(event)
        detail.received(event)
    }

    /// Back in the foreground: the socket may have dropped meanwhile
    /// (`ListsScreen.kt:134-139`).
    public func cameBack() {
        refreshAll()
    }

    /// Both reads again: after every write, on `lists.changed`, and when the
    /// screen comes back (`ListsData.refreshAll`).
    public func refreshAll() {
        lists.refresh()
        detail.refresh()
    }

    /// The saved server changed. Everything chosen in **this** house goes: a
    /// list id, a pending delete and the board all name ids that belong to
    /// the house the phone has left, and `2` exists in both.
    public func serverChanged() {
        savedFor = nil
        rememberSelection()
        setSelected(nil, read: false)
        boardKey = nil
        board = ListBoard()
        field = ListAddField()
        lastRead = []
        editingID = nil
        renaming = false
        showDone = true
        deleteTarget = nil
        toast = nil
        lists.serverChanged()
        detail.serverChanged()
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    // MARK: - what is drawn

    /// The lists, when they have been read (nil before, and when refused).
    public var loadedLists: [HouseholdList]? {
        if case .loaded(let rows) = lists.data { return rows }
        return nil
    }

    /// The read was refused, or never sent for want of a token.
    public var refused: Bool { lists.data == .refused }

    /// The selected list as the lists read has it.
    public var selected: HouseholdList? {
        loadedLists?.first { $0.id == selectedID }
    }

    /// The open list's own answer, when it is the selected list's.
    public var openDetail: HouseholdListDetail? {
        guard case .loaded(let answer) = detail.data, answer.list.id == selectedID, selectedID != nil
        else { return nil }
        return answer
    }

    /// The header's sub-line, once the lists are in.
    public var headerSub: String? { loadedLists.map(ListsStrings.headerSub) }

    // MARK: - choosing a list

    /// A chip, or nil for "the first one" (after a delete, or a list gone).
    public func select(_ id: Int64?) {
        savedID = id
        selection.save(server: container.store.serverURL, id: id)
        reselect()
    }

    private func rememberSelection() {
        let server = container.store.serverURL
        guard savedFor != server else { return }
        savedFor = server
        savedID = selection.read(server: server)
    }

    private func reselect() {
        let next = loadedLists.flatMap { listsSelected($0, savedID: savedID)?.id }
        if next != selectedID { setSelected(next, read: true) }
    }

    private func setSelected(_ id: Int64?, read: Bool) {
        selectedID = id
        reading.set(id)
        if read { detail.refresh() }
    }

    private func listsLanded() {
        rememberSelection()
        reselect()
    }

    /// The open list landed: onto its board (a new board for a new list or a
    /// new house), or held back until a drag's drop. A list deleted elsewhere
    /// while open here drops back to the first and re-reads
    /// (`ListsScreen.kt:125-132`, hand-off §6).
    private func detailLanded() {
        switch detail.data {
        case .loaded(let answer) where answer.list.id == selectedID:
            let key = "\(container.store.serverURL)#\(answer.list.id)"
            lastRead = answer.items
            if key != boardKey {
                boardKey = key
                board = ListBoard(answer.items)
                field = ListAddField()
                editingID = nil
                renaming = false
                showDone = true
            } else {
                board.serverSays(answer.items)
            }
        case .gone(let id) where id == selectedID:
            select(nil)
            lists.refresh()
        default:
            break
        }
    }

    // MARK: - the writes

    /// One write — `write` (`ListsScreen.kt:141-168`). **Keyed by the server
    /// it was for**: an answer that lands after the phone switched servers is
    /// dropped, not shown against the new one. `onFail` runs before the
    /// re-read (an optimistic change undone), and every write re-reads.
    private func write<T: Sendable>(
        _ action: String,
        onOk: @escaping @MainActor (T) -> Void = { _ in },
        onFail: @escaping @MainActor () -> Void = {},
        _ call: @escaping @Sendable () async throws -> T
    ) {
        let asked = container.store.serverURL
        Task { @MainActor [weak self] in
            // Untyped, because a trailing closure's thrown type is not
            // inferred from its context; every call in it is the client's,
            // which throws nothing but ``DomovoiError``.
            let result: Result<T, DomovoiError>
            do {
                result = .success(try await call())
            } catch let trouble as DomovoiError {
                result = .failure(trouble)
            } catch {
                result = .failure(.unreachable("\(error)"))
            }
            guard let self, self.container.store.serverURL == asked else { return }
            switch result {
            case .success(let value):
                onOk(value)
            case .failure(let error):
                onFail()
                if let message = listsFailure(action, error).message { self.toast = message }
            }
            self.refreshAll()
        }
    }

    /// The board back to what the server last said: an optimistic change
    /// that failed.
    private func undo() {
        board.serverSays(lastRead)
    }

    private static func body<E: Encodable>(_ value: E) -> Data {
        (try? JSONEncoder().encode(value)) ?? Data("{}".utf8)
    }

    /// `+ new list` → make: `POST /api/lists`, and the new list is selected
    /// from the 201's own id. `done(true)` closes the name field.
    public func makeList(_ name: String, done: @escaping @MainActor (Bool) -> Void) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let client = container.client
        let body = Self.body(["name": trimmed])
        write(ListsStrings.Action.makeList, onOk: { [weak self] (made: HouseholdList) in
            self?.select(made.id)
            done(true)
        }, onFail: { done(false) }) {
            try await client.post("/api/lists", body: body, as: HouseholdList.self)
        }
    }

    /// The rename field's save: blank or unchanged closes it without a
    /// request; otherwise `PATCH`, and a refusal (a taken name) leaves it
    /// open with the server's sentence said.
    public func rename(_ list: HouseholdList, to name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != list.name else {
            renaming = false
            return
        }
        let client = container.client
        let body = Self.body(["name": trimmed])
        write(ListsStrings.Action.rename, onOk: { [weak self] (_: HouseholdList) in
            self?.renaming = false
        }) {
            try await client.patch("/api/lists/\(list.id)", body: body, as: HouseholdList.self)
        }
    }

    /// The bin on the card: the confirm alert, never the delete itself.
    public func askToDelete(_ list: HouseholdList) {
        deleteTarget = ListsDeleteAsk(list: list, itemCount: board.items.count, server: container.store.serverURL)
    }

    /// The alert's dismissal. Leaves nothing else: the confirm takes its
    /// subject as an argument, so a dismissal that fires first in the same
    /// tap cannot turn the confirm into a no-op (``CalendarModel``'s lesson).
    public func cancelDelete() {
        deleteTarget = nil
    }

    /// `Delete list` in the alert: `DELETE /api/lists/{id}` (204, no body).
    public func confirmDelete(_ ask: ListsDeleteAsk) {
        deleteTarget = nil
        guard ask.server == container.store.serverURL else { return }
        let client = container.client
        let list = ask.list
        write(ListsStrings.Action.delete, onOk: { [weak self] (_: Void) in
            guard let self else { return }
            self.toast = ListsStrings.deleted(list.name)
            if self.savedID == list.id || self.selectedID == list.id { self.select(nil) }
        }) {
            try await client.send(method: "DELETE", path: "/api/lists/\(list.id)")
        }
    }

    /// `add` (or the keyboard's done): the field empties at once so the next
    /// item can be typed while this one travels; a failed add puts its words
    /// back if the field is still empty (``ListAddField``).
    public func add() {
        guard let list = openDetail?.list else { return }
        let client = container.client
        let asked = container.store.serverURL
        let path = "/api/lists/\(list.id)/items"
        let field = self.field
        Task { @MainActor [weak self] in
            guard let result = await field.submit({ (words: String) async throws(DomovoiError) -> ListItemAdded in
                try await client.post(path, body: Self.body(["text": words]), as: ListItemAdded.self)
            }) else { return }
            guard let self, self.container.store.serverURL == asked else { return }
            switch result {
            case .success(let added):
                if let note = listsAddNote(added) { self.toast = note }
            case .failure(let error):
                if let message = listsFailure(ListsStrings.Action.add, error).message { self.toast = message }
            }
            self.refreshAll()
        }
    }

    /// The checkbox: crossed off (or put back) at once, then `PATCH {done}`.
    public func toggle(_ item: HouseholdListItem) {
        guard let base = itemsPath else { return }
        board.change { all in all.map { x in
            var y = x
            if x.id == item.id { y.done = !item.done }
            return y
        } }
        let client = container.client
        let body = Self.body(["done": !item.done])
        write(ListsStrings.Action.save, onFail: { [weak self] in self?.undo() }) {
            try await client.send(method: "PATCH", path: "\(base)/\(item.id)", body: body)
        }
    }

    /// New words for an item, from the in-place edit. Empty or unchanged
    /// words change nothing.
    public func edit(_ item: HouseholdListItem, to text: String) {
        editingID = nil
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != item.text, let base = itemsPath else { return }
        board.change { all in all.map { x in
            var y = x
            if x.id == item.id { y.text = trimmed }
            return y
        } }
        let client = container.client
        let body = Self.body(["text": trimmed])
        write(ListsStrings.Action.save, onFail: { [weak self] in self?.undo() }) {
            try await client.send(method: "PATCH", path: "\(base)/\(item.id)", body: body)
        }
    }

    /// The row's bin: no confirm — one item is cheap to re-add.
    public func remove(_ item: HouseholdListItem) {
        guard let base = itemsPath else { return }
        board.change { all in all.filter { $0.id != item.id } }
        let client = container.client
        write(ListsStrings.Action.delete, onFail: { [weak self] in self?.undo() }) {
            try await client.send(method: "DELETE", path: "\(base)/\(item.id)")
        }
    }

    /// `PUT …/items/order` with the open items' ids in their new order.
    public func saveOrder(_ order: [Int64]) {
        guard let base = itemsPath else { return }
        board.applyOrder(order)
        let client = container.client
        let body = Self.body(["item_ids": order])
        write(ListsStrings.Action.move, onFail: { [weak self] in self?.undo() }) {
            try await client.send(method: "PUT", path: "\(base)/order", body: body)
        }
    }

    /// "move up" (-1) / "move down" (+1), the accessibility actions.
    public func step(_ item: HouseholdListItem, by delta: Int) {
        guard let order = listsOpenOrderStep(board.items, id: item.id, delta: delta) else { return }
        saveOrder(order)
    }

    public func canStep(_ item: HouseholdListItem, by delta: Int) -> Bool {
        listsOpenOrderStep(board.items, id: item.id, delta: delta) != nil
    }

    /// `clear crossed off`: no confirm (only ticked items go), and a short
    /// note of how many went.
    public func clearDone() {
        guard let list = openDetail?.list else { return }
        let client = container.client
        write(ListsStrings.Action.clear, onOk: { [weak self] (cleared: ListsCleared) in
            self?.toast = ListsStrings.removed(cleared.removed)
        }) {
            try await client.post("/api/lists/\(list.id)/clear-done", body: Data("{}".utf8), as: ListsCleared.self)
        }
    }

    // MARK: - dragging

    public func startDrag(_ id: Int64) { board.startDrag(id) }

    @discardableResult
    public func dragPast(_ id: Int64) -> Bool { board.dragPast(id) }

    /// The drop: a moved order is sent; an unmoved one takes what a re-read
    /// brought meanwhile.
    public func endDrag() {
        if let moved = board.endDrag() { saveOrder(moved) }
    }

    private var itemsPath: String? {
        openDetail.map { "/api/lists/\($0.list.id)/items" }
    }
}

/// The list the detail read will ask for, readable from its `@Sendable`
/// fetch closure — ``CalendarWindowBox``'s reason: `Mutex` is `~Copyable`, so
/// it can be a stored property but not a capture.
final class ListsIDBox: Sendable {

    private let value = Mutex<Int64?>(nil)

    var current: Int64? { value.withLock { $0 } }

    func set(_ next: Int64?) {
        value.withLock { $0 = next }
    }
}

/// The add field's words — `ListAddField` (`ListsModels.kt:239-262`).
///
/// ``submit(_:)`` empties it as soon as the add is sent, so the next item can
/// be typed while this one travels; a failed add puts its words back **only
/// if the field is still empty** — what was typed since is never overwritten.
/// The dashboard bug of 2026-11-11: fast typing dropped items.
@MainActor
@Observable
public final class ListAddField {

    public var text: String

    public init(_ initial: String = "") {
        text = initial
    }

    /// Nil when there was nothing to send.
    public func submit<T>(
        _ send: (String) async throws(DomovoiError) -> T
    ) async -> Result<T, DomovoiError>? {
        let words = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return nil }
        text = ""
        do throws(DomovoiError) {
            return .success(try await send(words))
        } catch {
            if text.isEmpty { text = words }
            return .failure(error)
        }
    }
}
