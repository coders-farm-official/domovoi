import CryptoKit
import XCTest
@testable import DomovoiMacKit

/// The install from an offline kit (phase P4), through the same fakes as
/// the rest of InstallerTests: launchd, limactl, curl and tar answered,
/// real files in a temporary prefix.
extension InstallerTests {
    var kitModels: KitModelsFixture { KitModelsFixture() }

    /// A kit for this test's pins (InstallerTests' blobs), on a fake volume,
    /// and the package's model pins and Lima seed beside the share.
    func kitSetUp(_ change: (KitFixture) -> Void = { _ in }) throws -> (KitFixture, URL, URL) {
        let base = prefix.deletingLastPathComponent()
        let volumes = base.appendingPathComponent("Volumes")
        let k = KitFixture.standard(macFiles: ["ubuntu-26.04-server-cloudimg-arm64.img": blobs["ubuntu"]!, "ollama-darwin.tgz": blobs["ollama"]!],
                                    models: kitModels)
        change(k)
        let root = try k.write(to: volumes.appendingPathComponent("DOMOVOIKIT"))
        try kitModels.conf.write(to: share.appendingPathComponent(ModelPins.file), atomically: true, encoding: .utf8)
        return (k, root, volumes)
    }

    func kitWant(_ k: KitFixture, tier: Tier = .floor) throws -> KitExpectation {
        KitExpectation(commit: k.commit, version: k.version, key: k.publicKey, pins: try Pins.load(share.appendingPathComponent("installer/pins.conf")),
                       modelPins: ModelPins.load(share: share), tier: tier)
    }

    /// Install options from a kit: Lima from the package's seed folder (as
    /// the system install carries it), everything else from the stick.
    func kitOptions(_ k: KitFixture, _ root: URL, _ volumes: URL, ollama: OllamaChoice = .own) throws -> InstallOptions {
        var o = try options(ollama: ollama)
        let seed = prefix.deletingLastPathComponent().appendingPathComponent("seed")
        try FileManager.default.createDirectory(at: seed, withIntermediateDirectories: true)
        try blobs["lima"]!.write(to: seed.appendingPathComponent("lima-2.2.1-Darwin-arm64.tar.gz"))
        o.seedCache = [seed]
        let (r, src) = OfflineKit.check(root: root, want: try kitWant(k), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        o.kit = src
        return o
    }

    func provisionCall() -> Command? { runner.ran("limactl", "shell").first { $0.arguments.contains("bash") } }
    func edits() -> [String] { runner.ran("limactl", "edit").map { $0.arguments[$0.arguments.firstIndex(of: "--set")! + 1] } }

    // ─── tests ─────────────────────────────────────────────────────────

    func testAnInstallFromAKitReachesNothingOutsideTheMac() throws {
        let (k, root, volumes) = try kitSetUp()
        try installer(kitOptions(k, root, volumes)).run()
        XCTAssertEqual(progress.events.filter { $0.1 == "done" }.map(\.0), Installer.steps)

        // Nothing downloaded on the Mac, no egress probe in the VM.
        XCTAssertEqual(runner.ran("curl"), [], "no download")
        XCTAssertFalse(runner.ran("limactl", "shell").contains { $0.arguments.contains("curl") }, "no internet check in the VM")
        XCTAssertTrue(progress.lines.contains { $0.contains("offline kit: no internet check in the VM") })
        // The pinned files came off the stick (Lima from the package's seed).
        XCTAssertEqual(try Data(contentsOf: layout.cache.appendingPathComponent("ubuntu-26.04-server-cloudimg-arm64.img")), blobs["ubuntu"])
        XCTAssertEqual(try Data(contentsOf: layout.cache.appendingPathComponent("ollama-darwin.tgz")), blobs["ollama"])
        XCTAssertTrue(progress.lines.contains { $0.contains("took ollama-darwin.tgz from the offline kit") }, "\(progress.lines)")
        // The models are in the Mac's own Ollama folder, at their pins.
        for m in kitModels.models where m.tier == "floor" {
            XCTAssertEqual(try Data(contentsOf: layout.ollamaModels.appendingPathComponent(m.pin.manifestRelative)), m.manifest, m.tag)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: layout.ollamaModels.appendingPathComponent(kitModels.model("qwen3:8b-q4_K_M").pin.manifestRelative).path),
                       "only the tier's models")

        // Lima shares the stick in read-only, provision.sh reads it there.
        let yaml = try String(contentsOf: layout.renderedVMConfig, encoding: .utf8)
        XCTAssertTrue(yaml.contains("- location: \"\(root.path)\"\n  mountPoint: \"/mnt/domovoi-kit\"\n  writable: false"), yaml)
        let prov = try XCTUnwrap(provisionCall())
        XCTAssertNotNil(prov.arguments.firstRange(of: ["--offline-kit", "/mnt/domovoi-kit"]))
        XCTAssertFalse(prov.arguments.contains("--no-ai-models"))
        XCTAssertNotNil(prov.arguments.firstRange(of: ["--ollama-url", "http://host.lima.internal:11500"]))
        XCTAssertTrue(runner.ran("limactl", "shell").contains { $0.arguments.contains("/mnt/domovoi-kit/kit.json") },
                      "the VM sees the kit before provision.sh runs")

        // Then the kit comes out of the VM's settings, and the VM starts again.
        XCTAssertEqual(edits(), [Lima.kitMountExpression(nil)])
        let order = runner.calls.map { $0.arguments.contains("bash") ? "provision" : (($0.executable as NSString).lastPathComponent == "limactl" ? $0.arguments.first ?? "" : "") }
        XCTAssertLessThan(order.firstIndex(of: "provision")!, order.firstIndex(of: "edit")!)
        XCTAssertEqual(launchd.loaded[Labels.vm]?.running, true, "the VM runs again, without the stick")
        let rec = try XCTUnwrap(try RecordStore.load(layout.installRecord))
        XCTAssertNil(rec.kitMount)
        XCTAssertEqual(rec.installedFromKit, "1.5.0 v1.5.0 \(k.commit)")
    }

    func testTheKitMountIsOnlyThereInKitMode() throws {
        // Online: no kit mount, no edit.
        try installer(options()).run()
        XCTAssertFalse(try String(contentsOf: layout.renderedVMConfig, encoding: .utf8).contains("/mnt/domovoi-kit"))
        XCTAssertEqual(edits(), [])
        let p = try XCTUnwrap(provisionCall())
        XCTAssertFalse(p.arguments.contains("--offline-kit"))
        // A repair from a kit: the existing VM gets the mount, stopped first,
        // and loses it again after provisioning.
        let (k, root, volumes) = try kitSetUp()
        runner.calls = []
        try installer(kitOptions(k, root, volumes)).run()
        XCTAssertEqual(edits(), [Lima.kitMountExpression(root.path), Lima.kitMountExpression(nil)])
        XCTAssertEqual(runner.ran("limactl", "create").count, 0, "the VM is kept")
        let names = runner.calls.map { c -> String in
            if c.executable == LaunchdInstaller.launchctl { return "launchctl " + (c.arguments.first ?? "") }
            if c.arguments.contains("bash") { return "provision" }
            return ((c.executable as NSString).lastPathComponent) + " " + (c.arguments.first ?? "")
        }
        let firstEdit = names.firstIndex(of: "limactl edit")!
        XCTAssertLessThan(names.firstIndex(of: "launchctl bootout")!, firstEdit, "the VM's job is stopped before Lima edits it")
        XCTAssertLessThan(firstEdit, names.firstIndex(of: "provision")!)
        XCTAssertNotNil(try XCTUnwrap(provisionCall()).arguments.firstRange(of: ["--offline-kit", "/mnt/domovoi-kit"]))
        XCTAssertNil(try RecordStore.load(layout.installRecord)?.kitMount)
    }

    func testAMountLeftBehindIsTakenOutByTheNextRun() throws {
        let (k, root, volumes) = try kitSetUp()
        // provision.sh fails, and so does taking the kit out of the VM.
        provisionResult = "failed"
        runner.on { c in
            (c.executable as NSString).lastPathComponent == "limactl" && c.arguments.first == "edit" ? CommandResult(status: 1, stderr: "edit failed") : nil
        }
        XCTAssertThrowsError(try installer(kitOptions(k, root, volumes)).run())
        XCTAssertTrue(progress.lines.contains { $0.contains("taking the offline kit out of the VM failed") }, "\(progress.lines)")
        XCTAssertEqual(try RecordStore.load(layout.installRecord)?.kitMount, root.path, "the record knows the mount is still there")
        // The stick is gone; a repair without it takes the mount out before
        // the VM starts.
        runner.handlers.removeLast()
        provisionResult = "ok"
        runner.calls = []
        try installer(options()).run()
        XCTAssertEqual(edits(), [Lima.kitMountExpression(nil)])
        XCTAssertNil(try RecordStore.load(layout.installRecord)?.kitMount)
        XCTAssertFalse(try XCTUnwrap(provisionCall()).arguments.contains("--offline-kit"))
    }

    func testAKitWithoutAIInstallsWithoutTheModels() throws {
        let (k, root, volumes) = try kitSetUp { k in
            k.includes = []
            k.remove(kind: "ollama-model")
        }
        try installer(kitOptions(k, root, volumes)).run()
        XCTAssertTrue(try XCTUnwrap(provisionCall()).arguments.contains("--no-ai-models"))
        XCTAssertEqual((try? FileManager.default.contentsOfDirectory(atPath: layout.ollamaModels.path)) ?? [], [])
    }

    func testAKitNeverTakesTheHouseholdsOllamaOrTheInternet() throws {
        let (k, root, volumes) = try kitSetUp()
        let o = try kitOptions(k, root, volumes, ollama: .existing(port: 11500))
        XCTAssertThrowsError(try o.check()) { XCTAssertTrue("\($0)".contains("An install from an offline kit runs Domovoi's own Ollama"), "\($0)") }
        // A pinned file neither the seeds nor the kit hold: never downloaded.
        let src = try XCTUnwrap(o.kit)
        let dl = Downloader(runner: runner, cache: prefix, kit: src)
        XCTAssertThrowsError(try dl.ensure(Pin(url: "https://example.org/v2.2.1/lima-2.2.1-Darwin-arm64.tar.gz", sha256: sha(blobs["lima"]!)))) {
            XCTAssertTrue("\($0)".contains("would be downloaded, and this install is from an offline kit: nothing may (the kit has no mac/lima-2.2.1-Darwin-arm64.tar.gz)"), "\($0)")
        }
        XCTAssertEqual(runner.ran("curl"), [])
    }

    func testTheChecksPageAsksNothingOfTheInternetWithAKit() throws {
        let (k, root, volumes) = try kitSetUp()
        // The package's build.json and pinned key, as the system install has them.
        try #"{"schema":1,"version":"1.5.0","commit":"\#(k.commit)","repo_url":"https://example.org/domovoi.git"}"#
            .write(to: share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        let line = "ssh-ed25519 " + (KitFixture.sshString(Data("ssh-ed25519".utf8)) + KitFixture.sshString(k.publicKey)).base64EncodedString() + " test\n"
        try line.write(to: share.appendingPathComponent(MacReleaseNames.keyFile), atomically: true, encoding: .utf8)
        let report = try XCTUnwrap(KitPreflight.report(share: share, layout: layout, volumes: volumes, trust: { _ in true }, memoryBytes: 32 << 30))
        XCTAssertTrue(report.ok, "\(report.problems)")
        XCTAssertEqual(report.root, root.path)
        XCTAssertEqual(report.tier, "standard", "a 32 GB Mac with the standard models on the stick")

        let pre = Preflight(layout: layout, runner: runner, http: http)
        var asked: [String] = []
        pre.reachable = { asked.append($0); return true }
        pre.portInUse = { _ in false }
        pre.lan = { LanAddress(ipv4: "192.168.1.20", interface: "en0") }
        pre.isWiFi = { _ in false }
        pre.sysctl = { $0 == "machdep.cpu.brand_string" ? "Apple M2" : "1" }
        pre.freeDiskBytes = { 200_000_000_000 }
        pre.pfConfText = { FakePf.stockPfConf }
        let pins = try Pins.load(share.appendingPathComponent("installer/pins.conf"))
        let r = pre.run(pins: pins, ollamaMinimum: nil, kit: report)
        XCTAssertEqual(asked, [], "no request to the download sites")
        let row = try XCTUnwrap(r.checks.first { $0.id == "kit" })
        XCTAssertEqual(row.status, "ok")
        XCTAssertEqual(row.detail, "Installing from the offline kit on DOMOVOIKIT, Domovoi 1.5.0: nothing is downloaded")
        XCTAssertNil(r.checks.first { $0.id == "internet" })
        XCTAssertEqual(r.kit, report)
        // Without a kit, the internet is checked as before.
        _ = pre.run(pins: pins, ollamaMinimum: nil)
        XCTAssertFalse(asked.isEmpty)
        // A kit that can't be used stops a first install; a repair is warned.
        var bad = report
        bad.problems = ["No."]
        XCTAssertEqual(pre.run(pins: pins, ollamaMinimum: nil, kit: bad).checks.first { $0.id == "kit" }?.status, "fail")
        XCTAssertFalse(pre.run(pins: pins, ollamaMinimum: nil, kit: bad).ok)

        // The helper's own resolve, from --kit DIR, as root would.
        var o = try options()
        o.kitRequest = .path(root.path)
        o.tier = .standard
        try SetupCommands.resolveKit(&o, volumes: volumes, trust: { _ in true }, log: { _ in })
        XCTAssertNotNil(o.kit)
        XCTAssertEqual(o.tier, .standard)
        o.kitRequest = .off
        o.kit = nil
        try SetupCommands.resolveKit(&o, volumes: volumes, trust: { _ in true }, log: { _ in })
        XCTAssertNil(o.kit, "--no-kit: never a kit, even one plugged in")
    }

    func testTheSetupPagesHandTheHelperTheKitTheyShowed() throws {
        let withKit = SetupCommands.install(forUID: 501, codeTo: "/c", answersFile: "/a", kit: "/Volumes/DOMOVOIKIT")
        XCTAssertEqual(Array(withKit.suffix(2)), ["--kit", "/Volumes/DOMOVOIKIT"])
        XCTAssertEqual(SetupCommands.install(forUID: 501, codeTo: "/c", answersFile: nil).last, "--no-kit")
        for (argv, want) in [(["install", "--kit", "/Volumes/K"], KitSetup.Request.path("/Volumes/K")), (["install", "--no-kit"], .off), (["install"], .auto)] {
            var a = try Args(argv)
            XCTAssertEqual(try SetupCommands.kitRequest(&a), want)
        }
        var both = try Args(["install", "--kit", "/Volumes/K", "--no-kit"])
        XCTAssertThrowsError(try SetupCommands.kitRequest(&both))
        var rel = try Args(["install", "--kit", "K"])
        XCTAssertThrowsError(try SetupCommands.kitRequest(&rel))
    }
}

/// The Lima template and provision.sh's arguments in kit mode.
final class KitArgsTests: XCTestCase {
    func testTheKitMountOnlyInKitMode() throws {
        let t = try read("vm/domovoi.yaml")
        let media = [MediaMount(name: "Music", hostPath: "/Users/Shared/Domovoi/Music")]
        let plain = try LimaTemplate.render(template: t, size: VMSize(cpus: 2, memoryGiB: 6, diskGiB: 40), imagePath: "/i.img", imageSHA256: "ab",
                                            mounts: media, ports: Ports.real)
        XCTAssertFalse(plain.contains("domovoi-kit"))
        let kit = try LimaTemplate.render(template: t, size: VMSize(cpus: 2, memoryGiB: 6, diskGiB: 40), imagePath: "/i.img", imageSHA256: "ab",
                                          mounts: media, ports: Ports.real, kitMount: "/Volumes/DOMOVOIKIT")
        XCTAssertTrue(kit.contains("""
        - location: "/Users/Shared/Domovoi/Music"
          mountPoint: "/srv/media/music"
          writable: true
        - location: "/Volumes/DOMOVOIKIT"
          mountPoint: "/mnt/domovoi-kit"
          writable: false
        """), kit)
        let none = try LimaTemplate.render(template: t, size: VMSize(cpus: 2, memoryGiB: 6, diskGiB: 40), imagePath: "/i.img", imageSHA256: "ab",
                                           mounts: [], ports: Ports.real, kitMount: "/Volumes/K")
        XCTAssertTrue(none.contains("mounts:\n- location: \"/Volumes/K\""), none)
        XCTAssertThrowsError(try LimaTemplate.render(template: t, size: VMSize(cpus: 2, memoryGiB: 6, diskGiB: 40), imagePath: "/i.img", imageSHA256: "ab",
                                                     mounts: [], ports: Ports.real, kitMount: "relative"))
        // limactl edit's expression keeps the other mounts.
        XCTAssertEqual(Lima.kitMountExpression(nil), #".mounts = ((.mounts // []) | map(select(.mountPoint != "/mnt/domovoi-kit")))"#)
        XCTAssertEqual(Lima.kitMountExpression("/Volumes/My \"Kit\""),
                       #".mounts = ((.mounts // []) | map(select(.mountPoint != "/mnt/domovoi-kit"))) + [{"location": "/Volumes/My \"Kit\"", "mountPoint": "/mnt/domovoi-kit", "writable": false}]"#)
        // The Windows side mounts it at the same place.
        let win = try String(contentsOf: repoDir.appendingPathComponent("windows/common/OfflineKit.cs"), encoding: .utf8)
        XCTAssertTrue(win.contains("MountPoint = \"\(KitNames.guestMount)\""))
    }

    func testProvisionArgsFromAKit() throws {
        let ans = InstallRecord.Answers(internet: "never", tier: .floor, ollama: "own", mediaRoot: "/m", media: [], ports: Ports.real,
                                        size: VMSize(cpus: 2, memoryGiB: 6, diskGiB: 40), timezone: nil)
        let src = ProvisionSource(script: URL(fileURLWithPath: "/p.sh"), repoURL: "https://example.org/d.git", ref: String(repeating: "c", count: 40))
        let online = ProvisionArgs.build(source: src, answers: ans, ollamaURL: "http://host.lima.internal:11434")
        XCTAssertFalse(online.contains("--offline-kit"))
        XCTAssertFalse(online.contains("--no-ai-models"))
        let kit = ProvisionArgs.build(source: src, answers: ans, ollamaURL: "http://host.lima.internal:11434", offlineKit: KitNames.guestMount)
        XCTAssertNotNil(kit.firstRange(of: ["--offline-kit", "/mnt/domovoi-kit"]))
        XCTAssertNotNil(kit.firstRange(of: ["--internet", "never"]))
        XCTAssertNotNil(kit.firstRange(of: ["--ollama-url", "http://host.lima.internal:11434"]), "the Mac's Ollama, which provision.sh allows in kit mode")
        XCTAssertFalse(kit.contains("--no-ai-models"))
        XCTAssertTrue(ProvisionArgs.build(source: src, answers: ans, ollamaURL: "u", offlineKit: KitNames.guestMount, noAIModels: true).contains("--no-ai-models"))
        // provision.sh takes both, and no longer refuses a kit with --ollama-url (a Mac's VM).
        let prov = try String(contentsOf: repoDir.appendingPathComponent("scripts/linux/provision.sh"), encoding: .utf8)
        XCTAssertTrue(prov.contains("--offline-kit) OFFLINE_KIT=$val ;;"))
        XCTAssertFalse(prov.contains("is not supported yet: the offline kit installs on a PC"))
        XCTAssertTrue(prov.contains("the Mac's setup copies the kit's models into its Ollama before this runs"))
    }
}
