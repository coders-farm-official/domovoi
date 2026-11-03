import Foundation
import XCTest
@testable import DomovoiKit

/// An audio engine a test can ask what it was handed.
///
/// `canPlay` is the whole point of ``StationsPlayback``: a build with no
/// engine must draw no play affordance **and** send no `POST /play`, because
/// that POST stamps the station into the house's Recent strip.
final class StationsPlaybackSpy: StationsPlayback {

    let played = Shared<[Int]>([])
    private let able: Bool

    init(canPlay: Bool = true) {
        self.able = canPlay
    }

    var canPlay: Bool { able }

    @MainActor func play(station: StationsRow) {
        played.mutate { $0.append(station.id) }
    }
}

/// Stations' data layer against a server that is not a server: what is asked
/// for, what a star does to which row, what is refused before it is sent, and
/// whether an answer that arrived late still belongs to the house on screen.
@MainActor
final class StationsModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the house

    /// Twenty-three favourites, three Recent rows and a catalogue of
    /// thirty-four, so that page one of twenty is full and a twenty-first row
    /// exists, and page one of thirty likewise. Every assertion below is made
    /// with data only one row has.
    private final class House: Sendable {

        /// Every request that reached the stub, as `"METHOD path?query"`.
        let seen = Shared<[String]>([])

        /// Every body that came with one, in the same order.
        let bodies = Shared<[String]>([])

        /// The id the next `POST /stations` answers.
        let savedID = Shared<Int>(101)

        /// Whether `resolve-simulcast` finds one.
        let resolves = Shared<Bool>(true)

        /// The favourites read answers this instead of rows, when it is set.
        let favoritesStatus = Shared<Int>(200)

        /// The ids a `DELETE` has taken away.
        ///
        /// A real deletion and not a recorded one, because a `forget` is
        /// followed by a re-read: a house that kept answering the row would
        /// put it back under its own "forgot" toast, and the test of the
        /// removal would be a race.
        let deleted = Shared<Set<Int>>([])

        /// `rows` without anything this house has deleted. The id is matched
        /// on the `{"id":N,` the generator always opens with.
        func alive(_ rows: [String]) -> [String] {
            let gone = deleted.current
            guard !gone.isEmpty else { return rows }
            return rows.filter { row in
                !gone.contains { row.hasPrefix("{\"id\":\($0),") }
            }
        }

        static func station(
            id: Int,
            name: String,
            source: String = "online",
            streamURL: String? = "https://stream.example/\(UUID().uuidString)",
            externalID: String? = nil,
            favorited: Bool = true,
            interval: Int? = 180,
            extra: String = ""
        ) -> String {
            let url = streamURL.map { "\"\($0)\"" } ?? "null"
            let ext = externalID.map { "\"\($0)\"" } ?? "null"
            let every = interval.map(String.init) ?? "null"
            return """
            {"id":\(id),"name":"\(name)","source":"\(source)","stream_url":\(url),
             "external_id":\(ext),"country_code":"US","language":"english","tags":["indie"],
             "favorited":\(favorited),"sample_interval_sec":\(every)\(extra.isEmpty ? "" : ",")\(extra)}
            """
        }

        /// `Favourite Kestrel 01` … `23`, zero-padded so the order is
        /// assertable. 04 is playing something, 05 has no ICY support and 06
        /// has never been probed — the three now-playing branches, one row
        /// each.
        static let favorites: [String] = (1...23).map { n in
            let name = String(format: "Favourite Kestrel %02d", n)
            switch n {
            case 4:
                return station(
                    id: n, name: name, streamURL: "https://stream.example/kestrel-04",
                    extra: """
                    "now_playing":"Sonora Pine — Red Fire Serpent",
                    "now_playing_updated_at":"2026-11-02T10:00:00Z",
                    "icy_supported":true,"last_sampled_at":"2026-11-02T10:00:00Z"
                    """
                )
            case 5:
                return station(id: n, name: name, extra: #""icy_supported":false"#)
            case 6:
                return station(id: n, name: name, extra: #""icy_supported":null"#)
            default:
                return station(id: n, name: name)
            }
        }

        /// The one FM favourite: no stream URL, so it is not playable and a
        /// simulcast can be resolved for it.
        static let wjim = station(
            id: 77, name: "WJIM-FM", source: "fm", streamURL: nil,
            extra: #""frequency_mhz":97.5,"call_sign":"WJIM","market_city":"Lansing","market_state":"MI""#
        )

        /// Three played, one of which is **not** a favourite — so the star in
        /// Recent has something to promote, and the favourites list must not
        /// hold it.
        static let recent: [String] = [
            station(id: 1, name: "Favourite Kestrel 01", extra: #""last_played_at":"2026-11-02T09:00:00Z""#),
            station(id: 2, name: "Favourite Kestrel 02", extra: #""last_played_at":"2026-11-02T08:00:00Z""#),
            station(
                id: 90, name: "Played Once Gull", favorited: false,
                extra: #""last_played_at":"2026-11-02T07:00:00Z","created_by_play":true"#
            ),
        ]

        /// Thirty-four hits. `Catalogue Ibis 02` shares an `external_id` with
        /// favourite 2, so it comes back with a real id and `favorited: true`;
        /// `Catalogue Ibis 03` carries a **host-local** URL, which
        /// ``StationsRules/playable(_:)`` must refuse.
        static let catalogue: [String] = (1...34).map { n in
            let name = String(format: "Catalogue Ibis %02d", n)
            switch n {
            case 2:
                return station(id: 2, name: name, externalID: "rb-kestrel-02", favorited: true)
            case 3:
                return station(
                    id: 0, name: name, streamURL: "http://127.0.0.1:9000/stream",
                    externalID: "rb-ibis-03", favorited: false
                )
            default:
                return station(
                    id: 0, name: name, streamURL: "https://ibis.example/\(n)",
                    externalID: String(format: "rb-ibis-%02d", n), favorited: false
                )
            }
        }

        static func array(_ rows: [String], limit: Int, offset: Int) -> String {
            let from = min(max(0, offset), rows.count)
            let to = min(from + max(0, limit), rows.count)
            return "[" + rows[from..<to].joined(separator: ",") + "]"
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
                let method = request.httpMethod ?? "GET"
                let line = parts.query.map { "\(method) \(parts.path)?\($0)" } ?? "\(method) \(parts.path)"
                if parts.path != "/api/health" {
                    seen.mutate { $0.append(line) }
                    bodies.mutate {
                        $0.append(request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? "")
                    }
                }
                let limit = Int(value("limit") ?? "0") ?? 0
                let offset = Int(value("offset") ?? "0") ?? 0

                switch (method, parts.path) {
                case ("GET", "/api/health"):
                    // The identity gate's probe. Without it every read below
                    // is refused and every assertion is about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")

                case ("GET", "/api/plugins/radio/stations"):
                    guard favoritesStatus.current == 200 else {
                        return .json(#"{"detail":"the plugin is restarting"}"#, status: favoritesStatus.current)
                    }
                    if value("source") == "fm" {
                        var rows = alive([Self.wjim])
                        if let needle = value("q"), !Self.wjim.lowercased().contains(needle.lowercased()) {
                            rows = []
                        }
                        if let frequency = value("frequency_mhz"), frequency != "97.5" { rows = [] }
                        return .json(Self.array(rows, limit: limit, offset: offset))
                    }
                    var rows = alive(Self.favorites + [Self.wjim])
                    if let needle = value("q") {
                        rows = rows.filter { $0.lowercased().contains(needle.lowercased()) }
                    }
                    return .json(Self.array(rows, limit: limit, offset: offset))

                case ("GET", "/api/plugins/radio/recent"):
                    return .json("[" + Self.recent.joined(separator: ",") + "]")

                case ("GET", "/api/plugins/radio/badge"):
                    return .json(#"{"favorites":23}"#)

                case ("GET", "/api/plugins/radio/search"):
                    var rows = Self.catalogue
                    if let needle = value("q"), !needle.isEmpty {
                        rows = rows.filter { $0.lowercased().contains(needle.lowercased()) }
                    }
                    return .json(Self.array(rows, limit: limit, offset: offset))

                case ("GET", "/api/plugins/radio/detections"):
                    guard value("station_id") == "4" else { return .json("[]") }
                    return .json("""
                    [{"id":5,"station_id":4,"artist":"Sonora Pine","title":"Red Fire Serpent",
                      "detected_at":"2026-11-02T10:00:00Z","fingerprint_source":"dejavu_local",
                      "in_library":true,"library_track_id":3012},
                     {"id":4,"station_id":4,"artist":"Someone","title":"A B",
                      "detected_at":"2026-11-02T09:40:00Z","fingerprint_source":"shazam",
                      "in_library":false}]
                    """)

                case ("POST", "/api/plugins/radio/stations"):
                    let id = savedID.current
                    return .json(
                        Self.station(id: id, name: "Catalogue Ibis 01", externalID: "rb-ibis-01"),
                        status: 201
                    )

                case ("POST", "/api/plugins/radio/play"):
                    // The route answers the saved row, which is how an
                    // unsaved hit gets an id.
                    let id = savedID.current
                    return .json(Self.station(id: id, name: "Catalogue Ibis 01", favorited: false))

                case ("DELETE", _):
                    if let id = Int(parts.path.split(separator: "/").last ?? "") {
                        deleted.mutate { $0.insert(id) }
                    }
                    return .json("", status: 204)

                case ("PATCH", _):
                    return .json(Self.station(id: 1, name: "Favourite Kestrel 01"))

                case ("POST", _) where parts.path.hasSuffix("/resolve-simulcast"):
                    guard resolves.current else {
                        return .json(#"{"resolved":false,"message":"no radio-browser match for WJIM"}"#)
                    }
                    return .json(#"{"resolved":true,"stream_url":"https://simul.example/wjim"}"#)

                default:
                    return .json("{}", status: 404)
                }
            }
        }

        /// Every request to `path`, in order.
        func requests(to path: String) -> [String] {
            seen.current.filter { $0.contains(path) }
        }
    }

    /// The instant-favourites debounce, as a gate a test opens — the shape
    /// ``FilesModelTests`` uses, and for its reason: a gate that ran out
    /// looks exactly like a wait that finished, so it says which it was.
    private final class Gate: Sendable {
        let asked = Shared<[Int64]>([])
        let gaveUp = Shared<Bool>(false)
        private let opened = Shared<Bool>(false)

        func release() { opened.set(true) }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let opened = self.opened
            let gaveUp = self.gaveUp
            return { ms in
                asked.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    if opened.current { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                gaveUp.set(true)
                throw CancellationError()
            }
        }
    }

    private func rig(
        house: House = House(),
        playback: StationsPlaybackSpy = StationsPlaybackSpy(),
        gate: Gate? = nil
    ) -> (StationsModel, House, InMemoryShellStore, AppContainer) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = StationsModel(
            in: container,
            playback: playback,
            wait: gate?.sleeper ?? { _ in },
            clock: { 1_793_613_840_000 }
        )
        return (model, house, store, container)
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

    /// Long enough for anything in flight to land, for the tests that assert
    /// nothing happened.
    private func nothingHappens() async {
        for _ in 0..<2_000 { await Task.yield() }
    }

    /// Every favourite, both pages — the FM row sorts last (the server's
    /// `favorited DESC, name ASC, id ASC`), so a test about it has to reach
    /// the foot of the list first.
    private func loadEveryFavourite(_ model: StationsModel) async {
        model.favorites.loadMore()
        await settleUntil({ !model.favorites.state.hasMore && !model.favorites.state.loading },
                          "the whole favourites list")
    }

    private func started(_ model: StationsModel) async {
        model.start()
        await settleUntil({ model.favorites.state.loaded && model.badge.data != nil && model.recent.data != nil },
                          "the three reads")
    }

    // MARK: - the three cards

    func testTheFavouritesTheCountAndTheRecentStripAllLoad() async throws {
        let (model, _, _, _) = rig()
        await started(model)

        XCTAssertEqual(20, model.favorites.state.items.count, "page one is twenty (FAVORITES_PAGE_SIZE)")
        XCTAssertEqual("Favourite Kestrel 01", model.favorites.state.items.first?.name)
        XCTAssertEqual(23, model.favoritesTotal, "the count is /badge's, not the page's")
        XCTAssertEqual("23 favorited · 3 recent · tap a station to play it", model.subtitle)
        XCTAssertEqual(
            ["Favourite Kestrel 01", "Favourite Kestrel 02", "Played Once Gull"],
            model.recentRows.map(\.name)
        )
        XCTAssertFalse(
            model.favorites.state.items.contains { $0.name == "Played Once Gull" },
            "a row that was only ever played is not a favourite — if it were, the two lists would be one"
        )
    }

    /// The three now-playing branches, one favourite each: a screen that
    /// collapsed them would show the same line on all three.
    func testTheNowPlayingLineTellsItsThreeStatesApart() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        let rows = model.favorites.state.items
        let four = try XCTUnwrap(rows.first { $0.name == "Favourite Kestrel 04" })
        let five = try XCTUnwrap(rows.first { $0.name == "Favourite Kestrel 05" })
        let six = try XCTUnwrap(rows.first { $0.name == "Favourite Kestrel 06" })
        XCTAssertEqual(
            .track("Sonora Pine — Red Fire Serpent", since: "2026-11-02T10:00:00Z"),
            model.nowPlaying(four)
        )
        XCTAssertEqual(.noLiveMetadata, model.nowPlaying(five))
        XCTAssertEqual(.listening, model.nowPlaying(six))
        XCTAssertEqual(.ok, StationsRules.samplerTone(four), "04 has been sampled")
        XCTAssertEqual(.idle, StationsRules.samplerTone(five))
    }

    /// Page two is asked for near the foot, and only there: the twenty-first
    /// favourite exists after a `loadMore`, and the list then says it is
    /// whole.
    func testTheFavouritesListLoadsAsYouScroll() async throws {
        let (model, house, _, _) = rig()
        await started(model)

        XCTAssertFalse(model.shouldLoadMoreFavorites(lastVisible: 2),
                       "the third row is not near the foot of twenty")
        XCTAssertTrue(model.shouldLoadMoreFavorites(lastVisible: 19))

        model.favorites.loadMore()
        await settleUntil({ model.favorites.state.items.count > 20 }, "page two")
        XCTAssertEqual(24, model.favorites.state.items.count, "23 favourites plus the FM row")
        XCTAssertEqual("Favourite Kestrel 23", model.favorites.state.items[22].name)
        XCTAssertFalse(model.favorites.state.hasMore, "a short page is the end of the list")
        XCTAssertEqual(
            ["GET /api/plugins/radio/stations?favorited_only=true&limit=20",
             "GET /api/plugins/radio/stations?favorited_only=true&limit=20&offset=20"],
            house.requests(to: "favorited_only"),
            "two pages, the second by offset — and no third once the page came up short"
        )
    }

    // MARK: - the star

    /// **Plugin board F-P013.** The first tap POSTs the hit, which saves and
    /// favourites it; the id that POST answers is written onto the row, so
    /// the second tap PATCHes it off. Dropping the id made the second tap
    /// POST again and the star never came off.
    func testTheStarOnANewHitSavesAndThenUnfavourites() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.submit(override: "Catalogue Ibis 01")
        await settleUntil({ !model.results.state.items.isEmpty }, "the search")

        let hit = try XCTUnwrap(model.results.state.items.first)
        XCTAssertEqual(0, hit.id, "an unsaved directory hit")
        XCTAssertEqual("ext:rb-ibis-01", hit.rowKey)

        model.star(hit)
        await settleUntil({ model.results.state.items.first?.id == 101 }, "the POST's id")
        XCTAssertTrue(try XCTUnwrap(model.results.state.items.first).favorited)
        XCTAssertEqual("favorited Catalogue Ibis 01", model.toast)
        XCTAssertTrue(
            house.seen.current.contains("POST /api/plugins/radio/stations"),
            "the first tap created the row: \(house.seen.current)"
        )

        let saved = try XCTUnwrap(model.results.state.items.first)
        model.star(saved)
        await settleUntil({ model.results.state.items.first?.favorited == false }, "the second tap")
        XCTAssertEqual("unfavorited Catalogue Ibis 01", model.toast)
        XCTAssertEqual(
            ["PATCH /api/plugins/radio/stations/101"],
            house.requests(to: "/stations/101"),
            "the second tap PATCHED the id the POST answered — it did not POST again"
        )
        XCTAssertTrue(house.bodies.current.contains(#"{"favorited":false}"#))
    }

    /// A star on an FM row with a blank URL fires `resolve-simulcast` as a
    /// **separate** request, so a slow lookup cannot bounce the favourite
    /// itself (`StationSearch.kt:446`).
    func testFavouritingABlankFMRowResolvesItsSimulcastSeparately() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.switchScope(.fm)
        model.submit()
        await settleUntil({ !model.results.state.items.isEmpty }, "the FM browse")

        let wjim = try XCTUnwrap(model.results.state.items.first)
        XCTAssertEqual(77, wjim.id, "an FCC row is already in the table, so this is a PATCH")
        // The seeded row comes back favorited, so a star on it would
        // UNfavourite. Flip it first, which is the state the resolve follows.
        var unfavorited = wjim
        unfavorited.favorited = false
        model.star(unfavorited)
        await settleUntil({ model.toast == "simulcast found for WJIM-FM" }, "the resolve")
        XCTAssertEqual(
            ["PATCH /api/plugins/radio/stations/77", "POST /api/plugins/radio/stations/77/resolve-simulcast"],
            house.requests(to: "/stations/77"),
            "two requests, in that order — the favourite does not wait on the lookup"
        )
    }

    /// A Recent row is in the table by definition, so its star is always a
    /// PATCH — there is no POST path to get wrong (`StationsScreen.kt:325`).
    func testTheStarOnARecentRowIsAlwaysAPatch() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        let gull = try XCTUnwrap(model.recentRows.first { $0.name == "Played Once Gull" })
        XCTAssertFalse(gull.favorited)

        model.toggleFavorite(gull)
        await settleUntil({ model.toast != nil }, "the PATCH")
        XCTAssertEqual("favorited Played Once Gull", model.toast)
        XCTAssertEqual(["PATCH /api/plugins/radio/stations/90"], house.requests(to: "/stations/90"))
        XCTAssertTrue(house.bodies.current.contains(#"{"favorited":true}"#))
        XCTAssertTrue(
            house.requests(to: "POST /api/plugins/radio/stations").isEmpty,
            "nothing was created: the row already existed"
        )
    }

    // MARK: - the interval

    func testAnIntervalOutsideTheServersBoundsIsNotSent() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first)

        XCTAssertFalse(model.saveInterval(row, draft: "10"))
        XCTAssertEqual("interval must be between 30 and 86400 s", model.toast)
        await nothingHappens()
        XCTAssertTrue(
            house.requests(to: "PATCH").isEmpty,
            "a typo is a sentence, not a 422: \(house.seen.current)"
        )
    }

    func testAnAcceptedIntervalIsPatchedAndTheRowSaysSo() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first)
        XCTAssertEqual("US · sampling every 180s", model.metaLine(row))

        XCTAssertTrue(model.saveInterval(row, draft: " 600 "))
        await settleUntil({ model.favorites.state.items.first?.sampleIntervalSec == 600 }, "the PATCH")
        XCTAssertEqual("Favourite Kestrel 01: sampling every 600s", model.toast)
        XCTAssertEqual(
            "US · sampling every 600s",
            model.metaLine(try XCTUnwrap(model.favorites.state.items.first)),
            "the loaded row is patched in place rather than the page re-read to it"
        )
        XCTAssertTrue(house.bodies.current.contains(#"{"sample_interval_sec":600}"#))
    }

    // MARK: - forgetting

    func testForgettingTakesTheRowOffTheListAndClosesItsDetail() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first { $0.name == "Favourite Kestrel 04" })
        model.toggleDetail(row)
        XCTAssertEqual(4, model.selected)
        XCTAssertNotNil(model.detail)

        model.forget(row)
        await settleUntil({ model.toast == "forgot Favourite Kestrel 04" }, "the DELETE")
        XCTAssertNil(model.selected, "the sheet cannot stay open over a row that is gone")
        XCTAssertNil(model.detail)
        XCTAssertNil(model.selectedStation)
        XCTAssertFalse(
            model.favorites.state.items.contains { $0.name == "Favourite Kestrel 04" },
            "and the row went with it, rather than sitting under its own 'forgot' toast"
        )
        XCTAssertEqual(["DELETE /api/plugins/radio/stations/4"], house.requests(to: "DELETE"))
    }

    // MARK: - playing

    func testPlayingPostsPlayAndHandsTheAnsweredRowToTheEngine() async throws {
        let spy = StationsPlaybackSpy()
        let (model, house, _, _) = rig(playback: spy)
        await started(model)
        model.submit(override: "Catalogue Ibis 01")
        await settleUntil({ !model.results.state.items.isEmpty }, "the search")
        let hit = try XCTUnwrap(model.results.state.items.first)

        model.play(hit)
        await settleUntil({ !spy.played.current.isEmpty }, "the play")
        XCTAssertEqual([101], spy.played.current,
                       "the engine is handed the SAVED row: the stream proxy resolves an id, not a URL")
        XCTAssertEqual("playing Catalogue Ibis 01", model.toast)
        XCTAssertEqual(["POST /api/plugins/radio/play"], house.requests(to: "radio/play"))
        XCTAssertTrue(
            house.bodies.current.contains { $0.contains(#""external_id":"rb-ibis-01""#) },
            "an unsaved hit is sent in the directory shape, not as a station_id"
        )
    }

    /// Playing is not favouriting: `POST /play` leaves `favorited` FALSE, and
    /// nothing here stars anything (`StationPlay.kt`'s whole doc comment).
    func testPlayingDoesNotFavourite() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.submit(override: "Catalogue Ibis 01")
        await settleUntil({ !model.results.state.items.isEmpty })
        model.play(try XCTUnwrap(model.results.state.items.first))
        await settleUntil({ model.toast != nil }, "the play")

        XCTAssertFalse(model.results.state.items.first?.favorited ?? true)
        XCTAssertTrue(
            house.requests(to: "POST /api/plugins/radio/stations").isEmpty,
            "nothing was saved as a favourite: \(house.seen.current)"
        )
    }

    func testAnUnplayableStationSaysWhyAndSendsNothing() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.submit(override: "Catalogue Ibis 03")
        await settleUntil({ !model.results.state.items.isEmpty }, "the search")
        let hostLocal = try XCTUnwrap(model.results.state.items.first)
        XCTAssertEqual("http://127.0.0.1:9000/stream", hostLocal.streamURL)

        XCTAssertFalse(model.canPlay(hostLocal))
        XCTAssertEqual(
            "Catalogue Ibis 03 has no playable stream URL",
            model.whyNotPlayable(hostLocal)
        )
        model.play(hostLocal)
        await nothingHappens()
        XCTAssertEqual("Catalogue Ibis 03 has no playable stream URL", model.toast)
        XCTAssertTrue(house.requests(to: "radio/play").isEmpty,
                      "the proxy would 409 it, so it is refused here: \(house.seen.current)")
    }

    /// No engine in this build: no affordance anywhere, and **no request** —
    /// `POST /play` stamps the station into Recent, so a silent button's one
    /// visible effect would be a history entry for something nobody heard.
    func testWithNoEngineNothingIsOfferedAndNothingIsSent() async throws {
        let silent = StationsPlaybackSpy(canPlay: false)
        let (model, house, _, _) = rig(playback: silent)
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first)

        XCTAssertFalse(model.canPlayAnything)
        XCTAssertFalse(model.canPlay(row))
        XCTAssertNil(model.whyNotPlayable(row),
                     "a build that plays nothing does not say why one station is special")
        model.play(row)
        await nothingHappens()
        XCTAssertNil(model.toast)
        XCTAssertTrue(house.requests(to: "radio/play").isEmpty, "\(house.seen.current)")
        XCTAssertTrue(silent.played.current.isEmpty)
    }

    /// **[[read-after-await-wrong-key]].** A `/play` answered after the app
    /// moved house carries the OLD house's id; handing it to the engine would
    /// open the NEW house's `/stations/{id}/stream` under the name that was
    /// tapped. The switch happens **inside** the round trip, which is the
    /// only honest way to test it.
    func testAPlayThatLandsAfterTheHouseChangedIsDropped() async throws {
        let house = House()
        house.install()
        let spy = StationsPlaybackSpy()
        let park = parkRequest(
            to: "/api/plugins/radio/play",
            answering: { .json(House.station(id: 101, name: "Catalogue Ibis 01", favorited: false)) },
            otherwise: { request in
                guard let url = request.url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}") }
                if parts.path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                if parts.path == "/api/plugins/radio/badge" { return .json(#"{"favorites":23}"#) }
                return .json("[]")
            }
        )
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = StationsModel(in: container, playback: spy, wait: { _ in }, clock: { 0 })

        model.play(StationsRow(
            id: 0, name: "Catalogue Ibis 01",
            streamURL: "https://ibis.example/1", externalID: "rb-ibis-01"
        ))
        await settle(for: park.arrived, what: "the play")

        // The app moves house while the play is still open.
        _ = store.setServerURL(otherServer)
        model.serverChanged()
        park.release.set(true)
        await nothingHappens()

        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertTrue(spy.played.current.isEmpty,
                      "the old house's row must not be opened against the new house")
        XCTAssertNil(model.toast, "and nothing said it was playing")
    }

    // MARK: - the instant favourites

    func testTypingAnswersFromTheFavouritesBeforeAnythingIsSubmitted() async throws {
        let (model, house, _, _) = rig()
        await started(model)

        model.typed("Kestrel 04")
        await settleUntil({ !model.favoriteMatches.isEmpty }, "the instant favourites")
        XCTAssertEqual(["Favourite Kestrel 04"], model.favoriteMatches.map(\.name))
        XCTAssertFalse(model.submitted, "nothing was submitted: the directory was never asked")
        XCTAssertTrue(
            house.requests(to: "radio/search").isEmpty,
            "the directory is a network hop and is not touched per keystroke: \(house.seen.current)"
        )
        XCTAssertEqual(
            ["GET /api/plugins/radio/stations?favorited_only=true&q=Kestrel%2004&limit=8"],
            house.requests(to: "limit=8")
        )
    }

    /// The debounce, and the half of it Android gets for free: a superseded
    /// answer can never land. Here it is said twice — the task is cancelled,
    /// and the landing checks the text it was started for is still the text
    /// in the box.
    func testASupersededInstantFavouriteNeverLands() async throws {
        let gate = Gate()
        let (model, house, _, _) = rig(gate: gate)
        await started(model)

        model.typed("Kes")
        await settleUntil({ gate.asked.current.count == 1 }, "the first debounce")
        XCTAssertEqual([StationsModel.instantFavoritesDebounceMs], gate.asked.current)
        model.typed("Kestrel 04")
        await settleUntil({ gate.asked.current.count == 2 }, "the second debounce")
        gate.release()

        await settleUntil({ !model.favoriteMatches.isEmpty }, "the answer")
        await nothingHappens()
        XCTAssertFalse(gate.gaveUp.current, "the gate ran out, so this asserts nothing")
        XCTAssertEqual(["Favourite Kestrel 04"], model.favoriteMatches.map(\.name))
        XCTAssertEqual(
            ["GET /api/plugins/radio/stations?favorited_only=true&q=Kestrel%2004&limit=8"],
            house.requests(to: "limit=8"),
            "one request, for the text that was still in the box"
        )
    }

    func testClearingTheBoxClearsTheMatches() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        model.typed("Kestrel 04")
        await settleUntil({ !model.favoriteMatches.isEmpty })
        model.typed("")
        XCTAssertTrue(model.favoriteMatches.isEmpty, "cleared at once, not after a debounce")
    }

    // MARK: - the search surface

    func testAnOnlineSearchWithNeitherAQueryNorACountryAsksNothing() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.typedCountry("")
        model.submit(override: "   ")
        await nothingHappens()
        XCTAssertFalse(model.submitted)
        XCTAssertNil(model.searchEmptyNote, "nothing was asked, so nothing is empty")
        XCTAssertTrue(house.requests(to: "radio/search").isEmpty, "\(house.seen.current)")
    }

    func testASearchPagesByThirtyAndTheResultsLoadAsYouScroll() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.submit(override: "Catalogue")
        await settleUntil({ model.results.state.loaded }, "the search")

        XCTAssertTrue(model.submitted)
        XCTAssertEqual(30, model.results.state.items.count, "PAGE_SIZE")
        XCTAssertTrue(model.results.state.hasMore, "a full page looks like there is more")
        model.results.loadMore()
        await settleUntil({ model.results.state.items.count > 30 }, "page two")
        XCTAssertEqual(34, model.results.state.items.count)
        XCTAssertFalse(model.results.state.hasMore)
        XCTAssertEqual(
            ["GET /api/plugins/radio/search?q=Catalogue&country_code=US&limit=30",
             "GET /api/plugins/radio/search?q=Catalogue&country_code=US&limit=30&offset=30"],
            house.requests(to: "radio/search")
        )
    }

    func testAnEmptySearchSaysWhatWasSearchedForNotWhatIsTyped() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        model.submit(override: "nothing called this")
        await settleUntil({ model.results.state.loaded }, "the search")
        XCTAssertTrue(model.results.state.items.isEmpty)
        XCTAssertEqual("nothing in US called “nothing called this”", model.searchEmptyNote)

        // Typing on does not relabel the answer on screen.
        model.typed("something else")
        XCTAssertEqual("nothing in US called “nothing called this”", model.searchEmptyNote)
    }

    func testFlippingTheScopeEmptiesTheResultsRatherThanRelabellingThem() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        model.submit(override: "Catalogue")
        await settleUntil({ !model.results.state.items.isEmpty })

        model.switchScope(.fm)
        await nothingHappens()
        XCTAssertTrue(model.results.state.items.isEmpty,
                      "online results draw in the online row layout, not the FM one")
        XCTAssertFalse(model.submitted)
        XCTAssertTrue(model.showsFMTip, "and the FM tip is back")
        XCTAssertFalse(model.showsTagChips)
    }

    func testTheCountryBoxTakesTwoUpperCaseLetters() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        model.typedCountry("gbr")
        XCTAssertEqual("GB", model.country)
    }

    // MARK: - the detail

    func testTheDetailReadsThatStationsDetections() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        let four = try XCTUnwrap(model.favorites.state.items.first { $0.id == 4 })

        model.toggleDetail(four)
        let detail = try XCTUnwrap(model.detail)
        await settleUntil({ detail.detections.data != nil }, "the feed")
        XCTAssertEqual(["Red Fire Serpent", "A B"], detail.rows.map { $0.title ?? "" })
        XCTAssertEqual("local", detail.source(detail.rows[0]).label)
        XCTAssertEqual(.brand, detail.source(detail.rows[0]).tone)
        XCTAssertTrue(detail.inLibrary(detail.rows[0]))
        XCTAssertEqual("shazam", detail.source(detail.rows[1]).label,
                       "an unknown source passes through verbatim")
        XCTAssertFalse(detail.inLibrary(detail.rows[1]))
        XCTAssertEqual(["GET /api/plugins/radio/detections?station_id=4&limit=100"],
                       house.requests(to: "detections"))

        // The chevron again closes it, and stops the read.
        model.toggleDetail(four)
        XCTAssertNil(model.detail)
        XCTAssertNil(model.selected)
    }

    /// The field's three faces, and the one that offers `resolve` — only an
    /// FM row has a simulcast to look for.
    func testTheStreamFieldsThreeFaces() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        await loadEveryFavourite(model)
        let wjim = try XCTUnwrap(model.favorites.state.items.first { $0.id == 77 })
        let kestrel = try XCTUnwrap(model.favorites.state.items.first { $0.id == 4 })
        model.toggleDetail(wjim)
        let detail = try XCTUnwrap(model.detail)

        XCTAssertEqual(.empty(canResolve: true), detail.streamState(wjim))
        XCTAssertEqual(
            .empty(canResolve: false),
            detail.streamState(StationsRow(id: 9, source: "online")),
            "there is no simulcast to resolve for an online station"
        )
        XCTAssertEqual(.view("https://stream.example/kestrel-04"), detail.streamState(kestrel))

        detail.beginEdit(wjim)
        XCTAssertEqual(.edit, detail.streamState(wjim))
        XCTAssertEqual("", detail.draft)
        detail.typedDraft("ftp://nope")
        XCTAssertFalse(model.saveStreamURL(wjim, draft: detail.draft))
        XCTAssertEqual("stream URL must start with http(s)://", model.toast)
        XCTAssertEqual(.edit, detail.streamState(wjim), "a refused draft keeps the field open")

        detail.cancelEdit(wjim)
        XCTAssertEqual(.empty(canResolve: true), detail.streamState(wjim))
        XCTAssertEqual("", detail.draft, "and the abandoned text is gone")
    }

    func testSavingAStreamURLPatchesItOntoTheRow() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        await loadEveryFavourite(model)
        let wjim = try XCTUnwrap(model.favorites.state.items.first { $0.id == 77 })

        XCTAssertTrue(model.saveStreamURL(wjim, draft: " https://simul.example/wjim "))
        await settleUntil({ model.toast == "stream URL saved" }, "the PATCH")
        XCTAssertEqual(
            "https://simul.example/wjim",
            model.favorites.state.items.first { $0.id == 77 }?.streamURL,
            "trimmed, and patched onto the loaded row"
        )
        XCTAssertTrue(house.bodies.current.contains(#"{"stream_url":"https://simul.example/wjim"}"#))
    }

    func testResolvingASimulcastFromTheDetailSaysWhenThereIsNone() async throws {
        let house = House()
        house.resolves.set(false)
        let (model, _, _, _) = rig(house: house)
        await started(model)
        await loadEveryFavourite(model)
        let wjim = try XCTUnwrap(model.favorites.state.items.first { $0.id == 77 })

        model.resolveSimulcast(wjim)
        await settleUntil({ model.toast != nil }, "the resolve")
        XCTAssertEqual("no radio-browser match for WJIM", model.toast,
                       "the server's own message, not a sentence this app made up")
    }

    // MARK: - what is never asked for

    /// §6: this screen has no FCC import, so nothing it does may ever reach
    /// that route. A model with a method for it would be a control waiting to
    /// be drawn.
    func testNothingEverAsksForTheAdminOnlyFccImport() async throws {
        let (model, house, _, _) = rig()
        await started(model)
        model.submit(override: "Catalogue")
        await settleUntil({ model.results.state.loaded })
        model.switchScope(.fm)
        model.submit()
        await settleUntil({ model.results.state.loaded })
        model.toggleDetail(try XCTUnwrap(model.favorites.state.items.first))
        await nothingHappens()

        XCTAssertTrue(
            house.seen.current.allSatisfy { !$0.contains("fcc") },
            "a phone has no admin session, so the import would 401 every time: \(house.seen.current)"
        )
    }

    // MARK: - lifecycle

    /// The push the plugin's manifest declares. A list that ignored it would
    /// show a station somebody favourited on the dashboard only after a
    /// reopen.
    func testAStationsChangedPushRereadsTheThreeLists() async throws {
        let (model, house, _, container) = rig()
        await started(model)
        let before = house.seen.current.count

        container.stateEventArrived(StateEvent(type: "radio.stations.changed", frame: "{}"))
        await settleUntil({ house.seen.current.count > before + 2 }, "the re-reads")
        XCTAssertTrue(house.requests(to: "radio/badge").count >= 2)
        XCTAssertTrue(house.requests(to: "radio/recent").count >= 2)

        let unrelated = house.seen.current.count
        container.stateEventArrived(StateEvent(type: "music.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(unrelated, house.seen.current.count, "and nothing else re-reads this screen")
    }

    /// Moving house drops everything the old one's: the search, the typed
    /// query, the open sheet and the busy flags.
    func testMovingHouseDropsEveryThingTheOldOneHad() async throws {
        let (model, _, store, _) = rig()
        await started(model)
        model.submit(override: "Catalogue")
        await settleUntil({ !model.results.state.items.isEmpty })
        model.toggleDetail(try XCTUnwrap(model.favorites.state.items.first))
        model.typed("Kestrel")
        await settleUntil({ !model.favoriteMatches.isEmpty })

        _ = store.setServerURL(otherServer)
        model.serverChanged()

        XCTAssertEqual("", model.query)
        XCTAssertEqual("", model.asked)
        XCTAssertFalse(model.submitted)
        XCTAssertTrue(model.favoriteMatches.isEmpty)
        XCTAssertNil(model.selected, "a station id is the HOUSE's id: two houses can both have a 4")
        XCTAssertNil(model.detail)
        XCTAssertEqual(.online, model.scope)
    }
}
