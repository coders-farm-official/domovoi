import Foundation
@testable import DomovoiMacKit

/// The `mac/` folder of this checkout.
let macDir = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
let repoDir = macDir.deletingLastPathComponent()

func read(_ rel: String) throws -> String { try String(contentsOf: macDir.appendingPathComponent(rel), encoding: .utf8) }

func tempDir(_ name: String = #function) throws -> URL {
    let base = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("dmk-\(UUID().uuidString.prefix(8))")
    try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
    return base.resolvingSymlinksInPath()
}

/// Answers commands from a script of handlers; records every call.
final class FakeRunner: CommandRunner {
    var calls: [Command] = []
    var handlers: [(Command) -> CommandResult?] = []

    func on(_ h: @escaping (Command) -> CommandResult?) { handlers.append(h) }

    func run(_ command: Command, onLine: ((String) -> Void)?) throws -> CommandResult {
        calls.append(command)
        for h in handlers.reversed() {
            if let r = h(command) {
                if let onLine { r.stdout.split(separator: "\n", omittingEmptySubsequences: false).forEach { onLine(String($0)) } }
                return r
            }
        }
        return CommandResult(status: 0)
    }

    func ran(_ program: String, _ firstArg: String? = nil) -> [Command] {
        calls.filter { ($0.executable as NSString).lastPathComponent == program && (firstArg == nil || $0.arguments.first == firstArg) }
    }
}

final class FakeSleeper: Sleeper {
    var t = Date(timeIntervalSince1970: 1_800_000_000)
    var slept: Double = 0
    func sleep(_ seconds: Double) { t += seconds; slept += seconds }
    func now() -> Date { t }
}

final class FakeHTTP: HTTPClient {
    var answers: [String: HTTPResponse] = [:]
    var handler: ((String) -> HTTPResponse?)?
    var requested: [String] = []
    /// POSTs: url, body, headers. `postStatus` nil: nothing answers.
    var posts: [(url: String, body: Data, headers: [String: String])] = []
    var postStatus: Int? = 202
    func get(_ url: String, timeout: TimeInterval) -> HTTPResponse? {
        requested.append(url)
        return handler?(url) ?? answers[url]
    }
    func post(_ url: String, json: Data, headers: [String: String], timeout: TimeInterval) -> HTTPResponse? {
        posts.append((url, json, headers))
        return postStatus.map { HTTPResponse(status: $0) }
    }
    /// The `paths` of every POST so far.
    var postedPaths: [[String]] {
        posts.map { ((try? JSONSerialization.jsonObject(with: $0.body)) as? [String: Any])?["paths"] as? [String] ?? [] }
    }
}

final class QuietProgress: ProgressSink {
    var events: [(String, String, String)] = []
    var lines: [String] = []
    func event(step: String, state: String, message: String) { events.append((step, state, message)) }
    func log(_ text: String) { lines.append(text) }
}

/// A launchd that keeps jobs in a dictionary: bootstrap loads (running,
/// unless told it fails to spawn), bootout unloads.
final class FakeLaunchd {
    var loaded: [String: LaunchdJobState] = [:]
    var spawnFails: Set<String> = []
    var nextPid: Int32 = 500
    var bootstrapped: [String] = []
    /// One-shot jobs (RunAtLoad, no KeepAlive), by label: run at bootstrap
    /// with the job file's path, they return the exit code; the job is then
    /// loaded and "not running", as launchd shows a job that has exited.
    var oneShot: [String: (String) -> Int32] = [:]

    func install(on r: FakeRunner) {
        r.on { [unowned self] c in
            guard c.executable == LaunchdInstaller.launchctl else { return nil }
            let a = c.arguments
            switch a.first {
            case "print":
                let label = String(a[1].split(separator: "/").last!)
                guard let s = self.loaded[label] else { return CommandResult(status: 113, stderr: "Could not find service") }
                var t = "\(a[1]) = {\n\tactive count = 1\n"
                if let st = s.state { t += "\tstate = \(st)\n" }
                if let p = s.pid { t += "\tpid = \(p)\n" }
                if let e = s.lastExit { t += "\tlast exit code = \(e)\n" }
                t += "\tendpoints = {\n\t\tstate = active\n\t}\n}\n"
                return CommandResult(status: 0, stdout: t)
            case "bootstrap":
                let path = a[2]
                let label = ((path as NSString).lastPathComponent as NSString).deletingPathExtension
                self.bootstrapped.append(label)
                if let job = self.oneShot[label] {
                    let code = job(path)
                    self.loaded[label] = LaunchdJobState(state: "not running", pid: nil, lastExit: String(code))
                } else if self.spawnFails.contains(label) {
                    self.loaded[label] = LaunchdJobState(state: "spawn scheduled", pid: nil, lastExit: "78: EX_CONFIG")
                } else {
                    self.nextPid += 1
                    self.loaded[label] = LaunchdJobState(state: "running", pid: self.nextPid, lastExit: nil)
                }
                return CommandResult(status: 0)
            case "bootout":
                let label = String(a[1].split(separator: "/").last!)
                self.loaded[label] = nil
                return CommandResult(status: 0)
            default:
                return CommandResult(status: 0)
            }
        }
    }
}

/// Real files, but ownership is only written down: the tests can't give a
/// file to an account that doesn't exist (the role account).
final class TestFileOps: FileOps {
    let real = LocalFileOps()
    var owners: [String: String] = [:]
    func writeAtomically(_ data: Data, to url: URL, mode: Int) throws { try real.writeAtomically(data, to: url, mode: mode) }
    func makeLogFile(_ url: URL, owner: String?) throws {
        try real.makeLogFile(url, owner: nil)
        if let o = owner { owners[url.path] = o }
    }
    func chown(_ url: URL, owner: String) throws { owners[url.path] = owner }
}

/// tempDir() with every link resolved, /private included (realpath), for
/// code that opens a path part by part and refuses a link on the way
/// (/var is one; URL's resolvingSymlinksInPath strips /private again).
func realTempDir(_ name: String = #function) throws -> URL {
    let u = try tempDir(name)
    guard let r = realpath(u.path, nil) else { throw HelperError("no realpath for \(u.path)") }
    defer { free(r) }
    return URL(fileURLWithPath: String(cString: r), isDirectory: true)
}
