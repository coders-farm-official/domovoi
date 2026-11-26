import Foundation

// The helper's half of the Mac update (mac-installer PLAN 4.12), the twin of
// windows/helper/Update.cs, run as root behind the administrator prompt by
// the Domovoi app (MacUpdate.swift):
//
//   DomovoiHelper update --run NAME --from DIR --tag TAG [--commit SHA] --for-uid UID [--linux-first]
//       The Mac step: copy the app's checked download from DIR (that
//       account's own run folder, ~/Library/Application Support/Domovoi/
//       updates/NAME) into /Library/Application Support/Domovoi/updates/NAME,
//       which only root can write; check it again there (the release key the
//       install pinned, share/installer/release-signing.pub; the release TAG
//       and its commit; newer than the installed share/build.json); install
//       that copy with `installer -pkg ... -target /` (a command-line
//       install: no questions, and the package's postinstall does nothing);
//       then run the NEW helper's repair (`install --system --repair`), which
//       keeps the answers, asks nothing, removes nothing and rewrites
//       side.json with the new version. With --linux-first the Linux part
//       runs first (the menu's own "Update Domovoi...").
//   DomovoiHelper update --linux-only --run NAME
//       The Linux part alone: the dashboard's own update, in the VM as the
//       service user (`python -m domovoi.windows_side linux-update`: the
//       container guard's freeze, the check, a rolled-back commit, the pull,
//       the restart through the update unit), then a wait until the update
//       unit has finished.
//
// Either way the outcome goes to result.json in the run folder (root's,
// 0644: the app reads it, nobody else can write it), and the run's own log
// beside it. A refused copy is never installed. A failed install or repair
// restarts the jobs as they were and names its log.

public struct HelperUpdateRequest: Equatable {
    public var run: String
    public var from: URL?
    public var tag: String?
    public var commit: String?
    public var forUID: uid_t?
    public var linuxFirst = false
    public var linuxOnly = false

    public init(run: String) { self.run = run }

    /// `update`'s options (every one read before "every option used").
    public static func parse(_ a: inout Args) throws -> HelperUpdateRequest {
        let linuxOnly = a.flag("linux-only")
        let linuxFirst = a.flag("linux-first")
        guard let run = a.value("run"), MacUpdateRun.isRunName(run) else { throw HelperError("--run takes a run name (yyyyMMdd-HHmmss-xxxxxxxx)") }
        var r = HelperUpdateRequest(run: run)
        r.linuxOnly = linuxOnly
        r.linuxFirst = linuxFirst
        let from = a.value("from"), tag = a.value("tag"), commit = a.value("commit"), uid = try a.int("for-uid")
        _ = a.flag("parse-only")
        try a.checkAllUsed()
        if linuxOnly {
            if from != nil || tag != nil || commit != nil || linuxFirst { throw HelperError("--linux-only takes only --run") }
            return r
        }
        guard let from, from.hasPrefix("/") else { throw HelperError("--from takes the app's run folder") }
        guard let tag, MacReleaseNames.isTag(tag) else { throw HelperError("--tag takes a release tag") }
        if let c = commit, !Pattern.full(c, "[0-9a-f]{40}") { throw HelperError("--commit takes a full SHA") }
        guard let uid, uid >= 500 else { throw HelperError("--for-uid takes the account that downloaded the package") }
        r.from = URL(fileURLWithPath: from, isDirectory: true)
        r.tag = tag
        r.commit = commit
        r.forUID = uid_t(uid)
        return r
    }
}

public final class HelperUpdate {
    public let layout: Layout
    let runner: CommandRunner
    let sleeper: Sleeper
    let http: HTTPClient
    /// The installer (tests: a stand-in).
    public var installer = "/usr/sbin/installer"
    /// Which files count as root's (tests: their own rule).
    public var trust: (URL) -> Bool = PinnedRelease.rootOnly
    /// An account's home folder.
    public var home: (uid_t) -> String? = { uid in getpwuid(uid).map { String(cString: $0.pointee.pw_dir) } }
    /// The owner of a path (tests: anyone).
    public var ownerOf: (String) -> uid_t? = { p in
        var st = stat()
        return lstat(p, &st) == 0 ? st.st_uid : nil
    }
    /// The update unit's patience (the Windows helper's: 45 minutes).
    public var waitMinutes: Double = 45
    /// The core, for the Linux part's wait.
    public var core = "http://127.0.0.1:\(Ports.real.core)"
    /// Runs held at most (older run folders are removed).
    public var keepRuns = 5
    public var now: () -> Date = { Date() }
    var logURL: URL?

    public init(layout: Layout, runner: CommandRunner, sleeper: Sleeper, http: HTTPClient) {
        self.layout = layout
        self.runner = runner
        self.sleeper = sleeper
        self.http = http
    }

    var adminRoot: URL { MacUpdateRun.adminRoot(layout) }

    func log(_ text: String) {
        let line = "[\(HostFiles.iso(now()))] \(SetupCode.scrub(text))\n"
        FileHandle.standardError.write(Data(line.utf8))
        if let u = logURL, let h = FileHandle(forWritingAtPath: u.path) {
            h.seekToEndOfFile()
            h.write(Data(line.utf8))
            try? h.close()
        }
    }

    /// The whole run; never throws. Its outcome is also in result.json.
    @discardableResult
    public func run(_ req: HelperUpdateRequest) -> MacRunResult {
        let dir: URL
        do { dir = try makeRunFolder(req.run) } catch {
            log("update \(req.run): \(error)")
            return MacRunResult(ok: false, outcome: MacOutcome.failed, error: "\(error)", at: HostFiles.iso(now()))
        }
        let logFile = dir.appendingPathComponent(MacUpdateRun.logFile)
        FileManager.default.createFile(atPath: logFile.path, contents: nil, attributes: [.posixPermissions: 0o644])
        logURL = logFile
        var result = steps(req, dir: dir)
        result.at = HostFiles.iso(now())
        if result.log == nil { result.log = logFile.path }
        log("update \(req.run): \(result.ok ? "done" : "\(result.outcome ?? "failed"): \(result.error ?? "")")")
        if let d = try? result.data() { try? LocalFileOps().writeAtomically(d, to: dir.appendingPathComponent(MacUpdateRun.resultFile), mode: 0o644) }
        return result
    }

    func steps(_ req: HelperUpdateRequest, dir: URL) -> MacRunResult {
        var linux: String?
        if req.linuxFirst || req.linuxOnly {
            let (state, why) = linuxPart()
            linux = state
            if state == "failed" {
                return MacRunResult(ok: false, outcome: MacOutcome.linuxFailed, error: why ?? "the Linux side's update failed", linux: "failed")
            }
            if req.linuxOnly { return MacRunResult(ok: true, linux: state) }
        }
        guard let from = req.from, let tag = req.tag, let uid = req.forUID else {
            return MacRunResult(ok: false, outcome: MacOutcome.failed, error: "no download to install", linux: linux)
        }
        // 1. The copy, out of the account's reach, checked again.
        if let why = checkFrom(from, run: req.run, uid: uid) {
            return MacRunResult(ok: false, outcome: MacOutcome.failed, error: why, linux: linux)
        }
        for name in MacReleaseNames.all {
            let max = name == MacReleaseNames.package ? MacStep.maxPackageBytes : 64 * 1024
            if let why = copyIn(from.appendingPathComponent(name), to: dir.appendingPathComponent(name), max: max,
                                mode: name == MacReleaseNames.package ? 0o600 : 0o644) {
                return MacRunResult(ok: false, outcome: MacOutcome.noSignedSetup, error: "the download has no usable \(name) (\(why))", linux: linux)
            }
        }
        let build = PinnedRelease.build(share: layout.share, trust: trust)
        let want = MacSetupExpectation(tag: tag, commit: req.commit, installed: build?.version, key: PinnedRelease.key(share: layout.share, trust: trust))
        let pkg = dir.appendingPathComponent(MacReleaseNames.package)
        let files = MacSetupFiles(manifest: try? Data(contentsOf: dir.appendingPathComponent(MacReleaseNames.manifest)),
                                  manifestSig: try? Data(contentsOf: dir.appendingPathComponent(MacReleaseNames.manifestSig)),
                                  packageSig: try? Data(contentsOf: dir.appendingPathComponent(MacReleaseNames.packageSig)), package: pkg)
        let v = MacSetupCheck.run(want, files)
        if let bad = v.problem {
            try? FileManager.default.removeItem(at: pkg)
            log("refused the copy: \(bad)")
            return MacRunResult(ok: false, outcome: v.code ?? MacOutcome.badSignature, error: "Domovoi didn't install the package: \(bad)", linux: linux)
        }
        let version = v.manifest?.version
        log("the copy checks out: DomovoiSetup-mac.pkg \(version ?? "?") for \(tag), signed by the pinned key; installed \(build?.version ?? "?")")
        // 2. The package, as a command-line install (one at a time with any
        // install, repair or uninstall).
        do {
            let lock = try BusyLock(layout.busyLock, what: "update")
            defer { _ = lock }
            let r = try runner.run(Command(installer, ["-pkg", pkg.path, "-target", "/"], timeout: 1800, currentDirectory: "/")) { [weak self] line in
                self?.log("installer: \(line)")
            }
            guard r.ok else {
                restoreJobs()
                return MacRunResult(ok: false, outcome: MacOutcome.repairFailed,
                                    error: "the package didn't install (installer: \(r.timedOut ? "timed out" : "exit \(r.status)"): \(r.tail))", version: build?.version, linux: linux)
            }
        } catch {
            restoreJobs()
            return MacRunResult(ok: false, outcome: MacOutcome.repairFailed, error: "the package didn't install: \(error)", linux: linux)
        }
        let after = PinnedRelease.build(share: layout.share, trust: trust)
        guard after?.version == version else {
            restoreJobs()
            return MacRunResult(ok: false, outcome: MacOutcome.repairFailed,
                                error: "the package installed, but the Mac side says \(after?.version ?? "nothing"), not \(version ?? "?")", linux: linux)
        }
        // 3. The new helper's repair: every step again with the saved answers.
        // Never from an offline kit (--no-kit): this update came by the
        // internet, and a stick left plugged in is not part of it.
        var lastFail: String?
        do {
            let r = try runner.run(Command(layout.helperProgram.path, ["install", "--system", "--repair", "--no-kit"], timeout: 3 * 3600, currentDirectory: "/")) { [weak self] line in
                if line.hasPrefix(StdProgress.prefix),
                   let o = try? JSONSerialization.jsonObject(with: Data(line.dropFirst(StdProgress.prefix.count).utf8)) as? [String: Any],
                   o["state"] as? String == "fail" {
                    lastFail = "\(o["step"] as? String ?? "?"): \(o["message"] as? String ?? "")"
                }
                self?.log("repair: \(line)")
            }
            guard r.ok else {
                restoreJobs()
                return MacRunResult(ok: false, outcome: MacOutcome.repairFailed,
                                    error: "the repair after the package failed at \(lastFail ?? r.tail)", version: version, linux: linux,
                                    log: layout.logs.appendingPathComponent("helper.log").path)
            }
        } catch {
            restoreJobs()
            return MacRunResult(ok: false, outcome: MacOutcome.repairFailed, error: "the repair didn't start: \(error)", version: version, linux: linux)
        }
        return MacRunResult(ok: true, version: version, linux: linux)
    }

    // ─── the run folder ────────────────────────────────────────────────

    /// /Library/Application Support/Domovoi/updates (root, 0755), then a new
    /// folder NAME in it; the oldest runs past `keepRuns` removed.
    public func makeRunFolder(_ run: String) throws -> URL {
        guard MacUpdateRun.isRunName(run) else { throw HelperError("--run takes a run name") }
        let fm = FileManager.default
        guard fm.fileExists(atPath: layout.appSupport.path) else { throw HelperError("\(layout.appSupport.path) is missing: Domovoi isn't installed") }
        var st = stat()
        if lstat(adminRoot.path, &st) == 0 {
            guard (st.st_mode & S_IFMT) == S_IFDIR else { throw HelperError("\(adminRoot.path) isn't a folder; not using it") }
        } else {
            try fm.createDirectory(at: adminRoot, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o755])
        }
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: adminRoot.path)
        let old = ((try? fm.contentsOfDirectory(atPath: adminRoot.path)) ?? []).filter(MacUpdateRun.isRunName).sorted()
        for name in old.dropLast(max(0, keepRuns - 1)) where name != run { try? fm.removeItem(at: adminRoot.appendingPathComponent(name)) }
        let dir = adminRoot.appendingPathComponent(run, isDirectory: true)
        guard mkdir(dir.path, 0o755) == 0 else { throw HelperError("the run folder \(dir.path) can't be made (it may exist already): \(String(cString: strerror(errno)))") }
        return dir
    }

    /// The app's folder must be that account's own run folder, a real
    /// folder it owns.
    func checkFrom(_ from: URL, run: String, uid: uid_t) -> String? {
        guard let h = home(uid) else { return "no account \(uid) on this Mac" }
        let want = MacUpdateRun.userRoot(home: URL(fileURLWithPath: h, isDirectory: true)).appendingPathComponent(run).standardizedFileURL.path
        guard from.standardizedFileURL.path == want else { return "the download isn't in that account's own Domovoi updates folder" }
        var st = stat()
        guard lstat(want, &st) == 0, (st.st_mode & S_IFMT) == S_IFDIR else { return "the download folder isn't a folder" }
        guard ownerOf(want) == uid else { return "the download folder isn't that account's own" }
        return nil
    }

    /// One file in, never through a link, at most `max` bytes, as a new file.
    func copyIn(_ src: URL, to dest: URL, max: Int64, mode: mode_t) -> String? {
        let fd = open(src.path, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else { return String(cString: strerror(errno)) }
        defer { close(fd) }
        var st = stat()
        guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG else { return "not a file" }
        guard Int64(st.st_size) <= max else { return "larger than expected" }
        let out = open(dest.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode)
        guard out >= 0 else { return String(cString: strerror(errno)) }
        defer { close(out) }
        var buf = [UInt8](repeating: 0, count: 1 << 20)
        var total: Int64 = 0
        while true {
            let n = read(fd, &buf, buf.count)
            if n < 0 { return String(cString: strerror(errno)) }
            if n == 0 { break }
            total += Int64(n)
            if total > max { return "larger than expected" }
            var off = 0
            while off < n {
                let w = buf.withUnsafeBytes { write(out, $0.baseAddress! + off, n - off) }
                if w <= 0 { return String(cString: strerror(errno)) }
                off += w
            }
        }
        fchmod(out, mode)
        return nil
    }

    // ─── after a failure ───────────────────────────────────────────────

    /// The jobs as they were: each one whose file is there is loaded and
    /// running again (a failed install or repair may have stopped it). The
    /// root helper's own daemon is always restarted: the package may have
    /// replaced its program before the repair (whose `lan` step restarts it
    /// on success) failed, and the old process must not keep running.
    public func restoreJobs() {
        // The speech service (PHASE8A-STT.md) when it is installed: its file
        // is there only then.
        for label in [Labels.ollama, Labels.stt, Labels.vm, Labels.helper] {
            let plist = layout.plist(label)
            guard FileManager.default.fileExists(atPath: plist.path) else { continue }
            let target = "system/\(label)"
            let printed = try? runner.run(Command(LaunchdInstaller.launchctl, ["print", target], timeout: 20))
            if printed?.ok == true {
                if label == Labels.helper {
                    _ = try? runner.run(Command(LaunchdInstaller.launchctl, ["kickstart", "-k", target], timeout: 30))
                    log("restarted \(label) (the program on disk now)")
                } else if LaunchdJobState.parse(printed!.stdout).state != "running" {
                    _ = try? runner.run(Command(LaunchdInstaller.launchctl, ["kickstart", target], timeout: 30))
                    log("restarted \(label)")
                }
            } else {
                _ = try? runner.run(Command(LaunchdInstaller.launchctl, ["bootstrap", "system", plist.path], timeout: 30))
                log("loaded \(label) again")
            }
        }
    }

    // ─── the Linux part ────────────────────────────────────────────────

    /// The dashboard's own update, in the VM: ("updated" | "nothing_new" |
    /// "failed", why).
    func linuxPart() -> (String, String?) {
        guard let rec = try? RecordStore.load(layout.installRecord), rec.uninstalledAt == nil else {
            return ("failed", "Domovoi isn't installed (no install record)")
        }
        let lima = Lima(limactl: layout.limactl.path, limaHome: layout.limaHome.path, home: layout.jobHome.path, runner: runner, runAs: rec.roleAccount)
        let before = coreState()?.lastUpdateStarted
        let script = "cd /opt/domovoi && set -a && . domovoi/.env && set +a && exec .venv/bin/python -m domovoi.windows_side linux-update"
        let r: CommandResult
        do {
            r = try lima.shell(Names.instance, ["sudo", "-u", "domovoi", "-H", "sh", "-c", script], timeout: 1800)
        } catch {
            return ("failed", "the Linux update didn't start: \(error)")
        }
        guard let line = r.stdout.split(separator: "\n").last(where: { $0.hasPrefix("{") }),
              let o = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else {
            return ("failed", "the Linux update gave no answer (exit \(r.status)): \(r.tail)")
        }
        guard o["ok"] as? Bool == true else { return ("failed", o["error"] as? String ?? "the Linux side refused the update") }
        if o["nothing_new"] as? Bool == true { log("the Linux side: nothing new"); return ("nothing_new", nil) }
        log("the Linux side's update is running (\(o["from_sha"] as? String ?? "?") to \(o["to_sha"] as? String ?? "?"))")
        // Until the core says the update unit has recorded a new run and
        // nothing is under way.
        let start = sleeper.now()
        sleeper.sleep(3)
        while sleeper.now().timeIntervalSince(start) < waitMinutes * 60 {
            if let s = coreState(), !s.linuxBusy, let started = s.lastUpdateStarted, started != before, s.lastUpdateStatus != "running" {
                if s.lastUpdateStatus == "ok" { return ("updated", nil) }
                return ("failed", "the Linux side's update \(s.lastUpdateStatus ?? "failed")\(s.lastUpdateError.map { ": " + $0 } ?? "")")
            }
            sleeper.sleep(3)
        }
        return ("failed", "the Linux side's update didn't finish within \(Int(waitMinutes)) minutes")
    }

    func coreState() -> MacUpdateState? {
        guard let r = http.get(core + "/v1/mac/update", timeout: 10), r.status == 200 else { return nil }
        return MacUpdateState.decode(r.body)
    }
}
