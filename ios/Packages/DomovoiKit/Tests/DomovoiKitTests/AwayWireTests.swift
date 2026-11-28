import Foundation
import XCTest
@testable import DomovoiKit

/// The away chat wire's pure half, case for case with
/// `domovoi/tests/test_away_wire.py` where the phone has the same rule.
final class AwayWireTests: XCTestCase {

    func testFramesAreABigEndianLengthAndNeverEmptyOrOverLong() throws {
        XCTAssertEqual(AwayHex.string(try AwayWire.frame(Data([0xAB]))), "0001ab")
        XCTAssertEqual(try AwayWire.frame(Data(count: 65_535)).prefix(2), Data([0xFF, 0xFF]))
        XCTAssertThrowsError(try AwayWire.frame(Data()))
        XCTAssertThrowsError(try AwayWire.frame(Data(count: 65_536)))
    }

    func testTheFrameReaderTakesBytesHoweverTheyArrive() throws {
        var reader = AwayFrameReader()
        let stream = try AwayWire.frame(Data("hello".utf8)) + AwayWire.frame(Data("w".utf8))
        for byte in stream.dropLast() {
            reader.feed(Data([byte]))
        }
        XCTAssertEqual(try reader.next(), Data("hello".utf8))
        XCTAssertNil(try reader.next())
        reader.feed(stream.suffix(1))
        XCTAssertEqual(try reader.next(), Data("w".utf8))
        XCTAssertNil(try reader.next())
        reader.feed(Data([0, 0]))
        XCTAssertThrowsError(try reader.next())
    }

    func testThePhoneHelloIsProtoPlatformTwoAndTheVersion() throws {
        XCTAssertEqual(AwayHex.string(try AwayWire.phoneHello(version: "1.5.0")), "0001020" + "5" + AwayHex.string(Data("1.5.0".utf8)))
        // §8 item 1: four bytes at the least, because ver_len is always there.
        XCTAssertEqual(try AwayWire.phoneHello(version: ""), Data([0, 1, 2, 0]))
        XCTAssertEqual(try AwayWire.phoneHello(version: String(repeating: "9", count: 32)).count, 36)
        XCTAssertThrowsError(try AwayWire.phoneHello(version: "1.5.0 (25112701)"))
        XCTAssertEqual(AwayWire.safeVersion("1.5.0 (25112701)"), "1.5.025112701")
        XCTAssertEqual(AwayWire.safeVersion(String(repeating: "1", count: 40)).count, 32)
        XCTAssertEqual(AwayWire.prologue, Data("domovoi-away-v1".utf8))
        XCTAssertEqual(AwayWire.preface, Data("DAC1".utf8))
    }

    func testTheHouseHelloIsSixBytesAndProtoOne() throws {
        XCTAssertEqual(try AwayWire.houseHello(Data([0, 1, 0, 0x10, 0, 0])), AwayHouseHello(proto: 1, maxPicture: 1_048_576))
        XCTAssertThrowsError(try AwayWire.houseHello(Data([0, 2, 0, 0x10, 0, 0])))
        XCTAssertThrowsError(try AwayWire.houseHello(Data([0, 1, 0, 0x10, 0])))
    }

    func testMessagesHaveTheSixByteHeader() throws {
        let m = try AwayWire.message(.req, req: 0x01020304, body: Data("{}".utf8), more: true)
        XCTAssertEqual(AwayHex.string(m), "01010203040" + "1" + "7b7d")
        XCTAssertEqual(try AwayWire.message(.ping, req: 0), Data([3, 0, 0, 0, 0, 0]))
        XCTAssertThrowsError(try AwayWire.message(.req, req: 1, body: Data(count: AwayWire.maxPlaintext)))
    }

    func testHouseMessagesAreCheckedBeforeTheyAreRead() throws {
        func house(_ kind: UInt8, _ req: UInt32, _ flags: UInt8, _ body: String = "") -> Data {
            var d = Data([kind])
            withUnsafeBytes(of: req.bigEndian) { d.append(contentsOf: $0) }
            d.append(flags)
            d.append(Data(body.utf8))
            return d
        }
        XCTAssertEqual(try AwayWire.houseMessage(house(0x81, 7, 1, "{")), AwayMessage(kind: .resp, req: 7, more: true, body: Data("{".utf8)))
        XCTAssertEqual(try AwayWire.houseMessage(house(0x84, 0, 0)).kind, .pong)
        XCTAssertEqual(try AwayWire.houseMessage(house(0x85, 0, 0, "{\"code\":\"idle\"}")).kind, .houseBye)
        for bad in [
            house(0x01, 1, 0), // a phone kind
            house(0x86, 1, 0), // unknown
            house(0x81, 1, 2), // unknown flag
            house(0x82, 1, 1, "{}"), // a split EVENT
            house(0x83, 1, 1, "{}"), // a split ERR
            house(0x81, 0, 0, "{}"), // req 0 on a RESP
            house(0x84, 1, 0), // a PONG with a req
            house(0x84, 0, 0, "x"), // a PONG with a body
            Data([0x81, 0, 0]), // short
        ] {
            XCTAssertThrowsError(try AwayWire.houseMessage(bad), AwayHex.string(bad))
        }
    }

    func testARequestIsSplitWithMoreAndCappedAt160KiB() throws {
        let room = AwayWire.maxPlaintext - AwayWire.headerSize
        let one = try AwayWire.requestMessages(req: 3, json: Data(count: room))
        XCTAssertEqual(one.count, 1)
        XCTAssertEqual(one[0][5], 0)
        let json = Data(repeating: 0x61, count: room * 2 + 10)
        let parts = try AwayWire.requestMessages(req: 3, json: json)
        XCTAssertEqual(parts.count, 3)
        XCTAssertEqual(parts.map { $0[5] }, [1, 1, 0])
        XCTAssertEqual(parts.map { $0.count - AwayWire.headerSize }, [room, room, 10])
        XCTAssertEqual(Data(parts.flatMap { [UInt8]($0.dropFirst(AwayWire.headerSize)) }), json)
        XCTAssertNoThrow(try AwayWire.requestMessages(req: 3, json: Data(count: 160 * 1024)))
        XCTAssertThrowsError(try AwayWire.requestMessages(req: 3, json: Data(count: 160 * 1024 + 1)))
    }

    func testAPictureGoesInPiecesOfAtMost65000FromOffsetZero() throws {
        let picture = Data((0..<150_001).map { UInt8($0 & 0xFF) })
        let pieces = try AwayWire.blobMessages(req: 9, picture: picture)
        XCTAssertEqual(pieces.count, 3)
        var expected = 0
        var joined = Data()
        for p in pieces {
            let b = [UInt8](p)
            XCTAssertEqual(b[0], AwayKind.blob.rawValue)
            XCTAssertEqual(b[5], 0, "never MORE (§8 item 2)")
            let offset = Int(b[6]) << 24 | Int(b[7]) << 16 | Int(b[8]) << 8 | Int(b[9])
            XCTAssertEqual(offset, expected)
            XCTAssertLessThanOrEqual(b.count - 10, 65_000)
            joined.append(contentsOf: b[10...])
            expected += b.count - 10
        }
        XCTAssertEqual(joined, picture)
        XCTAssertThrowsError(try AwayWire.blobMessages(req: 9, picture: Data()))
        XCTAssertThrowsError(try AwayWire.blobMessages(req: 9, picture: Data(count: 1_048_577)))
    }

    private func object(_ r: AwayRequest) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: try r.json()) as? [String: Any])
    }

    func testTheRequestsCarryOnlyTheirOwnKeys() throws {
        let list = try object(.list(archived: false, limit: 50, cursor: nil))
        XCTAssertEqual(Set(list.keys), ["op", "archived", "limit", "cursor"])
        XCTAssertEqual(list["op"] as? String, "chat.list")
        XCTAssertTrue(list["cursor"] is NSNull)
        let history = try object(.history(chatID: 12, beforeID: nil, limit: 50))
        XCTAssertEqual(Set(history.keys), ["op", "chat_id", "before_id", "limit"])
        let id = AwayRequest.newClientID()
        let send = try object(.send(chatID: nil, clientID: id, text: "What's on the list?", picture: nil, adminGrant: nil))
        XCTAssertEqual(Set(send.keys), ["op", "chat_id", "client_id", "text"])
        XCTAssertTrue(send["chat_id"] is NSNull)
        let pic = AwayPictureDeclaration(type: "jpeg", size: 10, sha256: String(repeating: "a", count: 64))
        let withPicture = try object(.send(chatID: 3, clientID: id, text: "", picture: pic, adminGrant: nil))
        XCTAssertEqual(Set(withPicture.keys), ["op", "chat_id", "client_id", "text", "picture"])
        XCTAssertEqual(Set(try object(.stream(chatID: 3)).keys), ["op", "chat_id"])
        XCTAssertEqual(try object(.cancel(chatID: 3))["op"] as? String, "chat.cancel")
    }

    func testARequestTheHouseWouldRefuseNeverLeaves() {
        let id = AwayRequest.newClientID()
        let pic = AwayPictureDeclaration(type: "jpeg", size: 10, sha256: String(repeating: "a", count: 64))
        let bad: [AwayRequest] = [
            .list(archived: false, limit: 0, cursor: nil),
            .list(archived: false, limit: 51, cursor: nil),
            .list(archived: false, limit: 5, cursor: "has space"),
            .history(chatID: 0, beforeID: nil, limit: 5),
            .stream(chatID: 0),
            .send(chatID: nil, clientID: id.uppercased(), text: "x", picture: nil, adminGrant: nil),
            .send(chatID: nil, clientID: id, text: "   ", picture: nil, adminGrant: nil),
            .send(chatID: nil, clientID: id, text: String(repeating: "a", count: 32_001), picture: nil, adminGrant: nil),
            .send(chatID: nil, clientID: id, text: "a\u{0}", picture: nil, adminGrant: nil),
            .send(chatID: nil, clientID: id, text: "", picture: .init(type: "png", size: 1, sha256: pic.sha256), adminGrant: nil),
            .send(chatID: nil, clientID: id, text: "", picture: .init(type: "jpeg", size: 1_048_577, sha256: pic.sha256), adminGrant: nil),
            .send(chatID: nil, clientID: id, text: "", picture: .init(type: "jpeg", size: 1, sha256: "AB"), adminGrant: nil),
            .send(chatID: nil, clientID: id, text: "x", picture: nil, adminGrant: "has space"),
        ]
        for r in bad { XCTAssertThrowsError(try r.json(), "\(r)") }
        // 32,000 code points is fine, however many bytes they are.
        XCTAssertNoThrow(try AwayRequest.send(chatID: nil, clientID: id, text: String(repeating: "é", count: 32_000),
                                              picture: nil, adminGrant: nil).json())
        XCTAssertNoThrow(try AwayRequest.send(chatID: nil, clientID: id, text: "", picture: pic, adminGrant: nil).json())
    }

    func testTheHousesJSONMayNestDeeperThanFour() throws {
        let deep = Data(#"{"messages":[{"images":[{"kind":"picture"}]}],"more":false,"x":1}"#.utf8)
        XCTAssertEqual((try AwayWire.houseObject(deep)["more"]) as? Bool, false)
        XCTAssertThrowsError(try AwayWire.houseObject(Data("[1]".utf8)))
        XCTAssertThrowsError(try AwayWire.houseObject(Data("nope".utf8)))
    }
}
