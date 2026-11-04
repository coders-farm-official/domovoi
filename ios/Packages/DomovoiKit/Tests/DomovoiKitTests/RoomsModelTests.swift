import Foundation
import XCTest
@testable import DomovoiKit

/// Rooms' data layer against a server that is not a server: which read is made
/// when, what each write sends, what it refuses to send, and what it says
/// afterwards.
///
/// Android's satellites screen is five composables and its JVM suite tests
/// none of them, so the debounce, the optimistic switch and its rollback, the
/// clock-time guard, the double-tap guard and the room that an answer belongs
/// to are specified by nothing over there. The point of this file is that they
/// are specified here.
@MainActor
final class RoomsModelTests: XCTestCase {

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

    // MARK: - the rig

    /// Every wait the model asks for, with a counted allowance per period —
    /// ``MusicModelTests``' rig, because this model also has two loops of
    /// different periods (the one-second tick and the 300 ms volume debounce)
    /// and a test needs to let one turn of one of them through.
    private final class Waits: Sendable {
        let requested = Shared<[Int64]>([])
        private let budget = Shared<[Int64: Int]>([:])
        private let opened = Shared<Set<Int64>>([])

        func allow(_ ms: Int64, times: Int = 1) {
            budget.mutate { $0[ms, default: 0] += times }
        }

        func open(_ ms: Int64) {
            opened.mutate { $0.insert(ms) }
        }

        func asked(_ ms: Int64) -> Int { requested.current.filter { $0 == ms }.count }

        var sleeper: @Sendable (Int64) async throws -> Void {
            { [requested, budget, opened] ms in
                requested.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    // Cancellation **before** the budget: the other way round,
                    // a debounce the next drag had already cancelled wakes up
                    // and spends the allowance meant for its successor — the
                    // rig committing the bug the test is about.
                    try Task.checkCancellation()
                    if opened.current.contains(ms) { return }
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

    /// A house with two rooms — one online and playing, one offline — and
    /// answers for every path this screen reads or writes.
    ///
    /// Each piece is a ``Shared`` so a test can change what the house says
    /// **between** requests: a room that went quiet, a socket that died under
    /// the server's active-sessions map, a timer that fired first.
    private final class House: Sendable {

        /// `GET /api/satellites`. The kitchen reports everything; the office
        /// is the nine-field row an older server sends.
        let roster = Shared("""
        [{"room_id":"kitchen","status":"online","last_connected_at":"2026-11-02T18:00:00Z",
          "last_seen_at":"2026-11-02T19:30:00Z",
          "now_playing":{"state":"play","elapsed_sec":42,
            "song":{"title":"Marmalade Skies","artist":"The Beatles","duration_sec":431}},
          "wifi":{"rx_mbits":54.4,"tx_mbits":48.2,"ssid":"house","signal_dbm":-52},
          "mpd_ports":{"control":6600,"http":8000},"version":"aaaa111","volume":40,
          "full_duplex":true,"timers_own_only":false,"voice":"glados"},
         {"room_id":"office","status":"offline","last_connected_at":"2026-10-14T09:00:00Z"}]
        """)

        /// What `announced_to` comes back with — `[]` is the dead-socket case.
        let announcedTo = Shared("[\"kitchen\"]")
        let announceAllTo = Shared("[\"kitchen\"]")
        let announceStatus = Shared(200)

        let timers = Shared("""
        [{"id":1,"label":"pasta","is_reminder":false,"expires_at":"2026-11-02T19:35:00Z"},
         {"id":3,"label":null,"message":null,"is_reminder":true,
          "expires_at":"2026-11-02T20:00:00Z","masked":true}]
        """)
        let timerDeleteStatus = Shared(204)

        let quietHours = Shared(#"{"room_id":"kitchen","mode":"house","start":null,"end":null}"#)
        let quietPutStatus = Shared(200)
        let scopePutStatus = Shared(200)
        let volumeStatus = Shared(200)
        let addStatus = Shared(200)
        let addBody = Shared(#"{"queued":true,"message":null}"#)

        /// Every write's path and body, in order — so a test can say what left
        /// the phone rather than what the model thinks it sent.
        let writes = Shared<[(path: String, body: String)]>([])

        func wrote(to path: String) -> [String] {
            writes.current.filter { $0.path == path }.map(\.body)
        }

        /// The same bodies **decoded**, for a field whose text encoding is the
        /// serialiser's business rather than this app's (a URL's escaped
        /// solidus, a dictionary's key order).
        func sentJSON(to path: String) -> [[String: Any]] {
            wrote(to: path).compactMap {
                guard let bytes = $0.data(using: .utf8) else { return nil }
                return (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any]
            }
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let path = parts.path
                let method = request.httpMethod ?? "GET"
                if method != "GET" {
                    let body = request.httpBody
                        ?? request.httpBodyStream.flatMap { stream -> Data? in
                            stream.open()
                            defer { stream.close() }
                            var data = Data()
                            var buffer = [UInt8](repeating: 0, count: 4096)
                            while stream.hasBytesAvailable {
                                let read = stream.read(&buffer, maxLength: 4096)
                                if read <= 0 { break }
                                data.append(contentsOf: buffer[0..<read])
                            }
                            return data
                        }
                    writes.mutate {
                        $0.append((path, String(data: body ?? Data(), encoding: .utf8) ?? ""))
                    }
                }
                switch path {
                case "/api/health":
                    // The identity gate's probe: a trusted server has to prove
                    // itself before any read goes out, so a house that did not
                    // answer this would refuse every request below and every
                    // test over it would assert about nothing.
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/satellites":
                    return .json(roster.current)
                case "/api/config/version":
                    return .json(#"{"sha":"aaaa111"}"#)
                case "/api/capabilities":
                    return .json(#"{"handler_display":[{"name":"podcasts","tone":"media"}]}"#)
                case "/api/satellites/announce-all":
                    guard announceStatus.current == 200 else {
                        return .json(#"{"detail":"no rooms connected"}"#, status: announceStatus.current)
                    }
                    return .json(#"{"announced_to":\#(announceAllTo.current)}"#)
                case "/api/music/add-by-url":
                    guard addStatus.current == 200 else {
                        return .json(#"{"detail":"no fulfiller plugin recognises that address"}"#,
                                     status: addStatus.current)
                    }
                    return .json(addBody.current)
                default:
                    break
                }
                if path.hasSuffix("/announce") {
                    guard announceStatus.current == 200 else {
                        return .json(#"{"detail":"that room is not connected"}"#, status: announceStatus.current)
                    }
                    return .json(#"{"announced_to":\#(announcedTo.current)}"#)
                }
                if path.hasSuffix("/timers") { return .json(timers.current) }
                if path.contains("/timers/") {
                    guard timerDeleteStatus.current == 204 else {
                        return .json(#"{"detail":"that timer is gone"}"#, status: timerDeleteStatus.current)
                    }
                    return .json("", status: 204)
                }
                if path.hasSuffix("/quiet-hours") {
                    guard method == "GET" else {
                        guard quietPutStatus.current == 200 else {
                            return .json(#"{"detail":"mode must be one of house, custom, off"}"#,
                                         status: quietPutStatus.current)
                        }
                        return .json("{}")
                    }
                    return .json(quietHours.current)
                }
                if path.hasSuffix("/timer-announcements") {
                    guard scopePutStatus.current == 200 else {
                        return .json(#"{"detail":"nope"}"#, status: scopePutStatus.current)
                    }
                    return .json(#"{"room_id":"kitchen","own_only":true}"#)
                }
                if path.hasSuffix("/volume") {
                    guard volumeStatus.current == 200 else {
                        return .json(#"{"detail":"that board has no mixer"}"#, status: volumeStatus.current)
                    }
                    return .json("{}")
                }
                if path.hasSuffix("/health") {
                    return .json("""
                    {"latest":{"sample":{"reboot_needed":"network settings pending",
                      "net_step":{"state":"failed","detail":"wpa_supplicant refused the country code"},
                      "hostname":"domovoi-kitchen"}}}
                    """)
                }
                if path.hasSuffix("/config") {
                    return .json("""
                    {"reported":true,"fields":[
                      {"name":"mic_enabled","label":"Microphone","group":"audio","type":"bool","value":true}]}
                    """)
                }
                if path.hasSuffix("/sessions") || path.contains("/sessions?") {
                    return .json("""
                    [{"id":"3fa85f64571745","started_at":"2026-11-02T19:00:00Z",
                      "last_activity":"2026-11-02T19:01:12Z","person_id":4,"intent_count":2}]
                    """)
                }
                if path.hasSuffix("/conversations") {
                    return .json("""
                    [{"id":11,"session_id":"3fa85f64571745","at":"2026-11-02T19:00:05Z",
                      "user_text":"put the marmalade on the list","assistant_text":"added it",
                      "matched_handler":"lists"}]
                    """)
                }
                if path.hasSuffix("/notes") {
                    return .json(#"[{"id":3,"body":"the bins go out on Tuesday","captured_at":"2026-11-01T09:00:00Z"}]"#)
                }
                if path.hasSuffix("/recently-played") {
                    return .json("""
                    [{"id":9,"source":"library","title":"Marmalade Skies","can_add":false},
                     {"id":10,"source":"podcasts","title":"Episode 4","channel":"Some Show",
                      "url":"https://example.test/4","can_add":true}]
                    """)
                }
                if path.hasSuffix("/dropin/end") { return .json("{}") }
                return .json("{}", status: 404)
            }
        }
    }

    private func rig(
        house: House = House(),
        waits: Waits = Waits(),
        address: String? = nil
    ) -> (RoomsModel, House, Waits, InMemoryShellStore) {
        house.install()
        let at = address ?? server
        let store = InMemoryShellStore(serverURL: at, token: "household-abc")
        // Trusted **with its pin**, which is what a paired phone holds: an
        // address trusted with no pin makes the gate probe and refuse until it
        // is satisfied, and `setServerURL` refuses one that is not trusted at
        // all — both halves are needed to move house in a test.
        store.trustServer(at, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = RoomsModel(in: container, nowMs: { 0 }, wait: waits.sleeper)
        return (model, house, waits, store)
    }

    /// A park's `otherwise`: the identity probe signed, the roster as this
    /// house has it, and `{}` for everything else.
    nonisolated private static func plain(_ house: House) -> @Sendable (URLRequest) -> StubURLProtocol.Answer {
        { request in
            guard let parts = request.url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })
            else { return .json("{}") }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if parts.path == "/api/satellites" { return .json(house.roster.current) }
            return .json("{}")
        }
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

    /// Every task that could have started gets its chance — for asserting a
    /// **negative**.
    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    private func requests(to path: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == path }
    }

    /// Every request the stub has recorded, one per line, for a count that
    /// disagreed with this case.
    ///
    /// ``StubURLProtocol``'s script is **process-global** and is reset in
    /// `setUp`/`tearDown`, so a task still in flight when an earlier case
    /// returned appends to `recorded` after the reset and lands in this
    /// case's log. That is the mechanism behind
    /// `testEachTabIsReadWhenItIsShownAndNotBefore`'s flake — 1 red of 2 runs
    /// at `67ca5e8e`, 2 of 6 across the batch, every failure one of its
    /// `requests(to:).count` assertions, and no run of this file alone has
    /// ever reproduced it.
    ///
    /// The cure is cancelling the read when its model goes away, which is
    /// `ApiRead`'s and not this file's. Until then this prints the whole log
    /// beside the number, so the next red names the request rather than the
    /// count: a path this case never asks for is a leak from another case, and
    /// one it does ask for is a real double read.
    private func printTheWholeLog(_ why: String) {
        print("ROOMS REQUEST LOG (\(why)) — \(StubURLProtocol.requestCount) in all:")
        for (index, made) in StubURLProtocol.recorded.enumerated() {
            print("  [\(index)] \(made.httpMethod ?? "?") \(made.url?.absoluteString ?? "nil")")
        }
    }

    /// `requests(to: path).count == want`, and the whole log when it is not.
    private func assertAsked(
        _ want: Int,
        _ path: String,
        _ message: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let got = requests(to: path).count
        if got != want { printTheWholeLog("\(path): wanted \(want), got \(got)") }
        XCTAssertEqual(want, got, message, file: file, line: line)
    }

    private func loaded(_ model: RoomsModel) async {
        model.start()
        await settleUntil({ !model.rooms.isEmpty }, "the roster")
    }

    // MARK: - the roster

    func testTheRosterReadsOnceAndCountsWhatItGot() async {
        let (model, _, _, _) = rig()
        XCTAssertEqual(.loading, model.content, "before anything has answered")
        await loaded(model)
        XCTAssertEqual(["kitchen", "office"], model.rooms.map(\.roomID))
        XCTAssertEqual("1 online · 1 offline · 2 provisioned", model.summary)
        XCTAssertEqual(1, model.onlineCount)
        if case .rooms(let rows) = model.content {
            XCTAssertEqual(2, rows.count)
        } else {
            XCTFail("the roster should be rows, not \(model.content)")
        }
        await settleUntil({ model.coreSha != nil }, "the version read")
        XCTAssertEqual("aaaa111", model.coreSha)
        XCTAssertFalse(
            roomsNeedsUpgrade(coreSha: model.coreSha, roomVersion: model.rooms[0].version),
            "the kitchen is on the server's SHA"
        )
        XCTAssertFalse(
            roomsNeedsUpgrade(coreSha: model.coreSha, roomVersion: model.rooms[1].version),
            "and the office reports none, which is unknown rather than behind"
        )
    }

    /// A house with no rooms is **empty**, and a failed read with nothing to
    /// show is **failed** — two different sentences, which is why `content` is
    /// a decision and not three `if`s in a view.
    func testTheEmptyAndFailedStatesAreNotTheSame() async {
        let house = House()
        house.roster.set("[]")
        let (model, _, _, _) = rig(house: house)
        model.start()
        await settleUntil({ model.content == .empty }, "the empty roster")

        let broken = House()
        broken.roster.set("not json at all")
        let (second, _, _, _) = rig(house: broken)
        second.start()
        await settleUntil({ if case .failed = second.content { return true } else { return false } }, "the failure")
    }

    /// A read that fails **after** rows have landed keeps them: blanking a
    /// roster over one bad response is worse than showing one a few seconds
    /// old, and ``ApiRead`` is deliberately built that way.
    func testAFailedRefreshKeepsTheRoomsItHad() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        house.roster.set("not json at all")
        model.roster.refresh()
        await settleUntil({ model.roster.error != nil }, "the failed refresh")
        XCTAssertEqual(["kitchen", "office"], model.rooms.map(\.roomID))
        XCTAssertTrue(model.roster.isStale, "and it is marked stale rather than silent")
    }

    // MARK: - the tick

    /// The clocks move between reads, and the base resets when a payload
    /// **says something different about the songs** — not on every answer, or
    /// the clocks would never advance.
    func testTheSongClockExtrapolatesAndResetsOnAFreshPayload() async {
        let clock = Shared<Int64>(0)
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
        let waits = Waits()
        let model = RoomsModel(in: container, nowMs: { clock.current }, wait: waits.sleeper)
        await loaded(model)
        XCTAssertEqual(0, model.extraSec)

        clock.set(3_000)
        model.tick()
        XCTAssertEqual(3, model.extraSec, "three seconds since the payload landed")
        XCTAssertEqual(45, roomsElapsedSec(model.rooms[0].nowPlaying, extraSec: model.extraSec), "42 + 3")

        // The same payload again: the base does not move, so the clock keeps
        // counting from where it was.
        model.roster.refresh()
        await everythingLands()
        XCTAssertEqual(3, model.extraSec, "an identical answer is not a reason to reset")

        // A different song: the base resets to the server's canonical reading.
        house.roster.set("""
        [{"room_id":"kitchen","status":"online",
          "now_playing":{"state":"play","elapsed_sec":5,
            "song":{"title":"Something Else","duration_sec":200}}},
         {"room_id":"office","status":"offline"}]
        """)
        model.roster.refresh()
        await settleUntil({ model.rooms.first?.nowPlaying?.song?.title == "Something Else" }, "the new song")
        XCTAssertEqual(0, model.extraSec, "the base follows the payload")
    }

    // MARK: - opening a room

    /// Opening a room asks for its health and **nothing else**; each tab's
    /// read is made when that tab is first shown, and once.
    func testEachTabIsReadWhenItIsShownAndNotBefore() async {
        let (model, _, _, _) = rig()
        await loaded(model)
        model.open("kitchen")
        await settleUntil({ model.openReads?.health.data != nil }, "the health read")
        XCTAssertEqual("kitchen", model.openRoomID)
        XCTAssertEqual(.overview, model.tab)
        XCTAssertTrue(requests(to: "/api/satellites/kitchen/notes").isEmpty, "nothing else was asked for")
        XCTAssertTrue(requests(to: "/api/satellites/kitchen/timers").isEmpty)
        // The overview's facts are there from the first screen.
        XCTAssertEqual("network settings pending", roomsRebootNeeded(model.openReads?.health.data))
        XCTAssertEqual("failed", roomsNetStepLine(model.openReads?.health.data)?.word)

        model.select(.timers)
        await settleUntil({ model.openReads?.timers.data?.isEmpty == false }, "the timers read")
        assertAsked(1, "/api/satellites/kitchen/timers", "the timers tab was read once when shown")
        model.select(.overview)
        model.select(.timers)
        await nothingHappens()
        assertAsked(1, "/api/satellites/kitchen/timers", "a tab is read once")

        // The sessions tab needs both reads, because it shows each session's
        // own turns.
        model.select(.sessions)
        await settleUntil({ model.openReads?.sessions.data?.isEmpty == false }, "the sessions read")
        await settleUntil({ model.openReads?.conversations.data?.isEmpty == false }, "its turns")
        let turns = roomsTurns(
            of: model.openReads?.sessions.data?[0] ?? RoomsSession(),
            in: model.openReads?.conversations.data ?? []
        )
        XCTAssertEqual([11], turns.map(\.id))
    }

    /// The open room is read from the roster **every time**, so a room that
    /// goes offline while its detail is up says so.
    func testTheOpenRoomFollowsTheRosterRatherThanACopy() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.open("kitchen")
        XCTAssertTrue(model.openRoom?.online == true)
        house.roster.set("""
        [{"room_id":"kitchen","status":"offline","last_seen_at":"2026-11-02T19:30:00Z"},
         {"room_id":"office","status":"offline"}]
        """)
        model.roster.refresh()
        await settleUntil({ model.openRoom?.online == false }, "the room going offline")
        XCTAssertEqual("kitchen", model.openRoomID, "and it stays open")
    }

    // MARK: - announce to one room

    /// The toast is decided by **`announced_to`**: a 200 with an empty list
    /// means the socket under the server's active-sessions map is dead, and
    /// that is the whole reason this screen reads the body at all.
    func testAnnounceSaysWhatActuallyHappened() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let kitchen = model.rooms[0]

        // Nothing typed: a sentence, not a silent no-op.
        model.announce(to: kitchen)
        XCTAssertEqual("type a message first", model.takeToast())
        XCTAssertTrue(house.wrote(to: "/api/satellites/kitchen/announce").isEmpty, "and nothing was sent")

        // An offline room, checked **after** the empty message, as Android
        // orders it.
        let office = model.rooms[1]
        model.reads(for: "office").announceDraft = "dinner's ready"
        model.announce(to: office)
        XCTAssertEqual("office is offline", model.takeToast())
        XCTAssertTrue(house.wrote(to: "/api/satellites/office/announce").isEmpty)

        // It took it.
        model.reads(for: "kitchen").announceDraft = "  dinner's ready  "
        model.announce(to: kitchen)
        await settleUntil({ model.toast != nil }, "the announce")
        XCTAssertEqual("announced to kitchen", model.takeToast())
        XCTAssertEqual(
            [#"{"message":"dinner's ready"}"#],
            house.wrote(to: "/api/satellites/kitchen/announce"),
            "trimmed"
        )
        XCTAssertEqual("", model.reads(for: "kitchen").announceDraft, "and the field is cleared")

        // A 200 with an empty `announced_to`: accepted, never played.
        house.announcedTo.set("[]")
        model.reads(for: "kitchen").announceDraft = "are you there"
        model.announce(to: kitchen)
        await settleUntil({ model.toast != nil }, "the dead-socket announce")
        XCTAssertEqual(
            "kitchen accepted the request but didn't play — connection may be dead",
            model.takeToast()
        )
    }

    func testAFailedAnnounceSaysTheServersOwnWords() async {
        let house = House()
        house.announceStatus.set(503)
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.reads(for: "kitchen").announceDraft = "hello"
        model.announce(to: model.rooms[0])
        await settleUntil({ model.toast != nil }, "the failure")
        let said = model.takeToast() ?? ""
        XCTAssertTrue(said.hasPrefix("announce failed: "), said)
        XCTAssertTrue(said.contains("that room is not connected"), said)
        XCTAssertNil(model.reads(for: "kitchen").busy, "and the control is offered again")
        XCTAssertEqual("hello", model.reads(for: "kitchen").announceDraft, "the words are not thrown away")
    }

    // MARK: - broadcast

    func testBroadcastReusesHomesThreeOutcomes() async {
        let house = House()
        house.roster.set("""
        [{"room_id":"kitchen","status":"online"},{"room_id":"office","status":"online"}]
        """)
        let (model, _, _, _) = rig(house: house)
        await loaded(model)

        model.broadcastDraft = "   "
        model.broadcast()
        XCTAssertEqual("type a message first", model.takeToast())

        // Partial delivery names who took it.
        model.broadcastDraft = "dinner's ready"
        model.broadcast()
        await settleUntil({ model.toast != nil }, "the broadcast")
        XCTAssertEqual("broadcast partial — 1/2 reached (kitchen)", model.takeToast())
        XCTAssertEqual("", model.broadcastDraft)

        // Nothing took it.
        house.announceAllTo.set("[]")
        model.broadcastDraft = "anyone?"
        model.broadcast()
        await settleUntil({ model.toast != nil }, "the dead broadcast")
        XCTAssertEqual("broadcast queued but no rooms accepted it (dead connections?)", model.takeToast())

        // Everything took it.
        house.announceAllTo.set("[\"kitchen\",\"office\"]")
        model.broadcastDraft = "tea"
        model.broadcast()
        await settleUntil({ model.toast != nil }, "the full broadcast")
        XCTAssertEqual("broadcasted to 2 rooms", model.takeToast())
    }

    func testBroadcastRefusesWhenTheHouseHasNothingOnline() async {
        let house = House()
        house.roster.set(#"[{"room_id":"office","status":"offline"}]"#)
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.broadcastDraft = "hello"
        model.broadcast()
        XCTAssertEqual("no rooms connected — nothing to broadcast to", model.takeToast())
        await nothingHappens()
        XCTAssertTrue(house.wrote(to: "/api/satellites/announce-all").isEmpty)
    }

    // MARK: - volume

    /// A drag is dozens of values and **one** POST: the level that sticks must
    /// be the one the thumb was left on, not whichever request the server
    /// happened to handle last.
    func testADragSendsOneLevelAndItIsTheLastOne() async {
        let house = House()
        let waits = Waits()
        let (model, _, _, _) = rig(house: house, waits: waits)
        await loaded(model)
        let kitchen = model.rooms[0]

        for level in [45, 50, 55, 60, 65] {
            model.volumeDragged(kitchen, to: level)
        }
        XCTAssertEqual(65, model.reads(for: "kitchen").volumeDraft, "the slider shows it at once")
        XCTAssertTrue(model.reads(for: "kitchen").volumeDirty)
        await nothingHappens()
        XCTAssertTrue(house.wrote(to: "/api/satellites/kitchen/volume").isEmpty, "nothing sent while it settles")

        waits.open(RoomsModel.volumeDebounceMs)
        await settleUntil({ model.toast != nil }, "the volume POST")
        XCTAssertEqual([#"{"level":65}"#], house.wrote(to: "/api/satellites/kitchen/volume"))
        XCTAssertEqual("kitchen volume set to 65%", model.takeToast())
        XCTAssertFalse(model.reads(for: "kitchen").volumeDirty, "and the slider follows the server again")
    }

    /// A nudge is one discrete value, so it goes **at once** — and it is
    /// computed off the level on screen, which is the server's until something
    /// touches it.
    func testANudgeCommitsAtOnceFromTheLevelOnScreen() async {
        let house = House()
        let waits = Waits()
        let (model, _, _, _) = rig(house: house, waits: waits)
        await loaded(model)
        XCTAssertEqual(40, model.reads(for: "kitchen").volumeLevel(model.rooms[0]).level)
        XCTAssertTrue(model.reads(for: "kitchen").volumeLevel(model.rooms[0]).known)
        model.volumeNudged(model.rooms[0], by: 5)
        await settleUntil({ model.toast != nil }, "the nudge")
        XCTAssertEqual([#"{"level":45}"#], house.wrote(to: "/api/satellites/kitchen/volume"))
        XCTAssertEqual(0, waits.asked(RoomsModel.volumeDebounceMs), "no debounce for a nudge")
    }

    func testAFailedVolumeHandsTheSliderBackToTheServer() async {
        let house = House()
        house.volumeStatus.set(500)
        let waits = Waits()
        let (model, _, _, _) = rig(house: house, waits: waits)
        await loaded(model)
        model.volumeNudged(model.rooms[0], by: -5)
        await settleUntil({ model.toast != nil }, "the failure")
        XCTAssertTrue((model.takeToast() ?? "").hasPrefix("volume failed: "))
        XCTAssertFalse(
            model.reads(for: "kitchen").volumeDirty,
            "a drag that failed must not leave the slider on a level the room never took"
        )
    }

    // MARK: - "Only reminders for this device"

    func testTheSwitchMovesAtOnceAndRollsBackWhenItFails() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let kitchen = model.rooms[0]
        let reads = model.reads(for: "kitchen")
        XCTAssertFalse(reads.timerScopeOn(kitchen), "off is every room's default")

        model.setTimerScope(kitchen, on: true)
        XCTAssertTrue(reads.timerScopeOn(kitchen), "it moves before the server answers")
        await settleUntil({ model.toast != nil }, "the PUT")
        XCTAssertEqual([#"{"own_only":true}"#], house.wrote(to: "/api/satellites/kitchen/timer-announcements"))
        XCTAssertEqual("kitchen now announces only its own timers and reminders", model.takeToast())

        // Now one that fails: the switch goes back where it was.
        house.scopePutStatus.set(403)
        model.setTimerScope(kitchen, on: false)
        XCTAssertFalse(reads.timerScopeOn(kitchen))
        await settleUntil({ model.toast != nil }, "the refused PUT")
        XCTAssertTrue(
            (model.takeToast() ?? "").hasPrefix("change timer announcements failed: "),
            "and it says what the house said"
        )
        XCTAssertTrue(reads.timerScopeOn(kitchen), "rolled back to the position it was in")
    }

    /// The optimistic switch is **dropped** once the roster agrees with it.
    ///
    /// Without this the override is permanent: ``RoomsRoomModel/timerScopeOn(_:)``
    /// prefers it, so a change made from the dashboard or another phone could
    /// never move the switch again while the screen is up. Android drops it in
    /// a `LaunchedEffect(s.timers_own_only)` (`SatOverviewTab.kt:415-417`) and
    /// this is that half.
    func testTheSwitchFollowsTheServerAgainOnceItAgrees() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let reads = model.reads(for: "kitchen")

        model.setTimerScope(model.rooms[0], on: true)
        await settleUntil({ model.toast != nil }, "the PUT")
        _ = model.takeToast()
        XCTAssertEqual(true, reads.timerScopeOverride, "still optimistic until the roster lands")

        // The server caught up. Only now may the override go.
        house.roster.set(house.roster.current.replacingOccurrences(
            of: #""timers_own_only":false"#,
            with: #""timers_own_only":true"#
        ))
        model.roster.refresh()
        await settleUntil({ model.rooms.first?.timersOwnOnly == true }, "the fresh roster")
        reads.followServerTimerScope(model.rooms[0])
        XCTAssertNil(reads.timerScopeOverride, "the override agreed with the server, so it goes")
        XCTAssertTrue(reads.timerScopeOn(model.rooms[0]), "and the switch still reads on")

        // A roster that says the OTHER thing is a change this phone has not
        // seen land yet; dropping the override there would flick the switch
        // back under the finger, so it stays.
        reads.timerScopeOverride = false
        reads.followServerTimerScope(model.rooms[0])
        XCTAssertEqual(false, reads.timerScopeOverride, "a disagreeing roster does not clear it")
    }

    /// A board that never reported its level still shows the level being set.
    ///
    /// ``RoomsStrings/volumeHelp(room:levelKnown:online:)`` tells the person to
    /// move the slider in exactly this state, so a read-out frozen on
    /// `unknown` while the thumb moves contradicts the screen's own
    /// instruction. Android's read-out is `known || touched`
    /// (`SatOverviewTab.kt:291-295`).
    func testASliderOnABoardThatReportsNoLevelStillShowsWhatIsBeingSet() async {
        let house = House()
        // The nine-field row: no `volume` at all.
        house.roster.set("""
        [{"room_id":"kitchen","status":"online","last_connected_at":"2026-11-02T18:00:00Z"}]
        """)
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let reads = model.reads(for: "kitchen")
        let before = reads.volumeLevel(model.rooms[0])
        XCTAssertEqual(50, before.level, "the fallback, since nothing was reported")
        XCTAssertFalse(before.known, "and it is honestly unknown until something touches it")

        model.volumeDragged(model.rooms[0], to: 72)
        let after = reads.volumeLevel(model.rooms[0])
        XCTAssertEqual(72, after.level)
        XCTAssertTrue(after.known, "a touched slider has a level worth printing")
    }

    // MARK: - quiet hours

    /// Custom hours that are not both clock times **send nothing**: a screen
    /// that posted `7:5` would have the server decide what it meant.
    func testBadCustomHoursAreNotSentAndSayWhy() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let kitchen = model.rooms[0]
        let reads = model.reads(for: "kitchen")
        reads.quietStartDraft = "22:00"
        reads.quietEndDraft = "7:5"
        model.saveQuietHours(kitchen, mode: "custom")
        XCTAssertEqual("times look like 22:00", model.takeToast())
        await nothingHappens()
        XCTAssertTrue(house.wrote(to: "/api/satellites/kitchen/quiet-hours").isEmpty)

        // Corrected, and both times are normalised on the way out.
        reads.quietEndDraft = "7:05"
        reads.quietModeDraft = "custom"
        model.saveQuietHours(kitchen, mode: "custom")
        await settleUntil({ model.toast != nil }, "the PUT")
        let sent = house.wrote(to: "/api/satellites/kitchen/quiet-hours").first ?? ""
        XCTAssertTrue(sent.contains("\"start\":\"22:00\""), sent)
        XCTAssertTrue(sent.contains("\"end\":\"07:05\""), sent)
        XCTAssertTrue(sent.contains("\"mode\":\"custom\""), sent)
        XCTAssertEqual("kitchen keeps quiet 22:00–07:05", model.takeToast())
        XCTAssertNil(reads.quietModeDraft, "the radios follow the server again")
    }

    func testPickingTheHouseModeSendsTheModeAlone() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.saveQuietHours(model.rooms[0], mode: "off")
        await settleUntil({ model.toast != nil }, "the PUT")
        XCTAssertEqual([#"{"mode":"off"}"#], house.wrote(to: "/api/satellites/kitchen/quiet-hours"))
        XCTAssertEqual("kitchen has no quiet hours now", model.takeToast())
    }

    // MARK: - timers

    /// A **404 is not a failure**: the timer fired, or somebody else cancelled
    /// it, between the read and the tap. Android toasts "cancel failed: HTTP
    /// 404" for that.
    func testCancellingATimerThatHasAlreadyGoneIsNotAFailure() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.open("kitchen")
        model.select(.timers)
        await settleUntil({ model.openReads?.timers.data?.isEmpty == false }, "the timers read")
        let timers = model.openReads?.timers.data ?? []
        XCTAssertEqual("reminder (words hidden)", roomsTimerLabel(timers[1]), "rule M1, from the wire")

        model.cancelTimer(model.rooms[0], timers[0])
        await settleUntil({ model.toast != nil }, "the DELETE")
        XCTAssertEqual("cancelled timer #1", model.takeToast())

        house.timerDeleteStatus.set(404)
        model.cancelTimer(model.rooms[0], timers[1])
        await settleUntil({ model.toast != nil }, "the second DELETE")
        XCTAssertEqual("that one already finished", model.takeToast())
    }

    /// Two taps on one row send **one** DELETE — the double-tap guard. The
    /// row's `cancel` is a 44-point target in a list that is still refreshing,
    /// and the second request would 404 and say so for no reason.
    func testTwoTapsOnOneTimerSendOneDelete() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let timer = RoomsTimer(id: 1, label: "pasta", expiresAt: "2026-11-02T19:35:00Z")
        model.cancelTimer(model.rooms[0], timer)
        model.cancelTimer(model.rooms[0], timer)
        await settleUntil({ model.toast != nil }, "the DELETE")
        await everythingLands()
        XCTAssertEqual(1, house.wrote(to: "/api/satellites/kitchen/timers/1").count)
    }

    // MARK: - drop in

    func testHangUpIsTheOneDropInWriteAndItReReadsTheRoster() async {
        let house = House()
        house.roster.set("""
        [{"room_id":"kitchen","status":"online","full_duplex":true,"in_call_with":"office"},
         {"room_id":"office","status":"online","full_duplex":true,"in_call_with":"kitchen"}]
        """)
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        XCTAssertEqual(.inCall("office"), roomsDropInState(model.rooms[0]))
        let before = requests(to: "/api/satellites").count
        model.hangUp(model.rooms[0])
        await settleUntil({ model.toast != nil }, "the hang up")
        XCTAssertEqual("hung up kitchen", model.takeToast())
        XCTAssertEqual(1, house.wrote(to: "/api/satellites/kitchen/dropin/end").count)
        await settleUntil({ self.requests(to: "/api/satellites").count > before }, "the roster re-read")
    }

    // MARK: - add to library

    /// `can_add` does not promise the call will be accepted, so a refusal
    /// shows **the server's own words** and the optimistic mark goes back.
    func testARefusedAddSaysWhatTheHouseSaidAndUnmarksTheRow() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.open("kitchen")
        model.select(.recentlyPlayed)
        await settleUntil({ model.openReads?.played.data?.isEmpty == false }, "the played read")
        let rows = model.openReads?.played.data ?? []
        XCTAssertFalse(rows[0].canAdd, "a library row has nothing to add")

        // A library row is not sent at all, whatever a view asks for.
        model.addToLibrary(model.rooms[0], rows[0])
        await nothingHappens()
        XCTAssertTrue(house.wrote(to: "/api/music/add-by-url").isEmpty)

        house.addStatus.set(403)
        model.addToLibrary(model.rooms[0], rows[1])
        XCTAssertTrue(model.openReads?.queuedAdds.contains(10) == true, "marked while it is in flight")
        await settleUntil({ model.toast != nil }, "the refused add")
        XCTAssertEqual("no fulfiller plugin recognises that address", model.takeToast())
        XCTAssertFalse(
            model.openReads?.queuedAdds.contains(10) == true,
            "nothing was queued, so the mark goes back"
        )
        // Decoded rather than matched as text: `JSONSerialization` escapes a
        // solidus (`https:\/\/example.test\/4`), which is valid JSON and not
        // something an assertion should be pinned to.
        let sent = house.sentJSON(to: "/api/music/add-by-url").first ?? [:]
        XCTAssertEqual("https://example.test/4", sent["url"] as? String)
        XCTAssertEqual("kitchen", sent["room_id"] as? String)
        XCTAssertEqual("Episode 4", sent["title"] as? String)
    }

    func testAQueuedAddTakesTheServersGracefulAbsenceCopy() async {
        let house = House()
        house.addBody.set(#"{"queued":false,"message":"no fulfiller plugin is installed"}"#)
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        model.open("kitchen")
        model.select(.recentlyPlayed)
        await settleUntil({ model.openReads?.played.data?.isEmpty == false }, "the played read")
        model.addToLibrary(model.rooms[0], model.openReads?.played.data?[1] ?? RoomsPlayed(canAdd: true))
        await settleUntil({ model.toast != nil }, "the add")
        XCTAssertEqual("no fulfiller plugin is installed", model.takeToast())
    }

    // MARK: - [[read-after-await-wrong-key]]

    /// **The house is half of every key.** A move inside the round trip must
    /// leave nothing of the old house's answer behind: no toast about a room
    /// that is not in this house, and no cleared field in a house whose field
    /// was never sent.
    ///
    /// The switch happens **inside** the request — the only honest test of
    /// this shape — by parking the announce at the stub until the move has
    /// happened.
    func testAnAnnounceAnsweredAfterMovingHouseSaysNothing() async {
        // The rig **first**, and the park over it afterwards: `House.install`
        // replaces the dispatcher, so parking before it would be parking a
        // dispatcher nothing ever asks.
        let house = House()
        let (model, _, _, store) = rig(house: house)
        await loaded(model)
        let park = parkRequest(
            to: "/api/satellites/kitchen/announce",
            answering: { .json(#"{"announced_to":["kitchen"]}"#) },
            otherwise: Self.plain(house)
        )
        let kitchen = model.rooms[0]
        model.reads(for: "kitchen").announceDraft = "dinner's ready"
        model.announce(to: kitchen)
        await settle(for: park.arrived, what: "the announce request")

        // The move, while the request is still open at the server.
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        model.serverChanged()
        XCTAssertNil(model.openRoomID, "and the open room went with the house")

        park.release.set(true)
        await everythingLands()
        XCTAssertFalse(park.ranOut.current, "the park was released rather than timing out")
        XCTAssertNil(
            model.toast,
            "the old house's announcement must not say \"announced to kitchen\" in the new one"
        )
    }

    /// The **room** is the other half of the key. An answer for the room the
    /// message was sent to must name that room, even when a tap has opened
    /// another one while the request was in flight — the shape where a reply
    /// for room A lands under room B.
    func testAnAnnounceAnsweredAfterOpeningAnotherRoomStillNamesItsOwnRoom() async {
        let house = House()
        let (model, _, _, _) = rig(house: house)
        await loaded(model)
        let park = parkRequest(
            to: "/api/satellites/kitchen/announce",
            answering: { .json(#"{"announced_to":[]}"#) },
            otherwise: Self.plain(house)
        )
        model.open("kitchen")
        model.reads(for: "kitchen").announceDraft = "dinner's ready"
        model.announce(to: model.rooms[0])
        await settle(for: park.arrived, what: "the announce request")

        // A tap moves the screen to the other room **inside** the round trip.
        model.open("office")
        park.release.set(true)
        await settleUntil({ model.toast != nil }, "the announce answer")
        XCTAssertFalse(park.ranOut.current, "the park was released rather than timing out")
        XCTAssertEqual(
            "kitchen accepted the request but didn't play — connection may be dead",
            model.takeToast(),
            "the answer is about the room the message went to, not the room now on screen"
        )
        XCTAssertEqual("", model.reads(for: "kitchen").announceDraft, "and it is the kitchen's field that cleared")
    }

    // MARK: - lifecycle

    func testTheScreenHoldsNothingAfterItGoesAway() async {
        let (model, _, _, _) = rig()
        await loaded(model)
        model.open("kitchen")
        model.select(.notes)
        await settleUntil({ model.openReads?.notes.data?.isEmpty == false }, "the notes read")
        model.stop()
        let after = StubURLProtocol.requestCount
        model.received(StateEvent(type: "satellites.presence.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(after, StubURLProtocol.requestCount, "a push after stop() asks for nothing")
    }

    /// A push re-reads the roster, and the open room's timers and health —
    /// and **not** its conversation history, which must not re-sort under a
    /// finger while somebody is reading it.
    func testAPushReReadsThePresenceNotTheHistory() async {
        let (model, _, _, _) = rig()
        await loaded(model)
        model.open("kitchen")
        model.select(.conversations)
        await settleUntil({ model.openReads?.conversations.data?.isEmpty == false }, "the conversations read")
        let rosterBefore = requests(to: "/api/satellites").count
        let historyBefore = requests(to: "/api/satellites/kitchen/conversations").count

        model.received(StateEvent(type: "satellites.presence.changed", frame: "{}"))
        await settleUntil({ self.requests(to: "/api/satellites").count > rosterBefore }, "the roster re-read")
        await everythingLands()
        XCTAssertEqual(
            historyBefore,
            requests(to: "/api/satellites/kitchen/conversations").count,
            "the history is re-read by a tap, not by a push"
        )

        // And a push this screen does not care about moves nothing.
        let settled = requests(to: "/api/satellites").count
        model.received(StateEvent(type: "chat.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(settled, requests(to: "/api/satellites").count)
    }
}
