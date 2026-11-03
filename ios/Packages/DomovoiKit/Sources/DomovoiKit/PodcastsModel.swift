import Foundation
import Observation

/// The Podcasts screen: the paged shows grid, one show's episodes, the two
/// searches, the subscribe sheet, the unsubscribe confirm and the manual
/// feed poll.
///
/// `PodcastsScreen.kt:78-206` plus `PodcastsEpisodes.kt:71-213` and
/// `PodcastsSubscribe.kt:54-209`, whose state is sixteen `remember`s and
/// four `LaunchedEffect`s spread across three composables. It is a model here
/// for the reason every one of these has been: whether a search is sent to
/// the server or applied locally, which of four emptinesses is on screen,
/// whether a reply that landed late belongs to the show the sheet is
/// showing, and whether a sheet may close over a write still in flight are
/// all **decisions**, and a view cannot be asked whether it got them right.
///
/// ### What this does not do, and why there is no control for it
///
/// **Playing an episode**, the resume prompt, and the resume position
/// read/write. `wf/ios-n-player` is merged and its engine *can* take a
/// remote-URL episode — but **nothing in the app owns a ``PlayerModel``**:
/// it is on neither ``AppContainer`` nor ``ShellModel``, and putting it there
/// is a change to shared infrastructure this branch must not make. A player
/// this screen built for itself would be destroyed when somebody left
/// Podcasts, so the play control, the resume dialog and the position
/// read/write are left out entirely — not drawn disabled, not stubbed
/// (DECISIONS.md, "Phone playback has an engine and no owner").
/// ``PodcastPath/audio(episodeID:download:)``, ``PodcastPosition``,
/// ``PodcastPath/savePositionBody(deviceID:personID:positionSec:speed:)`` and
/// ``PodcastRules/offersResume(positionSec:)`` are the seam, and
/// **nothing in this model calls them**.
///
/// **Playing to a room.** Neither app can: a room plays library track ids
/// through `POST /api/music/play-tracks` and an episode has none
/// (`CastPlan.kt:10-20, 70-77`), and no server route plays an episode in a
/// room — that exists only through voice, in the core's own MPD path. The
/// header subtitle is reworded accordingly (``PodcastStrings/subtitle``).
///
/// **Saving an episode to the device.** No download manager on iOS, a 64 MiB
/// ceiling on the one save path there is, and **no size on the row** to check
/// it against. ``PodcastStrings/inTheDashboard`` is what the screen
/// says instead.
///
/// **The "listening as *name*" pill.** This app stores no listener person, so
/// `person_id` is simply absent from every position query — and absent is a
/// different key from empty on the server.
///
/// **Pull to refresh does not poll the feeds.** ``reload()`` re-reads the
/// list; ``poll()`` is the only thing that fetches the internet, which is
/// Android's own split and its own reasoning (`PodcastsScreen.kt:104-105`).
@MainActor
@Observable
public final class PodcastsModel {

    /// `podcasts.changed`: a subscribe, an unsubscribe, or the poller writing
    /// episodes — `pg_notify('podcasts_changed')` at `podcasts.py:257, 275`,
    /// mapped to the channel `podcasts` (`realtime.py:157`) and broadcast as
    /// `podcasts.changed` (`:375`). `PodcastsScreen.kt:95`.
    public static let listEvents: Set<String> = ["podcasts.changed"]

    /// The episodes list also re-reads on a position write
    /// (`podcast_positions.changed`, `PodcastsEpisodes.kt:112`): another
    /// device finishing an episode is the one thing that changes under this
    /// sheet while nobody touches it.
    public static let episodeEvents: Set<String> = ["podcasts.changed", "podcast_positions.changed"]

    /// How long after the last keystroke a search becomes a request. Both
    /// fields, both 250 ms (`PodcastsScreen.kt:88`, `PodcastsEpisodes.kt:104`).
    public static let searchDebounceMs: Int64 = 250

    private let container: AppContainer
    private let wait: @Sendable (Int64) async throws -> Void

    // MARK: - the shows grid

    /// What is typed in the shows field, which is **not** what was asked for.
    public private(set) var filter = ""

    /// The needle the grid on screen was asked with: trimmed and debounced.
    /// Named back in "no shows match", so the message is about the result
    /// being looked at rather than whatever has been typed since.
    public private(set) var needle = ""

    public private(set) var shows: Pager<PodcastShow, Int64>

    // MARK: - one show's episodes

    /// The show whose sheet is open, or nil.
    ///
    /// Also the **identity check** for a late unsubscribe: Android closes the
    /// overlay only `if (selected?.id == sub.id)` because the DELETE runs in
    /// the app's scope and can land after a back gesture took the overlay
    /// away (`PodcastsScreen.kt:183-186`).
    public private(set) var selected: PodcastShow?

    /// Built when a show is picked, torn down when the sheet closes. Nil
    /// rather than an empty pager that is always there: a pager keyed to no
    /// show would still be registered for pushes and would still reload on
    /// one.
    public private(set) var episodes: Pager<PodcastEpisode, Int64>?

    public private(set) var episodeFilter = ""
    public private(set) var episodeNeedle = ""

    // MARK: - the writes

    public private(set) var polling = false
    public private(set) var subscribing = false

    /// Whether the subscribe sheet is up.
    ///
    /// The model's and not the view's, for one behaviour: a subscribe runs
    /// past the sheet (Android puts it in `app.scope` precisely so closing
    /// the dialog mid-flight does not cancel the household's write,
    /// `PodcastsSubscribe.kt:90`), and the sheet closes **on success only**.
    /// That is a decision about a write, so it lives where the write does.
    public private(set) var subscribeSheetOpen = false

    /// The show a destructive confirm is up for (`PodcastsEpisodes.kt:194`).
    public private(set) var confirmingUnsubscribe: PodcastShow?

    public private(set) var discoveries: [PodcastDiscovery] = []
    public private(set) var discovering = false

    /// A failed directory search, shown **inside the subscribe sheet**.
    ///
    /// Android toasts it (`PodcastsSubscribe.kt:82`). Here a toast would be
    /// drawn by the shell, behind the sheet, where nobody can read it — so
    /// the sentence goes where the person is looking. Same words
    /// (``failureText(_:_:)`` with ``PodcastStrings/verbDiscovery``), a
    /// different place. DECISIONS.md.
    public private(set) var discoveryError: String?

    /// A failed subscribe, shown inside the sheet for ``discoveryError``'s
    /// reason — and it matters more here, because a failed subscribe is
    /// exactly the case where the sheet stays up.
    public private(set) var subscribeError: String?

    /// The shell's toast. Only ever set for something that lands with no
    /// sheet in front of it.
    public private(set) var toast: String?

    // MARK: - the house, and the key every write holds

    /// **The mechanism for [[read-after-await-wrong-key]].** A counter taken
    /// before a write's first `await` and checked after it.
    ///
    /// The key that changes here is the house. A show id and an episode id
    /// are the *house's* ids, and two houses can both have a subscription 7 —
    /// so `selected?.id == show.id` cannot tell house A's "Unsubscribed" from
    /// house B's, and an unsubscribe for A would remove B's card and close
    /// B's sheet. ``ChatModel`` writes the same mechanism down at length and
    /// this is the same one, for the same reason.
    ///
    /// A counter rather than the address itself, because leaving a house and
    /// coming back is also a change: the first visit's in-flight write is as
    /// wrong on the second visit as it was on the first.
    private var houseEpoch = 0

    private func stillHome(_ epoch: Int) -> Bool { epoch == houseEpoch }

    private var debounce: Task<Void, Never>?
    private var episodeDebounce: Task<Void, Never>?

    /// ``AppContainer/onStateEvent(_:)``'s token, held while the screen is on
    /// screen. A ``Pager`` keys *itself* to the server address but not to the
    /// socket — nothing calls ``Pager/received(_:)`` unless a model does — so
    /// this is the wiring that makes `podcasts.changed` reload the grid, and
    /// ``MusicModel`` holds the same one for the same reason.
    private var eventSink: Int?

    public init(
        in container: AppContainer,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.container = container
        self.wait = wait
        self.shows = Self.showsPager(search: "", in: container)
    }

    // MARK: - the two pagers

    private static func showsPager(
        search: String,
        in container: AppContainer
    ) -> Pager<PodcastShow, Int64> {
        let client = container.client
        let base = PodcastPath.shows(search: search)
        return Pager(in: container, eventTypes: Self.listEvents, keyOf: { $0.id }) { request in
            try await client.get(Paging.path(base, request), as: Page<PodcastShow>.self)
        }
    }

    private static func episodesPager(
        showID: Int64,
        search: String,
        in container: AppContainer
    ) -> Pager<PodcastEpisode, Int64> {
        let client = container.client
        let base = PodcastPath.episodes(showID: showID, search: search)
        return Pager(in: container, eventTypes: Self.episodeEvents, keyOf: { $0.id }) { request in
            try await client.get(Paging.path(base, request), as: Page<PodcastEpisode>.self)
        }
    }

    // MARK: - the shows search

    /// A keystroke in the shows field. The request waits
    /// ``searchDebounceMs`` for the next one.
    public func typed(_ text: String) {
        filter = text
        debounce?.cancel()
        let settled = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard settled != needle else { return }
        debounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            try? await wait(Self.searchDebounceMs)
            guard !Task.isCancelled, let self, settled != self.needle else { return }
            self.applyFilter(settled)
        }
    }

    /// A new needle: a different list from page one, with a new pager.
    ///
    /// A new pager rather than `shows.reset()`, because the **path** changes
    /// and ``Pager``'s is fixed at construction. The old one is stopped so it
    /// leaves the server-key registry; a pager that could only join is a leak
    /// with a callback. ``VideosModel`` does exactly this, and for the same
    /// reason the search is the server's: `?q=` covers the title, the
    /// **author** and the feed URL (`podcasts.py:171-174`), none of which a
    /// local filter over the loaded page could do.
    private func applyFilter(_ settled: String) {
        needle = settled
        shows.stop()
        shows = Self.showsPager(search: settled, in: container)
        shows.reset()
    }

    // MARK: - opening a show

    /// A card was tapped: the sheet opens and the episodes list reads page
    /// one.
    ///
    /// Re-tapping the show already open does nothing, rather than throwing
    /// away a loaded back catalogue and re-reading it.
    public func select(_ show: PodcastShow) {
        guard selected?.id != show.id else { return }
        selected = show
        episodeFilter = ""
        episodeNeedle = ""
        episodeDebounce?.cancel()
        episodes?.stop()
        let pager = Self.episodesPager(showID: show.id, search: "", in: container)
        episodes = pager
        pager.reset()
    }

    /// The sheet closed. The pager is stopped as well as dropped, so it
    /// leaves the server-key and push registries rather than reloading a list
    /// nobody is looking at.
    public func closeEpisodes() {
        episodeDebounce?.cancel()
        episodes?.stop()
        episodes = nil
        selected = nil
        episodeFilter = ""
        episodeNeedle = ""
        confirmingUnsubscribe = nil
    }

    /// A keystroke in the episodes field.
    public func typedEpisode(_ text: String) {
        episodeFilter = text
        episodeDebounce?.cancel()
        let settled = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard settled != episodeNeedle else { return }
        episodeDebounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            try? await wait(Self.searchDebounceMs)
            guard !Task.isCancelled, let self, settled != self.episodeNeedle else { return }
            self.applyEpisodeFilter(settled)
        }
    }

    private func applyEpisodeFilter(_ settled: String) {
        guard let show = selected else { return }
        episodeNeedle = settled
        episodes?.stop()
        let pager = Self.episodesPager(showID: show.id, search: settled, in: container)
        episodes = pager
        pager.reset()
    }

    // MARK: - poll now

    /// Fetch every subscribed feed now, and say what it found.
    ///
    /// Guarded against a second tap while one is in flight, as Android's
    /// `if (polling) return` is (`PodcastsScreen.kt:112`): a poll makes the
    /// server fetch every feed, and two at once is two passes over the
    /// internet for one person's impatience.
    ///
    /// The list is reloaded on success because a poll writes episodes, which
    /// changes every card's `downloaded_count`.
    public func poll() {
        guard !polling else { return }
        polling = true
        let client = container.client
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var landed: PodcastPoll?
            var said: String?
            do {
                landed = try await client.post(PodcastPath.poll, as: PodcastPoll.self)
            } catch let trouble as DomovoiError {
                said = failureText(PodcastStrings.verbPoll, trouble)
            } catch {
                said = failureText(PodcastStrings.verbPoll, .unreachable("\(error)"))
            }
            // ``houseEpoch``: a "Polled — 3 downloaded" for a house the person
            // has left names episodes that are not on screen, and the new
            // house's list is its own start()'s business.
            guard let self, self.stillHome(epoch) else { return }
            self.polling = false
            if let landed {
                self.toast = PodcastStrings.polled(downloaded: landed.downloaded, fresh: landed.fresh)
                self.shows.reload()
            } else {
                self.toast = said
            }
        }
    }

    // MARK: - subscribing

    public func openSubscribe() {
        subscribeSheetOpen = true
        discoveryError = nil
        subscribeError = nil
    }

    /// The person closed the sheet. A subscribe already in flight is **not**
    /// cancelled — it is the household's write, not this sheet's
    /// (`PodcastsSubscribe.kt:90`) — so `subscribing` is left as it is and
    /// the task below finishes on its own.
    public func closeSubscribe() {
        subscribeSheetOpen = false
        discoveries = []
        discoveryError = nil
        subscribeError = nil
    }

    /// Search the directory for `query`.
    ///
    /// Blank, or a search already running, does nothing — Android's
    /// `if (q.isBlank() || busy) return` (`PodcastsSubscribe.kt:72`). The
    /// server types `q` with `min_length=1` (`podcasts.py:390`), so a blank
    /// would be a 422 for a field the person can see is empty.
    public func discover(_ query: String) {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty, !discovering else { return }
        discovering = true
        discoveryError = nil
        let client = container.client
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var found: [PodcastDiscovery]?
            var said: String?
            do {
                found = try await client.get(
                    PodcastPath.discover(query: needle),
                    as: [PodcastDiscovery].self
                )
            } catch let trouble as DomovoiError {
                // The server's own words, which here can be the 409
                // internet-off refusal (`podcasts.py:397`) or "discovery
                // unavailable (offline?)" (`:408`). Both are worth showing as
                // they come: they say which of the two happened.
                said = failureText(PodcastStrings.verbDiscovery, trouble)
            } catch {
                said = failureText(PodcastStrings.verbDiscovery, .unreachable("\(error)"))
            }
            // ``houseEpoch``: house A's directory hits are subscribed into
            // whichever house is current, so landing them under B would offer
            // an `add` that writes somewhere the person did not search from.
            guard let self, self.stillHome(epoch) else { return }
            self.discovering = false
            if let found {
                self.discoveries = found
            } else {
                self.discoveryError = said
            }
        }
    }

    /// Subscribe to `feedURL` — typed into the url tab, or a directory hit's
    /// own feed.
    ///
    /// On success: the toast, the list reloaded, the sheet closed. The sheet
    /// is closed **last and only here**, which is Android's `if (open())
    /// onClose()` (`PodcastsSubscribe.kt:101`) in the only form it needs on
    /// iOS — setting an already-false flag false is a no-op, so a sheet the
    /// person dismissed mid-flight is not reopened and not re-closed.
    public func subscribe(feedURL: String) {
        let feed = feedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !feed.isEmpty, !subscribing else { return }
        subscribing = true
        subscribeError = nil
        let client = container.client
        let body = PodcastPath.subscribeBody(feedURL: feed)
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var said: String?
            do {
                try await client.send(method: "POST", path: PodcastPath.subscriptions, body: body)
            } catch let trouble as DomovoiError {
                // A feed the server will not fetch is a **400** with its own
                // reason (`_checked_feed_url`, `podcasts.py:64-75`), and an
                // unpaired phone is the device refusal. Both read better than
                // anything this app could say instead.
                said = failureText(PodcastStrings.verbSubscribe, trouble)
            } catch {
                said = failureText(PodcastStrings.verbSubscribe, .unreachable("\(error)"))
            }
            guard let self, self.stillHome(epoch) else { return }
            self.subscribing = false
            guard said == nil else {
                self.subscribeError = said
                return
            }
            self.toast = PodcastStrings.subscribed
            self.shows.reload()
            self.subscribeSheetOpen = false
            self.discoveries = []
        }
    }

    // MARK: - unsubscribing

    public func confirmUnsubscribe(_ show: PodcastShow) {
        confirmingUnsubscribe = show
    }

    public func cancelUnsubscribe() {
        confirmingUnsubscribe = nil
    }

    /// Unsubscribe from `show`, which removes the house's downloaded episodes
    /// with it — hence the destructive confirm this is called from.
    ///
    /// The card is removed with ``Pager/remove(_:)`` rather than by a reload,
    /// which also brings the total down so the count line does not claim a
    /// show that is gone (`PodcastsScreen.kt:186`). The sheet closes **only
    /// if this same show is still the one open**, because the reply can land
    /// after a back gesture took the sheet away or after another show was
    /// opened (`:183-185`).
    public func unsubscribe(_ show: PodcastShow) {
        confirmingUnsubscribe = nil
        let client = container.client
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var said: String?
            do {
                try await client.send(method: "DELETE", path: PodcastPath.subscription(show.id))
            } catch let trouble as DomovoiError {
                said = PodcastStrings.unsubscribeFailed(trouble.message)
            } catch {
                said = PodcastStrings.unsubscribeFailed("\(error)")
            }
            // ``houseEpoch``: subscription ids are per house, so without this
            // an unsubscribe for A would remove the card with the same id in
            // B and close B's sheet.
            guard let self, self.stillHome(epoch) else { return }
            guard said == nil else {
                self.toast = said
                return
            }
            self.toast = PodcastStrings.unsubscribed
            self.shows.remove(show.id)
            if self.selected?.id == show.id { self.closeEpisodes() }
        }
    }

    // MARK: - what the screen draws

    /// `"showing 50 of 62 shows"`, or nil before the grid has answered —
    /// "0 shows" and "not asked yet" are different facts.
    public var countText: String? {
        guard shows.state.loaded else { return nil }
        return Paging.countText(
            shown: shows.state.items.count,
            total: shows.state.total,
            noun: PodcastStrings.showsNoun
        )
    }

    /// The same for the open show's episodes.
    public var episodeCountText: String? {
        guard let state = episodes?.state, state.loaded else { return nil }
        return Paging.countText(
            shown: state.items.count,
            total: state.total,
            noun: PodcastStrings.episodesNoun
        )
    }

    /// Nothing at all, and nothing is wrong: the grid answered and it is
    /// empty. Never while a read is in flight or has failed — "no
    /// subscriptions yet" beside a failed read is a lie about the house.
    public var isEmpty: Bool {
        Self.settledEmpty(shows.state)
    }

    /// Which of the two empty messages: a search that matched nothing, or a
    /// house with no subscriptions (`PodcastsScreen.kt:152-156`).
    public var emptyNote: (title: String, detail: String)? {
        guard isEmpty else { return nil }
        if !needle.isEmpty {
            return (PodcastStrings.noShowsMatch, PodcastStrings.searchIs(needle))
        }
        return (PodcastStrings.noSubscriptions, PodcastStrings.noSubscriptionsDetail)
    }

    /// The episode sheet's own two empties (`PodcastsEpisodes.kt:179-182`).
    /// `no episodes yet` points at `poll now`, which is the thing that would
    /// fill it.
    public var episodesEmptyNote: (title: String, detail: String)? {
        guard let state = episodes?.state, Self.settledEmpty(state) else { return nil }
        if !episodeNeedle.isEmpty {
            return (PodcastStrings.noEpisodesMatch, PodcastStrings.searchIs(episodeNeedle))
        }
        return (PodcastStrings.noEpisodes, PodcastStrings.noEpisodesDetail)
    }

    /// Said once for both lists: answered, empty, not loading, not failed.
    private static func settledEmpty<Row: PagedRow>(_ state: PagedState<Row>) -> Bool {
        state.items.isEmpty && state.loaded && state.error == nil && !state.loading
    }

    /// Whether the tile at `lastVisible` is close enough to the end to ask
    /// for another page. The guarded question, never a bare "the last tile
    /// appeared" — ``Paging/nearEnd(totalItems:lastVisible:threshold:)`` says
    /// why at length.
    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard shows.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: shows.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.gridThreshold
        )
    }

    /// The same for the episodes list, which is a list and so takes the
    /// list's threshold (`Paged.kt:89`).
    public func shouldLoadMoreEpisodes(lastVisible: Int) -> Bool {
        guard let state = episodes?.state, state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.listThreshold
        )
    }

    /// A pull on the grid: re-read what is loaded, **without** fetching any
    /// feed. The split Android is explicit about (`PodcastsScreen.kt:104-105`)
    /// — a pull must not go to the internet behind the person's back.
    public func reload() {
        shows.reload()
    }

    public func setToast(_ said: String?) { toast = said }

    // MARK: - coming and going

    /// The screen appeared: be keyed to the server again, and read page one
    /// if nothing has been read.
    ///
    /// ``Pager/resume()`` rather than `reset()` for an already-loaded grid:
    /// the point of keeping the cards is not re-reading them, and a grid that
    /// jumped to the top on every return to the route would lose the person's
    /// place.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        shows.resume()
        if !shows.state.loaded { shows.reset() }
        if let episodes {
            episodes.resume()
            if !episodes.state.loaded { episodes.reset() }
        }
    }

    /// A push arrived. Each list filters by type itself, exactly as the web
    /// hooks do; nothing ever sends a narrower subscription — the iOS socket
    /// subscribes to everything (`StateSocket.swift:87`).
    ///
    /// The episodes list takes `podcast_positions.changed` as well, which the
    /// grid does not: another device finishing an episode changes a row in
    /// the open sheet and nothing on the grid.
    public func received(_ event: StateEvent) {
        shows.received(event)
        episodes?.received(event)
    }

    /// The app moved house. Everything this screen holds is the old house's —
    /// including the show the sheet is open on and the directory hits in the
    /// subscribe sheet, both of which are keyed to ids and feeds the new
    /// house knows nothing about.
    ///
    /// **The shows search is deliberately kept.** `filter` and `needle` are
    /// not cleared, as ``VideosModel/serverChanged()`` does not clear its
    /// own: the pager's path is fixed at construction, so clearing the field
    /// without rebuilding the pager would show an empty search box over a
    /// grid still filtered by the old needle — and rebuilding it
    /// unconditionally would throw the search away on a sink that fired
    /// without the address actually changing (``Pager/serverChanged()``
    /// guards on the *value*, which is the whole point of that guard). The
    /// episode needles **are** cleared, because the sheet they belong to is
    /// closing with them.
    public func serverChanged() {
        houseEpoch += 1
        debounce?.cancel()
        episodeDebounce?.cancel()
        episodes?.stop()
        episodes = nil
        selected = nil
        episodeFilter = ""
        episodeNeedle = ""
        polling = false
        subscribing = false
        discovering = false
        discoveries = []
        discoveryError = nil
        subscribeError = nil
        subscribeSheetOpen = false
        confirmingUnsubscribe = nil
        toast = nil
        shows.serverChanged()
    }

    public func stop() {
        debounce?.cancel()
        episodeDebounce?.cancel()
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        shows.stop()
        episodes?.stop()
    }
}
