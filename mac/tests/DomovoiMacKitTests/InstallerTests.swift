import CryptoKit
import XCTest
@testable import DomovoiMacKit

/// The whole user-mode install and both uninstalls, into a temporary
/// prefix, with launchd, limactl, curl, tar and HTTP answered by fakes. The
/// files and folders are real: what the record lists is what is on disk.
final class InstallerTests: XCTestCase {
    var prefix: URL!
    var share: URL!
    var runner: FakeRunner!
    var launchd: FakeLaunchd!
    var http: FakeHTTP!
    var sleeper: FakeSleeper!
    var progress: QuietProgress!
    var created = false
    var guestFiles: [String: Data] = [:]
    var provisionResult = "ok"
    /// More fields for provision.sh's final line (`,"phone_app":false`).
    var provisionFinalExtra = ""
    /// How many egress probes from the guest fail before one gets through.
    var egressFailures = 0
    var haEvent = #"{"time":"2026-10-30T17:43:00Z","status":{"running":true,"degraded":false,"exiting":false}}"#
    /// The system install, into the temporary root (as if it were /), as
    /// root (the test says so), with the role account made by a fake dscl.
    var system = false
    /// dscl's accounts: name -> UID.
    var accounts: [String: Int] = [:]
    /// P3-8: macOS refuses `dscl -delete` from the uninstaller's own
    /// context (the app's administrator prompt); `jobRefusedToo` refuses it
    /// from the launchd job as well. TCC itself can't be faked: this only
    /// drives the control flow around its answer.
    var deleteRefused = false
    var jobRefusedToo = false
    var inLaunchdJob = false
    static let refusal = "<main> delete status: eDSPermissionError\n<dscl_cmd> DS Error: -14120 (eDSPermissionError)"
    let fileOps = TestFileOps()
    /// What the unpacked limactl says it is (the Lima tarball's version).
    var limactlVersion = "2.2.1"
    /// pfctl and sysctl (the system install's `lan` step), and a stock pf.conf.
    var pf: FakePf!
    var pfConf: URL!
    /// The guest's lima0 answer (GuestNet.script).
    var guestNet = "addr 192.168.64.5/24\ngw 192.168.64.1\ndefault-dev lima0\nrule ok\n"

    let blobs = ["lima": Data("lima tarball".utf8), "ollama": Data("ollama tarball".utf8), "ubuntu": Data("ubuntu image".utf8),
                 "stt": Data("ggml model".utf8)]

    func sha(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

    override func setUpWithError() throws {
        let base = try tempDir()
        prefix = base.appendingPathComponent("p")
        try FileManager.default.createDirectory(at: prefix, withIntermediateDirectories: false)
        share = base.appendingPathComponent("share")
        try FileManager.default.createDirectory(at: share.appendingPathComponent("installer"), withIntermediateDirectories: true)
        for d in ["vm", "launchd"] { try FileManager.default.copyItem(at: macDir.appendingPathComponent(d), to: share.appendingPathComponent(d)) }
        try """
        LIMA_VERSION=2.2.1
        LIMA_URL=https://example.org/v2.2.1/lima-2.2.1-Darwin-arm64.tar.gz
        LIMA_SHA256=\(sha(blobs["lima"]!))
        OLLAMA_VERSION=0.34.4
        OLLAMA_URL=https://example.org/v0.34.4/ollama-darwin.tgz
        OLLAMA_SHA256=\(sha(blobs["ollama"]!))
        OLLAMA_MIN_VERSION=0.12.7
        UBUNTU_IMAGE_URL=https://example.org/ubuntu-26.04-server-cloudimg-arm64.img
        UBUNTU_IMAGE_SHA256=\(sha(blobs["ubuntu"]!))
        """.write(to: share.appendingPathComponent("installer/pins.conf"), atomically: true, encoding: .utf8)

        runner = FakeRunner()
        launchd = FakeLaunchd()
        launchd.install(on: runner)
        http = FakeHTTP()
        sleeper = FakeSleeper()
        progress = QuietProgress()
        runner.on { [unowned self] c in self.answer(c) }
        pf = FakePf()
        pf.install(on: runner)
        pfConf = base.appendingPathComponent("pf.conf")
        try FakePf.stockPfConf.write(to: pfConf, atomically: true, encoding: .utf8)
        http.handler = { [unowned self] url in
            let p = self.system ? Ports.real : Ports(web: 6390, core: 6391, ollama: 11500)
            if url.hasSuffix(":\(p.ollama)/api/version") {
                return self.launchd.loaded[Labels.ollama]?.running == true ? HTTPResponse(status: 200, body: Data(#"{"version":"0.34.4"}"#.utf8)) : nil
            }
            if url.hasSuffix(":6395/health") {
                return self.launchd.loaded[Labels.stt]?.running == true ? HTTPResponse(status: 200, body: Data(#"{"status":"ok"}"#.utf8)) : nil
            }
            if url.hasSuffix(":\(p.web)/api/health") || url.hasSuffix(":\(p.core)/v1/health") {
                return self.launchd.loaded[Labels.vm]?.running == true ? HTTPResponse(status: 200) : nil
            }
            return nil
        }
    }

    var cleanup: [URL] = []

    override func tearDownWithError() throws {
        if let p = prefix { try? FileManager.default.removeItem(at: p.deletingLastPathComponent()) }
        for u in cleanup { try? FileManager.default.removeItem(at: u) }
    }

    func answer(_ c: Command) -> CommandResult? {
        // The system install runs limactl as the role account:
        // sudo -n -u ROLE /usr/bin/env VAR=... limactl ARGS.
        if c.executable == Lima.sudo {
            let a = c.arguments
            XCTAssertEqual(Array(a.prefix(4)), ["-n", "-u", "_domovoi", "/usr/bin/env"])
            XCTAssertEqual(c.currentDirectory, "/")
            var env: [String: String] = [:]
            var i = 4
            while i < a.count, let eq = a[i].firstIndex(of: "="), !a[i].hasPrefix("/") {
                env[String(a[i][..<eq])] = String(a[i][a[i].index(after: eq)...])
                i += 1
            }
            XCTAssertNotNil(accounts["_domovoi"], "limactl runs as an account that exists")
            return answer(Command(a[i], Array(a[(i + 1)...]), environment: env, stdin: c.stdin, timeout: c.timeout))
        }
        let a = c.arguments
        switch (c.executable as NSString).lastPathComponent {
        case "dscl":
            // dscl . -read|-list|-create|-delete PATH [KEY VALUE]
            let verb = a[1], path = a.count > 2 ? a[2] : ""
            let name = String(path.split(separator: "/").last ?? "")
            switch verb {
            case "-read":
                guard let uid = accounts[name] else { return CommandResult(status: 56, stderr: "eDSRecordNotFound") }
                return CommandResult(status: 0, stdout: a.count > 3 && a[3] == "UniqueID" ? "UniqueID: \(uid)\n" : "RecordName: \(name)\n")
            case "-list":
                let ids = ["root 0", "_www 70", "_taken 400"] + accounts.map { "\($0.key) \($0.value)" }
                return CommandResult(status: 0, stdout: ids.joined(separator: "\n") + "\n")
            case "-create":
                if path.hasPrefix("/Users/"), a.count > 4, a[3] == "UniqueID" { accounts[name] = Int(a[4]) }
                return CommandResult(status: 0)
            case "-delete":
                if deleteRefused && (!inLaunchdJob || jobRefusedToo) { return CommandResult(status: 40, stderr: Self.refusal) }
                if path.hasPrefix("/Users/") { accounts[name] = nil }
                return CommandResult(status: 0)
            default: return CommandResult(status: 1)
            }
        case "curl":
            let out = a[a.firstIndex(of: "-o")! + 1], url = a.last!
            let key = url.contains("ggml-") ? "stt" : url.contains("lima") ? "lima" : url.contains("ollama") ? "ollama" : "ubuntu"
            FileManager.default.createFile(atPath: out, contents: blobs[key])
            return CommandResult(status: 0)
        case "tar":
            XCTAssertEqual(a.first, "--no-same-owner", "root's tar must not keep the archive's owners")
            let dir = URL(fileURLWithPath: a[a.firstIndex(of: "-C")! + 1])
            let archive = a[a.firstIndex(of: "-xzf")! + 1]
            let files = archive.contains("lima") ? ["bin/limactl", "share/lima/lima-guestagent.Linux-aarch64.gz"] : ["ollama", "lib/ollama/libggml.so"]
            if let m = archive.range(of: #"lima-([0-9.]+)-Darwin"#, options: .regularExpression) {
                limactlVersion = String(archive[m].dropFirst(5).dropLast(7))
            }
            for f in files {
                let u = dir.appendingPathComponent(f)
                try? FileManager.default.createDirectory(at: u.deletingLastPathComponent(), withIntermediateDirectories: true)
                FileManager.default.createFile(atPath: u.path, contents: Data("x".utf8), attributes: [.posixPermissions: 0o755])
            }
            return CommandResult(status: 0)
        case "limactl":
            XCTAssertEqual(c.environment["LIMA_HOME"], layout.limaHome.path, "never the user's own ~/.lima")
            switch a.first {
            case "--version": return CommandResult(status: 0, stdout: "limactl version \(limactlVersion)\n")
            case "list":
                guard created else { return CommandResult(status: 0) }
                return CommandResult(status: 0, stdout: "domovoi \(launchd.loaded[Labels.vm]?.running == true ? "Running" : "Stopped")\n")
            case "create":
                created = true
                try? FileManager.default.createDirectory(at: layout.limaHome.appendingPathComponent("domovoi"), withIntermediateDirectories: true)
                FileManager.default.createFile(atPath: layout.limaHome.appendingPathComponent("domovoi/diffdisk").path, contents: Data("disk".utf8))
                // The host agent's events, as Lima writes them once the guest is ready.
                FileManager.default.createFile(atPath: layout.limaHome.appendingPathComponent("domovoi/ha.stdout.log").path,
                                               contents: Data((haEvent + "\n").utf8))
                return CommandResult(status: 0)
            case "delete":
                created = false
                try? FileManager.default.removeItem(at: layout.limaHome.appendingPathComponent("domovoi"))
                return CommandResult(status: 0)
            case "shell":
                let inner = Array(a.dropFirst(4))
                if inner.count == 4, inner[3] == GuestNet.script { return CommandResult(status: 0, stdout: guestNet) }
                if inner.first == "curl" {
                    XCTAssertTrue(inner.contains("https://example.org/"), "the pinned image's host")
                    if egressFailures > 0 { egressFailures -= 1; return CommandResult(status: 28, stderr: "curl: (28) Connection timed out") }
                    return CommandResult(status: 0, stdout: "200")
                }
                if inner.contains("bash") {
                    return CommandResult(status: provisionResult == "ok" ? 0 : 1, stdout: """
                    starting
                    @@DOMOVOI {"v":1,"step":"preflight","state":"start","percent":1}
                    @@DOMOVOI {"v":1,"step":"finish","state":"\(provisionResult == "ok" ? "done" : "fail")","percent":100,"final":true,"result":"\(provisionResult)","failed_step":\(provisionResult == "ok" ? "null" : "\"venv\"")\(provisionFinalExtra)}
                    """)
                }
                if inner.starts(with: ["sudo", "rm", "-f"]) {
                    for p in inner.dropFirst(3) { guestFiles[p] = nil }
                    return CommandResult(status: 0)
                }
                if inner.starts(with: ["sudo", "sha256sum"]) {
                    let d = guestFiles[inner[2]] ?? Data()
                    return CommandResult(status: 0, stdout: "\(SHA256Hex.of(d))  \(inner[2])\n")
                }
                if inner.starts(with: ["sudo", "sh", "-c"]), inner[3].contains("install -o root") {
                    let path = inner[3].components(separatedBy: "\" '").last!.replacingOccurrences(of: "'", with: "")
                    guestFiles[path] = c.stdin
                    return CommandResult(status: 0)
                }
                if inner.starts(with: ["sudo", "sh", "-c"]) {
                    let path = inner[3].components(separatedBy: "mv -f ").last!.components(separatedBy: "' '").last!.replacingOccurrences(of: "'", with: "")
                    guestFiles[path] = c.stdin
                    return CommandResult(status: 0)
                }
                if inner.starts(with: ["sudo", "-u", "domovoi"]) { return CommandResult(status: 0, stdout: "192.168.1.20\n") }
                return CommandResult(status: 0)
            default: return CommandResult(status: 0)
            }
        default: return nil
        }
    }

    var layout: Layout { system ? Layout(mode: .system, root: prefix) : Layout.user(prefix: prefix.path) }

    func options(provision: Bool = true, ollama: OllamaChoice = .own) throws -> InstallOptions {
        var o = InstallOptions(layout: layout, ports: system ? Ports.real : Ports(web: 6390, core: 6391, ollama: 11500), ollama: ollama,
                               internet: "never", tier: .floor, size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40),
                               mediaRoot: layout.defaultMediaRoot.path, mediaNames: ["Music", "Videos"], timezone: "Europe/Berlin",
                               share: share, provision: nil, launchdDomain: system ? "system" : "user/501")
        if system { o.role = Names.roleAccount }
        if provision {
            let script = prefix.deletingLastPathComponent().appendingPathComponent("provision.sh")
            FileManager.default.createFile(atPath: script.path, contents: Data("#!/bin/bash\n".utf8))
            o.provision = ProvisionSource(script: script, repoURL: "https://example.org/domovoi.git", ref: "main", trackBranch: "stable")
        }
        o.commit = String(repeating: "c", count: 40)
        return o
    }

    func installer(_ o: InstallOptions) throws -> Installer {
        let i = try Installer(options: o, runner: runner, fileOps: fileOps, http: http, sleeper: sleeper, progress: progress)
        i.lanAddress = { LanAddress(ipv4: "192.168.1.20", interface: "en0") }
        i.readInterfaces = { [LanInterface(name: "en0", ipv4: "192.168.1.20", prefix: 24, wifi: true, primary: true)] }
        i.pfConf = pfConf
        i.isRoot = { true }
        return i
    }

    func uninstall(everything: Bool) throws -> UninstallReport {
        try Uninstaller(layout: layout, runner: runner, sleeper: sleeper, fileOps: fileOps)
            .run(removeEverything: everything, confirm: everything ? "DELETE" : nil)
    }

    func contents(_ u: URL) -> [String] { (try? FileManager.default.contentsOfDirectory(atPath: u.path))?.sorted() ?? [] }

    func everything() -> [String] {
        let e = FileManager.default.enumerator(atPath: prefix.path)
        var out: [String] = []
        while let p = e?.nextObject() as? String { out.append(p) }
        return out.sorted()
    }

    // ─── tests ─────────────────────────────────────────────────────────

    func testAFullInstallThenRemoveEverythingLeavesThePrefixEmpty() throws {
        let inst = try installer(options())
        try inst.run()
        XCTAssertEqual(progress.events.filter { $0.1 == "done" }.map(\.0), Installer.steps)

        // What is on disk.
        let l = layout
        for u in [l.limactl, l.ollamaDir(version: "0.34.4").appendingPathComponent("ollama"), l.renderedVMConfig, l.installRecord,
                  l.plist(Labels.ollama), l.plist(Labels.vm), l.logs.appendingPathComponent("ollama.log"), l.logs.appendingPathComponent("vm.log"),
                  l.logs.appendingPathComponent("provision.log"), l.defaultMediaRoot.appendingPathComponent("Music")] {
            XCTAssertTrue(FileManager.default.fileExists(atPath: u.path), u.path)
        }
        XCTAssertEqual(contents(l.cache), ["lima-2.2.1-Darwin-arm64.tar.gz", "ollama-darwin.tgz", "ubuntu-26.04-server-cloudimg-arm64.img"])
        let yaml = try String(contentsOf: l.renderedVMConfig, encoding: .utf8)
        XCTAssertTrue(yaml.contains("- location: \"\(l.cache.path)/ubuntu-26.04-server-cloudimg-arm64.img\""))
        XCTAssertTrue(yaml.contains("mountPoint: \"/srv/media/videos\""))
        let attrs = try FileManager.default.attributesOfItem(atPath: l.limaHome.path)
        XCTAssertEqual((attrs[.posixPermissions] as? NSNumber)?.intValue, 0o700)

        // What the jobs are.
        XCTAssertEqual(Set(launchd.loaded.keys), [Labels.ollama, Labels.vm])
        XCTAssertEqual(launchd.bootstrapped, [Labels.ollama, Labels.vm], "Ollama before the VM")
        XCTAssertTrue(runner.calls.contains { $0.arguments == ["bootstrap", "user/501", l.plist(Labels.vm).path] })

        // provision.sh went in whole, then ran with the Mac's answers, the own Ollama and the guard.
        XCTAssertEqual(guestFiles[ProvisionSource.guestScript], Data("#!/bin/bash\n".utf8))
        let prov = try XCTUnwrap(runner.ran("limactl", "shell").first { $0.arguments.contains("bash") })
        XCTAssertEqual(Array(prov.arguments.suffix(from: 4).prefix(3)), ["sudo", "bash", ProvisionSource.guestScript])
        for pair in [["--ollama-url", "http://host.lima.internal:11500"], ["--vm-guard", "192.168.5.0/24"], ["--tier", "floor"],
                     ["--media", "music=/srv/media/music"], ["--timezone", "Europe/Berlin"], ["--track-branch", "stable"]] {
            XCTAssertNotNil(prov.arguments.firstRange(of: pair), "\(pair)")
        }

        // The two host files went into the VM.
        let lan = try XCTUnwrap(guestFiles[HostFiles.lanFile])
        XCTAssertEqual((try JSONSerialization.jsonObject(with: lan) as? [String: Any])?["ipv4"] as? String, "192.168.1.20")
        let side = try XCTUnwrap(guestFiles[HostFiles.sideFile])
        XCTAssertEqual((try JSONSerialization.jsonObject(with: side) as? [String: Any])?["build_commit"] as? String, String(repeating: "c", count: 40))

        // The record lists it all, and is on disk.
        let rec = try XCTUnwrap(try RecordStore.load(l.installRecord))
        XCTAssertEqual(rec.ollamaURL, "http://host.lima.internal:11500")
        XCTAssertEqual(Set(rec.done.keys), Set(Installer.steps))
        XCTAssertTrue(rec.has(.limaInstance, label: "domovoi"))
        XCTAssertTrue(rec.has(.launchdJob, path: l.plist(Labels.vm).path, label: Labels.vm))

        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertTrue(r.recordRemoved)
        XCTAssertEqual(r.left, [])
        XCTAssertEqual(everything(), [], "the prefix is empty")
        XCTAssertTrue(launchd.loaded.isEmpty)
        XCTAssertFalse(created, "limactl delete ran")
        // limactl was still there for the delete: the instance went before the program files.
        let order = runner.calls.map { "\(($0.executable as NSString).lastPathComponent) \($0.arguments.first ?? "")" }
        XCTAssertLessThan(order.lastIndex(of: "launchctl bootout")!, order.lastIndex(of: "limactl delete")!)

        // A second run finds no record and does nothing.
        let again = try uninstall(everything: true)
        XCTAssertEqual(again, UninstallReport())
    }

    func testARunAgainIsARepairThatChangesNothing() throws {
        try installer(options()).run()
        let before = everything()
        let rec1 = try RecordStore.load(layout.installRecord)!
        launchd.bootstrapped = []
        try installer(options()).run()
        XCTAssertEqual(everything(), before)
        XCTAssertEqual(try RecordStore.load(layout.installRecord)!.created, rec1.created)
        XCTAssertEqual(runner.ran("limactl", "create").count, 1)
        XCTAssertEqual(launchd.bootstrapped, [Labels.ollama], "Ollama's job is rewritten; the running VM job is left running")
    }

    func testKeepDataKeepsTheVMModelsLogsAndRecord() throws {
        try installer(options()).run()
        let modelBlob = layout.ollamaModels.appendingPathComponent("blobs/sha256-x")
        try FileManager.default.createDirectory(at: modelBlob.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: modelBlob.path, contents: Data("model".utf8))

        let r = try uninstall(everything: false)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertFalse(r.recordRemoved)
        XCTAssertTrue(launchd.loaded.isEmpty, "no job left to start anything")
        let fm = FileManager.default
        for gone in [layout.limaDir, layout.ollamaRoot, layout.plist(Labels.vm), layout.plist(Labels.ollama), layout.launchDaemons, layout.bin] {
            XCTAssertFalse(fm.fileExists(atPath: gone.path), "program files go: \(gone.path)")
        }
        for kept in [layout.limaHome.appendingPathComponent("domovoi/diffdisk"), modelBlob, layout.cache, layout.installRecord,
                     layout.logs.appendingPathComponent("provision.log"), layout.defaultMediaRoot.appendingPathComponent("Music")] {
            XCTAssertTrue(fm.fileExists(atPath: kept.path), "data stays: \(kept.path)")
        }
        XCTAssertTrue(created, "the VM is stopped, not deleted")
        let rec = try XCTUnwrap(try RecordStore.load(layout.installRecord))
        XCTAssertNotNil(rec.uninstalledAt)
        XCTAssertFalse(rec.created.contains { $0.keep == .program && $0.kind != .dir }, "the record no longer lists what went")

        // Reinstall onto the kept data reuses the VM, then remove everything.
        try installer(options()).run()
        XCTAssertEqual(runner.ran("limactl", "create").count, 1)
        XCTAssertNil(try RecordStore.load(layout.installRecord)?.uninstalledAt)
        XCTAssertTrue(try uninstall(everything: true).ok)
        XCTAssertEqual(everything(), [])
    }

    func testAVMThatNeverGetsReadyStopsBeforeProvision() throws {
        // Running, and a shell works, but the host agent never says ready
        // (or says it is exiting): provision.sh must not start.
        for event in [#"{"status":{"running":false}}"#, #"{"status":{"running":true,"exiting":true}}"#] {
            haEvent = event
            let i = try installer(options())
            i.vmWait = 30
            XCTAssertThrowsError(try i.run()) { XCTAssertTrue("\($0)".contains("not ready"), "\($0)") }
            XCTAssertFalse(runner.calls.contains { $0.arguments.contains("bash") }, "provision.sh never ran")
            XCTAssertTrue(try uninstall(everything: true).ok)
            XCTAssertEqual(everything(), [])
        }
    }

    func testProvisionWaitsForTheGuestToReachTheInternet() throws {
        egressFailures = 4
        try installer(options()).run()
        XCTAssertEqual(egressFailures, 0)
        XCTAssertTrue(progress.lines.contains { $0.contains("the VM reaches example.org (after 5 tries") }, "\(progress.lines)")
    }

    func testNoInternetInTheGuestStopsBeforeProvision() throws {
        egressFailures = 1000
        let i = try installer(options())
        i.egressWait = 60
        XCTAssertThrowsError(try i.run()) { XCTAssertTrue("\($0)".contains("the VM can't reach https://example.org/"), "\($0)") }
        XCTAssertFalse(runner.calls.contains { $0.arguments.contains("bash") }, "provision.sh never ran")
        XCTAssertTrue(try uninstall(everything: true).ok)
        XCTAssertEqual(everything(), [])
    }

    func testRemoveEverythingNeedsTheWord() throws {
        try installer(options(provision: false)).run()
        let u = Uninstaller(layout: layout, runner: runner, sleeper: sleeper, fileOps: LocalFileOps())
        XCTAssertThrowsError(try u.run(removeEverything: true, confirm: nil))
        XCTAssertThrowsError(try u.run(removeEverything: true, confirm: "delete"))
        XCTAssertFalse(launchd.loaded.isEmpty, "nothing was touched")
    }

    func testWhatIsNotOursStays() throws {
        try installer(options(provision: false)).run()
        let theirs = layout.appSupport.appendingPathComponent("notes.txt")
        FileManager.default.createFile(atPath: theirs.path, contents: Data("mine".utf8))
        let song = layout.defaultMediaRoot.appendingPathComponent("Music/song.mp3")
        FileManager.default.createFile(atPath: song.path, contents: Data("la".utf8))

        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok)
        XCTAssertEqual(try String(contentsOf: theirs, encoding: .utf8), "mine")
        XCTAssertEqual(try String(contentsOf: song, encoding: .utf8), "la", "media files are never deleted")
        XCTAssertTrue(r.left.contains { $0.hasPrefix(layout.appSupport.path + " (not empty: notes.txt") }, "\(r.left)")
        XCTAssertTrue(r.left.contains { $0.contains("Music (not empty: song.mp3") }, "\(r.left)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.defaultMediaRoot.appendingPathComponent("Videos").path), "an empty media folder it made goes")
        XCTAssertEqual(contents(layout.appSupport), ["notes.txt"])
    }

    func testAHalfFinishedInstallIsUndoneToo() throws {
        var o = try options()
        o.stopAfter = "ollama"
        try installer(o).run()
        XCTAssertEqual(Set(launchd.loaded.keys), [Labels.ollama])
        XCTAssertTrue(try uninstall(everything: true).ok)
        XCTAssertEqual(everything(), [])
        XCTAssertTrue(launchd.loaded.isEmpty)
    }

    func testAFailedProvisionStopsAndNamesItsLog() throws {
        provisionResult = "failed"
        XCTAssertThrowsError(try installer(options()).run()) { e in
            XCTAssertTrue("\(e)".contains("provision.sh: failed at venv"), "\(e)")
            XCTAssertTrue("\(e)".contains("provision.log"))
        }
        let rec = try XCTUnwrap(try RecordStore.load(layout.installRecord))
        XCTAssertNil(rec.done["provision"])
        XCTAssertNotNil(rec.done["vm-start"])
        XCTAssertTrue(try uninstall(everything: true).ok)
        XCTAssertEqual(everything(), [])
    }

    /// provision.sh couldn't download the phone app (no internet): never
    /// fatal. The install finishes, and the helper tells Setup's last page.
    func testAPhoneAppThatCouldNotBeDownloadedIsSaidNotFatal() throws {
        provisionFinalExtra = #","phone_app":false"#
        try installer(options()).run()
        XCTAssertEqual(progress.events.filter { $0.1 == "done" }.map(\.0), Installer.steps, "the install went on")
        XCTAssertTrue(progress.events.contains { $0.0 == "phone-app" && $0.1 == "missing" && $0.2 == ProvisionProgress.phoneAppMissing },
                      "\(progress.events.filter { $0.0 == "phone-app" })")
        XCTAssertEqual(progress.events.first { $0.0 == "provision" && $0.1 == "done" }?.2,
                       "provision.sh ok; the phone app couldn't be downloaded")
    }

    /// S3-4: "Restore from a backup". The backup goes into the VM root's
    /// only (0600, made under umask 077), provision.sh gets
    /// --restore-identity for that file, and the file is gone after the
    /// run, whether it worked or not. Its contents are never logged.
    func testARestoredIdentityGoesInRootOnlyForOneRunThenIsGone() throws { try restoreRun(result: "ok") }
    func testARestoredIdentityIsGoneAfterAFailedRunToo() throws { try restoreRun(result: "failed") }

    func restoreRun(result: String) throws {
        let backup = Data(#"{"kind":"domovoi-server-identity-backup","private_key":"c2VjcmV0","fingerprint":"ab:cd"}"#.utf8)
        provisionResult = result
        var o = try options()
        o.restoreIdentity = backup
        let inst = try installer(o)
        if result == "ok" { try inst.run() } else { XCTAssertThrowsError(try inst.run()) }
        let put = try XCTUnwrap(runner.calls.first { $0.arguments.last?.contains("install -o root") == true && $0.arguments.last!.contains(IdentityBackup.guestFile) })
        XCTAssertEqual(put.stdin, backup)
        XCTAssertTrue(put.arguments.last!.contains("-m 0600"), put.arguments.last!)
        XCTAssertTrue(put.arguments.last!.contains("umask 077"), put.arguments.last!)
        let prov = try XCTUnwrap(runner.ran("limactl", "shell").first { $0.arguments.contains("bash") })
        XCTAssertNotNil(prov.arguments.firstRange(of: ["--restore-identity", IdentityBackup.guestFile]))
        XCTAssertNil(guestFiles[IdentityBackup.guestFile], "\(result): removed after the run")
        XCTAssertFalse(progress.lines.joined().contains("c2VjcmV0"), "the key is never logged")
        XCTAssertFalse(progress.events.contains { $0.2.contains("c2VjcmV0") })
    }

    func testNoBackupNoRestoreArgument() throws {
        try installer(options()).run()
        let prov = try XCTUnwrap(runner.ran("limactl", "shell").first { $0.arguments.contains("bash") })
        XCTAssertFalse(prov.arguments.contains("--restore-identity"))
        XCTAssertFalse(runner.calls.contains { $0.arguments.contains { $0.contains(IdentityBackup.guestFile) } })
    }

    func testAPhoneAppOnTheServerSaysNothing() throws {
        provisionFinalExtra = #","phone_app":true"#
        try installer(options()).run()
        XCTAssertFalse(progress.events.contains { $0.0 == "phone-app" })
        XCTAssertEqual(progress.events.first { $0.0 == "provision" && $0.1 == "done" }?.2, "provision.sh ok")
    }

    func testTheExistingOllamaIsUsedAndNeverTouched() throws {
        http.answers["http://127.0.0.1:11434/api/version"] = HTTPResponse(status: 200, body: Data(#"{"version":"0.40.12"}"#.utf8))
        var o = try options(ollama: .existing(port: 11434))
        o.ports.ollama = 11434
        try installer(o).run()
        XCTAssertEqual(Set(launchd.loaded.keys), [Labels.vm], "no Ollama job of ours")
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.ollamaRoot.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.ollamaModels.path), "the models live in that Ollama's folder")
        let prov = try XCTUnwrap(runner.ran("limactl", "shell").first { $0.arguments.contains("bash") })
        XCTAssertNotNil(prov.arguments.firstRange(of: ["--ollama-url", "http://host.lima.internal:11434"]))
        XCTAssertFalse(runner.ran("curl").contains { $0.arguments.last!.contains("ollama") }, "Ollama not downloaded")
        XCTAssertTrue(try uninstall(everything: true).ok)
        XCTAssertEqual(everything(), [])
    }

    func testAnOldExistingOllamaStopsBeforeAnyVM() throws {
        http.answers["http://127.0.0.1:11434/api/version"] = HTTPResponse(status: 200, body: Data(#"{"version":"0.11.0"}"#.utf8))
        var o = try options(ollama: .existing(port: 11434))
        o.ports.ollama = 11434
        XCTAssertThrowsError(try installer(o).run())
        XCTAssertEqual(runner.ran("limactl", "create").count, 0)
    }

    func testSomethingElseOnOurOllamaPortStopsTheInstall() throws {
        http.answers["http://127.0.0.1:11500/api/version"] = HTTPResponse(status: 200, body: Data(#"{"version":"0.40.12"}"#.utf8))
        http.handler = nil
        XCTAssertThrowsError(try installer(options()).run()) { XCTAssertTrue("\($0)".contains("already answers on 127.0.0.1:11500")) }
    }

    func testADownloadThatDoesntMatchIsDeleted() throws {
        runner.on { c in
            guard c.executable == "/usr/bin/curl" else { return nil }
            FileManager.default.createFile(atPath: c.arguments[c.arguments.firstIndex(of: "-o")! + 1], contents: Data("tampered".utf8))
            return CommandResult(status: 0)
        }
        XCTAssertThrowsError(try installer(options()).run()) { XCTAssertTrue("\($0)".contains("SHA-256 mismatch")) }
        XCTAssertEqual(contents(layout.cache), [])
    }

    func testSeedCacheIsUsedWhenItMatches() throws {
        let seed = prefix.deletingLastPathComponent().appendingPathComponent("seed")
        try FileManager.default.createDirectory(at: seed, withIntermediateDirectories: false)
        FileManager.default.createFile(atPath: seed.appendingPathComponent("lima-2.2.1-Darwin-arm64.tar.gz").path, contents: blobs["lima"])
        FileManager.default.createFile(atPath: seed.appendingPathComponent("ollama-darwin.tgz").path, contents: Data("wrong".utf8))
        var o = try options(provision: false)
        o.seedCache = [seed]
        try installer(o).run()
        let fetched = runner.ran("curl").map { $0.arguments.last! }
        XCTAssertFalse(fetched.contains { $0.contains("lima") }, "Lima came from the seed")
        XCTAssertTrue(fetched.contains { $0.contains("ollama") }, "a seed that doesn't match is not used")
        XCTAssertTrue(try uninstall(everything: true).ok)
        XCTAssertEqual(everything(), [])
        XCTAssertTrue(FileManager.default.fileExists(atPath: seed.appendingPathComponent("lima-2.2.1-Darwin-arm64.tar.gz").path), "the seed is not ours")
    }

    func testOptionsAreChecked() throws {
        var o = try options()
        o.ports = Ports(web: 6369, core: 6391, ollama: 11500)
        XCTAssertThrowsError(try o.check(), "the user mode never takes a real port")
        o = try options()
        o.mediaRoot = "/Users/Shared/Domovoi"
        XCTAssertThrowsError(try o.check(), "user-mode media stays in the prefix")
        o = try options()
        o.internet = "maybe"
        XCTAssertThrowsError(try o.check())
        o = try options()
        o.layout = .system
        o.role = Names.roleAccount
        XCTAssertThrowsError(try Installer(options: o, runner: runner, fileOps: LocalFileOps(), http: http, sleeper: sleeper, progress: progress).run(),
                             "the system install needs root") { XCTAssertTrue("\($0)".contains("administrator"), "\($0)") }
        o.role = nil
        XCTAssertThrowsError(try o.check(), "the system install needs its role account")
        o.role = Names.roleAccount
        for bad in ["/", "/Users", "/Library/Application Support/Domovoi/state", "/System/Library"] {
            o.mediaRoot = bad
            XCTAssertThrowsError(try o.check(), bad)
        }
        o.mediaRoot = "/Users/Shared/Domovoi"
        XCTAssertNoThrow(try o.check())
    }

    // ─── the system install (Phase 3), into a temporary "/" ────────────

    /// What macOS and the package put there before Setup runs.
    func systemRoot() throws -> [String] {
        let fm = FileManager.default
        if !system {
            // A short root: Lima's sockets must fit 104 bytes under
            // ROOT/Library/Application Support/Domovoi/lima (the real one is /).
            let short = URL(fileURLWithPath: "/private/tmp/dmk\(UUID().uuidString.prefix(6))/p")
            try fm.createDirectory(at: short, withIntermediateDirectories: true)
            cleanup.append(prefix.deletingLastPathComponent())
            prefix = short
        }
        system = true
        for d in ["Applications/Domovoi.app/Contents/MacOS", "Library/LaunchDaemons", "Library/Logs", "Users/Shared",
                  "Library/Application Support/Domovoi/bin", "Library/Application Support/Domovoi/share/vm"] {
            try fm.createDirectory(at: prefix.appendingPathComponent(d), withIntermediateDirectories: true)
        }
        for f in ["Applications/Domovoi.app/Contents/MacOS/Domovoi", "Library/Application Support/Domovoi/bin/DomovoiHelper",
                  "Library/Application Support/Domovoi/bin/domovoi-uninstall", "Library/Application Support/Domovoi/share/vm/domovoi.yaml"] {
            fm.createFile(atPath: prefix.appendingPathComponent(f).path, contents: Data("x".utf8))
        }
        return ["Applications", "Library", "Library/Application Support", "Library/LaunchDaemons", "Library/Logs", "Users", "Users/Shared"]
    }

    func testSystemInstallMakesTheRoleAccountAndRemoveEverythingLeavesNothing() throws {
        let macOS = try systemRoot()
        try installer(options()).run()
        let l = layout

        // The role account, in the range, as the record says.
        let uid = try XCTUnwrap(accounts["_domovoi"])
        XCTAssertEqual(uid, 399, "the highest free number: 400 is taken")
        let rec = try XCTUnwrap(try RecordStore.load(l.installRecord))
        XCTAssertEqual(rec.roleAccount, "_domovoi")
        XCTAssertEqual(rec.roleUID, 399)
        XCTAssertTrue(runner.calls.contains { $0.executable == RoleAccount.dscl && $0.arguments == [".", "-create", "/Users/_domovoi", "IsHidden", "1"] })
        XCTAssertTrue(runner.calls.contains { $0.executable == RoleAccount.dscl && $0.arguments == [".", "-create", "/Users/_domovoi", "UserShell", "/usr/bin/false"] })

        // The jobs are LaunchDaemons in the system domain, run by the role
        // account, and their log files are that account's (S1/S4).
        XCTAssertTrue(runner.calls.contains { $0.arguments == ["bootstrap", "system", l.plist(Labels.vm).path] })
        for label in [Labels.vm, Labels.ollama] {
            let p = try LaunchdPlist(text: String(contentsOf: l.plist(label), encoding: .utf8))
            XCTAssertEqual(p.userName, "_domovoi")
            XCTAssertEqual(p.dictionary["GroupName"] as? String, "_domovoi")
            for f in p.logFiles { XCTAssertEqual(fileOps.owners[f], "_domovoi", f) }
        }
        // limactl only ever as the role account; LIMA_HOME, models and the
        // jobs' home are given to it.
        XCTAssertFalse(runner.calls.contains { ($0.executable as NSString).lastPathComponent == "limactl" }, "never as root")
        XCTAssertTrue(runner.calls.contains { $0.executable == Lima.sudo && $0.arguments.contains("create") })
        for tree in [l.limaHome, l.ollamaModels, l.jobHome] {
            XCTAssertTrue(runner.calls.contains { $0.executable == "/usr/sbin/chown" && $0.arguments == ["-R", "_domovoi:_domovoi", tree.path] }, tree.path)
        }
        // The media folders carry the role account's inherited entry.
        for m in [l.defaultMediaRoot, l.defaultMediaRoot.appendingPathComponent("Music")] {
            XCTAssertTrue(runner.calls.contains { $0.executable == MediaACL.chmod && $0.arguments == ["+a", MediaACL.entry(for: "_domovoi"), m.path] }, m.path)
        }
        // Not recorded: the folders the package made.
        XCTAssertFalse(rec.has(.dir, path: l.appSupport.path))
        XCTAssertFalse(rec.has(.dir, path: l.bin.path))

        // Phase 4: the household's way in. The root helper's job (no
        // UserName: pf needs root), the redirect from the LAN to lima0, one
        // pf reference, forwarding on, the guest's LAN fence asked for.
        XCTAssertTrue(runner.calls.contains { $0.arguments == ["bootstrap", "system", l.plist(Labels.helper).path] })
        let helperJob = try LaunchdPlist(text: String(contentsOf: l.plist(Labels.helper), encoding: .utf8))
        XCTAssertNil(helperJob.userName)
        XCTAssertEqual(helperJob.programArguments, [l.helperProgram.path, "serve", "--system"])
        XCTAssertTrue(helperJob.keepAlive)
        XCTAssertTrue(pf.anchor.contains("rdr pass on en0 inet proto tcp from 192.168.1.0/24 to (en0) port { 80 6369 6370 7676 8050:8099 8096 9467 } -> 192.168.64.5"),
                      "nothing on this Mac answers lsof for port 80, so the short address is redirected too")
        let short = try XCTUnwrap(guestFiles[HostFiles.shortAddressFile], "the VM is told about the Mac's port 80")
        XCTAssertEqual((try JSONSerialization.jsonObject(with: short) as? [String: Any])?["state"] as? String, "on")
        XCTAssertTrue(pf.anchor.contains("block drop in quick on en0 inet from any to <\(PfRules.fenceTable)>"), "and nothing else forwarded")
        XCTAssertEqual(pf.refs.count, 1)
        XCTAssertEqual(pf.forwarding, "1")
        XCTAssertTrue(rec.has(.hostNetwork, label: PfRules.anchor))
        XCTAssertTrue(progress.events.contains { $0.0 == "lan" && $0.1 == "done" && $0.2.contains("reach Domovoi at 192.168.1.20") },
                      "\(progress.events.filter { $0.0 == "lan" })")
        let prov = try XCTUnwrap(runner.calls.first { $0.executable == Lima.sudo && $0.arguments.contains("bash") })
        XCTAssertNotNil(prov.arguments.firstRange(of: ["--vm-guard-lan", "lima0"]))
        let lan = try XCTUnwrap(guestFiles[HostFiles.lanFile])
        XCTAssertEqual((try JSONSerialization.jsonObject(with: lan) as? [String: Any])?["interface"] as? String, "en0")
        // S2-10: the network-name report, which the core vouches from: the redirect's address.
        let vouched = try XCTUnwrap(guestFiles[HostFiles.lanNameFile])
        XCTAssertEqual((try JSONSerialization.jsonObject(with: vouched) as? [String: Any])?["addresses"] as? [String], ["192.168.1.20"])

        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        // The redirect, the pf reference and forwarding are as Setup found
        // them, and only after the helper that keeps them up was stopped.
        XCTAssertEqual(pf.anchor, "")
        XCTAssertEqual(pf.refs, [])
        XCTAssertEqual(pf.forwarding, "0")
        let bootout = try XCTUnwrap(runner.calls.firstIndex { $0.arguments == ["bootout", "system/\(Labels.helper)"] })
        let release = try XCTUnwrap(runner.calls.firstIndex { $0.executable == PfController.pfctl && $0.arguments.first == "-X" })
        XCTAssertLessThan(bootout, release)
        XCTAssertNil(accounts["_domovoi"], "the account is gone")
        XCTAssertTrue(runner.calls.contains { $0.executable == "/usr/sbin/pkgutil" && $0.arguments == ["--forget", "com.domovoi.mac"] })
        XCTAssertTrue(runner.calls.contains { $0.executable == MediaACL.chmod && $0.arguments == ["-a", MediaACL.entry(for: "_domovoi"), l.defaultMediaRoot.path] })
        XCTAssertEqual(everything(), macOS, "only what macOS had: the package's files and folders are gone too")
        // The VM was deleted as the role account, before the account went.
        let order = runner.calls.map { c -> String in
            if c.executable == Lima.sudo, let i = c.arguments.firstIndex(where: { $0.hasSuffix("limactl") }) { return "limactl \(c.arguments[i + 1])" }
            return "\((c.executable as NSString).lastPathComponent) \(c.arguments.dropFirst(c.executable == RoleAccount.dscl ? 1 : 0).first ?? "")"
        }
        XCTAssertLessThan(order.lastIndex(of: "limactl delete")!, order.lastIndex(of: "dscl -delete")!)
    }

    func testSystemKeepDataThenReinstallReusesTheUID() throws {
        _ = try systemRoot()
        try installer(options()).run()
        let r = try uninstall(everything: false)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertEqual(r.exitStatus, 0)
        XCTAssertEqual(accounts["_domovoi"], 399, "keep data keeps the account that owns the data (P3-8)")
        XCTAssertTrue(runner.calls.filter { $0.executable == RoleAccount.dscl && $0.arguments.contains("-delete") }.isEmpty, "not even tried")
        XCTAssertTrue(r.kept.contains { $0.hasPrefix("account _domovoi (UID 399)") }, "\(r.kept)")
        XCTAssertTrue(r.summary(removeEverything: false).contains("the hidden account _domovoi that owns them"))
        let rec = try XCTUnwrap(try RecordStore.load(layout.installRecord))
        XCTAssertEqual(rec.roleUID, 399, "its UID is kept for a reinstall")
        XCTAssertTrue(rec.has(.account, label: "_domovoi"), "still listed: remove everything deletes it later")
        XCTAssertNotNil(rec.uninstalledAt)
        for gone in [layout.app, layout.helperProgram, layout.uninstallProgram, layout.share, layout.bin] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: gone.path), gone.path)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: layout.limaHome.appendingPathComponent("domovoi/diffdisk").path))

        // Someone else took 399 meanwhile? No: it is free, and is asked for.
        accounts["_other"] = 398
        _ = try systemRoot()
        try installer(options()).run()
        XCTAssertEqual(accounts["_domovoi"], 399)
        XCTAssertEqual(runner.ran("limactl", "create").count + runner.calls.filter { $0.executable == Lima.sudo && $0.arguments.contains("create") }.count, 1,
                       "the kept VM is used")
        XCTAssertTrue(try uninstall(everything: true).ok)
    }

    func testSystemInstallAddsTheMenuBarIconAndKeepsTheMacAwakeAndUninstallUndoesBoth() throws {
        let macOS = try systemRoot()
        let pm = FakePmset()
        pm.install(on: runner)
        var o = try options()
        o.keepAwake = true
        try installer(o).run()
        let l = layout

        // The icon's LaunchAgent: every account's session, as that account.
        let agent = try LaunchdPlist(text: String(contentsOf: l.menuBarAgent, encoding: .utf8))
        XCTAssertEqual(agent.label, Labels.menuBar)
        XCTAssertEqual(agent.programArguments, [l.appProgram.path, "--menu-bar"])
        XCTAssertEqual(agent.dictionary["LimitLoadToSessionType"] as? String, "Aqua")
        XCTAssertNil(agent.userName, "never root, never the role account")
        XCTAssertEqual((agent.dictionary["KeepAlive"] as? [String: Any])?["SuccessfulExit"] as? Bool, false)
        XCTAssertFalse(launchd.bootstrapped.contains(Labels.menuBar), "launchd starts it at sign-in; the setup pages for their own account")
        // The password-free Restart (Train 1 #15): a root job with fixed
        // arguments, started by launchd for a connection to its socket.
        let job = try LaunchdPlist(text: String(contentsOf: l.plist(Labels.restart), encoding: .utf8))
        XCTAssertEqual(job.programArguments, [l.helperProgram.path, "restart-trigger", "--system"])
        XCTAssertNil(job.userName, "root")
        let sock = try XCTUnwrap((job.dictionary["Sockets"] as? [String: Any])?["Listener"] as? [String: Any])
        XCTAssertEqual(sock["SockPathName"] as? String, l.restartSocket.path)
        XCTAssertEqual(sock["SockPathMode"] as? Int, 438)
        XCTAssertNil(job.dictionary["inetdCompatibility"], "one process for every connection")
        XCTAssertNil(job.dictionary["RunAtLoad"])
        XCTAssertNil(job.dictionary["KeepAlive"])
        XCTAssertNil(job.dictionary["StandardOutPath"])
        XCTAssertTrue(launchd.bootstrapped.contains(Labels.restart))
        let attrs = try FileManager.default.attributesOfItem(atPath: l.runDir.path)
        XCTAssertEqual((attrs[.posixPermissions] as? NSNumber)?.intValue, 0o755)
        XCTAssertFalse(FileManager.default.fileExists(atPath: l.legacyRestartRequests.path))
        // Keep this Mac awake: recorded first, then set.
        XCTAssertEqual(pm.acSleep, 0)
        XCTAssertEqual(KeepAwake.record(at: l.powerRecord)?.acSleepBefore, 10)
        let rec = try XCTUnwrap(try RecordStore.load(l.installRecord))
        XCTAssertEqual(rec.answers.keepAwake, true)
        XCTAssertTrue(rec.has(.file, path: l.menuBarAgent.path))
        XCTAssertEqual(progress.events.first { $0.0 == "power" && $0.1 == "done" }?.2.contains("no longer sleeps"), true)

        // Uninstall as both programs run it: the cleanup, then the record's list.
        _ = MacSideCleanup.beforeUninstall(layout: l, runner: runner, fileOps: fileOps, log: { _ in })
        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertEqual(pm.acSleep, 10, "the sleep setting is back")
        XCTAssertEqual(everything(), macOS, "the LaunchAgent and its folder are gone too")
    }

    func testAPowerSettingThatCantChangeDoesntStopTheInstall() throws {
        _ = try systemRoot()
        var o = try options()
        o.keepAwake = true
        try installer(o).run()   // pmset answers nothing here
        XCTAssertEqual(progress.events.first { $0.0 == "power" && $0.1 == "done" }?.2.hasPrefix("not changed"), true)
        XCTAssertEqual(progress.events.last?.0, "health", "the install went on")
    }

    func testTheUserModeNeverTouchesPowerOrTheMenuBar() throws {
        var o = try options()
        o.keepAwake = true
        try installer(o).run()
        XCTAssertFalse(runner.calls.contains { $0.executable == KeepAwake.pmset })
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.menuBarAgent.path))
    }


    // ─── P3-8: macOS refuses the account delete from the app's prompt ──

    /// The launchd job runs its program through the same fake dscl, as the
    /// job's context (`inLaunchdJob`); its standard error goes to its log.
    func runOneShotJobs() {
        launchd.oneShot[AccountRemoval.label] = { [unowned self] path in
            let d = NSDictionary(contentsOfFile: path) as? [String: Any] ?? [:]
            let args = d["ProgramArguments"] as? [String] ?? []
            XCTAssertEqual(d["KeepAlive"] as? Bool, false)
            XCTAssertNil(d["UserName"], "root: no UserName")
            guard let exe = args.first else { return 78 }
            self.inLaunchdJob = true
            defer { self.inLaunchdJob = false }
            let r = self.answer(Command(exe, Array(args.dropFirst()))) ?? CommandResult(status: 0)
            if let log = d["StandardErrorPath"] as? String { try? r.stderr.write(toFile: log, atomically: true, encoding: .utf8) }
            return r.status
        }
    }

    func testRemoveEverythingFallsBackToALaunchdJobWhenDsclIsRefused() throws {
        let macOS = try systemRoot()
        try installer(options()).run()
        deleteRefused = true
        runOneShotJobs()
        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertEqual(r.unfinished, [])
        XCTAssertEqual(r.exitStatus, 0)
        XCTAssertNil(accounts["_domovoi"], "deleted by the job")
        XCTAssertTrue(r.removed.contains("account _domovoi (by a launchd job)"), "\(r.removed)")
        // The job ran in the system domain from a file in the record's
        // folder, and nothing of it is left: not loaded, file and log gone.
        let jobFile = layout.state.appendingPathComponent("\(AccountRemoval.label).plist").path
        XCTAssertTrue(runner.calls.contains { $0.executable == LaunchdInstaller.launchctl && $0.arguments == ["bootstrap", "system", jobFile] })
        XCTAssertNil(launchd.loaded[AccountRemoval.label])
        XCTAssertEqual(everything(), macOS, "the package's files and the record are gone too")
    }

    func testARefusedAccountDeleteStillRemovesTheProgramsAndSaysHowToFinish() throws {
        let macOS = try systemRoot()
        try installer(options()).run()
        deleteRefused = true
        jobRefusedToo = true
        runOneShotJobs()
        let r = try uninstall(everything: true)
        // Not a failure: the rest went (P3-8 left the app, bin, share and the
        // receipt behind because the account step "failed").
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertEqual(accounts["_domovoi"], 399, "still there")
        XCTAssertEqual(r.unfinished.count, 1)
        XCTAssertTrue(r.unfinished[0].contains(RoleAccount.finishCommand("_domovoi")), r.unfinished[0])
        XCTAssertTrue(runner.calls.contains { $0.executable == "/usr/sbin/pkgutil" && $0.arguments == ["--forget", "com.domovoi.mac"] }, "the receipt went")
        for gone in [layout.app, layout.helperProgram, layout.uninstallProgram, layout.share, layout.installRecord] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: gone.path), gone.path)
        }
        XCTAssertEqual(everything(), macOS, "only the account is left, and it is not a file")
        XCTAssertNil(launchd.loaded[AccountRemoval.label], "the job never stays loaded")
        // What the person reads, and how the app takes it.
        XCTAssertEqual(r.exitStatus, 3)
        let text = r.summary(removeEverything: true)
        XCTAssertTrue(text.hasPrefix("Domovoi removed, except for one thing."), text)
        XCTAssertTrue(text.contains(UninstallReport.unfinishedMarker + " macOS did not let Domovoi delete its hidden account _domovoi."), text)
        XCTAssertTrue(text.contains("\n    sudo dscl . -delete /Users/_domovoi; sudo dscl . -delete /Groups/_domovoi\n"), "the line to paste, on its own line")
        XCTAssertTrue(UninstallReport.appShowsRemoved(promptOK: false, output: text), "exit 3 is a 'removed' page, not an error")
        XCTAssertFalse(UninstallReport.appShowsRemoved(promptOK: false, output: "Uninstall did not finish: run it again. x"))
        XCTAssertTrue(UninstallReport.appShowsRemoved(promptOK: true, output: "Domovoi removed."))
    }

    func testAnotherDsclErrorIsNotRetriedAsAJob() throws {
        _ = try systemRoot()
        try installer(options()).run()
        runner.on { c in
            c.executable == RoleAccount.dscl && c.arguments.contains("-delete") ? CommandResult(status: 1, stderr: "DS Error: -14009 (eDSUnknownNodeName)") : nil
        }
        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertEqual(r.unfinished.count, 1)
        XCTAssertFalse(launchd.bootstrapped.contains(AccountRemoval.label), "only a permission refusal is worth another context")
    }

    func testAFailureBeforeTheAccountStillKeepsEverything() throws {
        // The rule that stays: a job or VM step that fails stops the run
        // before anything else is removed, so a second run can finish it.
        _ = try systemRoot()
        try installer(options()).run()
        runner.on { c in c.executable == LaunchdInstaller.launchctl && c.arguments.first == "bootout" ? CommandResult(status: 5, stderr: "Boot-out failed: 5") : nil }
        let r = try uninstall(everything: true)
        XCTAssertFalse(r.ok)
        XCTAssertEqual(r.exitStatus, 1)
        XCTAssertEqual(accounts["_domovoi"], 399)
        XCTAssertTrue(FileManager.default.fileExists(atPath: layout.app.path), "the package step waits for a clean run")
        XCTAssertTrue(r.summary(removeEverything: true).hasPrefix("Uninstall did not finish: run it again."))
    }

    func testATamperedRecordTouchesNothingOutsideThePrefix() throws {
        try installer(options(provision: false)).run()
        var rec = try RecordStore.load(layout.installRecord)!
        rec.created.append(.init(kind: .tree, path: "/Users/alex", keep: .program, step: "evil"))
        try LocalFileOps().writeAtomically(try JSONEncoder().encode(rec), to: layout.installRecord, mode: 0o644)
        XCTAssertThrowsError(try uninstall(everything: true)) { XCTAssertTrue("\($0)".contains("outside")) }
        rec.created.removeLast()
        rec.created.append(.init(kind: .tree, path: prefix.path + "/../elsewhere", keep: .program, step: "evil"))
        try LocalFileOps().writeAtomically(try JSONEncoder().encode(rec), to: layout.installRecord, mode: 0o644)
        XCTAssertThrowsError(try uninstall(everything: true))
        XCTAssertFalse(launchd.loaded.isEmpty)
    }

    // ─── the speech service (PHASE8A-STT.md) ─────────────────────────────

    func addSttPins() throws {
        let u = share.appendingPathComponent("installer/pins.conf")
        let text = try String(contentsOf: u, encoding: .utf8) + """

        WHISPERCPP_VERSION=1.9.5
        WHISPERCPP_COMMIT=\(String(repeating: "d", count: 40))
        STT_MODEL_NAME=small.en
        STT_MODEL_URL=https://example.org/x/resolve/\(String(repeating: "e", count: 40))/ggml-small.en.bin
        STT_MODEL_SHA256=\(sha(blobs["stt"]!))
        STT_MODEL_BYTES=10
        """
        try text.write(to: u, atomically: true, encoding: .utf8)
    }

    func placeWhisperServer() throws {
        let p = layout.sttProgram
        try FileManager.default.createDirectory(at: p.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: p.path, contents: Data("#!/bin/sh\n".utf8), attributes: [.posixPermissions: 0o755])
    }

    func testNoSpeechPinsMeansNoSpeechService() throws {
        try placeWhisperServer()
        var o = try options()
        o.ports.stt = 6395
        try installer(o).run()
        XCTAssertNil(launchd.loaded[Labels.stt])
        let rec = try XCTUnwrap(try RecordStore.load(layout.installRecord))
        XCTAssertNil(rec.sttURL)
        let prov = try XCTUnwrap(runner.ran("limactl", "shell").first { $0.arguments.contains("bash") })
        XCTAssertFalse(prov.arguments.contains("--host-stt-url"), "the core keeps its own Whisper")
    }

    func testTheUserModeInstallsNoSpeechServiceWithoutAPort() throws {
        try addSttPins()
        try placeWhisperServer()
        try installer(options()).run()
        XCTAssertNil(launchd.loaded[Labels.stt])
        XCTAssertNil(try RecordStore.load(layout.installRecord)?.sttURL)
    }

    func testAPackageWithoutWhisperServerInstallsNone() throws {
        try addSttPins()
        var o = try options()
        o.ports.stt = 6395
        try installer(o).run()
        XCTAssertNil(launchd.loaded[Labels.stt])
        XCTAssertTrue(progress.events.contains { $0.0 == "stt" && $0.1 == "done" && $0.2.contains("no whisper-server") })
    }

    func testTheSpeechServiceIsInstalledHandedToProvisionAndRemoved() throws {
        try addSttPins()
        try placeWhisperServer()
        var o = try options()
        o.ports.stt = 6395
        try installer(o).run()
        let l = layout
        XCTAssertEqual(launchd.loaded[Labels.stt]?.running, true)
        XCTAssertEqual(launchd.bootstrapped.prefix(2), [Labels.ollama, Labels.stt], "Ollama, then speech, before the VM")
        let model = l.sttModels.appendingPathComponent("ggml-small.en.bin")
        XCTAssertEqual(try Data(contentsOf: model), blobs["stt"])
        XCTAssertFalse(contents(l.cache).contains("ggml-small.en.bin"), "the model lives in its own folder, not the cache")
        let plist = try String(contentsOf: l.plist(Labels.stt), encoding: .utf8)
        for want in [l.sttProgram.path, model.path, "<string>127.0.0.1</string>", "<string>6395</string>", "stt.log"] {
            XCTAssertTrue(plist.contains(want), want)
        }
        XCTAssertFalse(plist.contains("--convert"), "no ffmpeg, no temporary files")
        let rec = try XCTUnwrap(try RecordStore.load(l.installRecord))
        XCTAssertEqual(rec.sttURL, "http://host.lima.internal:6395")
        XCTAssertEqual(rec.sttModel, "small.en")
        XCTAssertTrue(rec.has(.tree, path: l.sttModels.path))
        let prov = try XCTUnwrap(runner.ran("limactl", "shell").first { $0.arguments.contains("bash") })
        XCTAssertNotNil(prov.arguments.firstRange(of: ["--host-stt-url", "http://host.lima.internal:6395", "--host-stt-model", "small.en"]))

        // Keep data keeps the model (like the AI models); the job goes.
        let r = try uninstall(everything: false)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertNil(launchd.loaded[Labels.stt])
        XCTAssertFalse(FileManager.default.fileExists(atPath: l.plist(Labels.stt).path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: model.path))
    }

    func testARemoveEverythingTakesTheSpeechModelToo() throws {
        try addSttPins()
        try placeWhisperServer()
        var o = try options()
        o.ports.stt = 6395
        try installer(o).run()
        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.sttModels.path))
        XCTAssertNil(launchd.loaded[Labels.stt])
    }

    func testASpeechServiceThatDoesntAnswerDoesntStopTheInstall() throws {
        try addSttPins()
        try placeWhisperServer()
        var o = try options()
        o.ports.stt = 6395
        let inst = try installer(o)
        inst.sttWait = 4
        let handler = http.handler
        http.handler = { url in url.hasSuffix(":6395/health") ? nil : handler?(url) }
        try inst.run()
        XCTAssertTrue(progress.events.contains { $0.0 == "stt" && $0.1 == "done" && $0.2.contains("isn't answering yet") })
        XCTAssertEqual(try RecordStore.load(layout.installRecord)?.sttURL, "http://host.lima.internal:6395",
                       "the core is pointed at it all the same: it falls back, and Health says so")
    }
}

extension InstallerTests {
    /// Security review round 2: the uninstall's own busy lock (the real one,
    /// with its status file) must not leave state/busy.json, and with it
    /// state/ and the Domovoi folder, behind a remove-everything run.
    func testRemoveEverythingUnderTheRealBusyLockLeavesNothing() throws {
        let macOS = try systemRoot()
        try installer(options()).run()
        let held = try BusyLock(layout.busyLock, what: "uninstall")
        XCTAssertTrue(FileManager.default.fileExists(atPath: layout.busyStatus.path))
        let r = try uninstall(everything: true)
        XCTAssertTrue(r.ok, "\(r.failed)")
        XCTAssertTrue(r.left.isEmpty, "\(r.left)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.appSupport.path), "the Domovoi folder is gone")
        XCTAssertEqual(everything(), macOS)
        withExtendedLifetime(held) {}
    }
}
