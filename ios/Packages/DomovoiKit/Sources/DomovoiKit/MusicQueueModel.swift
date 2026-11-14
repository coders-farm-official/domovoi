import Foundation
import Observation

/// A room's queue: which room, what is in it, its Up next, and the edits.
///
/// `QueueTab.kt`'s `rememberRoomQueue` and `RoomQueueSelection` as a type
/// (Android 21b5ccfb, cb780e90, bd274c2f, 6d14aa91). Over there the
/// selection lives in a `@Stable` class hoisted out of the lazy items for
/// the reason its own comment gives — the header scrolls out of composition
/// and takes its `remember` with it — and the edits are functions closed
/// over `app.scope` so a request already sent finishes after the tab is gone.
/// Both of those are what a model is on this side, so this is one.
///
/// Drawn in Music's "room queue" tab (``MusicQueueView``) and in a room's
/// queue sheet (`RoomQueueSheet`: Music's room cards, Home's room rows, the
/// room's own screen) — the same rows, edits and search box, for one room.
///
/// ### What it refuses to offer
///
/// ``RoomQueue`` carries `editable` and `guest`, and this model does the
/// arithmetic on them once (``mayClear``, ``mayRemove(_:)``, ``mayMove``,
/// ``controls``) so that no view works it out a second time and differently.
/// A tap this model would be refused for is **refused here**, before the
/// request: an offer the server will turn down is not an offer.
///
/// ### Every answer is keyed by the server and room it was for
///
/// The read carries both (``RoomQueueRead``) and is shown only while they
/// are still the ones on screen; an edit's answer is acted on only while the
/// app still talks to the server and the room the edit was for, and says
/// nothing otherwise ([[read-after-await-wrong-key]]).
@MainActor
@Observable
public final class MusicQueueModel {

    // MARK: - what is read

    /// The picked room's queue, with the server and room it was asked of.
    /// Rebuilt when the room changes, because a different room is a
    /// different path.
    public private(set) var read: ApiRead<RoomQueueRead>

    /// Which room's queue is on screen, or nil before any room has been
    /// reported. Never a made-up name.
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

    /// The last thing said, kept for a sheet that draws it inline (a sheet
    /// covers the shell's toast — ``MusicMoreSheetStatus``'s reason).
    public private(set) var said: String?
    public private(set) var saidFailed = false

    /// Rows drawn from the window's start (``RoomQueueWindow/lines(_:items:shown:earlier:)``):
    /// more as the list scrolls.
    public private(set) var shown = RoomQueueWindow.rowsStep

    /// The played songs folded away above the playing one were asked for.
    public private(set) var earlier = false

    /// Picks a "less like this" took back, gone from the view at once.
    public private(set) var dropped: DroppedPicks?

    /// "Save as playlist" for this room's queue. One per room and server: a
    /// name typed for one room's queue is not saved as another's.
    public private(set) var save = SaveAsPlaylistFlow()

    // MARK: - what it was built from

    private let container: AppContainer
    private let deviceID: String
    private let api: RoomQueueApi
    private var eventSink: Int?
    private var nudgeSink: Int?
    private var watch = RoomPlayWatch()

    /// The push a queue listens for. The same one the now-playing strip
    /// listens for, because a track finishing is both a now-playing change
    /// and a queue change (`QueueTab.kt:121`) — and only **this** room's
    /// change re-reads it (``RoomPlayWatch``).
    public static let events: Set<String> = ["music.now_playing.changed"]

    public init(container: AppContainer, deviceID: String) {
        self.container = container
        self.deviceID = deviceID
        self.api = RoomQueueApi(client: container.client, deviceID: deviceID)
        self.read = Self.reader(room: nil, api: api, in: container)
    }

    /// One read for one room.
    ///
    /// With no room there is no request at all, because
    /// `/api/music/queue/` with nothing after it is not this room's queue,
    /// it is a 404.
    ///
    /// **No event types on the read itself**: a push re-reads through
    /// ``received(_:)``, which asks ``RoomPlayWatch`` first. A read that also
    /// re-read on its own would make every room's song change in the house a
    /// request for this one.
    private static func reader(room: String?, api: RoomQueueApi, in container: AppContainer) -> ApiRead<RoomQueueRead> {
        let store = container.store
        return ApiRead(in: container) {
            let server = store.serverURL
            guard let room else {
                return RoomQueueRead(server: server, room: "", queue: RoomQueue(editable: false))
            }
            return RoomQueueRead(server: server, room: room, queue: try await api.read(room))
        }
    }

    // MARK: - what is drawn

    /// This room's answer on this server, or nil while it is still reading
    /// (``RoomQueueWindow/queue(_:server:room:)``).
    public var answer: RoomQueue? {
        RoomQueueWindow.queue(read.data, server: container.store.serverURL, room: room)
    }

    /// The answer, or an empty queue nobody may edit.
    public var queue: RoomQueue { answer ?? RoomQueue(editable: false) }

    /// The rows shown: the queue's, less the picks just taken back.
    public var items: [QueueItem] {
        RoomUpNextRules.visible(queue, dropped: dropped, server: container.store.serverURL, room: room)
    }

    public var controls: RoomQueueControls { RoomQueueControls(queue) }

    /// The spinner, and only for a first read: a refresh over a list that is
    /// already on screen does not blank it. Another room's answer held while
    /// this room's read is out is still loading.
    public var isFirstLoad: Bool {
        guard room != nil else { return false }
        return answer == nil && read.error == nil
    }

    /// The search that adds to this room is drawn
    /// (``RoomQueueWindow/canAdd(answered:queue:)``).
    public var canAdd: Bool { RoomQueueWindow.canAdd(answered: answer != nil, queue: queue) }

    public var mayClear: Bool { queue.mayClear && controls.clear && !busy }

    public var mayMove: Bool { queue.mayMove && !busy }

    public func mayRemove(_ item: QueueItem) -> Bool { queue.mayRemove(item) && !busy }

    /// Tap a row to play it now: an editable queue, never the row playing.
    public func mayPlayNow(_ item: QueueItem) -> Bool { controls.playNow && !busy && !item.playing }

    /// "save as playlist": the household's, over a queue with something in it.
    public var maySave: Bool { controls.save }

    /// The autoplay switch: the household's, over a room with a session.
    public var mayAutoplay: Bool { controls.autoplay }

    /// "less like this" on the shown row `index`: a pick still to come.
    public func mayLessLike(_ index: Int) -> Bool {
        RoomUpNextRules.lessLikeOffered(controls, items: items, index: index) && !busy
    }

    /// Can this entry go up, and down? The ends of the list are the answer,
    /// and ``mayMove`` is the permission.
    public func mayMoveUp(_ index: Int) -> Bool { mayMove && index > 0 }

    public func mayMoveDown(_ index: Int) -> Bool { mayMove && index < items.count - 1 }

    public var watchOnlyNote: String? { queue.watchOnlyNote }

    /// `"3 in the queue"`, or nil for a queue that has not answered.
    public var countText: String? {
        guard answer != nil else { return nil }
        return MusicStrings.queueCount(items.count)
    }

    /// What to say over an empty queue, or nil when it is not empty.
    ///
    /// Android's words (`QueueTab.kt:416`) where the search box is drawn
    /// above — "search above" is then true. Where it is not (a queue this
    /// phone may only watch), the sentence that does not point at a box
    /// that is not there.
    public var emptyNote: String? {
        guard answer != nil, items.isEmpty, read.error == nil, let room else { return nil }
        if canAdd { return "\(QueueStrings.queueEmpty) — \(QueueStrings.queueEmptyNote(room: room))" }
        return MusicStrings.queueEmpty(room: room)
    }

    /// The lines the list draws (``RoomQueueWindow/lines(_:items:shown:earlier:)``).
    public var lines: [RoomQueueLine] {
        RoomQueueWindow.lines(queue, items: items, shown: shown, earlier: earlier)
    }

    /// The Up next head's "why" and its one muted line.
    public var upNextBasis: String { RoomUpNextRules.basis(queue) }

    public var upNextLine: String? {
        RoomUpNextRules.line(queue, waiting: RoomUpNextRules.rows(items).count)
    }

    /// Whether the row at `index` is a pick still to come (the sparkle).
    public func isWaitingPick(_ index: Int) -> Bool {
        items.indices.contains(index) && items[index].isPick && RoomUpNextRules.rows(items).contains(index)
    }

    // MARK: - lifecycle

    /// The tab appeared, over these rooms.
    ///
    /// The default is **the room that is playing** — that is the queue
    /// somebody opened this tab to look at (`QueueTab.kt:114`) — and the
    /// first reported room otherwise. A room the person has since picked is
    /// kept, unless the server has stopped reporting it.
    public func start(rooms: [String], playing: String?) {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        if nudgeSink == nil {
            // The rest of a long playlist arrives after the play's answer:
            // read again when asked (``RoomQueueNudges``, a few times at most).
            nudgeSink = RoomQueueNudges.shared.listen { [weak self] server, room in
                guard let self, server == self.container.store.serverURL, room == self.room else { return }
                self.read.refresh()
            }
        }
        // One request, not two: ``offer(rooms:playing:)`` builds a new read
        // and sends it when the room moves.
        if !offer(rooms: rooms, playing: playing) {
            read.refresh()
        }
    }

    /// The rooms changed under us. Not a re-read on every now-playing push.
    /// - Returns: whether the open room changed.
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

    /// The person picked a room. Its list starts at the top again, and what
    /// its now-playing was is forgotten.
    public func pick(_ wanted: String?) {
        guard wanted != room else { return }
        read.stop()
        room = wanted
        resetRoomState()
        read = Self.reader(room: wanted, api: api, in: container)
        read.refresh()
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        if let nudgeSink {
            RoomQueueNudges.shared.remove(nudgeSink)
            self.nudgeSink = nil
        }
        read.stop()
    }

    /// A push arrived. Re-reads only when it is this room's change
    /// (``RoomPlayWatch``); an event without the rooms re-reads, as before.
    public func received(_ event: StateEvent) {
        guard Self.events.contains(event.type) else { return }
        if watch.shouldReread(server: container.store.serverURL, room: room, data: RoomQueueWindow.eventData(event)) {
            read.refresh()
        }
    }

    /// The saved server changed under us. The room is forgotten, not kept:
    /// a room name is one house's word.
    public func serverChanged() {
        read.stop()
        room = nil
        resetRoomState()
        read = Self.reader(room: nil, api: api, in: container)
    }

    private func resetRoomState() {
        shown = RoomQueueWindow.rowsStep
        earlier = false
        watch.reset()
        save = SaveAsPlaylistFlow()
        confirmingClear = false
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    // MARK: - the window

    /// The next ``RoomQueueWindow/rowsStep`` rows.
    public func showMore() { shown += RoomQueueWindow.rowsStep }

    /// The played songs folded away above the one playing.
    public func showEarlier() { earlier = true }

    // MARK: - the edits

    /// Take one entry out. `song_ids` is a **list** on the wire even for one.
    public func remove(_ item: QueueItem) {
        guard mayRemove(item) else { return }
        let label = item.displayTitle
        edit(failure: MusicStrings.removeFailed, { (api: RoomQueueApi, room: String) async throws(DomovoiError) in
            try await api.remove(room, songIDs: [item.songID])
        }, done: { _ in MusicStrings.removedFromQueue(label) })
    }

    /// Move one shown entry to a shown position.
    ///
    /// Refused for a position outside the list, here rather than as a 422:
    /// the server takes `to_position: int = Field(..., ge=0)`. The position
    /// sent is the room's own (``RoomUpNextRules/moveTarget(shown:all:to:)``),
    /// so a picks just taken back that are still in the room do not shift it.
    public func move(_ item: QueueItem, to position: Int) {
        let shownItems = items
        guard mayMove, room != nil, shownItems.indices.contains(position) else { return }
        let target = RoomUpNextRules.moveTarget(shown: shownItems, all: queue.items, to: position)
        edit(failure: MusicStrings.moveFailed, { (api: RoomQueueApi, room: String) async throws(DomovoiError) in
            try await api.move(room, songID: item.songID, to: target)
        })
    }

    /// Up one. Separate from ``move(_:to:)`` so a view never does index
    /// arithmetic — an off-by-one there moves the wrong track.
    public func moveUp(_ index: Int) {
        guard mayMoveUp(index), items.indices.contains(index) else { return }
        move(items[index], to: index - 1)
    }

    public func moveDown(_ index: Int) {
        guard mayMoveDown(index), items.indices.contains(index) else { return }
        move(items[index], to: index + 1)
    }

    /// Empty the queue, which stops the room. Household only.
    public func clear() {
        confirmingClear = false
        guard mayClear, let room else { return }
        edit(failure: MusicStrings.clearFailed, { (api: RoomQueueApi, room: String) async throws(DomovoiError) in
            try await api.clear(room)
        }, done: { _ in MusicStrings.clearedQueue(room: room) })
    }

    /// Play the entry now: the room's real jump (`POST …/play`, cb780e90),
    /// where the first port moved it to the front and skipped.
    public func playNow(_ item: QueueItem) {
        guard mayPlayNow(item), let room else { return }
        let title = item.title ?? "track"
        edit(failure: { "play failed: \($0)" }, { (api: RoomQueueApi, room: String) async throws(DomovoiError) in
            try await api.play(room, songID: item.songID)
        }, done: { _ in RoomUpNextStrings.playingNow(title, room: room) })
    }

    /// The room's autoplay switch: its session, PATCHed.
    public func setAutoplay(_ on: Bool) {
        guard mayAutoplay, let session = queue.sessionID, let room else { return }
        edit(failure: { "autoplay failed: \($0)" }, { (api: RoomQueueApi, _: String) async throws(DomovoiError) in
            try await api.setAutoplay(session: session, on: on)
        }, done: { _ in RoomUpNextStrings.autoplayNote(room, on: on) })
    }

    /// "Less like this" on a waiting pick: its artist leaves the mix, and the
    /// picks the session took back leave the view at once (the room drops
    /// them within ~5 s) — for this server, room and session only.
    public func lessLike(_ item: QueueItem) {
        guard let index = items.firstIndex(of: item), mayLessLike(index),
              let seq = item.upNextSeq, let session = queue.sessionID else { return }
        let artist = item.artist
        let server = container.store.serverURL
        edit(failure: { "less like this failed: \($0)" }, { (api: RoomQueueApi, _: String) async throws(DomovoiError) in
            try await api.lessLikeThis(session: session, seq: seq)
        }, done: { [weak self] removed in
            guard let self, let room = self.room else { return nil }
            let held = self.dropped.flatMap {
                $0.server == server && $0.room == room && $0.sessionID == session ? $0 : nil
            }
            self.dropped = (held ?? DroppedPicks(server: server, room: room, sessionID: session, seqs: []))
                .plus(removed + [seq])
            return RoomUpNextStrings.lessLikeNote(artist, removed: removed.count)
        })
    }

    /// Save the room's queue as a playlist, under the name the flow asked for.
    public func saveQueue() {
        guard maySave, let room, save.canSaveRoom else { return }
        let flow = save
        let api = api
        let server = container.store.serverURL
        Task { @MainActor [weak self] in
            let went = await flow.saveRoom { (name: String) async throws(DomovoiError) -> RoomSaveResult in
                try await api.savePlaylist(room, name: name)
            }
            // The flow belongs to this room and server: a note for a save
            // sent elsewhere is never shown here.
            guard let self, self.container.store.serverURL == server, self.room == room, self.save === flow else { return }
            self.speak(flow.note, failed: !went)
        }
    }

    /// The one path every edit takes.
    ///
    /// One at a time, released in a `defer`; the request runs on a `Task`
    /// the model owns, so an edit already sent finishes and says how it went
    /// after the tab is gone. `done` gets the answer only while the app still
    /// talks to the server and the room the edit was for — another house's
    /// queue never takes it, and nothing is said.
    private func edit<T: Sendable>(
        failure: @escaping (String) -> String,
        _ action: @escaping @Sendable (RoomQueueApi, String) async throws(DomovoiError) -> T,
        done: @escaping (T) -> String? = { _ in nil }
    ) {
        guard let room, !busy else { return }
        let server = container.store.serverURL
        let api = api
        busy = true
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                let answer = try await action(api, room)
                guard let self, self.container.store.serverURL == server, self.room == room else { return }
                if let line = done(answer) { self.speak(line, failed: false) }
                // The server is the truth about the order and about what is
                // playing; the list is re-read rather than patched locally.
                self.read.refresh()
            } catch let trouble {
                guard let self, self.container.store.serverURL == server else { return }
                self.speak(failure(queueErrorText(trouble)), failed: true)
            }
        }
    }

    private func speak(_ line: String, failed: Bool) {
        guard !line.isEmpty else { return }
        toast = line
        said = line
        saidFailed = failed
    }

    // MARK: - the bodies

    /// `{"song_ids":[7],"device_id":"ios-…"}`.
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
