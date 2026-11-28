import Darwin
import Foundation
import XCTest
@testable import DomovoiKit

/// The Tor engine's seam: an fd-backed stream over a real socket pair, the
/// engine transport over a fake engine, and Tor's folders and key file.
final class AwayTransportTests: XCTestCase {

    private func socketPair() throws -> (Int32, Int32) {
        var fds: [Int32] = [0, 0]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &fds) == 0 else { throw POSIXError(.EIO) }
        return (fds[0], fds[1])
    }

    func testAnFdStreamReadsAndWritesASocketPair() async throws {
        let (mine, theirs) = try socketPair()
        let stream = FdAwayByteStream(fileDescriptor: mine)
        let hello = Data("hello".utf8)
        XCTAssertEqual(hello.withUnsafeBytes { Darwin.write(theirs, $0.baseAddress, $0.count) }, 5)
        let got = try await stream.read()
        XCTAssertEqual(got, hello)
        let big = Data((0..<300_000).map { UInt8($0 % 253) })
        let reader = Task.detached { () -> Data in
            var out = Data()
            var buffer = [UInt8](repeating: 0, count: 65_536)
            while out.count < big.count {
                let n = Darwin.read(theirs, &buffer, buffer.count)
                if n <= 0 { break }
                out.append(contentsOf: buffer[0..<n])
            }
            return out
        }
        try await stream.write(big)
        let echoed = await reader.value
        XCTAssertEqual(echoed, big)
        // The other end closes: the end of the stream is an empty read.
        Darwin.close(theirs)
        let end = try await stream.read()
        XCTAssertEqual(end, Data())
        await stream.close()
        await stream.close()
    }

    func testClosingWakesABlockedReadAndTheOtherEndSeesTheEnd() async throws {
        let (mine, theirs) = try socketPair()
        let stream = FdAwayByteStream(fileDescriptor: mine)
        let pending = Task { try await stream.read() }
        try await Task.sleep(for: .milliseconds(50))
        await stream.close()
        let woke = try await pending.value
        XCTAssertEqual(woke, Data())
        var byte: UInt8 = 0
        XCTAssertEqual(Darwin.read(theirs, &byte, 1), 0)
        Darwin.close(theirs)
    }

    func testAWriteAfterTheOtherEndWentIsAnErrorNotASignal() async throws {
        let (mine, theirs) = try socketPair()
        let stream = FdAwayByteStream(fileDescriptor: mine)
        Darwin.close(theirs)
        do {
            for _ in 0..<4 { try await stream.write(Data(count: 70_000)) }
            XCTFail("wrote")
        } catch {
            // AwayTransportError.unreachable, and the process is still here.
        }
        await stream.close()
    }

    /// The whole wire over a socket pair: the phone on an fd stream, the
    /// house on another, as it will run over the engine's pipe.
    func testAConnectionRunsOverFdStreams() async throws {
        let (mine, theirs) = try socketPair()
        let house = FakeAwayHouse { m in [(AwayKind.resp, m.req, #"{"cancelled":false}"#, false)] }
        await house.serve(FdAwayByteStream(fileDescriptor: theirs))
        let c = AwayConnection(stream: FdAwayByteStream(fileDescriptor: mine), phoneKey: .generate(),
                               housePublicKey: house.key.publicKey, version: "1.5.0")
        _ = try await c.open(timeout: 5)
        var answers = 0
        for try await _ in try await c.request(.cancel(chatID: 3)) { answers += 1 }
        XCTAssertEqual(answers, 1)
        await c.close()
    }

    final class FakeEngine: AwayTorEngine, @unchecked Sendable {
        let lock = NSLock()
        var starts: [(String, String, String)] = []
        var opens: [(String, Int32)] = []
        var stops = 0
        var startResult: Int32 = 0
        var houseEnd: Int32 = -1
        var authSeen: [String] = []
        /// Answers `open` with this (a crate `ERR_*`) instead of a socket.
        var openResult: Int32?

        func start(stateDir: String, cacheDir: String, authDir: String) -> Int32 {
            lock.lock(); defer { lock.unlock() }
            starts.append((stateDir, cacheDir, authDir))
            authSeen = (try? FileManager.default.contentsOfDirectory(atPath: authDir)) ?? []
            return startResult
        }

        func open(onion: String, port: Int32) -> Int32 {
            lock.lock(); defer { lock.unlock() }
            opens.append((onion, port))
            if let openResult { return openResult }
            var fds: [Int32] = [0, 0]
            guard socketpair(AF_UNIX, SOCK_STREAM, 0, &fds) == 0 else { return -1 }
            houseEnd = fds[1]
            var on: Int32 = 1
            _ = setsockopt(fds[1], SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
            return fds[0]
        }

        func stop() { lock.lock(); stops += 1; lock.unlock() }
    }

    private func tempDirectories() -> AwayTorDirectories {
        AwayTorDirectories(root: FileManager.default.temporaryDirectory
            .appendingPathComponent("away-tor-\(UUID().uuidString)", isDirectory: true))
    }

    private let onion = String(repeating: "b", count: 56) + ".onion"

    func testTheEngineStartsOnceWithItsFoldersAndHandsBackFdStreams() async throws {
        let engine = FakeEngine()
        let dirs = tempDirectories()
        defer { try? FileManager.default.removeItem(at: dirs.root) }
        let key = AwayClientAuthKey(privateKey: NoiseKeyPair.generate().privateKey)
        try dirs.writeAuth(onion: onion, key: key)
        let transport = EngineAwayTransport(engine: engine, directories: dirs)
        let stream = try await transport.open(AwayEndpoint(onion: onion, port: 80), clientAuth: key)
        let second = try await transport.open(AwayEndpoint(onion: onion, port: 80), clientAuth: key)
        XCTAssertEqual(engine.starts.count, 1)
        XCTAssertEqual(engine.starts[0].0, dirs.state.path)
        XCTAssertEqual(engine.starts[0].2, dirs.auth.path)
        XCTAssertEqual(engine.authSeen, [String(repeating: "b", count: 56) + ".auth_private"], "the key was there before Tor started")
        XCTAssertEqual(engine.opens.count, 2)
        XCTAssertEqual(engine.opens[0].0, onion)
        XCTAssertEqual(engine.opens[0].1, 80)
        // The fd is a live stream.
        let houseEnd = engine.houseEnd
        _ = Data("x".utf8).withUnsafeBytes { Darwin.write(houseEnd, $0.baseAddress, 1) }
        let got = try await second.read()
        XCTAssertEqual(got, Data("x".utf8))
        await second.close()
        await stream.close()
        await transport.stop()
        await transport.stop()
        XCTAssertEqual(engine.stops, 1)
    }

    func testAnEngineThatDoesntStartOrOpenIsUnreachable() async throws {
        let engine = FakeEngine()
        engine.startResult = 3
        let dirs = tempDirectories()
        defer { try? FileManager.default.removeItem(at: dirs.root) }
        let transport = EngineAwayTransport(engine: engine, directories: dirs)
        let key = AwayClientAuthKey(privateKey: Data(count: 32))
        do {
            _ = try await transport.open(AwayEndpoint(onion: onion, port: 80), clientAuth: key)
            XCTFail("opened")
        } catch let e as AwayTransportError {
            guard case .unreachable = e else { return XCTFail("\(e)") }
        }
    }

    /// The crate's `ERR_*` codes say why, in words with no key in them; Tor
    /// that isn't running after all (`ERR_NOT_STARTED`) is started again by
    /// the next `open`.
    func testAnOpenFailureSaysWhyAndTorThatStoppedIsStartedAgain() async throws {
        let engine = FakeEngine()
        let dirs = tempDirectories()
        defer { try? FileManager.default.removeItem(at: dirs.root) }
        let transport = EngineAwayTransport(engine: engine, directories: dirs)
        let key = AwayClientAuthKey(privateKey: Data(count: 32))
        for (code, why) in [(Int32(-4), "the house's onion refused this phone's key"),
                            (-3, "the house's onion wasn't found"),
                            (-5, "the house's onion didn't answer in time"),
                            (-2, "Tor isn't running")] {
            engine.openResult = code
            do {
                _ = try await transport.open(AwayEndpoint(onion: onion, port: 80), clientAuth: key)
                XCTFail("opened")
            } catch {
                XCTAssertEqual(error as? AwayTransportError, .unreachable(why))
            }
        }
        XCTAssertEqual(engine.starts.count, 1, "started once for the first four")
        engine.openResult = nil
        let stream = try await transport.open(AwayEndpoint(onion: onion, port: 80), clientAuth: key)
        XCTAssertEqual(engine.starts.count, 2, "ERR_NOT_STARTED: started again")
        await stream.close()
        await transport.stop()
    }

    func testTheAuthFileIsTorsLineInA0600FileInA0700Folder() throws {
        let dirs = tempDirectories()
        defer { try? FileManager.default.removeItem(at: dirs.root) }
        let raw = Data((0..<32).map { UInt8($0) })
        let key = AwayClientAuthKey(privateKey: raw)
        try dirs.writeAuth(onion: onion, key: key)
        let file = dirs.auth.appendingPathComponent(String(repeating: "b", count: 56) + ".auth_private")
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertEqual(text, String(repeating: "b", count: 56) + ":descriptor:x25519:" + AwayKeyCodec.base32(raw) + "\n")
        let fm = FileManager.default
        XCTAssertEqual((try fm.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertEqual((try fm.attributesOfItem(atPath: dirs.auth.path)[.posixPermissions] as? NSNumber)?.intValue, 0o700)
        XCTAssertEqual((try fm.attributesOfItem(atPath: dirs.state.path)[.posixPermissions] as? NSNumber)?.intValue, 0o700)
        dirs.removeAuth()
        XCTAssertFalse(fm.fileExists(atPath: file.path))
        XCTAssertFalse("\(key)".contains(AwayKeyCodec.base32(raw)))
    }

    /// On the iPhone the app's transport is Arti (``DomovoiTorEngine``
    /// under ``EngineAwayTransport``); where the crate isn't linked (the Mac,
    /// where `swift test` runs) it is the placeholder that opens nothing.
    func testTheShippingTransportIsArtiOnTheIPhoneAndThePlaceholderElsewhere() async {
        let t = AwayTransports.forApp()
        #if os(iOS)
        XCTAssertTrue(t is EngineAwayTransport)
        XCTAssertTrue(t.available)
        #else
        XCTAssertTrue(t is TorAwayTransport)
        XCTAssertFalse(t.available)
        do {
            _ = try await t.open(AwayEndpoint(onion: onion, port: 80), clientAuth: AwayClientAuthKey(privateKey: Data(count: 32)))
            XCTFail("opened")
        } catch {
            XCTAssertEqual(error as? AwayTransportError, .notAvailable)
        }
        #endif
    }

    func testTheDirectTransportReachesALocalListener() async throws {
        // Loopback only, on a port the kernel picks.
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        addr.sin_port = 0
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, len) } }
        Darwin.listen(fd, 1)
        _ = withUnsafeMutablePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.getsockname(fd, $0, &len) } }
        let port = UInt16(bigEndian: addr.sin_port)
        let accepted = Task.detached { Darwin.accept(fd, nil, nil) }
        let t = DirectAwayTransport(host: "127.0.0.1", port: port)
        let stream = try await t.open(AwayEndpoint(onion: onion, port: 80), clientAuth: AwayClientAuthKey(privateKey: Data(count: 32)))
        let peer = await accepted.value
        try await stream.write(Data("DAC1".utf8))
        var buffer = [UInt8](repeating: 0, count: 4)
        XCTAssertEqual(Darwin.read(peer, &buffer, 4), 4)
        XCTAssertEqual(Data(buffer), Data("DAC1".utf8))
        await stream.close()
        Darwin.close(peer)
        Darwin.close(fd)
    }
}
