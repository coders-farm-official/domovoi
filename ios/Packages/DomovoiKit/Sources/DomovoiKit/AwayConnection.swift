import Foundation

// ===========================================================================
// One away chat connection: preface, Noise IK, hellos, then requests over
// the encrypted channel (AWAY-CHAT-WIRE §2-§4, §8), over any
// ``AwayByteStream``.
// ===========================================================================

/// How a connection ended, or why a request on it couldn't finish.
public enum AwayClosed: Error, Equatable, Sendable {
    /// The house closed the stream before its hello (§2.3: a wrong key, a
    /// revoked phone, away chat off, a stranger's guess — the house never
    /// says which). The app shows "Can't reach your house." and re-checks
    /// `GET /api/away/status` the next time it is home (§8 item 8).
    case silent
    /// The house said goodbye (§3.1): `shutting_down`, `away_off`,
    /// `revoked`, `idle` or `replaced`.
    case bye(String)
    /// The stream broke or ended after the hellos, without a goodbye.
    case lost
    /// The house broke the protocol; the phone closed the connection.
    case protocolError(String)
    /// The house didn't answer the handshake in time.
    case timedOut
    /// The phone closed it.
    case closedHere
}

/// The house's `ERR` for one request (§5).
public struct AwayErr: Error, Equatable, Sendable {
    public let code: String
    public let detail: String
    public let retryAfter: Int?

    public init(code: String, detail: String = "", retryAfter: Int? = nil) {
        self.code = code
        self.detail = detail
        self.retryAfter = retryAfter
    }

    /// A second `chat.send` while one is still running on this connection
    /// is refused here, as the house would (§4.6: one running reply).
    static let sendRunning = AwayErr(code: "busy", detail: "a reply is still being written")
}

/// One message of a request's answer.
public enum AwayAnswer: Equatable, Sendable {
    /// The `RESP` JSON, its pieces joined.
    case resp(Data)
    /// One stream `EVENT`'s JSON.
    case event(Data)
}

/// The timers a connection runs on, injectable for the tests.
public struct AwayClock: Sendable {
    public var now: @Sendable () -> Double
    public var sleep: @Sendable (Double) async throws -> Void

    public init(now: @Sendable @escaping () -> Double, sleep: @Sendable @escaping (Double) async throws -> Void) {
        self.now = now
        self.sleep = sleep
    }

    public static let system = AwayClock(
        now: { ProcessInfo.processInfo.systemUptime },
        sleep: { try await Task.sleep(for: .milliseconds(Int64(max(0, $0) * 1000))) }
    )
}

/// One connection to the house's away listener.
///
/// **Writes keep the Noise counter's order.** Each message is encrypted in
/// the actor, synchronously, and its write is chained after the previous
/// one, so the order on the wire is the order of the nonces whatever the
/// actor's re-entrancy does.
public actor AwayConnection {

    public static let handshakeTimeoutSec: Double = 30

    private let stream: any AwayByteStream
    private let phoneKey: NoiseKeyPair
    private let housePublicKey: Data
    private let version: String
    private let clock: AwayClock
    private let pingEvery: Double

    private var transport: NoiseTransport?
    private var frames = AwayFrameReader()
    private var writeChain: Task<Void, Error>?
    private var lastSent: Double = 0
    private var reader: Task<Void, Never>?
    private var pinger: Task<Void, Never>?
    private var nextReq: UInt32 = 0
    private var pending: [UInt32: Pending] = [:]
    private var slotWaiters: [CheckedContinuation<Void, Error>] = []
    private(set) public var closed: AwayClosed?
    public private(set) var hello: AwayHouseHello?
    private var onClose: (@Sendable (AwayClosed) -> Void)?

    private final class Pending {
        let request: AwayRequest
        let continuation: AsyncThrowingStream<AwayAnswer, Error>.Continuation
        var respParts = Data()
        var answered = false
        var refused = false

        init(_ request: AwayRequest, _ continuation: AsyncThrowingStream<AwayAnswer, Error>.Continuation) {
            self.request = request
            self.continuation = continuation
        }
    }

    public init(
        stream: any AwayByteStream,
        phoneKey: NoiseKeyPair,
        housePublicKey: Data,
        version: String,
        clock: AwayClock = .system,
        pingEvery: Double = AwayWire.pingEverySec
    ) {
        self.stream = stream
        self.phoneKey = phoneKey
        self.housePublicKey = housePublicKey
        self.version = AwayWire.safeVersion(version)
        self.clock = clock
        self.pingEvery = pingEvery
    }

    /// Told once, when the connection ends for any reason.
    public func onClose(_ sink: @Sendable @escaping (AwayClosed) -> Void) {
        if let closed { sink(closed) } else { onClose = sink }
    }

    public var isOpen: Bool { transport != nil && closed == nil }

    // MARK: - the handshake

    /// Preface, Noise IK message 1 (PHONE_HELLO), message 2 (HOUSE_HELLO).
    /// A house that closes first is ``AwayClosed/silent``.
    public func open(timeout: Double = AwayConnection.handshakeTimeoutSec) async throws -> AwayHouseHello {
        var hs = try NoiseIK(initiator: true, prologue: AwayWire.prologue, staticKey: phoneKey,
                             remoteStatic: housePublicKey)
        let first = try hs.writeMessage(try AwayWire.phoneHello(version: version))
        do {
            try await stream.write(AwayWire.preface + AwayWire.frame(first))
        } catch {
            try await fail(.silent)
        }
        lastSent = clock.now()
        let deadline = Task { [clock, weak self] in
            try? await clock.sleep(timeout)
            guard !Task.isCancelled else { return }
            await self?.handshakeTimedOut()
        }
        defer { deadline.cancel() }
        let second: Data
        do {
            second = try await nextFrame()
        } catch let c as AwayClosed {
            try await fail(c == .lost ? .silent : c)
        } catch {
            try await fail(.silent)
        }
        let payload: Data
        do {
            payload = try hs.readMessage(second)
        } catch {
            try await fail(.silent)
        }
        let houseHello: AwayHouseHello
        do {
            houseHello = try AwayWire.houseHello(payload)
        } catch {
            try await fail(.protocolError(error.reason))
        }
        if let closed { throw closed }
        transport = try hs.transport()
        hello = houseHello
        reader = Task { [weak self] in await self?.readLoop() }
        pinger = Task { [weak self] in await self?.pingLoop() }
        return houseHello
    }

    private func handshakeTimedOut() async {
        guard transport == nil, closed == nil else { return }
        await finish(.timedOut)
    }

    private func fail(_ why: AwayClosed) async throws -> Never {
        await finish(why)
        throw closed ?? why
    }

    /// The next whole frame, reading as much as it takes.
    private func nextFrame() async throws -> Data {
        while true {
            if let closed { throw closed }
            if let frame = try frames.next() { return frame }
            let chunk: Data
            do {
                chunk = try await stream.read()
            } catch {
                throw closed ?? AwayClosed.lost
            }
            if chunk.isEmpty { throw closed ?? AwayClosed.lost }
            frames.feed(chunk)
        }
    }

    // MARK: - writing

    private func write(_ plain: Data) async throws {
        guard closed == nil, transport != nil else { throw closed ?? AwayClosed.lost }
        let data = try AwayWire.frame(try transport!.encrypt(plain))
        lastSent = clock.now()
        let previous = writeChain
        let stream = self.stream
        let next = Task {
            _ = await previous?.result
            try await stream.write(data)
        }
        writeChain = next
        do {
            try await next.value
        } catch {
            await finish(closed ?? .lost)
            throw closed ?? AwayClosed.lost
        }
    }

    private func pingLoop() async {
        while closed == nil {
            let due = lastSent + pingEvery - clock.now()
            if due > 0 {
                do { try await clock.sleep(due) } catch { return }
                continue
            }
            do { try await write(try AwayWire.message(.ping, req: 0)) } catch { return }
        }
    }

    // MARK: - reading

    private func readLoop() async {
        while closed == nil {
            let frame: Data
            do {
                frame = try await nextFrame()
            } catch let c as AwayClosed {
                await finish(c)
                return
            } catch let e as AwayWireError {
                await finish(.protocolError(e.reason))
                return
            } catch {
                await finish(.lost)
                return
            }
            do {
                guard var t = transport else { return }
                let plain = try t.decrypt(frame)
                transport = t
                try await handle(try AwayWire.houseMessage(plain))
            } catch let e as AwayWireError {
                await finish(.protocolError(e.reason))
                return
            } catch {
                await finish(.protocolError("\(error)"))
                return
            }
        }
    }

    private func handle(_ m: AwayMessage) async throws {
        switch m.kind {
        case .pong:
            return
        case .houseBye:
            let code = (try? AwayWire.houseObject(m.body))?["code"] as? String ?? ""
            await finish(.bye(code))
            return
        default:
            break
        }
        // A message for a request this phone no longer holds (one it gave up
        // on locally) is read and dropped.
        guard let p = pending[m.req] else { return }
        switch m.kind {
        case .resp:
            p.respParts.append(m.body)
            guard p.respParts.count <= AwayWire.maxResponse else { throw AwayWireError("a RESP over 1 MiB") }
            if m.more { return }
            let body = p.respParts
            p.respParts = Data()
            let object = try AwayWire.houseObject(body)
            p.answered = true
            p.continuation.yield(.resp(body))
            if !Self.eventsFollow(p.request, object) { end(m.req, nil) }
        case .event:
            let object = try AwayWire.houseObject(m.body)
            p.continuation.yield(.event(m.body))
            let type = object["type"] as? String
            if type == "done" || type == "error" { end(m.req, nil) }
        case .err:
            let object = (try? AwayWire.houseObject(m.body)) ?? [:]
            let retry = (object["retry_after"] as? NSNumber).map { $0.intValue }
            p.refused = true
            end(m.req, AwayErr(code: object["code"] as? String ?? "internal",
                               detail: object["detail"] as? String ?? "", retryAfter: retry))
        default:
            throw AwayWireError("a phone kind from the house")
        }
    }

    /// Whether `EVENT`s follow this `RESP` (loopback.py's `_streams`): every
    /// `chat.send` (§8 item 3: a repeated `client_id` too), and a
    /// `chat.stream` that found a reply running.
    static func eventsFollow(_ request: AwayRequest, _ resp: [String: Any]) -> Bool {
        switch request {
        case .send: return true
        case .stream: return resp["streaming"] as? Bool == true
        default: return false
        }
    }

    private func end(_ req: UInt32, _ error: (any Error)?) {
        guard let p = pending.removeValue(forKey: req) else { return }
        if let error { p.continuation.finish(throwing: error) } else { p.continuation.finish() }
        if !slotWaiters.isEmpty { slotWaiters.removeFirst().resume() }
    }

    // MARK: - requests

    /// Send one request (and its picture), and read its answer: the `RESP`,
    /// then the `EVENT`s that follow it. The stream throws the house's
    /// ``AwayErr`` for an `ERR`, or ``AwayClosed`` when the connection ends
    /// first.
    ///
    /// At most four requests are open on a connection (§4.6); a fifth waits
    /// here for one to end. A second `chat.send` while one is running is
    /// refused here with `busy`, as the house would.
    public func request(_ request: AwayRequest, picture: Data? = nil) async throws -> AsyncThrowingStream<AwayAnswer, Error> {
        if let closed { throw closed }
        guard transport != nil else { throw AwayClosed.lost }
        let json = try request.json()
        if case .send(_, _, _, let declared, _) = request {
            guard (declared == nil) == (picture == nil), declared?.size ?? 0 == picture?.count ?? 0 else {
                throw AwayWireError("the picture and its declaration disagree")
            }
            if pending.values.contains(where: { $0.request.isSend }) { throw AwayErr.sendRunning }
        } else if picture != nil {
            throw AwayWireError("only chat.send carries a picture")
        }
        while pending.count >= AwayWire.maxOpenRequests {
            try await withCheckedThrowingContinuation { slotWaiters.append($0) }
            if let closed { throw closed }
        }
        let req = takeReq()
        let (answers, continuation) = AsyncThrowingStream<AwayAnswer, Error>.makeStream()
        let p = Pending(request, continuation)
        pending[req] = p
        for message in try AwayWire.requestMessages(req: req, json: json) {
            try await write(message)
        }
        if let picture {
            // §8 item 7: once the house refused the request, its pieces stop.
            for piece in try AwayWire.blobMessages(req: req, picture: picture) {
                if p.refused || closed != nil { break }
                try await write(piece)
            }
        }
        return answers
    }

    private func takeReq() -> UInt32 {
        repeat {
            nextReq = nextReq == UInt32.max ? 1 : nextReq + 1
        } while pending[nextReq] != nil
        return nextReq
    }

    // MARK: - closing

    /// Say goodbye and close the stream.
    public func close() async {
        guard closed == nil else { return }
        if transport != nil {
            try? await write(try AwayWire.message(.bye, req: 0))
        }
        await finish(.closedHere)
    }

    private func finish(_ why: AwayClosed) async {
        guard closed == nil else { return }
        closed = why
        pinger?.cancel()
        for (req, _) in pending { end(req, why) }
        for waiter in slotWaiters { waiter.resume(throwing: why) }
        slotWaiters = []
        let sink = onClose
        onClose = nil
        sink?(why)
        await stream.close()
    }
}
