import Foundation
import XCTest
@testable import DomovoiKit

/// "play in a room" (Radio 1.5.0): the version gate, the rest of the gate,
/// what a refusal says, the missing-route backstop, and the request itself.
/// Android's `StationRoomPlayTest.kt` is the spec for every sentence here.
@MainActor
final class StationsRoomPlayTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the manifest

    private static func caps(version: String?, publisher: String? = codersFarm, slug: String = "radio") -> Capabilities {
        Capabilities(plugins: [
            CapabilityPlugin(
                slug: slug,
                version: version,
                name: "Radio Stations",
                publisher: publisher,
                appCapabilities: [capStations]
            ),
        ])
    }

    // MARK: - the version gate

    func testTheVersionIsComparedAsNumbers() {
        let min = StationsRoomPlay.minimumRadioVersion
        XCTAssertEqual(min, "1.5.0")
        XCTAssertFalse(StationsRoomPlay.versionAtLeast(nil, min), "absent is not new enough")
        XCTAssertFalse(StationsRoomPlay.versionAtLeast("", min))
        XCTAssertFalse(StationsRoomPlay.versionAtLeast("1.4.9", min))
        XCTAssertTrue(StationsRoomPlay.versionAtLeast("1.5.0", min))
        XCTAssertTrue(StationsRoomPlay.versionAtLeast("1.5", min), "a missing part counts as zero")
        XCTAssertTrue(StationsRoomPlay.versionAtLeast("1.10.0", min), "1.10 is newer than 1.5, numerically")
        XCTAssertTrue(StationsRoomPlay.versionAtLeast("2.0.0", min))
        XCTAssertFalse(StationsRoomPlay.versionAtLeast("0.99.99", min))
        XCTAssertTrue(StationsRoomPlay.versionAtLeast("1.5.0-rc1", min), "a pre-release suffix is dropped, as Android does")
        for junk in ["junk", "v1.5.0", "1..5", "1.5.x", "1.5.0.", ".1.5", "1,5,0", "１.５.０"] {
            XCTAssertFalse(StationsRoomPlay.versionAtLeast(junk, min), "\(junk) is not a version")
        }
    }

    func testTheGateIsOurRadioAtOneFiveOnAPairedPhoneNotInRoomModeWithARoom() {
        func offered(_ caps: Capabilities, paired: Bool = true, roomMode: Bool = false,
                     missing: Bool = false, rooms: [String] = ["kitchen"]) -> Bool {
            StationsRoomPlay.offered(capabilities: caps, paired: paired, roomMode: roomMode,
                                     routeMissing: missing, rooms: rooms)
        }
        XCTAssertTrue(offered(Self.caps(version: "1.5.0")))
        XCTAssertTrue(offered(Self.caps(version: "1.10.0")))
        XCTAssertFalse(offered(Self.caps(version: "1.4.9")))
        XCTAssertFalse(offered(Self.caps(version: nil)))
        XCTAssertFalse(offered(Self.caps(version: "junk")))
        XCTAssertFalse(offered(.empty), "no manifest, no control")
        XCTAssertFalse(offered(Self.caps(version: "1.5.0", publisher: "Someone Else")),
                       "a third-party radio is not ours, whatever its version")
        XCTAssertFalse(offered(Self.caps(version: "1.5.0", publisher: nil)), "no publisher fails closed")
        XCTAssertFalse(offered(Self.caps(version: "1.5.0"), paired: false), "never a guest or an unpaired phone")
        XCTAssertFalse(offered(Self.caps(version: "1.5.0"), roomMode: true), "never in room mode")
        XCTAssertFalse(offered(Self.caps(version: "1.5.0"), missing: true), "a missing route stays hidden")
        XCTAssertFalse(offered(Self.caps(version: "1.5.0"), rooms: []), "no rooms, nothing to pick")
    }

    // MARK: - the request

    func testThePathAndTheBody() throws {
        XCTAssertEqual(StationsPath.playInRoom(104), "/api/plugins/radio/stations/104/play-in-room")
        let body = StationsPath.playInRoomBody("kitchen")
        XCTAssertEqual(String(decoding: body, as: UTF8.self), #"{"room_id":"kitchen"}"#)
        let tricky = StationsPath.playInRoomBody(#"kid's "den""#)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: tricky) as? [String: Any])
        XCTAssertEqual(object["room_id"] as? String, #"kid's "den""#, "a room id is escaped, not pasted")
        XCTAssertEqual(object.count, 1)
    }

    // MARK: - what a refusal says

    private func http(_ status: Int, _ body: String) -> DomovoiError {
        let failure = ApiFailure(status: status, message: "\(status): \(body)", body: body)
        return status >= 500 ? .serverTrouble(failure) : .httpError(failure)
    }

    func testEachStatusHasItsSentenceWhenTheServerSendsNone() {
        XCTAssertEqual(StationsRoomPlay.failureLine(http(404, "{}")), "That room or station is gone.")
        XCTAssertEqual(StationsRoomPlay.failureLine(http(409, "{}")), "That station can't play right now.")
        XCTAssertEqual(StationsRoomPlay.failureLine(http(502, "{}")), "The room's player didn't take it.")
        XCTAssertEqual(StationsRoomPlay.failureLine(http(503, "{}")), "Couldn't reach the room. Try again.")
    }

    func testTheServersOwnDetailWins() {
        XCTAssertEqual(
            StationsRoomPlay.failureLine(http(409, #"{"detail":"The internet is set to No, so online stations can't play."}"#)),
            "The internet is set to No, so online stations can't play."
        )
        XCTAssertEqual(StationsRoomPlay.failureLine(http(404, #"{"detail":"no room called attic"}"#)),
                       "no room called attic")
        XCTAssertEqual(StationsRoomPlay.failureLine(http(502, #"{"detail":"kitchen's player said no"}"#)),
                       "kitchen's player said no")
    }

    func testAnythingElseIsTheOrdinaryFailureLine() {
        XCTAssertEqual(StationsRoomPlay.failureLine(.unreachable("timed out")), "play failed (offline?)")
        XCTAssertEqual(StationsRoomPlay.failureLine(http(418, "{}")), "play failed: 418: {}")
    }

    /// A plugin older than the route: a bare 404 or 405, or the framework's
    /// own "Not Found" / "Method Not Allowed". A 404 with a sentence of its
    /// own is an unknown station or room, **not** a missing route.
    func testTheMissingRouteBackstop() {
        XCTAssertTrue(StationsRoomPlay.isMissingRoute(http(404, "")))
        XCTAssertTrue(StationsRoomPlay.isMissingRoute(http(404, "<html>nope</html>")))
        XCTAssertTrue(StationsRoomPlay.isMissingRoute(http(405, "")))
        XCTAssertTrue(StationsRoomPlay.isMissingRoute(http(404, #"{"detail":"Not Found"}"#)))
        XCTAssertTrue(StationsRoomPlay.isMissingRoute(http(405, #"{"detail":"Method Not Allowed"}"#)))
        XCTAssertFalse(StationsRoomPlay.isMissingRoute(http(404, #"{"detail":"no such station"}"#)))
        XCTAssertFalse(StationsRoomPlay.isMissingRoute(http(404, "{}")), "a JSON body is a route that answered")
        XCTAssertFalse(StationsRoomPlay.isMissingRoute(http(409, "")))
        XCTAssertFalse(StationsRoomPlay.isMissingRoute(.unreachable("timed out")))
        XCTAssertEqual(StationsRoomPlay.failureLine(http(404, "")), "This server's Radio can't play in a room yet.")
        XCTAssertEqual(StationsRoomPlay.failureLine(http(405, #"{"detail":"Method Not Allowed"}"#)),
                       "This server's Radio can't play in a room yet.")
    }

    func testTheSuccessToastIsTheServersText() {
        let said = StationsRoomPlayAnswer(ok: true, roomID: "kitchen", stationID: 104,
                                          stationName: "Kestrel", text: "Playing Kestrel in the kitchen.")
        XCTAssertEqual(StationsRoomPlay.successLine(said, station: "Kestrel", room: "kitchen"),
                       "Playing Kestrel in the kitchen.")
        let silent = StationsRoomPlayAnswer(ok: true, roomID: "kitchen", stationID: 104, stationName: "Kestrel FM", text: " ")
        XCTAssertEqual(StationsRoomPlay.successLine(silent, station: "Kestrel", room: "office"),
                       "playing Kestrel FM in kitchen", "the server's names, when it sent no sentence")
    }

    // MARK: - the model, against a stub house

    private final class House: Sendable {
        /// What `play-in-room` answers.
        let status = Shared<Int>(200)
        let body = Shared<String>(#"{"ok":true,"room_id":"kitchen","station":{"id":4,"name":"Kestrel"},"text":"Playing Kestrel in kitchen."}"#)
        let asked = Shared<[String]>([])

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let method = request.httpMethod ?? "GET"
                if parts.path == "/api/health" {
                    let challenge = parts.queryItems?.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                asked.mutate { $0.append("\(method) \(parts.path)") }
                switch (method, parts.path) {
                case ("GET", "/api/music/now-playing"):
                    return .json(#"[{"room_id":"kitchen","state":"stop"},{"room_id":"office","state":"play"}]"#)
                case ("GET", "/api/plugins/radio/recent"):
                    return .json("[]")
                case ("POST", "/api/plugins/radio/stations/4/play-in-room"):
                    return .json(body.current, status: status.current)
                default:
                    return .json("[]")
                }
            }
        }
    }

    private func rig(
        _ house: House = House(),
        backstop: StationsRoomPlayBackstop = StationsRoomPlayBackstop()
    ) -> StationsModel {
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
        let immediate: @Sendable (Int64) async throws -> Void = { _ in }
        return StationsModel(
            in: container,
            playback: StationsPlaybackSpy(),
            roomPlayBackstop: backstop,
            wait: immediate,
            clock: { 1_793_613_840_000 }
        )
    }

    private func gate(version: String? = "1.5.0", paired: Bool = true, roomMode: Bool = false) -> StationsRoomPlayGate {
        StationsRoomPlayGate(server: server, capabilities: Self.caps(version: version), paired: paired, roomMode: roomMode)
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
        XCTFail("\(what) never settled", file: file, line: line)
    }

    private static let kestrel = StationsRow(id: 4, name: "Kestrel")

    func testAClosedGateAsksNothingAboutRooms() async {
        let house = House()
        let model = rig(house)
        model.start()
        model.use(roomGate: gate(version: "1.4.9"))
        for _ in 0..<500 { await Task.yield() }
        XCTAssertNil(model.rooms, "a screen that will never offer the control reads no rooms")
        XCTAssertFalse(house.asked.current.contains("GET /api/music/now-playing"))
        XCTAssertFalse(model.offersRoomPlay(Self.kestrel))
        model.stop()
    }

    func testAnOpenGateReadsTheRoomsAndPlaysInOne() async throws {
        let house = House()
        let model = rig(house)
        model.start()
        model.use(roomGate: gate())
        await settleUntil({ !model.roomChoices.isEmpty }, "the rooms")
        XCTAssertEqual(model.roomChoices, ["kitchen", "office"])
        XCTAssertTrue(model.offersRoomPlay(Self.kestrel))
        XCTAssertFalse(model.offersRoomPlay(StationsRow(id: 0, name: "a directory hit")),
                       "an unsaved hit has no id for the route")

        model.pickRoom(for: Self.kestrel)
        XCTAssertEqual(model.roomPick?.id, 4)
        model.playInRoom(Self.kestrel, room: "kitchen")
        XCTAssertNil(model.roomPick, "the picker closes on the tap")
        await settleUntil({ model.toast != nil }, "the toast")
        XCTAssertEqual(model.toast, "Playing Kestrel in kitchen.")

        let sent = try XCTUnwrap(StubURLProtocol.recorded.last {
            $0.url?.path == "/api/plugins/radio/stations/4/play-in-room"
        })
        XCTAssertEqual(sent.httpMethod, "POST")
        XCTAssertEqual(sent.value(forHTTPHeaderField: "X-Requested-With"), "DomovoiApp")
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(sent.httpBody)) as? [String: Any])
        XCTAssertEqual(object["room_id"] as? String, "kitchen")
        model.stop()
    }

    func testARoomTheHouseNeverReportedIsNotSent() async {
        let house = House()
        let model = rig(house)
        model.start()
        model.use(roomGate: gate())
        await settleUntil({ !model.roomChoices.isEmpty }, "the rooms")
        model.playInRoom(Self.kestrel, room: "attic")
        for _ in 0..<500 { await Task.yield() }
        XCTAssertFalse(house.asked.current.contains { $0.hasSuffix("/play-in-room") })
        model.stop()
    }

    func testARefusalSaysTheStatusSentence() async {
        let house = House()
        house.status.set(503)
        house.body.set("{}")
        let model = rig(house)
        model.start()
        model.use(roomGate: gate())
        await settleUntil({ !model.roomChoices.isEmpty }, "the rooms")
        model.playInRoom(Self.kestrel, room: "office")
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(model.toast, "Couldn't reach the room. Try again.")
        XCTAssertTrue(model.offersRoomPlay(Self.kestrel), "a 503 is not a missing route")
        model.stop()
    }

    /// A bare 404 hides the control for this server, and the hide outlives
    /// the screen's model: the next visit builds a new one over the same
    /// backstop and offers nothing.
    func testABare404HidesItForThisServer() async {
        let house = House()
        house.status.set(404)
        house.body.set("")
        let backstop = StationsRoomPlayBackstop()
        let model = rig(house, backstop: backstop)
        model.start()
        model.use(roomGate: gate())
        await settleUntil({ !model.roomChoices.isEmpty }, "the rooms")
        model.playInRoom(Self.kestrel, room: "kitchen")
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(model.toast, "This server's Radio can't play in a room yet.")
        XCTAssertTrue(backstop.missingOn(server))
        XCTAssertFalse(model.offersRoomPlay(Self.kestrel))
        XCTAssertEqual(model.roomChoices, [])
        model.stop()

        let again = rig(house, backstop: backstop)
        again.start()
        again.use(roomGate: gate())
        XCTAssertFalse(again.offersRoomPlay(Self.kestrel), "hidden for this server session, not this visit")
        again.stop()
    }

    func testRoomModeAndAnUnpairedPhoneAreNeverOffered() async {
        let house = House()
        let model = rig(house)
        model.start()
        model.use(roomGate: gate(roomMode: true))
        XCTAssertFalse(model.roomPlayPossible)
        model.use(roomGate: gate(paired: false))
        XCTAssertFalse(model.roomPlayPossible)
        for _ in 0..<500 { await Task.yield() }
        XCTAssertNil(model.rooms)
        model.stop()
    }
}
