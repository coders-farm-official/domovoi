import Foundation

/// The hidden role account that owns the VM and Ollama (mac-installer PLAN
/// 4.1): `_domovoi`, a UID and GID of its own in 200-400, `IsHidden 1`, shell
/// `/usr/bin/false`, no password (`*`), home in Domovoi's folder. Made and
/// removed with `dscl` (root only), through `CommandRunner` so the tests
/// answer for it.
///
/// A reinstall onto kept data asks for the UID the record kept, so the VM
/// disk and the models it left behind are its own again.
public final class RoleAccount {
    public static let dscl = "/usr/bin/dscl"
    /// The range the role account's UID and GID come from (PLAN 4.1): below
    /// 500, so the login window never lists it.
    public static let idRange = 200...400

    public let name: String
    let runner: CommandRunner

    public init(name: String, runner: CommandRunner) {
        self.name = name
        self.runner = runner
    }

    func dscl(_ args: [String]) throws -> CommandResult {
        try runner.run(Command(Self.dscl, ["."] + args, timeout: 60))
    }

    /// The account's UID, or nil when there is no such account.
    public func uid() throws -> Int? {
        let r = try dscl(["-read", "/Users/\(name)", "UniqueID"])
        guard r.ok else { return nil }
        // "UniqueID: 301"
        return r.stdout.split(whereSeparator: { $0 == " " || $0 == "\n" }).compactMap { Int($0) }.first
    }

    func used(_ kind: String, _ attribute: String) throws -> Set<Int> {
        let r = try dscl(["-list", "/\(kind)", attribute])
        guard r.ok else { throw HelperError("dscl can't list \(kind): \(r.tail)") }
        var ids = Set<Int>()
        for line in r.stdout.split(separator: "\n") {
            if let last = line.split(separator: " ").last, let n = Int(last) { ids.insert(n) }
        }
        return ids
    }

    /// A number free as both a UID and a GID: `preferred` when it is free,
    /// else the highest free one in the range (Apple's own role accounts sit
    /// at the low end).
    public func freeID(preferred: Int?) throws -> Int {
        let taken = try used("Users", "UniqueID").union(try used("Groups", "PrimaryGroupID"))
        if let p = preferred, Self.idRange.contains(p), !taken.contains(p) { return p }
        for n in Self.idRange.reversed() where !taken.contains(n) { return n }
        throw HelperError("no free user and group number in \(Self.idRange.lowerBound)-\(Self.idRange.upperBound) for \(name)")
    }

    /// Makes the account (and its group) unless it is there. Returns its UID
    /// and whether it was made now.
    @discardableResult
    public func ensure(home: String, realName: String, preferredID: Int?) throws -> (uid: Int, made: Bool) {
        if let u = try uid() { return (u, false) }
        let id = try freeID(preferred: preferredID)
        let g = "/Groups/\(name)", u = "/Users/\(name)"
        let steps: [[String]] = [
            ["-create", g],
            ["-create", g, "PrimaryGroupID", String(id)],
            ["-create", g, "Password", "*"],
            ["-create", g, "RealName", realName],
            ["-create", u],
            ["-create", u, "UniqueID", String(id)],
            ["-create", u, "PrimaryGroupID", String(id)],
            ["-create", u, "UserShell", "/usr/bin/false"],
            ["-create", u, "NFSHomeDirectory", home],
            ["-create", u, "RealName", realName],
            ["-create", u, "IsHidden", "1"],
            ["-create", u, "Password", "*"],
        ]
        for s in steps {
            let r = try dscl(s)
            guard r.ok else { throw HelperError("dscl \(s.joined(separator: " ")) failed: \(r.tail)") }
        }
        guard let made = try uid(), made == id else { throw HelperError("the account \(name) is not there after dscl made it") }
        return (id, true)
    }

    /// The account's two directory records, user then group.
    public var recordPaths: [String] { ["/Users/\(name)", "/Groups/\(name)"] }

    /// Which of the two records are still there.
    public func remainingRecords() throws -> [String] {
        try recordPaths.filter { try dscl(["-read", $0, "RecordName"]).ok }
    }

    /// Only an account whose name starts with `_domovoi` (and is a plain
    /// name: it goes into a command line and a launchd job) is ever deleted.
    func checkOurs() throws {
        let plain = name.unicodeScalars.allSatisfy { CharacterSet.alphanumerics.contains($0) || $0 == "_" || $0 == "-" }
        guard name.hasPrefix("_domovoi"), plain else { throw HelperError("refusing to delete the account \(name): not Domovoi's") }
    }

    /// Whether the directory record at `path` is there.
    func has(_ path: String) throws -> Bool {
        try dscl(["-read", path, "RecordName"]).ok
    }

    /// Deletes the account, then its group; fine when they are gone. Each
    /// delete is checked by reading the record again (Train 1 S2-20: the
    /// user record stayed while its group went). The group goes only once
    /// the user is gone, so a refusal leaves the account whole, never a
    /// user whose group is missing.
    public func delete() throws {
        try checkOurs()
        for path in recordPaths {
            guard try has(path) else { continue }
            let r = try dscl(["-delete", path])
            guard r.ok else { throw HelperError("dscl -delete \(path) failed: \(r.tail)") }
            if try has(path) { throw HelperError("dscl -delete \(path) answered, but the record is still there") }
        }
    }

    /// dscl's answer when the calling process may not change the directory
    /// (P3-8: from the app's administrator prompt on macOS 26.4.1, even as
    /// root; the same command from Terminal with sudo works).
    public static func isPermissionRefusal(_ text: String) -> Bool {
        text.contains("eDSPermissionError") || text.contains("-14120")
    }

    /// The lines a person pastes into Terminal to finish the delete by hand:
    /// one `sudo dscl . -delete` per record still there (`records`; nil:
    /// both, the user first).
    public static func finishCommand(_ name: String, records: [String]? = nil) -> String {
        let paths = records ?? ["/Users/\(name)", "/Groups/\(name)"]
        return paths.map { "sudo dscl . -delete \($0)" }.joined(separator: "; ")
    }

    // ─── what still runs as the account ─────────────────────────────────

    public static let ps = "/bin/ps"
    public static let kill = "/bin/kill"

    /// `ps`'s arguments: every process, its PID, effective and real UID, no
    /// header. One `-o` each: in BSD ps the text after `=` is the column's
    /// header, commas included.
    public static let psArguments = ["-A", "-o", "pid=", "-o", "uid=", "-o", "ruid="]

    /// The PIDs in `ps` output (`psArguments`) whose effective or real UID
    /// is `uid`. By number only, never by a program's name; never PID 0 or 1.
    public static func pids(owner uid: Int, psOutput: String) -> [Int32] {
        var out: [Int32] = []
        for line in psOutput.split(separator: "\n") {
            let f = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard f.count == 3, let pid = Int32(f[0]), pid > 1, let euid = Int(f[1]), let ruid = Int(f[2]) else { continue }
            if euid == uid || ruid == uid { out.append(pid) }
        }
        return out
    }
}

/// Deletes the role account from a context macOS allows (P3-8).
///
/// On macOS 26.4.1 `dscl . -delete /Users/_domovoi` answers
/// eDSPermissionError (-14120) when it runs under the app's administrator
/// prompt (`osascript ... with administrator privileges`, whose responsible
/// process is Domovoi.app), even as root; the very same command from
/// Terminal with sudo works, and making the account from the app's prompt
/// works too. So the refusal belongs to the calling context (a TCC-style
/// check on the responsible process), not to the command. The account has
/// no secure token.
///
/// So the delete runs here first (it works from Terminal with sudo); when
/// it is refused, it runs again as a one-shot LaunchDaemon:
/// launchd spawns it as root, outside the app's process tree, so its
/// responsible process is the job itself and not Domovoi.app. Whether macOS
/// lets that context delete the record can't be seen in a unit test (TCC
/// isn't faked here); the real-Mac check is in VERIFY-PHASE3 "P3-8 fix".
/// When both are refused the caller gets `.refused` and says what is left
/// and the one line that removes it.
///
/// Before any of that, whatever still runs as the account is ended (Train 1
/// S2-20: after Remove everything the user record stayed, its group went,
/// and a `distnoted` of the account, which launchd had started for its
/// jobs at boot, still ran): its launchd user domain is booted out, then
/// any process left with its UID gets TERM, then KILL. By UID only, and
/// only for a UID in the role account's range read from this account's
/// own record. The last page says so when one couldn't be ended.
///
/// `sysadminctl -deleteUser` is not used: on this macOS it has no
/// `-keepHome` ("not available on this system") and always deletes the
/// home, which is Domovoi's data folder; and it asks the same directory
/// service, so the same check would most likely refuse it too.
public final class AccountRemoval {
    public static let label = "com.domovoi.mac.account-delete"

    public enum Outcome: Equatable {
        /// Neither record was there.
        case alreadyGone
        /// Deleted: `via` says how ("dscl" or "a launchd job").
        case deleted(via: String)
        /// Still there; why.
        case refused(String)
    }

    let account: RoleAccount
    let layout: Layout
    let runner: CommandRunner
    let sleeper: Sleeper
    let fileOps: FileOps
    let log: (String) -> Void
    /// How long the launchd job may take to run and exit.
    public var jobWait: Double = 60
    /// How long the account's processes get to end after TERM, then after KILL.
    public var stopWait: Double = 5
    /// What still ran as the account after `run()` tried to end it.
    public private(set) var stillRunning: [Int32] = []

    public init(account: RoleAccount, layout: Layout, runner: CommandRunner, sleeper: Sleeper, fileOps: FileOps,
                log: @escaping (String) -> Void = { _ in }) {
        self.account = account
        self.layout = layout
        self.runner = runner
        self.sleeper = sleeper
        self.fileOps = fileOps
        self.log = log
    }

    /// The job's file and log: in the record's root-only folder, deleted
    /// right after the job ran.
    var plistURL: URL { layout.state.appendingPathComponent("\(Self.label).plist") }
    var logURL: URL { layout.state.appendingPathComponent("\(Self.label).log") }

    public func run() -> Outcome {
        let uid: Int?
        do {
            try account.checkOurs()
            if try account.remainingRecords().isEmpty { return .alreadyGone }
            uid = try account.uid()
        } catch {
            return .refused("\(error)")
        }
        if let uid { stillRunning = stopProcesses(uid: uid) }
        let first: String
        do {
            try account.delete()
            return .deleted(via: "dscl")
        } catch {
            first = "\(error)" + (stillRunning.isEmpty ? "" : " (\(stillRunning.count) process(es) still run as it)")
        }
        guard RoleAccount.isPermissionRefusal(first) else { return .refused(first) }
        log("\(first): macOS refused it from here; running the delete as a launchd job")
        var why = first
        do {
            // The user first, then the group, and the group only once the
            // user is gone (as RoleAccount.delete).
            for path in account.recordPaths {
                guard try account.has(path) else { continue }
                let r = try runJob([RoleAccount.dscl, ".", "-delete", path])
                if try account.has(path) {
                    why = "\(first); the launchd job's dscl -delete \(path) too: \(r.ok ? "it answered, but the record is still there" : r.message)"
                    break
                }
            }
            return try account.remainingRecords().isEmpty ? .deleted(via: "a launchd job") : .refused(why)
        } catch {
            return .refused("\(first); then \(error)")
        }
    }

    /// Ends what still runs as `uid` (the class's note). Returns the PIDs
    /// still there afterwards (none, as a rule).
    func stopProcesses(uid: Int) -> [Int32] {
        // Never anyone else's: the role account's range only (never root,
        // never a person's account).
        guard RoleAccount.idRange.contains(uid) else { return [] }
        // The domain launchd made for the account's jobs (distnoted and the
        // like run there). Not there is fine.
        _ = try? runner.run(Command(LaunchdInstaller.launchctl, ["bootout", "user/\(uid)"], timeout: 30))
        var left = pids(uid)
        if left.isEmpty { return [] }
        log("ending \(left.count) process(es) still running as \(account.name) (UID \(uid)): PID \(left.map { String($0) }.joined(separator: ", "))")
        for signal in ["-TERM", "-KILL"] {
            _ = try? runner.run(Command(RoleAccount.kill, [signal] + left.map { String($0) }, timeout: 10))
            let start = sleeper.now()
            while !left.isEmpty && sleeper.now().timeIntervalSince(start) < stopWait {
                sleeper.sleep(0.5)
                left = pids(uid)
            }
            if left.isEmpty { return [] }
        }
        log("still running as \(account.name) after KILL: PID \(left.map { String($0) }.joined(separator: ", "))")
        return left
    }

    /// The processes whose effective or real UID is `uid`; this one never.
    func pids(_ uid: Int) -> [Int32] {
        guard let r = try? runner.run(Command(RoleAccount.ps, RoleAccount.psArguments, timeout: 20)), r.ok else { return [] }
        return RoleAccount.pids(owner: uid, psOutput: r.stdout).filter { $0 != getpid() }
    }

    /// The last page's "Left to do by hand" for an account still there:
    /// `records`, the ones left (RoleAccount.remainingRecords: only those
    /// are in the line to paste); a process that wouldn't end means a
    /// restart comes first.
    public static func leftToDoByHand(name: String, records: [String], stillRunning: [Int32]) -> String {
        let why = stillRunning.isEmpty
            ? "The account can't sign in and nothing runs as it any more, so it is safe to leave. To remove it, open Terminal and paste this line "
            : "The account can't sign in, and what still ran as it stops when this Mac restarts. To remove it, restart this Mac, then open Terminal and paste this line "
        return "macOS did not let Domovoi delete its hidden account \(name). Everything else is removed. " + why +
            "(it asks for your Mac password):\n    \(RoleAccount.finishCommand(name, records: records.isEmpty ? nil : records))\n" +
            "If Terminal answers \"eDSPermissionError\", turn Terminal on in System Settings > Privacy & Security > Full Disk Access, " +
            "then paste the line again."
    }

    struct JobResult: Equatable {
        var ok: Bool
        var message: String
    }

    /// Runs `arguments` once as a root LaunchDaemon (system domain) and
    /// waits for it to exit. The job is unloaded and its file and log are
    /// deleted afterwards, whatever happened: nothing stays loaded.
    func runJob(_ arguments: [String]) throws -> JobResult {
        let dict: [String: Any] = [
            "Label": Self.label,
            "ProgramArguments": arguments,
            "RunAtLoad": true,
            "KeepAlive": false,
            "StandardOutPath": logURL.path,
            "StandardErrorPath": logURL.path,
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: dict, format: .xml, options: 0)
        let plist = try LaunchdPlist(text: String(decoding: data, as: UTF8.self))
        let jobs = LaunchdInstaller(domain: "system", runner: runner, sleeper: sleeper, fileOps: fileOps)
        defer {
            try? jobs.bootout(Self.label, wait: 30)
            try? FileManager.default.removeItem(at: plistURL)
            try? FileManager.default.removeItem(at: logURL)
        }
        try jobs.install(plist, at: plistURL)
        let start = sleeper.now()
        while sleeper.now().timeIntervalSince(start) <= jobWait {
            // "last exit code = (never exited)" until it has run once.
            if let s = try jobs.state(Self.label), !s.running, let e = s.lastExit, !e.contains("never") {
                let out = ((try? String(contentsOf: logURL, encoding: .utf8)) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let tail = out.split(separator: "\n").suffix(4).joined(separator: " | ")
                return JobResult(ok: e.hasPrefix("0"), message: "exit \(e)\(tail.isEmpty ? "" : ": " + tail)")
            }
            sleeper.sleep(1)
        }
        throw HelperError("the launchd job \(Self.label) did not finish in \(Int(jobWait)) s")
    }
}

/// The inherited access entry the media folders get, so files any account
/// drops in stay readable and writable by the VM (it runs as the role
/// account; PLAN 4.1). Directory rights (they map to read/write/execute on
/// the files that inherit them).
public enum MediaACL {
    public static let rights = "list,add_file,search,add_subdirectory,delete_child,readattr,writeattr,readextattr,writeextattr,readsecurity,file_inherit,directory_inherit"
    public static func entry(for account: String) -> String { "\(account) allow \(rights)" }
    public static let chmod = "/bin/chmod"
}
