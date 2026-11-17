import Foundation

/// Gathers the menu-bar icon's facts (MenuStatus), as whichever account is
/// signed in: no password, no root, nothing that changes anything. Every
/// call has a short timeout and none carries a credential.
public final class MenuProbe {
    let layout: Layout
    let http: HTTPClient
    let runner: CommandRunner

    public static let pgrep = "/usr/bin/pgrep"

    public init(layout: Layout = .system, http: HTTPClient, runner: CommandRunner) {
        self.layout = layout
        self.http = http
        self.runner = runner
    }

    /// The ports of the install the record describes (Domovoi's own when
    /// there is no record).
    public private(set) var ports = Ports.real

    public func gather() -> MenuFacts {
        var f = MenuFacts()
        var rec: InstallRecord?
        do {
            rec = try RecordStore.load(layout.installRecord)
            if let r = rec {
                f.install = r.uninstalledAt == nil ? .installed : .dataKept
                f.ownOllama = !r.answers.ollama.hasPrefix("existing")
                ports = r.answers.ports
            }
        } catch {
            f.install = .unreadable
        }

        // The dashboard asks the core and the database before it answers:
        // any answer with a JSON object counts, whatever its status (the
        // Windows tray's rule); one without says "degraded".
        if let h = http.get("http://127.0.0.1:\(ports.web)/api/health", timeout: 12) {
            let obj = ((try? JSONSerialization.jsonObject(with: h.body)) as? [String: Any]) ?? [:]
            f.dbReachable = obj["db_reachable"] as? Bool == true
            f.coreReachable = obj["domovoi_reachable"] as? Bool == true
            f.coreAlive = obj["core_alive"] as? Bool
            f.web = obj["status"] as? String == "ok" ? .ok : .degraded
        }
        if f.coreAlive == nil { f.coreAlive = http.get("http://127.0.0.1:\(ports.core)/v1/alive", timeout: 4) != nil }

        guard f.install == .installed || f.install == .unreadable else { return f }
        let disabled = disabledLabels()
        let role = rec?.roleAccount ?? Names.roleAccount
        f.vm = job(Labels.vm, program: "limactl", disabled: disabled, role: role)
        if f.ownOllama {
            f.ollama = job(Labels.ollama, program: "ollama", disabled: disabled, role: role)
            if f.web == .ok { f.ollamaAnswers = http.get("http://127.0.0.1:\(ports.ollama)/api/version", timeout: 4)?.status == 200 }
        }
        return f
    }

    /// The labels `launchctl print-disabled system` lists as disabled, or
    /// nil when it can't be read.
    public func disabledLabels() -> Set<String>? {
        guard let r = try? runner.run(Command(LaunchdInstaller.launchctl, ["print-disabled", "system"], timeout: 10)), r.ok else { return nil }
        return Self.parseDisabled(r.stdout)
    }

    /// Lines such as `"com.domovoi.mac.vm" => disabled` (older systems
    /// print `=> true` for disabled).
    public static func parseDisabled(_ text: String) -> Set<String> {
        var out = Set<String>()
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard line.hasPrefix("\""), let arrow = line.range(of: "=>") else { continue }
            let label = line[..<arrow.lowerBound].trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            let value = line[arrow.upperBound...].trimmingCharacters(in: .whitespaces)
            if value == "disabled" || value == "true" { out.insert(label) }
        }
        return out
    }

    func job(_ label: String, program: String, disabled: Set<String>?, role: String) -> JobFact {
        if disabled?.contains(label) == true { return .off }
        guard FileManager.default.fileExists(atPath: layout.plist(label).path) else { return .missing }
        // The process list is open to every account; -U: the role account's
        // copy only (the household's own Ollama app runs one too).
        guard let r = try? runner.run(Command(Self.pgrep, ["-x", "-U", role, program], timeout: 10)) else { return .unknown }
        switch r.status {
        case 0: return .running
        case 1:
            // Switched off in Login Items: macOS boots the job out of launchd
            // (no "disabled" line at all, Train 1 finding 20), so its file is
            // there and launchd doesn't know it. Not while Setup or Restart
            // works on the jobs (the busy lock): they unload and load them.
            if loaded(label) == false && !busy() { return .off }
            return .notRunning
        default: return .unknown
        }
    }

    /// Whether launchd knows the system job (`launchctl print system/LABEL`,
    /// read-only, open to any account): true, false (113, "Could not find
    /// service"), or nil when it can't be told.
    public func loaded(_ label: String) -> Bool? {
        guard let r = try? runner.run(Command(LaunchdInstaller.launchctl, ["print", "system/\(label)"], timeout: 10)) else { return nil }
        if r.status == 0 { return true }
        if r.status == 113 || (r.stdout + r.stderr).contains("Could not find service") { return false }
        return nil
    }

    /// Setup (install, repair, uninstall), an update or a Restart holds
    /// Domovoi's busy lock while it works: read from its status file, never
    /// by locking (any account's lock probe could make root's fail).
    public func busy() -> Bool { BusyLock.held(status: layout.busyStatus) }
}
