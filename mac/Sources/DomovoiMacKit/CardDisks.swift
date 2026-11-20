import Darwin
import Foundation

// Which disks the card helper offers: removable media only (an SD card in
// a reader or the Mac's own SD slot, a USB stick), never a disk inside the
// Mac, never the disk it starts from, never a disk image or an APFS disk,
// and only when its mounted volume looks like a freshly flashed Raspberry
// Pi boot partition (FAT, with config.txt, cmdline.txt and the board's
// .dtb: the server's overlay.looks_like_pi_boot, the Windows tray's
// CardDrives.cs). The facts come from `diskutil list -plist` and `diskutil
// info -plist` through a CommandRunner, so the tests answer for diskutil.
//
// diskutil is asked again about the one card picked, with the same rules,
// just before the write (CardJob): a card swapped in the reader while the
// window sat on its list is never written.

/// One disk or partition, as `diskutil info -plist` describes it.
public struct DiskFacts: Equatable {
    public var id = ""
    public var wholeDisk = false
    public var parent: String?
    /// Unknown counts as inside the Mac (never offered).
    public var isInternal = true
    public var removable = false
    public var ejectable = false
    public var virtual = false
    public var bus: String?
    public var fsType: String?
    public var fsName: String?
    public var content: String?
    public var apfs = false
    public var mountPoint: String?
    public var volumeName: String?
    public var mediaName: String?
    public var size: Int64 = 0
    public var free: Int64 = 0
    public var writable = false
    /// APFS containers: the partitions holding them.
    public var physicalStores: [String] = []

    public init() {}

    public static func parse(_ d: [String: Any]) -> DiskFacts {
        func b(_ k: String) -> Bool? { (d[k] as? NSNumber).map(\.boolValue) }
        func s(_ k: String) -> String? { (d[k] as? String).flatMap { $0.isEmpty ? nil : $0 } }
        func n(_ k: String) -> Int64? { (d[k] as? NSNumber).map(\.int64Value) }
        var f = DiskFacts()
        f.id = s("DeviceIdentifier") ?? ""
        f.wholeDisk = b("WholeDisk") ?? false
        f.parent = s("ParentWholeDisk")
        f.isInternal = b("Internal") ?? true
        f.removable = b("RemovableMedia") ?? b("Removable") ?? false
        f.ejectable = b("Ejectable") ?? false
        f.bus = s("BusProtocol")
        f.virtual = s("VirtualOrPhysical") == "Virtual" || f.bus == "Disk Image" || (b("SystemImage") ?? false)
        f.fsType = s("FilesystemType")
        f.fsName = s("FilesystemName") ?? s("FilesystemUserVisibleName")
        f.content = s("Content")
        f.mountPoint = s("MountPoint")
        f.volumeName = s("VolumeName")
        f.mediaName = s("MediaName") ?? s("IORegistryEntryName")
        f.size = n("TotalSize") ?? n("Size") ?? n("IOKitSize") ?? 0
        f.free = n("FreeSpace") ?? n("APFSContainerFree") ?? 0
        f.writable = b("WritableVolume") ?? b("Writable") ?? false
        if let stores = d["APFSPhysicalStores"] as? [[String: Any]] {
            f.physicalStores = stores.compactMap { $0["APFSPhysicalStore"] as? String }
        }
        f.apfs = f.fsType == "apfs" || d["APFSContainerReference"] != nil || d["APFSPhysicalStores"] != nil
            || f.content == "Apple_APFS" || f.content == "Apple_APFS_Container"
        return f
    }

    /// "disk4s1" -> "disk4"; "disk3s1s1" -> "disk3".
    public static func wholeID(_ id: String) -> String? {
        let r = try! NSRegularExpression(pattern: #"^(disk[0-9]+)"#)
        let ns = id as NSString
        guard let m = r.firstMatch(in: id, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range(at: 1))
    }
}

/// A card the helper can write.
public struct CardTarget: Equatable {
    public var partition: DiskFacts
    public var whole: DiskFacts
    /// Domovoi's marker is already in its config.txt.
    public var prepared: Bool

    public var volume: String { partition.mountPoint ?? "" }
    public var name: String { partition.volumeName ?? (volume as NSString).lastPathComponent }

    /// The card's capacity the way its label says it (decimal GB: a "32
    /// GB" card holds about 31.9 billion bytes); "?" when unknown.
    public static func cardSize(_ bytes: Int64) -> String {
        guard bytes > 0 else { return "?" }
        let gb = Double(bytes) / 1e9
        return String(format: gb >= 10 ? "%.0f GB" : "%.1f GB", gb)
    }

    public static func bytes(_ b: Int64) -> String {
        guard b > 0 else { return "" }
        if b >= 1 << 30 { return String(format: "%.1f GB", Double(b) / Double(1 << 30)) }
        return CardPackage.mib(b)
    }

    /// For the list: "bootfs: 32 GB card (Apple SDXC Reader)".
    public func title() -> String {
        var t = "\(name): \(Self.cardSize(whole.size)) card"
        if let m = whole.mediaName { t += " (\(m))" }
        return t
    }

    /// What the confirmation says, in plain words.
    public func confirmText() -> String {
        "Write Domovoi's files to \u{201C}\(name)\u{201D}, the \(Self.cardSize(whole.size)) card"
            + (whole.mediaName.map { " in \($0)" } ?? "") + " (\(whole.id))?"
    }
}

/// What a look at the Mac's disks found.
public struct CardScan: Equatable {
    public var targets: [CardTarget] = []
    /// Removable disks left out: not a freshly flashed Pi card.
    public var others = 0
    /// macOS didn't let this app look at a removable volume (its
    /// Files & Folders permission).
    public var accessDenied = false
    /// diskutil didn't answer.
    public var failed = false

    public init() {}
}

/// macOS refused to let this app read a removable volume.
public struct CardAccessDenied: Error {}

/// The few file looks the card checks need, so the tests can answer.
public protocol CardFiles: AnyObject {
    /// Throws CardAccessDenied when macOS won't let this program look.
    func exists(_ path: String) throws -> Bool
    func read(_ path: String, max: Int) throws -> [UInt8]
}

public final class LocalCardFiles: CardFiles {
    public init() {}

    public func exists(_ path: String) throws -> Bool {
        var st = stat()
        if lstat(path, &st) == 0 { return st.st_mode & 0o170000 == 0o100000 }
        if errno == EPERM || errno == EACCES { throw CardAccessDenied() }
        return false
    }

    public func read(_ path: String, max: Int) throws -> [UInt8] {
        let f = open(path, O_RDONLY | O_NOFOLLOW)
        guard f >= 0 else {
            if errno == EPERM || errno == EACCES { throw CardAccessDenied() }
            throw HelperError("can't read \(path)")
        }
        defer { close(f) }
        var out: [UInt8] = []
        var buf = [UInt8](repeating: 0, count: 65536)
        while true {
            let n = buf.withUnsafeMutableBytes { Darwin.read(f, $0.baseAddress, $0.count) }
            if n < 0 { if errno == EINTR { continue }; throw HelperError("can't read \(path)") }
            if n == 0 { break }
            out.append(contentsOf: buf[0..<n])
            if out.count > max { throw CardSourceTooBig() }
        }
        return out
    }
}

public final class CardDisks {
    public static let diskutil = "/usr/sbin/diskutil"
    /// SDXC tops out at 2 TB: anything bigger isn't an SD card.
    public static let maxCardBytes: Int64 = 2_200_000_000_000

    let runner: CommandRunner
    let files: CardFiles
    /// Where macOS mounts removable volumes (a test folder in the tests).
    let volumesRoot: String

    public init(runner: CommandRunner, files: CardFiles, volumesRoot: String = "/Volumes") {
        self.runner = runner
        self.files = files
        self.volumesRoot = volumesRoot
    }

    func plist(_ args: [String]) -> [String: Any]? {
        guard let r = try? runner.run(Command(Self.diskutil, args, timeout: 20)), r.ok,
              let o = try? PropertyListSerialization.propertyList(from: Data(r.stdout.utf8), options: [], format: nil) else { return nil }
        return o as? [String: Any]
    }

    public func info(_ id: String) -> DiskFacts? { plist(["info", "-plist", id]).map(DiskFacts.parse) }

    /// The whole disks this Mac starts from: the one holding "/" and the
    /// physical disks under its APFS container.
    public func bootDisks() -> Set<String> {
        var out = Set<String>()
        guard let root = info("/") else { return out }
        for id in [root.parent, DiskFacts.wholeID(root.id)].compactMap({ $0 }) {
            out.insert(id)
            if let c = info(id) {
                for s in c.physicalStores { if let w = DiskFacts.wholeID(s) { out.insert(w) } }
            }
        }
        return out
    }

    /// Every mounted partition or volume in `diskutil list -plist`, with
    /// its whole disk.
    func mounted() -> [(id: String, whole: String)]? {
        guard let l = plist(["list", "-plist"]), let all = l["AllDisksAndPartitions"] as? [[String: Any]] else { return nil }
        var out: [(id: String, whole: String)] = []
        for d in all {
            guard let whole = d["DeviceIdentifier"] as? String else { continue }
            if d["MountPoint"] as? String != nil { out.append((whole, whole)) }
            for key in ["Partitions", "APFSVolumes"] {
                for p in d[key] as? [[String: Any]] ?? [] {
                    if let id = p["DeviceIdentifier"] as? String, p["MountPoint"] as? String != nil { out.append((id, whole)) }
                }
            }
        }
        return out
    }

    /// Why a disk is never offered at all (not even counted among the
    /// removable ones left out), or nil.
    public static func neverOffered(_ p: DiskFacts, _ w: DiskFacts, boot: Set<String>) -> String? {
        if boot.contains(w.id) || (p.parent.map { boot.contains($0) } ?? false) { return "it's the disk this Mac starts from" }
        if w.isInternal && w.bus != "Secure Digital" { return "it's a disk inside this Mac" }
        if p.virtual || w.virtual { return "it's a disk image, not a card" }
        if !((w.removable || p.removable) && (w.ejectable || p.ejectable)) { return "it isn't removable media (an SD card or a USB stick)" }
        return nil
    }

    /// Why a removable disk isn't a card the helper writes, or nil.
    public func notACard(_ p: DiskFacts, _ w: DiskFacts) -> String? {
        if p.wholeDisk || p.id == w.id { return "it's a whole disk, not a Raspberry Pi card's boot partition" }
        if p.apfs || w.apfs { return "it's an APFS disk (a Mac disk), not a Raspberry Pi card" }
        if p.parent != nil && p.parent != w.id { return "diskutil gave its disk two different names" }
        guard let m = p.mountPoint, Self.directlyUnder(volumesRoot, m) else { return "it isn't mounted where macOS mounts cards" }
        if p.fsType != "msdos" { return "it isn't FAT (a Raspberry Pi card's boot partition is FAT)" }
        if w.size > Self.maxCardBytes { return "it's too big to be an SD card" }
        return nil
    }

    static func directlyUnder(_ root: String, _ path: String) -> Bool {
        guard path.hasPrefix(root + "/") else { return false }
        let rest = String(path.dropFirst(root.count + 1))
        return !rest.isEmpty && !rest.contains("/") && rest != "." && rest != ".."
    }

    /// config.txt, cmdline.txt and the board's .dtb (overlay.looks_like_pi_boot).
    func looksLikeBoot(_ mount: String, marker: String) throws -> Bool {
        try files.exists(mount + "/config.txt") && files.exists(mount + "/cmdline.txt") && (marker.isEmpty || files.exists(mount + "/" + marker))
    }

    func prepared(_ mount: String) -> Bool {
        guard let b = try? files.read(mount + "/config.txt", max: 1 << 20) else { return false }
        return CardText.contains(CardText.read(b), CardText.text(CardText.marker))
    }

    /// The cards to offer, by volume name, and how many other removable
    /// disks were left out.
    public func scan(marker: String) -> CardScan {
        var scan = CardScan()
        guard let list = mounted() else { scan.failed = true; return scan }
        let boot = bootDisks()
        var wholes: [String: DiskFacts] = [:]
        for (id, wholeID) in list {
            guard let p = info(id) else { continue }
            let w: DiskFacts
            if let c = wholes[wholeID] { w = c } else {
                guard let c = info(wholeID) else { continue }
                wholes[wholeID] = c
                w = c
            }
            if Self.neverOffered(p, w, boot: boot) != nil { continue }
            if notACard(p, w) != nil || !p.writable { scan.others += 1; continue }
            do {
                guard try looksLikeBoot(p.mountPoint!, marker: marker) else { scan.others += 1; continue }
            } catch is CardAccessDenied {
                scan.accessDenied = true
                continue
            } catch {
                scan.others += 1
                continue
            }
            scan.targets.append(CardTarget(partition: p, whole: w, prepared: prepared(p.mountPoint!)))
        }
        scan.targets.sort { ($0.name.lowercased(), $0.volume) < ($1.name.lowercased(), $1.volume) }
        return scan
    }

    /// The last look at the card picked, just before the write: the same
    /// rules, asked of diskutil afresh. Throws in household words.
    public func recheck(volume: String, partition: String, marker: String) throws -> CardTarget {
        let nothing = " Nothing was written."
        guard let p = info(partition), p.id == partition else { throw CardWriteError("The card isn't there any more. Put it back in and try again." + nothing) }
        guard p.mountPoint == volume else { throw CardWriteError("The card moved (macOS mounted it somewhere else). Pick it again." + nothing) }
        guard let wid = p.parent ?? DiskFacts.wholeID(p.id), let w = info(wid) else {
            throw CardWriteError("macOS can't say which disk the card is on. Put it back in and try again." + nothing)
        }
        if let why = Self.neverOffered(p, w, boot: bootDisks()) ?? notACard(p, w) {
            throw CardWriteError("\u{201C}\(p.volumeName ?? volume)\u{201D} isn't a card Domovoi writes: \(why)." + nothing)
        }
        if !p.writable {
            throw CardWriteError("The card is locked (read-only). If the card or its adapter has a lock switch, slide it to unlocked, then try again." + nothing)
        }
        let looks: Bool
        do { looks = try looksLikeBoot(volume, marker: marker) } catch is CardAccessDenied { throw CardWriteError(CardWriteError.accessWords + nothing) }
        guard looks else {
            throw CardWriteError("This card no longer looks like a freshly flashed Raspberry Pi card. Put the card back in and try again." + nothing)
        }
        return CardTarget(partition: p, whole: w, prepared: prepared(volume))
    }

    /// `diskutil eject` of the card's whole disk. Nil when it worked, else
    /// why not.
    public func eject(_ t: CardTarget) -> String? {
        guard let r = try? runner.run(Command(Self.diskutil, ["eject", t.whole.id], timeout: 60)) else { return "diskutil didn't start" }
        if r.ok { return nil }
        let tail = r.tail
        return tail.isEmpty ? "diskutil said no" : tail
    }

    /// What the card helper needs on this Mac: the system tools. Without
    /// them there is no "Prepare an SD card..." item.
    public static let tools = [diskutil, CardZip.zipinfo, CardZip.ditto]

    public static func available(isExecutable: (String) -> Bool) -> Bool {
        tools.allSatisfy(isExecutable)
    }
}
