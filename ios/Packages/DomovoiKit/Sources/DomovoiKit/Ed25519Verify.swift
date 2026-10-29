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
/// **Measured against CryptoKit on 2026-10-28** (macOS 26.4.1, Xcode 26.4,
/// M1 Pro) rather than assumed, because what Apple documents and what its
/// verifier does are not the same list. Every input below that the Kotlin
/// refuses, CryptoKit also refuses, so the two apps agree:
///
///  - A non-canonical scalar `s` (at or above the group order L) is refused
///    by CryptoKit **itself**: vector 2's signature with `s + L` substituted
///    does not verify. ``isCanonicalScalar(_:)`` therefore duplicates a check
///    Apple already makes. It stays anyway — Apple guarantees it nowhere, it
///    costs one comparison, and it makes the parity with `Ed25519.kt:55`
///    (`if (s >= L) return false`) visible instead of incidental. The review
///    that asked for it believed iOS was accepting what Android refuses; it
///    was not.
///  - A non-canonical POINT encoding (`y` in [2^255-19, 2^255), which the
///    Kotlin's `recoverX` rejects outright) is **constructed** without
///    throwing but does not verify, as a key or as a signature's `R`. Same
///    answer as Android by a different route, so no explicit check is needed.
///  - An all-zero key likewise constructs and then verifies nothing.
///  - A key of 0, 31, 33 or 64 bytes throws; a signature of 0, 63 or 65 bytes
///    is refused. A throw is folded into the same answer, so a malformed key,
///    a malformed signature and a wrong signature are all simply "no",
///    exactly as on Android.
///  - **Signing is randomised, not RFC 8032 deterministic**: two signatures
///    over one message with one key differ, and neither equals the vector's
///    published bytes. Only verification is pinned, so no test here may
///    assert signature bytes — it would fail at random. (The app never signs;
///    the test's signer exists only to prove the key pair round-trips.)
///  - Still open: whether the verifier is cofactorless (RFC 8032 strict) or
///    cofactored. The two agree on every vector and every honest signature;
///    only a small-order `R` tells them apart, and nothing we do produces
///    one. Left unmeasured on purpose.
public enum Ed25519Verify {
    public static let keySize = 32
    public static let signatureSize = 64

    /// Whether `signature` is `publicKey`'s signature over `message`. Never
    /// throws: the caller gets one answer, and it is no.
    public static func verify(publicKey: Data, message: Data, signature: Data) -> Bool {
        guard publicKey.count == keySize, signature.count == signatureSize else { return false }
        guard isCanonicalScalar(signature) else { return false }
        guard let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey) else { return false }
        return key.isValidSignature(signature, for: message)
    }

    /// L, the base point's subgroup order — 2^252 +
    /// 27742317777372353535851937790883648493 — as 32 bytes, **big-endian**,
    /// so a signature's little-endian `s` can be compared against it one
    /// byte at a time from the most significant end. Kept as bytes on
    /// purpose: no big-integer type is needed for a magnitude comparison,
    /// and Swift has none in the standard library.
    static let orderBigEndian: [UInt8] = [
        0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x14, 0xde, 0xf9, 0xde, 0xa2, 0xf7, 0x9c, 0xd6,
        0x58, 0x12, 0x63, 0x1a, 0x5c, 0xf5, 0xd3, 0xed,
    ]

    /// Whether the signature's scalar is canonical: `s < L`, where `s` is the
    /// signature's second 32 bytes read little-endian. The Kotlin's
    /// `if (s >= L) return false`, written without a big integer.
    ///
    /// A signature with `s >= L` is a valid signature with L (or a multiple)
    /// added to its scalar — a second encoding of the same signature, which
    /// is why this is malleability and not forgery. Android refuses it, so
    /// iOS refuses it.
    static func isCanonicalScalar(_ signature: Data) -> Bool {
        guard signature.count == signatureSize else { return false }
        // `Data` slices keep their parent's indices; copying to an Array
        // means `bytes[i]` is the i-th byte whatever the caller handed in.
        let bytes = Array(signature)
        for i in 0..<keySize {
            // s is little-endian, so its most significant byte is last.
            let scalarByte = bytes[signatureSize - 1 - i]
            let orderByte = orderBigEndian[i]
            if scalarByte < orderByte { return true }   // s < L
            if scalarByte > orderByte { return false }  // s > L
        }
        return false                                    // s == L, not below it
    }
}
