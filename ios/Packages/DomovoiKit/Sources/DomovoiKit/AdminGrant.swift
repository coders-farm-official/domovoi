import Foundation
import Synchronization

/// The day's grant on this phone (1.5.0; `docs/SECURITY_PRIVACY.md`, "Admin on
/// a phone: the day's grant"). A port of `android/.../net/AdminGrant.kt`.
///
/// After a password sign-in the phone may ask the server for a grant
/// (`POST /api/auth/phone-grant`): 256 random bits, bound to this phone's
/// device id, good for 24 hours from issue. The phone keeps it ONLY behind
/// its biometrics, one use at a time (``GrantLocker``), and swaps it for a
/// fresh admin session when it needs one (`POST /api/auth/phone-grant/exchange`).
/// Never the password, never a Bearer on disk; the grant itself is never
/// logged.
///
/// A stored grant is bound to the server it came from AND the identity that
/// server proved then: it is offered only to that server, after it proves
/// that same key again. A different key (a re-pin, another machine) drops it.
///
/// ### Where the secret is, which is the one place this differs from Android
///
/// Android seals the grant with an AES-GCM Keystore key that its strong
/// biometrics unlock, and keeps the **ciphertext** in Prefs beside the
/// server and identity it is bound to. iOS has the Keychain, which is that
/// arrangement already: a generic-password item whose access control asks
/// for the current Face ID or Touch ID set holds the grant itself
/// (``KeychainGrantLocker``). So what the shelf keeps is the metadata and a
/// **handle** to that item — ``GrantLocker/Sealed`` is opaque bytes the
/// locker hands back from ``GrantLocker/seal(_:)`` and is given again to
/// ``GrantLocker/open(_:)``. For the Keychain locker those bytes name the
/// item; for the tests' fake they are a scrambled copy, so the test that
/// says "never stored in the clear" still means something here.

/// The grant as this phone keeps it: who issued it and how to open it.
/// `StoredGrant` (`AdminGrant.kt:23`).
public struct StoredGrant: Codable, Equatable, Sendable {

    /// The server's base address, as ``ServerCredentials/normalize(_:)``
    /// gives it.
    public let server: String

    /// The identity the server proved when it issued the grant.
    public let fingerprint: String

    public let deviceID: String

    /// When the server said it ends (its clock decides; this is a hint), in
    /// wall-clock epoch milliseconds.
    public let expiresAtMs: Int64

    /// ``GrantLocker/Sealed/bytes``, base64.
    public let sealed: String

    public init(server: String, fingerprint: String, deviceID: String, expiresAtMs: Int64, sealed: String) {
        self.server = server
        self.fingerprint = fingerprint
        self.deviceID = deviceID
        self.expiresAtMs = expiresAtMs
        self.sealed = sealed
    }

    enum CodingKeys: String, CodingKey {
        case server
        case fingerprint
        case deviceID = "device_id"
        case expiresAtMs = "expires_at_ms"
        case sealed
    }
}

/// Where the one stored grant lives. `GrantShelf` (`AdminGrant.kt:37`), which
/// Android's Prefs is.
public protocol GrantShelf: Sendable {
    func readGrant() -> StoredGrant?
    func writeGrant(_ grant: StoredGrant?)
}

/// The app's shelf: one JSON blob under its own `UserDefaults` key, in the
/// shape ``UserDefaultsSavedState/chatPinsKey`` set — a key of its own rather
/// than a field in the shell's blob, because the shell's blob is one write of
/// the server, the trust and the pins, and the grant's metadata has nothing
/// to do with any of them. It holds no secret: the grant is the Keychain's.
///
/// Read once, at `init`, and kept in memory after that, as Android's Prefs
/// keeps `adminGrant` in a `@Volatile` field.
public final class SavedGrantShelf: GrantShelf {

    /// The key, under the same prefix as the shell's.
    public static let key = "domovoi.admin.grant"

    private let place: any SavedState
    private let held: Mutex<StoredGrant?>

    public init(place: any SavedState = UserDefaultsSavedState(key: SavedGrantShelf.key)) {
        self.place = place
        let loaded = place.read().flatMap { try? JSONDecoder().decode(StoredGrant.self, from: $0) }
        held = Mutex(loaded)
    }

    public func readGrant() -> StoredGrant? { held.withLock { $0 } }

    /// `nil` writes an empty blob rather than removing the key, because
    /// ``SavedState`` has no remove; an empty blob reads back as no grant.
    public func writeGrant(_ grant: StoredGrant?) {
        held.withLock { $0 = grant }
        place.write(grant.flatMap { try? JSONEncoder().encode($0) } ?? Data())
    }
}

/// A shelf that keeps nothing past the process: the tests', and a launch
/// that must not write the defaults.
public final class InMemoryGrantShelf: GrantShelf {
    private let held = Mutex<StoredGrant?>(nil)
    public init() {}
    public func readGrant() -> StoredGrant? { held.withLock { $0 } }
    public func writeGrant(_ grant: StoredGrant?) { held.withLock { $0 = grant } }
}

/// The biometric-gated place the grant is kept. `GrantLocker`
/// (`AdminGrant.kt:48`).
///
/// Both sealing and opening ask for the phone's biometrics. Enrolling new
/// biometrics makes what was sealed unreadable: ``open(_:)`` then throws
/// ``GrantLockerError/keyGone(_:)`` and the grant is dropped.
public protocol GrantLocker: Sendable {

    /// Face ID or Touch ID is set up and can be asked for.
    var available: Bool { get }

    /// Keep `grant` behind the biometrics; returns what opens it again.
    /// Throws ``GrantLockerError``.
    func seal(_ grant: String) async throws -> GrantSealed

    /// The grant `sealed` was made from. Throws ``GrantLockerError``.
    func open(_ sealed: GrantSealed) async throws -> String

    /// Deletes what is kept (sign-out, room mode, a pin change): whatever was
    /// sealed is gone.
    func destroy()
}

/// What ``GrantLocker/seal(_:)`` hands back: opaque to everyone but the
/// locker that made it. `GrantLocker.Sealed`.
public struct GrantSealed: Equatable, Sendable {
    public let bytes: Data
    public init(bytes: Data) { self.bytes = bytes }
}

/// Why the locker could not do it. Android's `GrantLocker.Cancelled` and
/// `GrantLocker.KeyGone`, plus the plain `IOException` its seal throws when
/// there is no key to be had.
public enum GrantLockerError: Error, Equatable, Sendable {

    /// The person cancelled the prompt, or it failed: nothing changed.
    case cancelled(String)

    /// What was sealed can never be opened again (new biometrics enrolled,
    /// the passcode removed, the item gone).
    case keyGone(String)

    /// The locker could not keep it at all (no passcode, no biometrics).
    case unavailable(String)

    public var message: String {
        switch self {
        case .cancelled(let why), .keyGone(let why), .unavailable(let why): return why
        }
    }

    /// `KeyGone`'s message (`AdminGrant.kt:59`), with the iPhone's words.
    public static let keyGoneMessage =
        "Face ID or Touch ID on this phone changed; sign in with the password again"
}
