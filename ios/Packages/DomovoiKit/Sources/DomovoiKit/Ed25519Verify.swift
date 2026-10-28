import CryptoKit
import Foundation

/// Ed25519 (RFC 8032) signature VERIFICATION, which is all the phone ever
/// needs: it checks the server's signed challenge (`IdentityGate`) and the
/// room-mode enrolment answers, and it never signs anything.
///
/// Android carries its own 165-line implementation over `BigInteger`, because
/// its providers only grew Ed25519 in recent releases and the app runs from
/// API 26. iOS has had CryptoKit's `Curve25519.Signing` since iOS 13, so this
/// port is a thin shell over Apple's code — and the test beside it keeps the
/// RFC 8032 vectors Android's `ServerIdentityTest` uses, so the Swift app,
/// the Kotlin app and the core's own signer (`domovoi/_ed25519.py`) are all
/// pinned to the same answers.
///
/// Differences from the Kotlin worth knowing:
///  - Apple documents neither whether its verifier is cofactorless (RFC 8032
///    strict) or cofactored, nor whether it refuses a non-canonical `s`,
///    which the Kotlin does explicitly (`if (s >= L) return false`). The two
///    agree on every vector and on every honest signature; only deliberately
///    malformed input could differ. TODO(verify-at-mac): run the
///    malformed-input test and record what CryptoKit does.
///  - CryptoKit refuses some keys when the value is constructed (a wrong
///    length, and possibly a non-canonical point) by throwing. A throw is
///    folded into the same answer, so a malformed key, a malformed signature
///    and a wrong signature are all simply "no", exactly as on Android.
public enum Ed25519Verify {
    public static let keySize = 32
    public static let signatureSize = 64

    /// Whether `signature` is `publicKey`'s signature over `message`. Never
    /// throws: the caller gets one answer, and it is no.
    public static func verify(publicKey: Data, message: Data, signature: Data) -> Bool {
        guard publicKey.count == keySize, signature.count == signatureSize else { return false }
        guard let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey) else { return false }
        return key.isValidSignature(signature, for: message)
    }
}
