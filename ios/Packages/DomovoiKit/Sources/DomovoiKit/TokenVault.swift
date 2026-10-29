import Foundation
import Security
import Synchronization

/// Where the household device tokens rest.
///
/// Android seals them with AES-256-GCM under a non-exportable Keystore key
/// (`android/.../data/TokenVault.kt`, security round 3, A6-04) because its
/// own preference store is a plain file. iOS has the Keychain, which is that
/// arrangement already — a secret held by the system, under a key the app
/// never sees — so this type is the Keychain used exactly as plan 1.2 says:
/// a generic password per server, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`,
/// never synchronised.
///
/// **What each attribute buys, and why these two.**
///
///  * `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. "AfterFirstUnlock"
///    rather than "WhenUnlocked" because the app has background work that
///    must reach the server with the phone in a pocket (the container
///    guard's check, a background refresh, a resumed download). "ThisDevice
///    Only" is the half that matters for a household credential: Apple
///    documents that such an item is not included in an iCloud or iTunes
///    backup, so the token cannot be restored onto a different phone — which
///    is what Android gets from `allowBackup=false` plus a device-bound
///    Keystore key.
///  * `kSecAttrSynchronizable` false. iCloud Keychain would put the
///    household token on every Apple device signed into that account,
///    including ones that have never been in the house. The token is per
///    server and per install on purpose.
///
/// **On macOS, and therefore in these tests.** The attributes above describe
/// the *data protection* keychain, which on macOS a process gets only by
/// asking for it (`kSecUseDataProtectionKeychain`) and only when it is
/// signed with a keychain-access-group entitlement. `swift test` on a Mac is
/// neither, so the real-Keychain test below reports its skip with the
/// `OSStatus` it got rather than passing quietly. Everything else is tested
/// against ``InMemoryKeychain``.
///
/// **What survives deleting the app — and what we rely on.** Apple documents
/// no guarantee either way, and the behaviour has changed between releases,
/// so this vault relies on neither: it never wipes anything at first launch
/// (so a token that survives a reinstall keeps working and the person is not
/// asked to pair again for nothing), and the whole app is built to treat a
/// missing token as "not paired yet", which is a screen it already has. The
/// forget path is the only thing that deliberately removes an item, and it
/// removes it — ``forget(server:)`` deletes the Keychain item rather than
/// overwriting it with an empty value, so there is no leftover row for a
/// future bug to read.
public struct TokenVault: Sendable {

    /// The service every item is filed under. One service, one account per
    /// server, so two households' tokens coexist exactly as they do on
    /// Android, where the key is the normalised address.
    public static let service = "org.codersfarm.domovoi.household-token"

    private let items: any KeychainItems

    public init(items: any KeychainItems = SystemKeychain()) {
        self.items = items
    }

    /// Save the token the server issued for `server`.
    ///
    /// A blank token REMOVES the entry: that is what unpairing means, and it
    /// is what Kotlin's `ServerCredentials.withToken` does with one. A token
    /// a header could not carry is refused here rather than saved, because
    /// the pairing screen is a paste target and
    /// ``isStorableDeviceToken(_:)`` is the server's own rule — Android
    /// refuses it in the screen; refusing it in the vault means no screen can
    /// forget to.
    public func save(token: String?, for server: String) throws {
        let account = Self.account(for: server)
        guard !account.isEmpty else { throw TokenVaultError.noServer }
        let value = token?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if value.isEmpty {
            try items.remove(account: account)
            return
        }
        guard isStorableDeviceToken(value) else { throw TokenVaultError.notStorable }
        guard let data = value.data(using: .utf8) else { throw TokenVaultError.notStorable }
        try items.set(data, account: account)
    }

    /// The token held for `server`, or nil. Nil for every reason at once —
    /// nothing saved, nothing readable, a Keychain that would not answer —
    /// because every one of them means the same thing to the app: this phone
    /// is not paired with that server, which is a screen it has.
    public func token(for server: String) -> String? {
        let account = Self.account(for: server)
        guard !account.isEmpty else { return nil }
        guard let data = try? items.get(account: account) else { return nil }
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        return headerSafeToken(text)
    }

    /// Forget this server's token. Deletes the item; it does not blank it.
    /// Succeeds when there was nothing there, so "forget" is idempotent and
    /// the Settings button can always do what it says.
    public func forget(server: String) throws {
        let account = Self.account(for: server)
        guard !account.isEmpty else { return }
        try items.remove(account: account)
    }

    /// The Keychain account for a server: the same normalised address every
    /// other per-server answer is keyed by, so the token, the refusal and the
    /// wait are all filed under one spelling.
    static func account(for server: String) -> String {
        ServerCredentials.normalize(server)
    }
}

public enum TokenVaultError: Error, Equatable, Sendable {
    /// There is no server to file a token under.
    case noServer
    /// Not a value the server would mint or a header could carry
    /// (``isStorableDeviceToken(_:)``).
    case notStorable
    /// The Keychain said no. Carries its `OSStatus` so a report can name it;
    /// `-34018` (`errSecMissingEntitlement`) is the one a Mac test run sees.
    case keychain(OSStatus)
}

/// The `SecItem` calls, behind a seam.
///
/// Not an abstraction for its own sake: the Keychain cannot be reached from
/// `swift test` on a Mac (see ``TokenVault``), and the logic above it — the
/// blank-means-forget rule, the storable check, the per-server keying — is
/// most of what there is to get wrong.
public protocol KeychainItems: Sendable {
    /// Add it, or replace what is there. Never leaves the account empty on
    /// the way.
    func set(_ secret: Data, account: String) throws
    /// Nil when there is no such item.
    func get(account: String) throws -> Data?
    /// Removing what is not there is not an error.
    func remove(account: String) throws
}

/// The real Keychain.
public struct SystemKeychain: KeychainItems {
    private let service: String

    public init(service: String = TokenVault.service) {
        self.service = service
    }

    private func query(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            // Ask for the data-protection keychain explicitly. On iOS this is
            // the only keychain there is and the key is ignored; on macOS,
            // without it, the item would go to the old file-based keychain
            // where `kSecAttrAccessible` means nothing.
            kSecUseDataProtectionKeychain as String: true,
        ]
    }

    public func set(_ secret: Data, account: String) throws {
        var attributes = query(account: account)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        attributes[kSecAttrSynchronizable as String] = false
        attributes[kSecValueData as String] = secret
        let added = SecItemAdd(attributes as CFDictionary, nil)
        if added == errSecSuccess { return }
        guard added == errSecDuplicateItem else { throw TokenVaultError.keychain(added) }
        // Replace the value in place rather than delete-then-add: a process
        // killed between a delete and an add would leave the phone unpaired
        // for no reason.
        //
        // The accessibility goes in the update as well as in the add. An
        // update that sent only `kSecValueData` would leave an existing item
        // on whatever `kSecAttrAccessible` it was created with — which, for an
        // item this app wrote, is this same value, so nothing in the shipping
        // app can reach it; it is in here because "a token written over an
        // older item keeps a weaker protection class" is not a thing to leave
        // depending on there being exactly one writer for ever.
        let updated = SecItemUpdate(
            query(account: account) as CFDictionary,
            [
                kSecValueData as String: secret,
                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            ] as CFDictionary
        )
        guard updated == errSecSuccess else { throw TokenVaultError.keychain(updated) }
    }

    public func get(account: String) throws -> Data? {
        var attributes = query(account: account)
        // `kSecAttrSynchronizable` is part of an item's primary key, and a
        // query that omits it matches NON-synchronizable items only. This app
        // writes `false`, so the shipping app always finds its own item — but
        // a read that cannot see an iCloud-keychain item would read as "not
        // paired" and send the person back to the pairing screen with a token
        // sitting right there, so ask for either kind and let the one that
        // exists answer.
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
        // Synchronizable-any for the same reason `get` asks for it, and it
        // matters more here: "forget this server" that left a matching item
        // behind would be a credential the person believes they deleted.
        var attributes = query(account: account)
        attributes[kSecAttrSynchronizable as String] = kSecAttrSynchronizableAny
        let status = SecItemDelete(attributes as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw TokenVaultError.keychain(status)
        }
    }
}

/// A Keychain that is a dictionary. For the tests, and for nothing else.
public final class InMemoryKeychain: KeychainItems {
    private let items = Mutex<[String: Data]>([:])

    public init() {}

    /// What the real Keychain cannot be asked: how many items are in there.
    /// A delete that overwrote with empty instead of removing would show up
    /// here and nowhere else.
    public var accounts: Set<String> { items.withLock { Set($0.keys) } }

    public func set(_ secret: Data, account: String) throws {
        items.withLock { $0[account] = secret }
    }

    public func get(account: String) throws -> Data? {
        items.withLock { $0[account] }
    }

    public func remove(account: String) throws {
        items.withLock { $0[account] = nil }
    }
}
