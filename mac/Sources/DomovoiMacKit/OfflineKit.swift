import CryptoKit
import Darwin
import Foundation

// Installing from an offline kit on a Mac (release 1.5.0, phase P4;
// docs/OFFLINE_KIT.md, "Installing on a Mac"). A house that is never online
// gets a USB stick made by an online Domovoi server with "A Mac with an
// Apple chip" ticked; the household opens release/DomovoiSetup-mac.pkg on
// it, and the setup pages find the stick (any /Volumes/<name>/kit.json; the
// label is DOMOVOIKIT, but nothing depends on it) and install from it with
// no internet.
//
// Before anything from the kit is used, OfflineKit.check decides whether
// this kit may be, in this order (the Windows Setup's order,
// windows/common/OfflineKit.cs, and its words where they fit):
//   1. The stick's SHA256SUMS for the files the trust chain reads (kit.json,
//      the release's signed Mac manifest and its signature). This only
//      catches a bad copy or a dying stick; it is not trust.
//   2. kit.json: a kit format this Setup reads, made for a Mac, naming a
//      release. Then what is on the stick: no link of any kind, nothing
//      kit.json doesn't account for, no oddly named apt folder (the Python
//      checker's rules, domovoi/offline_kit/checker.py _unlisted).
//   3. The release signature: release/DomovoiSetup-mac.manifest.json signed
//      with the release key this package pins (ReleaseSignature.swift,
//      share/installer/release-signing.pub), naming the tag and commit
//      kit.json names, and that commit the one this package was built from
//      (share/build.json): the kit and this Setup are one release. A kit of
//      an older release is never used ("newer kits only": nothing goes back
//      to an older release); a newer one is refused with "open the
//      DomovoiSetup-mac.pkg on the stick".
//   4. The parts: the Mac's own (the Ubuntu arm64 cloud image, Ollama for
//      macOS, whisper.cpp's model) listed with this package's pins.conf
//      hashes, the AI models of the tier (provision-manifest.conf's
//      digests), and what provision.sh --offline-kit needs on an arm64 VM
//      (the same list as its kit_check).
// Every file the Mac then takes (KitSource.copy) is hashed as it is read:
// each stored piece against SHA256SUMS and kit.json, the joined whole
// (.partNN joined in order) against kit.json and the pin. Inside the VM,
// provision.sh checks everything else against the pins at the same commit.

public enum KitNames {
    public static let kitJSON = "kit.json"
    public static let sums = "SHA256SUMS"
    /// The kit format this package reads (domovoi/offline_kit/format.py KIT_FORMAT).
    public static let formatRead = 1
    public static let macTarget = "mac-arm64"
    public static let pcTarget = "pc-x86_64"
    public static let manifestPath = "release/" + MacReleaseNames.manifest
    public static let manifestSigPath = "release/" + MacReleaseNames.manifestSig
    public static let bundlePath = "source/domovoi.bundle"
    /// Where Lima mounts the kit in the VM, read-only (the Windows side's
    /// drvfs mount point, so provision.sh sees one path on both).
    public static let guestMount = "/mnt/domovoi-kit"
    /// Where a stick shows up on a Mac.
    public static let volumes = URL(fileURLWithPath: "/Volumes", isDirectory: true)
    public static let remake = " Make the stick again."
    static let maxKitJSON = 64 << 20

    /// What an operating system leaves on a stick by itself, as the Python
    /// checker's OS_LITTER_DIRS and is_os_litter (format.py) name them.
    static let osRootDirs: Set<String> = ["System Volume Information", "$RECYCLE.BIN", ".Trashes", ".Spotlight-V100",
                                          ".fseventsd", ".TemporaryItems", ".DocumentRevisions-V100", "lost+found"]
    static let osFiles: Set<String> = [".DS_Store", "Thumbs.db", "desktop.ini", ".metadata_never_index"]

    static func isOSFile(_ name: String) -> Bool { name.hasPrefix("._") || osFiles.contains(name) }
}

/// kit.json, as the Mac reads it: the fields it needs, every file with its
/// size, SHA-256 and parts, and each component.
public struct KitIndex: Equatable {
    public struct Part: Equatable {
        public var path: String
        public var size: Int64
        public var sha256: String
    }

    public struct Entry: Equatable {
        public var path: String
        public var size: Int64
        public var sha256: String
        /// Empty: stored whole.
        public var parts: [Part]
    }

    public struct Component: Equatable {
        public var kind: String
        public var name: String
        public var householdAdded: Bool
        public var files: [String]
    }

    public var format: Int?
    public var version: String?
    public var tag: String?
    public var commit: String?
    public var targets: [String] = []
    public var includes: [String] = []
    public var files: [String: Entry] = [:]
    public var components: [Component] = []
    /// Every file as stored (whole files and parts), and the split files' joined names.
    public var stored: Set<String> = []
    public var joined: Set<String> = []

    public var kinds: Set<String> { Set(components.map(\.kind)) }
    public func has(_ path: String) -> Bool { files[path] != nil }
    public func hasUnder(_ prefix: String) -> Bool { files.keys.contains { $0.hasPrefix(prefix) } }

    /// A kit path as kit.json and SHA256SUMS write them: relative, "/"
    /// between names, no "." or "..", no control character.
    public static func safePath(_ p: String) -> Bool {
        if p.isEmpty || p.hasPrefix("/") || p.contains("\\") || p.contains(":") { return false }
        if p.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7f }) { return false }
        return !p.split(separator: "/", omittingEmptySubsequences: false).contains { $0.isEmpty || $0 == "." || $0 == ".." }
    }

    public static func parse(_ data: Data) throws -> KitIndex {
        guard data.count <= KitNames.maxKitJSON else { throw HelperError("kit.json is far too large") }
        guard let any = try? JSONSerialization.jsonObject(with: data), let d = any as? [String: Any] else {
            throw HelperError("kit.json isn't JSON")
        }
        func str(_ o: [String: Any], _ k: String) -> String? { o[k] as? String }
        func int(_ o: [String: Any], _ k: String) -> Int64? {
            // A JSON true/false is an NSNumber too, of type "c" (char).
            guard let n = o[k] as? NSNumber, n.objCType.pointee != 0x63 else { return nil }
            let v = n.int64Value
            return NSNumber(value: v) == n ? v : nil
        }
        var k = KitIndex()
        k.format = int(d, "kit_format").map { Int($0) }
        k.version = str(d, "domovoi_version")
        k.tag = str(d, "tag")
        k.commit = str(d, "commit")
        k.targets = (d["targets"] as? [Any])?.compactMap { $0 as? String } ?? []
        k.includes = (d["includes"] as? [Any])?.compactMap { $0 as? String } ?? []
        for c in (d["components"] as? [Any])?.compactMap({ $0 as? [String: Any] }) ?? [] {
            var comp = Component(kind: str(c, "kind") ?? "", name: str(c, "name") ?? "", householdAdded: (c["household_added"] as? Bool) ?? false, files: [])
            for f in (c["files"] as? [Any])?.compactMap({ $0 as? [String: Any] }) ?? [] {
                guard let p = str(f, "path"), safePath(p) else { continue }
                comp.files.append(p)
                if k.files[p] != nil { continue }   // a file two components share is stored once
                var e = Entry(path: p, size: int(f, "size") ?? -1, sha256: str(f, "sha256") ?? "", parts: [])
                for part in (f["parts"] as? [Any])?.compactMap({ $0 as? [String: Any] }) ?? [] {
                    guard let pp = str(part, "path"), safePath(pp) else { continue }
                    e.parts.append(Part(path: pp, size: int(part, "size") ?? -1, sha256: str(part, "sha256") ?? ""))
                }
                k.files[p] = e
                if e.parts.isEmpty {
                    k.stored.insert(p)
                } else {
                    k.joined.insert(p)
                    e.parts.forEach { k.stored.insert($0.path) }
                }
            }
            k.components.append(comp)
        }
        return k
    }
}

/// The stick's SHA256SUMS: `<sha256>  <path>` lines (sha256sum's format).
public enum KitSums {
    public static func parse(_ text: String) throws -> [String: String] {
        var out: [String: String] = [:]
        // By UTF-16 unit: Swift's Characters would keep "\r\n" together.
        for (i, raw) in text.components(separatedBy: "\n").enumerated() {
            var line = Substring(raw)
            if line.unicodeScalars.last == "\r" { line = Substring(line.unicodeScalars.dropLast()) }
            if line.trimmingCharacters(in: .whitespaces).isEmpty { continue }
            let bad = HelperError("\(KitNames.sums) line \(i + 1) can't be read")
            guard line.count > 66 else { throw bad }
            let hex = String(line.prefix(64)), sep = line.dropFirst(64).prefix(2), path = String(line.dropFirst(66))
            guard Pattern.full(hex, "[0-9a-f]{64}"), sep == "  " || sep == " *", KitIndex.safePath(path), out[path] == nil else { throw bad }
            out[path] = hex
        }
        if out.isEmpty { throw HelperError("\(KitNames.sums) lists nothing") }
        return out
    }
}

/// The AI models of a tier, as provision.sh pins them
/// (scripts/linux/provision-manifest.conf: tier_<tier>_<role>_model and
/// _digest; the digest is the SHA-256 of the model's manifest, what Ollama
/// reports). The package carries that file beside provision.sh
/// (share/provision-manifest.conf, mac/build.sh).
public struct ModelPin: Equatable {
    public var tag: String
    public var digest: String
    /// qa, tool or vision.
    public var role: String

    /// Where a kit keeps the model's manifest (provision.sh kit_model_manifest).
    public var manifestPath: String {
        var name = String(tag.split(separator: ":", maxSplits: 1).first ?? "")
        let ver = tag.contains(":") ? String(tag.split(separator: ":", maxSplits: 1)[1]) : "latest"
        if !name.contains("/") { name = "library/" + name }
        return "ollama/models/manifests/registry.ollama.ai/\(name)/\(ver)"
    }

    /// The manifest's place under an Ollama models folder.
    public var manifestRelative: String { String(manifestPath.dropFirst("ollama/models/".count)) }
}

public struct ModelPins: Equatable {
    var values: [String: String]

    public static let file = "provision-manifest.conf"

    public static func parse(_ text: String) -> ModelPins {
        var v: [String: String] = [:]
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            v[String(line[..<eq])] = String(line[line.index(after: eq)...])
        }
        return ModelPins(values: v)
    }

    /// The package's copy (share/provision-manifest.conf), or the checkout's
    /// when the helper runs from mac/ in a checkout.
    public static func load(share: URL) -> ModelPins? {
        for u in [share.appendingPathComponent(file), share.deletingLastPathComponent().appendingPathComponent("scripts/linux/" + file)] {
            if let t = try? String(contentsOf: u, encoding: .utf8) { return parse(t) }
        }
        return nil
    }

    /// provision.sh model_pairs: the QA model, the router, then (unless
    /// `vision` is false) the vision model, each once. Nil when the tier
    /// isn't pinned.
    public func models(tier: Tier, vision: Bool = true) -> [ModelPin]? {
        var out: [ModelPin] = []
        for role in ["qa", "tool"] + (vision ? ["vision"] : []) {
            guard let tag = values["tier_\(tier.rawValue)_\(role)_model"], let dg = values["tier_\(tier.rawValue)_\(role)_digest"],
                  !tag.isEmpty, Pattern.full(dg, "[0-9a-f]{64}") else { return nil }
            if !out.contains(where: { $0.tag == tag }) { out.append(ModelPin(tag: tag, digest: dg, role: role)) }
        }
        return out
    }
}

/// What this package asks of a kit: its own release and pins.
public struct KitExpectation {
    /// share/build.json: the commit this package was built from, and its version.
    public var commit: String
    public var version: String
    /// The release key this package pins (32 bytes), or nil.
    public var key: Data?
    public var pins: Pins
    /// provision-manifest.conf's model pins, or nil when the package has none.
    public var modelPins: ModelPins?
    /// The tier this Mac's memory gives (Tier.forMac): the kit's models may
    /// make it the floor (provision.sh resolve_tier's rule for a kit).
    public var tier: Tier
    /// Whether Domovoi runs its own Ollama (an offline kit needs it).
    public var ownOllama: Bool = true
    /// A repair of an install (only the words differ).
    public var repair: Bool = false

    public init(commit: String, version: String, key: Data?, pins: Pins, modelPins: ModelPins?, tier: Tier) {
        self.commit = commit
        self.version = version
        self.key = key
        self.pins = pins
        self.modelPins = modelPins
        self.tier = tier
    }
}

/// What the check found, for the setup pages (in the preflight report).
public struct KitReport: Codable, Equatable {
    /// The kit's folder.
    public var root: String
    /// The volume's name when the kit is a volume's root (DOMOVOIKIT).
    public var volume: String?
    public var version: String?
    public var tag: String?
    public var commit: String?
    /// The kit has the AI models (kit.json includes "ai").
    public var ai: Bool = false
    /// The tier whose models go into the Mac's Ollama.
    public var tier: String?
    /// Each reason the kit can't be used, in the household's words.
    public var problems: [String] = []
    /// What the household should know (no AI models, the floor tier).
    public var notes: [String] = []

    public init(root: String, volume: String? = nil) {
        self.root = root
        self.volume = volume
    }

    public var ok: Bool { problems.isEmpty }
    public var place: String { volume ?? root }

    /// The check page's line.
    public var headline: String {
        ok ? "Installing from the offline kit on \(place), Domovoi \(version ?? "?"): nothing is downloaded"
            : "The offline kit on \(place) can't be used: " + problems.joined(separator: " ")
    }
}

/// A checked kit, for the install: where it is, its index and checksums,
/// and what the Mac takes from it.
public final class KitSource {
    public let root: URL
    public let index: KitIndex
    public let sums: [String: String]
    public let report: KitReport
    /// The tier's models that go into the Mac's own Ollama (empty: no AI).
    public let models: [ModelPin]
    public let tier: Tier
    public var ai: Bool { report.ai }

    init(root: URL, index: KitIndex, sums: [String: String], report: KitReport, models: [ModelPin], tier: Tier) {
        self.root = root
        self.index = index
        self.sums = sums
        self.report = report
        self.models = models
        self.tier = tier
    }

    public func has(_ path: String) -> Bool { index.has(path) }

    func local(_ path: String) -> URL { root.appendingPathComponent(path) }

    /// Reads the kit file `path` (its parts joined, in order) into `sink`,
    /// holding each stored piece to SHA256SUMS and kit.json and the whole to
    /// kit.json and, when given, to `sha256` (the pin). Nothing is kept of
    /// a file that fails: the caller writes to a temporary name.
    func stream(_ path: String, sha256 want: String?, into sink: (Data) throws -> Void) throws {
        guard let e = index.files[path] else { throw HelperError("the offline kit has no \(path)." + KitNames.remake) }
        let pieces = e.parts.isEmpty ? [KitIndex.Part(path: e.path, size: e.size, sha256: e.sha256)] : e.parts
        var whole = SHA256()
        var total: Int64 = 0
        for p in pieces {
            guard let listed = sums[p.path] else { throw HelperError("This stick has a damaged file: \(p.path) (\(KitNames.sums) doesn't list it)." + KitNames.remake) }
            guard let h = FileHandle(forReadingAtPath: local(p.path).path) else {
                throw HelperError("This stick has a damaged file: \(p.path) (missing, or it can't be read). Is the stick still plugged in?")
            }
            defer { try? h.close() }
            var piece = SHA256()
            var n: Int64 = 0
            while true {
                let chunk = try h.read(upToCount: 4 << 20) ?? Data()
                if chunk.isEmpty { break }
                piece.update(data: chunk)
                whole.update(data: chunk)
                n += Int64(chunk.count)
                try sink(chunk)
            }
            let got = piece.finalize().map { String(format: "%02x", $0) }.joined()
            guard got == listed, got == p.sha256, n == p.size else {
                throw HelperError("This stick has a damaged file: \(p.path) (its SHA-256 or size isn't the one the kit lists)." + KitNames.remake)
            }
            total += n
        }
        let got = whole.finalize().map { String(format: "%02x", $0) }.joined()
        guard got == e.sha256, total == e.size else {
            throw HelperError("This stick has a damaged file: \(path) (its parts don't join to the file kit.json names)." + KitNames.remake)
        }
        if let w = want, got != w.lowercased() {
            throw HelperError("The offline kit's \(path) isn't the one this Setup's release pins (SHA-256 \(got.prefix(12)), the pin is \(w.prefix(12))): nothing of it was used.")
        }
    }

    /// The file into `dest` (made afresh; a temporary name until it is whole and checked).
    public func copy(_ path: String, to dest: URL, sha256 want: String?) throws {
        let fm = FileManager.default
        let tmp = dest.deletingLastPathComponent().appendingPathComponent(".\(dest.lastPathComponent).kit-part")
        try? fm.removeItem(at: tmp)
        guard fm.createFile(atPath: tmp.path, contents: nil, attributes: [.posixPermissions: 0o644]),
              let out = FileHandle(forWritingAtPath: tmp.path) else { throw HelperError("can't write \(tmp.path)") }
        do {
            try stream(path, sha256: want) { try out.write(contentsOf: $0) }
            try out.close()
        } catch {
            try? out.close()
            try? fm.removeItem(at: tmp)
            throw error
        }
        try? fm.removeItem(at: dest)
        guard rename(tmp.path, dest.path) == 0 else {
            let e = String(cString: strerror(errno))
            try? fm.removeItem(at: tmp)
            throw HelperError("can't move \(tmp.lastPathComponent) to \(dest.path): \(e)")
        }
    }

    /// A small kit file, checked the same way.
    public func data(_ path: String, sha256 want: String? = nil, max: Int64 = 1 << 20) throws -> Data {
        guard let e = index.files[path], e.size <= max else { throw HelperError("the offline kit's \(path) is missing or too large." + KitNames.remake) }
        var d = Data()
        try stream(path, sha256: want) { d.append($0) }
        return d
    }
}

public enum OfflineKit {
    /// The kits plugged into this Mac: every /Volumes/<name> with a kit.json
    /// (a plain file) at its top, sorted by name. The label is DOMOVOIKIT,
    /// but a household may rename it.
    public static func find(volumes: URL = KitNames.volumes) -> [URL] {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: volumes.path) else { return [] }
        return names.sorted().compactMap { n in
            let root = volumes.appendingPathComponent(n, isDirectory: true)
            var st = stat()
            guard lstat(root.appendingPathComponent(KitNames.kitJSON).path, &st) == 0, st.st_mode & S_IFMT == S_IFREG else { return nil }
            return root
        }
    }

    /// The kit to use: `given` (the helper's --kit), else the one plugged
    /// in; with more than one, the one of this package's commit (else the
    /// first by name).
    public static func locate(given: String?, volumes: URL = KitNames.volumes, commit: String?) -> URL? {
        if let g = given { return URL(fileURLWithPath: g, isDirectory: true).standardizedFileURL }
        let all = find(volumes: volumes)
        if all.count > 1, let c = commit {
            for u in all {
                if let d = try? Data(contentsOf: u.appendingPathComponent(KitNames.kitJSON)), d.count <= KitNames.maxKitJSON,
                   let k = try? KitIndex.parse(d), k.commit == c { return u }
            }
        }
        return all.first
    }

    static func hex(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

    /// Whether this kit may be used, and if so the checked kit. Read-only.
    /// `volumeRoot`: the kit is a volume's top folder, where the operating
    /// system's own folders are let be.
    public static func check(root given: URL, want: KitExpectation, volumes: URL = KitNames.volumes) -> (KitReport, KitSource?) {
        let root = given.standardizedFileURL
        let volumeRoot = root.deletingLastPathComponent().standardizedFileURL.path == volumes.standardizedFileURL.path
        var r = KitReport(root: root.path, volume: volumeRoot ? root.lastPathComponent : nil)
        func no(_ why: String) -> (KitReport, KitSource?) { r.problems.append(why); return (r, nil) }
        let fm = FileManager.default
        guard fm.fileExists(atPath: root.appendingPathComponent(KitNames.kitJSON).path) else {
            return no("There is no offline kit at \(root.path): no kit.json there. Choose the stick's top folder, the one with kit.json in it.")
        }

        // 1. The trust chain's files, against the stick's checksums.
        let sums: [String: String]
        do {
            guard let t = try? String(contentsOf: root.appendingPathComponent(KitNames.sums), encoding: .utf8) else {
                throw HelperError("there is no \(KitNames.sums)")
            }
            sums = try KitSums.parse(t)
        } catch {
            return no("This stick's checksums can't be used: \(error)." + KitNames.remake)
        }
        var first: [String: Data] = [:]
        for p in [KitNames.kitJSON, KitNames.manifestPath, KitNames.manifestSigPath] {
            guard let listed = sums[p] else { return no("This stick has a damaged file: \(p) (\(KitNames.sums) doesn't list it)." + KitNames.remake) }
            guard let d = try? Data(contentsOf: root.appendingPathComponent(p)), d.count <= KitNames.maxKitJSON else {
                return no("This stick has a damaged file: \(p) (missing)." + KitNames.remake)
            }
            guard hex(d) == listed else { return no("This stick has a damaged file: \(p)." + KitNames.remake) }
            first[p] = d
        }

        // 2. kit.json.
        let k: KitIndex
        do { k = try KitIndex.parse(first[KitNames.kitJSON]!) } catch { return no("kit.json can't be read (\(error))." + KitNames.remake) }
        r.version = k.version
        r.tag = k.tag
        r.commit = k.commit
        r.ai = k.includes.contains("ai")
        guard let format = k.format, format >= 1 else { return no("kit.json has no kit format." + KitNames.remake) }
        if format > KitNames.formatRead {
            return no("This kit was made by a newer Domovoi (kit format \(format); this Setup reads format \(KitNames.formatRead)). Open the DomovoiSetup-mac.pkg on the stick, in its release folder.")
        }
        guard k.targets.contains(KitNames.macTarget) else {
            return no(k.targets.contains(KitNames.pcTarget)
                      ? "This kit was made for a Windows or Linux PC only. A Mac needs a kit with \"A Mac with an Apple chip\" ticked: make the kit again with it ticked."
                      : "This kit isn't for a Mac: make the kit again with \"A Mac with an Apple chip\" ticked.")
        }
        guard let kc = k.commit, Pattern.full(kc, "[0-9a-f]{40}"), let kt = k.tag, MacReleaseNames.isTag(kt) else {
            return no("kit.json names no release." + KitNames.remake)
        }

        // 2b. What is on the stick: no links, nothing unaccounted for.
        let extra = unaccounted(root: root, index: k, volumeRoot: volumeRoot)
        if !extra.isEmpty {
            var s = extra.prefix(5).joined(separator: " ")
            if extra.count > 5 { s += " And \(extra.count - 5) more like these." }
            return no("This stick holds what a kit never does, so Setup used nothing from it. " + s + KitNames.remake)
        }

        // 3. The release signature, and the release this package is.
        guard let key = want.key, key.count == 32 else {
            return no("This Setup has no release key built in, so it can't check an offline kit. Open the DomovoiSetup-mac.pkg on the stick, in its release folder.")
        }
        if let why = SSHSig.problem(first[KitNames.manifestSigPath]!, message: first[KitNames.manifestPath]!, key: key,
                                    namespace: MacReleaseNames.manifestNamespace) {
            return no("This kit is not a Domovoi release signed with Domovoi's release key (\(why)), so Setup used nothing from it. Use a kit from a house you trust, made again.")
        }
        let (parsed, mwhy) = MacSetupManifest.parse(first[KitNames.manifestPath]!)
        guard let m = parsed else {
            return no("This kit is not a Domovoi release signed with Domovoi's release key (\(mwhy ?? "its manifest isn't readable")), so Setup used nothing from it.")
        }
        guard m.tag == kt, m.commit == kc else {
            return no("This stick's kit.json names \(kt) (commit \(kc.prefix(12))), but its signed release is \(m.tag) (commit \(m.commit.prefix(12)))." + KitNames.remake)
        }
        r.version = m.version
        if m.commit != want.commit {
            let c = ReleaseVersion.compare(m.version, want.version)
            let mine = want.repair ? "this Mac's Domovoi is \(want.version)" : "this Setup installs Domovoi \(want.version)"
            if let c, c < 0 {
                return no("This kit holds Domovoi \(m.version), older than \(want.version), and a kit never takes Domovoi back to an older release. Use a kit made by a server that runs Domovoi \(want.version) or newer.")
            } else if c == 0 {
                return no("This kit holds Domovoi \(m.version) at commit \(m.commit.prefix(12)), and \(mine) from commit \(want.commit.prefix(12)): they don't go together. Open the DomovoiSetup-mac.pkg on the stick, in its release folder.")
            }
            return no("This kit holds Domovoi \(m.version), and \(mine): they don't go together. "
                      + (want.repair ? "A repair uses only a kit of the release this Mac runs: take the stick out to repair without it."
                                     : "Open the DomovoiSetup-mac.pkg on the stick, in its release folder."))
        }

        // 4. The parts.
        if !want.ownOllama {
            return no("An install from an offline kit runs Domovoi's own Ollama, with the AI models from the stick: quit the Ollama on this Mac (port \(Ports.real.ollama)), then check again.")
        }
        var missing: [String] = []
        var wrong: [String] = []
        let p = want.pins
        var mac: [(String, Pin, String)] = [("Ubuntu's arm64 cloud image", p.ubuntuImage, "mac/" + p.ubuntuImage.fileName),
                                            ("Ollama for macOS", p.ollama, "mac/" + p.ollama.fileName)]
        if let stt = p.stt { mac.append(("the speech recognition model for the Mac", stt.model, "mac/" + stt.model.fileName)) }
        var used: [String] = []
        for (label, pin, path) in mac {
            guard let e = k.files[path] else {
                // Every kit carries Ollama for its target, AI ticked or not
                // (1.5.0's kit builder; a Mac kit is refused at build time
                // without the macOS pin), so only a broken kit lacks it.
                if pin == p.ollama {
                    r.problems.append("This kit is missing Ollama for macOS. Make a new kit from an up-to-date house.")
                } else {
                    missing.append("\(label) (\(path))")
                }
                continue
            }
            if e.sha256 != pin.sha256 { wrong.append(path) }
            used.append(path)
        }
        // What provision.sh --offline-kit needs on the VM (its kit_check, for
        // aarch64 with the Mac's Ollama: no Ollama for Linux).
        if !k.has(KitNames.bundlePath) { missing.append("Domovoi's source (\(KitNames.bundlePath))") }
        if !k.hasUnder("apt/arm64/ubuntu/dists/") { missing.append("Ubuntu's packages for arm64 (apt/arm64/ubuntu)") }
        if !k.hasUnder("apt/arm64/docker/dists/") || !k.has("apt/arm64/docker/gpg") { missing.append("Docker's packages and key for arm64 (apt/arm64/docker, with gpg)") }
        if !k.hasUnder("python/cp314-aarch64/") { missing.append("the Python packages for aarch64 (python/cp314-aarch64)") }
        if !k.kinds.contains("container-image") { missing.append("the database images (images/)") }
        if !k.kinds.contains("whisper-model") { missing.append("the speech recognition model (speech/whisper-...)") }
        if !k.kinds.contains("piper-voice") { missing.append("the voices (speech/piper)") }
        if !k.kinds.contains("speaker-model") { missing.append("voice ID's model (speech/speaker)") }

        // The AI models: the tier's, as provision.sh pins them; the floor's
        // when the stick has only those (a kit from a floor-tier house).
        var tier = want.tier
        var models: [ModelPin] = []
        if r.ai {
            if let mp = want.modelPins, var list = mp.models(tier: tier) {
                if tier == .standard, list.contains(where: { $0.role != "vision" && !k.has($0.manifestPath) }), let floor = mp.models(tier: .floor) {
                    tier = .floor
                    list = floor
                    r.notes.append("The kit has the smaller AI models only (a kit from a house with less memory), so this Mac gets those.")
                }
                for mdl in list where !k.has(mdl.manifestPath) {
                    if mdl.role == "vision" {
                        r.notes.append("The kit has no model for pictures in chat (\(mdl.tag)): chat can't read pictures until an admin adds it, with internet.")
                    } else {
                        missing.append("the AI model \(mdl.tag) (\(mdl.manifestPath))")
                    }
                }
                models = list.filter { k.has($0.manifestPath) }
                // Each model's manifest and blobs, as its component lists them.
                for mdl in models {
                    used.append(mdl.manifestPath)
                    for c in k.components where c.kind == "ollama-model" && c.files.contains(mdl.manifestPath) {
                        used += c.files.filter { $0 != mdl.manifestPath && !used.contains($0) }
                    }
                }
            } else {
                missing.append("this Setup's model pins (provision-manifest.conf in the package)")
            }
        } else {
            r.notes.append("The kit has no AI models: Domovoi is installed without AI chat and questions, which can be added later, with internet.")
        }
        r.tier = tier.rawValue
        if !wrong.isEmpty { r.problems.append("This kit's \(wrong.joined(separator: ", ")) isn't the one this Setup's release pins." + KitNames.remake) }
        if !missing.isEmpty { r.problems.append("This kit is missing what Setup needs: " + missing.joined(separator: "; ") + ". Make the kit again.") }
        for path in used {
            let pieces = k.files[path].map { $0.parts.isEmpty ? [path] : $0.parts.map(\.path) } ?? []
            for piece in pieces where sums[piece] == nil {
                r.problems.append("This stick has a damaged file: \(piece) (\(KitNames.sums) doesn't list it)." + KitNames.remake)
            }
        }
        guard r.ok else { return (r, nil) }
        return (r, KitSource(root: root, index: k, sums: sums, report: r, models: models, tier: tier))
    }

    // ─── what is on the stick ──────────────────────────────────────────

    /// An apt suite or component folder's name (provision.sh kit_apt_name).
    static func aptName(_ s: String) -> Bool { Pattern.full(s, "[a-z0-9][a-z0-9.+-]{0,63}") }

    static func shown(_ rel: String) -> String { String(rel.unicodeScalars.map { $0.value < 0x20 || $0.value == 0x7f ? "?" : Character($0) }) }

    static func parents(_ rel: String) -> [String] {
        let parts = rel.split(separator: "/").map(String.init)
        return (1..<max(1, parts.count)).map { parts[0..<$0].joined(separator: "/") }
    }

    /// Everything on the stick kit.json doesn't account for, in the Python
    /// checker's words (checker.py _unlisted; OfflineKit.cs Unaccounted): a
    /// link of any kind (never walked into), something that isn't a plain
    /// file or folder, a file kit.json doesn't list, a split file here
    /// whole, a folder holding nothing of the kit, an apt suite or component
    /// folder with a name apt's source stanza can't take. What the system
    /// leaves on a stick (.DS_Store, ._*, ...) is let be in any folder as
    /// plain files; its folders (.Spotlight-V100, .fseventsd, ...) only at a
    /// volume's top. Read-only.
    public static func unaccounted(root: URL, index k: KitIndex, volumeRoot: Bool) -> [String] {
        let listed = k.stored.union([KitNames.kitJSON, KitNames.sums])
        var listedDirs = Set<String>()
        for p in listed { parents(p).forEach { listedDirs.insert($0) } }
        var withFiles = Set<String>()
        var seenDirs: [String] = []
        var found: [String] = []
        var todo: [String] = [""]
        let fm = FileManager.default
        while let at = todo.popLast() {
            let dir = at.isEmpty ? root : root.appendingPathComponent(at)
            guard let names = try? fm.contentsOfDirectory(atPath: dir.path) else {
                found.append((at.isEmpty ? "The kit's folder" : shown(at) + "/") + " can't be read, so what is in it can't be checked.")
                continue
            }
            var subdirs: [String] = []
            for name in names.sorted() {
                let rel = at.isEmpty ? name : at + "/" + name
                var st = stat()
                guard lstat(dir.appendingPathComponent(name).path, &st) == 0 else { found.append(shown(rel) + " can't be read."); continue }
                let type = st.st_mode & S_IFMT
                if type == S_IFLNK { found.append(shown(rel) + " is a link, which a kit never holds."); continue }
                if type == S_IFDIR {
                    if at.isEmpty && volumeRoot && KitNames.osRootDirs.contains(name) { continue }
                    let parts = rel.split(separator: "/")
                    if (parts.count == 5 || parts.count == 6) && parts[0] == "apt" && parts[3] == "dists" && !aptName(name) {
                        found.append("The package folder \(shown(rel))/ has a name a kit never uses.")
                    }
                    seenDirs.append(rel)
                    subdirs.append(rel)
                    continue
                }
                if type == S_IFREG && KitNames.isOSFile(name) { continue }
                parents(rel).forEach { withFiles.insert($0) }
                if type != S_IFREG {
                    found.append(shown(rel) + " is a link or not a plain file, which a kit never holds.")
                } else if listed.contains(rel) {
                } else if k.joined.contains(rel) {
                    found.append(shown(rel) + " is here whole, but kit.json says it is stored in parts only.")
                } else {
                    found.append(shown(rel) + " is not part of the kit (kit.json doesn't list it).")
                }
            }
            todo.append(contentsOf: subdirs.reversed())
        }
        for rel in seenDirs where !listedDirs.contains(rel) && !withFiles.contains(rel) {
            found.append(shown(rel) + "/ is not part of the kit (kit.json lists nothing in it).")
        }
        return found
    }
}

/// The kit's AI models into the Mac's own Ollama folder (Ollama's layout:
/// manifests/registry.ollama.ai/<ns>/<name>/<tag>, blobs/sha256-<hex>), as
/// provision.sh's kit_install_model puts them into Linux's: each manifest
/// must be the pinned build (its SHA-256 is the digest provision-manifest.conf
/// pins) before anything is copied, each blob it names must hash to its own
/// name (parts joined, every piece checked), blobs first and the manifest
/// last (until then Ollama doesn't know the model). A blob already there
/// with the right hash is kept. provision.sh then holds each model to its
/// pin again, through Ollama's own list.
public enum KitModels {
    public static func install(_ models: [ModelPin], from kit: KitSource, into dir: URL, log: (String) -> Void) throws -> [String] {
        let fm = FileManager.default
        var done: [String] = []
        for mdl in models {
            let man = try kit.data(mdl.manifestPath, max: 16 << 20)
            guard OfflineKit.hex(man) == mdl.digest else {
                throw HelperError("the offline kit's \(mdl.tag) is not the build this release pins (its manifest's SHA-256 is \(OfflineKit.hex(man).prefix(12)), the pin is \(mdl.digest.prefix(12))); nothing of it was copied")
            }
            let text = String(decoding: man, as: UTF8.self)
            var digests: [String] = []
            let re = try NSRegularExpression(pattern: "sha256:([0-9a-f]{64})")
            for m in re.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                if let r = Range(m.range(at: 1), in: text), !digests.contains(String(text[r])) { digests.append(String(text[r])) }
            }
            guard !digests.isEmpty else { throw HelperError("the offline kit's manifest of \(mdl.tag) names no blobs") }
            let blobs = dir.appendingPathComponent("blobs", isDirectory: true)
            try fm.createDirectory(at: blobs, withIntermediateDirectories: true)
            var copied = 0
            for d in digests {
                let dest = blobs.appendingPathComponent("sha256-\(d)")
                if fm.fileExists(atPath: dest.path), (try? Digest.sha256(of: dest)) == d { continue }
                try kit.copy("ollama/models/blobs/sha256-\(d)", to: dest, sha256: d)
                copied += 1
            }
            let mdest = dir.appendingPathComponent(mdl.manifestRelative)
            try fm.createDirectory(at: mdest.deletingLastPathComponent(), withIntermediateDirectories: true)
            let tmp = mdest.deletingLastPathComponent().appendingPathComponent(".\(mdest.lastPathComponent).kit-part")
            try man.write(to: tmp)
            try? fm.removeItem(at: mdest)
            try fm.moveItem(at: tmp, to: mdest)
            log("\(mdl.tag) copied from the offline kit (manifest \(mdl.digest.prefix(12)), \(digests.count) blobs, \(copied) new, each checked)")
            done.append(mdl.tag)
        }
        return done
    }
}

/// The kit for an install run: found, checked, and what the run does
/// without one. The helper resolves it before the install starts.
public enum KitSetup {
    /// How the helper was asked: --kit DIR, --no-kit, or neither (it looks).
    public enum Request: Equatable {
        case auto
        case off
        case path(String)
    }

    /// What this package asks of a kit, from its share/ folder.
    public static func expectation(share: URL, tier: Tier, trust: (URL) -> Bool = PinnedRelease.rootOnly) throws -> KitExpectation {
        let build = try BuildInfo.load(share)
        let pins = try Pins.load(share.appendingPathComponent("installer/pins.conf"))
        return KitExpectation(commit: build.commit, version: build.version, key: PinnedRelease.key(share: share, trust: trust),
                              pins: pins, modelPins: ModelPins.load(share: share), tier: tier)
    }

    /// The kit this run uses, or nil. A kit asked for by --kit, or found by
    /// a first install, that can't be used stops the run, with the reasons.
    /// A repair that finds a kit it can't use goes on without it, saying
    /// why (it takes only a kit of the release it runs).
    public static func resolve(_ request: Request, want: KitExpectation, volumes: URL = KitNames.volumes,
                               log: (String) -> Void) throws -> KitSource? {
        let root: URL
        switch request {
        case .off: return nil
        case .path(let p): root = URL(fileURLWithPath: p, isDirectory: true)
        case .auto:
            guard let r = OfflineKit.locate(given: nil, volumes: volumes, commit: want.commit) else { return nil }
            root = r
        }
        let (report, kit) = OfflineKit.check(root: root, want: want, volumes: volumes)
        if let kit {
            log("offline kit: Domovoi \(report.version ?? "?") (\(report.tag ?? "?"), commit \((report.commit ?? "").prefix(12))) at \(report.root); "
                + "AI models: \(report.ai ? kit.models.map(\.tag).joined(separator: ", ") : "none"); nothing is downloaded")
            report.notes.forEach { log("offline kit: \($0)") }
            return kit
        }
        if want.repair, request == .auto {
            log("note: the offline kit at \(report.root) is not used: " + report.problems.joined(separator: " "))
            return nil
        }
        throw HelperError(report.problems.joined(separator: " "))
    }
}

/// What the setup pages say about an offline kit.
public enum KitText {
    /// The list of changes: in place of "Downloads about 10-13 GB".
    public static func consent(_ k: KitReport) -> String {
        "Copies Domovoi \(k.version ?? "") from the offline kit on \(k.place) instead of downloading it: nothing is downloaded, "
            + "and nothing on this Mac reaches the internet while Setup runs. The stick is shared read-only with Domovoi's virtual machine "
            + "while it installs; keep it plugged in until Setup has finished."
    }

    /// The internet question's opening line.
    public static func internetQuestion(_ k: KitReport) -> String {
        "Setup installs from the offline kit on \(k.place) and needs no internet. After that, Domovoi works with or without it. "
            + "A house with no internet answers No. Your voice and recordings never leave this Mac either way."
    }

    /// The installed page, when a kit is plugged in.
    public static func repair(_ k: KitReport) -> String {
        k.ok ? "Repair uses the offline kit on \(k.place) (Domovoi \(k.version ?? "?")): nothing is downloaded. Keep it plugged in until it finishes."
            : "The offline kit on \(k.place) isn't used: " + k.problems.joined(separator: " ")
    }
}
