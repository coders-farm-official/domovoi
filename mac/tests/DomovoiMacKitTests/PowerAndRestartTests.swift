import XCTest
@testable import DomovoiMacKit

/// pmset, answered from a dictionary: `-g custom` prints it, `-c sleep N` sets it.
final class FakePmset {
    var acSleep = 10
    var battery = true
    var setFails = false
    var sets: [Int] = []
    /// pmset's autorestart (nil: this Mac doesn't list it).
    var autorestart: Int?
    var autorestartSets: [Int] = []

    var text: String {
        (battery ? "Battery Power:\n Sleep On Power Button 1\n lowpowermode         0\n sleep                1\n displaysleep         2\n" : "")
            + "AC Power:\n Sleep On Power Button 1\n" + (autorestart.map { " autorestart          \($0)\n" } ?? "")
            + " sleep                \(acSleep)\n displaysleep         10\n womp                 1\n"
    }

    var batt: String {
        "Now drawing from 'AC Power'\n" + (battery ? " -InternalBattery-0 (id=4653155)\t100%; charged; 0:00 remaining present: true\n" : "")
    }

    func install(on r: FakeRunner) {
        r.on { [unowned self] c in
            guard c.executable == KeepAwake.pmset else { return nil }
            if c.arguments == ["-g", "custom"] { return CommandResult(status: 0, stdout: self.text) }
            if c.arguments == ["-g", "batt"] { return CommandResult(status: 0, stdout: self.batt) }
            if c.arguments.count == 3, c.arguments[0] == "-a", c.arguments[1] == "autorestart", let n = Int(c.arguments[2]) {
                if self.setFails { return CommandResult(status: 1, stderr: "pmset: must be run as root") }
                self.autorestart = n
                self.autorestartSets.append(n)
                return CommandResult(status: 0)
            }
            if c.arguments.count == 3, c.arguments[0] == "-c", c.arguments[1] == "sleep", let n = Int(c.arguments[2]) {
                if self.setFails { return CommandResult(status: 1, stderr: "pmset: must be run as root") }
                self.acSleep = n
                self.sets.append(n)
                return CommandResult(status: 0)
            }
            return CommandResult(status: 1, stderr: "unexpected pmset \(c.arguments)")
        }
    }
}

/// A system layout in a temporary root, with an install record.
func makeInstalledLayout(_ dir: URL, ollama: String = "own") throws -> Layout {
    let layout = Layout(mode: .system, root: dir)
    let ans = InstallRecord.Answers(internet: "never", tier: .floor, ollama: ollama, mediaRoot: "/Users/Shared/Domovoi", media: [],
                                    ports: Ports.real, size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: nil)
    var rec = InstallRecord(mode: .system, root: dir.path, answers: ans, launchdDomain: "system")
    rec.roleAccount = "_domovoi"
    try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
    try RecordStore(url: layout.installRecord, record: rec, fileOps: LocalFileOps()).save()
    try FileManager.default.createDirectory(at: layout.launchDaemons, withIntermediateDirectories: true)
    for l in [Labels.vm, Labels.ollama] { FileManager.default.createFile(atPath: layout.plist(l).path, contents: Data("x".utf8)) }
    return layout
}

final class KeepAwakeTests: XCTestCase {
    var dir: URL!
    var layout: Layout!
    var runner: FakeRunner!
    var pmset: FakePmset!

    override func setUpWithError() throws {
        dir = try tempDir()
        layout = try makeInstalledLayout(dir)
        runner = FakeRunner()
        pmset = FakePmset()
        pmset.install(on: runner)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    var k: KeepAwake { KeepAwake(layout: layout, runner: runner, fileOps: LocalFileOps(), now: { Date(timeIntervalSince1970: 1_800_000_000) }) }

    func testParsesALaptopAndADesktop() {
        let p = PowerProfile.parse(pmset.text)
        XCTAssertEqual(p.acSleepMinutes, 10)
        XCTAssertEqual(p.battery?["sleep"], "1")
        XCTAssertEqual(p.ac["Sleep On Power Button"], "1", "a key with spaces")
        XCTAssertTrue(p.laptop)
        pmset.battery = false
        XCTAssertFalse(PowerProfile.parse(pmset.text).laptop)
        XCTAssertEqual(PowerProfile.parse(" sleep 0\n displaysleep 10\n").acSleepMinutes, 0, "no header: the AC settings")
        XCTAssertNil(PowerProfile.parse("").acSleepMinutes)
    }

    func testOnRecordsFirstThenOffPutsItBack() throws {
        try k.turnOn()
        XCTAssertEqual(pmset.sets, [0])
        XCTAssertEqual(k.record?.acSleepBefore, 10, "the old value, written before the change")
        try k.turnOn()
        XCTAssertEqual(pmset.sets, [0], "already on: nothing changed again")
        XCTAssertEqual(k.record?.acSleepBefore, 10, "a second on never records our own 0 as before")
        let said = try k.turnOff()
        XCTAssertEqual(pmset.sets, [0, 10])
        XCTAssertNil(k.record)
        XCTAssertTrue(said.contains("back to 10"), said)
        XCTAssertEqual(try k.turnOff(), "Domovoi hadn't changed this Mac's sleep setting")
        XCTAssertEqual(pmset.sets, [0, 10])
    }

    func testTheOwnersOwnChangeStays() throws {
        try k.turnOn()
        pmset.acSleep = 30   // changed in System Settings since
        let said = try k.turnOff()
        XCTAssertEqual(pmset.sets, [0], "theirs is left as it is")
        XCTAssertNil(k.record)
        XCTAssertTrue(said.contains("left as it is"), said)
    }

    func testAMacThatNeverSleptStaysSo() throws {
        pmset.acSleep = 0
        try k.turnOn()
        XCTAssertEqual(pmset.sets, [])
        XCTAssertEqual(k.record?.acSleepBefore, 0)
        try k.turnOff()
        XCTAssertEqual(pmset.sets, [], "0 before, 0 after: nothing to put back")
    }

    func testFailuresAreSaid() throws {
        pmset.setFails = true
        XCTAssertThrowsError(try k.turnOn())
        let none = KeepAwake(layout: Layout(mode: .system, root: dir.appendingPathComponent("nothing")), runner: runner, fileOps: LocalFileOps())
        XCTAssertThrowsError(try none.turnOn()) { XCTAssertTrue("\($0)".contains("isn't installed")) }
    }

    func testTheAnswerIsSavedForARepair() throws {
        try k.saveAnswer(false)
        XCTAssertEqual(try RecordStore.load(layout.installRecord)?.answers.keepAwake, false)
        try k.saveAnswer(true)
        XCTAssertEqual(try RecordStore.load(layout.installRecord)?.answers.keepAwake, true)
    }

    func testUninstallPutsItBackAndEndsEveryAccountsIcon() throws {
        try k.turnOn()
        runner.on { c in c.executable == "/usr/bin/who" ? CommandResult(status: 0, stdout: "alex     console  Nov  2 09:00\nsam      console  Nov  2 10:00\nalex     ttys001  Nov  2 09:05\n") : nil }
        let said = MacSideCleanup.beforeUninstall(layout: layout, runner: runner, fileOps: LocalFileOps(), log: { _ in },
                                                  uidOf: { ["alex": 501, "sam": 502][$0] })
        XCTAssertEqual(pmset.acSleep, 10)
        XCTAssertNil(k.record, "power.json is gone, so Remove everything finds the state folder empty")
        XCTAssertEqual(said.count, 1)
        let boots = runner.calls.filter { $0.executable == MenuBarAgent.launchctl && $0.arguments.first == "bootout" }.map { $0.arguments[1] }
        XCTAssertEqual(boots, ["gui/501/\(Labels.menuBar)", "gui/502/\(Labels.menuBar)"])

        let user = FakeRunner()
        XCTAssertEqual(MacSideCleanup.beforeUninstall(layout: Layout.user(prefix: dir.path), runner: user, fileOps: LocalFileOps(), log: { _ in }), [])
        XCTAssertTrue(user.calls.isEmpty, "the user mode never touches power or other accounts")
    }

    func testConsoleUsers() {
        XCTAssertEqual(MenuBarAgent.consoleUsers(whoOutput: "alex console x\nalex console y\nsam ttys000 z\n"), ["alex"])
    }
}

final class RestartTests: XCTestCase {
    func f(_ change: (inout RestartFacts) -> Void) -> RestartFacts {
        var x = RestartFacts()
        x.updateUnitLoaded = true
        x.updateUnitState = "inactive"
        change(&x)
        return x
    }

    func testTheLadder() throws {
        XCTAssertEqual(try RestartPlan.decide(f { _ in }), [.startUpdateUnit], "all well: what the dashboard's Restart does")
        XCTAssertEqual(try RestartPlan.decide(f { $0.updateUnitState = "failed" }), [.startUpdateUnit])
        XCTAssertEqual(try RestartPlan.decide(f { $0.updateUnitLoaded = false }), [.restartUnits], "no update unit: core and web")
        XCTAssertEqual(try RestartPlan.decide(f { $0.vmReachable = false }), [.kickstart(Labels.vm, kill: true), .waitVM])
        XCTAssertEqual(try RestartPlan.decide(f { $0.vm.running = false }), [.kickstart(Labels.vm, kill: false), .waitVM])
        XCTAssertEqual(try RestartPlan.decide(f { $0.vm.loaded = false; $0.vm.running = false }), [.bootstrap(Labels.vm), .waitVM])
        XCTAssertEqual(try RestartPlan.decide(f { $0.ollamaAnswers = false }), [.kickstart(Labels.ollama, kill: true), .startUpdateUnit])
        XCTAssertEqual(try RestartPlan.decide(f { $0.ollama.loaded = false }), [.bootstrap(Labels.ollama), .startUpdateUnit])
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownOllama = false; $0.ollama.loaded = false }), [.startUpdateUnit], "the household's Ollama: not ours")
    }

    /// The speech service (PHASE8A-STT.md): restarted like Ollama when it is
    /// installed, never a reason to refuse (the core falls back on its own).
    func testTheSpeechServiceRung() throws {
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownStt = false; $0.stt.loaded = false }), [.startUpdateUnit], "none installed")
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownStt = true; $0.stt.loaded = false }), [.bootstrap(Labels.stt), .startUpdateUnit])
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownStt = true; $0.stt.running = false }), [.kickstart(Labels.stt, kill: false), .startUpdateUnit])
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownStt = true; $0.sttAnswers = false }), [.kickstart(Labels.stt, kill: true), .startUpdateUnit])
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownStt = true; $0.stt.disabled = true; $0.stt.running = false }), [.startUpdateUnit],
                       "switched off by the owner: left off, and no refusal")
        XCTAssertEqual(try RestartPlan.decide(f { $0.ownStt = true; $0.stt.plist = false }), [.startUpdateUnit])
    }

    func testOneAtATimeAndTheOwnersSwitch() {
        for busy in ["activating", "active", "deactivating", "reloading"] {
            XCTAssertThrowsError(try RestartPlan.decide(f { $0.updateUnitState = busy })) { XCTAssertEqual("\($0)", RestartPlan.underWay) }
        }
        XCTAssertThrowsError(try RestartPlan.decide(f { $0.vm.disabled = true })) {
            XCTAssertTrue("\($0)".contains("“limactl”") && "\($0)".contains("Login Items"), "\($0)")
        }
        XCTAssertThrowsError(try RestartPlan.decide(f { $0.ollama.disabled = true })) { XCTAssertTrue("\($0)".contains("Infra Technologies")) }
        XCTAssertThrowsError(try RestartPlan.decide(f { $0.vm.plist = false })) { XCTAssertTrue("\($0)".contains("Repair")) }
        XCTAssertThrowsError(try RestartPlan.decide(f { $0.installed = false }))
    }

    // ─── the helper's run, against fakes ───────────────────────────────

    var dir: URL!
    var layout: Layout!
    var runner: FakeRunner!
    var launchd: FakeLaunchd!
    var http: FakeHTTP!
    var unit = "LoadState=loaded\nActiveState=inactive\n"
    var disabled = "disabled services = {\n}\n"
    var guest: [[String]] = []
    /// The update unit's ActiveState once the restart has started it (nil: as `unit` says).
    var unitAfterStart: String?
    var started = false
    /// Whether the dashboard says "ok" (asked on every probe).
    var healthy: () -> Bool = { true }
    /// Called when the services' restart is sent (a test changes the world then).
    var onStart: () -> Void = {}
    let sleeper = FakeSleeper()

    let okHealth = HTTPResponse(status: 200, body: Data(#"{"status":"ok","db_reachable":true,"domovoi_reachable":true,"core_alive":true}"#.utf8))
    let degraded = HTTPResponse(status: 200, body: Data(#"{"status":"degraded","db_reachable":true,"domovoi_reachable":false,"core_alive":false}"#.utf8))

    override func setUpWithError() throws {
        dir = try tempDir()
        layout = try makeInstalledLayout(dir)
        runner = FakeRunner()
        launchd = FakeLaunchd()
        launchd.install(on: runner)
        launchd.loaded[Labels.vm] = LaunchdJobState(state: "running", pid: 10, lastExit: nil)
        launchd.loaded[Labels.ollama] = LaunchdJobState(state: "running", pid: 11, lastExit: nil)
        http = FakeHTTP()
        http.answers["http://127.0.0.1:11434/api/version"] = HTTPResponse(status: 200, body: Data(#"{"version":"0.34.4"}"#.utf8))
        // The dashboard on Domovoi's own port; the core's /v1/alive never answers here.
        http.handler = { [unowned self] url in
            url == "http://127.0.0.1:6369/api/health" ? (self.healthy() ? self.okHealth : self.degraded) : nil
        }
        runner.on { [unowned self] c in
            if c.executable == LaunchdInstaller.launchctl, c.arguments == ["print-disabled", "system"] { return CommandResult(status: 0, stdout: self.disabled) }
            guard c.executable == Lima.sudo else { return nil }
            XCTAssertEqual(Array(c.arguments.prefix(3)), ["-n", "-u", "_domovoi"], "limactl as the role account")
            guard let i = c.arguments.firstIndex(where: { $0.hasSuffix("limactl") }) else { return CommandResult(status: 1) }
            let a = Array(c.arguments[(i + 1)...])
            if a.first == "list" { return CommandResult(status: 0, stdout: "domovoi \(self.launchd.loaded[Labels.vm]?.running == true ? "Running" : "Stopped")\n") }
            if a.first == "shell" {
                let inner = Array(a.dropFirst(4))
                self.guest.append(inner)
                if inner == ["true"] { return CommandResult(status: 0) }
                if inner.first == "systemctl", inner.dropFirst().first == "show" {
                    if self.started, let s = self.unitAfterStart { return CommandResult(status: 0, stdout: "LoadState=loaded\nActiveState=\(s)\n") }
                    return CommandResult(status: 0, stdout: self.unit)
                }
                if inner.contains("--no-block") {
                    self.started = true
                    self.onStart()
                }
                return CommandResult(status: 0)
            }
            return CommandResult(status: 0)
        }
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func restarter() -> Restarter { Restarter(layout: layout, runner: runner, sleeper: sleeper, http: http) }

    var vmKills: Int { runner.calls.filter { $0.arguments == ["kickstart", "-k", "system/\(Labels.vm)"] }.count }

    func testAHealthyVMStartsTheUpdateUnit() throws {
        let r = try restarter().run()
        XCTAssertTrue(r.back)
        XCTAssertFalse(r.escalated)
        XCTAssertEqual(r.said, "Domovoi is back: its services were restarted.")
        XCTAssertTrue(guest.contains(["sudo", "systemctl", "--no-block", "start", "domovoi-update.service"]))
        XCTAssertTrue(guest.contains(["systemctl", "show", "--property=ActiveState", "domovoi-update.service"]), "waited for the unit to end")
        XCTAssertFalse(runner.calls.contains { $0.arguments.first == "kickstart" || $0.arguments.first == "bootstrap" }, "healthy: no escalation")
    }

    func testHealthCountsOnlyOnceTheUpdateUnitHasEnded() throws {
        // The old core's "ok" from before the unit stopped it must not count:
        // the unit runs for 30 s, and health is asked only after.
        unitAfterStart = "activating"
        var looksWhileRunning = 0
        healthy = { [unowned self] in
            if self.unitAfterStart == "activating" { looksWhileRunning += 1 }
            return true
        }
        runner.on { [unowned self] c in
            if c.arguments.contains("--property=ActiveState"), self.sleeper.slept >= 30 { self.unitAfterStart = "inactive" }
            return nil
        }
        let r = try restarter().run()
        XCTAssertTrue(r.back)
        XCTAssertEqual(vmKills, 0)
        XCTAssertEqual(looksWhileRunning, 0)
        XCTAssertGreaterThanOrEqual(sleeper.slept, 30)
    }

    func testUnhealthyAfterTheServicesRestartTheVMIsRestartedOnce() throws {
        healthy = { [unowned self] in self.vmKills > 0 }
        let r = try restarter().run()
        XCTAssertTrue(r.back, r.said)
        XCTAssertTrue(r.escalated)
        XCTAssertEqual(vmKills, 1, "one rung up: the VM, as for a VM that can't be reached")
        XCTAssertEqual(r.said, "Domovoi is back: its services were restarted, and when it still didn't answer, its virtual machine was restarted too.")
        XCTAssertEqual(guest.filter { $0.contains("--no-block") }.count, 1, "the services' restart was sent once")
    }

    func testStillSilentAfterTheVMRestartSaysSoAndStops() throws {
        healthy = { false }
        let r = try restarter().run()
        XCTAssertFalse(r.back)
        XCTAssertTrue(r.escalated)
        XCTAssertEqual(vmKills, 1, "at most one escalation per press: no loop")
        XCTAssertTrue(r.said.hasPrefix("Domovoi is still not answering — choose Save logs for support"), r.said)
        XCTAssertTrue(r.said.contains("What was done: restarted Domovoi's services, then restarted its virtual machine; now the dashboard answers but can't reach the core."), r.said)
        XCTAssertFalse(r.said.contains("\n"))
        XCTAssertEqual(RestartResult.alertText(r.said), r.said, "the menu shows it as it is")
    }

    func testNoEscalationOverTheOwnersSwitch() throws {
        healthy = { false }
        // Switched off in Login Items while the services restarted.
        onStart = { [unowned self] in self.disabled = "disabled services = {\n\t\"com.domovoi.mac.vm\" => disabled\n}\n" }
        let r = try restarter().run()
        XCTAssertFalse(r.back)
        XCTAssertFalse(r.escalated)
        XCTAssertEqual(vmKills, 0)
        XCTAssertTrue(r.said.contains("didn't restart its virtual machine") && r.said.contains("Login Items"), r.said)
        XCTAssertFalse(runner.calls.contains { $0.arguments.first == "bootstrap" || $0.arguments.first == "enable" })
    }

    func testAnUpdateStillRunningIsNeverCutShort() throws {
        healthy = { false }
        unitAfterStart = "activating"
        let r = try restarter().run()
        XCTAssertFalse(r.back)
        XCTAssertFalse(r.escalated)
        XCTAssertEqual(vmKills, 0)
        XCTAssertTrue(r.said.hasPrefix("Domovoi is still restarting or updating after 10 minutes"), r.said)
    }

    func testAVMRestartFromTheStartIsNotEscalatedAgain() throws {
        launchd.loaded[Labels.vm] = nil
        healthy = { false }
        let r = try restarter().run()
        XCTAssertFalse(r.back)
        XCTAssertFalse(r.escalated, "the VM was the rung already")
        XCTAssertEqual(launchd.bootstrapped, [Labels.vm])
        XCTAssertEqual(vmKills, 0)
        XCTAssertTrue(r.said.contains("What was done: restarted its virtual machine; now"), r.said)
    }

    func testTheMenusWords() {
        XCTAssertEqual(RestartResult.alertText(RestartPlan.underWay), "Domovoi didn't restart: \(RestartPlan.underWay).")
        XCTAssertEqual(RestartResult.noticeBody("Domovoi is back: its services were restarted.\n"), "Its services were restarted.")
        XCTAssertEqual(RestartResult.noticeBody(""), "It answers again.")
        XCTAssertEqual(RestartHealth(coreAlive: true).line, "the core runs but the dashboard doesn't answer")
        XCTAssertEqual(RestartHealth().line, "neither the dashboard nor the core answers")
        XCTAssertEqual(try RestartPlan.escalate(f { _ in }), [.kickstart(Labels.vm, kill: true), .waitVM])
        XCTAssertEqual(try RestartPlan.escalate(f { $0.vm.loaded = false; $0.vm.running = false }), [.bootstrap(Labels.vm), .waitVM])
        XCTAssertThrowsError(try RestartPlan.escalate(f { $0.updateUnitState = "activating" })) { XCTAssertEqual("\($0)", RestartPlan.underWay) }
        XCTAssertThrowsError(try RestartPlan.escalate(f { $0.vm.disabled = true }))
    }

    func testARestartUnderWayIsRefused() throws {
        unit = "LoadState=loaded\nActiveState=activating\n"
        XCTAssertThrowsError(try restarter().run()) { XCTAssertEqual("\($0)", RestartPlan.underWay) }
        XCTAssertFalse(guest.contains { $0.contains("--no-block") }, "nothing was started")
    }

    func testWithoutTheUpdateUnitCoreAndWebRestart() throws {
        unit = "LoadState=not-found\nActiveState=inactive\n"
        try restarter().run()
        XCTAssertTrue(guest.contains(["sudo", "systemctl", "--no-block", "restart", "domovoi-core.service", "domovoi-web.service"]))
    }

    func testASwitchedOffServiceIsNeverSwitchedOnByUs() throws {
        disabled = "disabled services = {\n\t\"com.domovoi.mac.vm\" => disabled\n}\n"
        launchd.loaded[Labels.vm] = nil
        XCTAssertThrowsError(try restarter().run()) { XCTAssertTrue("\($0)".contains("Login Items")) }
        XCTAssertFalse(runner.calls.contains { $0.arguments.first == "bootstrap" || $0.arguments.first == "enable" })
    }

    func testAnUnloadedVMIsLoadedAgainAndWaitedFor() throws {
        launchd.loaded[Labels.vm] = nil
        try restarter().run()
        XCTAssertEqual(launchd.bootstrapped, [Labels.vm])
        XCTAssertTrue(runner.calls.contains { $0.arguments == ["bootstrap", "system", layout.plist(Labels.vm).path] })
        XCTAssertFalse(guest.contains { $0.contains("--no-block") }, "the Linux side starts its own services as it boots")
    }

    func testASilentOllamaIsRestarted() throws {
        http.answers = [:]
        try restarter().run()
        XCTAssertTrue(runner.calls.contains { $0.arguments == ["kickstart", "-k", "system/\(Labels.ollama)"] })
    }

    func testOneAtATimeWithSetup() throws {
        let held = try BusyLock(layout.busyLock)
        XCTAssertThrowsError(try restarter().run()) { XCTAssertTrue("\($0)".contains("wait for it to finish")) }
        _ = held
    }
}
