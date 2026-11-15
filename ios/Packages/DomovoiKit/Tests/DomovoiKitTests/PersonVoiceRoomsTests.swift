import Foundation
import XCTest
@testable import DomovoiKit

/// Rooms that know this voice (`GET /api/people/{id}/voice-rooms`, 1.3.0),
/// on the person page's profile tab.
///
/// The first half mirrors Android's `PersonVoiceRoomsTest` one case for one
/// case; the second is the model's read: alongside the five lists, quiet on a
/// 404, keeping what is shown on any other failure, re-read on refresh and
/// when the sample count moves, and never landing under the wrong person or
/// the wrong house ([[read-after-await-wrong-key]]).
@MainActor
final class PersonVoiceRoomsTests: XCTestCase {

    private let kitchen = PersonVoiceRoom(roomID: "kitchen", current: true)
    private let office = PersonVoiceRoom(roomID: "office", legacy: true)
    private let garage = PersonVoiceRoom(roomID: "garage")

    private func rooms(_ json: String) -> [PersonVoiceRoom] {
        PersonVoiceRooms.read(Data(json.utf8))
    }

    private static func hintFor(_ who: String, _ said: String) -> String {
        "Rooms recognize a voice best once they have heard it themselves. In each room "
            + "marked \u{201C}not yet\u{201D}, \(who) can say \u{201C}my name is \(said)\u{201D} and answer yes."
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - Android's PersonVoiceRoomsTest

    func testParsesEveryRoomWithMissingFlagsAsFalse() {
        let parsed = rooms(
            """
            [
              {"room_id":"kitchen","current":true,"legacy":false},
              {"room_id":"office","legacy":true},
              {"room_id":"garage"},
              {"room_id":"den","current":null,"legacy":true,"extra":1}
            ]
            """
        )
        XCTAssertEqual(
            parsed,
            [kitchen, office, garage, PersonVoiceRoom(roomID: "den", legacy: true)]
        )
        XCTAssertEqual(parsed.map(\.known), [true, true, false, true])
    }

    func testDropsRowsThatAreNotRooms() {
        XCTAssertEqual(
            rooms(#"[{"current":true}, {"room_id":""}, {"room_id":"  "}, 7, null, {"room_id":"kitchen","current":true}]"#),
            [kitchen]
        )
        XCTAssertEqual(rooms(#"{"detail":"Not Found"}"#), [])
        XCTAssertEqual(rooms("[]"), [])
        XCTAssertEqual(rooms("not json"), [])
    }

    /// A flag that is a number is not a boolean: Android's decoder fails that
    /// row, so it is dropped here too, and `1` is never read as `true` (the
    /// `NSNumber`-bridges-to-`Bool` trap).
    func testAFlagThatIsNotABooleanDropsItsRow() {
        XCTAssertEqual(
            rooms(#"[{"room_id":"attic","current":1}, {"room_id":"garage"}]"#),
            [garage]
        )
    }

    /// The server's own order (it sorts by room name; a phone never re-sorts).
    func testKeepsTheServersOrder() {
        XCTAssertEqual(
            rooms(#"[{"room_id":"office","legacy":true},{"room_id":"garage"},{"room_id":"kitchen","current":true}]"#)
                .map(\.roomID),
            ["office", "garage", "kitchen"]
        )
    }

    func testCountIsKnownOverTotal() {
        XCTAssertEqual(PersonVoiceRooms.count([kitchen, office, garage]), "2/3")
        XCTAssertEqual(PersonVoiceRooms.count([garage]), "0/1")
        XCTAssertEqual(PersonVoiceRooms.count([kitchen, office]), "2/2")
    }

    func testChipsMarkOnlyRoomsWithNeitherSample() {
        XCTAssertEqual(PersonVoiceRooms.chip(kitchen), "kitchen")
        XCTAssertEqual(PersonVoiceRooms.chip(office), "office")
        XCTAssertEqual(PersonVoiceRooms.chip(garage), "garage \u{00B7} not yet")
        XCTAssertEqual(PeopleStrings.voiceRoomsLabel, "rooms that know this voice")
    }

    func testHintOnlyWhenARoomIsNotYet() {
        XCTAssertNil(PersonVoiceRooms.hint(name: "Priya", rooms: [kitchen, office]))
        XCTAssertEqual(
            PersonVoiceRooms.hint(name: "Priya", rooms: [kitchen, garage]),
            Self.hintFor("Priya", "Priya")
        )
        // Trimmed, as Android's `name.trim()` is.
        XCTAssertEqual(
            PersonVoiceRooms.hint(name: " Priya ", rooms: [garage]),
            Self.hintFor("Priya", "Priya")
        )
    }

    func testHintWithoutANameSaysTheyAndAnEllipsis() {
        XCTAssertEqual(
            PersonVoiceRooms.hint(name: "  ", rooms: [garage]),
            Self.hintFor("they", "\u{2026}")
        )
    }

    func testHiddenWhenThereAreNoRooms() {
        XCTAssertFalse(PersonVoiceRooms.shows([]))
        XCTAssertTrue(PersonVoiceRooms.shows([garage]))
    }

    func testAServerWithoutTheRouteHidesTheSectionQuietly() async {
        let loaded = await PersonVoiceRooms.load {
            throw DomovoiError.httpError(
                ApiFailure(status: 404, message: #"404: {"detail":"Not Found"}"#)
            )
        }
        XCTAssertEqual(loaded, [])
    }

    func testOtherFailuresKeepWhatIsShown() async {
        let trouble = await PersonVoiceRooms.load {
            throw DomovoiError.serverTrouble(ApiFailure(status: 500, message: "500: boom"))
        }
        XCTAssertNil(trouble)
        let unreachable = await PersonVoiceRooms.load {
            throw DomovoiError.unreachable("Failed to connect to 192.168.1.20:6369")
        }
        XCTAssertNil(unreachable)
    }

    func testAnAnswerIsParsed() async {
        let loaded = await PersonVoiceRooms.load {
            Data(#"[{"room_id":"kitchen","current":true},{"room_id":"garage"}]"#.utf8)
        }
        XCTAssertEqual(loaded, [kitchen, garage])
    }

    // MARK: - the model's read

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    private let priya = Person(id: 1, name: "Priya", voiceProfileCount: 2)
    private let jordan = Person(id: 2, name: "Jordan", voiceProfileCount: 1)

    /// The house: person 1 has kitchen (current) and garage (not yet); person
    /// 2 has the office only. Every other read answers empty.
    private final class House: Sendable {
        /// `nil` answers the rows; a status answers that failure instead.
        let roomsStatus = Shared<Int?>(nil)

        static let priyaRooms = #"[{"room_id":"kitchen","current":true},{"room_id":"garage"}]"#
        static let jordanRooms = #"[{"room_id":"office","legacy":true}]"#

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if parts.path.hasSuffix("/voice-rooms") {
                if let status = roomsStatus.current {
                    return .json(#"{"detail":"Not Found"}"#, status: status)
                }
                switch parts.path {
                case "/api/people/1/voice-rooms": return .json(Self.priyaRooms)
                case "/api/people/2/voice-rooms": return .json(Self.jordanRooms)
                default: return .json("[]")
                }
            }
            return parts.path.hasSuffix("/preferences") ? .json("{}") : .json("[]")
        }

        func install() {
            StubURLProtocol.dispatch { request in self.answer(request) }
        }
    }

    private func rig() -> (PersonDetailModel, House, InMemoryShellStore) {
        let house = House()
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
        return (PersonDetailModel(in: container, clock: { 0 }), house, store)
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

    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    private func roomReads(_ personID: Int) -> Int {
        StubURLProtocol.recorded.filter { $0.url?.path == "/api/people/\(personID)/voice-rooms" }.count
    }

    private func readsSince(_ mark: Int) -> [String] {
        Array(StubURLProtocol.recorded.dropFirst(mark)).compactMap { $0.url?.path }
            .filter { $0.hasPrefix("/api/people/") }
    }

    func testShowingSomebodyReadsTheirRoomsAlongsideTheFiveLists() async {
        let (model, _, _) = rig()
        model.show(priya)
        await settleUntil({ !model.loading && !model.voiceRooms.isEmpty }, "the reads")

        XCTAssertEqual(model.voiceRooms, [kitchen, garage])
        XCTAssertEqual(roomReads(1), 1)
        XCTAssertNil(model.toast)
        XCTAssertEqual(model.errors, .none)
    }

    /// A 404 (a server from before 1.3.0) is no rooms: the section that was
    /// shown goes away, and nothing is said.
    func testAServerWithoutTheRouteHidesTheRoomsAndSaysNothing() async {
        let (model, house, _) = rig()
        model.show(priya)
        await settleUntil({ !model.loading && !model.voiceRooms.isEmpty }, "the first read")

        house.roomsStatus.set(404)
        model.refresh()
        await settleUntil({ !model.loading && model.voiceRooms.isEmpty }, "the 404")
        await nothingHappens()

        XCTAssertEqual(model.voiceRooms, [])
        XCTAssertNil(model.toast, "the rooms' 404 raised a toast")
        XCTAssertEqual(model.errors, .none)
    }

    /// Any other failure keeps what is shown, and is never a toast.
    func testAnotherFailureKeepsTheRoomsShownAndSaysNothing() async {
        let (model, house, _) = rig()
        model.show(priya)
        await settleUntil({ !model.loading && !model.voiceRooms.isEmpty }, "the first read")

        house.roomsStatus.set(500)
        model.refresh()
        await settleUntil({ roomReads(1) == 2 && !model.loading }, "the failed re-read")
        await nothingHappens()

        XCTAssertEqual(model.voiceRooms, [kitchen, garage], "a failed re-read blanked the rooms")
        XCTAssertNil(model.toast, "the rooms' failure raised a toast")
    }

    func testRefreshReReadsTheRooms() async {
        let (model, _, _) = rig()
        model.show(priya)
        await settleUntil({ !model.loading && !model.voiceRooms.isEmpty }, "the first read")

        model.refresh()
        await settleUntil({ roomReads(1) == 2 }, "the rooms' re-read")
        XCTAssertEqual(roomReads(1), 2)
    }

    /// The same person with a new sample count (a room just learned them)
    /// re-reads the rooms, and only the rooms; the same count reads nothing.
    func testANewSampleCountReReadsOnlyTheRooms() async {
        let (model, _, _) = rig()
        model.show(priya)
        await settleUntil({ !model.loading && !model.voiceRooms.isEmpty }, "the first read")

        var mark = StubURLProtocol.recorded.count
        model.show(Person(id: 1, name: "Priya", lastSeenAt: "2026-11-14T10:00:00Z", voiceProfileCount: 2))
        await nothingHappens()
        XCTAssertEqual(readsSince(mark), [], "the same sample count read something")

        mark = StubURLProtocol.recorded.count
        model.show(Person(id: 1, name: "Priya", voiceProfileCount: 3))
        await settleUntil({ !self.readsSince(mark).isEmpty }, "the rooms' re-read")
        await nothingHappens()
        XCTAssertEqual(readsSince(mark), ["/api/people/1/voice-rooms"])
        XCTAssertEqual(model.person?.voiceProfileCount, 3)
    }

    /// Person 1's rooms must never land under person 2: the switch happens
    /// **inside** person 1's parked rooms read.
    ///
    /// Person 2's own rooms read fails (500), which keeps what is shown — so
    /// person 1's answer, released after the switch, is the only thing that
    /// could put rooms on person 2's page. Released before person 2's reads
    /// can settle: the stub answers one request at a time, and person 2's
    /// queue behind the park.
    func testSwitchingPersonInsideTheRoomsRoundTripKeepsThemStraight() async {
        let (model, house, _) = rig()
        let park = parkRequest(
            to: "/api/people/1/voice-rooms",
            answering: { .json(House.priyaRooms) },
            otherwise: { request in
                request.url?.path == "/api/people/2/voice-rooms"
                    ? .json(#"{"detail":"down"}"#, status: 500)
                    : house.answer(request)
            }
        )

        model.show(priya)
        await settleUntil({ park.arrived.current }, "person 1's rooms read")
        model.show(jordan)
        park.release.set(true)
        await settleUntil({ !model.loading && self.roomReads(2) == 1 }, "person 2's reads")
        await nothingHappens()

        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")
        XCTAssertEqual(model.person?.id, 2)
        XCTAssertEqual(model.voiceRooms, [], "person 1's rooms landed under person 2")
    }

    /// The same for the house: an answer from the server the phone has left
    /// is not shown under the one it moved to.
    func testMovingHouseInsideTheRoomsRoundTripShowsNoneOfTheOldHouse() async {
        let (model, house, store) = rig()
        let park = parkRequest(
            to: "/api/people/1/voice-rooms",
            answering: { .json(House.priyaRooms) },
            otherwise: { house.answer($0) }
        )

        model.show(priya)
        await settleUntil({ park.arrived.current }, "person 1's rooms read")
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        park.release.set(true)
        await settleUntil({ !model.loading }, "the five reads")
        await nothingHappens()

        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")
        XCTAssertEqual(model.voiceRooms, [], "the old house's rooms are shown in the new one")
    }
}
