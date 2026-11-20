import Darwin
import XCTest
@testable import DomovoiMacKit

/// The system install's parts that need no root to test: the role account's
/// dscl calls, limactl as that account, the preflight's rules, the setup
/// code's hand-off, the package's build record, the package files.
final class SystemPartsTests: XCTestCase {
    func testLimaRunsAsTheRoleAccountThroughSudoWithItsEnvironment() {
        let l = Lima(limactl: "/L/limactl", limaHome: "/A/lima", home: "/A/home", runner: FakeRunner(), runAs: "_domovoi")
        let c = l.command(["list"], timeout: 9)
        XCTAssertEqual(c.executable, "/usr/bin/sudo")
        XCTAssertEqual(c.arguments, ["-n", "-u", "_domovoi", "/usr/bin/env", "LIMA_HOME=/A/lima", "HOME=/A/home",
                                     "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "/L/limactl", "list"])
        XCTAssertEqual(c.currentDirectory, "/")
        XCTAssertEqual(c.environment, [:], "sudo would drop it anyway")
        let u = Lima(limactl: "/L/limactl", limaHome: "/A/lima", home: "/A/home", runner: FakeRunner())
        XCTAssertEqual(u.command(["list"]).executable, "/L/limactl", "the user mode runs it directly")
    }

    func testRoleAccountPicksAFreeNumberAndOnlyDeletesItsOwn() throws {
        let r = FakeRunner()
        r.on { c in
            guard c.executable == RoleAccount.dscl else { return nil }
            if c.arguments.contains("-read") { return CommandResult(status: 56) }
            if c.arguments == [".", "-list", "/Users", "UniqueID"] { return CommandResult(status: 0, stdout: "root 0\n_a 400\n_b 399\n") }
            if c.arguments == [".", "-list", "/Groups", "PrimaryGroupID"] { return CommandResult(status: 0, stdout: "wheel 0\n_g 398\n") }
            return CommandResult(status: 0)
        }
        let a = RoleAccount(name: "_domovoi", runner: r)
        XCTAssertEqual(try a.freeID(preferred: nil), 397, "free as both a UID and a GID")
        XCTAssertEqual(try a.freeID(preferred: 250), 250, "the kept UID when it is free")
        XCTAssertEqual(try a.freeID(preferred: 399), 397, "not when it is taken")
        XCTAssertEqual(try a.freeID(preferred: 600), 397, "not outside the range")
        XCTAssertThrowsError(try RoleAccount(name: "alex", runner: r).delete())
    }

    func testSetupCodeShapeAndScrub() {
        XCTAssertTrue(SetupCode.isWellFormed("amber-birch-cedar-delta-ember-fjord-grove-heron"))
        XCTAssertFalse(SetupCode.isWellFormed("amber-birch-cedar-delta-ember-fjord-grove"))
        XCTAssertFalse(SetupCode.isWellFormed("Amber-birch-cedar-delta-ember-fjord-grove-heron"))
        XCTAssertFalse(SetupCode.isWellFormed("amber-birch-cedar-delta-ember-fjord-grove-heron\n"))
        XCTAssertEqual(SetupCode.scrub("code: amber-birch-cedar-delta-ember-fjord-grove-heron ok"), "code: [hidden] ok")
        XCTAssertEqual(SetupCode.scrub("vm-start done"), "vm-start done")
    }

    func testHandoffMakesANewPrivateFileAndNeverWritesThroughALink() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let f = d.appendingPathComponent("code")
        try Handoff.write("secret-words", to: f.path, owner: getuid())
        XCTAssertEqual(try String(contentsOf: f, encoding: .utf8), "secret-words\n")
        let mode = (try FileManager.default.attributesOfItem(atPath: f.path)[.posixPermissions] as? NSNumber)?.intValue
        XCTAssertEqual(mode, 0o600)
        XCTAssertThrowsError(try Handoff.write("again", to: f.path, owner: getuid()), "never over an existing file")
        let target = d.appendingPathComponent("target")
        FileManager.default.createFile(atPath: target.path, contents: Data("keep".utf8))
        let link = d.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        XCTAssertThrowsError(try Handoff.write("x", to: link.path, owner: getuid()))
        XCTAssertEqual(try String(contentsOf: target, encoding: .utf8), "keep")
    }

    func testBusyLockIsOneAtATime() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let f = d.appendingPathComponent(".busy")
        var first: BusyLock? = try BusyLock(f)
        XCTAssertNotNil(first)
        // flock is per open file: a second open in this process conflicts too.
        XCTAssertThrowsError(try BusyLock(f))
        first = nil
        XCTAssertNoThrow(try BusyLock(f))
    }

    func testBuildInfoIsChecked() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let sha = String(repeating: "a", count: 40)
        func write(_ j: String) throws { try j.write(to: d.appendingPathComponent("build.json"), atomically: true, encoding: .utf8) }
        try write(#"{"schema":1,"version":"0.2.0-dev","commit":"\#(sha)","bundle":"origin.bundle","ref":"main","track_branch":"main","provision_args":["--x"]}"#)
        let b = try BuildInfo.load(d)
        let p = b.provisionSource(share: d)
        XCTAssertEqual(p.repoBundle, d.appendingPathComponent("origin.bundle"))
        XCTAssertNil(p.repoURL)
        XCTAssertEqual(p.ref, "main")
        XCTAssertEqual(p.trackBranch, "main")
        XCTAssertEqual(p.extraArgs, ["--x"])
        XCTAssertNil(p.allowedSigners, "only when the package carries one")
        try write(#"{"schema":1,"version":"0.2.0","commit":"\#(sha)","repo_url":"https://example.org/d.git"}"#)
        XCTAssertEqual(try BuildInfo.load(d).provisionSource(share: d).ref, sha, "the commit itself by default")
        for bad in [#"{"schema":1,"version":"v","commit":"abc","repo_url":"u"}"#,
                    #"{"schema":1,"version":"v","commit":"\#(sha)"}"#,
                    #"{"schema":1,"version":"v","commit":"\#(sha)","bundle":"../x"}"#,
                    #"{"schema":2,"version":"v","commit":"\#(sha)","repo_url":"u"}"#] {
            try write(bad)
            XCTAssertThrowsError(try BuildInfo.load(d), bad)
        }
    }

    func testPortOwnersGetOnePopUpEachAndTheHouseholdsOllamaIsNamed() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let r = FakeRunner()
        r.on { c in
            guard c.executable == "/usr/sbin/lsof" else { return nil }
            let port = c.arguments.first { $0.hasPrefix("-iTCP:") }!.dropFirst(6)
            switch port {
            case "11434": return CommandResult(status: 0, stdout: "p4242\ncollama\n")
            case "8051", "8052": return CommandResult(status: 0, stdout: "p77\ncnode\n")
            default: return CommandResult(status: 1)
            }
        }
        let http = FakeHTTP()
        http.answers["http://127.0.0.1:11434/api/version"] = HTTPResponse(status: 200, body: Data(#"{"version":"0.40.12"}"#.utf8))
        let p = Preflight(layout: Layout(mode: .system, root: d), runner: r, http: http)
        p.portInUse = { [11434, 8051, 8052, 9467].contains($0) }
        let rep = p.run(pins: nil, ollamaMinimum: OllamaVersion("0.12.7"))
        // In port order; 9467's owner is out of a standard user's sight.
        XCTAssertEqual(rep.conflicts.map(\.owner), ["node", "another program (perhaps another account's)", "Ollama"])
        let ollama = try XCTUnwrap(rep.conflicts.first { $0.owner == "Ollama" })
        XCTAssertEqual(ollama.ports, [11434])
        XCTAssertEqual(ollama.ollamaVersion, "0.40.12")
        XCTAssertTrue(ollama.ollamaUsable)
        XCTAssertEqual(rep.conflicts.first { $0.owner == "node" }?.ports, [8051, 8052])
        XCTAssertFalse(rep.ok)
        XCTAssertEqual(rep.installState, "none")
    }

    func testARepairsOwnPortsAreNotConflicts() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let layout = Layout(mode: .system, root: d)
        let answers = InstallRecord.Answers(internet: "never", tier: .floor, ollama: "own", mediaRoot: "/Users/Shared/Domovoi", media: [],
                                            ports: Ports.real, size: VMSize(cpus: 2, memoryGiB: 6, diskGiB: 40), timezone: nil)
        try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
        try RecordStore(url: layout.installRecord, record: InstallRecord(mode: .system, root: "/", answers: answers, launchdDomain: "system"),
                        fileOps: LocalFileOps()).save()
        let p = Preflight(layout: layout, runner: FakeRunner(), http: FakeHTTP())
        p.portInUse = { [6369, 6370, 11434].contains($0) }
        let rep = p.run(pins: nil, ollamaMinimum: nil)
        XCTAssertEqual(rep.installState, "installed")
        XCTAssertEqual(rep.conflicts, [])
    }

    func testPreflightRules() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let r = FakeRunner()
        r.on { c in
            switch c.executable {
            case "/usr/bin/fdesetup": return CommandResult(status: 0, stdout: "FileVault is On.\n")
            case "/usr/bin/pmset": return CommandResult(status: 0, stdout: "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;\n")
            default: return nil
            }
        }
        let p = Preflight(layout: Layout(mode: .system, root: d), runner: r, http: FakeHTTP())
        p.portInUse = { _ in false }
        p.sysctl = { ["hw.optional.arm64": "1", "machdep.cpu.brand_string": "Apple M1 Pro", "kern.hv_support": "1", "hw.model": "MacBookPro18,1"][$0] }
        p.memoryBytes = 16 << 30
        p.osVersion = OperatingSystemVersion(majorVersion: 26, minorVersion: 4, patchVersion: 1)
        p.freeDiskBytes = { 300_000_000_000 }
        p.lan = { LanAddress(ipv4: "192.168.1.20", interface: "en0") }
        p.isWiFi = { _ in true }
        var rep = p.run(pins: nil, ollamaMinimum: nil)
        XCTAssertTrue(rep.ok, "\(rep.checks.filter { $0.status == "fail" })")
        XCTAssertEqual(rep.fileVault, "on")
        XCTAssertTrue(rep.laptop)
        XCTAssertTrue(rep.battery)
        XCTAssertEqual(rep.onAC, true)
        XCTAssertEqual(rep.tier, "floor")
        XCTAssertEqual(rep.wifi, true)

        p.memoryBytes = 8 << 30
        p.osVersion = OperatingSystemVersion(majorVersion: 15, minorVersion: 6, patchVersion: 0)
        p.freeDiskBytes = { 20_000_000_000 }
        p.lan = { nil }
        p.sysctl = { ["hw.optional.arm64": "0", "kern.hv_support": "0"][$0] }
        rep = p.run(pins: nil, ollamaMinimum: nil)
        XCTAssertFalse(rep.ok)
        XCTAssertEqual(Set(rep.checks.filter { $0.status == "fail" }.map(\.id)), ["chip", "macos", "memory", "virtualization", "disk", "network"])

        XCTAssertEqual(Preflight.portList([6369, 8050, 8051, 8052, 8053, 11434]), "6369, 8050-8053, 11434")
        XCTAssertEqual(Preflight.friendlyName(command: "com.docker.backend", path: nil), "Docker Desktop")
        XCTAssertEqual(Preflight.friendlyName(command: "x", path: "/Applications/OrbStack.app/Contents/MacOS/x"), "OrbStack")
        XCTAssertEqual(Preflight.friendlyName(command: "Plex", path: "/Applications/Plex Media Server.app/Contents/MacOS/Plex"), "Plex Media Server")
    }

    func testThePackageFilesHangTogether() throws {
        let dist = try read("installer/distribution.xml")
        XCTAssertTrue(dist.contains(#"hostArchitectures="arm64""#))
        XCTAssertTrue(dist.contains("DomovoiCore.pkg"))
        XCTAssertTrue(dist.contains(#"<pkg-ref id="\#(Layout.packageID)""#))
        let info = try read("installer/Info.plist").replacingOccurrences(of: "@VERSION@", with: "0.2.0-dev").replacingOccurrences(of: "@PKG_VERSION@", with: "0.2.0")
        let plist = try XCTUnwrap(try PropertyListSerialization.propertyList(from: Data(info.utf8), format: nil) as? [String: Any])
        XCTAssertEqual(plist["CFBundleExecutable"] as? String, "Domovoi", "the app target's name")
        XCTAssertEqual(plist["LSMinimumSystemVersion"] as? String, "26.0")
        // One install script, which never opens the app while Installer
        // runs (P3-1): it starts the job that opens Setup after Installer
        // has quit. The conclusion page stays the fallback.
        let scripts = try FileManager.default.contentsOfDirectory(atPath: macDir.appendingPathComponent("installer/scripts").path)
        XCTAssertEqual(scripts, ["postinstall"])
        let post = try read("installer/scripts/postinstall")
        let commands = post.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.hasPrefix("#") }
        XCTAssertFalse(commands.contains { $0.hasPrefix("open ") || $0.contains("/usr/bin/open") }, "postinstall never opens anything itself")
        XCTAssertTrue(post.contains("bootstrap \"gui/$uid\""), "the job goes to the console user's GUI session")
        XCTAssertTrue(post.contains("COMMAND_LINE_INSTALL"))
        XCTAssertTrue(post.hasSuffix("main \"$@\"\nexit 0\n"), "it always exits 0")
        for script in ["installer/scripts/postinstall", "installer/open-setup.sh"] {
            XCTAssertTrue(FileManager.default.isExecutableFile(atPath: macDir.appendingPathComponent(script).path), script)
        }
        XCTAssertTrue(dist.contains(#"require-scripts="false""#))
        // Train 1 finding 1: the page says Setup opens on Close, with
        // Applications as the fallback.
        let conclusion = try read("installer/resources/conclusion.html")
        XCTAssertTrue(conclusion.contains("Domovoi Setup opens when you close this window"))
        XCTAssertTrue(conclusion.contains("open <b>Domovoi</b> from your Applications"))
        let build = try read("build.sh")
        XCTAssertTrue(build.contains("BundleIsRelocatable false"), "the app never lands in a build folder")
        XCTAssertTrue(build.contains("--identifier com.domovoi.mac "))
        XCTAssertTrue(build.contains(#"--scripts "$W/scripts""#))
        XCTAssertTrue(build.contains(#""$MAC/installer/open-setup.sh" "$S/installer/""#), "the waiter ships in share/installer")
        XCTAssertTrue(build.contains("Scripts/postinstall"), "build.sh checks the script landed in the package")
        XCTAssertTrue(build.contains("work.noindex"), "the staged app never lands where Spotlight indexes")
    }

    /// postinstall and open-setup.sh against stand-ins (no root, no launchd):
    /// a command-line install, nobody at the screen, no Installer running,
    /// the happy path, the waiter's wait, give-up and self-unload.
    func testTheInstallScriptsAgainstStandIns() throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [macDir.appendingPathComponent("tests/installer-scripts.sh").path]
        let o = Pipe()
        p.standardOutput = o
        p.standardError = o
        try p.run()
        let out = String(decoding: o.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        p.waitUntilExit()
        XCTAssertEqual(p.terminationStatus, 0, out)
        XCTAssertTrue(out.contains("installer-scripts: all passed"), out)
        for script in ["installer/scripts/postinstall", "installer/open-setup.sh"] {
            let n = Process()
            n.executableURL = URL(fileURLWithPath: "/bin/bash")
            n.arguments = ["-n", macDir.appendingPathComponent(script).path]
            try n.run()
            n.waitUntilExit()
            XCTAssertEqual(n.terminationStatus, 0, "\(script) parses")
        }
    }
}
