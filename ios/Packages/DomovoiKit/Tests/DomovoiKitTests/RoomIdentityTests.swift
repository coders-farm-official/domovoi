import Foundation
import XCTest
@testable import DomovoiKit

/// The room phone's port of satellite/server_identity.py (Android's
/// `RoomIdentityTest`): an address-bound health proof against the pinned key,
/// and signed file lists. The fixture (Android's test resource
/// roommode/identity-fixture.json, read from the repo) was signed by the
/// core's own domovoi/server_identity.py, so these bytes are the core's.
final class RoomIdentityTests: XCTestCase {

    private func fixture() throws -> RoomJSON {
        try RoomTestKit.json("android/app/src/test/resources/roommode/identity-fixture.json")
    }

    private func fields() throws -> (RoomJSON, fp: String, challenge: String, addr: String) {
        let f = try fixture()
        return (f, f["fingerprint"]?.stringValue ?? "", f["challenge"]?.stringValue ?? "", f["addr"]?.stringValue ?? "")
    }

    private func refused(_ body: () throws -> Any, containing: String? = nil, line: UInt = #line) {
        do {
            _ = try body()
            XCTFail("accepted", line: line)
        } catch let e as RoomIdentity.Failure {
            if let containing { XCTAssertTrue(e.message.contains(containing), e.message, line: line) }
        } catch {
            XCTFail("\(error)", line: line)
        }
    }

    func testTheCoresBoundHealthAnswerVerifies() throws {
        let (f, fp, challenge, addr) = try fields()
        XCTAssertEqual(fp, try RoomIdentity.verifyHealthDocument(f["health"], challenge: challenge, expected: fp, addr: addr))
    }

    func testAnUnboundAnswerIsNotProofForAPinnedRoom() throws {
        let (f, fp, challenge, addr) = try fields()
        refused({ try RoomIdentity.verifyHealthDocument(f["health_unbound"], challenge: challenge, expected: fp, addr: addr) }, containing: "not bound")
    }

    func testAnAnswerForAnotherAddressOrNonceOrKeyIsRefused() throws {
        let (f, fp, challenge, addr) = try fields()
        refused({ try RoomIdentity.verifyHealthDocument(f["health"], challenge: challenge, expected: fp, addr: "192.168.1.99:6370") })
        refused({ try RoomIdentity.verifyHealthDocument(f["health"], challenge: String(("00" + challenge).prefix(32)), expected: fp, addr: addr) })
        refused({ try RoomIdentity.verifyHealthDocument(f["health"], challenge: challenge, expected: "SHA256:X+zrZv/IbzjZUnhsbWlsecLbwjndTpG0ZynXOif7V+k", addr: addr) })
        guard case .object(var doc)? = f["health"], case .object(var identity)? = doc["identity"],
              var sig = identity["signature"]?.stringValue.flatMap({ Data(base64Encoded: $0) })
        else { return XCTFail("no signature in the fixture") }
        sig[0] ^= 1
        identity["signature"] = .string(sig.base64EncodedString())
        doc["identity"] = .object(identity)
        refused({ try RoomIdentity.verifyHealthDocument(.object(doc), challenge: challenge, expected: fp, addr: addr) }, containing: "did not verify")
    }

    func testARefusedAddressSaysSo() throws {
        let (f, fp, challenge, addr) = try fields()
        let doc = RoomJSON.object(["identity": .object([
            "algorithm": .string("ed25519"),
            "public_key": f["health"]?["identity"]?["public_key"] ?? .null,
            "challenge": .string(challenge),
            "addr_refused": .string(addr),
        ])])
        refused({ try RoomIdentity.verifyHealthDocument(doc, challenge: challenge, expected: fp, addr: addr) }, containing: "would not sign")
    }

    func testVerifyServerAsksWithANewNonceAndTheDialedAddress() throws {
        let core = RoomTestCore()
        var asked: String?
        let proved = try RoomIdentity.verifyServer(httpBase: "http://192.168.1.30:6370", fingerprint: core.fingerprint) { url in
            asked = url
            return core.healthAnswer(url: url)
        }
        XCTAssertEqual(core.fingerprint, proved)
        XCTAssertTrue(asked?.hasPrefix("http://192.168.1.30:6370/v1/health?challenge=") == true, asked ?? "nil")
        XCTAssertTrue(asked?.hasSuffix("&addr=192.168.1.30%3A6370") == true, asked ?? "nil")
        // Two asks, two nonces.
        var second: String?
        _ = try RoomIdentity.verifyServer(httpBase: "http://192.168.1.30:6370", fingerprint: core.fingerprint) { url in
            second = url
            return core.healthAnswer(url: url)
        }
        XCTAssertNotEqual(asked, second)
        // A transport failure is "not provable", in words.
        refused({
            try RoomIdentity.verifyServer(httpBase: "http://192.168.1.30:6370", fingerprint: core.fingerprint) { _ in
                throw RoomNetworkFailure("refused")
            }
        }, containing: "could not reach")
        // Another key: refused.
        let rogue = RoomTestCore()
        refused({
            try RoomIdentity.verifyServer(httpBase: "http://192.168.1.30:6370", fingerprint: core.fingerprint) { rogue.healthAnswer(url: $0) }
        }, containing: "different server")
    }

    func testDialedAddressesAreSpelledLikeThePis() throws {
        XCTAssertEqual("192.168.1.30:6370", try RoomIdentity.dialedAddress("http://192.168.1.30:6370"))
        XCTAssertEqual("domovoi.local:80", try RoomIdentity.dialedAddress("http://Domovoi.LOCAL/"))
        XCTAssertEqual("[fd00::5]:6370", try RoomIdentity.dialedAddress("ws://[fd00::5]:6370"))
        XCTAssertEqual("h:443", try RoomIdentity.dialedAddress("https://h"))
        XCTAssertEqual("[fd00:0:0:0:0:0:0:5]:6370", try RoomIdentity.dialedAddress("http://[FD00:0:0:0:0:0:0:5]:6370"))
    }

    func testTheCoresSignedListVerifiesAndCanonicalisesByteForByte() throws {
        let (f, fp, _, _) = try fields()
        let envelope = f["envelope"]
        let accepted = try RoomIdentity.verifyManifestEnvelope(envelope, channel: RoomIdentity.wakeBaseChannel, expected: fp)
        XCTAssertEqual(4, accepted.manifest.count)
        XCTAssertEqual(f["manifest_digest"]?.stringValue, accepted.digest)
        guard let manifest = envelope?["manifest"] else { return XCTFail("no manifest") }
        XCTAssertEqual(f["canonical_manifest"]?.stringValue, String(decoding: try RoomIdentity.canonicalJSON(manifest), as: UTF8.self))
        refused({ try RoomIdentity.verifyManifestEnvelope(envelope, channel: RoomIdentity.soundsChannel, expected: fp) })
        refused({ try RoomIdentity.verifyManifestEnvelope(envelope, channel: RoomIdentity.wakeBaseChannel, expected: "SHA256:X+zrZv/IbzjZUnhsbWlsecLbwjndTpG0ZynXOif7V+k") })
        guard case .object(let o)? = envelope else { return XCTFail("no envelope") }
        var changed = o
        changed["manifest"] = .object(["melspectrogram.onnx": .string(String(repeating: "00", count: 32))])
        refused({ try RoomIdentity.verifyManifestEnvelope(.object(changed), channel: RoomIdentity.wakeBaseChannel, expected: fp) }, containing: "did not verify")
        var unsigned = o
        unsigned["signature_v2"] = nil
        refused({ try RoomIdentity.verifyManifestEnvelope(.object(unsigned), channel: RoomIdentity.wakeBaseChannel, expected: fp) }, containing: "upgrade")
        // A float serial is not a serial.
        var floaty = o
        floaty["serial"] = .double(3.0)
        refused({ try RoomIdentity.verifyManifestEnvelope(.object(floaty), channel: RoomIdentity.wakeBaseChannel, expected: fp) }, containing: "serial")
    }

    func testAnOlderListIsRefused() throws {
        let a = RoomSignedList(manifest: [:], serial: 5, issuedAt: 1, digest: "x")
        try RoomIdentity.checkFreshness(a, last: nil)
        try RoomIdentity.checkFreshness(a, last: RoomSeenList(serial: 5, digest: "x"))
        try RoomIdentity.checkFreshness(a, last: RoomSeenList(serial: 4, digest: "y"))
        refused({ try RoomIdentity.checkFreshness(a, last: RoomSeenList(serial: 6, digest: "x")) }, containing: "older")
        refused({ try RoomIdentity.checkFreshness(a, last: RoomSeenList(serial: 5, digest: "y")) }, containing: "reuses")
    }

    func testCanonicalJSONEscapesOnlyWhatPythonEscapes() throws {
        let doc = RoomJSON.parse(#"{"b":"é\n\"\\\u0001\u007f","a":[1,true,null],"😀":"x","Z":"y"}"#)
        guard let doc else { return XCTFail("did not parse") }
        XCTAssertEqual("{\"Z\":\"y\",\"a\":[1,true,null],\"b\":\"é\\n\\\"\\\\\\u0001\u{7f}\",\"😀\":\"x\"}",
                       String(decoding: try RoomIdentity.canonicalJSON(doc), as: UTF8.self))
        refused({ try RoomIdentity.canonicalJSON(.object(["a": .double(0.5)])) })
    }
}
