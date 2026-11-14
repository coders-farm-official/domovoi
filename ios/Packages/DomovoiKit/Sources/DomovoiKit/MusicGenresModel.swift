import Foundation
import Observation

/// Music's `genres` tab: the library's genres a page at a time, searched and
/// sorted by the server, with how far the genre read has got.
///
/// `GenresTab.kt`'s `rememberGenresTab` and `genresTab`, as a type, for
/// ``MusicModel``'s reason: the debounce, the re-read while reading and the
/// "no genre" rule are specified by `MusicGenresTests` here and by nothing
/// that runs over a composable there.
///
/// ### Built the first time the tab is opened
///
/// ``MusicView`` holds this as an optional built on first use, as it does the
/// room queue and the playlists — a person who never opens Genres sends none
/// of its reads (Android's `if (ui.tab == GENRES) rememberGenresTab(ui)`).
///
/// ### The re-read while the server reads tags
///
/// Every answer says whether the background tag read is done. While it is not,
/// the list is re-read every ``MusicGenres/readPollMs`` **keeping its place**
/// (``Pager/reload(fresh:)``), so the genres and the "N of M" fill in without
/// a pull. The loop is this model's, not a view's — a row never holds a
/// `Task` — and it ends by itself the first time an answer says `reading:
/// false`, or when the tab goes.
@MainActor
@Observable
public final class MusicGenresModel {

    /// `GenresTab.kt:181`: a rescan or a tag pass changes the genres.
    public static let events: Set<String> = ["library.indexer.changed"]

    // MARK: - what is read

    /// The genres, a page at a time. Rebuilt for a new search or sort, as
    /// ``MusicModel/library`` is, because the path is baked into the fetch.
    public private(set) var list: Pager<MusicGenreRow, String>

    /// How far the genre read has got, or nil before any answer. Library-wide
    /// (``MusicGenreProgress``), so kept across a new search — and dropped on
    /// a new server.
    public private(set) var progress: MusicGenreProgress?

    // MARK: - what the person has chosen

    /// The search as typed. Debounced; the list follows ``loadedSearch``.
    public var search = "" {
        didSet {
            guard search != oldValue else { return }
            scheduleSearch()
        }
    }

    /// The sort: one deliberate tap, so not debounced.
    public var sort: MusicGenreSort = .name {
        didSet {
            guard sort != oldValue else { return }
            debounce?.cancel()
            apply()
        }
    }

    public private(set) var loadedSearch = ""
    public private(set) var loadedSort: MusicGenreSort = .name

    // MARK: - what it was built from

    private let container: AppContainer
    private let wait: @Sendable (Int64) async throws -> Void
    private var debounce: Task<Void, Never>?
    private var poll: Task<Void, Never>?
    private var eventSink: Int?
    private var onScreen = false

    /// The server ``progress`` was read from. An answer for another one is
    /// dropped on landing ([[read-after-await-wrong-key]]).
    private var progressServer: String?

    public init(
        container: AppContainer,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.container = container
        self.wait = wait
        // A placeholder that reads nothing, so that every stored property is
        // set before the real pager captures `self`.
        self.list = Pager(keyOf: { $0.key }) { _ in
            Page(items: [], total: 0, serverHasMore: false, nextCursor: nil)
        }
        self.list = makePager(search: "", sort: .name)
    }

    /// One pager over a search and sort. The fetch decodes the page **and**
    /// the progress from one answer, and hands the progress back tagged with
    /// the server it was asked of.
    private func makePager(search: String, sort: MusicGenreSort) -> Pager<MusicGenreRow, String> {
        let client = container.client
        let store = container.store
        let base = MusicGenres.path(search: search, sort: sort)
        return Pager(
            in: container,
            eventTypes: Self.events,
            keyOf: { $0.key }
        ) { [weak self] request in
            let asked = store.serverURL
            let answer = try await client.get(Paging.path(base, request), as: MusicGenreAnswer.self)
            await self?.landed(answer.progress, server: asked)
            return answer.page
        }
    }

    // MARK: - what is drawn

    /// The rows to draw, "no genre" last once the whole list is in and no
    /// search narrows it — neither the typed one nor the loaded one.
    public var rows: [MusicGenreRow] {
        let narrowed = search + loadedSearch
        return MusicGenres.listRows(
            list.state.items,
            progress: progress,
            search: narrowed,
            hasMore: list.state.hasMore
        )
    }

    public var readingLine: String? { MusicGenres.readingLine(progress) }

    public var reading: Bool { progress?.reading == true }

    /// The spinner, for a first read only.
    public var isFirstLoad: Bool { !list.state.loaded && list.state.loading && rows.isEmpty }

    /// `"12 genres"`, or nil before an answer (`GenresTab.kt:254`).
    public var countText: String? {
        guard list.state.loaded else { return nil }
        return MusicGenresStrings.genres(list.state.total ?? list.state.items.count)
    }

    /// What an empty list says: the search matched none, or there are none
    /// (yet). Nil when there are rows, before an answer, and over a failure,
    /// which says itself.
    public var emptyNote: (title: String, note: String)? {
        let state = list.state
        guard state.loaded, state.error == nil, rows.isEmpty else { return nil }
        let typed = loadedSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        if !typed.isEmpty {
            return (MusicGenresStrings.noGenresMatch, MusicGenresStrings.searchedFor(typed))
        }
        if reading {
            return (MusicGenresStrings.noGenresFoundYet, MusicGenresStrings.genresAppearAsRead)
        }
        return (MusicGenresStrings.noGenresYet, MusicGenresStrings.genresAppearFromTags)
    }

    /// Whether the empty state offers `clear search`.
    public var mayClearSearch: Bool {
        !loadedSearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// ``MusicModel/shouldLoadMore(lastVisible:)``'s guarded question.
    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard list.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: list.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.listThreshold
        )
    }

    // MARK: - lifecycle

    public func start() {
        onScreen = true
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        // ``MusicModel/start()``'s order, for its reason: `resume` re-keys a
        // pager that ``stop()`` took off the server registry.
        list.resume()
        if !list.state.loaded { list.reset() }
        if reading { startPolling() }
    }

    public func stop() {
        onScreen = false
        debounce?.cancel(); debounce = nil
        poll?.cancel(); poll = nil
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        list.stop()
    }

    public func received(_ event: StateEvent) { list.received(event) }

    /// The saved server changed: the old house's read progress is not this
    /// one's.
    public func serverChanged() {
        progress = nil
        progressServer = nil
        poll?.cancel(); poll = nil
        list.serverChanged()
    }

    /// Clear the search (the empty state's one control).
    public func clearSearch() {
        search = ""
        debounce?.cancel()
        apply()
    }

    // MARK: - the read

    /// A page landed with this progress, asked of `server`.
    func landed(_ fresh: MusicGenreProgress, server: String) {
        guard server == container.store.serverURL else { return }
        progress = fresh
        progressServer = server
        if fresh.reading {
            startPolling()
        } else {
            poll?.cancel(); poll = nil
        }
    }

    private func startPolling() {
        guard poll == nil, onScreen else { return }
        let sleeper = wait
        poll = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await sleeper(MusicGenres.readPollMs)
                guard !Task.isCancelled, let self else { return }
                guard self.onScreen, self.reading else {
                    self.poll = nil
                    return
                }
                self.list.reload()
            }
        }
    }

    private func scheduleSearch() {
        debounce?.cancel()
        let sleeper = wait
        debounce = Task { @MainActor [weak self] in
            try? await sleeper(MusicGenres.searchDebounceMs)
            guard !Task.isCancelled else { return }
            self?.apply()
        }
    }

    /// A new list from page one over a new pager, when the query moved.
    private func apply() {
        guard search != loadedSearch || sort != loadedSort else { return }
        list.stop()
        loadedSearch = search
        loadedSort = sort
        list = makePager(search: search, sort: sort)
        list.reset()
    }
}
