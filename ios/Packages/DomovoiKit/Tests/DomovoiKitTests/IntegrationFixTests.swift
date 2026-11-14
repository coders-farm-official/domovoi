import Foundation
import XCTest
@testable import DomovoiKit

/// The integration fix of batch 2026-11-14 (wf/ios-e-all): where the music
/// and queues units meet (A), and the claim unit's low review findings (B).
/// Each case names the fix it pins; each was shown to fail on the code before
/// it (on a throwaway branch).
///
/// No index into an array anywhere here: a trap would end the run.

// MARK: - B: the claim unit's lows

final class IntegrationClaimTests: XCTestCase {

    private let base = "http://10.0.0.9:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func book(_ keychain: any KeychainItems = InMemoryKeychain()) -> DeviceOwnerBook {
        DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: keychain))
    }

    private func client() -> DomovoiClient {
        let server = base
        return DomovoiClient(
            book: ServerBook(activeServer: { server }, tokenForServer: { _ in "household-abc" }),
            gate: AdmitsAnyServer(),
            configuration: StubURLProtocol.session(timeout: 10)
        )
    }

    private static func row(owner: Int?, name: String? = nil, key: String? = nil) -> StubURLProtocol.Answer {
        func q(_ v: String?) -> String { v.map { "\"\($0)\"" } ?? "null" }
        return .json("""
            {"device_id":"ios-1a2b","name":"iPhone","shared_screen":true,
             "owner_person_id":\(owner.map(String.init) ?? "null"),"owner_person_name":\(q(name)),
             "owner_claimed_at":null,"claim_key":\(q(key))}
            """)
    }

    // B1 — a forget while a claim is out: the key it minted is not saved
    // again after the forget took everything.
    func testB1_AKeyMintedWhileAForgetRanIsNotSavedAgain() async throws {
        let b = book()
        let server = base
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "k0-held-AAAAAAAAAAAAAAAAAAAAA", for: server)
        StubURLProtocol.dispatch { _ in
            // The person forgets this server while the claim is on its way.
            b.forget(server: server)
            return Self.row(owner: 3, name: "Alex", key: "k1-minted-AAAAAAAAAAAAAAAAAAAA")
        }
        _ = try? await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
        XCTAssertNil(b.claimKey(for: base), "the key minted in flight was saved after the forget")
        XCTAssertNil(b.deviceOwner(for: base), "the owner came back after the forget")
        XCTAssertNil(b.sharedScreen(for: base), "the shared-screen answer came back after the forget")
    }

    // B1 — the same for a registration.
    func testB1_ARegistrationAnsweredAfterAForgetStoresNothing() async {
        let b = book()
        let server = base
        StubURLProtocol.dispatch { _ in
            b.forget(server: server)
            return Self.row(owner: 3, name: "Alex")
        }
        _ = await DeviceRegistry.register(
            with: client(), book: b, server: base, deviceID: "ios-1a2b", name: "iPhone", userAgent: "ua"
        )
        XCTAssertNil(b.deviceOwner(for: base), "a registration answered after a forget was stored")
        XCTAssertNil(b.sharedScreen(for: base))
    }

    // B1 — an answer with nothing forgotten in between is still stored.
    func testB1_WithNoForgetTheAnswerIsStored() async throws {
        let b = book()
        StubURLProtocol.dispatch { _ in Self.row(owner: 3, name: "Alex", key: "k1-minted-AAAAAAAAAAAAAAAAAAAA") }
        _ = try await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
        XCTAssertEqual("k1-minted-AAAAAAAAAAAAAAAAAAAA", b.claimKey(for: base))
        XCTAssertEqual(3, b.deviceOwner(for: base)?.id)
    }

    // B2 — the registration request itself carries the 15 s timeout.
    func testB2_TheRegistrationRequestTimesOutAfterFifteenSeconds() async {
        StubURLProtocol.dispatch { _ in Self.row(owner: nil) }
        _ = await DeviceRegistry.register(
            with: client(), book: book(), server: base, deviceID: "ios-1a2b", name: "iPhone", userAgent: "ua"
        )
        let sent = StubURLProtocol.recorded.first { $0.url?.path == "/api/devices/register" }
        XCTAssertNotNil(sent, "no registration went out")
        XCTAssertEqual(15, sent?.timeoutInterval ?? -1, "the registration waits longer than 15 s")
    }

    // B2 — at the deadline the line moves on, and an answer that comes after
    // it is not stored.
    func testB2_ARegistrationPastItsDeadlineFreesTheLineAndStoresNothing() async throws {
        let b = book()
        let release = Shared(false)
        StubURLProtocol.dispatch { _ in
            let end = Date().addingTimeInterval(3)
            while Date() < end, !release.current { Thread.sleep(forTimeInterval: 0.02) }
            return Self.row(owner: 3, name: "Alex", key: "k-late-AAAAAAAAAAAAAAAAAAAAAAA")
        }
        let started = Date()
        let row = await DeviceRegistry.register(
            with: client(), book: b, server: base, deviceID: "ios-1a2b", name: "iPhone", userAgent: "ua",
            timeout: 0.3
        )
        let took = Date().timeIntervalSince(started)
        XCTAssertNil(row, "a registration past its deadline came back as answered")
        XCTAssertLessThan(took, 2.0, "the registration held the line for \(took) s")

        // The line is free: the next call starts at once.
        let lineStarted = Date()
        let ran = await b.line.run { Date() }
        XCTAssertLessThan(ran.timeIntervalSince(lineStarted), 1.0, "the line was still held")

        // The late answer lands now; nothing of it is stored.
        release.set(true)
        try await Task.sleep(for: .milliseconds(800))
        XCTAssertNil(b.claimKey(for: base), "an answer past the deadline was stored")
        XCTAssertNil(b.deviceOwner(for: base))
    }

    // B3 — the line knows when it is being run from inside itself (the
    // precondition reads this flag; a nested run would wait for itself).
    func testB3_TheLineKnowsWhenItIsInside() async {
        let line = ClaimLine()
        XCTAssertFalse(ClaimLine.isInside)
        let inside = await line.run { ClaimLine.isInside }
        XCTAssertTrue(inside, "a body cannot tell it is inside the line")
        XCTAssertFalse(ClaimLine.isInside, "the flag leaked out of the body")
        // And two calls in a row, from outside, both run.
        let again = await line.run { 7 }
        XCTAssertEqual(7, again)
    }

    // B4 — an answer of "no key" after a Keychain read that FAILED deletes
    // the key anyway.
    func testB4_NoKeyAfterAFailedReadStillDeletesTheKey() throws {
        final class FlakyKeychain: KeychainItems, @unchecked Sendable {
            let inner = InMemoryKeychain()
            var failNext = true
            func set(_ secret: Data, account: String) throws { try inner.set(secret, account: account) }
            func get(account: String) throws -> Data? {
                if failNext { failNext = false; throw TokenVaultError.keychain(-25308) }
                return try inner.get(account: account)
            }
            func remove(account: String) throws { try inner.remove(account: account) }
        }
        let keychain = FlakyKeychain()
        let account = ServerCredentials.normalize(base)
        try keychain.inner.set(Data("stale-key-AAAAAAAAAAAA".utf8), account: account)
        let b = book(keychain)
        // The answer: nobody's, no key. The read before it fails.
        b.rememberDeviceOwner(nil, claimKey: nil, for: base)
        XCTAssertNil(try keychain.inner.get(account: account), "a key the answer said is gone stayed in the Keychain")
    }
}

/// C: a natural end counts the time actually heard (owner, 2026-11-14:
/// "count real time"), and is still `finished`.
final class IntegrationRealTimeTests: XCTestCase {

    func testC_ASeekToNearTheEndCountsOnlyTheTimeHeard() throws {
        var mono: Int64 = 0
        var t = ListenTracker(wallMs: { 1_762_970_591_000 }, monoMs: { mono })
        // A 6:00 song: ten seconds heard, a seek to 5:50, the last ten heard.
        t.begin(trackID: 812, server: "s", durationSec: 360, playing: true)
        mono += 10_000
        mono += 10_000
        let row = try XCTUnwrap(t.close(natural: true))
        XCTAssertEqual(20, row.playedSec, "a natural end was padded up to the song's length")
        XCTAssertEqual("finished", row.outcome)
        XCTAssertEqual(360, row.durationSec)
    }

    func testC_ANaturalEndAfterASkipFlagIsStillFinished() throws {
        var mono: Int64 = 0
        var t = ListenTracker(wallMs: { 0 }, monoMs: { mono })
        t.begin(trackID: 9, server: "s", durationSec: 200, playing: true)
        mono += 5_000
        t.skip()
        let row = try XCTUnwrap(t.close(natural: true))
        XCTAssertEqual("finished", row.outcome)
        XCTAssertEqual(5, row.playedSec)
    }
}

/// B5: the outbox keeps one file moved aside, not every one.
final class IntegrationOutboxTests: XCTestCase {

    private var dir: URL!
    private var file: URL { dir.appendingPathComponent("listens-outbox.json") }

    override func setUpWithError() throws {
        try super.setUpWithError()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("integ-outbox-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    func testB5_OnlyTheNewestCorruptFileIsKept() throws {
        // Three left from earlier, the oldest first.
        for (ms, text) in [(1_000, "old one"), (2_000, "old two"), (3_000, "old three")] {
            try Data(text.utf8).write(to: dir.appendingPathComponent("listens-outbox.corrupt-\(ms).json"))
        }
        try Data("not json at all".utf8).write(to: file)
        let o = ListenOutbox(file: file)
        o.add(OutboxRow(
            id: "row-1", server: "http://10.0.0.9:6369", trackID: 1, deviceID: "ios-1",
            startedAt: "2026-11-12T18:03:11Z", playedSec: 30, outcome: "stopped"
        ))
        let names = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        let aside = names.filter { $0.contains(".corrupt-") }
        XCTAssertEqual(1, aside.count, "corrupt files piled up: \(names)")
        let kept = try XCTUnwrap(aside.first, "\(names)")
        XCTAssertEqual(
            "not json at all",
            String(decoding: try Data(contentsOf: dir.appendingPathComponent(kept)), as: UTF8.self),
            "the one kept is not the newest"
        )
        XCTAssertEqual(["row-1"], o.all().map(\.id))
    }
}

/// B1 (the listen half) and B6: through the shell.
@MainActor
final class IntegrationShellTests: XCTestCase {

    private let houseA = "http://10.0.0.42:6369"
    private let houseB = "http://10.0.0.43:6369"
    private var dir: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        StubURLProtocol.reset()
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/health" {
                let parts = request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
                let challenge = (parts?.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            return .json(#"{"deleted":42}"#)
        }
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("integ-shell-\(UUID().uuidString)")
    }

    override func tearDown() {
        StubURLProtocol.reset()
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func shell(outbox: ListenOutbox? = nil) -> (ShellModel, InMemoryShellStore, DeviceOwnerBook) {
        let store = InMemoryShellStore(serverURL: houseA, token: "household-abc")
        store.trustServer(houseA, identity: TestIdentity.real.pin)
        store.trustServer(houseB, identity: TestIdentity.real.pin)
        store.setToken("household-abc", for: houseB)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let book = DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: InMemoryKeychain()))
        let model = ShellModel(
            container: container,
            deviceSelf: { DeviceSelf(deviceID: "ios-1a2b", name: "iPhone", userAgent: "ua") },
            listeners: PlayerListenerStore(place: SavedStateBox()),
            owners: book,
            listenOutbox: outbox
        )
        return (model, store, book)
    }

    // B1 — forgetting the active server while a song plays: the song closing
    // afterwards adds no row for the server just forgotten.
    func testB1_ClosingTheSongAfterForgettingTheActiveServerAddsNoRow() async throws {
        let outbox = ListenOutbox(file: dir.appendingPathComponent("o.json"))
        let (model, _, _) = shell(outbox: outbox)
        defer { model.stop() }
        let log = try XCTUnwrap(model.listens, "the shell built no listen log")
        let song = PlayerItem.fromTrack(id: 812, title: "Song", durationSec: 200)
        log.observe(ListenSnapshot(item: song, playing: true, local: true))
        // Long enough to be a listen (a second or more).
        try await Task.sleep(for: .milliseconds(1_300))

        model.forgetTheActiveServer()
        // The player stopping as the app leaves that server.
        log.stopped()
        try await Task.sleep(for: .milliseconds(600))

        let forgotten = ServerCredentials.normalize(houseA)
        XCTAssertEqual(
            [], outbox.all().filter { $0.server == forgotten }.map(\.trackID),
            "a row was added for the server just forgotten"
        )
    }

    // B6 — "Clear" pressed after the app moved house: nothing is sent, and a
    // line says why.
    func testB6_AClearRefusedForAServerChangeSaysWhy() async {
        let (model, store, book) = shell()
        defer { model.stop() }
        book.rememberDeviceOwner(
            DeviceOwner(id: 3, name: "Alex", claimedAt: "2025-01-01T00:00:00Z"),
            claimKey: "alex-key-AAAAAAAAA", for: houseA
        )
        let page = PeopleThisPhoneModel(shell: model)
        let alex = Person(id: 3, name: "Alex")
        page.askToClear(alex)
        XCTAssertEqual(ClearCheck.allowed, page.ask?.check, "\(String(describing: page.ask))")
        XCTAssertTrue(store.setServerURL(houseB), "the rig could not switch house")
        model.dismissToast()
        page.confirmClear(alex)
        XCTAssertNil(page.ask)
        XCTAssertFalse(page.clearing)
        let said = model.toast?.text ?? ""
        XCTAssertTrue(said.contains("another server"), "nothing said why the clear was not sent: \"\(said)\"")
        XCTAssertEqual(0, StubURLProtocol.recorded.filter { $0.httpMethod == "DELETE" }.count)
    }
}

// MARK: - A: the music and queues units, joined

@MainActor
final class IntegrationMusicQueueTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private final class House: Sendable {
        let detail = Shared(#"{"key":"rock","name":"Rock","track_count":3,"track_ids":[3,4,5]}"#)
        /// Called when the queue add arrives, before it is answered.
        let onAdd = Shared<(@Sendable () -> Void)?>(nil)

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url else { return .json("{}", status: 404) }
                switch url.path {
                case "/api/health":
                    let challenge = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                        .queryItems?.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/music/genre": return .json(self.detail.current)
                case "/api/music/library": return .json(#"{"items":[],"total":0}"#)
                case "/api/music/now-playing":
                    return .json(#"[{"room_id":"parlour","state":"stop"},{"room_id":"attic","state":"stop"}]"#)
                case "/api/music/library/stats": return .json(#"{"total_tracks":1,"enriched_count":0}"#)
                case "/api/music/play-track": return .json(#"{"ok":true}"#)
                default:
                    if url.path.hasPrefix("/api/music/queue/"), url.path.hasSuffix("/add") {
                        self.onAdd.current?()
                        return .json(#"{"queued":[{"song_id":1,"file":"a"}],"requested":3,"started":false,"position":1}"#)
                    }
                    return .json("{}", status: 404)
                }
            }
        }
    }

    private func store() -> InMemoryShellStore {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        return store
    }

    private func container(_ store: InMemoryShellStore) -> AppContainer {
        AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
    }

    private func settleUntil(_ done: @MainActor () -> Bool, _ what: String, line: UInt = #line) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled", line: line)
    }

    private func bodies(_ matching: (String) -> Bool) -> [[String: Any]] {
        StubURLProtocol.recorded.compactMap { request in
            guard let path = request.url?.path, matching(path), let data = request.httpBody else { return nil }
            return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        }
    }

    private func genreModel(_ c: AppContainer) -> MusicGenresDetailModel {
        MusicGenresDetailModel(
            genre: MusicGenreRow(key: "rock", name: "Rock", trackCount: 3),
            container: c,
            deviceID: "ios-test-device",
            say: { _ in }
        )
    }

    // A1 — the genre's room `add to queue`: every id, at the END of the
    // picked room's queue, as this phone, said in the sheet.
    func testA1_AGenreAddsToTheEndOfTheRoomsQueue() async throws {
        let house = House()
        house.install()
        let detail = genreModel(container(store()))
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.detail.answered }, "the genre's ids")
        detail.pick("attic")
        let said = await detail.queueInRoom(rooms: ["parlour", "attic"])
        XCTAssertEqual("added 3 songs · Rock to attic's queue", said)
        let sent = bodies { $0 == "/api/music/queue/attic/add" }
        XCTAssertEqual(1, sent.count, "\(StubURLProtocol.recorded.map { $0.url?.path ?? "" })")
        let body = try XCTUnwrap(sent.first)
        XCTAssertEqual("end", body["where"] as? String)
        XCTAssertEqual([3, 4, 5], body["track_ids"] as? [Int])
        XCTAssertEqual("ios-test-device", body["device_id"] as? String)
    }

    // A1 — the app moved house while the add was out: nothing is said.
    func testA1_AGenreAddAnsweredAfterAServerSwitchSaysNothing() async {
        let house = House()
        house.install()
        let s = store()
        let detail = genreModel(container(s))
        detail.start()
        defer { detail.stop() }
        await settleUntil({ detail.detail.answered }, "the genre's ids")
        let other = "http://10.0.0.43:6369"
        s.trustServer(other, identity: TestIdentity.real.pin)
        house.onAdd.set { _ = s.setServerURL(other) }
        let said = await detail.queueInRoom(rooms: ["parlour"])
        XCTAssertNil(said, "an answer from the house the app left was said")
    }

    // A1 — no room online, or no ids yet: nothing is sent.
    func testA1_NoRoomNoAdd() async {
        let house = House()
        house.install()
        let detail = genreModel(container(store()))
        let said = await detail.queueInRoom(rooms: [])
        XCTAssertNil(said)
        XCTAssertTrue(bodies { $0.hasSuffix("/add") }.isEmpty)
    }

    /// This phone's queue, by hand.
    private final class FakePhone: PhoneQueueSink {
        var hasQueue = false
        var castsTo: String?
        var enqueued: [[Int64]] = []
        func enqueue(_ tracks: [LibraryTrack]) { enqueued.append(tracks.map(\.id)) }
        func playNext(_ tracks: [LibraryTrack]) {}
    }

    private func tools(_ c: AppContainer, phone: FakePhone?, paired: Bool = true) -> QueueTools {
        QueueTools(
            container: c,
            deviceID: "ios-test-device",
            paired: paired,
            listener: RoomListener(personID: "5"),
            phone: phone.map { p in LazyPhoneQueue(built: { p }, make: { p }) }
        )
    }

    // A1 — `queue here`: the songs loaded so far at the end of this phone's
    // queue, said by name.
    func testA1_QueueHereEnqueuesTheLoadedSongsOnThisPhone() {
        let phone = FakePhone()
        let t = tools(container(store()), phone: phone)
        let songs = [LibraryTrack(id: 3, title: "a"), LibraryTrack(id: 4, title: "b")]
        XCTAssertEqual("queued 2 songs · Rock", t.queueHere(songs, label: "Rock"))
        XCTAssertEqual([[3, 4]], phone.enqueued)
        XCTAssertNil(t.queueHere([], label: "Rock"), "an empty list was queued")
        XCTAssertNil(tools(container(store()), phone: nil).queueHere(songs, label: "Rock"), "queued with no player")
    }

    // A1/A3 — a song row offers Play next / Add to queue only where its tap
    // can land: the room it casts to when paired, else a player here.
    func testA3_ASongRowOffersTheQueueOnlyWhereItCanLand() {
        let c = container(store())
        XCTAssertFalse(tools(c, phone: nil).offersSongQueue, "offered with no player and no cast")
        let here = FakePhone()
        XCTAssertTrue(tools(c, phone: here).offersSongQueue)
        let casting = FakePhone()
        casting.castsTo = "parlour"
        XCTAssertTrue(tools(c, phone: casting).offersSongQueue)
        XCTAssertFalse(tools(c, phone: casting, paired: false).offersSongQueue, "a room offered to an unpaired phone")
    }

    // A2 — "play in <room>" from the library row and the track sheet: this
    // phone's id, the listener, and the list it was opened from.
    func testA2_PlayInARoomSendsThisPhoneTheListenerAndTheList() async throws {
        let house = House()
        house.install()
        let c = container(store())
        let model = MusicModel(container: c, clock: { 0 }, wait: { _ in try await Task.sleep(for: .seconds(3_600)) })
        model.start()
        await settleUntil({ model.nowPlaying.answered }, "the rooms")
        XCTAssertTrue(model.castRooms.contains("parlour"), "\(model.castRooms)")
        let t = tools(c, phone: nil)
        let track = LibraryTrack(id: 503, title: "Thrumming Ashkeep")
        XCTAssertTrue(model.play(track, in: "parlour", tools: t, context: UpNextContext.genre(key: "rock", name: "Rock")))
        await settleUntil({ !self.bodies { $0 == "/api/music/play-track" }.isEmpty }, "the play")
        let body = try XCTUnwrap(bodies { $0 == "/api/music/play-track" }.first)
        XCTAssertEqual("parlour", body["room_id"] as? String)
        XCTAssertEqual(503, body["track_id"] as? Int)
        XCTAssertEqual("ios-test-device", body["device_id"] as? String)
        XCTAssertEqual(5, body["listener_person_id"] as? Int)
        let context = try XCTUnwrap(body["context"] as? [String: Any], "\(body)")
        XCTAssertEqual("genre", context["kind"] as? String)
        XCTAssertEqual("rock", context["ref"] as? String)
    }
}
