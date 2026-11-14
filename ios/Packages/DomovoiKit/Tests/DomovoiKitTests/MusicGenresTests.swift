import XCTest
@testable import DomovoiKit

/// Music → Genres (Android 3cdd261b, batch 2 §3), a playlist's sentence on
/// the phone (§5), and the player's Lyrics · Queue · Hide tabs (§6).
///
/// The JSON below is what `web/backend/api/music_genres.py`'s response models
/// (`GenrePage`, `GenreDetail`) serialise to — the same answers Android's
/// `GenresTest.kt` was written against, dumped with `model_dump_json()` from
/// the server, not written to fit the decoder.
@MainActor
final class MusicGenresTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// `GET /api/music/genres` while the background tag read is still going.
    private let readingPage = """
    {"total":3,"items":[{"key":"hip hop","name":"Hip-Hop","track_count":12,"cover_track_id":7},\
    {"key":"rock","name":"Rock","track_count":1,"cover_track_id":null}],"limit":50,"offset":null,\
    "has_more":false,"next_cursor":null,"tags_read":1200,"tags_total":5232,"reading":true,\
    "untagged_count":40,"musicbrainz_pending":0}
    """

    /// The same once every track's tags are read, with a next page to come.
    private let donePage = """
    {"total":120,"items":[{"key":"ambient","name":"Ambient","track_count":3,"cover_track_id":11}],\
    "limit":1,"offset":null,"has_more":true,"next_cursor":"eyJrIjpbImFtYmllbnQiXX0",\
    "tags_read":5232,"tags_total":5232,"reading":false,"untagged_count":40,"musicbrainz_pending":0}
    """

    /// `readingPage` once the read is done: the whole list, nothing more to
    /// page. (`donePage` says `has_more` forever, which a pager filling a
    /// page reads to the end of — no house answers that twice.)
    private let readDonePage = """
    {"total":3,"items":[{"key":"hip hop","name":"Hip-Hop","track_count":12,"cover_track_id":7},\
    {"key":"rock","name":"Rock","track_count":1,"cover_track_id":null}],"limit":50,"offset":null,\
    "has_more":false,"next_cursor":null,"tags_read":5232,"tags_total":5232,"reading":false,\
    "untagged_count":40,"musicbrainz_pending":0}
    """

    /// `GET /api/music/genre?key=` for the no-genre bucket.
    private let noGenreDetail =
        #"{"key":"","name":"no genre","track_count":2,"track_ids":[5,9],"track_ids_truncated":false}"#

    private func answer(_ text: String) throws -> MusicGenreAnswer {
        try JSONDecoder().decode(MusicGenreAnswer.self, from: Data(text.utf8))
    }

    // MARK: - the answers

    func testTheListAndItsReadProgressDecode() throws {
        let got = try answer(readingPage)
        XCTAssertEqual(
            [MusicGenreRow(key: "hip hop", name: "Hip-Hop", trackCount: 12, coverTrackID: 7),
             MusicGenreRow(key: "rock", name: "Rock", trackCount: 1, coverTrackID: nil)],
            got.page.items
        )
        XCTAssertEqual(3, got.page.total)
        XCTAssertFalse(got.page.hasMore(loadedBefore: 0))
        XCTAssertEqual(
            MusicGenreProgress(tagsRead: 1200, tagsTotal: 5232, reading: true, untaggedCount: 40),
            got.progress
        )
    }

    func testAFinishedReadIsNotReading() throws {
        let got = try answer(donePage)
        XCTAssertTrue(got.page.hasMore(loadedBefore: 0))
        XCTAssertEqual("eyJrIjpbImFtYmllbnQiXX0", got.page.nextCursor)
        XCTAssertFalse(got.progress.reading)
        XCTAssertEqual(40, got.progress.untaggedCount)
    }

    func testOneGenreDecodes() throws {
        let got = try JSONDecoder().decode(MusicGenreDetail.self, from: Data(noGenreDetail.utf8))
        XCTAssertEqual(MusicGenreDetail(key: "", name: "no genre", trackCount: 2, trackIDs: [5, 9]), got)
    }

    // MARK: - the rules

    func testWhileReadingTheTabSaysHowFar() throws {
        XCTAssertEqual(
            "reading genres from your files… 1,200 of 5,232",
            MusicGenres.readingLine(try answer(readingPage).progress)
        )
    }

    func testOnceReadOrBeforeAnyAnswerThereIsNoReadingLine() throws {
        XCTAssertNil(MusicGenres.readingLine(try answer(donePage).progress))
        XCTAssertNil(MusicGenres.readingLine(nil))
    }

    func testNoGenreComesLastOnceTheWholeListIsIn() throws {
        let got = try answer(readingPage)
        let rows = MusicGenres.listRows(
            got.page.items, progress: got.progress, search: "",
            hasMore: got.page.hasMore(loadedBefore: 0)
        )
        XCTAssertEqual(["hip hop", "rock", MusicGenres.noGenreKey], rows.map(\.key))
        XCTAssertEqual(MusicGenreRow(key: "", name: "no genre", trackCount: 40), rows.last)
    }

    /// Mid-list it would sit between two pages' genres.
    func testNoGenreWaitsForTheLastPage() throws {
        let got = try answer(donePage)
        let rows = MusicGenres.listRows(
            got.page.items, progress: got.progress, search: "",
            hasMore: got.page.hasMore(loadedBefore: 0)
        )
        XCTAssertEqual(["ambient"], rows.map(\.key))
    }

    func testNoGenreIsNotASearchResultAndNotShownEmpty() {
        let items = [MusicGenreRow(key: "rock", name: "Rock", trackCount: 1)]
        let progress = MusicGenreProgress(tagsRead: 5, tagsTotal: 5, reading: false, untaggedCount: 3)
        XCTAssertEqual(["rock"], MusicGenres.listRows(items, progress: progress, search: "ro", hasMore: false).map(\.key))
        XCTAssertEqual(
            ["rock"],
            MusicGenres.listRows(
                items,
                progress: MusicGenreProgress(tagsRead: 5, tagsTotal: 5, untaggedCount: 0),
                search: "",
                hasMore: false
            ).map(\.key)
        )
    }

    /// The empty key is **sent** as `genre=` — present and empty is the
    /// no-genre bucket on the server, absent is the whole library.
    func testTheSongsOfAGenreAreTheLibraryListByArtist() {
        XCTAssertEqual("/api/music/library?genre=hip%20hop&sort=artist", MusicGenres.songsPath(key: "hip hop"))
        XCTAssertEqual("/api/music/library?genre=&sort=artist", MusicGenres.songsPath(key: MusicGenres.noGenreKey))
        XCTAssertEqual("/api/music/genres?sort=tracks&q=r%26b", MusicGenres.path(search: "r&b", sort: .tracks))
        XCTAssertEqual("/api/music/genres?sort=name", MusicGenres.path(search: "  ", sort: .name))
        XCTAssertEqual("/api/music/genre?key=", MusicGenres.detailPath(key: ""))
    }

    func testARoomGetsAtMostItsCap() {
        let ids = (Int64(1)...1200).map { $0 }
        XCTAssertEqual(Array(ids.prefix(500)), MusicGenres.roomTrackIDs(ids, shuffle: false))
        XCTAssertEqual(
            Array(ids.reversed().prefix(500)),
            MusicGenres.roomTrackIDs(ids, shuffle: true, shuffler: { $0.reversed() })
        )
        XCTAssertEqual(
            "a room takes 500 at a time — the first 500 (or a random 500 when shuffled)",
            MusicGenres.roomCapNote(ids.count)
        )
        XCTAssertNil(MusicGenres.roomCapNote(500))
    }

    func testThePhoneSaysHowMuchOfALongSetItTakes() {
        XCTAssertEqual(
            "this device takes 500 at a time — the first 500 (or a random 500 when shuffled)",
            MusicGenres.phoneCapNote(501)
        )
        XCTAssertNil(MusicGenres.phoneCapNote(500))
    }

    /// §5: a set played here is named as the set, "playing Favorites on this
    /// device" — and nothing more is said when the player says the combined
    /// `… · paused kitchen` sentence itself (no toast was taken).
    func testASetPlayedHereIsNamedAsTheSet() {
        XCTAssertEqual(
            "playing Favorites on this device",
            MusicGenres.saidAfterPlayingHere(took: "playing \"Hey Jude\" on this device", label: "Favorites")
        )
        XCTAssertNil(MusicGenres.saidAfterPlayingHere(took: nil, label: "Favorites"))
    }

    /// Android's tab order since 3cdd261b: `genres` straight after `library`.
    func testGenresIsTheSecondTab() {
        XCTAssertEqual([.library, .genres, .queue, .playlists, .stats], MusicTab.shown(hasPlayer: false))
        XCTAssertEqual("genres", MusicTab.genres.label(libraryTotal: 5_232, queueCount: 3, playlistCount: 2))
    }

    // MARK: - the player's panel tabs (`PlayerSheetTabsTest.kt`)

    private func tab(
        here: MusicPlayerPanelTab? = nil,
        picked: MusicPlayerPanelTab? = nil,
        lyricsTab: Bool = true,
        found: Bool = false,
        loading: Bool = false
    ) -> MusicPlayerPanelTab {
        MusicPlayerPanel.choose(
            here: here, picked: picked, lyricsTab: lyricsTab, lyricsFound: found, lyricsLoading: loading
        )
    }

    func testLyricsByDefaultWhenTheSongHasThem() {
        XCTAssertEqual(.lyrics, tab(found: true))
        // Still loading: Lyrics, so it doesn't flick to the queue and back.
        XCTAssertEqual(.lyrics, tab(loading: true))
    }

    func testTheQueueByDefaultWhenItHasNone() {
        XCTAssertEqual(.queue, tab(found: false))
        XCTAssertEqual(.queue, tab(lyricsTab: false, found: false))
    }

    func testThePickSticksAcrossSongs() {
        XCTAssertEqual(.hide, tab(picked: .hide, found: true))
        XCTAssertEqual(.queue, tab(picked: .queue, found: true))
        XCTAssertEqual(.lyrics, tab(picked: .lyrics, found: true))
    }

    func testALyricsPickGivesWayOnASongWithout() {
        XCTAssertEqual(.queue, tab(picked: .lyrics, found: false))
        XCTAssertEqual(.queue, tab(picked: .lyrics, lyricsTab: false))
    }

    func testATabPickedForThisSongStaysEvenLyricsWithNone() {
        XCTAssertEqual(.lyrics, tab(here: .lyrics, found: false))
        XCTAssertEqual(.queue, tab(here: .queue, picked: .hide, found: true))
        XCTAssertEqual(.queue, tab(here: .lyrics, lyricsTab: false))
    }

    func testTheTabsOffered() {
        XCTAssertEqual([.lyrics, .queue, .hide], MusicPlayerPanel.tabs(lyricsTab: true))
        XCTAssertEqual([.queue, .hide], MusicPlayerPanel.tabs(lyricsTab: false))
    }

    func testOnlyWordsCountAsFoundLyrics() {
        XCTAssertTrue(MusicPlayerPanel.lyricsFound(.plain("la la")))
        XCTAssertTrue(MusicPlayerPanel.lyricsFound(.timed([])))
        XCTAssertFalse(MusicPlayerPanel.lyricsFound(.instrumental))
        XCTAssertFalse(MusicPlayerPanel.lyricsFound(.looking))
        XCTAssertFalse(MusicPlayerPanel.lyricsFound(.noLyrics))
        XCTAssertFalse(MusicPlayerPanel.lyricsFound(nil))
    }

    /// While casting, the room's track keys the pick, so a new song in the
    /// room is a new song even though the phone's own entry has not moved.
    func testTheSongAPickBelongsToFollowsTheRoomWhileCasting() {
        XCTAssertEqual("lib-7", MusicPlayerPanel.songKey(castingTo: nil, lyricsTrackID: 7, currentUID: "lib-7"))
        XCTAssertNotEqual(
            MusicPlayerPanel.songKey(castingTo: "parlour", lyricsTrackID: 7, currentUID: "lib-1"),
            MusicPlayerPanel.songKey(castingTo: "parlour", lyricsTrackID: 8, currentUID: "lib-1")
        )
    }

    // MARK: - the models, against a house

    /// What the house answers, changeable between requests.
    private final class House: Sendable {
        let genres = Shared("")
        let detail = Shared("")
        let songs = Shared(#"{"items":[],"total":0}"#)

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url else { return .json("{}", status: 404) }
                switch url.path {
                case "/api/health":
                    let challenge = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                        .queryItems?.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/music/genres": return .json(self.genres.current)
                case "/api/music/genre": return .json(self.detail.current)
                case "/api/music/library": return .json(self.songs.current)
                case "/api/music/play-tracks": return .json(#"{"ok":true}"#)
                default: return .json("{}", status: 404)
                }
            }
        }
    }

    /// Waits that return when allowed and park otherwise (`MusicModelTests.Waits`,
    /// the counted half).
    private final class Waits: Sendable {
        let requested = Shared<[Int64]>([])
        private let budget = Shared<[Int64: Int]>([:])

        func allow(_ ms: Int64, times: Int = 1) { budget.mutate { $0[ms, default: 0] += times } }

        func asked(_ ms: Int64) -> Int { requested.current.filter { $0 == ms }.count }

        var sleeper: @Sendable (Int64) async throws -> Void {
            { [requested, budget] ms in
                requested.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    try Task.checkCancellation()
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

    private func container(_ store: InMemoryShellStore) -> AppContainer {
        AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
    }

    private func store() -> InMemoryShellStore {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        return store
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func queries(_ path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard let url = request.url, url.path == path else { return nil }
            return URLComponents(url: url, resolvingAgainstBaseURL: false)?.query ?? ""
        }
    }

    /// The tab reads its genres and the read's progress from one answer, and
    /// draws "no genre" last.
    func testTheTabReadsItsGenresAndHowFarTheReadHasGot() async {
        let house = House()
        house.genres.set(readingPage)
        house.install()
        let model = MusicGenresModel(container: container(store()), wait: Waits().sleeper)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.list.state.loaded && model.progress != nil }, "the genres read")

        XCTAssertEqual(["sort=name&limit=50"], queries("/api/music/genres"))
        XCTAssertEqual(["hip hop", "rock", ""], model.rows.map(\.key))
        XCTAssertEqual("reading genres from your files… 1,200 of 5,232", model.readingLine)
        XCTAssertEqual("3 genres", model.countText)
    }

    /// While the server reads tags the list re-reads itself every ten
    /// seconds; the first answer that says the read is done ends that.
    func testWhileReadingItRereadsEveryTenSecondsAndStopsWhenDone() async {
        let house = House()
        house.genres.set(readingPage)
        house.install()
        let waits = Waits()
        let model = MusicGenresModel(container: container(store()), wait: waits.sleeper)
        model.start()
        defer { model.stop() }
        await settleUntil({ waits.asked(MusicGenres.readPollMs) == 1 }, "the first ten-second wait")
        XCTAssertEqual(1, queries("/api/music/genres").count)

        // The read finishes between the two answers.
        house.genres.set(readDonePage)
        waits.allow(MusicGenres.readPollMs)
        await settleUntil({ queries("/api/music/genres").count == 2 }, "the re-read")
        await model.list.idle()
        await settleUntil({ model.progress?.reading == false }, "the finished read landing")

        // Done: a further turn of the clock asks for nothing.
        waits.allow(MusicGenres.readPollMs)
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertEqual(2, queries("/api/music/genres").count, "it kept re-reading after the read was done")
        XCTAssertNil(model.readingLine)
    }

    /// A progress answer asked of another house is not this one's
    /// ([[read-after-await-wrong-key]]).
    func testAProgressFromAnotherHouseIsDropped() {
        let model = MusicGenresModel(container: container(store()), wait: Waits().sleeper)
        model.landed(MusicGenreProgress(tagsRead: 1, tagsTotal: 9, reading: true), server: "http://10.0.0.99:6369")
        XCTAssertNil(model.progress)
        model.landed(MusicGenreProgress(tagsRead: 1, tagsTotal: 9, reading: true), server: server)
        XCTAssertEqual(9, model.progress?.tagsTotal)
    }

    /// A sort is one tap: a new list from page one, at once.
    func testANewSortIsANewList() async {
        let house = House()
        house.genres.set(readDonePage)
        house.install()
        let model = MusicGenresModel(container: container(store()), wait: Waits().sleeper)
        model.start()
        defer { model.stop() }
        await settleUntil({ model.list.state.loaded }, "the genres read")
        model.sort = .tracks
        await settleUntil({ queries("/api/music/genres").count == 2 }, "the sorted read")
        XCTAssertEqual("sort=tracks&limit=50", queries("/api/music/genres").last)
    }

    /// The whole genre goes to a room — its ids, at most 500, with this
    /// phone's device id — and the no-genre bucket's songs are asked for as
    /// `genre=` (present and empty).
    func testPlayingAGenreInARoomSendsItsIdsCappedAndNamesThisPhone() async throws {
        let house = House()
        let ids = (1...1200).map(String.init).joined(separator: ",")
        house.detail.set(#"{"key":"","name":"no genre","track_count":1200,"track_ids":[\#(ids)]}"#)
        house.install()
        let said = Shared<[String]>([])
        let detail = MusicGenresDetailModel(
            genre: MusicGenreRow(key: "", name: "no genre", trackCount: 1200),
            container: container(store()),
            deviceID: "ios-test-device",
            say: { line in said.mutate { $0.append(line) } }
        )
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.detail.answered && detail.songs.state.loaded }, "the genre's reads")
        XCTAssertEqual(["genre=&sort=artist&limit=100"], queries("/api/music/library"))
        XCTAssertEqual(1200, detail.roomIDs.count)
        XCTAssertEqual(
            "a room takes 500 at a time — the first 500 (or a random 500 when shuffled)",
            detail.roomCapNote
        )

        XCTAssertTrue(detail.play(shuffle: false, rooms: ["parlour", "attic"]))
        await settleUntil({ !StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/play-tracks" }.isEmpty },
                          "the play")
        let sent = try XCTUnwrap(
            StubURLProtocol.recorded.first { $0.url?.path == "/api/music/play-tracks" }?.httpBody
        )
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: sent) as? [String: Any])
        XCTAssertEqual("parlour", body["room_id"] as? String)
        XCTAssertEqual("ios-test-device", body["device_id"] as? String)
        let got = try XCTUnwrap(body["track_ids"] as? [Int])
        XCTAssertEqual(Array(1...500), got)
        XCTAssertEqual(["playing no genre in parlour…"], said.current)
    }

    /// With no room online nothing is sent, and the picked room is used while
    /// it is still online.
    func testNoRoomNoPlayAndThePickedRoomIsTheOneUsed() async {
        let house = House()
        house.detail.set(#"{"key":"rock","name":"Rock","track_count":2,"track_ids":[3,4]}"#)
        house.install()
        let detail = MusicGenresDetailModel(
            genre: MusicGenreRow(key: "rock", name: "Rock", trackCount: 2),
            container: container(store()),
            deviceID: "ios-test-device",
            say: { _ in }
        )
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.detail.answered }, "the genre's ids")
        XCTAssertFalse(detail.play(shuffle: false, rooms: []))
        XCTAssertFalse(detail.mayPlayInRoom([]))
        detail.pick("attic")
        XCTAssertEqual("attic", detail.room(in: ["parlour", "attic"]))
        XCTAssertEqual("parlour", detail.room(in: ["parlour"]), "a picked room that went away is not used")
        for _ in 0..<500 { await Task.yield() }
        XCTAssertTrue(queries("/api/music/play-tracks").isEmpty)
    }

    /// The ids were read from one house; the phone has since moved to
    /// another. They are **not** sent to a room of the new one — the
    /// read-after-await defect, keyed off by the server the read was for.
    func testIdsReadFromAnotherHouseAreNeverSent() async {
        let house = House()
        house.detail.set(#"{"key":"rock","name":"Rock","track_count":2,"track_ids":[3,4]}"#)
        house.install()
        let store = store()
        let detail = MusicGenresDetailModel(
            genre: MusicGenreRow(key: "rock", name: "Rock", trackCount: 2),
            container: container(store),
            deviceID: "ios-test-device",
            say: { _ in }
        )
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.detail.answered }, "the genre's ids")
        XCTAssertEqual([3, 4], detail.roomIDs)

        let next = "http://10.0.0.77:6369"
        store.trustServer(next, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(next))

        XCTAssertEqual([], detail.roomIDs, "the old house's ids are still offered to the new house's rooms")
        XCTAssertFalse(detail.play(shuffle: false, rooms: ["parlour"]))
        for _ in 0..<500 { await Task.yield() }
        XCTAssertTrue(queries("/api/music/play-tracks").isEmpty)
    }

    func testThePlayBodyIsTheServersShape() {
        let body = MusicGenresDetailModel.playBody(room: "par\"lour", trackIDs: [3, 4], deviceID: "ios-1")
        XCTAssertEqual(
            #"{"room_id":"par\"lour","track_ids":[3,4],"device_id":"ios-1"}"#,
            String(decoding: body, as: UTF8.self)
        )
    }
}
