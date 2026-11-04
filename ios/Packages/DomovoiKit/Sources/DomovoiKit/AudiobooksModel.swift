import Foundation
import Observation

/// The Audiobooks library: the paged list, the sort, the debounced search,
/// the reindex, and what a tap on `play` does.
///
/// `AudiobooksScreen.kt:112`'s composable, whose state is eight `remember`s
/// and four `LaunchedEffect`s. It is a model here for ``VideosModel``'s
/// reason: when a new list opens at the top, which of two empty messages is
/// shown, whether `play` is drawn at all — these are decisions, and a view
/// cannot be asked whether it got them right.
///
/// ### What is not here, and why there is no control for it
///
/// * **No "add to server"** (`:197`). A document picker, a folder grant and a
///   multipart upload are the PhoneMedia item's. A button that opens nothing
///   is exactly the control the owner's rule forbids.
/// * **No "listening as" chip** (`:205`). Nothing on iOS stores a listener
///   person, so the chip would always have to say `listening as …` — a
///   control stating something the app cannot know. DECISIONS §1.
/// * **No save badge and no "save to phone"** while ``shelf`` is nil, and
///   **no `play`** while ``player`` is nil. Both are seams for branches
///   landing beside this one (`AudiobooksPlayer.swift`), and nil draws
///   nothing rather than a dead control. DECISIONS §5.
/// * **No "play in a room."** Android offers none and the server has no route
///   for it: a cast sends library track ids and a book has no library id
///   (`CastPlan.kt:7-26`); a book in a room is a voice path in the core. The
///   page's subtitle does not promise one. DECISIONS §4.
@MainActor
@Observable
public final class AudiobooksModel {

    /// `AudiobooksScreen.kt:130`'s `eventTypes = setOf("podcasts.changed")`,
    /// which looks like a copy-paste from Podcasts and is **correct**: the
    /// audiobook indexer notifies `podcasts_changed`
    /// (`workers/audiobook_indexer.py:514`) and `realtime.py:157` maps that
    /// channel to the `podcasts` event type. Verified end to end rather than
    /// assumed; there is no `audiobooks.changed`.
    public static let listEvents: Set<String> = ["podcasts.changed"]

    /// How long after the last keystroke the search becomes a request
    /// (`:123-126`).
    public static let searchDebounceMs: Int64 = 250

    // MARK: - what the screen holds

    /// What is typed, which is **not** what was asked for.
    public private(set) var search = ""

    /// The needle the current list was asked with: trimmed and debounced.
    /// Shown in the "no books match" line, so it names where the result
    /// actually came from rather than whatever has been typed since.
    public private(set) var needle = ""

    public private(set) var sort: AudiobooksSort = .title

    public private(set) var list: Pager<AudiobookRow, Int64>

    public private(set) var toast: String?

    /// A reindex is in flight: the header's button says `indexing…`.
    public private(set) var reindexing = false

    /// A new sort or needle has loaded and the view has not been put back at
    /// the top yet. See ``toppedTheList()``.
    public private(set) var wantsTop = false

    /// The resume dialog's question, or nil. Raised by ``start(_:)`` when
    /// ``BookStartPlan/asks`` and answered by ``playPlan(from:)`` or
    /// ``dismissAsk()``.
    public private(set) var ask: (book: AudiobookRow, plan: BookStartPlan)?

    /// The phone's places. Shared with ``AudiobookDetailModel`` so a page and
    /// the library cannot disagree about where a book is.
    public let positions: BookPositionSync

    public let covers: AudiobookCoverStore

    /// Whatever plays audio on this phone, when something does. **Nil draws
    /// no `play` control**, and the chapter rows become plain rows.
    public weak var player: (any AudiobooksPlayer)?

    /// The phone's saved copies, when the shelf exists. **Nil draws no badge
    /// and no save control.**
    public weak var shelf: (any AudiobooksShelf)?

    private let container: AppContainer
    private let wait: @Sendable (Int64) async throws -> Void
    private let serverKey: @Sendable () -> BookServerKey
    private let remote: @Sendable () -> (any BookPositionRemote)?

    /// Whether this phone can talk to the server about places at all — it
    /// has a device id. False makes a book page say so
    /// (``AudiobooksStrings/noDeviceID``) instead of silently losing the
    /// cross-device offer.
    public let knowsThisPhone: Bool

    private var debounce: Task<Void, Never>?

    /// The `sort|needle` the view has already been put at the top for —
    /// `toppedFor` (`:144`). Saved on Android so coming back to the page
    /// keeps the place **in the same list**; the same intent here, held by
    /// the model rather than by a view.
    private var toppedFor: String

    /// Places set while away go up once, when the list has answered
    /// (`:155-162`). A flag and not a `LaunchedEffect`'s key, because the
    /// list answers many times and this is wanted on the first of them.
    private var pushedAll = false

    // MARK: - building one

    /// The model the app uses.
    ///
    /// - Parameters:
    ///   - deviceID: ``ShellModel/deviceID``, which is optional. Nil means no
    ///     server slot and no other places — the local place still works, and
    ///     an empty `device_id` is a *different* device to the server rather
    ///     than an absent one. DECISIONS §3.
    ///   - places: where the phone's places rest. The app's is a file in
    ///     Application Support; a test passes ``BookPositionMemory``.
    ///   - listener: who the server's rows belong to — D11, and
    ///     ``ShellModel/listeners``. A closure, not a value, because Android
    ///     reads `prefs.listenerPersonId.value` at **each** request
    ///     (`ApiPositionRemote.kt:27`): a person picked in Settings has to
    ///     take effect on the next save, not on the next launch. Left out,
    ///     every row is the unidentified listener's, which is what this screen
    ///     did before the picker existed.
    public convenience init(
        in container: AppContainer,
        deviceID: String?,
        listener: @Sendable @escaping () -> String? = { nil },
        places: (any SavedState)? = nil,
        covers: AudiobookCoverStore? = nil,
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        let store = container.store
        let client = container.client
        let key: @Sendable () -> BookServerKey = {
            let url = store.serverURL
            let pin = IdentityGate.pinKey(url).flatMap { store.pinFor($0) }
            return BookServerKey.of(serverURL: url, pin: pin)
        }
        let kept = places ?? BookPositionFile.inApplicationSupport() ?? BookPositionMemory()
        self.init(
            in: container,
            serverKey: key,
            remote: {
                ApiBookPositionRemote(
                    client: client, deviceID: deviceID, server: key(), listener: listener
                )
            },
            knowsThisPhone: !ServerCredentials.isBlank(deviceID ?? ""),
            positions: BookPositionSync(
                store: BookPositionStore(place: kept),
                remote: {
                    ApiBookPositionRemote(
                        client: client, deviceID: deviceID, server: key(), listener: listener
                    )
                },
                clock: clock
            ),
            covers: covers ?? AudiobookCoverStore(in: container),
            wait: wait
        )
    }

    /// The initialiser a test drives: the server key, the remote and the sync
    /// are all given, so a case can switch houses or answer a slot by hand.
    public init(
        in container: AppContainer,
        serverKey: @Sendable @escaping () -> BookServerKey,
        remote: @Sendable @escaping () -> (any BookPositionRemote)?,
        knowsThisPhone: Bool,
        positions: BookPositionSync,
        covers: AudiobookCoverStore,
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.container = container
        self.serverKey = serverKey
        self.remote = remote
        self.knowsThisPhone = knowsThisPhone
        self.positions = positions
        self.covers = covers
        self.wait = wait
        self.toppedFor = "\(AudiobooksSort.title.rawValue)|"
        self.list = Self.pager(sort: .title, needle: "", in: container)
        self.list.onLoaded = { [weak self] in self?.listAnswered() }
    }

    /// One list over a sort and a needle.
    ///
    /// A new pager per list rather than `list.reset()`, as ``VideosModel``
    /// does and for the same reason: the **path** changes and ``Pager``'s is
    /// fixed at construction.
    private static func pager(
        sort: AudiobooksSort,
        needle: String,
        in container: AppContainer
    ) -> Pager<AudiobookRow, Int64> {
        let client = container.client
        let base = AudiobookPath.list(sort: sort, q: needle)
        return Pager(
            in: container,
            eventTypes: Self.listEvents,
            keyOf: { $0.id }
        ) { request in
            try await client.get(Paging.path(base, request), as: Page<AudiobookRow>.self)
        }
    }

    // MARK: - the sort and the search

    /// A keystroke. The request waits ``searchDebounceMs`` for the next one.
    public func typed(_ text: String) {
        search = text
        debounce?.cancel()
        let settled = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard settled != needle else { return }
        debounce = Task { @MainActor [weak self] in
            guard let wait = self?.wait else { return }
            // A wait that threw does **not** apply the needle. Kotlin's
            // `delay` throwing `CancellationException` ends the coroutine
            // before `needle = search.trim()` is reached
            // (`AudiobooksScreen.kt:123-126`), so a swallowed throw here
            // would make a cancelled keystroke the one that searched.
            do {
                try await wait(Self.searchDebounceMs)
            } catch {
                return
            }
            guard !Task.isCancelled, let self, settled != self.needle else { return }
            self.openList(sort: self.sort, needle: settled)
        }
    }

    /// A sort chip. `:215`.
    public func choose(_ next: AudiobooksSort) {
        guard next != sort else { return }
        openList(sort: next, needle: needle)
    }

    private var listKey: String { "\(sort.rawValue)|\(needle)" }

    private func openList(sort: AudiobooksSort, needle: String) {
        self.sort = sort
        self.needle = needle
        ask = nil
        list.stop()
        list = Self.pager(sort: sort, needle: needle, in: container)
        list.onLoaded = { [weak self] in self?.listAnswered() }
        list.reset()
    }

    /// A page landed. Two things happen once each: a new list asks the view
    /// to go to the top, and the places set while away go up.
    private func listAnswered() {
        // A new sort or search is a new list, and it opens at the top. Batch
        // 18 on Android: it opened where the *old* list was scrolled to, the
        // first book out of view, because a lazy grid follows the old first
        // row's key into the new list. Android waits for the reload to finish
        // and then scrolls; the wait here is this callback, which only runs
        // when a page has landed (`:143-150`).
        if toppedFor != listKey {
            toppedFor = listKey
            wantsTop = true
        }
        guard !pushedAll, !list.state.items.isEmpty else { return }
        pushedAll = true
        // `pushAll` only — Android also calls `BookDownloads.kickWaiting`
        // here (`:159`), which resumes the phone's downloads and is the
        // PhoneMedia item's.
        let key = serverKey()
        let sync = positions
        Task { await sync.pushAll(key) }
    }

    /// The view has scrolled to the top. Clears ``wantsTop``.
    public func toppedTheList() {
        wantsTop = false
    }

    // MARK: - what the screen draws

    public var books: [AudiobookRow] { list.state.items }

    /// `"showing 9 of 9 books"`, or nil before the list has answered.
    public var countText: String? {
        guard list.state.loaded else { return nil }
        return Paging.countText(
            shown: list.state.items.count,
            total: list.state.total,
            noun: AudiobooksStrings.booksNoun
        )
    }

    /// Nothing at all, and nothing is wrong. Never while a read is in flight
    /// or has failed — "no audiobooks yet" beside a failed read is a lie
    /// about the house.
    public var isEmpty: Bool {
        list.state.items.isEmpty && list.state.loaded && list.state.error == nil && !list.state.loading
    }

    /// Which of the two empty messages: a search that matched nothing, or a
    /// house with no audiobooks in it. `:233-238`.
    public var emptyNote: (title: String, detail: String)? {
        guard isEmpty else { return nil }
        if !needle.isEmpty {
            return (AudiobooksStrings.noMatch, AudiobooksStrings.searchIs(needle))
        }
        return (AudiobooksStrings.noneYet, AudiobooksStrings.noneYetSub)
    }

    /// Whether the row at `lastVisible` is close enough to the end to ask for
    /// another page — the guarded question, never a bare "the last row
    /// appeared".
    ///
    /// ``Paging/gridThreshold``, not the list one: Android's Audiobooks is a
    /// `LazyVerticalGrid` and keeps the grid's 12 (`:240`), so the two apps
    /// ask for page two at the same row even though this draws one column.
    public func shouldLoadMore(lastVisible: Int) -> Bool {
        guard list.state.canLoadMore else { return false }
        return Paging.nearEnd(
            totalItems: list.state.items.count,
            lastVisible: lastVisible,
            threshold: Paging.gridThreshold
        )
    }

    /// The meta line under a row: `"10:12:00 · 12 chapters · 584 MB"`, with
    /// an em dash for a length the server did not give and the size left out
    /// when ``BookRules/sizeBytes(_:)`` will not guess one (`:312-316`).
    public func meta(_ book: AudiobookRow) -> String {
        var out = fmtDur(book.durationSec)
        if !book.chapters.isEmpty { out += " · " + AudiobooksStrings.chapterCount(book.chapters.count) }
        if let size = BookRules.sizeBytes(book) { out += " · " + fmtBytes(size) }
        return out
    }

    /// The badge for a row, or nil — which is every row while there is no
    /// shelf.
    public func shelfState(_ book: AudiobookRow) -> AudiobooksShelfState? {
        shelf?.state(ofBookID: book.id)
    }

    /// Whether `play` is drawn for this book at all.
    ///
    /// Two reasons it is not: there is nothing to play it with
    /// (``player`` nil), or the book has **no playable file** — a folder book
    /// that names none, whose audio route would answer 400
    /// (`audiobooks.py:325-326`). Either way the row still draws and still
    /// opens; what it does not do is offer a button that cannot work.
    public func canPlay(_ book: AudiobookRow) -> Bool {
        player != nil && !BookRules.parts(book).isEmpty
    }

    // MARK: - the reindex

    /// `POST /api/audiobooks/reindex`, then the toast and a reload. `:167-176`.
    ///
    /// The house is settled before the request and checked after it: a scan
    /// of the old library must not announce itself over the new one's screen
    /// or make it re-read — [[read-after-await-wrong-key]].
    public func reindex() {
        guard !reindexing else { return }
        reindexing = true
        let client = container.client
        let key = serverKey()
        Task { @MainActor [weak self] in
            defer { self?.reindexing = false }
            do {
                let answer = try await client.post(
                    AudiobookPath.reindex,
                    as: AudiobookReindexResult.self
                )
                guard let self, self.serverKey().isSame(key) else { return }
                self.toast = AudiobooksStrings.indexed(answer.scanned)
                self.list.reload()
            } catch let trouble as DomovoiError {
                guard let self, self.serverKey().isSame(key) else { return }
                self.toast = failureText(AudiobooksStrings.reindexVerb, trouble)
            } catch {
                guard let self, self.serverKey().isSame(key) else { return }
                self.toast = failureText(AudiobooksStrings.reindexVerb, .unreachable("\(error)"))
            }
        }
    }

    public func setToast(_ said: String?) {
        toast = said
    }

    // MARK: - starting a book

    /// Start `book` here, asking first when there is a place to resume or
    /// somewhere further on. `startBook` (`:276-288`).
    ///
    /// Does nothing when ``canPlay(_:)`` is false, which is the same fact the
    /// absent button states.
    ///
    /// The plan's two reads are bounded at three seconds
    /// (``BookStartPlanner/othersTimeoutMs``), so there is a real window in
    /// which the app moves house while one is in flight — and
    /// ``serverChanged()`` has already cleared ``ask``. The key captured
    /// before the `await` is checked after it, as
    /// ``AudiobookDetailModel/refreshPlace()`` does, so a plan made for the
    /// old library neither raises its dialog over the new one nor starts
    /// playing a book the connected house has never heard of —
    /// [[read-after-await-wrong-key]].
    public func start(_ book: AudiobookRow) {
        guard canPlay(book) else { return }
        let key = serverKey()
        let sync = positions
        let there = remote()
        let wait = self.wait
        Task { @MainActor [weak self] in
            let plan = await BookStartPlanner.plan(
                book: book,
                server: key,
                sync: sync,
                remote: there,
                sleep: wait
            )
            guard let self, self.serverKey().isSame(key) else { return }
            if plan.asks {
                self.ask = (book: book, plan: plan)
            } else {
                self.player?.play(
                    AudiobookPlayRequest.streaming(book, startSec: 0, speed: plan.speed)
                )
            }
        }
    }

    /// The dialog's answer: play the asked-about book from `startSec`.
    /// `playPlan` (`BookUi.kt:196`).
    public func playPlan(from startSec: Double) {
        guard let asked = ask else { return }
        ask = nil
        player?.play(
            AudiobookPlayRequest.streaming(asked.book, startSec: startSec, speed: asked.plan.speed)
        )
    }

    /// Play `book` from `startSec` with no question — a tap on a chapter row
    /// (`BookPage.kt:221`). The speed is the one the phone's place carries.
    ///
    /// ``BookPositionSync/open(bookID:server:)`` talks to the server's slot,
    /// so the key is checked again on the way back: a chapter tapped in the
    /// old library does not start playing after the app has moved house.
    public func play(_ book: AudiobookRow, from startSec: Double) {
        guard canPlay(book) else { return }
        let key = serverKey()
        let sync = positions
        Task { @MainActor [weak self] in
            let place = await sync.open(bookID: book.id, server: key)
            guard let self, self.serverKey().isSame(key) else { return }
            self.player?.play(
                AudiobookPlayRequest.streaming(book, startSec: startSec, speed: place.speed)
            )
        }
    }

    public func dismissAsk() {
        ask = nil
    }

    // MARK: - coming and going

    /// A detail model for one book, sharing this model's places, covers and
    /// player. The one way a book page is made, so there is no second store
    /// of a place.
    public func detail(bookID: Int64) -> AudiobookDetailModel {
        AudiobookDetailModel(
            bookID: bookID,
            in: container,
            library: self,
            serverKey: serverKey,
            remote: remote
        )
    }

    /// The screen appeared: keyed to the server again, and page one if
    /// nothing has been read. ``Pager/resume()`` and not `reset()`, so coming
    /// back to the tab does not lose the person's place in the list.
    public func start() {
        list.resume()
        if !list.state.loaded { list.reset() }
    }

    /// The app moved house. Everything this screen holds is the old house's.
    public func serverChanged() {
        debounce?.cancel()
        ask = nil
        toast = nil
        pushedAll = false
        covers.serverChanged()
        list.serverChanged()
    }

    public func stop() {
        debounce?.cancel()
        list.stop()
    }
}
