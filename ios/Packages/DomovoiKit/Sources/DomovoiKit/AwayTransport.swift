import Darwin
import Foundation
#if DEBUG
import Network
#endif

// ===========================================================================
// The seam the Tor engine plugs into (away chat, 1.5.0).
// ===========================================================================
//
// Away chat reaches the house over one TCP stream to its away onion, with
// Tor's client authorization (AWAY-CHAT-WIRE §2.1). The iPhone's Tor is Arti,
// from the shared Rust crate `domovoi_tor` (the 2026-11-27 spike), linked in
// once it lands; everything above this file speaks to ``AwayTransport`` and
// nothing else:
// the Noise handshake, the wire, the chat client and the controller that
// decides when it runs. The engine's whole job is
//
//     open(onion:port, client-auth key) -> a byte stream
//     stop()                            -> Tor stops, its streams close
//
// and the app builds one in exactly one place, ``AwayTransports/forApp()``.
// Today that is ``TorAwayTransport``, which says "not available yet" and
// opens nothing. The crate drops in as an ``AwayTorEngine`` (its three C
// calls) under ``EngineAwayTransport``, whose streams are the fds it hands
// back (``FdAwayByteStream``). ``DirectAwayTransport`` (plain TCP to a fixed host and port,
// the client-auth key unused) exists for the tests and for a debug build
// against a loopback house, and is compiled only in DEBUG, so a Release build
// cannot reach it.
//
// Lifetime is not the engine's business: ``AwayChatController`` starts it only
// while the chat screen is open and the phone is off the home network, and
// stops it shortly after the chat closes or the app leaves the screen.

/// One byte stream to the house's away onion (one TCP stream through Tor).
public protocol AwayByteStream: Sendable {
    /// The next bytes that arrived. **Empty at the end of the stream** (the
    /// house closed it); throws when it broke.
    func read() async throws -> Data
    /// Send these bytes, in call order. Returns once they were taken.
    func write(_ data: Data) async throws
    /// Close it. Closing twice is harmless.
    func close() async
}

/// Where the house's away onion is (the enrolment's answer).
public struct AwayEndpoint: Equatable, Sendable {
    public let onion: String
    public let port: Int

    public init(onion: String, port: Int) {
        self.onion = onion
        self.port = port
    }
}

/// The private half of the phone's Tor client-authorization key (X25519).
/// Its description never prints the key.
public struct AwayClientAuthKey: Equatable, Sendable, CustomStringConvertible {
    public let privateKey: Data

    public init(privateKey: Data) { self.privateKey = privateKey }

    /// Tor's `.auth_private` line for `onion`
    /// (`<address without .onion>:descriptor:x25519:<base32 private key>`),
    /// the form C tor's `ClientOnionAuthDir` reads and Arti's keystore
    /// imports. For the engine; never logged.
    public func authPrivateLine(onion: String) -> String {
        let address = onion.hasSuffix(".onion") ? String(onion.dropLast(6)) : onion
        return "\(address):descriptor:x25519:\(AwayKeyCodec.base32(privateKey))"
    }

    public var description: String { "AwayClientAuthKey(…)" }
}

/// Why a transport could not open a stream.
public enum AwayTransportError: Error, Equatable, Sendable {
    /// This build has no engine that can reach an onion.
    case notAvailable
    /// The engine ran, and the onion (or the host) could not be reached.
    case unreachable(String)
}

/// A Tor engine (or a test's stand-in) that opens streams to an onion.
public protocol AwayTransport: AnyObject, Sendable {
    /// Whether this engine can open streams in this build.
    var available: Bool { get }
    /// Open one stream to `endpoint`, presenting `clientAuth` for it. Starts
    /// the engine first when it isn't running. Throws
    /// ``AwayTransportError``.
    func open(_ endpoint: AwayEndpoint, clientAuth: AwayClientAuthKey) async throws -> any AwayByteStream
    /// Stop the engine: every stream it opened closes. Called when the chat
    /// screen has been closed for a little while, when the phone gets home
    /// and when the app leaves the screen. Stopping twice is harmless.
    func stop() async
}

/// The iPhone's Tor engine until the shared Rust crate `domovoi_tor` (Arti,
/// chosen by the 2026-11-27 spike) is linked in: it opens nothing and says
/// so; the chat screen shows "Can't reach your house." and Settings says this
/// build can't reach the house from away yet.
public final class TorAwayTransport: AwayTransport {
    public init() {}

    public var available: Bool { false }

    public func open(_ endpoint: AwayEndpoint, clientAuth: AwayClientAuthKey) async throws -> any AwayByteStream {
        throw AwayTransportError.notAvailable
    }

    public func stop() async {}
}

// MARK: - the Arti engine's seam

/// The shared crate's C API (`native/domovoi-tor/`, `domovoi_tor.h`), as
/// Swift sees it: **this is the protocol the crate's binding implements**.
///
///     int32_t dtor_start(const char *state, const char *cache, const char *auth);
///     int32_t dtor_open(const char *onion, int32_t port);   // fd, or -1
///     void    dtor_stop(void);
///
/// `start` bootstraps Tor and blocks until it is ready (0) or failed (not 0);
/// it reads client-authorization keys from `authDir`, where
/// ``AwayTorDirectories/writeAuth(onion:key:)`` has put this house's.
/// `open` hands back one end of a Unix socket pair whose other end the crate
/// pipes to a Tor stream to `onion:port`; the caller owns that fd
/// (``FdAwayByteStream`` closes it). `stop` shuts Tor down and ends every
/// stream. All three may block, so they are never called on the main actor.
public protocol AwayTorEngine: Sendable {
    func start(stateDir: String, cacheDir: String, authDir: String) -> Int32
    func open(onion: String, port: Int32) -> Int32
    func stop()
}

/// Where Tor keeps its state, its cache and the client-authorization keys:
/// under Application Support, each folder `0700`. State and cache are kept
/// between chat sessions (a warm start is much faster); the auth key file is
/// written when Tor starts and deleted when it stops, so the private key
/// rests on disk only while the chat runs (the Keychain holds it otherwise).
public struct AwayTorDirectories: Sendable {
    public let root: URL

    public init(root: URL) { self.root = root }

    /// The app's: `Application Support/domovoi-tor`.
    public static func forApp() -> AwayTorDirectories? {
        guard let support = try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                          appropriateFor: nil, create: true) else { return nil }
        return AwayTorDirectories(root: support.appendingPathComponent("domovoi-tor", isDirectory: true))
    }

    public var state: URL { root.appendingPathComponent("state", isDirectory: true) }
    public var cache: URL { root.appendingPathComponent("cache", isDirectory: true) }
    public var auth: URL { root.appendingPathComponent("auth", isDirectory: true) }

    /// The three folders, `0700`.
    public func make() throws {
        let fm = FileManager.default
        for dir in [root, state, cache, auth] {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
        }
    }

    /// The file name for `onion`: `<56 characters>.auth_private`.
    public static func authFileName(onion: String) -> String {
        let address = onion.hasSuffix(".onion") ? String(onion.dropLast(6)) : onion
        return "\(address).auth_private"
    }

    /// Write this house's client-authorization key where Tor reads it
    /// (`<onion56>:descriptor:x25519:<base32 private key>` and a newline,
    /// `0600`, in the `0700` auth folder). Replaces any other.
    public func writeAuth(onion: String, key: AwayClientAuthKey) throws {
        try make()
        removeAuth()
        let file = auth.appendingPathComponent(Self.authFileName(onion: onion))
        let line = Data((key.authPrivateLine(onion: onion) + "\n").utf8)
        guard FileManager.default.createFile(atPath: file.path, contents: line, attributes: [.posixPermissions: 0o600]) else {
            throw AwayTransportError.unreachable("couldn't write the client-authorization key")
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }

    /// Delete every key file (Tor stopped, away chat off, the server forgotten).
    public func removeAuth() {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: auth.path) else { return }
        for name in names where name.hasSuffix(".auth_private") {
            try? fm.removeItem(at: auth.appendingPathComponent(name))
        }
    }
}

/// An ``AwayTransport`` over an ``AwayTorEngine``: Tor starts at the first
/// `open` (with the folders and the key file the controller wrote) and stops
/// at ``stop()``; each stream is the fd the engine hands back.
public final class EngineAwayTransport: AwayTransport, @unchecked Sendable {
    // @unchecked: `started` is behind `lock`; the engine and the folders are
    // Sendable.
    private let engine: any AwayTorEngine
    private let directories: AwayTorDirectories
    private let lock = NSLock()
    private var started = false

    public init(engine: any AwayTorEngine, directories: AwayTorDirectories) {
        self.engine = engine
        self.directories = directories
    }

    public var available: Bool { true }

    public func open(_ endpoint: AwayEndpoint, clientAuth: AwayClientAuthKey) async throws -> any AwayByteStream {
        let engine = engine
        let dirs = directories
        if !isStarted {
            try dirs.make()
            let rc = await Self.offMain {
                engine.start(stateDir: dirs.state.path, cacheDir: dirs.cache.path, authDir: dirs.auth.path)
            }
            guard rc == 0 else { throw AwayTransportError.unreachable("Tor didn't start (\(rc))") }
            setStarted(true)
        }
        let fd = await Self.offMain { engine.open(onion: endpoint.onion, port: Int32(endpoint.port)) }
        guard fd >= 0 else { throw AwayTransportError.unreachable("the onion couldn't be reached") }
        return FdAwayByteStream(fileDescriptor: fd)
    }

    public func stop() async {
        guard isStarted else { return }
        setStarted(false)
        let engine = engine
        await Self.offMain { engine.stop() }
    }

    private var isStarted: Bool { lock.lock(); defer { lock.unlock() }; return started }
    private func setStarted(_ v: Bool) { lock.lock(); started = v; lock.unlock() }

    /// Run a blocking engine call on a thread of its own.
    static func offMain<T: Sendable>(_ work: @Sendable @escaping () -> T) async -> T {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async { continuation.resume(returning: work()) }
        }
    }
}

/// A stream socket's file descriptor (the engine's end of its socket pair)
/// as an ``AwayByteStream``: blocking reads and writes, each on a queue of
/// its own. It owns the fd: ``close()`` shuts the socket down (waking a
/// blocked read) and the fd is closed once nothing can still be using it.
public final class FdAwayByteStream: AwayByteStream, @unchecked Sendable {
    // @unchecked: `fd` is a let; `closed` and `fdClosed` are behind `lock`.
    private let fd: Int32
    private let readQueue = DispatchQueue(label: "org.codersfarm.domovoi.away-fd.read")
    private let writeQueue = DispatchQueue(label: "org.codersfarm.domovoi.away-fd.write")
    private let lock = NSLock()
    private var closed = false
    private var fdClosed = false

    public init(fileDescriptor: Int32) {
        fd = fileDescriptor
        // A write after the other end went away is an error here, never a
        // SIGPIPE that ends the app.
        var on: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
    }

    deinit { closeFd() }

    private var isClosed: Bool { lock.lock(); defer { lock.unlock() }; return closed }

    public func read() async throws -> Data {
        let fd = fd
        return try await withCheckedThrowingContinuation { continuation in
            readQueue.async { [self] in
                var buffer = [UInt8](repeating: 0, count: 65_536)
                while true {
                    let n = Darwin.read(fd, &buffer, buffer.count)
                    if n > 0 { return continuation.resume(returning: Data(buffer[0..<n])) }
                    if n == 0 || isClosed { return continuation.resume(returning: Data()) }
                    if errno == EINTR { continue }
                    if errno == ECONNRESET { return continuation.resume(returning: Data()) }
                    return continuation.resume(throwing: AwayTransportError.unreachable("read failed (\(errno))"))
                }
            }
        }
    }

    public func write(_ data: Data) async throws {
        let fd = fd
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            writeQueue.async { [self] in
                let bytes = [UInt8](data)
                var sent = 0
                while sent < bytes.count {
                    if isClosed { return continuation.resume(throwing: AwayTransportError.unreachable("closed")) }
                    let n = bytes.withUnsafeBufferPointer { Darwin.write(fd, $0.baseAddress! + sent, bytes.count - sent) }
                    if n > 0 { sent += n; continue }
                    if n < 0 && errno == EINTR { continue }
                    return continuation.resume(throwing: AwayTransportError.unreachable("write failed (\(errno))"))
                }
                continuation.resume()
            }
        }
    }

    private func markClosed() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let first = !closed
        closed = true
        return first
    }

    public func close() async {
        guard markClosed() else { return }
        _ = shutdown(fd, SHUT_RDWR)
        // Once the queues have let go of it, the fd itself goes.
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            readQueue.async { [self] in writeQueue.async { [self] in closeFd(); c.resume() } }
        }
    }

    private func closeFd() {
        lock.lock()
        defer { lock.unlock() }
        guard !fdClosed else { return }
        fdClosed = true
        Darwin.close(fd)
    }
}

/// Which engine the app uses: the one place it is built. When the
/// `domovoi_tor` binding lands this becomes
/// `EngineAwayTransport(engine: DomovoiTorEngine(), directories: dirs)`.
public enum AwayTransports {
    public static func forApp() -> any AwayTransport { TorAwayTransport() }
}

#if DEBUG
/// Plain TCP to a fixed host and port, for the tests and for a debug build
/// talking to a loopback house (`ios/scripts/away_loopback_house.py`). The
/// onion and the client-auth key are ignored. **DEBUG only**: a Release build
/// has no way to reach a house except through ``AwayTransports/forApp()``.
public final class DirectAwayTransport: AwayTransport {
    private let host: String
    private let port: UInt16

    public init(host: String, port: UInt16) {
        self.host = host
        self.port = port
    }

    public var available: Bool { true }

    public func open(_ endpoint: AwayEndpoint, clientAuth: AwayClientAuthKey) async throws -> any AwayByteStream {
        guard let p = NWEndpoint.Port(rawValue: port) else { throw AwayTransportError.unreachable("bad port") }
        let connection = NWConnection(host: NWEndpoint.Host(host), port: p, using: .tcp)
        let stream = NWAwayByteStream(connection)
        try await stream.start()
        return stream
    }

    public func stop() async {}
}

/// An `NWConnection` as an ``AwayByteStream``.
final class NWAwayByteStream: AwayByteStream, @unchecked Sendable {
    // @unchecked: `connection` is a thread-safe Network object; `queue` is a
    // let; nothing else is stored.
    private let connection: NWConnection
    private let queue = DispatchQueue(label: "org.codersfarm.domovoi.away-direct")

    init(_ connection: NWConnection) { self.connection = connection }

    func start() async throws {
        let once = OnceFlag()
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    if once.take() { continuation.resume() }
                case .failed(let error), .waiting(let error):
                    if once.take() { continuation.resume(throwing: AwayTransportError.unreachable("\(error)")) }
                case .cancelled:
                    if once.take() { continuation.resume(throwing: AwayTransportError.unreachable("cancelled")) }
                default:
                    break
                }
            }
            connection.start(queue: queue)
        }
    }

    func read() async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { data, _, complete, error in
                if let data, !data.isEmpty {
                    continuation.resume(returning: data)
                } else if complete {
                    continuation.resume(returning: Data())
                } else if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: Data())
                }
            }
        }
    }

    func write(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error { continuation.resume(throwing: error) } else { continuation.resume() }
            })
        }
    }

    func close() async { connection.cancel() }
}

/// A flag that is taken once, from any thread.
final class OnceFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var taken = false

    func take() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if taken { return false }
        taken = true
        return true
    }
}
#endif
