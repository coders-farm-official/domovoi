import CryptoKit
import Darwin
import Foundation

// Writes a card the way the server's write_overlay does
// (domovoi/satellite_media/overlay.py), as the Windows tray's
// windows/tray/CardWriter.cs does: domovoi/ and userconf.txt copied from the
// zip, domovoi/ap.json written for a Wi-Fi setup card, and the config.txt and
// cmdline.txt edits merged (CardText). Plain file writes on the card's boot
// partition and nowhere else: nothing is formatted, nothing outside that
// one volume is touched.
//
// Order: free space first; then the files the first-boot hook runs; then
// config.txt and cmdline.txt last, so a card pulled out halfway never boots
// into a hook whose files aren't there. Every file is read back and checked.
//
// On a Mac every write goes through one descriptor for the card's volume,
// opened once and checked (FAT, mounted from the partition diskutil named,
// at the path the menu named): names below it are opened with O_NOFOLLOW,
// so a volume swapped under the path afterwards is never written. Each file
// is flushed to the card itself (F_FULLFSYNC) before the next. write(2)
// adds no extended attributes, so there are no AppleDouble "._" files;
// stray ones beside Domovoi's files (a Finder copy made them) are removed.

public struct CardWriteError: Error, CustomStringConvertible, Equatable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var description: String { message }

    /// macOS's Files & Folders permission said no. Unsigned (test) builds
    /// lose that permission with every update, so it can come back.
    public static let accessWords = "macOS didn't let Domovoi use the card. In System Settings > Privacy & Security > Files & Folders, "
        + "allow Domovoi to use Removable Volumes, then try again. (Test builds of Domovoi ask for this again after each update.)"
}

/// The card's volume, by one open descriptor.
public final class CardVolume {
    let fd: Int32
    public let path: String

    /// Opens `path` with no link anywhere on the way (O_NOFOLLOW per part).
    public init(path: String) throws {
        guard path.hasPrefix("/") else { throw HelperError("\(path): not an absolute path") }
        var cur = open("/", O_RDONLY | O_DIRECTORY)
        guard cur >= 0 else { throw HelperError("can't open /") }
        for comp in path.split(separator: "/") {
            let c = String(comp)
            guard c != ".", c != ".." else { close(cur); throw HelperError("\(path): no . or .. in the card's path") }
            let next = openat(cur, c, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            let e = errno
            close(cur)
            guard next >= 0 else {
                if e == EPERM || e == EACCES { throw CardWriteError(CardWriteError.accessWords) }
                throw CardWriteError("The card isn't there any more. Put it back in and try again.")
            }
            cur = next
        }
        fd = cur
        self.path = path
    }

    deinit { close(fd) }

    /// (folder descriptor, name) for "name" or "domovoi/name"; the caller
    /// closes the folder when `owned`.
    func parent(_ rel: String, create: Bool = false) throws -> (dir: Int32, name: String, owned: Bool) {
        let parts = rel.split(separator: "/").map(String.init)
        guard parts.count == 1 || parts.count == 2, parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
            throw HelperError("not a card path: \(rel)")
        }
        if parts.count == 1 { return (fd, parts[0], false) }
        if create && mkdirat(fd, parts[0], 0o755) != 0 && errno != EEXIST { throw Self.posix("make the \(parts[0]) folder on the card") }
        let d = openat(fd, parts[0], O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard d >= 0 else { throw Self.posix("open the \(parts[0]) folder on the card") }
        return (d, parts[1], true)
    }

    /// A POSIX failure in household words.
    static func posix(_ what: String) -> CardWriteError {
        let e = errno
        switch e {
        case EPERM: return CardWriteError(CardWriteError.accessWords)
        case EROFS, EACCES:
            return CardWriteError("macOS won't let Domovoi write to the card. If the card or its adapter has a lock switch, slide it to unlocked, then try again.")
        case ENOSPC: return CardWriteError("The card is full. Flash it again with Raspberry Pi OS Lite (64-bit), which leaves room, then try again.")
        case ENOENT, ENXIO, ENODEV, EIO:
            return CardWriteError("macOS couldn't \(what) (\(String(cString: strerror(e)))). Check the card is still in, then try again.")
        default:
            return CardWriteError("macOS couldn't \(what) (\(String(cString: strerror(e)))).")
        }
    }

    public func exists(_ rel: String) -> Bool {
        guard let p = try? parent(rel) else { return false }
        defer { if p.owned { close(p.dir) } }
        var st = stat()
        return fstatat(p.dir, p.name, &st, AT_SYMLINK_NOFOLLOW) == 0 && st.st_mode & 0o170000 == 0o100000
    }

    public func read(_ rel: String, max: Int = 1 << 30) throws -> [UInt8] {
        var out: [UInt8] = []
        try stream(rel) { out.append(contentsOf: $0); if out.count > max { throw CardSourceTooBig() } }
        return out
    }

    public func stream(_ rel: String, _ chunk: ([UInt8]) throws -> Void) throws {
        let (d, n, owned) = try parent(rel)
        defer { if owned { close(d) } }
        let f = openat(d, n, O_RDONLY | O_NOFOLLOW)
        guard f >= 0 else { throw Self.posix("read \(rel) on the card") }
        defer { close(f) }
        var buf = [UInt8](repeating: 0, count: 1 << 20)
        while true {
            let got = buf.withUnsafeMutableBytes { Darwin.read(f, $0.baseAddress, $0.count) }
            if got < 0 { if errno == EINTR { continue }; throw Self.posix("read \(rel) on the card") }
            if got == 0 { break }
            try chunk(Array(buf[0..<got]))
        }
    }

    /// Replaces `rel` whole: `fill` gets a write function; then flushed to
    /// the card itself.
    public func write(_ rel: String, _ fill: (_ put: ([UInt8]) throws -> Void) throws -> Void) throws {
        let (d, n, owned) = try parent(rel, create: true)
        defer { if owned { close(d) } }
        let f = openat(d, n, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0o644)
        guard f >= 0 else { throw Self.posix("write \(rel) on the card") }
        defer { close(f) }
        try fill { bytes in
            var off = 0
            while off < bytes.count {
                let w = bytes.withUnsafeBytes { Darwin.write(f, $0.baseAddress! + off, bytes.count - off) }
                if w < 0 { if errno == EINTR { continue }; throw Self.posix("write \(rel) on the card") }
                off += w
            }
        }
        // F_FULLFSYNC: onto the card, not just into its reader's cache.
        if fcntl(f, F_FULLFSYNC) != 0 && fsync(f) != 0 { throw Self.posix("write \(rel) on the card") }
    }

    public func writeBytes(_ rel: String, _ bytes: [UInt8]) throws { try write(rel) { try $0(bytes) } }

    public func names(in folder: String) -> [String] {
        let d = openat(fd, folder, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard d >= 0 else { return [] }
        defer { close(d) }
        return ((try? CardFolderSource.entries(d)) ?? []).map { $0.name }
    }

    public func remove(_ rel: String) {
        guard let p = try? parent(rel) else { return }
        defer { if p.owned { close(p.dir) } }
        _ = unlinkat(p.dir, p.name, 0)
    }

    /// Free space on the volume, or nil when macOS won't say.
    public func freeBytes() -> Int64? {
        var s = statfs()
        guard fstatfs(fd, &s) == 0 else { return nil }
        return Int64(s.f_bavail) * Int64(s.f_bsize)
    }
}

/// What the open card volume is: its file system, the device it is
/// mounted from, and where.
public protocol VolumeIdentity: AnyObject {
    func identify(_ v: CardVolume) -> (fsType: String, from: String, on: String)?
}

/// fstatfs on the volume's descriptor.
public final class StatfsIdentity: VolumeIdentity {
    public init() {}

    public func identify(_ v: CardVolume) -> (fsType: String, from: String, on: String)? {
        var s = statfs()
        guard fstatfs(v.fd, &s) == 0 else { return nil }
        func str<T>(_ t: T) -> String {
            withUnsafePointer(to: t) { p in p.withMemoryRebound(to: CChar.self, capacity: MemoryLayout<T>.size) { String(cString: $0) } }
        }
        return (str(s.f_fstypename), str(s.f_mntfromname), str(s.f_mntonname))
    }
}

/// A card the zip's edits can't make a Domovoi card of.
public enum CardChecks {
    /// Another first-boot script already on the kernel line (Raspberry Pi
    /// Imager's settings add one): Domovoi's edits keep it, the way the
    /// server's do, so Domovoi's would never run.
    public static func cmdlineProblem(_ cmdline: [UInt8], tokens: [CardText.Text]) -> String? {
        let after = CardText.editCmdline(CardText.read(cmdline), wanted: tokens)
        if !CardText.pySplit(after).contains(CardText.text(CardText.firstrunToken)) {
            return "This card already runs another first-boot script (Raspberry Pi Imager's settings add one), so Domovoi's setup would never start. "
                + "Flash it again, and when Imager offers to apply OS customisation settings, choose No."
        }
        return nil
    }

    /// user:$6$salt$hash and a newline: what overlay.write_overlay writes,
    /// and the only shape Pi OS's first boot reads.
    public static let userconfLine = try! NSRegularExpression(pattern: #"^[a-z_][a-z0-9_-]{0,31}:\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}\n\z"#)

    public static func userconfOK(_ s: String) -> Bool {
        userconfLine.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }
}

public struct CardWriteResult: Equatable {
    public var written: [String] = []
    public var checked: [String] = []
    public var removed: [String] = []
    public var configChanged = false
    public var cmdlineChanged = false
}

public enum CardWriter {
    /// The server's margin over the payload (builder.py: + 8 MiB).
    public static let margin: Int64 = 8 * 1024 * 1024
    static let payload = "domovoi/payload.tar.gz"

    public static func checkFree(_ free: Int64, needed: Int64) throws {
        if free < needed {
            throw CardWriteError("The card has \(CardPackage.mib(free)) free, and the room's files need \(CardPackage.mib(needed)). "
                + "Flash it again with Raspberry Pi OS Lite (64-bit), which leaves room, then try again.")
        }
    }

    static func hex(_ h: SHA256) -> String { h.finalize().map { String(format: "%02x", $0) }.joined() }

    static func sha(_ v: CardVolume, _ rel: String) throws -> String {
        var h = SHA256()
        try v.stream(rel) { h.update(data: Data($0)) }
        return hex(h)
    }

    /// `psk`: the setup network's password (Wi-Fi setup cards); `userconf`:
    /// the userconf.txt line for a console login the server made again,
    /// or nil for the zip's own. `progress(message, percent)`.
    public static func write(_ pkg: CardPackage, from src: CardSource, to vol: CardVolume, psk: String?, userconf: String? = nil,
                             progress: (String, Int) -> Void = { _, _ in }) throws -> CardWriteResult {
        var result = CardWriteResult()
        guard vol.exists("config.txt"), vol.exists("cmdline.txt"), vol.exists(pkg.bootMarker) else {
            throw CardWriteError("This card no longer looks like a freshly flashed Raspberry Pi card. Put the card back in and try again.")
        }
        var ap: [UInt8]?
        if pkg.isPortal {
            let typed = psk ?? ""
            if let why = CardSetupPassword.problem(typed) { throw CardWriteError(why) }
            guard let b = CardApJson.bytes(ssid: pkg.apSsid ?? "", psk: CardSetupPassword.normalize(typed)) else {
                throw CardWriteError("These Wi-Fi setup card files don't name their setup network. Build them again in the dashboard.")
            }
            ap = b
        }
        var login: [UInt8]?
        if let u = userconf {
            guard CardChecks.userconfOK(u) else {
                throw CardWriteError("The console login Domovoi sent isn't one a Raspberry Pi can use. Try again, or prepare the card again on the dashboard.")
            }
            login = Array(u.utf8)
        }
        let config0 = try vol.read("config.txt", max: 1 << 20), cmdline0 = try vol.read("cmdline.txt", max: 1 << 20)
        if let why = CardChecks.cmdlineProblem(cmdline0, tokens: pkg.cmdlineTokens) { throw CardWriteError(why) }

        progress("Checking the free space on the card", 2)
        guard let free = vol.freeBytes() else { throw CardWriteError("macOS can't say how much space the card has. Put it back in and try again.") }
        try checkFree(free, needed: pkg.copyBytes + margin)

        var hashes: [String: String] = [:]
        var order = pkg.files
        // The payload first (the big one), the hook's files after it.
        order.sort { ($0.entry == payload ? 0 : 1, $0.entry) < ($1.entry == payload ? 0 : 1, $1.entry) }
        var done: Int64 = 0
        let all = max(1, pkg.copyBytes)
        for f in order {
            if login != nil && f.dest == "userconf.txt" { continue }   // the new one goes in below
            guard try src.size(f.entry) == f.size else { throw CardWriteError("The card files changed while the card was being written. Pick the zip again.") }
            progress("Copying \(f.dest)", 5 + Int(80 * done / all))
            var h = SHA256()
            var n: Int64 = 0
            try vol.write(f.dest) { put in
                try src.stream(f.entry) { chunk in
                    h.update(data: Data(chunk))
                    n += Int64(chunk.count)
                    try put(chunk)
                }
            }
            guard n == f.size else { throw CardWriteError("The card files changed while the card was being written. Pick the zip again.") }
            hashes[f.dest] = hex(h)
            if f.entry == payload && hashes[f.dest] != pkg.payloadSha256 {
                throw CardWriteError("The room software in the zip is damaged (its SHA-256 doesn't match). Download the card files again.")
            }
            result.written.append(f.dest)
            done += f.size
        }
        if let l = login {
            progress("Writing the new console login (userconf.txt)", 86)
            try vol.writeBytes("userconf.txt", l)
            hashes["userconf.txt"] = SHA256Hex.of(Data(l))
            result.written.append("userconf.txt")
        }
        if let a = ap {
            progress("Writing the setup network password (domovoi/ap.json)", 86)
            try vol.writeBytes(CardZip.apJson, a)
            result.written.append(CardZip.apJson)
        }
        // Finder's AppleDouble files beside Domovoi's (an earlier copy by
        // hand made them): not Domovoi's, and not wanted on the card.
        for name in vol.names(in: "domovoi") where name.hasPrefix("._") || name == ".DS_Store" {
            vol.remove("domovoi/" + name)
            result.removed.append("domovoi/" + name)
        }

        // Every copied file read back and checked BEFORE the boot hook goes
        // in: a card that fails here never starts Domovoi's setup.
        progress("Reading the copied files back to check them", 86)
        for (dest, want) in hashes.sorted(by: { $0.key < $1.key }) {
            guard try sha(vol, dest) == want else {
                throw CardWriteError("\(dest) on the card isn't what was written. The card may be failing: try another one.")
            }
            result.checked.append(dest)
        }
        let sumText = String(decoding: try vol.read("domovoi/payload.sha256", max: 1 << 16), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let onCard = try sha(vol, payload)
        guard sumText.hasPrefix(onCard + " "), onCard == pkg.payloadSha256 else {
            throw CardWriteError("The room software on the card doesn't match its checksum. The card may be failing: try another one.")
        }
        result.checked.append("domovoi/payload.tar.gz against payload.sha256")
        if let a = ap {
            guard try vol.read(CardZip.apJson, max: 1 << 16) == a else { throw CardWriteError("domovoi/ap.json on the card isn't what was written. Try another card.") }
            result.checked.append(CardZip.apJson)
        }

        progress("Adding Domovoi's lines to config.txt and cmdline.txt", 92)
        let block = pkg.configBlock, tokens = pkg.cmdlineTokens
        let cfg = CardText.apply(config0, exists: true) { CardText.editConfig($0, block: block) }
        if cfg.changed { try vol.writeBytes("config.txt", cfg.bytes); result.written.append("config.txt") }
        result.configChanged = cfg.changed
        let cmd = CardText.apply(cmdline0, exists: true) { CardText.editCmdline($0, wanted: tokens) }
        if cmd.changed { try vol.writeBytes("cmdline.txt", cmd.bytes); result.written.append("cmdline.txt") }
        result.cmdlineChanged = cmd.changed

        progress("Reading config.txt and cmdline.txt back to check them", 96)
        let cfgBack = try vol.read("config.txt", max: 1 << 20), cmdBack = try vol.read("cmdline.txt", max: 1 << 20)
        let cfgText = CardText.read(cfgBack), cmdText = CardText.read(cmdBack)
        guard cfgBack == cfg.bytes, CardText.editConfig(cfgText, block: block) == cfgText else {
            throw CardWriteError("config.txt on the card isn't what was written. Try another card.")
        }
        guard cmdBack == cmd.bytes, CardText.editCmdline(cmdText, wanted: tokens) == cmdText else {
            throw CardWriteError("cmdline.txt on the card isn't what was written. Try another card.")
        }
        result.checked.append("config.txt")
        result.checked.append("cmdline.txt")
        progress("Done", 100)
        return result
    }
}

/// The whole card write, in the menu-bar app as the signed-in account
/// (owner decision 2026-11-03: no administrator prompt, as on Windows; the
/// card is that account's own mounted volume): diskutil asked again about
/// the card just before writing, its volume opened once and identified,
/// the write and read-back, then the eject. The setup password stays in
/// memory.
public final class CardJob {
    /// The unpacked card files, in the zip's private work folder.
    public static let filesName = "files"

    let runner: CommandRunner
    let files: CardFiles
    let identity: VolumeIdentity
    let volumesRoot: String

    public init(runner: CommandRunner, files: CardFiles = LocalCardFiles(), identity: VolumeIdentity = StatfsIdentity(), volumesRoot: String = "/Volumes") {
        self.runner = runner
        self.files = files
        self.identity = identity
        self.volumesRoot = volumesRoot
    }

    public struct Result: Equatable {
        public var checked: Int
        public var written: [String]
        public var ejected: Bool
        /// Why the eject didn't work (the card is written and checked).
        public var ejectProblem: String?
    }

    /// `volume`/`partition`: the card the person picked (CardTarget);
    /// `psk`: the setup password (Wi-Fi setup cards); `userconf`: a console
    /// login the server made again. `progress(percent, message)`.
    public func run(_ pkg: CardPackage, from src: CardSource, volume: String, partition: String, psk: String?, userconf: String? = nil,
                    progress: (Int, String) -> Void = { _, _ in }) throws -> Result {
        if pkg.isPortal && psk == nil { throw CardWriteError("The setup network password is missing. Nothing was written.") }
        let disks = CardDisks(runner: runner, files: files, volumesRoot: volumesRoot)
        progress(1, "Checking the card")
        let target = try disks.recheck(volume: volume, partition: partition, marker: pkg.bootMarker)
        let result: CardWriteResult
        do {
            // Scoped: the volume's descriptor is closed before the eject.
            let vol = try CardVolume(path: volume)
            guard let id = identity.identify(vol), id.fsType == "msdos", id.from == "/dev/" + partition, id.on == volume else {
                throw CardWriteError("The card changed while Domovoi was checking it. Put it back in and try again. Nothing was written.")
            }
            result = try CardWriter.write(pkg, from: src, to: vol, psk: psk, userconf: userconf, progress: { text, pct in progress(pct, text) })
        }
        progress(100, "Ejecting the card")
        let problem = disks.eject(target)
        return Result(checked: result.checked.count, written: result.written, ejected: problem == nil, ejectProblem: problem)
    }
}
