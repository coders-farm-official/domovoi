import Foundation
import Observation

/// Music's reads, its query and the two things a tap can do.
///
/// `MusicScreen.kt`'s first 350 lines — the reads, the debounce, the pager,
/// the local tick and the actions — as a type, for the reason ``HomeModel``
/// gives: `MusicScreen.kt` has no test, because Android's JVM suite is
/// unit-only, so the debounce, the reset-on-filter-change and the
/// edit-in-place are specified by nothing at all over there. Here they are
/// specified by `MusicModelTests`.
///
/// ### What this batch builds, and what it deliberately leaves alone
///
/// **Built**: the library list (search, the source filter, the sort, the
/// favourites switch, load-as-you-scroll), the stats count, the now-playing
/// strip as *information*, favouriting a library row, and playing a library
/// row in a room.
///
/// **Not built here, but built since** (2026-11-02, corrected because this
/// list had gone stale and a doc comment that is wrong about what exists is
/// worse than none): the room queue is ``MusicQueueModel``, the stats tab is
/// ``MusicStatTile``, rescan and enrich are ``MusicRoomActions``, the track
/// drawer is `MusicTrackSheet`, playlists are ``MusicPlaylistsModel`` and
/// ``MusicPlaylistDetailModel``, lyrics are ``MusicLyricsStore`` and
/// ``MusicRoomLyricsModel``, cover art is ``MusicCoverStore``, and the cast
/// planner is ``MusicCastPlanner``.
///
/// **Still not built, and no control claims otherwise**: uploading a file from
/// the phone, and saving a track to it. The rule they are left out under is
/// the owner's own: a control that cannot do what it says is not drawn
/// ([[no-misleading-controls]]).
///
/// The player tab — this phone's own playback — **is** built: it needed an
/// audio engine and ``PlayerModelMusicEngine`` conforms to
/// ``MusicMorePlayerEngine`` since D46, registered from `MusicView`'s `.task`.
/// It is still absent from a build with no audio engine, which is what
/// ``MusicTab/shown(hasPlayer:)`` is for.
@MainActor
@Observable
public final class MusicModel {

    // MARK: - the reads

    /// The library, a page at a time. Rebuilt whenever the query changes,
    /// because a new filter is a new list from page one.
    public private(set) var library: Pager<LibraryTrack, Int64>

    public let stats: ApiRead<LibraryStats>
    public let nowPlaying: ApiRead<[NowPlayingRoom]>

    // MARK: - what the person has chosen

    /// The query, **as typed**. Writing it schedules the debounce; the
    /// library is not re-read until it settles.
    public var query = MusicLibraryQuery() {
        didSet {
            guard query != oldValue else { return }
            scheduleQueryChange(from: oldValue)
        }
    }

    /// The query the library is actually loaded for. Lags ``query`` by the
    /// debounce while someone is typing.
    public private(set) var loadedQuery = MusicLibraryQuery()

    /// The source slugs seen on the pages fetched so far, in the order they
    /// were first seen (``MusicPage/addingSources(seen:from:)``).
    public private(set) var seenSources: [String] = []

    /// Seconds since the last now-playing answer, for the elapsed line.
    public private(set) var tick = 0

    /// The last thing that went wrong that the person should be told about —
    /// a failed play, a failed favourite. Read and cleared by the screen.
    public private(set) var toast: String?

    // MARK: - what it was built from

    /// Internal rather than private: ``MusicRoomActions`` is an extension in
    /// this module and every one of its actions needs the client.
    let container: AppContainer
    private let clock: @Sendable () -> Int64

    /// How the debounce waits.
    ///
    /// Injected for ``HomeModel``'s reason: a test that has to say "typing
    /// did not send a request, and then it did" cannot say it over a real
    /// 250 ms, and a suite that sleeps for every debounce it specifies is a
    /// suite that gets its debounces specified once.
    private let wait: @Sendable (Int64) async throws -> Void
    private var debounce: Task<Void, Never>?
    private var tickLoop: Task<Void, Never>?
    private var eventSink: Int?
    private var onScreen = false

    /// How long typing settles for before the library is re-read
    /// (`MusicScreen.kt:103`'s `delay(250)`).
    public static let searchDebounceMs: Int64 = 250

    /// How often the elapsed line moves on its own between pushes.
    public static let tickMs: Int64 = 1_000

    /// The pushes each read listens for — `MusicScreen.kt:120-152`.
    public static let libraryEvents: Set<String> = ["library.indexer.changed"]
    public static let nowPlayingEvents: Set<String> = ["music.now_playing.changed"]

    public init(
        container: AppContainer,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.container = container
        self.clock = clock
        self.wait = wait
        let client = container.client

        self.stats = ApiRead(in: container, eventTypes: Self.libraryEvents) {
            try await client.get("/api/music/library/stats", as: LibraryStats.self)
        }
        self.nowPlaying = ApiRead(in: container, eventTypes: Self.nowPlayingEvents) {
            try await client.get("/api/music/now-playing", as: [NowPlayingRoom].self)
        }
        self.library = Self.pager(for: MusicLibraryQuery(), in: container)

        // The tick goes back to zero when a now-playing answer lands, because
        // the server's `elapsed_sec` is canonical and the tick is additive on
        // top of it. `onValue` is the read's own success path, which is the
        // seam that exists so this is not a line in a view somebody forgets —
        // `MusicScreen.kt:152` is `LaunchedEffect(nowPlaying.data) { tick = 0 }`
        // and keys on the data *object*, so it moves when a read returns and
        // not when one fails.
        self.nowPlaying.onValue = { [weak self] in
            self?.tick = 0
            self?.collectSources()
        }
        self.library.onLoaded = { [weak self] in self?.collectSources() }
    }

    /// One library pager over `query`.
    ///
    /// `fresh` is never asked for: the library is not a filesystem list from
    /// the app's point of view — a rescan is the server's `reindex`, which is
    /// a later batch's button.
    private static func pager(
        for query: MusicLibraryQuery,
        in container: AppContainer
    ) -> Pager<LibraryTrack, Int64> {
        let client = container.client
        let base = query.path
        return Pager(
            in: container,
            eventTypes: Self.libraryEvents,
            keyOf: { $0.id }
        ) { request in
            try await client.get(Paging.path(base, request), as: Page<LibraryTrack>.self)
        }
    }

    // MARK: - what is drawn

    /// The rooms any "play in a room" control may offer.
    public var rooms: [NowPlayingRoom] { nowPlaying.data ?? [] }

    public var castRooms: [String] { MusicPage.castRooms(rooms) }

    public var playingCount: Int { MusicPage.playingCount(rooms) }

    /// The room that actually has sound coming out of it, or nil.
    ///
    /// What the room queue opens on: "the queue you opened this tab to look
    /// at" (`QueueTab.kt:114`). Deliberately **not**
    /// ``MusicPage/defaultRoom(_:)``, which falls back to the first room
    /// there is: that fallback belongs to the queue model, which has to
    /// decide between a room it was already showing and a new one, and a
    /// value that has already fallen back cannot be told apart from a real
    /// answer.
    public var playingRoom: String? { rooms.first(where: \.isPlaying)?.roomID }

    /// `stats.total_tracks`, or nil while that read has not answered — and
    /// nil is **not** zero anywhere it is used.
    public var libraryTotal: Int? { stats.data?.totalTracks }

    public var header: String {
        MusicStrings.header(tracks: libraryTotal, roomsPlaying: playingCount)
    }

    public var sourceOptions: [String] {
        MusicPage.sourceOptions(seen: seenSources, chosen: query.source)
    }

    /// `"showing 100 of 5,232 tracks"`, or nil while nothing has loaded.
    public var countText: String? {
        guard library.state.loaded else { return nil }
        return Paging.countText(
            shown: library.state.items.count,
            total: library.state.total ?? libraryTotal,
            noun: "tracks"
        )
    }

    /// What to say when the list came back empty, or nil when it did not.
    public var emptyNote: String? {
        let list = library.state
        guard list.loaded, list.items.isEmpty, list.error == nil else { return nil }
        return loadedQuery.emptyNote(libraryTotal: libraryTotal)
    }

    /// Whether to ask for the next page now, given what the list is showing.
    ///
    /// Through ``Paging/nearEnd(totalItems:lastVisible:threshold:)`` and its
    /// guard, never through a bare `onAppear` on the last row — which is the
    /// iOS shape of the trap that had Music asking for page two before anyone
    /// had scrolled.
    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard library.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: library.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.listThreshold
        )
    }

    // MARK: - lifecycle

    /// The screen appeared: read everything, start the tick.
    public func start() {
        onScreen = true
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        stats.refresh()
        nowPlaying.refresh()
        // `resume` before the test for a reset, because the branch that keeps
        // the rows is the branch that would otherwise come back unkeyed:
        // ``Pager/stop()`` left the server registry and only a read rejoins.
        library.resume()
        if !library.state.loaded { library.reset() }
        restartTickLoop()
    }

    /// The screen is gone. "A route holds nothing" (`Pagination.swift`): the
    /// pager, the reads and the tick all stop, and coming back re-reads.
    public func stop() {
        onScreen = false
        tickLoop?.cancel(); tickLoop = nil
        debounce?.cancel(); debounce = nil
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        stats.stop()
        nowPlaying.stop()
        library.stop()
    }

    /// A push arrived. Each read filters by type itself, exactly as the web
    /// hooks do; nothing ever sends a narrower subscription (map §1.2).
    public func received(_ event: StateEvent) {
        stats.received(event)
        nowPlaying.received(event)
        library.received(event)
    }

    /// The saved server changed under us.
    public func serverChanged() {
        stats.serverChanged()
        nowPlaying.serverChanged()
        library.serverChanged()
        seenSources = []
    }

    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }

    /// The one writer of ``toast``.
    ///
    /// Internal, for the extension in ``MusicRoomActions`` — and a method
    /// rather than loosening `private(set)`, so that "what can set a toast"
    /// stays a list `grep` can produce.
    func setToast(_ said: String) { toast = said }

    // MARK: - the query, and the one place a new list is built

    /// A query change: the search debounces, everything else is immediate.
    ///
    /// The split is Android's (`MusicScreen.kt:101`): `qDebounced` is the only
    /// value behind a `delay`, because a sort or a filter is one deliberate
    /// tap while a search is one keystroke of several. Sending a request per
    /// keystroke is the thing the debounce exists to stop, and debouncing a
    /// tap would just make the screen feel broken.
    private func scheduleQueryChange(from old: MusicLibraryQuery) {
        debounce?.cancel()
        let searchOnly = old.search != query.search
            && old.source == query.source
            && old.sort == query.sort
            && old.favoritedOnly == query.favoritedOnly
        guard searchOnly else {
            applyQuery()
            return
        }
        let sleeper = wait
        debounce = Task { @MainActor [weak self] in
            try? await sleeper(Self.searchDebounceMs)
            guard !Task.isCancelled else { return }
            self?.applyQuery()
        }
    }

    /// A new list from page one, over a new pager.
    ///
    /// **A new pager rather than a reset**, because the path is baked into
    /// the old one's `fetch`. The alternative — a pager whose fetch reads a
    /// mutable query — is how a page one for `"bea"` comes back carrying rows
    /// for `"beatles"`; the generation counter in ``Pager`` covers a reset,
    /// not a closure reading state that has since moved.
    private func applyQuery() {
        guard query != loadedQuery else { return }
        library.stop()
        loadedQuery = query
        library = Self.pager(for: query, in: container)
        library.onLoaded = { [weak self] in self?.collectSources() }
        library.reset()
    }

    private func collectSources() {
        seenSources = MusicPage.addingSources(seen: seenSources, from: library.state.items)
    }

    private func restartTickLoop() {
        tickLoop?.cancel()
        let sleeper = wait
        tickLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await sleeper(Self.tickMs)
                guard !Task.isCancelled, let self, self.onScreen else { return }
                // Only while something is playing. Android ticks
                // unconditionally; a tick nothing reads is a wakeup a second
                // for a screen with four idle rooms on it, and
                // ``NowPlayingRoom/elapsed(tick:)`` ignores it for every room
                // that is not playing anyway.
                guard self.playingCount > 0 else { continue }
                self.tick += 1
            }
        }
    }

    // MARK: - the two things a tap can do

    /// Favourite, or un-favourite, one library row.
    ///
    /// `MusicScreen.kt:227`. The loaded row **changes in place** — no re-read
    /// of every page scrolled through — and under "favourites only" an
    /// un-favourited track leaves the list, because it no longer matches what
    /// the person asked to see.
    public func toggleFavorite(_ track: LibraryTrack) {
        let wanted = !track.favorited
        let id = track.id
        let narrowed = loadedQuery.favoritedOnly
        let client = container.client
        Task { @MainActor [weak self] in
            // `do throws(DomovoiError)`, not a bare `do`: the only thing in
            // here that can fail is the request, and saying so is what lets
            // the catch read `trouble.message` — the client's own sentence —
            // instead of stringifying an `any Error`.
            do throws(DomovoiError) {
                try await client.send(
                    method: "PATCH",
                    path: "/api/music/library/\(id)",
                    body: Self.json(["favorited": wanted])
                )
                guard let self else { return }
                if !wanted, narrowed {
                    self.library.remove(id)
                } else {
                    self.library.update(id) { $0.favorited(wanted) }
                }
                self.nowPlaying.refresh()
            } catch let trouble {
                self?.toast = MusicStrings.favoriteFailed(trouble.message)
            }
        }
    }

    /// Play one library row in one room — `POST /api/music/play-track`
    /// (`TrackDrawer.kt:97`).
    ///
    /// A room this phone has not seen in a now-playing answer is **refused
    /// here**, not sent: the picker only offers rooms the server reported
    /// (``MusicPage/castRooms(_:)``), and a model that would send any string
    /// it was handed makes that offer a courtesy rather than a rule.
    @discardableResult
    public func play(_ track: LibraryTrack, in room: String) -> Bool {
        guard castRooms.contains(room) else { return false }
        let id = track.id
        let label = track.displayTitle
        let client = container.client
        Task { @MainActor [weak self] in
            // `do throws(DomovoiError)`, not a bare `do`: the only thing in
            // here that can fail is the request, and saying so is what lets
            // the catch read `trouble.message` — the client's own sentence —
            // instead of stringifying an `any Error`.
            do throws(DomovoiError) {
                try await client.send(
                    method: "POST",
                    path: "/api/music/play-track",
                    body: Self.json(["room_id": room, "track_id": id])
                )
                guard let self else { return }
                self.toast = MusicStrings.queuedInRoom(label, room: room)
                self.nowPlaying.refresh()
            } catch let trouble {
                self?.toast = MusicStrings.playFailed(trouble.message)
            }
        }
        return true
    }

    /// A tiny JSON body writer, for the two posts above.
    ///
    /// Hand-rolled over `JSONSerialization` because the values are two kinds
    /// — a string and an `Int64` — and a `[String: Any]` on the way to a
    /// `Sendable` closure is the untyped hole this package does not have.
    ///
    /// `nonisolated` because it is a pure function of its arguments and the
    /// model it lives in is `@MainActor`: without it, a test of the bytes a
    /// body has would have to be a main-actor test, which is a test about
    /// where it was called from rather than about what it wrote.
    nonisolated static func json(_ fields: KeyValuePairs<String, any MusicJSONValue>) -> Data {
        let body = fields.map { "\"\($0.key)\":\($0.value.jsonLiteral)" }.joined(separator: ",")
        return Data("{\(body)}".utf8)
    }
}

/// What ``MusicModel/json(_:)`` can write. Three conformers and no more: a
/// body this app sends is a known shape, and the point of the protocol is
/// that an unknown one does not compile.
public protocol MusicJSONValue: Sendable {
    var jsonLiteral: String { get }
}

extension String: MusicJSONValue {
    /// Escaped for JSON: a room name is a person's word and may hold a quote
    /// or a backslash. Only the two characters that can break the literal,
    /// plus the control characters JSON forbids bare.
    public var jsonLiteral: String {
        var out = "\""
        for character in unicodeScalars {
            switch character {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if character.value < 0x20 {
                    out += String(format: "\\u%04x", character.value)
                } else {
                    out.unicodeScalars.append(character)
                }
            }
        }
        return out + "\""
    }
}

extension Int64: MusicJSONValue {
    public var jsonLiteral: String { "\(self)" }
}

extension Bool: MusicJSONValue {
    public var jsonLiteral: String { self ? "true" : "false" }
}
