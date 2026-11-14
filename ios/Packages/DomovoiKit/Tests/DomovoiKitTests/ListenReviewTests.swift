import Foundation
import Security
import XCTest
@testable import DomovoiKit

/// The security review of the claim unit (wf/ios-e-claim ba70c348): each
/// case names the finding it pins, and each was shown to fail with the
/// defect put back (on a throwaway branch).
final class ListenClaimReviewTests: XCTestCase {

    private let base = "http://10.0.0.9:6369"
    private let other = "http://10.0.0.5:6369"

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

    /// A client whose active server a test can move mid-call.
    private func client(_ active: Shared<String>) -> DomovoiClient {
        DomovoiClient(
            book: ServerBook(activeServer: { active.current }, tokenForServer: { _ in "household-abc" }),
            gate: AdmitsAnyServer(),
            configuration: StubURLProtocol.session(timeout: 10)
        )
    }

    private static func row(owner: Int?, name: String? = nil, key: String? = nil) -> StubURLProtocol.Answer {
        func q(_ v: String?) -> String { v.map { "\"\($0)\"" } ?? "null" }
        return .json("""
            {"device_id":"ios-1a2b","name":"iPhone","shared_screen":false,
             "owner_person_id":\(owner.map(String.init) ?? "null"),"owner_person_name":\(q(name)),
             "owner_claimed_at":null,"claim_key":\(q(key))}
            """)
    }

    /// Hold a request until `done()` or `seconds` pass, off the main actor.
    private static func park(_ seconds: Double, until done: () -> Bool) {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end, !done() { Thread.sleep(forTimeInterval: 0.02) }
    }

    private static func isPut(_ r: URLRequest) -> Bool { r.httpMethod == "PUT" }

    // MARK: - 2A: a stale registration answer cannot wipe a fresh key

    /// The registration is out; a claim is pressed and mints K2. Without the
    /// line the claim lands first and the registration's "nobody" — computed
    /// before it — lands after and deletes K2, after which every claim is a
    /// 403. In line, the claim waits for the registration.
    func testARegistrationAnswerCannotWipeAKeyAClaimMintedAfterIt() async throws {
        let b = book()
        let active = Shared(base)
        let c = client(active)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/devices/register" {
                // Wait for the claim to go out (it never does, in line).
                Self.park(1.5) { StubURLProtocol.recorded.contains(where: Self.isPut) }
                return Self.row(owner: nil)
            }
            return Self.row(owner: 3, name: "Alex", key: "k2-minted-AAAAAAAAAAAAAAAAAAAA")
        }
        let server = base
        let registering = Task {
            await DeviceRegistry.register(
                with: c, book: b, server: server, deviceID: "ios-1a2b", name: "iPhone", userAgent: "ua"
            )
        }
        for _ in 0..<200 where StubURLProtocol.requestCount == 0 { try await Task.sleep(for: .milliseconds(10)) }
        let owner = try await claimThisPhone(client: c, book: b, server: base, deviceID: "ios-1a2b", personID: 3)
        _ = await registering.value
        XCTAssertEqual(3, owner?.id)
        XCTAssertEqual("k2-minted-AAAAAAAAAAAAAAAAAAAA", b.claimKey(for: base), "a stale answer wiped the fresh key")
        XCTAssertEqual(3, b.deviceOwner(for: base)?.id)
    }

    // MARK: - 2B: two presses never cross

    /// Two claims pressed together (the block, the clear dialog, the Settings
    /// card): the second must carry the key the first was given, and both
    /// must be answered yes. Crossed, the second carries the OLD key and is
    /// refused "can't prove it".
    func testTwoClaimsInARowEachCarryTheKeyTheOneBeforeWasGiven() async throws {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "k0-held-AAAAAAAAAAAAAAAAAAAAA", for: base)
        let active = Shared(base)
        let c = client(active)
        // The house: a claim with the key it holds now mints the next one.
        let current = Shared("k0-held-AAAAAAAAAAAAAAAAAAAAA")
        let minted = Shared(0)
        let statuses = Shared<[Int]>([])
        StubURLProtocol.dispatch { request in
            if minted.current == 0 {
                // The first: wait for the second to go out (never, in line).
                Self.park(1.5) { StubURLProtocol.recorded.filter(Self.isPut).count >= 2 }
            }
            guard request.value(forHTTPHeaderField: claimKeyHeader) == current.current else {
                statuses.mutate { $0.append(403) }
                return .json(
                    #"{"detail":"this phone or browser can't prove it is Alex's (its claim key is missing or out of date). An admin can change it, or set it to nobody so it can say \"this is me\" again."}"#,
                    status: 403
                )
            }
            minted.mutate { $0 += 1 }
            let next = "k\(minted.current)-minted-AAAAAAAAAAAAAAAAAAAA"
            current.set(next)
            statuses.mutate { $0.append(200) }
            return Self.row(owner: 3, name: "Alex", key: next)
        }
        let server = base
        async let first = try? claimThisPhone(client: c, book: b, server: server, deviceID: "ios-1a2b", personID: 3)
        try await Task.sleep(for: .milliseconds(50))
        async let second = try? claimThisPhone(client: c, book: b, server: server, deviceID: "ios-1a2b", personID: 3)
        _ = await (first, second)
        XCTAssertEqual([200, 200], statuses.current, "the presses crossed: one carried a key already replaced")
        XCTAssertEqual(current.current, b.claimKey(for: base), "the phone does not hold the house's latest key")
    }

    /// A "can't prove it" refusal drops the key only if it is still the one
    /// that was SENT: a key minted while the refused call was out stays.
    func testACantProveRefusalDoesNotWipeANewerKey() async {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "k1-sent-AAAAAAAAAAAAAAAAAAAAA", for: base)
        let active = Shared(base)
        let base = self.base
        StubURLProtocol.dispatch { _ in
            // Meanwhile the phone was given K2 (an answer that did not go
            // through this call).
            b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "k2-newer-AAAAAAAAAAAAAAAAAAAA", for: base)
            return .json(
                #"{"detail":"this phone or browser can't prove it is Alex's (its claim key is missing or out of date). An admin can change it, or set it to nobody so it can say \"this is me\" again."}"#,
                status: 403
            )
        }
        _ = try? await claimThisPhone(client: client(active), book: b, server: base, deviceID: "ios-1a2b", personID: nil)
        XCTAssertEqual("k1-sent-AAAAAAAAAAAAAAAAAAAAA", StubURLProtocol.header(claimKeyHeader, at: 0))
        XCTAssertEqual("k2-newer-AAAAAAAAAAAAAAAAAAAA", b.claimKey(for: base), "the refusal of K1 wiped K2")
    }

    // MARK: - mid-flight server switches

    /// The app moves house while a claim is out: its answer is filed under
    /// the house it was SENT to, and the new house learns nothing from it.
    func testAClaimAnsweredAfterAServerSwitchIsFiledUnderTheServerItWentTo() async throws {
        let b = book()
        let active = Shared(base)
        let other = self.other
        StubURLProtocol.dispatch { _ in
            active.set(other)
            return Self.row(owner: 3, name: "Alex", key: "k-of-base-AAAAAAAAAAAAAAAAAAAAA")
        }
        let owner = try await claimThisPhone(client: client(active), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
        XCTAssertEqual(3, owner?.id)
        XCTAssertEqual("k-of-base-AAAAAAAAAAAAAAAAAAAAA", b.claimKey(for: base))
        XCTAssertNil(b.claimKey(for: other), "the old house's key was filed under the new one")
        XCTAssertNil(b.deviceOwner(for: other))

        // And the next claim, for the new house, does not carry it.
        StubURLProtocol.dispatch { _ in Self.row(owner: 5, name: "Priya", key: "k-of-other-AAAAAAAAAAAAAAAAAAAA") }
        _ = try await claimThisPhone(client: client(active), book: b, server: other, deviceID: "ios-1a2b", personID: 5)
        XCTAssertNil(StubURLProtocol.header(claimKeyHeader, at: 1), "base's key went to another server")
    }

    func testARegistrationAnsweredAfterAServerSwitchIsFiledUnderTheServerItWentTo() async {
        let b = book()
        let active = Shared(base)
        let other = self.other
        StubURLProtocol.dispatch { _ in
            active.set(other)
            return Self.row(owner: 3, name: "Alex")
        }
        _ = await DeviceRegistry.register(
            with: client(active), book: b, server: base, deviceID: "ios-1a2b", name: "iPhone", userAgent: "ua"
        )
        XCTAssertEqual(3, b.deviceOwner(for: base)?.id)
        XCTAssertNil(b.deviceOwner(for: other))
    }

    // MARK: - the Keychain

    /// The claim keys are added this-device-only and never synchronised,
    /// under a service of their own.
    func testTheClaimKeysKeychainAttributesArePinned() {
        let attributes = ClaimKeyVault.systemItems().addAttributes(account: "http://10.0.0.9:6369", secret: Data("k".utf8))
        XCTAssertEqual(ClaimKeyVault.service, attributes[kSecAttrService as String] as? String)
        XCTAssertEqual(
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String,
            attributes[kSecAttrAccessible as String] as? String
        )
        XCTAssertEqual(false, attributes[kSecAttrSynchronizable as String] as? Bool)
        XCTAssertEqual(kSecClassGenericPassword as String, attributes[kSecClass as String] as? String)
    }

    /// A Keychain that failed to answer is asked again, not remembered as
    /// "no key" for the rest of the process.
    func testAFailedKeychainReadIsNotCachedAsNoKey() {
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
        try? keychain.inner.set(Data("held-key-1234".utf8), account: ServerCredentials.normalize(base))
        let b = book(keychain)
        XCTAssertNil(b.claimKey(for: base), "the failed read")
        XCTAssertEqual("held-key-1234", b.claimKey(for: base), "a failed read was cached as no key")
    }
}

/// Finding 1: every forget goes through the store's one signal, and takes
/// whose the phone was, its key, the "listening as" pick and the waiting
/// songs with it.
@MainActor
final class ListenForgetTests: XCTestCase {

    private let houseA = "http://10.0.0.42:6369"
    private let houseB = "http://10.0.0.43:6369"
    private var dir: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        StubURLProtocol.reset()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("forget-\(UUID().uuidString)")
    }

    override func tearDown() {
        StubURLProtocol.reset()
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private struct Rig {
        let store: InMemoryShellStore
        let model: ShellModel
        let book: DeviceOwnerBook
        let keychain: InMemoryKeychain
        let outbox: ListenOutbox
    }

    private func rig() -> Rig {
        let store = InMemoryShellStore(serverURL: houseA, token: "household-abc")
        store.trustServer(houseA, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: houseB, name: nil)
        store.trustServer(houseB, identity: nil)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let keychain = InMemoryKeychain()
        let book = DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: keychain))
        let outbox = ListenOutbox(file: dir.appendingPathComponent("o.json"))
        let model = ShellModel(
            container: container,
            listeners: PlayerListenerStore(place: SavedStateBox()),
            owners: book,
            listenOutbox: outbox
        )
        for house in [houseA, houseB] {
            book.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "key-for-\(house.suffix(2))-AAAAAAAA", for: house)
            outbox.add(OutboxRow(
                id: "row-\(house.suffix(2))", server: ServerCredentials.normalize(house), trackID: 1,
                deviceID: "ios-1", startedAt: "2026-11-12T18:03:11Z", playedSec: 30, outcome: "stopped"
            ))
        }
        model.listeners.set("5")
        return Rig(store: store, model: model, book: book, keychain: keychain, outbox: outbox)
    }

    /// A saved row's X (the picker's and Settings' — both call
    /// `removeKnownServer`) and the active server's forget.
    func testEveryForgetPathDropsTheOwnerTheKeyThePickAndTheSongs() {
        let r = rig()
        XCTAssertEqual("5", r.model.listeners.pick(for: houseA))
        XCTAssertEqual(2, r.keychain.accounts.count)

        r.store.removeKnownServer(houseB)
        XCTAssertNil(r.book.deviceOwner(for: houseB))
        XCTAssertNil(r.book.claimKey(for: houseB))
        XCTAssertEqual([ServerCredentials.normalize(houseA)], r.keychain.accounts, "a forgotten server's key stayed")
        XCTAssertEqual(["row-" + String(houseA.suffix(2))], r.outbox.all().map(\.id))
        XCTAssertNotNil(r.book.deviceOwner(for: houseA), "forgetting B took A's owner")

        r.model.forgetTheActiveServer()
        XCTAssertNil(r.book.deviceOwner(for: houseA))
        XCTAssertNil(r.book.claimKey(for: houseA))
        XCTAssertEqual([], r.keychain.accounts, "the active server's key stayed")
        XCTAssertNil(r.model.listeners.pick(for: houseA), "the listening-as pick stayed")
        XCTAssertTrue(r.outbox.all().isEmpty)
        r.model.stop()
    }

    /// A different machine at the same address: what the old one said goes.
    func testAReplacedPinDropsTheClaimKey() {
        let r = rig()
        r.store.replacePin(url: houseA, pin: TestIdentity.rogue.pin)
        XCTAssertNil(r.book.claimKey(for: houseA), "the old machine's key would go to the new one")
        XCTAssertNil(r.book.deviceOwner(for: houseA))
        XCTAssertNotNil(r.book.claimKey(for: houseB))
        r.model.stop()
    }

    /// The saved store says so on all three paths too.
    func testTheSavedStoreSignalsEveryForget() {
        let store = SavedShellStore(place: SavedStateBox(), vault: TokenVault(items: InMemoryKeychain()))
        store.trustServer(houseA, identity: nil)
        _ = store.setServerURL(houseA)
        store.upsertKnownServer(url: houseB, name: nil)
        let heard = Shared<[String]>([])
        store.notifyServerForgotten { url in heard.mutate { $0.append(ServerCredentials.normalize(url)) } }
        store.replacePin(url: houseA, pin: TestIdentity.rogue.pin)
        store.removeKnownServer(houseB)
        _ = store.forgetActiveServer()
        XCTAssertEqual(
            [houseA, houseB, houseA].map(ServerCredentials.normalize), heard.current
        )
    }
}

/// Findings 3, 4, 7, 8 and the test gaps on the outbox, the sender and the log.
final class ListenOutboxReviewTests: XCTestCase {

    private let base = "http://10.0.0.9:6369"
    private var dir: URL!
    private var file: URL { dir.appendingPathComponent("listens-outbox.json") }

    override func setUpWithError() throws {
        try super.setUpWithError()
        StubURLProtocol.reset()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("outbox-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        StubURLProtocol.reset()
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func row(_ i: Int) -> OutboxRow {
        OutboxRow(
            id: "row-\(i)", server: ServerCredentials.normalize(base), trackID: 1000 + Int64(i),
            deviceID: "ios-1a2b", startedAt: "2026-11-12T18:03:11Z", playedSec: 30, outcome: "stopped"
        )
    }

    private func sender(_ o: ListenOutbox) -> ListenSender {
        let client = DomovoiClient(
            book: .fixed(server: base, token: "household-abc"), gate: AdmitsAnyServer(),
            configuration: StubURLProtocol.session(timeout: 10)
        )
        let active = ServerCredentials.normalize(base)
        return ListenSender(outbox: o, client: client, server: { active })
    }

    /// The folder is excluded from backup (a flag on the file would be lost
    /// at the next atomic write).
    func testTheOutboxFolderIsExcludedFromBackup() throws {
        let folder = ListenOutbox.excludedFolder(dir.appendingPathComponent("listens", isDirectory: true))
        let values = try folder.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(true, values.isExcludedFromBackup)
    }

    func testAFileThatIsNotAListIsMovedAsideNotOverwritten() throws {
        try Data("not json at all".utf8).write(to: file)
        let o = ListenOutbox(file: file)
        o.add(row(1))
        XCTAssertEqual(["row-1"], o.all().map(\.id))
        let names = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        let aside = try XCTUnwrap(names.first { $0.contains("corrupt") }, "\(names)")
        XCTAssertEqual("not json at all", String(decoding: try Data(contentsOf: dir.appendingPathComponent(aside)), as: UTF8.self))
    }

    func testOneBadRowIsSkippedNotTheList() throws {
        let good = String(decoding: try JSONEncoder().encode([row(1)]), as: UTF8.self)
        let mixed = "[" + #"{"id":"broken"}"# + "," + String(good.dropFirst().dropLast()) + "]"
        try Data(mixed.utf8).write(to: file)
        XCTAssertEqual(["row-1"], ListenOutbox(file: file).all().map(\.id))
    }

    func testANonJsonAnswerIsNotADelivery() async {
        let o = ListenOutbox(file: file)
        o.add(row(1))
        StubURLProtocol.enqueue(.json("<html>a captive portal</html>"))
        let result = await sender(o).flush()
        XCTAssertEqual(.kept, result)
        XCTAssertEqual(1, o.all().count, "a 2xx that is not the server's answer deleted the rows")
    }

    /// Two flushes asked for together send the rows once.
    func testTwoFlushesAtOnceSendTheRowsOnce() async {
        let o = ListenOutbox(file: file)
        o.add(row(1))
        o.add(row(2))
        StubURLProtocol.dispatch { _ in
            // Give a second, parallel flush time to go out.
            let end = Date().addingTimeInterval(0.5)
            while Date() < end, StubURLProtocol.requestCount < 2 { Thread.sleep(forTimeInterval: 0.02) }
            return .json(#"{"accepted":2,"dropped":0,"recording":true}"#)
        }
        let s = sender(o)
        async let a = s.flush()
        async let b = s.flush()
        _ = await (a, b)
        XCTAssertEqual(1, StubURLProtocol.requestCount, "two flushes read the same batch")
        XCTAssertTrue(o.all().isEmpty)
    }
}

@MainActor
final class ListenLogReviewTests: XCTestCase {

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

    /// A skip closes the listen AT ONCE: time between the skip and the next
    /// snapshot is not the skipped song's.
    func testASkipClosesAtOnce() throws {
        var mono: Int64 = 0
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("skip-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let o = ListenOutbox(file: dir.appendingPathComponent("o.json"))
        let log = ListenLog(
            outbox: o,
            tracker: ListenTracker(wallMs: { 0 }, monoMs: { mono }),
            sender: nil,
            server: { "http://10.0.0.5:6369" },
            deviceID: { "ios-1" },
            pickFor: { _ in nil },
            sharedAt: { _ in false },
            io: { $0() }
        )
        let song = PlayerItem.fromTrack(id: 812, title: "Song", durationSec: 200)
        let next = PlayerItem.fromTrack(id: 813, title: "Next", durationSec: 200)
        log.observe(ListenSnapshot(item: song, playing: true, local: true))
        mono += 20_000
        log.skip()
        mono += 30_000
        log.observe(ListenSnapshot(item: next, playing: true, local: true))
        XCTAssertEqual([20], o.all().map(\.playedSec))
        XCTAssertEqual(["skipped"], o.all().map(\.outcome))
    }

    /// The engine's length is the CURRENT item's only while the engine is on
    /// it: once a song has finished and the engine moved on alone, its length
    /// is not reported against the model's item.
    func testTheEnginesLengthIsReportedOnlyForTheSourceItIsOn() {
        final class Recorder: PlayerListenHooks {
            var snapshots: [ListenSnapshot] = []
            func observe(_ snapshot: ListenSnapshot) { snapshots.append(snapshot) }
            func skip() {}
            func ended() {}
            func stopped() {}
        }
        let h = PlayerHarness()
        let r = Recorder()
        h.model.listens = r
        h.engine.durationSec = 210
        h.model.play([PlayerSeed.transmission, PlayerSeed.atmosphere])
        XCTAssertEqual(210, r.snapshots.last?.durationSec, "the engine is on the model's item")
        // The engine advanced by itself (AVQueuePlayer): the fake still says
        // its index is 0, which is NOT the model's new item at index 1.
        h.engine.durationSec = 999
        h.engine.finishCurrentItem()
        XCTAssertEqual(PlayerSeed.atmosphere, r.snapshots.last?.item)
        XCTAssertNil(r.snapshots.last?.durationSec, "another source's length was booked against this item")
    }
}

/// Finding 9 and the test gap: "clear" goes through the model's own guard.
@MainActor
final class ListenClearGuardTests: XCTestCase {

    private let houseA = "http://10.0.0.42:6369"
    private let houseB = "http://10.0.0.43:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/health" {
                let parts = request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
                let challenge = (parts?.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            return .json(#"{"deleted":42}"#)
        }
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func shell() -> (ShellModel, InMemoryShellStore, DeviceOwnerBook) {
        let store = InMemoryShellStore(serverURL: houseA, token: "household-abc")
        store.trustServer(houseA, identity: TestIdentity.real.pin)
        // The same machine's identity at both addresses, so a DELETE sent to
        // B by mistake would get through the identity gate and be seen.
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
            owners: book
        )
        return (model, store, book)
    }

    private let alex = Person(id: 3, name: "Alex")

    private func deletes() -> [URLRequest] { StubURLProtocol.recorded.filter { $0.httpMethod == "DELETE" } }

    private func settle(_ model: PeopleThisPhoneModel) async {
        for _ in 0..<300 {
            if !model.clearing { break }
            try? await Task.sleep(for: .milliseconds(10))
        }
        try? await Task.sleep(for: .milliseconds(50))
    }

    func testSomeoneElsesPhoneSendsNoDelete() async {
        let (model, _, book) = shell()
        book.rememberDeviceOwner(DeviceOwner(id: 5, name: "Priya", claimedAt: "2025-01-01T00:00:00Z"), claimKey: "priya-key-AAAAAAAA", for: houseA)
        let page = PeopleThisPhoneModel(shell: model)
        page.askToClear(alex)
        guard case .notHere? = page.ask?.check else { return XCTFail("\(String(describing: page.ask))") }
        page.confirmClear(alex)
        await settle(page)
        XCTAssertEqual([], deletes(), "a DELETE went from someone else's phone")
        model.stop()
    }

    func testAnAllowedClearGoesAndASwitchedServerRefusesIt() async {
        let (model, store, book) = shell()
        book.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex", claimedAt: "2025-01-01T00:00:00Z"), claimKey: "alex-key-AAAAAAAAA", for: houseA)
        let page = PeopleThisPhoneModel(shell: model)

        // Allowed, then the app moves house before "Clear" is pressed.
        page.askToClear(alex)
        XCTAssertEqual(ClearCheck.allowed, page.ask?.check)
        _ = store.setServerURL(houseB)
        page.confirmClear(alex)
        await settle(page)
        XCTAssertEqual([], deletes(), "the clear went to a server it was not worked out for")
        XCTAssertNil(page.ask)

        // Back home: allowed and sent, with the key.
        _ = store.setServerURL(houseA)
        page.askToClear(alex)
        XCTAssertEqual(ClearCheck.allowed, page.ask?.check, "\(String(describing: page.ask))")
        page.confirmClear(alex)
        XCTAssertTrue(page.clearing, "confirmClear refused an allowed ask")
        await settle(page)
        print("CLEAR GUARD paths: \(StubURLProtocol.recorded.map { "\($0.httpMethod ?? "") \($0.url?.absoluteString ?? "")" }) refused=\(page.clearRefused ?? "-")")
        XCTAssertEqual(["/api/music/listens/people/3"], deletes().map { $0.url?.path ?? "" })
        XCTAssertEqual("alex-key-AAAAAAAAA", deletes().first?.value(forHTTPHeaderField: claimKeyHeader))
        model.stop()
    }
}
