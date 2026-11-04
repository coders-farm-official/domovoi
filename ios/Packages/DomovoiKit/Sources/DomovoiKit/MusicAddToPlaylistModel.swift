import Foundation
import Observation

/// "Add to playlist" for one track: which playlists hold it, all of them, and
/// one tap to toggle.
///
/// `AddToPlaylistSheet.kt` as a type. Three things in it are easy to get wrong
/// and each one cost a real control somewhere:
///
/// 1. **Favorites routes through the library, not through the playlists.**
///    `POST /api/playlists/0/tracks` is a **400** whose own detail says to use
///    `PATCH /api/music/library/{track_id} {favorited: true}` instead
///    (`playlists.py:382`). A port that forgets this ships a row that fails
///    every time it is tapped — [[no-misleading-controls]] exactly. The branch
///    is ``isFavorites(_:)`` and it is the first thing ``toggle(_:)`` tests.
/// 2. **A 409 is not a failure.** The server answers "track N is already in
///    this playlist" (`playlists.py:438`) when the track is already there,
///    which is the state the person asked for. Android's bulk add counts those
///    apart from real failures and says "added 3 tracks, 1 already in"
///    (`MusicScreen.kt:255-264`); a single add says the same thing and nothing
///    about failing.
/// 3. **The toast names the row that was tapped**, which is why the name is
///    captured before the request rather than read back out of a list that
///    the answer's own refresh may have reordered.
@MainActor
@Observable
public final class MusicAddToPlaylistModel {

    public static let events: Set<String> = ["playlists.changed"]

    // MARK: - what is read

    /// Which playlists hold this track — `GET /api/music/library/{id}/playlists`,
    /// a **bare array**, with Favorites (id 0) first when the row is
    /// favourited (`music.py:341`).
    public let memberships: ApiRead<[Playlist]>

    /// Every playlist, Favorites included and pinned on top by the server.
    public let all: ApiRead<[Playlist]>

    // MARK: - what it is about

    public private(set) var track: LibraryTrack

    /// The name typed into the create field.
    public var newName = ""

    public private(set) var busy = false

    public private(set) var toast: String?

    /// Whether ``toast`` is a **failure** rather than an outcome.
    ///
    /// The sheets report their own outcomes inline now (a page sheet covers
    /// the shell's toast, so a message forwarded there was drawn behind the
    /// sheet that was still up and nobody ever saw it), and an inline line
    /// has to be inked: a failure is `errInk`, everything else `fgMuted`.
    /// Sniffing the sentence for the word "failed" would be a classifier over
    /// wording, so the flag is set where the sentence is.
    public private(set) var toastFailed = false

    /// Every ``toast`` goes through here, so the flag cannot drift from the
    /// sentence beside it.
    private func say(_ said: String, failed: Bool = false) {
        toast = said
        toastFailed = failed
    }

    /// Set once a write has gone through, so the screen can close the sheet
    /// and tell the list behind it to re-read (Android's `onMutated`).
    public private(set) var mutated = false

    private let container: AppContainer
    private var eventSink: Int?

    public init(track: LibraryTrack, container: AppContainer) {
        self.track = track
        self.container = container
        let client = container.client
        let id = track.id
        self.memberships = ApiRead(in: container, eventTypes: Self.events) {
            try await client.get(
                MusicPlaylistPath.memberships(trackID: id),
                as: [Playlist].self
            )
        }
        self.all = ApiRead(in: container, eventTypes: Self.events) {
            try await client.get(MusicPlaylistPath.all, as: [Playlist].self)
        }
    }

    // MARK: - what is drawn

    /// Every row the sheet offers, in the server's order — **Favorites
    /// included**, because toggling it is the one way to favourite a track
    /// from this sheet and leaving it out would hide a thing the sheet can do.
    public var playlists: [Playlist] { all.data ?? [] }

    /// The ids this track is in.
    public var memberIDs: Set<Int64> {
        Set((memberships.data ?? []).map(\.id))
    }

    public func holdsTrack(_ playlist: Playlist) -> Bool {
        memberIDs.contains(playlist.id)
    }

    /// A row's second line: `"in this playlist"`, or its count
    /// (`AddToPlaylistSheet.kt:191`).
    public func subtitle(_ playlist: Playlist) -> String {
        holdsTrack(playlist)
            ? MusicMoreStrings.inThisPlaylist
            : MusicMoreStrings.trackCountText(playlist.trackCount)
    }

    public var isFirstLoad: Bool { all.data == nil && all.loading }

    /// What to say when there are no playlists at all. The create field below
    /// it is the answer, which is why this sentence points at it
    /// (`AddToPlaylistSheet.kt:155`).
    public var emptyNote: String? {
        guard all.answered, playlists.isEmpty, all.error == nil else { return nil }
        return MusicMoreStrings.noPlaylistsCreateOne
    }

    public var mayCreate: Bool {
        !busy && !newName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Whether this row is the virtual Favorites view.
    ///
    /// By ``Playlist/isVirtual`` **and** by the id, because they are two
    /// different claims and only one of them is the server's rule: the id is
    /// `FAVORITES_VIRTUAL_ID` in `playlists.py` and is what every write route
    /// refuses, while `is_virtual` is a flag a future composed playlist would
    /// also carry. Either one routing this row through the library PATCH is
    /// the safe way round — a real playlist never has id 0.
    nonisolated public static func isFavorites(_ playlist: Playlist) -> Bool {
        playlist.isVirtual || playlist.id == MusicPlaylistPath.favoritesID
    }

    // MARK: - lifecycle

    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        memberships.refresh()
        all.refresh()
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        memberships.stop()
        all.stop()
    }

    public func received(_ event: StateEvent) {
        memberships.received(event)
        all.received(event)
    }

    public func takeToast() -> String? {
        defer { toast = nil; toastFailed = false }
        return toast
    }

    public func takeMutated() -> Bool {
        defer { mutated = false }
        return mutated
    }

    // MARK: - the one tap

    /// Add this track to `playlist`, or take it out of it.
    ///
    /// Favorites goes through `PATCH /api/music/library/{id} {favorited}`; a
    /// real playlist through `POST`/`DELETE …/tracks`.
    @discardableResult
    public func toggle(_ playlist: Playlist) -> Bool {
        guard !busy else { return false }
        let inIt = holdsTrack(playlist)
        // Captured **before** the await, all three of them: the answer lands
        // after a refresh may have reordered the list, and a toast built from
        // `playlists[index]` afterwards names whichever row is there now.
        // That is [[read-after-await-wrong-key]] in its smallest shape.
        let name = playlist.name
        let label = track.displayTitle
        let trackID = track.id
        let favorites = Self.isFavorites(playlist)
        busy = true
        let client = container.client

        let method: String
        let path: String
        let body: Data?
        if favorites {
            method = "PATCH"
            path = MusicPlaylistPath.libraryRow(trackID)
            body = MusicModel.json(["favorited": !inIt])
        } else if inIt {
            method = "DELETE"
            path = MusicPlaylistPath.removeTrack(playlist.id, trackID: trackID)
            body = nil
        } else {
            method = "POST"
            path = MusicPlaylistPath.addTrack(playlist.id)
            body = MusicModel.json(["track_id": trackID])
        }

        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                try await client.send(method: method, path: path, body: body)
                guard let self else { return }
                self.say(Self.note(
                    favorites: favorites,
                    removing: inIt,
                    playlist: name,
                    track: label
                ))
                self.settled()
            } catch let trouble {
                guard let self else { return }
                if trouble.failure?.status == 409 {
                    // Already there: the state that was asked for. Said as
                    // such, and the reads are still refreshed, because the
                    // memberships this phone holds were out of date.
                    self.say(MusicMoreStrings.alreadyInPlaylist(label, playlist: name))
                    self.settled()
                    return
                }
                self.say(MusicMoreStrings.addToPlaylistFailed(trouble.message), failed: true)
                // The memberships this phone showed were wrong about something;
                // re-read rather than leaving a tick on a row the server
                // refused.
                self.memberships.refresh()
                self.all.refresh()
            }
        }
        return true
    }

    /// Create a playlist and put this track in it — two calls, one gesture
    /// (`AddToPlaylistSheet.kt:116`).
    ///
    /// The new id comes **out of the 201's body**. There is no other way to
    /// learn it: `POST /api/playlists` answers the created playlist
    /// (`playlists.py:179`) and nothing else names it.
    @discardableResult
    public func create() -> Bool {
        let wanted = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !busy, !wanted.isEmpty else { return false }
        let label = track.displayTitle
        let trackID = track.id
        busy = true
        let client = container.client
        let body = MusicModel.json(["name": wanted])
        Task { @MainActor [weak self] in
            defer { self?.busy = false }
            do throws(DomovoiError) {
                let created = try await client.post(
                    MusicPlaylistPath.create,
                    body: body,
                    as: Playlist.self
                )
                // Zero would be `Playlist`'s default for a missing `id`, and
                // zero is Favorites — adding to it is the 400 above. A 201
                // without an id is a server this app does not understand, so
                // the create is reported and the add is not attempted.
                guard created.id != MusicPlaylistPath.favoritesID else {
                    self?.say(MusicMoreStrings.createFailed(MusicMoreStrings.createGaveNoID), failed: true)
                    self?.settled()
                    return
                }
                try await client.send(
                    method: "POST",
                    path: MusicPlaylistPath.addTrack(created.id),
                    body: MusicModel.json(["track_id": trackID])
                )
                guard let self else { return }
                self.newName = ""
                self.say(MusicMoreStrings.createdAndAdded(wanted, title: label))
                self.settled()
            } catch let trouble {
                self?.say(MusicMoreStrings.createFailed(trouble.message), failed: true)
            }
        }
        return true
    }

    /// A write went through: the reads are stale and the list behind the sheet
    /// is too.
    private func settled() {
        mutated = true
        memberships.refresh()
        all.refresh()
    }

    /// Which sentence a successful toggle says.
    ///
    /// `nonisolated static` so a test can state all four without a model: the
    /// Favorites pair is the one that must not read like a playlist's, because
    /// Favorites is not one.
    nonisolated static func note(
        favorites: Bool,
        removing: Bool,
        playlist: String,
        track: String
    ) -> String {
        if favorites {
            return removing
                ? MusicMoreStrings.unfavorited(track)
                : MusicMoreStrings.favorited(track)
        }
        return removing
            ? MusicMoreStrings.removedFromPlaylistShort(playlist)
            : MusicMoreStrings.addedToPlaylist(playlist)
    }
}
