import XCTest
@testable import DomovoiMacKit

final class LimaTemplateTests: XCTestCase {
    func render(_ mounts: [MediaMount], ports: Ports = Ports(web: 6390, core: 6391, ollama: 11500)) throws -> String {
        try LimaTemplate.render(template: read("vm/domovoi.yaml"), size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40),
                                imagePath: "/Library/Application Support/Domovoi/cache/ubuntu.img", imageSHA256: String(repeating: "e", count: 64),
                                mounts: mounts, ports: ports)
    }

    func testTheRepoTemplateFillsIn() throws {
        let y = try render([MediaMount(name: "Music", hostPath: "/Users/Shared/Domovoi/Music")])
        XCTAssertFalse(y.contains("{{"))
        for line in ["vmType: vz", "arch: aarch64", "cpus: 4", "memory: \"6GiB\"", "disk: \"40GiB\"",
                     "- location: \"/Library/Application Support/Domovoi/cache/ubuntu.img\"",
                     "  digest: \"sha256:\(String(repeating: "e", count: 64))\"",
                     "  system: false", "  user: false", "mountType: virtiofs",
                     "- location: \"/Users/Shared/Domovoi/Music\"", "  mountPoint: \"/srv/media/music\"", "  writable: true",
                     "  loadDotSSHPubKeys: false", "  name: lima", "- vzNAT: true"] {
            XCTAssertTrue(y.contains("\n" + line + "\n"), "missing line: \(line)")
        }
    }

    func testOnlyTheTwoForwardsReachTheMac() throws {
        let y = try render([])
        XCTAssertTrue(y.contains("\nmounts: []\n"), "no home folder mount: Lima's default would mount ~")
        let hostPorts = y.split(separator: "\n").filter { $0.contains("hostPort:") }.map { $0.trimmingCharacters(in: .whitespaces) }
        XCTAssertEqual(Set(hostPorts), ["hostPort: 6390", "hostPort: 6391"])
        XCTAssertEqual(y.components(separatedBy: "hostIP: 127.0.0.1").count - 1, 4, "every forward on the Mac's loopback only")
        XCTAssertFalse(y.contains("hostIP: 0.0.0.0"))
        XCTAssertEqual(y.components(separatedBy: "ignore: true").count - 1, 2)
        // The ignore rules come after the forwards (Lima takes the first match).
        XCTAssertLessThan(y.range(of: "hostPort: 6391")!.lowerBound, y.range(of: "ignore: true")!.lowerBound)
    }

    func testBadMediaIsRefused() {
        XCTAssertThrowsError(try render([MediaMount(name: "../x", hostPath: "/a")]))
        XCTAssertThrowsError(try render([MediaMount(name: "1music", hostPath: "/a")]))
        XCTAssertThrowsError(try render([MediaMount(name: "Music", hostPath: "relative")]))
        XCTAssertThrowsError(try render([MediaMount(name: "Music", hostPath: "/a"), MediaMount(name: "music", hostPath: "/b")]))
    }

    func testAPathWithQuotesStaysOneYAMLString() throws {
        let y = try render([MediaMount(name: "Music", hostPath: "/Users/Shared/Alex \"Mix\": #1")])
        XCTAssertTrue(y.contains("- location: \"/Users/Shared/Alex \\\"Mix\\\": #1\"\n"))
    }

    func testVMSizeAndTierFollowTheMac() {
        XCTAssertEqual(VMSize.forMac(memoryBytes: 17_179_869_184, cores: 10), VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40))
        XCTAssertEqual(VMSize.forMac(memoryBytes: 25_769_803_776, cores: 8).memoryGiB, 8)
        XCTAssertEqual(VMSize.forMac(memoryBytes: 17_179_869_184, cores: 2).cpus, 2)
        XCTAssertEqual(Tier.forMac(memoryBytes: 17_179_869_184), .floor)
        XCTAssertEqual(Tier.forMac(memoryBytes: 25_769_803_776), .standard)
    }

    /// provision.sh's tiers (1 / 2 from 24 GB / 3 from 48 GB), and 4 only
    /// on a Mac of 64 GB or more: Macs as they are sold, in GiB.
    func testOllamaAnswersMoreAtOnceOnBiggerMacs() {
        let gib: UInt64 = 1 << 30
        let want: [(UInt64, Int)] = [(8, 1), (16, 1), (18, 1), (24, 2), (32, 2), (36, 2), (48, 3), (64, 4), (96, 4), (128, 4)]
        for (size, n) in want {
            XCTAssertEqual(OllamaParallel.forMac(memoryBytes: size * gib), n, "\(size) GB")
        }
    }

    func testSocketRoom() throws {
        XCTAssertNoThrow(try Lima.checkSocketRoom(limaHome: Layout.system.limaHome.path, instance: "domovoi"))
        XCTAssertThrowsError(try Lima.checkSocketRoom(limaHome: "/Users/alex/" + String(repeating: "deep/", count: 12) + "lima", instance: "domovoi"))
        // A home-folder prefix fits only because the user mode keeps LIMA_HOME at PREFIX/lima.
        let user = Layout.user(prefix: "/Users/alex/mac-installer-tests/phase2/p")
        XCTAssertEqual(user.limaHome.path, "/Users/alex/mac-installer-tests/phase2/p/lima")
        XCTAssertNoThrow(try Lima.checkSocketRoom(limaHome: user.limaHome.path, instance: "domovoi"))
        XCTAssertThrowsError(try Lima.checkSocketRoom(limaHome: user.appSupport.appendingPathComponent("lima").path, instance: "domovoi"))
    }
}

final class LaunchdTests: XCTestCase {
    let values = ["LABEL": Labels.ollama, "OLLAMA_BIN": "/Library/Application Support/Domovoi/ollama/0.34.4/ollama",
                  "OLLAMA_PORT": "11434", "OLLAMA_MODELS": "/Library/Application Support/Domovoi/ollama-models",
                  "JOB_HOME": "/Library/Application Support/Domovoi/home", "LOG_FILE": "/Library/Logs/Domovoi/ollama.log",
                  "OLLAMA_NUM_PARALLEL": "2"]

    func testOllamaPlistForTheSystemInstall() throws {
        let p = try LaunchdPlist.render(template: read("launchd/com.domovoi.mac.ollama.plist"), values: values, role: Names.roleAccount)
        XCTAssertEqual(p.label, Labels.ollama)
        XCTAssertEqual(p.userName, "_domovoi")
        XCTAssertEqual(p.dictionary["GroupName"] as? String, "_domovoi")
        XCTAssertTrue(p.keepAlive)
        XCTAssertEqual(p.dictionary["ProcessType"] as? String, "Interactive")
        XCTAssertEqual(p.programArguments, [values["OLLAMA_BIN"]!, "serve"])
        XCTAssertEqual(p.environment["OLLAMA_HOST"], "127.0.0.1:11434", "loopback only")
        XCTAssertEqual(p.environment["OLLAMA_MODELS"], values["OLLAMA_MODELS"])
        XCTAssertEqual(p.environment["OLLAMA_NUM_PARALLEL"], "2")
        XCTAssertEqual(p.logFiles, ["/Library/Logs/Domovoi/ollama.log"])
    }

    func testUserModeHasNoRoleAccount() throws {
        let p = try LaunchdPlist.render(template: read("launchd/com.domovoi.mac.ollama.plist"), values: values, role: nil)
        XCTAssertNil(p.userName)
        XCTAssertNil(p.dictionary["GroupName"])
    }

    func testVMPlist() throws {
        let p = try LaunchdPlist.render(template: read("launchd/com.domovoi.mac.vm.plist"), values: [
            "LABEL": Labels.vm, "LIMACTL": "/x/bin/lima/bin/limactl", "INSTANCE": "domovoi", "LIMA_HOME": "/x/lima & co",
            "JOB_HOME": "/x/home", "LOG_FILE": "/x/vm.log"], role: Names.roleAccount)
        XCTAssertEqual(p.programArguments, ["/x/bin/lima/bin/limactl", "start", "--foreground", "--tty=false", "domovoi"])
        XCTAssertEqual(p.environment["LIMA_HOME"], "/x/lima & co", "escaped, then parsed back")
        XCTAssertEqual(p.dictionary["ExitTimeOut"] as? Int, 90)
        XCTAssertTrue(p.keepAlive)
    }

    func testParsePrint() {
        let text = """
        system/com.domovoi.mac.vm = {
        \tactive count = 1
        \tpath = /Library/LaunchDaemons/com.domovoi.mac.vm.plist
        \tstate = spawn scheduled
        \tlast exit code = 78: EX_CONFIG
        \tendpoints = {
        \t\tstate = running
        \t\tpid = 9
        \t}
        }
        """
        let s = LaunchdJobState.parse(text)
        XCTAssertEqual(s.state, "spawn scheduled")
        XCTAssertNil(s.pid, "a nested section's pid is not the job's")
        XCTAssertEqual(s.lastExit, "78: EX_CONFIG")
        XCTAssertFalse(s.running)
        XCTAssertTrue(LaunchdJobState.parse("x = {\n\tstate = running\n\tpid = 412\n}").running)
    }

    func installer(_ r: FakeRunner, _ sl: FakeSleeper, _ ops: RecordingFileOps) -> LaunchdInstaller {
        LaunchdInstaller(domain: "system", runner: r, sleeper: sl, fileOps: ops)
    }

    func testLogFilesAreMadeForTheRoleAccountBeforeBootstrap() throws {
        let r = FakeRunner(), ld = FakeLaunchd(), sl = FakeSleeper(), ops = RecordingFileOps()
        ld.install(on: r)
        r.on { c in
            if c.arguments.first == "bootstrap" { ops.events.append("bootstrap") }
            return nil
        }
        let p = try LaunchdPlist.render(template: read("launchd/com.domovoi.mac.ollama.plist"), values: values, role: Names.roleAccount)
        let st = try installer(r, sl, ops).install(p, at: URL(fileURLWithPath: "/tmp/com.domovoi.mac.ollama.plist"))
        XCTAssertTrue(st.running)
        XCTAssertEqual(ops.events, ["write /tmp/com.domovoi.mac.ollama.plist 644", "log /Library/Logs/Domovoi/ollama.log owner _domovoi", "bootstrap"])
    }

    func testAJobThatCantSpawnStopsTheInstallAndSaysWhy() throws {
        let r = FakeRunner(), ld = FakeLaunchd(), sl = FakeSleeper(), ops = RecordingFileOps()
        ld.install(on: r)
        ld.spawnFails = [Labels.ollama]
        let p = try LaunchdPlist.render(template: read("launchd/com.domovoi.mac.ollama.plist"), values: values, role: nil)
        XCTAssertThrowsError(try installer(r, sl, ops).install(p, at: URL(fileURLWithPath: "/tmp/com.domovoi.mac.ollama.plist"))) { e in
            XCTAssertTrue("\(e)".contains("system/com.domovoi.mac.ollama is not running"), "\(e)")
            XCTAssertTrue("\(e)".contains("78: EX_CONFIG"))
            XCTAssertTrue("\(e)".contains("log file"))
        }
    }

    func testReinstallBootsTheOldJobOutFirst() throws {
        let r = FakeRunner(), ld = FakeLaunchd(), sl = FakeSleeper(), ops = RecordingFileOps()
        ld.install(on: r)
        ld.loaded[Labels.ollama] = LaunchdJobState(state: "running", pid: 7, lastExit: nil)
        let p = try LaunchdPlist.render(template: read("launchd/com.domovoi.mac.ollama.plist"), values: values, role: nil)
        let st = try installer(r, sl, ops).install(p, at: URL(fileURLWithPath: "/tmp/com.domovoi.mac.ollama.plist"))
        XCTAssertNotEqual(st.pid, 7)
        let verbs = r.ran("launchctl").map { $0.arguments[0] }.filter { $0 != "print" }
        XCTAssertEqual(verbs, ["bootout", "bootstrap"])
    }
}

/// Records what would change instead of changing it.
final class RecordingFileOps: FileOps {
    var events: [String] = []
    func writeAtomically(_ data: Data, to url: URL, mode: Int) throws { events.append("write \(url.path) \(String(mode, radix: 8))") }
    func makeLogFile(_ url: URL, owner: String?) throws { events.append("log \(url.path) owner \(owner ?? "-")") }
    func chown(_ url: URL, owner: String) throws { events.append("chown \(url.path) \(owner)") }
}
