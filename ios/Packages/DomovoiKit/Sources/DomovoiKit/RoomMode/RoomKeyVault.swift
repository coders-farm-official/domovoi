import Foundation
import Security

/// Where a room keeps its pairing key: one secret, read and written whole.
///
/// Android seals it under its own Keystore key in its own file
/// (`RoomStore.kt:37-40`, `domovoi-room-key`). Here it is one Keychain item
/// with room mode's own service, so it can never be mistaken for, or read
/// as, the household token (``TokenVault`` uses another service). Not
/// `TokenVault` itself: that type keeps its test double behind `Mutex`
/// (iOS 18), and this folder stays iOS-15-clean (ROOMMODE-MAP §6.0).
public protocol RoomKeyVault: AnyObject, Sendable {
    /// The key, or nil when there is none or the Keychain will not give it.
    func read() -> String?
    /// Writes the key, or removes it (`nil`). False when the Keychain refused.
    @discardableResult func write(_ key: String?) -> Bool
}

/// The real one: a generic-password item, this device only, readable after
/// the first unlock (a room phone on its charger restarts without anyone
/// unlocking it — and then the person opens Domovoi, which unlocks it).
public final class RoomKeychainKey: RoomKeyVault {

    public static let service = "org.codersfarm.domovoi.room-key"
    public static let account = "room"

    private let service: String
    private let account: String

    public init(service: String = RoomKeychainKey.service, account: String = RoomKeychainKey.account) {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            // iOS has only this keychain; on macOS (the package tests) the
            // old file keychain would ignore the accessibility class.
            kSecUseDataProtectionKeychain as String: true,
        ]
    }

    public func read() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let text = String(data: data, encoding: .utf8),
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return text
    }

    @discardableResult
    public func write(_ key: String?) -> Bool {
        guard let key, !key.isEmpty, let data = key.data(using: .utf8) else {
            let status = SecItemDelete(query as CFDictionary)
            return status == errSecSuccess || status == errSecItemNotFound
        }
        var add = query
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        add[kSecAttrSynchronizable as String] = false
        add[kSecValueData as String] = data
        let added = SecItemAdd(add as CFDictionary, nil)
        if added == errSecSuccess { return true }
        guard added == errSecDuplicateItem else { return false }
        // In place, never delete-then-add: a process killed in between would
        // leave the room without its key, and the core would park it again.
        let updated = SecItemUpdate(
            query as CFDictionary,
            [
                kSecValueData as String: data,
                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            ] as CFDictionary
        )
        return updated == errSecSuccess
    }
}

/// The tests' key, and a UI-test launch's (`-domovoi-room-key`).
public final class InMemoryRoomKey: RoomKeyVault {
    private let key: RoomLock<String?>

    public init(_ key: String? = nil) {
        self.key = RoomLock(key)
    }

    public func read() -> String? {
        key.withLock { value in
            guard let value, !value.isEmpty else { return nil }
            return value
        }
    }

    @discardableResult
    public func write(_ key: String?) -> Bool {
        self.key.withLock { $0 = key }
        return true
    }
}
