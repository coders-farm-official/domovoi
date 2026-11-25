import CryptoKit
import Foundation
@testable import DomovoiMacKit

/// An offline kit on disk, in the layout and kit.json shape the server's
/// writer makes (domovoi/offline_kit/write.py write_kit, the same shape
/// scripts/linux/tests/test-provision.sh make_kit writes): files split into
/// .partNN above `partSize`, SHA256SUMS over every stored file and kit.json,
/// and release/DomovoiSetup-mac.manifest.json signed, as finish_release.py
/// signs it (`ssh-keygen -Y sign`, SSHSIG, Ed25519, sha512), with a key
/// made for the test and never kept.
final class KitFixture {
    struct File {
        var path: String
        var data: Data
    }

    struct Comp {
        var kind: String
        var name: String
        var files: [File]
    }

    var version = "1.5.0"
    var tag = "v1.5.0"
    var commit = String(repeating: "c", count: 40)
    var targets = ["mac-arm64"]
    var includes = ["ai", "vision"]
    var partSize = 3_900_000_000
    var format = 1
    var components: [Comp] = []
    /// The signed manifest's own fields, when a test wants them to differ.
    var manifestTag: String?
    var manifestVersion: String?
    var manifestCommit: String?
    var namespace = MacReleaseNames.manifestNamespace
    let key = Curve25519.Signing.PrivateKey()
    var signer: Curve25519.Signing.PrivateKey?

    var publicKey: Data { key.publicKey.rawRepresentation }

    static func sha(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

    /// The pinned Mac files (as InstallerTests' pins.conf pins them), the
    /// parts provision.sh needs, and the floor and standard tiers' models.
    static func standard(macFiles: [String: Data], models: KitModelsFixture) -> KitFixture {
        let k = KitFixture()
        k.components = [
            Comp(kind: "guide", name: "start", files: [File(path: "START-HERE.txt", data: Data("plain steps\n".utf8))]),
            Comp(kind: "source", name: "domovoi", files: [File(path: KitNames.bundlePath, data: Data("bundle".utf8))]),
            Comp(kind: "linux-installer", name: "provision.sh", files: [File(path: "linux/provision.sh", data: Data("#!/bin/bash\n".utf8))]),
            Comp(kind: "apt", name: "ubuntu", files: [File(path: "apt/arm64/ubuntu/dists/resolute/InRelease", data: Data("in\n".utf8)),
                                                      File(path: "apt/arm64/ubuntu/dists/resolute/main/binary-arm64/Packages", data: Data("p\n".utf8))]),
            Comp(kind: "apt", name: "docker", files: [File(path: "apt/arm64/docker/gpg", data: Data("KEY\n".utf8)),
                                                      File(path: "apt/arm64/docker/dists/resolute/InRelease", data: Data("in\n".utf8))]),
            Comp(kind: "python-wheels", name: "cp314-aarch64", files: [File(path: "python/cp314-aarch64/fixture-1.0-py3-none-any.whl", data: Data("w".utf8))]),
            Comp(kind: "container-image", name: "postgres", files: [File(path: "images/postgres.tar", data: Data("pg".utf8))]),
            Comp(kind: "whisper-model", name: "small.en", files: [File(path: "speech/whisper-small.en/model.bin", data: Data("wh".utf8))]),
            Comp(kind: "piper-voice", name: "en_US-kristin-medium", files: [File(path: "speech/piper/en_US-kristin-medium.onnx", data: Data("pv".utf8))]),
            Comp(kind: "speaker-model", name: "speaker", files: [File(path: "speech/speaker/speaker.onnx", data: Data("sp".utf8))]),
        ]
        for (name, data) in macFiles.sorted(by: { $0.key < $1.key }) {
            k.components.append(Comp(kind: "mac-part", name: name, files: [File(path: "mac/" + name, data: data)]))
        }
        for m in models.models {
            k.components.append(Comp(kind: "ollama-model", name: m.tag, files: [File(path: m.pin.manifestPath, data: m.manifest)]
                                     + m.blobs.map { File(path: "ollama/models/blobs/sha256-\(sha($0))", data: $0) }))
        }
        return k
    }

    func remove(kind: String, name: String? = nil) { components.removeAll { $0.kind == kind && (name == nil || $0.name == name) } }

    // ─── SSHSIG, as ssh-keygen -Y sign writes it ───────────────────────

    static func sshString(_ d: Data) -> Data { SSHReader.string(d) }

    static func sign(_ message: Data, namespace: String, key: Curve25519.Signing.PrivateKey) throws -> Data {
        let pub = sshString(Data("ssh-ed25519".utf8)) + sshString(key.publicKey.rawRepresentation)
        let h = Data(SHA512.hash(data: message))
        let signed = Data("SSHSIG".utf8) + sshString(Data(namespace.utf8)) + sshString(Data()) + sshString(Data("sha512".utf8)) + sshString(h)
        let sig = try key.signature(for: signed)
        let sigBlob = sshString(Data("ssh-ed25519".utf8)) + sshString(sig)
        let blob = Data("SSHSIG".utf8) + Data([0, 0, 0, 1]) + sshString(pub) + sshString(Data(namespace.utf8)) + sshString(Data())
            + sshString(Data("sha512".utf8)) + sshString(sigBlob)
        return Data("-----BEGIN SSH SIGNATURE-----\n\(blob.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed]))\n-----END SSH SIGNATURE-----\n".utf8)
    }

    // ─── writing ───────────────────────────────────────────────────────

    /// Writes the kit into `root` (made afresh) and returns it.
    @discardableResult
    func write(to root: URL) throws -> URL {
        let fm = FileManager.default
        try? fm.removeItem(at: root)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let manifest: [String: Any] = ["kind": MacReleaseNames.manifestKind, "schema": 1, "tag": manifestTag ?? tag,
                                       "version": manifestVersion ?? version, "commit": manifestCommit ?? commit,
                                       "file": MacReleaseNames.package, "size": 3, "sha256": String(repeating: "a", count: 64)]
        let md = try JSONSerialization.data(withJSONObject: manifest, options: [.sortedKeys])
        let sig = try Self.sign(md, namespace: namespace, key: signer ?? key)
        var comps = components
        comps.insert(Comp(kind: "release", name: "mac", files: [File(path: KitNames.manifestPath, data: md),
                                                                 File(path: KitNames.manifestSigPath, data: sig)]), at: 0)
        var sums: [String: String] = [:]
        var jcomps: [[String: Any]] = []
        for c in comps {
            var jfiles: [[String: Any]] = []
            for f in c.files {
                var jf: [String: Any] = ["path": f.path, "size": f.data.count, "sha256": Self.sha(f.data)]
                var stored: [(String, Data)] = []
                // Only the models' blobs are ever big enough to split (a
                // small part_size here splits nothing else either).
                if f.data.count > partSize && f.path.hasPrefix("ollama/models/blobs/") {
                    var parts: [[String: Any]] = []
                    var i = 0, n = 1
                    while i < f.data.count {
                        let piece = f.data.subdata(in: i..<min(f.data.count, i + partSize))
                        let p = f.path + String(format: ".part%02d", n)
                        parts.append(["path": p, "size": piece.count, "sha256": Self.sha(piece)])
                        stored.append((p, piece))
                        i += partSize
                        n += 1
                    }
                    jf["parts"] = parts
                } else {
                    stored.append((f.path, f.data))
                }
                for (p, d) in stored {
                    let u = root.appendingPathComponent(p)
                    try fm.createDirectory(at: u.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try d.write(to: u)
                    sums[p] = Self.sha(d)
                }
                jfiles.append(jf)
            }
            jcomps.append(["kind": c.kind, "name": c.name, "version": "x", "licence": "MIT", "household_added": false,
                           "pinned_by": NSNull(), "files": jfiles])
        }
        let kit: [String: Any] = ["kit_format": format, "domovoi_version": version, "tag": tag, "commit": commit,
                                  "created_at": "2026-11-22T10:15:00Z", "targets": targets, "includes": includes,
                                  "android_version_code": NSNull(), "part_size": partSize,
                                  "notices": [["id": "do-not-sell", "text": "Don't sell this stick."]], "components": jcomps]
        let kj = try JSONSerialization.data(withJSONObject: kit, options: [.sortedKeys, .prettyPrinted])
        try kj.write(to: root.appendingPathComponent(KitNames.kitJSON))
        sums[KitNames.kitJSON] = Self.sha(kj)
        try Self.writeSums(sums, root)
        return root
    }

    static func writeSums(_ sums: [String: String], _ root: URL) throws {
        let text = sums.keys.sorted().map { "\(sums[$0]!)  \($0)\n" }.joined()
        try text.write(to: root.appendingPathComponent(KitNames.sums), atomically: true, encoding: .utf8)
    }

    /// SHA256SUMS made again for what is on the stick now (a stick someone
    /// changed and re-summed: the checksums can't catch it, the signature must).
    static func resum(_ root: URL, _ paths: [String]) throws {
        var sums = try KitSums.parse(String(contentsOf: root.appendingPathComponent(KitNames.sums), encoding: .utf8))
        for p in paths { sums[p] = sha(try Data(contentsOf: root.appendingPathComponent(p))) }
        try writeSums(sums, root)
    }
}

/// Fake Ollama models as a kit carries them: a manifest naming a config and
/// one layer, each a small blob; the pin is the manifest's SHA-256.
struct KitModelsFixture {
    struct Model {
        var tag: String
        var role: String
        var tier: String
        var manifest: Data
        var blobs: [Data]
        var pin: ModelPin { ModelPin(tag: tag, digest: KitFixture.sha(manifest), role: role) }
    }

    var models: [Model] = []

    init() {
        let tiers: [(String, [(String, String)])] = [
            ("floor", [("qa", "llama3.2:3b-instruct-q4_K_M"), ("tool", "qwen3:4b-instruct-2507-q4_K_M"), ("vision", "qwen3-vl:2b-instruct-q4_K_M")]),
            ("standard", [("qa", "llama3.2:3b-instruct-q4_K_M"), ("tool", "qwen3:8b-q4_K_M"), ("vision", "qwen3-vl:2b-instruct-q4_K_M")]),
        ]
        for (tier, roles) in tiers {
            for (role, tag) in roles where !models.contains(where: { $0.tag == tag }) {
                let cfg = Data("config of \(tag)".utf8), layer = Data("weights of \(tag), a little longer than the config".utf8)
                let man = Data(#"{"schemaVersion":2,"config":{"digest":"sha256:\#(KitFixture.sha(cfg))"},"layers":[{"digest":"sha256:\#(KitFixture.sha(layer))"}]}"#.utf8)
                models.append(Model(tag: tag, role: role, tier: tier, manifest: man, blobs: [cfg, layer]))
            }
        }
    }

    func model(_ tag: String) -> Model { models.first { $0.tag == tag }! }

    /// provision-manifest.conf's tier lines for these models.
    var conf: String {
        let roles: [(String, [(String, String)])] = [
            ("floor", [("qa", "llama3.2:3b-instruct-q4_K_M"), ("tool", "qwen3:4b-instruct-2507-q4_K_M"), ("vision", "qwen3-vl:2b-instruct-q4_K_M")]),
            ("standard", [("qa", "llama3.2:3b-instruct-q4_K_M"), ("tool", "qwen3:8b-q4_K_M"), ("vision", "qwen3-vl:2b-instruct-q4_K_M")]),
        ]
        var out = "# provision-manifest.conf (test)\ntier_standard_min_mem_mib=22528\n"
        for (tier, rs) in roles {
            for (role, tag) in rs {
                out += "tier_\(tier)_\(role)_model=\(tag)\ntier_\(tier)_\(role)_digest=\(model(tag).pin.digest)\n"
            }
        }
        return out
    }
}
