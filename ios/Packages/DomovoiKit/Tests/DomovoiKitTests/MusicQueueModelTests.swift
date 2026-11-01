import XCTest
@testable import DomovoiKit

/// The room queue against a server that is not a server: which room it opens
/// on, how many requests that costs, what each edit sends, and what it
/// refuses to send.
///
/// `QueueTab.kt`'s `rememberRoomQueue` is a composable and has no test. The
/// three things worth pinning are the three it gets wrong or leaves
/// unspecified over there: the room it defaults to, the one-at-a-time gate on
/// an edit, and the guest rules — which Android cannot keep at all, because
/// its `RoomQueue` has no `guest` field.
@MainActor
final class MusicQueueModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let phone = "ios-ab12cd34ef567890"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A house whose rooms each have a queue, and whose edits can be made to
    /// fail.
    private final class House: Sendable {
        /// What each room's queue read answers, keyed by room.
        let queues = Shared<[String: String]>([:])
        /// The status every edit comes back with.
        let editStatus = Shared(200)
        /// How many times each room's queue has been read.
        let reads = Shared<[String: Int]>([:])

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let path = parts.path

                if path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                guard path.hasPrefix("/api/music/queue/") else { return .json("{}", status: 404) }
                let rest = String(path.dropFirst("/api/music/queue/".count))
                if rest.hasSuffix("/remove") || rest.hasSuffix("/move") || rest.hasSuffix("/clear") {
                    guard self.editStatus.current == 200 else {
                        return .json(
                            #"{"detail":"that queue would not move"}"#,
                            status: self.editStatus.current
                        )
                    }
                    return .json("{}")
                }
                let room = rest.removingPercentEncoding ?? rest
                self.reads.mutate { $0[room, default: 0] += 1 }
                return .json(self.queues.current[room] ?? #"{"items":[],"editable":true,"guest":false}"#)
            }
        }
    }

    /// A household queue with two entries, the first of them playing.
    private let twoEntries = """
    {"room_id":"living room","current_song_id":7,"editable":true,"guest":false,
     "items":[{"song_id":7,"pos":0,"file":"/srv/a.mp3","title":"Hey Jude",
                "artist":"The Beatles","duration_sec":431,"playing":true},
               {"song_id":9,"pos":1,"file":"/srv/b.mp3","title":"Come Together",
                "artist":"The Beatles","duration_sec":259}]}
    """

    /// The same queue, answered to a guest.
    private let guestQueue = """
    {"room_id":"living room","editable":true,"guest":true,
     "items":[{"song_id":7,"title":"Hey Jude","playing":true},
               {"song_id":9,"title":"Come Together"}]}
    """

    private func rig(house: House = House()) -> (MusicQueueModel, AppContainer, House) {
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
        return (MusicQueueModel(container: container, deviceID: phone), container, house)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the queue",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Start over these rooms and wait for the read, asserting it did not
    /// fail — `answered` is true of a failure too, so without this every
    /// test below could be asserting over an empty queue it never got.
    private func started(
        _ queue: MusicQueueModel,
        rooms: [String],
        playing: String? = nil,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        queue.start(rooms: rooms, playing: playing)
        await settleUntil({ queue.read.answered }, "the queue read", file: file, line: line)
        XCTAssertNil(queue.read.error, "the queue read failed", file: file, line: line)
    }

    /// The one push a queue listens for.
    private let nowPlayingChanged = StateEvent(
        type: "music.now_playing.changed",
        frame: #"{"type":"music.now_playing.changed"}"#
    )

    private func requests(to suffix: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path.hasSuffix(suffix) == true }
    }

    private func body(of request: URLRequest) -> String {
        guard let data = request.httpBody else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    private func edits() -> [URLRequest] {
        StubURLProtocol.recorded.filter {
            guard let path = $0.url?.path else { return false }
            return path.hasSuffix("/remove") || path.hasSuffix("/move") || path.hasSuffix("/clear")
        }
    }

    // MARK: - which room it opens on

    /// The room that is **playing** — "the queue you opened this tab to look
    /// at" — not the first one in the list.
    func testItOpensOnTheRoomThatIsPlaying() async {
        let (queue, _, house) = rig()
        house.queues.set(["living room": twoEntries])

        await started(queue, rooms: ["garage", "living room"], playing: "living room")
        defer { queue.stop() }

        XCTAssertEqual("living room", queue.room)
        XCTAssertEqual(2, queue.items.count)
        XCTAssertEqual(1, house.reads.current["living room"])
        XCTAssertNil(house.reads.current["garage"], "the other room was never read")
    }

    /// With nothing playing, the first room there is.
    func testWithNothingPlayingItOpensOnTheFirstRoom() async {
        let (queue, _, _) = rig()

        await started(queue, rooms: ["garage", "living room"])
        defer { queue.stop() }

        XCTAssertEqual("garage", queue.room)
    }

    /// No rooms, no request. `/api/music/queue/` with nothing after it is a
    /// 404, not an empty queue.
    func testWithNoRoomsItAsksForNothing() async {
        let (queue, _, _) = rig()

        queue.start(rooms: [], playing: nil)
        await settleUntil({ queue.read.answered })
        defer { queue.stop() }

        XCTAssertNil(queue.room)
        XCTAssertTrue(queue.items.isEmpty)
        XCTAssertTrue(requests(to: "/api/music/queue/").isEmpty)
        XCTAssertFalse(queue.queue.editable, "nothing to edit, and nothing claiming otherwise")
        XCTAssertNil(queue.emptyNote, "no room means no sentence about a room")
    }

    /// **One request per visit, not two.** The model builds a new read when
    /// the room moves and sends it there, so a second refresh afterwards
    /// would cancel that one and send another — a request sent and dropped
    /// every time the tab opens.
    func testOpeningTheTabCostsOneRequest() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])

        await started(queue, rooms: ["den"], playing: "den")
        defer { queue.stop() }

        XCTAssertEqual(1, house.reads.current["den"])
        XCTAssertEqual(1, requests(to: "/api/music/queue/den").count)
    }

    /// The path carries this phone's id, which is what the server reads the
    /// permission off, and the room is escaped.
    func testTheReadCarriesTheDeviceIDAndEscapesTheRoom() async {
        let (queue, _, _) = rig()

        await started(queue, rooms: ["Kids' room"])
        defer { queue.stop() }

        let asked = StubURLProtocol.recorded.compactMap { $0.url?.absoluteString }
            .filter { $0.contains("/api/music/queue/") }
        XCTAssertEqual(
            ["\(server)/api/music/queue/Kids%27%20room?device_id=\(phone)"],
            asked
        )
    }

    // MARK: - rooms coming and going

    /// A room the person picked is kept when the server still reports it, and
    /// nothing is re-read for it.
    func testARoomStillThereIsKept() async {
        let (queue, _, house) = rig()

        await started(queue, rooms: ["garage", "den"])
        queue.offer(rooms: ["den", "garage"], playing: "den")
        await settleUntil({ queue.read.answered })
        defer { queue.stop() }

        XCTAssertEqual("garage", queue.room)
        XCTAssertEqual(1, house.reads.current["garage"])
        XCTAssertNil(house.reads.current["den"])
    }

    /// A room that has gone is given up: leaving it picked would leave the
    /// tab asking for a queue that cannot answer.
    func testARoomThatHasGoneIsGivenUp() async {
        let (queue, _, house) = rig()

        await started(queue, rooms: ["garage"])
        queue.offer(rooms: ["den"], playing: nil)
        await settleUntil({ queue.room == "den" && queue.read.answered })
        defer { queue.stop() }

        XCTAssertEqual(1, house.reads.current["den"])
    }

    /// And the last room going leaves no room at all, rather than the one
    /// that is not there any more.
    func testEveryRoomGoingLeavesNoRoom() async {
        let (queue, _, _) = rig()

        await started(queue, rooms: ["garage"])
        queue.offer(rooms: [], playing: nil)
        await settleUntil({ queue.room == nil })
        defer { queue.stop() }

        XCTAssertTrue(queue.items.isEmpty)
    }

    /// Picking by hand reads the new room and only the new room.
    func testPickingARoomReadsThatRoom() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])

        await started(queue, rooms: ["garage", "den"])
        queue.pick("den")
        await settleUntil({ queue.room == "den" && queue.items.count == 2 })
        defer { queue.stop() }

        XCTAssertEqual(1, house.reads.current["garage"])
        XCTAssertEqual(1, house.reads.current["den"])
    }

    /// Picking the room that is already open sends nothing.
    func testPickingTheRoomAlreadyOpenSendsNothing() async {
        let (queue, _, house) = rig()

        await started(queue, rooms: ["garage"])
        queue.pick("garage")
        await settleUntil({ queue.read.answered })
        defer { queue.stop() }

        XCTAssertEqual(1, house.reads.current["garage"])
    }

    // MARK: - what it says

    func testTheCountAndTheEmptySentence() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])

        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertEqual("2 in the queue", queue.countText)
        XCTAssertNil(queue.emptyNote)
    }

    func testAnEmptyQueueNamesTheRoomAndWhatToDo() async {
        let (queue, _, _) = rig()

        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertEqual("0 in the queue", queue.countText)
        XCTAssertEqual(
            "nothing queued in den — ask it to play something, or play a track from the library",
            queue.emptyNote
        )
    }

    /// One entry is "1 in the queue", not "1 in the queues".
    func testOneEntryIsSaidInTheSingular() async {
        let (queue, _, house) = rig()
        house.queues.set([
            "den": #"{"editable":true,"guest":false,"items":[{"song_id":1,"title":"a"}]}"#,
        ])

        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertEqual("1 in the queue", queue.countText)
    }

    /// A read that failed is not an empty queue: no sentence about a room
    /// with nothing in it, because this app does not know that it has
    /// nothing in it.
    func testAFailedReadIsNotAnEmptyQueue() async {
        let (queue, _, _) = rig()
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/health" else {
                return .json(#"{"detail":"the queue is away"}"#, status: 503)
            }
            let parts = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let challenge = (parts?.queryItems ?? []).first { $0.name == "challenge" }?.value
            return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
        }

        queue.start(rooms: ["den"], playing: nil)
        await settleUntil({ queue.read.answered })
        defer { queue.stop() }

        XCTAssertNotNil(queue.read.error)
        XCTAssertNil(queue.emptyNote)
        XCTAssertFalse(queue.queue.editable)
    }

    // MARK: - the three edits

    func testARemoveSendsTheEntryAndSaysWhatItDid() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.remove(queue.items[1])
        await settleUntil({ queue.toast != nil }, "the remove")

        XCTAssertEqual(#"removed "Come Together""#, queue.takeToast())
        let sent = requests(to: "/api/music/queue/den/remove")
        XCTAssertEqual(1, sent.count)
        XCTAssertEqual("POST", sent.first?.httpMethod)
        XCTAssertEqual(#"{"song_ids":[9],"device_id":"\#(phone)"}"#, body(of: sent[0]))
        // And the list is re-read rather than patched: the server is the
        // truth about an edit four other clients also see.
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the re-read")
    }

    /// A successful move says **nothing**: the list redraws in the new order,
    /// which says it better than a sentence over the top of it.
    func testAMoveSendsThePositionAndSaysNothing() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.moveDown(0)
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the move")

        XCTAssertNil(queue.toast)
        let sent = requests(to: "/api/music/queue/den/move")
        XCTAssertEqual(#"{"song_id":7,"to_position":1,"device_id":"\#(phone)"}"#, body(of: sent[0]))
    }

    func testMovingUpSendsThePositionAbove() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.moveUp(1)
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the move")

        let sent = requests(to: "/api/music/queue/den/move")
        XCTAssertEqual(#"{"song_id":9,"to_position":0,"device_id":"\#(phone)"}"#, body(of: sent[0]))
    }

    /// The ends of the list send nothing. The server takes `to_position: int
    /// = Field(..., ge=0)`, so a `-1` from an up-arrow on the first row is a
    /// 422 with nothing for the person to do about it.
    func testTheEndsOfTheListCannotBeMovedPastThem() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayMoveUp(0))
        XCTAssertFalse(queue.mayMoveDown(1))
        queue.moveUp(0)
        queue.moveDown(1)
        // And a position outside the list, asked for directly.
        queue.move(queue.items[0], to: 5)
        queue.move(queue.items[0], to: -1)
        await settleUntil({ queue.read.answered })

        XCTAssertTrue(edits().isEmpty, "nothing should have gone out")
        XCTAssertEqual(1, house.reads.current["den"])
    }

    func testAClearSendsTheDeviceAndSaysWhichRoom() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.confirmingClear = true
        queue.clear()
        await settleUntil({ queue.toast != nil }, "the clear")

        XCTAssertFalse(queue.confirmingClear, "the confirmation is closed by the act")
        XCTAssertEqual("cleared den's queue", queue.takeToast())
        let sent = requests(to: "/api/music/queue/den/clear")
        XCTAssertEqual(#"{"device_id":"\#(phone)"}"#, body(of: sent[0]))
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the re-read")
    }

    // MARK: - what it refuses

    /// A guest may not clear. The server refuses it before it even looks for
    /// a block, so offering it would be offering a refusal.
    func testAGuestsClearIsRefusedHereRatherThanByTheServer() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": guestQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayClear)
        queue.clear()
        await settleUntil({ queue.read.answered })

        XCTAssertTrue(edits().isEmpty)
        XCTAssertNil(queue.toast)
    }

    /// And may not take out the entry playing now, which the core refuses
    /// because the core is what knows which entry that is.
    func testAGuestMayNotRemoveWhatIsPlayingButMayRemoveTheRest() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": guestQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayRemove(queue.items[0]))
        XCTAssertTrue(queue.mayRemove(queue.items[1]))

        queue.remove(queue.items[0])
        await settleUntil({ queue.read.answered })
        XCTAssertTrue(edits().isEmpty, "the playing entry is the core's to refuse")

        queue.remove(queue.items[1])
        await settleUntil({ queue.toast != nil }, "the guest's remove")
        XCTAssertEqual(1, requests(to: "/api/music/queue/den/remove").count)
    }

    /// `editable: false` is the end of all three.
    func testABlockedDeviceSendsNothingAtAll() async {
        let (queue, _, house) = rig()
        house.queues.set([
            "den": """
            {"editable":false,"guest":false,
             "blocked_reason":"Kamron's iPad isn't allowed to edit the queue in den",
             "items":[{"song_id":7,"title":"Hey Jude","playing":true},
                       {"song_id":9,"title":"Come Together"}]}
            """,
        ])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayClear)
        XCTAssertFalse(queue.mayMove)
        XCTAssertFalse(queue.mayRemove(queue.items[1]))
        XCTAssertEqual(
            "Kamron's iPad isn't allowed to edit the queue in den. "
                + "You can watch the queue but not change it.",
            queue.watchOnlyNote
        )

        queue.remove(queue.items[1])
        queue.moveDown(0)
        queue.clear()
        await settleUntil({ queue.read.answered })

        XCTAssertTrue(edits().isEmpty)
    }

    // MARK: - one edit at a time

    /// A second edit while one is in flight sends nothing. Two moves racing
    /// each other on one list produce an order neither tap asked for.
    func testASecondEditWhileOneIsInFlightSendsNothing() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        // The first edit is held open at the server, so the second is asked
        // for **inside** the first one's round trip rather than after it.
        // The queue body is copied into a local first: the dispatcher is
        // `@Sendable` and must not reach back into the test case to read it.
        let answer = twoEntries
        let park = parkRequest(
            to: "/api/music/queue/den/remove",
            answering: { .json("{}") },
            otherwise: { request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                guard parts.path == "/api/health" else { return .json(answer) }
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
        )

        queue.remove(queue.items[1])
        let arrived = await settle(for: park.arrived, what: "the first remove")
        XCTAssertTrue(arrived)
        XCTAssertTrue(queue.busy)

        queue.remove(queue.items[0])
        queue.clear()
        queue.moveDown(0)
        XCTAssertEqual(1, edits().count, "only the first edit went")

        park.release.set(true)
        await settleUntil({ !queue.busy }, "the first remove to finish")
        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")
        // And the gate is open again afterwards.
        XCTAssertTrue(queue.mayClear)
    }

    /// The gate is released on a **failure** too — a `defer`, not a line at
    /// the end of the success path, or one refusal would disable the screen's
    /// controls for good.
    func testAFailedEditReleasesTheGateAndSaysWhatWentWrong() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        house.editStatus.set(503)
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.remove(queue.items[1])
        await settleUntil({ queue.toast != nil }, "the failed remove")

        // `DomovoiError.message` is "{status}: {body}" (``ApiFailure/message``
        // says why there is no reason phrase in it), so the toast is checked
        // by what it names rather than by an exact string: which edit failed,
        // and the server's own sentence.
        let said = try XCTUnwrap(queue.takeToast())
        XCTAssertTrue(said.hasPrefix("remove failed:"), said)
        XCTAssertTrue(said.contains("that queue would not move"), said)
        XCTAssertFalse(queue.busy)
        XCTAssertEqual(1, house.reads.current["den"], "a failed edit does not re-read")
    }

    func testAFailedMoveAndAFailedClearEachSayWhichOneFailed() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        house.editStatus.set(403)
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.moveDown(0)
        await settleUntil({ queue.toast != nil }, "the failed move")
        let move = try XCTUnwrap(queue.takeToast())
        XCTAssertTrue(move.hasPrefix("move failed:"), move)

        queue.clear()
        await settleUntil({ queue.toast != nil }, "the failed clear")
        let clear = try XCTUnwrap(queue.takeToast())
        XCTAssertTrue(clear.hasPrefix("clear failed:"), clear)
    }

    // MARK: - pushes, and moving house

    /// A now-playing push re-reads the queue: a track finishing is both a
    /// now-playing change and a queue change.
    func testANowPlayingPushReReadsTheQueue() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.received(nowPlayingChanged)
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the re-read")
    }

    /// And a push about something else does not.
    func testAnUnrelatedPushIsIgnored() async {
        let (queue, _, house) = rig()
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.received(StateEvent(type: "timer.changed", frame: #"{"type":"timer.changed"}"#))
        for _ in 0..<64 { await Task.yield() }

        XCTAssertEqual(1, house.reads.current["den"])
    }

    /// Moving house **forgets the room**. A room name is one house's word:
    /// the next house may have a room of that name which is a different
    /// speaker, and may have none at all.
    func testMovingHouseForgetsTheRoomAndAsksForNothing() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.serverChanged()
        for _ in 0..<64 { await Task.yield() }

        XCTAssertNil(queue.room)
        XCTAssertTrue(queue.items.isEmpty)
        XCTAssertEqual(1, house.reads.current["den"], "no read for the old room's name")
        XCTAssertNil(queue.emptyNote)
    }

    /// And the next offer picks again, from the new house's rooms.
    func testAfterMovingHouseTheNextOfferPicksAgain() async {
        let (queue, _, house) = rig()
        await started(queue, rooms: ["den"])
        queue.serverChanged()
        queue.offer(rooms: ["kitchen"], playing: "kitchen")
        await settleUntil({ queue.room == "kitchen" && queue.read.answered })
        defer { queue.stop() }

        XCTAssertEqual(1, house.reads.current["kitchen"])
    }

    /// `stop()` takes the model off the push registry, so a push after it
    /// reads nothing.
    func testAPushAfterStopReadsNothing() async {
        let (queue, container, house) = rig()
        await started(queue, rooms: ["den"])
        queue.stop()

        container.stateEventArrived(nowPlayingChanged)
        for _ in 0..<64 { await Task.yield() }

        XCTAssertEqual(1, house.reads.current["den"])
    }
}
