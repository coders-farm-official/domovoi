import CryptoKit
import Foundation
import XCTest
@testable import DomovoiKit

/// What the room-mode package tests share: a core that signs (its own
/// throwaway key, over CryptoKit), fake HTTP, a fake socket transport, fake
/// Wi-Fi, music and ears, and a scripted microphone.
enum RoomTestKit {

    /// The repository's root, from this file (ios/Packages/DomovoiKit/Tests/DomovoiKitTests/…).
    static let repo: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    static func json(_ relative: String) throws -> RoomJSON {
        let data = try Data(contentsOf: repo.appendingPathComponent(relative))
        guard let doc = RoomJSON.parse(data) else {
            XCTFail("\(relative) is not JSON")
            throw RoomNetworkFailure("bad fixture")
        }
        return doc
    }

    static func tempDir() -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("room-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Waits up to `seconds` for `condition`.
    @discardableResult
    static func wait(_ seconds: Double = 5, _ condition: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if condition() { return true }
            Thread.sleep(forTimeInterval: 0.01)
        }
        return condition()
    }
}

/// A Domovoi core's identity, with the private half, so a test can sign
/// health answers and file lists the room checks.
struct RoomTestCore: Sendable {
    let key: Curve25519.Signing.PrivateKey

    init() { key = Curve25519.Signing.PrivateKey() }

    var publicKey: Data { key.publicKey.rawRepresentation }
    var fingerprint: String { RoomIdentity.fingerprintOf(publicKey) }

    func sign(_ message: Data) -> String {
        (try! key.signature(for: message)).base64EncodedString()
    }

    /// `/v1/health?challenge=&addr=`'s answer, signed for what was asked.
    func healthAnswer(url: String) -> String {
        let query = url.split(separator: "?", maxSplits: 1).last.map(String.init) ?? ""
        var q: [String: String] = [:]
        for pair in query.split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1).map(String.init)
            if kv.count == 2 { q[kv[0]] = kv[1].removingPercentEncoding ?? kv[1] }
        }
        let c = q["challenge"] ?? ""
        let a = q["addr"] ?? ""
        let sig = sign(RoomIdentity.healthMessage(challenge: c, addr: a))
        return RoomJSON.object([
            "status": .string("ok"),
            "identity": .object([
                "algorithm": .string("ed25519"),
                "public_key": .string(publicKey.base64EncodedString()),
                "fingerprint": .string(fingerprint),
                "challenge": .string(c),
                "addr": .string(a),
                "signature": .string(sig),
            ]),
        ]).serialized
    }

    /// A `manifest.sig` envelope.
    func envelope(channel: String, manifest: [String: String], serial: Int64, issuedAt: Int64 = 1_760_000_000) -> String {
        let m = RoomJSON.object(manifest.mapValues { .string($0) })
        let sig = sign(try! RoomIdentity.manifestMessageV2(channel: channel, manifest: m, issuedAt: issuedAt, serial: serial))
        return RoomJSON.object([
            "algorithm": .string("ed25519"),
            "public_key": .string(publicKey.base64EncodedString()),
            "fingerprint": .string(fingerprint),
            "channel": .string(channel),
            "manifest": m,
            "issued_at": .int(issuedAt),
            "serial": .int(serial),
            "signature": .string("unused"),
            "signature_v2": .string(sig),
        ]).serialized
    }
}

/// HTTP answered by a closure (nil: a 404).
final class FakeRoomHTTP: RoomHTTP, @unchecked Sendable {
    private let state = RoomLock<(asked: [String], answer: @Sendable (String) -> Data?)>(([], { _ in nil }))

    init(_ answer: @escaping @Sendable (String) -> Data? = { _ in nil }) {
        state.withLock { $0.answer = answer }
    }

    func answer(_ f: @escaping @Sendable (String) -> Data?) { state.withLock { $0.answer = f } }
    var asked: [String] { state.withLock { $0.asked } }

    func get(_ url: String, limit: Int) throws -> Data {
        let f = state.withLock { s -> @Sendable (String) -> Data? in
            s.asked.append(url)
            return s.answer
        }
        guard let body = f(url) else { throw RoomNetworkFailure("404 from \(RoomNetwork.withoutQuery(url))", status: 404) }
        if body.count > limit { throw RoomNetworkFailure("\(url) is too large") }
        return body
    }

    /// A core that proves itself and serves `lists[path]` (signed already).
    static func core(_ core: RoomTestCore, lists: [String: String] = [:]) -> FakeRoomHTTP {
        FakeRoomHTTP { url in
            if url.contains("/v1/health?") { return Data(core.healthAnswer(url: url).utf8) }
            for (path, body) in lists where url.hasSuffix(path) || url.contains(path + "?") { return Data(body.utf8) }
            return nil
        }
    }
}

/// One socket the engine opened: what it sent, and a way to be the core.
final class FakeRoomSocket: RoomSocket, @unchecked Sendable {
    let url: String
    let events: RoomSocketEvents
    private let state = RoomLock<(texts: [String], binaries: Int, closed: Bool)>(([], 0, false))

    init(url: String, events: RoomSocketEvents) {
        self.url = url
        self.events = events
    }

    var texts: [String] { state.withLock { $0.texts } }
    var frames: [RoomJSON] { texts.compactMap { RoomJSON.parse($0) } }
    var binaries: Int { state.withLock { $0.binaries } }
    var isClosed: Bool { state.withLock { $0.closed } }

    func types() -> [String] { frames.compactMap { $0["type"]?.stringValue } }
    func first(_ type: String) -> RoomJSON? { frames.first { $0["type"]?.stringValue == type } }
    func all(_ type: String) -> [RoomJSON] { frames.filter { $0["type"]?.stringValue == type } }

    func sendText(_ text: String) -> Bool {
        state.withLock { s in
            guard !s.closed else { return false }
            s.texts.append(text)
            return true
        }
    }

    func sendBinary(_ data: Data) -> Bool {
        state.withLock { s in
            guard !s.closed else { return false }
            s.binaries += 1
            return true
        }
    }

    func close() { state.withLock { $0.closed = true } }

    // the core's side
    func say(_ frame: RoomJSON) { events.text(frame.serialized) }
    func say(_ text: String) { events.text(text) }
    func serverClose() {
        state.withLock { $0.closed = true }
        events.closed()
    }
}

/// Opens fake sockets; each opens at once.
final class FakeRoomTransport: RoomSocketTransport, @unchecked Sendable {
    private let state = RoomLock<[FakeRoomSocket]>([])
    /// Runs on every new socket (after "opened"): the core's script.
    let onOpen: RoomLock<(@Sendable (FakeRoomSocket) -> Void)?> = RoomLock(nil)

    var sockets: [FakeRoomSocket] { state.withLock { $0 } }
    var last: FakeRoomSocket? { sockets.last }

    func open(_ url: String, events: RoomSocketEvents) -> any RoomSocket {
        let s = FakeRoomSocket(url: url, events: events)
        state.withLock { $0.append(s) }
        let script = onOpen.withLock { $0 }
        DispatchQueue.global().async {
            events.opened()
            script?(s)
        }
        return s
    }
}

final class FakeRoomWifi: RoomWifiWatching, @unchecked Sendable {
    private let up = RoomLock(true)
    var available: Bool { up.withLock { $0 } }
    func start(_ onChange: @escaping @Sendable (Bool) -> Void) {}
    func stop() {}
}

final class FakeRoomMusic: RoomMusicPlaying, @unchecked Sendable {
    let started = RoomLock<[String]>([])
    let stops = RoomLock(0)
    var playing: Bool { false }
    func bind(onReady: @escaping @Sendable () -> Void, onFailed: @escaping @Sendable (String, String, Int) -> Void) {}
    func start(_ streamURL: String) { started.withLock { $0.append(streamURL) } }
    func stop() { stops.withLock { $0 += 1 } }
    func setGain(_ gain: Float) {}
}

/// Ears that wake on a frame whose first sample is ``wakeMark`` and hear
/// speech in a frame whose first sample is over 500.
final class FakeRoomEars: RoomEars {
    static let wakeMark: Int16 = 3_000
    func wakeScore(_ frame: [Int16]) throws -> Float? { frame.first == Self.wakeMark ? 0.912 : 0.01 }
    func resetWake() {}
    func isSpeech(_ frame: [Int16]) throws -> Bool { (frame.first ?? 0) > 500 }
    func resetVoice() {}
}

struct FakeRoomEarsLoader: RoomEarsLoading {
    func load(_ files: RoomModelFiles) throws -> any RoomEars { FakeRoomEars() }
}

/// A microphone that hands over the frames queued on it, then quiet noise,
/// a millisecond a frame (never exact zeros).
final class ScriptedPCMSource: RoomPCMSource, @unchecked Sendable {
    private let state = RoomLock<(open: Bool, queue: [[Int16]], starts: [Bool], stops: Int)>((false, [], [], 0))

    var starts: [Bool] { state.withLock { $0.starts } }
    var stops: Int { state.withLock { $0.stops } }
    var queued: Int { state.withLock { $0.queue.count } }

    /// `count` frames of the same sample.
    func queue(_ sample: Int16, frames count: Int) {
        state.withLock { $0.queue += Array(repeating: [Int16](repeating: sample, count: RoomAudioFrame.samples), count: count) }
    }

    func queue(samples: [Int16]) {
        var at = 0
        var frames: [[Int16]] = []
        while at < samples.count {
            var f = Array(samples[at..<min(samples.count, at + RoomAudioFrame.samples)])
            if f.count < RoomAudioFrame.samples { f += [Int16](repeating: 1, count: RoomAudioFrame.samples - f.count) }
            frames.append(f)
            at += RoomAudioFrame.samples
        }
        state.withLock { $0.queue += frames }
    }

    func start(call: Bool) throws {
        state.withLock {
            $0.open = true
            $0.starts.append(call)
        }
    }

    func read(into frame: inout [Int16]) -> Int {
        Thread.sleep(forTimeInterval: 0.001)
        let next: [Int16]?? = state.withLock { s in
            guard s.open else { return .none }
            return .some(s.queue.isEmpty ? nil : s.queue.removeFirst())
        }
        guard let next else { return -1 }
        for i in frame.indices { frame[i] = next?[i] ?? Int16((i % 5) - 2) | 1 }
        return frame.count
    }

    func stop() {
        state.withLock {
            $0.open = false
            $0.stops += 1
        }
    }
}
