import Foundation
import Synchronization

// ===========================================================================
// The per-device key (1.5.0, PRIVATE-CHATS.md §2.1; docs/SECURITY_PRIVACY.md
// "Private chats"). `net/DeviceKey.kt` on Android, rule for rule.
// ===========================================================================
//
// The household token is the same on every phone and the device id is
// self-asserted, so neither can say "this chat is THIS phone's". The server
// mints a 256-bit device key the first time this phone's row registers asking
// for one (`want_device_key`), answers it ONCE (`device_key`), and keeps only
// its SHA-256. The phone keeps it per server in the Keychain (its own service,
// this-device-only, never synchronised: ``DeviceKeyVault``) and sends it as
// ``deviceKeyHeader`` on the routes that decide whose something is
// (``DeviceKeyRoutes``).
//
// Where it goes is ``DomovoiClient``'s decision alone, by the household
// token's rules made stricter: only to the ACTIVE server, the very scheme,
// host and port it was issued by (never the drop-in socket's other port),
// only once that server has proved its identity (the claim key's L56 rule),
// never in room mode, never on any redirect hop (``RedirectPolicy`` strips it
// and nothing puts it back), and a header a caller set itself is always
// stripped. It is never logged and never shown, and it never sits in a type
// whose description would print it: it is read straight out of the JSON.

/// The header the device key goes in (`chat_access.py` `DEVICE_KEY_HEADER`).
public let deviceKeyHeader = "X-Domovoi-Device-Key"

/// The registration body's ask (core-0063: on EVERY register, at pairing and
/// on every app start). Without it a 1.5.0 server mints no key.
public let wantDeviceKeyField = "want_device_key"

/// The routes that carry the device key: core-0056's list, and the server
/// routes that read it through `devices.caller_device_row`. Nothing else: the
/// key proves nothing anywhere else, so it goes nowhere else.
public enum DeviceKeyRoutes {
    /// Whole-segment prefixes: `/api/chat` covers `/api/chat/threads/7`,
    /// never `/api/chatter`.
    static let prefixes = [
        "/api/chat",
        "/api/help/deliveries",
        "/ws/state",
        "/api/auth/phone-grant",
        "/api/music/listens",
        "/api/music/up-next",
        "/api/faces",
        "/api/media/search",
        "/api/devices",
    ]

    /// The room plays that replace a room's queue (music.py `_note_room_start`).
    static let exact: Set<String> = [
        "/api/music/play",
        "/api/music/play-playlist",
        "/api/music/play-track",
        "/api/music/play-tracks",
    ]

    public static func carries(_ path: String) -> Bool {
        let p = path.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? path
        if exact.contains(p) { return true }
        // `/api/people/{id}/music-mix`
        let parts = p.split(separator: "/", omittingEmptySubsequences: false)
        if parts.count == 5 || (parts.count == 6 && parts[5].isEmpty),
           parts[0].isEmpty, parts[1] == "api", parts[2] == "people", !parts[3].isEmpty, parts[4] == "music-mix" {
            return true
        }
        return prefixes.contains { p == $0 || p.hasPrefix($0 + "/") }
    }
}

/// A device key as the server mints it (url-safe base64, 43 characters):
/// printable ASCII without a space, at most 200 bytes, or nothing.
/// `headerSafeDeviceKey` (`DeviceKey.kt`).
public func headerSafeDeviceKey(_ key: String?) -> String? {
    headerSafeClaimKey(key)
}

/// What a registration answer says about the device key.
public enum DeviceKeyAnswer: Equatable, Sendable, CustomStringConvertible {
    /// Just minted: keep it. Comes once.
    case minted(String)
    /// The key this phone presented is this row's: nothing to do.
    case ok
    /// The row has a key and this phone didn't present it: lost.
    case lost
    /// Nothing said: a server from before 1.5.0, or one that keeps no keys
    /// yet. The phone works keyless.
    case none

    /// Never the key itself.
    public var description: String {
        switch self {
        case .minted: return "minted(…)"
        case .ok: return "ok"
        case .lost: return "lost"
        case .none: return "none"
        }
    }

    /// Read `device_key` / `device_key_ok` (devices.py register).
    public static func of(_ data: Data) -> DeviceKeyAnswer {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return .none }
        return of(object)
    }

    public static func of(_ object: [String: Any]) -> DeviceKeyAnswer {
        if let minted = headerSafeDeviceKey(object["device_key"] as? String) { return .minted(minted) }
        guard let ok = object["device_key_ok"] as? NSNumber, CFGetTypeID(ok) == CFBooleanGetTypeID() else {
            return .none
        }
        return ok.boolValue ? .ok : .lost
    }
}

/// A fresh device id for a phone starting over at one server: `ios-` and 64
/// bits of hex, as ``DeviceIdentity/mint()``.
public func newDeviceID() -> String { DeviceIdentity.mint() }

/// Where the device keys rest: the Keychain, one generic password per
/// server, under a service of their own (never the household tokens' or the
/// claim keys'), this-device-only and never synchronised (``SystemKeychain``).
public struct DeviceKeyVault: Sendable {
    public static let service = "org.codersfarm.domovoi.device-key"

    private let items: any KeychainItems

    public static func systemItems() -> SystemKeychain { SystemKeychain(service: service) }

    public init(items: any KeychainItems = DeviceKeyVault.systemItems()) {
        self.items = items
    }

    /// What a read found; a Keychain that would not answer is NOT "no key".
    public enum Read: Equatable, Sendable {
        case key(String)
        case none
        case failed
    }

    public func read(for server: String) -> Read {
        let account = ServerCredentials.normalize(server)
        guard !account.isEmpty else { return .none }
        let data: Data?
        do { data = try items.get(account: account) } catch { return .failed }
        guard let data else { return .none }
        guard let text = String(data: data, encoding: .utf8), let safe = headerSafeDeviceKey(text) else { return .none }
        return .key(safe)
    }

    /// Keep `key` for `server` (nil removes it). True once the Keychain took
    /// it: the key is answered once, so a write that didn't land is a key lost.
    @discardableResult
    public func set(_ key: String?, for server: String) -> Bool {
        let account = ServerCredentials.normalize(server)
        guard !account.isEmpty else { return false }
        if let safe = headerSafeDeviceKey(key), let data = safe.data(using: .utf8) {
            return (try? items.set(data, account: account)) != nil
        }
        return (try? items.remove(account: account)) != nil
    }
}

/// Where the device keys are kept, and which device id this phone uses, per
/// server (normalised address). `DeviceKeyBook` (`DeviceKey.kt`).
public protocol DeviceKeyBook: AnyObject, Sendable {
    /// The key this phone holds for `server`, or nil. Never logged.
    func deviceKey(for server: String) -> String?
    /// Keep `key` for `server` (nil: forget it). True once it is stored.
    @discardableResult func rememberDeviceKey(_ key: String?, for server: String) -> Bool
    /// The device id this phone started over under at `server` after losing
    /// its key there, or nil: the install's own id.
    func startedOverID(for server: String) -> String?
    /// The key for `server` is lost (`device_key_ok: false`): forget it and
    /// start over there under a NEW device id, which is returned. The old id's
    /// private chats stay on the server, for its admins. Other servers keep
    /// their ids and keys.
    func startOver(at server: String) -> String
}

/// The app's ``DeviceKeyBook``: keys in the Keychain (``DeviceKeyVault``),
/// the per-server ids in `UserDefaults` (not secret: an id rides on every
/// queue row). The tests use the same type over a ``SavedStateBox`` and an
/// ``InMemoryKeychain``, and that is also the default, so nothing built
/// without the app's own (a test's container) touches the real Keychain.
///
/// `@unchecked Sendable` with the reason the house rule asks for: every
/// mutable thing is behind the one `Mutex`, the Keychain and `UserDefaults`
/// writes included; ``changed`` is set once by the shell before any call.
public final class DeviceKeyStore: DeviceKeyBook, @unchecked Sendable {

    public static let idsKey = "domovoi.device.ids"

    private struct State {
        var ids: [String: String]
        /// Keys read from the Keychain so far; `.some(nil)` is "read, none".
        var keys: [String: String?] = [:]
    }

    private let place: any SavedState
    private let vault: DeviceKeyVault
    private let state: Mutex<State>

    /// Told after a key comes or goes. Carries nothing.
    public var changed: (@Sendable () -> Void)?

    /// Told which server this phone started over at (a lost key, a new
    /// device id there): a grant kept for it goes (``AdminSession``). Set
    /// once by the container before any call.
    public var startedOver: (@Sendable (String) -> Void)?

    public init(place: any SavedState = SavedStateBox(), vault: DeviceKeyVault = DeviceKeyVault(items: InMemoryKeychain())) {
        self.place = place
        self.vault = vault
        let ids = place.read().flatMap { try? JSONDecoder().decode([String: String].self, from: $0) } ?? [:]
        self.state = Mutex(State(ids: ids))
    }

    /// The app's: the Keychain and `UserDefaults`.
    public static func forApp() -> DeviceKeyStore {
        DeviceKeyStore(place: UserDefaultsSavedState(key: idsKey), vault: DeviceKeyVault())
    }

    private static func k(_ server: String) -> String { ServerCredentials.normalize(server) }

    public func deviceKey(for server: String) -> String? {
        let key = Self.k(server)
        guard !key.isEmpty else { return nil }
        return state.withLock { s in
            if let cached = s.keys[key] { return cached }
            switch vault.read(for: key) {
            case .key(let found):
                s.keys[key] = .some(found)
                return found
            case .none:
                s.keys[key] = .some(nil)
                return nil
            case .failed:
                // Not cached: asked again next time.
                return nil
            }
        }
    }

    public func holdsDeviceKey(for server: String) -> Bool { deviceKey(for: server) != nil }

    @discardableResult
    public func rememberDeviceKey(_ key: String?, for server: String) -> Bool {
        let k = Self.k(server)
        guard !k.isEmpty else { return false }
        let safe = headerSafeDeviceKey(key)
        let stored = state.withLock { s -> Bool in
            let ok = vault.set(safe, for: k)
            s.keys[k] = .some(safe)
            return ok
        }
        changed?()
        return stored
    }

    public func startedOverID(for server: String) -> String? {
        state.withLock { $0.ids[Self.k(server)] }
    }

    public func startOver(at server: String) -> String {
        let k = Self.k(server)
        let id = newDeviceID()
        guard !k.isEmpty else { return id }
        state.withLock { s in
            _ = vault.set(nil, for: k)
            s.keys[k] = .some(nil)
            s.ids[k] = id
            if let blob = try? JSONEncoder().encode(s.ids) { place.write(blob) }
        }
        changed?()
        startedOver?(k)
        return id
    }

    /// The phone forgot `server`, or the machine at its address changed (a
    /// replaced pin): its key goes (it must not go to whatever holds the new
    /// identity), and so does any id it started over under there.
    public func forget(server: String) {
        let k = Self.k(server)
        guard !k.isEmpty else { return }
        state.withLock { s in
            _ = vault.set(nil, for: k)
            s.keys[k] = .some(nil)
            if s.ids.removeValue(forKey: k) != nil, let blob = try? JSONEncoder().encode(s.ids) { place.write(blob) }
        }
        changed?()
    }
}
