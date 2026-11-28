import Foundation
import Security
import XCTest
@testable import DomovoiKit

/// The away keys, where they rest and the enrolment's three routes.
final class AwayKeysTests: XCTestCase {

    func testBase32IsRFC4648UpperCaseWithoutPadding() {
        XCTAssertEqual(AwayKeyCodec.base32(Data("foobar".utf8)), "MZXW6YTBOI")
        XCTAssertEqual(AwayKeyCodec.base32(Data("f".utf8)), "MY")
        XCTAssertEqual(AwayKeyCodec.base32(Data()), "")
        let k = AwayKeyCodec.base32(Data(repeating: 0xFF, count: 32))
        XCTAssertEqual(k.count, 52, "Tor's client-auth key: 52 characters")
        XCTAssertEqual(k, k.uppercased())
    }

    func testBase64urlBothWays() {
        let raw = Data((0..<32).map { UInt8(255 - $0) })
        let text = AwayKeyCodec.base64url(raw)
        XCTAssertEqual(text.count, 43)
        XCTAssertFalse(text.contains("=") || text.contains("+") || text.contains("/"))
        XCTAssertEqual(AwayKeyCodec.unbase64url(text, size: 32), raw)
        XCTAssertNil(AwayKeyCodec.unbase64url(text + "A", size: 32))
        XCTAssertNil(AwayKeyCodec.unbase64url(String(text.dropLast()) + "+", size: 32))
    }

    /// The same keys Python's `away_store.tor_auth_raw` and `keys.unb64url`
    /// read: base32 of the 32 bytes, base64url of the 32 bytes.
    func testTheKeysMatchTheHousesReaders() {
        let raw = AwayHex.data("31e0303fd6418d2f8c0e78b91f22e8caed0fbe48656dcf4767e4834f701b8f62")
        XCTAssertEqual(AwayKeyCodec.base32(raw), "GHQDAP6WIGGS7DAOPC4R6IXIZLWQ7PSIMVW46R3H4SBU64A3R5RA")
        XCTAssertEqual(AwayKeyCodec.base64url(raw), "MeAwP9ZBjS-MDni5HyLoyu0Pvkhlbc9HZ-SDT3Abj2I")
    }

    func testTheKeychainItemIsThisDeviceOnlyWhenUnlockedAndNeverSynced() {
        let a = AwayKeychain().addAttributes(account: "x", secret: Data([1]))
        XCTAssertEqual(a[kSecAttrAccessible as String] as! CFString, kSecAttrAccessibleWhenUnlockedThisDeviceOnly)
        XCTAssertEqual(a[kSecAttrSynchronizable as String] as? Bool, false)
        XCTAssertEqual(a[kSecAttrService as String] as? String, AwayKeychain.service)
        XCTAssertEqual(a[kSecUseDataProtectionKeychain as String] as? Bool, true)
    }

    private func enrolment(_ server: String = "http://192.168.1.20:6369") -> AwayEnrolment {
        AwayEnrolment(server: server, torAuthPrivate: Data(repeating: 1, count: 32), noisePrivate: Data(repeating: 2, count: 32),
                      onion: String(repeating: "c", count: 56) + ".onion", port: 80, houseNoisePublic: Data(repeating: 3, count: 32),
                      proto: 1)
    }

    func testTheStoreKeepsOneEnrolmentAndWipesIt() {
        let items = InMemoryKeychain()
        let store = AwayKeyStore(items: items)
        XCTAssertNil(store.enrolment)
        XCTAssertTrue(store.keep(enrolment()))
        XCTAssertEqual(store.enrolment(for: "http://192.168.1.20:6369/"), enrolment())
        XCTAssertNil(store.enrolment(for: "http://192.168.1.30:6369"))
        // Read back from the Keychain, not only the cache.
        XCTAssertEqual(AwayKeyStore(items: items).enrolment, enrolment())
        store.wipe(server: "http://192.168.1.30:6369")
        XCTAssertNotNil(store.enrolment)
        store.wipe(server: "http://192.168.1.20:6369")
        XCTAssertNil(store.enrolment)
        XCTAssertTrue(items.accounts.isEmpty)
    }

    func testSwitchingToAnotherServerWipesTheKeys() {
        let store = AwayKeyStore()
        store.keep(enrolment())
        store.activeServerIs("http://192.168.1.20:6369")
        XCTAssertNotNil(store.enrolment)
        store.activeServerIs("http://192.168.1.30:6369")
        XCTAssertNil(store.enrolment)
        store.keep(enrolment())
        store.activeServerIs("")
        XCTAssertNil(store.enrolment, "forgetting the server leaves none active")
    }

    func testTheRecheckAndLapsedFlags() {
        let store = AwayKeyStore()
        store.keep(enrolment())
        let s = "http://192.168.1.20:6369"
        XCTAssertFalse(store.needsRecheck(s))
        store.markRecheck(s)
        XCTAssertTrue(store.needsRecheck(s))
        XCTAssertFalse(store.needsRecheck("http://192.168.1.30:6369"))
        store.markLapsed(s)
        XCTAssertTrue(store.lapsed(s))
        XCTAssertFalse(store.needsRecheck(s))
        XCTAssertNil(store.enrolment, "the keys go with a lapse")
        store.keep(enrolment())
        XCTAssertFalse(store.lapsed(s), "setting it up again clears it")
    }

    func testAnEnrolmentNeverPrintsItsKeys() {
        XCTAssertEqual("\(enrolment())", "AwayEnrolment(http://192.168.1.20:6369)")
    }

    // MARK: the routes

    final class Calls: @unchecked Sendable {
        let lock = NSLock()
        var seen: [(String, String, Data?, [String: String])] = []
        var answer: Result<Data, DomovoiError> = .success(Data("{}".utf8))

        func call(_ m: String, _ p: String, _ b: Data?, _ h: [String: String]) throws(DomovoiError) -> Data {
            lock.lock(); defer { lock.unlock() }
            seen.append((m, p, b, h))
            return try answer.get()
        }
    }

    private func api(_ calls: Calls) -> AwayEnrolApi {
        AwayEnrolApi(deviceID: { "ios-0123456789abcdef" }) { m, p, b, h throws(DomovoiError) in try calls.call(m, p, b, h) }
    }

    private let onion = String(repeating: "d", count: 56) + ".onion"

    func testEnrollingSendsBothPublicHalvesAndKeepsWhatTheHouseSaid() async throws {
        let calls = Calls()
        let house = NoiseKeyPair.generate()
        calls.answer = .success(Data("""
        {"onion":"\(onion)","port":80,"house_noise_pub":"\(AwayKeyCodec.base64url(house.publicKey))","proto":1,"state":"starting"}
        """.utf8))
        let e = try await api(calls).enroll(server: "http://192.168.1.20:6369")
        XCTAssertEqual(calls.seen.count, 1)
        let (method, path, body, headers) = calls.seen[0]
        XCTAssertEqual(method, "POST")
        XCTAssertEqual(path, "/api/away/enroll")
        XCTAssertEqual(headers["X-Domovoi-Device-Id"], "ios-0123456789abcdef")
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: body!) as? [String: String])
        XCTAssertEqual(Set(sent.keys), ["tor_auth_pub", "noise_pub"])
        XCTAssertEqual(sent["tor_auth_pub"], AwayKeyCodec.base32(try NoiseKeyPair(privateKey: e.torAuthPrivate).publicKey))
        XCTAssertEqual(sent["noise_pub"], AwayKeyCodec.base64url(try e.noiseKey().publicKey))
        XCTAssertNotEqual(e.torAuthPrivate, e.noisePrivate)
        XCTAssertEqual(e.onion, onion)
        XCTAssertEqual(e.port, 80)
        XCTAssertEqual(e.houseNoisePublic, house.publicKey)
        XCTAssertEqual(e.server, "http://192.168.1.20:6369")
    }

    private func refused(_ status: Int, _ code: String?) -> DomovoiError {
        let body = code.map { #"{"detail":"x","code":"\#($0)"}"# } ?? #"{"detail":"x"}"#
        return .refused(ApiFailure(status: status, message: "HTTP \(status)", body: body))
    }

    func testEveryRefusalHasItsOwnLine() async {
        let cases: [(DomovoiError, AwayEnrolRefusal)] = [
            (refused(403, "lan_only"), .lanOnly),
            (refused(409, "away_off"), .awayOff),
            (refused(409, "internet_off"), .internetOff),
            (refused(409, "guest"), .guest),
            (refused(401, "device_key_required"), .deviceKey),
            (refused(401, nil), .deviceKey),
            (refused(403, nil), .deviceKey),
            (.serverTrouble(ApiFailure(status: 501, message: "", body: #"{"detail":"no admin"}"#)), .notSetUp),
            (refused(400, "bad_key"), .badKey),
            (.unreachable("offline"), .trouble("offline")),
        ]
        for (error, expected) in cases {
            let calls = Calls()
            calls.answer = .failure(error)
            do {
                _ = try await api(calls).enroll(server: "http://192.168.1.20:6369")
                XCTFail("enrolled")
            } catch {
                XCTAssertEqual(error, expected)
            }
        }
        XCTAssertEqual(AwayEnrolRefusal.awayOff.line, AwayStrings.awayOff)
        XCTAssertEqual(AwayEnrolRefusal.internetOff.line, AwayStrings.internetOff)
    }

    func testANonsenseAnswerKeepsNothing() async {
        for body in [#"{"onion":"x.onion","port":80,"house_noise_pub":"AAAA","proto":1}"#,
                     #"{"onion":"\#(onion)","port":80,"house_noise_pub":"AAAA","proto":1}"#,
                     #"{"onion":"\#(onion)","port":80,"house_noise_pub":"\#(AwayKeyCodec.base64url(Data(count: 32)))","proto":2}"#] {
            let calls = Calls()
            calls.answer = .success(Data(body.utf8))
            do {
                _ = try await api(calls).enroll(server: "http://192.168.1.20:6369")
                XCTFail("enrolled: \(body)")
            } catch {
                guard case .trouble = error else { return XCTFail("\(error)") }
            }
        }
    }

    func testOffIsADeleteAndStatusIsRead() async throws {
        let calls = Calls()
        calls.answer = .success(Data(#"{"ok":true}"#.utf8))
        try await api(calls).unenroll()
        XCTAssertEqual(calls.seen.last?.0, "DELETE")
        XCTAssertEqual(calls.seen.last?.1, "/api/away/enroll")
        calls.answer = .success(Data(#"{"house":"internet_off","enrolled":true,"state":null}"#.utf8))
        let s = try await api(calls).status()
        XCTAssertEqual(s, AwayStatus(house: "internet_off", enrolled: true, state: nil))
        XCTAssertEqual(calls.seen.last?.0, "GET")
        XCTAssertEqual(calls.seen.last?.1, "/api/away/status")
        XCTAssertEqual(AwayStrings.line(forStatus: s), AwayStrings.internetOff)
    }

    func testTheDeviceKeyGoesToTheAwayRoutes() {
        XCTAssertTrue(DeviceKeyRoutes.carries("/api/away/enroll"))
        XCTAssertTrue(DeviceKeyRoutes.carries("/api/away/status"))
        XCTAssertFalse(DeviceKeyRoutes.carries("/api/awayx"))
    }
}

/// The container's hooks: a server switch, a forget and a device-key start
/// over each wipe the away keys of the server they were made at.
final class AwayKeysWipeTests: XCTestCase {

    private let a = "http://192.168.1.20:6369"
    private let b = "http://192.168.1.30:6369"

    private func container(_ keys: AwayKeyStore) -> (AppContainer, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: a, token: "household-abc")
        store.trustServer(b, identity: TestIdentity.real.pin)
        let c = AppContainer(store: store, vault: TokenVault(items: InMemoryKeychain()), lock: HouseholdLock(),
                             awayKeys: keys)
        return (c, store)
    }

    private func enrolment(_ server: String) -> AwayEnrolment {
        AwayEnrolment(server: server, torAuthPrivate: Data(count: 32), noisePrivate: Data(count: 32),
                      onion: String(repeating: "f", count: 56) + ".onion", port: 80, houseNoisePublic: Data(count: 32), proto: 1)
    }

    func testSwitchingServersWipesTheOldServersKeys() {
        let keys = AwayKeyStore()
        let (_, store) = container(keys)
        keys.keep(enrolment(a))
        _ = store.setServerURL(b)
        XCTAssertNil(keys.enrolment)
    }

    func testForgettingTheServerWipesItsKeys() {
        let keys = AwayKeyStore()
        let (c, _) = container(keys)
        keys.keep(enrolment(a))
        _ = c.store.forgetActiveServer()
        XCTAssertNil(keys.enrolment)
    }

    func testStartingOverUnderANewDeviceIDWipesThem() {
        let keys = AwayKeyStore()
        let (c, _) = container(keys)
        keys.keep(enrolment(a))
        _ = c.deviceKeys.startOver(at: a)
        XCTAssertNil(keys.enrolment)
    }
}
