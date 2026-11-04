import Foundation
import Observation

/// A row of a paged list, which has to say **what the server calls the array
/// it comes in**.
///
/// Android passes that key as an argument at every call site
/// (`net/Paging.kt`'s `toPage(itemsKey:)` — `"items"` for Music, `"videos"`
/// for the video feed, `"entries"` for Files). Swift's `Decodable` cannot
/// take an argument, and threading one through would mean hand-writing a
/// decoder per screen. A static on the row type is the same fact, said once
/// where the row is declared, and the compiler then cannot let a screen
/// forget it.
public protocol PagedRow: Decodable, Sendable {
    /// The key the server's object answer puts the rows under. `"items"`
    /// unless the endpoint says otherwise.
    static var itemsKey: String { get }
}

public extension PagedRow {
    static var itemsKey: String { "items" }
}

/// One page of a list, decoded from whatever shape the server answered in.
///
/// `net/Paging.kt`'s `Page`, and it **tolerates every older server** for the
/// same reason: a bare array, or an object with no paging fields, is one
/// complete page. The server's contract is `web/backend/api/paging.py` —
/// `limit` plus the opaque keyset `cursor` from the previous answer's
/// `next_cursor`, answered with `total`, `has_more` and `next_cursor` — and
/// Music still answers the older offset shape, with a `total` and no
/// `has_more`.
///
/// ### `hasMore` is a question, not a field
///
/// This is the one place the port deliberately differs from the Kotlin.
/// There, `Paging.decode` takes `loadedBefore` and bakes the inference into
/// the decoded value, so **every call site has to remember to pass
/// `loadedBefore = req.offset`** — `MusicScreen.kt:122` does, and a call site
/// that forgot would get `hasMore == false` on an offset-paged list and stop
/// after page one, silently. Here the raw answer is kept and the question is
/// asked by the only thing that knows how many rows are already loaded: the
/// pager. A screen cannot get it wrong because a screen is never asked.
///
/// ### Deliberately no `extra`
///
/// Android's `Page.extra` carries the rest of the answer — a folder's
/// breadcrumb, a library's per-source counts — as a `JsonObject` it can hand
/// around untyped. Nothing in this batch reads one, and a `[String: Any]` to
/// hold it would be the only untyped hole in this package. The screen that
/// needs it is Files (I3), and what it should add is a second generic
/// parameter for a decoded `Extra`, not a dictionary.
public struct Page<Row: PagedRow>: Sendable {

    public let items: [Row]

    /// How many rows the whole list has, when the server said.
    public let total: Int?

    /// The server's own `has_more`, or nil when it did not say one. Read
    /// through ``hasMore(loadedBefore:)``, never directly.
    public let serverHasMore: Bool?

    /// The opaque cursor for the next page, when the server gave one.
    public let nextCursor: String?

    public init(items: [Row], total: Int?, serverHasMore: Bool?, nextCursor: String?) {
        self.items = items
        self.total = total
        self.serverHasMore = serverHasMore
        self.nextCursor = nextCursor
    }

    /// Whether there is another page, given how many rows were loaded before
    /// this one arrived.
    ///
    /// The server's word when it gave one; otherwise an answer with a `total`
    /// has more when the rows so far fall short of it, and an answer with
    /// neither has not got any more.
    public func hasMore(loadedBefore: Int) -> Bool {
        if let serverHasMore { return serverHasMore }
        guard let total else { return false }
        return loadedBefore + items.count < total
    }
}

extension Page: Decodable {

    private struct Key: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }
        init(_ name: String) { stringValue = name }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { return nil }
    }

    public init(from decoder: any Decoder) throws {
        // A bare array is one complete page: every row there is, and nothing
        // more to ask for. `total` is the count rather than nil, so a caller
        // showing "N of M" says the same number twice instead of dropping to
        // its unknown-total wording.
        if var array = try? decoder.unkeyedContainer() {
            var rows: [Row] = []
            while !array.isAtEnd { rows.append(try array.decode(Row.self)) }
            self.init(items: rows, total: rows.count, serverHasMore: false, nextCursor: nil)
            return
        }
        let object = try decoder.container(keyedBy: Key.self)
        let rows = try object.decodeIfPresent([Row].self, forKey: Key(Row.itemsKey)) ?? []
        self.init(
            items: rows,
            total: try object.decodeIfPresent(Int.self, forKey: Key("total")),
            serverHasMore: try object.decodeIfPresent(Bool.self, forKey: Key("has_more")),
            nextCursor: try object.decodeIfPresent(String.self, forKey: Key("next_cursor"))
        )
    }
}

/// What a fetch is asked for.
///
/// `offset` is the rows already loaded: a server from before cursors (Music's
/// offset paging) reads it; a newer one follows `cursor` and ignores it.
/// `fresh` asks a filesystem list to re-read disk rather than answer from its
/// index.
public struct PageRequest: Equatable, Sendable {
    public let cursor: String?
    public let offset: Int
    public let limit: Int
    public let fresh: Bool

    public init(cursor: String?, offset: Int, limit: Int, fresh: Bool = false) {
        self.cursor = cursor
        self.offset = offset
        self.limit = limit
        self.fresh = fresh
    }
}

/// Everything a paged list shows: the rows, what is known about the whole
/// list, and whether the last attempt is in flight or failed.
public struct PagedState<Row: PagedRow>: Sendable {
    public var items: [Row] = []
    public var total: Int?
    public var hasMore = false
    public var nextCursor: String?
    public var loading = false

    /// The last failure's message, cleared by the next success.
    public var error: String?

    /// At least one answer has arrived for this list — so "nothing here" can
    /// be told apart from "nothing yet", which is the difference between an
    /// empty-state and a spinner.
    public var loaded = false

    public init() {}

    /// Whether the scroll trigger may ask for the next page: one at a time,
    /// and **never over a failure**. A list that kept asking after a failure
    /// would retry on every frame the last row stayed visible.
    public var canLoadMore: Bool { hasMore && !loading && error == nil }
}

public extension Paging {

    // MARK: - the pure transitions
    //
    // Apart from the pager for the reason `net/Paging.kt`'s `object Paging`
    // gives: so they can be driven directly by a test, with no task, no
    // clock and no server.

    /// `page` appended to `items`, skipping any row whose key is already
    /// there.
    ///
    /// A list reloaded while the server is inserting rows answers the same
    /// row twice — the same offset means something different once a row is
    /// added above it — and a duplicate key in a SwiftUI `ForEach` is a
    /// runtime complaint and a view that stops updating.
    static func merge<Row, Key: Hashable>(
        _ items: [Row],
        _ page: [Row],
        keyOf: (Row) -> Key
    ) -> [Row] {
        guard !page.isEmpty else { return items }
        var seen = Set(items.map(keyOf))
        var out = items
        out.reserveCapacity(items.count + page.count)
        for row in page where seen.insert(keyOf(row)).inserted { out.append(row) }
        return out
    }

    /// `page` added to `state`: the rows merged, the totals taken, the
    /// failure cleared.
    ///
    /// `hasMore` is asked of the page with the row count **from before the
    /// merge**, which is what `loadedBefore` means (see ``Page``).
    static func appended<Row: PagedRow, Key: Hashable>(
        _ state: PagedState<Row>,
        _ page: Page<Row>,
        keyOf: (Row) -> Key
    ) -> PagedState<Row> {
        var next = PagedState<Row>()
        next.items = merge(state.items, page.items, keyOf: keyOf)
        next.total = page.total ?? state.total
        next.hasMore = page.hasMore(loadedBefore: state.items.count)
        next.nextCursor = page.nextCursor
        next.loading = false
        next.error = nil
        next.loaded = true
        return next
    }

    /// A failure, with whatever was loaded kept: `net/Paging.kt`'s `failed`.
    /// The rows already on screen do not vanish because the next page could
    /// not be read.
    static func failed<Row: PagedRow>(
        _ state: PagedState<Row>,
        _ message: String
    ) -> PagedState<Row> {
        var next = state
        next.loading = false
        next.error = message
        next.loaded = true
        return next
    }

    /// `"showing 100 of 5,232 tracks"`, or `"100 tracks"` when the total is
    /// unknown.
    ///
    /// Grouped in `en_US` rather than the phone's locale, because Android
    /// pins `Locale.US` (`net/Paging.kt`'s `countText`) and the two apps
    /// should put the same string under the same list. The app has no
    /// localisation yet; when it gets one this is one of the lines that has
    /// to change on both sides at once.
    static func countText(shown: Int, total: Int?, noun: String) -> String {
        let style = IntegerFormatStyle<Int>().locale(Locale(identifier: "en_US"))
        guard let total else { return "\(shown.formatted(style)) \(noun)" }
        return "showing \(shown.formatted(style)) of \(total.formatted(style)) \(noun)"
    }

    /// `base` (the filters and the sort, with no paging) plus this request's
    /// paging parameters.
    ///
    /// The cursor is percent-encoded, where Android's `URLEncoder` sends a
    /// space as `+`. A cursor is opaque and the server's own encoder produces
    /// one, so neither spelling has ever had a space in it — but `+` means a
    /// space to a form-encoded query parser and `%20` means it everywhere, so
    /// the difference is noted rather than matched.
    static func path(_ base: String, _ request: PageRequest) -> String {
        var out = base
        out += base.contains("?") ? "&" : "?"
        out += "limit=\(request.limit)"
        if request.offset > 0 { out += "&offset=\(request.offset)" }
        if let cursor = request.cursor {
            let allowed = CharacterSet(charactersIn: "-._~").union(.alphanumerics)
            let safe = cursor.addingPercentEncoding(withAllowedCharacters: allowed) ?? cursor
            out += "&cursor=\(safe)"
        }
        if request.fresh { out += "&refresh=true" }
        return out
    }

    /// A page cut from a list already in memory — the phone's own lists,
    /// which have no server to ask.
    static func slice<Row: PagedRow>(_ all: [Row], _ request: PageRequest) -> Page<Row> {
        let from = min(max(0, request.offset), all.count)
        let to = min(from + max(0, request.limit), all.count)
        return Page(
            items: Array(all[from..<to]),
            total: all.count,
            serverHasMore: to < all.count,
            nextCursor: nil
        )
    }
}

/// One list, loaded a page at a time.
///
/// `net/Paging.kt`'s `Pager`, which `Pagination.swift` said belonged "with
/// the first paged screen" — this is that screen's batch, and the three facts
/// that file states about **navigation** are still its own.
///
/// ### What it is keyed to, and why that is not a line in a view
///
/// Android resets a pager from the screen:
/// `LaunchedEffect(serverUrl, *keys) { pager.reset() }` (`Paged.kt:60`). A
/// pager built with ``init(in:eventTypes:pageSize:keyOf:fetch:)`` joins
/// ``AppContainer/onServerChange(_:)`` itself, for the reason ``ApiRead``
/// gives at length: a key that is a property of having been built cannot be
/// forgotten by the next screen, and this project has already shipped one
/// read whose re-key was a line somebody left out.
///
/// ### A reset while a page is in flight drops that page
///
/// The generation counter, and it is not belt-and-braces: `reset()` is what a
/// new search term does, and a slow page one for `"bea"` answering after
/// page one for `"beatles"` would otherwise append the wrong rows to the
/// right list. Cancellation alone does not cover it — a `fetch` that ignores
/// cancellation still returns a value.
@MainActor
@Observable
public final class Pager<Row: PagedRow, Key: Hashable & Sendable> {

    public private(set) var state = PagedState<Row>()

    public let pageSize: Int

    /// Run when a page lands, **in the pager's own success path**.
    ///
    /// ``ApiRead/onValue``'s twin, and it exists for the same reason: the one
    /// consumer is Music's source filter, which accumulates the slugs seen
    /// across pages, and a view remembering to call it after every
    /// `loadMore`, `reload` and `reset` is how that came to have no caller at
    /// all on the read side. Deliberately not `public`: a wiring point for a
    /// model in this module, not something a screen reaches for.
    var onLoaded: (@MainActor () -> Void)?

    private let keyOf: @Sendable (Row) -> Key
    private let fetch: @Sendable (PageRequest) async throws -> Page<Row>
    private let base: @Sendable () -> String
    private let eventTypes: Set<String>
    private let keyedTo: AppContainer?

    private var generation = 0
    private var job: Task<Void, Never>?
    private var serverSink: Int?
    private var lastBase: String?

    /// A list of `container`'s server, **keyed to its address**. The
    /// initialiser to use.
    ///
    /// - Parameters:
    ///   - eventTypes: the pushes that mean "re-read this list" — a reload
    ///     that keeps the list where it is, not a jump to the top.
    ///   - keyOf: the row's stable identity, which is what de-duplicates a
    ///     merge and what ``update(_:_:)`` and ``remove(_:)`` address.
    ///   - fetch: one page. It must not swallow its own errors.
    public convenience init(
        in container: AppContainer,
        eventTypes: Set<String> = [],
        pageSize: Int = Paging.defaultPageSize,
        keyOf: @Sendable @escaping (Row) -> Key,
        fetch: @Sendable @escaping (PageRequest) async throws -> Page<Row>
    ) {
        let store = container.store
        self.init(
            base: { store.serverURL },
            eventTypes: eventTypes,
            keyedTo: container,
            pageSize: pageSize,
            keyOf: keyOf,
            fetch: fetch
        )
    }

    /// A list over a `base` of the caller's own, for a test that drives the
    /// address by hand. **Not keyed to anything** unless `keyedTo` is given.
    public init(
        base: @Sendable @escaping () -> String = { "" },
        eventTypes: Set<String> = [],
        keyedTo: AppContainer? = nil,
        pageSize: Int = Paging.defaultPageSize,
        keyOf: @Sendable @escaping (Row) -> Key,
        fetch: @Sendable @escaping (PageRequest) async throws -> Page<Row>
    ) {
        self.base = base
        self.eventTypes = eventTypes
        self.keyedTo = keyedTo
        self.pageSize = max(1, pageSize)
        self.keyOf = keyOf
        self.fetch = fetch
        joinTheServerKey()
    }

    /// Join ``AppContainer/onServerChange(_:)``, once, if there is a
    /// container and this list is not on it already.
    ///
    /// Called from `init`, from ``resume()``, and from both reads — the same
    /// re-join ``ApiRead/refresh()`` makes, and for the reason stated there:
    /// ``stop()`` takes the list off the registry, and SwiftUI does not
    /// promise that `@State` dies on every disappearance, so a screen that
    /// comes back may be holding a pager that has already left.
    ///
    /// **Retracted, 2026-10-31:** this used to be called from `init` alone,
    /// and the batch that wrote it said nothing about coming back. A list
    /// that had stopped and been shown again was still read from, still
    /// reloaded by pushes — and no longer keyed to the address, so a switch
    /// of houses left the old house's rows on screen under the new one's
    /// name. Found by `MusicModelTests` driving stop-then-start, which is
    /// what a tab switch is.
    private func joinTheServerKey() {
        guard serverSink == nil, let keyedTo else { return }
        serverSink = keyedTo.onServerChange { [weak self] in
            Task { @MainActor in self?.serverChanged() }
        }
    }

    // MARK: - what a list is asked to do

    /// The screen is here again: be keyed to the server once more, without
    /// re-reading anything.
    ///
    /// What it is for: a screen whose pager is **already loaded** does not
    /// reset on reappearing — that is the point of keeping the rows — so
    /// without this the one path back from ``stop()`` would skip the re-join
    /// entirely.
    public func resume() {
        joinTheServerKey()
    }

    /// A new list: forget what was loaded and read page one.
    public func reset() {
        readFromTop(want: 0, fresh: false)
    }

    /// The next page, when there is one and nothing is in flight.
    public func loadMore() {
        joinTheServerKey()
        let current = state
        guard current.canLoadMore else { return }
        let mine = generation
        state.loading = true
        state.error = nil
        job = Task { @MainActor [weak self] in
            guard let self else { return }
            let request = PageRequest(
                cursor: current.nextCursor,
                offset: current.items.count,
                limit: self.pageSize
            )
            do {
                let page = try await self.fetch(request)
                guard !Task.isCancelled, mine == self.generation else { return }
                self.state = Paging.appended(self.state, page, keyOf: self.keyOf)
                self.onLoaded?()
            } catch is CancellationError {
                return
            } catch let trouble {
                guard !Task.isCancelled, mine == self.generation else { return }
                self.state = Paging.failed(self.state, Self.message(of: trouble))
            }
        }
    }

    /// After a failure: page one again when nothing loaded, else the page
    /// that failed.
    public func retry() {
        guard !state.items.isEmpty else {
            reset()
            return
        }
        state.error = nil
        loadMore()
    }

    /// Re-read what is loaded — a push, or after an edit — **keeping the list
    /// where it is**. Past ``Paging/reloadCap`` rows it starts over at page
    /// one instead, because re-reading twenty pages to keep a scroll position
    /// is twenty requests.
    public func reload(fresh: Bool = false) {
        let loaded = state.items.count
        readFromTop(want: loaded > Paging.reloadCap ? 0 : loaded, fresh: fresh)
    }

    /// One loaded row changed in place, with no re-read of every page
    /// scrolled through. A favourite toggled on row 300 does not re-ask for
    /// rows 1 to 300.
    public func update(_ key: Key, _ change: (Row) -> Row) {
        state.items = state.items.map { keyOf($0) == key ? change($0) : $0 }
    }

    /// One loaded row gone — it left the filter the list is showing. The
    /// total comes down with it, so the count text does not claim a row that
    /// is not there.
    public func remove(_ key: Key) {
        let before = state.items.count
        state.items = state.items.filter { keyOf($0) != key }
        let gone = before - state.items.count
        if let total = state.total { state.total = max(0, total - gone) }
    }

    /// Re-read if the saved server is not the one the last read was for —
    /// ``ApiRead/serverChanged()``'s rule, and for its reason: the Kotlin's
    /// key is the *value*, not the event.
    public func serverChanged() {
        guard lastBase != base() else { return }
        reset()
    }

    /// A push arrived. Reloads when its type is one of `eventTypes`.
    public func received(_ event: StateEvent) {
        guard eventTypes.contains(event.type) else { return }
        reload()
    }

    /// Stop: the screen is going away.
    public func stop() {
        generation += 1
        job?.cancel()
        job = nil
        if let serverSink {
            keyedTo?.removeServerChangeSink(serverSink)
            self.serverSink = nil
        }
    }

    /// For a test: wait for whatever is in flight.
    public func idle() async {
        await job?.value
    }

    // MARK: - the read

    /// Read from the top, filling up to `want` rows (or one page, whichever
    /// is more) in as many requests as it takes.
    ///
    /// The loop is what keeps a reload's scroll position: a list showing 300
    /// rows re-reads 300, not 50. It asks for as much as the server will give
    /// in one go, capped at ``Paging/serverMaxPage``.
    private func readFromTop(want: Int, fresh: Bool) {
        joinTheServerKey()
        generation += 1
        let mine = generation
        job?.cancel()
        lastBase = base()

        let before = state
        if want == 0 {
            var blank = PagedState<Row>()
            blank.loading = true
            state = blank
        } else {
            state.loading = true
            state.error = nil
        }

        let target = max(pageSize, min(want, Paging.reloadCap))
        job = Task { @MainActor [weak self] in
            guard let self else { return }
            var items: [Row] = []
            var cursor: String?
            var last: Page<Row>?
            var more = true
            var landedHasMore = false
            while more, items.count < target {
                let limit = min(max(target - items.count, self.pageSize), Paging.serverMaxPage)
                let request = PageRequest(
                    cursor: cursor,
                    offset: items.count,
                    limit: limit,
                    // Only the first request of a reload asks the server to
                    // re-walk the disk; the pages after it read what that
                    // walk produced.
                    fresh: fresh && cursor == nil
                )
                let page: Page<Row>
                do {
                    page = try await self.fetch(request)
                } catch is CancellationError {
                    return
                } catch let trouble {
                    guard !Task.isCancelled, mine == self.generation else { return }
                    self.state = Paging.failed(
                        want == 0 ? PagedState<Row>() : before,
                        Self.message(of: trouble)
                    )
                    return
                }
                guard !Task.isCancelled, mine == self.generation else { return }
                let loadedBefore = items.count
                items = Paging.merge(items, page.items, keyOf: self.keyOf)
                last = page
                cursor = page.nextCursor
                // A page that answered nothing and gave no cursor ends the
                // loop whatever `has_more` said: without this an older server
                // that answers an empty page with `has_more` true spins.
                landedHasMore = page.hasMore(loadedBefore: loadedBefore)
                more = landedHasMore && (cursor != nil || !page.items.isEmpty)
            }
            guard let landed = last, mine == self.generation else { return }
            var next = PagedState<Row>()
            next.items = items
            next.total = landed.total
            // The last page's own answer, asked while the count it needed was
            // still in hand. Recomputing it from `items.count` here would be
            // wrong whenever the merge dropped a duplicate row.
            next.hasMore = landedHasMore
            next.nextCursor = landed.nextCursor
            next.loaded = true
            self.state = next
            self.onLoaded?()
        }
    }

    /// Kotlin's `e.message ?: "request failed"`.
    private static func message(of error: any Error) -> String {
        if let domovoi = error as? DomovoiError { return domovoi.message }
        let text = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        return text.isEmpty ? "request failed" : text
    }
}
