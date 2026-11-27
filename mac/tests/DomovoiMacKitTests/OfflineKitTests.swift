import CryptoKit
import XCTest
@testable import DomovoiMacKit

/// The Mac's check of an offline kit (OfflineKit.swift, phase P4): finding
/// it, the trust chain (checksums, the release signature with the pinned
/// key, the package's own release), the parts, and reading files off it.
final class OfflineKitTests: XCTestCase {
    var base: URL!
    var volumes: URL!
    var root: URL!
    let models = KitModelsFixture()
    let macFiles = ["ubuntu-26.04-server-cloudimg-arm64.img": Data("ubuntu image".utf8), "ollama-darwin.tgz": Data("ollama tarball".utf8),
                    "ggml-small.en.bin": Data("ggml model".utf8)]
    var kit: KitFixture!

    override func setUpWithError() throws {
        base = try tempDir()
        volumes = base.appendingPathComponent("Volumes")
        try FileManager.default.createDirectory(at: volumes.appendingPathComponent("Macintosh HD"), withIntermediateDirectories: true)
        root = volumes.appendingPathComponent("DOMOVOIKIT")
        kit = KitFixture.standard(macFiles: macFiles, models: models)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: base) }

    func pins() throws -> Pins {
        try Pins.parse("""
        LIMA_VERSION=2.2.1
        LIMA_URL=https://example.org/v2.2.1/lima-2.2.1-Darwin-arm64.tar.gz
        LIMA_SHA256=\(String(repeating: "1", count: 64))
        OLLAMA_VERSION=0.34.4
        OLLAMA_URL=https://example.org/v0.34.4/ollama-darwin.tgz
        OLLAMA_SHA256=\(KitFixture.sha(macFiles["ollama-darwin.tgz"]!))
        OLLAMA_MIN_VERSION=0.12.7
        UBUNTU_IMAGE_URL=https://example.org/ubuntu-26.04-server-cloudimg-arm64.img
        UBUNTU_IMAGE_SHA256=\(KitFixture.sha(macFiles["ubuntu-26.04-server-cloudimg-arm64.img"]!))
        WHISPERCPP_VERSION=1.9.5
        WHISPERCPP_COMMIT=\(String(repeating: "d", count: 40))
        STT_MODEL_NAME=small.en
        STT_MODEL_URL=https://example.org/resolve/\(String(repeating: "5", count: 40))/ggml-small.en.bin
        STT_MODEL_SHA256=\(KitFixture.sha(macFiles["ggml-small.en.bin"]!))
        STT_MODEL_BYTES=10
        """)
    }

    func want(tier: Tier = .floor) throws -> KitExpectation {
        KitExpectation(commit: kit.commit, version: "1.5.0", key: kit.publicKey, pins: try pins(), modelPins: ModelPins.parse(models.conf), tier: tier)
    }

    func refused(_ w: KitExpectation? = nil, _ words: String, file: StaticString = #filePath, line: UInt = #line) throws {
        let (r, src) = OfflineKit.check(root: root, want: try w ?? want(), volumes: volumes)
        XCTAssertNil(src, "refused", file: file, line: line)
        XCTAssertFalse(r.ok, file: file, line: line)
        XCTAssertTrue(r.problems.joined(separator: " ").contains(words), "\(r.problems)", file: file, line: line)
    }

    // ─── detection ─────────────────────────────────────────────────────

    func testAKitIsFoundOnAnyVolumeWhateverItsName() throws {
        XCTAssertEqual(OfflineKit.find(volumes: volumes), [])
        XCTAssertNil(OfflineKit.locate(given: nil, volumes: volumes, commit: kit.commit))
        let renamed = volumes.appendingPathComponent("Grandma's stick")
        try kit.write(to: renamed)
        XCTAssertEqual(OfflineKit.find(volumes: volumes).map(\.lastPathComponent), ["Grandma's stick"], "the label is not relied on")
        // A kit.json that is a folder (or a link) is no kit.
        try FileManager.default.createDirectory(at: volumes.appendingPathComponent("Other/kit.json"), withIntermediateDirectories: true)
        XCTAssertEqual(OfflineKit.find(volumes: volumes).count, 1)
        // Two kits: the one of this package's release wins.
        let other = KitFixture.standard(macFiles: macFiles, models: models)
        other.commit = String(repeating: "e", count: 40)
        try other.write(to: volumes.appendingPathComponent("AAA"))
        XCTAssertEqual(OfflineKit.locate(given: nil, volumes: volumes, commit: kit.commit)?.lastPathComponent, "Grandma's stick")
        XCTAssertEqual(OfflineKit.locate(given: nil, volumes: volumes, commit: nil)?.lastPathComponent, "AAA")
        // --kit DIR: that folder, as given.
        XCTAssertEqual(OfflineKit.locate(given: "/somewhere/kit/", volumes: volumes, commit: nil)?.path, "/somewhere/kit")
    }

    func testAGoodKitChecksOut() throws {
        try kit.write(to: root)
        let (r, src) = OfflineKit.check(root: root, want: try want(), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        let k = try XCTUnwrap(src)
        XCTAssertEqual(r.volume, "DOMOVOIKIT")
        XCTAssertEqual(r.version, "1.5.0")
        XCTAssertTrue(r.ai)
        XCTAssertEqual(r.headline, "Installing from the offline kit on DOMOVOIKIT, Domovoi 1.5.0: nothing is downloaded")
        XCTAssertEqual(k.tier, .floor)
        XCTAssertEqual(k.models.map(\.tag), ["llama3.2:3b-instruct-q4_K_M", "qwen3:4b-instruct-2507-q4_K_M", "qwen3-vl:2b-instruct-q4_K_M"])
        // A kit in a folder (--kit) is no volume's top.
        let folder = base.appendingPathComponent("copy/kit")
        try kit.write(to: folder)
        let (r2, _) = OfflineKit.check(root: folder, want: try want(), volumes: volumes)
        XCTAssertTrue(r2.ok, "\(r2.problems)")
        XCTAssertNil(r2.volume)
        XCTAssertTrue(r2.headline.contains("on \(folder.path)"))
    }

    func testNoKitThere() throws {
        try refused(nil, "There is no offline kit at \(root.path): no kit.json there")
    }

    // ─── the signature ─────────────────────────────────────────────────

    func testASignatureByAnotherKeyIsRefused() throws {
        kit.signer = Curve25519.Signing.PrivateKey()
        try kit.write(to: root)
        try refused(nil, "This kit is not a Domovoi release signed with Domovoi's release key (it is signed with a key other than Domovoi's release key)")
    }

    func testAChangedManifestIsRefusedEvenWhenReSummed() throws {
        try kit.write(to: root)
        let m = root.appendingPathComponent(KitNames.manifestPath)
        var d = try Data(contentsOf: m)
        d.append(contentsOf: Array(" ".utf8))
        try d.write(to: m)
        // Damage first: SHA256SUMS catches a bad copy.
        try refused(nil, "This stick has a damaged file: \(KitNames.manifestPath).")
        // Someone who sums it again is caught by the signature.
        try KitFixture.resum(root, [KitNames.manifestPath])
        try refused(nil, "its signature doesn't match the file")
    }

    func testTheWrongNamespaceAndNoKeyAreRefused() throws {
        kit.namespace = MacReleaseNames.packageNamespace
        try kit.write(to: root)
        try refused(nil, "its signature is for something else")
        var w = try want()
        w.key = nil
        try refused(w, "This Setup has no release key built in")
    }

    func testTheSignedReleaseMustBeKitJsons() throws {
        kit.manifestCommit = String(repeating: "f", count: 40)
        try kit.write(to: root)
        try refused(nil, "kit.json names v1.5.0 (commit cccccccccccc), but its signed release is v1.5.0 (commit ffffffffffff)")
    }

    // ─── the release: this package's, never older ──────────────────────

    func testAnOlderKitIsRefused() throws {
        kit.version = "1.4.9"; kit.tag = "v1.4.9"; kit.commit = String(repeating: "b", count: 40)
        try kit.write(to: root)
        var w = try want()
        w.commit = String(repeating: "c", count: 40)
        try refused(w, "This kit holds Domovoi 1.4.9, older than 1.5.0, and a kit never takes Domovoi back to an older release")
        w.repair = true
        try refused(w, "older than 1.5.0")
    }

    func testANewerKitOrAnotherBuildIsRefused() throws {
        kit.version = "1.5.1"; kit.tag = "v1.5.1"; kit.commit = String(repeating: "d", count: 40)
        try kit.write(to: root)
        var w = try want()
        w.commit = String(repeating: "c", count: 40)
        try refused(w, "This kit holds Domovoi 1.5.1, and this Setup installs Domovoi 1.5.0: they don't go together. Open the DomovoiSetup-mac.pkg on the stick")
        w.repair = true
        try refused(w, "A repair uses only a kit of the release this Mac runs")
        // The same version from another commit.
        kit.version = "1.5.0"; kit.tag = "v1.5.0"
        try kit.write(to: root)
        w.repair = false
        try refused(w, "This kit holds Domovoi 1.5.0 at commit dddddddddddd, and this Setup installs Domovoi 1.5.0 from commit cccccccccccc")
    }

    func testAKitForAnotherMachineOrANewerFormatIsRefused() throws {
        kit.targets = ["pc-x86_64"]
        try kit.write(to: root)
        try refused(nil, "This kit was made for a Windows or Linux PC only. A Mac needs a kit with \"A Mac with an Apple chip\" ticked")
        kit.targets = ["pc-x86_64", "mac-arm64"]
        kit.format = 2
        try kit.write(to: root)
        try refused(nil, "This kit was made by a newer Domovoi (kit format 2; this Setup reads format 1)")
    }

    // ─── the parts ─────────────────────────────────────────────────────

    func testMissingPartsAreNamed() throws {
        kit.remove(kind: "mac-part", name: "ollama-darwin.tgz")
        kit.remove(kind: "python-wheels")
        kit.remove(kind: "ollama-model", name: "qwen3:4b-instruct-2507-q4_K_M")
        try kit.write(to: root)
        try refused(nil, "This kit is missing Ollama for macOS. Make a new kit from an up-to-date house.")
        try refused(nil, "This kit is missing what Setup needs: the Python packages for aarch64 (python/cp314-aarch64); the AI model qwen3:4b-instruct-2507-q4_K_M")
    }

    func testAPartThatIsNotThePinnedOneIsRefused() throws {
        kit.components = kit.components.map { c in
            var c = c
            if c.name == "ollama-darwin.tgz" { c.files[0].data = Data("another Ollama".utf8) }
            return c
        }
        try kit.write(to: root)
        try refused(nil, "This kit's mac/ollama-darwin.tgz isn't the one this Setup's release pins")
    }

    func testAKitWithoutAIOrVisionOrTheStandardModels() throws {
        // No vision model: noted, not refused.
        kit.remove(kind: "ollama-model", name: "qwen3-vl:2b-instruct-q4_K_M")
        try kit.write(to: root)
        var (r, src) = OfflineKit.check(root: root, want: try want(), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        XCTAssertTrue(r.notes.joined().contains("no model for pictures in chat"))
        XCTAssertEqual(src?.models.count, 2)
        // A 24 GB Mac (standard) from a floor house's kit: the floor's models.
        kit.remove(kind: "ollama-model", name: "qwen3:8b-q4_K_M")
        try kit.write(to: root)
        (r, src) = OfflineKit.check(root: root, want: try want(tier: .standard), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        XCTAssertEqual(src?.tier, .floor)
        XCTAssertEqual(r.tier, "floor")
        // No AI ticked: installs without the models.
        kit.includes = []
        kit.remove(kind: "ollama-model")
        try kit.write(to: root)
        (r, src) = OfflineKit.check(root: root, want: try want(tier: .standard), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        XCTAssertFalse(r.ai)
        XCTAssertEqual(src?.models, [])
        XCTAssertTrue(r.notes.joined().contains("The kit has no AI models"))
        // Every kit carries Ollama for macOS, AI ticked or not (1.5.0's kit
        // builder): one without it is broken, whatever its AI answer.
        kit.remove(kind: "mac-part", name: "ollama-darwin.tgz")
        try kit.write(to: root)
        try refused(try want(), "This kit is missing Ollama for macOS. Make a new kit from an up-to-date house.")
    }

    func testTheHouseholdsOwnOllamaCantTakeAKit() throws {
        try kit.write(to: root)
        var w = try want()
        w.ownOllama = false
        try refused(w, "An install from an offline kit runs Domovoi's own Ollama")
    }

    // ─── what is on the stick ──────────────────────────────────────────

    func testWhatAKitNeverHoldsIsRefusedAndTheSystemsLitterLetBe() throws {
        try kit.write(to: root)
        let fm = FileManager.default
        // A Mac's Finder and Spotlight leave these: fine.
        try Data("x".utf8).write(to: root.appendingPathComponent(".DS_Store"))
        try Data("x".utf8).write(to: root.appendingPathComponent("mac/._ollama-darwin.tgz"))
        try fm.createDirectory(at: root.appendingPathComponent(".Spotlight-V100/Store"), withIntermediateDirectories: true)
        try Data("x".utf8).write(to: root.appendingPathComponent(".Spotlight-V100/Store/db"))
        var (r, _) = OfflineKit.check(root: root, want: try want(), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        // Anything else is not.
        try Data("x".utf8).write(to: root.appendingPathComponent("mac/extra.sh"))
        try fm.createSymbolicLink(atPath: root.appendingPathComponent("speech/link").path, withDestinationPath: "/etc")
        try fm.createDirectory(at: root.appendingPathComponent("apt/arm64/ubuntu/dists/Bad Name"), withIntermediateDirectories: true)
        (r, _) = OfflineKit.check(root: root, want: try want(), volumes: volumes)
        let words = r.problems.joined(separator: " ")
        XCTAssertTrue(words.hasPrefix("This stick holds what a kit never does, so Setup used nothing from it."), words)
        XCTAssertTrue(words.contains("mac/extra.sh is not part of the kit (kit.json doesn't list it)."), words)
        XCTAssertTrue(words.contains("speech/link is a link, which a kit never holds."), words)
        XCTAssertTrue(words.contains("The package folder apt/arm64/ubuntu/dists/Bad Name/ has a name a kit never uses."), words)
        // The system's own folders count only at a volume's top.
        let folder = base.appendingPathComponent("copy/kit")
        try kit.write(to: folder)
        try fm.createDirectory(at: folder.appendingPathComponent(".Spotlight-V100"), withIntermediateDirectories: true)
        try Data("x".utf8).write(to: folder.appendingPathComponent(".Spotlight-V100/db"))
        (r, _) = OfflineKit.check(root: folder, want: try want(), volumes: volumes)
        XCTAssertTrue(r.problems.joined().contains(".Spotlight-V100/db is not part of the kit"), "\(r.problems)")
    }

    // ─── reading files off the stick ───────────────────────────────────

    func testASplitFileIsJoinedAndEveryPieceChecked() throws {
        kit.partSize = 16
        try kit.write(to: root)
        let layerPath = "ollama/models/blobs/sha256-\(KitFixture.sha(models.model("qwen3:4b-instruct-2507-q4_K_M").blobs[1]))"
        XCTAssertTrue(FileManager.default.fileExists(atPath: root.appendingPathComponent(layerPath + ".part01").path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent(layerPath).path), "stored as parts only")
        let (r, src) = OfflineKit.check(root: root, want: try want(), volumes: volumes)
        XCTAssertTrue(r.ok, "\(r.problems)")
        let k = try XCTUnwrap(src)
        let out = base.appendingPathComponent("out")
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let dest = out.appendingPathComponent("blob")
        try k.copy(layerPath, to: dest, sha256: KitFixture.sha(models.model("qwen3:4b-instruct-2507-q4_K_M").blobs[1]))
        XCTAssertEqual(try Data(contentsOf: dest), models.model("qwen3:4b-instruct-2507-q4_K_M").blobs[1])

        // A damaged part: named, and nothing is left behind.
        let p2 = root.appendingPathComponent(layerPath + ".part02")
        var d = try Data(contentsOf: p2)
        d[0] ^= 0xff
        try d.write(to: p2)
        try? FileManager.default.removeItem(at: dest)
        XCTAssertThrowsError(try k.copy(layerPath, to: dest, sha256: nil)) {
            XCTAssertTrue("\($0)".contains("This stick has a damaged file: \(layerPath).part02"), "\($0)")
        }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: out.path), [], "no half copy")
        // A part SHA256SUMS doesn't list.
        try kit.write(to: root)
        var sums = try KitSums.parse(String(contentsOf: root.appendingPathComponent(KitNames.sums), encoding: .utf8))
        sums[layerPath + ".part01"] = nil
        try KitFixture.writeSums(sums, root)
        let (r3, _) = OfflineKit.check(root: root, want: try want(), volumes: volumes)
        XCTAssertTrue(r3.problems.joined().contains("\(layerPath).part01 (SHA256SUMS doesn't list it)"), "\(r3.problems)")
    }

    func testAFileThatIsNotItsPinIsNeverUsed() throws {
        try kit.write(to: root)
        let k = try XCTUnwrap(OfflineKit.check(root: root, want: try want(), volumes: volumes).1)
        let dest = base.appendingPathComponent("img")
        XCTAssertThrowsError(try k.copy("mac/ubuntu-26.04-server-cloudimg-arm64.img", to: dest, sha256: String(repeating: "0", count: 64))) {
            XCTAssertTrue("\($0)".contains("isn't the one this Setup's release pins"), "\($0)")
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: dest.path))
        // Changed on the stick after the check (a hash mismatch).
        try Data("ubuntu imagX".utf8).write(to: root.appendingPathComponent("mac/ubuntu-26.04-server-cloudimg-arm64.img"))
        XCTAssertThrowsError(try k.copy("mac/ubuntu-26.04-server-cloudimg-arm64.img", to: dest, sha256: nil)) {
            XCTAssertTrue("\($0)".contains("This stick has a damaged file: mac/ubuntu-26.04-server-cloudimg-arm64.img"), "\($0)")
        }
    }

    // ─── the Ollama models ─────────────────────────────────────────────

    func testTheModelsGoIntoTheMacsOllamaFolderChecked() throws {
        kit.partSize = 20
        try kit.write(to: root)
        let k = try XCTUnwrap(OfflineKit.check(root: root, want: try want(), volumes: volumes).1)
        let dir = base.appendingPathComponent("models")
        var lines: [String] = []
        let done = try KitModels.install(k.models, from: k, into: dir, log: { lines.append($0) })
        XCTAssertEqual(done, k.models.map(\.tag))
        let fm = FileManager.default
        for m in k.models {
            let fx = models.model(m.tag)
            XCTAssertEqual(try Data(contentsOf: dir.appendingPathComponent(m.manifestRelative)), fx.manifest)
            XCTAssertTrue(m.manifestRelative.hasPrefix("manifests/registry.ollama.ai/library/"))
            for b in fx.blobs { XCTAssertEqual(try Data(contentsOf: dir.appendingPathComponent("blobs/sha256-\(KitFixture.sha(b))")), b) }
        }
        XCTAssertFalse(try fm.contentsOfDirectory(atPath: dir.appendingPathComponent("blobs").path).contains { $0.hasPrefix(".") }, "no temporary files left")
        // Again: the blobs there are kept.
        lines = []
        _ = try KitModels.install(k.models, from: k, into: dir, log: { lines.append($0) })
        XCTAssertTrue(lines.allSatisfy { $0.contains(", 0 new,") }, "\(lines)")

        // A manifest that isn't the pinned build: nothing of it is copied.
        let other = base.appendingPathComponent("models2")
        var bad = k.models[1]
        bad.digest = String(repeating: "0", count: 64)
        XCTAssertThrowsError(try KitModels.install([bad], from: k, into: other, log: { _ in })) {
            XCTAssertTrue("\($0)".contains("is not the build this release pins"), "\($0)")
        }
        XCTAssertFalse(fm.fileExists(atPath: other.path))
    }

    func testModelPinsReadTheTiers() throws {
        let mp = ModelPins.parse(models.conf)
        XCTAssertEqual(mp.models(tier: .floor)?.map(\.role), ["qa", "tool", "vision"])
        XCTAssertEqual(mp.models(tier: .floor, vision: false)?.count, 2)
        XCTAssertEqual(ModelPin(tag: "qwen3:8b-q4_K_M", digest: "", role: "tool").manifestPath,
                       "ollama/models/manifests/registry.ollama.ai/library/qwen3/8b-q4_K_M")
        XCTAssertEqual(ModelPin(tag: "alex/model:v1", digest: "", role: "tool").manifestPath,
                       "ollama/models/manifests/registry.ollama.ai/alex/model/v1")
        XCTAssertNil(ModelPins.parse("tier_floor_qa_model=x\n").models(tier: .floor))
        // The real file has both tiers (the package carries it).
        let real = try XCTUnwrap(ModelPins.load(share: macDir))
        XCTAssertEqual(real.models(tier: .floor)?.count, 3)
        XCTAssertNotNil(real.models(tier: .standard))
    }

    func testKitJSONAndSumsReading() throws {
        XCTAssertThrowsError(try KitSums.parse(""))
        XCTAssertThrowsError(try KitSums.parse("\(String(repeating: "a", count: 64))  ../etc/passwd\n"))
        XCTAssertThrowsError(try KitSums.parse("\(String(repeating: "a", count: 64)) x\n"))
        XCTAssertEqual(try KitSums.parse("\(String(repeating: "a", count: 64))  a/b\r\n\n\(String(repeating: "b", count: 64)) *c\n"),
                       ["a/b": String(repeating: "a", count: 64), "c": String(repeating: "b", count: 64)])
        for bad in ["", "/abs", "a/../b", "a//b", "a\\b", "c:x", "a/./b", "a\u{1}b"] { XCTAssertFalse(KitIndex.safePath(bad), bad) }
        XCTAssertThrowsError(try KitIndex.parse(Data("[]".utf8)))
    }

    // ─── what the run does with one ────────────────────────────────────

    func testResolveStopsAnInstallAndLetsARepairGoOnWithout() throws {
        kit.targets = ["pc-x86_64"]
        try kit.write(to: root)
        var lines: [String] = []
        XCTAssertThrowsError(try KitSetup.resolve(.auto, want: try want(), volumes: volumes, log: { lines.append($0) })) {
            XCTAssertTrue("\($0)".contains("made for a Windows or Linux PC only"), "\($0)")
        }
        var w = try want()
        w.repair = true
        XCTAssertNil(try KitSetup.resolve(.auto, want: w, volumes: volumes, log: { lines.append($0) }))
        XCTAssertTrue(lines.contains { $0.hasPrefix("note: the offline kit at \(root.path) is not used:") }, "\(lines)")
        // Asked for by name, it must be usable, repair or not.
        XCTAssertThrowsError(try KitSetup.resolve(.path(root.path), want: w, volumes: volumes, log: { _ in }))
        XCTAssertNil(try KitSetup.resolve(.off, want: w, volumes: volumes, log: { _ in }))
        kit.targets = ["mac-arm64"]
        try kit.write(to: root)
        let k = try KitSetup.resolve(.auto, want: try want(), volumes: volumes, log: { lines.append($0) })
        XCTAssertNotNil(k)
        XCTAssertTrue(lines.last?.contains("offline kit: Domovoi 1.5.0 (v1.5.0, commit cccccccccccc)") == true, "\(lines)")
    }

    func testTheSetupPagesWords() throws {
        try kit.write(to: root)
        let r = OfflineKit.check(root: root, want: try want(), volumes: volumes).0
        XCTAssertTrue(KitText.consent(r).hasPrefix("Copies Domovoi 1.5.0 from the offline kit on DOMOVOIKIT instead of downloading it"))
        XCTAssertTrue(KitText.internetQuestion(r).contains("A house with no internet answers No."))
        XCTAssertTrue(KitText.repair(r).hasPrefix("Repair uses the offline kit on DOMOVOIKIT (Domovoi 1.5.0)"))
        var bad = r
        bad.problems = ["Nope."]
        XCTAssertEqual(KitText.repair(bad), "The offline kit on DOMOVOIKIT isn't used: Nope.")
        XCTAssertEqual(bad.headline, "The offline kit on DOMOVOIKIT can't be used: Nope.")
        // The port sheet: from a kit, the household's Ollama isn't offered.
        let c = PortConflict(owner: "Ollama", ports: [11434], program: nil, pid: nil, ollamaVersion: "0.30.0", ollamaUsable: true, ollamaIsApp: false)
        XCTAssertTrue(c.sheetText.contains("Domovoi can use it instead"))
        XCTAssertTrue(c.sheetText(offlineKit: true).contains("An install from an offline kit runs Domovoi's own Ollama"))
        XCTAssertFalse(c.sheetText(offlineKit: true).contains("can use it instead"))
    }
}
