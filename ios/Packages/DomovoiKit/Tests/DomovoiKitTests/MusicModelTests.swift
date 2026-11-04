import XCTest
@testable import DomovoiKit

/// Music's data layer against a server that is not a server: what is asked
/// for, when a query change becomes a request, what a tap sends, and what it
/// refuses to send.
///
/// `MusicScreen.kt` has no test — Android's JVM suite is unit-only and the
/// screen is a composable — so the debounce, the reset-on-filter-change and
/// the edit-in-place are specified by nothing over there. The point of this
/// file is that they are specified here.
@MainActor
final class MusicModelTests: XCTestCase {

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

    /// Every wait the model asks for, and a budget of the ones that come back
    /// at once.
    ///
    /// `HomeModelTests.Waits` with a budget added, because this model has two
    /// loops of different periods — the search debounce and the elapsed tick
    /// — and a test needs to let **one** turn of one of them through. A wait
    /// with no budget left parks until its task is cancelled, which is what
    /// lets a test say "the loop asked for this period and then nothing else
    /// happened".
    private final class Waits: Sendable {
        let requested = Shared<[Int64]>([])
        private let budget = Shared<[Int64: Int]>([:])
        private let opened = Shared<Set<Int64>>([])

        /// Let `times` further waits of `ms` return at once — a **counted**
        /// allowance, for a loop whose every turn a test wants to see.
        ///
        /// Only safe where nothing cancels the waiter: a cancelled task that
        /// wakes up and takes the allowance before noticing it is cancelled
        /// spends an allowance nobody receives, which is how this rig made
        /// the debounce test flaky. Use ``open(_:)`` wherever a wait may be
        /// cancelled, which is every debounce.
        func allow(_ ms: Int64, times: Int = 1) {
            budget.mutate { $0[ms, default: 0] += times }
        }

        /// Let every wait of `ms` return at once, from now on.
        func open(_ ms: Int64) {
            opened.mutate { $0.insert(ms) }
        }

        func asked(_ ms: Int64) -> Int {
            requested.current.filter { $0 == ms }.count
        }

        /// **Polls the budget rather than deciding once.**
        ///
        /// `HomeModelTests.Waits` parks on an hour-long sleep, which works
        /// there because every wait it lets through is allowed *before* the
        /// loop asks. Music's tick asks first and is allowed later — the test
        /// has to see that nothing counted while nothing was playing — and a
        /// task already inside `Task.sleep(3_600)` cannot be woken by a
        /// budget granted afterwards. So this waits by polling.
        ///
        /// Bounded, like every wait in this suite, and the fall-back at the
        /// end is a park rather than a return: a loop that was never allowed
        /// through must not quietly turn into a hot loop that makes the test
        /// pass for the wrong reason.
        var sleeper: @Sendable (Int64) async throws -> Void {
            { [requested, budget, opened] ms in
                requested.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    // Cancellation is checked **before** the budget, not
                    // after. The other way round, a debounce the next
                    // keystroke had already cancelled woke up, took the
                    // allowance meant for its successor and then dropped it
                    // on its own `isCancelled` guard — so the successor
                    // waited for an allowance that had been spent by a task
                    // whose answer nobody wanted. That is the test rig
                    // committing the bug the thing under test is about.
                    try Task.checkCancellation()
                    if opened.current.contains(ms) { return }
                    let through = budget.mutate { left -> Bool in
                        guard let have = left[ms], have > 0 else { return false }
                        left[ms] = have - 1
                        return true
                    }
                    if through { return }
                    await Task.yield()
                }
                try await Task.sleep(for: .seconds(3_600))
            }
        }
    }

    /// A house with a library in it, answering the four paths Music reads and
    /// the two it writes.
    ///
    /// Each piece is a ``Shared`` so a test can change what the house says
    /// **between** requests — a track favourited, a room that started
    /// playing, a library that went away.
    private final class House: Sendable {
        let total = Shared(120)
        let nowPlaying = Shared("[]")
        let stats = Shared(#"{"total_tracks":5232,"enriched_count":10}"#)
        let libraryStatus = Shared(200)
        let writeStatus = Shared(200)
        let favourited = Shared<Set<Int64>>([])

        /// The rows this house would answer for a window of the library.
        ///
        /// Ids below 50 are stamped `spotify` and the rest `jellyfin`, so
        /// that the source filter has something to accumulate **across**
        /// pages rather than all on page one.
        private func rows(offset: Int, limit: Int, narrowed: Bool) -> String {
            let count = narrowed ? 0 : total.current
            let from = min(offset, count)
            let to = min(from + limit, count)
            guard from < to else { return "" }
            let favourites = favourited.current
            return (from..<to).map { index -> String in
                let id = Int64(index)
                let source = index < 50 ? "spotify" : "jellyfin"
                return """
                {"id":\(id),"title":"track \(id)","artist":"artist \(id % 7)",\
                "duration_sec":200,"source":"\(source)","added_via":"manual",\
                "file_path":"/srv/music/track \(id).mp3",\
                "favorited":\(favourites.contains(id) ? "true" : "false")}
                """
            }.joined(separator: ",")
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                let (total, nowPlaying, stats) = (self.total, self.nowPlaying, self.stats)
                let (libraryStatus, writeStatus) = (self.libraryStatus, self.writeStatus)
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? {
                    query.first { $0.name == name }?.value
                }

                switch parts.path {
                case "/api/health":
                    // The identity gate's probe. A trusted server has to
                    // prove itself before any read goes out, so a house that
                    // did not answer this would refuse every request below
                    // and every test over it would assert about nothing.
                    let challenge = value("challenge")
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/music/library":
                    guard libraryStatus.current == 200 else {
                        return .json(#"{"detail":"the library is away"}"#, status: libraryStatus.current)
                    }
                    let offset = Int(value("offset") ?? "0") ?? 0
                    let limit = Int(value("limit") ?? "50") ?? 50
                    let narrowed = (value("q")?.isEmpty == false)
                    let body = self.rows(offset: offset, limit: limit, narrowed: narrowed)
                    let count = narrowed ? 0 : total.current
                    return .json(#"{"items":[\#(body)],"total":\#(count)}"#)
                case "/api/music/library/stats":
                    return .json(stats.current)
                case "/api/music/now-playing":
                    return .json(nowPlaying.current)
                case "/api/music/play-track":
                    guard writeStatus.current == 200 else {
                        return .json(#"{"detail":"that room is not listening"}"#, status: writeStatus.current)
                    }
                    return .json("{}")
                default:
                    guard parts.path.hasPrefix("/api/music/library/") else {
                        return .json("{}", status: 404)
                    }
                    guard writeStatus.current == 200 else {
                        return .json(#"{"detail":"the library is read-only"}"#, status: writeStatus.current)
                    }
                    return .json("{}")
                }
            }
        }
    }

    private func rig(
        house: House = House(),
        waits: Waits = Waits()
    ) -> (MusicModel, AppContainer, House, Waits) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        // Trusted **with its pin**, which is what a paired phone holds. An
        // address trusted with no pin makes the gate probe `/api/health` and
        // refuse until it is satisfied, and `setServerURL` refuses an address
        // that is not trusted at all — so both halves are needed to be able
        // to move house in a test.
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = MusicModel(container: container, clock: { 0 }, wait: waits.sleeper)
        return (model, container, house, waits)
    }

    /// Two rooms, one of them playing.
    private let twoRooms = """
    [{"room_id":"living room","state":"play","elapsed_sec":30,\
    "song":{"title":"Hey Jude","artist":"The Beatles","duration_sec":431}},\
    {"room_id":"garage","state":"stop"}]
    """

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

    /// Every library request's query string, in order.
    private func libraryQueries() -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                parts.path == "/api/music/library"
            else { return nil }
            return parts.query ?? ""
        }
    }

    private func requests(to path: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == path }
    }

    private func body(of request: URLRequest) -> String {
        guard let data = request.httpBody else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    /// Start the model and wait for all three reads to answer.
    ///
    /// **It asserts none of them failed**, because `answered` is true of a
    /// failure too: a rig whose server refused every request would otherwise
    /// let every test that uses it assert over nils and empty lists, which is
    /// a suite of tests that cannot fail.
    private func started(
        _ model: MusicModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        model.start()
        await settleUntil(
            { model.library.state.loaded && model.stats.answered && model.nowPlaying.answered },
            "the three reads",
            file: file,
            line: line
        )
        XCTAssertNil(model.library.state.error, "the library read failed", file: file, line: line)
        XCTAssertNil(model.stats.error, "the stats read failed", file: file, line: line)
        XCTAssertNil(model.nowPlaying.error, "the now-playing read failed", file: file, line: line)
    }

    // MARK: - what is asked for

    /// The library is asked for with the sort and the paging, and nothing
    /// else; the stats and now-playing reads go out beside it.
    func testStartingReadsTheLibraryTheStatsAndTheRooms() async {
        let (model, _, house, _) = rig()
        house.nowPlaying.set(twoRooms)

        await started(model)
        defer { model.stop() }

        XCTAssertEqual(["sort=added_desc&limit=50"], libraryQueries())
        XCTAssertEqual(50, model.library.state.items.count)
        XCTAssertEqual(120, model.library.state.total)
        XCTAssertEqual(5232, model.libraryTotal)
        XCTAssertEqual(["living room", "garage"], model.castRooms)
        XCTAssertEqual(1, model.playingCount)
        XCTAssertEqual("5232 tracks · 1 room playing", model.header)
    }

    /// The page the person is looking at is asked for with its offset, and
    /// the count line says what is loaded against the whole list.
    func testScrollingAsksForTheNextPageAndTheCountLineFollows() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }

        XCTAssertEqual("showing 50 of 120 tracks", model.countText)

        model.library.loadMore()
        await model.library.idle()

        XCTAssertEqual(
            ["sort=added_desc&limit=50", "sort=added_desc&limit=50&offset=50"],
            libraryQueries()
        )
        XCTAssertEqual("showing 100 of 120 tracks", model.countText)
    }

    /// Nothing is said about a list that has not answered.
    ///
    /// The count line and the empty note are both nil before the first page
    /// lands, because "no tracks here" over a library that is still loading
    /// is the same wrong claim as the one a first install was told about its
    /// server.
    func testNothingIsClaimedAboutAListThatHasNotAnsweredYet() async {
        let (model, _, _, _) = rig()

        XCTAssertNil(model.countText)
        XCTAssertNil(model.emptyNote)

        await started(model)
        defer { model.stop() }

        XCTAssertNotNil(model.countText)
        XCTAssertNil(model.emptyNote, "fifty tracks landed")
    }

    /// An empty answer to a search says so as a search, and an empty answer
    /// to an open library says so as a library.
    func testTheEmptyNoteIsAboutWhateverTheListWasAskedFor() async {
        let (model, _, house, waits) = rig()
        await started(model)
        defer { model.stop() }

        waits.open(MusicModel.searchDebounceMs)
        model.query.search = "nothing matches this"
        await settleUntil { model.loadedQuery.search == "nothing matches this" }
        await model.library.idle()
        XCTAssertEqual(MusicStrings.noTracksForSearch, model.emptyNote)

        house.total.set(0)
        house.stats.set(#"{"total_tracks":0}"#)
        model.query.search = ""
        model.query.sort = .title
        await model.library.idle()
        model.stats.refresh()
        await settleUntil { model.libraryTotal == 0 && model.library.state.items.isEmpty }
        XCTAssertEqual(MusicStrings.libraryEmpty, model.emptyNote)
    }

    /// A failed read is not an empty list: the note stays away and the
    /// failure is the pager's to show.
    func testAFailedReadIsNotAnEmptyLibrary() async {
        let (model, _, house, _) = rig()
        house.libraryStatus.set(500)

        model.start()
        defer { model.stop() }
        await settleUntil { model.library.state.error != nil }

        XCTAssertNil(model.emptyNote, "nothing is known about the list, so nothing is said")
        XCTAssertNotNil(model.library.state.error)
    }

    // MARK: - the debounce

    /// **A search waits and a tap does not.**
    ///
    /// Android's split (`MusicScreen.kt:101`): `qDebounced` is the only value
    /// behind a `delay`, because a sort is one deliberate tap while a search
    /// is one keystroke of several. Debouncing the tap would make the screen
    /// feel broken; not debouncing the search sends a request per keystroke.
    func testASearchWaitsAndASortDoesNot() async {
        let (model, _, _, waits) = rig()
        await started(model)
        defer { model.stop() }
        XCTAssertEqual(1, libraryQueries().count)

        model.query.search = "beat"
        await settleUntil { waits.asked(MusicModel.searchDebounceMs) == 1 }
        XCTAssertEqual(1, libraryQueries().count, "the keystroke has not been sent")
        XCTAssertEqual("", model.loadedQuery.search, "and the list is still the old one")

        model.query.sort = .title
        await settleUntil { model.loadedQuery.sort == .title }
        await model.library.idle()
        XCTAssertEqual(2, libraryQueries().count, "the tap went straight out")
    }

    /// Three keystrokes are one request, and it carries the last of them.
    ///
    /// Each keystroke cancels the wait the one before it started, which is
    /// what makes a debounce a debounce rather than a queue of requests that
    /// arrive late.
    func testTypingSendsOneRequestWhenItSettles() async {
        let (model, _, _, waits) = rig()
        await started(model)
        defer { model.stop() }

        model.query.search = "b"
        await settleUntil { waits.asked(MusicModel.searchDebounceMs) == 1 }
        model.query.search = "be"
        await settleUntil { waits.asked(MusicModel.searchDebounceMs) == 2 }
        XCTAssertEqual(1, libraryQueries().count, "two keystrokes and no request")

        waits.open(MusicModel.searchDebounceMs)
        model.query.search = "bea"
        await settleUntil({ model.loadedQuery.search == "bea" }, "the settled search")
        await model.library.idle()
        XCTAssertEqual("bea", model.loadedQuery.search)

        XCTAssertEqual(
            ["sort=added_desc&limit=50", "sort=added_desc&q=bea&limit=50"],
            libraryQueries(),
            "one request, for the term that settled"
        )
    }

    /// A new query is a new list from page one, and the page the person had
    /// scrolled to is not asked for again.
    func testANewQueryStartsAtPageOne() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }
        model.library.loadMore()
        await model.library.idle()
        XCTAssertEqual(100, model.library.state.items.count)

        model.query.favoritedOnly = true
        await settleUntil { model.loadedQuery.favoritedOnly }
        await model.library.idle()

        XCTAssertEqual(
            "sort=added_desc&favorited=true&limit=50",
            libraryQueries().last,
            "page one of the new list"
        )
        XCTAssertLessThanOrEqual(model.library.state.items.count, 50)
    }

    /// Setting the query to what it already is sends nothing.
    func testAQueryThatDidNotChangeSendsNothing() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }

        model.query = MusicLibraryQuery()
        model.query.sort = .newest
        await Task.yield()

        XCTAssertEqual(1, libraryQueries().count)
    }

    // MARK: - the source filter's open vocabulary

    /// The source options grow as pages land, and the growing is the pager's
    /// own success path rather than a line in a view.
    ///
    /// `onLoaded` exists for this one consumer; a view that had to call it
    /// after every `loadMore`, `reload` and `reset` is how ``TokenVault``
    /// came to have no caller at all.
    func testTheSourceOptionsGrowAsPagesLand() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }

        XCTAssertEqual(["all", "spotify"], model.sourceOptions)

        model.library.loadMore()
        await model.library.idle()

        XCTAssertEqual(["all", "jellyfin", "spotify"], model.sourceOptions)
    }

    /// Switching houses forgets the slugs the old one's library had: they are
    /// not options in a house that has never reported them.
    func testSwitchingHousesForgetsTheSourcesSeen() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }
        XCTAssertEqual(["all", "spotify"], model.sourceOptions)

        model.serverChanged()

        XCTAssertEqual(["all"], model.sourceOptions)
    }

    // MARK: - the two things a tap can do

    /// Favouriting sends the patch and changes the loaded row **in place** —
    /// no re-read of the pages scrolled through.
    func testFavouritingPatchesTheRowAndReReadsNoPages() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }
        let before = libraryQueries().count
        guard let track = model.library.state.items.first(where: { $0.id == 3 }) else {
            return XCTFail("the library answered no row 3")
        }
        XCTAssertFalse(track.favorited)

        model.toggleFavorite(track)
        await settleUntil { model.library.state.items.first { $0.id == 3 }?.favorited == true }

        let patches = requests(to: "/api/music/library/3")
        XCTAssertEqual(1, patches.count)
        XCTAssertEqual("PATCH", patches.first?.httpMethod)
        XCTAssertEqual(#"{"favorited":true}"#, body(of: patches[0]))
        XCTAssertEqual(before, libraryQueries().count, "no page was asked for again")
        XCTAssertEqual(50, model.library.state.items.count)
    }

    /// **Un-favouriting under "favourites only" takes the row out of the
    /// list**, because it no longer matches what the person asked to see —
    /// and the total comes down with it.
    func testUnfavouritingUnderFavouritesOnlyTakesTheRowOut() async {
        let (model, _, house, _) = rig()
        house.favourited.set([0, 1, 2])
        await started(model)
        defer { model.stop() }

        model.query.favoritedOnly = true
        await settleUntil { model.loadedQuery.favoritedOnly }
        await model.library.idle()
        let shown = model.library.state.items.count
        guard let track = model.library.state.items.first(where: { $0.favorited }) else {
            return XCTFail("no favourited row to un-favourite")
        }

        model.toggleFavorite(track)
        await settleUntil { model.library.state.items.count == shown - 1 }

        XCTAssertEqual(#"{"favorited":false}"#, body(of: requests(to: "/api/music/library/\(track.id)")[0]))
        XCTAssertNil(model.library.state.items.first { $0.id == track.id })
    }

    /// Un-favouriting with the switch off leaves the row where it is, with
    /// its flag moved.
    func testUnfavouritingWithTheSwitchOffLeavesTheRowThere() async {
        let (model, _, house, _) = rig()
        house.favourited.set([3])
        await started(model)
        defer { model.stop() }
        guard let track = model.library.state.items.first(where: { $0.id == 3 }) else {
            return XCTFail("the library answered no row 3")
        }
        XCTAssertTrue(track.favorited)

        model.toggleFavorite(track)
        await settleUntil { model.library.state.items.first { $0.id == 3 }?.favorited == false }

        XCTAssertEqual(50, model.library.state.items.count)
    }

    /// A favourite the server refused says so and **leaves the row as it
    /// was**: nothing is drawn as though it worked.
    func testAFailedFavouriteSaysSoAndMovesNothing() async {
        let (model, _, house, _) = rig()
        await started(model)
        defer { model.stop() }
        house.writeStatus.set(500)
        guard let track = model.library.state.items.first(where: { $0.id == 3 }) else {
            return XCTFail("the library answered no row 3")
        }

        model.toggleFavorite(track)
        await settleUntil { model.toast != nil }

        XCTAssertTrue(
            model.toast?.hasPrefix("favorite failed:") == true,
            "the toast says the server's own reason: \(model.toast ?? "nil")"
        )
        XCTAssertEqual(false, model.library.state.items.first { $0.id == 3 }?.favorited)
        XCTAssertNotNil(model.takeToast(), "the screen reads it")
        XCTAssertNil(model.toast, "and it is cleared, so it is not shown twice")
    }

    /// Playing a row sends the room and the track, and says where it went.
    func testPlayingSendsTheRoomAndTheTrack() async {
        let (model, _, house, _) = rig()
        house.nowPlaying.set(twoRooms)
        await started(model)
        defer { model.stop() }
        guard let track = model.library.state.items.first(where: { $0.id == 5 }) else {
            return XCTFail("the library answered no row 5")
        }

        XCTAssertTrue(model.play(track, in: "garage"))
        await settleUntil { model.toast != nil }

        let posts = requests(to: "/api/music/play-track")
        XCTAssertEqual(1, posts.count)
        XCTAssertEqual("POST", posts.first?.httpMethod)
        XCTAssertEqual(#"{"room_id":"garage","track_id":5}"#, body(of: posts[0]))
        XCTAssertEqual(#"playing "track 5" in garage…"#, model.toast)
    }

    /// **A room the server never reported is refused here, not sent.**
    ///
    /// The picker only offers the rooms now-playing answered
    /// (``MusicPage/castRooms(_:)``); a model that would send any string it
    /// was handed makes that offer a courtesy rather than a rule. Android's
    /// made-up `"kitchen"` is exactly the string this refuses.
    func testPlayingRefusesARoomTheServerNeverReported() async {
        let (model, _, house, _) = rig()
        house.nowPlaying.set(twoRooms)
        await started(model)
        defer { model.stop() }
        guard let track = model.library.state.items.first else {
            return XCTFail("the library answered nothing")
        }

        XCTAssertFalse(model.play(track, in: "kitchen"))
        XCTAssertFalse(model.play(track, in: ""))
        await Task.yield()

        XCTAssertEqual(0, requests(to: "/api/music/play-track").count, "nothing was sent")
        XCTAssertNil(model.toast, "and nothing was claimed")
    }

    /// With no room reported at all, there is nothing to play into — which is
    /// the state a house whose satellites are all offline is in.
    func testWithNoRoomsReportedThereIsNothingToPlayInto() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }
        guard let track = model.library.state.items.first else {
            return XCTFail("the library answered nothing")
        }

        XCTAssertEqual([], model.castRooms)
        XCTAssertNil(MusicPage.defaultRoom(model.rooms))
        XCTAssertFalse(model.play(track, in: "living room"))
    }

    /// A play the server refused says the server's reason.
    func testAFailedPlaySaysWhyItFailed() async {
        let (model, _, house, _) = rig()
        house.nowPlaying.set(twoRooms)
        await started(model)
        defer { model.stop() }
        house.writeStatus.set(409)
        guard let track = model.library.state.items.first else {
            return XCTFail("the library answered nothing")
        }

        XCTAssertTrue(model.play(track, in: "garage"))
        await settleUntil { model.toast != nil }

        XCTAssertTrue(
            model.toast?.hasPrefix("play failed:") == true,
            "got: \(model.toast ?? "nil")"
        )
    }

    // MARK: - the scroll trigger

    /// **A screen showing a spinner is not near its end.**
    ///
    /// Through ``Paging/nearEnd(totalItems:lastVisible:threshold:)`` and its
    /// guard, never a bare `onAppear` on the last row — which is the iOS
    /// shape of the trap that had Music asking for page two before anyone had
    /// scrolled (seen on Android 2026-10-12).
    func testTheScrollTriggerNeedsAListToBeScrolling() async {
        let (model, _, house, _) = rig()
        house.total.set(4)
        await started(model)
        defer { model.stop() }
        XCTAssertEqual(4, model.library.state.items.count)

        XCTAssertFalse(model.shouldLoadMore(lastVisible: 3), "four rows is not a scrolled list")
    }

    /// A list that is being scrolled asks, and only near its end.
    func testALongListAsksNearItsEndAndNotAtItsTop() async {
        let (model, _, _, _) = rig()
        await started(model)
        defer { model.stop() }

        XCTAssertFalse(model.shouldLoadMore(lastVisible: 2))
        XCTAssertTrue(model.shouldLoadMore(lastVisible: 45))
    }

    /// Nothing is asked for while a page is in flight, or over a failure, or
    /// once the list is whole — the guard that stops a visible last row
    /// retrying on every frame.
    func testTheScrollTriggerIsSilentWhileLoadingAndOverAFailure() async {
        let (model, _, house, _) = rig()
        await started(model)
        defer { model.stop() }

        house.libraryStatus.set(500)
        model.library.loadMore()
        await model.library.idle()
        XCTAssertNotNil(model.library.state.error)
        XCTAssertFalse(model.shouldLoadMore(lastVisible: 45), "not over a failure")

        house.libraryStatus.set(200)
        house.total.set(50)
        model.library.retry()
        await model.library.idle()
        XCTAssertFalse(model.library.state.hasMore)
        XCTAssertFalse(model.shouldLoadMore(lastVisible: 45), "that is the whole list")
    }

    // MARK: - the elapsed tick

    /// **The tick only moves while something is playing.**
    ///
    /// Android ticks unconditionally; a tick nothing reads is a wakeup a
    /// second for a screen with four idle rooms on it, and
    /// ``NowPlayingRoom/elapsed(tick:)`` ignores it for every room that is
    /// not playing anyway.
    func testTheTickOnlyMovesWhileSomethingIsPlaying() async {
        let (model, _, house, waits) = rig()
        await started(model)
        defer { model.stop() }
        XCTAssertEqual(0, model.tick)

        waits.allow(MusicModel.tickMs)
        await settleUntil { waits.asked(MusicModel.tickMs) >= 2 }
        XCTAssertEqual(0, model.tick, "nothing is playing, so there is nothing to count")

        house.nowPlaying.set(twoRooms)
        model.nowPlaying.refresh()
        await settleUntil { model.playingCount == 1 }
        waits.allow(MusicModel.tickMs)
        await settleUntil { model.tick == 1 }

        let room = model.rooms.first { $0.roomID == "living room" }
        XCTAssertEqual(31, room?.elapsed(tick: model.tick), "30 from the server, 1 from here")
    }

    /// A now-playing answer puts the tick back to zero, because the server's
    /// figure is canonical and the tick is additive on top of it.
    func testANowPlayingAnswerPutsTheTickBack() async {
        let (model, _, house, waits) = rig()
        house.nowPlaying.set(twoRooms)
        await started(model)
        defer { model.stop() }

        waits.allow(MusicModel.tickMs, times: 2)
        await settleUntil { model.tick >= 1 }

        model.nowPlaying.refresh()
        await settleUntil { model.tick == 0 }
    }

    // MARK: - the pushes, and the lifecycle

    /// Each read listens for its own pushes and ignores everything else on
    /// the socket.
    func testEachReadListensForItsOwnPushes() async {
        let (model, container, house, _) = rig()
        house.nowPlaying.set(twoRooms)
        await started(model)
        defer { model.stop() }
        let libraryReads = libraryQueries().count
        let statsReads = model.stats.fetches
        let roomReads = model.nowPlaying.fetches

        container.stateEventArrived(StateEvent(type: "timers.changed", frame: "{}"))
        await Task.yield()
        XCTAssertEqual(libraryReads, libraryQueries().count)
        XCTAssertEqual(statsReads, model.stats.fetches)
        XCTAssertEqual(roomReads, model.nowPlaying.fetches)

        container.stateEventArrived(StateEvent(type: "music.now_playing.changed", frame: "{}"))
        await settleUntil { model.nowPlaying.fetches == roomReads + 1 }
        XCTAssertEqual(statsReads, model.stats.fetches, "the rooms moved, not the library")

        container.stateEventArrived(StateEvent(type: "library.indexer.changed", frame: "{}"))
        await settleUntil { model.stats.fetches == statsReads + 1 }
        await model.library.idle()
        XCTAssertGreaterThan(libraryQueries().count, libraryReads, "and the list was re-read")
    }

    /// An indexer push re-reads **what is loaded**, keeping the list where it
    /// is: the person stays where they were scrolled to.
    func testAnIndexerPushKeepsTheListWhereItIs() async {
        let (model, container, _, _) = rig()
        await started(model)
        defer { model.stop() }
        model.library.loadMore()
        await model.library.idle()
        XCTAssertEqual(100, model.library.state.items.count)
        StubURLProtocol.reset()
        House().install()

        container.stateEventArrived(StateEvent(type: "library.indexer.changed", frame: "{}"))
        await settleUntil { !libraryQueries().isEmpty }
        await model.library.idle()

        XCTAssertEqual(["sort=added_desc&limit=100"], libraryQueries(), "all hundred, in one request")
        XCTAssertEqual(100, model.library.state.items.count)
    }

    /// The route went away: the reads, the pager, the debounce, the tick and
    /// the socket sink all stop, and coming back re-reads.
    ///
    /// "A route holds nothing" (`Pagination.swift`): a screen that kept
    /// reading after it left is four requests a second for a tab nobody is
    /// looking at.
    func testStoppingLeavesEverythingAndComingBackReReads() async {
        let (model, container, _, _) = rig()
        let before = container.stateEventSinkCount
        await started(model)
        XCTAssertEqual(before + 1, container.stateEventSinkCount)

        model.stop()

        XCTAssertEqual(before, container.stateEventSinkCount)
        let reads = libraryQueries().count
        container.stateEventArrived(StateEvent(type: "library.indexer.changed", frame: "{}"))
        await Task.yield()
        XCTAssertEqual(reads, libraryQueries().count, "a stopped screen does not read")

        model.start()
        defer { model.stop() }
        await settleUntil { model.stats.fetches >= 2 }
        XCTAssertEqual(before + 1, container.stateEventSinkCount)
    }

    /// **A screen that came back is keyed to the server again — including its
    /// list, which did not re-read.**
    ///
    /// The defect this was written for: `Pager.stop()` leaves the server
    /// registry and only `init` used to rejoin, while `start()` deliberately
    /// does *not* reset a list that is already loaded (that is the point of
    /// keeping the rows). So the one path back from a tab switch skipped the
    /// re-join, and a switch of houses left the old house's tracks on screen
    /// under the new house's name — the read-after-await class
    /// ([[read-after-await-wrong-key]]) with the key left off entirely.
    ///
    /// The assertion is about the *rows*, not the sink count, because a sink
    /// that is registered and does nothing would pass a count.
    func testAListThatCameBackIsStillKeyedToTheHouse() async {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let house = House()
        house.install()
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = MusicModel(container: container, clock: { 0 }, wait: Waits().sleeper)
        await started(model)
        XCTAssertEqual(50, model.library.state.items.count)

        // The tab goes away and comes back. The list is already loaded, so
        // nothing re-reads — which is exactly the state the bug lived in.
        model.stop()
        model.start()
        defer { model.stop() }
        await settleUntil { model.stats.fetches >= 2 }
        let reads = libraryQueries().count

        // The person switches houses. The new one has a library of three.
        let next = "http://10.0.0.77:6369"
        house.total.set(3)
        store.trustServer(next, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(next))
        container.serverAddressChanged()
        await settleUntil { libraryQueries().count > reads }
        await model.library.idle()

        XCTAssertEqual(3, model.library.state.items.count, "the new house's list, not the old one's")
    }
}
