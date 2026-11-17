import XCTest
@testable import DomovoiMacKit

/// The app's command lines, through the parser the helper runs (P3-5: the
/// app's Try Again sent `--repair` and the helper said "--repair needs a
/// value"; nothing had put the two side by side).
final class SetupCommandsTests: XCTestCase {
    var dir: URL!
    var layout: Layout!
    var share: URL!

    override func setUpWithError() throws {
        dir = try tempDir()
        layout = Layout(mode: .system, root: dir.appendingPathComponent("root"))
        share = dir.appendingPathComponent("share")
        try FileManager.default.createDirectory(at: share, withIntermediateDirectories: true)
        try #"{"schema":1,"version":"0.2.0-dev","commit":"\#(String(repeating: "b", count: 40))","bundle":"origin.bundle","ref":"main","track_branch":"main"}"#
            .write(to: share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    /// What the helper's `install` does with argv, up to the work: the
    /// layout, the options, the hand-off options, then "every option used".
    func parseInstall(_ argv: [String]) throws -> InstallOptions {
        var a = try Args(argv)
        XCTAssertEqual(a.command, "install")
        XCTAssertEqual(try Machine.layout(&a, defaultSystem: false), .system)
        let o = try SetupCommands.systemOptions(&a, layout: layout, share: share)
        _ = a.value("code-to")
        _ = try a.int("for-uid")
        try a.checkAllUsed()
        return o
    }

    func testTheFirstInstallsArgvParses() throws {
        let answers = dir.appendingPathComponent("answers.json")
        try JSONEncoder().encode(SetupAnswers(internet: "sometimes", mediaRoot: "/Users/Shared/Domovoi", ollama: "own")).write(to: answers)
        let argv = SetupCommands.install(forUID: getuid(), codeTo: dir.appendingPathComponent("code").path, answersFile: answers.path)
        let o = try parseInstall(argv)
        XCTAssertEqual(o.internet, "sometimes")
        XCTAssertEqual(o.ports, Ports.real)
        XCTAssertEqual(o.role, Names.roleAccount)
        XCTAssertEqual(o.mediaOwner, String(cString: getpwuid(getuid())!.pointee.pw_name))
    }

    func testTryAgainsArgvParsesAndUsesTheSavedAnswers() throws {
        // The saved answers, as a first install left them.
        let media = ["Music", "Videos"].map { MediaMount(name: $0, hostPath: "/Users/Shared/Domovoi/\($0)") }
        let ans = InstallRecord.Answers(internet: "always", tier: .floor, ollama: "own", mediaRoot: "/Users/Shared/Domovoi", media: media,
                                        ports: Ports.real, size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: nil)
        try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
        try RecordStore(url: layout.installRecord, record: InstallRecord(mode: .system, root: "/", answers: ans, launchdDomain: "system"),
                        fileOps: LocalFileOps()).save()
        let argv = SetupCommands.install(forUID: getuid(), codeTo: dir.appendingPathComponent("code").path, answersFile: nil)
        XCTAssertTrue(argv.contains("--repair"))
        let o = try parseInstall(argv)
        XCTAssertEqual(o.internet, "always")
        XCTAssertEqual(o.mediaNames, ["Music", "Videos"])
        // And a repair with no install to repair says so.
        try FileManager.default.removeItem(at: layout.installRecord)
        XCTAssertThrowsError(try parseInstall(argv)) { XCTAssertTrue("\($0)".contains("isn't installed"), "\($0)") }
    }

    func testEveryFlagTheAppSendsIsAFlag() {
        // A bare --name the app sends must be one the parser knows takes no value.
        for argv in [SetupCommands.install(forUID: 501, codeTo: "/c", answersFile: nil), SetupCommands.install(forUID: 501, codeTo: "/c", answersFile: "/a"),
                     SetupCommands.setupCode(forUID: 501, codeTo: "/c"), SetupCommands.uninstall(removeEverything: true), SetupCommands.uninstall(removeEverything: false),
                     SetupCommands.uninstall(removeEverything: true, forUID: 501), SetupCommands.uninstall(removeEverything: false, forUID: 501),
                     SetupCommands.restart(), SetupCommands.saveLogs(forUID: 501, toDir: "/d"), SetupCommands.keepAwake(true), SetupCommands.keepAwake(false),
                     SetupCommands.startAfterPowerFailure(true), SetupCommands.startAfterPowerFailure(false)] {
            for (i, x) in argv.enumerated() where x.hasPrefix("--") {
                let next = i + 1 < argv.count ? argv[i + 1] : nil
                if next == nil || next!.hasPrefix("--") {
                    XCTAssertTrue(Args.flagNames.contains(String(x.dropFirst(2))), "\(x) in \(argv) is sent without a value but isn't a flag")
                }
            }
        }
    }

    func testSetupCodeAndUninstallArgvParse() throws {
        var a = try Args(SetupCommands.setupCode(forUID: 501, codeTo: "/tmp/c"))
        XCTAssertEqual(a.command, "setup-code")
        XCTAssertEqual(try Machine.layout(&a, defaultSystem: true), .system)
        XCTAssertEqual(a.value("code-to"), "/tmp/c")
        XCTAssertEqual(try a.int("for-uid"), 501)
        _ = a.value("share")
        XCTAssertNoThrow(try a.checkAllUsed())

        for everything in [true, false] {
            // The parser both uninstall programs run (not a copy of it: the
            // copy that stood here passed while the real one refused
            // --keep-data, P3-7). domovoi-uninstall prepends "uninstall".
            var u = try Args(["uninstall"] + SetupCommands.uninstall(removeEverything: everything))
            let req = try SetupCommands.parseUninstall(&u)
            XCTAssertEqual(req.layout, .system)
            XCTAssertEqual(req.removeEverything, everything)
            XCTAssertEqual(req.confirm, everything ? "DELETE" : nil)
        }
        var both = try Args(["uninstall", "--keep-data", "--remove-everything"])
        XCTAssertThrowsError(try SetupCommands.parseUninstall(&both))
    }

    /// The menu-bar icon's three administrator actions, through the parsers
    /// the helper runs.
    func testTheMenusArgvParse() throws {
        var r = try Args(SetupCommands.restart())
        XCTAssertEqual(r.command, "restart")
        XCTAssertFalse(try SetupCommands.parseRestart(&r))

        var s = try Args(SetupCommands.saveLogs(forUID: 501, toDir: "/private/var/folders/x/T/domovoi-menu-1/logs"))
        XCTAssertEqual(s.command, "save-logs")
        let req = try SetupCommands.parseSaveLogs(&s)
        XCTAssertEqual(req.uid, 501)
        XCTAssertEqual(req.dir, "/private/var/folders/x/T/domovoi-menu-1/logs")

        for on in [true, false] {
            var p = try Args(SetupCommands.keepAwake(on))
            XCTAssertEqual(p.command, "power")
            XCTAssertEqual(try SetupCommands.parsePower(&p), SetupCommands.PowerRequest(setting: .keepAwake, on: on, parseOnly: false))
            var f = try Args(SetupCommands.startAfterPowerFailure(on))
            XCTAssertEqual(f.command, "power")
            XCTAssertEqual(try SetupCommands.parsePower(&f), SetupCommands.PowerRequest(setting: .startAfterPowerFailure, on: on, parseOnly: false))
        }
        var bad = try Args(["power", "--keep-awake", "maybe"])
        XCTAssertThrowsError(try SetupCommands.parsePower(&bad))
        var both = try Args(["power", "--keep-awake", "on", "--start-after-power-failure", "on"])
        XCTAssertThrowsError(try SetupCommands.parsePower(&both), "one setting per call")
        var neither = try Args(["power", "--system"])
        XCTAssertThrowsError(try SetupCommands.parsePower(&neither))
        var rel = try Args(["save-logs", "--for-uid", "501", "--to-dir", "logs"])
        XCTAssertThrowsError(try SetupCommands.parseSaveLogs(&rel), "an absolute folder only")
        var user = try Args(["restart", "--user-mode", "--prefix", "/tmp/x"])
        XCTAssertThrowsError(try SetupCommands.parseRestart(&user), "the system install only")
    }
}

/// Every app button's command line, through the binary that button calls
/// (P3-7: Keep my data sent `--keep-data` to domovoi-uninstall, whose parser
/// refused it; the earlier test had gone through the helper's parsing only).
/// `--parse-only` reads the whole command line and stops.
final class AppToBinaryTests: XCTestCase {
    /// .build/<config>/, where SwiftPM put the test bundle and the programs.
    var productsDir: URL {
        Bundle(for: AppToBinaryTests.self).bundleURL.deletingLastPathComponent()
    }

    func run(_ program: String, _ args: [String]) throws -> (status: Int32, out: String, err: String) {
        let exe = productsDir.appendingPathComponent(program)
        XCTAssertTrue(FileManager.default.isExecutableFile(atPath: exe.path), "\(exe.path) is not built")
        let p = Process()
        p.executableURL = exe
        p.arguments = args
        let o = Pipe(), e = Pipe()
        p.standardOutput = o
        p.standardError = e
        try p.run()
        let od = o.fileHandleForReading.readDataToEndOfFile(), ed = e.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: od, as: UTF8.self), String(decoding: ed, as: UTF8.self))
    }

    /// The parse errors this class of bug produces.
    func assertNoParseError(_ r: (status: Int32, out: String, err: String), _ what: String, file: StaticString = #filePath, line: UInt = #line) {
        for bad in ["unknown option", "needs a value", "unexpected argument", "not both", "say which install"] {
            XCTAssertFalse(r.err.contains(bad), "\(what): \(r.err)", file: file, line: line)
        }
    }

    func testUninstallButtonsAgainstDomovoiUninstall() throws {
        for everything in [false, true] {
            let r = try run("domovoi-uninstall", SetupCommands.uninstall(removeEverything: everything, forUID: getuid()) + ["--parse-only"])
            assertNoParseError(r, "uninstall everything=\(everything)")
            XCTAssertEqual(r.status, 0, r.err)
            XCTAssertTrue(r.out.contains("\(SetupCommands.parseOnlyLine) uninstall system remove-everything=\(everything)"), r.out)
        }
    }

    func testInstallRepairAndSetupCodeButtonsAgainstTheHelper() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        try #"{"schema":1,"version":"0.2.0-dev","commit":"\#(String(repeating: "c", count: 40))","bundle":"origin.bundle","ref":"main","track_branch":"main"}"#
            .write(to: d.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        let answers = d.appendingPathComponent("answers.json")
        try JSONEncoder().encode(SetupAnswers(internet: "never", mediaRoot: "/Users/Shared/Domovoi", ollama: "own")).write(to: answers)
        let code = d.appendingPathComponent("code").path
        // Only --share is added to the app's argv: the package's share/
        // folder isn't beside a build's program.
        let first = try run("DomovoiHelper", SetupCommands.install(forUID: getuid(), codeTo: code, answersFile: answers.path) + ["--parse-only", "--share", d.path])
        assertNoParseError(first, "install")
        XCTAssertEqual(first.status, 0, first.err)
        XCTAssertTrue(first.out.contains("\(SetupCommands.parseOnlyLine) install system internet=never"), first.out)

        // Repair reads the saved answers from the real /Library record: on a
        // Mac without Domovoi that is "isn't installed", never a parse error.
        let repair = try run("DomovoiHelper", SetupCommands.install(forUID: getuid(), codeTo: code, answersFile: nil) + ["--parse-only", "--share", d.path])
        assertNoParseError(repair, "repair")
        XCTAssertTrue(repair.status == 0 || repair.err.contains("isn't installed"), repair.err)

        let sc = try run("DomovoiHelper", SetupCommands.setupCode(forUID: getuid(), codeTo: code) + ["--parse-only"])
        assertNoParseError(sc, "setup-code")
        XCTAssertEqual(sc.status, 0, sc.err)

        for everything in [false, true] {
            let r = try run("DomovoiHelper", ["uninstall"] + SetupCommands.uninstall(removeEverything: everything, forUID: getuid()) + ["--parse-only"])
            assertNoParseError(r, "helper uninstall everything=\(everything)")
            XCTAssertEqual(r.status, 0, r.err)
        }
    }

    func testTheMenusButtonsAgainstTheHelper() throws {
        let cases: [([String], String)] = [
            (SetupCommands.restart(), "restart"),
            (SetupCommands.saveLogs(forUID: getuid(), toDir: "/private/tmp/x"), "save-logs"),
            (SetupCommands.keepAwake(true), "power keep-awake=true"),
            (SetupCommands.keepAwake(false), "power keep-awake=false"),
            (SetupCommands.startAfterPowerFailure(true), "power start-after-power-failure=true"),
            (SetupCommands.startAfterPowerFailure(false), "power start-after-power-failure=false"),
        ]
        for (argv, want) in cases {
            let r = try run("DomovoiHelper", argv + ["--parse-only"])
            assertNoParseError(r, want)
            XCTAssertEqual(r.status, 0, r.err)
            XCTAssertTrue(r.out.contains("\(SetupCommands.parseOnlyLine) \(want)"), r.out)
        }
    }

    func testTheAppCallsOnlyThese() throws {
        // Every runAsAdmin in the app passes a SetupCommands argv: a button
        // that built its own would escape the tests above.
        let src = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/SetupModel.swift"), encoding: .utf8)
        let calls = src.components(separatedBy: "try runAsAdmin(").dropFirst()
        XCTAssertEqual(calls.count, 3, "install/repair, setup code, uninstall")
        for c in calls {
            let head = String(c.prefix(120))
            XCTAssertTrue(head.hasPrefix("helper, args") || head.hasPrefix("helper, SetupCommands.") || head.hasPrefix("uninstaller, args"), head)
        }
        XCTAssertTrue(src.contains("let args = SetupCommands.install("))
        XCTAssertTrue(src.contains("let args = SetupCommands.uninstall("))

        // The menu-bar icon: every administrator call is the helper with a
        // SetupCommands argv, and nothing else in it asks for an administrator.
        let menu = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/MenuBar.swift"), encoding: .utf8)
        let menuCalls = menu.components(separatedBy: "try runAsAdmin(").dropFirst()
        XCTAssertEqual(menuCalls.count, 4, "restart, keep awake, start after a power failure, save logs")
        for c in menuCalls { XCTAssertTrue(c.hasPrefix("helper, SetupCommands."), String(c.prefix(80))) }
        XCTAssertEqual(menu.components(separatedBy: "administrator privileges").count, 1, "only AdminScript builds the prompt")
        // The card helper's window asks for no administrator at all (owner,
        // 2026-11-03): a standard account prepares a card by itself.
        let card = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/CardWindow.swift"), encoding: .utf8)
        for word in ["runAsAdmin(", "osascript", "AdminScript", "administrator privileges", "menu.helper"] {
            XCTAssertFalse(card.contains(word), "CardWindow.swift: \(word)")
        }
        // And the helper has no card command left to call.
        let helperMain = try String(contentsOf: macDir.appendingPathComponent("Sources/DomovoiHelper/main.swift"), encoding: .utf8)
        XCTAssertFalse(helperMain.contains("card-write"))
        let main = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/AppMain.swift"), encoding: .utf8)
        XCTAssertFalse(main.contains("runAsAdmin") || main.contains("osascript"))
    }

    func testTheAdminScriptQuotesEveryWordAndRefusesAQuote() throws {
        let sh = try AdminScript.shell("/L/bin/DomovoiHelper", ["restart", "--system"], out: "/t/out", err: "/t/err", background: false)
        XCTAssertEqual(sh, "'/L/bin/DomovoiHelper' 'restart' '--system' >'/t/out' 2>'/t/err' </dev/null")
        XCTAssertTrue(try AdminScript.shell("/p", [], out: "/o", err: "/e", background: true).hasSuffix(" &"))
        XCTAssertThrowsError(try AdminScript.shell("/p", ["it's"], out: "/o", err: "/e", background: false))
        // A double quote and a backslash in the shell line are escaped for AppleScript.
        XCTAssertEqual(AdminScript.appleScript(shell: #"'a' "b" \c"#, prompt: "P"),
                       #"do shell script "'a' \"b\" \\c" with prompt "P" with administrator privileges"#)
        XCTAssertEqual(AdminScript.reason("noise\nDomovoiHelper: a restart or update is already under way\n"), "a restart or update is already under way")
        XCTAssertTrue(AdminScript.cancelled("execution error: User canceled. (-128)"))
    }
}
