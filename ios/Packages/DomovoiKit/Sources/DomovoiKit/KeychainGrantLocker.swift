import Foundation
import LocalAuthentication
import Security

/// Today's admin grant, kept in the Keychain behind Face ID or Touch ID.
///
/// The iPhone's `BiometricGrantLocker` (`android/.../data/BiometricGrantLocker.kt`).
/// Android needs a Keystore key and AES-GCM because its preference store is a
/// plain file; the Keychain is a secret held by the system already, so here
/// the grant is the item's value and the access control is the lock:
///
///  * `kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly`: only while the phone
///    has a passcode (removing it deletes the item), never in a backup, never
///    on another device.
///  * `.biometryCurrentSet`: reading it needs Face ID or Touch ID, **with the
///    fingers or face enrolled when it was kept**. Enrolling a new one makes
///    the item unreadable for good, which is Android's
///    `setInvalidatedByBiometricEnrollment(true)`: the phone then drops the
///    grant and asks for the password.
///  * Never synchronised: iCloud Keychain would put a day of admin on every
///    Apple device signed into the account.
///
/// What ``seal(_:)`` hands back is the item's account name — a fresh random
/// one per grant — and not the grant: the shelf keeps that handle beside the
/// server and identity it is bound to, and the grant stays here.
///
/// **Both halves ask for the face or finger.** Keeping asks too, which the
/// Keychain alone would not (adding an item needs no authentication): it is
/// the person saying yes to keeping admin, as Android's seal prompt is, and a
/// cancelled prompt keeps nothing (the grant is then revoked). Opening asks
/// first through `LAContext`, then reads the item with that same evaluated
/// context, so the person sees one prompt and a read that fails after a
/// passed check can only mean the item is gone.
///
/// Not exercised by `swift test`: the Mac's test process has neither the
/// data-protection keychain nor Face ID (``TokenVault`` says why), and the
/// tests never touch the real Keychain or `LAContext`. The status mapping is
/// a pure function and is tested.
public struct KeychainGrantLocker: GrantLocker {

    /// The service every grant item is filed under.
    public static let service = "org.codersfarm.domovoi.admin-grant"

    /// What the prompt says when admin is resumed.
    public static let openReason = "Use Face ID to go on as admin"

    /// What the prompt says when admin is kept.
    public static let sealReason = "Use Face ID to keep admin on this phone today"

    public init() {}

    public var available: Bool {
        var error: NSError?
        return LAContext().canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
    }

    public func seal(_ grant: String) async throws -> GrantSealed {
        let context = LAContext()
        try await Self.ask(context, reason: Self.sealReason)
        var problem: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(
            nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, .biometryCurrentSet, &problem
        ) else {
            throw GrantLockerError.unavailable(AdminSession.noBiometrics)
        }
        // One grant at a time: whatever was kept before goes first.
        destroy()
        let account = UUID().uuidString
        let attributes: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: account,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
            kSecAttrAccessControl as String: access,
            kSecValueData as String: Data(grant.utf8),
        ]
        let status = SecItemAdd(attributes as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw GrantLockerError.unavailable(AdminSession.noBiometrics)
        }
        return GrantSealed(bytes: Data(account.utf8))
    }

    public func open(_ sealed: GrantSealed) async throws -> String {
        guard let account = String(data: sealed.bytes, encoding: .utf8), !account.isEmpty else {
            throw GrantLockerError.keyGone(GrantLockerError.keyGoneMessage)
        }
        let context = LAContext()
        try await Self.ask(context, reason: Self.openReason)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: account,
            kSecUseDataProtectionKeychain as String: true,
            kSecUseAuthenticationContext as String: context,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var found: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &found)
        if let failure = Self.failure(status) { throw failure }
        guard let data = found as? Data, let grant = String(data: data, encoding: .utf8) else {
            throw GrantLockerError.keyGone(GrantLockerError.keyGoneMessage)
        }
        return grant
    }

    public func destroy() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecUseDataProtectionKeychain as String: true,
        ]
        SecItemDelete(query as CFDictionary)
    }

    /// The person's face or finger, now. A cancel, a fallback tap or a
    /// failed match is ``GrantLockerError/cancelled(_:)`` (nothing changed);
    /// biometrics that are gone or locked out are
    /// ``GrantLockerError/unavailable(_:)``.
    private static func ask(_ context: LAContext, reason: String) async throws {
        context.localizedReason = reason
        // Face ID only, no passcode fallback: the item needs the biometrics
        // themselves, so a passcode would pass this check and fail the read.
        context.localizedFallbackTitle = ""
        do {
            _ = try await context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: reason)
        } catch let error as LAError {
            switch error.code {
            case .biometryNotAvailable, .biometryNotEnrolled, .biometryLockout, .passcodeNotSet:
                throw GrantLockerError.unavailable(AdminSession.noBiometrics)
            default:
                throw GrantLockerError.cancelled("the Face ID or Touch ID check didn't finish")
            }
        } catch {
            throw GrantLockerError.cancelled("the Face ID or Touch ID check didn't finish")
        }
    }

    /// What a Keychain read's status means, after a check that passed. Nil
    /// for success.
    ///
    ///  * `errSecUserCanceled`, `errSecInteractionNotAllowed`: the read could
    ///    not ask (the app left the screen): nothing changed.
    ///  * `errSecItemNotFound`, `errSecAuthFailed`, and anything else: the
    ///    item cannot be read with the biometrics enrolled now — new ones
    ///    were enrolled (`.biometryCurrentSet`), the passcode was removed, or
    ///    it was deleted. It never will be, so the grant is dropped.
    static func failure(_ status: OSStatus) -> GrantLockerError? {
        switch status {
        case errSecSuccess:
            return nil
        case errSecUserCanceled, errSecInteractionNotAllowed:
            return .cancelled("the Face ID or Touch ID check didn't finish")
        default:
            return .keyGone(GrantLockerError.keyGoneMessage)
        }
    }
}
