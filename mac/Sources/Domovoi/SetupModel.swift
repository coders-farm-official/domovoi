import AppKit
import DomovoiMacKit
import Foundation
import UniformTypeIdentifiers

/// The setup pages' state (mac-installer PLAN 5): the check, the list of
/// changes, the questions, then the work, which the root helper does after
/// one administrator prompt. Nothing on this Mac changes before that prompt.
///
/// The app never runs as root. It runs the helper's read-only `preflight`
/// as the signed-in user; the install, repair and uninstall go through
/// `osascript ... with administrator privileges` (the system's own password
/// prompt), and the helper reports back through files in this account's
/// private temporary folder: its progress lines, and the setup code (a file
/// only this account can read, deleted once read).
final class SetupModel: ObservableObject {
    enum Page: Equatable { case checking, check, consent, internet, media, working, finished, failed, installed, uninstalled }

    @Published var page: Page = .checking
    @Published var report: PreflightReport?
    @Published var preflightError: String?
    @Published var conflict: PortConflict?
    @Published var showConflict = false
    @Published var internet: String?
    @Published var mediaRoot = "/Users/Shared/Domovoi"
    @Published var ollama = "own"
    /// "Keep this Mac awake while it's plugged in" (consent page; on by
    /// default, as the Windows tray's Keep this PC awake).
    @Published var keepAwake = true
    /// "Start this Mac after a power failure" (consent page, desktop Macs
    /// only, and only while FeatureGates.startAfterPowerFailure is on).
    @Published var startAfterPowerFailure = true
    @Published var steps: [StepRow] = []
    @Published var detail = ""
    @Published var failure: String?
    @Published var setupCode: String?
    @Published var setupCodeNote: String?
    /// The finished page's word when provision.sh couldn't download the
    /// phone app (its final line's "phone_app" false); nil otherwise.
    @Published var phoneAppNote: String?
    /// "Restore from a backup" (the media page; S3-4): the house identity
    /// backup's bytes, read once when chosen, and what it says of itself.
    @Published var identityData: Data?
    @Published var identitySummary: IdentityBackup.Summary?
    @Published var identityError: String?
    @Published var busy = false
    @Published var workTitle = "Installing Domovoi"
    @Published var uninstallMessage = ""

    struct StepRow: Identifiable, Equatable {
        var id: String
        var label: String
        var state: String   // waiting, running, done, fail
    }


    // Overrides in debug builds only (see MenuBar.helper).
    #if DEBUG
    let helper: String = ProcessInfo.processInfo.environment["DOMOVOI_HELPER"]
        ?? "/Library/Application Support/Domovoi/bin/DomovoiHelper"
    let uninstaller: String = ProcessInfo.processInfo.environment["DOMOVOI_UNINSTALL"]
        ?? "/Library/Application Support/Domovoi/bin/domovoi-uninstall"
    #else
    let helper = "/Library/Application Support/Domovoi/bin/DomovoiHelper"
    let uninstaller = "/Library/Application Support/Domovoi/bin/domovoi-uninstall"
    #endif
    static let dashboard = URL(string: "http://localhost:6369/")!
    static let internetDoc = URL(string: "https://github.com/coders-farm-official/domovoi/blob/main/docs/INTERNET.md#what-goes-out")!

    var workDir: URL?
    var progressOffset: UInt64 = 0
    var pending = Data()
    var pollTimer: Timer?
    var lastAlive = Date()
    var finalSeen = false
    /// Conflicts still to show, one pop-up each (PLAN 5: one per owner).
    var conflictQueue: [PortConflict] = []

    // ─── the check ─────────────────────────────────────────────────────

    func runPreflight() {
        page = .checking
        preflightError = nil
        let helper = self.helper
        DispatchQueue.global().async {
            let r = Self.run(helper, ["preflight", "--json"])
            DispatchQueue.main.async {
                guard let data = r.out.data(using: .utf8), let rep = try? JSONDecoder().decode(PreflightReport.self, from: data) else {
                    self.preflightError = "Setup couldn't check this Mac: \(r.err.isEmpty ? "the helper gave no answer" : r.err)"
                    self.page = .check
                    return
                }
                self.report = rep
                if rep.installState == "installed" { self.page = .installed; self.startMenuBarIcon(); return }
                self.page = .check
                self.conflictQueue = rep.conflicts.filter { !(self.ollama == "existing" && $0.ollamaVersion != nil) }
                self.nextConflict()
            }
        }
    }

    func nextConflict() {
        if conflictQueue.isEmpty { conflict = nil; showConflict = false; return }
        conflict = conflictQueue.removeFirst()
        showConflict = true
    }

    /// The household's own Ollama is used instead of Domovoi's (PLAN 12.2).
    func useExistingOllama() {
        ollama = "existing"
        if var r = report {
            r.conflicts.removeAll { $0.ollamaVersion != nil }
            if r.conflicts.isEmpty, let i = r.checks.firstIndex(where: { $0.id == "ports" }) {
                r.checks[i].status = "ok"
                r.checks[i].detail = "free (the Ollama already on this Mac will answer Domovoi)"
            }
            r.ok = !r.checks.contains { $0.status == "fail" }
            report = r
        }
        nextConflict()
    }

    var canContinue: Bool { report?.ok == true }

    /// The consent page's power-failure option: behind its feature gate, and
    /// never on a laptop (Preflight: a built-in battery counts as one).
    var offerPowerFailure: Bool { SetupOffers.powerFailure(gate: FeatureGates.startAfterPowerFailure, report: report) }

    // ─── the work ──────────────────────────────────────────────────────

    func startInstall(repair: Bool) {
        failure = nil
        setupCode = nil
        setupCodeNote = nil
        phoneAppNote = nil
        workTitle = repair ? "Repairing Domovoi" : "Installing Domovoi"
        do {
            let dir = try makeWorkDir()
            var answersFile: String?
            if !repair {
                // The backup goes to the helper as a file of this account's,
                // 0600 in its private work folder (IdentityBackup.read).
                var restore: String?
                if let id = identityData, report?.installState != "kept" {
                    let f = dir.appendingPathComponent("identity.json")
                    guard FileManager.default.createFile(atPath: f.path, contents: id, attributes: [.posixPermissions: 0o600]) else {
                        throw HelperError("Setup couldn't hand the house identity backup over")
                    }
                    restore = f.path
                }
                let answers = SetupAnswers(internet: internet ?? "never", mediaRoot: mediaRoot, ollama: ollama, keepAwake: keepAwake,
                                           startAfterPowerFailure: offerPowerFailure ? startAfterPowerFailure : nil, restoreIdentity: restore)
                let f = dir.appendingPathComponent("answers.json")
                try JSONEncoder().encode(answers).write(to: f)
                answersFile = f.path
            }
            let args = SetupCommands.install(forUID: getuid(), codeTo: dir.appendingPathComponent("code").path, answersFile: answersFile)
            steps = InstallSteps.rows(speech: packageHasSpeechService).map { StepRow(id: $0.id, label: $0.label, state: "waiting") }
            detail = "Waiting for an administrator's password..."
            page = .working
            busy = true
            try runAsAdmin(helper, args, out: dir.appendingPathComponent("progress"), err: dir.appendingPathComponent("errors"), background: true) { ok, why in
                if !ok {
                    self.busy = false
                    self.page = repair ? .installed : .media
                    self.failure = why
                    return
                }
                self.detail = "Starting..."
                self.startPolling()
            }
        } catch {
            failure = "\(error)"
        }
    }

    func makeWorkDir() throws -> URL {
        let d = FileManager.default.temporaryDirectory.appendingPathComponent("domovoi-setup-\(UUID().uuidString.prefix(8))", isDirectory: true)
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        for f in ["progress", "errors"] { FileManager.default.createFile(atPath: d.appendingPathComponent(f).path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        workDir = d
        progressOffset = 0
        pending = Data()
        finalSeen = false
        return d
    }

    /// Runs `program args` as root through the system's administrator
    /// prompt. In the background (`&`) the prompt returns once the password
    /// is accepted and the helper reports through `out`; otherwise it
    /// returns when the program ends.
    func runAsAdmin(_ program: String, _ args: [String], out: URL, err: URL, background: Bool, done: @escaping (Bool, String) -> Void) throws {
        let shell = try AdminScript.shell(program, args, out: out.path, err: err.path, background: background)
        let script = AdminScript.appleScript(shell: shell, prompt: AdminScript.prompt("Domovoi Setup needs an administrator to change this Mac."))
        DispatchQueue.global().async {
            let r = Self.run(AdminScript.osascript, ["-e", script])
            DispatchQueue.main.async {
                if r.status == 0 { done(true, ""); return }
                let why = AdminScript.cancelled(r.err) ? "The administrator prompt was cancelled: nothing was changed." : "The administrator prompt failed: \(r.err)"
                done(false, why)
            }
        }
    }

    func startPolling() {
        lastAlive = Date()
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.poll() }
    }

    func poll() {
        guard let dir = workDir, let h = FileHandle(forReadingAtPath: dir.appendingPathComponent("progress").path) else { return }
        defer { try? h.close() }
        h.seek(toFileOffset: progressOffset)
        let chunk = h.readDataToEndOfFile()
        progressOffset += UInt64(chunk.count)
        pending.append(chunk)
        while let nl = pending.firstIndex(of: 0x0a) {
            let line = String(decoding: pending[pending.startIndex..<nl], as: UTF8.self)
            pending.removeSubrange(pending.startIndex...nl)
            handle(line)
        }
        if finalSeen { return }
        // A helper that died without its last line: say so, don't wait for ever.
        if Date().timeIntervalSince(lastAlive) > 10 {
            lastAlive = Date()
            // By name: a standard user can't read a root process's
            // arguments on macOS, so `pgrep -f "... install"` never matches.
            let r = Self.run("/usr/bin/pgrep", ["-x", "DomovoiHelper"])
            if r.status != 0 {
                poll2Final()
            }
        }
    }

    /// One last read, then a failure if the final line never came.
    func poll2Final() {
        guard !finalSeen else { return }
        let err = workDir.flatMap { try? String(contentsOf: $0.appendingPathComponent("errors"), encoding: .utf8) } ?? ""
        finish(ok: false, message: "Setup stopped without finishing. \(err.split(separator: "\n").suffix(3).joined(separator: " "))")
    }

    func handle(_ line: String) {
        guard line.hasPrefix(StdProgress.prefix),
              let obj = try? JSONSerialization.jsonObject(with: Data(line.dropFirst(StdProgress.prefix.count).utf8)) as? [String: Any],
              let step = obj["step"] as? String, let state = obj["state"] as? String else { return }
        let message = obj["message"] as? String ?? ""
        if step == "install" {
            finish(ok: state == "done", message: message)
            return
        }
        if step == "phone-app" {
            if state == "missing" { phoneAppNote = ProvisionProgress.phoneAppMissing }
            return
        }
        if let i = steps.firstIndex(where: { $0.id == step }) {
            switch state {
            case "start": steps[i].state = "running"; detail = steps[i].label + "..."
            case "done": steps[i].state = "done"; if step == "setup-code" { setupCodeNote = message }
            case "fail": steps[i].state = "fail"
            case "progress": detail = message
            default: break
            }
        }
    }

    func finish(ok: Bool, message: String) {
        finalSeen = true
        pollTimer?.invalidate()
        busy = false
        if ok {
            readSetupCode()
            startMenuBarIcon()
            page = .finished
        } else {
            failure = message
            page = .failed
        }
    }

    func readSetupCode() {
        guard let dir = workDir else { return }
        let f = dir.appendingPathComponent("code")
        if let c = try? String(contentsOf: f, encoding: .utf8) {
            let code = c.trimmingCharacters(in: .whitespacesAndNewlines)
            if SetupCode.isWellFormed(code) { setupCode = code }
        }
        try? FileManager.default.removeItem(at: dir)
        workDir = nil
    }

    /// The setup code of an installed Domovoi (one administrator prompt).
    func showSetupCode() {
        do {
            let dir = try makeWorkDir()
            busy = true
            try runAsAdmin(helper, SetupCommands.setupCode(forUID: getuid(), codeTo: dir.appendingPathComponent("code").path),
                           out: dir.appendingPathComponent("progress"), err: dir.appendingPathComponent("errors"), background: false) { ok, why in
                self.busy = false
                if ok {
                    self.readSetupCode()
                    if self.setupCode == nil { self.setupCodeNote = "There is no setup code: the first admin is already set up." }
                } else {
                    self.failure = why
                }
            }
        } catch { failure = "\(error)" }
    }

    // ─── uninstall ─────────────────────────────────────────────────────

    /// The uninstall deletes this app while this process shows the pages
    /// (Setup opened from Applications while the menu-bar icon runs is the
    /// icon's own process). So: the header's icon is read beforehand, and
    /// this account's icon is left running (`forUID`) so the final page
    /// stays until the person closes it (Train 1 S2-19).
    func uninstall(removeEverything: Bool) {
        AppIcon.preload()
        do {
            let dir = try makeWorkDir()
            busy = true
            failure = nil
            let args = SetupCommands.uninstall(removeEverything: removeEverything, forUID: getuid())
            try runAsAdmin(uninstaller, args, out: dir.appendingPathComponent("progress"), err: dir.appendingPathComponent("errors"), background: false) { ok, why in
                self.busy = false
                let out = (try? String(contentsOf: dir.appendingPathComponent("progress"), encoding: .utf8)) ?? ""
                let err = (try? String(contentsOf: dir.appendingPathComponent("errors"), encoding: .utf8)) ?? ""
                try? FileManager.default.removeItem(at: dir)
                // Exit 3 (the prompt reports a failure) is "removed, except
                // one thing to finish by hand": the output says what (P3-8).
                if UninstallReport.appShowsRemoved(promptOK: ok, output: out) {
                    self.uninstallMessage = out.trimmingCharacters(in: .whitespacesAndNewlines)
                    self.page = .uninstalled
                } else {
                    self.failure = why.isEmpty ? err : why + (err.isEmpty ? "" : " " + err)
                }
            }
        } catch { failure = "\(error)" }
    }

    // ─── small things ──────────────────────────────────────────────────

    /// The menu-bar icon in this account's session now, not only from its
    /// next sign-in (the install put its LaunchAgent in place; launchd
    /// starts it for every account as each signs in). As this account: no
    /// password. Already running is fine; a job left loaded by an earlier
    /// uninstall run from here is loaded afresh (MenuBarAgent.startCommands).
    func startMenuBarIcon() {
        let layout = Layout.system
        guard FileManager.default.fileExists(atPath: layout.menuBarAgent.path) else { return }
        DispatchQueue.global().async {
            let runner = ProcessRunner()
            let cmds = MenuBarAgent.startCommands(uid: getuid(), layout: layout, loaded: MenuBarAgent.state(uid: getuid(), runner: runner),
                                                  ownPid: getpid())
            for c in cmds {
                var r = try? runner.run(c)
                // Right after a bootout launchd may still be letting go of
                // the old job ("Bootstrap failed: 5").
                var tries = 1
                while c.arguments.first == "bootstrap", r?.ok != true, tries < 4 {
                    Thread.sleep(forTimeInterval: 1)
                    r = try? runner.run(c)
                    tries += 1
                }
            }
        }
    }

    func openDashboard() { NSWorkspace.shared.open(Self.dashboard) }

    /// System Settings > Privacy & Security > FileVault (owner decision
    /// 2026-10-28: we never change it; the person chooses).
    func openFileVaultSettings() {
        let urls = ["x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?FileVault",
                    "x-apple.systempreferences:com.apple.preference.security?FileVault"]
        for u in urls { if let url = URL(string: u), NSWorkspace.shared.open(url) { return } }
    }

    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    /// Picks the house identity backup the dashboard downloaded (Settings >
    /// About & updates). Checked for shape here, so a wrong file is caught
    /// now and not after a long install; the core checks the key itself.
    func chooseIdentityBackup() {
        let p = NSOpenPanel()
        p.canChooseFiles = true
        p.canChooseDirectories = false
        p.allowsMultipleSelection = false
        p.allowedContentTypes = [.json]
        p.message = "Choose the house identity backup (domovoi-house-identity.json)"
        p.prompt = "Restore This Backup"
        guard p.runModal() == .OK, let u = p.url else { return }
        identityError = nil
        do {
            let attrs = try FileManager.default.attributesOfItem(atPath: u.path)
            if let n = attrs[.size] as? Int, n > IdentityBackup.maxBytes { throw HelperError("this file is far too large to be a house identity backup") }
            let data = try Data(contentsOf: u)
            switch IdentityBackup.check(data) {
            case .success(let sum):
                identityData = data
                identitySummary = sum
            case .failure(let e):
                throw e
            }
        } catch {
            identityData = nil
            identitySummary = nil
            identityError = "\(error is HelperError ? "\(error)" : error.localizedDescription)"
        }
    }

    func forgetIdentityBackup() {
        identityData = nil
        identitySummary = nil
        identityError = nil
    }

    func chooseMediaFolder() {
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.canCreateDirectories = true
        p.allowsMultipleSelection = false
        p.directoryURL = URL(fileURLWithPath: "/Users/Shared")
        p.prompt = "Use This Folder"
        if p.runModal() == .OK, let u = p.url { mediaRoot = u.path }
    }

    static func run(_ program: String, _ args: [String]) -> (status: Int32, out: String, err: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: program)
        p.arguments = args
        let o = Pipe(), e = Pipe()
        p.standardOutput = o
        p.standardError = e
        p.standardInput = FileHandle.nullDevice
        do { try p.run() } catch { return (-1, "", "can't run \(program): \(error.localizedDescription)") }
        let od = o.fileHandleForReading.readDataToEndOfFile()
        let ed = e.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: od, as: UTF8.self), String(decoding: ed, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
    }
}
