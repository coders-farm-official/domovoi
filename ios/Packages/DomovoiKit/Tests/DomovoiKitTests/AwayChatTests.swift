import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import DomovoiKit

/// The typed client over a session, the chat screen on the away path, the
/// controller's lifetime rule, Settings' model and the picture.
final class AwayChatClientTests: XCTestCase {

    private func client(_ house: FakeAwayHouse) async -> (AwayChatClient, AwaySession) {
        let session = AwaySession(enrolment: await fakeEnrolment(house: house), transport: FakeAwayTransport(house: house),
                                  version: "1.5.0", handshakeTimeout: 5)
        return (AwayChatClient(session: session), session)
    }

    func testTheListReadsEveryPage() async throws {
        let house = FakeAwayHouse { m in
            let cursor = m.json["cursor"] as? String
            if cursor == nil {
                return [(.resp, m.req, #"{"threads":[{"id":9,"title":"a","updated_at":"2026-11-27T10:00:00+00:00"}],"next":"p2"}"#, false)]
            }
            return [(.resp, m.req, #"{"threads":[{"id":4,"title":"b","shared":false,"mine":true,"can_write":true}],"next":null}"#, false)]
        }
        let (c, session) = await client(house)
        let list = try await c.threads(archived: false)
        XCTAssertEqual(list.threads.map(\.id), [9, 4])
        XCTAssertEqual(list.threads[1].mine, true)
        let got = await house.received
        XCTAssertEqual(got.map { $0.json["cursor"] as? String }, [nil, "p2"])
        XCTAssertEqual(got.first?.json["archived"] as? Bool, false)
        await session.stop()
    }

    func testHistoryReadsBackToTheStartAndKeepsPictureEntries() async throws {
        let house = FakeAwayHouse { m in
            if m.json["before_id"] is NSNull {
                return [(.resp, m.req, #"{"thread":{"id":12,"title":"x"},"messages":[{"id":5,"role":"user","content":"look","images":[{"kind":"picture"}]},{"id":6,"role":"assistant","content":"a cat"}],"more":true}"#, false)]
            }
            return [(.resp, m.req, #"{"thread":{"id":12},"messages":[{"id":3,"role":"user","content":"hi"}],"more":false}"#, false)]
        }
        let (c, session) = await client(house)
        let list = try await c.messages(12)
        XCTAssertEqual(list.messages.map(\.id), [3, 5, 6])
        XCTAssertEqual(list.messages[1].images.count, 1)
        XCTAssertEqual(list.thread?.id, 12)
        let got = await house.received
        XCTAssertEqual((got.last?.json["before_id"] as? NSNumber)?.int64Value, 5)
        await session.stop()
    }

    func testASendIsItsIdsThenTheHomeStreamsEvents() async throws {
        let house = FakeAwayHouse { m in
            [
                (.resp, m.req, #"{"chat_id":12,"message_id":345,"user_message":{"id":345}}"#, false),
                (.event, m.req, #"{"type":"delta","text":"Milk"}"#, false),
                (.event, m.req, #"{"type":"done","message":{"id":346,"role":"assistant","content":"Milk"}}"#, false),
            ]
        }
        let (c, session) = await client(house)
        let start = try await c.send(chatID: nil, clientID: AwayRequest.newClientID(), text: "list?", picture: nil)
        XCTAssertEqual(start.chatID, 12)
        XCTAssertEqual(start.messageID, 345)
        var events: [ServerEvent] = []
        for try await e in start.events { events.append(e) }
        XCTAssertEqual(events.map(\.name), ["delta", "done"])
        let done: ChatMessage? = events[1].payload()
        XCTAssertEqual(done?.content, "Milk")
        let sent = await house.received.first?.json
        XCTAssertTrue(sent?["chat_id"] is NSNull)
        XCTAssertNil(sent?["admin_grant"], "the home chat never acts as admin on this phone, so away sends no grant")
        await session.stop()
    }

    func testAnErrorEventKeepsItsCodeAndATooLargeOneGetsWords() {
        let e = AwayChatClient.serverEvent(Data(#"{"type":"error","code":"cancelled","detail":"stopped","message":{"id":1}}"#.utf8))
        XCTAssertEqual(e?.name, "error")
        let payload = try? JSONSerialization.jsonObject(with: Data(e!.data.utf8)) as? [String: Any]
        XCTAssertEqual(payload?["code"] as? String, "cancelled")
        XCTAssertNil(payload?["message"])
        let big = AwayChatClient.serverEvent(Data(#"{"type":"error","code":"x","too_large":true}"#.utf8))
        XCTAssertTrue(big!.data.contains(AwayStrings.somethingWrong))
        XCTAssertNil(AwayChatClient.serverEvent(Data("[]".utf8)))
    }

    func testResumeIsNilWithNothingRunningAndEventsWithOne() async throws {
        let house = FakeAwayHouse { m in
            (m.json["chat_id"] as? NSNumber)?.intValue == 1
                ? [(.resp, m.req, #"{"streaming":false}"#, false)]
                : [(.resp, m.req, #"{"streaming":true,"message_id":7}"#, false),
                   (.event, m.req, #"{"type":"delta","text":"so far"}"#, false),
                   (.event, m.req, #"{"type":"error","code":"cancelled","detail":"stopped"}"#, false)]
        }
        let (c, session) = await client(house)
        let none = try await c.resume(1)
        XCTAssertNil(none)
        let some = try await c.resume(2)
        var names: [String] = []
        for try await e in some! { names.append(e.name) }
        XCTAssertEqual(names, ["delta", "error"])
        await session.stop()
    }

    func testCancelAndAnErrThrown() async throws {
        let house = FakeAwayHouse { m in
            (m.json["chat_id"] as? NSNumber)?.intValue == 1
                ? [(.resp, m.req, #"{"cancelled":true}"#, false)]
                : [(.err, m.req, #"{"code":"not_found","detail":"that chat isn't here"}"#, false)]
        }
        let (c, session) = await client(house)
        let cancelled = try await c.cancel(1)
        XCTAssertTrue(cancelled)
        do {
            _ = try await c.messages(2)
            XCTFail("read")
        } catch let err as AwayErr {
            XCTAssertEqual(err.code, "not_found")
        }
        await session.stop()
    }

    func testTheSessionOpensAgainAfterAnIdleClose() async throws {
        let house = FakeAwayHouse { m in [(.resp, m.req, #"{"cancelled":false}"#, false)] }
        let transport = FakeAwayTransport(house: house)
        let session = AwaySession(enrolment: await fakeEnrolment(house: house), transport: transport, version: "1.5.0")
        let c = AwayChatClient(session: session)
        _ = try await c.cancel(1)
        await house.send(.houseBye, 0, #"{"code":"idle"}"#)
        try await Task.sleep(for: .milliseconds(50))
        _ = try await c.cancel(1)
        XCTAssertEqual(transport.opens, 2)
        await session.stop()
        XCTAssertEqual(transport.stops, 1)
        do {
            _ = try await c.cancel(1)
            XCTFail("opened after stop")
        } catch {}
    }

    func testEveryFailureHasItsSharedLine() {
        XCTAssertEqual(awayFailureLine(AwayClosed.silent), "Can't reach your house.")
        XCTAssertEqual(awayFailureLine(AwayClosed.bye("away_off")), "Chat from away is turned off on your house.")
        XCTAssertEqual(awayFailureLine(AwayClosed.bye("revoked")), "Away chat isn't set up for this phone any more.")
        XCTAssertEqual(awayFailureLine(AwayTransportError.notAvailable), "Can't reach your house.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "unsupported_picture")), "That picture couldn't be sent.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "too_large"), hadPicture: true), "That picture couldn't be sent.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "busy")), "Your house is busy. Try again shortly.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "rate", retryAfter: 3)), "Your house is busy. Try again shortly.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "unavailable")), "Your house is busy. Try again shortly.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "ai_off")), "Your house's AI is turned off.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "bad_request")), "Something went wrong.")
        XCTAssertEqual(awayFailureLine(AwayErr(code: "internal")), "Something went wrong.")
        XCTAssertEqual(AwayStrings.internetOff, "Your house's internet setting doesn't allow chat from away.")
        XCTAssertEqual(AwayStrings.setUpAgainAtHome, "Away chat needs setting up again at home.")
        XCTAssertEqual(AwayStrings.connecting, "Connecting to your house…")
        XCTAssertEqual(AwayStrings.offer, "Chat with your house when you're away? You can turn this off any time.")
    }
}

// MARK: - the picture

final class AwayPictureTests: XCTestCase {

    /// A big JPEG with a camera's metadata in it, GPS included.
    private func photo(width: Int, height: Int) throws -> Data {
        let space = CGColorSpaceCreateDeviceRGB()
        // Noise, so the JPEG is large.
        var seed: UInt32 = 7
        var pixels = [UInt8](repeating: 255, count: width * height * 4)
        for i in 0..<(width * height) {
            seed = seed &* 1_103_515_245 &+ 12_345
            pixels[i * 4] = UInt8(truncatingIfNeeded: seed >> 8)
            pixels[i * 4 + 1] = UInt8(truncatingIfNeeded: seed >> 16)
            pixels[i * 4 + 2] = UInt8(truncatingIfNeeded: seed >> 24)
        }
        let ctx = try XCTUnwrap(CGContext(data: &pixels, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                          space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
        let image = try XCTUnwrap(ctx.makeImage())
        let out = NSMutableData()
        let dest = try XCTUnwrap(CGImageDestinationCreateWithData(out as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil))
        let meta: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: 1.0,
            kCGImagePropertyGPSDictionary: [kCGImagePropertyGPSLatitude: 51.5, kCGImagePropertyGPSLongitude: 0.12,
                                            kCGImagePropertyGPSLatitudeRef: "N", kCGImagePropertyGPSLongitudeRef: "W"],
            kCGImagePropertyExifDictionary: [kCGImagePropertyExifUserComment: "secret place",
                                             kCGImagePropertyExifDateTimeOriginal: "2026:11:27 10:00:00"],
            kCGImagePropertyTIFFDictionary: [kCGImagePropertyTIFFMake: "PhoneCo", kCGImagePropertyTIFFModel: "One"],
        ]
        CGImageDestinationAddImage(dest, image, meta as CFDictionary)
        XCTAssertTrue(CGImageDestinationFinalize(dest))
        return out as Data
    }

    func testAPhotoIsShrunkStrippedAndHashed() throws {
        let original = try photo(width: 3000, height: 2000)
        XCTAssertGreaterThan(original.count, 1_048_576)
        let source = try XCTUnwrap(CGImageSourceCreateWithData(original as CFData, nil))
        let had = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        XCTAssertNotNil(had?[kCGImagePropertyGPSDictionary], "the fixture really carries GPS")

        let p = try XCTUnwrap(AwayPicture.prepare(original))
        XCTAssertEqual(max(p.width, p.height), 1024)
        XCTAssertEqual(min(p.width, p.height), 683)
        XCTAssertLessThanOrEqual(p.jpeg.count, 1_048_576)
        XCTAssertEqual(p.jpeg.prefix(3), Data([0xFF, 0xD8, 0xFF]))
        XCTAssertEqual(p.sha256, AwayPicture.hex(p.jpeg))
        XCTAssertEqual(p.sha256.count, 64)
        let out = try XCTUnwrap(CGImageSourceCreateWithData(p.jpeg as CFData, nil))
        let props = CGImageSourceCopyPropertiesAtIndex(out, 0, nil) as? [CFString: Any] ?? [:]
        XCTAssertNil(props[kCGImagePropertyGPSDictionary])
        let exif = props[kCGImagePropertyExifDictionary] as? [CFString: Any] ?? [:]
        XCTAssertNil(exif[kCGImagePropertyExifUserComment])
        XCTAssertNil(exif[kCGImagePropertyExifDateTimeOriginal])
        let tiff = props[kCGImagePropertyTIFFDictionary] as? [CFString: Any] ?? [:]
        XCTAssertNil(tiff[kCGImagePropertyTIFFMake])
        XCTAssertNil(CGImageSourceCopyMetadataAtIndex(out, 0, nil).flatMap {
            CGImageMetadataCopyStringValueWithPath($0, nil, "exif:UserComment" as CFString)
        })
    }

    func testASmallPictureKeepsItsSize() throws {
        let p = try XCTUnwrap(AwayPicture.prepare(try photo(width: 400, height: 300)))
        XCTAssertEqual(p.width, 400)
        XCTAssertEqual(p.height, 300)
    }

    func testNotAPictureIsNil() {
        XCTAssertNil(AwayPicture.prepare(Data("hello".utf8)))
    }
}

// MARK: - the controller and Settings

@MainActor
final class AwayModelTests: XCTestCase {

    private let server = "http://192.168.1.20:6369"

    final class Calls: @unchecked Sendable {
        let lock = NSLock()
        var seen: [String] = []
        var answers: [String: Result<Data, DomovoiError>] = [:]

        func call(_ m: String, _ p: String) throws(DomovoiError) -> Data {
            lock.lock(); defer { lock.unlock() }
            seen.append("\(m) \(p)")
            return try (answers["\(m) \(p)"] ?? .success(Data("{}".utf8))).get()
        }
    }

    private func model(_ house: FakeAwayHouse, keys: AwayKeyStore = AwayKeyStore(), calls: Calls = Calls(),
                       available: Bool = true, stopDelay: Double = 0.05,
                       directories: AwayTorDirectories? = nil) -> (AwayModel, FakeAwayTransport) {
        let transport = FakeAwayTransport(house: house, available: available)
        let api = AwayEnrolApi(deviceID: { "ios-1" }) { m, p, _, _ throws(DomovoiError) in try calls.call(m, p) }
        let server = self.server
        let m = AwayModel(keys: keys, api: api, transport: transport, version: "1.5.0", server: { server },
                          stopDelay: stopDelay, directories: directories)
        return (m, transport)
    }

    private func enrolled(_ house: FakeAwayHouse) async -> AwayKeyStore {
        let keys = AwayKeyStore()
        keys.keep(await fakeEnrolment(house: house, server: server))
        return keys
    }

    func testAwayIsTheOutOfReachShellAndNothingElse() {
        XCTAssertTrue(awayIsAway(.localMedia(.outOfReach)))
        XCTAssertFalse(awayIsAway(.workspace))
        XCTAssertFalse(awayIsAway(.connecting))
        XCTAssertFalse(awayIsAway(.pairing))
        XCTAssertFalse(awayIsAway(.localMedia(.noServer)))
        XCTAssertFalse(awayIsAway(.localMedia(.choseThisPhone)))
    }

    func testTorRunsOnlyWithTheChatOpenAndThePhoneAway() async {
        let house = FakeAwayHouse()
        let (m, transport) = model(house, keys: await enrolled(house))
        let c = m.controller
        c.chatOpened()
        XCTAssertFalse(c.running, "at home: today's path")
        XCTAssertFalse(c.awayPath)
        c.chatClosed()
        c.homeChanged(atHome: false)
        XCTAssertFalse(c.running, "away, but the chat isn't open")
        c.chatOpened()
        XCTAssertTrue(c.running)
        XCTAssertNotNil(c.link)
        let ready = await awayEventually { c.phase == .ready }
        XCTAssertTrue(ready)
        XCTAssertEqual(transport.opens, 1)
    }

    func testItStopsShortlyAfterTheChatCloses() async {
        let house = FakeAwayHouse()
        let (m, transport) = model(house, keys: await enrolled(house), stopDelay: 0.1)
        let c = m.controller
        c.homeChanged(atHome: false)
        c.chatOpened()
        c.chatClosed()
        XCTAssertTrue(c.running, "a grace first")
        c.chatOpened()
        try? await Task.sleep(for: .milliseconds(200))
        XCTAssertTrue(c.running, "reopened inside the grace: still running")
        c.chatClosed()
        let stopped = await awayEventually { !c.running }
        XCTAssertTrue(stopped)
        let engineStopped = await awayEventually { transport.stops == 1 }
        XCTAssertTrue(engineStopped)
        XCTAssertNil(c.link)
        XCTAssertEqual(c.phase, .off)
    }

    func testItStopsAtOnceInTheBackgroundAndAtHome() async {
        let house = FakeAwayHouse()
        let (m, _) = model(house, keys: await enrolled(house), stopDelay: 60)
        let c = m.controller
        c.homeChanged(atHome: false)
        c.chatOpened()
        XCTAssertTrue(c.running)
        c.activeChanged(false)
        XCTAssertFalse(c.running, "never in the background")
        c.activeChanged(true)
        XCTAssertTrue(c.running)
        c.homeChanged(atHome: true)
        XCTAssertFalse(c.running, "home: today's path")
    }

    func testTheKeyFileIsWrittenBeforeTorAndDeletedWhenItStops() async throws {
        let house = FakeAwayHouse()
        let dirs = AwayTorDirectories(root: FileManager.default.temporaryDirectory
            .appendingPathComponent("away-tor-\(UUID().uuidString)", isDirectory: true))
        defer { try? FileManager.default.removeItem(at: dirs.root) }
        let (m, _) = model(house, keys: await enrolled(house), directories: dirs)
        let c = m.controller
        c.homeChanged(atHome: false)
        c.chatOpened()
        let names = try FileManager.default.contentsOfDirectory(atPath: dirs.auth.path)
        XCTAssertEqual(names, [String(repeating: "a", count: 56) + ".auth_private"])
        c.activeChanged(false)
        let gone = await awayEventually {
            ((try? FileManager.default.contentsOfDirectory(atPath: dirs.auth.path)) ?? ["x"]).isEmpty
        }
        XCTAssertTrue(gone)
    }

    func testNoEngineIsCantReachAndNoKeysIsNoAwayPath() async {
        let house = FakeAwayHouse()
        let (m, _) = model(house, keys: await enrolled(house), available: false)
        m.controller.homeChanged(atHome: false)
        m.controller.chatOpened()
        XCTAssertEqual(m.controller.phase, .failed(AwayStrings.unreachable))
        XCTAssertFalse(m.engineAvailable)
        let (bare, _) = model(house)
        bare.controller.homeChanged(atHome: false)
        bare.controller.chatOpened()
        XCTAssertFalse(bare.controller.awayPath)
        XCTAssertFalse(bare.controller.running)
    }

    func testSilenceMeansAskTheHouseNextTimeHome() async throws {
        let house = FakeAwayHouse()
        await house.setSilent(true)
        let keys = await enrolled(house)
        let calls = Calls()
        calls.answers["GET /api/away/status"] = .success(Data(#"{"house":"on","enrolled":false,"state":null}"#.utf8))
        let (m, _) = model(house, keys: keys, calls: calls)
        m.controller.homeChanged(atHome: false)
        m.controller.chatOpened()
        let failed = await awayEventually { m.controller.phase == .failed(AwayStrings.unreachable) }
        XCTAssertTrue(failed)
        XCTAssertTrue(keys.needsRecheck(server))
        // Away, no status is asked.
        await m.checkAtHome()
        XCTAssertTrue(calls.seen.isEmpty)
        m.controller.chatClosed()
        m.controller.homeChanged(atHome: true)
        await m.checkAtHome()
        XCTAssertEqual(calls.seen, ["GET /api/away/status"])
        XCTAssertFalse(m.enrolled, "enrolled:false: the keys went")
        XCTAssertTrue(m.lapsed)
        XCTAssertEqual(m.line, AwayStrings.notSetUpAnyMore)
        // Away again: the chat says it needs setting up at home.
        m.controller.homeChanged(atHome: false)
        XCTAssertTrue(m.controller.lapsed)
        // Nothing more to ask.
        await m.checkAtHome()
        XCTAssertEqual(calls.seen.count, 1)
    }

    func testTheHousesReasonsAreSaidAtHome() async {
        let house = FakeAwayHouse()
        let keys = await enrolled(house)
        let calls = Calls()
        calls.answers["GET /api/away/status"] = .success(Data(#"{"house":"internet_off","enrolled":true,"state":null}"#.utf8))
        let (m, _) = model(house, keys: keys, calls: calls)
        await m.checkAtHome(force: true)
        XCTAssertEqual(m.line, AwayStrings.internetOff)
        XCTAssertTrue(m.enrolled)
        calls.answers["GET /api/away/status"] = .success(Data(#"{"house":"away_off","enrolled":true,"state":null}"#.utf8))
        await m.checkAtHome(force: true)
        XCTAssertEqual(m.line, AwayStrings.awayOff)
    }

    func testTheOfferAfterPairingTurnsItOn() async {
        let house = FakeAwayHouse()
        let calls = Calls()
        calls.answers["POST /api/away/enroll"] = .success(Data("""
        {"onion":"\(String(repeating: "e", count: 56)).onion","port":80,"house_noise_pub":"\(AwayKeyCodec.base64url(house.key.publicKey))","proto":1,"state":"ready"}
        """.utf8))
        let keys = AwayKeyStore()
        let (m, _) = model(house, keys: keys, calls: calls)
        m.paired()
        XCTAssertTrue(m.offerUp)
        m.offerAnswered(true)
        XCTAssertFalse(m.offerUp)
        let on = await awayEventually { m.enrolled }
        XCTAssertTrue(on)
        XCTAssertEqual(keys.enrolment?.onion, String(repeating: "e", count: 56) + ".onion")
        m.paired()
        XCTAssertFalse(m.offerUp, "already set up: no offer")
    }

    func testNotNowKeepsNothingAndARefusalIsSaid() async {
        let house = FakeAwayHouse()
        let calls = Calls()
        calls.answers["POST /api/away/enroll"] = .failure(.refused(ApiFailure(status: 409, message: "",
                                                                              body: #"{"detail":"x","code":"away_off"}"#)))
        let (m, _) = model(house, calls: calls)
        m.paired()
        m.offerAnswered(false)
        XCTAssertTrue(calls.seen.isEmpty)
        await m.turnOn()
        XCTAssertFalse(m.enrolled)
        XCTAssertEqual(m.line, AwayStrings.awayOff)
    }

    func testOffIsADeleteAndAWipe() async {
        let house = FakeAwayHouse()
        let keys = await enrolled(house)
        let calls = Calls()
        let (m, _) = model(house, keys: keys, calls: calls)
        XCTAssertTrue(m.enrolled)
        await m.turnOff()
        XCTAssertEqual(calls.seen, ["DELETE /api/away/enroll"])
        XCTAssertFalse(m.enrolled)
        XCTAssertNil(keys.enrolment)
    }

    func testForgettingTheServerTurnsItOffFirst() async {
        let house = FakeAwayHouse()
        let keys = await enrolled(house)
        let calls = Calls()
        let (m, _) = model(house, keys: keys, calls: calls)
        await m.parting("http://192.168.1.30:6369")
        XCTAssertTrue(calls.seen.isEmpty, "not this server's keys")
        await m.parting(server)
        XCTAssertEqual(calls.seen, ["DELETE /api/away/enroll"])
        XCTAssertNil(keys.enrolment)
    }
}
