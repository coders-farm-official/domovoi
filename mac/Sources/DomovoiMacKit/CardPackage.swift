import CryptoKit
import Darwin
import Foundation

// The card files the dashboard builds (Settings > Rooms > prepare a room's
// SD card > the zip download), read and checked before anything goes near a
// card: the Mac port of the Windows tray's windows/tray/CardZip.cs, for the
// menu-bar card helper (mac-installer PHASE8-PARITY.md 2.5).
//
// The server writes the zip in domovoi/satellite_media/builder.py:
// README.txt, config.txt and cmdline.txt (only Domovoi's additions),
// userconf.txt (the console password's hash) and domovoi/ (firstrun.sh,
// payload.tar.gz, payload.sha256, build-info.json, device-info.json,
// server-identity.json, and claim.json on a Wi-Fi setup card). No passwords:
// the setup network's one is fetched with claim.json's one-time pass
// (CardKeys) or typed, and domovoi/ap.json is written from it. claim.json
// never goes on the card.
//
// On a Mac the zip is unpacked by the system's own tools (zipinfo for the
// names, ditto to unpack) into a private folder, and the checks run on that
// folder through a CardSource, which reads every file below one folder
// descriptor and never follows a link (a zip can carry symbolic links, and
// ditto makes them). It all runs in the menu-bar app as the signed-in
// account: no administrator, as on Windows.

public struct CardZipError: Error, CustomStringConvertible, Equatable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var description: String { message }
}

public struct CardClaim: Equatable {
    public var jobId: Int
    public var secret: String
    public var expiresAt: Date?
}

public struct CardFileEntry: Equatable {
    /// The name in the zip (and in the unpacked folder).
    public var entry: String
    /// The path on the card, relative, with "/".
    public var dest: String
    public var size: Int64
}

public struct CardPackage {
    public var board = ""
    public var boardLabel = ""
    /// The board's .dtb on a flashed card.
    public var bootMarker = ""
    public var micProfile = ""
    /// "portal" or "usb".
    public var setupTransport = "usb"
    /// Portal cards: the setup network's name.
    public var apSsid: String?
    public var builtAt = ""
    public var offline = false
    public var configBlock: CardText.Text = []
    public var cmdlineTokens: [CardText.Text] = []
    public var payloadSha256 = ""
    /// domovoi/server-identity.json, or nil.
    public var serverFingerprint: String?
    /// domovoi/claim.json, or nil (a USB card, an older zip).
    public var claim: CardClaim?
    public var files: [CardFileEntry] = []

    public var isPortal: Bool { setupTransport == "portal" }

    public var copyBytes: Int64 { files.reduce(0) { $0 + $1.size } }

    public func summary() -> String {
        jobLine() + ". For " + boardLabel + (isPortal ? ", set up over Wi-Fi (\(apSsid ?? ""))" : ", adopted over USB")
            + ". " + CardPackage.mib(copyBytes) + " to copy."
    }

    /// Which dashboard build these files are, the way its row in Settings >
    /// Rooms reads (#17 · pi02w · respeaker_2mic_hat_v2), and when.
    public func jobLine() -> String {
        "Dashboard build " + (claim.map { "#\($0.jobId) \u{00B7} " } ?? "") + board + " \u{00B7} " + micProfile + ", built " + when()
    }

    public func when() -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        var d = iso.date(from: builtAt)
        if d == nil {
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            d = iso.date(from: builtAt)
        }
        guard let date = d else { return builtAt }
        let f = DateFormatter()
        f.dateFormat = "d MMM yyyy, HH:mm"
        return f.string(from: date)
    }

    public static func mib(_ bytes: Int64) -> String {
        // In steps: Xcode 16's type checker gives up on the one-liner.
        let unit: Int64 = 1024 * 1024
        let whole: Int64 = (bytes + unit - 1) / unit
        return "\(whole) MB"
    }
}

/// Where the unpacked card files are read from (the tests answer with a
/// folder of their own).
public protocol CardSource: AnyObject {
    /// Every regular file, as "name" or "domovoi/name". Throws for a link,
    /// a device, a folder other than domovoi/, or a file the rules refuse.
    func names() throws -> [String]
    func size(_ name: String) throws -> Int64
    /// The whole file, refused when it is bigger than `max`.
    func read(_ name: String, max: Int) throws -> [UInt8]
    /// The file in pieces (the payload is tens of MB).
    func stream(_ name: String, _ chunk: ([UInt8]) throws -> Void) throws
}

public enum CardZip {
    public static let namePrefix = "domovoi-satellite-overlay"
    public static let readme = "README.txt"
    public static let apJson = "domovoi/ap.json"
    public static let claimJson = "domovoi/claim.json"
    public static let required = ["domovoi/build-info.json", "domovoi/payload.sha256", "domovoi/payload.tar.gz", "domovoi/firstrun.sh",
                                  "domovoi/device-info.json", "config.txt", "cmdline.txt"]
    /// Far bigger than any card zip the server builds (the payload is tens
    /// of MB); a zip that unpacks to more is refused before it is unpacked.
    public static let maxUnpacked: Int64 = 1 << 30
    public static let textMax = 1024 * 1024

    public static let zipinfo = "/usr/bin/zipinfo"
    public static let ditto = "/usr/bin/ditto"

    /// The boards the server prepares cards for (boards.py, BOARDS with
    /// supported=True), with the file a flashed card of each carries.
    public static let boards: [String: (label: String, marker: String)] = [
        "pi02w": ("Raspberry Pi Zero 2 W (and Pi 3/4/5)", "bcm2710-rpi-zero-2-w.dtb"),
    ]

    static func re(_ p: String) -> NSRegularExpression { try! NSRegularExpression(pattern: p) }
    static let sha256Line = re(#"^([0-9a-f]{64})[ \t]+\*?payload\.tar\.gz\s*\z"#)
    static let plainName = re(#"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\z"#)
    static let ssidPattern = re(#"^[\x21-\x7e]([\x20-\x7e]{0,30}[\x21-\x7e])?\z"#)
    static let claimSecret = re(#"^[A-Za-z0-9_-]{32,200}\z"#)
    /// Names Windows keeps for devices: refused here too, so a zip either
    /// helper accepts is one both accept.
    static let deviceName = try! NSRegularExpression(pattern: #"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?\z"#, options: [.caseInsensitive])

    static func matches(_ r: NSRegularExpression, _ s: String) -> Bool {
        r.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }

    static func again(_ what: String) -> CardZipError {
        CardZipError("This zip's \(what) isn't the one Domovoi writes. Download the card files again.")
    }

    // ─── names ─────────────────────────────────────────────────────────

    /// Where a name in the zip goes on the card, or nil for a name the card
    /// doesn't get (the README, the two files merged instead of copied,
    /// ap.json/console.json/claim.json). Throws for a name no Domovoi zip has.
    public static func dest(for entry: String) throws -> String? {
        if entry == readme || entry == "config.txt" || entry == "cmdline.txt" { return nil }
        if entry == "userconf.txt" { return "userconf.txt" }
        if entry.hasPrefix("domovoi/") {
            let name = String(entry.dropFirst(8))
            if matches(plainName, name) && !matches(deviceName, name) && !name.hasSuffix(".") {
                return name == "ap.json" || name == "console.json" || name == "claim.json" ? nil : "domovoi/" + name
            }
        }
        throw CardZipError("This zip holds \"\(entry)\", which a Domovoi card zip never does. Download the card files again from the dashboard.")
    }

    /// The zip's names, as `zipinfo -1` lists them, checked BEFORE anything
    /// is unpacked: every name one a Domovoi zip has (so nothing can land
    /// outside the folder: no "..", no absolute path, no __MACOSX/), none
    /// twice (the card is FAT: case and trailing dots and spaces don't
    /// count), the required ones there. Returns the file names.
    public static func checkNames(_ listed: [String]) throws -> [String] {
        var files: [String] = []
        var seen = Set<String>(), onCard = Set<String>()
        for name in listed where !name.isEmpty {
            if name == "domovoi/" { continue }   // the folder's own entry
            _ = try dest(for: name)
            var folded = name
            while let last = folded.last, last == "." || last == " " { folded.removeLast() }
            if !seen.insert(name).inserted || !onCard.insert(folded.lowercased()).inserted {
                throw CardZipError("This zip names \"\(name)\" twice. Download the card files again.")
            }
            files.append(name)
        }
        for r in required where !seen.contains(r) {
            throw CardZipError("This isn't a Domovoi room card zip (it has no \(r)). Download it from the dashboard: Settings > Rooms > prepare a room's SD card.")
        }
        return files
    }

    /// `zipinfo -t`'s last line: "7 files, 41945221 bytes uncompressed, ...".
    public static func unpackedBytes(_ zipinfoT: String) -> Int64? {
        let r = re(#"([0-9]+) files?, ([0-9]+) bytes uncompressed"#)
        let s = zipinfoT as NSString
        guard let m = r.firstMatch(in: zipinfoT, range: NSRange(location: 0, length: s.length)) else { return nil }
        return Int64(s.substring(with: m.range(at: 2)))
    }

    /// The newest card files in a folder, or nil: a zip (a browser names a
    /// second download "domovoi-satellite-overlay (1).zip"), or the folder
    /// Safari unpacks it to and keeps instead ("domovoi-satellite-overlay",
    /// "domovoi-satellite-overlay 2"; Train 1 finding 24: Safari opens
    /// "safe" downloads and deletes the zip). Owner decision: either one.
    public static func newest(in folder: URL) -> URL? {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: folder.path) else { return nil }
        var best: (URL, Date)?
        for n in names where n.hasPrefix(namePrefix) {
            let u = folder.appendingPathComponent(n)
            guard let a = try? fm.attributesOfItem(atPath: u.path), let t = a[.modificationDate] as? Date else { continue }
            let type = a[.type] as? FileAttributeType
            let zip = type == .typeRegular && n.lowercased().hasSuffix(".zip")
            let unpacked = type == .typeDirectory && isUnpacked(u)
            guard zip || unpacked else { continue }
            if best == nil || t > best!.1 { best = (u, t) }
        }
        return best?.0
    }

    /// A folder that looks like the unpacked card files (its own
    /// domovoi/build-info.json); the full checks come later.
    public static func isUnpacked(_ folder: URL) -> Bool {
        var dir: ObjCBool = false
        let info = folder.appendingPathComponent("domovoi/build-info.json").path
        return FileManager.default.fileExists(atPath: folder.path, isDirectory: &dir) && dir.boolValue
            && FileManager.default.fileExists(atPath: info)
    }

    /// Finder's own file, which appears in any folder someone opened.
    public static let finderFile = ".DS_Store"

    /// Copies an unpacked card folder into `into` (a folder that must not
    /// exist yet, inside a private one), with the same checks as a zip
    /// before anything is copied: only plain files at the two levels a card
    /// zip has (no links), the names a card zip may hold, every required
    /// one, and the size limit. ditto copies it with no extended
    /// attributes, resource forks, quarantine or ACLs; the copy is checked
    /// again when it is loaded, so a folder changed in between is caught.
    public static func copyFolder(_ folder: URL, into: URL, runner: CommandRunner) throws {
        let src: CardFolderSource
        do { src = try CardFolderSource(path: folder.path) } catch {
            throw CardZipError("That isn't a folder of card files.")
        }
        let listed = try src.names().filter { ($0 as NSString).lastPathComponent != finderFile }
        let names = try checkNames(listed)
        var total: Int64 = 0
        for n in names {
            total += try src.size(n)
            guard total <= maxUnpacked else { throw CardZipError("That folder is far too big to be Domovoi's card files.") }
        }
        let fm = FileManager.default
        guard !fm.fileExists(atPath: into.path) else { throw HelperError("\(into.path) is already there") }
        let r = try runner.run(Command(ditto, ["--norsrc", "--noextattr", "--noqtn", "--noacl", folder.path, into.path], timeout: 300))
        guard r.ok else { throw CardZipError("The card files couldn't be copied from that folder. Download them again from the dashboard.") }
        for f in [finderFile, "domovoi/" + finderFile] { try? fm.removeItem(at: into.appendingPathComponent(f)) }
    }

    /// Unpacks `zip` into `into` (a folder that must not exist yet, inside
    /// a private one), with only the system's tools: zipinfo lists the
    /// names and the unpacked size first, ditto unpacks with no extended
    /// attributes, resource forks, quarantine or ACLs.
    public static func unpack(_ zip: URL, into: URL, runner: CommandRunner) throws {
        let fm = FileManager.default
        guard let a = try? fm.attributesOfItem(atPath: zip.path), (a[.type] as? FileAttributeType) == .typeRegular else {
            throw CardZipError("There's no file there.")
        }
        if let size = a[.size] as? NSNumber, size.int64Value > maxUnpacked { throw CardZipError("That zip is far too big to be Domovoi's.") }
        let list = try runner.run(Command(zipinfo, ["-1", zip.path], timeout: 60))
        guard list.ok else { throw CardZipError("That file isn't a zip (or it's damaged). Download the card files again from the dashboard.") }
        _ = try checkNames(list.stdout.split(separator: "\n", omittingEmptySubsequences: false).map(String.init))
        let total = try runner.run(Command(zipinfo, ["-t", zip.path], timeout: 60))
        guard total.ok, let bytes = unpackedBytes(total.stdout) else { throw CardZipError("Part of that zip is damaged. Download the card files again from the dashboard.") }
        guard bytes <= maxUnpacked else { throw CardZipError("That zip is far too big to be Domovoi's.") }
        guard !fm.fileExists(atPath: into.path) else { throw HelperError("\(into.path) is already there") }
        let r = try runner.run(Command(ditto, ["-x", "-k", "--norsrc", "--noextattr", "--noqtn", "--noacl", zip.path, into.path], timeout: 300))
        guard r.ok else { throw CardZipError("Part of that zip is damaged. Download the card files again from the dashboard.") }
    }

    // ─── the checks on the unpacked files ──────────────────────────────

    /// Every check CardZip.cs makes, on the unpacked folder.
    public static func load(_ src: CardSource) throws -> CardPackage {
        let names = try checkNames(src.names())
        var pkg = CardPackage()
        for n in names {
            if let d = try dest(for: n) { pkg.files.append(CardFileEntry(entry: n, dest: d, size: try src.size(n))) }
        }
        pkg.files.sort { $0.entry < $1.entry }

        let info = try json(src, "domovoi/build-info.json", "build-info.json")
        guard int(info["schema"]) == 1 else {
            throw CardZipError("These card files come from a newer Domovoi than this card helper knows. Update Domovoi on this Mac.")
        }
        pkg.board = info["board"] as? String ?? ""
        guard let board = boards[pkg.board] else {
            throw CardZipError("These card files are for a board this card helper doesn't know (\"\(pkg.board)\").")
        }
        pkg.boardLabel = board.label
        pkg.bootMarker = board.marker
        pkg.micProfile = info["mic_profile"] as? String ?? ""
        pkg.builtAt = info["built_at"] as? String ?? ""
        pkg.offline = bool(info["offline"]) ?? false

        let dev = try json(src, "domovoi/device-info.json", "device-info.json")
        pkg.setupTransport = dev["setup_transport"] as? String ?? "usb"
        guard pkg.setupTransport == "usb" || pkg.setupTransport == "portal" else {
            throw CardZipError("These card files ask for a setup route this card helper doesn't know (\"\(pkg.setupTransport)\").")
        }
        if pkg.isPortal {
            guard let s = dev["ap_ssid"] as? String, matches(ssidPattern, s) else {
                throw CardZipError("These Wi-Fi setup card files don't name their setup network. Build them again in the dashboard.")
            }
            pkg.apSsid = s
        }

        guard let block = CardText.blockFromZip(try text(src, "config.txt", "config.txt")) else { throw again("config.txt") }
        pkg.configBlock = block
        guard let tokens = CardText.tokensFromZip(try text(src, "cmdline.txt", "cmdline.txt")) else { throw again("cmdline.txt") }
        pkg.cmdlineTokens = tokens

        let sumText = try text(src, "domovoi/payload.sha256", "payload.sha256")
        let ns = sumText as NSString
        guard let m = sha256Line.firstMatch(in: sumText, range: NSRange(location: 0, length: ns.length)) else { throw again("payload.sha256") }
        pkg.payloadSha256 = ns.substring(with: m.range(at: 1))
        var hasher = SHA256()
        try src.stream("domovoi/payload.tar.gz") { hasher.update(data: Data($0)) }
        let actual = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard actual == pkg.payloadSha256 else {
            throw CardZipError("The room software in this zip is damaged (its SHA-256 doesn't match). Download the card files again.")
        }
        if names.contains("domovoi/server-identity.json") {
            pkg.serverFingerprint = try json(src, "domovoi/server-identity.json", "server-identity.json")["fingerprint"] as? String
        }
        if pkg.isPortal && names.contains(claimJson) {
            pkg.claim = readClaim(try json(src, claimJson, "claim.json"))
        }
        return pkg
    }

    /// A pass that isn't the shape the server writes is left out (the
    /// helper then asks for the password, as for an older zip) rather than
    /// refusing card files that are otherwise fine.
    static func readClaim(_ d: [String: Any]) -> CardClaim? {
        guard int(d["schema"]) == 1, let job = int(d["job_id"]), job > 0, let secret = d["secret"] as? String, matches(claimSecret, secret) else { return nil }
        var c = CardClaim(jobId: job, secret: secret, expiresAt: nil)
        if let e = d["expires_at"] as? String {
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime]
            c.expiresAt = iso.date(from: e)
            if c.expiresAt == nil {
                iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                c.expiresAt = iso.date(from: e)
            }
        }
        return c
    }

    /// Whether the card files were built by the Domovoi on this Mac: the
    /// identity they carry against the one the dashboard's /api/health
    /// reports. Not a signature (the fingerprint is public); it stops files
    /// that came from somewhere else.
    public enum Origin: Equatable { case thisServer, otherServer, unknown }

    public static func origin(zip: String?, server: String?) -> Origin {
        guard let z = zip, !z.isEmpty, let s = server, !s.isEmpty else { return .unknown }
        return z == s ? .thisServer : .otherServer
    }

    /// UTF-8 text, strictly (a byte that isn't UTF-8 means it isn't text).
    static func text(_ src: CardSource, _ name: String, _ what: String) throws -> String {
        let b: [UInt8]
        do { b = try src.read(name, max: textMax) } catch is CardSourceTooBig { throw CardZipError("This zip's \(what) is far too big to be Domovoi's.") }
        let s = String(decoding: b, as: UTF8.self)
        guard Array(s.utf8) == b else { throw CardZipError("This zip's \(what) isn't text.") }
        return s
    }

    static func json(_ src: CardSource, _ name: String, _ what: String) throws -> [String: Any] {
        let t = try text(src, name, what)
        guard let o = try? JSONSerialization.jsonObject(with: Data(t.utf8)), let d = o as? [String: Any] else {
            throw CardZipError("This zip's \(what) isn't readable. Download the card files again.")
        }
        return d
    }

    /// A JSON whole number (never true/false, which Foundation also bridges to 1/0).
    static func int(_ v: Any?) -> Int? {
        guard let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() else { return nil }
        let d = n.doubleValue
        guard d == d.rounded(), abs(d) < 1e15 else { return nil }
        return n.intValue
    }

    static func bool(_ v: Any?) -> Bool? {
        guard let n = v as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else { return nil }
        return n.boolValue
    }
}

/// A file bigger than a read allowed.
public struct CardSourceTooBig: Error {}

/// The unpacked folder, read through descriptors: every name is opened
/// with O_NOFOLLOW below a folder descriptor, and a file is used only when
/// it is a regular file.
public final class CardFolderSource: CardSource {
    let fd: Int32
    public let path: String

    static let ifmt: mode_t = 0o170000, ifreg: mode_t = 0o100000, ifdir: mode_t = 0o040000

    /// The unpacked folder, by path (the folder itself must not be a link).
    public init(path: String) throws {
        let parent = (path as NSString).deletingLastPathComponent
        let p = open(parent, O_RDONLY | O_DIRECTORY)
        guard p >= 0 else { throw HelperError("can't open \(parent)") }
        defer { close(p) }
        let f = openat(p, (path as NSString).lastPathComponent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard f >= 0 else { throw HelperError("\(path): not a plain folder (\(String(cString: strerror(errno))))") }
        fd = f
        self.path = path
    }

    deinit { close(fd) }

    static func entries(_ dirfd: Int32) throws -> [(name: String, mode: mode_t)] {
        let d = dup(dirfd)
        guard d >= 0, let dir = fdopendir(d) else { throw HelperError("can't list the card files") }
        defer { closedir(dir) }
        rewinddir(dir)
        var out: [(name: String, mode: mode_t)] = []
        while let e = readdir(dir) {
            let name = withUnsafePointer(to: e.pointee.d_name) { p in
                p.withMemoryRebound(to: CChar.self, capacity: 1024) { String(cString: $0) }
            }
            if name == "." || name == ".." { continue }
            var st = stat()
            guard fstatat(dirfd, name, &st, AT_SYMLINK_NOFOLLOW) == 0 else { throw HelperError("can't look at \(name) in the card files") }
            out.append((name, st.st_mode))
        }
        return out
    }

    public func names() throws -> [String] {
        var out: [String] = []
        for e in try Self.entries(fd) {
            let kind = e.mode & Self.ifmt
            if kind == Self.ifdir && e.name == "domovoi" {
                let sub = openat(fd, "domovoi", O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
                guard sub >= 0 else { throw HelperError("can't open the card files' domovoi folder") }
                defer { close(sub) }
                for s in try Self.entries(sub) {
                    guard s.mode & Self.ifmt == Self.ifreg else {
                        throw CardZipError("This zip holds \"domovoi/\(s.name)\", which a Domovoi card zip never does. Download the card files again from the dashboard.")
                    }
                    out.append("domovoi/" + s.name)
                }
            } else if kind == Self.ifreg {
                out.append(e.name)
            } else {
                throw CardZipError("This zip holds \"\(e.name)\", which a Domovoi card zip never does. Download the card files again from the dashboard.")
            }
        }
        return out.sorted()
    }

    /// An open, checked descriptor for a file; the caller closes it.
    func openChecked(_ name: String) throws -> (fd: Int32, size: Int64) {
        let parts = name.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard (parts.count == 1 || (parts.count == 2 && parts[0] == "domovoi")), let last = parts.last, !last.isEmpty, last != ".", last != ".." else {
            throw HelperError("not a card file name: \(name)")
        }
        var dirfd = fd
        var opened: Int32 = -1
        if parts.count == 2 {
            opened = openat(fd, "domovoi", O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            guard opened >= 0 else { throw HelperError("can't open the card files' domovoi folder") }
            dirfd = opened
        }
        defer { if opened >= 0 { close(opened) } }
        let f = openat(dirfd, last, O_RDONLY | O_NOFOLLOW)
        guard f >= 0 else { throw HelperError("can't open \(name) in the card files (\(String(cString: strerror(errno))))") }
        var st = stat()
        guard fstat(f, &st) == 0, st.st_mode & Self.ifmt == Self.ifreg else {
            close(f)
            throw HelperError("\(name) in the card files isn't a plain file")
        }
        return (f, Int64(st.st_size))
    }

    public func size(_ name: String) throws -> Int64 {
        let (f, size) = try openChecked(name)
        close(f)
        return size
    }

    public func read(_ name: String, max: Int) throws -> [UInt8] {
        var out: [UInt8] = []
        try stream(name) { chunk in
            out.append(contentsOf: chunk)
            if out.count > max { throw CardSourceTooBig() }
        }
        return out
    }

    public func stream(_ name: String, _ chunk: ([UInt8]) throws -> Void) throws {
        let (f, _) = try openChecked(name)
        defer { close(f) }
        var buf = [UInt8](repeating: 0, count: 1 << 20)
        while true {
            let n = buf.withUnsafeMutableBytes { Darwin.read(f, $0.baseAddress, $0.count) }
            if n < 0 {
                if errno == EINTR { continue }
                throw HelperError("can't read \(name) in the card files (\(String(cString: strerror(errno))))")
            }
            if n == 0 { break }
            try chunk(Array(buf[0..<n]))
        }
    }
}
