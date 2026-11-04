import Foundation
import Observation

/// The room queue tab: which room, what is in it, and the three edits.
///
/// `QueueTab.kt`'s `rememberRoomQueue` (`:104-201`) as a type. Over there the
/// selection lives in a `@Stable` class hoisted out of the lazy items for the
/// reason its own comment gives — the header scrolls out of composition and
/// takes its `remember` with it — and the edits are functions closed over
/// `app.scope` so a request already sent finishes after the tab is gone.
/// Both of those are what a model is on this side, so this is one.
///
/// ### What it refuses to offer
///
/// ``RoomQueue`` carries `editable` and `guest`, and this model does the
/// arithmetic on them once (``mayClear``, ``mayRemove(_:)``, ``mayMove``) so
/// that no view works it out a second time and differently. A tap this model
/// would be refused for is **refused here**, before the request — the same
/// rule ``MusicModel/play(_:in:)`` keeps about rooms the server never
/// reported, for the same reason: an offer the server will turn down is not
/// an offer.
@MainActor
@Observable
public final class MusicQueueModel {

    // MARK: - what is read

    /// The picked room's queue. Rebuilt when the room changes, because a
    /// different room is a different path.
    public private(set) var read: ApiRead<RoomQueue>

    /// Which room's queue is on screen, or nil before any room has been
    /// reported.
    ///
    /// Never a made-up name: it is set from what the server said, which is
    /// the same rule ``MusicPage/defaultRoom(_:)`` keeps.
    public private(set) var room: String?

    /// An edit is in flight. One at a time, as Android's `sel.busy` is: two
    /// moves racing each other on one list produce an order neither tap
    /// asked for.
    public private(set) var busy = false

    /// The clear confirmation is up.
    public var confirmingClear = false

    /// Something to tell the person. Taken by the screen, as
    /// ``MusicModel/toast`` is.
    public private(set) var toast: String?

    // MARK: - what it was built from

    private let container: AppContainer
    private let deviceID: String
    private var eventSink: Int?

    /// The push a queue listens for. The same one the now-playing strip
    /// listens for, because a track finishing is both a now-playing change
    /// and a queue change (`QueueTab.kt:121`).
    public static let events: Set<String> = ["music.now_playing.changed"]

    public init(container: AppContainer, deviceID: String) {
        self.container = container
        self.deviceID = deviceID
        self.read = Self.reader(room: nil, deviceID: deviceID, in: container)
    }

    /// One read for one room.
    ///
    /// With no room there is no request at all: an empty queue is returned
    /// without asking, because `/api/music/queue/` with nothing after it is
    /// not this room's queue, it is a 404 — and the screen has nothing to
    /// show either way. Android returns `RoomQueue()` from the same branch.
    private static func reader(
        room: String?,
        deviceID: String,
        in container: AppContainer
    ) -> ApiRead<RoomQueue> {
        let client = container.client
        return ApiRead(in: container, eventTypes: Self.events) {
            guard let room else { return RoomQueue(editable: false) }
            return try await client.get(
                MusicQueuePath.read(room: room, deviceID: deviceID),
                as: RoomQueue.self
            )
        }
    }

    // MARK: - what is drawn

    public var queue: RoomQueue { read.data ?? RoomQueue(editable: false) }

    public var items: [QueueItem] { queue.items }

    /// The spinner, and only for a first read: a refresh over a list that is
    /// already on screen does not blank it.
    public var isFirstLoad: Bool { read.data == nil && read.loading }

    public var mayClear: Bool { queue.mayClear && !busy }

    public var mayMove: Bool { queue.mayMove && !busy }

    public func mayRemove(_ item: QueueItem) -> Bool { queue.mayRemove(item) && !busy }

    /// Can this entry go up, and down? The ends of the list are the answer,
    /// and ``mayMove`` is the permission.
    public func mayMoveUp(_ index: Int) -> Bool { mayMove && index > 0 }

    public func mayMoveDown(_ index: Int) -> Bool { mayMove && index < items.count - 1 }

    public var watchOnlyNote: String? { queue.watchOnlyNote }

    /// `"3 in the queue"`, or nil for a queue that has not answered.
    public var countText: String? {
        guard read.answered || read.data != nil else { return nil }
        return MusicStrings.queueCount(items.count)
    }

    /// What to say over an empty queue, or nil when it is not empty.
    ///
    /// **One sentence, naming the room.** `QueueTab.kt:217` says "queue is
    /// empty" over "cast from the Player tab, or say \"play something\" in
    /// <room>", and half of that is a direction to a tab this app does not
    /// have. A first draft of this had a second sentence for a room that is
    /// playing with nothing queued — a station, say — and that is a claim
    /// about **why** the queue is empty which this model cannot make: all it
    /// knows is that what is playing did not come from the queue.
    public var emptyNote: String? {
        guard read.answered, items.isEmpty, read.error == nil else { return nil }
        guard let room else { return nil }
        return MusicStrings.queueEmpty(room: room)
    }

    // MARK: - lifecycle

    /// The tab appeared, over these rooms.
    ///
    /// - Parameters:
    ///   - rooms: every room the server has reported.
    ///   - playing: the room that is actually playing something, if any.
    ///
    /// The default is **the room that is playing** — that is the queue
    /// somebody opened this tab to look at (`QueueTab.kt:114`) — and the
    /// first reported room otherwise. A room the person has since picked is
    /// kept, unless the server has stopped reporting it, in which case
    /// keeping it would leave the screen asking for a queue that cannot
    /// answer.
    public func start(rooms: [String], playing: String?) {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        // One request, not two: ``offer(rooms:playing:)`` builds a new read
        // and sends it when the room moves, so refreshing afterwards as well
        // would cancel that one and send another. `ApiRead.refresh()` does
        // cancel the in-flight read, so the duplicate would not be two
        // answers — it would be a request sent and dropped, every time
        // the tab opens.
        if !offer(rooms: rooms, playing: playing) {
            read.refresh()
        }
    }

    /// The rooms changed under us — a satellite came online, or went away.
    ///
    /// Separate from ``start(rooms:playing:)`` because it must **not**
    /// re-read on every now-playing push: the read has its own subscription
    /// for that, and a second refresh per push is two requests where the
    /// screen needs one.
    /// - Returns: whether the open room changed, which is how
    ///   ``start(rooms:playing:)`` knows whether a read has already gone.
    @discardableResult
    public func offer(rooms: [String], playing: String?) -> Bool {
        let wanted: String?
        if let room, rooms.contains(room) {
            wanted = room
        } else {
            wanted = playing.flatMap { rooms.contains($0) ? $0 : nil } ?? rooms.first
        }
        guard wanted != room else { return false }
        pick(wanted)
        return true
    }

    /// The person picked a room.
    public func pick(_ wanted: String?) {
        guard wanted != room else { return }
        read.stop()
        room = wanted
        read = Self.reader(room: wanted, deviceID: deviceID, in: container)
        read.refresh()
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        read.stop()
    }

    public func received(_ event: StateEvent) {
        read.received(event)
    }

    /// The saved server changed under us.
    ///
    /// The room is forgotten, not kept. A room name is one house's word: the
    /// next house may have a room of that name which is a different speaker,
    /// and may have no rooms at all. Carrying the pick across a switch is the
    /// pager's stale-key bug in another shape
    /// ([[read-after-await-wrong-key]]), and the next
    /// ``offer(rooms:playing:)`` — which the screen makes from the new
    /// house's now-playing answer — is what picks again.
    ///
    /// The old read is **stopped and dropped** rather than told the server
    /// changed: it is about to be replaced, so clearing its data would be
    /// work on an object nothing will look at again.
    public func serverChanged() {
        read.stop()
        room = nil
        read = Self.reader(room: nil, deviceID: deviceID, in: container)
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    // MARK: - the three edits

    /// Take one entry out.
    ///
    /// Addressed by `song_ids`, which is a **list** on the wire even for one
    /// entry (`QueueRemoveRequest.song_ids`), so this sends a one-element
    /// array rather than inventing a scalar the server would reject.
    public func remove(_ item: QueueItem) {
        guard mayRemove(item), let room else { return }
        let label = item.displayTitle
        send(
            path: MusicQueuePath.remove(room: room),
            body: Self.json(songIDs: [item.songID], deviceID: deviceID),
            success: { MusicStrings.removedFromQueue(label) },
            failure: MusicStrings.removeFailed
        )
    }

    /// Move one entry to a position.
    ///
    /// Refused for a position outside the list, here rather than as a 422:
    /// the server takes `to_position: int = Field(..., ge=0)`, so a `-1` from
    /// an up-arrow on the first row is a validation error with nothing for
    /// the person to do about it.
    public func move(_ item: QueueItem, to position: Int) {
        guard mayMove, let room, (0..<items.count).contains(position) else { return }
        send(
            path: MusicQueuePath.move(room: room),
            body: Self.json(songID: item.songID, to: position, deviceID: deviceID),
            // Android toasts nothing on a successful move: the list redraws
            // in the new order, which says it better than a sentence over the
            // top of it. Only the failure speaks.
            success: { nil },
            failure: MusicStrings.moveFailed
        )
    }

    /// Up one. Separate from ``move(_:to:)`` so a view never does index
    /// arithmetic — an off-by-one there moves the wrong track.
    public func moveUp(_ index: Int) {
        guard mayMoveUp(index), let item = items.indices.contains(index) ? items[index] : nil else {
            return
        }
        move(item, to: index - 1)
    }

    public func moveDown(_ index: Int) {
        guard mayMoveDown(index), let item = items.indices.contains(index) ? items[index] : nil
        else { return }
        move(item, to: index + 1)
    }

    /// Empty the queue, which stops the room.
    ///
    /// Household only (``RoomQueue/mayClear``). The confirmation is the
    /// screen's; this is what the confirmation calls.
    public func clear() {
        confirmingClear = false
        guard mayClear, let room else { return }
        send(
            path: MusicQueuePath.clear(room: room),
            body: Self.json(deviceID: deviceID),
            success: { MusicStrings.clearedQueue(room: room) },
            failure: MusicStrings.clearFailed
        )
    }

    /// The one path all three edits take.
    ///
    /// Held open while the request is in flight and **released in a
    /// `defer`**, so a throw cannot leave the screen's controls disabled for
    /// good — which is what Android's `try/finally` is for at each of its
    /// three call sites, written once here.
    ///
    /// The request runs on a `Task` the model owns rather than the view's, as
    /// `app.scope` does on Android: an edit already sent must still finish
    /// and still say how it went after the tab is gone.
    private func send(
        path: String,
        body: Data,
        success: @escaping () -> String?,
        failure: @escaping (String) -> String
    ) {
        busy = true
        let client = container.client
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                try await client.send(method: "POST", path: path, body: body)
                guard let self else { return }
                if let said = success() { self.toast = said }
                // The server is the truth about the order and about what is
                // playing; the list is re-read rather than patched locally,
                // because a local reorder is this phone's guess at an edit
                // four other clients also see.
                self.read.refresh()
            } catch let trouble {
                self?.toast = failure(trouble.message)
            }
        }
    }

    // MARK: - the bodies

    /// `{"song_ids":[7],"device_id":"ios-…"}`.
    ///
    /// The three writers below are `nonisolated` for ``MusicModel/json(_:)``'s
    /// reason: they are pure functions of their arguments, and a test of the
    /// bytes a body has should not have to be a main-actor test to read them.
    nonisolated static func json(songIDs: [Int64], deviceID: String) -> Data {
        let list = songIDs.map { "\($0)" }.joined(separator: ",")
        return Data("{\"song_ids\":[\(list)],\"device_id\":\(deviceID.jsonLiteral)}".utf8)
    }

    /// `{"song_id":7,"to_position":2,"device_id":"ios-…"}`.
    nonisolated static func json(songID: Int64, to position: Int, deviceID: String) -> Data {
        Data(
            """
            {"song_id":\(songID),"to_position":\(position),\
            "device_id":\(deviceID.jsonLiteral)}
            """.utf8
        )
    }

    /// `{"device_id":"ios-…"}`.
    nonisolated static func json(deviceID: String) -> Data {
        MusicModel.json(["device_id": deviceID])
    }
}
