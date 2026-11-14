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
        /// What a "less like this" answers.
        let feedbackAnswer = Shared(#"{"removed":[5,6]}"#)

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
                if path.hasPrefix("/api/music/up-next/sessions/") {
                    // `{removed: [seq]}` (`up_next.py:412`) for a feedback;
                    // the session itself for a PATCH.
                    return path.hasSuffix("/feedback")
                        ? .json(self.feedbackAnswer.current)
                        : .json(#"{"id":"s-1","autoplay":false}"#)
                }
                guard path.hasPrefix("/api/music/queue/") else { return .json("{}", status: 404) }
                let rest = String(path.dropFirst("/api/music/queue/".count))
                if rest.hasSuffix("/save-playlist") {
                    guard self.editStatus.current == 200 else {
                        return .json(#"{"detail":"playlist named 'Road' already exists (id=3)"}"#, status: 409)
                    }
                    return .json(#"{"playlist_id":12,"name":"Road","saved":3,"skipped":1,"duplicates":1}"#, status: 201)
                }
                if rest.hasSuffix("/add") {
                    return .json(#"{"queued":[{"song_id":41,"file":"a.mp3"}],"requested":1}"#)
                }
                if rest.hasSuffix("/remove") || rest.hasSuffix("/move") || rest.hasSuffix("/clear")
                    || rest.hasSuffix("/play") {
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
    {"current_song_id":7,"editable":true,"guest":false,
     "items":[{"song_id":7,"pos":0,"file":"/srv/a.mp3","title":"Hey Jude",
                "artist":"The Beatles","duration_sec":431,"playing":true},
               {"song_id":9,"pos":1,"file":"/srv/b.mp3","title":"Come Together",
                "artist":"The Beatles","duration_sec":259}]}
    """

    /// The same queue, answered to a guest.
    private let guestQueue = """
    {"editable":true,"guest":true,
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

    /// The entry at `index`, or a clean failure.
    ///
    /// **Never `queue.items[index]`.** A regression that empties the queue
    /// would trap there, and a trap takes the rest of the xctest process with
    /// it: the negative control for this batch died at exactly that line and
    /// so reported nothing about the eighteen sabotages behind it. This fails
    /// the one test and lets the suite finish.
    private func entry(
        _ queue: MusicQueueModel,
        _ index: Int,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws -> QueueItem {
        try XCTUnwrap(
            queue.items.indices.contains(index) ? queue.items[index] : nil,
            "no entry \(index) in a queue of \(queue.items.count)",
            file: file,
            line: line
        )
    }

    private func requests(to suffix: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path.hasSuffix(suffix) == true }
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

    /// An empty queue this phone may add to points at the search box above
    /// it, in Android's words (`QueueTab.kt:416`, 21b5ccfb).
    func testAnEmptyQueueNamesTheRoomAndWhatToDo() async {
        let (queue, _, _) = rig()

        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertEqual("0 in the queue", queue.countText)
        XCTAssertTrue(queue.canAdd)
        XCTAssertEqual(
            "queue is empty — search above to add a song, or say \"play something\" in den",
            queue.emptyNote
        )
    }

    /// One it may only watch has no search box, so its sentence does not
    /// point at one.
    func testAnEmptyQueueThisPhoneMayOnlyWatchDoesNotPointAtTheSearch() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": #"{"items":[],"editable":false,"guest":true,"blocked_reason":"nothing is playing"}"#])

        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.canAdd)
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

    func testARemoveSendsTheEntryAndSaysWhatItDid() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.remove(try entry(queue, 1))
        await settleUntil({ queue.toast != nil }, "the remove")

        XCTAssertEqual(#"removed "Come Together""#, queue.takeToast())
        let sent = requests(to: "/api/music/queue/den/remove")
        XCTAssertEqual(1, sent.count)
        XCTAssertEqual("POST", sent.first?.httpMethod)
        XCTAssertEqual(#"{"song_ids":[9],"device_id":"\#(phone)"}"#, try firstBody(of: sent))
        // And the list is re-read rather than patched: the server is the
        // truth about an edit four other clients also see.
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the re-read")
    }

    /// A successful move says **nothing**: the list redraws in the new order,
    /// which says it better than a sentence over the top of it.
    func testAMoveSendsThePositionAndSaysNothing() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.moveDown(0)
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the move")

        XCTAssertNil(queue.toast)
        let sent = requests(to: "/api/music/queue/den/move")
        XCTAssertEqual(
            #"{"song_id":7,"to_position":1,"device_id":"\#(phone)"}"#,
            try firstBody(of: sent)
        )
    }

    func testMovingUpSendsThePositionAbove() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.moveUp(1)
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the move")

        let sent = requests(to: "/api/music/queue/den/move")
        XCTAssertEqual(
            #"{"song_id":9,"to_position":0,"device_id":"\#(phone)"}"#,
            try firstBody(of: sent)
        )
    }

    /// The ends of the list send nothing. The server takes `to_position: int
    /// = Field(..., ge=0)`, so a `-1` from an up-arrow on the first row is a
    /// 422 with nothing for the person to do about it.
    func testTheEndsOfTheListCannotBeMovedPastThem() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayMoveUp(0))
        XCTAssertFalse(queue.mayMoveDown(1))
        queue.moveUp(0)
        queue.moveDown(1)
        // And a position outside the list, asked for directly.
        queue.move(try entry(queue, 0), to: 5)
        queue.move(try entry(queue, 0), to: -1)
        await settleUntil({ queue.read.answered })

        XCTAssertTrue(edits().isEmpty, "nothing should have gone out")
        XCTAssertEqual(1, house.reads.current["den"])
    }

    func testAClearSendsTheDeviceAndSaysWhichRoom() async throws {
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
        XCTAssertEqual(#"{"device_id":"\#(phone)"}"#, try firstBody(of: sent))
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
    func testAGuestMayNotRemoveWhatIsPlayingButMayRemoveTheRest() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": guestQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayRemove(try entry(queue, 0)))
        XCTAssertTrue(queue.mayRemove(try entry(queue, 1)))

        queue.remove(try entry(queue, 0))
        await settleUntil({ queue.read.answered })
        XCTAssertTrue(edits().isEmpty, "the playing entry is the core's to refuse")

        queue.remove(try entry(queue, 1))
        await settleUntil({ queue.toast != nil }, "the guest's remove")
        XCTAssertEqual(1, requests(to: "/api/music/queue/den/remove").count)
    }

    /// `editable: false` is the end of all three.
    func testABlockedDeviceSendsNothingAtAll() async throws {
        let (queue, _, house) = rig()
        house.queues.set([
            "den": """
            {"editable":false,"guest":false,
             "blocked_reason":"Jordan's iPad isn't allowed to edit the queue in den",
             "items":[{"song_id":7,"title":"Hey Jude","playing":true},
                       {"song_id":9,"title":"Come Together"}]}
            """,
        ])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayClear)
        XCTAssertFalse(queue.mayMove)
        XCTAssertFalse(queue.mayRemove(try entry(queue, 1)))
        XCTAssertEqual(
            "Jordan's iPad isn't allowed to edit the queue in den. "
                + "You can watch the queue but not change it.",
            queue.watchOnlyNote
        )

        queue.remove(try entry(queue, 1))
        queue.moveDown(0)
        queue.clear()
        await settleUntil({ queue.read.answered })

        XCTAssertTrue(edits().isEmpty)
    }

    // MARK: - one edit at a time

    /// A second edit while one is in flight sends nothing. Two moves racing
    /// each other on one list produce an order neither tap asked for.
    func testASecondEditWhileOneIsInFlightSendsNothing() async throws {
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

        queue.remove(try entry(queue, 1))
        let arrived = await settle(for: park.arrived, what: "the first remove")
        XCTAssertTrue(arrived)
        XCTAssertTrue(queue.busy)

        queue.remove(try entry(queue, 0))
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

        queue.remove(try entry(queue, 1))
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

    // MARK: - Up next in the room (cb780e90), the jump, the push filter

    /// A room with a session: the song playing, one somebody asked for, and
    /// two of the room's own picks.
    private let upNextQueue = """
    {"current_song_id":7,"editable":true,"guest":false,"session_id":"s-1","autoplay":true,
     "up_next":{"session_id":"s-1","mode":"library","autoplay":true,
                "seed":{"track_id":3,"title":"Hey Jude"},"ended":false},
     "items":[{"song_id":7,"pos":0,"title":"Hey Jude","artist":"The Beatles","playing":true,"track_id":3},
              {"song_id":8,"pos":1,"title":"Asked For","artist":"Alex"},
              {"song_id":9,"pos":2,"title":"Pick One","artist":"Kestrel","origin":"auto",
               "reason_label":"same artist","up_next_seq":5,"track_id":11},
              {"song_id":10,"pos":3,"title":"Pick Two","artist":"Kestrel","origin":"auto",
               "reason_label":"90s","up_next_seq":6,"track_id":12}]}
    """

    /// A pick waiting between two songs somebody asked for.
    private let pickBetween = """
    {"current_song_id":7,"editable":true,"guest":false,"session_id":"s-1","autoplay":true,
     "items":[{"song_id":7,"pos":0,"title":"Hey Jude","playing":true},
              {"song_id":9,"pos":1,"title":"Pick One","artist":"Kestrel","origin":"auto","up_next_seq":5},
              {"song_id":8,"pos":2,"title":"Asked For"},
              {"song_id":10,"pos":3,"title":"Also Asked"}]}
    """

    private func bodyFields(_ request: URLRequest?) throws -> [String: Any] {
        let data = try XCTUnwrap(request?.httpBody, "no body was sent")
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    /// A row's tap is the room's own jump — `POST …/play` with the entry's
    /// song id — and never offered on the row that is playing.
    func testQueues_ATapPlaysTheEntryNowThroughTheRoomsJump() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayPlayNow(try entry(queue, 0)), "the song playing is not a jump")
        queue.playNow(try entry(queue, 2))
        await settleUntil({ queue.toast != nil }, "the jump")

        XCTAssertEqual(#"playing "Pick One" in den"#, queue.takeToast())
        let fields = try bodyFields(requests(to: "/api/music/queue/den/play").first)
        XCTAssertEqual(9, fields["song_id"] as? Int)
        XCTAssertEqual(phone, fields["device_id"] as? String)
        XCTAssertTrue(requests(to: "/skip").isEmpty, "no move-and-skip chain")
    }

    /// The push carries every room's now-playing: only **this** room's state
    /// or song re-reads it (6d14aa91).
    func testQueues_APushForAnotherRoomDoesNotReReadThisOne() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        func push(_ data: String) -> StateEvent {
            StateEvent(
                type: "music.now_playing.changed",
                frame: #"{"type":"music.now_playing.changed","data":"# + data + "}"
            )
        }
        // The first one seen re-reads: what the room did before it is unknown.
        queue.received(push(#"{"den":{"state":"play","song":{"file":"a.mp3","title":"Hey Jude"}}}"#))
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the first push")
        // Another room's song changes; this room's is the same.
        queue.received(push(
            #"{"den":{"state":"play","song":{"file":"a.mp3","title":"Hey Jude"}},"kitchen":{"state":"play","song":{"file":"k.mp3"}}}"#
        ))
        for _ in 0..<64 { await Task.yield() }
        XCTAssertEqual(2, house.reads.current["den"], "another room's song re-read this room's queue")
        // This room's song changes.
        queue.received(push(#"{"den":{"state":"play","song":{"file":"b.mp3","title":"Come Together"}}}"#))
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 3 }, "this room's change")
    }

    /// "Less like this" sends the pick's seq and takes back what the session
    /// said at once; the row playing is never one of them.
    func testQueues_LessLikeThisTakesThePicksOutAtOnce() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertFalse(queue.mayLessLike(1), "a song somebody asked for is not a pick")
        XCTAssertTrue(queue.mayLessLike(2))
        queue.lessLike(try entry(queue, 2))
        await settleUntil({ queue.toast != nil }, "the feedback")

        XCTAssertEqual("no more Kestrel in this mix (1 more taken out)", queue.takeToast())
        XCTAssertEqual([7, 8], queue.items.map(\.songID))
        let sent = requests(to: "/api/music/up-next/sessions/s-1/feedback")
        let fields = try bodyFields(sent.first)
        XCTAssertEqual(5, fields["seq"] as? Int)
        XCTAssertEqual("less_like_this", fields["kind"] as? String)
    }

    /// With a pick hidden from the view, a move sends the room's own
    /// position for the row it lands on, not the shown index.
    func testQueues_AMoveAfterAPickWasTakenBackSendsTheRoomsPosition() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": pickBetween])
        house.feedbackAnswer.set(#"{"removed":[5]}"#)
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.lessLike(try entry(queue, 1))
        await settleUntil({ queue.items.count == 3 && !queue.busy }, "the pick taken back")
        XCTAssertEqual([7, 8, 10], queue.items.map(\.songID))

        queue.moveUp(2)
        await settleUntil({ !self.requests(to: "/api/music/queue/den/move").isEmpty }, "the move")
        XCTAssertEqual(
            #"{"song_id":10,"to_position":2,"device_id":"\#(phone)"}"#,
            try firstBody(of: requests(to: "/api/music/queue/den/move"))
        )
    }

    func testQueues_TheAutoplaySwitchPatchesTheRoomsSession() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertTrue(queue.mayAutoplay)
        queue.setAutoplay(false)
        await settleUntil({ queue.toast != nil }, "the switch")

        XCTAssertEqual("autoplay off in den — the queue stops at the end", queue.takeToast())
        let sent = requests(to: "/api/music/up-next/sessions/s-1")
        XCTAssertEqual("PATCH", sent.first?.httpMethod)
        XCTAssertEqual(false, try bodyFields(sent.first)["autoplay"] as? Bool)
    }

    /// A guest gets the jump, the moves and remove — no switch, no "less like
    /// this", no saving, no clear.
    func testQueues_AGuestGetsNoSwitchNoLessLikeNoSaveNoClear() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue.replacingOccurrences(of: #""guest":false"#, with: #""guest":true"#)])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertTrue(queue.mayPlayNow(try entry(queue, 1)))
        XCTAssertTrue(queue.mayMove)
        XCTAssertFalse(queue.mayAutoplay)
        XCTAssertFalse(queue.mayLessLike(2))
        XCTAssertFalse(queue.maySave)
        XCTAssertFalse(queue.mayClear)
    }

    /// Saving the room's queue says the server's counts, the web's sentence.
    func testQueues_SavingTheRoomsQueueSaysWhatTheServerKept() async throws {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertTrue(queue.maySave)
        queue.save.ask()
        queue.save.name = "  Road "
        queue.saveQueue()
        await settleUntil({ queue.toast != nil }, "the save")

        XCTAssertEqual(
            #"Saved as "Road" (3 songs); 1 left out: not songs from the library, 1 repeat saved once."#,
            queue.takeToast()
        )
        XCTAssertFalse(queue.save.open)
        let fields = try bodyFields(requests(to: "/api/music/queue/den/save-playlist").first)
        XCTAssertEqual("Road", fields["name"] as? String)
        XCTAssertEqual(phone, fields["device_id"] as? String)
    }

    /// A name already taken is the server's own sentence.
    func testQueues_ASaveRefusedSaysTheServersSentence() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        house.editStatus.set(409)
        queue.save.ask()
        queue.save.name = "Road"
        queue.saveQueue()
        await settleUntil({ queue.said != nil }, "the refusal")

        XCTAssertEqual("Couldn't save the playlist: playlist named 'Road' already exists (id=3)", queue.said)
        XCTAssertTrue(queue.saidFailed)
        XCTAssertTrue(queue.save.open, "the name field stays, to try another")
    }

    /// An edit answered after the app moved house says nothing
    /// ([[read-after-await-wrong-key]]).
    func testQueues_AnEditAnsweredAfterMovingHouseSaysNothing() async throws {
        let (queue, container, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        queue.playNow(try entry(queue, 1))
        XCTAssertTrue(container.store.setServerURL("http://10.0.0.43:6369"))
        await settleUntil({ !queue.busy }, "the edit")

        XCTAssertNil(queue.toast)
        XCTAssertNil(queue.said)
    }

    /// A playlist still filling the room nudges its open queue to read
    /// again (bd274c2f) — this room on this server, and no other.
    func testQueues_ANudgeForThisRoomReReadsAndOneForAnotherDoesNot() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": twoEntries])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        RoomQueueNudges.shared.nudge(server: server, room: "kitchen")
        RoomQueueNudges.shared.nudge(server: "http://10.0.0.43:6369", room: "den")
        for _ in 0..<64 { await Task.yield() }
        XCTAssertEqual(1, house.reads.current["den"])

        RoomQueueNudges.shared.nudge(server: server, room: "den")
        await settleUntil({ (house.reads.current["den"] ?? 0) >= 2 }, "the nudge")
    }

    /// `stop()` leaves the nudges too.
    func testQueues_StopLeavesTheNudges() async {
        let (queue, _, _) = rig()
        let before = RoomQueueNudges.shared.count
        await started(queue, rooms: ["den"])
        XCTAssertEqual(before + 1, RoomQueueNudges.shared.count)
        queue.stop()
        XCTAssertEqual(before, RoomQueueNudges.shared.count)
    }

    /// The Up next head's words come from the answer, and it sits before the
    /// first pick still to come.
    func testQueues_TheHeadSaysWhatTheMixIsBuiltFrom() async {
        let (queue, _, house) = rig()
        house.queues.set(["den": upNextQueue])
        await started(queue, rooms: ["den"])
        defer { queue.stop() }

        XCTAssertEqual("based on Hey Jude", queue.upNextBasis)
        XCTAssertNil(queue.upNextLine, "picks are waiting and autoplay is on")
        XCTAssertEqual([.entry(0), .entry(1), .upNextHead, .entry(2), .entry(3)], queue.lines)
        XCTAssertTrue(queue.isWaitingPick(2))
        XCTAssertFalse(queue.isWaitingPick(1))
    }
}
