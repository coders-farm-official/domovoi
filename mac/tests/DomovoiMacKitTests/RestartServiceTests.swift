import Darwin
import XCTest
@testable import DomovoiMacKit

/// The password-free Restart (RestartService; Train 1 finding 15, the
/// socket design after the security review of e5a3b7d7): one connection
/// per request, served over a socketpair here exactly as launchd hands the
/// job its connection.
final class RestartServiceTests: XCTestCase {
    var dir: URL!
    var layout: Layout!
    var t = Date(timeIntervalSince1970: 1_800_000_000)
    var restarts = 0
    var restartTakes: TimeInterval = 30
    var restartSleeps: TimeInterval = 0
    var result = RestartResult(back: true, escalated: false, said: "Domovoi is back: its services were restarted.")
    var failure: Error?
    var logged: [String] = []
    let lock = NSLock()
    var sockPath = ""
    var listener: Int32 = -1

    override func setUpWithError() throws {
        dir = try realTempDir()
        layout = Layout(mode: .system, root: dir)
        try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
        // As launchd would: a listening socket (a short path: 104 bytes).
        sockPath = "/tmp/dmk-\(UUID().uuidString.prefix(8)).sock"
        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let path = sockPath
        withUnsafeMutableBytes(of: &addr.sun_path) { buf in for (i, b) in Array(path.utf8).enumerated() { buf[i] = b } }
        let l = listener
        let bound = withUnsafePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(l, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
        XCTAssertEqual(bound, 0)
        XCTAssertEqual(listen(listener, 128), 0)
        // 500 clients and their server ends in one test process.
        var rl = rlimit()
        getrlimit(RLIMIT_NOFILE, &rl)
        rl.rlim_cur = min(rl.rlim_max, 4096)
        setrlimit(RLIMIT_NOFILE, &rl)
    }

    override func tearDownWithError() throws {
        close(listener)
        unlink(sockPath)
        try? FileManager.default.removeItem(at: dir)
    }

    func server() -> RestartService.Server {
        let s = RestartService.Server(layout: layout, fileOps: LocalFileOps(),
                                      now: { [unowned self] in self.lock.lock(); defer { self.lock.unlock() }; return self.t },
                                      log: { [unowned self] l in self.lock.lock(); self.logged.append(l); self.lock.unlock() }) { [unowned self] in
            self.lock.lock()
            self.restarts += 1
            let sleep = self.restartSleeps
            self.lock.unlock()
            if sleep > 0 { Thread.sleep(forTimeInterval: sleep) }
            self.lock.lock()
            self.t += self.restartTakes
            let f = self.failure, r = self.result
            self.lock.unlock()
            if let f { throw f }
            return r
        }
        s.idleExit = 0.3
        return s
    }

    /// Runs one server until it is idle; `clients` runs meanwhile.
    func serve(_ s: RestartService.Server, _ clients: () -> Void) {
        let done = expectation(description: "server idle")
        let l = listener
        DispatchQueue.global().async { s.run([l]); done.fulfill() }
        clients()
        wait(for: [done], timeout: 120)
    }

    /// One client: connect, send `bytes`, read every line until the end.
    func client(_ bytes: [UInt8] = [RestartService.verb], halfClose: Bool = true) -> [String] {
        var fd: Int32 = -1
        for _ in 0..<500 {
            if let f = try? RestartService.connect(sockPath) { fd = f; break }
            Thread.sleep(forTimeInterval: 0.01)   // the backlog was full: again
        }
        guard fd >= 0 else { return ["NOCONNECT"] }
        defer { close(fd) }
        var tv = timeval(tv_sec: 60, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var b = bytes
        if !b.isEmpty { _ = write(fd, &b, b.count) }
        if halfClose { shutdown(fd, SHUT_WR) }
        var data = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while true { let n = read(fd, &buf, buf.count); if n <= 0 { break }; data.append(contentsOf: buf[0..<n]) }
        return String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init)
    }

    func state(_ lines: [String]) -> RestartService.State? {
        lines.last { $0.hasPrefix("RESULT ") }.flatMap { try? JSONDecoder().decode(RestartService.State.self, from: Data($0.dropFirst(7).utf8)) }
    }

    func testConnectingIsTheRequestAndThePeerIsLogged() {
        var lines: [String] = []
        serve(server()) { lines = client([], halfClose: false) }   // nothing sent: the 200 ms wait, then a request
        XCTAssertEqual(restarts, 1)
        XCTAssertEqual(lines.first, "STARTED")
        XCTAssertEqual(state(lines)?.phase, "done")
        XCTAssertEqual(state(lines)?.askedBy, Int(getuid()))
        XCTAssertEqual(RestartService.readState(layout.restartState), state(lines), "the answer is kept for the menu too")
        XCTAssertTrue(logged.contains { $0.contains("asked for by uid \(getuid())") }, "\(logged)")
    }

    func testAnythingButTheVerbIsRefused() {
        var lines: [String] = []
        serve(server()) { lines = client(Array("rm -rf /".utf8)) }
        XCTAssertEqual(lines, ["ERROR unknown request"])
        XCTAssertEqual(restarts, 0)
        XCTAssertNil(RestartService.readState(layout.restartState))
    }

    func testTwoMinutesCountFromTheFinish() {
        restartTakes = 100
        serve(server()) { _ = client() }            // starts at t, finishes at t+100
        t += 50                                     // 50 s after the finish
        var lines: [String] = []
        serve(server()) { lines = client() }
        XCTAssertEqual(restarts, 1)
        XCTAssertEqual(state(lines)?.phase, "refused")
        XCTAssertTrue(state(lines)?.said.contains("Wait 70 seconds") == true, state(lines)?.said ?? "")
        t += 71
        serve(server()) { lines = client() }
        XCTAssertEqual(state(lines)?.phase, "done")
        XCTAssertEqual(restarts, 2)
    }

    func testARefusalBeforeActingDoesntCountAFailureAfterActingDoes() {
        failure = RestartRefused(RestartPlan.underWay)       // an update is running
        var lines: [String] = []
        serve(server()) { lines = client() }
        XCTAssertEqual(state(lines)?.phase, "refused")
        XCTAssertEqual(state(lines)?.said, RestartPlan.underWay)
        XCTAssertNil(state(lines)?.lastActedAt)
        // A step that throws mid-way: it acted, so the two minutes count.
        failure = HelperError("launchctl kickstart com.domovoi.mac.vm failed")
        t += 1
        serve(server()) { lines = client() }
        XCTAssertEqual(state(lines)?.phase, "done")
        XCTAssertEqual(state(lines)?.back, false)
        XCTAssertNotNil(state(lines)?.lastActedAt)
        XCTAssertTrue(state(lines)?.said.hasPrefix("Domovoi didn't restart: launchctl kickstart") == true, state(lines)?.said ?? "")
        failure = nil
        t += 10
        serve(server()) { lines = client() }
        XCTAssertEqual(state(lines)?.phase, "refused", "it acted: the two minutes count")
        XCTAssertEqual(restarts, 2, "the third was refused by the limit, before any restart")
    }

    func testAFinishInTheFutureDoesntBlock() {
        let s = RestartService.State(phase: "done", seenAt: HostFiles.iso(t), lastActedAt: HostFiles.iso(t.addingTimeInterval(3600)),
                                     finishedAt: HostFiles.iso(t.addingTimeInterval(3600)), back: true, said: "x")
        XCTAssertNil(RestartService.tooSoon(s, now: t))
        XCTAssertNotNil(RestartService.tooSoon(s, now: t.addingTimeInterval(3601)))
    }

    func testARequestDuringARunGetsThisRunsResult() throws {
        // An old result on disk must never be what a joiner gets.
        let old = RestartService.State(phase: "done", seenAt: "2020-01-01T00:00:00Z", lastActedAt: "2020-01-01T00:00:00Z",
                                       finishedAt: "2020-01-01T00:00:00Z", back: false, said: "an old run")
        try JSONEncoder().encode(old).write(to: layout.restartState)
        restartSleeps = 1.0
        var first: [String] = [], second: [String] = []
        serve(server()) {
            let g = DispatchGroup()
            DispatchQueue.global().async(group: g) { let a = self.client(); self.lock.lock(); first = a; self.lock.unlock() }
            Thread.sleep(forTimeInterval: 0.5)
            DispatchQueue.global().async(group: g) { let a = self.client(); self.lock.lock(); second = a; self.lock.unlock() }
            g.wait()
        }
        XCTAssertEqual(restarts, 1, "one restart for both")
        XCTAssertEqual(first.first, "STARTED")
        XCTAssertEqual(second.first, "RUNNING")
        XCTAssertEqual(state(second), state(first))
        XCTAssertEqual(state(second)?.said, result.said, "this run's result, not the old one")
    }

    /// Security review round 2: 500 connections at once are one process
    /// (this one), at most one restart at a time, and every one gets an
    /// answer or BUSY.
    func testFiveHundredConnectionsAtOnce() {
        restartSleeps = 1.0
        let n = 500
        var answers = [[String]](repeating: [], count: n)
        let s = server()
        s.idleExit = 2
        serve(s) {
            DispatchQueue.concurrentPerform(iterations: n) { i in
                let a = self.client()
                self.lock.lock(); answers[i] = a; self.lock.unlock()
            }
        }
        var results = 0, busy = 0
        var other: [[String]] = []
        for a in answers {
            if state(a) != nil { results += 1 } else if a.first?.hasPrefix("BUSY ") == true { busy += 1 } else { other.append(a) }
        }
        XCTAssertEqual(other.count, 0, "every connection got an answer or BUSY: \(other.prefix(3))")
        XCTAssertEqual(results + busy, n)
        XCTAssertGreaterThan(results, 0)
        XCTAssertLessThanOrEqual(restarts, 3, "never a restart per connection (\(restarts))")
        XCTAssertEqual(s.connections, n, "all served by this one process")
        XCTAssertLessThan(logged.count, 80, "per-connection lines are rate-limited (\(logged.count))")
    }

    func testTheClientSideReadsTheAnswer() {
        var seen: [String] = []
        var s: RestartService.State?
        let path = sockPath
        serve(server()) {
            if let fd = try? RestartService.connect(path) {
                s = RestartService.ask(fd) { seen.append($0) }
                close(fd)
            }
        }
        XCTAssertEqual(seen, ["STARTED"])
        XCTAssertEqual(s?.phase, "done")
        XCTAssertThrowsError(try RestartService.connect("/tmp/dmk-nothing-\(UUID().uuidString.prefix(6)).sock"))
    }

    func testTheMenuSeesWhetherThisInstallHasIt() throws {
        XCTAssertFalse(RestartService.available(layout, requireRoot: false), "no job file: an older install, the password route")
        try FileManager.default.createDirectory(at: layout.launchDaemons, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: layout.plist(Labels.restart).path, contents: Data("x".utf8))
        XCTAssertFalse(RestartService.available(layout, requireRoot: false), "no socket")
        try FileManager.default.createDirectory(at: layout.runDir, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: layout.restartSocket.path, contents: Data())
        XCTAssertFalse(RestartService.available(layout, requireRoot: false), "a plain file isn't the socket")
    }

    func testTheJobFileTemplate() throws {
        let text = try Template.render(try read("launchd/com.domovoi.mac.restart.plist"), values: [
            "LABEL": Labels.restart, "HELPER": "/H/DomovoiHelper", "SOCKET": "/R/run/restart.sock", "LOG_FILE": "/L/helper.log",
        ])
        let p = try LaunchdPlist(text: text)
        XCTAssertEqual(p.programArguments, ["/H/DomovoiHelper", "restart-trigger", "--system"])
        XCTAssertNil(p.userName)
        XCTAssertFalse(p.keepAlive)
        XCTAssertNil(p.dictionary["RunAtLoad"])
        XCTAssertNil(p.dictionary["inetdCompatibility"], "one process, never one per connection")
        XCTAssertNil(p.dictionary["WatchPaths"])
        XCTAssertEqual(p.logFiles, ["/L/helper.log"])
        let l = try XCTUnwrap((p.dictionary["Sockets"] as? [String: Any])?["Listener"] as? [String: Any])
        XCTAssertEqual(l["SockPathName"] as? String, "/R/run/restart.sock")
        XCTAssertEqual(l["SockPathMode"] as? Int, Int(RestartService.socketMode))
    }

    func testTheCommandTakesNoArguments() throws {
        var ok = try Args(["restart-trigger", "--system"])
        XCTAssertNoThrow(try SetupCommands.parseRestartTrigger(&ok))
        var extra = try Args(["restart-trigger", "--system", "--json"])
        XCTAssertThrowsError(try SetupCommands.parseRestartTrigger(&extra))
        var user = try Args(["restart-trigger", "--user-mode", "--prefix", "/tmp/x"])
        XCTAssertThrowsError(try SetupCommands.parseRestartTrigger(&user))
    }
}

// The busy lock (security review 2026-11-11): mode 0600, so no other
/// account can hold a shared lock on it; the menu reads its status file.
final class BusyLockTests: XCTestCase {
    func testTheLockIs0600AndSaysWhoHoldsIt() throws {
        let dir = try realTempDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let layout = Layout(mode: .system, root: dir)
        try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
        // An older install's 0644 file is tightened.
        FileManager.default.createFile(atPath: layout.busyLock.path, contents: nil, attributes: [.posixPermissions: 0o644])
        var held: BusyLock? = try BusyLock(layout.busyLock, what: "restart")
        let mode = (try FileManager.default.attributesOfItem(atPath: layout.busyLock.path)[.posixPermissions] as? NSNumber)?.intValue
        XCTAssertEqual(mode, 0o600)
        XCTAssertTrue(BusyLock.held(status: layout.busyStatus))
        let doc = try JSONSerialization.jsonObject(with: Data(contentsOf: layout.busyStatus)) as? [String: Any]
        XCTAssertEqual(doc?["what"] as? String, "restart")
        XCTAssertThrowsError(try BusyLock(layout.busyLock), "one at a time")
        _ = held
        held = nil
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.busyStatus.path))
        XCTAssertFalse(BusyLock.held(status: layout.busyStatus))
        // A status file left by a process that is gone isn't "busy".
        try Data(#"{"pid": 999999, "what": "x"}"#.utf8).write(to: layout.busyStatus)
        XCTAssertFalse(BusyLock.held(status: layout.busyStatus))
    }
}

/// Removing a tree another account can write into, as the uninstall and a
/// repair do with the first version's requests folder.
final class SafeRemoveTests: XCTestCase {
    var dir: URL!
    override func setUpWithError() throws { dir = try realTempDir() }
    override func tearDownWithError() throws {
        // Anything the tests made read-only or immutable, back to removable.
        _ = try? Process.run(URL(fileURLWithPath: "/usr/bin/chflags"), arguments: ["-R", "nouchg", dir.path]).waitUntilExit()
        try? FileManager.default.removeItem(at: dir)
    }

    func makeFiles(_ d: URL, _ n: Int) throws {
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        for i in 0..<n { FileManager.default.createFile(atPath: d.appendingPathComponent("f\(i)").path, contents: Data()) }
    }

    func testPlantedFoldersLinksAndImmutableFiles() throws {
        let tree = dir.appendingPathComponent("restart-requests")
        let outside = dir.appendingPathComponent("outside")
        try makeFiles(outside, 20)
        try makeFiles(tree.appendingPathComponent("a/b/c"), 5)
        try FileManager.default.createSymbolicLink(at: tree.appendingPathComponent("link-to-outside"), withDestinationURL: outside)
        try FileManager.default.createSymbolicLink(at: tree.appendingPathComponent("a/link-file"), withDestinationURL: outside.appendingPathComponent("f0"))
        let stuck = tree.appendingPathComponent("a/stuck")
        FileManager.default.createFile(atPath: stuck.path, contents: Data("x".utf8))
        XCTAssertEqual(chflags(stuck.path, UInt32(UF_IMMUTABLE)), 0)
        XCTAssertEqual(chmod(tree.path, 0o1733), 0)
        try SafeRemove.remove(tree.path)
        XCTAssertFalse(FileManager.default.fileExists(atPath: tree.path))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: outside.path).count, 20, "nothing outside the tree")
        XCTAssertNoThrow(try SafeRemove.remove(tree.path), "gone already is fine")
    }

    func testALinkInPlaceOfTheTargetIsRemovedNotFollowed() throws {
        let outside = dir.appendingPathComponent("outside")
        try makeFiles(outside, 3)
        let target = dir.appendingPathComponent("restart-requests")
        try FileManager.default.createSymbolicLink(at: target, withDestinationURL: outside)
        try SafeRemove.remove(target.path)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: outside.path).count, 3)
        XCTAssertNil(try? FileManager.default.destinationOfSymbolicLink(atPath: target.path))
    }

    /// The review's probe, against SafeRemove: a folder under the tree is
    /// swapped for a link to `outside` over and over while it is removed.
    func testASymlinkSwapMidWalkDeletesNothingOutside() throws {
        var lost = 0
        for trial in 0..<10 {
            let root = dir.appendingPathComponent("t\(trial)")
            let x = root.appendingPathComponent("req/x")
            let d = x.appendingPathComponent("d")
            let outside = root.appendingPathComponent("outside")
            try makeFiles(d, 2000)
            try makeFiles(outside, 2000)
            var stop = false
            let racer = Thread {
                let tmp = x.appendingPathComponent("held").path
                while !stop {
                    if rename(d.path, tmp) == 0 {
                        _ = symlink(outside.path, d.path)
                        usleep(200)
                        _ = unlink(d.path)
                        _ = rename(tmp, d.path)
                    }
                }
            }
            racer.start()
            _ = try? SafeRemove.remove(root.appendingPathComponent("req").path)
            stop = true
            Thread.sleep(forTimeInterval: 0.05)
            lost += 2000 - ((try? FileManager.default.contentsOfDirectory(atPath: outside.path).count) ?? 0)
        }
        XCTAssertEqual(lost, 0, "files outside the tree were deleted")
    }
}
