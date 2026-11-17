import XCTest
@testable import DomovoiMacKit

/// The helper's half of the Mac update (HelperUpdate.swift), into a
/// temporary root laid out as the system install, with `installer`, the new
/// helper's repair, launchctl and the VM answered by fakes. The downloads
/// are the signed fixtures in mac/tests/release-sig/.
final class HelperUpdateTests: XCTestCase {
    let fx = macDir.appendingPathComponent("tests/release-sig")
    let commit = "c0ffee0123456789abcdef0123456789abcdef01"
    let run = "20270115-080000-0a1b2c3d"
    var dir: URL!
    var layout: Layout!
    var home: URL!
    var from: URL!
    var runner: FakeRunner!
    var http: FakeHTTP!
    var installerExit: Int32 = 0
    var installedVersion = "1.2.0"
    var repairOK = true

    override func setUpWithError() throws {
        dir = try tempDir()
        layout = Layout(mode: .system, root: dir.appendingPathComponent("root"))
        let fm = FileManager.default
        try fm.createDirectory(at: layout.share.appendingPathComponent("installer"), withIntermediateDirectories: true)
        try fm.createDirectory(at: layout.bin, withIntermediateDirectories: true)
        try fm.createDirectory(at: layout.logs, withIntermediateDirectories: true)
        try fm.copyItem(at: fx.appendingPathComponent("test-key.pub"), to: layout.share.appendingPathComponent(MacReleaseNames.keyFile))
        try writeBuild("1.1.0")
        home = dir.appendingPathComponent("Users/alex")
        from = MacUpdateRun.userRoot(home: home).appendingPathComponent(run)
        try fm.createDirectory(at: from, withIntermediateDirectories: true)
        for (name, src) in [(MacReleaseNames.package, "package.bin"), (MacReleaseNames.packageSig, "package.bin.sig"),
                            (MacReleaseNames.manifest, "manifest-1.2.0.json"), (MacReleaseNames.manifestSig, "manifest-1.2.0.json.sig")] {
            try fm.copyItem(at: fx.appendingPathComponent(src), to: from.appendingPathComponent(name))
        }
        runner = FakeRunner()
        http = FakeHTTP()
        runner.on { [unowned self] c in
            switch (c.executable as NSString).lastPathComponent {
            case "installer":
                // The package puts the new share/build.json in place.
                if self.installerExit == 0 { try? self.writeBuild(self.installedVersion) }
                return CommandResult(status: self.installerExit, stdout: "installer: The upgrade was successful.\n", stderr: self.installerExit == 0 ? "" : "installer: Error")
            case "DomovoiHelper":
                return self.repairOK
                    ? CommandResult(status: 0, stdout: "\(StdProgress.prefix){\"step\":\"install\",\"state\":\"done\",\"message\":\"Domovoi is installed\",\"v\":1}\n")
                    : CommandResult(status: 1, stdout: "\(StdProgress.prefix){\"step\":\"provision\",\"state\":\"fail\",\"message\":\"provision.sh: failed at venv\",\"v\":1}\n")
            case "launchctl":
                if c.arguments.first == "print" { return CommandResult(status: 113) }
                return CommandResult(status: 0)
            default: return nil
            }
        }
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func writeBuild(_ version: String) throws {
        try #"{"schema":1,"version":"\#(version)","commit":"\#(commit)","repo_url":"https://example.org/d.git"}"#
            .write(to: layout.share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
    }

    func helper() -> HelperUpdate {
        let h = HelperUpdate(layout: layout, runner: runner, sleeper: FakeSleeper(), http: http)
        h.trust = { _ in true }
        h.home = { [unowned self] uid in uid == 501 ? self.home.path : nil }
        h.ownerOf = { _ in 501 }
        h.now = { Date(timeIntervalSince1970: 1_800_000_000) }
        return h
    }

    func request(tag: String = "v1.2.0", from: URL? = nil, uid: uid_t = 501) -> HelperUpdateRequest {
        var r = HelperUpdateRequest(run: run)
        r.from = from ?? self.from
        r.tag = tag
        r.commit = commit
        r.forUID = uid
        return r
    }

    var runDir: URL { MacUpdateRun.adminRoot(layout).appendingPathComponent(run) }
    func written() -> MacRunResult? { MacRunResult.read(runDir.appendingPathComponent(MacUpdateRun.resultFile)) }

    func testACheckedPackageIsInstalledThenRepaired() throws {
        let r = helper().run(request())
        XCTAssertTrue(r.ok, r.error ?? "")
        XCTAssertEqual(r.version, "1.2.0")
        XCTAssertEqual(written(), r, "the app reads the same in result.json")
        let installs = runner.ran("installer")
        XCTAssertEqual(installs.count, 1)
        XCTAssertEqual(installs[0].arguments, ["-pkg", runDir.appendingPathComponent(MacReleaseNames.package).path, "-target", "/"],
                       "the root-only copy, never the account's download")
        let repairs = runner.ran("DomovoiHelper")
        XCTAssertEqual(repairs.map(\.arguments), [["install", "--system", "--repair"]])
        XCTAssertEqual(repairs[0].executable, layout.helperProgram.path, "the NEW helper, from where the package put it")
        // The order: the package, then its repair.
        let names = runner.calls.map { ($0.executable as NSString).lastPathComponent }
        XCTAssertLessThan(names.firstIndex(of: "installer")!, names.firstIndex(of: "DomovoiHelper")!)
        // Modes: the copy of the package root's alone, the result readable.
        let pkgMode = try FileManager.default.attributesOfItem(atPath: runDir.appendingPathComponent(MacReleaseNames.package).path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(pkgMode?.intValue, 0o600)
        let resMode = try FileManager.default.attributesOfItem(atPath: runDir.appendingPathComponent(MacUpdateRun.resultFile).path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(resMode?.intValue, 0o644)
        let log = try String(contentsOf: runDir.appendingPathComponent(MacUpdateRun.logFile), encoding: .utf8)
        XCTAssertTrue(log.contains("the copy checks out"), log)
    }

    func testTheCopyIsCheckedAgainAndATamperedOneNeverInstalled() throws {
        // Changed after the app's own check: the helper's copy doesn't check out.
        var pkg = try Data(contentsOf: from.appendingPathComponent(MacReleaseNames.package))
        pkg[5] ^= 1
        try pkg.write(to: from.appendingPathComponent(MacReleaseNames.package))
        let r = helper().run(request())
        XCTAssertFalse(r.ok)
        XCTAssertEqual(r.outcome, MacOutcome.badSignature)
        // Its signature (on a Mac) or, past that, its SHA-256: either way the package.
        XCTAssertTrue(r.error!.hasPrefix("Domovoi didn't install the package: the package"), r.error!)
        XCTAssertTrue(runner.ran("installer").isEmpty && runner.ran("DomovoiHelper").isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: runDir.appendingPathComponent(MacReleaseNames.package).path), "the refused copy is deleted")
        XCTAssertEqual(written()?.outcome, MacOutcome.badSignature)
    }

    func testTheHelpersOwnPinsDecide() {
        // The key the install pinned, not the app's: another key there refuses.
        try? FileManager.default.removeItem(at: layout.share.appendingPathComponent(MacReleaseNames.keyFile))
        try? FileManager.default.copyItem(at: fx.appendingPathComponent("other-key.pub"), to: layout.share.appendingPathComponent(MacReleaseNames.keyFile))
        XCTAssertEqual(helper().run(request()).outcome, MacOutcome.badSignature)
        XCTAssertTrue(runner.ran("installer").isEmpty)
    }

    func testFilesNotRootsAreNotBelieved() {
        let h = helper()
        h.trust = { _ in false }
        let r = h.run(request())
        XCTAssertEqual(r.outcome, MacOutcome.noKey, "no key it can believe: nothing installed")
        XCTAssertTrue(runner.ran("installer").isEmpty)
    }

    func testNotNewerThanWhatTheInstallSaysIsRefused() throws {
        try writeBuild("1.2.0")
        let r = helper().run(request())
        XCTAssertEqual(r.outcome, MacOutcome.notNewer)
        XCTAssertTrue(runner.ran("installer").isEmpty)
    }

    func testOnlyTheAccountsOwnRunFolderIsTaken() throws {
        let elsewhere = dir.appendingPathComponent("tmp/x")
        try FileManager.default.createDirectory(at: elsewhere, withIntermediateDirectories: true)
        for r in [request(from: elsewhere), request(uid: 502)] {
            try? FileManager.default.removeItem(at: runDir)
            let out = helper().run(r)
            XCTAssertEqual(out.outcome, MacOutcome.failed)
            XCTAssertFalse(out.error!.contains("can't be made"), "refused for the folder, not the run name: \(out.error!)")
        }
        let h = helper()
        h.ownerOf = { _ in 502 }
        try? FileManager.default.removeItem(at: runDir)
        let notOwn = h.run(request())
        XCTAssertEqual(notOwn.outcome, MacOutcome.failed)
        XCTAssertTrue(notOwn.error!.contains("isn't that account's own"), notOwn.error!)
        XCTAssertTrue(runner.ran("installer").isEmpty)
    }

    func testALinkInTheDownloadIsRefused() throws {
        let p = from.appendingPathComponent(MacReleaseNames.manifest)
        try FileManager.default.removeItem(at: p)
        try FileManager.default.createSymbolicLink(at: p, withDestinationURL: fx.appendingPathComponent("manifest-1.2.0.json"))
        let r = helper().run(request())
        XCTAssertEqual(r.outcome, MacOutcome.noSignedSetup)
        XCTAssertTrue(runner.ran("installer").isEmpty)
    }

    func testARunFolderIsNeverReused() {
        _ = helper().run(request())
        let again = helper().run(request())
        XCTAssertEqual(again.outcome, MacOutcome.failed)
        XCTAssertTrue(again.error!.contains("can't be made"), again.error!)
        XCTAssertEqual(runner.ran("installer").count, 1)
    }

    func testOldRunsAreRemoved() throws {
        let root = MacUpdateRun.adminRoot(layout)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        for i in 0..<7 { try FileManager.default.createDirectory(at: root.appendingPathComponent("20261101-00000\(i)-0a1b2c3d"), withIntermediateDirectories: false) }
        _ = helper().run(request())
        let left = try FileManager.default.contentsOfDirectory(atPath: root.path).sorted()
        XCTAssertEqual(left.count, 5)
        XCTAssertEqual(left.last, run)
    }

    func testAFailedInstallRestartsTheJobsAndSaysSo() throws {
        installerExit = 1
        for label in [Labels.ollama, Labels.vm] {
            try FileManager.default.createDirectory(at: layout.launchDaemons, withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: layout.plist(label).path, contents: Data("x".utf8))
        }
        let r = helper().run(request())
        XCTAssertEqual(r.outcome, MacOutcome.repairFailed)
        XCTAssertTrue(r.error!.contains("the package didn't install"), r.error!)
        XCTAssertTrue(runner.ran("DomovoiHelper").isEmpty, "no repair after a failed install")
        let boots = runner.ran("launchctl", "bootstrap")
        XCTAssertEqual(boots.map { $0.arguments.last! }, [layout.plist(Labels.ollama).path, layout.plist(Labels.vm).path], "both jobs loaded again")
        XCTAssertEqual(r.log, runDir.appendingPathComponent(MacUpdateRun.logFile).path)
    }

    /// The speech service (PHASE8A-STT.md) is put back like Ollama, in order
    /// (Ollama, speech, then the VM), and only when it is installed.
    func testAFailedInstallRestartsTheSpeechServiceToo() throws {
        installerExit = 1
        for label in [Labels.ollama, Labels.stt, Labels.vm] {
            try FileManager.default.createDirectory(at: layout.launchDaemons, withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: layout.plist(label).path, contents: Data("x".utf8))
        }
        _ = helper().run(request())
        let boots = runner.ran("launchctl", "bootstrap")
        XCTAssertEqual(boots.map { $0.arguments.last! },
                       [layout.plist(Labels.ollama).path, layout.plist(Labels.stt).path, layout.plist(Labels.vm).path])
    }

    func testAFailedRepairRestartsTheHelperDaemonSoTheOldProgramDoesntRunOn() throws {
        // Phase 4's helper is a long-running daemon: the package replaced its
        // program, the repair (whose lan step restarts it) failed.
        repairOK = false
        try FileManager.default.createDirectory(at: layout.launchDaemons, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: layout.plist(Labels.helper).path, contents: Data("x".utf8))
        runner.on { c in
            guard c.executable == LaunchdInstaller.launchctl, c.arguments.first == "print", c.arguments.last == "system/\(Labels.helper)" else { return nil }
            return CommandResult(status: 0, stdout: "system/\(Labels.helper) = {\n\tstate = running\n\tpid = 77\n}\n")
        }
        let r = helper().run(request())
        XCTAssertEqual(r.outcome, MacOutcome.repairFailed)
        XCTAssertEqual(runner.ran("launchctl", "kickstart").map(\.arguments), [["kickstart", "-k", "system/\(Labels.helper)"]],
                       "restarted although it was running")
    }

    func testAPackageThatDidntLandIsAFailure() {
        installedVersion = "1.1.0"
        let r = helper().run(request())
        XCTAssertEqual(r.outcome, MacOutcome.repairFailed)
        XCTAssertTrue(r.error!.contains("says 1.1.0, not 1.2.0"), r.error!)
        XCTAssertTrue(runner.ran("DomovoiHelper").isEmpty)
    }

    func testAFailedRepairNamesItsStepAndLog() {
        repairOK = false
        let r = helper().run(request())
        XCTAssertEqual(r.outcome, MacOutcome.repairFailed)
        XCTAssertEqual(r.error, "the repair after the package failed at provision: provision.sh: failed at venv")
        XCTAssertEqual(r.log, layout.logs.appendingPathComponent("helper.log").path)
        XCTAssertEqual(r.version, "1.2.0", "the package itself is in")
    }

    // ─── the Linux part ────────────────────────────────────────────────

    func linuxRequest(first: Bool) -> HelperUpdateRequest {
        if first { var r = request(); r.linuxFirst = true; return r }
        var r = HelperUpdateRequest(run: run)
        r.linuxOnly = true
        return r
    }

    func installRecord() throws {
        let ans = InstallRecord.Answers(internet: "always", tier: .floor, ollama: "own", mediaRoot: "/Users/Shared/Domovoi", media: [],
                                        ports: Ports.real, size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: nil)
        var rec = InstallRecord(mode: .system, root: layout.root.path, answers: ans, launchdDomain: "system")
        rec.roleAccount = Names.roleAccount
        try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
        try RecordStore(url: layout.installRecord, record: rec, fileOps: LocalFileOps()).save()
    }

    /// The VM answers linux-update with `answer`; the core then reports the
    /// update unit's run as `status`.
    func linux(_ answer: String, status: String = "ok") {
        var polls = 0
        runner.on { c in
            guard c.executable == Lima.sudo, c.arguments.contains("linux-update") || c.arguments.last?.contains("linux-update") == true else { return nil }
            XCTAssertTrue(c.arguments.contains(Names.roleAccount), "limactl as the role account")
            XCTAssertTrue(c.arguments.contains("-u") && c.arguments.contains("domovoi"), "the service user in the VM")
            return CommandResult(status: 0, stdout: "INFO something\n\(answer)\n")
        }
        http.handler = { url in
            guard url.hasSuffix("/v1/mac/update") else { return nil }
            polls += 1
            let started = polls > 1 ? "2026-11-02T10:05:00Z" : "2026-11-02T09:00:00Z"
            let busy = polls == 2
            return HTTPResponse(status: 200, body: Data(#"{"schema":1,"mac":null,"step_needed":false,"linux":{"busy":\#(busy),"last_update":{"status":"\#(polls == 2 ? "running" : status)","started_at":"\#(started)","error":\#(status == "ok" ? "null" : "\"migrations failed\"")}}}"#.utf8))
        }
    }

    func testTheLinuxPartAloneWaitsForTheUpdateUnit() throws {
        try installRecord()
        linux(#"{"ok": true, "started": true, "nothing_new": false, "error": null, "from_sha": "aaa", "to_sha": "bbb"}"#)
        let r = helper().run(linuxRequest(first: false))
        XCTAssertTrue(r.ok, r.error ?? "")
        XCTAssertEqual(r.linux, "updated")
        XCTAssertTrue(runner.ran("installer").isEmpty, "no package for the Linux part")
    }

    func testTheLinuxPartWithNothingNew() throws {
        try installRecord()
        linux(#"{"ok": true, "started": false, "nothing_new": true, "error": null}"#)
        let r = helper().run(linuxRequest(first: false))
        XCTAssertTrue(r.ok)
        XCTAssertEqual(r.linux, "nothing_new")
    }

    func testAFailedLinuxPartStopsBeforeThePackage() throws {
        try installRecord()
        linux(#"{"ok": false, "started": false, "nothing_new": false, "error": "the pull failed: no network"}"#)
        let r = helper().run(linuxRequest(first: true))
        XCTAssertEqual(r.outcome, MacOutcome.linuxFailed)
        XCTAssertEqual(r.error, "the pull failed: no network")
        XCTAssertTrue(runner.ran("installer").isEmpty, "nothing on the Mac changed")
    }

    func testAnUpdateUnitThatRolledBackIsALinuxFailure() throws {
        try installRecord()
        linux(#"{"ok": true, "started": true, "nothing_new": false, "error": null}"#, status: "rolled_back")
        let r = helper().run(linuxRequest(first: true))
        XCTAssertEqual(r.outcome, MacOutcome.linuxFailed)
        XCTAssertEqual(r.error, "the Linux side's update rolled_back: migrations failed")
    }

    func testLinuxFirstThenThePackage() throws {
        try installRecord()
        linux(#"{"ok": true, "started": false, "nothing_new": true, "error": null}"#)
        let r = helper().run(linuxRequest(first: true))
        XCTAssertTrue(r.ok, r.error ?? "")
        XCTAssertEqual(r.linux, "nothing_new")
        XCTAssertEqual(r.version, "1.2.0")
        XCTAssertEqual(runner.ran("installer").count, 1)
    }
}
