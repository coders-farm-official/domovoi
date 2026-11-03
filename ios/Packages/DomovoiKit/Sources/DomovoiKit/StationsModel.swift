import Foundation
import Observation

/// The Stations screen: the favourites, the ten most recent, the two-scope
/// search, and every write a phone is allowed to make to the radio plugin.
///
/// `StationsScreen.kt:84` and `StationSearch.kt:84`, whose state is nineteen
/// `remember`s and three `LaunchedEffect`s spread over two composables and
/// their rows. It is a model here because what the star does, which id it
/// keeps, which of four sentences a search's emptiness gets, whether a
/// station can be played at all and whether an answer that arrived late still
/// belongs to the house on screen are all **decisions**, and a view cannot be
/// asked whether it got them right.
///
/// ### What this does not do, and why there is no control for it
///
/// **`import fcc fm`.** `POST /fcc-import` carries no `@device_endpoint`, so
/// it sits on the radio module's admin default and the module's docstring
/// says so in as many words (`web.py:38-40`). This app has no admin session
/// and cannot get one, so the button would 401 from every phone every time —
/// Files' delete hit the same wall (``FileStrings/deleteNeedsAnAdmin``). The
/// screen says so once, where Android puts the button
/// (``StationsStrings/importIsAnAdminJob``), and this model has no method for
/// it. Android's own button is worse than useless: it decodes the answer as
/// `{state, inserted, updated}` where the route answers
/// `start_import_job`'s `{started, state, result, error}`, so its toast
/// always reads "fcc running: 0 new, 0 updated" about a job that has not
/// started work (MAP §8.1, reported, not fixed — `android/` is out of
/// bounds).
///
/// **Playing, until the `player` item merges.** ``StationsPlayback`` is the
/// seam; a witness whose `canPlay` is false draws no play affordance at all
/// and ``play(_:)`` sends nothing, because `POST /play` stamps the station
/// into Recent and is never sent for its side-effect alone.
///
/// **Pages.** Android's `prev` / `next` footers, its `page n / m` line, its
/// `"nothing on this page"` empty and the `LaunchedEffect` that steps back a
/// page when unfavouriting empties the last one are all gone, under the
/// owner's standing decision of 2026-10-11: both lists load as you scroll.
/// None of those four things means anything in a list with no pages. The
/// favourites **count** stays, from `/badge`.
@MainActor
@Observable
public final class StationsModel {

    /// `radio.stations.changed` — the plugin's manifest declares
    /// `realtime_channel = "radio.stations"` and `realtime.py:375`
    /// broadcasts `f"{channel}.changed"`, so this is the type Android
    /// subscribes to and the type that arrives.
    public static let listEvents: Set<String> = ["radio.stations.changed"]

    /// `FAVORITES_PAGE_SIZE` (`StationsScreen.kt:79`).
    public static let favoritesPageSize = 20

    /// `PAGE_SIZE` (`StationSearch.kt:37`).
    public static let searchPageSize = 30

    /// `StationSearch.kt:119`'s `delay(220)`.
    public static let instantFavoritesDebounceMs: Int64 = 220

    private let container: AppContainer
    private let playback: any StationsPlayback
    private let wait: @Sendable (Int64) async throws -> Void
    private let clock: @Sendable () -> Int64

    // MARK: - the reads

    /// Every favourite, twenty at a time, newest-sorted by the server
    /// (`favorited DESC, name ASC, id ASC`).
    public let favorites: Pager<StationsRow, String>

    /// The last ten played. **Not paged**, by design: the server trims the
    /// table to ten (`web.py:72`, `_trim_recents`), so there is no longer
    /// history to ask for.
    public let recent: ApiRead<[StationsRow]>

    /// The favourites total, which is all that is left of Android's use of
    /// `/badge` (it also made it the page count).
    public let badge: ApiRead<StationsBadge>

    // MARK: - the search surface

    public private(set) var scope: StationsScope = .online

    /// What is typed, which is **not** what was asked for: the directory is a
    /// network hop and is not touched per keystroke.
    public private(set) var query = ""

    /// The country box, online scope only. Two letters, upper-cased, as
    /// Android's `it.take(2).uppercase()` does (`StationSearch.kt:212`).
    public private(set) var country = "US"

    /// The query the results on screen were asked with. Named back in the
    /// "nothing matches" line, so the message is about what is on screen
    /// rather than whatever has been typed since.
    public private(set) var asked = ""

    /// The scope the results on screen were asked in — the same reason: the
    /// four empty sentences are per scope, and flipping the scope empties the
    /// list rather than relabelling it.
    public private(set) var askedScope: StationsScope = .online

    /// A search has been run. Android's `submitted`: before it, the tag chips
    /// and the FM tip are on screen and no empty state is.
    public private(set) var submitted = false

    /// The directory results, thirty at a time. Rebuilt per search, because
    /// a ``Pager``'s path is fixed at construction.
    public private(set) var results: Pager<StationsRow, String>

    /// Up to eight favourites matching what is typed, answered locally and on
    /// screen before the directory request has been sent
    /// (`StationSearch.kt:112`).
    public private(set) var favoriteMatches: [StationsRow] = []

    // MARK: - what is open, and what is in flight

    /// The favourite whose detail is open, by station id.
    public private(set) var selected: Int?

    /// That station's detections and stream-URL editor.
    public private(set) var detail: StationsDetailModel?

    /// The rows with a write in flight, by ``StationsRow/rowKey``.
    ///
    /// Android keeps a `busy` per row (`StationSearch.kt:402`). A set here
    /// rather than one flag, because the writes this screen makes are
    /// per-station and a star on one row must not disable the star on
    /// another.
    public private(set) var busyKeys: Set<String> = []

    public private(set) var toast: String?

    private var debounce: Task<Void, Never>?
    private var eventSink: Int?

    /// **The mechanism for [[read-after-await-wrong-key]], said once.**
    ///
    /// The key that changes under this screen is the house, and a station id
    /// is the *house's* id: two houses can both have a station 4. So every
    /// `Task` this type starts takes this number **before its first await**
    /// and asks ``stillHome(_:)`` after it. A task that no longer holds the
    /// house it was started for plays nothing, says nothing and refreshes
    /// nothing.
    ///
    /// A counter rather than the address, for ``ChatModel``'s reason: leaving
    /// a house and coming back is also a change, and the address cannot tell
    /// the two visits apart.
    private var houseEpoch = 0

    private func stillHome(_ epoch: Int) -> Bool { epoch == houseEpoch }

    public init(
        in container: AppContainer,
        playback: any StationsPlayback,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.container = container
        self.playback = playback
        self.wait = wait
        self.clock = clock

        let client = container.client
        self.favorites = Self.list(
            path: StationsPath.favorites,
            pageSize: Self.favoritesPageSize,
            in: container
        )
        self.recent = ApiRead(in: container, eventTypes: Self.listEvents) {
            try await client.get(StationsPath.recent, as: [StationsRow].self)
        }
        self.badge = ApiRead(in: container, eventTypes: Self.listEvents) {
            try await client.get(StationsPath.badge, as: StationsBadge.self)
        }
        // Nothing has been searched for, so this list asks the server
        // nothing — ``FilesModel``'s "no library chosen yet" pager, for its
        // reason: the screen has one list to draw from and one place to ask
        // about paging, whether or not a search has been run.
        self.results = Self.list(path: nil, pageSize: Self.searchPageSize, in: container)
    }

    /// One of this screen's bare-array lists, paged.
    ///
    /// `path` is the filters with no paging; ``Paging/path(_:_:)`` adds
    /// `limit` and `offset`. A nil path is a list with nothing to ask, which
    /// answers one empty page and is `loaded`.
    ///
    /// **No `eventTypes` on the search results.** `radio.stations.changed`
    /// fires on every play and every star, and re-reading a directory search
    /// on each of those would re-ask radio-browser over the internet for a
    /// list nothing about the house changed. Android does not re-read it
    /// either (its results are a `remember`, not a `rememberApi`). The
    /// favourites list does take the event.
    private static func list(
        path: String?,
        pageSize: Int,
        in container: AppContainer
    ) -> Pager<StationsRow, String> {
        let client = container.client
        guard let path else {
            return Pager(in: container, pageSize: pageSize, keyOf: { $0.rowKey }) { _ in
                Page(items: [], total: nil, serverHasMore: false, nextCursor: nil)
            }
        }
        let events: Set<String> = path == StationsPath.favorites ? Self.listEvents : []
        return Pager(
            in: container,
            eventTypes: events,
            pageSize: pageSize,
            keyOf: { $0.rowKey }
        ) { request in
            let rows = try await client.get(
                Paging.path(path, request),
                as: [StationsRow].self
            )
            return StationsRules.page(rows, limit: request.limit)
        }
    }

    // MARK: - what the screen reads off this model

    public var favoritesTotal: Int? { badge.data?.favorites }

    public var recentRows: [StationsRow] { recent.data ?? [] }

    /// `"23 favorited · 3 recent · tap a station to play it"`.
    ///
    /// Falls back to the loaded count when `/badge` has not answered, as
    /// Android does (`StationsScreen.kt:183`).
    public var subtitle: String {
        StationsStrings.subtitle(
            favorites: favoritesTotal ?? favorites.state.items.count,
            recent: recentRows.count
        )
    }

    /// The submit button's word (`StationSearch.kt:222`).
    public var submitLabel: String {
        StationsStrings.submit(scope, loading: results.state.loading)
    }

    /// The tag chips show before a search has been run, in the online scope
    /// only — radio-browser's tags mean nothing to the FCC import
    /// (`StationSearch.kt:264`).
    public var showsTagChips: Bool {
        scope == .online && !results.state.loading && !submitted
    }

    /// The FM tip, same condition in the other scope (`StationSearch.kt:285`).
    public var showsFMTip: Bool {
        scope == .fm && !results.state.loading && !submitted
    }

    /// The line under `"no stations match"`, or nil when nothing is empty.
    ///
    /// Asked of what was **searched for**, not of what is typed: someone who
    /// has carried on typing after an empty answer is still looking at the
    /// empty answer.
    public var searchEmptyNote: String? {
        let state = results.state
        guard submitted, state.loaded, state.items.isEmpty, state.error == nil, !state.loading else {
            return nil
        }
        return StationsStrings.noMatchNote(scope: askedScope, query: asked, country: country)
    }

    /// The favourites list answered and has none — "no favourites yet", and
    /// never beside a failure or a read in flight, which would be a lie about
    /// the house.
    public var favoritesAreEmpty: Bool {
        let state = favorites.state
        return state.loaded && state.items.isEmpty && state.error == nil && !state.loading
    }

    public func shouldLoadMoreFavorites(lastVisible: Int) -> Bool {
        Self.shouldLoadMore(favorites, lastVisible: lastVisible)
    }

    public func shouldLoadMoreResults(lastVisible: Int) -> Bool {
        Self.shouldLoadMore(results, lastVisible: lastVisible)
    }

    /// The guarded question, never a bare "the last row appeared"
    /// (``Paging/nearEnd(totalItems:lastVisible:threshold:)``).
    private static func shouldLoadMore(
        _ pager: Pager<StationsRow, String>,
        lastVisible: Int
    ) -> Bool {
        guard pager.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: pager.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.listThreshold
        )
    }

    /// Whether this row may be tapped to play, which is the **only** thing
    /// that draws a play affordance.
    ///
    /// Two different noes and both of them silent: no engine in this build
    /// (``StationsPlayback/canPlay``) draws nothing anywhere, and an
    /// unplayable URL draws a disabled glyph with ``whyNotPlayable(_:)``
    /// beside it, which is Android's shape (`StationSearch.kt:538`).
    public var canPlayAnything: Bool { playback.canPlay }

    public func canPlay(_ station: StationsRow) -> Bool {
        playback.canPlay && StationsRules.playable(station)
    }

    /// Why a station cannot be played, for the row that draws a disabled
    /// control — nil when it can be, or when nothing can.
    public func whyNotPlayable(_ station: StationsRow) -> String? {
        guard playback.canPlay, !StationsRules.playable(station) else { return nil }
        return StationsRules.unplayableReason(station)
    }

    /// `"97.5 FM · sampling every 180s"`, and the row's three-state
    /// now-playing line, measured against this model's clock.
    public func metaLine(_ station: StationsRow) -> String {
        StationsStrings.metaLine(station)
    }

    public func nowPlaying(_ station: StationsRow) -> StationsNowPlaying {
        StationsRules.nowPlaying(station)
    }

    public func relative(_ iso: String?) -> String {
        relTime(iso, nowMs: clock())
    }

    public func isBusy(_ station: StationsRow) -> Bool {
        busyKeys.contains(station.rowKey)
    }

    // MARK: - the search surface

    /// Flip the scope. The results, the paging and `submitted` all reset,
    /// because results from the other scope draw in the other row layout
    /// (`StationSearch.kt:148`).
    public func switchScope(_ next: StationsScope) {
        guard next != scope else { return }
        scope = next
        clearResults()
    }

    /// A keystroke. The directory is not touched; the **favourites** are,
    /// after ``instantFavoritesDebounceMs``.
    ///
    /// Android's debounce is the cancellation of a `LaunchedEffect(q)`, which
    /// is why a superseded answer can never land there. Here the same thing
    /// has to be said twice: the previous task is cancelled, and the landing
    /// checks that the text it was started for is still the text in the box.
    /// Cancellation alone is best effort — a request already past its await
    /// still returns a value.
    public func typed(_ text: String) {
        query = text
        debounce?.cancel()
        let settled = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !settled.isEmpty else {
            favoriteMatches = []
            return
        }
        let epoch = houseEpoch
        let client = container.client
        debounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            try? await wait(Self.instantFavoritesDebounceMs)
            guard !Task.isCancelled, let self, self.stillHome(epoch) else { return }
            guard self.query.trimmingCharacters(in: .whitespacesAndNewlines) == settled else { return }
            // Degrades quietly, as Android's `getOrDefault(emptyList())`
            // does: the directory search still works, and a toast about a
            // peek nobody asked for would be noise on every keystroke.
            let found = try? await client.get(
                StationsPath.favoriteMatches(settled),
                as: [StationsRow].self
            )
            guard !Task.isCancelled, self.stillHome(epoch) else { return }
            guard self.query.trimmingCharacters(in: .whitespacesAndNewlines) == settled else { return }
            self.favoriteMatches = found ?? []
        }
    }

    public func typedCountry(_ text: String) {
        // `it.take(2).uppercase()` (`StationSearch.kt:212`). The server reads
        // a two-letter code, so a third character is not a filter it can
        // apply.
        country = String(text.prefix(2)).uppercased()
    }

    /// Run the search. `override` is the tag chip's word, which searches
    /// immediately rather than waiting for the state write to land
    /// (`StationSearch.kt:275`).
    public func submit(override: String? = nil) {
        if let override { query = override }
        let text = query
        guard let path = StationsPath.search(scope: scope, query: text, country: country) else {
            // Online with neither a query nor a country: Android clears the
            // results and un-submits rather than asking radio-browser for
            // everything it has (`StationSearch.kt:126`).
            clearResults()
            return
        }
        asked = text.trimmingCharacters(in: .whitespacesAndNewlines)
        askedScope = scope
        submitted = true
        results.stop()
        results = Self.list(path: path, pageSize: Self.searchPageSize, in: container)
        results.reset()
    }

    /// A tag chip: the word goes in the box and the search runs with it.
    public func tapTag(_ tag: String) {
        submit(override: tag)
    }

    private func clearResults() {
        results.stop()
        results = Self.list(path: nil, pageSize: Self.searchPageSize, in: container)
        results.reset()
        submitted = false
        asked = ""
    }

    // MARK: - the detail

    /// Open or close a favourite's detail — the chevron, which is not the
    /// row: the row plays. Android's two-tap split, kept
    /// (`StationsScreen.kt:478`).
    public func toggleDetail(_ station: StationsRow) {
        if selected == station.id {
            closeDetail()
            return
        }
        selected = station.id
        detail?.stop()
        detail = StationsDetailModel(in: container, stationID: station.id, clock: clock)
        detail?.start()
    }

    public func closeDetail() {
        selected = nil
        detail?.stop()
        detail = nil
    }

    /// The row the open detail is about, taken from the **loaded favourites**
    /// so that a star, an interval save or a push updates what the sheet
    /// shows.
    ///
    /// Nil once the station is gone — which is what a `forget` from inside
    /// the sheet does, and is why the sheet is dismissed rather than left
    /// drawing a row that no longer exists.
    public var selectedStation: StationsRow? {
        guard let selected else { return nil }
        return favorites.state.items.first { $0.id == selected }
    }

    // MARK: - the writes

    /// A tap on a **search row's** star: the state machine of this screen
    /// (`StationSearch.kt:409`, plugin board F-P013).
    ///
    /// A saved row is a PATCH of `favorited`. A directory hit is a POST that
    /// saves and favourites — and **the id that POST answers is written back
    /// onto the row**, or the next tap looks at `id == 0` again, POSTs a
    /// second time and the star never comes off. That is the bug the plugin
    /// board opened as F-P013, and the fix lives here rather than in a row
    /// because the row is in a lazy list and a lazy row never holds a
    /// `Task`.
    public func star(_ row: StationsRow) {
        let key = row.rowKey
        guard !busyKeys.contains(key) else { return }
        busyKeys.insert(key)
        let epoch = houseEpoch
        let client = container.client
        let tap = StationsRules.starTap(rowID: row.id, favorited: row.favorited)
        Task { @MainActor [weak self] in
            defer { self?.busyKeys.remove(key) }
            do throws(DomovoiError) {
                switch tap {
                case .patch(let id, let favorited):
                    _ = try await client.patch(
                        StationsPath.station(id),
                        body: StationsPath.favoritedBody(favorited),
                        as: StationsRow.self
                    )
                    guard let self, self.stillHome(epoch) else { return }
                    self.results.update(key) { loaded in
                        var next = loaded
                        next.favorited = favorited
                        return next
                    }
                    self.toast = favorited
                        ? StationsStrings.favorited(row.name)
                        : StationsStrings.unfavorited(row.name)
                    self.refreshLists()
                    // An FCC FM row has no `stream_url` until a simulcast is
                    // found, so the poller has nothing to hit. Fired as a
                    // SEPARATE request, as Android fires it, so a slow or
                    // failing lookup cannot bounce the favourite itself
                    // (`StationSearch.kt:446`).
                    if favorited, row.source == "fm",
                       (row.streamURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        self.resolveAfterFavoriting(id: id, name: row.name)
                    }
                case .post:
                    let saved = try await client.post(
                        StationsPath.create,
                        body: StationsPath.createBody(row),
                        as: StationsRow.self
                    )
                    guard let self, self.stillHome(epoch) else { return }
                    self.results.update(key) { loaded in
                        var next = loaded
                        next.id = saved.id
                        next.favorited = true
                        return next
                    }
                    self.toast = StationsStrings.favorited(row.name)
                    self.refreshLists()
                }
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.favoriteFailed(trouble.message)
            }
        }
    }

    /// A tap on a **Recent or Favorites row's** star, which is always a PATCH
    /// — those rows are in the table by definition (`StationsScreen.kt:325`).
    public func toggleFavorite(_ station: StationsRow) {
        let key = station.rowKey
        guard !busyKeys.contains(key), station.isSaved else { return }
        busyKeys.insert(key)
        let next = !station.favorited
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            defer { self?.busyKeys.remove(key) }
            do throws(DomovoiError) {
                _ = try await client.patch(
                    StationsPath.station(station.id),
                    body: StationsPath.favoritedBody(next),
                    as: StationsRow.self
                )
                guard let self, self.stillHome(epoch) else { return }
                self.toast = next
                    ? StationsStrings.favorited(station.name)
                    : StationsStrings.unfavorited(station.name)
                // Unfavouriting takes the row out of the favourites list, so
                // that list is re-read rather than patched: the server owns
                // which rows the filter holds, and this phone's guess at it
                // is a row that reappears on the next push.
                self.refreshLists()
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.favoriteFailed(trouble.message)
            }
        }
    }

    /// `PATCH {sample_interval_sec}`, with the server's own bounds checked
    /// first so a typo is a sentence rather than a 422
    /// (`StationsScreen.kt:470`).
    ///
    /// - Returns: whether the draft was accepted, so a field can close itself
    ///   only when something was actually sent.
    @discardableResult
    public func saveInterval(_ station: StationsRow, draft: String) -> Bool {
        switch StationsRules.interval(draft) {
        case .refused(let why):
            toast = why
            return false
        case .ok(let seconds):
            let key = station.rowKey
            guard !busyKeys.contains(key) else { return false }
            busyKeys.insert(key)
            let epoch = houseEpoch
            let client = container.client
            Task { @MainActor [weak self] in
                defer { self?.busyKeys.remove(key) }
                do throws(DomovoiError) {
                    _ = try await client.patch(
                        StationsPath.station(station.id),
                        body: StationsPath.intervalBody(seconds),
                        as: StationsRow.self
                    )
                    guard let self, self.stillHome(epoch) else { return }
                    self.toast = StationsStrings.intervalSaved(station.name, seconds)
                    // Patched in place, not re-read: the one field that
                    // changed is on a row that may be three pages down, and
                    // re-reading to it would be fifteen requests
                    // (``Pager/update(_:_:)``).
                    self.favorites.update(key) { loaded in
                        var next = loaded
                        next.sampleIntervalSec = seconds
                        return next
                    }
                } catch let trouble {
                    guard let self, self.stillHome(epoch) else { return }
                    self.toast = StationsStrings.saveFailed(trouble.message)
                }
            }
            return true
        }
    }

    /// `PATCH {stream_url}`, or an explicit null to clear it
    /// (`StationDetail.kt:224`).
    @discardableResult
    public func saveStreamURL(_ station: StationsRow, draft: String) -> Bool {
        switch StationsRules.streamURL(draft) {
        case .refused(let why):
            toast = why
            return false
        case .ok(let url):
            let key = station.rowKey
            guard !busyKeys.contains(key) else { return false }
            busyKeys.insert(key)
            let epoch = houseEpoch
            let client = container.client
            Task { @MainActor [weak self] in
                defer { self?.busyKeys.remove(key) }
                do throws(DomovoiError) {
                    _ = try await client.patch(
                        StationsPath.station(station.id),
                        body: StationsPath.streamURLBody(url),
                        as: StationsRow.self
                    )
                    guard let self, self.stillHome(epoch) else { return }
                    self.toast = url == nil
                        ? StationsStrings.streamCleared
                        : StationsStrings.streamSaved
                    self.favorites.update(key) { loaded in
                        var next = loaded
                        next.streamURL = url
                        return next
                    }
                } catch let trouble {
                    guard let self, self.stillHome(epoch) else { return }
                    self.toast = StationsStrings.saveFailed(trouble.message)
                }
            }
            return true
        }
    }

    /// `DELETE /stations/{id}` — behind the confirmation, which the view
    /// draws and this does not check: a destructive write with no dialog is a
    /// view bug, and a model that refused without one would make the dialog
    /// impossible to test apart from the delete.
    public func forget(_ station: StationsRow) {
        let key = station.rowKey
        guard !busyKeys.contains(key) else { return }
        busyKeys.insert(key)
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            defer { self?.busyKeys.remove(key) }
            do throws(DomovoiError) {
                try await client.send(method: "DELETE", path: StationsPath.station(station.id))
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.forgot(station.name)
                if self.selected == station.id { self.closeDetail() }
                // Taken off the list before the re-read lands, so the row
                // does not sit there under its own "forgot" toast.
                self.favorites.remove(key)
                self.refreshLists()
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.forgetFailed(trouble.message)
            }
        }
    }

    /// `POST /stations/{id}/resolve-simulcast` from the detail's empty stream
    /// field (`StationDetail.kt:243`).
    ///
    /// Its wording is the detail's, which is not the wording the same request
    /// gets when it is fired behind a star — Android has two sentences for
    /// the two places and so does this.
    public func resolveSimulcast(_ station: StationsRow) {
        let key = station.rowKey
        guard !busyKeys.contains(key) else { return }
        busyKeys.insert(key)
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            defer { self?.busyKeys.remove(key) }
            do throws(DomovoiError) {
                let answer = try await client.post(
                    StationsPath.resolveSimulcast(station.id),
                    as: StationsSimulcast.self
                )
                guard let self, self.stillHome(epoch) else { return }
                if answer.resolved {
                    self.toast = StationsStrings.foundSimulcast(station.callSign ?? station.name)
                    self.refreshLists()
                } else {
                    self.toast = answer.message ?? StationsStrings.noSimulcastFound
                }
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.resolveFailed(trouble.message)
            }
        }
    }

    /// The resolve that follows a star on a blank FM row, in its own request
    /// and with its own three sentences (`StationSearch.kt:446`).
    private func resolveAfterFavoriting(id: Int, name: String) {
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                let answer = try await client.post(
                    StationsPath.resolveSimulcast(id),
                    as: StationsSimulcast.self
                )
                guard let self, self.stillHome(epoch) else { return }
                if answer.resolved {
                    self.toast = StationsStrings.simulcastFound(name)
                } else if let message = answer.message {
                    self.toast = StationsStrings.noSimulcast(message)
                }
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.simulcastFailed(trouble.message)
            }
        }
    }

    /// Play a station on **this phone** — a favourite, a Recent row, or a
    /// directory hit that has never been saved (`StationPlay.kt:51`).
    ///
    /// `POST /play` first, always: the stream proxy resolves a **row id**
    /// rather than a URL, so an unsaved hit has to be persisted before
    /// anything can open it — which the route does with `created_by_play` set
    /// and `favorited` left FALSE. **Playing is not favouriting**; the star is
    /// the only thing that favourites, and the Recent trim reclaims anything
    /// that was only ever played.
    ///
    /// Nothing is sent when this build cannot play, because the POST stamps
    /// the station into Recent: the one visible effect of a silent button
    /// would be a history entry for something nobody heard.
    public func play(_ station: StationsRow) {
        guard playback.canPlay else { return }
        guard StationsRules.playable(station) else {
            toast = StationsRules.unplayableReason(station)
            return
        }
        let epoch = houseEpoch
        let client = container.client
        let playback = self.playback
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                let row = try await client.post(
                    StationsPath.play,
                    body: StationsPath.playBody(station),
                    as: StationsRow.self
                )
                // **The house, taken before the await.** A `/play` answered
                // after the app moved house is a row from the old house, and
                // handing it to the engine would open the NEW house's
                // `/stations/{id}/stream` with the OLD house's id — a
                // different station, or a 404, under the name of the one that
                // was tapped.
                guard let self, self.stillHome(epoch) else { return }
                playback.play(station: row)
                self.toast = StationsStrings.playing(row.name)
                // Ahead of the push, which arrives a tick or two later
                // (`StationPlay.kt:77`'s `onPlayed`).
                self.recent.refresh()
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = StationsStrings.playFailed(trouble.message)
            }
        }
    }

    /// Everything a write can have changed: the favourites, their count and
    /// the Recent strip.
    private func refreshLists() {
        favorites.reload()
        recent.refresh()
        badge.refresh()
    }

    public func setToast(_ said: String?) { toast = said }

    // MARK: - coming and going

    /// The screen appeared: read everything, and join the push registry.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        recent.refresh()
        badge.refresh()
        // `resume` before the test for a reset, because the branch that keeps
        // the rows is the branch that would otherwise come back unkeyed:
        // ``Pager/stop()`` leaves the server registry and only a read
        // rejoins.
        favorites.resume()
        if !favorites.state.loaded { favorites.reset() }
        results.resume()
        detail?.start()
    }

    /// The screen is gone. "A route holds nothing" (`Pagination.swift`).
    public func stop() {
        debounce?.cancel()
        debounce = nil
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        recent.stop()
        badge.stop()
        favorites.stop()
        results.stop()
        detail?.stop()
    }

    /// A push arrived. Each read filters by `type` itself, exactly as the web
    /// hooks do.
    public func received(_ event: StateEvent) {
        recent.received(event)
        badge.received(event)
        favorites.received(event)
        detail?.received(event)
    }

    /// The app moved house. Everything this screen holds is the old house's —
    /// including the station ids, which two houses can both have.
    public func serverChanged() {
        houseEpoch += 1
        debounce?.cancel()
        debounce = nil
        busyKeys = []
        favoriteMatches = []
        query = ""
        asked = ""
        country = "US"
        scope = .online
        askedScope = .online
        closeDetail()
        clearResults()
        recent.serverChanged()
        badge.serverChanged()
        favorites.serverChanged()
    }
}
