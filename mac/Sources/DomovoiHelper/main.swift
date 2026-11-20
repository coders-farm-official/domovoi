import DomovoiMacKit
import Foundation

// DomovoiHelper: the Mac host side's helper (mac-installer PLAN 4.8).
// The system install (root: the role account, /Library, LaunchDaemons in
// the system domain, Domovoi's own ports), run by the setup pages through
// an administrator prompt or by hand with sudo; and the user mode, which
// installs into a test folder as the signed-in user, on test ports.

let usage = """
usage:
  sudo DomovoiHelper install --system [--answers FILE | --internet X --media-root DIR --ollama own|existing]
                             [--repair] [--for-uid UID --code-to FILE]
  DomovoiHelper preflight [--json]                  check this Mac (changes nothing; any account)
  DomovoiHelper status [--json]                     the installed Domovoi's jobs, VM and health
  sudo DomovoiHelper setup-code --for-uid UID --code-to FILE
  sudo DomovoiHelper uninstall [--keep-data | --remove-everything --confirm DELETE] [--for-uid UID]
  sudo DomovoiHelper update --run NAME --from DIR --tag TAG [--commit SHA] --for-uid UID [--linux-first]
                                                    install a checked release package (the Domovoi app runs
                                                    this behind the administrator prompt; MacUpdate.swift)
  sudo DomovoiHelper update --linux-only --run NAME the Linux part alone (the dashboard's own update, in the VM)
  DomovoiHelper app-update poll [--json]            as the Domovoi app does every 30 s: report an earlier run,
                                                    read the state, and start a request made from this Mac
  DomovoiHelper app-update run [--both]             the Mac part now (--both: the Linux part first), behind
                                                    the administrator prompt; answers a waiting request
  sudo DomovoiHelper serve --system                 the running helper (its LaunchDaemon starts it at boot):
                                                    the home network's way in, domovoi.local, the media watcher
  DomovoiHelper network [--json]                    what the running helper last did about the home network
  sudo DomovoiHelper network --sync                 one network sync now (pf redirect, lan.json), then the same

the menu-bar icon's actions (root, the system install):
  sudo DomovoiHelper restart                         restart what needs it (one at a time), wait for
                                                     Domovoi to answer, restart the VM once if it doesn't
  sudo DomovoiHelper save-logs --for-uid UID --to-dir DIR
                                                     the logs, secrets blanked, into DIR
                                                     (that account's own private folder)
  sudo DomovoiHelper power --keep-awake on|off       Keep this Mac awake (pmset -c sleep)
  sudo DomovoiHelper power --start-after-power-failure on|off
                                                     desktop Macs (pmset -a autorestart); only off
                                                     works while its feature gate is off

  --repair     run every step again with the answers this install saved
  --for-uid    the account that ran Setup: it owns the media folders Setup
               makes, and gets the setup code
  --code-to    a new file (in that account's own temporary folder) for the
               setup code, 0600, never in a log

user mode (tests, no root):
  DomovoiHelper install --user-mode --prefix DIR --web-port N --core-port N [options]
  DomovoiHelper plan    --user-mode --prefix DIR ...     what install would do, changing nothing
  DomovoiHelper status  --user-mode --prefix DIR [--wait-healthy SECONDS]
  DomovoiHelper uninstall --user-mode --prefix DIR [--remove-everything --confirm DELETE]

install options:
  --ollama own|existing|existing:PORT   Domovoi's own Ollama (default) or the one already on this Mac
  --ollama-port N          own Ollama's port (user mode: required, not 11434)
  --stt-port N             the speech service's port (user mode: optional, not 6371;
                           without it the user mode installs no speech service)
  --internet always|sometimes|never     (default never)
  --tier floor|standard    (default: from this Mac's memory)
  --media-root DIR         (default PREFIX/Users/Shared/Domovoi)
  --media NAME             a media folder, repeatable (default: Music, Pictures, Videos, Documents, Audiobooks)
  --cpus N --memory-gib N --disk-gib N  the VM (default: from this Mac)
  --provision-script FILE  provision.sh to run in the VM (none: the VM is left as Lima made it)
  --repo-bundle FILE | --repo-url URL   where provision.sh clones from
  --ref REF --track-branch BRANCH --allowed-signers FILE --no-vision-model
  --provision-arg ARG      passed to provision.sh as is, repeatable
  --seed-cache DIR         take verified downloads from DIR first, repeatable
  --seed-models DIR        copy an Ollama models folder in (APFS clone)
  --jobs process|user|gui  how the jobs run: detached processes (default; launchd
                           takes no job from SSH), or launchd's user/UID or gui/UID domain
  --commit SHA             for side.json
  --stop-after STEP        \(Installer.steps.joined(separator: ", "))
  --share DIR              the mac/ folder (default: found above this program)
"""

func installOptions(_ a: inout Args) throws -> InstallOptions {
    let layout = try Machine.layout(&a)
    if layout.mode == .system { return try SetupCommands.systemOptions(&a, layout: layout) }
    guard let web = try a.int("web-port"), let core = try a.int("core-port") else {
        throw HelperError("the user mode needs --web-port and --core-port (test ports, not 6369/6370)")
    }
    let ollama = try OllamaChoice.parse(a.value("ollama") ?? "own")
    var ollamaPort: Int
    switch ollama {
    case .existing(let p): ollamaPort = p
    case .own:
        guard let p = try a.int("ollama-port") else { throw HelperError("the user mode's own Ollama needs --ollama-port (a test port, not 11434)") }
        ollamaPort = p
    }
    if case .existing = ollama, try a.int("ollama-port") != nil { throw HelperError("--ollama-port is for Domovoi's own Ollama; give existing:PORT") }
    let mem = Machine.memoryBytes()
    var size = VMSize.forMac(memoryBytes: mem, cores: Machine.cores())
    if let c = try a.int("cpus") { size.cpus = c }
    if let m = try a.int("memory-gib") { size.memoryGiB = m }
    if let d = try a.int("disk-gib") { size.diskGiB = d }
    var tier = Tier.forMac(memoryBytes: mem)
    if let t = a.value("tier") {
        guard let tt = Tier(rawValue: t) else { throw HelperError("--tier is floor or standard") }
        tier = tt
    }
    var provision: ProvisionSource?
    if let s = a.value("provision-script") {
        var p = ProvisionSource(script: URL(fileURLWithPath: s))
        p.repoBundle = a.value("repo-bundle").map { URL(fileURLWithPath: $0) }
        p.repoURL = a.value("repo-url")
        if p.repoBundle != nil && p.repoURL != nil { throw HelperError("--repo-bundle or --repo-url, not both") }
        p.ref = a.value("ref")
        p.trackBranch = a.value("track-branch")
        p.allowedSigners = a.value("allowed-signers").map { URL(fileURLWithPath: $0) }
        p.noVisionModel = a.flag("no-vision-model")
        p.extraArgs = a.all("provision-arg")
        for f in [p.script, p.repoBundle, p.allowedSigners].compactMap({ $0 }) where !FileManager.default.fileExists(atPath: f.path) {
            throw HelperError("no such file: \(f.path)")
        }
        provision = p
    }
    let mediaRoot = a.value("media-root") ?? layout.defaultMediaRoot.path
    let names = a.all("media")
    var o = InstallOptions(layout: layout, ports: Ports(web: web, core: core, ollama: ollamaPort, stt: try a.int("stt-port")), ollama: ollama,
                           internet: a.value("internet") ?? "never", tier: tier, size: size, mediaRoot: mediaRoot,
                           mediaNames: names.isEmpty ? MediaMount.defaultNames : names, timezone: macTimeZone(),
                           share: try Machine.findShare(a.value("share")), provision: provision,
                           launchdDomain: try Machine.domain(&a, layout: layout))
    o.seedCache = a.all("seed-cache").map { URL(fileURLWithPath: $0, isDirectory: true) }
    o.seedModels = a.value("seed-models").map { URL(fileURLWithPath: $0, isDirectory: true) }
    o.commit = a.value("commit")
    o.stopAfter = a.value("stop-after")
    if let s = o.stopAfter, !Installer.steps.contains(s) { throw HelperError("--stop-after: no step \(s)") }
    return o
}

func statusJSON(_ layout: Layout, _ domain: String, ports: Ports?, role: String? = nil) throws -> [String: Any] {
    let runner = ProcessRunner()
    let l = jobManager(domain: domain, layout: layout, runner: runner, sleeper: RealSleeper(), fileOps: LocalFileOps())
    var out: [String: Any] = ["prefix": layout.root.path, "domain": domain]
    var jobs: [String: Any] = [:]
    for label in [Labels.ollama, Labels.stt, Labels.vm, Labels.helper] {
        if let s = try l.state(label) {
            jobs[label] = ["state": s.state ?? "?", "pid": s.pid.map { Int($0) } as Any, "last_exit": s.lastExit as Any]
        } else {
            jobs[label] = ["state": "not loaded"]
        }
    }
    out["jobs"] = jobs
    let lima = Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner, runAs: role)
    if role != nil && geteuid() != 0 {
        out["vm"] = "unknown (only an administrator can ask the VM's account)"
    } else {
        out["vm"] = FileManager.default.isExecutableFile(atPath: lima.limactl) ? (try lima.status(Names.instance) ?? "missing") : "no limactl"
    }
    if let p = ports {
        let http = URLSessionHTTP()
        out["web_health"] = http.get("http://127.0.0.1:\(p.web)/api/health", timeout: 5)?.status ?? 0
        out["core_health"] = http.get("http://127.0.0.1:\(p.core)/v1/health", timeout: 5)?.status ?? 0
    }
    return out
}

func printJSON(_ obj: Any) {
    if let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) { print(String(decoding: d, as: UTF8.self)) }
}

/// Hands the setup code to the account that ran Setup (never logged).
func handOverSetupCode(_ inst: Installer, _ a: inout Args, progress: StdProgress) throws {
    let to = a.value("code-to")
    let uid = try a.int("for-uid")
    guard let to else { return }
    guard let uid else { throw HelperError("--code-to needs --for-uid") }
    if let code = try inst.readSetupCode() {
        try Handoff.write(code, to: to, owner: uid_t(uid))
        progress.event(step: "setup-code", state: "done", message: "ready")
    } else {
        progress.event(step: "setup-code", state: "done", message: "none (the first admin is already set up)")
    }
}

/// A running install, as the system install's progress: its final event,
/// one JSON line the setup pages wait for.
func finalEvent(_ progress: StdProgress, ok: Bool, _ message: String) {
    progress.event(step: "install", state: ok ? "done" : "fail", message: message)
}

/// The Domovoi app's update, from a terminal (the menu-bar app calls the
/// same MacUpdateAgent): never as root, the prompt asks for the password.
func appUpdate(_ a: inout Args) throws -> Int32 {
    guard !Machine.isRoot else { throw HelperError("app-update runs as the signed-in person, not root: macOS asks for an administrator's password itself") }
    let both = a.flag("both")
    let json = a.flag("json")
    let verb = a.positional.first
    try a.checkAllUsed()
    let env = MacStepEnv.installed()
    let agent = MacUpdateAgent(server: LoopbackMacUpdateServer(appVersion: env.installed), env: env)
    switch verb {
    case "poll":
        let p = agent.poll(seenRequest: nil, stepRunning: false)
        if let d = p.done { print(MacStep.words(d.result, afterLinux: d.afterLinux)) }
        if json {
            let s = p.state
            printJSON(["mac": s?.macVersion as Any, "target": s?.targetTag as Any, "step_needed": s?.stepNeeded ?? false,
                       "why_not": s?.whyNot as Any, "request": s?.requestID as Any, "action": "\(p.action)"])
        }
        guard p.state != nil else { print("Domovoi on this Mac isn't answering."); return 1 }
        switch p.action {
        case .nothing:
            print(p.state?.stepNeeded == true ? "The Mac side can update to \(p.state?.targetVersion ?? "?"): app-update run" : "Nothing to do.")
            return 0
        case .notify:
            print("The dashboard asked for the Mac part: app-update run does it now.")
            return 0
        case .startNow:
            let r = agent.run(p.state, fromMenu: false)
            print(MacStep.words(r.result, afterLinux: r.afterLinux))
            return r.result.ok ? 0 : 1
        }
    case "run":
        _ = agent.finishPending()
        let r = agent.run(agent.server.state(), fromMenu: true, linuxFirst: both)
        print(MacStep.words(r.result, afterLinux: r.afterLinux))
        return r.result.ok ? 0 : 1
    default:
        throw HelperError("app-update poll | run [--both]")
    }
}

func main() -> Int32 {
    do {
        var a = try Args(Array(CommandLine.arguments.dropFirst()))
        if a.flag("help") || a.command == nil { print(usage); return a.command == nil ? 2 : 0 }
        switch a.command! {
        case "install", "plan":
            let parseOnly = a.flag("parse-only")
            let o = try installOptions(&a)
            if parseOnly { _ = a.value("code-to"); _ = try a.int("for-uid") }
            try a.checkAllUsed()
            if parseOnly { print("\(SetupCommands.parseOnlyLine) install \(o.layout.mode.rawValue) internet=\(o.internet) media=\(o.mediaNames.count)"); return 0 }
            if a.command == "plan" {
                try o.check()
                print("would install into \(o.layout.root.path) (launchd \(o.launchdDomain)):")
                for s in Installer.steps { print("  \(s)") ; if s == o.stopAfter { break } }
                print("ports: web \(o.ports.web), core \(o.ports.core), Ollama \(o.ollama.label) \(o.ports.ollama)")
                print("VM: \(o.size.cpus) CPUs, \(o.size.memoryGiB) GiB, disk \(o.size.diskGiB) GiB; tier \(o.tier.rawValue)")
                print("media: " + o.media.map { "\($0.hostPath) -> \($0.guestPath)" }.joined(separator: "; "))
                if let p = o.provision {
                    print("provision.sh " + ProvisionArgs.build(source: p, answers: o.answers, ollamaURL: "http://host.lima.internal:\(o.ports.ollama)").joined(separator: " "))
                }
                return 0
            }
            let layout = o.layout
            let progress = StdProgress(logFile: {
                let u = layout.logs.appendingPathComponent("helper.log")
                return FileManager.default.fileExists(atPath: u.path) ? u : nil
            })
            var lock: BusyLock?
            do {
                if layout.mode == .system {
                    guard Machine.isRoot else { throw HelperError("the system install changes this Mac: run it with sudo, or through Domovoi's setup pages") }
                    lock = try BusyLock(layout.busyLock, what: FileManager.default.fileExists(atPath: layout.installRecord.path) ? "repair" : "install")
                    // As root too: root sees every port's owner. A repair
                    // finds Domovoi's own there and lets them be.
                    let pre = Preflight(layout: layout, runner: ProcessRunner(), http: URLSessionHTTP())
                    var r = pre.run(pins: nil, ollamaMinimum: nil)
                    if case .existing = o.ollama { r.conflicts.removeAll { $0.ports == [Ports.real.ollama] && $0.ollamaVersion != nil } }
                    let blocking = r.checks.filter { $0.status == "fail" && $0.id != "ports" }
                    if let b = blocking.first { throw HelperError("\(b.label): \(b.detail)") }
                    if !r.conflicts.isEmpty {
                        throw HelperError("Domovoi's ports are in use: " + r.conflicts.map { "\($0.owner) (\(Preflight.portList($0.ports)))" }.joined(separator: "; "))
                    }
                }
                let inst = try Installer(options: o, runner: ProcessRunner(), fileOps: LocalFileOps(), http: URLSessionHTTP(),
                                         sleeper: RealSleeper(), progress: progress)
                try inst.run()
                if layout.mode == .system { try handOverSetupCode(inst, &a, progress: progress) }
                progress.log("install finished")
                finalEvent(progress, ok: true, o.stopAfter.map { "stopped after \($0)" } ?? "Domovoi is installed")
            } catch {
                finalEvent(progress, ok: false, "\(error)")
                throw error
            }
            _ = lock
            return 0
        case "preflight":
            let layout = try Machine.layout(&a, defaultSystem: true)
            let json = a.flag("json")
            try a.checkAllUsed()
            let share = try? Machine.findShare(nil)
            let pins = share.flatMap { try? Pins.load($0.appendingPathComponent("installer/pins.conf")) }
            let r = Preflight(layout: layout, runner: ProcessRunner(), http: URLSessionHTTP()).run(pins: pins, ollamaMinimum: pins?.ollamaMinimum)
            if json {
                let enc = JSONEncoder()
                enc.outputFormatting = [.sortedKeys]
                print(String(decoding: try enc.encode(r), as: UTF8.self))
            } else {
                for c in r.checks { print("\(c.status.uppercased().padding(toLength: 5, withPad: " ", startingAt: 0)) \(c.label): \(c.detail)") }
                print(r.ok ? "ready to install" : "not ready")
            }
            return r.ok ? 0 : 3
        case "setup-code":
            let layout = try Machine.layout(&a, defaultSystem: true)
            if a.flag("parse-only") {
                _ = a.value("code-to"); _ = try a.int("for-uid"); _ = a.value("share")
                try a.checkAllUsed()
                print("\(SetupCommands.parseOnlyLine) setup-code \(layout.mode.rawValue)")
                return 0
            }
            guard layout.mode == .system, Machine.isRoot else { throw HelperError("setup-code: the system install, as root") }
            guard let rec = try RecordStore.load(layout.installRecord), rec.uninstalledAt == nil else { throw HelperError("Domovoi isn't installed") }
            let share = try Machine.findShare(a.value("share"))
            let o = InstallOptions(layout: layout, ports: rec.answers.ports, ollama: .own, internet: rec.answers.internet, tier: rec.answers.tier,
                                   size: rec.answers.size, mediaRoot: rec.answers.mediaRoot, mediaNames: rec.answers.media.map(\.name),
                                   timezone: rec.answers.timezone, share: share, provision: nil, launchdDomain: rec.launchdDomain)
            var oo = o
            oo.role = rec.roleAccount
            let progress = StdProgress()
            let inst = try Installer(options: oo, runner: ProcessRunner(), fileOps: LocalFileOps(), http: URLSessionHTTP(),
                                     sleeper: RealSleeper(), progress: progress)
            try handOverSetupCode(inst, &a, progress: progress)
            try a.checkAllUsed()
            return 0
        case "status":
            let layout = try Machine.layout(&a, defaultSystem: true)
            let wait = try a.int("wait-healthy")
            _ = a.flag("json")
            try a.checkAllUsed()
            let rec = try RecordStore.load(layout.installRecord)
            let ports = rec?.answers.ports
            let domain = rec?.launchdDomain ?? (layout.mode == .system ? "system" : ProcessJobs.domainName)
            let start = Date()
            var s = try statusJSON(layout, domain, ports: ports, role: rec?.roleAccount)
            s["installed"] = rec == nil ? "no" : rec?.uninstalledAt == nil ? "yes" : "data kept"
            if let r = rec { s["steps_done"] = r.done.keys.sorted() }
            if let w = wait {
                while (s["web_health"] as? Int) != 200 || (s["core_health"] as? Int) != 200 || (s["vm"] as? String) != "Running" {
                    if Date().timeIntervalSince(start) > Double(w) { printJSON(s); return 1 }
                    Thread.sleep(forTimeInterval: 2)
                    s = try statusJSON(layout, domain, ports: ports, role: rec?.roleAccount)
                }
                s["waited_s"] = Int(Date().timeIntervalSince(start))
            }
            printJSON(s)
            return 0
        case "update":
            let req = try HelperUpdateRequest.parse(&a)
            if CommandLine.arguments.contains("--parse-only") {
                print("\(SetupCommands.parseOnlyLine) update \(req.linuxOnly ? "linux-only" : "package")")
                return 0
            }
            guard Machine.isRoot else { throw HelperError("update changes this Mac: the Domovoi app runs it behind the administrator prompt") }
            let u = HelperUpdate(layout: .system, runner: ProcessRunner(), sleeper: RealSleeper(), http: URLSessionHTTP())
            let r = u.run(req)
            printJSON(["ok": r.ok, "outcome": r.outcome as Any, "error": r.error as Any, "version": r.version as Any, "linux": r.linux as Any, "log": r.log as Any])
            return r.ok ? 0 : 1
        case "app-update":
            return try appUpdate(&a)
        case "serve":
            let layout = try Machine.layout(&a, defaultSystem: true)
            try a.checkAllUsed()
            guard layout.mode == .system, Machine.isRoot else { throw HelperError("serve: the system install's helper, as root") }
            guard let rec = try RecordStore.load(layout.installRecord), rec.uninstalledAt == nil else {
                throw HelperError("Domovoi isn't installed: nothing to serve")
            }
            let progress = StdProgress()
            HelperService(layout: layout, record: rec, runner: ProcessRunner(), fileOps: LocalFileOps(), http: URLSessionHTTP(),
                          log: progress.log).run()
        case "network":
            let layout = try Machine.layout(&a, defaultSystem: true)
            let doSync = a.flag("sync")
            _ = a.flag("json")
            try a.checkAllUsed()
            if doSync {
                guard layout.mode == .system, Machine.isRoot else { throw HelperError("network --sync changes pf: run it with sudo") }
                guard let rec = try RecordStore.load(layout.installRecord), rec.uninstalledAt == nil else { throw HelperError("Domovoi isn't installed") }
                let progress = StdProgress()
                let lima = Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path,
                                runner: ProcessRunner(), runAs: rec.roleAccount)
                let sync = NetworkSync(layout: layout, lima: lima, runner: ProcessRunner(), fileOps: LocalFileOps(), log: progress.log)
                var s = try sync.sync(refreshVM: true)
                // The running helper's own parts, as it last wrote them.
                let old = NetworkSync.readStatus(layout)
                s.localName = old?.localName
                s.watcher = old?.watcher
                try sync.writeStatus(s)
            }
            guard let s = NetworkSync.readStatus(layout) else {
                print("{\"status\": \"none yet\"}")
                return 1
            }
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            print(String(decoding: try enc.encode(s), as: UTF8.self))
            return s.problem == nil ? 0 : 3
        case "uninstall":
            let req = try SetupCommands.parseUninstall(&a)
            if req.parseOnly { print("\(SetupCommands.parseOnlyLine) uninstall \(req.layout.mode.rawValue) remove-everything=\(req.removeEverything)"); return 0 }
            let layout = req.layout, everything = req.removeEverything, confirm = req.confirm
            if layout.mode == .system && !Machine.isRoot { throw HelperError("uninstalling changes this Mac: run it with sudo") }
            let lock = layout.mode == .system && FileManager.default.fileExists(atPath: layout.appSupport.path) ? try BusyLock(layout.busyLock, what: "uninstall") : nil
            defer { _ = lock }
            let progress = StdProgress()
            // Not for a Remove everything that is about to be refused (no DELETE).
            if !everything || confirm == "DELETE" {
                for line in MacSideCleanup.beforeUninstall(layout: layout, runner: ProcessRunner(), fileOps: LocalFileOps(), log: progress.log,
                                                                         keepIconOf: req.setupUID) { print(line) }
            }
            let u = Uninstaller(layout: layout, runner: ProcessRunner(), sleeper: RealSleeper(), fileOps: LocalFileOps(), log: progress.log)
            let r = try u.run(removeEverything: everything, confirm: confirm)
            printJSON(["ok": r.ok, "removed": r.removed.count, "kept": r.kept, "left": r.left, "failed": r.failed, "unfinished": r.unfinished,
                       "record_removed": r.recordRemoved])
            return r.exitStatus
        case "restart-trigger":
            // com.domovoi.mac.restart's program (RestartService): root only,
            // no arguments but --system, started by launchd for a connection
            // to its socket. One process serves every connection and leaves
            // when idle. Its log goes to standard error, which the job file
            // sends to helper.log (no second copy).
            try SetupCommands.parseRestartTrigger(&a)
            guard Machine.isRoot else { throw HelperError("restart-trigger is the root job's: ask for a restart from the Domovoi menu") }
            let progress = StdProgress(logFile: { nil })
            let listeners = try RestartService.launchdSockets()
            let server = RestartService.Server(layout: .system, fileOps: LocalFileOps(), log: progress.log) {
                try Restarter(layout: .system, runner: ProcessRunner(), sleeper: RealSleeper(), http: URLSessionHTTP(), log: progress.log).run()
            }
            return server.run(listeners)
        case "restart":
            let parseOnly = try SetupCommands.parseRestart(&a)
            if parseOnly { print("\(SetupCommands.parseOnlyLine) restart"); return 0 }
            guard Machine.isRoot else { throw HelperError("restarting Domovoi needs an administrator: run it with sudo, or from the Domovoi menu") }
            let progress = StdProgress(logFile: { Layout.system.logs.appendingPathComponent("helper.log") })
            let r = Restarter(layout: .system, runner: ProcessRunner(), sleeper: RealSleeper(), http: URLSessionHTTP(), log: progress.log)
            do {
                let res = try r.run()
                // Not back: the sentence on standard error, as every failure
                // (the menu shows it as it is), and a failing exit.
                if !res.back { throw HelperError(res.said) }
                print(res.said)
            } catch {
                progress.log("restart refused or failed: \(error)")
                throw error
            }
            return 0
        case "save-logs":
            let req = try SetupCommands.parseSaveLogs(&a)
            if req.parseOnly { print("\(SetupCommands.parseOnlyLine) save-logs"); return 0 }
            guard Machine.isRoot else { throw HelperError("Domovoi's logs are an administrator's to read: run it with sudo, or from the Domovoi menu") }
            let out = try OwnedFolder(req.dir, owner: req.uid)
            let r = try SupportBundle(layout: .system, runner: ProcessRunner()).write(into: out)
            print("copied \(r.written.count) file(s) to \(req.dir)")
            for s in r.skipped { print("skipped: \(s)") }
            return 0
        case "power":
            let req = try SetupCommands.parsePower(&a)
            let on = req.on
            if req.parseOnly { print("\(SetupCommands.parseOnlyLine) power \(req.setting.rawValue)=\(on)"); return 0 }
            guard Machine.isRoot else { throw HelperError("changing this Mac's power settings needs an administrator: run it with sudo, or from the Domovoi menu") }
            let lock = try BusyLock(Layout.system.busyLock, what: "setup")
            defer { _ = lock }
            let said: String
            switch req.setting {
            case .keepAwake:
                let k = KeepAwake(layout: .system, runner: ProcessRunner(), fileOps: LocalFileOps())
                said = try on ? k.turnOn() : k.turnOff()
                try k.saveAnswer(on)
            case .startAfterPowerFailure:
                // Gated (FeatureGates.startAfterPowerFailure): on is refused
                // while the gate is off; off always puts the old value back.
                let p = StartAfterPowerFailure(layout: .system, runner: ProcessRunner(), fileOps: LocalFileOps())
                said = try on ? p.turnOn() : p.turnOff()
                try p.saveAnswer(on)
            }
            StdProgress(logFile: { Layout.system.logs.appendingPathComponent("helper.log") }).log("\(req.setting.rawValue) \(on ? "on" : "off"): \(said)")
            print(said)
            return 0
        default:
            throw HelperError("no command \(a.command!)\n\(usage)")
        }
    } catch {
        FileHandle.standardError.write(Data("DomovoiHelper: \(error)\n".utf8))
        return 1
    }
}

exit(main())
