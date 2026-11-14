import Foundation
import Network

/// A request or a socket the room could not make, in words.
public struct RoomNetworkFailure: Error, Equatable, CustomStringConvertible {
    public let message: String
    /// The HTTP status when there was a response.
    public let status: Int?

    public init(_ message: String, status: Int? = nil) {
        self.message = message
        self.status = status
    }

    public var description: String { message }
}

/// A blocking GET: the body of a 200, or a ``RoomNetworkFailure`` (any other
/// status too). Never follows a redirect. `RoomModels.kt:496-514`, `get`.
public protocol RoomHTTP: Sendable {
    func get(_ url: String, limit: Int) throws -> Data
}

extension RoomHTTP {
    /// Four megabytes: a manifest, a health answer or a clip.
    public func get(_ url: String) throws -> Data { try get(url, limit: 4 * 1024 * 1024) }
}

/// The room's open socket.
public protocol RoomSocket: AnyObject, Sendable {
    /// False when the socket is already gone.
    @discardableResult func sendText(_ text: String) -> Bool
    @discardableResult func sendBinary(_ data: Data) -> Bool
    /// Closes it; no event is reported after this.
    func close()
}

/// What a room socket tells the engine; any thread.
public struct RoomSocketEvents: Sendable {
    public let opened: @Sendable () -> Void
    public let text: @Sendable (String) -> Void
    public let binary: @Sendable (Data) -> Void
    /// The socket failed (or the upgrade was refused): why.
    public let failed: @Sendable (String) -> Void
    /// The server closed it.
    public let closed: @Sendable () -> Void

    public init(
        opened: @escaping @Sendable () -> Void,
        text: @escaping @Sendable (String) -> Void,
        binary: @escaping @Sendable (Data) -> Void,
        failed: @escaping @Sendable (String) -> Void,
        closed: @escaping @Sendable () -> Void
    ) {
        self.opened = opened
        self.text = text
        self.binary = binary
        self.failed = failed
        self.closed = closed
    }
}

/// Opens the room's socket. Exactly one of `failed` / `closed` per socket,
/// and neither after ``RoomSocket/close()`` (the obligation
/// `DropInSocketTransport` carries; the shape is that one's, binary and
/// text, without its `Mutex` — ROOMMODE-MAP §6.0).
public protocol RoomSocketTransport: Sendable {
    func open(_ url: String, events: RoomSocketEvents) -> any RoomSocket
}

/// "Is there Wi-Fi (or a wired dock)?" — never the phone's mobile data.
public protocol RoomWifiWatching: AnyObject, Sendable {
    var available: Bool { get }
    /// Starts watching; `onChange` hears every change.
    func start(_ onChange: @escaping @Sendable (Bool) -> Void)
    func stop()
}

/// The room's way onto the network (`roommode/RoomNetwork.kt`, ROOMMODE-MAP
/// §3.9): Wi-Fi (or a wired adapter), never the phone's mobile data, so a
/// phone whose Wi-Fi drops goes offline instead of carrying the room's
/// microphone over cellular.
///
/// The room's session is NOT the app's: there is no household-token code path
/// at all (a room phone sends no household credential anywhere; this type
/// never sees `TokenVault`), it keeps the app's cleartext rule
/// (``CleartextPolicy``: plain http only on the home network), and it follows
/// NO redirect at all, HTTP or the WebSocket upgrade: the room talks to the
/// one core it proved, and a 3xx from it is an error, never a hop to another
/// host with the room's pairing key in the hello. Pings every 15 s, so a
/// half-open socket fails within ~30 s.
public final class RoomNetwork: NSObject, RoomHTTP, RoomSocketTransport, URLSessionWebSocketDelegate, @unchecked Sendable {

    /// Seconds to connect before a request is given up (OkHttp's 6 s).
    public static let connectTimeout: TimeInterval = 6
    /// Seconds of silence on a read before it fails (OkHttp's 30 s).
    public static let readTimeout: TimeInterval = 30
    /// Seconds between WebSocket pings; one unanswered ping by the next
    /// tick fails the socket.
    public static let pingInterval: TimeInterval = 15

    /// The room's configuration: the client's own (no cookies, no cache),
    /// and Wi-Fi only.
    public static func configuration() -> URLSessionConfiguration {
        let c = DomovoiClient.defaultConfiguration()
        c.allowsCellularAccess = false
        c.allowsExpensiveNetworkAccess = false
        c.waitsForConnectivity = false
        c.timeoutIntervalForRequest = readTimeout
        c.httpAdditionalHeaders = nil
        return c
    }

    private let links = RoomLock<[Int: RoomSocketLink]>([:])
    private let sessionHolder = RoomLock<URLSession?>(nil)
    private let delegateQueue: OperationQueue = {
        let q = OperationQueue()
        q.name = "room-network"
        q.maxConcurrentOperationCount = 1
        return q
    }()

    public init(configuration: URLSessionConfiguration = RoomNetwork.configuration()) {
        super.init()
        sessionHolder.withLock { $0 = URLSession(configuration: configuration, delegate: self, delegateQueue: delegateQueue) }
    }

    private var session: URLSession {
        sessionHolder.withLock { $0! }
    }

    /// Ends the session; every request and socket on it is cancelled.
    public func invalidate() {
        session.invalidateAndCancel()
    }

    // MARK: - the cleartext rule

    /// Refuses a URL the app's cleartext rule refuses (plain http/ws to a
    /// host off the home network), before anything leaves the phone.
    static func checkCleartext(_ url: URL) throws {
        guard let scheme = url.scheme?.lowercased() else { throw RoomNetworkFailure("\(url.absoluteString) is not an address") }
        switch scheme {
        case "https", "wss": return
        case "http", "ws":
            if !CleartextPolicy.cleartextPermitted(url.host) {
                throw RoomNetworkFailure(CleartextPolicy.refusalMessage(url.host ?? ""))
            }
        default:
            throw RoomNetworkFailure("\(url.absoluteString) is not an http address")
        }
    }

    // MARK: - RoomHTTP

    public func get(_ url: String, limit: Int) throws -> Data {
        guard let u = URL(string: url) else { throw RoomNetworkFailure("\(url) is not an address") }
        try Self.checkCleartext(u)
        var request = URLRequest(url: u)
        request.httpMethod = "GET"
        request.timeoutInterval = Self.readTimeout
        let done = DispatchSemaphore(value: 0)
        let answer = RoomLock<(Data?, URLResponse?, Error?)>((nil, nil, nil))
        let task = session.dataTask(with: request) { data, response, error in
            answer.withLock { $0 = (data, response, error) }
            done.signal()
        }
        task.resume()
        // The request's own timeout ends it; this is the backstop for a body
        // that keeps trickling in.
        if done.wait(timeout: .now() + 300) == .timedOut {
            task.cancel()
            throw RoomNetworkFailure("\(Self.withoutQuery(url)) took too long")
        }
        let (data, response, error) = answer.withLock { $0 }
        let short = Self.withoutQuery(url)
        if let http = response as? HTTPURLResponse {
            guard http.statusCode == 200 else {
                throw RoomNetworkFailure("\(http.statusCode) from \(short)", status: http.statusCode)
            }
        } else if let error {
            throw RoomNetworkFailure((error as NSError).localizedDescription)
        } else {
            throw RoomNetworkFailure("no answer from \(short)")
        }
        guard let data else { throw RoomNetworkFailure("no body from \(short)") }
        if data.count > limit { throw RoomNetworkFailure("\(short) is too large") }
        return data
    }

    static func withoutQuery(_ url: String) -> String {
        url.split(separator: "?", maxSplits: 1).first.map(String.init) ?? url
    }

    // MARK: - RoomSocketTransport

    public func open(_ url: String, events: RoomSocketEvents) -> any RoomSocket {
        guard let u = URL(string: url) else {
            return RoomDeadSocket(failing: events, why: "\(url) is not an address")
        }
        do {
            try Self.checkCleartext(u)
        } catch {
            return RoomDeadSocket(failing: events, why: RoomIdentity.describe(error))
        }
        var request = URLRequest(url: u)
        request.timeoutInterval = Self.connectTimeout + Self.readTimeout
        let task = session.webSocketTask(with: request)
        // Big enough for a burst of speech; the core sends 30-100 ms frames.
        task.maximumMessageSize = 1 << 20
        let link = RoomSocketLink(task: task, events: events) { [weak self] id in
            _ = self?.links.withLock { $0.removeValue(forKey: id) }
        }
        links.withLock { $0[task.taskIdentifier] = link }
        task.resume()
        link.run()
        return link
    }

    // MARK: - the session's delegate

    /// No redirect, ever: the 3xx itself is the answer (an error for a GET,
    /// a failed upgrade for the socket).
    public func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }

    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        let link = links.withLock { $0[webSocketTask.taskIdentifier] }
        link?.didOpen()
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let link = links.withLock { $0[task.taskIdentifier] }
        link?.didComplete()
    }
}

/// A socket that was never opened: it reports its failure once, soon.
final class RoomDeadSocket: RoomSocket, @unchecked Sendable {
    init(failing events: RoomSocketEvents, why: String) {
        DispatchQueue.global().async { events.failed(why) }
    }

    func sendText(_ text: String) -> Bool { false }
    func sendBinary(_ data: Data) -> Bool { false }
    func close() {}
}

/// One room socket over `URLSessionWebSocketTask`: its receive loop, its
/// pings, and the one-end-per-link rule.
///
/// `@unchecked Sendable`: the mutable state is behind ``state`` (a
/// ``RoomLock``), `task` and `events` are `let`, and
/// `URLSessionWebSocketTask` is documented thread-safe for `send`,
/// `receive`, `sendPing` and `cancel`.
final class RoomSocketLink: RoomSocket, @unchecked Sendable {

    private struct State {
        var opened = false
        var finished = false
        var awaitingPong = false
        var timer: DispatchSourceTimer?
    }

    private let task: URLSessionWebSocketTask
    private let events: RoomSocketEvents
    private let forget: @Sendable (Int) -> Void
    private let state = RoomLock(State())
    private let queue = DispatchQueue(label: "room-socket-ping")

    init(task: URLSessionWebSocketTask, events: RoomSocketEvents, forget: @escaping @Sendable (Int) -> Void) {
        self.task = task
        self.events = events
        self.forget = forget
    }

    func run() {
        receiveNext()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        let every = DispatchTimeInterval.milliseconds(Int(RoomNetwork.pingInterval * 1000))
        timer.schedule(deadline: .now() + every, repeating: every)
        timer.setEventHandler { [weak self] in self?.tick() }
        let keep = state.withLock { s -> Bool in
            guard !s.finished else { return false }
            s.timer = timer
            return true
        }
        if keep { timer.resume() }
    }

    func didOpen() {
        let first = state.withLock { s -> Bool in
            guard !s.finished, !s.opened else { return false }
            s.opened = true
            return true
        }
        if first { events.opened() }
    }

    /// The task ended (an upgrade refused, a dropped connection): the
    /// receive loop reports it; this only covers a task that ends before a
    /// receive was ever answered.
    func didComplete() {
        ended()
    }

    private func receiveNext() {
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                self.didOpen()
                switch message {
                case .string(let text): self.events.text(text)
                case .data(let data): self.events.binary(data)
                @unknown default: break
                }
                if !self.state.withLock({ $0.finished }) { self.receiveNext() }
            case .failure:
                self.ended()
            }
        }
    }

    private func tick() {
        let missed = state.withLock { s -> Bool? in
            guard !s.finished, s.opened else { return nil }
            if s.awaitingPong { return true }
            s.awaitingPong = true
            return false
        }
        guard let missed else { return }
        if missed {
            fail("the server stopped answering pings")
            return
        }
        task.sendPing { [weak self] error in
            guard let self else { return }
            if error != nil {
                self.fail("the connection dropped")
            } else {
                self.state.withLock { $0.awaitingPong = false }
            }
        }
    }

    func sendText(_ text: String) -> Bool {
        guard !state.withLock({ $0.finished }) else { return false }
        task.send(.string(text)) { [weak self] error in
            if error != nil { self?.fail("a send failed") }
        }
        return true
    }

    func sendBinary(_ data: Data) -> Bool {
        guard !state.withLock({ $0.finished }) else { return false }
        task.send(.data(data)) { [weak self] error in
            if error != nil { self?.fail("a send failed") }
        }
        return true
    }

    func close() {
        guard claim() else { return }
        task.cancel(with: .normalClosure, reason: Data("room mode stopped".utf8))
    }

    private func ended() {
        if task.closeCode != .invalid {
            guard claim() else { return }
            task.cancel(with: .goingAway, reason: nil)
            events.closed()
            return
        }
        if let http = task.response as? HTTPURLResponse, http.statusCode != 101 {
            fail("the server answered \(http.statusCode)")
        } else if let error = task.error {
            fail((error as NSError).localizedDescription)
        } else {
            fail("the connection dropped")
        }
    }

    private func fail(_ why: String) {
        guard claim() else { return }
        task.cancel(with: .goingAway, reason: nil)
        events.failed(why)
    }

    /// One end per link.
    private func claim() -> Bool {
        let timer = state.withLock { s -> DispatchSourceTimer?? in
            guard !s.finished else { return .none }
            s.finished = true
            let t = s.timer
            s.timer = nil
            return .some(t)
        }
        guard let t = timer else { return false }
        t?.cancel()
        forget(task.taskIdentifier)
        return true
    }
}

/// Wi-Fi or a wired adapter, from `NWPathMonitor` (ROOMMODE-MAP §3.9): a
/// home network with no internet is still the home network.
///
/// In the Simulator the Mac's own network counts (it has no Wi-Fi of its
/// own to report); a phone is held to Wi-Fi or wired.
public final class RoomWifiWatch: RoomWifiWatching, @unchecked Sendable {

    private struct State {
        var wifi = false
        var wired = false
        var any = false
        var monitors: [NWPathMonitor] = []
        var onChange: (@Sendable (Bool) -> Void)?
    }

    private let state = RoomLock(State())
    private let queue = DispatchQueue(label: "room-wifi")

    public init() {}

    public var available: Bool {
        state.withLock { s in
            #if targetEnvironment(simulator)
            return s.wifi || s.wired || s.any
            #else
            return s.wifi || s.wired
            #endif
        }
    }

    public func start(_ onChange: @escaping @Sendable (Bool) -> Void) {
        stop()
        let wifi = NWPathMonitor(requiredInterfaceType: .wifi)
        let wired = NWPathMonitor(requiredInterfaceType: .wiredEthernet)
        let any = NWPathMonitor()
        state.withLock {
            $0.monitors = [wifi, wired, any]
            $0.onChange = onChange
        }
        wifi.pathUpdateHandler = { [weak self] path in self?.set(.wifi, path.status == .satisfied) }
        wired.pathUpdateHandler = { [weak self] path in self?.set(.wired, path.status == .satisfied) }
        any.pathUpdateHandler = { [weak self] path in self?.set(.any, path.status == .satisfied) }
        wifi.start(queue: queue)
        wired.start(queue: queue)
        any.start(queue: queue)
    }

    public func stop() {
        let monitors = state.withLock { s -> [NWPathMonitor] in
            let m = s.monitors
            s.monitors = []
            s.onChange = nil
            s.wifi = false
            s.wired = false
            s.any = false
            return m
        }
        for m in monitors { m.cancel() }
    }

    private enum Kind { case wifi, wired, any }

    private func set(_ kind: Kind, _ value: Bool) {
        let before = available
        let callback = state.withLock { s -> (@Sendable (Bool) -> Void)? in
            switch kind {
            case .wifi: s.wifi = value
            case .wired: s.wired = value
            case .any: s.any = value
            }
            return s.onChange
        }
        let after = available
        if before != after { callback?(after) }
    }
}
