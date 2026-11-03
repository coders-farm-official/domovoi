import Foundation
import Observation

/// The playlists tab: the list, and playing one in a room.
///
/// `PlaylistsTab.kt` plus `MusicScreen.kt:333`'s `onPlayPlaylist`, as a type.
///
/// The server pins Favorites on top (`playlists.py:82`) and this does **not**
/// re-sort: the order is the house's answer, and a client that sorted it again
/// would disagree with the dashboard about which playlist is first.
@MainActor
@Observable
public final class MusicPlaylistsModel {

    /// The push a playlist list listens for.
    public static let events: Set<String> = ["playlists.changed"]

    // MARK: - what is read

    public let read: ApiRead<[Playlist]>

    // MARK: - what is drawn

    /// Something to tell the person. Taken by the screen, as
    /// ``MusicModel/toast`` is.
    public private(set) var toast: String?

    private let container: AppContainer
    private let deviceID: String
    private var eventSink: Int?

    public init(container: AppContainer, deviceID: String) {
        self.container = container
        self.deviceID = deviceID
        let client = container.client
        self.read = ApiRead(in: container, eventTypes: Self.events) {
            // A **bare array**, not a wrapper. Three of Home's four reads on
            // this track were never decoded because a fixture guessed the
            // wrapper ([[ios-stub-house]]); this one is read from
            // `playlists.py:82`.
            try await client.get(MusicPlaylistPath.all, as: [Playlist].self)
        }
    }

    public var playlists: [Playlist] { read.data ?? [] }

    /// The playlists a track can be added to as playlists — Favorites is not
    /// one of them, and every write path for it is different
    /// (``MusicAddToPlaylistModel/toggle(_:)``).
    public var realPlaylists: [Playlist] { playlists.filter { !$0.isVirtual } }

    /// The spinner, and only for a first read.
    public var isFirstLoad: Bool { read.data == nil && read.loading }

    /// What to say over an empty list, or nil when it is not empty.
    public var emptyNote: (String, String)? {
        guard read.answered, playlists.isEmpty, read.error == nil else { return nil }
        return (MusicMoreStrings.noPlaylists, MusicMoreStrings.noPlaylistsNote)
    }

    /// The tab's count, or nil while nothing has answered — **nil is not
    /// zero**, and `playlists · 0` for a house whose read is in flight is a
    /// claim this model cannot make.
    public var count: Int? { read.data.map(\.count) }

    // MARK: - lifecycle

    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        read.refresh()
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        read.stop()
    }

    public func received(_ event: StateEvent) { read.received(event) }

    public func serverChanged() { read.serverChanged() }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    // MARK: - the one thing a row's button does

    /// Play `playlist` in the first room the server reported
    /// (`MusicScreen.kt:333`).
    ///
    /// - Parameter rooms: every room the server has reported. **Not a name
    ///   this model invents**: with none it says so rather than sending a
    ///   request to a room that does not exist, which is the same rule
    ///   ``MusicModel/play(_:in:)`` keeps.
    @discardableResult
    public func play(_ playlist: Playlist, rooms: [String]) -> Bool {
        guard let room = rooms.first else {
            toast = MusicMoreStrings.noRoomsForPlaylist(playlist.name)
            return false
        }
        toast = MusicMoreStrings.playingPlaylist(playlist.name, room: room)
        let client = container.client
        let body = Self.playBody(room: room, playlistID: playlist.id, shuffle: false, deviceID: deviceID)
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(
                    method: "POST",
                    path: MusicPlaylistPath.playPlaylist,
                    body: body
                )
            } catch let trouble {
                self?.toast = MusicMoreStrings.playPlaylistFailed(trouble.message)
            }
        }
        return true
    }

    /// `{"room_id":"kitchen","playlist_id":4,"shuffle":false,"device_id":"ios-…"}`.
    ///
    /// **`device_id` is here and is not in Android's body.** `play-playlist`
    /// replaces the room's queue, so the server runs
    /// `_assert_may_replace_queue(request, body.device_id, body.room_id)`
    /// (`music.py:1734`, WEB-11). ``DomovoiClient`` sends no `X-Device-Id`
    /// header — grepped: only `X-Device-Token` exists — and a phone has no
    /// registration cookie, so the body is the only channel iOS has. Without
    /// it a device an admin has blocked from editing a room's queue could
    /// still replace it from here. Android's omission is a hole, not a design
    /// (DECISIONS).
    ///
    /// `nonisolated` for ``MusicModel/json(_:)``'s reason: a test of the bytes
    /// a body has should not have to be a main-actor test to read them.
    nonisolated static func playBody(
        room: String,
        playlistID: Int64,
        shuffle: Bool,
        deviceID: String
    ) -> Data {
        MusicModel.json([
            "room_id": room,
            "playlist_id": playlistID,
            "shuffle": shuffle,
            "device_id": deviceID,
        ])
    }
}
