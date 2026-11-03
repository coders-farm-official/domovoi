import XCTest
@testable import DomovoiKit

/// Where a podcast or a book was left off: the two reads, the two writes, and
/// the read-after-await the whole file is really for.
@MainActor
final class PlayerPositionsTests: XCTestCase {

    private var house = PlayerHouse()

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        house = PlayerHouse()
        house.install()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    // MARK: - the routes

    func testOnlySpokenMediaHasAPlaceAtAll() {
        XCTAssertEqual(
            PlayerServerPositions.path(for: PlayerSeed.episode), "/api/podcasts/positions/9"
        )
        XCTAssertEqual(
            PlayerServerPositions.path(for: PlayerSeed.folderBook), "/api/audiobooks/2/position"
        )
        XCTAssertNil(PlayerServerPositions.path(for: PlayerSeed.transmission), "a song does not resume")
        XCTAssertNil(PlayerServerPositions.path(for: PlayerSeed.station))
        XCTAssertNil(PlayerServerPositions.path(for: PlayerSeed.phoneSong))
    }

    func testTheReadQueryCarriesTheDeviceAndThePersonWhenThereIsOne() {
        XCTAssertEqual(
            PlayerServerPositions.readQuery(deviceID: "phone-1", personID: nil),
            "?device_id=phone-1"
        )
        XCTAssertEqual(
            PlayerServerPositions.readQuery(deviceID: "phone-1", personID: "kamron"),
            "?device_id=phone-1&person_id=kamron"
        )
        XCTAssertEqual(
            PlayerServerPositions.readQuery(deviceID: "phone-1", personID: ""),
            "?device_id=phone-1",
            "an empty person is no person: the server keys the row by (device, person)"
        )
    }

    func testAnAmpersandInAnIdIsEscapedRatherThanSplittingTheQuery() {
        XCTAssertEqual(
            PlayerServerPositions.readQuery(deviceID: "a&b=c", personID: nil),
            "?device_id=a%26b%3Dc"
        )
    }

    // MARK: - the write body

    private func fields(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testThePositionIsSentAsAnIntBecauseThatIsWhatPositionSaveIsTypedAs() throws {
        let body = try fields(PlayerServerPositions.saveBody(
            deviceID: "phone-1", personID: nil, positionSec: 912.6, speed: 1.5
        ))
        XCTAssertEqual(body["device_id"] as? String, "phone-1")
        XCTAssertEqual(
            body["position_sec"] as? Int, 912,
            "podcasts.py:109 and audiobooks.py:78 type it int; a float there is a 422"
        )
        // Asserted on the BYTES, not on the parsed value: `JSONSerialization`
        // hands back an `NSNumber`, and `is Double` is true for the `912` in
        // `{"position_sec":912}` as well as for `912.6` — so the parsed check
        // above cannot tell them apart and that assertion would always pass.
        let wire = String(decoding: PlayerServerPositions.saveBody(
            deviceID: "phone-1", personID: nil, positionSec: 912.6, speed: 1.5
        ), as: UTF8.self)
        XCTAssertTrue(wire.contains("\"position_sec\":912"), wire)
        XCTAssertFalse(wire.contains("912.6"), "a float there is a 422: \(wire)")
        XCTAssertEqual(body["speed"] as? Double, 1.5)
        XCTAssertNil(body["person_id"], "absent, not null, when nobody is identified")
    }

    func testThePersonIsIncludedOnlyWhenThereIsOne() throws {
        let named = try fields(PlayerServerPositions.saveBody(
            deviceID: "phone-1", personID: "kamron", positionSec: 1, speed: 1
        ))
        XCTAssertEqual(named["person_id"] as? String, "kamron")
        let blank = try fields(PlayerServerPositions.saveBody(
            deviceID: "phone-1", personID: "", positionSec: 1, speed: 1
        ))
        XCTAssertNil(blank["person_id"])
    }

    func testAnUnmeasurablePositionIsSentAsZeroRatherThanNonsense() throws {
        for value in [Double.nan, -5, -Double.infinity] {
            let body = try fields(PlayerServerPositions.saveBody(
                deviceID: "p", personID: nil, positionSec: value, speed: 1
            ))
            XCTAssertEqual(body["position_sec"] as? Int, 0, "position \(value)")
        }
    }

    // MARK: - the read's shape

    func testTheServersAnswerDecodesIntoAPlace() throws {
        let read = try JSONDecoder().decode(
            PlayerPositionRead.self, from: Data(#"{"position_sec":912,"speed":1.5}"#.utf8)
        )
        XCTAssertEqual(read.asPosition.positionSec, 912)
        XCTAssertEqual(read.asPosition.speed, 1.5)
    }

    func testAnEmptyRowAnswersTheServersOwnDefault() throws {
        // `{"position_sec": 0, "speed": 1.0}` is written in the route
        // (`podcasts.py:531`), and the client's default must be the same one
        // or a house with no row would start every book at 1.0 but a slow
        // read at something else.
        let read = try JSONDecoder().decode(PlayerPositionRead.self, from: Data("{}".utf8))
        XCTAssertEqual(read.asPosition.positionSec, 0)
        XCTAssertEqual(read.asPosition.speed, 1.0)
        XCTAssertEqual(PlayerPosition.start.positionSec, 0)
        XCTAssertEqual(PlayerPosition.start.speed, 1.0)
    }

    // MARK: - through the model

    func testAPlaceIsReadFromTheServer() async {
        let h = PlayerHarness()
        house.podcastPosition.set(#"{"position_sec":912,"speed":1.25}"#)
        let place = await h.model.fetchPosition(PlayerSeed.episode)
        XCTAssertEqual(place.positionSec, 912)
        XCTAssertEqual(place.speed, 1.25)
        XCTAssertTrue(PlayerHouse.paths.contains("/api/podcasts/positions/9"))
    }

    func testASongHasNoPlaceToRead() async {
        let h = PlayerHarness()
        let before = PlayerHouse.paths.count
        let place = await h.model.fetchPosition(PlayerSeed.transmission)
        XCTAssertEqual(place, .start)
        XCTAssertEqual(PlayerHouse.paths.count, before, "and nothing is asked")
    }

    func testAFailedReadStartsAtTheTopWithoutClearingTheServersPlace() async {
        let h = PlayerHarness()
        // A body that is not a place at all.
        house.podcastPosition.set("not json")
        let place = await h.model.fetchPosition(PlayerSeed.episode)
        XCTAssertEqual(
            place, .start,
            "a read that fails must not lose the place by claiming zero loudly: it just starts at the top"
        )
        XCTAssertFalse(
            PlayerHouse.methods(for: "/api/podcasts/positions/9").contains("POST"),
            "and it writes nothing, so the server's row survives a bad read"
        )
    }

    /// **The read-after-await case.**
    ///
    /// The switch happens *inside* the round trip — the house runs
    /// `duringRead` while it is answering — which is the only arrangement that
    /// catches this. A test that switched the server before or after the
    /// `await` would pass against the broken code.
    func testAPlaceThatLandsAfterTheHouseChangedIsDroppedRatherThanApplied() async {
        let place = await readWithSwitch(
            to: PlayerServerScope(address: "http://10.0.0.99:6369", fingerprint: "BB")
        )
        XCTAssertEqual(
            place, .start,
            "912 s is the FIRST house's place for episode 9; applying it to the second house's "
            + "episode 9 is the top defect class on this track"
        )
    }

    func testAPlaceThatLandsWhileTheHouseIsStillTheSameOneIsKept() async {
        // The negative half. Without it the case above would also pass for a
        // `fetchPosition` that returned `.start` whatever happened — which is
        // the shape of a case that was never testing anything.
        let place = await readWithSwitch(
            to: PlayerServerScope(address: "http://192.168.0.9:6369", fingerprint: "AA")
        )
        XCTAssertEqual(
            place.positionSec, 912,
            "the same house, found at a new address: nothing is dropped for that"
        )
    }

    /// Read episode 9's place, switching the shell to `to` **while the request
    /// is parked in the house**.
    ///
    /// The park is what makes the switch provably inside the round trip: the
    /// request has left, and its answer has not been applied.
    private func readWithSwitch(to second: PlayerServerScope) async -> PlayerPosition {
        let h = PlayerHarness()
        h.model.serverChanged(to: PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA"))
        house.podcastPosition.set(#"{"position_sec":912,"speed":1.25}"#)
        house.holdPosition.set(true)

        let reading = Task { @MainActor in await h.model.fetchPosition(PlayerSeed.episode) }
        await h.settle(
            until: { self.house.positionArrivals.current > 0 },
            "the position request reaching the house"
        )
        h.model.serverChanged(to: second)
        house.release()
        return await reading.value
    }

    // MARK: - the save

    func testPausingASpokenItemFlushesItsPlace() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        h.engine.currentTimeSec = 300
        h.model.tickForTests()
        h.model.pause()
        await h.settle(
            until: { PlayerHouse.methods(for: "/api/podcasts/positions/9").contains("POST") },
            "the place being flushed on pause"
        )
        // The bytes that left, not a value this process happened to hold.
        let body = PlayerHouse.bodies(for: "/api/podcasts/positions/9").last
        XCTAssertEqual(body?["device_id"] as? String, "phone-1")
        XCTAssertEqual(body?["position_sec"] as? Int, 300)
        XCTAssertNil(body?["person_id"], "nobody is identified on this phone yet")
    }

    func testThePersonIsSentOnceTheHouseHasIdentifiedAListener() async {
        let h = PlayerHarness()
        h.model.listenerPersonID = "kamron"
        h.model.play([PlayerSeed.episode])
        h.engine.currentTimeSec = 90
        h.model.tickForTests()
        h.model.pause()
        await h.settle(
            until: { !PlayerHouse.bodies(for: "/api/podcasts/positions/9").isEmpty },
            "the place being flushed"
        )
        let body = PlayerHouse.bodies(for: "/api/podcasts/positions/9").last
        XCTAssertEqual(body?["person_id"] as? String, "kamron", "the server keys the row by (device, person)")
    }

    func testPausingASongFlushesNothing() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        let before = PlayerHouse.paths.count
        h.model.pause()
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(PlayerHouse.paths.count, before, "a song does not resume, so it has no place to save")
    }

    func testTheTenSecondTickerFlushesWhilePlaying() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        h.engine.status = .playing
        let before = PlayerHouse.methods(for: "/api/podcasts/positions/9").count
        // Twenty 500 ms ticks is ten seconds.
        for _ in 0..<19 { h.model.tickForTests() }
        XCTAssertEqual(
            PlayerHouse.methods(for: "/api/podcasts/positions/9").count, before,
            "nineteen ticks is not yet ten seconds"
        )
        h.model.tickForTests()
        await h.settle(
            until: { PlayerHouse.methods(for: "/api/podcasts/positions/9").count > before },
            "the ten-second save"
        )
    }

    func testThePausedTickerSavesNothing() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        h.engine.status = .readyToPlay
        let before = PlayerHouse.paths.count
        for _ in 0..<60 { h.model.tickForTests() }
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(PlayerHouse.paths.count, before, "nothing is playing, so nothing moved")
    }

    func testBackgroundingFlushesThePlace() async {
        // iOS kills the process without a reliable callback when the app is
        // swiped out of the switcher, so this is the last moment we are
        // certainly alive (`PlaybackService.onTaskRemoved`'s job).
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        h.model.noteEnteredBackground()
        await h.settle(
            until: { PlayerHouse.methods(for: "/api/podcasts/positions/9").contains("POST") },
            "the place being flushed on backgrounding"
        )
    }

    // MARK: - the Audiobooks seam

    func testWithAStorePluggedInTheServerSlotIsNotUsedForABook() async {
        let h = PlayerHarness()
        let store = FakePlayerPositionStore()
        h.model.positions = store
        let before = PlayerHouse.paths.count

        let place = await h.model.fetchPosition(PlayerSeed.folderBook)

        XCTAssertEqual(place.positionSec, 120, "the store's place, not the server slot's")
        XCTAssertEqual(place.speed, 1.5)
        XCTAssertEqual(
            PlayerHouse.paths.count, before,
            "one place per book across streaming and a saved copy is the Audiobooks item's job"
        )
    }

    func testAStoreThatDoesNotOwnAnItemLeavesItToTheServerSlot() async {
        let h = PlayerHarness()
        let store = FakePlayerPositionStore()
        store.owned = [.audiobook]
        h.model.positions = store
        house.podcastPosition.set(#"{"position_sec":44,"speed":1.0}"#)
        let place = await h.model.fetchPosition(PlayerSeed.episode)
        XCTAssertEqual(place.positionSec, 44, "a podcast is not the book store's")
    }

    func testTheStoreIsToldWhereABookGotTo() async {
        let h = PlayerHarness()
        let store = FakePlayerPositionStore()
        h.model.positions = store
        h.model.serverChanged(to: PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA"))
        h.model.play([PlayerSeed.folderBook])
        h.engine.currentTimeSec = 40
        h.model.tickForTests()
        h.model.pause()
        await h.settle(until: { !store.recorded.isEmpty }, "the book's place being recorded")
        XCTAssertEqual(store.recorded.first?.uid, "book-2")
        XCTAssertEqual(store.recorded.first?.positionSec, 40)
        XCTAssertEqual(store.recorded.first?.server?.fingerprint, "AA")
    }
}
