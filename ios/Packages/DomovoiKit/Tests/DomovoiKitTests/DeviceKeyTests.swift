import XCTest
import Synchronization
@testable import DomovoiKit

/// The per-device key (1.5.0, PRIVATE-CHATS.md §2.1, core-0056/core-0063):
/// every registration asks for it, the key answered once is kept (in the
/// Keychain, its own service) and presented from then on, a lost key starts
/// the phone over under a new device id at that server only, an older
/// server leaves the phone keyless, and the header goes only to the active
/// server's key routes, only after it proved itself, never in room mode and
/// never on a redirect. The twin of Android's `DeviceKeyTest.kt`.
final class DeviceKeyTests: XCTestCase {

    private let server = "http://10.0.0.9:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func client(
        keys: DeviceKeyStore,
        token: String? = "household-abc",
        lock: HouseholdLock = HouseholdLock(),
        gate: any TokenGate = AdmitsAnyServer(),
        active: (@Sendable () -> String)? = nil
    ) -> DomovoiClient {
        let server = self.server
        return DomovoiClient(
            book: ServerBook(
                activeServer: active ?? { server },
                tokenForServer: { _ in token },
                deviceKeyForServer: { keys.deviceKey(for: $0) }
            ),
            gate: gate,
            lock: lock,
            configuration: StubURLProtocol.session(timeout: nil)
        )
    }

    private struct ClosedGate: TokenGate {
        func requireAdmitted(_ base: ServerURL) throws {
            throw DomovoiError.notPermitted("this server has not proved itself on this network")
        }
    }

    /// The server's register (devices.py `_device_key`), with its rows.
    private final class FakeServer: Sendable {
        struct State {
            var rows: [String: String?] = [:]
            var minted = 0
            var asked: [(id: String, want: Bool?, presented: String?)] = []
        }
        let state = Mutex(State())
        let keys: Bool
        init(keys: Bool = true) { self.keys = keys }

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard request.url?.path == "/api/devices/register" else { return .json("{}") }
            // The stub hands the dispatcher the request with its body stream
            // already read; the recorded copy has the bytes.
            let body = StubURLProtocol.recorded.last?.httpBody ?? Data()
            let object = (try? JSONSerialization.jsonObject(with: body) as? [String: Any]) ?? [:]
            let id = object["device_id"] as? String ?? ""
            let want = object["want_device_key"] as? Bool
            let presented = request.value(forHTTPHeaderField: deviceKeyHeader)
            let (key, ok): (String?, Bool?) = state.withLock { s in
                s.asked.append((id, want, presented))
                let stored = s.rows[id] ?? nil
                if s.rows[id] == nil { s.rows[id] = .some(nil) }
                guard keys else { return (nil, nil) }
                if let stored { return (nil, stored == presented) }
                guard want == true else { return (nil, nil) }
                s.minted += 1
                let made = "dk-\(s.minted)-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                s.rows[id] = .some(made)
                return (made, true)
            }
            func q(_ v: String?) -> String { v.map { "\"\($0)\"" } ?? "null" }
            let extra = keys ? #","device_key":\#(q(key)),"device_key_ok":\#(ok.map { $0 ? "true" : "false" } ?? "null")"# : ""
            return .json(#"{"device_id":"\#(id)","name":"iPhone","shared_screen":false,"owner_person_id":null,"claim_key":null\#(extra)}"#)
        }
    }

    private func register(_ client: DomovoiClient, _ keys: DeviceKeyStore, book: DeviceOwnerBook) async -> DeviceRegistry.DeviceRow? {
        await DeviceRegistry.register(
            with: client, book: book, keys: keys, server: server,
            deviceID: "ios-1a2b3c4d5e6f7a8b", name: "iPhone", userAgent: "iOS 18; iPhone"
        )
    }

    private func owners() -> DeviceOwnerBook {
        DeviceOwnerBook(place: SavedStateBox(), vault: ClaimKeyVault(items: InMemoryKeychain()))
    }

    // ---- registration -------------------------------------------------------

    func testEveryRegistrationAsksForTheKey() async throws {
        let body = try XCTUnwrap(JSONSerialization.jsonObject(
            with: DeviceRegistry.registerPayload(deviceID: "ios-1", name: "n", userAgent: "u")
        ) as? [String: Any])
        XCTAssertEqual(body["want_device_key"] as? Bool, true)

        let fake = FakeServer()
        StubURLProtocol.dispatch { fake.answer($0) }
        let keys = DeviceKeyStore()
        let c = client(keys: keys)
        let book = owners()
        for _ in 0..<3 { _ = await register(c, keys, book: book) }
        XCTAssertEqual(fake.state.withLock { $0.asked.map(\.want) }, [true, true, true])
    }

    func testTheKeyAnsweredOnceIsKeptAndPresentedFromThenOn() async {
        let fake = FakeServer()
        StubURLProtocol.dispatch { fake.answer($0) }
        let items = InMemoryKeychain()
        let keys = DeviceKeyStore(vault: DeviceKeyVault(items: items))
        let c = client(keys: keys)
        let book = owners()
        _ = await register(c, keys, book: book)
        let key = keys.deviceKey(for: server)
        XCTAssertEqual(key, "dk-1-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        XCTAssertEqual(items.accounts, ["http://10.0.0.9:6369"], "kept in the Keychain, by server")
        _ = await register(c, keys, book: book)
        _ = await register(c, keys, book: book)
        let asked = fake.state.withLock { $0.asked }
        XCTAssertNil(asked[0].presented)
        XCTAssertEqual(asked[1].presented, key)
        XCTAssertEqual(asked[2].presented, key)
        XCTAssertEqual(fake.state.withLock { $0.minted }, 1, "a repeat register changes nothing")
        XCTAssertNil(keys.startedOverID(for: server))
    }

    func testALostKeyStartsOverUnderANewIdAndRegistersAgain() async {
        let fake = FakeServer()
        fake.state.withLock { $0.rows["ios-1a2b3c4d5e6f7a8b"] = .some("dk-old-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB") }
        StubURLProtocol.dispatch { fake.answer($0) }
        let keys = DeviceKeyStore()
        keys.rememberDeviceKey("other-key", for: "http://10.0.0.20:6369")
        let c = client(keys: keys)
        let book = owners()
        _ = await register(c, keys, book: book)
        let asked = fake.state.withLock { $0.asked }
        XCTAssertEqual(asked.count, 2)
        XCTAssertEqual(asked[0].id, "ios-1a2b3c4d5e6f7a8b")
        let fresh = asked[1].id
        XCTAssertNotEqual(fresh, "ios-1a2b3c4d5e6f7a8b", "a NEW device id")
        XCTAssertEqual(keys.startedOverID(for: server), fresh)
        XCTAssertEqual(keys.deviceKey(for: server), fake.state.withLock { $0.rows[fresh] ?? nil })
        XCTAssertEqual(fake.state.withLock { $0.rows["ios-1a2b3c4d5e6f7a8b"] ?? nil },
                       "dk-old-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "the old row keeps its key, for the admins")
        XCTAssertEqual(keys.deviceKey(for: "http://10.0.0.20:6369"), "other-key", "another server is untouched")
        XCTAssertNil(keys.startedOverID(for: "http://10.0.0.20:6369"))
        // The next start is just a repeat, under the new id.
        _ = await register(c, keys, book: book)
        XCTAssertEqual(fake.state.withLock { $0.asked.last?.id }, fresh)
        XCTAssertEqual(fake.state.withLock { $0.asked.last?.presented }, keys.deviceKey(for: server))
    }

    func testAnOlderServerLeavesThePhoneKeyless() async {
        let fake = FakeServer(keys: false)
        StubURLProtocol.dispatch { fake.answer($0) }
        let keys = DeviceKeyStore()
        let c = client(keys: keys)
        let book = owners()
        _ = await register(c, keys, book: book)
        _ = await register(c, keys, book: book)
        XCTAssertNil(keys.deviceKey(for: server))
        XCTAssertNil(keys.startedOverID(for: server))
        XCTAssertEqual(fake.state.withLock { $0.asked.count }, 2)
    }

    func testTheAnswerNeverPrintsTheKey() {
        let minted = DeviceKeyAnswer.of(Data(#"{"device_key":"dk-secret-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE","device_key_ok":true}"#.utf8))
        XCTAssertEqual(minted, .minted("dk-secret-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"))
        XCTAssertFalse("\(minted)".contains("secret"))
        XCTAssertEqual(DeviceKeyAnswer.of(Data(#"{"device_key":null,"device_key_ok":true}"#.utf8)), .ok)
        XCTAssertEqual(DeviceKeyAnswer.of(Data(#"{"device_key":null,"device_key_ok":false}"#.utf8)), .lost)
        XCTAssertEqual(DeviceKeyAnswer.of(Data(#"{"device_key_ok":null}"#.utf8)), .none)
        XCTAssertEqual(DeviceKeyAnswer.of(Data("{}".utf8)), .none)
    }

    func testForgettingAServerTakesItsKeyAndId() {
        let keys = DeviceKeyStore()
        keys.rememberDeviceKey("dk-A-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC", for: server)
        _ = keys.startOver(at: server)
        keys.rememberDeviceKey("dk-B-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC", for: server)
        keys.forget(server: server)
        XCTAssertNil(keys.deviceKey(for: server))
        XCTAssertNil(keys.startedOverID(for: server))
    }

    // ---- where the header goes ---------------------------------------------

    private func keyed() -> DeviceKeyStore {
        let keys = DeviceKeyStore()
        keys.rememberDeviceKey("dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", for: server)
        keys.rememberDeviceKey("dk-elsewhere-FFFFFFFFFFFFFFFFFFFFFFFFFFFFF", for: "http://10.0.0.10:6369")
        return keys
    }

    func testTheKeyRoutesCarryItAndNothingElseDoes() async throws {
        let c = client(keys: keyed())
        let keyRoutes = [
            "/api/chat/threads", "/api/chat/threads/7/messages", "/api/help/deliveries?person_id=3",
            "/api/auth/phone-grant", "/api/auth/phone-grant/exchange", "/api/people/3/music-mix",
            "/api/music/listens/people/3", "/api/music/up-next/sessions", "/api/music/play-track",
            "/api/faces/people/3/forget", "/api/media/search", "/api/devices/register",
        ]
        let plain = ["/api/music/library", "/api/timers", "/api/chatter", "/api/health", "/api/people"]
        for _ in 0..<(keyRoutes.count + plain.count) { StubURLProtocol.enqueue(.json("{}")) }
        for path in keyRoutes + plain { try await c.raw(method: "GET", path: path) }
        for i in keyRoutes.indices {
            XCTAssertEqual(StubURLProtocol.header(deviceKeyHeader, at: i), "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", keyRoutes[i])
        }
        for i in plain.indices {
            XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: keyRoutes.count + i), plain[i])
        }
    }

    func testNeverToAnotherHostOrPortAndACallersOwnIsStripped() async throws {
        let c = client(keys: keyed())
        StubURLProtocol.enqueue(.json("{}"), .json("{}"), .json("{}"))
        try await c.raw(method: "GET", path: "http://10.0.0.10:6369/api/chat/threads",
                        headers: [deviceKeyHeader: "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"])
        try await c.raw(method: "GET", path: "/api/music/library", headers: [deviceKeyHeader: "forged"])
        try await c.raw(method: "GET", path: "/api/chat/threads", headers: [deviceKeyHeader: "forged"])
        XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: 0), "another host")
        XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: 1), "a caller's header on a plain route")
        XCTAssertEqual(StubURLProtocol.header(deviceKeyHeader, at: 2), "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
                       "and the real one on a key route, whatever a caller set")
        // The socket: the state socket carries it, the drop-in socket on the
        // core's port (where the token goes) does not.
        XCTAssertEqual(c.webSocketUpgrade("ws://10.0.0.9:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceKeyHeader),
                       "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF")
        XCTAssertNil(c.webSocketUpgrade("ws://10.0.0.9:6370/ws/state")?.mint()?.value(forHTTPHeaderField: deviceKeyHeader))
    }

    func testNeverInRoomMode() async throws {
        let lock = HouseholdLock()
        let c = client(keys: keyed(), lock: lock)
        lock.setRoomModeActive(true)
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        try await c.raw(method: "GET", path: "/api/chat/threads")
        XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: 0))
        XCTAssertNil(c.webSocketUpgrade("ws://10.0.0.9:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceKeyHeader))
        lock.setRoomModeActive(false)
        try await c.raw(method: "GET", path: "/api/chat/threads")
        XCTAssertEqual(StubURLProtocol.header(deviceKeyHeader, at: 1), "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF")
    }

    func testItWaitsForTheServerToProveItselfTokenOrNot() async throws {
        let held = await {
            do {
                try await client(keys: keyed(), token: nil, gate: ClosedGate()).raw(method: "GET", path: "/api/chat/threads")
                return false
            } catch { return true }
        }()
        XCTAssertTrue(held)
        XCTAssertEqual(StubURLProtocol.requestCount, 0, "nothing left the phone")
        // A keyless route of an unpaired phone still goes, bare, as before.
        StubURLProtocol.enqueue(.json("{}"))
        try await client(keys: keyed(), token: nil, gate: ClosedGate()).raw(method: "GET", path: "/api/health")
        XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: 0))
    }

    func testARedirectHopNeverCarriesIt() async throws {
        let c = client(keys: keyed())
        StubURLProtocol.enqueue(.moved(302, to: "\(server)/api/chat/threads?again=1"), .json("{}"))
        try await c.raw(method: "GET", path: "/api/chat/threads")
        XCTAssertEqual(StubURLProtocol.header(deviceKeyHeader, at: 0), "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF")
        if StubURLProtocol.requestCount > 1 {
            XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: 1), "a hop is not the request made to the server")
        }
    }

    func testTheKeyIsTheOneTheActiveServerIssued() async throws {
        let active = Mutex("http://10.0.0.9:6369")
        let keys = keyed()
        let c = client(keys: keys, active: { active.withLock { $0 } })
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        try await c.raw(method: "GET", path: "/api/chat/threads")
        active.withLock { $0 = "http://10.0.0.20:6369" }
        // Still addressed to the old server: out of scope now, no key.
        try await c.raw(method: "GET", path: "http://10.0.0.9:6369/api/chat/threads")
        XCTAssertEqual(StubURLProtocol.header(deviceKeyHeader, at: 0), "dk-active-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF")
        XCTAssertNil(StubURLProtocol.header(deviceKeyHeader, at: 1))
    }

    func testTheRouteListMatchesWholeSegments() {
        XCTAssertTrue(DeviceKeyRoutes.carries("/api/chat"))
        XCTAssertTrue(DeviceKeyRoutes.carries("/api/chat/threads?scope=all"))
        XCTAssertFalse(DeviceKeyRoutes.carries("/api/chatter"))
        XCTAssertTrue(DeviceKeyRoutes.carries("/api/music/play"))
        XCTAssertFalse(DeviceKeyRoutes.carries("/api/music/playlists"))
        XCTAssertTrue(DeviceKeyRoutes.carries("/api/people/3/music-mix"))
        XCTAssertFalse(DeviceKeyRoutes.carries("/api/people/3"))
        XCTAssertFalse(DeviceKeyRoutes.carries("/api/people/3/music-mix/x"))
        XCTAssertFalse(DeviceKeyRoutes.carries("/v1/dropin/kitchen"))
    }
}
