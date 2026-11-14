import Foundation
import Observation

/// One genre, opened: its songs by artist a page at a time, and playing all
/// of it in a room or on this phone.
///
/// `GenresTab.kt`'s `GenreDrawer` (3cdd261b), which mirrors the web's
/// `GenreDrawer`. Shown as a sheet, as this app shows every drawer
/// (``MusicPlaylistDetailModel``'s call for `PlaylistDrawer.kt`).
///
/// ### Two lists of the same songs, for two different jobs
///
/// * **The room** gets every id the server has for the genre
///   (`GET /api/music/genre?key=`, artist order, at most 2,000), of which a
///   room takes ``MusicGenres/roomTrackCap`` at a time.
/// * **This phone** gets the songs loaded so far
///   (`GET /api/music/library?genre=&sort=artist`), which is what the sheet
///   says under its buttons while there are more to scroll to.
///
/// ### The room's ids are tagged with the server they were read for
///
/// ``detail`` holds a ``MusicGenreDetailRead``: the answer and the address it
/// was asked of. ``roomIDs`` is empty unless that address is still the saved
/// one and the answer is for this genre — so an answer that lands after the
/// house changed is never sent on to a room of the new house
/// ([[read-after-await-wrong-key]], `GenresTab.kt:357-365`).
///
/// ### What is not here, and why
///
/// Android's drawer also has **add to queue** in a room, **queue here**, and
/// per-song **play next** / **add to queue**. Those are the queue work's
/// (batch 2026-11-14, the queues unit owns "play next / add to queue" in
/// every drawer), and this phone's engine has no enqueue yet
/// (``MusicMorePlayerEngine``) — so no button is drawn for them here rather
/// than one that cannot do what it says ([[no-misleading-controls]]).
@MainActor
@Observable
public final class MusicGenresDetailModel {

    public static let events: Set<String> = ["library.indexer.changed"]

    public let genre: MusicGenreRow

    /// Every id of the genre, for a room, tagged with its server.
    public let detail: ApiRead<MusicGenreDetailRead>

    /// The genre's songs as library rows, a hundred at a time.
    public let songs: Pager<LibraryTrack, Int64>

    /// The room chip the person picked, if any.
    public private(set) var picked: String?

    private let container: AppContainer
    private let deviceID: String
    private var eventSink: Int?

    /// Where this model's sentences go. The sheet closes on `play`, as
    /// Android's drawer does, so a failure that comes back later has no sheet
    /// to be said in — the screen hands in the shell's toast.
    private let say: @MainActor @Sendable (String) -> Void

    public init(
        genre: MusicGenreRow,
        container: AppContainer,
        deviceID: String,
        say: @escaping @MainActor @Sendable (String) -> Void
    ) {
        self.genre = genre
        self.container = container
        self.deviceID = deviceID
        self.say = say
        let client = container.client
        let store = container.store
        let detailPath = MusicGenres.detailPath(key: genre.key)
        self.detail = ApiRead(in: container, eventTypes: Self.events) {
            let asked = store.serverURL
            let answer = try await client.get(detailPath, as: MusicGenreDetail.self)
            return MusicGenreDetailRead(server: asked, detail: answer)
        }
        let songsPath = MusicGenres.songsPath(key: genre.key)
        self.songs = Pager(
            in: container,
            eventTypes: Self.events,
            pageSize: MusicGenres.songsPageSize,
            keyOf: { $0.id }
        ) { request in
            try await client.get(Paging.path(songsPath, request), as: Page<LibraryTrack>.self)
        }
    }

    // MARK: - what is drawn

    /// The ids a room may be sent: only from an answer for **this** genre on
    /// **the saved** server; empty otherwise.
    public var roomIDs: [Int64] {
        guard
            let read = detail.data,
            read.server == container.store.serverURL,
            read.detail.key == genre.key
        else { return [] }
        return read.detail.trackIDs
    }

    /// The genre's size: the detail's count once it has answered for this
    /// server, else the list row's.
    public var count: Int {
        guard
            let read = detail.data,
            read.server == container.store.serverURL,
            read.detail.key == genre.key
        else { return genre.trackCount }
        return read.detail.trackCount
    }

    public var subtitle: String { MusicGenresStrings.detailSubtitle(count: count, key: genre.key) }

    /// The songs loaded so far — what "play on this device" hands over.
    public var loaded: [LibraryTrack] { songs.state.items }

    /// The room a play goes to: the picked one while it is still online,
    /// else the first the server reported. Never a name of this model's own.
    public func room(in rooms: [String]) -> String? {
        if let picked, rooms.contains(picked) { return picked }
        return rooms.first
    }

    public func pick(_ room: String) { picked = room }

    /// Whether `play` / `shuffle` in a room can do anything right now.
    public func mayPlayInRoom(_ rooms: [String]) -> Bool {
        room(in: rooms) != nil && !roomIDs.isEmpty
    }

    public var roomCapNote: String? { MusicGenres.roomCapNote(roomIDs.count) }

    /// Under the phone buttons while there are more songs than are loaded.
    public var loadedSoFarNote: String? {
        guard songs.state.hasMore, !loaded.isEmpty else { return nil }
        return MusicGenresStrings.loadedSoFar(loaded.count)
    }

    public var phoneCapNote: String? { MusicGenres.phoneCapNote(loaded.count) }

    /// The empty state, once the songs answered with none.
    public var emptyNote: (title: String, note: String)? {
        let state = songs.state
        guard state.loaded, state.items.isEmpty, state.error == nil else { return nil }
        return (MusicGenresStrings.noSongs, MusicGenresStrings.noSongsNote)
    }

    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard songs.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: songs.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.listThreshold
        )
    }

    // MARK: - lifecycle

    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        detail.refresh()
        songs.resume()
        if !songs.state.loaded { songs.reset() }
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        detail.stop()
        songs.stop()
    }

    public func received(_ event: StateEvent) {
        detail.received(event)
        songs.received(event)
    }

    // MARK: - the room

    /// Play (or shuffle) the whole genre in the room — `POST
    /// /api/music/play-tracks` with at most ``MusicGenres/roomTrackCap`` ids
    /// (`GenresTab.kt:392`). Says what it is doing at once, and the failure
    /// if one comes back.
    ///
    /// Refused — false, nothing sent — with no room online or no ids for this
    /// server yet; the sheet does not offer the buttons then.
    @discardableResult
    public func play(
        shuffle: Bool,
        rooms: [String],
        shuffler: ([Int64]) -> [Int64] = { $0.shuffled() }
    ) -> Bool {
        guard let room = room(in: rooms) else { return false }
        let send = MusicGenres.roomTrackIDs(roomIDs, shuffle: shuffle, shuffler: shuffler)
        guard !send.isEmpty else { return false }
        say(MusicGenresStrings.playingInRoom(genre.name, room: room, shuffle: shuffle))
        let client = container.client
        let body = Self.playBody(room: room, trackIDs: send, deviceID: deviceID)
        let say = self.say
        Task { @MainActor in
            do throws(DomovoiError) {
                try await client.send(method: "POST", path: MusicPlaylistPath.playTracks, body: body)
            } catch let trouble {
                say(MusicGenresStrings.playInRoomFailed(trouble.message, shuffle: shuffle))
            }
        }
        return true
    }

    /// `{"room_id":"parlour","track_ids":[503,504],"device_id":"ios-…"}` —
    /// `CastTracksRequest` (`music.py:1888`). `device_id` because play-tracks
    /// REPLACES the room's queue and the server checks the caller may
    /// (`_assert_may_replace_queue`); ``MusicPlaylistsModel/playBody(room:playlistID:shuffle:deviceID:)``
    /// says why the body is iOS's only channel for it.
    nonisolated static func playBody(room: String, trackIDs: [Int64], deviceID: String) -> Data {
        let ids = trackIDs.map(String.init).joined(separator: ",")
        let text = "{\"room_id\":\(room.jsonLiteral),\"track_ids\":[\(ids)],\"device_id\":\(deviceID.jsonLiteral)}"
        return Data(text.utf8)
    }
}
