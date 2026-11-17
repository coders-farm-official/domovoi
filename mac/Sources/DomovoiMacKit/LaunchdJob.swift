import Foundation

/// Waiting, behind a protocol so the tests don't.
public protocol Sleeper: AnyObject {
    func sleep(_ seconds: Double)
    func now() -> Date
}

public final class RealSleeper: Sleeper {
    public init() {}
    public func sleep(_ seconds: Double) { Thread.sleep(forTimeInterval: seconds) }
    public func now() -> Date { Date() }
}

/// What `launchctl print DOMAIN/LABEL` says about a job.
public struct LaunchdJobState: Equatable {
    public var state: String?
    public var pid: Int32?
    public var lastExit: String?

    public var running: Bool { state == "running" && pid != nil }

    /// Reads the job's own lines (one tab in), not the nested sections'.
    public static func parse(_ text: String) -> LaunchdJobState {
        var s = LaunchdJobState()
        for line in text.split(separator: "\n") {
            guard line.hasPrefix("\t"), !line.hasPrefix("\t\t") else { continue }
            let t = line.dropFirst()
            func value(_ key: String) -> String? {
                t.hasPrefix(key + " = ") ? String(t.dropFirst(key.count + 3)).trimmingCharacters(in: .whitespaces) : nil
            }
            if s.state == nil, let v = value("state") { s.state = v }
            if s.pid == nil, let v = value("pid") { s.pid = Int32(v) }
            if s.lastExit == nil, let v = value("last exit code") { s.lastExit = v }
        }
        return s
    }
}

/// A launchd job file from a `mac/launchd` template.
public struct LaunchdPlist {
    public let label: String
    public let text: String
    public let dictionary: [String: Any]

    public init(text: String) throws {
        guard let data = text.data(using: .utf8),
              let obj = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil),
              let d = obj as? [String: Any] else {
            throw HelperError("the launchd job file doesn't parse as a property list")
        }
        guard let label = d["Label"] as? String, !label.isEmpty else { throw HelperError("the launchd job file has no Label") }
        guard let args = d["ProgramArguments"] as? [String], let prog = args.first, prog.hasPrefix("/") else {
            throw HelperError("\(label): ProgramArguments must start with an absolute path")
        }
        self.label = label
        self.text = text
        self.dictionary = d
    }

    public var userName: String? { dictionary["UserName"] as? String }
    public var keepAlive: Bool { (dictionary["KeepAlive"] as? Bool) ?? false }
    public var logFiles: [String] {
        var seen: [String] = []
        for k in ["StandardOutPath", "StandardErrorPath"] {
            if let p = dictionary[k] as? String, !seen.contains(p) { seen.append(p) }
        }
        return seen
    }
    public var programArguments: [String] { dictionary["ProgramArguments"] as? [String] ?? [] }
    public var environment: [String: String] { dictionary["EnvironmentVariables"] as? [String: String] ?? [:] }

    /// Renders `template` (a `mac/launchd/*.plist` file). `role` names the
    /// account that runs the job (system install); nil runs it as whoever
    /// owns the domain (user mode).
    public static func render(template: String, values: [String: String], role: String?) throws -> LaunchdPlist {
        var v = values.mapValues(Template.xmlEscape)
        if let role {
            v["ROLE_USER"] = Template.xmlEscape(role)
            v["ROLE_GROUP"] = Template.xmlEscape(role)
        }
        let text = try Template.render(template, values: v, sections: ["ROLE": role != nil])
        return try LaunchdPlist(text: text)
    }
}

/// Installs and removes launchd jobs: the S1/S4 lessons built in.
///
/// launchd opens StandardOutPath and StandardErrorPath *as the job's
/// UserName*. A role account can't create a file in a root-only folder, so
/// the spawn fails (EACCES, `last exit code = 78: EX_CONFIG`) and a KeepAlive
/// job retries quietly for ever, looking "allowed" under Login Items
/// (spikes S1/S4, 2026-10-30). So the log files are made first, owned by
/// that account, and every KeepAlive job must be seen `running` after
/// bootstrap, or the install stops and says why.
public final class LaunchdInstaller {
    public let domain: String
    let runner: CommandRunner
    let sleeper: Sleeper
    let fileOps: FileOps
    public var runningWait: Double = 15
    /// How long a job must stay up after it is first seen running: a job
    /// that crashes at once shows `running` for a moment.
    public var settle: Double = 3

    public static let launchctl = "/bin/launchctl"

    public init(domain: String, runner: CommandRunner, sleeper: Sleeper, fileOps: FileOps) {
        self.domain = domain
        self.runner = runner
        self.sleeper = sleeper
        self.fileOps = fileOps
    }

    /// `system` for the real install; `gui/UID` or `user/UID` for the user mode.
    public static func userDomain(uid: uid_t = getuid(), gui: Bool) -> String {
        "\(gui ? "gui" : "user")/\(uid)"
    }

    public func target(_ label: String) -> String { "\(domain)/\(label)" }

    public func state(_ label: String) throws -> LaunchdJobState? {
        let r = try runner.run(Command(Self.launchctl, ["print", target(label)], timeout: 20))
        return r.ok ? LaunchdJobState.parse(r.stdout) : nil
    }

    /// Writes `plist` to `path` and starts it. Returns the running state.
    @discardableResult
    public func install(_ plist: LaunchdPlist, at path: URL) throws -> LaunchdJobState {
        try bootout(plist.label)
        try fileOps.writeAtomically(Data(plist.text.utf8), to: path, mode: 0o644)
        for f in plist.logFiles {
            try fileOps.makeLogFile(URL(fileURLWithPath: f), owner: plist.userName)
        }
        var boot = try runner.run(Command(Self.launchctl, ["bootstrap", domain, path.path], timeout: 30))
        // Right after a bootout launchd may still be letting go of the old
        // job ("Bootstrap failed: 5: Input/output error").
        var tries = 1
        while !boot.ok && tries < 5 {
            sleeper.sleep(1)
            boot = try runner.run(Command(Self.launchctl, ["bootstrap", domain, path.path], timeout: 30))
            tries += 1
        }
        guard boot.ok else {
            throw HelperError("launchctl bootstrap \(target(plist.label)) failed (exit \(boot.status)): \(boot.tail)")
        }
        guard plist.keepAlive else { return try state(plist.label) ?? LaunchdJobState() }
        return try waitRunning(plist.label)
    }

    public func waitRunning(_ label: String) throws -> LaunchdJobState {
        let start = sleeper.now()
        var last = LaunchdJobState()
        while sleeper.now().timeIntervalSince(start) <= runningWait {
            if let s = try state(label) {
                last = s
                if s.running {
                    sleeper.sleep(settle)
                    if let again = try state(label), again.running { return again } else { continue }
                }
            }
            sleeper.sleep(1)
        }
        var why = "state \(last.state ?? "unknown")"
        if let e = last.lastExit { why += ", last exit code \(e)" }
        var hint = ""
        if last.lastExit?.hasPrefix("78") == true {
            hint = " (exit 78 is usually a log file the job's account can't open)"
        }
        throw HelperError("\(target(label)) is not running after \(Int(runningWait)) s: \(why)\(hint); for the reason: sudo log show --last 2m --predicate 'eventMessage CONTAINS \"\(label)\"'")
    }

    /// Stops and unloads the job; fine when it isn't loaded. Waits until
    /// launchd has let go of it.
    public func bootout(_ label: String, wait: Double = 120) throws {
        guard try state(label) != nil else { return }
        let r = try runner.run(Command(Self.launchctl, ["bootout", target(label)], timeout: wait + 10))
        let start = sleeper.now()
        while try state(label) != nil {
            if sleeper.now().timeIntervalSince(start) > wait {
                throw HelperError("\(target(label)) is still loaded \(Int(wait)) s after bootout (exit \(r.status)): \(r.tail)")
            }
            sleeper.sleep(1)
        }
    }
}
