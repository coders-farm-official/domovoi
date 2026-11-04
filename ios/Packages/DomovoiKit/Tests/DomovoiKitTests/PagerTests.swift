import XCTest
@testable import DomovoiKit

/// The paging infrastructure every list screen is about to be built on: what
/// a page is when the server answers in one of its three shapes, the pure
/// transitions, and the pager's own behaviour against a feed that is not a
/// server.
///
/// Android's `net/Paging.kt` has **no** test — the JVM suite is unit-only and
/// `Paged.kt` is composables — so `PaginationTests` (the thresholds) plus
/// this file are the only specification either app has of the contract.
/// Divergences from the Kotlin are therefore not things to check against a
/// test over there; they are things this file has to state. There are two,
/// both in ``Page``'s doc: `hasMore` is asked rather than baked in, and there
/// is no `extra`.
@MainActor
final class PagerTests: XCTestCase {

    // MARK: - the rig

    /// A row with a key. `name` is optional so that a server which stops
    /// sending it does not turn a page into a decode failure — the rule
    /// `MusicModels.swift` states, kept in the test rig too, so that a shape
    /// used here is a shape the app would accept.
    private struct Row: Equatable, Sendable, PagedRow {
        let id: Int
        let name: String?

        init(id: Int, name: String? = nil) {
            self.id = id
            self.name = name
        }
    }

    /// A row whose array the server calls something else. Its only job is to
    /// prove ``PagedRow/itemsKey`` is *read* rather than merely declared:
    /// Android passes the key as an argument at every call site, and a site
    /// that passed the wrong one would decode an empty page, silently.
    private struct Clip: Equatable, Sendable, PagedRow {
        static var itemsKey: String { "videos" }
        let id: Int
    }

    private typealias Answer = @Sendable (PageRequest) throws -> Page<Row>

    /// The feed a pager reads from: what it was asked, what it answers, and a
    /// park so a test can make something happen **while a page is in
    /// flight**.
    ///
    /// Everything is a ``Shared`` rather than a stored property because the
    /// fetch closure is `@Sendable`; that is the reason `Shared` exists at
    /// all, stated in its own doc.
    private final class Feed: Sendable {
        let asked = Shared<[PageRequest]>([])
        let answer: Shared<Answer>
        let park = Shared<Bool>(false)
        let release = Shared<Bool>(false)

        /// Set when a park hit its bound instead of being released — the same
        /// guard ``ParkedRequest/ranOut`` is. **Assert it is false**: a park
        /// that ran out means whatever followed was asserted over a page that
        /// arrived for a different reason than the test thinks.
        let ranOut = Shared<Bool>(false)

        /// How many fetches have **returned a value**, as opposed to been
        /// asked. A test about a page that lands too late has to wait for the
        /// late one to come back, or it asserts before the thing it is about
        /// has happened at all.
        let answered = Shared<Int>(0)

        init(_ first: @escaping Answer) {
            answer = Shared(first)
        }

        var fetch: @Sendable (PageRequest) async throws -> Page<Row> {
            { [asked, answer, park, release, ranOut, answered] request in
                asked.mutate { $0.append(request) }
                // **What this request will answer is decided now, not when
                // the park lets go.** A real server's answer is determined by
                // the request; reading `answer.current` after the park made
                // the late page carry whatever the *new* list wanted, so a
                // test about the late page landing could not fail — measured
                // 2026-10-31, when it passed with both of ``Pager``'s
                // generation checks taken out.
                let mine = answer.current
                if park.current {
                    // Deliberately **does not check `Task.isCancelled`**: a
                    // fetch that ignores cancellation and still returns a
                    // value is exactly the case the generation counter is
                    // there for (``Pager``'s doc), and a park that bailed out
                    // on cancellation could not produce it.
                    var letGo = false
                    for _ in 0..<settleYieldLimit {
                        if release.current {
                            letGo = true
                            break
                        }
                        await Task.yield()
                    }
                    if !letGo { ranOut.set(true) }
                }
                let page = try mine(request)
                answered.mutate { $0 += 1 }
                return page
            }
        }

        var limits: [Int] { asked.current.map(\.limit) }
        var offsets: [Int] { asked.current.map(\.offset) }
        var count: Int { asked.current.count }
    }

    private func rows(_ ids: Range<Int>) -> [Row] {
        ids.map { Row(id: $0) }
    }

    /// A server that pages by offset and answers a `total` with no
    /// `has_more` — Music's shape, and the one whose inference the port moved
    /// out of the decoder.
    private func offsetFeed(total: Int) -> Feed {
        Feed { request in
            let from = min(request.offset, total)
            let to = min(from + request.limit, total)
            return Page(
                items: from < to ? (from..<to).map { Row(id: $0) } : [],
                total: total,
                serverHasMore: nil,
                nextCursor: nil
            )
        }
    }

    private func pager(
        _ feed: Feed,
        pageSize: Int = Paging.defaultPageSize,
        base: Shared<String> = Shared(""),
        eventTypes: Set<String> = []
    ) -> Pager<Row, Int> {
        Pager(
            base: { base.current },
            eventTypes: eventTypes,
            keyedTo: nil,
            pageSize: pageSize,
            keyOf: { $0.id },
            fetch: feed.fetch
        )
    }

    private func decode<R: PagedRow>(_ text: String, as _: R.Type = R.self) throws -> Page<R> {
        try JSONDecoder().decode(Page<R>.self, from: Data(text.utf8))
    }

    // MARK: - what a page is, in each shape the server answers in

    /// A bare array is one complete page.
    ///
    /// The older endpoints answer this, and `net/Paging.kt` tolerates it for
    /// that reason. `total` is the row count rather than nil so a count line
    /// says "showing 2 of 2" instead of dropping to its unknown-total
    /// wording, which would read as though more were coming.
    func testABareArrayIsOneCompletePage() throws {
        let page: Page<Row> = try decode(#"[{"id":1},{"id":2}]"#)

        XCTAssertEqual([1, 2], page.items.map(\.id))
        XCTAssertEqual(2, page.total)
        XCTAssertFalse(page.hasMore(loadedBefore: 0))
        XCTAssertNil(page.nextCursor)
    }

    /// A `total` and no `has_more`: whether there is more is a question about
    /// how many rows are already loaded, and the answer changes with it.
    ///
    /// This is the divergence from the Kotlin, where `loadedBefore` is an
    /// argument to the decoder and so a call site can forget it. Here the one
    /// thing that knows the count is the only thing that asks.
    func testATotalWithNoHasMoreIsAnsweredAgainstWhatIsLoaded() throws {
        let page: Page<Row> = try decode(#"{"items":[{"id":1},{"id":2}],"total":120}"#)

        XCTAssertNil(page.serverHasMore)
        XCTAssertTrue(page.hasMore(loadedBefore: 0), "2 of 120 loaded")
        XCTAssertTrue(page.hasMore(loadedBefore: 117), "119 of 120 loaded")
        XCTAssertFalse(page.hasMore(loadedBefore: 118), "all 120 loaded")
        XCTAssertFalse(page.hasMore(loadedBefore: 500), "more than the server claims")
    }

    /// The server's own word wins over the arithmetic, in both directions.
    ///
    /// A `total` is a snapshot of a list that is being written to — the
    /// indexer adds rows while the person scrolls — so a server that says
    /// `has_more` is the only party that knows.
    func testTheServersHasMoreBeatsTheTotalBothWays() throws {
        let stops: Page<Row> = try decode(#"{"items":[{"id":1}],"total":500,"has_more":false}"#)
        XCTAssertFalse(stops.hasMore(loadedBefore: 0), "1 of 500, and the server says that is all")

        let goes: Page<Row> = try decode(#"{"items":[{"id":1}],"total":1,"has_more":true}"#)
        XCTAssertTrue(goes.hasMore(loadedBefore: 0), "the total is met, and the server says more")
    }

    /// An object with no paging fields at all is also one complete page —
    /// and its total is **unknown**, not zero.
    func testAnAnswerWithNeitherFieldHasNoMoreAndNoTotal() throws {
        let page: Page<Row> = try decode(#"{"items":[{"id":1},{"id":2}]}"#)

        XCTAssertEqual(2, page.items.count)
        XCTAssertNil(page.total)
        XCTAssertFalse(page.hasMore(loadedBefore: 0))
    }

    /// A missing or null `items` is an empty page, not a throw: the whole
    /// point of this decoder.
    func testAnAnswerWithNoRowsAtAllDecodes() throws {
        let empty: Page<Row> = try decode(#"{"total":0}"#)
        XCTAssertTrue(empty.items.isEmpty)
        XCTAssertEqual(0, empty.total)

        let nulled: Page<Row> = try decode(#"{"items":null,"total":0}"#)
        XCTAssertTrue(nulled.items.isEmpty)
    }

    /// The array's key is the row type's, and a page that looked under the
    /// wrong one comes back empty.
    ///
    /// Both halves matter: the first says the static is read, the second says
    /// what the failure would look like if a row type declared the wrong key
    /// — rows missing beside a total that claims them, which is what a
    /// forgotten `itemsKey:` argument gave Android.
    func testTheArraysKeyIsTheRowTypesOwn() throws {
        let right: Page<Clip> = try decode(#"{"videos":[{"id":1},{"id":2}],"total":2}"#)
        XCTAssertEqual([1, 2], right.items.map(\.id))

        let wrong: Page<Clip> = try decode(#"{"items":[{"id":1},{"id":2}],"total":2}"#)
        XCTAssertTrue(wrong.items.isEmpty, "a clip's rows are not under `items`")
        XCTAssertEqual(2, wrong.total, "and the total still claims two")
    }

    /// A row the page cannot decode is still a throw. The leniency is about
    /// *missing* fields, not about rows of the wrong type, and a page of
    /// nonsense must not read as an empty list — which is a quiet "no tracks
    /// here" over a working library.
    func testARowOfTheWrongShapeIsStillAFailure() {
        XCTAssertThrowsError(try decode(#"{"items":[{"id":"not a number"}]}"#, as: Row.self))
    }

    // MARK: - the pure transitions

    /// A row already loaded is not appended twice.
    ///
    /// Not tidiness: the same offset means something different once the
    /// indexer inserts a row above it, so a reload mid-insert answers a row
    /// the list already has, and a duplicate id in a SwiftUI `ForEach` is a
    /// runtime complaint and a view that stops updating.
    func testMergeSkipsARowThatIsAlreadyThere() {
        let merged = Paging.merge(rows(0..<3), [Row(id: 2), Row(id: 3)], keyOf: \.id)

        XCTAssertEqual([0, 1, 2, 3], merged.map(\.id))
    }

    /// `appended` asks the page's `hasMore` with the count from **before**
    /// the merge.
    ///
    /// The numbers are chosen so the two readings disagree: 50 loaded, a page
    /// of 50 of which one is a duplicate, and a total of 101. Asked with the
    /// count before the merge (50) there is one row still to come; asked with
    /// the count after it (99) the arithmetic says 149 rows for a list of
    /// 101, and the list would stop one row short of itself.
    func testAppendedAsksHasMoreWithTheCountFromBeforeTheMerge() {
        var state = PagedState<Row>()
        state.items = rows(0..<50)
        let page = Page(items: rows(49..<99), total: 101, serverHasMore: nil, nextCursor: "c1")

        let next = Paging.appended(state, page, keyOf: \.id)

        XCTAssertEqual(99, next.items.count, "one row of the page was already there")
        XCTAssertTrue(next.hasMore, "50 + 50 < 101, which is the question that was asked")
        XCTAssertEqual(101, next.total)
        XCTAssertEqual("c1", next.nextCursor)
        XCTAssertTrue(next.loaded)
        XCTAssertNil(next.error)
    }

    /// A page with no total of its own keeps the one the list already had,
    /// rather than dropping the count line to its unknown wording mid-scroll.
    func testAppendedKeepsTheTotalWhenThePageDidNotSayOne() {
        var state = PagedState<Row>()
        state.items = rows(0..<2)
        state.total = 7

        let next = Paging.appended(
            state,
            Page(items: rows(2..<4), total: nil, serverHasMore: true, nextCursor: nil),
            keyOf: \.id
        )

        XCTAssertEqual(7, next.total)
    }

    /// A failure keeps the rows and stops the asking.
    ///
    /// Both halves are the point: the rows on screen do not vanish because
    /// page nine could not be read, and ``PagedState/canLoadMore`` goes false
    /// so a list whose last row stays visible does not retry on every frame.
    func testAFailureKeepsTheRowsAndStopsTheAsking() {
        var state = PagedState<Row>()
        state.items = rows(0..<50)
        state.hasMore = true
        state.loading = true

        let next = Paging.failed(state, "the network went away")

        XCTAssertEqual(50, next.items.count)
        XCTAssertEqual("the network went away", next.error)
        XCTAssertFalse(next.loading)
        XCTAssertTrue(next.hasMore, "there is still more; it just cannot be asked for now")
        XCTAssertFalse(next.canLoadMore)
    }

    /// Nothing loaded yet is not the same as nothing there: the empty state
    /// and the spinner are different screens.
    func testLoadedIsWhatTellsNothingHereFromNothingYet() {
        let fresh = PagedState<Row>()
        XCTAssertFalse(fresh.loaded)

        let answered = Paging.appended(
            fresh,
            Page(items: [], total: 0, serverHasMore: false, nextCursor: nil),
            keyOf: \.id
        )
        XCTAssertTrue(answered.loaded)
        XCTAssertTrue(answered.items.isEmpty)
    }

    /// The count line, grouped in `en_US` the way Android pins `Locale.US`,
    /// so the two apps put the same string under the same list.
    func testCountTextSaysTheSameAsAndroids() {
        XCTAssertEqual(
            "showing 100 of 5,232 tracks",
            Paging.countText(shown: 100, total: 5232, noun: "tracks")
        )
        XCTAssertEqual("100 tracks", Paging.countText(shown: 100, total: nil, noun: "tracks"))
        XCTAssertEqual("1 track", Paging.countText(shown: 1, total: nil, noun: "track"))
    }

    /// The paging parameters go on after the filters, a zero offset is left
    /// off, and the cursor is percent-encoded.
    ///
    /// `?` versus `&` is decided by the base, because the base is a query of
    /// the screen's own (Music's sort and filters) and a second `?` is not a
    /// URL.
    func testThePathAddsPagingToWhateverTheScreenAsked() {
        XCTAssertEqual(
            "/api/music/library?sort=title&limit=50",
            Paging.path("/api/music/library?sort=title", PageRequest(cursor: nil, offset: 0, limit: 50))
        )
        XCTAssertEqual(
            "/api/files?limit=50&offset=100",
            Paging.path("/api/files", PageRequest(cursor: nil, offset: 100, limit: 50))
        )
        XCTAssertEqual(
            "/api/files?limit=50&cursor=a%2Fb%20c&refresh=true",
            Paging.path("/api/files", PageRequest(cursor: "a/b c", offset: 0, limit: 50, fresh: true))
        )
    }

    /// A page cut from a list already in memory, including past the end.
    func testSliceCutsAPageFromMemoryAndStopsAtTheEnd() {
        let all = rows(0..<10)

        let first = Paging.slice(all, PageRequest(cursor: nil, offset: 0, limit: 4))
        XCTAssertEqual([0, 1, 2, 3], first.items.map(\.id))
        XCTAssertEqual(10, first.total)
        XCTAssertTrue(first.hasMore(loadedBefore: 0))

        let last = Paging.slice(all, PageRequest(cursor: nil, offset: 8, limit: 4))
        XCTAssertEqual([8, 9], last.items.map(\.id))
        XCTAssertFalse(last.hasMore(loadedBefore: 8))

        let past = Paging.slice(all, PageRequest(cursor: nil, offset: 50, limit: 4))
        XCTAssertTrue(past.items.isEmpty, "off the end is empty, not a crash")
    }

    // MARK: - the pager

    /// Page one, then the page after it, asked for with the right offset.
    func testResetReadsPageOneAndLoadMoreAsksForTheNext() async {
        let feed = offsetFeed(total: 120)
        let list = pager(feed)

        list.reset()
        await list.idle()

        XCTAssertEqual(50, list.state.items.count)
        XCTAssertEqual(120, list.state.total)
        XCTAssertTrue(list.state.canLoadMore)
        XCTAssertEqual([0], feed.offsets)

        list.loadMore()
        await list.idle()
        XCTAssertEqual(100, list.state.items.count)
        XCTAssertEqual([0, 50], feed.offsets)

        list.loadMore()
        await list.idle()
        XCTAssertEqual(120, list.state.items.count)
        XCTAssertFalse(list.state.hasMore, "that is the whole list")
        XCTAssertFalse(list.state.canLoadMore)

        list.loadMore()
        await list.idle()
        XCTAssertEqual(3, feed.count, "nothing is asked for once there is no more")
    }

    /// `loadMore` while a page is in flight asks for nothing: one at a time.
    func testLoadMoreWhileAPageIsInFlightAsksForNothing() async {
        let feed = offsetFeed(total: 500)
        let list = pager(feed)
        list.reset()
        await list.idle()

        feed.park.set(true)
        list.loadMore()
        XCTAssertTrue(list.state.loading)
        XCTAssertFalse(list.state.canLoadMore)
        list.loadMore()
        list.loadMore()
        feed.release.set(true)
        await list.idle()

        XCTAssertEqual(2, feed.count, "page one, and one page two")
        XCTAssertFalse(feed.ranOut.current)
    }

    /// **A page in flight for the old list does not land in the new one.**
    ///
    /// The `"bea"` / `"beatles"` property from ``Pager``'s own doc: a search
    /// term changes, the slow page one for the old term answers afterwards,
    /// and its rows must not appear under the new term. Driven with a fetch
    /// that ignores cancellation, because a fetch that honoured it could not
    /// produce the case.
    ///
    /// **Which guard carries it, measured rather than argued.** `readFromTop`
    /// cancels the job *and* bumps the generation, and it would be easy to
    /// write that the cancellation is the real guard and the counter is belt
    /// and braces. It is the other way round: with both generation checks
    /// removed and the cancellation untouched, the old term's rows land in
    /// the new list (run 2026-10-31, this test the only failure). Cancelling
    /// a task that is inside a fetch which ignores cancellation stops
    /// nothing — and an `URLSession` request already in flight is exactly
    /// that fetch.
    ///
    /// **It waits for the old fetch to come back.** Written without that, it
    /// passed with *both* generation checks removed — measured, 2026-10-31 —
    /// because the old task was still suspended when the assertions ran, so
    /// the write it was about had not been attempted yet. A test that cannot
    /// fail is the thing this suite exists to avoid, so the wait is for
    /// `answered == 2`: both pages produced, the late one included.
    func testAPageInFlightForTheOldListDoesNotLandInTheNewOne() async {
        let feed = Feed { _ in
            Page(items: [Row(id: 900, name: "old")], total: 1, serverHasMore: false, nextCursor: nil)
        }
        let list = pager(feed)

        feed.park.set(true)
        list.reset()
        // The park is reached on the main actor; let it get there.
        while feed.count == 0 { await Task.yield() }

        // The term changes while page one is in flight. A new pager is what
        // Music actually builds for a new query; a reset is the narrower case
        // and the one this guard is for.
        feed.answer.set { _ in
            Page(items: [Row(id: 1, name: "new")], total: 1, serverHasMore: false, nextCursor: nil)
        }
        feed.park.set(false)
        list.reset()

        // The new list lands first, **while the old page is still parked**.
        // Releasing it before this would not make it late at all: the old
        // task is ahead of the new one in the actor's queue, so it would
        // write first and the new list would simply overwrite it — which is
        // how an earlier version of this test passed with both of ``Pager``'s
        // generation checks removed.
        await list.idle()
        XCTAssertEqual([1], list.state.items.map(\.id))
        XCTAssertEqual(1, feed.answered.current, "the old page has not answered yet")

        // Now the old term's page comes back, into a list that has moved on.
        feed.release.set(true)
        for _ in 0..<settleYieldLimit where feed.answered.current < 2 { await Task.yield() }
        for _ in 0..<64 { await Task.yield() }

        XCTAssertEqual(2, feed.answered.current, "the late page really did come back")
        XCTAssertEqual([1], list.state.items.map(\.id), "the old term's rows are not here")
        XCTAssertEqual(["new"], list.state.items.map { $0.name ?? "" })
        XCTAssertEqual(2, feed.count, "and the old page did answer — it was dropped, not lost")
        XCTAssertFalse(feed.ranOut.current)
    }

    /// A reload re-reads **what is loaded**, in as few requests as the server
    /// will allow, and keeps the list where it is.
    ///
    /// The person is 150 rows down a list; a push arrives; they stay 150 rows
    /// down. One request of 150, not three of 50.
    func testAReloadRereadsWhatIsLoadedInOneRequest() async {
        let feed = offsetFeed(total: 1200)
        let list = pager(feed)
        list.reset()
        await list.idle()
        list.loadMore()
        await list.idle()
        list.loadMore()
        await list.idle()
        XCTAssertEqual(150, list.state.items.count)

        feed.asked.set([])
        list.reload()
        await list.idle()

        XCTAssertEqual([150], feed.limits, "one request for everything that was loaded")
        XCTAssertEqual([0], feed.offsets)
        XCTAssertEqual(150, list.state.items.count)
        XCTAssertTrue(list.state.hasMore)
    }

    /// One request never asks for more than the server's maximum page, so a
    /// read of a long list is several requests rather than one the server
    /// refuses.
    ///
    /// **And a page size over that maximum overshoots**: the loop asks for at
    /// least a whole page every time round, so a pager configured with 300
    /// against a server that gives 200 lands 400 rows in two requests rather
    /// than 300 in two. Measured here rather than described, because it is
    /// the kind of arithmetic a later change would silently alter. No screen
    /// is configured that way — the default is 50 and the only caller that
    /// sets it is a grid — so it is written down as what happens, not relied
    /// upon as a promise.
    func testAReadNeverAsksForMoreThanTheServerWillGive() async {
        let feed = offsetFeed(total: 1200)
        let list = pager(feed, pageSize: 300)

        list.reset()
        await list.idle()

        XCTAssertEqual([Paging.serverMaxPage, Paging.serverMaxPage], feed.limits)
        XCTAssertEqual(400, list.state.items.count)
    }

    /// Past ``Paging/reloadCap`` a reload starts over at page one instead,
    /// because re-reading twenty pages to keep a scroll position is twenty
    /// requests.
    func testPastTheCapAReloadStartsOverAtPageOne() async {
        let feed = offsetFeed(total: 2000)
        let list = pager(feed, pageSize: 200)
        list.reset()
        await list.idle()
        while list.state.items.count <= Paging.reloadCap, list.state.canLoadMore {
            list.loadMore()
            await list.idle()
        }
        XCTAssertGreaterThan(list.state.items.count, Paging.reloadCap)

        feed.asked.set([])
        list.reload()
        await list.idle()

        XCTAssertEqual([0], feed.offsets)
        XCTAssertEqual([200], feed.limits)
        XCTAssertEqual(200, list.state.items.count, "the list is back at the top")
    }

    /// Only the first request of a reload asks the server to re-walk the
    /// disk; the pages after it read what that walk produced.
    func testOnlyTheFirstRequestOfAReloadAsksForAFreshWalk() async {
        let feed = Feed { request in
            Page(
                items: (request.offset..<(request.offset + request.limit)).map { Row(id: $0) },
                total: 10_000,
                serverHasMore: nil,
                nextCursor: "c\(request.offset)"
            )
        }
        let list = pager(feed, pageSize: 200)
        list.reset()
        await list.idle()
        list.loadMore()
        await list.idle()
        XCTAssertEqual(400, list.state.items.count)

        feed.asked.set([])
        list.reload(fresh: true)
        await list.idle()

        XCTAssertEqual([true, false], feed.asked.current.map(\.fresh), "the walk is asked for once")
    }

    /// An empty page that still claims `has_more` ends the read.
    ///
    /// Without the guard an older server answering `{"items": [], "total":
    /// 500, "has_more": true}` spins the loop forever — on the main actor,
    /// which is a wedged app rather than a slow one.
    func testAnEmptyPageThatClaimsMoreDoesNotSpin() async {
        let feed = Feed { _ in
            Page(items: [], total: 500, serverHasMore: true, nextCursor: nil)
        }
        let list = pager(feed)

        list.reset()
        await list.idle()

        XCTAssertEqual(1, feed.count, "asked once, and believed the empty answer")
        XCTAssertTrue(list.state.items.isEmpty)
        XCTAssertTrue(list.state.loaded)
    }

    /// A row changed in place, with no re-read of the pages scrolled through.
    func testUpdateChangesOneLoadedRowAndAsksForNothing() async {
        let feed = offsetFeed(total: 120)
        let list = pager(feed)
        list.reset()
        await list.idle()
        feed.asked.set([])

        list.update(7) { Row(id: $0.id, name: "favourited") }

        XCTAssertEqual("favourited", list.state.items.first { $0.id == 7 }?.name)
        XCTAssertNil(list.state.items.first { $0.id == 8 }?.name)
        XCTAssertEqual(0, feed.count)
    }

    /// A row gone takes the total down with it, so the count line does not
    /// claim a row that is not there.
    func testRemoveBringsTheTotalDownWithTheRow() async {
        let feed = offsetFeed(total: 120)
        let list = pager(feed)
        list.reset()
        await list.idle()

        list.remove(7)

        XCTAssertEqual(49, list.state.items.count)
        XCTAssertEqual(119, list.state.total)

        list.remove(10_000)
        XCTAssertEqual(119, list.state.total, "a row that was not loaded takes nothing")
    }

    /// Page one failing leaves an empty list with a message, and `retry`
    /// starts over.
    func testAFailedFirstPageSaysSoAndRetryStartsOver() async {
        let feed = Feed { _ in throw DomovoiError.unreachable("no route to host") }
        let list = pager(feed)

        list.reset()
        await list.idle()

        XCTAssertTrue(list.state.items.isEmpty)
        XCTAssertEqual("no route to host", list.state.error)
        XCTAssertTrue(list.state.loaded, "it answered — with a failure")
        XCTAssertFalse(list.state.canLoadMore)

        feed.answer.set { _ in
            Page(items: [Row(id: 1)], total: 1, serverHasMore: false, nextCursor: nil)
        }
        list.retry()
        await list.idle()

        XCTAssertEqual([1], list.state.items.map(\.id))
        XCTAssertNil(list.state.error)
    }

    /// A later page failing keeps the rows, and `retry` asks for the page
    /// that failed rather than starting over.
    func testAFailedLaterPageKeepsTheRowsAndRetryAsksForItAgain() async {
        let feed = offsetFeed(total: 120)
        let list = pager(feed)
        list.reset()
        await list.idle()

        feed.answer.set { _ in throw DomovoiError.unreachable("the network went away") }
        list.loadMore()
        await list.idle()

        XCTAssertEqual(50, list.state.items.count, "the rows on screen stay")
        XCTAssertEqual("the network went away", list.state.error)
        XCTAssertFalse(list.state.canLoadMore)

        feed.answer.set { request in
            Page(
                items: (request.offset..<min(request.offset + request.limit, 120)).map { Row(id: $0) },
                total: 120,
                serverHasMore: nil,
                nextCursor: nil
            )
        }
        feed.asked.set([])
        list.retry()
        await list.idle()

        XCTAssertEqual([50], feed.offsets, "the page that failed, not page one")
        XCTAssertEqual(100, list.state.items.count)
        XCTAssertNil(list.state.error)
    }

    // MARK: - what the list is keyed to

    /// A pager re-reads when the saved address moved, and does nothing when
    /// it did not.
    ///
    /// ``ApiRead/serverChanged()``'s rule, for the same reason: the key is
    /// the *value*, so a caller that shouts on every redraw costs nothing.
    func testServerChangedRereadsOnlyWhenTheAddressMoved() async {
        let feed = offsetFeed(total: 10)
        let base = Shared("http://10.0.0.42:6369")
        let list = pager(feed, base: base)

        list.reset()
        await list.idle()
        XCTAssertEqual(1, feed.count)

        list.serverChanged()
        list.serverChanged()
        await list.idle()
        XCTAssertEqual(1, feed.count, "the address has not changed")

        base.set("http://10.0.0.77:6369")
        list.serverChanged()
        await list.idle()
        XCTAssertEqual(2, feed.count)
    }

    /// A pager built in a container joins the server key **itself**.
    ///
    /// The whole reason that initialiser exists: Android resets its pagers
    /// from `LaunchedEffect(serverUrl, …)` in the screen, and this project
    /// has already shipped one read whose re-key was a line somebody left
    /// out.
    func testAPagerBuiltInAContainerJoinsTheServerKeyItself() async {
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let feed = offsetFeed(total: 10)
        let list = Pager<Row, Int>(in: container, keyOf: { $0.id }, fetch: feed.fetch)
        let joined = container.serverChangeSinkCount

        list.reset()
        await list.idle()
        XCTAssertEqual(1, feed.count)

        store.trustServer("http://10.0.0.77:6369")
        XCTAssertTrue(store.setServerURL("http://10.0.0.77:6369"))
        container.serverAddressChanged()
        for _ in 0..<settleYieldLimit where feed.count < 2 { await Task.yield() }
        await list.idle()

        XCTAssertEqual(2, feed.count, "the list re-read because it was told the house moved")

        list.stop()
        XCTAssertEqual(joined - 1, container.serverChangeSinkCount, "and it leaves when the screen does")
    }

    /// Only the named pushes reload; everything else on the socket is
    /// ignored, because the socket subscribes to everything and every
    /// consumer filters client-side.
    func testOnlyTheNamedPushesReload() async {
        let feed = offsetFeed(total: 10)
        let list = pager(feed, eventTypes: ["library.indexer.changed"])
        list.reset()
        await list.idle()
        XCTAssertEqual(1, feed.count)

        list.received(StateEvent(type: "timers.changed", frame: "{}"))
        await list.idle()
        XCTAssertEqual(1, feed.count)

        list.received(StateEvent(type: "library.indexer.changed", frame: "{}"))
        await list.idle()
        XCTAssertEqual(2, feed.count)
    }

    /// `onLoaded` fires on the pager's own success paths and not on a
    /// failure.
    ///
    /// It exists because the one consumer — Music's source filter — would
    /// otherwise be a line every view had to remember after every
    /// `loadMore`, `reload` and `reset`, which is exactly how ``ApiRead``
    /// came to have a seam with no caller.
    func testOnLoadedFiresWhenAPageLandsAndNotWhenOneFails() async {
        let feed = offsetFeed(total: 120)
        let list = pager(feed)
        let landings = Shared(0)
        list.onLoaded = { landings.mutate { $0 += 1 } }

        list.reset()
        await list.idle()
        XCTAssertEqual(1, landings.current)

        list.loadMore()
        await list.idle()
        XCTAssertEqual(2, landings.current)

        feed.answer.set { _ in throw DomovoiError.unreachable("gone") }
        list.loadMore()
        await list.idle()
        XCTAssertEqual(2, landings.current, "a failure is not a landing")
    }

    /// `stop` leaves the server key and drops what is in flight, so a route
    /// that went away is not still reading.
    func testStopLeavesTheKeyAndDropsThePageInFlight() async {
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "t")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let feed = offsetFeed(total: 500)
        let list = Pager<Row, Int>(in: container, keyOf: { $0.id }, fetch: feed.fetch)
        let joined = container.serverChangeSinkCount

        feed.park.set(true)
        list.reset()
        while feed.count == 0 { await Task.yield() }
        list.stop()
        feed.release.set(true)
        await list.idle()

        XCTAssertTrue(list.state.items.isEmpty, "the page it was reading does not land")
        XCTAssertEqual(joined - 1, container.serverChangeSinkCount)
        XCTAssertFalse(feed.ranOut.current)
    }
}
