import XCTest
@testable import DomovoiKit

/// Listening history on the iPhone (MOBILE-HANDOFF-2026-11-12 §2), ported
/// from Android's `ListenTrackerTest`, `ListenOutboxTest`, `ListenLogTest`
/// and `ListeningHistoryTest` (a1e611b6, 14ac3aec).
final class ListenTrackerTests: XCTestCase {

    private var mono: Int64 = 0
    private lazy var t = ListenTracker(wallMs: { 1_762_970_591_000 }, monoMs: { [unowned self] in self.mono })

    private func play(_ sec: Int64) { mono += sec * 1000 }

    func testASkipAt20sOf200IsSkipped() throws {
        t.begin(trackID: 812, server: "s", durationSec: 200, playing: true)
        play(20)
        t.skip()
        let row = try XCTUnwrap(t.close(natural: false))
        XCTAssertEqual("skipped", row.outcome)
        XCTAssertEqual(20, row.playedSec)
        XCTAssertEqual(200, row.durationSec)
        XCTAssertEqual(812, row.trackID)
    }

    func testAt185Of200ThenNextIsFinished() {
        t.begin(trackID: 812, server: "s", durationSec: 200, playing: true)
        play(185)
        t.skip()
        XCTAssertEqual("finished", t.close(natural: false)?.outcome)
    }

    func testPauseTimeIsNotCounted() throws {
        t.begin(trackID: 812, server: "s", durationSec: 200, playing: true)
        play(30)
        t.playing(false)
        play(600)
        t.playing(true)
        play(10)
        let row = try XCTUnwrap(t.close(natural: false))
        XCTAssertEqual(40, row.playedSec)
        XCTAssertEqual("stopped", row.outcome)
    }

    func testASongThatRanToItsEndIsFinishedEvenIfTheClockSaysLess() throws {
        t.begin(trackID: 812, server: "s", durationSec: 200, playing: true)
        play(150)
        t.skip()
        let row = try XCTUnwrap(t.close(natural: true))
        XCTAssertEqual("finished", row.outcome)
        XCTAssertEqual(200, row.playedSec)
    }

    func testPlayedNeverExceedsTheLength() {
        t.begin(trackID: 812, server: "s", durationSec: 100, playing: true)
        play(130)
        XCTAssertEqual(100, t.close(natural: false)?.playedSec)
    }

    func testUnderASecondIsNoListen() {
        t.begin(trackID: 812, server: "s", durationSec: 200, playing: true)
        mono += 900
        XCTAssertNil(t.close(natural: false))
    }

    func testStartedAtIsWhenItBegan() throws {
        t.begin(trackID: 812, server: "s", durationSec: 200, playing: true)
        play(5)
        let row = try XCTUnwrap(t.close(natural: false))
        XCTAssertEqual("2025-11-12T18:03:11Z", OutboxRow.isoUTC(epochMs: row.startedAtMs))
    }

    func testTheRuleIsTheServers() {
        XCTAssertEqual("finished", ListenTracker.outcome(playedSec: 180, durationSec: 200, skipped: true))
        XCTAssertEqual("skipped", ListenTracker.outcome(playedSec: 179, durationSec: 200, skipped: true))
        XCTAssertEqual("stopped", ListenTracker.outcome(playedSec: 179, durationSec: 200, skipped: false))
        XCTAssertEqual("stopped", ListenTracker.outcome(playedSec: 500, durationSec: nil, skipped: false))
    }
}

final class ListenOutboxTests: XCTestCase {

    private let base = "http://10.0.0.9:6369"
    private var dir: URL!
    private var file: URL { dir.appendingPathComponent("listens-outbox.json") }

    override func setUpWithError() throws {
        try super.setUpWithError()
        StubURLProtocol.reset()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("listens-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        StubURLProtocol.reset()
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func row(_ i: Int, server: String? = nil) -> OutboxRow {
        OutboxRow(
            id: "row-\(i)", server: ServerCredentials.normalize(server ?? base), trackID: 1000 + Int64(i),
            deviceID: "ios-1a2b", startedAt: "2026-11-12T18:03:11Z", playedSec: 187, durationSec: 214,
            outcome: "finished"
        )
    }

    private func fill(_ n: Int, server: String? = nil) -> ListenOutbox {
        let o = ListenOutbox(file: file)
        for i in 0..<n { o.add(row(i, server: server)) }
        return o
    }

    private func sender(_ outbox: ListenOutbox, onOff: @Sendable @escaping () -> Void = {}) -> ListenSender {
        let client = DomovoiClient(
            book: .fixed(server: base, token: "household-abc"),
            gate: AdmitsAnyServer(),
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let active = ServerCredentials.normalize(base)
        return ListenSender(outbox: outbox, client: client, server: { active }, onRecordingOff: onOff)
    }

    private func ok(recording: Bool = true) -> StubURLProtocol.Answer {
        .json(#"{"accepted":1,"dropped":0,"recording":\#(recording)}"#)
    }

    private func sentCount(at index: Int) throws -> Int {
        let body = try XCTUnwrap(StubURLProtocol.recorded[safe: index]?.httpBody, "request \(index) was not sent")
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        return (object["listens"] as? [Any])?.count ?? -1
    }

    func testRowsSurviveARestart() {
        _ = fill(3)
        let again = ListenOutbox(file: file)
        XCTAssertEqual(["row-0", "row-1", "row-2"], again.all().map(\.id))
        XCTAssertEqual([1000, 1001, 1002], again.all().map(\.trackID))
    }

    func testAtMost500TheOldestGoFirst() {
        let rows = fill(503).all()
        XCTAssertEqual(500, rows.count)
        XCTAssertEqual("row-3", rows.first?.id)
        XCTAssertEqual("row-502", rows.last?.id)
    }

    func testTheWireRowIsTheContracts() throws {
        let one = OutboxRow(
            id: "row-0", server: "s", trackID: 1000, deviceID: "ios-1a2b", startedAt: "2026-11-12T18:03:11Z",
            playedSec: 187, durationSec: 214, outcome: "finished", listenerPersonID: 3
        )
        let wire = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(one.wire)) as? [String: Any])
        XCTAssertEqual(1000, wire["track_id"] as? Int)
        XCTAssertEqual("ios-1a2b", wire["device_id"] as? String)
        XCTAssertEqual("2026-11-12T18:03:11Z", wire["started_at"] as? String)
        XCTAssertEqual(187, wire["played_sec"] as? Int)
        XCTAssertEqual(214, wire["duration_sec"] as? Int)
        XCTAssertEqual("finished", wire["outcome"] as? String)
        XCTAssertEqual(3, wire["listener_person_id"] as? Int)
        XCTAssertEqual("ios", wire["player"] as? String)
        XCTAssertEqual("explicit", wire["origin"] as? String)
        XCTAssertNil(wire["id"], "the outbox's own id stays here")
        XCTAssertNil(wire["server"], "the outbox's own server stays here")
        XCTAssertNil(wire["as_household"], "as_household only when picked")
        let household = OutboxRow(
            id: "h", server: "s", trackID: 1, deviceID: "d", startedAt: "x", playedSec: 2, outcome: "stopped",
            asHousehold: true
        )
        let hw = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(household.wire)) as? [String: Any])
        XCTAssertEqual(true, hw["as_household"] as? Bool)
        XCTAssertNil(hw["listener_person_id"])
    }

    func testDeletedOn200InBatchesOf200() async throws {
        let o = fill(500)
        StubURLProtocol.enqueue(ok(), ok(), ok())
        let result = await sender(o).flush()
        XCTAssertEqual(.sent, result)
        XCTAssertEqual([200, 200, 100], try (0..<3).map { try sentCount(at: $0) })
        XCTAssertEqual("/api/music/listens", StubURLProtocol.recorded.first?.url?.path)
        XCTAssertTrue(o.all().isEmpty)
    }

    func testKeptWhenTheNetworkFails() async {
        let o = fill(500)
        StubURLProtocol.enqueue(StubURLProtocol.Answer(error: URLError(.notConnectedToInternet)))
        let result = await sender(o).flush()
        XCTAssertEqual(.kept, result)
        XCTAssertEqual(500, o.all().count)
    }

    func testKeptOnRefusalsThatPass() async {
        for code in [401, 403, 408, 429, 500, 503] {
            try? FileManager.default.removeItem(at: file)
            StubURLProtocol.reset()
            let o = fill(5)
            StubURLProtocol.enqueue(.json(#"{"detail":"no"}"#, status: code))
            let result = await sender(o).flush()
            XCTAssertEqual(.kept, result, "\(code)")
            XCTAssertEqual(5, o.all().count, "\(code) keeps")
        }
    }

    func testDroppedOn422() async {
        let o = fill(5)
        StubURLProtocol.enqueue(.json(
            #"{"detail":"ios-1a2b: more seconds played than have passed since its first song began"}"#, status: 422
        ))
        _ = await sender(o).flush()
        XCTAssertTrue(o.all().isEmpty, "a batch that would fail forever goes")
        XCTAssertEqual(1, StubURLProtocol.requestCount)
    }

    /// A song ends while the flush is on its way: its row must stay, and go
    /// in the next batch rather than be deleted with the first.
    func testOnlyTheSentIdsAreDeleted() async throws {
        let o = fill(2)
        let late = row(99)
        let first = Shared(true)
        let okBody = ok()
        StubURLProtocol.dispatch { _ in
            if first.current {
                first.set(false)
                o.add(late)
            }
            return okBody
        }
        _ = await sender(o).flush()
        XCTAssertEqual(2, try sentCount(at: 0))
        let second = try XCTUnwrap(StubURLProtocol.recorded[safe: 1]?.httpBody)
        let listens = (try JSONSerialization.jsonObject(with: second) as? [String: Any])?["listens"] as? [[String: Any]]
        XCTAssertEqual([1099], listens?.compactMap { $0["track_id"] as? Int })
        XCTAssertTrue(o.all().isEmpty)
    }

    func testARowWaitsForItsOwnServer() async throws {
        let o = ListenOutbox(file: file)
        o.add(row(1, server: "http://10.0.0.5:6369"))
        o.add(row(2))
        StubURLProtocol.enqueue(ok())
        _ = await sender(o).flush()
        XCTAssertEqual(1, StubURLProtocol.requestCount)
        XCTAssertEqual(1, try sentCount(at: 0))
        XCTAssertEqual(["row-1"], o.all().map(\.id))
    }

    func testRecordingOffIsHeard() async {
        let o = fill(1)
        StubURLProtocol.enqueue(ok(recording: false))
        let off = Shared(false)
        _ = await sender(o) { off.set(true) }.flush()
        XCTAssertTrue(off.current)
        XCTAssertTrue(o.all().isEmpty)
    }
}

@MainActor
final class ListenLogTests: XCTestCase {

    private var mono: Int64 = 0
    private var pick: String?
    private var shared = false
    private let server = "http://10.0.0.5:6369"
    private var dir: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("listenlog-\(UUID().uuidString)")
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func outbox() -> ListenOutbox { ListenOutbox(file: dir.appendingPathComponent("o.json")) }

    private func log(_ outbox: ListenOutbox) -> ListenLog {
        ListenLog(
            outbox: outbox,
            tracker: ListenTracker(wallMs: { 1_762_970_591_000 }, monoMs: { [unowned self] in self.mono }),
            sender: nil,
            server: { [server] in server },
            deviceID: { "ios-1a2b" },
            pickFor: { [unowned self] _ in self.pick },
            sharedAt: { [unowned self] _ in self.shared },
            io: { $0() }
        )
    }

    private let song = PlayerItem.fromTrack(id: 812, title: "Song", artist: "Artist", durationSec: 200)
    private let next = PlayerItem.fromTrack(id: 813, title: "Next", artist: "Artist", durationSec: 180)

    private func here(_ item: PlayerItem?, playing: Bool = true, local: Bool = true) -> ListenSnapshot {
        ListenSnapshot(item: item, playing: playing, local: local)
    }

    func testASongPlayedHereThenSkippedIsOneSkippedRow() {
        let o = outbox()
        let l = log(o)
        l.observe(here(song, playing: false))
        l.observe(here(song))
        mono += 20_000
        l.skip()
        l.observe(here(next))
        let rows = o.all()
        XCTAssertEqual(1, rows.count)
        XCTAssertEqual(812, rows.first?.trackID)
        XCTAssertEqual("skipped", rows.first?.outcome)
        XCTAssertEqual(20, rows.first?.playedSec)
        XCTAssertEqual(server, rows.first?.server)
        XCTAssertEqual("2025-11-12T18:03:11Z", rows.first?.startedAt)
    }

    func testANaturalEndIsFinished() {
        let o = outbox()
        let l = log(o)
        l.observe(here(song))
        mono += 195_000
        l.ended()
        l.observe(here(next))
        mono += 180_000
        l.ended()
        XCTAssertEqual(["finished", "finished"], o.all().map(\.outcome))
    }

    /// Nothing but library songs played HERE: never a podcast, a book, radio,
    /// a song on the phone, or anything while casting.
    func testNothingButLibrarySongsPlayedHere() {
        let o = outbox()
        let l = log(o)
        let others = [
            PlayerItem.fromEpisode(id: 5, title: "Episode", show: "Show", durationSec: 1800, artwork: nil, chapters: []),
            PlayerItem.fromStation(id: 7, name: "Radio"),
            PlayerItem.fromDeviceAudio(id: 8, title: "Phone file", artist: nil, uri: "file:///x.m4a"),
        ]
        for item in others {
            l.observe(here(item))
            mono += 60_000
            l.stopped()
        }
        l.observe(here(song, local: false))
        mono += 60_000
        l.stopped()
        XCTAssertTrue(o.all().isEmpty, "\(o.all())")
    }

    /// Casting closes what was playing here, as stopped.
    func testCastingStopsTheSongHere() {
        let o = outbox()
        let l = log(o)
        l.observe(here(song))
        mono += 30_000
        l.observe(here(song, local: false))
        mono += 60_000
        XCTAssertEqual([30], o.all().map(\.playedSec))
        XCTAssertEqual(["stopped"], o.all().map(\.outcome))
    }

    func testASongBackFromACastStartsWhenItPlaysHere() {
        let o = outbox()
        let l = log(o)
        l.observe(here(song, local: false))
        l.observe(here(song, playing: false))
        l.observe(here(song))
        mono += 30_000
        l.stopped()
        XCTAssertEqual([30], o.all().map(\.playedSec))
    }

    func testWhoseTheRowIs() throws {
        let o = outbox()
        let l = log(o)
        func one() throws -> OutboxRow {
            l.observe(here(song))
            mono += 10_000
            l.stopped()
            l.observe(here(nil))
            return try XCTUnwrap(o.all().last)
        }
        pick = nil
        var r = try one()
        XCTAssertNil(r.listenerPersonID)
        XCTAssertFalse(r.asHousehold)
        pick = "3"
        XCTAssertEqual(3, try one().listenerPersonID)
        pick = ListenerPicks.household
        r = try one()
        XCTAssertNil(r.listenerPersonID)
        XCTAssertTrue(r.asHousehold)
        // A shared screen's songs are the household's: no pick is sent.
        shared = true
        pick = "3"
        r = try one()
        XCTAssertNil(r.listenerPersonID)
        XCTAssertFalse(r.asHousehold)
    }

    func testNoMoreLoggingOnceTheHouseholdKeepsNone() {
        let o = outbox()
        let l = log(o)
        l.recordingOff()
        l.observe(here(song))
        mono += 60_000
        l.stopped()
        XCTAssertTrue(o.all().isEmpty)
    }
}

/// The player's hooks: the phone's own player says why the song in front
/// changed, so the log can tell a skip from an end from a stop.
@MainActor
final class ListenPlayerHookTests: XCTestCase {

    private final class Recorder: PlayerListenHooks {
        var calls: [String] = []
        var snapshots: [ListenSnapshot] = []
        func observe(_ snapshot: ListenSnapshot) { snapshots.append(snapshot) }
        func skip() { calls.append("skip") }
        func ended() { calls.append("ended") }
        func stopped() { calls.append("stopped") }
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        PlayerHouse().install()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    private let three = [PlayerSeed.transmission, PlayerSeed.atmosphere, PlayerSeed.decades]

    func testTheHooksSayWhyTheSongChanged() {
        let h = PlayerHarness()
        let r = Recorder()
        h.model.listens = r
        h.model.play(three)
        XCTAssertEqual(["stopped"], r.calls, "a new play stops what was there")
        XCTAssertEqual(PlayerSeed.transmission, r.snapshots.last?.item)
        XCTAssertEqual(true, r.snapshots.last?.playing)
        XCTAssertEqual(true, r.snapshots.last?.local)

        h.model.next()
        XCTAssertEqual(["stopped", "skip"], r.calls)
        XCTAssertEqual(PlayerSeed.atmosphere, r.snapshots.last?.item)

        h.engine.finishCurrentItem()
        XCTAssertEqual(["stopped", "skip", "ended"], r.calls)
        XCTAssertEqual(PlayerSeed.decades, r.snapshots.last?.item)

        // `next` on the last song moves nothing, so it is not a skip.
        h.model.next()
        XCTAssertEqual(["stopped", "skip", "ended"], r.calls)

        h.model.jump(to: 0)
        XCTAssertEqual(["stopped", "skip", "ended", "skip"], r.calls)
        h.model.jump(to: 0)
        XCTAssertEqual(["stopped", "skip", "ended", "skip"], r.calls, "a jump to the song in front is no skip")

        h.model.remove(at: 2)
        XCTAssertEqual(["stopped", "skip", "ended", "skip"], r.calls, "removing another row is no skip")
        h.model.remove(at: 0)
        XCTAssertEqual(["stopped", "skip", "ended", "skip", "skip"], r.calls)

        h.model.clear()
        XCTAssertEqual(["stopped", "skip", "ended", "skip", "skip", "stopped"], r.calls)
        XCTAssertNil(r.snapshots.last?.item)
    }
}

final class ListeningHistoryTests: XCTestCase {

    private let alex = 3
    private let now: Int64 = 1_762_970_591_000             // 2025-11-12T18:03:11Z
    private let longAgo = "2025-11-12T17:00:00.123456Z"     // an hour before
    private let justNow = "2025-11-12T17:58:11+00:00"       // five minutes before
    private let base = "http://10.0.0.9:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - clearing

    func testOwnPhoneClears() async throws {
        XCTAssertEqual(.allowed, clearCheck(
            personID: alex, name: "Alex", owner: DeviceOwner(id: alex, name: "Alex", claimedAt: longAgo),
            shared: false, keyHeld: true, nowMs: now
        ))
        let book = DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: InMemoryKeychain()))
        book.rememberDeviceOwner(DeviceOwner(id: alex, name: "Alex", claimedAt: longAgo), claimKey: "held-key-1234", for: base)
        StubURLProtocol.enqueue(.json(#"{"deleted":42}"#))
        let client = DomovoiClient(
            book: .fixed(server: base, token: "household-abc"), gate: AdmitsAnyServer(),
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let n = try await clearListeningHistory(client: client, book: book, server: base, deviceID: "ios-1a2b", personID: alex)
        let sent = try XCTUnwrap(StubURLProtocol.recorded.first)
        XCTAssertEqual("DELETE", sent.httpMethod)
        XCTAssertEqual("/api/music/listens/people/3", sent.url?.path)
        XCTAssertEqual("ios-1a2b", sent.value(forHTTPHeaderField: deviceIDHeader))
        XCTAssertEqual("held-key-1234", sent.value(forHTTPHeaderField: claimKeyHeader))
        XCTAssertEqual(42, n)
    }

    func testSomeoneElsesPhoneDoesNotClear() {
        let check = clearCheck(
            personID: alex, name: "Alex", owner: DeviceOwner(id: 5, name: "Priya", claimedAt: longAgo),
            shared: false, keyHeld: true, nowMs: now
        )
        guard case .notHere(let sentence, let why, let offers) = check else { return XCTFail("\(check)") }
        XCTAssertEqual("Each person clears their own. Only Alex’s own phone or browser, or an admin, can clear it.", sentence)
        XCTAssertEqual("This phone is Priya’s.", why)
        XCTAssertEqual([.thisIsMe, .openDashboard], offers)
        XCTAssertEqual(0, StubURLProtocol.requestCount, "nothing was sent")
    }

    func testNobodysPhoneSharedScreenNoKeyAndAFreshClaimDoNotClear() {
        func check(_ owner: DeviceOwner?, shared: Bool = false, key: Bool = true) -> (String, [ClearOffer]) {
            guard case .notHere(_, let why, let offers) = clearCheck(
                personID: alex, name: "Alex", owner: owner, shared: shared, keyHeld: key, nowMs: now
            ) else { XCTFail("allowed"); return ("", []) }
            return (why, offers)
        }
        var (why, offers) = check(nil)
        XCTAssertTrue(why.hasPrefix("If this is Alex’s phone, say “this is me” on this page"), why)
        XCTAssertEqual([.thisIsMe, .openDashboard], offers)
        (why, offers) = check(nil, shared: true)
        XCTAssertEqual("This is a shared screen, so it can’t be anyone’s.", why)
        XCTAssertEqual([.openDashboard], offers)
        (why, offers) = check(DeviceOwner(id: alex, name: "Alex", claimedAt: longAgo), key: false)
        XCTAssertEqual("This phone is Alex’s but can’t prove it yet: say “this is me” again on this page.", why)
        XCTAssertEqual([.thisIsMe, .openDashboard], offers)
        (why, offers) = check(DeviceOwner(id: alex, name: "Alex", claimedAt: justNow))
        XCTAssertEqual("This phone said it was Alex’s less than 10 minutes ago. Until then only an admin can clear it.", why)
        XCTAssertEqual([.openDashboard], offers)
        // A claim an admin set (no claimed-at) counts as fresh: an admin's.
        _ = check(DeviceOwner(id: alex, name: "Alex", claimedAt: nil))
    }

    func testTheClaimTimeIsRead() {
        XCTAssertFalse(claimIsFresh(longAgo, nowMs: now))
        XCTAssertTrue(claimIsFresh(justNow, nowMs: now))
        XCTAssertFalse(claimIsFresh("2025-11-12 17:00:00+00:00", nowMs: now), "a space for the T")
        XCTAssertTrue(claimIsFresh("not a time", nowMs: now), "unreadable counts as fresh")
    }

    func testTheSummaryDecodesTheServersAnswer() throws {
        let s = try JSONDecoder().decode(ListensSummary.self, from: Data("""
            {"keep":"365","keep_label":"1 year","keep_days":365,
             "choices":[{"value":"365","label":"1 year"},{"value":"off","label":"Don't keep"}],
             "total":57,"oldest_at":"2026-10-01T12:00:00Z","household":4,
             "people":[{"person_id":3,"name":"Alex","listens":42},{"person_id":5,"name":"Priya","listens":11}]}
            """.utf8))
        XCTAssertEqual("42 songs played for Alex · kept a year", ListeningStrings.line(s, personID: alex, name: "Alex"))
        XCTAssertEqual("nothing kept for Jordan", ListeningStrings.line(s, personID: 9, name: "Jordan"))
        let off = ListensSummary(keep: "off", keepLabel: "Don't keep")
        XCTAssertEqual(
            "Domovoi keeps no listening history (Settings → Privacy → Listening history).",
            ListeningStrings.line(off, personID: alex, name: "Alex")
        )
        XCTAssertEqual(
            "This deletes the 42 songs played for Alex in every room and on every phone and browser. "
                + "Up next stops learning from them. Songs played for the whole household and for other people stay.",
            ListeningStrings.confirmText(name: "Alex", count: 42)
        )
        XCTAssertEqual("1 song", ListeningStrings.songs(1))
    }

    // MARK: - "this phone"

    func testTheThisPhoneBlockIsTheDashboards() {
        var b = thisPhoneBlock(personID: alex, name: "Alex", owner: nil, shared: false, keyHeld: false, deviceCount: 0)
        XCTAssertEqual("Is this Alex’s phone? Then what plays here counts as Alex’s listening, and Alex can clear it from here.", b.line)
        XCTAssertEqual(.thisIsMe, b.action)
        XCTAssertEqual("no phone or browser is Alex’s", b.small)
        b = thisPhoneBlock(personID: alex, name: "Alex", owner: DeviceOwner(id: alex, name: "Alex"), shared: false, keyHeld: true, deviceCount: 2)
        XCTAssertEqual("This phone is Alex’s. What plays here counts as Alex’s listening, and Alex can clear it from here.", b.line)
        XCTAssertEqual(.notMe, b.action)
        XCTAssertEqual("1 other phone or browser is Alex’s", b.small)
        b = thisPhoneBlock(personID: alex, name: "Alex", owner: DeviceOwner(id: alex, name: "Alex"), shared: false, keyHeld: false, deviceCount: 1)
        XCTAssertEqual("This phone is Alex’s, but it can’t prove it yet. Say “this is me” again to act as Alex here.", b.line)
        XCTAssertEqual(.thisIsMe, b.action)
        b = thisPhoneBlock(personID: alex, name: "Alex", owner: DeviceOwner(id: 5, name: "Priya"), shared: false, keyHeld: true, deviceCount: 2)
        XCTAssertEqual("This phone is Priya’s.", b.line)
        XCTAssertEqual(.itsTheirsInstead, b.action)
        XCTAssertEqual("2 phones or browsers are Alex’s · an admin has to say yes to add this one", b.small)
        b = thisPhoneBlock(personID: alex, name: "Alex", owner: nil, shared: true, keyHeld: false, deviceCount: 1)
        XCTAssertEqual("This is a shared screen, so it isn’t anyone’s: what plays here counts for the whole household.", b.line)
        XCTAssertNil(b.action, "no button on a shared screen")
        XCTAssertEqual("1 phone or browser is Alex’s", b.small)
    }

    // MARK: - "listening as"

    func testListeningAsFollowsThePhoneUntilPicked() {
        XCTAssertEqual("3", ListenerPicks.effective(nil, owner: 3, shared: false))
        XCTAssertNil(ListenerPicks.effective(nil, owner: 3, shared: true), "a shared screen is the household's")
        XCTAssertEqual("5", ListenerPicks.effective("5", owner: 3, shared: false))
        XCTAssertNil(ListenerPicks.effective(ListenerPicks.household, owner: 3, shared: false))

        XCTAssertNil(ListenerPicks.toStore("3", owner: 3, shared: false), "picking the owner again follows the phone")
        XCTAssertEqual("5", ListenerPicks.toStore("5", owner: 3, shared: false))
        XCTAssertEqual(ListenerPicks.household, ListenerPicks.toStore(nil, owner: 3, shared: false))
        XCTAssertNil(ListenerPicks.toStore(nil, owner: nil, shared: false), "household on a phone nobody claimed is just following it")
    }

    /// The store, per server: a pick on one house is not the other's, and
    /// with no pick the listener is whoever the phone is.
    func testTheListenerIsPerServerAndFollowsTheOwner() {
        let book = DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: InMemoryKeychain()))
        let active = Shared(base)
        let place = SavedStateBox()
        let store = PlayerListenerStore(place: place)
        store.attach(book: book, server: { active.current })
        XCTAssertNil(store.personID)
        book.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: nil, for: base)
        XCTAssertEqual("3", store.personID, "nothing picked: the phone's owner")
        store.set("5")
        XCTAssertEqual("5", store.personID)
        store.set(nil)
        XCTAssertNil(store.personID, "household on a claimed phone is the household on purpose")
        XCTAssertEqual(ListenerPicks.household, store.pick(for: base))
        store.set("3")
        XCTAssertNil(store.pick(for: base), "the owner again: follow the phone")

        active.set("http://10.0.0.5:6369")
        XCTAssertNil(store.personID, "another house: its own (empty) pick and owner")
        store.set("7")
        active.set(base)
        XCTAssertEqual("3", store.personID)

        // A shared screen is the household's, whatever the owner.
        book.setSharedScreen(true, for: base)
        XCTAssertNil(store.personID)
    }

    /// A pick from before it was per server belongs to the server the phone
    /// was on.
    func testALegacyPickMovesToTheActiveServer() {
        let place = SavedStateBox(Data("9".utf8))
        let store = PlayerListenerStore(place: place)
        let book = DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: InMemoryKeychain()))
        store.attach(book: book, server: { [base] in base })
        XCTAssertEqual("9", store.personID)
        XCTAssertEqual("9", PlayerListenerStore(place: place).pick(for: base), "and is saved there")
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}
