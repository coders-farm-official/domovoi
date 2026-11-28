#if os(iOS)
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import DomovoiKit

/// Away chat over REAL Tor, on an iPhone Simulator: Arti
/// (``DomovoiTorEngine``, the DomovoiTor.xcframework) bootstraps from the
/// live Tor network, finds the house's away onion with this phone's client
/// authorization, and the phone's own Noise, wire and chat client talk to the
/// real away listener behind it.
///
/// It needs a maintainer's local house, kept outside the repository: the web
/// app's API, the away listener and the core's relay on loopback against a
/// test database, with a C tor publishing the listener as a v3 onion service
/// whose `authorized_clients` the house keeps in step with enrolment, and a
/// `house.json` beside it (`web_url`, `token`, `onion`, `house_noise_pub`;
/// while a file `web-off` sits beside it the house's HTTP is down, as when
/// the phone is away). `AWAY_TOR_E2E_HOUSE` names that file (through
/// xcodebuild: `TEST_RUNNER_AWAY_TOR_E2E_HOUSE`); skipped without it.
/// Prints how long each step took.
final class AwayTorE2ETests: XCTestCase {

    struct House: Decodable {
        let web_url: String
        let token: String
        let onion: String
        let house_noise_pub: String
    }

    private var said: [String] = []
    private func log(_ line: String) {
        said.append(line)
        print("[tor-e2e] " + line)
    }

    private static func http(_ house: House, _ method: String, _ path: String, body: Data?,
                      headers: [String: String]) async throws -> (Int, Data) {
        var request = URLRequest(url: URL(string: house.web_url + path)!)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 10
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(house.token, forHTTPHeaderField: deviceTokenHeader)
        for (k, v) in headers { request.setValue(v, forHTTPHeaderField: k) }
        let (data, response) = try await URLSession.shared.data(for: request)
        return ((response as? HTTPURLResponse)?.statusCode ?? 0, data)
    }

    private func api(_ house: House, deviceID: String, deviceKey: String) -> AwayEnrolApi {
        AwayEnrolApi(deviceID: { deviceID }) { method, path, body, headers throws(DomovoiError) in
            let answer: (Int, Data)
            do {
                answer = try await Self.http(house, method, path, body: body,
                                             headers: headers.merging([deviceKeyHeader: deviceKey]) { a, _ in a })
            } catch {
                throw DomovoiError.unreachable("\(error)")
            }
            guard (200...299).contains(answer.0) else {
                throw DomovoiError.refused(ApiFailure(status: answer.0, message: "HTTP \(answer.0)",
                                                      body: String(decoding: answer.1, as: UTF8.self)))
            }
            return answer.1
        }
    }

    /// The house's HTTP, up (`true`) or down, as it answers now.
    private func webAnswers(_ house: House) async -> Bool {
        var request = URLRequest(url: URL(string: house.web_url + "/api/health")!)
        request.timeoutInterval = 2
        return (try? await URLSession.shared.data(for: request)) != nil
    }

    private func waitWeb(_ house: House, up: Bool) async throws {
        for _ in 0..<60 {
            if await webAnswers(house) == up { return }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTFail("the house's HTTP never went \(up ? "up" : "down")")
    }

    private func drain(_ events: AsyncThrowingStream<ServerEvent, Error>) async throws -> [ServerEvent] {
        var out: [ServerEvent] = []
        for try await e in events { out.append(e) }
        return out
    }

    private func seconds(since t0: Date) -> String { String(format: "%.1f s", Date().timeIntervalSince(t0)) }

    func testTheIPhoneChatsWithItsHouseOverRealTor() async throws {
        guard let path = ProcessInfo.processInfo.environment["AWAY_TOR_E2E_HOUSE"] else {
            throw XCTSkip("AWAY_TOR_E2E_HOUSE is not set: no house behind an onion to talk to")
        }
        let houseFile = URL(fileURLWithPath: path)
        let house = try JSONDecoder().decode(House.self, from: Data(contentsOf: houseFile))
        let webOff = houseFile.deletingLastPathComponent().appendingPathComponent("web-off")
        try? FileManager.default.removeItem(at: webOff)
        try await waitWeb(house, up: true)

        // 1. At home: a household phone with a device key, enrolled.
        let deviceID = "ios-tor-e2e-" + UUID().uuidString.prefix(8).lowercased()
        let registration = try JSONSerialization.data(withJSONObject: [
            "device_id": deviceID, "name": "Tor e2e iPhone", "platform": "ios",
            "user_agent": "DomovoiKit tests", "want_device_key": true,
        ])
        let (status, body) = try await Self.http(house, "POST", "/api/devices/register", body: registration,
                                            headers: [AwayEnrolApi.deviceIDHeader: deviceID])
        XCTAssertEqual(status, 200, String(decoding: body, as: UTF8.self))
        let deviceKey = try XCTUnwrap((try JSONSerialization.jsonObject(with: body) as? [String: Any])?["device_key"] as? String)
        let api = api(house, deviceID: deviceID, deviceKey: deviceKey)
        let enrolment = try await api.enroll(server: house.web_url)
        XCTAssertEqual(enrolment.onion, house.onion)
        XCTAssertEqual(enrolment.port, 80)
        log("registered \(deviceID) and enrolled (onion \(enrolment.onion.prefix(8))…)")

        // 2. Away: the house's HTTP goes, Tor is all there is.
        FileManager.default.createFile(atPath: webOff.path, contents: Data())
        try await waitWeb(house, up: false)
        log("the house's HTTP is down")

        // Tor's folders as the app's are (Application Support/domovoi-tor),
        // fresh, so the first start is a cold one.
        let dirs = AwayTorDirectories(root: FileManager.default.temporaryDirectory
            .appendingPathComponent("tor-e2e-\(UUID().uuidString)", isDirectory: true))
        defer { try? FileManager.default.removeItem(at: dirs.root) }
        try dirs.writeAuth(onion: enrolment.onion, key: enrolment.clientAuth)
        let transport = EngineAwayTransport(engine: DomovoiTorEngine(), directories: dirs)
        let session = AwaySession(enrolment: enrolment, transport: transport, version: "1.5.0", handshakeTimeout: 60)
        let client = AwayChatClient(session: session)

        var t0 = Date()
        _ = try await session.connection()
        log("cold: Tor bootstrapped, the onion found with client auth, Noise IK done in \(seconds(since: t0))")

        t0 = Date()
        let start = try await client.send(chatID: nil, clientID: AwayRequest.newClientID(),
                                          text: "What's on the shopping list?", picture: nil)
        let events = try await drain(start.events)
        XCTAssertEqual(events.last?.name, "done")
        let done: ChatMessage? = events.last?.payload()
        XCTAssertEqual(done?.content, "you said: What's on the shopping list?")
        log("chat.send: new chat \(start.chatID), \(events.filter { $0.name == "delta" }.count) deltas, done in \(seconds(since: t0))")

        t0 = Date()
        let list = try await client.threads(archived: false)
        XCTAssertEqual(list.threads.map(\.id), [start.chatID])
        let history = try await client.messages(start.chatID)
        XCTAssertEqual(history.messages.map(\.role), ["user", "assistant"])
        log("chat.list and chat.history in \(seconds(since: t0))")

        // A picture in several 65,000-byte pieces, through Tor.
        let picture = try XCTUnwrap(AwayPicture.prepare(try jpeg()))
        XCTAssertGreaterThan(picture.jpeg.count, 65_000, "more than one piece")
        t0 = Date()
        let withPicture = try await client.send(chatID: start.chatID, clientID: AwayRequest.newClientID(),
                                                text: "What's in this?", picture: picture)
        let pictureEnd = try await drain(withPicture.events).last?.name
        XCTAssertTrue(["done", "error"].contains(pictureEnd ?? ""))
        let rows = try await client.messages(start.chatID)
        XCTAssertEqual(rows.messages.first { $0.id == withPicture.messageID }?.images.count, 1)
        log("a \(picture.jpeg.count)-byte picture in \((picture.jpeg.count + 64_999) / 65_000) pieces, reply \(pictureEnd ?? "?"), in \(seconds(since: t0))")

        // 3. The chat screen closes: Tor stops. It opens again: a warm start.
        await session.stop()
        let again = AwaySession(enrolment: enrolment, transport: transport, version: "1.5.0", handshakeTimeout: 60)
        t0 = Date()
        _ = try await again.connection()
        log("warm: Tor started again from its kept state and cache, connected in \(seconds(since: t0))")
        let later = try await AwayChatClient(session: again).messages(start.chatID)
        XCTAssertEqual(later.messages.count, rows.messages.count)
        await again.stop()

        // 4. Client authorization is what lets it in: a wrong key isn't.
        let wrongDirs = AwayTorDirectories(root: dirs.root)
        try wrongDirs.writeAuth(onion: enrolment.onion, key: AwayClientAuthKey(privateKey: NoiseKeyPair.generate().privateKey))
        let wrong = EngineAwayTransport(engine: DomovoiTorEngine(), directories: wrongDirs)
        t0 = Date()
        do {
            let stream = try await wrong.open(enrolment.endpoint, clientAuth: enrolment.clientAuth)
            await stream.close()
            XCTFail("a wrong client-auth key reached the onion")
        } catch let e as AwayTransportError {
            log("a wrong client-auth key: \(e) after \(seconds(since: t0))")
        }
        await wrong.stop()

        // 5. Turned off at home: the open onion connection gets BYE revoked.
        try dirs.writeAuth(onion: enrolment.onion, key: enrolment.clientAuth)
        let last = AwaySession(enrolment: enrolment, transport: transport, version: "1.5.0", handshakeTimeout: 60)
        let connection = try await last.connection()
        let closed = ClosedBox()
        await connection.onClose { closed.set($0) }
        try? FileManager.default.removeItem(at: webOff)
        try await waitWeb(house, up: true)
        t0 = Date()
        try await api.unenroll()
        let cut = await awayEventually(30) { closed.value != nil }
        XCTAssertTrue(cut)
        XCTAssertEqual(closed.value, .bye("revoked"))
        log("DELETE at home cut the onion connection (BYE revoked) in \(seconds(since: t0))")
        await last.stop()
        dirs.removeAuth()

        try (said.joined(separator: "\n") + "\n").write(to: URL(fileURLWithPath: houseFile.path + ".ios-tor-e2e"),
                                                      atomically: true, encoding: .utf8)
    }

    /// A noisy 1600 x 1200 JPEG (the app shrinks and strips it).
    private func jpeg() throws -> Data {
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
}
#endif
