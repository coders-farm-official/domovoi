import Foundation
import Security

// ===========================================================================
// Away chat's keys and its enrolment at home (1.5.0, AWAY-CHAT-WIRE §1).
// ===========================================================================
//
// At home, over the dashboard's API, the phone makes two X25519 key pairs —
// Tor's client authorization, and its Noise static key — keeps both private
// halves in the Keychain and sends the public halves, bound to its device key
// (`POST /api/away/enroll`). The answer is the house's away onion, its port
// and the house's away Noise key, which are kept beside the private keys and
// nowhere else.
//
// The Keychain item is this phone's alone: `WhenUnlockedThisDeviceOnly`
// (never in a backup, never on another device, unreadable while the phone is
// locked — away chat only runs on an open screen anyway) and never
// synchronised. It is wiped when away chat is turned off, when the server is
// forgotten or its identity changes (``ShellStore/notifyServerForgotten(to:)``),
// when the active server switches to another one, when the phone starts over
// at the server under a new device id (the house revokes on a key change),
// and when the house says the phone isn't enrolled any more.

/// The two key encodings the enrolment uses.
public enum AwayKeyCodec {

    private static let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".utf8)

    /// RFC 4648 base32, upper case, no padding: Tor's client-auth key form
    /// (32 bytes -> 52 characters).
    public static func base32(_ data: Data) -> String {
        var out = [UInt8]()
        var buffer = 0
        var bits = 0
        for byte in data {
            buffer = (buffer << 8) | Int(byte)
            bits += 8
            while bits >= 5 {
                out.append(alphabet[(buffer >> (bits - 5)) & 31])
                bits -= 5
            }
        }
        if bits > 0 { out.append(alphabet[(buffer << (5 - bits)) & 31]) }
        return String(decoding: out, as: UTF8.self)
    }

    /// Unpadded base64url (32 bytes -> 43 characters): the Noise keys' form.
    public static func base64url(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    /// `size` bytes from unpadded base64url, or nil for any other shape
    /// (`domovoi/away/keys.py` `unb64url`).
    public static func unbase64url(_ text: String, size: Int) -> Data? {
        guard text.count == (size * 4 + 2) / 3,
              text.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }) else { return nil }
        var b64 = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b64.count % 4 != 0 { b64 += "=" }
        guard let data = Data(base64Encoded: b64), data.count == size, base64url(data) == text else { return nil }
        return data
    }
}

/// What the phone holds for away chat at one server: its two private keys
/// and the enrolment's answer. Never printed: its description says only
/// which server.
public struct AwayEnrolment: Equatable, Sendable, Codable, CustomStringConvertible {
    /// The normalised server address it was made at.
    public let server: String
    public let torAuthPrivate: Data
    public let noisePrivate: Data
    public let onion: String
    public let port: Int
    public let houseNoisePublic: Data
    public let proto: Int

    public init(server: String, torAuthPrivate: Data, noisePrivate: Data, onion: String, port: Int,
                houseNoisePublic: Data, proto: Int) {
        self.server = ServerCredentials.normalize(server)
        self.torAuthPrivate = torAuthPrivate
        self.noisePrivate = noisePrivate
        self.onion = onion
        self.port = port
        self.houseNoisePublic = houseNoisePublic
        self.proto = proto
    }

    public var endpoint: AwayEndpoint { AwayEndpoint(onion: onion, port: port) }
    public var clientAuth: AwayClientAuthKey { AwayClientAuthKey(privateKey: torAuthPrivate) }
    public func noiseKey() throws(NoiseError) -> NoiseKeyPair { try NoiseKeyPair(privateKey: noisePrivate) }

    public var description: String { "AwayEnrolment(\(server))" }
}

/// The real Keychain for the away keys: `WhenUnlockedThisDeviceOnly`, never
/// synchronised, in the data-protection keychain. ``SystemKeychain`` with a
/// stricter class than the household token's (that one must be readable by a
/// room in the background; this is read only on an open chat screen).
public struct AwayKeychain: KeychainItems {
    public static let service = "org.codersfarm.domovoi.away-keys"

    public init() {}

    private func query(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: account,
            kSecUseDataProtectionKeychain as String: true,
        ]
    }

    /// What an item is added with. A function so a test can pin it.
    func addAttributes(account: String, secret: Data) -> [String: Any] {
        var attributes = query(account: account)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        attributes[kSecAttrSynchronizable as String] = false
        attributes[kSecValueData as String] = secret
        return attributes
    }

    public func set(_ secret: Data, account: String) throws {
        // Delete then add, so the item always carries this class (an update
        // keeps the class it was made with).
        try remove(account: account)
        let added = SecItemAdd(addAttributes(account: account, secret: secret) as CFDictionary, nil)
        guard added == errSecSuccess else { throw TokenVaultError.keychain(added) }
    }

    public func get(account: String) throws -> Data? {
        var attributes = query(account: account)
        attributes[kSecAttrSynchronizable as String] = kSecAttrSynchronizableAny
        attributes[kSecReturnData as String] = true
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(attributes as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw TokenVaultError.keychain(status) }
        return result as? Data
    }

    public func remove(account: String) throws {
        var attributes = query(account: account)
        attributes[kSecAttrSynchronizable as String] = kSecAttrSynchronizableAny
        let status = SecItemDelete(attributes as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw TokenVaultError.keychain(status) }
    }
}

/// Where the away enrolment rests: one Keychain item (one enrolment at a
/// time, for the server it was made at), and a flag beside it, outside the
/// Keychain, saying the house must be asked about it next time at home
/// (§8 item 8).
public final class AwayKeyStore: @unchecked Sendable {
    // @unchecked: every mutable thing is behind `lock`; `items` and `place`
    // are Sendable stores.

    static let account = "enrolment"

    private let items: any KeychainItems
    private let flags: any SavedState
    private let lock = NSLock()
    private var cached: AwayEnrolment??

    /// Told after the enrolment comes or goes.
    public var changed: (@Sendable () -> Void)?

    public init(items: any KeychainItems = InMemoryKeychain(), flags: any SavedState = SavedStateBox()) {
        self.items = items
        self.flags = flags
    }

    /// The app's: the real Keychain, and `UserDefaults` for the flags.
    public static func forApp() -> AwayKeyStore {
        AwayKeyStore(items: AwayKeychain(), flags: UserDefaultsSavedState(key: "domovoi.away.flags"))
    }

    /// The enrolment, whichever server it is for.
    public var enrolment: AwayEnrolment? {
        lock.lock()
        defer { lock.unlock() }
        if let cached { return cached }
        let read = (try? items.get(account: Self.account)).flatMap { $0 }
            .flatMap { try? JSONDecoder().decode(AwayEnrolment.self, from: $0) }
        cached = .some(read)
        return read
    }

    /// The enrolment for `server`, or nil.
    public func enrolment(for server: String) -> AwayEnrolment? {
        guard let e = enrolment, e.server == ServerCredentials.normalize(server), !e.server.isEmpty else { return nil }
        return e
    }

    /// Keep it (replacing any other). False when the Keychain didn't take it.
    @discardableResult
    public func keep(_ enrolment: AwayEnrolment) -> Bool {
        guard let data = try? JSONEncoder().encode(enrolment) else { return false }
        let ok: Bool
        lock.lock()
        do {
            try items.set(data, account: Self.account)
            cached = .some(enrolment)
            ok = true
        } catch {
            cached = nil
            ok = false
        }
        lock.unlock()
        setFlags(Flags())
        changed?()
        return ok
    }

    /// Wipe the keys, whatever server they are for.
    public func wipe() {
        lock.lock()
        try? items.remove(account: Self.account)
        cached = .some(nil)
        lock.unlock()
        changed?()
    }

    /// Wipe them if they are `server`'s.
    public func wipe(server: String) {
        guard enrolment(for: server) != nil else { return }
        wipe()
    }

    /// The active server is now `server`: keys made at any other go.
    public func activeServerIs(_ server: String) {
        guard let e = enrolment, e.server != ServerCredentials.normalize(server) else { return }
        wipe()
    }

    // MARK: flags (not secret)

    struct Flags: Codable, Equatable {
        /// A connect met silence: ask `GET /api/away/status` next time home.
        var recheck: String?
        /// The house said this phone isn't enrolled any more (the keys are
        /// gone): the chat says so away, and Settings offers to set it up.
        var lapsed: String?
    }

    func readFlags() -> Flags {
        flags.read().flatMap { try? JSONDecoder().decode(Flags.self, from: $0) } ?? Flags()
    }

    private func setFlags(_ f: Flags) {
        if let data = try? JSONEncoder().encode(f) { flags.write(data) }
    }

    /// A connect to `server` met silence then a close.
    public func markRecheck(_ server: String) {
        var f = readFlags()
        f.recheck = ServerCredentials.normalize(server)
        setFlags(f)
    }

    public func needsRecheck(_ server: String) -> Bool { readFlags().recheck == ServerCredentials.normalize(server) }

    public func clearRecheck() {
        var f = readFlags()
        f.recheck = nil
        setFlags(f)
    }

    /// The house no longer has this phone enrolled at `server`.
    public func markLapsed(_ server: String) {
        var f = readFlags()
        f.lapsed = ServerCredentials.normalize(server)
        f.recheck = nil
        setFlags(f)
        wipe()
    }

    public func lapsed(_ server: String) -> Bool { readFlags().lapsed == ServerCredentials.normalize(server) }
}

// MARK: - the three routes

/// What the house says about away chat (`GET /api/away/status`).
public struct AwayStatus: Equatable, Sendable {
    /// `on`, `away_off` or `internet_off`.
    public let house: String
    public let enrolled: Bool
    /// `ready`, `starting` or nil.
    public let state: String?

    public init(house: String, enrolled: Bool, state: String?) {
        self.house = house
        self.enrolled = enrolled
        self.state = state
    }
}

/// Why setting away chat up (or asking about it) didn't work: each of the
/// enrolment's answers (§1, §8 item 6), in the app's words.
public enum AwayEnrolRefusal: Error, Equatable, Sendable {
    /// 403 `lan_only`: not on the home network.
    case lanOnly
    /// 409 `away_off`.
    case awayOff
    /// 409 `internet_off`.
    case internetOff
    /// 409 `guest`: a guest phone or a shared screen.
    case guest
    /// 401 `device_key_required` (or the device tier's own 401/403): the
    /// phone can't prove which device it is.
    case deviceKey
    /// 501: the house isn't set up yet (no admin).
    case notSetUp
    /// 400 `bad_key`: the keys were refused (made again on the next try).
    case badKey
    /// Not reached, or an answer that made no sense.
    case trouble(String)

    /// The line the app shows.
    public var line: String {
        switch self {
        case .lanOnly: return AwayStrings.homeNetworkOnly
        case .awayOff: return AwayStrings.awayOff
        case .internetOff: return AwayStrings.internetOff
        case .guest: return AwayStrings.guestPhone
        case .deviceKey: return AwayStrings.deviceKeyNeeded
        case .notSetUp: return AwayStrings.houseNotSetUp
        case .badKey, .trouble: return AwayStrings.somethingWrong
        }
    }

    /// From a refused HTTP answer.
    public static func of(_ error: DomovoiError) -> AwayEnrolRefusal {
        guard let f = error.failure else { return .trouble(error.message) }
        let code = (try? JSONSerialization.jsonObject(with: Data(f.body.utf8)) as? [String: Any])?["code"] as? String
        switch (f.status, code) {
        case (403, "lan_only"): return .lanOnly
        case (409, "away_off"): return .awayOff
        case (409, "internet_off"): return .internetOff
        case (409, "guest"): return .guest
        case (400, "bad_key"): return .badKey
        case (401, _), (403, _): return .deviceKey
        case (501, _): return .notSetUp
        default: return .trouble("HTTP \(f.status)")
        }
    }
}

/// `POST|DELETE /api/away/enroll` and `GET /api/away/status`, over a call the
/// app wires to its client (``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:headers:onlyTo:)``,
/// which adds the household token and, for `/api/away`, the device key —
/// ``DeviceKeyRoutes``).
public struct AwayEnrolApi: Sendable {

    public typealias Call = @Sendable (_ method: String, _ path: String, _ body: Data?, _ headers: [String: String]) async throws(DomovoiError) -> Data

    public static let enrollPath = "/api/away/enroll"
    public static let statusPath = "/api/away/status"
    /// The header the wire note names beside the device key. The house
    /// identifies the phone by its key alone; this says which id it means.
    public static let deviceIDHeader = "X-Domovoi-Device-Id"

    private let call: Call
    private let deviceID: @Sendable () -> String

    public init(deviceID: @Sendable @escaping () -> String, call: @escaping Call) {
        self.call = call
        self.deviceID = deviceID
    }

    private var headers: [String: String] {
        let id = deviceID()
        return id.isEmpty ? [:] : [Self.deviceIDHeader: id]
    }

    /// Make the two key pairs, enrol their public halves, and return what to
    /// keep (nothing is kept here: the caller keeps it once it has it).
    public func enroll(server: String) async throws(AwayEnrolRefusal) -> AwayEnrolment {
        let tor = NoiseKeyPair.generate()
        var noise = NoiseKeyPair.generate()
        while noise.publicKey == tor.publicKey { noise = NoiseKeyPair.generate() }
        let body: Data
        do {
            body = try AwayWire.json([
                "tor_auth_pub": AwayKeyCodec.base32(tor.publicKey),
                "noise_pub": AwayKeyCodec.base64url(noise.publicKey),
            ])
        } catch {
            throw .trouble("couldn't write the keys")
        }
        let answer: Data
        do {
            answer = try await call("POST", Self.enrollPath, body, headers)
        } catch {
            throw AwayEnrolRefusal.of(error)
        }
        guard let object = try? JSONSerialization.jsonObject(with: answer) as? [String: Any],
              let onion = object["onion"] as? String, Self.isOnion(onion),
              let port = (object["port"] as? NSNumber)?.intValue, (1...65_535).contains(port),
              let houseKey = (object["house_noise_pub"] as? String).flatMap({ AwayKeyCodec.unbase64url($0, size: 32) }),
              let proto = (object["proto"] as? NSNumber)?.intValue else {
            throw .trouble("the house's answer made no sense")
        }
        guard proto == Int(AwayWire.proto) else { throw .trouble("the house speaks a newer away chat") }
        return AwayEnrolment(server: server, torAuthPrivate: tor.privateKey, noisePrivate: noise.privateKey,
                             onion: onion, port: port, houseNoisePublic: houseKey, proto: proto)
    }

    /// A v3 onion address: 56 base32 characters and `.onion`.
    static func isOnion(_ s: String) -> Bool {
        guard s.hasSuffix(".onion") else { return false }
        let name = s.dropLast(6)
        return name.count == 56 && name.allSatisfy { ("a"..."z").contains($0) || ("2"..."7").contains($0) }
    }

    /// Turn it off for this phone. Always `{"ok": true}` at the house.
    public func unenroll() async throws(AwayEnrolRefusal) {
        do {
            _ = try await call("DELETE", Self.enrollPath, nil, headers)
        } catch {
            throw AwayEnrolRefusal.of(error)
        }
    }

    public func status() async throws(AwayEnrolRefusal) -> AwayStatus {
        let answer: Data
        do {
            answer = try await call("GET", Self.statusPath, nil, headers)
        } catch {
            throw AwayEnrolRefusal.of(error)
        }
        guard let object = try? JSONSerialization.jsonObject(with: answer) as? [String: Any],
              let house = object["house"] as? String, let enrolled = object["enrolled"] as? Bool else {
            throw .trouble("the house's answer made no sense")
        }
        return AwayStatus(house: house, enrolled: enrolled, state: object["state"] as? String)
    }
}
