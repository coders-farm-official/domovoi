import XCTest
@testable import DomovoiMacKit

// The fixes after Train 1, sitting 2 (mac-installer TRAIN1-FINDINGS.md,
// "Sitting 2"), each against its finding's number.

/// S2-19: Setup's window vanished at the end of both uninstalls. Setup
/// opened from Applications while the menu-bar icon runs is the icon's own
/// process, and the uninstall ended every signed-in account's icon
/// (`launchctl bootout gui/UID/...menubar`), that process included.
final class UninstallFinalPageTests: XCTestCase {
    func testTheIconNeverQuitsUnderSetupsWindowOrWhileSetupWorks() {
        // The uninstall deleted the app: "uninstalled", but the final page
        // is up, or the uninstall still runs.
        XCTAssertFalse(MenuBarQuit.shouldQuit(setupWindowOpen: true, setupBusy: false, install: .dataKept, bundleExists: false, agentFileExists: false),
                       "the final page stays until the person closes it")
        XCTAssertFalse(MenuBarQuit.shouldQuit(setupWindowOpen: true, setupBusy: false, install: .notSetUp, bundleExists: false, agentFileExists: false),
                       "Remove everything's final page too")
        XCTAssertFalse(MenuBarQuit.shouldQuit(setupWindowOpen: false, setupBusy: true, install: .dataKept, bundleExists: false, agentFileExists: false),
                       "the uninstall is still running (its window closed meanwhile)")
        // The window closed: the icon quits.
        XCTAssertTrue(MenuBarQuit.shouldQuit(setupWindowOpen: false, setupBusy: false, install: .dataKept, bundleExists: false, agentFileExists: false))
        XCTAssertTrue(MenuBarQuit.shouldQuit(setupWindowOpen: false, setupBusy: false, install: .notSetUp, bundleExists: false, agentFileExists: false))
    }

    func testWhenTheIconCountsDomovoiAsUninstalled() {
        XCTAssertTrue(MenuBarQuit.uninstalled(install: .installed, bundleExists: false, agentFileExists: true), "the app itself is gone")
        XCTAssertFalse(MenuBarQuit.uninstalled(install: .installed, bundleExists: true, agentFileExists: true))
        XCTAssertFalse(MenuBarQuit.uninstalled(install: .unreadable, bundleExists: true, agentFileExists: false), "an unreadable record counts as installed")
        XCTAssertTrue(MenuBarQuit.uninstalled(install: .dataKept, bundleExists: true, agentFileExists: false))
        XCTAssertFalse(MenuBarQuit.uninstalled(install: .notSetUp, bundleExists: true, agentFileExists: true),
                       "the package is installed and Setup hasn't run yet: the icon stays")
        XCTAssertFalse(MenuBarQuit.shouldQuit(setupWindowOpen: false, setupBusy: false, install: .installed, bundleExists: true, agentFileExists: true))
    }

    func testTheUninstallArgvNamesSetupsAccount() throws {
        for everything in [false, true] {
            let argv = SetupCommands.uninstall(removeEverything: everything, forUID: 501)
            XCTAssertEqual(Array(argv.suffix(2)), ["--for-uid", "501"])
            var a = try Args(["uninstall"] + argv)
            let req = try SetupCommands.parseUninstall(&a)
            XCTAssertEqual(req.setupUID, 501)
            XCTAssertEqual(req.removeEverything, everything)
        }
        var none = try Args(["uninstall"] + SetupCommands.uninstall(removeEverything: false))
        let byHand = try SetupCommands.parseUninstall(&none)
        XCTAssertNil(byHand.setupUID, "by hand with sudo: every icon ends, as before")
        var bad = try Args(["uninstall", "--keep-data", "--for-uid", "-1"])
        XCTAssertThrowsError(try SetupCommands.parseUninstall(&bad))
    }

    func testTheUninstallLeavesSetupsAccountsIconRunning() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let layout = Layout(mode: .system, root: d)
        let runner = FakeRunner()
        runner.on { c in c.executable == "/usr/bin/who" ? CommandResult(status: 0, stdout: "alex     console  Nov  2 09:00\nsam      console  Nov  2 10:00\n") : nil }
        var said: [String] = []
        _ = MacSideCleanup.beforeUninstall(layout: layout, runner: runner, fileOps: LocalFileOps(), log: { said.append($0) },
                                           keepIconOf: 501, uidOf: { ["alex": 501, "sam": 502][$0] })
        let boots = runner.calls.filter { $0.executable == MenuBarAgent.launchctl && $0.arguments.first == "bootout" }.map { $0.arguments[1] }
        XCTAssertEqual(boots, ["gui/502/\(Labels.menuBar)"], "only the other account's icon is ended")
        XCTAssertTrue(said.contains { $0.contains("alex's menu-bar icon running") }, "\(said)")
    }

    /// The job the uninstall left loaded in Setup's account: a reinstall in
    /// the same session starts the icon again.
    func testSetupStartsTheIconAgainAfterItsOwnUninstall() {
        let l = Layout.system
        func args(_ s: LaunchdJobState?, ownPid: Int32 = 999) -> [[String]] {
            MenuBarAgent.startCommands(uid: 501, layout: l, loaded: s, ownPid: ownPid).map(\.arguments)
        }
        let boot = ["bootstrap", "gui/501", l.menuBarAgent.path]
        XCTAssertEqual(args(nil), [boot], "not loaded: loaded")
        XCTAssertEqual(args(LaunchdJobState(state: "not running", pid: nil, lastExit: "0")),
                       [["bootout", "gui/501/\(Labels.menuBar)"], boot], "left loaded after it quit: loaded afresh")
        XCTAssertEqual(args(LaunchdJobState(state: "running", pid: 812, lastExit: nil)), [], "running: left alone")
        XCTAssertEqual(args(LaunchdJobState(state: "not running", pid: 999, lastExit: nil), ownPid: 999), [], "never this very process")
        XCTAssertEqual(args(LaunchdJobState(state: nil, pid: nil, lastExit: nil)), [], "an answer it can't read: left alone")
    }

    /// The app sends what the tests above parse (not a copy of it).
    func testTheAppSendsItsOwnAccount() throws {
        let src = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/SetupModel.swift"), encoding: .utf8)
        XCTAssertTrue(src.contains("SetupCommands.uninstall(removeEverything: removeEverything, forUID: getuid())"))
        let menu = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/MenuBar.swift"), encoding: .utf8)
        XCTAssertTrue(menu.contains("MenuBarQuit.shouldQuit(setupWindowOpen: app?.setupWindow != nil, setupBusy: app?.setupModel.busy == true"))
        XCTAssertFalse(menu.contains("static func uninstalled("), "the one quit rule is MenuBarQuit's")
    }
}
