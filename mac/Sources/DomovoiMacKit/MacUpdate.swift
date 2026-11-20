import Foundation

// "Update Domovoi" on the Mac side (mac-installer PLAN 4.12), the twin of
// windows/tray/Updater.cs.
//
// The Domovoi app on the Mac (the menu-bar icon, one per signed-in account)
// polls the core over loopback (GET /v1/mac/update, every 30 s): which Mac
// side is installed, which release the Linux side includes, whether a Mac
// step is needed, and an admin's request from the dashboard. A request from
// a browser on this Mac starts the Mac step at once (macOS's administrator
// prompt comes next); one from another device shows a notification, and a
// click on it starts the step. "Update Domovoi..." in the menu does both
// halves: the Linux part runs first, in the helper, then the Mac part.
//
// The Mac step: download the release's DomovoiSetup-mac.pkg, its manifest
// and both signatures from the release repository into a private run folder
// (~/Library/Application Support/Domovoi/updates/<run>, 0700), check them
// (MacSetupCheck: the release key the install pinned, the release the core
// named, newer than the installed Mac side), then start the installed helper
// as root behind the system's administrator prompt (`DomovoiHelper update`,
// HelperUpdate.swift): it copies the files into the root-only
// /Library/Application Support/Domovoi/updates/<run>, checks them again
// there with its own reading of the pinned key, installs the package
// (`installer -pkg`, a command-line install: the package's postinstall does
// nothing then) and runs the new helper's repair. Nothing is installed whose
// check fails, and the reason is said in plain words. A dashboard request is
// claimed on the core first, so only one signed-in account's app runs it.
// The package replaces this app's own bundle, so the run goes into
// pending.json first, and whichever app polls next reports its outcome (with
// the claim) BEFORE it reads the state again (MacUpdatePoll), so a finished
// run never starts another.
//
// MacStep is pure orchestration over four protocols (the core, the release
// downloads, the administrator prompt, the clock and files), so the tests
// run it whole against fakes. The menu-bar app (Phase 5) owns the timer, the
// notification and the menu items; it calls MacUpdateAgent.

/// The outcome words the core keeps (domovoi/windows_side.py OUTCOMES): the
/// Windows tray's, the same on a Mac.
public enum MacOutcome {
    public static let ok = "ok"
    public static let nothingNew = "nothing_new"
    public static let declined = "declined"
    public static let downloadFailed = "download_failed"
    public static let noSignedSetup = MacSetupCheck.noSignedSetup
    public static let badSignature = MacSetupCheck.badSignature
    public static let notNewer = MacSetupCheck.notNewer
    public static let noKey = MacSetupCheck.noKey
    public static let repairFailed = "repair_failed"
    public static let linuxFailed = "linux_failed"
    public static let internetOff = "internet_off"
    public static let notInstalled = "not_installed"
    public static let failed = "failed"
    public static let all: Set<String> = [ok, nothingNew, declined, downloadFailed, noSignedSetup, badSignature, notNewer, noKey,
                                          repairFailed, linuxFailed, internetOff, notInstalled, failed]
}

/// What `GET /v1/mac/update` says (domovoi/windows_side.py agent_state "mac").
public struct MacUpdateState: Equatable {
    /// The Mac side the core read from side.json (nil: it sees none).
    public var macVersion: String?
    public var targetTag: String?
    public var targetVersion: String?
    public var targetCommit: String?
    public var stepNeeded = false
    public var whyNot: String?
    public var internet: String?
    public var linuxBusy = false
    public var lastUpdateStarted: String?
    public var lastUpdateStatus: String?
    public var lastUpdateError: String?
    public var requestID: String?
    public var requestOnThisMac = false
    public var requestAfterLinux = false
    public var requestClaimed = false

    public init() {}

    public static func decode(_ data: Data) -> MacUpdateState? {
        guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], o["schema"] as? Int == 1 else { return nil }
        var s = MacUpdateState()
        if let m = o["mac"] as? [String: Any] { s.macVersion = m["version"] as? String }
        if let t = o["target"] as? [String: Any] {
            s.targetTag = (t["tag"] as? String).flatMap { MacReleaseNames.isTag($0) ? $0 : nil }
            s.targetVersion = t["version"] as? String
            s.targetCommit = (t["commit"] as? String).flatMap { Pattern.full($0, "[0-9a-f]{40}") ? $0 : nil }
        }
        s.stepNeeded = o["step_needed"] as? Bool ?? false
        s.whyNot = o["why_not"] as? String
        s.internet = o["internet"] as? String
        if let l = o["linux"] as? [String: Any] {
            s.linuxBusy = l["busy"] as? Bool ?? false
            if let u = l["last_update"] as? [String: Any] {
                s.lastUpdateStarted = u["started_at"] as? String
                s.lastUpdateStatus = u["status"] as? String
                s.lastUpdateError = u["error"] as? String
            }
        }
        if let r = o["request"] as? [String: Any], let id = r["id"] as? String, Pattern.full(id, "[0-9a-f]{16}") {
            s.requestID = id
            s.requestOnThisMac = r["on_this_pc"] as? Bool ?? false
            s.requestAfterLinux = r["after_linux"] as? Bool ?? false
            s.requestClaimed = r["claimed"] as? Bool ?? false
        }
        return s
    }
}

public protocol MacUpdateServer: AnyObject {
    func state() -> MacUpdateState?
    /// The one app that runs this request: its token, or nil (another
    /// account's app took it, or it is gone).
    func claim(_ requestID: String) -> String?
    /// False only when the core should hear it again later (no answer).
    func report(requestID: String?, claim: String?, outcome: String, detail: String?, log: String?, version: String?) -> Bool
}

public enum Fetched: Equatable {
    case ok
    /// The release has no such file.
    case notFound
    case failed(String)
}

public protocol ReleaseDownloads: AnyObject {
    /// The asset into `to` (a new file), at most `max` bytes.
    func fetch(repo: String, tag: String, asset: String, max: Int64, to: URL) -> Fetched
}

public enum AdminOutcome: Equatable {
    /// The program ran as root and ended with this exit code (its last
    /// lines of output with it).
    case exited(Int32, String)
    /// The person cancelled the administrator prompt.
    case declined
    /// The prompt itself failed.
    case failed(String)
}

public protocol AdminPrompt: AnyObject {
    /// Runs `program args` as root behind the system's administrator
    /// prompt, to its end.
    func run(_ program: String, _ args: [String], prompt: String) -> AdminOutcome
}

/// A run's names and files (both sides agree on them).
public enum MacUpdateRun {
    public static let resultFile = "result.json"
    public static let logFile = "update.log"
    public static let pendingFile = "pending.json"

    /// yyyyMMdd-HHmmss-xxxxxxxx (UTC, 8 random hex digits).
    public static func isRunName(_ s: String) -> Bool { Pattern.full(s, "[0-9]{8}-[0-9]{6}-[0-9a-f]{8}") }

    public static func newName(_ now: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMdd-HHmmss"
        let hex = (0..<4).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
        return "\(f.string(from: now))-\(hex)"
    }

    /// The account's own updates folder.
    public static func userRoot(home: URL) -> URL {
        home.appendingPathComponent("Library/Application Support/Domovoi/updates", isDirectory: true)
    }

    /// The root-only one (the helper's), under the system layout.
    public static func adminRoot(_ layout: Layout = .system) -> URL { layout.appSupport.appendingPathComponent("updates", isDirectory: true) }

    /// What the app passes the helper (the Mac step; `DomovoiHelper update`).
    public static func packageArguments(from: URL, run: String, tag: String, commit: String?, linuxFirst: Bool, forUID: uid_t) -> [String] {
        var a = ["update", "--run", run, "--from", from.path, "--tag", tag, "--for-uid", String(forUID)]
        if let c = commit { a += ["--commit", c] }
        if linuxFirst { a.append("--linux-first") }
        return a
    }

    /// The Linux part alone (the menu's "Update Domovoi..." when the Mac
    /// side is current).
    public static func linuxArguments(run: String) -> [String] { ["update", "--linux-only", "--run", run] }
}

/// result.json in the root-only run folder, written by the helper.
public struct MacRunResult: Codable, Equatable {
    public var schema = 1
    public var ok: Bool
    /// A MacOutcome when it didn't go (a refusal, the repair), else nil.
    public var outcome: String?
    public var error: String?
    /// The Mac side's version after the run (from the package's manifest).
    public var version: String?
    /// updated, nothing_new, failed, or nil when no Linux part ran.
    public var linux: String?
    public var log: String?
    public var at: String?

    public init(ok: Bool, outcome: String? = nil, error: String? = nil, version: String? = nil, linux: String? = nil, log: String? = nil, at: String? = nil) {
        self.ok = ok
        self.outcome = outcome
        self.error = error
        self.version = version
        self.linux = linux
        self.log = log
        self.at = at
    }

    public static func read(_ url: URL) -> MacRunResult? {
        guard let d = try? Data(contentsOf: url), d.count < 64 * 1024 else { return nil }
        return try? JSONDecoder().decode(MacRunResult.self, from: d)
    }

    public func data() throws -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys]
        return try e.encode(self) + Data("\n".utf8)
    }
}

public struct MacStepResult: Equatable {
    public var outcome: String
    public var detail: String?
    public var log: String?
    /// The release that was installed (ok).
    public var version: String?
    /// The run's name, when one was started.
    public var run: String?
    /// "package" or "linux".
    public var kind: String?
    /// The helper ran (behind the prompt).
    public var launched = false

    public init(outcome: String, detail: String? = nil, log: String? = nil, version: String? = nil) {
        self.outcome = outcome
        self.detail = detail
        self.log = log
        self.version = version
    }

    public var ok: Bool { outcome == MacOutcome.ok || outcome == MacOutcome.nothingNew }
}

public final class MacStepEnv {
    public var downloads: ReleaseDownloads
    public var admin: AdminPrompt
    /// PinnedRelease.key: the release key the install pinned.
    public var key: Data?
    /// The installed Mac side's version (share/build.json).
    public var installed: String?
    /// Where releases come from (share/build.json, else the project's).
    public var releaseRepo: String = MacReleaseNames.defaultRepo
    /// The installed helper, or nil when this Mac's Domovoi wasn't installed
    /// by the package.
    public var helper: String?
    /// ~/Library/Application Support/Domovoi/updates.
    public var updatesRoot: URL
    /// /Library/Application Support/Domovoi/updates.
    public var adminRoot: URL = MacUpdateRun.adminRoot()
    /// This account's uid (the helper checks the run folder is its own).
    public var uid: uid_t = getuid()
    public var now: () -> Date = { Date() }

    public init(downloads: ReleaseDownloads, admin: AdminPrompt, updatesRoot: URL) {
        self.downloads = downloads
        self.admin = admin
        self.updatesRoot = updatesRoot
    }

    /// The real one: the installed package's facts (share/ under the system
    /// layout), this account's folder, curl, the system's prompt.
    public static func installed(layout: Layout = .system, runner: CommandRunner = ProcessRunner()) -> MacStepEnv {
        let env = MacStepEnv(downloads: CurlDownloads(runner: runner), admin: OsascriptAdmin(runner: runner),
                             updatesRoot: MacUpdateRun.userRoot(home: FileManager.default.homeDirectoryForCurrentUser))
        let share = layout.share
        env.key = PinnedRelease.key(share: share)
        let build = PinnedRelease.build(share: share)
        env.installed = build?.version
        if let r = build?.releaseRepo, MacReleaseNames.isRepo(r) { env.releaseRepo = r }
        env.helper = FileManager.default.isExecutableFile(atPath: layout.helperProgram.path) ? layout.helperProgram.path : nil
        env.adminRoot = MacUpdateRun.adminRoot(layout)
        return env
    }
}

public enum MacStep {
    public static let maxPackageBytes: Int64 = 1 << 30
    static let maxSmall: Int64 = 64 * 1024
    public static let prompt = AdminScript.prompt("Domovoi needs an administrator to update this Mac's part of Domovoi.")

    /// What happens next: a result already (nothing to start), or the helper
    /// to start behind the prompt.
    public enum Prepared: Equatable {
        case done(MacStepResult)
        case launch(run: String, kind: String, version: String?, args: [String])
    }

    static func r(_ outcome: String, _ detail: String?) -> Prepared { .done(MacStepResult(outcome: outcome, detail: detail)) }

    /// Everything up to the administrator prompt. `linuxFirst`: the menu's
    /// own "Update Domovoi..." (the Linux part runs first, in the helper);
    /// false for a dashboard request.
    public static func prepare(_ env: MacStepEnv, _ s: MacUpdateState?, linuxFirst: Bool) -> Prepared {
        guard let s else {
            return r(MacOutcome.failed, "Domovoi on this Mac isn't answering, so there's nothing to update from. Check that it's running (the menu-bar icon says so), then try again.")
        }
        if s.internet == "never" {
            return r(MacOutcome.internetOff, "Internet access is turned off for this Domovoi (Settings > Privacy > Internet), and updates come from Domovoi's releases on the internet.")
        }
        guard env.helper != nil else {
            return r(MacOutcome.notInstalled, "This Mac's Domovoi wasn't installed by Domovoi's Mac package, so it can't update itself. Open the newer DomovoiSetup-mac.pkg by hand.")
        }
        if s.linuxBusy { return r(MacOutcome.failed, "Domovoi is updating or restarting right now. Try again when it's done.") }
        let mac = s.stepNeeded && s.targetTag != nil
        if !mac && !linuxFirst { return r(MacOutcome.nothingNew, "The Mac side is already up to date.") }
        let run = MacUpdateRun.newName(env.now())
        let dir = env.updatesRoot.appendingPathComponent(run, isDirectory: true)
        if !mac {
            // Only the Linux part: the installed helper, as root.
            return .launch(run: run, kind: "linux", version: nil, args: MacUpdateRun.linuxArguments(run: run))
        }
        let tag = s.targetTag!
        let version = s.targetVersion ?? String(tag.dropFirst())
        guard env.key != nil else {
            return r(MacOutcome.noKey, "This Mac side pins no release key, so it can't check a downloaded package. Open the newer DomovoiSetup-mac.pkg by hand.")
        }
        do {
            try FileManager.default.createDirectory(at: env.updatesRoot, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: env.updatesRoot.path)
            // An earlier run's leftovers (a refusal keeps its small files to
            // look at, until the next try).
            for old in (try? FileManager.default.contentsOfDirectory(atPath: env.updatesRoot.path)) ?? [] where MacUpdateRun.isRunName(old) {
                try? FileManager.default.removeItem(at: env.updatesRoot.appendingPathComponent(old))
            }
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        } catch {
            return r(MacOutcome.failed, "Domovoi couldn't make its download folder (\(error.localizedDescription)). Nothing was installed.")
        }
        for name in MacReleaseNames.all {
            let max = name == MacReleaseNames.package ? maxPackageBytes : maxSmall
            switch env.downloads.fetch(repo: env.releaseRepo, tag: tag, asset: name, max: max, to: dir.appendingPathComponent(name)) {
            case .ok: continue
            case .notFound:
                try? FileManager.default.removeItem(at: dir)
                return noSigned(version)
            case .failed(let why):
                try? FileManager.default.removeItem(at: dir)
                return r(MacOutcome.downloadFailed, "Domovoi \(version)'s Mac package couldn't be downloaded (\(why)). Nothing was installed. Try again later.")
            }
        }
        let files = MacSetupFiles(manifest: try? Data(contentsOf: dir.appendingPathComponent(MacReleaseNames.manifest)),
                                  manifestSig: try? Data(contentsOf: dir.appendingPathComponent(MacReleaseNames.manifestSig)),
                                  packageSig: try? Data(contentsOf: dir.appendingPathComponent(MacReleaseNames.packageSig)),
                                  package: dir.appendingPathComponent(MacReleaseNames.package))
        let v = MacSetupCheck.run(MacSetupExpectation(tag: tag, commit: s.targetCommit, installed: env.installed, key: env.key), files)
        if let bad = v.problem {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent(MacReleaseNames.package))
            if v.code == MacOutcome.notNewer { return r(MacOutcome.notNewer, "Domovoi didn't install the downloaded package: \(bad).") }
            if v.code == MacOutcome.noSignedSetup { return noSigned(version) }
            return r(v.code ?? MacOutcome.badSignature, "Domovoi didn't install the downloaded package: \(bad). It was deleted.")
        }
        return .launch(run: run, kind: "package", version: v.manifest?.version,
                       args: MacUpdateRun.packageArguments(from: dir, run: run, tag: tag, commit: s.targetCommit, linuxFirst: linuxFirst, forUID: env.uid))
    }

    static func noSigned(_ version: String) -> Prepared {
        r(MacOutcome.noSignedSetup, "Domovoi \(version) has no signed Mac package, so the Mac side can't update from it. Nothing was installed.")
    }

    /// The prompt, then the run's outcome.
    public static func launch(_ env: MacStepEnv, run: String, kind: String, version: String?, args: [String]) -> MacStepResult {
        guard let helper = env.helper else { return MacStepResult(outcome: MacOutcome.notInstalled, detail: "The helper isn't installed.") }
        var res: MacStepResult
        switch env.admin.run(helper, args, prompt: prompt) {
        case .declined:
            res = MacStepResult(outcome: MacOutcome.declined)
        case .failed(let why):
            res = MacStepResult(outcome: MacOutcome.failed, detail: "macOS didn't start the update: \(why).")
        case .exited(let code, let tail):
            res = finish(adminRoot: env.adminRoot, run: run, exitCode: code, kind: kind, version: version, tail: tail)
            res.launched = true
        }
        res.run = run
        res.kind = kind
        return res
    }

    /// How a run went, from its result file in the root-only run folder
    /// (adminRoot/run/result.json, which no other account can write).
    public static func finish(adminRoot: URL, run: String, exitCode: Int32, kind: String, version: String?, tail: String = "") -> MacStepResult {
        let dir = adminRoot.appendingPathComponent(run, isDirectory: true)
        let package = kind == "package"
        let defaultLog = dir.appendingPathComponent(MacUpdateRun.logFile).path
        guard let rr = MacRunResult.read(dir.appendingPathComponent(MacUpdateRun.resultFile)) else {
            let said = tail.trimmingCharacters(in: .whitespacesAndNewlines)
            return MacStepResult(outcome: package ? MacOutcome.repairFailed : MacOutcome.failed,
                                 detail: "The update ended without saying how it went (exit code \(exitCode))\(said.isEmpty ? "" : ": " + said).",
                                 log: FileManager.default.fileExists(atPath: defaultLog) ? defaultLog : nil)
        }
        let log = rr.log ?? defaultLog
        if !rr.ok, let o = rr.outcome, [MacOutcome.badSignature, MacOutcome.notNewer, MacOutcome.noKey, MacOutcome.noSignedSetup].contains(o) {
            // The helper refused the copy it checked again: never installed.
            return MacStepResult(outcome: o, detail: "\(rr.error ?? "Domovoi didn't install the package").")
        }
        if rr.ok {
            if !package && rr.linux == "nothing_new" { return MacStepResult(outcome: MacOutcome.nothingNew, detail: "Domovoi is up to date.") }
            return MacStepResult(outcome: MacOutcome.ok, log: log, version: package ? (rr.version ?? version) : nil)
        }
        let err = rr.error ?? "exit code \(exitCode)"
        if rr.linux == "failed" {
            return MacStepResult(outcome: MacOutcome.linuxFailed, detail: "The Linux part didn't update: \(err).\(package ? " Nothing on the Mac was changed." : "")", log: log)
        }
        return MacStepResult(outcome: package ? MacOutcome.repairFailed : MacOutcome.failed,
                             detail: package ? "The package couldn't update the Mac side: \(err)." : "The update didn't finish: \(err).", log: log)
    }

    /// What the notification or the message says. `afterLinux`: this run was
    /// asked for by a press that updated the Linux side first.
    public static func words(_ r: MacStepResult, afterLinux: Bool) -> String {
        switch r.outcome {
        case MacOutcome.ok: return r.version.map { "Domovoi is updated: the Mac side is now \($0)." } ?? "Domovoi is updated."
        case MacOutcome.nothingNew: return r.detail ?? "Domovoi is up to date."
        case MacOutcome.declined:
            return afterLinux
                ? "macOS didn't get an administrator's password, so the update is half done: the Linux part is updated, the Mac part isn't. Choose \"Update Domovoi...\" in the Domovoi menu to try again."
                : "macOS didn't get an administrator's password, so nothing was updated. Choose \"Update Domovoi...\" in the Domovoi menu to try again."
        default:
            return (r.detail ?? "The update didn't finish.") + (r.log.map { " The update's log: \($0)" } ?? "")
        }
    }
}

/// What the app does with a poll's answer.
public enum MacUpdateAction: Equatable { case nothing, startNow, notify }

public enum MacUpdateDecision {
    /// A dashboard request the app hasn't acted on: from a browser on this
    /// Mac, the Mac step at once (the prompt is next, in front of the person
    /// who just clicked); from another device, a notification to click.
    /// Nothing while a step runs, while an earlier run's outcome hasn't
    /// reached the core, when another app has claimed it, or when the
    /// request no longer needs a step.
    public static func onState(_ s: MacUpdateState?, seenRequest: String?, stepRunning: Bool, pendingRun: Bool) -> MacUpdateAction {
        guard let s, let id = s.requestID, id != seenRequest, !stepRunning, !pendingRun else { return .nothing }
        if !s.stepNeeded || s.linuxBusy || s.requestClaimed { return .nothing }
        return s.requestOnThisMac ? .startNow : .notify
    }
}

/// pending.json: the run an app started, until its outcome reaches the core.
public struct MacPendingRun: Codable, Equatable {
    public var run: String
    public var requestID: String?
    public var claim: String?
    public var kind: String
    public var started: String
    public var version: String?
    public var outcome: String?
    public var detail: String?
    public var log: String?
    public var shown = false
    public var afterLinux = false

    enum CodingKeys: String, CodingKey {
        case run, claim, kind, started, version, outcome, detail, log, shown
        case requestID = "request"
        case afterLinux = "after_linux"
    }

    public init(run: String, requestID: String?, claim: String?, kind: String, started: String, version: String?, afterLinux: Bool) {
        self.run = run
        self.requestID = requestID
        self.claim = claim
        self.kind = kind
        self.started = started
        self.version = version
        self.afterLinux = afterLinux
    }

    public static func path(_ root: URL) -> URL { root.appendingPathComponent(MacUpdateRun.pendingFile) }

    public static func load(_ root: URL) -> MacPendingRun? {
        guard let d = try? Data(contentsOf: path(root)), let p = try? JSONDecoder().decode(MacPendingRun.self, from: d),
              MacUpdateRun.isRunName(p.run) else { return nil }
        return p
    }

    public func save(_ root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys]
        try LocalFileOps().writeAtomically(try e.encode(self), to: Self.path(root), mode: 0o600)
    }

    public static func clear(_ root: URL) { try? FileManager.default.removeItem(at: path(root)) }
}

public struct MacPendingDone: Equatable {
    public var result: MacStepResult
    public var afterLinux: Bool
    /// Not shown to the person yet (the app that started it was ended).
    public var show: Bool
}

/// The app's side of it, for the menu-bar app: one poll every 30 s, and the
/// step when a request or the menu asks for it.
public final class MacUpdateAgent {
    public let server: MacUpdateServer
    public let env: MacStepEnv
    public var iso: (Date) -> String = { HostFiles.iso($0) }

    public init(server: MacUpdateServer, env: MacStepEnv) {
        self.server = server
        self.env = env
    }

    var root: URL { env.updatesRoot }

    /// One poll, in the order that keeps a finished run from starting
    /// another: first an earlier run's outcome goes to the core (which ends
    /// the request it answers), THEN the state is read, and only then is a
    /// request acted on.
    public func poll(seenRequest: String?, stepRunning: Bool) -> (action: MacUpdateAction, state: MacUpdateState?, done: MacPendingDone?) {
        let done = stepRunning ? nil : finishPending()
        let s = server.state()
        return (MacUpdateDecision.onState(s, seenRequest: seenRequest, stepRunning: stepRunning, pendingRun: MacPendingRun.load(root) != nil), s, done)
    }

    /// A run an earlier app started: its outcome from the run's result file,
    /// reported with its claim. Nil while it still runs, or until the core
    /// has it.
    public func finishPending() -> MacPendingDone? {
        guard var p = MacPendingRun.load(root) else { return nil }
        if p.outcome == nil {
            let started = ISO8601DateFormatter().date(from: p.started)
            let old = started.map { env.now().timeIntervalSince($0) > 3 * 3600 } ?? true
            let rr = MacRunResult.read(env.adminRoot.appendingPathComponent(p.run).appendingPathComponent(MacUpdateRun.resultFile))
            if rr == nil && !old { return nil }    // still running
            let fin = MacStep.finish(adminRoot: env.adminRoot, run: p.run, exitCode: rr?.ok == true ? 0 : 1, kind: p.kind, version: p.version)
            p.outcome = fin.outcome
            p.detail = fin.detail
            p.log = fin.log
            p.version = fin.version ?? p.version
            try? p.save(root)
        }
        guard server.report(requestID: p.requestID, claim: p.claim, outcome: p.outcome!, detail: p.detail, log: p.log, version: p.version) else { return nil }
        MacPendingRun.clear(root)
        var r = MacStepResult(outcome: p.outcome!, detail: p.detail, log: p.log, version: p.version)
        r.run = p.run
        return MacPendingDone(result: r, afterLinux: p.afterLinux, show: !p.shown)
    }

    /// The step: for the dashboard's request (`fromMenu` false: claimed
    /// first; another account's app may have it), or the menu's own "Update
    /// Domovoi..." (`fromMenu`: both halves unless `linuxFirst` is false; it
    /// answers a waiting request too, and then the Linux part, which that
    /// press already did, isn't run again).
    public func run(_ s: MacUpdateState?, fromMenu: Bool, linuxFirst wantLinux: Bool = true) -> (result: MacStepResult, afterLinux: Bool) {
        var requestID: String?
        var claim: String?
        var afterLinux = false
        // One run at a time: an earlier one (this app's, before it was ended)
        // is reported first, and one still running is waited for.
        if MacPendingRun.load(root) != nil {
            _ = finishPending()
            if MacPendingRun.load(root) != nil {
                return (MacStepResult(outcome: MacOutcome.failed, detail: "Domovoi is updating this Mac right now. Try again when it's done."), false)
            }
        }
        if let s, let id = s.requestID, !s.requestClaimed, s.stepNeeded {
            if let c = server.claim(id) {
                requestID = id
                claim = c
                afterLinux = s.requestAfterLinux
            } else if !fromMenu {
                return (MacStepResult(outcome: MacOutcome.nothingNew, detail: "Another account on this Mac is doing this update."), false)
            }
        } else if !fromMenu {
            return (MacStepResult(outcome: MacOutcome.nothingNew, detail: "There is no update waiting."), false)
        }
        // A dashboard press that updated the Linux side first never asks for
        // it again; the menu's own run does both halves.
        let linuxFirst = fromMenu && wantLinux && requestID == nil
        switch MacStep.prepare(env, s, linuxFirst: linuxFirst) {
        case .done(let r):
            _ = server.report(requestID: requestID, claim: claim, outcome: r.outcome, detail: r.detail, log: r.log, version: r.version)
            return (r, afterLinux)
        case let .launch(run, kind, version, args):
            var p = MacPendingRun(run: run, requestID: requestID, claim: claim, kind: kind, started: iso(env.now()), version: version, afterLinux: afterLinux)
            p.shown = true
            try? p.save(root)
            let r = MacStep.launch(env, run: run, kind: kind, version: version, args: args)
            // The helper has its own checked copy by now (or the prompt was
            // declined): the account's ~100 MB download goes.
            try? FileManager.default.removeItem(at: env.updatesRoot.appendingPathComponent(run))
            p.outcome = r.outcome
            p.detail = r.detail
            p.log = r.log
            p.version = r.version ?? p.version
            try? p.save(root)
            if server.report(requestID: requestID, claim: claim, outcome: r.outcome, detail: r.detail, log: r.log, version: r.version) {
                MacPendingRun.clear(root)
            }
            return (r, afterLinux)
        }
    }
}

// ─── the real ones ──────────────────────────────────────────────────────

/// The core's loopback routes (Lima's forward of 127.0.0.1:6370 into the
/// VM, where the core sees a loopback peer: domovoi/local_peer.py). Never a
/// proxy.
public final class LoopbackMacUpdateServer: MacUpdateServer {
    let base: String
    let appVersion: String?
    let session: URLSession

    public init(core: String = "http://127.0.0.1:\(Ports.real.core)", appVersion: String?) {
        base = core.hasSuffix("/") ? String(core.dropLast()) : core
        self.appVersion = appVersion
        let c = URLSessionConfiguration.ephemeral
        c.connectionProxyDictionary = [:]
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: c)
    }

    func send(_ method: String, _ path: String, _ body: [String: Any?]? = nil) -> (Int, Data)? {
        guard let u = URL(string: base + path) else { return nil }
        var req = URLRequest(url: u)
        req.httpMethod = method
        req.timeoutInterval = 10
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body.mapValues { $0 ?? NSNull() })
        }
        let done = DispatchSemaphore(value: 0)
        var out: (Int, Data)?
        session.dataTask(with: req) { data, resp, _ in
            if let h = resp as? HTTPURLResponse { out = (h.statusCode, data ?? Data()) }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 12)
        return out
    }

    public func state() -> MacUpdateState? {
        var path = "/v1/mac/update"
        if let v = appVersion, let q = v.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: ".-+"))) { path += "?app=" + q }
        guard let res = send("GET", path), res.0 == 200 else { return nil }
        return MacUpdateState.decode(res.1)
    }

    public func claim(_ requestID: String) -> String? {
        guard let res = send("POST", "/v1/mac/update/claim", ["request_id": requestID]), res.0 == 200,
              let o = (try? JSONSerialization.jsonObject(with: res.1)) as? [String: Any] else { return nil }
        return o["claim"] as? String
    }

    public func report(requestID: String?, claim: String?, outcome: String, detail: String?, log: String?, version: String?) -> Bool {
        let d = detail.map { String($0.prefix(400)) }
        guard let res = send("POST", "/v1/mac/update/result",
                             ["request_id": requestID, "claim": claim, "outcome": outcome, "detail": d, "log": log, "version": version]) else {
            return false
        }
        // A refusal (403: not this app's claim; 422) won't change on a retry.
        return res.0 < 500
    }
}

/// A release's files from GitHub (MacReleaseNames.url) with curl: https
/// only, redirects too (GitHub hands assets to its download host), a size
/// cap, the system's proxy settings as curl reads them.
public final class CurlDownloads: ReleaseDownloads {
    let runner: CommandRunner
    public init(runner: CommandRunner) { self.runner = runner }

    public func fetch(repo: String, tag: String, asset: String, max: Int64, to: URL) -> Fetched {
        guard let url = try? MacReleaseNames.url(repo: repo, tag: tag, asset: asset) else { return .failed("not a release file") }
        let part = to.appendingPathExtension("part")
        try? FileManager.default.removeItem(at: part)
        let r: CommandResult
        do {
            r = try runner.run(Command("/usr/bin/curl", ["-sSL", "--fail", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
                                                         "--max-filesize", String(max), "--connect-timeout", "30", "--retry", "2",
                                                         "-A", "DomovoiMac", "-o", part.path, "-w", "%{http_code}", url], timeout: 1800))
        } catch {
            return .failed("\(error)")
        }
        let code = r.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        guard r.ok else {
            try? FileManager.default.removeItem(at: part)
            if code == "404" { return .notFound }
            if r.status == 63 { return .failed("the file is larger than expected") }
            return .failed(code.isEmpty || code == "000" ? r.tail : "GitHub answered \(code)")
        }
        let size = (try? FileManager.default.attributesOfItem(atPath: part.path)[.size] as? NSNumber)?.int64Value ?? -1
        guard size >= 0, size <= max else {
            try? FileManager.default.removeItem(at: part)
            return .failed("the file is larger than expected")
        }
        guard rename(part.path, to.path) == 0 else { return .failed("can't keep the download: \(String(cString: strerror(errno)))") }
        return .ok
    }
}

/// The system's own administrator prompt (`osascript ... with administrator
/// privileges`), as the setup pages use it. Waits for the program to end.
public final class OsascriptAdmin: AdminPrompt {
    let runner: CommandRunner
    public init(runner: CommandRunner) { self.runner = runner }

    static let exitMark = "@@domovoi-exit:"

    /// The shell line osascript runs as root: every word single-quoted (a
    /// quote in one is refused), standard input closed, and the exit code
    /// and last lines printed after, so the shell itself always succeeds.
    public static func shellLine(_ program: String, _ args: [String]) throws -> String {
        for s in [program] + args where s.contains("'") || s.contains("\n") { throw HelperError("an argument has a quote in it: \(s)") }
        let cmd = ([program] + args).map { "'\($0)'" }.joined(separator: " ")
        return "out=$(\(cmd) </dev/null 2>&1); s=$?; printf '\(exitMark)%s\\n' \"$s\"; printf '%s\\n' \"$out\" | tail -n 5"
    }

    public static func appleScript(_ shell: String, prompt: String) -> String {
        func esc(_ s: String) -> String { s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }
        return "do shell script \"\(esc(shell))\" with prompt \"\(esc(prompt))\" with administrator privileges without altering line endings"
    }

    /// The program's exit code and its last lines, from osascript's output.
    public static func parse(_ stdout: String) -> (Int32, String)? {
        let lines = stdout.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        guard let i = lines.firstIndex(where: { $0.hasPrefix(exitMark) }), let code = Int32(lines[i].dropFirst(exitMark.count)) else { return nil }
        return (code, lines[(i + 1)...].joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines))
    }

    public func run(_ program: String, _ args: [String], prompt: String) -> AdminOutcome {
        let shell: String
        do { shell = try Self.shellLine(program, args) } catch { return .failed("\(error)") }
        let r: CommandResult
        do {
            r = try runner.run(Command("/usr/bin/osascript", ["-e", Self.appleScript(shell, prompt: prompt)]))
        } catch {
            return .failed("\(error)")
        }
        if r.stderr.contains("-128") { return .declined }
        guard r.ok, let parsed = Self.parse(r.stdout) else { return .failed(r.tail.isEmpty ? "osascript exit \(r.status)" : r.tail) }
        return .exited(parsed.0, parsed.1)
    }
}
