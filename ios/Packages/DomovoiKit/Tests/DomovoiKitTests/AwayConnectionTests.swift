import Foundation
import XCTest
@testable import DomovoiKit

/// One away chat connection against a fake house over an in-memory pipe.
final class AwayConnectionTests: XCTestCase {

    private func connect(_ house: FakeAwayHouse, phoneWriteDelay: Double = 0, pingEvery: Double = 45,
                         phoneKey: NoiseKeyPair = .generate()) async throws -> AwayConnection {
        let (phone, houseEnd) = AwayPipeEnd.pair(phoneWriteDelay: phoneWriteDelay)
        await house.serve(houseEnd)
        let c = AwayConnection(stream: phone, phoneKey: phoneKey, housePublicKey: house.key.publicKey,
                               version: "1.5.0", pingEvery: pingEvery)
        _ = try await c.open(timeout: 5)
        return c
    }

    private func collect(_ s: AsyncThrowingStream<AwayAnswer, Error>) async throws -> [AwayAnswer] {
        var out: [AwayAnswer] = []
        for try await a in s { out.append(a) }
        return out
    }

    func testTheHandshakeSendsTheIPhonesHelloAndReadsTheHouses() async throws {
        let house = FakeAwayHouse()
        let key = NoiseKeyPair.generate()
        let c = try await connect(house, phoneKey: key)
        let hello = await c.hello
        XCTAssertEqual(hello, AwayHouseHello(proto: 1, maxPicture: 1_048_576))
        let sent = await house.hello
        XCTAssertEqual(sent, try AwayWire.phoneHello(version: "1.5.0"))
        XCTAssertEqual(sent?[2], 2, "platform 2: iPhone")
        let seen = await house.phoneStatic
        XCTAssertEqual(seen, key.publicKey)
        let open = await c.isOpen
        XCTAssertTrue(open)
    }

    func testAHouseThatHangsUpBeforeItsHelloIsSilence() async throws {
        let house = FakeAwayHouse()
        await house.setSilent(true)
        do {
            _ = try await connect(house)
            XCTFail("connected")
        } catch let closed as AwayClosed {
            XCTAssertEqual(closed, .silent)
        }
    }

    func testTheWrongHouseKeyIsSilenceToo() async throws {
        let house = FakeAwayHouse()
        let (phone, houseEnd) = AwayPipeEnd.pair()
        await house.serve(houseEnd)
        let c = AwayConnection(stream: phone, phoneKey: .generate(), housePublicKey: NoiseKeyPair.generate().publicKey,
                               version: "1.5.0")
        do {
            _ = try await c.open(timeout: 5)
            XCTFail("connected")
        } catch let closed as AwayClosed {
            XCTAssertEqual(closed, .silent)
        }
    }

    func testAHouseThatNeverAnswersTimesOut() async throws {
        let (phone, _) = AwayPipeEnd.pair()
        let c = AwayConnection(stream: phone, phoneKey: .generate(), housePublicKey: NoiseKeyPair.generate().publicKey,
                               version: "1.5.0")
        do {
            _ = try await c.open(timeout: 0.1)
            XCTFail("connected")
        } catch let closed as AwayClosed {
            XCTAssertEqual(closed, .timedOut)
        }
    }

    func testAListIsOneRequestAndOneAnswer() async throws {
        let house = FakeAwayHouse { m in
            [(AwayKind.resp, m.req, #"{"threads":[{"id":3,"title":"Milk"}],"next":null,"later":1}"#, false)]
        }
        let c = try await connect(house)
        let answers = try await collect(try await c.request(.list(archived: false, limit: 50, cursor: nil)))
        XCTAssertEqual(answers.count, 1)
        let got = await house.received
        XCTAssertEqual(got.first?.kind, AwayKind.req.rawValue)
        XCTAssertNotEqual(got.first?.req, 0)
        XCTAssertEqual(got.first?.json["op"] as? String, "chat.list")
    }

    func testASplitAnswerIsJoined() async throws {
        let house = FakeAwayHouse { m in
            [(AwayKind.resp, m.req, #"{"threads":[],"#, true), (AwayKind.resp, m.req, #""next":null}"#, false)]
        }
        let c = try await connect(house)
        let answers = try await collect(try await c.request(.list(archived: false, limit: 50, cursor: nil)))
        XCTAssertEqual(answers, [.resp(Data(#"{"threads":[],"next":null}"#.utf8))])
    }

    func testASendIsItsAnswerThenEventsUntilDone() async throws {
        let house = FakeAwayHouse { m in
            [
                (AwayKind.resp, m.req, #"{"chat_id":12,"message_id":345,"user_message":{}}"#, false),
                (AwayKind.event, m.req, #"{"type":"delta","text":"Milk, "}"#, false),
                (AwayKind.event, m.req, #"{"type":"delta","text":"eggs"}"#, false),
                (AwayKind.event, m.req, #"{"type":"done","message":{"id":346,"role":"assistant","content":"Milk, eggs"}}"#, false),
            ]
        }
        let c = try await connect(house)
        let answers = try await collect(try await c.request(.send(chatID: nil, clientID: AwayRequest.newClientID(),
                                                                  text: "list?", picture: nil, adminGrant: nil)))
        XCTAssertEqual(answers.count, 4)
        // The slot is free again: a second send goes.
        let again = try await collect(try await c.request(.send(chatID: 12, clientID: AwayRequest.newClientID(),
                                                                text: "more", picture: nil, adminGrant: nil)))
        XCTAssertEqual(again.count, 4)
    }

    func testAStreamWithNothingRunningEndsWithItsAnswer() async throws {
        let house = FakeAwayHouse { m in [(AwayKind.resp, m.req, #"{"streaming":false}"#, false)] }
        let c = try await connect(house)
        let answers = try await collect(try await c.request(.stream(chatID: 4)))
        XCTAssertEqual(answers.count, 1)
    }

    func testAnErrIsThrownWithItsCodeAndWait() async throws {
        let house = FakeAwayHouse { m in [(AwayKind.err, m.req, #"{"code":"rate","detail":"wait","retry_after":7}"#, false)] }
        let c = try await connect(house)
        do {
            _ = try await collect(try await c.request(.cancel(chatID: 2)))
            XCTFail("answered")
        } catch let err as AwayErr {
            XCTAssertEqual(err, AwayErr(code: "rate", detail: "wait", retryAfter: 7))
        }
        let open = await c.isOpen
        XCTAssertTrue(open, "an ERR leaves the connection open")
    }

    func testAPictureGoesInOrderedPiecesAfterItsRequest() async throws {
        let house = FakeAwayHouse()
        let picture = Data((0..<140_000).map { UInt8($0 % 251) })
        await house.setAnswer { m in
            m.kind == AwayKind.blob.rawValue ? [] : [(AwayKind.resp, m.req, #"{"chat_id":1,"message_id":2,"user_message":{}}"#, false)]
        }
        let c = try await connect(house)
        let declared = AwayPictureDeclaration(type: "jpeg", size: picture.count, sha256: AwayPicture.hex(picture))
        let s = try await c.request(.send(chatID: 1, clientID: AwayRequest.newClientID(), text: "", picture: declared,
                                          adminGrant: nil), picture: picture)
        _ = s
        await house.waitFor(4)
        let got = await house.received
        XCTAssertEqual(got.map(\.kind), [1, 2, 2, 2])
        XCTAssertEqual(Data(got.dropFirst().flatMap { [UInt8]($0.body.dropFirst(4)) }), picture)
        XCTAssertTrue(got.dropFirst().allSatisfy { $0.req == got[0].req && !$0.more })
    }

    func testAPictureRefusedStopsItsPieces() async throws {
        // §8 item 7: after the ERR, no more pieces.
        let house = FakeAwayHouse { m in
            m.kind == AwayKind.req.rawValue ? [(AwayKind.err, m.req, #"{"code":"rate","detail":"","retry_after":3}"#, false)] : []
        }
        let picture = Data(repeating: 0xAB, count: 1_000_000)
        let c = try await connect(house, phoneWriteDelay: 0.02)
        let declared = AwayPictureDeclaration(type: "jpeg", size: picture.count, sha256: AwayPicture.hex(picture))
        let s = try await c.request(.send(chatID: 1, clientID: AwayRequest.newClientID(), text: "", picture: declared,
                                          adminGrant: nil), picture: picture)
        do {
            _ = try await collect(s)
            XCTFail("answered")
        } catch let err as AwayErr {
            XCTAssertEqual(err.code, "rate")
        }
        try await Task.sleep(for: .milliseconds(100))
        let blobs = await house.received.filter { $0.kind == AwayKind.blob.rawValue }.count
        XCTAssertLessThan(blobs, 16, "a 1 MB picture is 16 pieces; the refusal stopped them")
    }

    func testAtMostFourRequestsAreOpenAndAFifthWaits() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        var streams: [AsyncThrowingStream<AwayAnswer, Error>] = []
        for i in 1...4 { streams.append(try await c.request(.history(chatID: Int64(i), beforeID: nil, limit: 5))) }
        let fifth = Task { try await c.request(.history(chatID: 5, beforeID: nil, limit: 5)) }
        await house.waitFor(4)
        try await Task.sleep(for: .milliseconds(100))
        let count = await house.received.count
        XCTAssertEqual(count, 4, "the fifth waited")
        let first = await house.received[0].req
        await house.send(.resp, first, #"{"messages":[],"more":false}"#)
        _ = try await fifth.value
        await house.waitFor(5)
        let after = await house.received.count
        XCTAssertEqual(after, 5)
    }

    func testOnlyOneSendRunsAtATime() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        _ = try await c.request(.send(chatID: 1, clientID: AwayRequest.newClientID(), text: "a", picture: nil, adminGrant: nil))
        do {
            _ = try await c.request(.send(chatID: 1, clientID: AwayRequest.newClientID(), text: "b", picture: nil, adminGrant: nil))
            XCTFail("sent")
        } catch let err as AwayErr {
            XCTAssertEqual(err.code, "busy")
        }
    }

    func testAQuietConnectionPings() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house, pingEvery: 0.05)
        await house.waitFor(2)
        let got = await house.received
        XCTAssertEqual(got.first?.kind, AwayKind.ping.rawValue)
        XCTAssertEqual(got.first?.req, 0)
        XCTAssertEqual(got.first?.body, Data())
        await c.close()
    }

    func testTheHousesGoodbyeEndsEveryRequestWithItsCode() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        let box = ClosedBox()
        await c.onClose { box.set($0) }
        let s = try await c.request(.history(chatID: 1, beforeID: nil, limit: 5))
        await house.waitFor(1)
        await house.send(.houseBye, 0, #"{"code":"revoked"}"#)
        do {
            _ = try await collect(s)
            XCTFail("answered")
        } catch let closed as AwayClosed {
            XCTAssertEqual(closed, .bye("revoked"))
        }
        XCTAssertEqual(box.value, .bye("revoked"))
        let open = await c.isOpen
        XCTAssertFalse(open)
    }

    func testABrokenMessageClosesTheConnection() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        let s = try await c.request(.history(chatID: 1, beforeID: nil, limit: 5))
        await house.waitFor(1)
        await house.sendRaw(Data([0x86, 0, 0, 0, 1, 0]))
        do {
            _ = try await collect(s)
            XCTFail("answered")
        } catch let closed as AwayClosed {
            guard case .protocolError = closed else { return XCTFail("\(closed)") }
        }
    }

    func testAStreamEndingWithoutGoodbyeIsLost() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        let s = try await c.request(.history(chatID: 1, beforeID: nil, limit: 5))
        await house.waitFor(1)
        await house.hangUp()
        do {
            _ = try await collect(s)
            XCTFail("answered")
        } catch let closed as AwayClosed {
            XCTAssertEqual(closed, .lost)
        }
    }

    func testClosingSaysGoodbye() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        await c.close()
        await house.waitFor(1)
        let got = await house.received
        XCTAssertEqual(got.last?.kind, AwayKind.bye.rawValue)
        do {
            _ = try await c.request(.cancel(chatID: 1))
            XCTFail("sent")
        } catch let closed as AwayClosed {
            XCTAssertEqual(closed, .closedHere)
        }
    }

    func testAMessageForARequestItGaveUpOnIsDropped() async throws {
        let house = FakeAwayHouse()
        let c = try await connect(house)
        await house.send(.event, 99, #"{"type":"delta","text":"x"}"#)
        let s = try await c.request(.cancel(chatID: 1))
        await house.waitFor(1)
        let req = await house.received[0].req
        await house.send(.resp, req, #"{"cancelled":true}"#)
        let answers = try await collect(s)
        XCTAssertEqual(answers.count, 1)
    }
}

final class ClosedBox: @unchecked Sendable {
    private let lock = NSLock()
    private var _value: AwayClosed?
    var value: AwayClosed? { lock.lock(); defer { lock.unlock() }; return _value }
    func set(_ v: AwayClosed) { lock.lock(); _value = v; lock.unlock() }
}
