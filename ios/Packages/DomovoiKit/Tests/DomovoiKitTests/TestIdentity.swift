import CryptoKit
import Foundation
@testable import DomovoiKit

/// A server's identity, with the private half — so a test can **be** a
/// Domovoi server rather than replay one recording.
///
/// The counterpart of `FakeDomovoi.healthBody` in
/// `android/.../net/IdentityGateTest.kt`, which signs its challenges with
/// `Ed25519.sign`. `DomovoiKit` ships no signer (the phone never signs), so
/// the signing lives here, in the test target, over CryptoKit.
///
/// `ServerIdentityTests.testTheTestOnlySignerProducesAnswersTheCheckAccepts`
/// is what keeps this honest: a fake whose answers the real check rejected
/// would turn every gate test into a test of this file.
struct TestIdentity: Sendable {

    /// The seeds the RFC 8032 vectors publish, which is what Android's gate
    /// tests use, so "the real server" and "the rogue" are the same two keys
    /// on both phones.
    static let realSeed = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
    static let rogueSeed = "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"

    static let real = try! TestIdentity(seed: realSeed)
    static let rogue = try! TestIdentity(seed: rogueSeed)

    private let key: Curve25519.Signing.PrivateKey

    init(seed: String) throws {
        var bytes = Data()
        var index = seed.startIndex
        while index < seed.endIndex {
            let next = seed.index(index, offsetBy: 2)
            bytes.append(UInt8(seed[index..<next], radix: 16)!)
            index = next
        }
        key = try Curve25519.Signing.PrivateKey(rawRepresentation: bytes)
    }

    var publicKeyBytes: Data { key.publicKey.rawRepresentation }
    var publicKey: String { publicKeyBytes.base64EncodedString() }
    var fingerprint: String { ServerIdentity.fingerprintOf(publicKeyBytes) }
    var pin: ServerIdentity.Pin { ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint) }

    /// A `/api/health` answer. With `challenge` nil it is the ADVERTISED
    /// block the picker's open read sees (no challenge, no signature); with
    /// one it is the signed proof. The shape is `FakeDomovoi.healthBody`'s,
    /// field for field.
    func healthBody(challenge: String?) throws -> String {
        var identity = #"{"algorithm":"ed25519","public_key":"\#(publicKey)","fingerprint":"\#(fingerprint)""#
        if let challenge {
            let signature = try key.signature(for: ServerIdentity.healthMessage(challenge)).base64EncodedString()
            identity += #","challenge":"\#(challenge)","signature":"\#(signature)""#
        }
        identity += "}"
        return #"{"status":"ok","stt":"ok","identity":\#(identity)}"#
    }

    /// A server from before identity: no block at all.
    static let noIdentityBody = #"{"status":"ok","stt":"ok"}"#

    /// A web backend that answered while its core did not
    /// (`web/backend/main.py`).
    static let coreDownBody = #"{"status":"degraded","db_reachable":true,"domovoi_reachable":false,"stt":null,"identity":null}"#
}
