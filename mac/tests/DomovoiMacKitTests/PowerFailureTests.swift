import XCTest
@testable import DomovoiMacKit

/// "Start after a power failure" (pmset autorestart): desktop Macs only,
/// behind FeatureGates.startAfterPowerFailure. The tests turn the gate on
/// for the object under test (`enabled: true`); the build's own value is
/// pinned by testTheGateIsOffInThisBuild.
final class PowerFailureTests: XCTestCase {
    var dir: URL!
    var layout: Layout!
    var runner: FakeRunner!
    var pmset: FakePmset!

    override func setUpWithError() throws {
        dir = try tempDir()
        layout = try makeInstalledLayout(dir)
        runner = FakeRunner()
        pmset = FakePmset()
        pmset.battery = false   // a Mac mini
        pmset.autorestart = 0
        pmset.install(on: runner)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    /// hw.model as an M2-or-later Mac says it, desktop or not: only the battery tells.
    func p(enabled: Bool = true) -> StartAfterPowerFailure {
        StartAfterPowerFailure(layout: layout, runner: runner, fileOps: LocalFileOps(), now: { Date(timeIntervalSince1970: 1_800_000_000) },
                               enabled: enabled, isLaptop: { [unowned self] in Preflight.thisMacIsLaptop(runner: self.runner, sysctl: { _ in "Mac14,3" }) })
    }

    func testTheGateIsOffInThisBuild() {
        // Flip this together with FeatureGates.startAfterPowerFailure, and
        // only once mac-installer/TRAIN-CHECKLIST.md's real-Mac test passed.
        XCTAssertFalse(FeatureGates.startAfterPowerFailure, "unverified on Apple Silicon: no control may show it")
        XCTAssertNil(PowerFailureItem.decide(laptop: false, value: 0, ours: false), "gate off: no menu item, even on a desktop Mac")
        XCTAssertFalse(SetupOffers.powerFailure(gate: FeatureGates.startAfterPowerFailure, report: nil))
    }

    func testGateOffRefusesOnButStillPutsBack() throws {
        XCTAssertThrowsError(try p(enabled: false).turnOn()) { XCTAssertTrue("\($0)".contains("isn't available"), "\($0)") }
        XCTAssertEqual(pmset.autorestartSets, [], "nothing changed")
        XCTAssertNil(p().record, "nothing recorded")
        // Set while a test build had the gate on, then the gate is off again:
        // off (and uninstall) still put the old value back.
        try p().turnOn()
        XCTAssertEqual(pmset.autorestart, 1)
        try p(enabled: false).turnOff()
        XCTAssertEqual(pmset.autorestart, 0)
    }

    func testOnRecordsFirstThenOffPutsItBack() throws {
        let said = try p().turnOn()
        XCTAssertEqual(pmset.autorestartSets, [1])
        XCTAssertEqual(p().record?.before, 0, "the old value, written before the change")
        XCTAssertEqual(said, "this Mac now starts by itself when the power comes back")
        try p().turnOn()
        XCTAssertEqual(pmset.autorestartSets, [1], "already on: nothing changed again")
        XCTAssertEqual(p().record?.before, 0, "a second on never records our own 1 as before")
        XCTAssertTrue(runner.calls.contains { $0.arguments == ["-a", "autorestart", "1"] })
        let off = try p().turnOff()
        XCTAssertEqual(pmset.autorestartSets, [1, 0])
        XCTAssertNil(p().record)
        XCTAssertTrue(off.contains("as before"), off)
        XCTAssertEqual(try p().turnOff(), "Domovoi hadn't changed this Mac's power-failure setting")
    }

    func testTheOwnersOwnSettingStays() throws {
        try p().turnOn()
        pmset.autorestart = 0   // switched off in System Settings since
        let said = try p().turnOff()
        XCTAssertEqual(pmset.autorestartSets, [1], "theirs is left as it is")
        XCTAssertTrue(said.contains("left as it is"), said)

        pmset.autorestart = 1   // the owner had it on before Domovoi
        try p().turnOn()
        XCTAssertEqual(p().record?.before, 1)
        try p().turnOff()
        XCTAssertEqual(pmset.autorestart, 1, "1 before, 1 after: nothing to put back")
    }

    func testALaptopIsNeverGivenIt() throws {
        pmset.battery = true
        XCTAssertThrowsError(try p().turnOn()) { XCTAssertTrue("\($0)".contains("laptop"), "\($0)") }
        XCTAssertEqual(pmset.autorestartSets, [])
        XCTAssertNil(p().record)
        // An old MacBook says so in hw.model, battery or not.
        XCTAssertTrue(Preflight.isLaptop(model: "MacBookPro18,3", pmsetBatt: "Now drawing from 'AC Power'\n"))
        XCTAssertTrue(Preflight.isLaptop(model: "Mac15,12", pmsetBatt: " -InternalBattery-0 (id=1)\t80%;"), "an M3 MacBook Air: the battery tells")
        XCTAssertFalse(Preflight.isLaptop(model: "Mac14,3", pmsetBatt: "Now drawing from 'AC Power'\n"), "a Mac mini")
        // Unreadable pmset: counted as a laptop, so it is never turned on blind.
        let silent = FakeRunner()
        silent.on { _ in CommandResult(status: 1, stderr: "no") }
        XCTAssertTrue(Preflight.thisMacIsLaptop(runner: silent, sysctl: { _ in "Mac14,3" }))
        XCTAssertTrue(Preflight.thisMacIsLaptop(runner: FakeRunner(), sysctl: { _ in "Mac14,3" }), "empty output: can't tell, so a laptop")
    }

    func testAMacWithoutTheSettingIsRefused() throws {
        pmset.autorestart = nil
        XCTAssertThrowsError(try p().turnOn()) { XCTAssertTrue("\($0)".contains("no setting"), "\($0)") }
        XCTAssertNil(p().record)
        pmset.autorestart = 0
        pmset.setFails = true
        XCTAssertThrowsError(try p().turnOn())
    }

    func testTheAnswerIsSavedForARepair() throws {
        try p().saveAnswer(true)
        XCTAssertEqual(try RecordStore.load(layout.installRecord)?.answers.startAfterPowerFailure, true)
        try p().saveAnswer(false)
        XCTAssertEqual(try RecordStore.load(layout.installRecord)?.answers.startAfterPowerFailure, false)
        XCTAssertNil(try RecordStore.load(layout.installRecord)?.answers.keepAwake, "the other answer untouched")
    }

    func testUninstallPutsItBack() throws {
        try p().turnOn()
        XCTAssertEqual(pmset.autorestart, 1)
        let said = MacSideCleanup.beforeUninstall(layout: layout, runner: runner, fileOps: LocalFileOps(), log: { _ in }, uidOf: { _ in nil })
        XCTAssertEqual(pmset.autorestart, 0, "put back as recorded")
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.autoRestartRecord.path), "the state folder is left empty for Remove everything")
        XCTAssertTrue(said.contains { $0.hasPrefix("Start after a power failure is off") }, "\(said)")
    }

    func testTheMenuItem() {
        XCTAssertNil(PowerFailureItem.decide(gate: true, laptop: true, value: 0, ours: false), "never on a laptop")
        XCTAssertNil(PowerFailureItem.decide(gate: true, laptop: false, value: nil, ours: false), "unreadable: no item")
        let off = PowerFailureItem.decide(gate: true, laptop: false, value: 0, ours: false)!
        XCTAssertFalse(off.checked)
        XCTAssertTrue(off.enabled)
        let ours = PowerFailureItem.decide(gate: true, laptop: false, value: 1, ours: true)!
        XCTAssertTrue(ours.checked && ours.enabled)
        let theirs = PowerFailureItem.decide(gate: true, laptop: false, value: 1, ours: false)!
        XCTAssertTrue(theirs.checked)
        XCTAssertFalse(theirs.enabled, "the owner's own setting: shown, not ours to undo")

        let running = MenuVerdict(state: .running, line: "Domovoi is running")
        let k = KeepAwakeItem.decide(acSleepMinutes: 0, ours: true, laptop: false)
        let m = MenuItems.build(verdict: running, attention: [], keepAwake: k, update: nil, powerFailure: off)
        let t = m.map { $0.kind == .separator ? "-" : $0.title }
        XCTAssertEqual(Array(t[5...9]), ["Keep this Mac awake", "This Mac doesn't go to sleep by itself while it's plugged in.",
                                         "Start after a power failure", off.line, "-"])
        XCTAssertFalse(MenuItems.build(verdict: running, attention: [], keepAwake: k, update: nil).contains { $0.action == .startAfterPowerFailure })
        let busy = MenuItems.build(verdict: running, attention: [], keepAwake: nil, update: nil, busy: [.startAfterPowerFailure], powerFailure: off)
        XCTAssertTrue(busy.contains { $0.title == "Changing the power setting..." && !$0.enabled })
    }

    func testTheSetupOffer() {
        func report(laptop: Bool) -> PreflightReport {
            PreflightReport(ok: true, checks: [], conflicts: [], fileVault: "off", laptop: laptop, battery: laptop, onAC: true, memoryGB: 16, tier: "standard",
                            installState: "none", lanIPv4: nil, lanInterface: nil, wifi: nil, freeDiskGB: 100, macOS: "26.0", chip: "Apple M4", asRoot: false)
        }
        XCTAssertTrue(SetupOffers.powerFailure(gate: true, report: report(laptop: false)))
        XCTAssertFalse(SetupOffers.powerFailure(gate: true, report: report(laptop: true)))
        XCTAssertFalse(SetupOffers.powerFailure(gate: true, report: nil))
        XCTAssertFalse(SetupOffers.powerFailure(gate: false, report: report(laptop: false)))
    }

    func testTheInstallStepAndARepairKeepTheAnswer() throws {
        let a = SetupAnswers(internet: "never", mediaRoot: "/Users/Shared/Domovoi", ollama: "own", keepAwake: true, startAfterPowerFailure: true)
        let json = String(decoding: try JSONEncoder().encode(a), as: UTF8.self)
        XCTAssertTrue(json.contains("\"start_after_power_failure\":true"), json)
        XCTAssertEqual(try JSONDecoder().decode(SetupAnswers.self, from: Data(#"{"internet":"never","media_root":"/x","ollama":"own"}"#.utf8)).startAfterPowerFailure,
                       nil, "an answers file from before: left alone")
        // The install record from before this field reads as nil too.
        var rec = try RecordStore.load(layout.installRecord)!
        XCTAssertNil(rec.answers.startAfterPowerFailure)
        rec.answers.startAfterPowerFailure = true
        let back = try JSONDecoder().decode(InstallRecord.self, from: try JSONEncoder().encode(rec))
        XCTAssertEqual(back.answers.startAfterPowerFailure, true)
    }
}
