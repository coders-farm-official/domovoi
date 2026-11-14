import XCTest
@testable import DomovoiKit

/// "This is me" on the iPhone (MOBILE-HANDOFF-2026-11-12 §1), ported from
/// `android/.../net/DeviceRegistryTest.kt` (a1e611b6): the server's answer
/// is what the phone stores as its owner, per server; the claim key it hands
/// back once is kept in the Keychain for that server only, sent only to it,
/// sent only when held, and dropped by the web's rules; a refusal is shown,
/// never shown as the owner.
final class ListenClaimTests: XCTestCase {

    private let base = "http://10.0.0.9:6369"
    private let other = "http://10.0.0.5:6369"
    private let minted = "k-minted_once-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func client(server: String? = nil) -> DomovoiClient {
        DomovoiClient(
            book: .fixed(server: server ?? base, token: "household-abc"),
            gate: AdmitsAnyServer(),
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    private func book(keychain: InMemoryKeychain = InMemoryKeychain(), place: SavedStateBox = SavedStateBox()) -> DeviceOwnerBook {
        DeviceOwnerBook(place: place, vault: ClaimKeyVault(items: keychain))
    }

    /// A device row exactly as web/backend/api/devices.py answers it.
    private func row(owner: Int?, name: String? = nil, claimedAt: String? = nil, key: String? = nil, shared: Bool = false) -> String {
        func q(_ v: String?) -> String { v.map { "\"\($0)\"" } ?? "null" }
        return """
        {"device_id":"ios-1a2b","name":"iPhone","platform":"ios",
         "user_agent":"iOS 18.3; iPhone","first_seen_at":"2026-11-01T10:00:00Z",
         "last_seen_at":"2026-11-12T18:00:00.123456Z","shared_screen":\(shared),
         "owner_person_id":\(owner.map(String.init) ?? "null"),"owner_person_name":\(q(name)),
         "owner_claimed_at":\(q(claimedAt)),"claim_key":\(q(key))}
        """
    }

    private func register(_ book: DeviceOwnerBook, client: DomovoiClient? = nil) async -> DeviceRegistry.DeviceRow? {
        await DeviceRegistry.register(
            with: client ?? self.client(), book: book, server: base,
            deviceID: "ios-1a2b", name: "iPhone", userAgent: "iOS 18.3; iPhone"
        )
    }

    // MARK: - the answer is the truth, per server

    func testTheAnswersOwnerIsStoredForThatServerAndNoOther() async throws {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 9, name: "Jordan"), claimKey: "other-key", for: other)
        StubURLProtocol.enqueue(.json(row(owner: 3, name: "Alex", claimedAt: "2026-11-12T17:00:00Z")))
        _ = await register(b)
        XCTAssertEqual(DeviceOwner(id: 3, name: "Alex", claimedAt: "2026-11-12T17:00:00Z"), b.deviceOwner(for: base))
        XCTAssertEqual(DeviceOwner(id: 9, name: "Jordan"), b.deviceOwner(for: other), "another server's owner untouched")
        XCTAssertEqual("other-key", b.claimKey(for: other), "another server's key untouched")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(StubURLProtocol.recorded.first?.httpBody)) as? [String: Any])
        XCTAssertFalse(body.keys.contains("owner_person_id"), "the register body carries no owner")
        XCTAssertNil(StubURLProtocol.header(claimKeyHeader, at: 0), "a registration never carries the claim key")

        // An admin set it to nobody from the dashboard: the next start says so.
        StubURLProtocol.enqueue(.json(row(owner: nil)))
        _ = await register(b)
        XCTAssertNil(b.deviceOwner(for: base))
    }

    func testAnAnswerFromAServerWithoutOwnersChangesNothing() async {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "k1-held-key", for: base)
        StubURLProtocol.enqueue(.json(#"{"device_id":"ios-1a2b","name":"iPhone","shared_screen":false}"#))
        _ = await register(b)
        XCTAssertEqual(DeviceOwner(id: 3, name: "Alex"), b.deviceOwner(for: base))
        XCTAssertEqual("k1-held-key", b.claimKey(for: base))
    }

    /// The registration is filed under the server it was SENT to, and not
    /// sent at all once the app has moved (read-after-await).
    func testARegistrationForAServerTheAppLeftIsNotSent() async {
        let b = book()
        let row = await DeviceRegistry.register(
            with: client(), book: b, server: other,
            deviceID: "ios-1a2b", name: "iPhone", userAgent: "iOS 18.3; iPhone"
        )
        XCTAssertNil(row)
        XCTAssertEqual(0, StubURLProtocol.requestCount)
    }

    // MARK: - "this is me" / "not me"

    func testThisIsMeSendsThePutWithTheDeviceIdAndStoresTheKey() async throws {
        let b = book()
        StubURLProtocol.enqueue(.json(row(owner: 3, name: "Alex", claimedAt: "2026-11-12T18:03:11Z", key: minted)))
        let owner = try await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
        let sent = try XCTUnwrap(StubURLProtocol.recorded.first)
        XCTAssertEqual("PUT", sent.httpMethod)
        XCTAssertEqual("/api/devices/ios-1a2b/person", sent.url?.path)
        XCTAssertEqual("ios-1a2b", sent.value(forHTTPHeaderField: deviceIDHeader))
        XCTAssertNil(sent.value(forHTTPHeaderField: claimKeyHeader), "no key held yet: none sent, not an empty one")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(sent.httpBody)) as? [String: Any])
        XCTAssertEqual(3, body["person_id"] as? Int)
        XCTAssertEqual(DeviceOwner(id: 3, name: "Alex", claimedAt: "2026-11-12T18:03:11Z"), owner)
        XCTAssertEqual(minted, b.claimKey(for: base))
    }

    func testNotMeSendsTheHeldKeyAndTheKeyGoesWithTheOwner() async throws {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "held-key-1234", for: base)
        StubURLProtocol.enqueue(.json(row(owner: nil)))
        let owner = try await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: nil)
        XCTAssertEqual("held-key-1234", StubURLProtocol.header(claimKeyHeader, at: 0))
        XCTAssertEqual("ios-1a2b", StubURLProtocol.header(deviceIDHeader, at: 0))
        let body = String(decoding: try XCTUnwrap(StubURLProtocol.recorded.first?.httpBody), as: UTF8.self)
        XCTAssertTrue(body.contains("\"person_id\":null"), body)
        XCTAssertNil(owner)
        XCTAssertNil(b.claimKey(for: base), "nobody's: the key is dead")
    }

    func testAKeyIsDroppedWhenAnAdminChangedThePerson() async {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "held-key-1234", for: base)
        // Same person, no new key (a plain registration): kept.
        StubURLProtocol.enqueue(.json(row(owner: 3, name: "Alex")))
        _ = await register(b)
        XCTAssertEqual("held-key-1234", b.claimKey(for: base))
        // Another person, no key (an admin set it): dropped.
        StubURLProtocol.enqueue(.json(row(owner: 5, name: "Priya")))
        _ = await register(b)
        XCTAssertEqual(DeviceOwner(id: 5, name: "Priya"), b.deviceOwner(for: base))
        XCTAssertNil(b.claimKey(for: base))
    }

    /// A 403 is thrown with the server's sentence, and nothing about it is
    /// stored as the owner.
    func testARefusedClaimIsShownNotStored() async {
        let b = book()
        let said = "Alex is already claimed by another phone or browser. An admin can add this one too."
        StubURLProtocol.enqueue(.json(#"{"detail":"\#(said)"}"#, status: 403))
        do {
            _ = try await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
            XCTFail("a refusal throws")
        } catch {
            XCTAssertEqual(.takenByAnother, claimRefusal(status: error.failure?.status ?? 0, detail: claimDetail(error)))
            XCTAssertEqual(said, claimDetail(error), "the server's own sentence is what is shown")
        }
        XCTAssertNil(b.deviceOwner(for: base), "still nobody's")
    }

    /// A 409 (a shared screen) is likewise the server's sentence, not swallowed.
    func testASharedScreenRefusalIsShown() async {
        let b = book()
        StubURLProtocol.enqueue(.json(#"{"detail":"this is a shared screen, so it is the whole household's"}"#, status: 409))
        do {
            _ = try await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
            XCTFail("a refusal throws")
        } catch {
            XCTAssertEqual(.sharedScreen, claimRefusal(status: error.failure?.status ?? 0, detail: claimDetail(error)))
            XCTAssertEqual("this is a shared screen, so it is the whole household's", claimDetail(error))
        }
    }

    func testCantProveDropsTheStoredKey() async {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "stale-key-1234", for: base)
        StubURLProtocol.enqueue(.json(
            #"{"detail":"this phone or browser can't prove it is Alex's (its claim key is missing or out of date). An admin can change it, or set it to nobody so it can say \"this is me\" again."}"#,
            status: 403
        ))
        _ = try? await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: nil)
        XCTAssertEqual("stale-key-1234", StubURLProtocol.header(claimKeyHeader, at: 0))
        XCTAssertNil(b.claimKey(for: base))
        XCTAssertEqual(DeviceOwner(id: 3, name: "Alex"), b.deviceOwner(for: base), "the owner stands: the server said so")
    }

    func testTheRefusalsAreTold() {
        XCTAssertEqual(.sharedScreen, claimRefusal(status: 409, detail: "this is a shared screen, so it is the whole household's"))
        XCTAssertEqual(.gone, claimRefusal(status: 404, detail: "person 3 not found"))
        XCTAssertEqual(.notThisDevice, claimRefusal(status: 403, detail: "only that phone or browser itself, or an admin, can change whose it is"))
        XCTAssertEqual(.cantProve, claimRefusal(
            status: 403,
            detail: "this phone or browser said it was Alex's before claims had keys: say \"this is me\" again on Alex's People page first"
        ))
        XCTAssertEqual(.other, claimRefusal(status: 500, detail: nil))
    }

    // MARK: - the key goes to the server that minted it, and nowhere else

    /// The app moved to another house: a claim read for the old one is not
    /// sent at all, so its key cannot reach the new one.
    func testAClaimForAServerTheAppLeftIsNotSent() async {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "held-key-1234", for: other)
        do {
            _ = try await claimThisPhone(client: client(), book: b, server: other, deviceID: "ios-1a2b", personID: nil)
            XCTFail("not sent")
        } catch {
            XCTAssertEqual(DomovoiError.unreachable(DomovoiClient.serverChanged), error)
        }
        XCTAssertEqual(0, StubURLProtocol.requestCount)
    }

    /// The key held for one server is never put on a request to another,
    /// even when both are known.
    func testOnlyTheActiveServersOwnKeyIsSent() async throws {
        let b = book()
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: "key-of-the-other-house", for: other)
        StubURLProtocol.enqueue(.json(row(owner: 3, name: "Alex", key: minted)))
        _ = try await claimThisPhone(client: client(), book: b, server: base, deviceID: "ios-1a2b", personID: 3)
        XCTAssertNil(StubURLProtocol.header(claimKeyHeader, at: 0))
        XCTAssertEqual("key-of-the-other-house", b.claimKey(for: other))
        XCTAssertEqual(minted, b.claimKey(for: base))
    }

    /// A redirect never carries the key: not to another host, and not even
    /// to the same one.
    func testARedirectStripsTheClaimKey() throws {
        var request = URLRequest(url: try XCTUnwrap(URL(string: base + "/api/devices/ios-1a2b/person")))
        request.httpMethod = "GET"
        request.setValue("held-key-1234", forHTTPHeaderField: claimKeyHeader)
        for location in ["http://10.0.0.77:6369/elsewhere", base + "/api/devices/ios-1a2b/person/"] {
            let next = try XCTUnwrap(RedirectPolicy.followUp(request, status: 302, location: location, isWebSocketUpgrade: false))
            XCTAssertNil(next.value(forHTTPHeaderField: claimKeyHeader), location)
        }
    }

    // MARK: - where the key rests

    /// In the Keychain, one item per server, under a service of its own — and
    /// the owners' `UserDefaults` blob never holds it.
    func testTheKeyIsInTheKeychainPerServerAndNotInTheDefaults() throws {
        let keychain = InMemoryKeychain()
        let place = SavedStateBox()
        let b = book(keychain: keychain, place: place)
        b.rememberDeviceOwner(DeviceOwner(id: 3, name: "Alex"), claimKey: minted, for: base + "/")
        XCTAssertEqual([ServerCredentials.normalize(base)], keychain.accounts)
        let blob = String(decoding: try XCTUnwrap(place.read()), as: UTF8.self)
        XCTAssertTrue(blob.contains("Alex"))
        XCTAssertFalse(blob.contains(minted), "the claim key is in the owners' defaults blob")
        // A new process reads both back.
        let again = book(keychain: keychain, place: place)
        XCTAssertEqual(DeviceOwner(id: 3, name: "Alex"), again.deviceOwner(for: base))
        XCTAssertEqual(minted, again.claimKey(for: base))
        // Nobody's again: the item is deleted, not blanked.
        again.rememberDeviceOwner(nil, claimKey: nil, for: base)
        XCTAssertEqual([], keychain.accounts)
        XCTAssertNotEqual(ClaimKeyVault.service, TokenVault.service)
    }

    func testAKeyAHeaderCannotCarryIsNotKept() {
        XCTAssertNil(headerSafeClaimKey("has a space"))
        XCTAssertNil(headerSafeClaimKey(""))
        XCTAssertNil(headerSafeClaimKey("caf\u{00E9}"))
        XCTAssertNil(headerSafeClaimKey(String(repeating: "a", count: 201)))
        XCTAssertEqual(minted, headerSafeClaimKey(" \(minted) "))
        XCTAssertEqual([deviceIDHeader: "ios-1"], claimHeaders(deviceID: "ios-1", claimKey: nil))
        XCTAssertEqual([deviceIDHeader: "ios-1"], claimHeaders(deviceID: "ios-1", claimKey: "bad key"))
    }

    func testTheAnswerDecodes() throws {
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(row(owner: 3, name: "Alex", claimedAt: "2026-11-12T18:03:11.5+00:00").utf8)) as? [String: Any])
        let answer = try XCTUnwrap(ownerAnswer(object, before: nil, heldKey: nil))
        XCTAssertEqual(DeviceOwner(id: 3, name: "Alex", claimedAt: "2026-11-12T18:03:11.5+00:00"), answer.owner)
        XCTAssertNil(answer.claimKey)
        XCTAssertNil(ownerAnswer(["device_id": "x"], before: nil, heldKey: "k"), "no owner field: says nothing")
    }
}
