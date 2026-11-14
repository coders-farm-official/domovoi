import Foundation
import Observation

/// Play next / Add to queue, for the phone's own queue or a room's.
///
/// `QueueAdder.kt` (21b5ccfb, cb780e90, 6d14aa91): one place every song row,
/// the track sheet and the queue's search box go through, so "where did it
/// go" has one answer.

/// The phone's own queue, as adding to it needs it.
@MainActor
public protocol PhoneQueueSink: AnyObject {
    /// Something is queued here already.
    var hasQueue: Bool { get }
    /// The room this phone casts to now, or nil while it plays here.
    var castsTo: String? { get }
    func enqueue(_ tracks: [LibraryTrack])
    func playNext(_ tracks: [LibraryTrack])
}

/// ``PlayerModel`` as a ``PhoneQueueSink``: `PlayItem.fromTrack` per row,
/// then the player's own ``PlayerModel/enqueue(_:)`` and
/// ``PlayerModel/playNext(_:)`` (`PlayerController.kt:340-356`), which start
/// the phone playing when nothing was queued.
@MainActor
public final class PlayerModelQueueSink: PhoneQueueSink {
    public let player: PlayerModel

    public init(_ player: PlayerModel) { self.player = player }

    public var hasQueue: Bool { !player.queue.isEmpty }
    public var castsTo: String? { player.target.roomID }

    public func enqueue(_ tracks: [LibraryTrack]) { player.enqueue(tracks.map(Self.item)) }
    public func playNext(_ tracks: [LibraryTrack]) { player.playNext(tracks.map(Self.item)) }

    static func item(_ track: LibraryTrack) -> PlayerItem {
        PlayerItem.fromTrack(
            id: track.id,
            title: track.displayTitle,
            artist: track.artist,
            album: track.album,
            durationSec: track.durationSec
        )
    }
}

@MainActor
public final class QueueAdder {

    private let phone: (any PhoneQueueSink)?
    private let room: RoomQueueApi
    /// A genre's songs (up to 200), read from the server.
    private let genreSongs: @Sendable (String) async throws(DomovoiError) -> [LibraryTrack]
    private let shuffler: ([LibraryTrack]) -> [LibraryTrack]

    public init(
        phone: (any PhoneQueueSink)?,
        room: RoomQueueApi,
        genreSongs: @Sendable @escaping (String) async throws(DomovoiError) -> [LibraryTrack],
        shuffler: @escaping ([LibraryTrack]) -> [LibraryTrack] = { $0.shuffled() }
    ) {
        self.phone = phone
        self.room = room
        self.genreSongs = genreSongs
        self.shuffler = shuffler
    }

    /// The app's: a room's routes on `client`, a genre's songs from the
    /// library, and the phone's own queue when this build has a player.
    public convenience init(client: DomovoiClient, deviceID: String, phone: (any PhoneQueueSink)?) {
        self.init(
            phone: phone,
            room: RoomQueueApi(client: client, deviceID: deviceID),
            genreSongs: { (key: String) async throws(DomovoiError) -> [LibraryTrack] in
                try await client.get(QueueAsk.genreSongsPath(key), as: Page<LibraryTrack>.self).items
            }
        )
    }

    /// Whether `target` can be added to at all: a room always (the server
    /// answers for it), the phone only when this build has a player.
    public func offers(_ target: QueueTarget) -> Bool {
        if case .phone = target { return phone != nil }
        return true
    }

    /// `tracks` to `target`: after what plays now (`next`) or at the end.
    ///
    /// A room gets them through its queue routes; the phone queues them
    /// here. While this phone casts to **that** room and keeps a queue of its
    /// own for it (what a re-cast starts from), that queue gets them too, as
    /// the web's does — never an empty one, which would start the phone
    /// playing, and never while it plays here or casts elsewhere. Returns how
    /// many the target took; a room's refusal throws.
    @discardableResult
    public func add(_ target: QueueTarget, tracks: [LibraryTrack], next: Bool) async throws(DomovoiError) -> Int {
        guard !tracks.isEmpty else { return 0 }
        switch target {
        case .phone:
            guard let phone else { throw DomovoiError.notPermitted("this phone has no player") }
            if next { phone.playNext(tracks) } else { phone.enqueue(tracks) }
            return tracks.count
        case .room(let roomID):
            let ids = tracks.map(\.id)
            let took = next
                ? try await room.playNext(roomID, trackIDs: ids)
                : try await room.add(roomID, trackIDs: ids).count
            if let phone, phone.hasQueue, phone.castsTo == roomID {
                if next { phone.playNext(tracks) } else { phone.enqueue(tracks) }
            }
            return took
        }
    }

    /// A search row's songs: the song, or a genre's, shuffled, the first 25.
    public func tracks(of find: QueueFind) async throws(DomovoiError) -> [LibraryTrack] {
        switch find {
        case .song(let track): return [track]
        case .genre(let genre): return QueueAsk.genreTake(try await genreSongs(genre.key), shuffler: shuffler)
        }
    }

    /// A search row added from the box; the line the box says after it (the
    /// web's words).
    public func add(_ target: QueueTarget, find: QueueFind, next: Bool) async -> String {
        let label = find.label
        do throws(DomovoiError) {
            let tracks = try await tracks(of: find)
            guard !tracks.isEmpty else { return QueueStrings.nothingToAdd(label) }
            let took = try await add(target, tracks: tracks, next: next)
            return QueueStrings.added(
                label,
                count: target.roomID != nil ? took : tracks.count,
                next: next,
                room: target.roomID
            )
        } catch let trouble {
            return QueueStrings.addFailed(label, queueErrorText(trouble))
        }
    }

    /// One song to `target`'s queue, said as a toast; a room's refusal is
    /// said too (`songToQueue`).
    public func song(_ target: QueueTarget, track: LibraryTrack, next: Bool) async -> String {
        let title = track.title ?? "track"
        do throws(DomovoiError) {
            try await add(target, tracks: [track], next: next)
            return QueueStrings.songQueued(target, title: title, next: next)
        } catch let trouble {
            return QueueStrings.addFailed("\"\(title)\"", queueErrorText(trouble))
        }
    }
}

/// One search from the queue's box, as the web makes it: songs by title,
/// artist or album, and genres, read side by side. A genre read that fails
/// only loses the genre rows (the web's `.catch(() => null)`); a song read
/// that fails throws.
public func queueSearch(client: DomovoiClient, term: String) async throws(DomovoiError) -> [QueueFind] {
    async let genrePage = client.get(QueueAsk.genresPath(term), as: QueueGenrePage.self)
    let songs = try await client.get(QueueAsk.songsPath(term), as: Page<LibraryTrack>.self).items
    let genres = (try? await genrePage)?.items
    return queueFinds(songs: songs, genres: genres)
}

/// The library songs of a phone queue, in order: what "save as playlist"
/// keeps.
public func savableTrackIDs(_ queue: [MusicPlayerItem]) -> [Int64] {
    queue.filter { $0.kind == .library }.map(\.trackID)
}

/// "Save as playlist" (the web's `PlayerQueueSave`): a playlist named `name`
/// (trimmed), then each song added in order; a song the server refuses is
/// skipped, as on the web. Returns the playlist's id. Nothing is created for
/// a blank name or an empty list.
@discardableResult
public func saveQueueAsPlaylist(client: DomovoiClient, name: String, trackIDs: [Int64]) async throws(DomovoiError) -> Int64? {
    let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !clean.isEmpty, !trackIDs.isEmpty else { return nil }
    let created = try await client.post(
        MusicPlaylistPath.create,
        body: RoomPlayBodies.encode(["name": clean]),
        as: Playlist.self
    )
    for id in trackIDs {
        // One the server will not take is skipped (the web's `catch {}`).
        try? await client.send(
            method: "POST",
            path: MusicPlaylistPath.addTrack(created.id),
            body: RoomPlayBodies.encode(["track_id": id])
        )
    }
    return created.id
}

/// "Save as playlist" (the web's `PlayerQueueSave`): a button that first
/// asks for a name, then saves. Plain state, so a test can walk it.
@MainActor
@Observable
public final class SaveAsPlaylistFlow {
    public var open = false
    public var name = ""
    public private(set) var saving = false
    public private(set) var note = ""
    public private(set) var failed = false

    public init() {}

    /// The button: ask for a name. Nothing is sent yet.
    public func ask() {
        open = true
        note = ""
        failed = false
    }

    public func cancel() { open = false }

    public func canSave(_ trackIDs: [Int64]) -> Bool {
        open && !trackIDs.isEmpty && !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !saving
    }

    /// Saves the phone's queue under the name asked for; false (nothing
    /// sent) until there is one.
    @discardableResult
    public func save(client: DomovoiClient, trackIDs: [Int64]) async -> Bool {
        guard canSave(trackIDs) else { return false }
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        saving = true
        note = ""
        defer { saving = false }
        do throws(DomovoiError) {
            try await saveQueueAsPlaylist(client: client, name: clean, trackIDs: trackIDs)
            note = QueueStrings.savedAs(clean)
            failed = false
            name = ""
            open = false
            return true
        } catch let trouble {
            note = QueueStrings.saveFailed(queueErrorText(trouble))
            failed = true
            return false
        }
    }

    /// A room's queue has no list here to check: the server says what it
    /// held.
    public var canSaveRoom: Bool {
        open && !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !saving
    }

    /// Saves a **room's** queue (`send`: `POST …/queue/{room}/save-playlist`
    /// with the name asked for), the room's own list as the room holds it.
    /// The note is the web's: how many songs and what was left out, or the
    /// server's sentence for a refusal (a name taken, nothing to save).
    @discardableResult
    public func saveRoom(_ send: (String) async throws(DomovoiError) -> RoomSaveResult) async -> Bool {
        guard canSaveRoom else { return false }
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        saving = true
        note = ""
        defer { saving = false }
        do throws(DomovoiError) {
            note = RoomUpNextStrings.saveNote(try await send(clean), fallbackName: clean)
            failed = false
            name = ""
            open = false
            return true
        } catch let trouble {
            note = QueueStrings.saveFailed(queueErrorText(trouble))
            failed = true
            return false
        }
    }
}

/// The queue's search box, as a model (`QueueAddBox.kt`'s state).
///
/// The typed text, the debounced search, its rows and the one status line.
/// Keyed by the server **and** by the target: a change of server drops the
/// answer in flight, so one house's songs never land under another's box,
/// and a box built for one room's queue is never offered to another
/// ([[read-after-await-wrong-key]]).
@MainActor
@Observable
public final class QueueSearchModel {

    public let target: QueueTarget

    /// One prompt, picked when the box is built and kept while typing.
    public let prompt: String

    /// What is typed. Written through ``update(_:)``, which is what starts
    /// the debounced search.
    public private(set) var text = ""

    /// The rows, or nil while nothing has been asked.
    public private(set) var finds: [QueueFind]?
    /// The term the rows answer.
    public private(set) var asked: String?
    public private(set) var busy = false
    public private(set) var status = ""

    private let container: AppContainer
    private let adder: QueueAdder
    private var pending: Task<Void, Never>?
    private let debounceNs: UInt64

    public init(
        target: QueueTarget,
        container: AppContainer,
        adder: QueueAdder,
        prompt: String = QueueAsk.prompt(),
        debounceMs: UInt64 = QueueAsk.debounceMs
    ) {
        self.target = target
        self.container = container
        self.adder = adder
        self.prompt = prompt
        self.debounceNs = debounceMs * 1_000_000
    }

    /// The term the box is asking about now.
    public var term: String? { QueueAsk.term(text) }

    /// The rows to draw: only the ones answering what is typed now.
    public var rows: [QueueFind] {
        guard let finds, let term, asked == term else { return [] }
        return finds
    }

    /// "Nothing matches …", once a search for what is typed came back empty.
    public var nothingNote: String? {
        guard let finds, finds.isEmpty, let term, asked == term, !busy, status.isEmpty else { return nil }
        return QueueStrings.nothingMatches(term)
    }

    /// The one line under the rows: what an add said, a failure, or
    /// "searching…".
    public var line: String {
        if !status.isEmpty { return status }
        return busy && term != nil ? QueueStrings.searching : ""
    }

    /// The person typed.
    public func update(_ typed: String) {
        guard typed != text else { return }
        text = typed
        status = ""
        schedule()
    }

    public func clear() { update("") }

    private func schedule() {
        pending?.cancel()
        guard let term else {
            finds = nil
            busy = false
            asked = nil
            return
        }
        let client = container.client
        let server = container.store.serverURL
        let wait = debounceNs
        pending = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: wait)
            guard !Task.isCancelled, let self else { return }
            self.busy = true
            do throws(DomovoiError) {
                let found = try await queueSearch(client: client, term: term)
                guard !Task.isCancelled, self.container.store.serverURL == server else { return }
                self.finds = found
                self.status = ""
            } catch let trouble {
                guard !Task.isCancelled, self.container.store.serverURL == server else { return }
                self.finds = []
                self.status = QueueStrings.searchFailed(queueErrorText(trouble))
            }
            self.asked = term
            self.busy = false
        }
    }

    /// Add a row. The model's own `Task`, so a sheet closing before the room
    /// answers still hears how it went; nothing is said once the app talks to
    /// another server.
    public func add(_ find: QueueFind, next: Bool) {
        let server = container.store.serverURL
        let adder = adder
        let target = target
        Task { @MainActor [weak self] in
            let said = await adder.add(target, find: find, next: next)
            guard let self, self.container.store.serverURL == server else { return }
            self.status = said
        }
    }

    public func stop() {
        pending?.cancel()
        pending = nil
    }

    /// Wait for the search in flight, for a test.
    func settle() async { await pending?.value }
}

/// This phone's queue, **built only when something is put in it**.
///
/// ``ShellModel/player`` builds the player on first read, and the player
/// activates a `.playback` session — one built to answer "is anything queued
/// here?" would stop whatever the person was already listening to for a
/// question whose answer was no. So the two questions a room add asks
/// (``hasQueue``, ``castsTo``) read a player only if one exists already, and
/// only an add to this phone builds one.
@MainActor
public final class LazyPhoneQueue: PhoneQueueSink {
    private let built: () -> (any PhoneQueueSink)?
    private let make: () -> (any PhoneQueueSink)?

    public init(built: @escaping () -> (any PhoneQueueSink)?, make: @escaping () -> (any PhoneQueueSink)?) {
        self.built = built
        self.make = make
    }

    public var hasQueue: Bool { built()?.hasQueue ?? false }
    public var castsTo: String? { built()?.castsTo }

    /// Whether this build can play here at all — asked the way a screen asks
    /// whether it may draw a play control (`RouteScreen.swift`'s `stations`
    /// slot): by asking for the player.
    public var available: Bool { make() != nil }

    public func enqueue(_ tracks: [LibraryTrack]) { make()?.enqueue(tracks) }
    public func playNext(_ tracks: [LibraryTrack]) { make()?.playNext(tracks) }
}

/// What every queue surface needs, made once from the shell: the house, this
/// phone's id, whether it is paired, whose mix it follows, and its own queue.
@MainActor
public final class QueueTools {
    public let container: AppContainer
    public let deviceID: String
    /// The household's library and a room's queue both need the pairing;
    /// the search box and the queue buttons are hidden without it
    /// (`QueueAddBox.kt:62`, `RoomQueueSheet.kt:58`).
    public let paired: Bool
    public let listener: RoomListener
    public let phone: LazyPhoneQueue?

    public init(container: AppContainer, deviceID: String, paired: Bool, listener: RoomListener, phone: LazyPhoneQueue?) {
        self.container = container
        self.deviceID = deviceID
        self.paired = paired
        self.listener = listener
        self.phone = phone
    }

    public func adder() -> QueueAdder {
        QueueAdder(client: container.client, deviceID: deviceID, phone: phone)
    }

    /// A search box for `target`'s queue.
    public func search(_ target: QueueTarget) -> QueueSearchModel {
        QueueSearchModel(target: target, container: container, adder: adder())
    }

    /// A room's queue model.
    public func roomQueue() -> MusicQueueModel {
        MusicQueueModel(container: container, deviceID: deviceID)
    }

    /// Where a song row's Play next / Add to queue go now: the room this
    /// phone casts to, else this phone (read at the tap).
    public var followingTarget: QueueTarget { .following(castsTo: phone?.castsTo) }

    /// Whether this phone's own queue may be offered at all.
    public var mayQueueHere: Bool { phone?.available ?? false }
}

public extension ShellModel {
    /// The queue tools for this shell, or nil with no device id — a queue
    /// edit without `device_id` is refused by the server (`music_queue.py`).
    func queueTools() -> QueueTools? {
        guard let deviceID, !ServerCredentials.isBlank(deviceID) else { return nil }
        let phone = LazyPhoneQueue(
            built: { [weak self] in
                guard let self, self.playerIsBuilt, let player = self.player else { return nil }
                return PlayerModelQueueSink(player)
            },
            make: { [weak self] in self?.player.map(PlayerModelQueueSink.init) }
        )
        return QueueTools(
            container: container,
            deviceID: deviceID,
            paired: isPaired,
            listener: RoomListener(personID: listeners.personID, shared: visibility.sharedScreen),
            phone: phone
        )
    }
}
