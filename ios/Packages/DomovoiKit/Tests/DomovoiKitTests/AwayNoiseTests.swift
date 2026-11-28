import Foundation
import XCTest
@testable import DomovoiKit

/// Hex both ways, for the away chat tests.
enum AwayHex {
    static func data(_ text: String) -> Data {
        var out = Data()
        var index = text.startIndex
        while index < text.endIndex {
            let next = text.index(index, offsetBy: 2)
            out.append(UInt8(text[index..<next], radix: 16)!)
            index = next
        }
        return out
    }

    static func string(_ data: Data) -> String { data.map { String(format: "%02x", $0) }.joined() }
}

/// The away fixture (`ios/scripts/make-away-noise-fixture.py`): cacophony's
/// IK vector, and a handshake plus transport messages the house's own Noise
/// wrote with away chat's prologue.
struct AwayNoiseFixture: Decodable {
    struct Message: Decodable {
        let payload: String
        let ciphertext: String
    }

    struct Cacophony: Decodable {
        let init_prologue: String
        let init_static: String
        let init_ephemeral: String
        let init_remote_static: String
        let resp_static: String
        let resp_ephemeral: String
        let handshake_hash: String
        let messages: [Message]
    }

    struct Transport: Decodable {
        let from: String
        let plaintext: String
        let ciphertext: String
    }

    struct Away: Decodable {
        let prologue: String
        let phone_static: String
        let phone_ephemeral: String
        let house_static: String
        let house_static_public: String
        let house_ephemeral: String
        let phone_hello: String
        let house_hello: String
        let message1: String
        let message2: String
        let handshake_hash: String
        let transport: [Transport]
    }

    let cacophony_ik: [Cacophony]
    let away: Away

    static func load() throws -> AwayNoiseFixture {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "away_noise_interop", withExtension: "json", subdirectory: "Fixtures"))
        return try JSONDecoder().decode(AwayNoiseFixture.self, from: Data(contentsOf: url))
    }
}

final class AwayNoiseTests: XCTestCase {

    private func pair(_ hex: String) throws -> NoiseKeyPair { try NoiseKeyPair(privateKey: AwayHex.data(hex)) }

    /// Cacophony's `Noise_IK_25519_ChaChaPoly_SHA256`, both roles, every
    /// message: the same vector `test_friend_houses_noise.py` pins the house to.
    func testThePublishedIKVector() throws {
        let fixture = try AwayNoiseFixture.load()
        let v = try XCTUnwrap(fixture.cacophony_ik.first)
        var i = try NoiseIK(initiator: true, prologue: AwayHex.data(v.init_prologue),
                            staticKey: pair(v.init_static), remoteStatic: AwayHex.data(v.init_remote_static),
                            ephemeral: pair(v.init_ephemeral))
        var r = try NoiseIK(initiator: false, prologue: AwayHex.data(v.init_prologue),
                            staticKey: pair(v.resp_static), ephemeral: pair(v.resp_ephemeral))
        let m0 = v.messages[0], m1 = v.messages[1]
        let c0 = try i.writeMessage(AwayHex.data(m0.payload))
        XCTAssertEqual(AwayHex.string(c0), m0.ciphertext)
        XCTAssertEqual(AwayHex.string(try r.readMessage(c0)), m0.payload)
        XCTAssertEqual(r.remoteStatic, try pair(v.init_static).publicKey)
        let c1 = try r.writeMessage(AwayHex.data(m1.payload))
        XCTAssertEqual(AwayHex.string(c1), m1.ciphertext)
        XCTAssertEqual(AwayHex.string(try i.readMessage(c1)), m1.payload)
        XCTAssertEqual(AwayHex.string(i.handshakeHash), v.handshake_hash)
        XCTAssertEqual(AwayHex.string(r.handshakeHash), v.handshake_hash)
        var it = try i.transport(), rt = try r.transport()
        // The rest alternate, initiator first, as cacophony writes them.
        for (n, m) in v.messages.dropFirst(2).enumerated() {
            let fromInitiator = n % 2 == 0
            let c = fromInitiator ? try it.encrypt(AwayHex.data(m.payload)) : try rt.encrypt(AwayHex.data(m.payload))
            XCTAssertEqual(AwayHex.string(c), m.ciphertext, "transport message \(n)")
            let p = fromInitiator ? try rt.decrypt(c) : try it.decrypt(c)
            XCTAssertEqual(AwayHex.string(p), m.payload)
        }
    }

    /// The house's own bytes for an away handshake: what the Swift initiator
    /// writes is exactly what `noise.py` wrote, and it reads the house's
    /// answer and transport messages.
    func testTheHousesAwayHandshakeAndTransportMessages() throws {
        let a = try AwayNoiseFixture.load().away
        XCTAssertEqual(a.prologue, AwayHex.string(AwayWire.prologue))
        XCTAssertEqual(a.phone_hello, AwayHex.string(try AwayWire.phoneHello(version: "1.5.0")))
        var phone = try NoiseIK(initiator: true, prologue: AwayWire.prologue, staticKey: pair(a.phone_static),
                                remoteStatic: AwayHex.data(a.house_static_public), ephemeral: pair(a.phone_ephemeral))
        let m1 = try phone.writeMessage(AwayHex.data(a.phone_hello))
        XCTAssertEqual(AwayHex.string(m1), a.message1)
        XCTAssertEqual(AwayHex.string(try phone.readMessage(AwayHex.data(a.message2))), a.house_hello)
        XCTAssertEqual(AwayHex.string(phone.handshakeHash), a.handshake_hash)
        var t = try phone.transport()
        for m in a.transport {
            if m.from == "phone" {
                XCTAssertEqual(AwayHex.string(try t.encrypt(AwayHex.data(m.plaintext))), m.ciphertext)
            } else {
                XCTAssertEqual(AwayHex.string(try t.decrypt(AwayHex.data(m.ciphertext))), m.plaintext)
            }
        }
    }

    /// The responder role (the client tests' fake house) against the house's
    /// first message.
    func testTheResponderReadsTheHousesFirstMessage() throws {
        let a = try AwayNoiseFixture.load().away
        var house = try NoiseIK(initiator: false, prologue: AwayWire.prologue, staticKey: pair(a.house_static),
                                ephemeral: pair(a.house_ephemeral))
        XCTAssertEqual(AwayHex.string(try house.readMessage(AwayHex.data(a.message1))), a.phone_hello)
        XCTAssertEqual(house.remoteStatic, try pair(a.phone_static).publicKey)
        XCTAssertEqual(AwayHex.string(try house.writeMessage(AwayHex.data(a.house_hello))), a.message2)
    }

    func testTheNonceIsFourZerosThenALittleEndianCounter() {
        XCTAssertEqual(AwayHex.string(NoiseCrypto.nonce(0)), "000000000000000000000000")
        XCTAssertEqual(AwayHex.string(NoiseCrypto.nonce(1)), "000000000100000000000000")
        XCTAssertEqual(AwayHex.string(NoiseCrypto.nonce(0x0102030405060708)), "000000000807060504030201")
    }

    private func handshake(prologueI: Data = AwayWire.prologue, prologueR: Data = AwayWire.prologue,
                           houseKnown: NoiseKeyPair? = nil) throws -> (NoiseIK, NoiseIK, NoiseKeyPair) {
        let phoneKey = NoiseKeyPair.generate(), houseKey = NoiseKeyPair.generate()
        let i = try NoiseIK(initiator: true, prologue: prologueI, staticKey: phoneKey,
                            remoteStatic: (houseKnown ?? houseKey).publicKey)
        let r = try NoiseIK(initiator: false, prologue: prologueR, staticKey: houseKey)
        return (i, r, phoneKey)
    }

    func testARoundTripAndTheResponderLearnsWhoCalled() throws {
        var (i, r, phoneKey) = try handshake()
        XCTAssertEqual(try r.readMessage(try i.writeMessage(Data("hi".utf8))), Data("hi".utf8))
        XCTAssertEqual(r.remoteStatic, phoneKey.publicKey)
        XCTAssertEqual(try i.readMessage(try r.writeMessage(Data("yo".utf8))), Data("yo".utf8))
        var it = try i.transport(), rt = try r.transport()
        XCTAssertEqual(try rt.decrypt(try it.encrypt(Data("a".utf8))), Data("a".utf8))
        XCTAssertEqual(try it.decrypt(try rt.encrypt(Data("b".utf8))), Data("b".utf8))
    }

    func testADifferentPrologueIsRefused() throws {
        var (i, r, _) = try handshake(prologueR: Data("domovoi-away-v2".utf8))
        XCTAssertThrowsError(try r.readMessage(try i.writeMessage()))
    }

    func testTheWrongHouseKeyIsRefused() throws {
        var (i, r, _) = try handshake(houseKnown: NoiseKeyPair.generate())
        XCTAssertThrowsError(try r.readMessage(try i.writeMessage()))
    }

    func testAFlippedBitIsRefusedAndDoesNotMoveTheCounter() throws {
        var (i, r, _) = try handshake()
        _ = try r.readMessage(try i.writeMessage())
        _ = try i.readMessage(try r.writeMessage())
        var it = try i.transport(), rt = try r.transport()
        var c = try it.encrypt(Data("hello".utf8))
        c[0] ^= 1
        XCTAssertThrowsError(try rt.decrypt(c))
        c[0] ^= 1
        XCTAssertEqual(try rt.decrypt(c), Data("hello".utf8))
        // A replay is refused: the counter moved on.
        XCTAssertThrowsError(try rt.decrypt(c))
    }

    func testOutOfTurnAndOversizeAreRefused() throws {
        var (i, _, _) = try handshake()
        XCTAssertThrowsError(try i.readMessage(Data(count: 96)))
        XCTAssertThrowsError(try i.writeMessage(Data(count: 65_535)))
        XCTAssertThrowsError(try NoiseIK(initiator: true, prologue: Data(), staticKey: .generate()))
    }

    func testAShortFirstMessageIsRefused() throws {
        var (i, _, _) = try handshake()
        let m = try i.writeMessage()
        for cut in [0, 31, 32, 79, m.count - 1] {
            var r = try NoiseIK(initiator: false, prologue: AwayWire.prologue, staticKey: .generate())
            XCTAssertThrowsError(try r.readMessage(m.prefix(cut)), "cut \(cut)")
        }
    }

    func testALowOrderEphemeralIsRefused() throws {
        var r = try NoiseIK(initiator: false, prologue: AwayWire.prologue, staticKey: .generate())
        // The all-zero point: the X25519 result is all zeros.
        XCTAssertThrowsError(try r.readMessage(Data(count: 96)))
    }

    func testAKeyPairNeverPrintsItsPrivateHalf() {
        let k = NoiseKeyPair.generate()
        XCTAssertFalse("\(k)".contains(AwayHex.string(k.privateKey)))
        XCTAssertThrowsError(try NoiseKeyPair(privateKey: Data(count: 31)))
    }
}
