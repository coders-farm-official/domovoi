import Foundation
import XCTest
@testable import DomovoiKit

/// Podcasts' data layer against a server that is not a server: what the
/// screen asks for, when a keystroke becomes a request, which writes it
/// refuses to repeat, and what a reply that lands late is allowed to change.
///
/// Android has no test over any of it — the three composables are untested on
/// the JVM — so the debounce, the sheet that closes only on success, the card
/// that is removed rather than re-read, and the house a late reply belongs to
/// are specified by nothing over there.
@MainActor
final class PodcastsModelTests: XCTestCase {

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

    /// A search field's debounce, as a gate a test opens.
    ///
    /// Bounded rather than `while !open`: an unreleased wait that spun for
    /// ever would be a hung suite instead of a failed test, which is the trap
    /// ``settle(for:what:)`` exists for.
    private final class Waits: Sendable {
        let asked = Shared<[Int64]>([])
        private let opened = Shared<Bool>(false)

        func release() { opened.set(true) }

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

    /// A house with 62 shows and one back catalogue.
    ///
    /// The seed is chosen so the assertions **discriminate**: the show that a
    /// search must find carries the needle in its **author** and in no title
    /// at all, so a local filter over the loaded page would find nothing and
    /// the test would fail. 62 is past one page of 50, so a count line that
    /// read `62 of 62` would be wrong rather than coincidentally right.
    private final class House: Sendable {
        let shows = Shared<Int>(62)
        let episodes = Shared<Int>(57)
        let pollStatus = Shared<Int>(200)
        let subscribeStatus = Shared<Int>(200)
        let deleteStatus = Shared<Int>(200)
        let discoverStatus = Shared<Int>(200)

        /// `Night Shift NN`, with the one named show at index 0 — the only
        /// row with an author, and the only one with artwork.
        private func showRows(offset: Int, limit: Int, narrowed: Bool) -> String {
            let count = narrowed ? min(1, shows.current) : shows.current
            let from = min(offset, count)
            let to = min(from + limit, count)
            guard from < to else { return "" }
            return (from..<to).map { index in
                guard index > 0 else {
                    return """
                    {"id":1,"feed_url":"https://feeds.example.com/kitchen.xml",\
                    "title":"The Kitchen Table","author":"Marisa Whitlock",\
                    "artwork":"/api/podcasts/subscriptions/1/artwork",\
                    "episode_count":57,"downloaded_count":9}
                    """
                }
                // Index 1 is the show the server titled nothing, so the
                // feed-URL fallback has a row of its own.
                if index == 1 {
                    return """
                    {"id":2,"feed_url":"https://feeds.example.com/untitled.xml",\
                    "title":null,"episode_count":3,"downloaded_count":0}
                    """
                }
                return """
                {"id":\(index + 1),"feed_url":"https://feeds.example.com/ns\(index).xml",\
                "title":"Night Shift \(index)","episode_count":\(index),"downloaded_count":0}
                """
            }.joined(separator: ",")
        }

        private func episodeRows(offset: Int, limit: Int, narrowed: Bool) -> String {
            let count = narrowed ? min(1, episodes.current) : episodes.current
            let from = min(offset, count)
            let to = min(from + limit, count)
            guard from < to else { return "" }
            return (from..<to).map { index in
                guard index > 0 else {
                    return """
                    {"id":901,"title":"Brining a bird","duration_sec":3180,"has_file":true,\
                    "download_status":"downloaded","file_ext":".mp3",\
                    "chapters":[{"title":"Why brine","start_sec":0},{"title":"The bird","start_sec":640}]}
                    """
                }
                return """
                {"id":\(901 + index),"title":"Stock from a carcass \(index)",\
                "duration_sec":1800,"has_file":false,"download_status":"pending","chapters":null}
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
                let offset = Int(value("offset") ?? "0") ?? 0
                let limit = Int(value("limit") ?? "50") ?? 50
                let narrowed = (value("q")?.isEmpty == false)
                let method = request.httpMethod ?? "GET"

                switch (method, parts.path) {
                case ("GET", "/api/health"):
                    // The identity gate's probe. A server that did not answer
                    // this would refuse every read below, and every assertion
                    // over them would be an assertion about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")

                case ("GET", "/api/podcasts/subscriptions"):
                    let count = narrowed ? min(1, self.shows.current) : self.shows.current
                    let rows = self.showRows(offset: offset, limit: limit, narrowed: narrowed)
                    return .json(#"{"items":[\#(rows)],"total":\#(count),"limit":\#(limit),"has_more":\#(offset + limit < count),"next_cursor":null,"offset":\#(offset)}"#)

                case ("POST", "/api/podcasts/subscriptions"):
                    guard self.subscribeStatus.current == 200 else {
                        return .json(
                            #"{"detail":"refusing this feed URL — it resolves to this house"}"#,
                            status: self.subscribeStatus.current
                        )
                    }
                    self.shows.mutate { $0 += 1 }
                    return .json(#"{"id":99,"feed_url":"https://new/f.xml","title":null,"keep_n":5,"artwork":null}"#)

                case ("DELETE", let path) where path.hasPrefix("/api/podcasts/subscriptions/"):
                    guard self.deleteStatus.current == 200 else {
                        return .json(#"{"detail":"subscription 1 not found"}"#, status: self.deleteStatus.current)
                    }
                    return .json(#"{"deleted":true}"#)

                case ("GET", let path) where path.hasSuffix("/episodes"):
                    let count = narrowed ? min(1, self.episodes.current) : self.episodes.current
                    let rows = self.episodeRows(offset: offset, limit: limit, narrowed: narrowed)
                    return .json(#"{"items":[\#(rows)],"total":\#(count),"limit":\#(limit),"has_more":\#(offset + limit < count),"next_cursor":null,"offset":\#(offset)}"#)

                case ("POST", "/api/podcasts/poll"):
                    guard self.pollStatus.current == 200 else {
                        return .json(#"{"detail":"poll failed: feed timed out"}"#, status: self.pollStatus.current)
                    }
                    return .json(#"{"polled":4,"new":2,"downloaded":3,"evicted":0}"#)

                case ("GET", "/api/podcasts/discover"):
                    guard self.discoverStatus.current == 200 else {
                        return .json(
                            #"{"detail":"internet access is turned off for podcast search"}"#,
                            status: self.discoverStatus.current
                        )
                    }
                    return .json("""
                    [{"title":"The Kitchen Table","author":"Marisa Whitlock",
                      "artwork":"/api/podcasts/discover/artwork/k1",
                      "feed_url":"https://feeds.example.com/kitchen.xml"},
                     {"title":"Anonymous","feed_url":"https://feeds.example.com/anon.xml"}]
                    """)

                default:
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func rig(
        house: House = House(),
        waits: Waits = Waits()
    ) -> (PodcastsModel, House, Waits, AppContainer, InMemoryShellStore) {
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
        let model = PodcastsModel(in: container, wait: waits.sleeper)
        return (model, house, waits, container, store)
    }

    /// A model over a dispatcher **the caller installed**.
    ///
    /// ``rig(house:waits:)`` calls `House.install()`, which replaces whatever
    /// ``parkRequest(to:answering:otherwise:)`` just installed — so the two
    /// parked cases below build their house themselves and come here for the
    /// container. Getting this backwards is silent: the park never arrives,
    /// `settleUntil` fails on a timeout, and the reason is two lines apart.
    private func parkedRig() -> (PodcastsModel, AppContainer, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: server, name: nil)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (PodcastsModel(in: container, wait: { _ in }), container, store)
    }

    /// The identity gate's probe, answered properly: a server that did not
    /// prove itself would refuse every read, and every assertion over them
    /// would be an assertion about nothing.
    /// `nonisolated` because a `URLProtocol`'s dispatcher is called on a
    /// `URLSession` worker thread, and every member of this `@MainActor`
    /// class — statics included — is isolated unless it says otherwise.
    nonisolated private static func healthAnswer(_ parts: URLComponents) -> StubURLProtocol.Answer {
        let challenge = parts.queryItems?.first { $0.name == "challenge" }?.value
        return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
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
    /// For asserting a **negative** — that no request went out, that no sheet
    /// closed. A bare `await Task.yield()` is not enough and the mistake is
    /// invisible: the assertion passes because nothing has run yet, not
    /// because nothing happens, so it would pass with the guard it is testing
    /// deleted.
    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    private func requests(to path: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == path }
    }

    /// Every **GET** to `path`, as `URLComponents` reads its query.
    ///
    /// GETs only, and that is load-bearing here: a subscribe POSTs to
    /// `/api/podcasts/subscriptions`, the very path the grid reads, so a
    /// helper that counted every method would let the write itself satisfy
    /// an assertion about the list being re-read.
    ///
    /// Normalized, not the wire spelling: `URLComponents.query` re-spells
    /// escapes that are legal unescaped in a query. Fine for asserting
    /// *which* parameters went; use `url.absoluteString` to assert how one
    /// was escaped.
    private func queries(to path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard
                request.httpMethod == "GET",
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

    private func started(
        _ model: PodcastsModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.start()
        await settleUntil({ model.shows.state.loaded }, "the shows grid", file: file, line: line)
        XCTAssertNil(model.shows.state.error, file: file, line: line)
    }

    // MARK: - what it reads on arrival

    func testItReadsPageOneOfTheShowsGrid() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }

        XCTAssertEqual(["limit=50"], queries(to: "/api/podcasts/subscriptions"))
        XCTAssertEqual(50, model.shows.state.items.count)
        XCTAssertEqual(62, model.shows.state.total)
        XCTAssertEqual("showing 50 of 62 shows", model.countText)
        XCTAssertTrue(model.shows.state.hasMore)

        let first = model.shows.state.items.first
        XCTAssertEqual("The Kitchen Table", first?.displayTitle)
        XCTAssertEqual("Marisa Whitlock", first?.author)
        XCTAssertEqual("9/57 downloaded", first?.downloadedLine)
        XCTAssertEqual(
            "https://feeds.example.com/untitled.xml",
            model.shows.state.items.dropFirst().first?.displayTitle,
            "the title ?? feed_url chain, on the row the server titled nothing"
        )
    }

    /// Before the grid has answered there is no count and no empty state,
    /// because "0 shows" and "not asked yet" are different facts.
    func testThereIsNoCountOrEmptyStateBeforeTheGridAnswers() {
        let (model, _, _, _, _) = rig()

        XCTAssertNil(model.countText)
        XCTAssertFalse(model.isEmpty)
        XCTAssertNil(model.emptyNote)
        XCTAssertNil(model.episodeCountText)
        XCTAssertNil(model.episodesEmptyNote)
    }

    func testAnEmptyHouseSaysSubscribeAndASearchWithNoHitsSaysSo() async {
        let house = House()
        house.shows.set(0)
        let (model, _, waits, _, _) = rig(house: house)

        await started(model)
        defer { model.stop() }

        XCTAssertTrue(model.isEmpty)
        XCTAssertEqual(PodcastStrings.noSubscriptions, model.emptyNote?.title)
        XCTAssertEqual(PodcastStrings.noSubscriptionsDetail, model.emptyNote?.detail)

        model.typed("Whitlock")
        waits.release()
        await settleUntil { model.needle == "Whitlock" && model.shows.state.loaded }
        XCTAssertEqual(PodcastStrings.noShowsMatch, model.emptyNote?.title)
        XCTAssertEqual("search = \"Whitlock\"", model.emptyNote?.detail)
    }

    // MARK: - the search

    /// A keystroke is a wait, not a request. The wait is the one Android
    /// asks for, 250 ms (`PodcastsScreen.kt:88`).
    func testAKeystrokeWaitsBeforeItBecomesARequest() async {
        let (model, _, waits, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let before = queries(to: "/api/podcasts/subscriptions").count

        model.typed("Whit")
        await nothingHappens()

        XCTAssertEqual("Whit", model.filter)
        XCTAssertEqual("", model.needle, "nothing has been asked for yet")
        XCTAssertEqual(before, queries(to: "/api/podcasts/subscriptions").count)
        XCTAssertEqual([PodcastsModel.searchDebounceMs], waits.asked.current)
    }

    /// **The search is the server's, not a local filter**, and the needle
    /// proves it: `Whitlock` is in that show's **author** and in no title at
    /// all (`podcasts.py:171-174` searches title, author *and* feed URL), so
    /// a local filter over the loaded page would show nothing.
    func testTheNeedleGoesToTheServerAsAQueryParameter() async {
        let (model, _, waits, _, _) = rig()

        await started(model)
        defer { model.stop() }

        model.typed("  Whitlock  ")
        waits.release()
        await settleUntil { model.needle == "Whitlock" }
        await settleUntil { model.shows.state.loaded && model.shows.state.items.count == 1 }

        XCTAssertEqual(
            ["limit=50", "q=Whitlock&limit=50"],
            queries(to: "/api/podcasts/subscriptions"),
            "trimmed, escaped, and joined with & onto the search path"
        )
        XCTAssertEqual("The Kitchen Table", model.shows.state.items.first?.title)
        XCTAssertEqual("showing 1 of 1 shows", model.countText)
    }

    /// Typing and deleting back to the same needle asks for nothing: the
    /// guard is `settled != needle`, not "the text changed".
    func testTypingBackToTheSameNeedleAsksForNothing() async {
        let (model, _, waits, _, _) = rig()

        await started(model)
        defer { model.stop() }

        model.typed("")
        await nothingHappens()

        XCTAssertEqual([], waits.asked.current, "no wait was even started")
        XCTAssertEqual(["limit=50"], queries(to: "/api/podcasts/subscriptions"))
    }

    // MARK: - one show's episodes

    func testOpeningAShowReadsItsEpisodesAndClosingStopsThem() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let show = model.shows.state.items[0]

        model.select(show)
        await settleUntil { model.episodes?.state.loaded == true }

        XCTAssertEqual(show.id, model.selected?.id)
        XCTAssertEqual(
            ["limit=50"], queries(to: "/api/podcasts/subscriptions/1/episodes"),
            "the pager is keyed to the show that was tapped, not to the first row"
        )
        XCTAssertEqual(50, model.episodes?.state.items.count)
        XCTAssertEqual("showing 50 of 57 episodes", model.episodeCountText)

        let newest = model.episodes?.state.items.first
        XCTAssertEqual("Brining a bird", newest?.displayTitle)
        XCTAssertEqual("53:00 · 2 chapters", newest?.metaLine)
        XCTAssertEqual(true, newest?.canPlay)
        // The row with no file: its meta line says why, and nothing draws a
        // play control for it.
        let pending = model.episodes?.state.items.dropFirst().first
        XCTAssertEqual("30:00 · pending", pending?.metaLine)
        XCTAssertEqual(false, pending?.canPlay)

        model.closeEpisodes()
        XCTAssertNil(model.selected)
        XCTAssertNil(model.episodes, "a pager nobody is looking at must not stay on the push registry")
        XCTAssertEqual("", model.episodeNeedle)
    }

    /// Re-tapping the show already open throws nothing away — the back
    /// catalogue is not re-read for a second tap on the same card.
    func testReTappingTheOpenShowDoesNotReReadIt() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let show = model.shows.state.items[0]

        model.select(show)
        await settleUntil { model.episodes?.state.loaded == true }
        let before = queries(to: "/api/podcasts/subscriptions/1/episodes").count

        model.select(show)
        await nothingHappens()

        XCTAssertEqual(before, queries(to: "/api/podcasts/subscriptions/1/episodes").count)
    }

    /// The episodes search is the server's too, over the **title only**
    /// (`podcasts.py:339-341`), and it is debounced separately from the
    /// grid's.
    func testTheEpisodeSearchIsDebouncedAndSentToTheServer() async {
        let (model, _, waits, _, _) = rig()

        await started(model)
        defer { model.stop() }
        model.select(model.shows.state.items[0])
        await settleUntil { model.episodes?.state.loaded == true }

        model.typedEpisode("brine")
        await nothingHappens()
        XCTAssertEqual("", model.episodeNeedle)
        XCTAssertEqual([PodcastsModel.searchDebounceMs], waits.asked.current)

        waits.release()
        await settleUntil { model.episodeNeedle == "brine" }
        await settleUntil { model.episodes?.state.items.count == 1 }

        XCTAssertEqual(
            ["limit=50", "q=brine&limit=50"],
            queries(to: "/api/podcasts/subscriptions/1/episodes")
        )
        XCTAssertEqual("", model.needle, "the grid's own needle is untouched")
    }

    // MARK: - poll now

    func testPollSaysWhatItFoundAndReReadsTheGrid() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let before = queries(to: "/api/podcasts/subscriptions").count

        model.poll()
        XCTAssertTrue(model.polling, "the button reads \"polling…\" while it is in flight")
        await settleUntil { !model.polling }

        XCTAssertEqual(1, requests(to: "/api/podcasts/poll").count)
        XCTAssertEqual(
            "Polled — 3 downloaded, 2 new", model.toast,
            "the counts come off `downloaded` and `new`, not off `polled`"
        )
        await settleUntil { queries(to: "/api/podcasts/subscriptions").count > before }
    }

    /// A poll makes the server fetch every subscribed feed, so a second tap
    /// while one is running sends nothing (`PodcastsScreen.kt:112`).
    func testASecondPollIsNotSentWhileOneIsInFlight() async {
        let park = parkRequest(
            to: "/api/podcasts/poll",
            answering: { .json(#"{"polled":4,"new":2,"downloaded":3,"evicted":0}"#) },
            otherwise: { request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                guard parts.path == "/api/health" else { return .json("{}") }
                return Self.healthAnswer(parts)
            }
        )
        let (model, _, _) = parkedRig()

        model.poll()
        await settleUntil { park.arrived.current }
        model.poll()
        model.poll()
        await nothingHappens()

        XCTAssertEqual(1, requests(to: "/api/podcasts/poll").count)
        park.release.set(true)
        await settleUntil { !model.polling }
        XCTAssertFalse(park.ranOut.current, "the park was released, not timed out")
        XCTAssertEqual(1, requests(to: "/api/podcasts/poll").count)
        model.stop()
    }

    func testAFailedPollSaysSoAndLeavesTheGridAlone() async {
        let house = House()
        house.pollStatus.set(502)
        let (model, _, _, _, _) = rig(house: house)

        await started(model)
        defer { model.stop() }
        let before = queries(to: "/api/podcasts/subscriptions").count

        model.poll()
        await settleUntil { !model.polling }

        // ``ApiFailure/message`` is `"{status}: {body}"`, so the toast carries
        // the server's own JSON after the status — the shape every other
        // failure assertion in this suite checks by prefix.
        let said = model.toast ?? ""
        XCTAssertTrue(said.hasPrefix("Poll failed: 502"), "got \(said)")
        XCTAssertTrue(said.contains("poll failed: feed timed out"), "got \(said)")
        await nothingHappens()
        XCTAssertEqual(
            before, queries(to: "/api/podcasts/subscriptions").count,
            "nothing changed, so nothing is re-read"
        )
    }

    // MARK: - subscribing

    func testSubscribingPostsTheFeedReloadsTheGridAndClosesTheSheet() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        model.openSubscribe()
        let before = queries(to: "/api/podcasts/subscriptions").count

        model.subscribe(feedURL: "  https://new/f.xml  ")
        XCTAssertTrue(model.subscribing)
        await settleUntil { !model.subscribing }

        let posts = StubURLProtocol.recorded.filter {
            $0.httpMethod == "POST" && $0.url?.path == "/api/podcasts/subscriptions"
        }
        XCTAssertEqual(1, posts.count)
        XCTAssertEqual(#"{"feed_url":"https://new/f.xml"}"#, body(of: posts[0]))
        XCTAssertEqual(
            "DomovoiApp", posts[0].value(forHTTPHeaderField: "X-Requested-With"),
            "without it the server refuses the write before the side effect lands"
        )
        XCTAssertEqual(PodcastStrings.subscribed, model.toast)
        XCTAssertFalse(model.subscribeSheetOpen)
        await settleUntil { queries(to: "/api/podcasts/subscriptions").count > before }
        await settleUntil { model.shows.state.total == 63 }
    }

    /// The sheet stays up on a failure, with the server's own reason **in
    /// it** — a toast would be drawn behind the sheet, where nobody can read
    /// it (DECISIONS.md).
    func testAFailedSubscribeKeepsTheSheetOpenAndSaysWhyInsideIt() async {
        let house = House()
        house.subscribeStatus.set(400)
        let (model, _, _, _, _) = rig(house: house)

        await started(model)
        defer { model.stop() }
        model.openSubscribe()

        model.subscribe(feedURL: "https://this-house/f.xml")
        await settleUntil { !model.subscribing }

        XCTAssertTrue(model.subscribeSheetOpen, "the person has to be able to correct the URL")
        let why = model.subscribeError ?? ""
        XCTAssertTrue(why.hasPrefix("Subscribe failed: 400"), "got \(why)")
        XCTAssertTrue(
            why.contains("refusing this feed URL"), "got \(why)"
        )
        XCTAssertNil(model.toast, "nothing is said behind the sheet")
    }

    /// A blank feed, and a second tap while one is in flight, send nothing.
    func testABlankFeedAndARepeatTapSendNothing() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }

        model.subscribe(feedURL: "   ")
        await nothingHappens()
        XCTAssertFalse(model.subscribing)
        XCTAssertEqual(
            0,
            StubURLProtocol.recorded.filter {
                $0.httpMethod == "POST" && $0.url?.path == "/api/podcasts/subscriptions"
            }.count
        )
    }

    func testDiscoveryFillsTheSheetAndRefusesABlankTerm() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        model.openSubscribe()

        model.discover("  ")
        await nothingHappens()
        XCTAssertEqual(
            0, requests(to: "/api/podcasts/discover").count,
            "the server types q with min_length=1, so a blank would be a 422"
        )

        model.discover(" the kitchen table ")
        XCTAssertTrue(model.discovering)
        await settleUntil { !model.discovering }

        XCTAssertEqual(["q=the%20kitchen%20table"], queries(to: "/api/podcasts/discover"))
        XCTAssertEqual(2, model.discoveries.count)
        XCTAssertEqual("The Kitchen Table", model.discoveries.first?.displayTitle)
        XCTAssertNil(model.discoveries.last?.author, "the directory sends some hits with no artist")
        XCTAssertTrue(model.discoveries.allSatisfy(\.canSubscribe))
        XCTAssertNil(model.discoveryError)

        // Closing the sheet forgets the hits: they are the other house's as
        // soon as the person moves, and a stale list behind a reopened sheet
        // reads as a search that just ran.
        model.closeSubscribe()
        XCTAssertEqual([], model.discoveries)
    }

    /// The internet-off refusal is the server's own sentence, shown as it
    /// comes: it says which of the two failures happened.
    func testAFailedDiscoverySaysWhyInsideTheSheet() async {
        let house = House()
        house.discoverStatus.set(409)
        let (model, _, _, _, _) = rig(house: house)

        await started(model)
        defer { model.stop() }
        model.openSubscribe()

        model.discover("kitchen")
        await settleUntil { !model.discovering }

        let refusal = model.discoveryError ?? ""
        XCTAssertTrue(refusal.hasPrefix("Discovery failed: 409"), "got \(refusal)")
        XCTAssertTrue(
            refusal.contains("internet access is turned off"), "got \(refusal)"
        )
        XCTAssertEqual([], model.discoveries)
        XCTAssertNil(model.toast)
    }

    // MARK: - unsubscribing

    func testUnsubscribingRemovesTheCardItsTotalAndThatShowsSheet() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let show = model.shows.state.items[0]
        model.select(show)
        await settleUntil { model.episodes?.state.loaded == true }

        model.confirmUnsubscribe(show)
        XCTAssertEqual(show.id, model.confirmingUnsubscribe?.id)
        model.cancelUnsubscribe()
        XCTAssertNil(model.confirmingUnsubscribe)
        await nothingHappens()
        XCTAssertEqual(
            0, StubURLProtocol.recorded.filter { $0.httpMethod == "DELETE" }.count,
            "a cancelled confirm writes nothing"
        )

        model.unsubscribe(show)
        await settleUntil { model.toast != nil }

        let deletes = StubURLProtocol.recorded.filter { $0.httpMethod == "DELETE" }
        XCTAssertEqual(1, deletes.count)
        XCTAssertEqual("/api/podcasts/subscriptions/1", deletes[0].url?.path)
        XCTAssertEqual(PodcastStrings.unsubscribed, model.toast)
        XCTAssertFalse(model.shows.state.items.contains { $0.id == show.id })
        XCTAssertEqual(
            61, model.shows.state.total,
            "the total comes down with the row, so the count line cannot claim a show that is gone"
        )
        XCTAssertNil(model.selected, "this show's sheet closes with it")
        XCTAssertNil(model.episodes)
    }

    /// The reply can land after the sheet moved on — the DELETE is the
    /// household's write and is not cancelled by a dismissal
    /// (`PodcastsScreen.kt:183-185`). Only the show it was for may close.
    func testUnsubscribingDoesNotCloseADifferentShowsSheet() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let first = model.shows.state.items[0]
        let second = model.shows.state.items[1]

        model.unsubscribe(first)
        model.select(second)
        await settleUntil { model.toast != nil }

        XCTAssertEqual(PodcastStrings.unsubscribed, model.toast)
        XCTAssertFalse(model.shows.state.items.contains { $0.id == first.id })
        XCTAssertEqual(
            second.id, model.selected?.id,
            "the other show's sheet is still the one open"
        )
    }

    func testAFailedUnsubscribeKeepsTheCardAndSaysSo() async {
        let house = House()
        house.deleteStatus.set(404)
        let (model, _, _, _, _) = rig(house: house)

        await started(model)
        defer { model.stop() }
        let show = model.shows.state.items[0]

        model.unsubscribe(show)
        await settleUntil { model.toast != nil }

        XCTAssertTrue(
            model.toast?.hasPrefix("failed: 404") == true,
            "Android's own wording for this one write; got \(model.toast ?? "nothing")"
        )
        XCTAssertTrue(model.toast?.contains("subscription 1 not found") == true)
        XCTAssertTrue(
            model.shows.state.items.contains { $0.id == show.id },
            "a write that did not happen must not take the row off the grid"
        )
        XCTAssertEqual(62, model.shows.state.total)
    }

    // MARK: - the answer is filed under the house that was asked

    /// **The switch happens INSIDE the round trip**, which is the only place
    /// it means anything.
    ///
    /// The shape, for the fourth time on this track
    /// ([[read-after-await-wrong-key]]): a value read after an `await` that
    /// should have been captured before it. Here the value is the house. A
    /// subscription id is the *house's* id, so without
    /// ``PodcastsModel``'s epoch an unsubscribe for A would remove the card
    /// with the same id in B, close B's sheet, and put A's "Unsubscribed"
    /// under B's name.
    func testAnUnsubscribeForOneHouseDoesNothingToAnother() async {
        let park = parkRequest(
            to: "/api/podcasts/subscriptions/1",
            answering: { .json(#"{"deleted":true}"#) },
            otherwise: { request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                if parts.path == "/api/health" { return Self.healthAnswer(parts) }
                // Both houses answer the same one-show grid, so the card that
                // must survive the switch has the very id the DELETE named.
                return .json("""
                {"items":[{"id":1,"title":"The Kitchen Table","episode_count":57,
                 "downloaded_count":9}],"total":1,"limit":50,"has_more":false,
                 "next_cursor":null,"offset":0}
                """)
            }
        )
        let (model, container, store) = parkedRig()
        defer { model.stop() }

        model.start()
        await settleUntil { model.shows.state.loaded }
        let show = model.shows.state.items[0]
        model.select(show)
        await settleUntil { model.episodes?.state.loaded == true }

        model.unsubscribe(show)
        await settleUntil { park.arrived.current }

        // The person switches houses while the DELETE is in flight.
        let next = "http://10.0.0.77:6369"
        store.trustServer(next, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        store.setToken("household-xyz", for: next)
        container.serverAddressChanged()
        model.serverChanged()

        park.release.set(true)
        XCTAssertFalse(park.ranOut.current, "the park was released, not timed out")
        await settleUntil { model.shows.state.loaded && !model.shows.state.items.isEmpty }
        await nothingHappens()

        XCTAssertNil(
            model.toast,
            "A's \"Unsubscribed\" must not be said about B"
        )
        XCTAssertTrue(
            model.shows.state.items.contains { $0.id == 1 },
            "B's show 1 is not A's show 1, whenever A's answer lands"
        )
        XCTAssertEqual(1, model.shows.state.total, "and the total it never left")
    }

    // MARK: - pushes, and the guard on asking for more

    /// The grid reloads on `podcasts.changed`; the episodes list takes
    /// `podcast_positions.changed` as well, which the grid ignores.
    func testTheRightListReloadsForEachPush() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        model.select(model.shows.state.items[0])
        await settleUntil { model.episodes?.state.loaded == true }

        let showsBefore = queries(to: "/api/podcasts/subscriptions").count
        let episodesBefore = queries(to: "/api/podcasts/subscriptions/1/episodes").count

        model.received(StateEvent(type: "podcasts.changed", frame: "{}"))
        await settleUntil { queries(to: "/api/podcasts/subscriptions").count > showsBefore }
        await settleUntil {
            queries(to: "/api/podcasts/subscriptions/1/episodes").count > episodesBefore
        }

        let showsAfter = queries(to: "/api/podcasts/subscriptions").count
        let episodesAfter = queries(to: "/api/podcasts/subscriptions/1/episodes").count

        model.received(StateEvent(type: "podcast_positions.changed", frame: "{}"))
        await settleUntil {
            queries(to: "/api/podcasts/subscriptions/1/episodes").count > episodesAfter
        }
        await nothingHappens()
        XCTAssertEqual(
            showsAfter, queries(to: "/api/podcasts/subscriptions").count,
            "a position write changes a row in the sheet and nothing on the grid"
        )

        // Somebody else's push moves neither.
        let settled = (
            queries(to: "/api/podcasts/subscriptions").count,
            queries(to: "/api/podcasts/subscriptions/1/episodes").count
        )
        model.received(StateEvent(type: "files.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(settled.0, queries(to: "/api/podcasts/subscriptions").count)
        XCTAssertEqual(settled.1, queries(to: "/api/podcasts/subscriptions/1/episodes").count)
    }

    /// The scroll trigger is a guarded question, never "the last tile
    /// appeared": a grid showing a header and a spinner has a last row
    /// trivially within twelve of the end.
    func testAskingForAnotherPageTakesTheGuardedQuestion() async {
        let (model, _, _, _, _) = rig()

        XCTAssertFalse(
            model.shouldLoadMore(lastVisible: 0),
            "nothing is loaded, so there is nothing to be near the end of"
        )
        XCTAssertFalse(model.shouldLoadMoreEpisodes(lastVisible: 0))

        await started(model)
        defer { model.stop() }

        XCTAssertFalse(model.shouldLoadMore(lastVisible: 10), "tile 10 of 50 is not near the end")
        XCTAssertTrue(model.shouldLoadMore(lastVisible: 45))

        model.select(model.shows.state.items[0])
        await settleUntil { model.episodes?.state.loaded == true }
        XCTAssertFalse(model.shouldLoadMoreEpisodes(lastVisible: 20))
        XCTAssertTrue(
            model.shouldLoadMoreEpisodes(lastVisible: 45),
            "a list takes the list threshold, not the grid's"
        )
    }

    /// A pull re-reads the list and **does not** poll the feeds: Android is
    /// explicit that a pull must not fetch the internet behind the person's
    /// back (`PodcastsScreen.kt:104-105`).
    func testAPullReReadsTheListWithoutTouchingTheInternet() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        let before = queries(to: "/api/podcasts/subscriptions").count

        model.reload()
        await settleUntil { queries(to: "/api/podcasts/subscriptions").count > before }
        await nothingHappens()

        XCTAssertEqual(0, requests(to: "/api/podcasts/poll").count)
    }

    /// The whole screen's state is the old house's, and `serverChanged()`
    /// says so for every piece of it.
    func testMovingHouseDropsTheSheetTheHitsAndTheOpenShow() async {
        let (model, _, _, _, _) = rig()

        await started(model)
        defer { model.stop() }
        model.select(model.shows.state.items[0])
        await settleUntil { model.episodes?.state.loaded == true }
        model.openSubscribe()
        model.discover("kitchen")
        await settleUntil { !model.discovering }
        model.confirmUnsubscribe(model.shows.state.items[1])
        XCTAssertFalse(model.discoveries.isEmpty)

        model.serverChanged()

        XCTAssertNil(model.selected)
        XCTAssertNil(model.episodes)
        XCTAssertEqual([], model.discoveries)
        XCTAssertNil(model.discoveryError)
        XCTAssertNil(model.subscribeError)
        XCTAssertNil(model.confirmingUnsubscribe)
        XCTAssertFalse(model.subscribeSheetOpen)
        XCTAssertFalse(model.polling)
        XCTAssertFalse(model.subscribing)
        XCTAssertNil(model.toast)
    }
}
