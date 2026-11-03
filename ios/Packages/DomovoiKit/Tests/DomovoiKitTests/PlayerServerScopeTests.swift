import XCTest
@testable import DomovoiKit

/// Which server the queue belongs to, and what a move to another one leaves.
///
/// `ServerScopeTest` on Android, and the bug behind it is Kamron's own, from
/// 2026-10-12: he played music from one Domovoi, connected the app to a fresh
/// one at another address, and the mini player still showed the first one's
/// song. Id 12 on another server is another song, or nothing.
final class PlayerServerScopeTests: XCTestCase {

    private let one = PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA")
    private let sameAtANewAddress = PlayerServerScope(address: "http://192.168.0.9:6369", fingerprint: "AA")
    private let another = PlayerServerScope(address: "http://10.0.0.99:6369", fingerprint: "BB")

    // MARK: - telling servers apart

    func testTheSameIdentityAtANewAddressIsTheSameServer() {
        XCTAssertTrue(one.isSameServer(as: sameAtANewAddress))
        XCTAssertTrue(sameAtANewAddress.isSameServer(as: one))
    }

    func testADifferentIdentityIsADifferentServerWhateverTheAddress() {
        XCTAssertFalse(one.isSameServer(as: another))
        let samePlaceNewHouse = PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "BB")
        XCTAssertFalse(
            one.isSameServer(as: samePlaceNewHouse),
            "a reinstalled server at the same address is a different server"
        )
    }

    func testWithoutAnIdentityTheAddressIsWhatIsCompared() {
        let a = PlayerServerScope(address: "http://10.0.0.42:6369")
        let b = PlayerServerScope(address: "http://10.0.0.42:6369")
        XCTAssertTrue(a.isSameServer(as: b))
        XCTAssertFalse(a.isSameServer(as: PlayerServerScope(address: "http://10.0.0.99:6369")))
        XCTAssertTrue(
            a.isSameServer(as: PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA")),
            "only one side knows its identity, so the address decides"
        )
    }

    func testNoServerIsNeverTheSameAsAnything() {
        XCTAssertFalse(PlayerServerScope.none.isSameServer(as: one))
        XCTAssertFalse(PlayerServerScope.none.isSameServer(as: .none))
        XCTAssertFalse(one.isSameServer(as: .none))
    }

    func testMergingTakesWhateverTheOtherSideNowKnows() {
        let unknown = PlayerServerScope(address: "http://10.0.0.42:6369")
        let learned = unknown.merge(PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA"))
        XCTAssertEqual(learned.fingerprint, "AA", "an identity pinned since")
        let moved = one.merge(PlayerServerScope(address: "http://192.168.0.9:6369"))
        XCTAssertEqual(moved.address, "http://192.168.0.9:6369", "a new address")
        XCTAssertEqual(moved.fingerprint, "AA", "and the identity is kept")
    }

    func testOfTakesTheAddressTheIdentityPinsAreKeyedBy() {
        let scope = PlayerServerScope.of(serverURL: "http://10.0.0.42:6369/", pin: nil)
        XCTAssertEqual(scope.address, IdentityGate.pinKey("http://10.0.0.42:6369/"))
        XCTAssertNil(scope.fingerprint)
        XCTAssertNil(PlayerServerScope.of(serverURL: nil, pin: nil).address)
        XCTAssertNil(PlayerServerScope.of(serverURL: "", pin: nil).address)
    }

    // MARK: - what a move leaves of the queue

    func testMediaThatCameFromAServerIsEverythingButThePhonesOwn() {
        XCTAssertTrue(PlayerServerSwitch.fromServer(PlayerSeed.transmission))
        XCTAssertTrue(PlayerServerSwitch.fromServer(PlayerSeed.episode))
        XCTAssertTrue(PlayerServerSwitch.fromServer(PlayerSeed.station))
        XCTAssertFalse(PlayerServerSwitch.fromServer(PlayerSeed.phoneSong))
        let saved = PlayerItem.fromBook(id: 2, title: "Wayfinding", savedKey: "books/wayfinding")
        XCTAssertFalse(
            PlayerServerSwitch.fromServer(saved),
            "a book saved on this phone belongs to no server: its files are here"
        )
    }

    func testADifferentServerDropsItsMediaAndKeepsThePhonesSongs() {
        let queue = [PlayerSeed.transmission, PlayerSeed.phoneSong, PlayerSeed.episode]
        let plan = PlayerServerSwitch.plan(queue: queue, index: 0)
        XCTAssertEqual(plan.kept.map(\.uid), ["dev-77"])
        XCTAssertEqual(
            plan.removed, [2, 0],
            "highest first: the order they can be taken out of a player in without the later indices shifting"
        )
        XCTAssertTrue(plan.currentGoes, "Transmission was playing, and it is the old server's")
        XCTAssertEqual(plan.keptIndex, 0)
    }

    func testAPhoneSongThatIsPlayingPlaysOn() {
        let queue = [PlayerSeed.transmission, PlayerSeed.phoneSong, PlayerSeed.episode]
        let plan = PlayerServerSwitch.plan(queue: queue, index: 1)
        XCTAssertFalse(plan.currentGoes)
        XCTAssertEqual(plan.kept.map(\.uid), ["dev-77"])
        XCTAssertEqual(plan.keptIndex, 0, "its own place among what is left")
    }

    func testThePlayHeadLandsOnThePhoneSongAfterTheOneThatWent() {
        let second = PlayerItem.fromDeviceAudio(id: 78, title: "Second", uri: "file:///b.m4a")
        // index 1 is the server's episode; the phone songs are at 0 and 2.
        let queue = [PlayerSeed.phoneSong, PlayerSeed.episode, second]
        let plan = PlayerServerSwitch.plan(queue: queue, index: 1)
        XCTAssertTrue(plan.currentGoes)
        XCTAssertEqual(plan.kept.map(\.uid), ["dev-77", "dev-78"])
        XCTAssertEqual(plan.keptIndex, 1, "one phone song came before the play head, so the next one is at 1")
    }

    func testAQueueOfOnlyPhoneSongsLosesNothing() {
        let plan = PlayerServerSwitch.plan(queue: [PlayerSeed.phoneSong], index: 0)
        XCTAssertTrue(plan.removed.isEmpty)
        XCTAssertFalse(plan.currentGoes)
    }

    func testAnEmptyQueuePlansNothingAndDoesNotCrash() {
        let plan = PlayerServerSwitch.plan(queue: [], index: 0)
        XCTAssertTrue(plan.kept.isEmpty)
        XCTAssertTrue(plan.removed.isEmpty)
        XCTAssertEqual(plan.keptIndex, 0)
        XCTAssertTrue(plan.currentGoes, "nothing is playing, so nothing plays on")
    }

    func testAnOutOfRangeIndexDoesNotCrash() {
        let plan = PlayerServerSwitch.plan(queue: [PlayerSeed.transmission], index: 9)
        XCTAssertTrue(plan.currentGoes)
        XCTAssertEqual(plan.keptIndex, 0)
    }

    // MARK: - through the model

    @MainActor
    func testTheSameServerAtANewAddressKeepsTheQueue() async {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.serverChanged(to: one)
        h.model.play([PlayerSeed.transmission, PlayerSeed.atmosphere])
        h.model.serverChanged(to: sameAtANewAddress)
        XCTAssertEqual(h.model.queue.count, 2, "the same house, found at a new address")
        XCTAssertTrue(h.model.isPlaying)
        StubURLProtocol.reset()
    }

    @MainActor
    func testADifferentServerDropsItsMediaStopsPlaybackAndEndsTheCast() async {
        StubURLProtocol.reset()
        let house = PlayerHouse()
        house.install()
        let h = PlayerHarness()
        h.model.serverChanged(to: one)
        h.model.play([PlayerSeed.transmission, PlayerSeed.phoneSong])
        _ = await h.model.cast(to: "office")
        XCTAssertEqual(h.model.target, .room("office"))

        let before = PlayerHouse.paths.count
        h.model.serverChanged(to: another)

        XCTAssertEqual(h.model.target, .thisPhone, "a room on the old server is not the new server's to control")
        XCTAssertNil(h.model.roomReading)
        XCTAssertEqual(h.model.queue.map(\.uid), ["dev-77"], "the phone's own song stays")
        XCTAssertFalse(h.model.isPlaying)
        // Nothing is said to the new server about the old server's room: the
        // only requests after the switch would be the new server's own.
        let after = Array(PlayerHouse.paths.dropFirst(before))
        XCTAssertFalse(
            after.contains { $0.hasPrefix("/api/music/pause/") },
            "the old server's room is not paused through the new server"
        )
        StubURLProtocol.reset()
    }

    @MainActor
    func testTheQueueIsChangedBeforeThePauseSoNothingOfTheOldServerIsSent() async {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.serverChanged(to: one)
        // A podcast: pausing flushes its place, and that save must not reach
        // the new server (`:188-196` — the ordering is the fix).
        h.model.play([PlayerSeed.episode])
        let before = PlayerHouse.paths.filter { $0.hasPrefix("/api/podcasts/positions/") }.count
        h.model.serverChanged(to: another)
        await h.settle(until: { !h.model.isPlaying }, "playback stopping")
        await Task.yield()
        let after = PlayerHouse.paths.filter { $0.hasPrefix("/api/podcasts/positions/") }.count
        XCTAssertEqual(
            after, before,
            "the queue is emptied first, so the pause's position save finds nothing to send"
        )
        XCTAssertTrue(h.model.queue.isEmpty)
        StubURLProtocol.reset()
    }
}
