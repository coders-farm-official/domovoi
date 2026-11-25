import Foundation

/// The answers and inputs of one install run.
public struct InstallOptions {
    public var layout: Layout
    public var ports: Ports
    public var ollama: OllamaChoice
    public var internet: String
    public var tier: Tier
    public var size: VMSize
    public var mediaRoot: String
    public var mediaNames: [String]
    public var timezone: String?
    /// The `mac/` folder: vm/domovoi.yaml, launchd/*.plist, installer/pins.conf.
    public var share: URL
    public var provision: ProvisionSource?
    /// Folders holding already-verified downloads (user mode).
    public var seedCache: [URL] = []
    /// An Ollama models folder to copy (clone) into Domovoi's, when that is
    /// empty, instead of pulling several GB (user mode).
    public var seedModels: URL?
    public var launchdDomain: String
    /// The commit the Mac side was built from, for side.json.
    public var commit: String?
    /// The Mac side's version, for side.json: the package's (share/build.json)
    /// in the system install, so an update's repair reports the new one.
    public var macVersion: String = Names.macSideVersion
    /// Stop after this step (tests; `--stop-after`).
    public var stopAfter: String?
    /// The account that runs the jobs and owns the VM: the role account in
    /// the system install, nil (the signed-in user) in the user mode.
    public var role: String? = nil
    /// The account that ran Setup (the system install): it owns the media
    /// folders Setup makes, so the household can put files in them.
    public var mediaOwner: String? = nil
    /// Domovoi's own Ollama's OLLAMA_NUM_PARALLEL (``OllamaParallel``), from
    /// this Mac's memory; read again on every run, so a repair or an update
    /// writes it into a job installed before it existed.
    public var ollamaParallel: Int = OllamaParallel.forMac(memoryBytes: Machine.memoryBytes())
    /// "Keep this Mac awake" (system install): nil leaves the power settings alone.
    public var keepAwake: Bool? = nil
    /// "Start after a power failure" (system install, desktop Macs): nil leaves it alone.
    /// Turned on only while FeatureGates.startAfterPowerFailure is on.
    public var startAfterPowerFailure: Bool? = nil

    public init(layout: Layout, ports: Ports, ollama: OllamaChoice, internet: String, tier: Tier, size: VMSize,
                mediaRoot: String, mediaNames: [String], timezone: String?, share: URL, provision: ProvisionSource?,
                launchdDomain: String) {
        self.layout = layout
        self.ports = ports
        self.ollama = ollama
        self.internet = internet
        self.tier = tier
        self.size = size
        self.mediaRoot = mediaRoot
        self.mediaNames = mediaNames
        self.timezone = timezone
        self.share = share
        self.provision = provision
        self.launchdDomain = launchdDomain
    }

    public var media: [MediaMount] {
        mediaNames.map { MediaMount(name: $0, hostPath: (mediaRoot as NSString).appendingPathComponent($0)) }
    }

    public var answers: InstallRecord.Answers {
        InstallRecord.Answers(internet: internet, tier: tier, ollama: ollama.label, mediaRoot: mediaRoot, media: media,
                              ports: ports, size: size, timezone: timezone, keepAwake: keepAwake,
                              startAfterPowerFailure: startAfterPowerFailure)
    }

    public func check() throws {
        guard ["always", "sometimes", "never"].contains(internet) else { throw HelperError("--internet is always, sometimes or never") }
        try ports.check(for: layout.mode, ownOllama: ollama == .own)
        if case .existing(let p) = ollama, p == ports.web || p == ports.core { throw HelperError("the Ollama port can't be the web or core port") }
        if case .own = ollama, layout.mode == .user, Ports.reserved.contains(ports.ollama) {
            throw HelperError("the user mode can't run its Ollama on \(ports.ollama)")
        }
        guard mediaRoot.hasPrefix("/") else { throw HelperError("the media folder must be an absolute path") }
        if layout.mode == .system {
            guard role != nil else { throw HelperError("the system install needs its role account") }
            let std = URL(fileURLWithPath: mediaRoot).standardizedFileURL.path
            let app = layout.appSupport.path
            for bad in ["/", "/System", "/Library", "/Applications", "/Users", "/usr", "/bin", "/sbin", "/private", "/var", "/etc", "/Volumes"]
                where std == bad {
                throw HelperError("\(std) can't be the media folder: choose a folder inside it")
            }
            if std == app || std.hasPrefix(app + "/") || std.hasPrefix("/System/") {
                throw HelperError("\(std) can't be the media folder")
            }
        }
        if layout.mode == .user {
            let root = layout.root.path
            guard mediaRoot == root || mediaRoot.hasPrefix(root + "/") else {
                throw HelperError("in the user mode the media folder must be inside the prefix (\(root))")
            }
        }
        _ = try LimaTemplate.render(template: "{{MOUNTS}}\n{{CPUS}}{{MEMORY}}{{DISK}}{{IMAGE_LOCATION}}{{IMAGE_DIGEST}}{{WEB_PORT}}{{CORE_PORT}}",
                                    size: size, imagePath: "/x", imageSHA256: "0", mounts: media, ports: ports)
        try Lima.checkSocketRoom(limaHome: layout.limaHome.path, instance: Names.instance)
    }
}

/// What the helper reports as it goes: one JSON line per event on stdout
/// (for the setup pages later), the same in words on stderr.
public protocol ProgressSink: AnyObject {
    func event(step: String, state: String, message: String)
    func log(_ text: String)
}

public final class StdProgress: ProgressSink {
    public static let prefix = "@@DOMOVOI-MAC "
    let logFile: () -> URL?
    public init(logFile: @escaping () -> URL? = { nil }) { self.logFile = logFile }

    public func event(step: String, state: String, message raw: String) {
        // Nothing shaped like a setup code ever goes out (provision.sh's rule).
        let message = SetupCode.scrub(raw)
        let obj: [String: Any] = ["v": 1, "step": step, "state": state, "message": message]
        if let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) {
            print(Self.prefix + String(decoding: d, as: UTF8.self))
            fflush(stdout)
        }
        log("\(step): \(state)\(message.isEmpty ? "" : " - " + message)")
    }

    public func log(_ text: String) {
        let line = "[\(HostFiles.iso(Date()))] \(SetupCode.scrub(text))\n"
        FileHandle.standardError.write(Data(line.utf8))
        if let u = logFile(), let h = FileHandle(forWritingAtPath: u.path) {
            h.seekToEndOfFile()
            h.write(Data(line.utf8))
            try? h.close()
        }
    }
}

/// The install, as named steps, each safe to run again (a repair is the same
/// run). The user mode runs every step as the signed-in user into a prefix;
/// the system mode's root-only steps (role account, ownership, pf, power)
/// are Phase 3's and are refused here.
public final class Installer {
    public let options: InstallOptions
    public let store: RecordStore
    let runner: CommandRunner
    let fileOps: FileOps
    let http: HTTPClient
    let sleeper: Sleeper
    let progress: ProgressSink
    public let launchd: JobManager
    public var lanAddress: () -> LanAddress? = { LanAddress.current() }
    /// The Mac's `en*` interfaces, for the pf redirect and the forwarding
    /// fence (tests answer).
    public var readInterfaces: () -> [LanInterface] = { LanInterfaces.current(all: true) }
    /// /etc/pf.conf (tests give their own).
    public var pfConf = URL(fileURLWithPath: "/etc/pf.conf")
    /// How long to wait for the VM, and for health.
    public var vmWait: Double = 300
    public var healthWait: Double = 300
    /// FeatureGates.startAfterPowerFailure (tests turn it on to drive the step).
    public var startAfterPowerFailureGate = FeatureGates.startAfterPowerFailure
    /// The system install changes /: root only (tests say yes).
    public var isRoot: () -> Bool = { geteuid() == 0 }
    /// This run put a new limactl in place (a newer Lima pin): the VM's job
    /// is restarted so it runs it (stepVMStart).
    var limaReplaced = false

    public static let steps = ["account", "folders", "media", "lima", "ollama", "stt", "vm-config", "vm-create", "vm-start", "provision", "host-files",
                               "lan", "menu-bar", "power", "health"]

    public init(options: InstallOptions, runner: CommandRunner, fileOps: FileOps, http: HTTPClient, sleeper: Sleeper, progress: ProgressSink) throws {
        self.options = options
        self.runner = runner
        self.fileOps = fileOps
        self.http = http
        self.sleeper = sleeper
        self.progress = progress
        let layout = options.layout
        var record = InstallRecord(mode: layout.mode, root: layout.root.path, answers: options.answers, launchdDomain: options.launchdDomain)
        if let old = try RecordStore.load(layout.installRecord) {
            guard old.root == layout.root.path, old.mode == layout.mode else {
                throw HelperError("the install record at \(layout.installRecord.path) is for another install (\(old.mode.rawValue) at \(old.root))")
            }
            record = old
            record.answers = options.answers
            record.launchdDomain = options.launchdDomain
            record.uninstalledAt = nil
        }
        store = RecordStore(url: layout.installRecord, record: record, fileOps: fileOps)
        launchd = jobManager(domain: options.launchdDomain, layout: layout, runner: runner, sleeper: sleeper, fileOps: fileOps)
    }

    var layout: Layout { options.layout }

    public var lima: Lima {
        Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner, runAs: options.role)
    }

    public func pins() throws -> Pins { try Pins.load(options.share.appendingPathComponent("installer/pins.conf")) }

    func template(_ rel: String) throws -> String {
        let u = options.share.appendingPathComponent(rel)
        guard let t = try? String(contentsOf: u, encoding: .utf8) else { throw HelperError("can't read \(u.path)") }
        return t
    }

    /// Runs every step in order (up to `stopAfter`).
    public func run() throws {
        if layout.mode == .system && !isRoot() {
            throw HelperError("the system install changes this Mac: run it as an administrator (sudo), or through Domovoi's setup pages")
        }
        try options.check()
        for step in Self.steps {
            progress.event(step: step, state: "start", message: "")
            do {
                let note = try runStep(step)
                try store.update { $0.done[step] = HostFiles.iso(Date()) }
                progress.event(step: step, state: "done", message: note)
            } catch {
                try? store.save()
                progress.event(step: step, state: "fail", message: "\(error)")
                throw error
            }
            if step == options.stopAfter { progress.log("stopping after \(step), as asked"); return }
        }
    }

    func runStep(_ step: String) throws -> String {
        switch step {
        case "account": return try stepAccount()
        case "folders": return try stepFolders()
        case "media": return try stepMedia()
        case "lima": return try stepLima()
        case "ollama": return try stepOllama()
        case "stt": return try stepStt()
        case "vm-config": return try stepVMConfig()
        case "vm-create": return try stepVMCreate()
        case "vm-start": return try stepVMStart()
        case "provision": return try stepProvision()
        case "host-files": return try stepHostFiles()
        case "lan": return try stepLan()
        case "menu-bar": return try stepMenuBar()
        case "power": return try stepPower()
        case "health": return try stepHealth()
        default: throw HelperError("no step \(step)")
        }
    }

    // ─── recording helpers ─────────────────────────────────────────────

    /// Makes `dir` and any missing parents below the root, recording each
    /// one it made.
    func makeDir(_ dir: URL, keep: InstallRecord.Keep, step: String, mode: Int = 0o755, kind: InstallRecord.Kind = .dir) throws {
        let fm = FileManager.default
        let root = layout.root.path
        var chain: [URL] = []
        var d = dir.standardizedFileURL
        while d.path != root && d.path != "/" && !fm.fileExists(atPath: d.path) {
            chain.insert(d, at: 0)
            d = d.deletingLastPathComponent()
        }
        for c in chain {
            let last = c == dir.standardizedFileURL
            try store.update { $0.add(.init(kind: last ? kind : .dir, path: c.path, keep: last ? keep : .program, step: step)) }
            do {
                try fm.createDirectory(at: c, withIntermediateDirectories: false, attributes: [.posixPermissions: mode])
            } catch {
                throw HelperError("can't make \(c.path): \(error.localizedDescription)")
            }
        }
    }

    func record(_ item: InstallRecord.Item) throws { try store.update { $0.add(item) } }

    /// Gives `url` (and, for a tree, what is in it) to the role account,
    /// with `mode` on `url` itself. Nothing in the user mode.
    func giveToRole(_ url: URL, mode: Int, tree: Bool = false) throws {
        guard let role = options.role else { return }
        if tree {
            try runner.check(Command("/usr/sbin/chown", ["-R", "\(role):\(role)", url.path], timeout: 600), "chown \(url.lastPathComponent)")
        } else {
            try fileOps.chown(url, owner: role)
        }
        try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: url.path)
    }

    /// Leaves Time Machine out of a big folder that a backup can't use (the
    /// VM disk, the models; PLAN 4.1). A sticky exclusion, an attribute on
    /// the folder: it goes with the folder. Best effort.
    func excludeFromBackup(_ url: URL) {
        guard layout.mode == .system else { return }
        let r = try? runner.run(Command("/usr/bin/tmutil", ["addexclusion", url.path], timeout: 30))
        if r?.ok != true { progress.log("note: Time Machine exclusion for \(url.path) not set: \(r?.tail ?? "tmutil didn't run")") }
    }

    // ─── steps ─────────────────────────────────────────────────────────

    /// The role account (system install only). A reinstall onto kept data
    /// asks for the UID the record kept.
    func stepAccount() throws -> String {
        guard let role = options.role else { return "skipped (the user mode runs as the signed-in user)" }
        let acct = RoleAccount(name: role, runner: runner)
        try record(.init(kind: .account, label: role, keep: .program, step: "account"))
        let r = try acct.ensure(home: layout.jobHome.path, realName: "Domovoi", preferredID: store.record.roleUID)
        if let kept = store.record.roleUID, kept != r.uid {
            progress.log("note: \(role) is UID \(r.uid), not the kept \(kept): the kept files are given to the new UID")
        }
        try store.update {
            $0.roleAccount = role
            $0.roleUID = r.uid
        }
        return "\(role) (UID \(r.uid)\(r.made ? ", made now" : ", already there"))"
    }

    func stepFolders() throws -> String {
        let keeps: [URL: InstallRecord.Keep] = [layout.appSupport: .data, layout.bin: .program, layout.state: .data,
                                                layout.cache: .data, layout.jobHome: .data, layout.logs: .logs,
                                                layout.launchDaemons: .program]
        // The jobs' HOME holds only what Ollama and Lima put there: a tree.
        for f in layout.folders { try makeDir(f, keep: keeps[f] ?? .program, step: "folders", kind: f == layout.jobHome ? .tree : .dir) }
        // The jobs' HOME is the role account's own (Ollama's key, Lima's
        // settings), and nobody else's business.
        if FileManager.default.fileExists(atPath: layout.jobHome.path) { try giveToRole(layout.jobHome, mode: 0o700, tree: true) }
        let helperLog = layout.logs.appendingPathComponent("helper.log")
        if !FileManager.default.fileExists(atPath: helperLog.path) {
            try record(.init(kind: .file, path: helperLog.path, keep: .logs, step: "folders"))
            try fileOps.makeLogFile(helperLog, owner: nil)
        }
        try store.save()
        return layout.appSupport.path
    }

    func stepMedia() throws -> String {
        let root = URL(fileURLWithPath: options.mediaRoot)
        let folders = [root] + options.media.map { URL(fileURLWithPath: $0.hostPath) }
        for f in folders {
            let made = !FileManager.default.fileExists(atPath: f.path)
            try makeDir(f, keep: .data, step: "media")
            // A folder Setup made belongs to whoever ran Setup, writable by
            // the Mac's other accounts (group staff), so the household can
            // put files in it. A folder that was there is left as it was.
            if made, layout.mode == .system, let owner = options.mediaOwner {
                try fileOps.chown(f, owner: owner)
                try runner.check(Command("/usr/bin/chgrp", ["staff", f.path], timeout: 30), "chgrp staff \(f.lastPathComponent)")
                try FileManager.default.setAttributes([.posixPermissions: 0o775], ofItemAtPath: f.path)
            }
        }
        // The VM reads and writes the media as the role account: an
        // inherited access entry gives it every file put in later.
        if let role = options.role {
            let entry = MediaACL.entry(for: role)
            for f in folders {
                try record(.init(kind: .acl, path: f.path, label: role, keep: .program, step: "media"))
                // Taken off first, so a repair never adds it twice.
                _ = try runner.run(Command(MediaACL.chmod, ["-a", entry, f.path], timeout: 30))
                try runner.check(Command(MediaACL.chmod, ["+a", entry, f.path], timeout: 30), "the media access entry on \(f.path)")
            }
        }
        return options.media.map(\.name).joined(separator: ", ")
    }

    func stepLima() throws -> String {
        let pins = try pins()
        if FileManager.default.isExecutableFile(atPath: layout.limactl.path),
           let r = try? runner.run(lima.command(["--version"], timeout: 30)), r.ok, r.stdout.contains(pins.limaVersion) {
            return "limactl \(pins.limaVersion) already in place"
        }
        let dl = Downloader(runner: runner, cache: layout.cache, seeds: options.seedCache, log: progress.log)
        let tgz = try cacheFile(pins.lima, dl)
        // Only limactl and the arm64 guest agent are needed (Phase 0 S0).
        let tmp = layout.bin.appendingPathComponent(".lima-unpack")
        try? FileManager.default.removeItem(at: tmp)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: tmp) }
        // --no-same-owner: as root, tar would give the files the archive's
        // own owner (some UID on the build machine), who could then change
        // what the VM's account runs.
        try runner.check(Command("/usr/bin/tar", ["--no-same-owner", "-xzf", tgz.path, "-C", tmp.path, "bin/limactl", "share/lima/lima-guestagent.Linux-aarch64.gz"], timeout: 300), "unpacking Lima")
        try record(.init(kind: .tree, path: layout.limaDir.path, keep: .program, step: "lima"))
        try? FileManager.default.removeItem(at: layout.limaDir)
        try FileManager.default.moveItem(at: tmp, to: layout.limaDir)
        limaReplaced = true
        let v = try runner.check(lima.command(["--version"], timeout: 30), "limactl --version")
        guard v.stdout.contains(pins.limaVersion) else { throw HelperError("limactl says \(v.stdout.trimmingCharacters(in: .whitespacesAndNewlines)), not \(pins.limaVersion)") }
        return "limactl \(pins.limaVersion)"
    }

    func cacheFile(_ pin: Pin, _ dl: Downloader) throws -> URL {
        let dest = dl.path(for: pin)
        if !store.record.has(.file, path: dest.path) && !FileManager.default.fileExists(atPath: dest.path) {
            try record(.init(kind: .file, path: dest.path, keep: .data, step: "download"))
        }
        return try dl.ensure(pin).url
    }

    func stepOllama() throws -> String {
        let pins = try pins()
        switch options.ollama {
        case .existing(let port):
            let found = OllamaProbe(http: http, runner: runner).look(port: port)
            let warnings = try OllamaProbe.checkExisting(found, port: port, minimum: pins.ollamaMinimum)
            warnings.forEach { progress.log("note: \($0)") }
            try store.update {
                $0.ollamaURL = "http://host.lima.internal:\(port)"
                $0.ollamaVersion = found.versionText
            }
            return "using the Ollama already on this Mac (\(found.versionText ?? "?"), port \(port))"
        case .own:
            let port = options.ports.ollama
            // Nothing else may hold the port: a household Ollama there would
            // answer for ours.
            let before = OllamaProbe(http: http, runner: runner).look(port: port)
            let dir = layout.ollamaDir(version: pins.ollamaVersion)
            let bin = dir.appendingPathComponent("ollama")
            let jobLoaded = try launchd.state(Labels.ollama) != nil
            if before.answered && !jobLoaded {
                throw HelperError("something already answers on 127.0.0.1:\(port) (\(before.program ?? "unknown program")): close it, or choose --ollama existing:\(port)")
            }
            if !FileManager.default.isExecutableFile(atPath: bin.path) {
                let dl = Downloader(runner: runner, cache: layout.cache, seeds: options.seedCache, log: progress.log)
                let tgz = try cacheFile(pins.ollama, dl)
                try makeDir(layout.ollamaRoot, keep: .program, step: "ollama")
                try record(.init(kind: .tree, path: dir.path, keep: .program, step: "ollama"))
                let tmp = layout.ollamaRoot.appendingPathComponent(".unpack")
                try? FileManager.default.removeItem(at: tmp)
                try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: false)
                try runner.check(Command("/usr/bin/tar", ["--no-same-owner", "-xzf", tgz.path, "-C", tmp.path], timeout: 600), "unpacking Ollama")
                try? FileManager.default.removeItem(at: dir)
                try FileManager.default.moveItem(at: tmp, to: dir)
                guard FileManager.default.isExecutableFile(atPath: bin.path) else { throw HelperError("no ollama at the top of the Ollama tarball") }
            }
            if !FileManager.default.fileExists(atPath: layout.ollamaModels.path) {
                try record(.init(kind: .tree, path: layout.ollamaModels.path, keep: .models, step: "ollama"))
                try FileManager.default.createDirectory(at: layout.ollamaModels, withIntermediateDirectories: false)
            }
            try seedModelsIfEmpty()
            try giveToRole(layout.ollamaModels, mode: 0o755, tree: true)
            excludeFromBackup(layout.ollamaModels)
            let logFile = layout.logs.appendingPathComponent("ollama.log")
            if !store.record.has(.file, path: logFile.path) && !FileManager.default.fileExists(atPath: logFile.path) {
                try record(.init(kind: .file, path: logFile.path, keep: .logs, step: "ollama"))
            }
            let plist = try LaunchdPlist.render(template: template("launchd/\(Labels.ollama).plist"), values: [
                "LABEL": Labels.ollama, "OLLAMA_BIN": bin.path, "OLLAMA_PORT": String(port),
                "OLLAMA_MODELS": layout.ollamaModels.path, "JOB_HOME": layout.jobHome.path, "LOG_FILE": logFile.path,
                "OLLAMA_NUM_PARALLEL": String(min(4, max(1, options.ollamaParallel))),
            ], role: options.role)
            try record(.init(kind: .launchdJob, path: layout.plist(Labels.ollama).path, label: Labels.ollama, domain: launchd.domain, keep: .program, step: "ollama"))
            let st = try launchd.install(plist, at: layout.plist(Labels.ollama))
            // Ours answers, and is the pinned version.
            let start = sleeper.now()
            var found = OllamaFound.nothing
            while sleeper.now().timeIntervalSince(start) < 60 {
                found = OllamaProbe(http: http, runner: runner).look(port: port)
                if found.versionText != nil { break }
                sleeper.sleep(1)
            }
            guard found.versionText == pins.ollamaVersion else {
                throw HelperError("Domovoi's Ollama on 127.0.0.1:\(port) answers \(found.versionText ?? "nothing"), not \(pins.ollamaVersion); see \(logFile.path)")
            }
            try store.update {
                $0.ollamaURL = "http://host.lima.internal:\(port)"
                $0.ollamaVersion = found.versionText
            }
            return "Ollama \(pins.ollamaVersion) on 127.0.0.1:\(port) (pid \(st.pid.map(String.init) ?? "?"))"
        }
    }

    /// How long the speech service may take to answer /health after its
    /// job starts: the model load and Metal's first pipeline build.
    public var sttWait: Double = 120

    /// The speech service on the Mac's graphics chip (PHASE8A-STT.md):
    /// whisper.cpp's server, which the package carries, as a LaunchDaemon of
    /// the role account on the Mac's loopback, with its model downloaded
    /// here. Off (nothing installed, speech on the VM's processor, the
    /// record's sttURL cleared) when this package has no STT pins or no
    /// whisper-server, and in the user mode unless given --stt-port. A
    /// service that doesn't answer in time doesn't stop the install: the
    /// core hears every turn itself until it does, and says so on Health.
    func stepStt() throws -> String {
        let pins = try pins()
        let port = options.ports.stt ?? (layout.mode == .system ? Ports.sttDefault : nil)
        let why: String?
        if pins.stt == nil { why = "this package has no speech-service pins" }
        else if port == nil { why = "the user mode needs --stt-port" }
        else if !FileManager.default.isExecutableFile(atPath: layout.sttProgram.path) { why = "this package has no whisper-server" }
        else { why = nil }
        if let why {
            if try launchd.state(Labels.stt) != nil {
                // A repair from a package without the service: stop the old one.
                try launchd.bootout(Labels.stt, wait: 60)
                try? FileManager.default.removeItem(at: layout.plist(Labels.stt))
            }
            try store.update { $0.sttURL = nil; $0.sttModel = nil }
            return "skipped (\(why)): speech is recognised on the virtual machine's processor"
        }
        let sp = pins.stt!, p = port!
        let jobLoaded = try launchd.state(Labels.stt) != nil
        if !jobLoaded, http.get("http://127.0.0.1:\(p)/health", timeout: 3) != nil {
            throw HelperError("something already answers on 127.0.0.1:\(p), the port of Domovoi's speech service: close it and try again")
        }
        if !FileManager.default.fileExists(atPath: layout.sttModels.path) {
            try record(.init(kind: .tree, path: layout.sttModels.path, keep: .models, step: "stt"))
            try FileManager.default.createDirectory(at: layout.sttModels, withIntermediateDirectories: false)
        }
        // Into its own folder (not the cache): the job reads it from there.
        let dl = Downloader(runner: runner, cache: layout.sttModels, seeds: options.seedCache, log: progress.log)
        let model = try dl.ensure(sp.model).url
        // Older model files (a pin bump) go once the new one is in place.
        let present = (try? FileManager.default.contentsOfDirectory(atPath: layout.sttModels.path)) ?? []
        for name in present where name != model.lastPathComponent && name.hasPrefix("ggml-") {
            try? FileManager.default.removeItem(at: layout.sttModels.appendingPathComponent(name))
        }
        try giveToRole(layout.sttModels, mode: 0o755, tree: true)
        excludeFromBackup(layout.sttModels)
        let logFile = layout.logs.appendingPathComponent("stt.log")
        if !store.record.has(.file, path: logFile.path) && !FileManager.default.fileExists(atPath: logFile.path) {
            try record(.init(kind: .file, path: logFile.path, keep: .logs, step: "stt"))
        }
        let plist = try LaunchdPlist.render(template: template("launchd/\(Labels.stt).plist"), values: [
            "LABEL": Labels.stt, "STT_BIN": layout.sttProgram.path, "STT_MODEL": model.path, "STT_PORT": String(p),
            "JOB_HOME": layout.jobHome.path, "LOG_FILE": logFile.path,
        ], role: options.role)
        try record(.init(kind: .launchdJob, path: layout.plist(Labels.stt).path, label: Labels.stt, domain: launchd.domain, keep: .program, step: "stt"))
        let st = try launchd.install(plist, at: layout.plist(Labels.stt))
        try store.update {
            $0.sttURL = "http://host.lima.internal:\(p)"
            $0.sttModel = sp.modelName
        }
        let start = sleeper.now()
        while sleeper.now().timeIntervalSince(start) < sttWait {
            if http.get("http://127.0.0.1:\(p)/health", timeout: 3)?.status == 200 {
                return "speech service (whisper.cpp \(sp.whisperCppVersion), \(sp.modelName)) on 127.0.0.1:\(p) (pid \(st.pid.map(String.init) ?? "?"))"
            }
            sleeper.sleep(2)
        }
        progress.log("note: the speech service on 127.0.0.1:\(p) didn't answer in \(Int(sttWait)) s; see \(logFile.path)")
        return "the speech service isn't answering yet (see \(logFile.path)): the virtual machine recognises speech itself until it does"
    }

    func seedModelsIfEmpty() throws {
        guard let seed = options.seedModels else { return }
        let fm = FileManager.default
        guard (try? fm.contentsOfDirectory(atPath: layout.ollamaModels.path).isEmpty) == true else { return }
        for name in try fm.contentsOfDirectory(atPath: seed.path) where name == "blobs" || name == "manifests" {
            try fm.copyItem(at: seed.appendingPathComponent(name), to: layout.ollamaModels.appendingPathComponent(name))
        }
        progress.log("copied the models in \(seed.path) (an APFS clone)")
    }

    func stepVMConfig() throws -> String {
        let pins = try pins()
        let dl = Downloader(runner: runner, cache: layout.cache, seeds: options.seedCache, log: progress.log)
        let image = try cacheFile(pins.ubuntuImage, dl)
        // limactl reads it as the VM's account.
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: image.path)
        let yaml = try LimaTemplate.render(template: template("vm/domovoi.yaml"), size: options.size, imagePath: image.path,
                                           imageSHA256: pins.ubuntuImage.sha256, mounts: options.media, ports: options.ports)
        if !store.record.has(.file, path: layout.renderedVMConfig.path) {
            try record(.init(kind: .file, path: layout.renderedVMConfig.path, keep: .data, step: "vm-config"))
        }
        try fileOps.writeAtomically(Data(yaml.utf8), to: layout.renderedVMConfig, mode: 0o644)
        if !FileManager.default.fileExists(atPath: layout.limaHome.path) {
            try record(.init(kind: .tree, path: layout.limaHome.path, keep: .data, step: "vm-config"))
            try FileManager.default.createDirectory(at: layout.limaHome, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        }
        // LIMA_HOME is the VM account's alone (PLAN 3.2: 0700).
        try giveToRole(layout.limaHome, mode: 0o700, tree: true)
        excludeFromBackup(layout.limaHome)
        try lima.validate(layout.renderedVMConfig)
        return layout.renderedVMConfig.path
    }

    func stepVMCreate() throws -> String {
        if let s = try lima.status(Names.instance) { return "instance \(Names.instance) exists (\(s))" }
        try record(.init(kind: .limaInstance, label: Names.instance, keep: .data, step: "vm-create"))
        try lima.create(Names.instance, from: layout.renderedVMConfig)
        return "instance \(Names.instance) created"
    }

    func stepVMStart() throws -> String {
        let logFile = layout.logs.appendingPathComponent("vm.log")
        if !store.record.has(.file, path: logFile.path) && !FileManager.default.fileExists(atPath: logFile.path) {
            try record(.init(kind: .file, path: logFile.path, keep: .logs, step: "vm-start"))
        }
        let plist = try LaunchdPlist.render(template: template("launchd/\(Labels.vm).plist"), values: [
            "LABEL": Labels.vm, "LIMACTL": layout.limactl.path, "INSTANCE": Names.instance,
            "LIMA_HOME": layout.limaHome.path, "JOB_HOME": layout.jobHome.path, "LOG_FILE": logFile.path,
        ], role: options.role)
        try record(.init(kind: .launchdJob, path: layout.plist(Labels.vm).path, label: Labels.vm, domain: launchd.domain, keep: .program, step: "vm-start"))
        let jobRunning = try launchd.state(Labels.vm)?.running == true
        let vmRunning = try lima.status(Names.instance) == "Running"
        // An update's repair: a new limactl (the Lima pin moved) or a changed
        // job file only takes effect once the job starts again. The bootout
        // stops the old host agent, which shuts the VM down first.
        let plistPath = layout.plist(Labels.vm)
        let jobChanged = (try? String(contentsOf: plistPath, encoding: .utf8)) != plist.text
        if jobRunning && (limaReplaced || jobChanged) {
            progress.log("restarting the VM's job: \(limaReplaced ? "Lima was updated" : "its job file changed")")
            try launchd.install(plist, at: plistPath)
        } else if !(jobRunning && vmRunning) {
            // A VM started by hand (not by our job) is stopped first, so the
            // job owns it.
            if try lima.status(Names.instance) == "Running" { try lima.stop(Names.instance) }
            try launchd.install(plist, at: layout.plist(Labels.vm))
        }
        return try waitVM()
    }

    /// Lima's host agent reports `"running":true` once the guest's boot
    /// requirements are met: what a blocking `limactl start` waits for. A
    /// `Running` status and one working `limactl shell` come earlier, while
    /// the first boot still restarts things, and a session opened then is cut
    /// (exit 255; 2026-10-30, provision.sh lost mid-preflight).
    func hostAgentReady() -> Bool {
        let files = [layout.limaHome.appendingPathComponent("\(Names.instance)/ha.stdout.log"),
                     layout.logs.appendingPathComponent("vm.log")]
        for f in files {
            guard let text = try? String(contentsOf: f, encoding: .utf8) else { continue }
            // The last status event decides (a restart writes new ones).
            if let last = text.split(separator: "\n").last(where: { $0.contains("\"status\":{") }) {
                if last.contains("\"running\":true") && !last.contains("\"exiting\":true") { return true }
            }
        }
        return false
    }

    public func waitVM() throws -> String {
        let start = sleeper.now()
        var last = "unknown"
        var ready = false
        while sleeper.now().timeIntervalSince(start) < vmWait {
            last = try lima.status(Names.instance) ?? "missing"
            if last == "Running", hostAgentReady() { ready = true; break }
            sleeper.sleep(2)
        }
        guard ready else {
            throw HelperError("the VM is \(last), not ready, after \(Int(vmWait)) s; see \(layout.logs.appendingPathComponent("vm.log").path)")
        }
        // Then the guest's own first boot (cloud-init), over a session that
        // may still be cut once: retried.
        var r = CommandResult(status: -1)
        for _ in 0..<5 {
            r = try lima.shell(Names.instance, ["sudo", "cloud-init", "status", "--wait"], timeout: 900)
            // 0 done, 2 done with recoverable errors; 255 is the ssh session.
            if r.status == 0 || r.status == 2 { break }
            sleeper.sleep(5)
        }
        guard r.status == 0 || r.status == 2 else { throw HelperError("the VM's first boot didn't finish (cloud-init: exit \(r.status)): \(r.tail)") }
        return "VM ready (\(Int(sleeper.now().timeIntervalSince(start))) s)"
    }

    /// Puts a Mac file at `guestPath` in the VM, root's, 0644. Streamed
    /// through `limactl shell` on stdin: `limactl copy` picks rsync when the
    /// Mac has it, and that failed against the stock guest ("rsync: unexpected
    /// end of file", 2026-10-30), so nothing beyond the guest's shell is needed.
    func guestInstall(_ local: URL, _ guestPath: String) throws {
        let data: Data
        do { data = try Data(contentsOf: local) } catch { throw HelperError("can't read \(local.path): \(error.localizedDescription)") }
        let script = "set -e; t=$(mktemp); trap 'rm -f \"$t\"' EXIT; cat > \"$t\"; install -o root -g root -m 0644 \"$t\" '\(guestPath)'"
        let r = try lima.shell(Names.instance, ["sudo", "sh", "-c", script], stdin: data, timeout: 900)
        guard r.ok else { throw HelperError("can't put \(local.lastPathComponent) at \(guestPath) in the VM: \(r.tail)") }
        let check = try lima.shell(Names.instance, ["sudo", "sha256sum", guestPath])
        let want = Data(SHA256Hex.of(data).utf8)
        guard check.ok, check.stdout.hasPrefix(String(decoding: want, as: UTF8.self)) else {
            throw HelperError("\(guestPath) in the VM is not the Mac's \(local.lastPathComponent): \(check.tail)")
        }
    }

    public var egressWait: Double = 300

    /// provision.sh downloads from the start (apt first). Right after its
    /// first boot the guest's vzNAT route already answers DNS (Lima's own
    /// resolver) but TCP out of it timed out for a few minutes: apt failed
    /// "Unable to connect" in two runs, and the same request worked over the
    /// same interface minutes later (2026-10-30; not IPv6: no v6 default
    /// route, `curl -6` fails at once; not MTU: 1472-byte DF pings pass). So
    /// wait until an HTTPS request over the default route gets through, to
    /// the host the pinned Ubuntu image comes from.
    func waitEgress() throws {
        guard let host = (try? pins())?.ubuntuImage.url.split(separator: "/").dropFirst(1).first.map(String.init) else { return }
        let url = "https://\(host)/"
        let start = sleeper.now()
        var tries = 0
        var last = CommandResult(status: -1)
        while sleeper.now().timeIntervalSince(start) < egressWait {
            tries += 1
            last = try lima.shell(Names.instance, ["curl", "-4", "-sS", "-m", "8", "-o", "/dev/null", "-w", "%{http_code}", url], timeout: 30)
            if last.ok { break }
            sleeper.sleep(5)
        }
        guard last.ok else {
            throw HelperError("the VM can't reach \(url) after \(Int(egressWait)) s (\(tries) tries): \(last.tail)")
        }
        progress.log("the VM reaches \(host) (after \(tries) tr\(tries == 1 ? "y" : "ies"), \(Int(sleeper.now().timeIntervalSince(start))) s)")
    }

    func stepProvision() throws -> String {
        guard let src = options.provision else {
            progress.log("no provision.sh given: the VM is left as Lima made it")
            return "skipped (no --provision-script)"
        }
        guard let ollamaURL = store.record.ollamaURL else { throw HelperError("the Ollama step didn't run") }
        // 0755: provision.sh clones the bundle as its service user, which
        // must be able to reach it (nothing here is secret: the script, the
        // repository, the public allowed_signers). 0700 made git say the
        // bundle "does not exist" (2026-10-30).
        let mk = try lima.shell(Names.instance, ["sudo", "install", "-d", "-o", "root", "-g", "root", "-m", "0755", ProvisionSource.guestDir])
        guard mk.ok else { throw HelperError("can't make \(ProvisionSource.guestDir) in the VM: \(mk.tail)") }
        try waitEgress()
        try guestInstall(src.script, ProvisionSource.guestScript)
        if let b = src.repoBundle { try guestInstall(b, ProvisionSource.guestBundle) }
        if let s = src.allowedSigners { try guestInstall(s, ProvisionSource.guestSigners) }
        let args = ProvisionArgs.build(source: src, answers: options.answers, ollamaURL: ollamaURL,
                                       sttURL: store.record.sttURL, sttModel: store.record.sttModel)
        let logFile = layout.logs.appendingPathComponent("provision.log")
        if !store.record.has(.file, path: logFile.path) {
            try record(.init(kind: .file, path: logFile.path, keep: .logs, step: "provision"))
        }
        // Appended to: a repair run keeps the first install's log above it.
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        let log = FileHandle(forWritingAtPath: logFile.path)
        defer { try? log?.close() }
        log?.seekToEndOfFile()
        log?.write(Data("==== provision.sh run, \(HostFiles.iso(Date())) ====\n".utf8))
        progress.log("provision.sh \(args.joined(separator: " "))")
        var final: ProvisionProgress?
        var lastPercent = -1
        let r = try lima.shell(Names.instance, ["sudo", "bash", ProvisionSource.guestScript] + args, timeout: 3 * 3600) { line in
            log?.write(Data((line + "\n").utf8))
            guard let p = ProvisionProgress.parse(line) else { return }
            if p.final { final = p }
            if p.state == "start" || p.state == "fail" || (p.percent ?? 0) >= lastPercent + 10 {
                lastPercent = p.percent ?? lastPercent
                self.progress.event(step: "provision", state: "progress", message: p.plainLine)
            }
        }
        if !r.stderr.isEmpty { log?.write(Data(r.stderr.utf8)) }
        guard let f = final else { throw HelperError("provision.sh ended without its final line (exit \(r.status)); see \(logFile.path)") }
        guard f.result == "ok", r.ok else {
            throw HelperError("provision.sh: \(f.result ?? "?") at \(f.failedStep ?? "?"): \(f.message ?? ""); see \(logFile.path)")
        }
        // Never fatal (provision.sh's phone-app step): the install goes on,
        // and Setup's last page says so (SetupModel.handle).
        if f.phoneApp == false {
            progress.event(step: "phone-app", state: "missing", message: ProvisionProgress.phoneAppMissing)
            return "provision.sh ok; the phone app couldn't be downloaded"
        }
        return "provision.sh ok"
    }

    func stepHostFiles() throws -> String {
        guard let lan = lanAddress() else { throw HelperError("this Mac has no LAN IPv4 address to hand satellites") }
        let now = sleeper.now()
        for (path, data) in [(HostFiles.lanFile, try HostFiles.lanJSON(lan, at: now)),
                             (HostFiles.sideFile, try HostFiles.sideJSON(version: options.macVersion, commit: options.commit, at: now,
                                                                         updates: layout.mode == .system, cardHelper: layout.mode == .system))] {
            let r = try lima.shell(Names.instance, ["sudo", "sh", "-c", HostFiles.guestWriteScript(path: path)], stdin: data)
            guard r.ok else { throw HelperError("can't write \(path) in the VM: \(r.tail)") }
        }
        // Read back by the core's own reader, as the service user: the file
        // counts only if lan_address believes it.
        if options.provision != nil {
            let py = "from domovoi import lan_address; print(lan_address.host_lan_ipv4())"
            let r = try lima.shell(Names.instance, ["sudo", "-u", "domovoi", "sh", "-c", "cd /opt/domovoi && set -a && . domovoi/.env && set +a && .venv/bin/python -c '\(py)'"])
            let seen = r.stdout.split(separator: "\n").last.map(String.init)?.trimmingCharacters(in: .whitespaces)
            guard r.ok, seen == lan.ipv4 else {
                throw HelperError("the core doesn't take lan.json's address (it read \(seen ?? "nothing")): \(r.tail)")
            }
            return "lan.json \(lan.ipv4) (\(lan.interface ?? "?")), read back by the core; side.json \(options.macVersion)"
        }
        return "lan.json \(lan.ipv4) (\(lan.interface ?? "?")); side.json \(options.macVersion)"
    }

    /// The household's way in (Phase 4; PLAN 4.2a, 4.8): one network sync
    /// now (the pf redirect from the Mac's LAN address to the VM, lan.json),
    /// then the root helper's LaunchDaemon, which keeps both up as the
    /// network changes, publishes domovoi.local and watches the media
    /// folders. System install only: the user mode changes nothing of the
    /// Mac's own. A Mac with no home network now, or a pf.conf someone
    /// rewrote, doesn't stop the install: Domovoi works on this Mac, and the
    /// step says why the household can't reach it yet.
    func stepLan() throws -> String {
        guard layout.mode == .system else { return "skipped (the user mode doesn't change this Mac's network)" }
        try record(.init(kind: .hostNetwork, label: PfRules.anchor, keep: .program, step: "lan"))
        for f in NetworkSync.files(layout) where !store.record.has(.file, path: f.path) {
            try record(.init(kind: .file, path: f.path, keep: .program, step: "lan"))
        }
        let sync = NetworkSync(layout: layout, lima: lima, runner: runner, fileOps: fileOps, log: progress.log)
        sync.readInterfaces = readInterfaces
        sync.fallbackAddress = lanAddress
        sync.pf.pfConf = pfConf
        let status = try sync.sync(refreshVM: true)

        let logFile = layout.logs.appendingPathComponent("service.log")
        if !store.record.has(.file, path: logFile.path) && !FileManager.default.fileExists(atPath: logFile.path) {
            try record(.init(kind: .file, path: logFile.path, keep: .logs, step: "lan"))
        }
        let text = try Template.render(template("launchd/\(Labels.helper).plist"), values: [
            "LABEL": Labels.helper, "HELPER": Template.xmlEscape(layout.helperProgram.path), "LOG_FILE": Template.xmlEscape(logFile.path),
        ])
        let plist = try LaunchdPlist(text: text)
        try record(.init(kind: .launchdJob, path: layout.plist(Labels.helper).path, label: Labels.helper, domain: launchd.domain, keep: .program, step: "lan"))
        // Restarted on every run, so a repair or an upgrade runs the new helper.
        try launchd.install(plist, at: layout.plist(Labels.helper))

        let ifaces = status.interfaces.map { "\($0.name) \($0.ipv4)\($0.wifi == true ? " (Wi-Fi)" : "")" }.joined(separator: ", ")
        if let p = status.problem {
            progress.log("note: other devices can't reach Domovoi yet: \(p)")
            return "the helper is running; other devices can't reach Domovoi yet: \(p)"
        }
        return "phones and room speakers reach Domovoi at \(status.lanAddress ?? "?") (\(ifaces)) -> VM \(status.vm?.ipv4 ?? "?")"
    }

    /// The 8-word setup code the Linux side wrote for the first admin
    /// (provision.sh: the service user's ~/.domovoi/setup-code.txt), or nil
    /// when there is none (already claimed). Never logged.
    public func readSetupCode() throws -> String? {
        let r = try lima.shell(Names.instance, ["sudo", "-u", "domovoi", "cat", "/home/domovoi/.domovoi/setup-code.txt"], timeout: 60)
        guard r.ok else { return nil }
        let code = r.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        return SetupCode.isWellFormed(code) ? code : nil
    }

    public func health() -> (web: Bool, core: Bool) {
        let w = http.get("http://127.0.0.1:\(options.ports.web)/api/health", timeout: 5)?.status == 200
        let c = http.get("http://127.0.0.1:\(options.ports.core)/v1/health", timeout: 5)?.status == 200
        return (w, c)
    }

    func stepHealth() throws -> String {
        guard options.provision != nil else { return "skipped (nothing provisioned)" }
        let start = sleeper.now()
        var h = (web: false, core: false)
        while sleeper.now().timeIntervalSince(start) < healthWait {
            h = health()
            if h.web && h.core { return "web 127.0.0.1:\(options.ports.web) and core 127.0.0.1:\(options.ports.core) healthy" }
            sleeper.sleep(3)
        }
        throw HelperError("not healthy after \(Int(healthWait)) s: web \(h.web ? "ok" : "down"), core \(h.core ? "ok" : "down")")
    }
}
