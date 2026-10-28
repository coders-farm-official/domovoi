import CryptoKit
import Foundation
import XCTest
@testable import DomovoiKit

/// The phone's Ed25519 verifier is pinned to RFC 8032 — the same three
/// vectors `android/.../net/ServerIdentityTest.kt` pins the Kotlin
/// implementation to, and `domovoi/tests/test_server_identity.py` pins the
/// core's signer to. If CryptoKit ever disagreed with Android here, a phone
/// would refuse the server its sibling trusts, so this is the test that keeps
/// the two apps honest with each other.
final class Ed25519VerifyTests: XCTestCase {

    /// RFC 8032 §7.1: (private seed, public key, message, signature).
    private let vectors: [(seed: String, publicKey: String, message: String, signature: String)] = [
        (
            seed: "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
            publicKey: "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
            message: "",
            signature: "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
                + "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
        (
            seed: "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
            publicKey: "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
            message: "72",
            signature: "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08"
                + "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
        (
            seed: "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
            publicKey: "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
            message: "af82",
            signature: "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18"
                + "ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
    ]

    func testTheVerifierMatchesRfc8032() throws {
        for vector in vectors {
            XCTAssertTrue(
                Ed25519Verify.verify(
                    publicKey: try hex(vector.publicKey),
                    message: try hex(vector.message),
                    signature: try hex(vector.signature)
                ),
                vector.publicKey
            )
        }
    }

    /// CryptoKit derives the same public key from the same seed as the core's
    /// signer and Android's test-only signer do, so a fake server in a later
    /// gate test can sign a fresh challenge the way the core does.
    ///
    /// Signature BYTES are deliberately not compared: RFC 8032 signing is
    /// deterministic, but Apple does not document its implementation as such,
    /// so the test asks only that what CryptoKit signs verifies.
    /// TODO(verify-at-mac): print `signature(for:)` for vector 2 and, if it
    /// equals the RFC's bytes, tighten this to an equality assertion.
    func testTheSeedsGiveTheRfcPublicKeysAndSignaturesThatVerify() throws {
        for vector in vectors {
            let key = try Curve25519.Signing.PrivateKey(rawRepresentation: try hex(vector.seed))
            XCTAssertEqual(hexString(key.publicKey.rawRepresentation), vector.publicKey)
            let message = try hex(vector.message)
            let signature = try key.signature(for: message)
            XCTAssertEqual(signature.count, Ed25519Verify.signatureSize)
            XCTAssertTrue(
                Ed25519Verify.verify(
                    publicKey: key.publicKey.rawRepresentation,
                    message: message,
                    signature: signature
                )
            )
        }
    }

    func testASignatureIsRefusedForAnotherMessageAnotherKeyOrWhenBent() throws {
        let vector = vectors[1]
        let signature = try hex(vector.signature)
        XCTAssertFalse(
            Ed25519Verify.verify(publicKey: try hex(vector.publicKey), message: try hex("73"), signature: signature)
        )
        XCTAssertFalse(
            Ed25519Verify.verify(publicKey: try hex(vectors[0].publicKey), message: try hex("72"), signature: signature)
        )
        var bent = signature
        bent[10] ^= 1
        XCTAssertFalse(
            Ed25519Verify.verify(publicKey: try hex(vector.publicKey), message: try hex("72"), signature: bent)
        )
    }

    /// Malformed input is an answer, not an exception — a phone that threw
    /// here would crash on a server that answers rubbish.
    ///
    /// TODO(verify-at-mac): the all-zero key and the all-0xff signature are
    /// where CryptoKit's documented behaviour runs out (see
    /// `Ed25519Verify`). If one of these fails on the Mac, read what Apple
    /// actually does and decide whether the Kotlin's explicit `s >= L` check
    /// has to be added back in Swift.
    func testMalformedInputIsAnAnswerNotAnException() throws {
        let message = Data("m".utf8)
        XCTAssertFalse(Ed25519Verify.verify(publicKey: Data(), message: message, signature: Data()))
        XCTAssertFalse(
            Ed25519Verify.verify(
                publicKey: Data(repeating: 0, count: 32),
                message: message,
                signature: Data(repeating: 0xff, count: 64)
            )
        )
        XCTAssertFalse(
            Ed25519Verify.verify(publicKey: Data(repeating: 0, count: 31), message: message, signature: Data(count: 64))
        )
        XCTAssertFalse(
            Ed25519Verify.verify(publicKey: try hex(vectors[0].publicKey), message: message, signature: Data(count: 63))
        )
    }

    // ---- helpers -----------------------------------------------------------

    private func hex(_ text: String) throws -> Data {
        var bytes = [UInt8]()
        var high: UInt8?
        for character in text {
            guard let value = character.hexDigitValue, value < 16 else { throw Failure.notHex(text) }
            let digit = UInt8(value)
            if let first = high {
                bytes.append(first << 4 | digit)
                high = nil
            } else {
                high = digit
            }
        }
        guard high == nil else { throw Failure.notHex(text) }
        return Data(bytes)
    }

    private func hexString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    private enum Failure: Error { case notHex(String) }
}
