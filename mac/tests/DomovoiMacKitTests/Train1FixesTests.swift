import XCTest
@testable import DomovoiMacKit

// The fixes after Train 1, sitting 1 (mac-installer TRAIN1-FINDINGS.md,
// "Fixes after sitting 1"), each against its finding's number.

final class Train1WordsTests: XCTestCase {
    // 5 and 19: one bullet, counted, with Ollama under its maker's name.
    func testTheServicesAreCountedInOneBullet() {
        let all = ConsentText.services(ownOllama: true, speech: true)
        XCTAssertTrue(all.hasPrefix("Adds four background services, com.domovoi.mac.vm, com.domovoi.mac.ollama, com.domovoi.mac.helper and com.domovoi.mac.stt,"), all)
        XCTAssertTrue(all.contains("\"limactl\", \"DomovoiHelper\" and \"whisper-server\""), all)
        XCTAssertTrue(all.contains("Ollama is listed only as \"Infra Technologies, Inc\""), all)
        XCTAssertTrue(all.contains("switching off Infra Technologies, Inc turns off AI answers"), all)
        XCTAssertFalse(all.contains("one more"))
        XCTAssertTrue(ConsentText.services(ownOllama: true, speech: false).hasPrefix("Adds three background services"))
        XCTAssertTrue(ConsentText.services(ownOllama: false, speech: true).hasPrefix("Adds three background services, com.domovoi.mac.vm, com.domovoi.mac.helper and com.domovoi.mac.stt"))
        let two = ConsentText.services(ownOllama: false, speech: false)
        XCTAssertTrue(two.hasPrefix("Adds two background services, com.domovoi.mac.vm and com.domovoi.mac.helper,"), two)
        XCTAssertFalse(two.contains("Infra Technologies"))
        XCTAssertTrue(two.contains("The Ollama already on this Mac answers"))
    }

    // 6: every step the helper runs has its row, in its order.
    func testEveryHelperStepHasARow() {
        let rows = InstallSteps.rows(speech: true)
        XCTAssertEqual(rows.map(\.id), Installer.steps + ["setup-code"])
        for r in rows { XCTAssertNotEqual(r.label, r.id, "\(r.id) has words") }
        XCTAssertTrue(rows.contains { $0.id == "stt" } && rows.contains { $0.id == "lan" })
        XCTAssertFalse(InstallSteps.rows(speech: false).contains { $0.id == "stt" })
    }

    // 6: the status line inside the VM in words.
    func testTheProvisionLineIsWords() throws {
        let p = try XCTUnwrap(ProvisionProgress.parse(#"@@DOMOVOI {"step":"apt","state":"start","percent":1,"message":"Installing system packages"}"#))
        XCTAssertEqual(p.plainLine, "Installing system packages (1% of the way)")
        let q = try XCTUnwrap(ProvisionProgress.parse(#"@@DOMOVOI {"step":"apt","state":"progress"}"#))
        XCTAssertEqual(q.plainLine, "Installing Domovoi")
        XCTAssertFalse(p.plainLine.contains("apt"))
    }

    // 15: every administrator prompt says which password.
    func testThePromptsSayWhichPassword() {
        XCTAssertTrue(AdminScript.prompt("X.").hasSuffix("not your Domovoi admin password."))
        XCTAssertTrue(MacStep.prompt.contains("not your Domovoi admin password"))
    }

    // 17: helper.log says the steps in words.
    func testRestartStepsInWords() {
        XCTAssertEqual(RestartStep.startUpdateUnit.words,
                       "start domovoi-update.service (restart Domovoi's services, and load an update the dashboard already downloaded, if any)")
        XCTAssertEqual(RestartStep.kickstart(Labels.vm, kill: true).words, "restart com.domovoi.mac.vm (stop it first)")
        for s in [RestartStep.bootstrap("x"), .kickstart("x", kill: false), .waitVM, .startUpdateUnit, .restartUnits] {
            XCTAssertFalse(s.words.contains("DomovoiMacKit"))
        }
    }

    // 26: the dashboard answers and the core doesn't: a problem after a few
    // minutes, not "starting or updating" for ten.
    func testABrokenCoreIsAProblemSooner() {
        var f = MenuFacts()
        f.install = .installed
        f.vm = .running
        f.ollama = .running
        f.web = .degraded
        f.dbReachable = true
        f.coreAlive = false
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let early = MenuStatus.decide(f, notReadySince: now.addingTimeInterval(-60), now: now)
        XCTAssertEqual(early.state, .starting)
        XCTAssertEqual(early.detail, "If it isn't back in a few minutes, choose Restart Domovoi below.")
        let later = MenuStatus.decide(f, notReadySince: now.addingTimeInterval(-MenuStatus.degradedGrace), now: now)
        XCTAssertEqual(later.state, .problem)
        XCTAssertEqual(later.detail, "The voice service isn't running.")
        // Nothing answering at all (the VM still booting): the long grace.
        f.web = .noAnswer
        XCTAssertEqual(MenuStatus.decide(f, notReadySince: now.addingTimeInterval(-MenuStatus.degradedGrace), now: now).state, .starting)
    }

    // 19, 21: Ollama is named as Login Items shows it.
    func testOllamaIsNamedAsLoginItemsShowsIt() {
        XCTAssertTrue(MenuStatus.ollamaListedAs.hasPrefix("“Infra Technologies, Inc”"))
    }

    // 18: a Mac that never slept is not Domovoi's doing.
    func testKeepAwakeIsOursOnlyForARealTimer() {
        XCTAssertFalse(KeepAwakeItem.ours(nil))
        XCTAssertFalse(KeepAwakeItem.ours(PowerRecord(acSleepBefore: 0, setAt: "x")))
        XCTAssertTrue(KeepAwakeItem.ours(PowerRecord(acSleepBefore: 10, setAt: "x")))
        let item = KeepAwakeItem.decide(acSleepMinutes: 0, ours: KeepAwakeItem.ours(PowerRecord(acSleepBefore: 0, setAt: "x")), laptop: true)
        XCTAssertEqual(item, KeepAwakeItem(checked: true, enabled: false, line: "This Mac's own settings keep it awake."))
    }
}

extension KeepAwakeTests {
    // 18: the household set a timer since: the next "on" is Domovoi's, and
    // "off" puts that timer back.
    func testAnOnAfterTheOwnersTimerIsRecordedAgain() throws {
        pmset.acSleep = 0
        try k.turnOn()
        XCTAssertEqual(k.record?.acSleepBefore, 0)
        pmset.acSleep = 15
        try k.turnOn()
        XCTAssertEqual(pmset.sets, [0])
        XCTAssertEqual(k.record?.acSleepBefore, 15)
        XCTAssertTrue(KeepAwakeItem.ours(k.record))
        try k.turnOff()
        XCTAssertEqual(pmset.sets, [0, 15])
    }
}

extension RestartTests {
    // 26: a core that never answers after the services' restart: the VM
    // rung comes after `coreDeadWait`, not the whole `healthWait`.
    func testASilentCoreEscalatesSooner() throws {
        healthy = { [unowned self] in self.vmKills > 0 }
        var sleptAtKill: Double?
        runner.on { [unowned self] c in
            if c.arguments == ["kickstart", "-k", "system/\(Labels.vm)"], sleptAtKill == nil { sleptAtKill = self.sleeper.slept }
            return nil
        }
        let rs = restarter()
        let r = try rs.run()
        XCTAssertTrue(r.back && r.escalated, r.said)
        let at = try XCTUnwrap(sleptAtKill)
        XCTAssertLessThan(at, rs.settle + rs.healthWait, "didn't wait out the whole services rung")
        XCTAssertGreaterThanOrEqual(at, rs.coreDeadWait)
    }
}

extension MenuProbeTests {
    // 20, 21: switched off in Login Items, macOS boots the job out: its file
    // is there, launchd doesn't know it, and print-disabled says nothing.
    func testAJobBootedOutIsSwitchedOff() throws {
        try writeRecord()
        let http = FakeHTTP()
        http.answers["http://127.0.0.1:6369/api/health"] = HTTPResponse(status: 200, body: Data(#"{"status":"ok","db_reachable":true,"domovoi_reachable":true,"core_alive":true}"#.utf8))
        let r = FakeRunner()
        r.on { c in
            if c.arguments == ["print-disabled", "system"] { return CommandResult(status: 0, stdout: "disabled services = {\n}\n") }
            if c.executable == MenuProbe.pgrep { return CommandResult(status: c.arguments.last == "limactl" ? 0 : 1) }
            if c.arguments == ["print", "system/\(Labels.ollama)"] {
                return CommandResult(status: 113, stderr: "Could not find service \"com.domovoi.mac.ollama\" in domain for system\n")
            }
            return nil
        }
        let f = MenuProbe(layout: layout, http: http, runner: r).gather()
        XCTAssertEqual(f.vm, .running)
        XCTAssertEqual(f.ollama, .off)
        XCTAssertEqual(MenuStatus.attention(f).map(\.kind), [.ollamaSwitchedOff], "\"AI answers are off\", not \"Restart starts it again\"")

        // The VM's job booted out: grey "switched off", at once.
        let r2 = FakeRunner()
        r2.on { c in
            if c.arguments == ["print-disabled", "system"] { return CommandResult(status: 0, stdout: "disabled services = {\n}\n") }
            if c.executable == MenuProbe.pgrep { return CommandResult(status: 1) }
            if c.arguments.first == "print" { return CommandResult(status: 113) }
            return nil
        }
        let g = MenuProbe(layout: layout, http: FakeHTTP(), runner: r2).gather()
        XCTAssertEqual(g.vm, .off)
        let now = Date()
        XCTAssertEqual(MenuStatus.decide(g, notReadySince: now, now: now).line, "Domovoi is switched off")
    }

    // 20: loaded but not running is still "not running"; and while Setup or
    // a Restart holds the busy lock, an unloaded job isn't "switched off".
    func testLoadedOrBusyIsNotSwitchedOff() throws {
        try writeRecord()
        let r = FakeRunner()
        r.on { c in
            if c.executable == MenuProbe.pgrep { return CommandResult(status: 1) }
            if c.arguments.first == "print" { return CommandResult(status: 0, stdout: "system/com.domovoi.mac.vm = {\n}\n") }
            return CommandResult(status: 0, stdout: "disabled services = {\n}\n")
        }
        XCTAssertEqual(MenuProbe(layout: layout, http: FakeHTTP(), runner: r).gather().vm, .notRunning)

        let r2 = FakeRunner()
        r2.on { c in
            if c.executable == MenuProbe.pgrep { return CommandResult(status: 1) }
            if c.arguments.first == "print" { return CommandResult(status: 113) }
            return CommandResult(status: 0, stdout: "disabled services = {\n}\n")
        }
        try FileManager.default.createDirectory(at: layout.busyLock.deletingLastPathComponent(), withIntermediateDirectories: true)
        let lock = try BusyLock(layout.busyLock)
        XCTAssertEqual(MenuProbe(layout: layout, http: FakeHTTP(), runner: r2).gather().vm, .notRunning, "Setup at work")
        _ = lock
        withExtendedLifetime(lock) {}
    }
}

extension CardPackageTests {
    // 24: Safari unpacks the zip and deletes it: the folder counts too, and
    // the newest of either is picked.
    func testTheNewestZipOrUnpackedFolder() throws {
        let downloads = dir.appendingPathComponent("Downloads")
        try FileManager.default.createDirectory(at: downloads, withIntermediateDirectories: true)
        let zip = downloads.appendingPathComponent("domovoi-satellite-overlay.zip")
        try Data("PK".utf8).write(to: zip)
        try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 1_000)], ofItemAtPath: zip.path)
        XCTAssertEqual(CardZip.newest(in: downloads)?.lastPathComponent, zip.lastPathComponent)
        let folder = downloads.appendingPathComponent("domovoi-satellite-overlay 2")
        try CardFixture.write(folder)
        try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 2_000)], ofItemAtPath: folder.path)
        XCTAssertEqual(CardZip.newest(in: downloads)?.lastPathComponent, folder.lastPathComponent)
        // A folder of the same name that isn't card files is left out.
        let other = downloads.appendingPathComponent("domovoi-satellite-overlay-notes")
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 3_000)], ofItemAtPath: other.path)
        XCTAssertEqual(CardZip.newest(in: downloads)?.lastPathComponent, folder.lastPathComponent)
    }

    // 24: the unpacked folder gets the zip's checks before anything is copied.
    func testAnUnpackedFolderIsCheckedThenCopied() throws {
        let folder = dir.appendingPathComponent("domovoi-satellite-overlay")
        try CardFixture.write(folder)
        try Data("finder".utf8).write(to: folder.appendingPathComponent(".DS_Store"))
        let into = dir.appendingPathComponent("files")
        let r = FakeRunner()
        r.on { c in
            guard c.executable == CardZip.ditto else { return nil }
            // The real copy, so the copy can be loaded.
            let p = Process()
            p.executableURL = URL(fileURLWithPath: CardZip.ditto)
            p.arguments = c.arguments
            try? p.run(); p.waitUntilExit()
            return CommandResult(status: p.terminationStatus)
        }
        try CardZip.copyFolder(folder, into: into, runner: r)
        let d = try XCTUnwrap(r.ran("ditto").first)
        XCTAssertEqual(d.arguments, ["--norsrc", "--noextattr", "--noqtn", "--noacl", folder.path, into.path])
        XCTAssertFalse(FileManager.default.fileExists(atPath: into.appendingPathComponent(".DS_Store").path))
        XCTAssertNoThrow(try load(into))

        // A bad name, a link, a missing required file: nothing is copied.
        let bad = dir.appendingPathComponent("bad")
        try CardFixture.write(bad)
        try Data("x".utf8).write(to: bad.appendingPathComponent("evil.sh"))
        let r2 = FakeRunner()
        XCTAssertThrowsError(try CardZip.copyFolder(bad, into: dir.appendingPathComponent("f2"), runner: r2))
        XCTAssertTrue(r2.ran("ditto").isEmpty)
        let short = dir.appendingPathComponent("short")
        try CardFixture.write(short)
        try FileManager.default.removeItem(at: short.appendingPathComponent("domovoi/payload.tar.gz"))
        XCTAssertThrowsError(try CardZip.copyFolder(short, into: dir.appendingPathComponent("f3"), runner: r2)) {
            XCTAssertTrue("\($0)".contains("payload.tar.gz"), "\($0)")
        }
        let linked = dir.appendingPathComponent("linked")
        try CardFixture.write(linked)
        let fw = linked.appendingPathComponent("domovoi/firstrun.sh")
        try FileManager.default.removeItem(at: fw)
        try FileManager.default.createSymbolicLink(at: fw, withDestinationURL: bad.appendingPathComponent("evil.sh"))
        XCTAssertThrowsError(try CardZip.copyFolder(linked, into: dir.appendingPathComponent("f4"), runner: r2))
        XCTAssertTrue(r2.ran("ditto").isEmpty)
    }
}

extension RestartTests {
    /// Security review round 2: a refusal in the checks is RestartRefused
    /// (nothing touched); a step that throws mid-way is not (it acted).
    func testARefusalIsRefusedAndAStepFailureIsNot() throws {
        disabled = "disabled services = {\n\t\"com.domovoi.mac.vm\" => disabled\n}\n"
        XCTAssertThrowsError(try restarter().run()) { XCTAssertTrue($0 is RestartRefused, "\($0)") }
        disabled = "disabled services = {\n}\n"
        // The VM is running but can't be reached: the plan kills and starts
        // it again, and that kickstart fails after the stop.
        runner.on { c in
            if c.executable == Lima.sudo, c.arguments.contains("shell") { return CommandResult(status: 1) }
            if c.arguments.first == "kickstart" { return CommandResult(status: 3, stderr: "kickstart failed") }
            return nil
        }
        XCTAssertThrowsError(try restarter().run()) { e in
            XCTAssertFalse(e is RestartRefused, "a step failed after acting: \(e)")
        }
    }
}
