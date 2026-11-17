import Darwin
import Foundation

/// Starts, inspects and stops the Mac side's long-running jobs (Ollama, the
/// VM) from their `mac/launchd` files.
public protocol JobManager: AnyObject {
    /// Recorded with each job, so uninstall asks the same manager.
    var domain: String { get }
    func state(_ label: String) throws -> LaunchdJobState?
    @discardableResult
    func install(_ plist: LaunchdPlist, at path: URL) throws -> LaunchdJobState
    /// Stops it; fine when it isn't there.
    func bootout(_ label: String, wait: Double) throws
}

extension JobManager {
    public func bootout(_ label: String) throws { try bootout(label, wait: 120) }
}

extension LaunchdInstaller: JobManager {}

/// The user mode's jobs. launchd refuses to load a job for a standard user
/// over SSH, in every domain (2026-10-30, macOS 26.4: `bootstrap user/UID`
/// "5: Input/output error", `gui/UID` "125: Domain does not support
/// specified action", `load` exit 134). So the user mode runs the same job
/// file itself: the program, arguments, environment and log file the plist
/// names, as a detached process in a session of its own, its pid in
/// `<label>.pid` beside the plist. What it does not do is launchd's part:
/// no restart (KeepAlive), no start at boot. Those are tested with the
/// real LaunchDaemons (Phase 3).
///
/// It only ever signals a pid it wrote down itself, and only while that pid
/// still runs the job's program.
public final class ProcessJobs: JobManager {
    public static let domainName = "process"
    public var domain: String { Self.domainName }
    let folder: URL
    let fileOps: FileOps
    let sleeper: Sleeper
    public var settle: Double = 3

    public init(folder: URL, fileOps: FileOps, sleeper: Sleeper) {
        self.folder = folder
        self.fileOps = fileOps
        self.sleeper = sleeper
    }

    public func pidFile(_ label: String) -> URL { folder.appendingPathComponent("\(label).pid") }
    func plistFile(_ label: String) -> URL { folder.appendingPathComponent("\(label).plist") }

    /// The pid recorded for `label`, if that process still runs the job's
    /// program.
    func livePid(_ label: String) -> Int32? {
        guard let text = try? String(contentsOf: pidFile(label), encoding: .utf8),
              let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 else { return nil }
        guard kill(pid, 0) == 0 || errno == EPERM else { return nil }
        guard let data = try? Data(contentsOf: plistFile(label)), let plist = try? LaunchdPlist(text: String(decoding: data, as: UTF8.self)),
              let program = plist.programArguments.first else { return nil }
        return Self.executable(of: pid) == URL(fileURLWithPath: program).resolvingSymlinksInPath().path ? pid : nil
    }

    /// The path of the program `pid` runs (proc_pidpath).
    public static func executable(of pid: Int32) -> String? {
        var buf = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
        let n = proc_pidpath(pid, &buf, UInt32(buf.count))
        guard n > 0 else { return nil }
        return URL(fileURLWithPath: String(cString: buf)).resolvingSymlinksInPath().path
    }

    public func state(_ label: String) throws -> LaunchdJobState? {
        guard FileManager.default.fileExists(atPath: pidFile(label).path) else { return nil }
        if let pid = livePid(label) { return LaunchdJobState(state: "running", pid: pid, lastExit: nil) }
        return LaunchdJobState(state: "not running", pid: nil, lastExit: nil)
    }

    @discardableResult
    public func install(_ plist: LaunchdPlist, at path: URL) throws -> LaunchdJobState {
        guard path.standardizedFileURL == plistFile(plist.label).standardizedFileURL else {
            throw HelperError("\(plist.label): the job file must be \(plistFile(plist.label).path)")
        }
        try bootout(plist.label, wait: 120)
        try fileOps.writeAtomically(Data(plist.text.utf8), to: path, mode: 0o644)
        for f in plist.logFiles { try fileOps.makeLogFile(URL(fileURLWithPath: f), owner: nil) }
        let pid = try spawn(plist)
        try fileOps.writeAtomically(Data("\(pid)\n".utf8), to: pidFile(plist.label), mode: 0o644)
        sleeper.sleep(settle)
        guard let s = try state(plist.label), s.running else {
            let tail = plist.logFiles.first.flatMap { try? String(contentsOfFile: $0, encoding: .utf8) }?
                .split(separator: "\n").suffix(5).joined(separator: " | ") ?? ""
            throw HelperError("\(plist.label) exited at once: \(tail)")
        }
        return s
    }

    func spawn(_ plist: LaunchdPlist) throws -> Int32 {
        let args = plist.programArguments
        // launchd's environment: a plain PATH, then the job's own keys.
        var env = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
        for (k, v) in plist.environment { env[k] = v }
        let log = plist.logFiles.first ?? "/dev/null"

        var fa: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&fa)
        defer { posix_spawn_file_actions_destroy(&fa) }
        posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_addopen(&fa, 1, log, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        posix_spawn_file_actions_adddup2(&fa, 1, 2)
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        defer { posix_spawnattr_destroy(&attr) }
        // A session of its own: the helper and the SSH session it ran in
        // can end without taking the job with them.
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETSID | POSIX_SPAWN_CLOEXEC_DEFAULT))

        let cargs = args.map { strdup($0) } + [nil]
        let cenv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { (cargs + cenv).forEach { free($0) } }
        var pid: pid_t = 0
        let rc = posix_spawn(&pid, args[0], &fa, &attr, cargs, cenv)
        guard rc == 0 else { throw HelperError("can't start \(args[0]): \(String(cString: strerror(rc)))") }
        return pid
    }

    public func bootout(_ label: String, wait: Double) throws {
        defer { try? FileManager.default.removeItem(at: pidFile(label)) }
        guard let pid = livePid(label) else { return }
        var limit = wait
        if let data = try? Data(contentsOf: plistFile(label)), let p = try? LaunchdPlist(text: String(decoding: data, as: UTF8.self)),
           let t = p.dictionary["ExitTimeOut"] as? Int {
            limit = min(wait, Double(t))
        }
        kill(pid, SIGTERM)
        let start = sleeper.now()
        while kill(pid, 0) == 0 {
            // Our own child (the helper that started it is still running):
            // reap it, or it lingers as a zombie that kill(0) still finds.
            if waitpid(pid, nil, WNOHANG) == pid { break }
            if sleeper.now().timeIntervalSince(start) > limit {
                kill(pid, SIGKILL)
                sleeper.sleep(1)
                _ = waitpid(pid, nil, WNOHANG)
                break
            }
            sleeper.sleep(0.5)
        }
        // The job's own process group (it leads a session of its own): what
        // it started and left behind, such as Ollama's model runners.
        kill(-pid, SIGTERM)
    }
}

/// The manager a recorded domain names: `process` (the user mode), or a
/// launchd domain (`system`, `user/UID`, `gui/UID`).
public func jobManager(domain: String, layout: Layout, runner: CommandRunner, sleeper: Sleeper, fileOps: FileOps) -> JobManager {
    domain == ProcessJobs.domainName
        ? ProcessJobs(folder: layout.launchDaemons, fileOps: fileOps, sleeper: sleeper)
        : LaunchdInstaller(domain: domain, runner: runner, sleeper: sleeper, fileOps: fileOps)
}
