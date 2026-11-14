import XCTest
@testable import DomovoiKit

/// Play next / Add to queue (`QueueAdder.kt`) and the queue's search box
/// (`QueueAddBox.kt`) against a house that is not a house.
@MainActor
final class QueueAdderTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let phoneID = "ios-ab12cd34ef567890"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// This phone's queue, by hand.
    private final class FakePhone: PhoneQueueSink {
        var hasQueue = false
        var castsTo: String?
        var enqueued: [[Int64]] = []
        var nexted: [[Int64]] = []
        func enqueue(_ tracks: [LibraryTrack]) { enqueued.append(tracks.map(\.id)) }
        func playNext(_ tracks: [LibraryTrack]) { nexted.append(tracks.map(\.id)) }
    }

    private final class House: Sendable {
        let genresStatus = Shared(200)
        let addStatus = Shared(200)

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let path = parts.path
                if path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                let query = Dictionary(uniqueKeysWithValues: (parts.queryItems ?? []).map { ($0.name, $0.value ?? "") })
                if path == "/api/music/library", query["genre"] != nil {
                    let rows = (1...40).map { #"{"id":\#($0),"title":"g\#($0)"}"# }.joined(separator: ",")
                    return .json(#"{"items":[\#(rows)],"total":40}"#)
                }
                if path == "/api/music/library" {
                    return .json(#"{"items":[{"id":4,"title":"Hey Jude","artist":"The Beatles"}],"total":1}"#)
                }
                if path == "/api/music/genres" {
                    guard self.genresStatus.current == 200 else { return .json(#"{"detail":"no"}"#, status: 500) }
                    return .json(#"{"items":[{"key":"rock","name":"Rock","track_count":40}],"total":1,"limit":2}"#)
                }
                if path.hasSuffix("/add") {
                    guard self.addStatus.current == 200 else {
                        return .json(#"{"detail":"this room is not taking requests"}"#, status: 403)
                    }
                    return .json(#"{"queued":[{"song_id":41,"file":"a"},{"song_id":42,"file":"b"}]}"#)
                }
                return .json("{}", status: 404)
            }
        }
    }

    private func rig(_ house: House = House()) -> AppContainer {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        return AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
    }

    private func adds() -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path.hasSuffix("/add") == true }
    }

    private func fields(_ request: URLRequest?) throws -> [String: Any] {
        let data = try XCTUnwrap(request?.httpBody, "nothing was sent")
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private let song = LibraryTrack(id: 4, title: "Hey Jude", artist: "The Beatles")

    /// Play next to a room is **one** add with `where: next` — no move chain.
    func testQueues_PlayNextInARoomIsOneAddWithWhereNext() async throws {
        let container = rig()
        let adder = QueueAdder(client: container.client, deviceID: phoneID, phone: FakePhone())

        let said = await adder.song(.room("den"), track: song, next: true)

        XCTAssertEqual(#"playing next in den: "Hey Jude""#, said)
        XCTAssertEqual(1, adds().count)
        let body = try fields(adds().first)
        XCTAssertEqual("next", body["where"] as? String)
        XCTAssertEqual([4], body["track_ids"] as? [Int])
        XCTAssertEqual(phoneID, body["device_id"] as? String)
        XCTAssertTrue(StubURLProtocol.recorded.filter { $0.url?.path.hasSuffix("/move") == true }.isEmpty)
    }

    /// While this phone casts to **that** room and keeps a queue for it, its
    /// own queue gets the song too; never an empty one, never another room's.
    func testQueues_ARoomAddMirrorsIntoThePhonesQueueOnlyWhileCastingThere() async {
        let container = rig()
        let phone = FakePhone()
        let adder = QueueAdder(client: container.client, deviceID: phoneID, phone: phone)

        phone.castsTo = "den"
        phone.hasQueue = false
        _ = await adder.song(.room("den"), track: song, next: false)
        XCTAssertTrue(phone.enqueued.isEmpty, "an empty queue would start the phone playing")

        phone.hasQueue = true
        phone.castsTo = "kitchen"
        _ = await adder.song(.room("den"), track: song, next: false)
        XCTAssertTrue(phone.enqueued.isEmpty, "casting elsewhere")

        phone.castsTo = "den"
        _ = await adder.song(.room("den"), track: song, next: true)
        XCTAssertEqual([[4]], phone.nexted)
    }

    func testQueues_ThePhonesOwnQueueIsAskedNothingOfTheServer() async {
        let container = rig()
        let phone = FakePhone()
        let adder = QueueAdder(client: container.client, deviceID: phoneID, phone: phone)

        let said = await adder.song(.phone, track: song, next: false)

        XCTAssertEqual(#"queued "Hey Jude""#, said)
        XCTAssertEqual([[4]], phone.enqueued)
        XCTAssertTrue(adds().isEmpty)
    }

    func testQueues_ARoomsRefusalIsSaidInItsOwnWords() async {
        let house = House()
        house.addStatus.set(403)
        let container = rig(house)
        let adder = QueueAdder(client: container.client, deviceID: phoneID, phone: nil)

        let said = await adder.song(.room("den"), track: song, next: false)

        XCTAssertEqual(#"Couldn't add "Hey Jude": this room is not taking requests"#, said)
    }

    /// A genre row adds a shuffled 25 of its songs, and the room's count is
    /// what the room said it took.
    func testQueues_AGenreRowAddsTwentyFive() async throws {
        let container = rig()
        let client = container.client
        let adder = QueueAdder(
            phone: nil,
            room: RoomQueueApi(client: client, deviceID: phoneID),
            genreSongs: { (key: String) async throws(DomovoiError) -> [LibraryTrack] in
                try await client.get(QueueAsk.genreSongsPath(key), as: Page<LibraryTrack>.self).items
            },
            shuffler: { $0 }
        )

        let said = await adder.add(.room("den"), find: .genre(QueueGenre(key: "rock", name: "Rock", trackCount: 40)), next: false)

        XCTAssertEqual("Added in den: Rock songs (2 songs)", said, "the room's own count")
        let ids = try XCTUnwrap(try fields(adds().first)["track_ids"] as? [Int])
        XCTAssertEqual(Array(1...25), ids)
    }

    // MARK: - the search box

    func testQueues_TheBoxSearchesSongsAndGenres() async {
        let container = rig()
        let search = QueueSearchModel(
            target: .room("den"),
            container: container,
            adder: QueueAdder(client: container.client, deviceID: phoneID, phone: nil),
            prompt: "Feed me a song…",
            debounceMs: 0
        )
        search.update("h")
        XCTAssertNil(search.term)
        search.update("hey")
        await search.settle()

        XCTAssertEqual(["s4", "grock"], search.rows.map(\.id))
        XCTAssertNil(search.nothingNote)
        XCTAssertEqual("", search.line)
    }

    /// A genre read that fails only loses the genre rows.
    func testQueues_AFailedGenreReadLosesOnlyTheGenres() async {
        let house = House()
        house.genresStatus.set(500)
        let container = rig(house)
        let search = QueueSearchModel(
            target: .phone,
            container: container,
            adder: QueueAdder(client: container.client, deviceID: phoneID, phone: nil),
            debounceMs: 0
        )
        search.update("hey")
        await search.settle()

        XCTAssertEqual(["s4"], search.rows.map(\.id))
    }

    /// Rows answer only the term typed now: a shorter one hides them.
    func testQueues_ClearingTheBoxDropsItsRows() async {
        let container = rig()
        let search = QueueSearchModel(
            target: .phone,
            container: container,
            adder: QueueAdder(client: container.client, deviceID: phoneID, phone: nil),
            debounceMs: 0
        )
        search.update("hey")
        await search.settle()
        XCTAssertFalse(search.rows.isEmpty)
        search.clear()
        XCTAssertTrue(search.rows.isEmpty)
        XCTAssertNil(search.finds)
    }
}
