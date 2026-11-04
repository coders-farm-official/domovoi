import XCTest
@testable import DomovoiKit

/// The room cards' controls and the two library-wide jobs: what each one
/// sends, what it says afterwards, and which of them this model refuses to
/// send at all.
///
/// `MusicScreen.kt`'s `roomCtl`, `onPlayRandom`, `onFavoriteNp`, `onRescan`
/// and `onEnrich` are closures inside a composable, so none of them is
/// specified over there. Two things here are not ports but corrections, and
/// each has its own test: a room the server never reported is **refused**
/// rather than sent, and the five transport actions are a closed set rather
/// than a string a call site passes.
@MainActor
final class MusicRoomActionsTests: XCTestCase {

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

    /// A house with two rooms, one playing, and a library of one page.
    private final class House: Sendable {
        let nowPlaying = Shared("""
        [{"room_id":"living room","state":"play","elapsed_sec":30,"favorited":false,\
        "song":{"title":"Hey Jude","artist":"The Beatles","duration_sec":431}},\
        {"room_id":"garage","state":"stop"}]
        """)
        let favoriteReply = Shared(#"{"kind":"library","already_favorited":false,"title":"Hey Jude"}"#)
        let enrichReply = Shared("{}")
        let writeStatus = Shared(200)

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                switch parts.path {
                case "/api/health":
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/music/library":
                    return .json(#"{"items":[{"id":1,"title":"a"}],"total":1}"#)
                case "/api/music/library/stats":
                    return .json(#"{"total_tracks":1,"enriched_count":0}"#)
                case "/api/music/now-playing":
                    return .json(self.nowPlaying.current)
                case "/api/music/library/enrich":
                    return .json(self.enrichReply.current)
                default:
                    guard self.writeStatus.current == 200 else {
                        return .json(#"{"detail":"that room is not listening"}"#,
                                     status: self.writeStatus.current)
                    }
                    if parts.path.hasSuffix("/favorite") {
                        return .json(self.favoriteReply.current)
                    }
                    return .json("{}")
                }
            }
        }
    }

    private func rig(house: House = House()) -> (MusicModel, House) {
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
        // The waits are a park: nothing here is about the debounce or the
        // tick, and a real `Task.sleep(250)` in every test of a button is a
        // suite that takes longer than it measures.
        let model = MusicModel(
            container: container,
            clock: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3_600)) }
        )
        return (model, house)
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
        XCTAssertNil(model.nowPlaying.error, "the rooms read failed", file: file, line: line)
    }

    /// The POSTs to one path, **as `URL.path` spells it** — which is
    /// percent-DECODED. A recorded request for
    /// `/api/music/pause/living%20room` has the path
    /// `/api/music/pause/living room`, so matching the escaped spelling here
    /// found nothing and left five tests waiting for posts that had already
    /// gone out. ``urls(to:)`` is the one that sees the wire.
    private func posts(to path: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == path && $0.httpMethod == "POST" }
    }

    /// The addresses, exactly as they went out, escaping and all.
    private func urls(containing text: String) -> [String] {
        StubURLProtocol.recorded.compactMap { $0.url?.absoluteString }
            .filter { $0.contains(text) }
    }

    /// The body of the FIRST of `sent`, or a clean failure when nothing was
    /// sent.
    ///
    /// `sent[0]` traps on an empty list, and a trap takes the rest of the
    /// xctest process with it — which is how this batch's negative control
    /// lost eighteen sabotages behind one broken guard. Asserting the count
    /// first would do as well; this makes it impossible to forget.
    private func firstBody(
        of sent: [URLRequest],
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws -> String {
        let one = try XCTUnwrap(sent.first, "nothing was sent", file: file, line: line)
        guard let data = one.httpBody else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    private func nowPlayingReads() -> Int {
        StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/now-playing" }.count
    }

    // MARK: - the transport

    /// Each of the five addresses its own path, and each one re-reads the
    /// rooms afterwards: MPD answers the POST before its own state has
    /// settled, so the card moves on the answer to the refresh.
    func testEachTransportActionPostsItsOwnPathAndThenReReadsTheRooms() async {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }

        // **One at a time, settling between.** Five refreshes fired in one
        // tick are not five requests: `ApiRead.refresh()` cancels the read in
        // flight, so a test that waited for five would be waiting for
        // requests the model deliberately dropped — and would hang rather
        // than fail.
        for action in MusicTransportAction.allCases {
            let path = "/api/music/\(action.rawValue)/living room"
            let rooms = nowPlayingReads()
            XCTAssertTrue(model.control(action, in: "living room"), "\(action) was refused")
            await settleUntil({ self.posts(to: path).count == 1 }, "the \(action) post")
            await settleUntil({ self.nowPlayingReads() > rooms }, "the \(action) refresh")
        }

        // And the space in the room's name went out escaped, which is the
        // thing the paths are built by ``MusicQueuePath`` for.
        XCTAssertEqual(
            MusicTransportAction.allCases.map { "\(server)/api/music/\($0.rawValue)/living%20room" },
            MusicTransportAction.allCases.compactMap { urls(containing: "/\($0.rawValue)/").first }
        )
        XCTAssertNil(model.toast, "nothing to say when every one of them worked")
    }

    /// A room the server never reported is **refused here**, not sent. The
    /// cards are drawn from the now-playing answer, so a room this model has
    /// not seen cannot have a card — and a method that sent one anyway would
    /// make that check a courtesy.
    func testARoomTheServerNeverReportedIsRefused() async {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }

        XCTAssertFalse(model.control(.pause, in: "kitchen"))
        XCTAssertFalse(model.playSomething(in: "kitchen"))
        XCTAssertFalse(model.favoriteNowPlaying(in: "kitchen"))
        for _ in 0..<64 { await Task.yield() }

        XCTAssertTrue(
            StubURLProtocol.recorded.allSatisfy { $0.url?.path.contains("kitchen") != true },
            "nothing should have been sent to a room that is not there"
        )
        XCTAssertNil(model.toast)
    }

    /// The pause/resume button is one control with two states, and the state
    /// is read off the same property that chooses the icon — so the picture
    /// and the request cannot disagree.
    func testTheOneButtonPausesAPlayingRoomAndResumesAStoppedOne() async throws {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }

        let playing = try XCTUnwrap(model.rooms.first { $0.roomID == "living room" })
        let idle = try XCTUnwrap(model.rooms.first { $0.roomID == "garage" })
        XCTAssertTrue(playing.isPlaying)
        XCTAssertFalse(idle.isPlaying)
        XCTAssertTrue(model.togglePlayback(playing))
        XCTAssertTrue(model.togglePlayback(idle))
        await settleUntil(
            { self.posts(to: "/api/music/pause/living room").count == 1
                && self.posts(to: "/api/music/resume/garage").count == 1 },
            "the two posts"
        )
    }

    /// A failed action names **which** action failed, with the server's own
    /// sentence after it.
    func testAFailedTransportActionNamesItself() async throws {
        let (model, house) = rig()
        await started(model)
        defer { model.stop() }
        house.writeStatus.set(503)

        model.control(.skip, in: "living room")
        await settleUntil({ model.toast != nil }, "the failed skip")

        let said = try XCTUnwrap(model.takeToast())
        XCTAssertTrue(said.hasPrefix("skip failed:"), said)
        XCTAssertTrue(said.contains("that room is not listening"), said)
    }

    // MARK: - play something

    /// The body carries the room and the **server's** search term: the house
    /// decides what random means, so a plugin can change that answer without
    /// this app shipping again.
    func testPlaySomethingAsksTheServerWhatRandomMeans() async throws {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }

        XCTAssertTrue(model.playSomething(in: "garage"))
        // The toast goes up at once — "requested", which is what has
        // happened — and is not a claim that anything is playing yet.
        XCTAssertEqual("shuffle requested in garage…", model.takeToast())
        await settleUntil({ self.posts(to: "/api/music/play").count == 1 }, "the play")

        XCTAssertEqual(
            #"{"room_id":"garage","query":"something random"}"#,
            try firstBody(of: posts(to: "/api/music/play"))
        )
    }

    /// A room name with a quote in it is escaped in the **body**, not just in
    /// a path: `"Kids' room"` is one of the two names a real house produces.
    func testARoomNameWithAQuoteDoesNotBreakTheBody() async throws {
        let (model, house) = rig()
        house.nowPlaying.set(#"[{"room_id":"Kids\" room","state":"stop"}]"#)
        await started(model)
        defer { model.stop() }

        XCTAssertEqual([#"Kids" room"#], model.castRooms)
        XCTAssertTrue(model.playSomething(in: #"Kids" room"#))
        await settleUntil({ self.posts(to: "/api/music/play").count == 1 }, "the play")

        let sent = try firstBody(of: posts(to: "/api/music/play"))
        XCTAssertEqual(#"{"room_id":"Kids\" room","query":"something random"}"#, sent)
        // And it is still JSON, which is the thing the escaping is for.
        XCTAssertNoThrow(
            try JSONSerialization.jsonObject(with: Data(sent.utf8))
        )
    }

    // MARK: - favouriting what is playing

    /// **The server's sentence wins.** The `kind` vocabulary is open — a
    /// plugin reports its own slug — so a client that composed the toast
    /// itself would be naming somebody else's thing.
    func testTheServersOwnSentenceIsThePreferredToast() async {
        let (model, house) = rig()
        house.favoriteReply.set(
            #"{"kind":"podcast","already_favorited":false,"title":"Ep 12","message":"saved Ep 12 to your podcasts"}"#
        )
        await started(model)
        defer { model.stop() }

        XCTAssertTrue(model.favoriteNowPlaying(in: "living room"))
        await settleUntil({ model.toast != nil }, "the favourite")

        XCTAssertEqual("saved Ep 12 to your podcasts", model.takeToast())
        XCTAssertEqual(1, posts(to: "/api/music/now-playing/living room/favorite").count)
        XCTAssertEqual(
            ["\(server)/api/music/now-playing/living%20room/favorite"],
            urls(containing: "/favorite")
        )
    }

    /// With no sentence from the server, the generic one — and `radio` and an
    /// already-favourited answer each get their own.
    func testWithoutAServerSentenceTheToastIsComposedGenerically() async {
        let (model, house) = rig()
        await started(model)
        defer { model.stop() }

        XCTAssertTrue(model.favoriteNowPlaying(in: "living room"))
        await settleUntil({ model.toast != nil }, "the favourite")
        XCTAssertEqual("favorited Hey Jude", model.takeToast())

        house.favoriteReply.set(#"{"kind":"radio","already_favorited":true,"title":"BBC 6"}"#)
        XCTAssertTrue(model.favoriteNowPlaying(in: "living room"))
        await settleUntil({ model.toast != nil }, "the second favourite")
        XCTAssertEqual("BBC 6 is already a favorite station", model.takeToast())
    }

    /// A newly favourited **library** track reloads the list, because a row
    /// already on screen now has a star it did not have.
    func testANewLibraryFavouriteReloadsTheList() async {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }
        let before = StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/library" }.count

        model.favoriteNowPlaying(in: "living room")
        await settleUntil(
            { StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/library" }.count > before },
            "the reload"
        )
    }

    /// Anything else does not: nothing on screen changed, and a reload
    /// re-fetches every page the person has scrolled through.
    func testAFavouriteThatIsNotALibraryTrackDoesNotReloadTheList() async {
        let (model, house) = rig()
        house.favoriteReply.set(#"{"kind":"radio","already_favorited":false,"title":"BBC 6"}"#)
        await started(model)
        defer { model.stop() }
        let before = StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/library" }.count

        model.favoriteNowPlaying(in: "living room")
        await settleUntil({ model.toast != nil }, "the favourite")
        for _ in 0..<64 { await Task.yield() }

        XCTAssertEqual(
            before,
            StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/library" }.count
        )
    }

    /// And one that was **already** a favourite does not reload either: the
    /// star on screen is already right.
    func testAnAlreadyFavouritedLibraryTrackDoesNotReloadTheList() async {
        let (model, house) = rig()
        house.favoriteReply.set(#"{"kind":"library","already_favorited":true,"title":"Hey Jude"}"#)
        await started(model)
        defer { model.stop() }
        let before = StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/library" }.count

        model.favoriteNowPlaying(in: "living room")
        await settleUntil({ model.toast != nil }, "the favourite")
        for _ in 0..<64 { await Task.yield() }

        XCTAssertEqual("Hey Jude is already favorited", model.takeToast())
        XCTAssertEqual(
            before,
            StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/library" }.count
        )
    }

    // MARK: - the two library-wide jobs

    func testRescanPostsTheReindexAndSaysItStarted() async {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }

        model.rescanLibrary()
        await settleUntil({ model.toast != nil }, "the rescan")

        XCTAssertEqual("rescan started", model.takeToast())
        XCTAssertEqual(1, posts(to: "/api/music/library/reindex").count)
    }

    /// The toast says it started **after** the server said so, not before:
    /// a rescan that was refused must not have claimed to start.
    func testAFailedRescanSaysSoInsteadOfSayingItStarted() async throws {
        let (model, house) = rig()
        await started(model)
        defer { model.stop() }
        house.writeStatus.set(409)

        model.rescanLibrary()
        await settleUntil({ model.toast != nil }, "the failed rescan")

        let said = try XCTUnwrap(model.takeToast())
        XCTAssertTrue(said.hasPrefix("rescan failed:"), said)
        XCTAssertFalse(said.contains("started"), said)
    }

    /// Enrich reports **the server's answer**. The core starts a recognition
    /// sweep only when it can, and otherwise says why.
    func testEnrichSaysWhatTheCoreAnswered() async {
        let (model, house) = rig()
        await started(model)
        defer { model.stop() }

        house.enrichReply.set(#"{"queued":true}"#)
        model.enrichLibrary()
        await settleUntil({ model.toast != nil }, "the enrich")
        XCTAssertEqual("enrich started", model.takeToast())

        house.enrichReply.set(#"{"queued":false,"reason":"no_provider"}"#)
        model.enrichLibrary()
        await settleUntil({ model.toast != nil }, "the refused enrich")
        XCTAssertEqual(
            "song recognition needs a free AcoustID key or the Shazam add-on",
            model.takeToast()
        )
        XCTAssertEqual(2, posts(to: "/api/music/library/enrich").count)
    }

    /// An answer this app cannot decode at all is a failure, not a silent
    /// "started": the sweep's own refusal and a broken answer are different
    /// things and a person acting on them does different things.
    func testAnUnreadableEnrichAnswerIsAFailureAndNotAStart() async throws {
        let (model, house) = rig()
        await started(model)
        defer { model.stop() }
        house.enrichReply.set("not json at all")

        model.enrichLibrary()
        await settleUntil({ model.toast != nil }, "the broken enrich")

        let said = try XCTUnwrap(model.takeToast())
        XCTAssertTrue(said.hasPrefix("enrich failed:"), said)
    }

    // MARK: - deleting a track

    private func deletes() -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.httpMethod == "DELETE" }
    }

    /// The library-only delete carries no `also_file`, takes the row out of
    /// the list **in place**, and re-reads the stats — the header's total and
    /// every tile on the stats tab just changed.
    func testRemovingATrackFromTheLibraryKeepsTheFileAndPatchesTheList() async throws {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }
        XCTAssertEqual(1, model.library.state.items.count)
        let statsBefore = StubURLProtocol.recorded
            .filter { $0.url?.path == "/api/music/library/stats" }.count

        let track = try XCTUnwrap(model.library.state.items.first)
        model.deleteTrack(track, alsoFile: false)
        await settleUntil({ model.toast != nil }, "the delete")

        XCTAssertEqual(
            #"removed "a" from the library (file kept; a rescan will re-add it)"#,
            model.takeToast()
        )
        XCTAssertEqual(["/api/music/library/1"], deletes().compactMap { $0.url?.path })
        XCTAssertNil(deletes().first?.url?.query, "no also_file on a library-only delete")
        XCTAssertTrue(model.library.state.items.isEmpty, "the row left without a reload")
        await settleUntil(
            { StubURLProtocol.recorded
                .filter { $0.url?.path == "/api/music/library/stats" }.count > statsBefore },
            "the stats re-read"
        )
    }

    func testDeletingTheFileSaysSoAndAsksForIt() async throws {
        let (model, _) = rig()
        await started(model)
        defer { model.stop() }

        let track = try XCTUnwrap(model.library.state.items.first)
        model.deleteTrack(track, alsoFile: true)
        await settleUntil({ model.toast != nil }, "the delete")

        XCTAssertEqual(#"deleted "a" · file removed from disk"#, model.takeToast())
        XCTAssertEqual("also_file=true", deletes().first?.url?.query)
    }

    /// A refused delete leaves the row where it is: the list must not claim
    /// an edit the server did not make.
    func testAFailedDeleteLeavesTheRowOnScreen() async throws {
        let (model, house) = rig()
        await started(model)
        defer { model.stop() }
        house.writeStatus.set(403)

        let track = try XCTUnwrap(model.library.state.items.first)
        model.deleteTrack(track, alsoFile: true)
        await settleUntil({ model.toast != nil }, "the failed delete")

        let said = try XCTUnwrap(model.takeToast())
        XCTAssertTrue(said.hasPrefix("delete failed:"), said)
        XCTAssertEqual(1, model.library.state.items.count, "the row stayed")
    }
}
