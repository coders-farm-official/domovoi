import Foundation
import Observation

/// One playlist: its tracks in playback order, and the six things that can be
/// done to it.
///
/// `PlaylistDrawer.kt` as a type — Android's drawer; a `.sheet` here, as
/// ``MusicTrackSheet`` already is for `TrackDrawer.kt` (DECISIONS).
///
/// ### Favorites is not a playlist
///
/// Every write route refuses id 0: `DELETE /api/playlists/0` is refused
/// (`playlists.py:359`), `POST /api/playlists/0/tracks` is a **400** whose
/// detail names the library PATCH to use instead (`:382`), and the order and
/// patch routes are the same. So ``mayEdit`` gates every one of them, and the
/// screen draws no edit, no reorder and no delete for it at all — not greyed,
/// absent. A button that 400s every time is the control
/// [[no-misleading-controls]] forbids, and it is the one this port was most
/// likely to ship.
///
/// ### What is not, and why there is no control for it
///
/// * **`cover_color`** is read and shown, never edited. Android's edit form is
///   name, description and emoji only (`PlaylistDrawer.kt:271-295`), and the
///   colour arrives from whatever created the playlist.
/// * **Removing a track from the virtual Favorites** goes through the library
///   row's favourite flag, not through here — that is
///   ``MusicModel/toggleFavorite(_:)``. Android's drawer draws its `remove`
///   button for Favorites too (`PlaylistDrawer.kt:364`), where it sends
///   `DELETE /api/playlists/0/tracks/{id}`; the server's own guard list
///   (`playlists.py:467`) is why that is not ported.
@MainActor
@Observable
public final class MusicPlaylistDetailModel {

    /// The pushes the track list listens for. A library reindex can rename a
    /// track inside a playlist, which is why the second one is here
    /// (`PlaylistDrawer.kt:77`).
    public static let events: Set<String> = ["playlists.changed", "library.indexer.changed"]

    // MARK: - what is read

    public let read: ApiRead<[LibraryTrack]>

    // MARK: - what it is about

    /// The playlist as the list read it, with ``edit(name:description:emoji:)``
    /// applied once the server has agreed.
    public private(set) var playlist: Playlist

    /// The order shown while a reorder is in flight, or nil when the server's
    /// order is what is shown.
    ///
    /// Optimistic, and **rolled back on failure** (`PlaylistDrawer.kt:141`): a
    /// reorder that waits for the round trip makes the list appear to ignore
    /// the arrow, and three taps then send three reorders from three
    /// different starting points.
    public private(set) var localOrder: [LibraryTrack]?

    /// The room chip someone picked, or nil for "whatever the first room is".
    public private(set) var picked: String?

    public private(set) var busy = false

    public var editing = false

    public var confirmingDelete = false

    /// Set when the playlist is gone from the server, so the screen closes the
    /// sheet rather than leaving it open over nothing.
    public private(set) var deleted = false

    public private(set) var toast: String?

    private let container: AppContainer
    private let deviceID: String
    private var eventSink: Int?

    public init(playlist: Playlist, container: AppContainer, deviceID: String) {
        self.playlist = playlist
        self.container = container
        self.deviceID = deviceID
        let client = container.client
        let id = playlist.id
        self.read = ApiRead(in: container, eventTypes: Self.events) {
            // A **bare array** in playback order (`playlists.py:122`).
            try await client.get(MusicPlaylistPath.tracks(id), as: [LibraryTrack].self)
        }
    }

    // MARK: - what is drawn

    /// The rows, in the order to draw them.
    public var tracks: [LibraryTrack] { localOrder ?? read.data ?? [] }

    public var isFirstLoad: Bool { read.data == nil && read.loading }

    /// Whether this playlist can be edited, reordered or deleted at all.
    public var mayEdit: Bool { !playlist.isVirtual }

    /// Whether an edit may be sent right now. One at a time, as
    /// ``MusicQueueModel/busy`` is: two reorders racing each other produce an
    /// order neither tap asked for.
    public var mayWrite: Bool { !busy }

    public func mayMoveUp(_ index: Int) -> Bool { mayEdit && mayWrite && index > 0 }

    public func mayMoveDown(_ index: Int) -> Bool {
        mayEdit && mayWrite && index < tracks.count - 1
    }

    /// The room a `play` would go to: the picked chip while the server still
    /// reports it, else the first room there is, else nil.
    ///
    /// Nil is why `play` and `shuffle all` are **not drawn** with no room
    /// online: there is nothing to send them to, and the section label says so
    /// instead (`PlaylistDrawer.kt:222`).
    public func room(in rooms: [String]) -> String? {
        if let picked, rooms.contains(picked) { return picked }
        return rooms.first
    }

    /// What to say over an empty track list — the second line differs for
    /// Favorites, because the way to fill it is different
    /// (`PlaylistDrawer.kt:313`).
    public var emptyNote: (String, String)? {
        guard read.answered, tracks.isEmpty, read.error == nil else { return nil }
        return (
            MusicMoreStrings.noTracksYet,
            playlist.isVirtual
                ? MusicMoreStrings.favoriteATrackToAddIt
                : MusicMoreStrings.tapPlusToAddTracks
        )
    }

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

    /// A push arrived. The **local order is dropped**: a push means somebody
    /// else moved something, and holding this phone's optimistic order on top
    /// of a list that has since changed shows an order no house has.
    public func received(_ event: StateEvent) {
        guard Self.events.contains(event.type) else { return }
        localOrder = nil
        read.received(event)
    }

    public func serverChanged() {
        // A playlist id is one house's word. The sheet is closed rather than
        // re-read against a house that may have a playlist 4 which is
        // something else entirely ([[read-after-await-wrong-key]]).
        read.stop()
        deleted = true
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    public func pick(_ room: String) { picked = room }

    // MARK: - play, and shuffle

    /// `POST /api/music/play-playlist` for this playlist in `room`
    /// (`PlaylistDrawer.kt:96`).
    ///
    /// A room the server never reported is **refused here**, not sent: the
    /// chips only offer rooms the now-playing read named, and a model that
    /// would send any string it was handed makes that offer a courtesy rather
    /// than a rule.
    @discardableResult
    public func play(shuffle: Bool, rooms: [String]) -> Bool {
        guard let room = room(in: rooms) else { return false }
        toast = shuffle
            ? MusicMoreStrings.shufflingPlaylist(playlist.name, room: room)
            : MusicMoreStrings.playingPlaylist(playlist.name, room: room)
        let client = container.client
        let body = MusicPlaylistsModel.playBody(
            room: room,
            playlistID: playlist.id,
            shuffle: shuffle,
            deviceID: deviceID
        )
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(
                    method: "POST",
                    path: MusicPlaylistPath.playPlaylist,
                    body: body
                )
            } catch let trouble {
                self?.toast = shuffle
                    ? MusicMoreStrings.shufflePlaylistFailed(trouble.message)
                    : MusicMoreStrings.playPlaylistFailed(trouble.message)
            }
        }
        return true
    }

    // MARK: - the edits

    /// `PATCH /api/playlists/{id}` with the three fields Android's form has.
    ///
    /// The local copy is updated **after** the server agrees, not before: this
    /// is one deliberate save, so there is nothing for an optimistic write to
    /// buy, and a name shown that the server refused is a lie the next read
    /// quietly corrects.
    @discardableResult
    public func edit(name: String, description: String, emoji: String) -> Bool {
        let wanted = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard mayEdit, mayWrite, !wanted.isEmpty else { return false }
        let note = description.trimmingCharacters(in: .whitespacesAndNewlines)
        let glyph = String(emoji.trimmingCharacters(in: .whitespacesAndNewlines)
            .prefix(MusicMoreStrings.emojiMaxLength))
        busy = true
        let client = container.client
        let path = MusicPlaylistPath.one(playlist.id)
        let body = MusicModel.json([
            "name": wanted,
            "description": note,
            "cover_emoji": glyph,
        ])
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                try await client.send(method: "PATCH", path: path, body: body)
                guard let self else { return }
                self.playlist = self.playlist.with(
                    name: wanted,
                    description: note,
                    coverEmoji: glyph
                )
                self.editing = false
                self.toast = MusicMoreStrings.playlistUpdated
            } catch let trouble {
                self?.toast = MusicMoreStrings.updateFailed(trouble.message)
            }
        }
        return true
    }

    /// Move one row, optimistically, and send the whole new order.
    ///
    /// `PATCH /api/playlists/{id}/order` takes the **complete** set and
    /// refuses anything else: "the submitted set must equal the playlist's
    /// current set" (`playlists.py:318`). So this sends every id in the new
    /// order, and a list that is only partly loaded could not send a valid
    /// order at all — which is why the track read is not paged.
    @discardableResult
    public func move(from index: Int, by step: Int) -> Bool {
        guard mayEdit, mayWrite else { return false }
        var next = tracks
        let destination = index + step
        guard next.indices.contains(index), next.indices.contains(destination) else { return false }
        let moved = next.remove(at: index)
        next.insert(moved, at: destination)
        let before = localOrder
        localOrder = next
        busy = true
        let client = container.client
        let path = MusicPlaylistPath.order(playlist.id)
        let body = Self.orderBody(next.map(\.id))
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                try await client.send(method: "PATCH", path: path, body: body)
            } catch let trouble {
                guard let self else { return }
                // Rolled back to what was shown before the tap, not to nil: a
                // second arrow tapped while the first was in flight is
                // refused by ``mayWrite``, so `before` is the server's order
                // or the one previous optimistic step.
                self.localOrder = before
                self.toast = MusicMoreStrings.reorderFailed(trouble.message)
            }
        }
        return true
    }

    /// `DELETE /api/playlists/{id}/tracks/{track_id}`.
    @discardableResult
    public func remove(_ track: LibraryTrack) -> Bool {
        guard mayEdit, mayWrite else { return false }
        let label = track.displayTitle
        busy = true
        let client = container.client
        let path = MusicPlaylistPath.removeTrack(playlist.id, trackID: track.id)
        let name = playlist.name
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                try await client.send(method: "DELETE", path: path)
                guard let self else { return }
                self.localOrder = nil
                self.toast = MusicMoreStrings.removedFromPlaylist(label, playlist: name)
                self.read.refresh()
            } catch let trouble {
                self?.toast = MusicMoreStrings.removeFromPlaylistFailed(trouble.message)
            }
        }
        return true
    }

    /// `DELETE /api/playlists/{id}`. The confirmation is the screen's; this is
    /// what the confirmation calls.
    @discardableResult
    public func delete() -> Bool {
        confirmingDelete = false
        guard mayEdit, mayWrite else { return false }
        busy = true
        let client = container.client
        let path = MusicPlaylistPath.one(playlist.id)
        let name = playlist.name
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                try await client.send(method: "DELETE", path: path)
                guard let self else { return }
                self.toast = MusicMoreStrings.deletedPlaylist(name)
                self.deleted = true
            } catch let trouble {
                self?.toast = MusicMoreStrings.deletePlaylistFailed(trouble.message)
            }
        }
        return true
    }

    /// `{"track_ids":[4,7,9]}`.
    ///
    /// Hand-written rather than through ``MusicModel/json(_:)``, which has no
    /// array conformer — and does not get one, because an array is the shape
    /// where a typed writer stops paying for itself.
    nonisolated static func orderBody(_ ids: [Int64]) -> Data {
        let list = ids.map { "\($0)" }.joined(separator: ",")
        return Data("{\"track_ids\":[\(list)]}".utf8)
    }
}

extension Playlist {

    /// The same playlist with the three editable fields moved.
    ///
    /// Here rather than at the call site for ``LibraryTrack/favorited(_:)``'s
    /// reason: every field is a `let`, so an edit has to rebuild the row, and
    /// an eight-argument initialiser call inside a model is where a field
    /// added later gets silently dropped.
    func with(name: String, description: String, coverEmoji: String) -> Playlist {
        Playlist(
            id: id,
            name: name,
            description: description.isEmpty ? nil : description,
            trackCount: trackCount,
            createdAt: createdAt,
            isVirtual: isVirtual,
            coverEmoji: coverEmoji.isEmpty ? nil : coverEmoji,
            coverColor: coverColor
        )
    }
}
