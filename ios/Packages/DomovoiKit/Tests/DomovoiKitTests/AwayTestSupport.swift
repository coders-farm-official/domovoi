import Foundation
import XCTest
@testable import DomovoiKit

/// Bytes one way, in memory.
actor AwayByteQueue {
    private var chunks: [Data] = []
    private var waiters: [CheckedContinuation<Data, Never>] = []
    private var closed = false

    func push(_ data: Data) {
        guard !closed else { return }
        if !waiters.isEmpty { waiters.removeFirst().resume(returning: data) } else { chunks.append(data) }
    }

    func pop() async -> Data {
        if !chunks.isEmpty { return chunks.removeFirst() }
        if closed { return Data() }
        return await withCheckedContinuation { waiters.append($0) }
    }

    func close() {
        closed = true
        for w in waiters { w.resume(returning: Data()) }
        waiters = []
    }
}

/// One end of an in-memory pipe.
final class AwayPipeEnd: AwayByteStream {
    let inbound: AwayByteQueue
    let outbound: AwayByteQueue
    let writeDelay: Double

    init(inbound: AwayByteQueue, outbound: AwayByteQueue, writeDelay: Double = 0) {
        self.inbound = inbound
        self.outbound = outbound
        self.writeDelay = writeDelay
    }

    func read() async throws -> Data { await inbound.pop() }

    func write(_ data: Data) async throws {
        if writeDelay > 0 { try await Task.sleep(for: .milliseconds(Int(writeDelay * 1000))) }
        await outbound.push(data)
    }

    func close() async {
        await inbound.close()
        await outbound.close()
    }

    /// Two ends: (phone, house).
    static func pair(phoneWriteDelay: Double = 0) -> (AwayPipeEnd, AwayPipeEnd) {
        let a = AwayByteQueue(), b = AwayByteQueue()
        return (AwayPipeEnd(inbound: a, outbound: b, writeDelay: phoneWriteDelay), AwayPipeEnd(inbound: b, outbound: a))
    }
}

/// A message the fake house read from the phone.
struct PhoneMessage: Equatable, Sendable {
    let kind: UInt8
    let req: UInt32
    let more: Bool
    let body: Data

    var json: [String: Any] { (try? JSONSerialization.jsonObject(with: body) as? [String: Any]) ?? [:] }
}

/// A house on the other end of a byte stream: the Noise responder, the
/// hellos, then every phone message handed to `answer`, which says what to
/// send back (a list of `(kind, req, json body, more)`).
actor FakeAwayHouse {
    typealias Reply = (kind: AwayKind, req: UInt32, body: String, more: Bool)

    let key = NoiseKeyPair.generate()
    private(set) var received: [PhoneMessage] = []
    private(set) var hello: Data?
    private(set) var phoneStatic: Data?
    private var transport: NoiseTransport?
    private var stream: (any AwayByteStream)?
    private var answer: @Sendable (PhoneMessage) -> [Reply]
    /// Close without a word after the first message (a refused phone).
    var silent = false
    private var waiters: [(Int, CheckedContinuation<Void, Never>)] = []

    init(answer: @Sendable @escaping (PhoneMessage) -> [Reply] = { _ in [] }) {
        self.answer = answer
    }

    func setAnswer(_ a: @Sendable @escaping (PhoneMessage) -> [Reply]) { answer = a }
    func setSilent(_ s: Bool) { silent = s }

    func serve(_ stream: any AwayByteStream) {
        self.stream = stream
        Task { await self.run() }
    }

    /// Wait until `count` messages have arrived.
    func waitFor(_ count: Int) async {
        if received.count >= count { return }
        await withCheckedContinuation { waiters.append((count, $0)) }
    }

    private var reader = AwayFrameReader()

    private func frame() async -> Data? {
        guard let stream else { return nil }
        while true {
            if let f = try? reader.next() { return f }
            let chunk = (try? await stream.read()) ?? Data()
            if chunk.isEmpty { return nil }
            reader.feed(chunk)
        }
    }

    private func run() async {
        guard let stream else { return }
        var buffer = Data()
        // The preface.
        while buffer.count < 4 {
            let chunk = (try? await stream.read()) ?? Data()
            if chunk.isEmpty { return }
            buffer.append(chunk)
        }
        guard buffer.prefix(4) == AwayWire.preface else { await stream.close(); return }
        reader.feed(Data([UInt8](buffer)[4...]))
        guard let first = await frame() else { return }
        if silent { await stream.close(); return }
        var hs = try! NoiseIK(initiator: false, prologue: AwayWire.prologue, staticKey: key)
        guard let payload = try? hs.readMessage(first) else { await stream.close(); return }
        hello = payload
        phoneStatic = hs.remoteStatic
        let second = try! hs.writeMessage(Data([0, 1, 0, 0x10, 0, 0]))
        try? await stream.write(try! AwayWire.frame(second))
        transport = try! hs.transport()
        while let f = await frame() {
            guard let plain = try? transport!.decrypt(f) else { await stream.close(); return }
            let b = [UInt8](plain)
            let m = PhoneMessage(kind: b[0], req: UInt32(b[1]) << 24 | UInt32(b[2]) << 16 | UInt32(b[3]) << 8 | UInt32(b[4]),
                                 more: b[5] & 1 != 0, body: Data(b[6...]))
            received.append(m)
            let ready = waiters.filter { $0.0 <= received.count }
            waiters.removeAll { $0.0 <= received.count }
            for w in ready { w.1.resume() }
            if m.kind == AwayKind.bye.rawValue { await stream.close(); return }
            for r in answer(m) { await send(r.kind, r.req, r.body, more: r.more) }
        }
    }

    /// Send one message to the phone.
    func send(_ kind: AwayKind, _ req: UInt32, _ body: String = "", more: Bool = false) async {
        guard var t = transport, let stream else { return }
        let plain = try! AwayWire.message(kind, req: req, body: Data(body.utf8), more: more)
        let c = try! t.encrypt(plain)
        transport = t
        try? await stream.write(try! AwayWire.frame(c))
    }

    /// Send raw plaintext (a broken message).
    func sendRaw(_ plain: Data) async {
        guard var t = transport, let stream else { return }
        let c = try! t.encrypt(plain)
        transport = t
        try? await stream.write(try! AwayWire.frame(c))
    }

    func hangUp() async { await stream?.close() }
}

/// A transport whose streams go to a fake house.
final class FakeAwayTransport: AwayTransport, @unchecked Sendable {
    let house: FakeAwayHouse
    var available: Bool
    private let lock = NSLock()
    private var _opens = 0
    private var _stops = 0
    var phoneWriteDelay: Double = 0

    init(house: FakeAwayHouse, available: Bool = true) {
        self.house = house
        self.available = available
    }

    var opens: Int { lock.lock(); defer { lock.unlock() }; return _opens }
    var stops: Int { lock.lock(); defer { lock.unlock() }; return _stops }
    private func count(open: Bool) { lock.lock(); if open { _opens += 1 } else { _stops += 1 }; lock.unlock() }

    func open(_ endpoint: AwayEndpoint, clientAuth: AwayClientAuthKey) async throws -> any AwayByteStream {
        guard available else { throw AwayTransportError.notAvailable }
        count(open: true)
        let (phone, houseEnd) = AwayPipeEnd.pair(phoneWriteDelay: phoneWriteDelay)
        await house.serve(houseEnd)
        return phone
    }

    func stop() async { count(open: false) }
}

/// An enrolment for a fake house.
func fakeEnrolment(house: FakeAwayHouse, server: String = "http://192.168.1.20:6369") async -> AwayEnrolment {
    AwayEnrolment(server: server, torAuthPrivate: NoiseKeyPair.generate().privateKey,
                  noisePrivate: NoiseKeyPair.generate().privateKey,
                  onion: String(repeating: "a", count: 56) + ".onion", port: 80,
                  houseNoisePublic: house.key.publicKey, proto: 1)
}

/// Poll until `condition` holds or a few seconds pass.
@MainActor
func awayEventually(_ timeout: Double = 3, _ condition: @MainActor () -> Bool) async -> Bool {
    let end = Date().addingTimeInterval(timeout)
    while Date() < end {
        if condition() { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return condition()
}
