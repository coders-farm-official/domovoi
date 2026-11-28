import Foundation

// ===========================================================================
// Away chat's five requests, typed for the chat screen (1.5.0, §4), over a
// session that keeps one connection open while it is wanted.
// ===========================================================================

/// What the chat screen needs from away chat: the five requests, in the
/// shapes the home chat already reads (``ChatThreadList``,
/// ``ChatMessageList``, ``ServerEvent``), so the same screens draw both.
public protocol ChatAwayLink: AnyObject, Sendable {
    /// Every thread this phone may see (`chat.list`, all pages).
    func threads(archived: Bool) async throws -> ChatThreadList
    /// The newest messages of one chat (`chat.history`).
    func messages(_ chatID: Int64) async throws -> ChatMessageList
    /// Send one message (`chat.send`): the stored user turn, then the reply's
    /// events, as the home stream names them.
    func send(chatID: Int64?, clientID: String, text: String, picture: AwayPicture?) async throws -> AwaySendStart
    /// Pick up a reply still being written in `chatID` (`chat.stream`): nil
    /// when none is running.
    func resume(_ chatID: Int64) async throws -> AsyncThrowingStream<ServerEvent, Error>?
    /// Stop this phone's running reply in `chatID` (`chat.cancel`).
    func cancel(_ chatID: Int64) async throws -> Bool
}

/// A send the house has stored: its chat (new or not), the user turn's id,
/// and the reply's events.
public struct AwaySendStart: Sendable {
    public let chatID: Int64
    public let messageID: Int64
    public let events: AsyncThrowingStream<ServerEvent, Error>

    public init(chatID: Int64, messageID: Int64, events: AsyncThrowingStream<ServerEvent, Error>) {
        self.chatID = chatID
        self.messageID = messageID
        self.events = events
    }
}

/// Keeps one connection to the house open while it is wanted, opening a
/// fresh one when the last one ended (an idle close, a replaced one, a
/// dropped stream). The controller decides when it may run at all.
public actor AwaySession {

    private let enrolment: AwayEnrolment
    private let transport: any AwayTransport
    private let version: String
    private let clock: AwayClock
    private let handshakeTimeout: Double
    private let closedSink: @Sendable (AwayClosed) -> Void
    private var current: AwayConnection?
    private var opening: Task<AwayConnection, Error>?
    private var stopped = false

    public init(
        enrolment: AwayEnrolment,
        transport: any AwayTransport,
        version: String,
        clock: AwayClock = .system,
        handshakeTimeout: Double = AwayConnection.handshakeTimeoutSec,
        closed: @Sendable @escaping (AwayClosed) -> Void = { _ in }
    ) {
        self.enrolment = enrolment
        self.transport = transport
        self.version = version
        self.clock = clock
        self.handshakeTimeout = handshakeTimeout
        self.closedSink = closed
    }

    /// The open connection, or a new one.
    public func connection() async throws -> AwayConnection {
        if stopped { throw AwayClosed.closedHere }
        if let current, await current.isOpen { return current }
        if let opening { return try await opening.value }
        let task = Task { [enrolment, transport, version, clock, handshakeTimeout, closedSink] () throws -> AwayConnection in
            let stream = try await transport.open(enrolment.endpoint, clientAuth: enrolment.clientAuth)
            let connection = AwayConnection(stream: stream, phoneKey: try enrolment.noiseKey(),
                                            housePublicKey: enrolment.houseNoisePublic, version: version, clock: clock)
            await connection.onClose(closedSink)
            _ = try await connection.open(timeout: handshakeTimeout)
            return connection
        }
        opening = task
        defer { opening = nil }
        let connection = try await task.value
        if stopped {
            await connection.close()
            throw AwayClosed.closedHere
        }
        current = connection
        return connection
    }

    /// Close the connection and stop the engine. The session opens nothing
    /// after this.
    public func stop() async {
        stopped = true
        opening?.cancel()
        if let current { await current.close() }
        current = nil
        await transport.stop()
    }
}

/// The five requests over an ``AwaySession``.
public final class AwayChatClient: ChatAwayLink {

    /// How many `chat.list` pages are read for one list (50 each).
    static let maxListPages = 10
    /// How many `chat.history` pages are read for one chat.
    static let maxHistoryPages = 4

    private let session: AwaySession

    public init(session: AwaySession) { self.session = session }

    // MARK: answers

    private func answers(_ request: AwayRequest, picture: Data? = nil) async throws -> AsyncThrowingStream<AwayAnswer, Error> {
        let connection = try await session.connection()
        return try await connection.request(request, picture: picture)
    }

    /// A request that ends with its `RESP`.
    private func resp(_ request: AwayRequest) async throws -> [String: Any] {
        for try await answer in try await answers(request) {
            if case .resp(let body) = answer { return try AwayWire.houseObject(body) }
        }
        throw AwayClosed.lost
    }

    private static func data(_ object: Any) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }

    // MARK: ChatAwayLink

    public func threads(archived: Bool) async throws -> ChatThreadList {
        var all: [Any] = []
        var cursor: String?
        for _ in 0..<Self.maxListPages {
            let page = try await resp(.list(archived: archived, limit: AwayWire.maxLimit, cursor: cursor))
            all += page["threads"] as? [Any] ?? []
            guard let next = page["next"] as? String, !next.isEmpty else { break }
            cursor = next
        }
        return try DomovoiClient.decode(try Self.data(["threads": all]), as: ChatThreadList.self)
    }

    public func messages(_ chatID: Int64) async throws -> ChatMessageList {
        var pages: [[Any]] = []
        var thread: Any?
        var before: Int64?
        for _ in 0..<Self.maxHistoryPages {
            let page = try await resp(.history(chatID: chatID, beforeID: before, limit: AwayWire.maxLimit))
            thread = thread ?? page["thread"]
            let messages = page["messages"] as? [Any] ?? []
            pages.insert(messages, at: 0)
            guard page["more"] as? Bool == true,
                  let oldest = (messages.first as? [String: Any])?["id"] as? NSNumber else { break }
            before = oldest.int64Value
        }
        var body: [String: Any] = ["messages": pages.flatMap { $0 }]
        if let thread, !(thread is NSNull) { body["thread"] = thread }
        return try DomovoiClient.decode(try Self.data(body), as: ChatMessageList.self)
    }

    public func send(chatID: Int64?, clientID: String, text: String, picture: AwayPicture?) async throws -> AwaySendStart {
        let declared = picture.map { AwayPictureDeclaration(type: "jpeg", size: $0.jpeg.count, sha256: $0.sha256) }
        let stream = try await answers(.send(chatID: chatID, clientID: clientID, text: text, picture: declared, adminGrant: nil),
                                       picture: picture?.jpeg)
        let (body, events) = try await Self.split(stream)
        let resp = try AwayWire.houseObject(body)
        guard let chat = (resp["chat_id"] as? NSNumber)?.int64Value,
              let message = (resp["message_id"] as? NSNumber)?.int64Value else {
            throw AwayWireError("a send's answer without its ids")
        }
        return AwaySendStart(chatID: chat, messageID: message, events: events)
    }

    public func resume(_ chatID: Int64) async throws -> AsyncThrowingStream<ServerEvent, Error>? {
        let (body, events) = try await Self.split(try await answers(.stream(chatID: chatID)))
        guard try AwayWire.houseObject(body)["streaming"] as? Bool == true else { return nil }
        return events
    }

    public func cancel(_ chatID: Int64) async throws -> Bool {
        try await resp(.cancel(chatID: chatID))["cancelled"] as? Bool ?? false
    }

    // MARK: events

    /// A request's `RESP`, and the `EVENT`s after it as the home stream's
    /// events, one for one: `delta` and `error` as they are, `done` carrying
    /// its stored message (the home stream's `done` is the row itself).
    static func split(_ answers: AsyncThrowingStream<AwayAnswer, Error>) async throws -> (Data, AsyncThrowingStream<ServerEvent, Error>) {
        let (events, continuation) = AsyncThrowingStream<ServerEvent, Error>.makeStream()
        let first: Data = try await withCheckedThrowingContinuation { (resp: CheckedContinuation<Data, Error>) in
            let task = Task {
                var answered = false
                do {
                    for try await answer in answers {
                        switch answer {
                        case .resp(let body):
                            if !answered {
                                answered = true
                                resp.resume(returning: body)
                            }
                        case .event(let body):
                            if let event = serverEvent(body) { continuation.yield(event) }
                        }
                    }
                    if !answered { resp.resume(throwing: AwayClosed.lost) }
                    continuation.finish()
                } catch {
                    if !answered { resp.resume(throwing: error) }
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
        return (first, events)
    }

    static func serverEvent(_ body: Data) -> ServerEvent? {
        guard let object = try? AwayWire.houseObject(body), let type = object["type"] as? String else { return nil }
        switch type {
        case "done":
            let message = object["message"] as? [String: Any] ?? [:]
            guard let data = try? data(message) else { return nil }
            return ServerEvent(name: "done", data: String(decoding: data, as: UTF8.self))
        case "error":
            var out = object
            out.removeValue(forKey: "message")
            if (out["detail"] as? String ?? "").isEmpty {
                // §8 item 4: a too-large error comes without its words.
                out["detail"] = AwayStrings.somethingWrong
            }
            guard let data = try? data(out) else { return nil }
            return ServerEvent(name: "error", data: String(decoding: data, as: UTF8.self))
        default:
            return ServerEvent(name: type, data: String(decoding: body, as: UTF8.self))
        }
    }
}

/// The line the chat shows for anything away chat threw.
public func awayFailureLine(_ error: any Error, hadPicture: Bool = false) -> String {
    switch error {
    case let err as AwayErr: return AwayStrings.line(for: err, hadPicture: hadPicture)
    case let closed as AwayClosed: return AwayStrings.line(for: closed)
    case is AwayWireError: return hadPicture ? AwayStrings.pictureFailed : AwayStrings.somethingWrong
    case is DecodingError, is DomovoiError: return AwayStrings.somethingWrong
    default: return AwayStrings.unreachable
    }
}
