import CryptoKit
import Foundation

// The Mac update's trust in a downloaded DomovoiSetup-mac.pkg (mac-installer
// PLAN 4.12), the twin of windows/common/ReleaseSignature.cs. A release
// carries our own detached Ed25519 signatures, made by
// scripts/release/finish_release.py (`--mac-pkg`) with `ssh-keygen -Y sign`
// (OpenSSH's SSHSIG format, PROTOCOL.sshsig):
//
//   DomovoiSetup-mac.pkg.sig             over the package,  namespace domovoi-mac-setup
//   DomovoiSetup-mac.manifest.json       {kind, schema, tag, version, commit, file, size, sha256}
//   DomovoiSetup-mac.manifest.json.sig   over the manifest, namespace domovoi-mac-setup-manifest
//
// The public half of the release key is the one the install pinned: the
// package puts windows/installer/release-signing.pub (or, for a test build,
// the throwaway key build.sh was given) at share/installer/release-signing.pub,
// root:wheel 0644 in a folder only root can change. A build without it has
// no key, and no Mac step.
//
// MacSetupCheck.run is the whole decision: both signatures by the pinned key
// under their own namespaces, the manifest naming the release asked for (tag
// and commit) and a version newer than the installed one, and the package's
// size and SHA-256 the manifest's. Any "no" comes with plain words. The app
// checks its download with it, and the root helper checks its own copy again.

public enum MacReleaseNames {
    /// Where releases come from unless share/build.json names another
    /// repository (a test build for the update train: a fork).
    public static let defaultRepo = "coders-farm-official/domovoi"
    public static let package = "DomovoiSetup-mac.pkg"
    public static let packageSig = "DomovoiSetup-mac.pkg.sig"
    public static let manifest = "DomovoiSetup-mac.manifest.json"
    public static let manifestSig = "DomovoiSetup-mac.manifest.json.sig"
    public static let packageNamespace = "domovoi-mac-setup"
    public static let manifestNamespace = "domovoi-mac-setup-manifest"
    public static let manifestKind = "domovoi-mac-setup"
    /// The pinned key, relative to share/.
    public static let keyFile = "installer/release-signing.pub"
    /// The four files a Mac step downloads, small ones first.
    public static let all = [manifest, manifestSig, packageSig, package]

    public static func isTag(_ t: String) -> Bool { Pattern.full(t, #"v[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(-[0-9A-Za-z.]{1,40})?"#) }

    /// owner/name, as GitHub allows them.
    public static func isRepo(_ r: String) -> Bool { Pattern.full(r, #"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}"#) && !r.hasSuffix("/.") && !r.contains("/..") }

    /// https://github.com/<repo>/releases/download/<tag>/<asset>.
    public static func url(repo: String, tag: String, asset: String) throws -> String {
        guard isRepo(repo) else { throw HelperError("not a repository: \(repo)") }
        guard isTag(tag) else { throw HelperError("not a release tag: \(tag)") }
        guard all.contains(asset) else { throw HelperError("not a Mac release file: \(asset)") }
        return "https://github.com/\(repo)/releases/download/\(tag)/\(asset)"
    }
}

/// Whole-string regular expressions (ICU's `$` would also match before a
/// final newline, so the end is `\z`).
enum Pattern {
    static func full(_ s: String, _ pattern: String) -> Bool {
        guard let re = try? NSRegularExpression(pattern: "^(?:" + pattern + ")\\z") else { return false }
        return re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }
}

/// A release version: X.Y.Z, optionally -suffix (rc1 < rc2 < the release
/// itself), compared the way SemVer orders them. The same rules as
/// `ReleaseVersion` in ReleaseSignature.cs and `windows_side.compare_versions`.
public enum ReleaseVersion {
    static let shape = #"v?([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})(?:-([0-9A-Za-z.]{1,40}))?(?:\+[0-9A-Za-z.]*)?"#

    static func parts(_ v: String) -> (Int, Int, Int, String?)? {
        guard let re = try? NSRegularExpression(pattern: "^" + shape + "\\z"),
              let m = re.firstMatch(in: v, range: NSRange(v.startIndex..., in: v)) else { return nil }
        func g(_ i: Int) -> String? {
            guard let r = Range(m.range(at: i), in: v) else { return nil }
            return String(v[r])
        }
        guard let a = g(1).flatMap(Int.init), let b = g(2).flatMap(Int.init), let c = g(3).flatMap(Int.init) else { return nil }
        return (a, b, c, g(4))
    }

    public static func isValid(_ v: String) -> Bool { parts(v) != nil }

    /// <0, 0, >0; nil when either isn't a version.
    public static func compare(_ a: String, _ b: String) -> Int? {
        guard let x = parts(a), let y = parts(b) else { return nil }
        for (p, q) in [(x.0, y.0), (x.1, y.1), (x.2, y.2)] where p != q { return p < q ? -1 : 1 }
        switch (x.3, y.3) {
        case (nil, nil): return 0
        case (nil, _): return 1      // a release sorts after its candidates
        case (_, nil): return -1
        case let (px?, py?):
            let ix = px.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
            let iy = py.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
            for i in 0..<min(ix.count, iy.count) {
                let c = ident(ix[i], iy[i])
                if c != 0 { return c }
            }
            return ix.count == iy.count ? 0 : (ix.count < iy.count ? -1 : 1)
        }
    }

    /// "rc2" against "rc10": the letters, then the number after them.
    static func ident(_ a: String, _ b: String) -> Int {
        func split(_ s: String) -> (String, String)? {
            let letters = s.prefix { $0.isASCII && $0.isLetter }
            let rest = s.dropFirst(letters.count)
            guard rest.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
            return (String(letters), String(rest))
        }
        if let sa = split(a), let sb = split(b) {
            let (la, na) = sa, (lb, nb) = sb
            if la != lb { return la < lb ? -1 : 1 }
            if na.isEmpty || nb.isEmpty { return na.count == nb.count ? 0 : (na.count < nb.count ? -1 : 1) }
            // Up to 40 digits: compare as numbers without overflowing.
            let ta = String(na.drop { $0 == "0" }), tb = String(nb.drop { $0 == "0" })
            if ta.count != tb.count { return ta.count < tb.count ? -1 : 1 }
            return ta == tb ? 0 : (ta < tb ? -1 : 1)
        }
        return a == b ? 0 : (a < b ? -1 : 1)
    }
}

/// SSH wire strings: uint32 length (big-endian), then the bytes.
struct SSHReader {
    let b: [UInt8]
    var pos = 0
    var bad = false

    init(_ data: Data) { b = [UInt8](data) }

    var atEnd: Bool { !bad && pos == b.count }

    mutating func uint32() -> UInt32 {
        guard !bad, pos + 4 <= b.count else { bad = true; return 0 }
        let v = UInt32(b[pos]) << 24 | UInt32(b[pos + 1]) << 16 | UInt32(b[pos + 2]) << 8 | UInt32(b[pos + 3])
        pos += 4
        return v
    }

    mutating func raw(_ n: Int) -> Data? {
        guard !bad, n >= 0, pos + n <= b.count else { bad = true; return nil }
        let d = Data(b[pos..<(pos + n)])
        pos += n
        return d
    }

    mutating func bytes() -> Data? {
        let n = uint32()
        guard !bad, Int(n) <= b.count - pos else { bad = true; return nil }
        return raw(Int(n))
    }

    mutating func text() -> String? { bytes().map { String(decoding: $0, as: UTF8.self) } }

    static func string(_ s: Data) -> Data {
        let n = UInt32(s.count)
        return Data([UInt8(n >> 24 & 0xff), UInt8(n >> 16 & 0xff), UInt8(n >> 8 & 0xff), UInt8(n & 0xff)]) + s
    }
}

/// An OpenSSH public key line, "ssh-ed25519 <base64> [comment]".
public enum SSHKey {
    /// The 32 raw bytes of the first key line (comments and blank lines
    /// skipped), or nil when it is no Ed25519 public key.
    public static func parseEd25519(_ text: String) -> Data? {
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let l = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if l.isEmpty || l.hasPrefix("#") { continue }
            let parts = l.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard parts.count >= 2, parts[0] == "ssh-ed25519", let blob = Data(base64Encoded: String(parts[1])) else { return nil }
            var r = SSHReader(blob)
            let type = r.text()
            let key = r.bytes()
            guard type == "ssh-ed25519", let k = key, k.count == 32, r.atEnd else { return nil }
            return k
        }
        return nil
    }
}

/// An armored SSHSIG ("-----BEGIN SSH SIGNATURE-----"), checked against one
/// pinned key and one namespace.
public enum SSHSig {
    static let begin = "-----BEGIN SSH SIGNATURE-----"
    static let end = "-----END SSH SIGNATURE-----"
    static let magic = Data("SSHSIG".utf8)

    /// Nil when `message` carries a good signature by `key` under `namespace`;
    /// otherwise why not, in plain words.
    public static func problem(_ armored: Data, message: Data, key: Data?, namespace: String) -> String? {
        problem(armored, digest: { alg in
            switch alg {
            case "sha512": return Data(SHA512.hash(data: message))
            case "sha256": return Data(SHA256.hash(data: message))
            default: return nil
            }
        }, key: key, namespace: namespace)
    }

    /// The same for a file, hashed in pieces (the package is ~100 MB).
    public static func problem(_ armored: Data, file: URL, key: Data?, namespace: String) -> String? {
        problem(armored, digest: { alg in try? FileDigest.of(file, alg) }, key: key, namespace: namespace)
    }

    static func problem(_ armored: Data, digest: (String) -> Data?, key: Data?, namespace ns: String) -> String? {
        guard let key, key.count == 32 else { return "there is no release key to check it with" }
        guard let blob = unarmor(armored) else { return "its signature file isn't an SSH signature" }
        var r = SSHReader(blob)
        let m = r.raw(6)
        let version = r.uint32()
        let pub = r.bytes()
        let sigNs = r.text()
        let reserved = r.bytes()
        let hashAlg = r.text()
        let sigBlob = r.bytes()
        guard !r.bad, r.atEnd, m == magic, version == 1, let pub, let sigNs, let reserved, let hashAlg, let sigBlob else {
            return "its signature file isn't an SSH signature"
        }
        var pr = SSHReader(pub)
        let pubType = pr.text()
        let pubKey = pr.bytes()
        guard !pr.bad, pr.atEnd, pubType == "ssh-ed25519", let pubKey else { return "it isn't signed with an Ed25519 key" }
        guard same(pubKey, key) else { return "it is signed with a key other than Domovoi's release key" }
        guard sigNs == ns else { return "its signature is for something else (\"\(safe(sigNs))\", not \"\(ns)\")" }
        var sr = SSHReader(sigBlob)
        let sigType = sr.text()
        let sig = sr.bytes()
        guard !sr.bad, sr.atEnd, sigType == "ssh-ed25519", let sig, sig.count == 64 else { return "its signature is malformed" }
        guard hashAlg == "sha512" || hashAlg == "sha256" else { return "its signature uses an unknown hash (\(safe(hashAlg)))" }
        guard let h = digest(hashAlg) else { return "the file couldn't be read to check it" }
        var signed = magic
        signed += SSHReader.string(Data(sigNs.utf8))
        signed += SSHReader.string(reserved)
        signed += SSHReader.string(Data(hashAlg.utf8))
        signed += SSHReader.string(h)
        guard let pk = try? Curve25519.Signing.PublicKey(rawRepresentation: key) else { return "there is no release key to check it with" }
        return pk.isValidSignature(sig, for: signed) ? nil : "its signature doesn't match the file (it was changed, or signed by someone else)"
    }

    static func unarmor(_ data: Data) -> Data? {
        let t = String(decoding: data, as: UTF8.self).replacingOccurrences(of: "\r", with: "")
        guard let a = t.range(of: begin), let z = t.range(of: end), a.upperBound <= z.lowerBound else { return nil }
        let body = t[a.upperBound..<z.lowerBound].replacingOccurrences(of: "\n", with: "").trimmingCharacters(in: .whitespaces)
        return Data(base64Encoded: body)
    }

    static func same(_ a: Data, _ b: Data) -> Bool {
        guard a.count == b.count else { return false }
        var d: UInt8 = 0
        for (x, y) in zip(a, b) { d |= x ^ y }
        return d == 0
    }

    static func safe(_ s: String) -> String {
        String(s.unicodeScalars.filter { $0.value >= 32 && $0.value < 127 }.prefix(60).map { Character($0) })
    }
}

/// SHA-256 / SHA-512 of a file, read in pieces.
public enum FileDigest {
    public static func of(_ url: URL, _ alg: String) throws -> Data {
        guard let h = FileHandle(forReadingAtPath: url.path) else { throw HelperError("can't open \(url.path)") }
        defer { try? h.close() }
        var s256 = SHA256(), s512 = SHA512()
        while true {
            let chunk = try h.read(upToCount: 4 << 20) ?? Data()
            if chunk.isEmpty { break }
            if alg == "sha512" { s512.update(data: chunk) } else { s256.update(data: chunk) }
        }
        return alg == "sha512" ? Data(s512.finalize()) : Data(s256.finalize())
    }

    public static func sha256Hex(_ url: URL) throws -> String { try of(url, "sha256").map { String(format: "%02x", $0) }.joined() }
}

public struct MacSetupManifest: Equatable {
    public var tag: String
    public var version: String
    public var commit: String
    public var file: String
    public var sha256: String
    public var size: Int64

    /// The manifest's fields, or why not.
    public static func parse(_ data: Data) -> (MacSetupManifest?, String?) {
        guard let any = try? JSONSerialization.jsonObject(with: data), let d = any as? [String: Any] else {
            return (nil, "its manifest isn't readable")
        }
        func str(_ k: String) -> String? { d[k] as? String }
        func int(_ k: String) -> Int64? {
            // A JSON true/false is an NSNumber too, of type "c" (char).
            guard let n = d[k] as? NSNumber, n.objCType.pointee != 0x63 else { return nil }
            let v = n.int64Value
            return NSNumber(value: v) == n ? v : nil
        }
        guard str("kind") == MacReleaseNames.manifestKind, int("schema") == 1 else { return (nil, "its manifest is for something else") }
        guard let tag = str("tag"), let version = str("version"), MacReleaseNames.isTag(tag), ReleaseVersion.isValid(version), tag == "v" + version else {
            return (nil, "its manifest names no release")
        }
        guard let commit = str("commit"), Pattern.full(commit, "[0-9a-f]{40}") else { return (nil, "its manifest names no commit") }
        guard str("file") == MacReleaseNames.package, let sha = str("sha256"), Pattern.full(sha, "[0-9a-f]{64}"), let size = int("size"), size > 0 else {
            return (nil, "its manifest doesn't describe a Mac package")
        }
        return (MacSetupManifest(tag: tag, version: version, commit: commit, file: MacReleaseNames.package, sha256: sha, size: size), nil)
    }
}

/// What the Mac step asks of a download.
public struct MacSetupExpectation {
    /// The release the core named.
    public var tag: String
    /// Its commit, as the Linux side's checkout has it (nil: not checked).
    public var commit: String?
    /// The installed Mac side's version (share/build.json).
    public var installed: String?
    /// The pinned release key (32 bytes), or nil.
    public var key: Data?

    public init(tag: String, commit: String?, installed: String?, key: Data?) {
        self.tag = tag
        self.commit = commit
        self.installed = installed
        self.key = key
    }
}

/// The four files of a Mac step, in one folder.
public struct MacSetupFiles {
    public var manifest: Data?
    public var manifestSig: Data?
    public var packageSig: Data?
    /// The downloaded package (hashed from disk).
    public var package: URL?

    public init(manifest: Data?, manifestSig: Data?, packageSig: Data?, package: URL?) {
        self.manifest = manifest
        self.manifestSig = manifestSig
        self.packageSig = packageSig
        self.package = package
    }
}

public enum MacSetupCheck {
    // Why a download may not be installed, for the card
    // (domovoi/windows_side.py OUTCOMES).
    public static let noKey = "no_key"
    public static let noSignedSetup = "no_signed_setup"
    public static let badSignature = "bad_signature"
    public static let notNewer = "not_newer"

    public struct Verdict: Equatable {
        /// Nil when the files are what was asked for and may be installed;
        /// else why not, as a sentence's end ("Domovoi didn't install it: " + this).
        public var problem: String?
        /// One of the four codes above when there is a problem.
        public var code: String?
        public var manifest: MacSetupManifest?
        public var ok: Bool { problem == nil }
    }

    static func no(_ code: String, _ why: String, _ m: MacSetupManifest? = nil) -> Verdict { Verdict(problem: why, code: code, manifest: m) }

    public static func run(_ want: MacSetupExpectation, _ f: MacSetupFiles) -> Verdict {
        guard let key = want.key, key.count == 32 else { return no(noKey, "this Mac side pins no release key, so it can't check a package") }
        guard let manifest = f.manifest, let manifestSig = f.manifestSig else { return no(noSignedSetup, "the release has no signed manifest") }
        guard let pkg = f.package, let pkgSig = f.packageSig else { return no(noSignedSetup, "the release has no package signature") }
        if let why = SSHSig.problem(manifestSig, message: manifest, key: key, namespace: MacReleaseNames.manifestNamespace) {
            return no(badSignature, "its manifest can't be trusted: " + why)
        }
        let (parsed, why) = MacSetupManifest.parse(manifest)
        guard let m = parsed else { return no(badSignature, why ?? "its manifest isn't readable") }
        guard m.tag == want.tag else { return no(badSignature, "its manifest is for \(m.tag), not \(want.tag)", m) }
        if let c = want.commit, m.commit != c {
            return no(badSignature, "it was built from \(m.commit.prefix(12)), not \(want.tag)'s commit \(c.prefix(12))", m)
        }
        guard let installed = want.installed, ReleaseVersion.isValid(installed) else {
            // Fail closed: what can't be compared is never installed.
            return no(notNewer, "the installed Mac side's version isn't known (\(want.installed ?? "none")), so Domovoi can't tell this one is newer; open the newer DomovoiSetup-mac.pkg by hand", m)
        }
        guard let c = ReleaseVersion.compare(m.version, installed), c > 0 else {
            return no(notNewer, "it is version \(m.version), which isn't newer than the installed \(installed)", m)
        }
        if let why = SSHSig.problem(pkgSig, file: pkg, key: key, namespace: MacReleaseNames.packageNamespace) {
            return no(badSignature, "the package can't be trusted: " + why, m)
        }
        let size = (try? FileManager.default.attributesOfItem(atPath: pkg.path)[.size] as? NSNumber)?.int64Value ?? -1
        guard size == m.size else { return no(badSignature, "the package is \(size) bytes, its manifest says \(m.size)", m) }
        guard let sha = try? FileDigest.sha256Hex(pkg), sha == m.sha256 else {
            return no(badSignature, "the package's SHA-256 isn't the one its manifest names", m)
        }
        return Verdict(problem: nil, code: nil, manifest: m)
    }
}

/// The release key and the facts an update needs from what the package
/// installed (share/): believed only from files root owns that nobody else
/// can change, in folders only root can change.
public enum PinnedRelease {
    /// Owned by root and writable by nobody else (group write only for
    /// wheel or admin: an administrator can change the install anyway), and
    /// the same for each folder above it up to `/`. A symbolic link anywhere
    /// is refused. The tests pass their own rule.
    public static func rootOnly(_ url: URL) -> Bool {
        func safe(_ path: String, file: Bool) -> Bool {
            var st = stat()
            guard lstat(path, &st) == 0 else { return false }
            let type = st.st_mode & S_IFMT
            guard file ? type == S_IFREG : type == S_IFDIR else { return false }
            guard st.st_uid == 0, st.st_mode & S_IWOTH == 0 else { return false }
            return st.st_mode & S_IWGRP == 0 || st.st_gid == 0 || st.st_gid == 80
        }
        let std = url.standardizedFileURL
        guard safe(std.path, file: true) else { return false }
        var d = std.deletingLastPathComponent()
        while true {
            guard safe(d.path, file: false) else { return false }
            if d.path == "/" { return true }
            d = d.deletingLastPathComponent()
        }
    }

    /// The pinned key's 32 bytes, or nil (none, unreadable, or not root's).
    public static func key(share: URL, trust: (URL) -> Bool = rootOnly) -> Data? {
        let u = share.appendingPathComponent(MacReleaseNames.keyFile)
        guard trust(u), let text = try? String(contentsOf: u, encoding: .utf8) else { return nil }
        return SSHKey.parseEd25519(text)
    }

    /// The installed Mac side (share/build.json), believed the same way.
    public static func build(share: URL, trust: (URL) -> Bool = rootOnly) -> BuildInfo? {
        let u = share.appendingPathComponent("build.json")
        guard trust(u) else { return nil }
        return try? BuildInfo.load(share)
    }
}
