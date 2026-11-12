import Foundation
import XCTest
@testable import DomovoiKit

/// "Stop and close" (hand-off §2b; Android `PlayerController.stopAndClose()`
/// and `StopAndCloseTest.kt`, d19efd9c; the web's `closePlayer`): it stops,
/// empties the queue and the player has nothing left to show — and while
/// casting it stops the **room** first.
///
/// Before it, the iPhone app had two halves of it and neither was it: the
/// player tab's `stop` while casting only sent the room a stop and left the
/// phone "casting to" it with the queue up, and its `clear` emptied the queue
/// with the target still on the room and the room's poll still running.
///
/// The house here parks one chosen request until it is released, so a case
/// can act **inside** the round trip — the only place a read-after-await
/// defect shows (`read-after-await-wrong-key`).
@MainActor
final class PlayerModelStopAndCloseTests: XCTestCase {

    private let house = StopHouse()
    private let three = [PlayerSeed.transmission, PlayerSeed.atmosphere, PlayerSeed.decades]

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        house.install()
    }

    /// Every player a case built, so its room poll is stopped when the case
    /// ends: a refusal leaves the phone casting, and a poll left running reads
    /// `now-playing` into the next case's house two seconds later.
    private var built: [PlayerHarness] = []

    private func harness() -> PlayerHarness {
        let h = PlayerHarness()
        built.append(h)
        return h
    }

    override func tearDown() {
        for h in built { h.model.detachForRoomMode() }
        built = []
        house.releaseAll()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    private func castingToOffice(_ h: PlayerHarness) async {
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        XCTAssertEqual(h.model.target, .room("office"), "the case never got to casting")
        await h.settle(until: { h.model.roomReading != nil }, "the first room reading")
    }

    private func stops(_ room: String) -> Int {
        StopHouse.paths.filter { $0 == "/api/music/stop/\(room)" }.count
    }

    // MARK: - on the phone

    func testOnThePhoneItStopsEmptiesTheQueueAndClosesThePlayer() async {
        let h = harness()
        h.model.play(three, startAt: 1)
        XCTAssertTrue(h.model.isPlaying)

        let closed = await h.model.stopAndClose()

        XCTAssertTrue(closed)
        XCTAssertEqual(h.model.queue.map(\.uid), [], "the queue is still there")
        XCTAssertNil(h.model.current, "the player still has something to show")
        XCTAssertFalse(h.model.isPlaying)
        XCTAssertEqual(h.engine.itemCount, 0, "the phone's own player still holds the queue")
        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertEqual(
            StopHouse.posts.filter { $0.hasPrefix("/api/music/") }, [],
            "no room was told anything"
        )
    }

    // MARK: - while casting

    /// The room is stopped **first** — parked in the house, the phone's
    /// queue and target are still the room's — and only once the room has
    /// said it stopped is the room let go of and the queue emptied.
    func testWhileCastingItStopsTheRoomThenClears() async {
        let h = harness()
        await castingToOffice(h)
        house.hold("/api/music/stop/office")

        let closing = Task { @MainActor in await h.model.stopAndClose() }
        await h.settle(until: { self.house.arrivals("/api/music/stop/office") > 0 }, "the room's stop leaving")

        XCTAssertEqual(h.model.target, .room("office"), "the room was let go of before it answered")
        XCTAssertEqual(h.model.queue.count, 3, "the queue was emptied before the room answered")

        house.release("/api/music/stop/office")
        let closed = await closing.value

        XCTAssertTrue(closed)
        XCTAssertEqual(stops("office"), 1)
        XCTAssertEqual(StopHouse.methods(for: "/api/music/stop/office"), ["POST"])
        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertNil(h.model.roomReading)
        XCTAssertEqual(h.model.queue.map(\.uid), [])
        XCTAssertFalse(h.model.isPlaying)

        // Nothing watches the room any more: the poll reads every 2 s, so a
        // read already on its way may land, and none after it.
        try? await Task.sleep(for: .milliseconds(300))
        let reads = StopHouse.paths.filter { $0 == "/api/music/now-playing" }.count
        try? await Task.sleep(for: .milliseconds(2_500))
        XCTAssertEqual(
            StopHouse.paths.filter { $0 == "/api/music/now-playing" }.count, reads,
            "the room is still polled after the player closed"
        )
    }

    /// A room that refuses keeps everything: the target, the queue, the poll
    /// — the player stays up, and the caller says so.
    func testARoomThatRefusesTheStopChangesNothing() async {
        let h = harness()
        await castingToOffice(h)
        house.failing.set(["/api/music/stop/office": 502])

        let closed = await h.model.stopAndClose()

        XCTAssertFalse(closed)
        XCTAssertEqual(stops("office"), 1, "the room was never asked")
        XCTAssertEqual(h.model.target, .room("office"))
        XCTAssertEqual(h.model.queue.map(\.uid), three.map(\.uid))
        XCTAssertNotNil(h.model.roomReading)
    }

    /// A 2xx that says `ok: false` is a no as well (`roomActionNow`).
    func testARoomThatSaysItDidNotStopKeepsThePlayerToo() async {
        let h = harness()
        await castingToOffice(h)
        house.answers.set(["/api/music/stop/office": #"{"ok":false}"#])

        let closed = await h.model.stopAndClose()

        XCTAssertFalse(closed)
        XCTAssertEqual(h.model.target, .room("office"))
        XCTAssertEqual(h.model.queue.count, 3)
    }

    /// The player tab's model says the web's sentence, through the adapter.
    func testTheTabSaysTheRoomCouldNotBeStopped() async {
        let h = harness()
        await castingToOffice(h)
        house.failing.set(["/api/music/stop/office": 502])
        let tab = MusicLocalQueueModel(deviceID: "phone-1")
        tab.use(PlayerModelMusicEngine(h.model))

        XCTAssertTrue(tab.stopAndClose())
        await h.settle(until: { tab.toast != nil }, "the refusal being said")

        XCTAssertEqual(tab.takeToast(), "couldn't stop the room — the player is still open")
        XCTAssertFalse(tab.isEmpty, "the tab went to nothing queued over a room still playing")
    }

    func testTheTabClosesThroughTheAdapter() async {
        let h = harness()
        h.model.play(three)
        let tab = MusicLocalQueueModel(deviceID: "phone-1")
        tab.use(PlayerModelMusicEngine(h.model))
        XCTAssertFalse(tab.isEmpty)

        XCTAssertTrue(tab.stopAndClose())
        await h.settle(until: { tab.isEmpty }, "the tab going back to nothing queued")

        XCTAssertNil(tab.takeToast(), "a close that went through says nothing")
    }

    // MARK: - a play here meanwhile is kept (read after await)

    /// A play here that lands **while the room's answer is on its way** owns
    /// the queue by the time it comes: the stop's answer must not wipe it.
    func testAPlayHereDuringTheStopIsKept() async {
        let h = harness()
        await castingToOffice(h)
        house.hold("/api/music/stop/office")

        let closing = Task { @MainActor in await h.model.stopAndClose() }
        await h.settle(until: { self.house.arrivals("/api/music/stop/office") > 0 }, "the room's stop leaving")
        h.model.play([PlayerSeed.episode])
        house.release("/api/music/stop/office")
        _ = await closing.value

        XCTAssertEqual(h.model.queue.map(\.uid), ["pod-9"], "the stop's answer wiped what started here")
        XCTAssertTrue(h.model.isPlaying, "what started here was stopped by the room's answer")
        XCTAssertEqual(h.model.target, .thisPhone)
    }

    /// A play here that lands while "Stop and close" is **waiting behind a
    /// cast** on its way: the tap was about the queue before it, so nothing is
    /// stopped or cleared once the line moves.
    func testAPlayHereWhileTheCloseWaitsBehindACastIsKept() async {
        let h = harness()
        h.model.play(three)
        house.hold("/api/music/play-tracks")

        let casting = Task { @MainActor in await h.model.cast(to: "office") }
        await h.settle(until: { self.house.arrivals("/api/music/play-tracks") > 0 }, "the cast leaving")
        let closing = Task { @MainActor in await h.model.stopAndClose() }
        // Let the close take its place in line behind the cast.
        for _ in 0..<200 { await Task.yield() }
        h.model.play([PlayerSeed.episode])
        house.release("/api/music/play-tracks")
        _ = await casting.value
        let closed = await closing.value

        XCTAssertTrue(closed)
        XCTAssertEqual(h.model.queue.map(\.uid), ["pod-9"], "the close cleared what started here after it was tapped")
        XCTAssertTrue(h.model.isPlaying)
        XCTAssertEqual(stops("office"), 0, "the close stopped a room the play here had already left")
    }

    /// The close waits for a cast already on its way, and stops the room
    /// **that cast lands on** — not the phone's queue from under a room left
    /// playing with nothing watching it.
    func testTheCloseWaitsForACastOnItsWayAndStopsThatRoom() async {
        let h = harness()
        h.model.play(three)
        house.hold("/api/music/play-tracks")

        let casting = Task { @MainActor in await h.model.cast(to: "office") }
        await h.settle(until: { self.house.arrivals("/api/music/play-tracks") > 0 }, "the cast leaving")
        let closing = Task { @MainActor in await h.model.stopAndClose() }
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(h.model.queue.count, 3, "the close did not wait for the cast in flight")
        house.release("/api/music/play-tracks")
        _ = await casting.value
        let closed = await closing.value

        XCTAssertTrue(closed)
        XCTAssertEqual(stops("office"), 1, "the room the cast landed on was left playing")
        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertEqual(h.model.queue.map(\.uid), [])
    }

    /// The stop's answer arrives **after the app moved house**: it was about
    /// the old house's room, and the new house's player is not touched on its
    /// word — here the phone's own song, which the move kept, still plays.
    func testAStopAnsweredAfterTheAppMovedHouseLeavesTheNewHouseAlone() async {
        let h = harness()
        let first = PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA")
        let second = PlayerServerScope(address: "http://10.0.0.99:6369", fingerprint: "BB")
        h.model.serverChanged(to: first)
        h.model.play([PlayerSeed.transmission, PlayerSeed.phoneSong])
        _ = await h.model.cast(to: "office")
        XCTAssertEqual(h.model.target, .room("office"), "the case never got to casting")
        house.hold("/api/music/stop/office")

        let closing = Task { @MainActor in await h.model.stopAndClose() }
        await h.settle(until: { self.house.arrivals("/api/music/stop/office") > 0 }, "the room's stop leaving")
        h.model.serverChanged(to: second)
        h.model.jump(to: 0)
        house.release("/api/music/stop/office")
        _ = await closing.value

        XCTAssertEqual(h.model.queue.map(\.uid), [PlayerSeed.phoneSong.uid], "the old house's answer emptied the new house's queue")
        XCTAssertTrue(h.model.isPlaying, "the old house's answer stopped the phone's own song")
    }
}

/// The player's routes, with any one path parked until released and any one
/// path failing or answering what a case says.
///
/// `PlayerHouse`'s shape (`PlayerFakes.swift`), with the park generalised: a
/// stop or a cast has to be held **inside** its round trip for a case to act
/// there, and the park is the only way to be certain it is.
final class StopHouse: Sendable {
    let nowPlaying = Shared("""
    [{"room_id":"office","state":"play","elapsed_sec":42,"favorited":false,"track_id":12,\
    "song":{"title":"Atmosphere","artist":"Joy Division","duration_sec":390}},\
    {"room_id":"den","state":"stop"}]
    """)
    let failing = Shared<[String: Int]>([:])
    let answers = Shared<[String: String]>([:])
    private let held = Shared<Set<String>>([])
    private let arrived = Shared<[String: Int]>([:])
    private let gates = Shared<[String: DispatchSemaphore]>([:])

    func hold(_ path: String) {
        held.mutate { $0.insert(path) }
        gates.mutate { $0[path] = DispatchSemaphore(value: 0) }
    }

    func release(_ path: String) {
        held.mutate { $0.remove(path) }
        gates.current[path]?.signal()
    }

    func releaseAll() {
        for path in held.current { release(path) }
    }

    func arrivals(_ path: String) -> Int { arrived.current[path] ?? 0 }

    func install() {
        StubURLProtocol.dispatch { [self] request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            let path = parts.path
            self.arrived.mutate { $0[path, default: 0] += 1 }
            if self.held.current.contains(path), let gate = self.gates.current[path] {
                // Bounded, so a case that forgets to release fails on its
                // assertion rather than hanging the suite.
                _ = gate.wait(timeout: .now() + 0.8)
            }
            switch path {
            case "/api/health":
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            case "/api/music/now-playing":
                return .json(self.nowPlaying.current)
            default:
                if let status = self.failing.current[path] {
                    return .json(#"{"detail":"the music player is not answering","failed":"music_player"}"#,
                                 status: status)
                }
                return .json(self.answers.current[path] ?? #"{"ok":true}"#)
            }
        }
    }

    static var paths: [String] {
        StubURLProtocol.recorded.compactMap { $0.url?.path }
    }

    /// The paths that were POSTed to — what a room was told.
    static var posts: [String] {
        StubURLProtocol.recorded.filter { $0.httpMethod == "POST" }.compactMap { $0.url?.path }
    }

    static func methods(for path: String) -> [String] {
        StubURLProtocol.recorded
            .filter { $0.url?.path == path }
            .map { $0.httpMethod ?? "" }
    }
}
