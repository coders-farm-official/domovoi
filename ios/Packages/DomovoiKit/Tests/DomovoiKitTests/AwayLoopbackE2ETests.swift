import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import DomovoiKit

/// The phone's away chat against a REAL house on loopback: the dashboard's
/// API for the enrolment, and the real away listener wired to the real core
/// and chat code (`ios/scripts/away_loopback_house.py`, which says how to run
/// both halves). Skipped unless `AWAY_E2E_CONFIG` names the file that script
/// wrote. No Tor: the DEBUG-only direct transport reaches the listener.
final class AwayLoopbackE2ETests: XCTestCase {

    struct Config: Decodable {
        let web: String
        let listener_port: UInt16
        let token_header: String
        let token: String
        let device_id: String
        let device_key: String
    }

    private func config() throws -> (Config, URL) {
        guard let path = ProcessInfo.processInfo.environment["AWAY_E2E_CONFIG"] else {
            throw XCTSkip("AWAY_E2E_CONFIG is not set: no loopback house to talk to")
        }
        let url = URL(fileURLWithPath: path)
        return (try JSONDecoder().decode(Config.self, from: Data(contentsOf: url)), url)
    }

    /// The enrolment routes over plain URLSession, with the headers the app's
    /// client adds for `/api/away` (the household token and the device key).
    private func api(_ c: Config) -> AwayEnrolApi {
        AwayEnrolApi(deviceID: { c.device_id }) { method, path, body, headers throws(DomovoiError) in
            var request = URLRequest(url: URL(string: c.web + path)!)
            request.httpMethod = method
            request.httpBody = body
            request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue(c.token, forHTTPHeaderField: c.token_header)
            request.setValue(c.device_key, forHTTPHeaderField: deviceKeyHeader)
            for (k, v) in headers { request.setValue(v, forHTTPHeaderField: k) }
            let answer: (Data, URLResponse)
            do {
                answer = try await URLSession.shared.data(for: request)
            } catch {
                throw DomovoiError.unreachable("\(error)")
            }
            let status = (answer.1 as? HTTPURLResponse)?.statusCode ?? 0
            guard (200...299).contains(status) else {
                throw DomovoiError.refused(ApiFailure(status: status, message: "HTTP \(status)",
                                                      body: String(decoding: answer.0, as: UTF8.self)))
            }
            return answer.0
        }
    }

    /// Noise, so the shrunk JPEG still needs several 65,000-byte pieces.
    private func photo() throws -> Data {
        let (w, h) = (1600, 1200)
        var seed: UInt32 = 11
        var pixels = [UInt8](repeating: 255, count: w * h * 4)
        for i in 0..<(w * h) {
            seed = seed &* 1_103_515_245 &+ 12_345
            pixels[i * 4] = UInt8(truncatingIfNeeded: seed >> 8)
            pixels[i * 4 + 1] = UInt8(truncatingIfNeeded: seed >> 16)
            pixels[i * 4 + 2] = UInt8(truncatingIfNeeded: seed >> 24)
        }
        let ctx = try XCTUnwrap(CGContext(data: &pixels, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                          space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
        let out = NSMutableData()
        let dest = try XCTUnwrap(CGImageDestinationCreateWithData(out as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(dest, try XCTUnwrap(ctx.makeImage()), nil)
        XCTAssertTrue(CGImageDestinationFinalize(dest))
        return out as Data
    }

    private func drain(_ events: AsyncThrowingStream<ServerEvent, Error>) async throws -> [ServerEvent] {
        var out: [ServerEvent] = []
        for try await e in events { out.append(e) }
        return out
    }

    func testTheIPhoneChatsWithARealHouseOverLoopback() async throws {
        let (c, configURL) = try config()
        var said: [String] = []
        let api = api(c)

        // 1. Enrolment at home, over HTTP.
        let enrolment = try await api.enroll(server: c.web)
        XCTAssertTrue(enrolment.onion.hasSuffix(".onion"))
        XCTAssertEqual(enrolment.port, 80)
        let status = try await api.status()
        XCTAssertEqual(status.house, "on")
        XCTAssertTrue(status.enrolled)
        said.append("enrolled (onion \(enrolment.onion.prefix(8))…, state \(status.state ?? "nil"))")
        // The house rewrites the listener's phone list every 0.2 s.
        try await Task.sleep(for: .milliseconds(600))

        // 2. Away: the real listener, the real chat code.
        let transport = DirectAwayTransport(host: "127.0.0.1", port: c.listener_port)
        let session = AwaySession(enrolment: enrolment, transport: transport, version: "1.5.0", handshakeTimeout: 10)
        let client = AwayChatClient(session: session)
        let start = try await client.send(chatID: nil, clientID: AwayRequest.newClientID(),
                                          text: "What's on the shopping list?", picture: nil)
        let events = try await drain(start.events)
        XCTAssertEqual(events.last?.name, "done")
        let done: ChatMessage? = events.last?.payload()
        XCTAssertEqual(done?.content, "you said: What's on the shopping list?")
        let deltas = events.filter { $0.name == "delta" }.count
        said.append("chat.send: new chat \(start.chatID), \(deltas) deltas, done")

        let list = try await client.threads(archived: false)
        XCTAssertEqual(list.threads.map(\.id), [start.chatID], "private to this phone, and only its own")
        let history = try await client.messages(start.chatID)
        XCTAssertEqual(history.messages.map(\.role), ["user", "assistant"])
        XCTAssertEqual(history.thread?.id, start.chatID)
        let running = try await client.resume(start.chatID)
        XCTAssertNil(running)
        let cancelled = try await client.cancel(start.chatID)
        XCTAssertFalse(cancelled)
        said.append("chat.list, chat.history, chat.stream (none running), chat.cancel (nothing to stop)")

        // The same id again: the first answer, nothing posted twice (§8 item 3).
        let again = AwayRequest.newClientID()
        let first = try await client.send(chatID: start.chatID, clientID: again, text: "once", picture: nil)
        _ = try await drain(first.events)
        let repeated = try await client.send(chatID: start.chatID, clientID: again, text: "once", picture: nil)
        let repeatedEvents = try await drain(repeated.events)
        XCTAssertEqual(repeated.messageID, first.messageID)
        XCTAssertTrue(["done", "error"].contains(repeatedEvents.last?.name ?? ""))
        let afterRepeat = try await client.messages(start.chatID)
        XCTAssertEqual(afterRepeat.messages.filter { $0.content == "once" }.count, 1)
        said.append("a repeated client_id answered once")

        // 3. A picture: shrunk and stripped here, checked and re-encoded there.
        let picture = try XCTUnwrap(AwayPicture.prepare(try photo()))
        XCTAssertGreaterThan(picture.jpeg.count, 65_000, "more than one piece")
        let withPicture = try await client.send(chatID: start.chatID, clientID: AwayRequest.newClientID(),
                                                text: "What's in this?", picture: picture)
        let pictureEvents = try await drain(withPicture.events)
        let ending = pictureEvents.last
        XCTAssertTrue(["done", "error"].contains(ending?.name ?? ""))
        let rows = try await client.messages(start.chatID)
        let sentRow = rows.messages.first { $0.id == withPicture.messageID }
        XCTAssertEqual(sentRow?.images.count, 1, "the house kept the picture: a placeholder entry away")
        said.append("a \(picture.jpeg.count)-byte picture went in \((picture.jpeg.count + 64_999) / 65_000) pieces; the reply ended with \(ending?.name ?? "?")")

        // 4. The chat screen's own model on the away path.
        let model = await MainActor.run { () -> ChatModel in
            let store = InMemoryShellStore(serverURL: c.web, token: c.token)
            let container = AppContainer(store: store, vault: TokenVault(items: InMemoryKeychain()), lock: HouseholdLock())
            return ChatModel(in: container, deviceID: c.device_id, pins: ChatPinBook(place: SavedStateBox()))
        }
        await MainActor.run {
            model.useAway(client)
            model.startNewChat()
            XCTAssertTrue(model.send("And the timers?"))
        }
        let landed = await awayEventually(10) { !model.sending && model.openThread != nil }
        XCTAssertTrue(landed)
        let reread = await awayEventually(5) { model.transcript.messages.last?.content == "you said: And the timers?" }
        XCTAssertTrue(reread, "the reply, then the stored rows")
        said.append("ChatModel sent a new chat from away and drew the reply")

        // 5. Turned off at home: the open connection is cut, the next is silence.
        let connection = try await session.connection()
        let closed = ClosedBox()
        await connection.onClose { closed.set($0) }
        try await api.unenroll()
        let cut = await awayEventually(10) { closed.value != nil }
        XCTAssertTrue(cut)
        XCTAssertEqual(closed.value, .bye("revoked"))
        await session.stop()
        let later = AwaySession(enrolment: enrolment, transport: transport, version: "1.5.0", handshakeTimeout: 10)
        do {
            _ = try await later.connection()
            XCTFail("a revoked phone got in")
        } catch let why as AwayClosed {
            XCTAssertEqual(why, .silent)
        }
        let after = try await api.status()
        XCTAssertFalse(after.enrolled)
        said.append("DELETE cut the open connection (BYE revoked); the next connect was silence; status enrolled:false")

        try (said.joined(separator: "; ") + "\n").write(to: URL(fileURLWithPath: configURL.path + ".done"),
                                                      atomically: true, encoding: .utf8)
        print("away e2e: " + said.joined(separator: "\n  "))
    }
}
