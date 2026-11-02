import Foundation
import XCTest
@testable import DomovoiKit

/// Videos' data layer against a server that is not a server: what the screen
/// asks for, when a keystroke becomes a request, what a tap costs, and what
/// it refuses to send.
///
/// `VideosScreen.kt` has no test — Android's JVM suite is unit-only and the
/// screen is a composable — so the debounce, the per-library totals and the
/// resume read on a tap are specified by nothing over there.
@MainActor
final class VideosModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// The filter's debounce, as a gate a test opens.
    ///
    /// Bounded rather than `while !open`: an unreleased wait that spun for
    /// ever would be a hung suite instead of a failed test, which is the trap
    /// ``settle(for:what:)`` exists for.
    private final class Waits: Sendable {
        let asked = Shared<[Int64]>([])
        private let opened = Shared<Bool>(false)

        func release() {
            opened.set(true)
        }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let opened = self.opened
            return { ms in
                asked.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    if opened.current { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                throw CancellationError()
            }
        }
    }

    /// The house's `/api/videos`. Two libraries, so the grouping and the
    /// totals have something to be wrong about.
    private final class House: Sendable {
        let total = Shared<Int>(60)
        /// An older server that answers with rows and **no** `libraries`
        /// array. The headings then count what loaded, and a model that kept
        /// the previous needle's counts would draw those instead.
        let omitLibraries = Shared<Bool>(false)
        let recent = Shared<String>(#"{"recent":[]}"#)
        let position = Shared<String>(#"{"position_sec":90}"#)
        let positionStatus = Shared<Int>(200)
        let saveStatus = Shared<Int>(200)

        /// Ids below 40 are in `core:videos` and the rest in `usb`, so a
        /// library boundary falls inside page one and another inside page
        /// two.
        /// A narrowed read answers with **one** row, or none at all when the
        /// house is empty — a stub that always returned a row for a filter
        /// would make "no videos match" untestable.
        private func rows(offset: Int, limit: Int, narrowed: Bool) -> String {
            let count = narrowed ? min(1, total.current) : total.current
            let from = min(offset, count)
            let to = min(from + limit, count)
            guard from < to else { return "" }
            return (from..<to).map { index in
                let library = index < 40 ? "core:videos" : "usb"
                let label = index < 40 ? "Movies" : "USB stick"
                return """
                {"library_id":"\(library)","library_label":"\(label)",\
                "rel":"v\(index).mp4","name":"v\(index).mp4",\
                "size":\(1024 * (index + 1)),"mtime":1762000000}
                """
            }.joined(separator: ",")
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }

                switch parts.path {
                case "/api/health":
                    // The identity gate's probe. A server that did not answer
                    // this would refuse every read below, and every assertion
                    // over them would be an assertion about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")
                case "/api/videos/list":
                    let offset = Int(value("offset") ?? "0") ?? 0
                    let limit = Int(value("limit") ?? "50") ?? 50
                    let narrowed = (value("q")?.isEmpty == false)
                    let body = self.rows(offset: offset, limit: limit, narrowed: narrowed)
                    let count = narrowed ? min(1, self.total.current) : self.total.current
                    // The `libraries` array the headers' counts come from.
                    if self.omitLibraries.current {
                        return .json(#"{"videos":[\#(body)],"total":\#(count)}"#)
                    }
                    let libraries = narrowed
                        ? #"[{"library_id":"core:videos","total":\#(count)}]"#
                        : #"[{"library_id":"core:videos","total":40},{"library_id":"usb","total":20}]"#
                    return .json(#"{"videos":[\#(body)],"total":\#(count),"libraries":\#(libraries)}"#)
                case "/api/videos/recent":
                    return .json(self.recent.current)
                case "/api/videos/position":
                    if request.httpMethod == "POST" {
                        guard self.saveStatus.current == 200 else {
                            return .json(#"{"detail":"no room for that row"}"#, status: self.saveStatus.current)
                        }
                        return .json("{}")
                    }
                    guard self.positionStatus.current == 200 else {
                        return .json(#"{"detail":"away"}"#, status: self.positionStatus.current)
                    }
                    return .json(self.position.current)
                default:
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func rig(
        house: House = House(),
        deviceID: String? = "ios-1",
        waits: Waits = Waits()
    ) -> (VideosModel, House, Waits) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = VideosModel(in: container, deviceID: deviceID, wait: waits.sleeper)
        return (model, house, waits)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Give every task that could have been started its chance to run.
    ///
    /// For asserting a **negative** — that no request went out, that no wait
    /// was asked for. A bare `await Task.yield()` is not enough and the
    /// mistake is invisible: the assertion passes because nothing has run
    /// yet, not because nothing happens, so it would pass with the guard it
    /// is testing deleted. Two of the assertions in this file were exactly
    /// that until a sabotage run found them.
    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    private func requests(to path: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == path }
    }

    /// Every request to `path`, as `URLComponents` reads its query.
    ///
    /// **Normalized, not the wire spelling.** `URLComponents.query`
    /// re-spells escapes that are legal unescaped in a query, so a
    /// `library_id=core%3Avideos` that went out on the wire reads back here
    /// as `library_id=core:videos` — the same decoding trap as `URL.path`,
    /// one component along. Fine for asserting *which* parameters went; use
    /// `url.absoluteString` to assert how one was escaped.
    private func queries(to path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                parts.path == path
            else { return nil }
            return parts.query ?? ""
        }
    }

    private func body(of request: URLRequest) -> String {
        guard let data = request.httpBody else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    /// Start the model and wait for the grid and the strip to answer.
    private func started(
        _ model: VideosModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.start()
        await settleUntil({ model.list.state.loaded }, "the grid", file: file, line: line)
        await settleUntil({ model.recent.answered }, "the strip", file: file, line: line)
        XCTAssertNil(model.list.state.error, file: file, line: line)
    }

    // MARK: - what it reads on arrival

    func testItReadsPageOneAndTheResumeStrip() async throws {
        let (model, _, _) = rig()

        await started(model)

        XCTAssertEqual(["sort=library&limit=50"], queries(to: "/api/videos/list"))
        XCTAssertEqual(["device_id=ios-1"], queries(to: "/api/videos/recent"))
        XCTAssertEqual(50, model.list.state.items.count)
        XCTAssertEqual(60, model.list.state.total)
        XCTAssertEqual("showing 50 of 60 videos", model.countText)
    }

    /// Before the list has answered there is no count, because "0 videos" and
    /// "not asked yet" are different facts.
    func testThereIsNoCountBeforeTheListAnswers() {
        let (model, _, _) = rig()

        XCTAssertNil(model.countText)
        XCTAssertFalse(model.isEmpty)
        XCTAssertNil(model.emptyNote)
    }

    /// The header counts come from the server's `libraries` array, not from
    /// the rows that happen to have loaded: page one holds forty of
    /// `core:videos` and ten of `usb`, and the headings say 40 and 20.
    func testTheHeadingsCountWhatTheServerSaysTheLibraryHolds() async throws {
        let (model, _, _) = rig()

        await started(model)

        XCTAssertEqual(["core:videos": 40, "usb": 20], model.totals)
        let sections = model.sections
        XCTAssertEqual(["core:videos", "usb"], sections.map(\.libraryID))
        let movies = try XCTUnwrap(sections.first)
        XCTAssertEqual("Movies", movies.label)
        XCTAssertEqual(40, movies.count)
        XCTAssertEqual(40, movies.tiles.count)
        let usb = try XCTUnwrap(sections.last)
        XCTAssertEqual(20, usb.count)
        XCTAssertEqual(10, usb.tiles.count)
    }

    /// A phone with no device id is a phone with no resume store: the strip
    /// is never asked for, rather than asked for with a blank key that would
    /// read somebody else's rows.
    func testAPhoneWithNoDeviceIdNeverAsksForTheStrip() async throws {
        let (model, _, _) = rig(deviceID: nil)

        await started(model)

        XCTAssertTrue(requests(to: "/api/videos/recent").isEmpty)
        XCTAssertTrue(model.recentVideos.isEmpty)
        XCTAssertFalse(model.showsRecent)
    }

    func testTheStripIsShownOnlyWhenItHasRowsInIt() async throws {
        let house = House()
        house.recent.set(#"""
        {"recent":[{"library_id":"core:videos","rel":"v1.mp4","name":"v1.mp4",
                    "position_sec":72,"duration_sec":2910,"title":"Holiday"}]}
        """#)
        let (model, _, _) = rig(house: house)

        await started(model)

        XCTAssertTrue(model.showsRecent)
        let row = try XCTUnwrap(model.recentVideos.first)
        XCTAssertEqual("Holiday", row.displayTitle)
        XCTAssertEqual("1:12 / 48:30", row.resumeLine)
        XCTAssertEqual("recent", model.entries.first?.id)
    }

    // MARK: - the filter

    /// A keystroke waits for the next one. The wait is asked for with the
    /// debounce's own period, and **no request goes out** until it returns.
    func testAKeystrokeWaitsBeforeItAsksForAnything() async throws {
        let (model, _, waits) = rig()
        await started(model)
        let before = requests(to: "/api/videos/list").count

        model.typed("sea")

        XCTAssertEqual("sea", model.filter)
        // What was asked for, and asked for once.
        await settleUntil({ !waits.asked.current.isEmpty }, "the debounce")
        XCTAssertEqual([VideosModel.filterDebounceMs], waits.asked.current)
        XCTAssertEqual(before, requests(to: "/api/videos/list").count)
        // The needle is still the old one: it is what the rows on screen came
        // from, not what has been typed since.
        XCTAssertEqual("", model.needle)

        waits.release()
        await settleUntil({ model.needle == "sea" }, "the filter")
        await settleUntil({ model.list.state.loaded }, "the filtered grid")
        XCTAssertEqual("sort=library&q=sea&limit=50", queries(to: "/api/videos/list").last)
    }

    /// Four keystrokes, one query.
    ///
    /// ### What this does and does not pin
    ///
    /// It passes with `typed`'s `debounce?.cancel()` deleted, and that is
    /// worth writing down rather than leaving as a surprise for whoever
    /// deletes it. `applyFilter` also calls `list.stop()` on the pager it
    /// replaces, so three un-cancelled debounces produce three applyFilters
    /// whose first two pagers are stopped before their request reaches the
    /// wire — one query either way. The cancel's value is the work it avoids
    /// (three pagers built and torn down, three needles applied), not a
    /// different answer, and nothing observable from outside this class
    /// tells the two apart. So this pins the user-visible property, which is
    /// the one that matters, and the cancel stays because doing that work
    /// for nothing on every keystroke is not free on a phone.
    func testTypingFastAsksOnce() async throws {
        let (model, _, waits) = rig()
        await started(model)
        let before = requests(to: "/api/videos/list").count

        model.typed("s")
        model.typed("se")
        model.typed("sea")
        model.typed("sea ")
        waits.release()

        await settleUntil({ model.needle == "sea" }, "the filter")
        await settleUntil({ model.list.state.loaded }, "the filtered grid")
        XCTAssertEqual(before + 1, requests(to: "/api/videos/list").count)
        XCTAssertEqual("sort=library&q=sea&limit=50", queries(to: "/api/videos/list").last)
    }

    /// Typing the needle that is already applied — adding a space, deleting
    /// it — asks for nothing and does not even start a wait.
    func testTypingTheSameNeedleAsksForNothing() async throws {
        let (model, _, waits) = rig()
        await started(model)
        let before = requests(to: "/api/videos/list").count

        model.typed("  ")
        await nothingHappens()

        XCTAssertEqual("  ", model.filter)
        // No wait was even **asked for**, which is the assertion with teeth:
        // a model that started a debounce and only then found the needle
        // unchanged would record one here.
        XCTAssertTrue(waits.asked.current.isEmpty, "\(waits.asked.current)")
        XCTAssertEqual(before, requests(to: "/api/videos/list").count)
    }

    /// A filtered grid is a search result, and the strip is not part of it: a
    /// row the needle does not match would otherwise sit above "no videos
    /// match".
    func testTheStripIsHiddenWhileAFilterIsOn() async throws {
        let house = House()
        house.recent.set(#"{"recent":[{"library_id":"core:videos","rel":"v1.mp4","name":"v1.mp4"}]}"#)
        let (model, _, waits) = rig(house: house)
        await started(model)
        XCTAssertTrue(model.showsRecent)

        model.typed("sea")
        waits.release()
        await settleUntil({ model.needle == "sea" }, "the filter")

        XCTAssertFalse(model.showsRecent)
        // The rows are still held — nothing was thrown away, it is not drawn.
        XCTAssertFalse(model.recentVideos.isEmpty)
        XCTAssertNotEqual("recent", model.entries.first?.id)
    }

    /// The old totals go with the old needle. A filtered page that holds one
    /// video must not sit under a heading saying 40.
    func testANewNeedleClearsTheHeadingCounts() async throws {
        let (model, _, waits) = rig()
        await started(model)
        XCTAssertEqual(["core:videos": 40, "usb": 20], model.totals)

        model.typed("sea")
        waits.release()
        await settleUntil({ model.needle == "sea" }, "the filter")
        await settleUntil({ model.list.state.items.count == 1 }, "the filtered grid")

        XCTAssertEqual(["core:videos": 1], model.totals)
        let section = try XCTUnwrap(model.sections.first)
        XCTAssertEqual(1, section.count)
    }

    /// The old counts go with the old needle **even when the new answer
    /// carries none of its own**.
    ///
    /// The test above passes whether or not `applyFilter` clears the totals,
    /// because the filtered answer happens to bring a `libraries` array that
    /// overwrites them. This one takes that away: an older server sends rows
    /// and no totals, and then a model that had not cleared would sit one
    /// video under a heading saying forty.
    func testANeedleAgainstAServerThatSendsNoTotalsFallsBackToWhatLoaded() async throws {
        let house = House()
        let (model, _, waits) = rig(house: house)
        await started(model)
        XCTAssertEqual(["core:videos": 40, "usb": 20], model.totals)

        house.omitLibraries.set(true)
        model.typed("sea")
        waits.release()
        await settleUntil({ model.needle == "sea" }, "the filter")
        await settleUntil({ model.list.state.items.count == 1 }, "the filtered grid")

        XCTAssertEqual([:], model.totals)
        let section = try XCTUnwrap(model.sections.first)
        XCTAssertEqual(1, section.count)
        XCTAssertEqual(1, section.tiles.count)
    }

    func testAFilterThatMatchesNothingSaysSoAndNamesTheFilter() async throws {
        let house = House()
        house.total.set(0)
        let (model, _, waits) = rig(house: house)
        await started(model)

        model.typed("nothing at all")
        waits.release()
        await settleUntil({ model.needle == "nothing at all" }, "the filter")
        await settleUntil({ model.list.state.loaded && model.list.state.items.isEmpty }, "the empty grid")

        let note = try XCTUnwrap(model.emptyNote)
        XCTAssertEqual(VideoStrings.noMatch, note.title)
        XCTAssertEqual(VideoStrings.filterIs("nothing at all"), note.detail)
    }

    func testAHouseWithNoVideosSaysWhereToPutSome() async throws {
        let house = House()
        house.total.set(0)
        let (model, _, _) = rig(house: house)

        await started(model)

        let note = try XCTUnwrap(model.emptyNote)
        XCTAssertEqual(VideoStrings.noneFound, note.title)
        XCTAssertEqual(VideoStrings.noneFoundSub, note.detail)
    }

    // MARK: - loading more, and rescanning

    /// The guarded question: a grid showing fifty rows asks near the end and
    /// nowhere else.
    func testItAsksForAnotherPageOnlyNearTheEndOfWhatIsLoaded() async throws {
        let (model, _, _) = rig()
        await started(model)

        XCTAssertFalse(model.shouldLoadMore(lastVisible: 0))
        XCTAssertFalse(model.shouldLoadMore(lastVisible: 36))
        XCTAssertTrue(model.shouldLoadMore(lastVisible: 37))
        XCTAssertTrue(model.shouldLoadMore(lastVisible: 49))
    }

    /// Nothing more to load, nothing asked — however far down the grid the
    /// last tile is.
    func testAFullyLoadedGridAsksForNothing() async throws {
        let house = House()
        house.total.set(30)
        let (model, _, _) = rig(house: house)
        await started(model)

        XCTAssertFalse(model.list.state.canLoadMore)
        XCTAssertFalse(model.shouldLoadMore(lastVisible: 29))
    }

    func testTheSecondPageJoinsTheFirstAndKeepsTheLibrariesInOrder() async throws {
        let (model, _, _) = rig()
        await started(model)

        model.list.loadMore()
        await settleUntil({ model.list.state.items.count == 60 }, "page two")

        XCTAssertEqual("sort=library&limit=50&offset=50", queries(to: "/api/videos/list").last)
        XCTAssertEqual(["core:videos", "usb"], model.sections.map(\.libraryID))
        // All twenty of `usb` are loaded now, so its heading and its tiles
        // agree.
        let usb = try XCTUnwrap(model.sections.last)
        XCTAssertEqual(20, usb.count)
        XCTAssertEqual(20, usb.tiles.count)
    }

    /// "rescan videos" asks the server to re-walk the folders rather than
    /// answer from its cached listing.
    func testRescanAsksForAFreshWalk() async throws {
        let (model, _, _) = rig()
        await started(model)

        model.rescan()
        await settleUntil({ queries(to: "/api/videos/list").count > 1 }, "the rescan")

        XCTAssertEqual("sort=library&limit=50&refresh=true", queries(to: "/api/videos/list").last)
    }

    // MARK: - opening the feed

    /// A tap reads where this watcher left off, and the feed opens there.
    func testATapReadsWhereItWasLeftOff() async throws {
        let (model, _, _) = rig()
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.open(video, from: .grid, order: model.gridOrder)
        await settleUntil({ model.feed != nil }, "the feed")

        let open = try XCTUnwrap(model.feed)
        XCTAssertEqual(.grid, open.from)
        XCTAssertEqual(90, open.resumeSec)
        XCTAssertEqual(0, open.start)
        XCTAssertEqual(50, open.videos.count)
        let read = try XCTUnwrap(requests(to: "/api/videos/position").first)
        XCTAssertEqual(1, requests(to: "/api/videos/position").count)
        // The wire spelling, so an unescaped colon in the library id cannot
        // hide: `URLComponents.query` would re-spell `%3A` as `:`.
        XCTAssertEqual(
            "http://10.0.0.42:6369/api/videos/position?library_id=core%3Avideos&path=v0.mp4&device_id=ios-1",
            read.url?.absoluteString
        )
        XCTAssertFalse(model.opening)
    }

    /// The start index is where the video sits **in the order shown**, not
    /// its index in the page — which is what makes the feed open on the tile
    /// that was tapped.
    func testTheFeedOpensOnTheVideoThatWasTapped() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        let video = try XCTUnwrap(order.dropFirst(7).first)

        model.open(video, from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")

        XCTAssertEqual(7, try XCTUnwrap(model.feed).start)
    }

    /// A row the strip already drew carries its own position, so tapping
    /// there costs **no request**.
    func testATapInTheStripCostsNoRequest() async throws {
        let house = House()
        house.recent.set(#"""
        {"recent":[{"library_id":"core:videos","rel":"v1.mp4","name":"v1.mp4",
                    "position_sec":72,"duration_sec":2910}]}
        """#)
        let (model, _, _) = rig(house: house)
        await started(model)
        let video = try XCTUnwrap(model.recentVideos.first)

        model.open(video, from: .recent, order: model.recentVideos, resumeOverride: 72)

        // Synchronously open: no await, because there is nothing to wait for.
        let open = try XCTUnwrap(model.feed)
        XCTAssertEqual(.recent, open.from)
        XCTAssertEqual(72, open.resumeSec)
        XCTAssertTrue(requests(to: "/api/videos/position").isEmpty)
    }

    /// A resume read that failed opens the video at the beginning. Refusing
    /// to open it at all because the resume store did not answer would be the
    /// larger loss.
    func testAFailedResumeReadStillOpensTheVideo() async throws {
        let house = House()
        house.positionStatus.set(503)
        let (model, _, _) = rig(house: house)
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.open(video, from: .grid, order: model.gridOrder)
        await settleUntil({ model.feed != nil }, "the feed")

        XCTAssertEqual(0, try XCTUnwrap(model.feed).resumeSec)
    }

    /// A second tap while the first is still asking must not open two feeds.
    func testASecondTapWhileTheFirstIsAskingIsIgnored() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        let first = try XCTUnwrap(order.first)
        let second = try XCTUnwrap(order.dropFirst().first)

        model.open(first, from: .grid, order: order)
        XCTAssertTrue(model.opening)
        model.open(second, from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")

        XCTAssertEqual(1, requests(to: "/api/videos/position").count)
        XCTAssertEqual(0, try XCTUnwrap(model.feed).start)
    }

    /// And a tap while a feed is already open is ignored too — a long-press
    /// that lands as a tap behind the cover.
    func testATapWhileTheFeedIsOpenIsIgnored() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        model.open(try XCTUnwrap(order.first), from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")
        let before = requests(to: "/api/videos/position").count

        model.open(try XCTUnwrap(order.dropFirst(3).first), from: .grid, order: order)

        XCTAssertEqual(before, requests(to: "/api/videos/position").count)
        XCTAssertEqual(0, try XCTUnwrap(model.feed).start)
    }

    /// A phone with no device id has no resume store, so it opens at the
    /// beginning without asking.
    func testAPhoneWithNoDeviceIdOpensAtTheBeginning() async throws {
        let (model, _, _) = rig(deviceID: nil)
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.open(video, from: .grid, order: model.gridOrder)

        XCTAssertEqual(0, try XCTUnwrap(model.feed).resumeSec)
        XCTAssertTrue(requests(to: "/api/videos/position").isEmpty)
    }

    // MARK: - closing the feed

    /// Closing puts the video the viewer ended on back in the middle of the
    /// grid, and re-reads the strip — because a position just moved and
    /// nothing else would show it.
    func testClosingTheFeedCentresTheVideoAndRereadsTheStrip() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        model.open(try XCTUnwrap(order.first), from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")
        let before = requests(to: "/api/videos/recent").count

        model.feedClosed(on: "core:videos:v9.mp4")

        XCTAssertNil(model.feed)
        XCTAssertEqual("core:videos:v9.mp4", model.centerOn?.key)
        XCTAssertEqual(.grid, model.centerOn?.from)
        // Entry 0 is the header (there is no strip in this house), so the
        // tenth video is entry ten.
        XCTAssertEqual(10, model.centreIndex())
        await settleUntil(
            { requests(to: "/api/videos/recent").count > before },
            "the strip's re-read"
        )

        model.centred()
        XCTAssertNil(model.centerOn)
        XCTAssertNil(model.centreIndex())
    }

    /// A viewer who emptied the queue ended on nothing, so there is nothing
    /// to centre.
    func testClosingAnEmptiedFeedCentresNothing() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        model.open(try XCTUnwrap(order.first), from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")

        model.feedClosed(on: nil)

        XCTAssertNil(model.feed)
        XCTAssertNil(model.centerOn)
        XCTAssertNil(model.centreIndex())
    }

    /// A video that is no longer in the grid — the list reloaded under the
    /// feed — is not centred on, rather than centred on index −1.
    func testAVideoThatLeftTheGridIsNotCentredOn() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        model.open(try XCTUnwrap(order.first), from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")

        model.feedClosed(on: "core:videos:gone.mp4")

        XCTAssertEqual("core:videos:gone.mp4", model.centerOn?.key)
        XCTAssertNil(model.centreIndex())
    }

    /// A feed opened from the strip centres in the **strip**, whose indices
    /// are its own.
    func testAFeedOpenedFromTheStripCentresInTheStrip() async throws {
        let house = House()
        house.recent.set(#"""
        {"recent":[{"library_id":"core:videos","rel":"v1.mp4","name":"v1.mp4"},
                   {"library_id":"core:videos","rel":"v2.mp4","name":"v2.mp4"}]}
        """#)
        let (model, _, _) = rig(house: house)
        await started(model)
        model.open(
            try XCTUnwrap(model.recentVideos.first),
            from: .recent,
            order: model.recentVideos,
            resumeOverride: 0
        )

        model.feedClosed(on: "core:videos:v2.mp4")

        XCTAssertEqual(.recent, model.centerOn?.from)
        XCTAssertEqual(1, model.centreIndex())
    }

    // MARK: - saving where a video got to

    func testASaveSendsTheWholeRow() async throws {
        let (model, _, _) = rig()
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.savePosition(video, positionSec: 42, durationSec: 2910, ended: false)
        await settleUntil({ !requests(to: "/api/videos/position").isEmpty }, "the save")

        let request = try XCTUnwrap(requests(to: "/api/videos/position").first)
        XCTAssertEqual("POST", request.httpMethod)
        XCTAssertEqual(
            #"{"library_id":"core:videos","path":"v0.mp4","device_id":"ios-1","position_sec":42,"duration_sec":2910,"title":"v0.mp4"}"#,
            body(of: request)
        )
    }

    /// A video that ran to its end is saved at **zero** — "watched", not
    /// "90% through", which is what stops a finished video sitting in the
    /// strip for ever.
    func testAWatchedVideoIsSavedAtZero() async throws {
        let (model, _, _) = rig()
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.savePosition(video, positionSec: 2890, durationSec: 2910, ended: true)
        await settleUntil({ !requests(to: "/api/videos/position").isEmpty }, "the save")

        let request = try XCTUnwrap(requests(to: "/api/videos/position").first)
        XCTAssertTrue(body(of: request).contains(#""position_sec":0"#), body(of: request))
        XCTAssertTrue(body(of: request).contains(#""duration_sec":2910"#), body(of: request))
    }

    func testAPhoneWithNoDeviceIdSavesNothing() async throws {
        let (model, _, _) = rig(deviceID: nil)
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.savePosition(video, positionSec: 42, durationSec: 2910, ended: false)
        await nothingHappens()

        XCTAssertTrue(
            requests(to: "/api/videos/position").isEmpty,
            "\(requests(to: "/api/videos/position").compactMap { $0.url?.absoluteString })"
        )
    }

    /// A failed save is **silent**: it fires every five seconds while a video
    /// plays, and a toast per failure would bury the screen.
    func testAFailedSaveSaysNothing() async throws {
        let house = House()
        house.saveStatus.set(503)
        let (model, _, _) = rig(house: house)
        await started(model)
        let video = try XCTUnwrap(model.gridOrder.first)

        model.savePosition(video, positionSec: 42, durationSec: nil, ended: false)
        await settleUntil({ !requests(to: "/api/videos/position").isEmpty }, "the save")
        await Task.yield()

        XCTAssertNil(model.toast)
    }

    // MARK: - moving house

    /// Everything this screen holds **of its own** is the old house's.
    ///
    /// The grid and the strip are not asserted here, and that is the point:
    /// ``Pager/serverChanged()`` and ``ApiRead/serverChanged()`` are both
    /// guarded on the base address having actually changed (`lastBase !=
    /// base()`), so a call with the same server behind it is deliberately a
    /// no-op — the rows on screen are this server's and re-reading them would
    /// be a wasted round trip. What this screen must let go of regardless is
    /// its own state: an open feed, a video to centre on, a tap in flight and
    /// the headings' counts.
    func testMovingHouseLetsGoOfItsOwnState() async throws {
        let (model, _, _) = rig()
        await started(model)
        let order = model.gridOrder
        model.open(try XCTUnwrap(order.first), from: .grid, order: order)
        await settleUntil({ model.feed != nil }, "the feed")
        model.feedClosed(on: "core:videos:v1.mp4")
        XCTAssertFalse(model.totals.isEmpty)

        model.serverChanged()

        XCTAssertNil(model.feed)
        XCTAssertNil(model.centerOn)
        XCTAssertFalse(model.opening)
        XCTAssertTrue(model.totals.isEmpty)
    }

    /// The toast is the shell's to draw, and this screen's to hand over once.
    func testTheToastIsHandedOverOnce() {
        let (model, _, _) = rig()

        model.setToast("that did not save")
        XCTAssertEqual("that did not save", model.toast)

        model.setToast(nil)
        XCTAssertNil(model.toast)
    }
}
