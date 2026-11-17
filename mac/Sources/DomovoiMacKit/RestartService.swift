import Darwin
import Foundation

/// Restart Domovoi without a password (owner decision 2026-11-11, Train 1
/// finding 15; PLAN 4.9's socket design, after the security review of the
/// first, folder-based version): any process of any local account may ask
/// for Domovoi's restart, and nothing more. The permission is granted once
/// at install by the root helper (Setup's menu-bar step) and removed by
/// the uninstall.
///
/// - `com.domovoi.mac.restart` is a root LaunchDaemon with a launchd
///   `Sockets` entry: launchd itself makes the UNIX socket
///   /Library/Application Support/Domovoi/run/restart.sock (mode 0666) in a
///   root-owned 0755 folder, and starts the job's fixed program,
///   `DomovoiHelper restart-trigger --system`, when someone connects. ONE
///   process (launch_activate_socket, an accept loop: `Server`) answers
///   every connection, holds at most `maxOpen` at once (more get BUSY) and
///   leaves when idle. No RunAtLoad, no KeepAlive, no inetdCompatibility.
/// - Connecting is the request. The job waits 200 ms for one optional byte
///   (`R`); anything else is answered "ERROR" and closed. No account's data
///   becomes an argument, a path or a program, and nothing an account owns
///   is ever left on disk. The asking account is logged (getpeereid), at
///   most one connection line a second.
/// - One restart at a time, on a worker thread of that process: a
///   connection made while it runs is told RUNNING and answered with THIS
///   run's result, never a second restart.
/// - At most one restart every two minutes, counted from when the last
///   restart that acted finished. A refusal in Restarter's checks, before
///   any step (RestartRefused: switched off, an update or Setup at work),
///   doesn't count; anything that fails once a step ran does. A finish time
///   in the future (the clock went back) doesn't block.
/// - The answer is written to `state/restart.json` (root, 0644) and sent on
///   the connection: `STARTED` or `RUNNING`, then `RESULT <json>`.
///
/// An install from before this has no socket: the menu then keeps the
/// password route and says why (`available`).
public enum RestartService {
    public static let label = Labels.restart
    public static let minInterval: TimeInterval = 120
    /// launchd's SockPathMode (decimal 438 in the job file).
    public static let socketMode: mode_t = 0o666
    public static let verb = UInt8(ascii: "R")

    public struct State: Codable, Equatable {
        public var schema = 1
        /// running, done (it acted; see `back`) or refused (nothing done).
        public var phase: String
        /// When this run took its request.
        public var seenAt: String
        /// When the last restart that acted finished (carried over a
        /// refusal): the two minutes count from here.
        public var lastActedAt: String?
        public var finishedAt: String?
        public var back: Bool?
        public var said: String
        /// The account that asked (getpeereid), for the log and support.
        public var askedBy: Int?

        enum CodingKeys: String, CodingKey {
            case schema, phase, said, back
            case seenAt = "seen_at"
            case lastActedAt = "last_acted_at"
            case finishedAt = "finished_at"
            case askedBy = "asked_by"
        }

        public init(phase: String, seenAt: String, lastActedAt: String?, finishedAt: String? = nil, back: Bool? = nil,
                    said: String, askedBy: Int? = nil) {
            self.phase = phase
            self.seenAt = seenAt
            self.lastActedAt = lastActedAt
            self.finishedAt = finishedAt
            self.back = back
            self.said = said
            self.askedBy = askedBy
        }
    }

    public static func readState(_ url: URL) -> State? {
        guard let d = try? Data(contentsOf: url), d.count < 64 * 1024 else { return nil }
        return try? JSONDecoder().decode(State.self, from: d)
    }

    /// The refusal for a request inside the two minutes after the last
    /// restart that acted, or nil.
    public static func tooSoon(_ s: State?, now: Date) -> String? {
        guard let last = s?.lastActedAt.flatMap(HostFiles.parseISO) else { return nil }
        let since = now.timeIntervalSince(last)
        guard since >= 0, since < minInterval else { return nil }   // a future time: the clock went back
        let left = Int((minInterval - since).rounded(.up))
        // Two sentences, shown as they are (RestartResult.alertText; S2-5).
        return "\(RestartResult.tooSoonPrefix). Wait \(left) second\(left == 1 ? "" : "s"), then try again"
    }

    // ─── the menu's side (any account, no root) ─────────────────────────

    /// Whether this install has the password-free restart: its job file,
    /// and launchd's socket (root's, a socket, not a link).
    public static func available(_ layout: Layout, requireRoot: Bool = true) -> Bool {
        guard FileManager.default.fileExists(atPath: layout.plist(label).path) else { return false }
        var st = stat()
        guard lstat(layout.restartSocket.path, &st) == 0 else { return false }
        return (st.st_mode & S_IFMT) == S_IFSOCK && (!requireRoot || st.st_uid == 0)
    }

    /// Connects to the socket at `path`. The caller closes the descriptor.
    public static func connect(_ path: String) throws -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw HelperError("can't make a socket: \(String(cString: strerror(errno)))") }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else { close(fd); throw HelperError("socket path too long") }
        withUnsafeMutableBytes(of: &addr.sun_path) { buf in
            for (i, b) in bytes.enumerated() { buf[i] = b }
            buf[bytes.count] = 0
        }
        let ok = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard ok == 0 else {
            let why = String(cString: strerror(errno))
            close(fd)
            throw HelperError("Domovoi's restart helper isn't answering (\(why))")
        }
        return fd
    }

    /// Asks for a restart on a connected socket and reads the answer:
    /// `onLine` sees STARTED / RUNNING as they come; returns the result
    /// (nil: the connection ended without one). Reads at most 64 KB.
    public static func ask(_ fd: Int32, onLine: (String) -> Void = { _ in }) -> State? {
        var v = verb
        _ = write(fd, &v, 1)
        var buf = [UInt8](repeating: 0, count: 4096)
        var pending: [UInt8] = []
        var total = 0
        while total < 64 * 1024 {
            let n = read(fd, &buf, buf.count)
            if n < 0 && errno == EINTR { continue }
            if n <= 0 { break }
            total += n
            pending.append(contentsOf: buf[0..<n])
            while let nl = pending.firstIndex(of: 0x0a) {
                let line = String(decoding: pending[0..<nl], as: UTF8.self)
                pending.removeSubrange(0...nl)
                if line.hasPrefix("RESULT ") {
                    return try? JSONDecoder().decode(State.self, from: Data(line.dropFirst(7).utf8))
                }
                onLine(line)
            }
        }
        return nil
    }

    // ─── the root job's side ────────────────────────────────────────────

    /// The sockets launchd made for the job (`launch_activate_socket`,
    /// looked up at run time so nothing else links against it).
    public static func launchdSockets(_ name: String = "Listener") throws -> [Int32] {
        typealias Activate = @convention(c) (UnsafePointer<CChar>, UnsafeMutablePointer<UnsafeMutablePointer<Int32>?>, UnsafeMutablePointer<Int>) -> Int32
        guard let sym = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "launch_activate_socket") else {   // RTLD_DEFAULT
            throw HelperError("launch_activate_socket isn't available")
        }
        let activate = unsafeBitCast(sym, to: Activate.self)
        var fds: UnsafeMutablePointer<Int32>?
        var count = 0
        let r = name.withCString { activate($0, &fds, &count) }
        guard r == 0, let list = fds else { throw HelperError("launchd gave no socket \(name) (error \(r)): run only by launchd") }
        defer { free(list) }
        return (0..<count).map { list[$0] }
    }

    /// The one job process (security review round 2: never a process per
    /// connection). It accepts every connection itself, answers each one,
    /// runs at most one restart at a time on a worker thread, and leaves
    /// when idle; launchd starts it again on the next connection.
    ///
    /// - At most `maxOpen` connections are held at once (waiting for their
    ///   byte or for the running restart's result); any more get `BUSY` and
    ///   are closed at once.
    /// - Each connection: no SIGPIPE, 2 s send and receive timeouts; the
    ///   optional byte is waited for `readWait` (200 ms).
    /// - Connections that arrive during a run are told `RUNNING` and get
    ///   THIS run's result.
    /// - Per-connection log lines: at most one a second, the rest counted
    ///   in an "N more connections" line.
    public final class Server {
        let stateURL: URL
        let fileOps: FileOps
        let now: () -> Date
        let restart: () throws -> RestartResult
        let log: (String) -> Void
        public var maxOpen = 32
        public var readWait: TimeInterval = 0.2
        public var idleExit: TimeInterval = 30
        public var sendTimeout = 2

        struct Conn { let fd: Int32; let uid: uid_t?; let since: Date; var asked: Bool }
        var reading: [Conn] = []
        var waiting: [Conn] = []
        var running = false
        var runResult: State?
        var lastLog = Date.distantPast
        var quiet = 0
        let wake: [Int32]
        let lock = NSLock()
        public private(set) var connections = 0
        public private(set) var restarts = 0
        public private(set) var busyReplies = 0

        public init(layout: Layout, fileOps: FileOps, now: @escaping () -> Date = Date.init,
                    log: @escaping (String) -> Void, restart: @escaping () throws -> RestartResult) {
            self.stateURL = layout.restartState
            self.fileOps = fileOps
            self.now = now
            self.log = log
            self.restart = restart
            var p: [Int32] = [-1, -1]
            if pipe(&p) == 0 {
                for f in p { _ = fcntl(f, F_SETFL, fcntl(f, F_GETFL) | O_NONBLOCK); _ = fcntl(f, F_SETFD, FD_CLOEXEC) }
            }
            wake = p
        }

        deinit { for f in wake where f >= 0 { close(f) } }

        func write(_ s: State) {
            let enc = JSONEncoder()
            enc.outputFormatting = [.sortedKeys]
            if let d = try? enc.encode(s) { try? fileOps.writeAtomically(d + Data([0x0a]), to: stateURL, mode: 0o644) }
        }

        func send(_ fd: Int32, _ line: String) {
            let bytes = Array((line + "\n").utf8)
            var off = 0
            while off < bytes.count {
                let n = bytes[off...].withUnsafeBufferPointer { Darwin.write(fd, $0.baseAddress, $0.count) }
                if n < 0 && errno == EINTR { continue }
                if n <= 0 { return }   // gone, or not reading within the timeout: dropped
                off += n
            }
        }

        func result(_ fd: Int32, _ s: State?) {
            let enc = JSONEncoder()
            enc.outputFormatting = [.sortedKeys]
            let json = s.flatMap { try? enc.encode($0) }.map { String(decoding: $0, as: UTF8.self) } ?? "{}"
            send(fd, "RESULT " + json)
        }

        func name(_ uid: uid_t?) -> String {
            uid.map { u in "uid \(u)" + (getpwuid(u).map { " (\(String(cString: $0.pointee.pw_name)))" } ?? "") } ?? "an unknown account"
        }

        /// A per-connection line, at most one a second.
        func connLog(_ line: String) {
            let t = Date()
            if t.timeIntervalSince(lastLog) >= 1 {
                if quiet > 0 { log("restart-trigger: \(quiet) more connection(s)"); quiet = 0 }
                log(line)
                lastLog = t
            } else {
                quiet += 1
            }
        }

        func flushLog() {
            if quiet > 0 { log("restart-trigger: \(quiet) more connection(s)"); quiet = 0 }
        }

        func configure(_ fd: Int32) {
            var one: Int32 = 1
            _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
            var tv = timeval(tv_sec: sendTimeout, tv_usec: 0)
            _ = setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
            _ = setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
            _ = fcntl(fd, F_SETFD, FD_CLOEXEC)
            // Accepted from a non-blocking listener: blocking again, with the
            // timeouts above bounding every read and write.
            _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK)
        }

        func accept(_ listener: Int32) {
            while true {
                let fd = Darwin.accept(listener, nil, nil)
                if fd < 0 { return }   // EAGAIN: none left
                connections += 1
                configure(fd)
                if reading.count + waiting.count >= maxOpen {
                    busyReplies += 1
                    send(fd, "BUSY too many requests at once: try again in a moment")
                    close(fd)
                    connLog("restart-trigger: busy, a connection turned away")
                    continue
                }
                var uid: uid_t = 0, gid: gid_t = 0
                reading.append(Conn(fd: fd, uid: getpeereid(fd, &uid, &gid) == 0 ? uid : nil, since: Date(), asked: false))
            }
        }

        /// A connection whose byte came (or didn't, in time): a request,
        /// unless it sent something else.
        func requested(_ c: Conn, byte: UInt8?) {
            if let b = byte, b != verb {
                connLog("restart-trigger: \(name(c.uid)) sent something that isn't a request: refused")
                send(c.fd, "ERROR unknown request")
                close(c.fd)
                return
            }
            if running {
                connLog("restart-trigger: \(name(c.uid)) joins the restart already running")
                send(c.fd, "RUNNING")
                waiting.append(c)
                return
            }
            connLog("restart-trigger: a restart asked for by \(name(c.uid))")
            let before = readState(stateURL)
            let seen = HostFiles.iso(now())
            let asker = c.uid.map { Int($0) }
            if let why = tooSoon(before, now: now()) {
                let s = State(phase: "refused", seenAt: seen, lastActedAt: before?.lastActedAt, finishedAt: seen, said: why, askedBy: asker)
                write(s)
                result(c.fd, s)
                close(c.fd)
                return
            }
            running = true
            restarts += 1
            write(State(phase: "running", seenAt: seen, lastActedAt: before?.lastActedAt, said: "restarting", askedBy: asker))
            send(c.fd, "STARTED")
            waiting.append(c)
            let wakeFD = wake[1]
            Thread.detachNewThread { [self] in
                let s: State
                do {
                    let r = try restart()
                    let fin = HostFiles.iso(now())
                    s = State(phase: "done", seenAt: seen, lastActedAt: fin, finishedAt: fin, back: r.back, said: r.said, askedBy: asker)
                } catch let e as RestartRefused {
                    // Refused in its checks, nothing touched: the two
                    // minutes aren't used.
                    s = State(phase: "refused", seenAt: seen, lastActedAt: before?.lastActedAt, finishedAt: HostFiles.iso(now()),
                              said: e.message, askedBy: asker)
                } catch {
                    // A step failed after the restart began: it acted.
                    let fin = HostFiles.iso(now())
                    s = State(phase: "done", seenAt: seen, lastActedAt: fin, finishedAt: fin, back: false,
                              said: RestartResult.alertText("\(error)"), askedBy: asker)
                }
                write(s)
                lock.lock(); runResult = s; lock.unlock()
                var b: UInt8 = 1
                _ = Darwin.write(wakeFD, &b, 1)
            }
        }

        func finishRun() {
            lock.lock(); let s = runResult; runResult = nil; lock.unlock()
            guard let s else { return }
            log("restart-trigger: \(s.phase): \(s.said)")
            for c in waiting { result(c.fd, s); close(c.fd) }
            waiting.removeAll()
            running = false
        }

        /// Serves `listeners` until idle for `idleExit` with nothing running.
        @discardableResult
        public func run(_ listeners: [Int32]) -> Int32 {
            signal(SIGPIPE, SIG_IGN)
            for l in listeners { _ = fcntl(l, F_SETFL, fcntl(l, F_GETFL) | O_NONBLOCK) }
            var lastActivity = Date()
            while true {
                var fds = listeners.map { pollfd(fd: $0, events: Int16(POLLIN), revents: 0) }
                fds.append(pollfd(fd: wake[0], events: Int16(POLLIN), revents: 0))
                fds += reading.map { pollfd(fd: $0.fd, events: Int16(POLLIN), revents: 0) }
                let r = poll(&fds, nfds_t(fds.count), 50)
                if r < 0 && errno != EINTR { log("restart-trigger: poll failed: \(String(cString: strerror(errno)))"); return 1 }
                let t = Date()
                // The worker's "done".
                if fds[listeners.count].revents != 0 {
                    var b = [UInt8](repeating: 0, count: 64)
                    while read(wake[0], &b, b.count) > 0 {}
                    finishRun()
                    lastActivity = t
                }
                // Bytes (or ends, or the wait over) for the connections reading.
                var still: [Conn] = []
                var ready: [(Conn, UInt8?)] = []
                for (i, c) in reading.enumerated() {
                    let ev = fds[listeners.count + 1 + i].revents
                    if ev != 0 {
                        var b: UInt8 = 0
                        let n = read(c.fd, &b, 1)
                        ready.append((c, n == 1 ? b : nil))
                    } else if t.timeIntervalSince(c.since) >= readWait {
                        ready.append((c, nil))   // nothing sent: connecting is the request
                    } else {
                        still.append(c)
                    }
                }
                reading = still
                for (c, b) in ready { requested(c, byte: b); lastActivity = t }
                for (i, l) in listeners.enumerated() where fds[i].revents != 0 { accept(l); lastActivity = t }
                if !running && reading.isEmpty && waiting.isEmpty && t.timeIntervalSince(lastActivity) >= idleExit {
                    flushLog()
                    return 0
                }
            }
        }
    }
}

extension Installer {
    /// The password-free restart (RestartService), in Setup's menu-bar
    /// step: the root-owned socket folder and the root job launchd gives
    /// the socket to. Recorded (also when a repair finds them already
    /// there), so the uninstall removes both. The first version's requests
    /// folder, when a repair finds it, is removed without following
    /// anything in it.
    func installRestartService() throws -> String {
        let legacy = layout.legacyRestartRequests
        var lst = stat()
        if lstat(legacy.path, &lst) == 0 {
            try SafeRemove.remove(legacy.path)
            try store.update { r in
                for item in r.created where item.path == legacy.path { r.remove(item) }
            }
            progress.log("removed the old restart requests folder \(legacy.path)")
        }
        let dir = layout.runDir
        if !store.record.has(.dir, path: dir.path) {
            try record(.init(kind: .dir, path: dir.path, keep: .program, step: "menu-bar"))
        }
        var st = stat()
        if lstat(dir.path, &st) == 0 {
            guard (st.st_mode & S_IFMT) == S_IFDIR else { throw HelperError("\(dir.path) is not a folder: remove it, then Repair") }
        } else if mkdir(dir.path, 0o755) != 0 {
            throw HelperError("can't make \(dir.path): \(String(cString: strerror(errno)))")
        }
        if geteuid() == 0 {
            guard chown(dir.path, 0, 0) == 0 else { throw HelperError("can't give \(dir.path) to root") }
        }
        guard chmod(dir.path, 0o755) == 0 else { throw HelperError("can't set \(dir.path) to mode 0755") }
        for f in [layout.restartSocket, layout.restartState] where !store.record.has(.file, path: f.path) {
            try record(.init(kind: .file, path: f.path, keep: .program, step: "menu-bar"))
        }
        let logFile = layout.logs.appendingPathComponent("helper.log")
        let plist = try LaunchdPlist(text: try Template.render(template("launchd/\(Labels.restart).plist"), values: [
            "LABEL": Template.xmlEscape(Labels.restart),
            "HELPER": Template.xmlEscape(layout.helperProgram.path),
            "SOCKET": Template.xmlEscape(layout.restartSocket.path),
            "LOG_FILE": Template.xmlEscape(logFile.path),
        ]))
        try record(.init(kind: .launchdJob, path: layout.plist(Labels.restart).path, label: Labels.restart, domain: launchd.domain, keep: .program, step: "menu-bar"))
        try launchd.install(plist, at: layout.plist(Labels.restart))
        return "Restart in the menu needs no password (\(Labels.restart))"
    }
}
