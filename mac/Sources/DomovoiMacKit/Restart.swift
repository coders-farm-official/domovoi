import Foundation

/// The menu's "Restart Domovoi..." (mac-installer PLAN 4.9, 4.8's recovery
/// ladder), done by the root helper behind the administrator prompt: for
/// when the dashboard itself can't be reached.
///
/// One at a time, with the core's own rule (domovoi/self_restart.py
/// `underway`): systemd's state of domovoi-update.service inside the VM is
/// the source of truth, so a restart or update started any way (the
/// dashboard, another account's menu, ssh) refuses another while it runs.
/// The helper's busy lock adds Setup's install, repair and uninstall.
///
/// The ladder, lowest rung that fits:
/// - a service switched off in Login Items & Extensions: refused, and said
///   (the owner's switch is never overridden);
/// - a service not loaded: loaded again (`launchctl bootstrap`); loaded but
///   not running: started (`kickstart`); Ollama running but silent:
///   restarted (`kickstart -k`);
/// - the VM unreachable: its job restarted (`kickstart -k`), then waited for;
///   the Linux side starts its own services as it boots;
/// - the VM fine: what the dashboard's Restart does, `systemctl --no-block
///   start domovoi-update.service` (with nothing new it stops and starts
///   core and web and checks them), or, without that unit, a restart of
///   core and web.
///
/// Then the helper waits for Domovoi to answer (the dashboard's /api/health
/// saying "ok", which asks the core and the database). When that rung was
/// the services' restart and Domovoi still doesn't answer, it climbs one
/// rung, once per press (owner decision 2026-11-03): the VM's restart, the
/// same as for a VM that can't be reached, with the same rules (the owner's
/// switch, one restart or update at a time), then waits again. What it says
/// at the end is what was done and how Domovoi is: back, or still not
/// answering and what to do next.
public struct RestartFacts: Equatable {
    public struct Job: Equatable {
        public var plist = true
        public var disabled = false
        public var loaded = true
        public var running = true
        public init(plist: Bool = true, disabled: Bool = false, loaded: Bool = true, running: Bool = true) {
            self.plist = plist
            self.disabled = disabled
            self.loaded = loaded
            self.running = running
        }
    }

    public var installed = true
    public var ownOllama = true
    public var vm = Job()
    public var ollama = Job()
    public var ollamaAnswers = true
    /// The speech service (PHASE8A-STT.md), when one is installed. Never a
    /// reason to refuse: switched off or missing, the core recognises speech
    /// itself, and Health says so.
    public var ownStt = false
    public var stt = Job()
    public var sttAnswers = true
    /// `limactl shell` gets a command through.
    public var vmReachable = true
    /// domovoi-update.service's LoadState is "loaded" (nil: unknown).
    public var updateUnitLoaded: Bool?
    /// Its ActiveState (nil: unknown).
    public var updateUnitState: String?

    public init() {}
}

public enum RestartStep: Equatable {
    case bootstrap(String)
    case kickstart(String, kill: Bool)
    case waitVM
    case startUpdateUnit
    case restartUnits

    /// For helper.log, in words (Train 1 finding 17: it logged
    /// "[DomovoiMacKit.RestartStep.startUpdateUnit]").
    public var words: String {
        switch self {
        case .bootstrap(let l): return "load \(l)"
        case .kickstart(let l, let kill): return kill ? "restart \(l) (stop it first)" : "start \(l)"
        case .waitVM: return "wait for the virtual machine"
        // It downloads nothing (apply-update.sh never fetches): it restarts
        // Domovoi's services, and loads an update only when the dashboard
        // had already downloaded one (S2-4 review: "pull" was wrong).
        case .startUpdateUnit: return "start \(RestartPlan.updateUnit) (restart Domovoi's services, and load an update the dashboard already downloaded, if any)"
        case .restartUnits: return "restart " + RestartPlan.units.joined(separator: " and ")
        }
    }
}

public enum RestartPlan {
    /// The core's own words (self_restart._already_under_way).
    public static let underWay = "a restart or update is already under way; wait for it to finish"
    public static let busyStates: Set<String> = ["activating", "active", "deactivating", "reloading"]
    public static let updateUnit = "domovoi-update.service"
    public static let units = ["domovoi-core.service", "domovoi-web.service"]

    /// Installed, every job file there, none switched off by the owner.
    static func checkSwitches(_ f: RestartFacts) throws {
        guard f.installed else { throw HelperError("Domovoi isn't installed on this Mac") }
        var jobs: [(String, RestartFacts.Job, String)] = []
        if f.ownOllama { jobs.append((Labels.ollama, f.ollama, MenuStatus.ollamaListedAs)) }
        jobs.append((Labels.vm, f.vm, MenuStatus.vmListedAs))
        for (label, j, listed) in jobs {
            if j.disabled {
                throw HelperError("\(listed) is switched off in \(MenuStatus.loginItemsPlace): switch it on there, then restart Domovoi again")
            }
            if !j.plist { throw HelperError("Domovoi's background service \(label) is missing: open Domovoi from Applications and choose Repair") }
        }
    }

    public static func decide(_ f: RestartFacts) throws -> [RestartStep] {
        try checkSwitches(f)
        var steps: [RestartStep] = []
        if f.ownStt && f.stt.plist && !f.stt.disabled {
            if !f.stt.loaded { steps.append(.bootstrap(Labels.stt)) }
            else if !f.stt.running { steps.append(.kickstart(Labels.stt, kill: false)) }
            else if !f.sttAnswers { steps.append(.kickstart(Labels.stt, kill: true)) }
        }
        if f.ownOllama {
            if !f.ollama.loaded { steps.append(.bootstrap(Labels.ollama)) }
            else if !f.ollama.running { steps.append(.kickstart(Labels.ollama, kill: false)) }
            else if !f.ollamaAnswers { steps.append(.kickstart(Labels.ollama, kill: true)) }
        }
        if !f.vm.loaded { return steps + [.bootstrap(Labels.vm), .waitVM] }
        if !f.vm.running { return steps + [.kickstart(Labels.vm, kill: false), .waitVM] }
        if !f.vmReachable { return steps + [.kickstart(Labels.vm, kill: true), .waitVM] }
        if let s = f.updateUnitState, busyStates.contains(s) { throw HelperError(underWay) }
        return steps + [f.updateUnitLoaded == true ? .startUpdateUnit : .restartUnits]
    }

    /// The one rung up, after the services' restart left Domovoi silent:
    /// the VM's restart, as for a VM that can't be reached. The facts are
    /// read again, so the same rules hold now: the owner's switch, and no
    /// second restart while an update or restart runs.
    public static func escalate(_ f: RestartFacts) throws -> [RestartStep] {
        try checkSwitches(f)
        if let s = f.updateUnitState, busyStates.contains(s) { throw HelperError(underWay) }
        if !f.vm.loaded { return [.bootstrap(Labels.vm), .waitVM] }
        if !f.vm.running { return [.kickstart(Labels.vm, kill: false), .waitVM] }
        return [.kickstart(Labels.vm, kill: true), .waitVM]
    }

    /// A plan that ends in the services' restart (the VM was fine).
    public static func isServicesRestart(_ steps: [RestartStep]) -> Bool {
        steps.last == .startUpdateUnit || steps.last == .restartUnits
    }
}

/// How Domovoi answered after a restart, from this Mac's loopback.
public struct RestartHealth: Equatable {
    /// The dashboard gave a JSON answer.
    public var webAnswers = false
    /// ... with status "ok": it reached the core and the database.
    public var ok = false
    /// The core's /v1/alive answered (asked only when not ok).
    public var coreAlive = false

    public init(webAnswers: Bool = false, ok: Bool = false, coreAlive: Bool = false) {
        self.webAnswers = webAnswers
        self.ok = ok
        self.coreAlive = coreAlive
    }

    public var line: String {
        if ok { return "the dashboard and the core answer" }
        if webAnswers { return coreAlive ? "the dashboard answers but the core isn't ready" : "the dashboard answers but can't reach the core" }
        return coreAlive ? "the core runs but the dashboard doesn't answer" : "neither the dashboard nor the core answers"
    }
}

/// A restart refused in its checks, before any step ran: nothing was
/// touched, so it doesn't count toward the two-minute limit (RestartService).
public struct RestartRefused: Error, CustomStringConvertible, Equatable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var description: String { message }
}

/// What a press of Restart came to, said to the household as it is.
public struct RestartResult: Equatable {
    public var back: Bool
    /// The VM was restarted because the services' restart wasn't enough.
    public var escalated: Bool
    /// One line.
    public var said: String

    public init(back: Bool, escalated: Bool, said: String) {
        self.back = back
        self.escalated = escalated
        self.said = said.replacingOccurrences(of: "\n", with: " ")
    }

    public static let backPrefix = "Domovoi is back"
    /// Every "not back" line starts so (the menu shows it as it is).
    public static let notBackPrefix = "Domovoi is still "
    public static let support = "choose Save logs for support in the Domovoi menu and send the logs to whoever helps you with Domovoi"

    /// The restart service's refusal inside the two minutes after the last
    /// restart (RestartService.tooSoon): its own sentences.
    public static let tooSoonPrefix = "Domovoi was restarted less than two minutes ago"

    /// The menu's alert for a restart that failed or was refused: the
    /// helper's own sentences for "not back" and "too soon" (S2-5: not
    /// "Domovoi didn't restart: Domovoi was restarted ...: wait ..."), else
    /// "didn't restart: why".
    public static func alertText(_ why: String) -> String {
        if why.hasPrefix(notBackPrefix) { return why }
        if why.hasPrefix(tooSoonPrefix) { return why.hasSuffix(".") ? why : why + "." }
        return "Domovoi didn't restart: \(why)."
    }

    /// The menu's notification body for "back": what was done.
    public static func noticeBody(_ out: String) -> String {
        let line = out.split(separator: "\n").first.map { String($0).trimmingCharacters(in: .whitespaces) } ?? ""
        guard line.hasPrefix(backPrefix + ": ") else { return line.isEmpty ? "It answers again." : line }
        let rest = line.dropFirst(backPrefix.count + 2)
        return rest.prefix(1).uppercased() + String(rest.dropFirst())
    }
}

public final class Restarter {
    let layout: Layout
    let runner: CommandRunner
    let sleeper: Sleeper
    let http: HTTPClient
    let log: (String) -> Void
    public var vmWait: Double = 240
    /// For the update unit to finish (with nothing new it takes a minute;
    /// an update takes longer, and is never cut short).
    public var unitWait: Double = 600
    /// For Domovoi to answer after the services' restart.
    public var healthWait: Double = 180
    /// ... after the VM's restart (the Linux side boots first).
    public var vmHealthWait: Double = 300
    /// Before the first look: a --no-block start or restart is under way.
    public var settle: Double = 5
    /// After the services' restart, how long the core may stay silent
    /// (its /v1/alive not answering at all) before the VM's restart is
    /// tried instead of waiting out `healthWait` (Train 1 finding 26: five
    /// minutes of waiting on a rung that couldn't work). A core that boots
    /// answers /v1/alive well inside it.
    public var coreDeadWait: Double = 90

    public init(layout: Layout, runner: CommandRunner, sleeper: Sleeper, http: HTTPClient, log: @escaping (String) -> Void = { _ in }) {
        self.layout = layout
        self.runner = runner
        self.sleeper = sleeper
        self.http = http
        self.log = log
    }

    var launchd: LaunchdInstaller { LaunchdInstaller(domain: "system", runner: runner, sleeper: sleeper, fileOps: LocalFileOps()) }

    func lima(_ rec: InstallRecord) -> Lima {
        Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner,
             runAs: rec.roleAccount ?? Names.roleAccount)
    }

    public func facts(_ rec: InstallRecord?) throws -> RestartFacts {
        var f = RestartFacts()
        guard let rec, rec.uninstalledAt == nil else { f.installed = false; return f }
        f.ownOllama = !rec.answers.ollama.hasPrefix("existing")
        let disabled = (try? runner.run(Command(LaunchdInstaller.launchctl, ["print-disabled", "system"], timeout: 10)))
            .flatMap { $0.ok ? MenuProbe.parseDisabled($0.stdout) : nil } ?? []
        func job(_ label: String) throws -> RestartFacts.Job {
            let s = try launchd.state(label)
            return RestartFacts.Job(plist: FileManager.default.fileExists(atPath: layout.plist(label).path), disabled: disabled.contains(label),
                                    loaded: s != nil, running: s?.running == true)
        }
        f.vm = try job(Labels.vm)
        if f.ownOllama {
            f.ollama = try job(Labels.ollama)
            f.ollamaAnswers = http.get("http://127.0.0.1:\(rec.answers.ports.ollama)/api/version", timeout: 5)?.status == 200
        }
        if rec.sttURL != nil {
            f.ownStt = true
            f.stt = try job(Labels.stt)
            let port = rec.answers.ports.stt ?? Ports.sttDefault
            f.sttAnswers = http.get("http://127.0.0.1:\(port)/health", timeout: 5)?.status == 200
        }
        if f.vm.running {
            let l = lima(rec)
            f.vmReachable = (try? l.status(Names.instance)) == "Running" && ((try? l.shell(Names.instance, ["true"], timeout: 30))?.ok == true)
            if f.vmReachable, let r = try? l.shell(Names.instance, ["systemctl", "show", "--property=LoadState,ActiveState", RestartPlan.updateUnit], timeout: 30), r.ok {
                for line in r.stdout.split(separator: "\n") {
                    let kv = line.split(separator: "=", maxSplits: 1).map(String.init)
                    guard kv.count == 2 else { continue }
                    if kv[0] == "LoadState" { f.updateUnitLoaded = kv[1] == "loaded" }
                    if kv[0] == "ActiveState" { f.updateUnitState = kv[1] }
                }
            }
        }
        return f
    }

    /// Restarts what needs it, waits for Domovoi to answer, and climbs one
    /// rung (the VM) at most once. Throws `RestartRefused` for a refusal in
    /// the checks before any step (nothing was touched); anything else it
    /// throws came from a step, after something may have been restarted
    /// (security review round 2: such a failure counts as having acted).
    @discardableResult
    public func run() throws -> RestartResult {
        let lock: BusyLock
        let rec: InstallRecord
        let steps: [RestartStep]
        do {
            do { lock = try BusyLock(layout.busyLock, what: "restart") } catch {
                throw HelperError("Setup is installing, repairing or removing Domovoi, or another restart is running: wait for it to finish")
            }
            guard let r = try RecordStore.load(layout.installRecord) else { throw HelperError("Domovoi isn't installed on this Mac") }
            rec = r
            steps = try RestartPlan.decide(try facts(rec))
        } catch let e as RestartRefused {
            throw e
        } catch {
            throw RestartRefused("\(error)")
        }
        defer { _ = lock }
        log("restart: " + steps.map(\.words).joined(separator: ", "))
        for s in steps { try run(s, rec) }

        let services = RestartPlan.isServicesRestart(steps)
        var done = [services ? "restarted Domovoi's services" : "restarted its virtual machine"]
        if steps.last == .startUpdateUnit {
            guard waitUpdateUnit(rec) else {
                return said(RestartResult(back: false, escalated: false,
                                          said: "\(RestartResult.notBackPrefix)restarting or updating after \(Int(unitWait / 60)) minutes: it carries on by itself, so give it a few more minutes. If it isn't back after that, \(RestartResult.support)."))
            }
        } else {
            sleeper.sleep(settle)
        }
        var h = waitHealthy(rec, for: services ? healthWait : vmHealthWait, deadCoreAfter: services ? coreDeadWait : nil)
        if h.ok {
            return said(RestartResult(back: true, escalated: false,
                                      said: "\(RestartResult.backPrefix): \(services ? "its services were restarted" : "its virtual machine was restarted")."))
        }
        guard services else { return said(notBack(done, h, escalated: false)) }

        // One rung up, once: the VM.
        log("restart: still not answering after the services' restart (\(h.line)): restarting the virtual machine")
        let up: [RestartStep]
        do {
            up = try RestartPlan.escalate(try facts(rec))
        } catch {
            done.append("didn't restart its virtual machine (\(error))")
            return said(notBack(done, h, escalated: false))
        }
        log("restart: " + up.map(\.words).joined(separator: ", "))
        do {
            for s in up { try run(s, rec) }
        } catch {
            done.append("then tried to restart its virtual machine (\(error))")
            return said(notBack(done, health(rec), escalated: true))
        }
        done.append("then restarted its virtual machine")
        h = waitHealthy(rec, for: vmHealthWait)
        if h.ok {
            return said(RestartResult(back: true, escalated: true,
                                      said: "\(RestartResult.backPrefix): its services were restarted, and when it still didn't answer, its virtual machine was restarted too."))
        }
        return said(notBack(done, h, escalated: true))
    }

    func notBack(_ done: [String], _ h: RestartHealth, escalated: Bool) -> RestartResult {
        RestartResult(back: false, escalated: escalated,
                      said: "\(RestartResult.notBackPrefix)not answering — \(RestartResult.support). What was done: \(done.joined(separator: ", ")); now \(h.line).")
    }

    func said(_ r: RestartResult) -> RestartResult {
        log("restart: \(r.said)")
        return r
    }

    /// The dashboard's /api/health (open to any account), and the core's
    /// /v1/alive when that isn't "ok", for the line saying which is down.
    public func health(_ rec: InstallRecord) -> RestartHealth {
        var h = RestartHealth()
        if let r = http.get("http://127.0.0.1:\(rec.answers.ports.web)/api/health", timeout: 12) {
            let obj = (try? JSONSerialization.jsonObject(with: r.body)) as? [String: Any]
            h.webAnswers = obj != nil
            h.ok = r.status == 200 && obj?["status"] as? String == "ok"
        }
        if !h.ok { h.coreAlive = http.get("http://127.0.0.1:\(rec.answers.ports.core)/v1/alive", timeout: 4) != nil }
        return h
    }

    /// Waits up to `seconds` for "ok". `deadCoreAfter`: stop early once
    /// the core hasn't answered /v1/alive for that long, so the next rung
    /// is tried sooner.
    func waitHealthy(_ rec: InstallRecord, for seconds: Double, deadCoreAfter: Double? = nil) -> RestartHealth {
        let start = sleeper.now()
        var lastAlive = start
        var h = health(rec)
        while !h.ok && sleeper.now().timeIntervalSince(start) < seconds {
            if h.coreAlive { lastAlive = sleeper.now() }
            if let dead = deadCoreAfter, sleeper.now().timeIntervalSince(lastAlive) >= dead {
                log("restart: the core hasn't answered for \(Int(dead)) s (\(h.line))")
                break
            }
            sleeper.sleep(5)
            h = health(rec)
        }
        return h
    }

    /// domovoi-update.service's ActiveState now (nil: unreadable).
    func updateUnitState(_ rec: InstallRecord) -> String? {
        guard let r = try? lima(rec).shell(Names.instance, ["systemctl", "show", "--property=ActiveState", RestartPlan.updateUnit], timeout: 30), r.ok else { return nil }
        for line in r.stdout.split(separator: "\n") where line.hasPrefix("ActiveState=") {
            return String(line.dropFirst("ActiveState=".count))
        }
        return nil
    }

    /// Waits for the update unit (oneshot) to end. false: still running at
    /// the deadline (never cut short, and never a second restart over it).
    func waitUpdateUnit(_ rec: InstallRecord) -> Bool {
        let start = sleeper.now()
        sleeper.sleep(settle)
        while true {
            guard let s = updateUnitState(rec), RestartPlan.busyStates.contains(s) else { return true }
            if sleeper.now().timeIntervalSince(start) >= unitWait { return false }
            sleeper.sleep(5)
        }
    }

    func run(_ s: RestartStep, _ rec: InstallRecord) throws {
        switch s {
        case .bootstrap(let label):
            let r = try runner.run(Command(LaunchdInstaller.launchctl, ["bootstrap", "system", layout.plist(label).path], timeout: 30))
            guard r.ok else {
                let listed = label == Labels.vm ? MenuStatus.vmListedAs : label == Labels.stt ? MenuStatus.sttListedAs : MenuStatus.ollamaListedAs
                throw HelperError("\(label) couldn't be started (exit \(r.status)): \(r.tail). If \(listed) is switched off in \(MenuStatus.loginItemsPlace), switch it on there first")
            }
            _ = try launchd.waitRunning(label)
        case .kickstart(let label, let kill):
            try runner.check(Command(LaunchdInstaller.launchctl, ["kickstart"] + (kill ? ["-k"] : []) + ["system/\(label)"], timeout: 60), "launchctl kickstart \(label)")
        case .waitVM:
            let l = lima(rec)
            let start = sleeper.now()
            while sleeper.now().timeIntervalSince(start) < vmWait {
                if (try? l.status(Names.instance)) == "Running", (try? l.shell(Names.instance, ["true"], timeout: 30))?.ok == true { return }
                sleeper.sleep(3)
            }
            throw HelperError("the virtual machine didn't start in \(Int(vmWait)) s; Save logs for support has the details")
        case .startUpdateUnit:
            try shellCheck(rec, ["sudo", "systemctl", "--no-block", "start", RestartPlan.updateUnit])
        case .restartUnits:
            try shellCheck(rec, ["sudo", "systemctl", "--no-block", "restart"] + RestartPlan.units)
        }
    }

    func shellCheck(_ rec: InstallRecord, _ args: [String]) throws {
        let r = try lima(rec).shell(Names.instance, args, timeout: 60)
        guard r.ok else { throw HelperError("\(args.dropFirst().joined(separator: " ")) failed in the VM (exit \(r.status)): \(r.tail)") }
    }
}
