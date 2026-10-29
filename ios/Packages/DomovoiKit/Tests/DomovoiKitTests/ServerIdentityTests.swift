import CryptoKit
import Foundation
import XCTest
@testable import DomovoiKit

/// The identity check accepts exactly what the core's `health_answer`
/// produces — and nothing that is not that (security round 3, A6-03).
///
/// The fixture below is **the same bytes** `android/.../net/ServerIdentityTest.kt`
/// uses: one `/v1/health?challenge=…` answer generated with the core's own
/// signer for the RFC 8032 vector-1 seed. So the Swift check, the Kotlin
/// check and the Python signer are pinned to one another, and a change on any
/// one side fails here.
final class ServerIdentityTests: XCTestCase {

    private let challenge = "0123456789abcdef0123456789abcdef"
    private let fingerprint = "SHA256:If4x36FUomFia/hUBG/SJxt77UtqvkWqWId+9H+XIbk"
    private let publicKey = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="

    private var coreAnswer: String {
        """
        {"status":"ok","bot_name":"domovoi","stt":"ok","identity":{"algorithm":"ed25519",\
        "fingerprint":"\(fingerprint)","public_key":"\(publicKey)","challenge":"\(challenge)",\
        "signature":"KrwTHDAWdBmX8oh0dVDsMqBdNPKS6ZXw5ttWTGNEEkXKH8cB+ht9O3UbpXCYBR6tJ0x5UH+78V2jP29QP4bhDA=="}}
        """
    }

    // ---- what the core answers ---------------------------------------------

    func testTheCoresOwnAnswerVerifiesAndPinsItsKey() {
        let pin = ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint)
        XCTAssertEqual(.verified(pin: pin), ServerIdentity.check(healthBody: coreAnswer, challenge: challenge, pinned: nil))
        // ...and against the pin it just made.
        XCTAssertEqual(.verified(pin: pin), ServerIdentity.check(healthBody: coreAnswer, challenge: challenge, pinned: pin))
    }

    func testTheFingerprintIsTheCoresShape() throws {
        let key = try XCTUnwrap(Data(base64Encoded: publicKey))
        XCTAssertEqual(fingerprint, ServerIdentity.fingerprintOf(key))
        // The same key the RFC 8032 vector 1 publishes, so this fixture is
        // tied to the vectors `Ed25519VerifyTests` pins.
        XCTAssertEqual("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", hexString(key))
        // Unpadded, as `Base64.getEncoder().withoutPadding()` writes it: the
        // digest is 32 bytes, so a padded encoding would end in one `=`.
        XCTAssertFalse(fingerprint.hasSuffix("="))
    }

    func testTheSignedMessageIsTheCoresBytes() {
        XCTAssertEqual(Data("domovoi-health-v1\n\(challenge)".utf8), ServerIdentity.healthMessage(challenge))
    }

    // ---- a replay, and a rewritten replay ----------------------------------

    func testAReplayedAnswerSignsSomebodyElsesChallenge() {
        let ours = "ffffffffffffffffffffffffffffffff"
        XCTAssertEqual(
            .invalid(reason: "our challenge was not echoed"),
            ServerIdentity.check(healthBody: coreAnswer, challenge: ours, pinned: nil)
        )
        // An attacker who rewrites the echoed challenge to ours still has the
        // wrong signature: the recording cannot be made to fit.
        XCTAssertEqual(
            .invalid(reason: "the signature does not verify"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: challenge, with: ours), challenge: ours, pinned: nil)
        )
    }

    // ---- each failure step, in the order the check makes them --------------

    func testStep1ABodyThatIsNotAJsonObjectProvesNothing() {
        for body in ["not json", "", "[]", "\"a string\"", "123", "null", "{", "{\"a\":"] {
            XCTAssertEqual(
                .invalid(reason: "the health answer is not a JSON object"),
                ServerIdentity.check(healthBody: body, challenge: challenge, pinned: nil),
                "body: \(body)"
            )
        }
        XCTAssertEqual(
            .invalid(reason: "the health answer is not a JSON object"),
            ServerIdentity.check(healthBody: nil, challenge: challenge, pinned: nil)
        )
    }

    func testStep2NoIdentityBlockIsEitherAnOldServerOrACoreThatIsDown() {
        // web/backend/main.py leaves identity out whenever /v1/health did not
        // answer in time and says so in domovoi_reachable: a restart, an
        // update, a busy box. NOT "this server has no identity".
        XCTAssertEqual(
            .coreNotAnswering,
            ServerIdentity.check(
                healthBody: #"{"status":"degraded","db_reachable":true,"domovoi_reachable":false,"stt":null,"identity":null}"#,
                challenge: challenge, pinned: nil
            )
        )
        // A core that answered without an identity block, or a web backend
        // too old to say either way, is a server from before identity.
        XCTAssertEqual(
            .noIdentity,
            ServerIdentity.check(healthBody: #"{"status":"ok","domovoi_reachable":true}"#, challenge: challenge, pinned: nil)
        )
        XCTAssertEqual(
            .noIdentity,
            ServerIdentity.check(healthBody: #"{"status":"ok"}"#, challenge: challenge, pinned: nil)
        )
        // `identity` present but not an object is no identity block either.
        XCTAssertEqual(
            .noIdentity,
            ServerIdentity.check(healthBody: #"{"identity":"SHA256:abc"}"#, challenge: challenge, pinned: nil)
        )
    }

    func testStep3AnAlgorithmThisPhoneCannotVerifyIsRefused() {
        XCTAssertEqual(
            .invalid(reason: "unknown identity algorithm"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: "ed25519", with: "rsa"), challenge: challenge, pinned: nil)
        )
        // Spelling and case are the server's to get right: the core writes
        // exactly "ed25519", and Kotlin's `!=` is case-sensitive too.
        XCTAssertEqual(
            .invalid(reason: "unknown identity algorithm"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: "ed25519", with: "Ed25519"), challenge: challenge, pinned: nil)
        )
        XCTAssertEqual(
            .invalid(reason: "unknown identity algorithm"),
            ServerIdentity.check(healthBody: #"{"identity":{"public_key":"x"}}"#, challenge: challenge, pinned: nil)
        )
    }

    func testStep4APublicKeyMustBeBase64AndThirtyTwoBytes() {
        for key in ["AAAA", "", "not base64!!", String(repeating: "A", count: 300)] {
            XCTAssertEqual(
                .invalid(reason: "malformed public key"),
                ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: publicKey, with: key), challenge: challenge, pinned: nil),
                "key: \(key.prefix(12))"
            )
        }
        // A 31-byte and a 33-byte key, both perfectly good base64.
        XCTAssertEqual(
            .invalid(reason: "malformed public key"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: publicKey, with: Data(repeating: 0, count: 31).base64EncodedString()), challenge: challenge, pinned: nil)
        )
        XCTAssertEqual(
            .invalid(reason: "malformed public key"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: publicKey, with: Data(repeating: 0, count: 33).base64EncodedString()), challenge: challenge, pinned: nil)
        )
        // No `public_key` field at all is its own refusal, before the decode.
        XCTAssertEqual(
            .invalid(reason: "no public key"),
            ServerIdentity.check(healthBody: #"{"identity":{"algorithm":"ed25519"}}"#, challenge: challenge, pinned: nil)
        )
    }

    func testStep5AFingerprintThatIsNotTheKeysIsRefused() {
        XCTAssertEqual(
            .invalid(reason: "the fingerprint is not the key's"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: fingerprint, with: "SHA256:nope"), challenge: challenge, pinned: nil)
        )
        // The real server's fingerprint with somebody else's key behind it:
        // the pair has to agree, which is what makes the dashboard's string
        // worth comparing by eye.
        let otherKey = Data(repeating: 7, count: 32).base64EncodedString()
        XCTAssertEqual(
            .invalid(reason: "the fingerprint is not the key's"),
            ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: publicKey, with: otherKey), challenge: challenge, pinned: nil)
        )
        // The prefix is part of it: a bare base64 digest is not the shape.
        XCTAssertEqual(
            .invalid(reason: "the fingerprint is not the key's"),
            ServerIdentity.check(
                healthBody: coreAnswer.replacingOccurrences(of: fingerprint, with: String(fingerprint.dropFirst("SHA256:".count))),
                challenge: challenge, pinned: nil
            )
        )
    }

    func testStep7ASignatureMustBeBase64AndSixtyFourBytes() throws {
        let signature = try XCTUnwrap(signatureField(of: coreAnswer))
        for bad in ["AAAA", "", "not base64!!", Data(repeating: 0, count: 63).base64EncodedString(), Data(repeating: 0, count: 65).base64EncodedString()] {
            XCTAssertEqual(
                .invalid(reason: "malformed signature"),
                ServerIdentity.check(healthBody: coreAnswer.replacingOccurrences(of: signature, with: bad), challenge: challenge, pinned: nil),
                "signature: \(bad.prefix(12))"
            )
        }
        // No signature field at all: the same refusal, as in the Kotlin.
        let withoutSignature = coreAnswer.replacingOccurrences(of: #","signature":"\#(signature)""#, with: "")
        XCTAssertEqual(
            .invalid(reason: "malformed signature"),
            ServerIdentity.check(healthBody: withoutSignature, challenge: challenge, pinned: nil)
        )
    }

    func testStep8ABentSignatureDoesNotVerify() throws {
        let signature = try XCTUnwrap(signatureField(of: coreAnswer))
        var bytes = try XCTUnwrap(Data(base64Encoded: signature))
        bytes[10] ^= 1
        XCTAssertEqual(
            .invalid(reason: "the signature does not verify"),
            ServerIdentity.check(
                healthBody: coreAnswer.replacingOccurrences(of: signature, with: bytes.base64EncodedString()),
                challenge: challenge, pinned: nil
            )
        )
    }

    func testStep9ADifferentKeyThanThePinnedOneIsAMismatchEvenWhenItProvesItself() {
        // A well-formed proof of a key this phone did not pin. The verdict
        // carries the key it proved, because that is what the "it was
        // reinstalled, trust it again" answer pins.
        let pinned = ServerIdentity.Pin(publicKey: "c29tZWJvZHkgZWxzZXMga2V5IGhlcmUgLS0tLS0tLS0=", fingerprint: "SHA256:someoneelse")
        XCTAssertEqual(
            .mismatch(
                expected: "SHA256:someoneelse",
                seen: fingerprint,
                seenPin: ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint)
            ),
            ServerIdentity.check(healthBody: coreAnswer, challenge: challenge, pinned: pinned)
        )
    }

    func testAMismatchIsReachedOnlyByAPROVENOtherKey() throws {
        // The step order matters here and it is easy to get backwards: a
        // rogue that claims the real server's fingerprint but cannot sign
        // must read as `invalid`, not as `mismatch`. `mismatch` is the
        // verdict that offers the person a "trust it again" button, and a
        // button that pins an unproven key would be the whole layer undone.
        let pinned = ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint)
        let signature = try XCTUnwrap(signatureField(of: coreAnswer))
        var bytes = try XCTUnwrap(Data(base64Encoded: signature))
        bytes[3] ^= 0xff
        let rogue = coreAnswer
            .replacingOccurrences(of: publicKey, with: Data(repeating: 9, count: 32).base64EncodedString())
            .replacingOccurrences(of: signature, with: bytes.base64EncodedString())
        if case .mismatch = ServerIdentity.check(healthBody: rogue, challenge: challenge, pinned: pinned) {
            XCTFail("an unproven key must not reach the mismatch verdict")
        }
    }

    func testAPinnedServerThatOffersNoIdentityIsNoIdentityNotAMismatch() {
        // The gate turns this into `unproven` when something is pinned; the
        // pure check's job is only to say the block was absent.
        let pinned = ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint)
        XCTAssertEqual(
            .noIdentity,
            ServerIdentity.check(healthBody: #"{"status":"ok"}"#, challenge: challenge, pinned: pinned)
        )
    }

    // ---- the advertised (unproven) identity --------------------------------

    func testAnAdvertisedIdentityIsTakenOnlyWhenItHoldsTogether() {
        let pin = ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint)
        XCTAssertEqual(pin, ServerIdentity.advertised(algorithm: "ed25519", publicKey: publicKey, fingerprint: fingerprint))
        XCTAssertNil(ServerIdentity.advertised(algorithm: "rsa", publicKey: publicKey, fingerprint: fingerprint))
        XCTAssertNil(ServerIdentity.advertised(algorithm: "ed25519", publicKey: publicKey, fingerprint: "SHA256:nope"))
        XCTAssertNil(ServerIdentity.advertised(algorithm: "ed25519", publicKey: "AAAA", fingerprint: fingerprint))
        XCTAssertNil(ServerIdentity.advertised(algorithm: "ed25519", publicKey: nil, fingerprint: fingerprint))
        XCTAssertNil(ServerIdentity.advertised(algorithm: "ed25519", publicKey: publicKey, fingerprint: nil))
    }

    func testAnAdvertisedIdentityIsReadOutOfABodyTheSameWay() {
        let block = #"{"algorithm":"ed25519","public_key":"\#(publicKey)","fingerprint":"\#(fingerprint)"}"#
        XCTAssertEqual(
            ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint),
            ServerIdentity.advertisedIdentity(healthBody: #"{"identity":\#(block)}"#)
        )
        XCTAssertNil(ServerIdentity.advertisedIdentity(healthBody: #"{"status":"ok"}"#), "an older web backend")
        XCTAssertNil(
            ServerIdentity.advertisedIdentity(healthBody: #"{"identity":{"fingerprint":"SHA256:abc"}}"#),
            "a fingerprint with no key behind it is nothing to pin"
        )
        XCTAssertNil(
            ServerIdentity.advertisedIdentity(healthBody: #"{"identity":{"algorithm":"ed25519","public_key":"\#(publicKey)","fingerprint":"SHA256:abc"}}"#),
            "a fingerprint that is not the key's"
        )
        XCTAssertNil(
            ServerIdentity.advertisedIdentity(healthBody: #"{"identity":{"algorithm":"rsa","public_key":"\#(publicKey)","fingerprint":"\#(fingerprint)"}}"#),
            "an algorithm the phone does not verify"
        )
        XCTAssertNil(ServerIdentity.advertisedIdentity(healthBody: #"{"identity":{"fingerprint":""}}"#))
        XCTAssertNil(ServerIdentity.advertisedIdentity(healthBody: "not json"))
        XCTAssertNil(ServerIdentity.advertisedIdentity(healthBody: nil))
        // The advertised block has no challenge and no signature, and that is
        // not an objection to it: it is the picker's open read.
        XCTAssertNil(ServerIdentity.advertisedIdentity(healthBody: #"{"identity":{"algorithm":"ed25519","public_key":"\#(publicKey)"}}"#))
    }

    // ---- the challenge -----------------------------------------------------

    func testChallengesAreFreshAndTheShapeTheCoreAccepts() {
        var seen = Set<String>()
        for _ in 0..<64 {
            let challenge = ServerIdentity.newChallenge()
            XCTAssertEqual(32, challenge.count)
            XCTAssertTrue(challenge.allSatisfy { $0.isNumber && $0.isASCII || ("a"..."f").contains($0) }, challenge)
            seen.insert(challenge)
        }
        // 128 bits of randomness: sixty-four draws colliding would be a
        // broken generator, not bad luck.
        XCTAssertEqual(64, seen.count)
    }

    // ---- the pin book ------------------------------------------------------

    func testThePinBookSurvivesARoundTripAndACorruptBlobIsNotFatal() {
        let pins = ["http://10.0.0.42:6369": ServerIdentity.Pin(publicKey: publicKey, fingerprint: fingerprint)]
        XCTAssertEqual(pins, ServerIdentity.decodePins(ServerIdentity.encodePins(pins)))
        XCTAssertEqual([:], ServerIdentity.decodePins("{"))
        XCTAssertEqual([:], ServerIdentity.decodePins(nil))
        XCTAssertEqual([:], ServerIdentity.decodePins("[]"))
        XCTAssertEqual([:], ServerIdentity.decodePins(#"{"a":"not a pin"}"#))
    }

    func testThePinBookJsonUsesTheKotlinsFieldNames() {
        // So the two apps' stored blobs have the same STRUCTURE and the same
        // field names: `public_key`, not `publicKey`.
        //
        // Not the same TEXT, and the expectation below is where that shows:
        // `JSONEncoder` escapes a forward slash, so the key reads
        // `http:\/\/x:1` where `kotlinx.serialization` writes `http://x:1`.
        // Both decode to the same book (the second assertion reads the
        // Kotlin's spelling back), and `.withoutEscapingSlashes` is
        // deliberately not set — see `encodePins`.
        let blob = ServerIdentity.encodePins(["http://x:1": ServerIdentity.Pin(publicKey: "k", fingerprint: "f")])
        XCTAssertEqual(#"{"http:\/\/x:1":{"fingerprint":"f","public_key":"k"}}"#, blob)
        XCTAssertEqual(
            ["http://x:1": ServerIdentity.Pin(publicKey: "k", fingerprint: "f")],
            ServerIdentity.decodePins(#"{"http://x:1":{"public_key":"k","fingerprint":"f"}}"#)
        )
    }

    // ---- the JSON readings, which the verdicts rest on ---------------------

    func testADomovoiReachableFlagIsReadAsKotlinReadsIt() {
        // A real JSON boolean, and the string form Kotlin's
        // `toBooleanStrictOrNull` also accepts.
        XCTAssertEqual(false, ServerIdentity.jsonBool(false))
        XCTAssertEqual(true, ServerIdentity.jsonBool(true))
        XCTAssertEqual(false, ServerIdentity.jsonBool("false"))
        XCTAssertEqual(true, ServerIdentity.jsonBool("true"))
        // A NUMBER is not a boolean, however much `NSNumber as? Bool` would
        // like it to be: `0` must read as nil, or `domovoi_reachable: 0`
        // would hold the token on iOS and not on Android.
        XCTAssertNil(ServerIdentity.jsonBool(0))
        XCTAssertNil(ServerIdentity.jsonBool(1))
        XCTAssertNil(ServerIdentity.jsonBool("False"))
        XCTAssertNil(ServerIdentity.jsonBool("yes"))
        XCTAssertNil(ServerIdentity.jsonBool(nil))
        // And through the check itself, which is where it matters.
        XCTAssertEqual(
            .noIdentity,
            ServerIdentity.check(healthBody: #"{"domovoi_reachable":0}"#, challenge: challenge, pinned: nil)
        )
        XCTAssertEqual(
            .coreNotAnswering,
            ServerIdentity.check(healthBody: #"{"domovoi_reachable":"false"}"#, challenge: challenge, pinned: nil)
        )
    }

    func testAFieldThatIsNotAStringIsNotAFieldAndTheVerdictIsTheSameAsAndroids() {
        // Kotlin stringifies a number here ("25519") and this returns nil.
        // Either way the comparison fails and the verdict is identical, which
        // is the claim; these are the cases that back it.
        XCTAssertNil(ServerIdentity.jsonString(25519))
        XCTAssertNil(ServerIdentity.jsonString(true))
        XCTAssertNil(ServerIdentity.jsonString(["a": 1]))
        XCTAssertEqual(
            .invalid(reason: "unknown identity algorithm"),
            ServerIdentity.check(healthBody: #"{"identity":{"algorithm":25519}}"#, challenge: challenge, pinned: nil)
        )
        XCTAssertEqual(
            .invalid(reason: "our challenge was not echoed"),
            ServerIdentity.check(
                healthBody: coreAnswer.replacingOccurrences(of: #""challenge":"\#(challenge)""#, with: #""challenge":12345"#),
                challenge: challenge, pinned: nil
            )
        )
    }

    func testBase64WithoutItsPaddingIsReadAsJavaReadsIt() {
        // Measured, not assumed: Foundation refuses an unpadded encoding.
        let key = Data(repeating: 3, count: 32)
        let padded = key.base64EncodedString()
        XCTAssertTrue(padded.hasSuffix("="), "a 32-byte payload needs one `=`")
        let unpadded = String(padded.dropLast())
        XCTAssertNil(Data(base64Encoded: unpadded), "Foundation wants the padding; Java's decoder does not")
        // So the decoder pads it back, and the two apps read the same key.
        XCTAssertEqual(key, ServerIdentity.decodeBase64(unpadded))
        XCTAssertEqual(key, ServerIdentity.decodeBase64(padded))
        // A length of 4n+1 encodes nothing: one base64 character carries six
        // bits and no byte boundary falls there, so it is invalid to Java's
        // decoder as well and stays a refusal on both phones.
        XCTAssertEqual(1, (padded.count + 1) % 4)
        XCTAssertNil(ServerIdentity.decodeBase64(padded + "A"))
        XCTAssertNil(ServerIdentity.decodeBase64("A"))
        // Dropping only the padding is NOT that case — 43 characters is a
        // legitimate unpadded encoding of 32 bytes, which is the whole point
        // of the two assertions above it.
        XCTAssertEqual(3, unpadded.count % 4)
        XCTAssertNil(ServerIdentity.decodeBase64(String(repeating: "A", count: 257)), "the Kotlin's 256-character ceiling")
        XCTAssertNotNil(ServerIdentity.decodeBase64(String(repeating: "A", count: 256)))
        // And an unpadded key in a real answer verifies, rather than reading
        // as "malformed public key" — this is the case the padding is for.
        let unpaddedAnswer = coreAnswer.replacingOccurrences(of: publicKey, with: String(publicKey.dropLast()))
        guard case .verified(let pin) = ServerIdentity.check(healthBody: unpaddedAnswer, challenge: challenge, pinned: nil) else {
            return XCTFail("an unpadded public key should still verify")
        }
        XCTAssertEqual(fingerprint, pin.fingerprint)
        // ...and the pin keeps the server's own spelling, which is what the
        // mismatch comparison is made against.
        XCTAssertEqual(String(publicKey.dropLast()), pin.publicKey)
    }

    // ---- a signer, so the gate's tests can be a server ---------------------

    func testTheTestOnlySignerProducesAnswersTheCheckAccepts() throws {
        // `FakeDomovoi` in `IdentityGateTests` signs with this, so a failure
        // here would make every gate test a test of the fake.
        let seed = "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"
        let body = try TestIdentity(seed: seed).healthBody(challenge: challenge)
        guard case .verified(let pin) = ServerIdentity.check(healthBody: body, challenge: challenge, pinned: nil) else {
            return XCTFail("the fake server's own answer must verify")
        }
        XCTAssertEqual(try TestIdentity(seed: seed).fingerprint, pin.fingerprint)
        // And it signs the vector's published key, so the fake is the core's
        // sibling and not a lookalike.
        XCTAssertEqual("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", hexString(try TestIdentity(seed: seed).publicKeyBytes))
    }

    // ---- helpers -----------------------------------------------------------

    private func signatureField(of body: String) -> String? {
        guard let object = ServerIdentity.jsonObject(body),
              let identity = object["identity"] as? [String: Any] else { return nil }
        return identity["signature"] as? String
    }

    private func hexString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
