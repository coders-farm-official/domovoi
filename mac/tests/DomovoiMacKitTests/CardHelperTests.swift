import XCTest
@testable import DomovoiMacKit

// The menu-bar card helper (CardPackage, CardDisks, CardWriter, CardKeys,
// CardJob) against fakes: diskutil answered from dictionaries, a folder
// posing as /Volumes/bootfs, the dashboard answered by a closure.

/// What the dashboard's zip unpacks to, in a folder.
enum CardFixture {
    static let block = CardText.marker + "\ndtparam=audio=on\ndtoverlay=googlevoicehat-soundcard\n" + CardText.endMarker + "\n"
    static let cmdline = CardText.firstrunToken + " systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n"
    static let payload = [UInt8]((0..<5000).map { UInt8(($0 * 7 + 3) & 0xff) })
    static let fingerprint = "SHA256:abcdefabcdefabcdefabcdefabcdef"
    static let ssid = "domovoi-setup-4f2a"
    static let userconf = "domovoi:$6$abcdefgh$" + String(repeating: "A", count: 86) + "\n"

    static func json(_ o: Any) -> Data { try! JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) }

    /// `files/` as the zip unpacks: a Wi-Fi setup card unless `portal` is false.
    static func write(_ dir: URL, portal: Bool = true, claim: Bool = true, board: String = "pi02w", schema: Int = 1) throws {
        let fm = FileManager.default
        let d = dir.appendingPathComponent("domovoi", isDirectory: true)
        try fm.createDirectory(at: d, withIntermediateDirectories: true)
        try Data("README\n".utf8).write(to: dir.appendingPathComponent("README.txt"))
        try Data(block.utf8).write(to: dir.appendingPathComponent("config.txt"))
        try Data(cmdline.utf8).write(to: dir.appendingPathComponent("cmdline.txt"))
        try Data(userconf.utf8).write(to: dir.appendingPathComponent("userconf.txt"))
        try Data(payload).write(to: d.appendingPathComponent("payload.tar.gz"))
        try Data((SHA256Hex.of(Data(payload)) + "  payload.tar.gz\n").utf8).write(to: d.appendingPathComponent("payload.sha256"))
        try Data("#!/bin/sh\necho hi\n".utf8).write(to: d.appendingPathComponent("firstrun.sh"))
        try json(["schema": schema, "board": board, "mic_profile": "respeaker_2mic_hat_v2", "built_at": "2026-11-03T10:00:00+00:00", "offline": false])
            .write(to: d.appendingPathComponent("build-info.json"))
        var dev: [String: Any] = ["setup_transport": portal ? "portal" : "usb"]
        if portal { dev["ap_ssid"] = ssid }
        try json(dev).write(to: d.appendingPathComponent("device-info.json"))
        try json(["fingerprint": fingerprint]).write(to: d.appendingPathComponent("server-identity.json"))
        if portal && claim {
            try json(["schema": 1, "job_id": 17, "secret": String(repeating: "s", count: 43), "expires_at": "2099-01-01T00:00:00Z"])
                .write(to: d.appendingPathComponent("claim.json"))
        }
    }

    /// A freshly flashed card's boot partition.
    static func card(_ dir: URL, cmdline: String = "console=serial0,115200 console=tty1 root=PARTUUID=1234-02 rootfstype=ext4 fsck.repair=yes rootwait\n") throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data("# Pi OS\narm_64bit=1\n".utf8).write(to: dir.appendingPathComponent("config.txt"))
        try Data(cmdline.utf8).write(to: dir.appendingPathComponent("cmdline.txt"))
        try Data([0xd0, 0x0d]).write(to: dir.appendingPathComponent("bcm2710-rpi-zero-2-w.dtb"))
        try Data([1, 2, 3]).write(to: dir.appendingPathComponent("start.elf"))
    }
}

/// diskutil, answered from dictionaries.
final class FakeDiskutil {
    var info: [String: [String: Any]] = [:]
    var list: [[String: Any]] = []
    var ejectFails = false
    var ejected: [String] = []

    static func plist(_ o: Any) -> String {
        String(decoding: try! PropertyListSerialization.data(fromPropertyList: o, format: .xml, options: 0), as: UTF8.self)
    }

    func install(on r: FakeRunner) {
        r.on { [unowned self] c in
            guard c.executable == CardDisks.diskutil else { return nil }
            let a = c.arguments
            switch a.first {
            case "list": return CommandResult(status: 0, stdout: Self.plist(["AllDisksAndPartitions": self.list]))
            case "info":
                guard let d = self.info[a.last ?? ""] else { return CommandResult(status: 1, stderr: "Could not find disk") }
                return CommandResult(status: 0, stdout: Self.plist(d))
            case "eject":
                if self.ejectFails { return CommandResult(status: 1, stderr: "Volume bootfs on disk4 failed to unmount: dissented by PID 0") }
                self.ejected.append(a.last ?? "")
                return CommandResult(status: 0, stdout: "Disk \(a.last ?? "") ejected")
            default: return CommandResult(status: 1)
            }
        }
    }

    /// The Mac's own disk: disk0 (internal SSD) holding the APFS container
    /// disk3, whose volume is "/".
    func addBootDisk() {
        info["/"] = ["DeviceIdentifier": "disk3s1s1", "ParentWholeDisk": "disk3", "WholeDisk": false, "Internal": true, "FilesystemType": "apfs",
                     "MountPoint": "/", "APFSContainerReference": "disk3", "RemovableMedia": false, "Ejectable": false, "BusProtocol": "Apple Fabric"]
        info["disk3"] = ["DeviceIdentifier": "disk3", "WholeDisk": true, "Internal": true, "APFSPhysicalStores": [["APFSPhysicalStore": "disk0s2"]],
                         "RemovableMedia": false, "Ejectable": false, "VirtualOrPhysical": "Virtual", "Size": 494_384_795_648]
        info["disk0"] = ["DeviceIdentifier": "disk0", "WholeDisk": true, "Internal": true, "RemovableMedia": false, "Ejectable": false,
                         "BusProtocol": "Apple Fabric", "VirtualOrPhysical": "Physical", "Size": 500_277_790_720]
        list.append(["DeviceIdentifier": "disk0", "Partitions": [["DeviceIdentifier": "disk0s1"], ["DeviceIdentifier": "disk0s2"]]])
        list.append(["DeviceIdentifier": "disk3", "APFSVolumes": [["DeviceIdentifier": "disk3s1s1", "MountPoint": "/"]]])
    }

    /// A card in a reader: whole disk `n`, FAT boot partition sN s1 at `mount`.
    func addCard(_ n: Int, mount: String, name: String = "bootfs", size: Int64 = 31_914_983_424, bus: String = "USB", isInternal: Bool = false,
                 removable: Bool = true, fs: String = "msdos", writable: Bool = true, media: String = "SD Card Reader") {
        let whole = "disk\(n)", part = "disk\(n)s1"
        info[whole] = ["DeviceIdentifier": whole, "WholeDisk": true, "Internal": isInternal, "RemovableMedia": removable, "Ejectable": removable,
                       "BusProtocol": bus, "VirtualOrPhysical": "Physical", "Size": size, "MediaName": media, "Content": "FDisk_partition_scheme"]
        info[part] = ["DeviceIdentifier": part, "ParentWholeDisk": whole, "WholeDisk": false, "Internal": isInternal, "RemovableMedia": removable,
                      "Ejectable": removable, "BusProtocol": bus, "VirtualOrPhysical": "Physical", "FilesystemType": fs,
                      "FilesystemName": fs == "msdos" ? "MS-DOS (FAT32)" : fs, "MountPoint": mount, "VolumeName": name,
                      "TotalSize": 535_805_952, "FreeSpace": 470_000_000, "WritableVolume": writable, "Content": "Windows_FAT_32"]
        list.append(["DeviceIdentifier": whole, "Partitions": [["DeviceIdentifier": part, "MountPoint": mount], ["DeviceIdentifier": "disk\(n)s2"]]])
    }
}

/// The volume's identity, as the test says.
final class FakeIdentity: VolumeIdentity {
    var answer: (fsType: String, from: String, on: String)?
    init(_ a: (fsType: String, from: String, on: String)?) { answer = a }
    func identify(_ v: CardVolume) -> (fsType: String, from: String, on: String)? { answer }
}

final class FakeKeys: CardKeys {
    var claimAnswer = KeyAnswer.got("k7tqmzr4wd")
    var adminAnswer = KeyAnswer.got("k7tqmzr4wd")
    var claims = 0
    func claim(_ c: CardClaim) -> KeyAnswer { claims += 1; return claimAnswer }
    func adminFetch(jobId: Int, ssid: String, adminPassword: String) -> KeyAnswer { adminAnswer }
}

final class CardPackageTests: XCTestCase {
    var dir: URL!
    override func setUpWithError() throws { dir = try realTempDir() }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func load(_ d: URL) throws -> CardPackage { try CardZip.load(try CardFolderSource(path: d.path)) }

    func testTheDashboardsFilesLoad() throws {
        let f = dir.appendingPathComponent("files")
        try CardFixture.write(f)
        let p = try load(f)
        XCTAssertTrue(p.isPortal)
        XCTAssertEqual(p.apSsid, CardFixture.ssid)
        XCTAssertEqual(p.bootMarker, "bcm2710-rpi-zero-2-w.dtb")
        XCTAssertEqual(p.claim?.jobId, 17)
        XCTAssertEqual(p.serverFingerprint, CardFixture.fingerprint)
        XCTAssertEqual(p.payloadSha256, SHA256Hex.of(Data(CardFixture.payload)))
        // What goes on the card: never README, ap.json, claim.json, or the two merged files.
        XCTAssertEqual(p.files.map(\.dest).sorted(), ["domovoi/build-info.json", "domovoi/device-info.json", "domovoi/firstrun.sh", "domovoi/payload.sha256",
                                                      "domovoi/payload.tar.gz", "domovoi/server-identity.json", "userconf.txt"])
        XCTAssertTrue(p.jobLine().hasPrefix("Dashboard build #17 \u{00B7} pi02w \u{00B7} respeaker_2mic_hat_v2, built "))
        XCTAssertEqual(CardZip.origin(zip: p.serverFingerprint, server: CardFixture.fingerprint), .thisServer)
        XCTAssertEqual(CardZip.origin(zip: p.serverFingerprint, server: "SHA256:other"), .otherServer)
        XCTAssertEqual(CardZip.origin(zip: p.serverFingerprint, server: nil), .unknown)
    }

    func testDamagedOrForeignFilesAreRefused() throws {
        let f = dir.appendingPathComponent("files")
        try CardFixture.write(f)
        try Data([9, 9, 9]).write(to: f.appendingPathComponent("domovoi/payload.tar.gz"))
        XCTAssertThrowsError(try load(f)) { XCTAssertTrue("\($0)".contains("damaged"), "\($0)") }

        let g = dir.appendingPathComponent("g")
        try CardFixture.write(g, board: "pi5x")
        XCTAssertThrowsError(try load(g)) { XCTAssertTrue("\($0)".contains("doesn't know"), "\($0)") }
        let h = dir.appendingPathComponent("h")
        try CardFixture.write(h, schema: 2)
        XCTAssertThrowsError(try load(h)) { XCTAssertTrue("\($0)".contains("newer Domovoi"), "\($0)") }
        let i = dir.appendingPathComponent("i")
        try CardFixture.write(i)
        try Data("quiet\n".utf8).write(to: i.appendingPathComponent("cmdline.txt"))
        XCTAssertThrowsError(try load(i)) { XCTAssertTrue("\($0)".contains("cmdline.txt isn't the one Domovoi writes"), "\($0)") }
    }

    func testANameNoZipHasIsRefusedBeforeUnpacking() throws {
        let good = CardZip.required + ["README.txt", "userconf.txt", "domovoi/", "domovoi/claim.json"]
        XCTAssertEqual(try CardZip.checkNames(good).count, good.count - 1)
        for bad in ["../evil", "/etc/x", "__MACOSX/._config.txt", "domovoi/a/b", "domovoi/../x", "other.txt", "domovoi/.hidden", "domovoi/CON.txt"] {
            XCTAssertThrowsError(try CardZip.checkNames(good + [bad]), bad)
        }
        XCTAssertThrowsError(try CardZip.checkNames(good + ["domovoi/FIRSTRUN.SH"])) { XCTAssertTrue("\($0)".contains("twice"), "\($0)") }
        XCTAssertThrowsError(try CardZip.checkNames(Array(good.dropFirst()))) { XCTAssertTrue("\($0)".contains("isn't a Domovoi room card zip"), "\($0)") }
        XCTAssertEqual(CardZip.unpackedBytes("Archive:  x.zip\nZip file size: 1 bytes, number of entries: 11\n11 files, 41945221 bytes uncompressed, 41000000 bytes compressed:  2.3%\n"), 41945221)
        XCTAssertNil(CardZip.unpackedBytes("nonsense"))
    }

    func testUnpackListsFirstAndUsesDittoWithoutExtras() throws {
        let zip = dir.appendingPathComponent("domovoi-satellite-overlay.zip")
        try Data("PK".utf8).write(to: zip)
        let r = FakeRunner()
        var names = CardZip.required + ["README.txt", "userconf.txt"]
        r.on { c in
            guard c.executable == CardZip.zipinfo else { return nil }
            if c.arguments.first == "-1" { return CommandResult(status: 0, stdout: names.joined(separator: "\n") + "\n") }
            return CommandResult(status: 0, stdout: "9 files, 1234 bytes uncompressed, 1000 bytes compressed:  19.0%\n")
        }
        try CardZip.unpack(zip, into: dir.appendingPathComponent("files"), runner: r)
        let d = try XCTUnwrap(r.ran("ditto").first)
        XCTAssertEqual(d.arguments, ["-x", "-k", "--norsrc", "--noextattr", "--noqtn", "--noacl", zip.path, dir.appendingPathComponent("files").path])

        names.append("../../etc/passwd")
        let r2 = FakeRunner()
        r2.on { c in c.executable == CardZip.zipinfo ? CommandResult(status: 0, stdout: names.joined(separator: "\n")) : nil }
        XCTAssertThrowsError(try CardZip.unpack(zip, into: dir.appendingPathComponent("files2"), runner: r2))
        XCTAssertTrue(r2.ran("ditto").isEmpty, "nothing is unpacked from a zip with a bad name")

        let r3 = FakeRunner()
        r3.on { c in
            guard c.executable == CardZip.zipinfo else { return nil }
            return c.arguments.first == "-1" ? CommandResult(status: 0, stdout: CardZip.required.joined(separator: "\n"))
                : CommandResult(status: 0, stdout: "9 files, 99999999999 bytes uncompressed, 1000 bytes compressed\n")
        }
        XCTAssertThrowsError(try CardZip.unpack(zip, into: dir.appendingPathComponent("files3"), runner: r3)) { XCTAssertTrue("\($0)".contains("too big"), "\($0)") }
        XCTAssertTrue(r3.ran("ditto").isEmpty)
    }

    func testALinkInTheFolderIsNeverFollowed() throws {
        // A zip can carry a symbolic link and ditto makes it: never read
        // through, so nothing outside the zip lands on the card.
        let f = dir.appendingPathComponent("files")
        try CardFixture.write(f)
        let elsewhere = dir.appendingPathComponent("elsewhere")
        try Data("not the zip's".utf8).write(to: elsewhere)
        let fw = f.appendingPathComponent("domovoi/firstrun.sh")
        try FileManager.default.removeItem(at: fw)
        try FileManager.default.createSymbolicLink(at: fw, withDestinationURL: elsewhere)
        XCTAssertThrowsError(try load(f), "a symbolic link is refused")
        let src = try CardFolderSource(path: f.path)
        XCTAssertThrowsError(try src.read("domovoi/firstrun.sh", max: 100))

        try FileManager.default.removeItem(at: fw)
        try Data("ok\n".utf8).write(to: fw)
        XCTAssertNoThrow(try load(f))
        // The unpacked folder itself as a link: refused too.
        let link = dir.appendingPathComponent("files-link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: f)
        XCTAssertThrowsError(try CardFolderSource(path: link.path))
    }
}

final class CardDiskTests: XCTestCase {
    var dir: URL!
    var volumes: URL!
    let marker = "bcm2710-rpi-zero-2-w.dtb"

    override func setUpWithError() throws {
        dir = try realTempDir()
        volumes = dir.appendingPathComponent("Volumes", isDirectory: true)
        try FileManager.default.createDirectory(at: volumes, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func disks(_ fake: FakeDiskutil) -> CardDisks {
        let r = FakeRunner()
        fake.install(on: r)
        return CardDisks(runner: r, files: LocalCardFiles(), volumesRoot: volumes.path)
    }

    func vol(_ name: String) throws -> String {
        let v = volumes.appendingPathComponent(name)
        try CardFixture.card(v)
        return v.path
    }

    func testARemovableCardIsOfferedWithItsNameAndSize() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        f.addCard(4, mount: try vol("bootfs"))
        let s = disks(f).scan(marker: marker)
        XCTAssertEqual(s.targets.count, 1)
        let t = s.targets[0]
        XCTAssertEqual(t.whole.id, "disk4")
        XCTAssertEqual(t.partition.id, "disk4s1")
        XCTAssertFalse(t.prepared)
        XCTAssertEqual(t.title(), "bootfs: 32 GB card (SD Card Reader)")
        XCTAssertEqual(t.confirmText(), "Write Domovoi's files to \u{201C}bootfs\u{201D}, the 32 GB card in SD Card Reader (disk4)?")
        XCTAssertEqual(s.others, 0)
    }

    func testAnInternalDiskIsNeverOfferedNorCounted() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        // A FAT partition on a disk inside the Mac that even claims to be removable.
        f.addCard(5, mount: try vol("WIN"), name: "WIN", bus: "PCI-Express", isInternal: true)
        let s = disks(f).scan(marker: marker)
        XCTAssertTrue(s.targets.isEmpty)
        XCTAssertEqual(s.others, 0)
        XCTAssertEqual(CardDisks.neverOffered(f.info["disk5s1"].map(DiskFacts.parse)!, f.info["disk5"].map(DiskFacts.parse)!, boot: []),
                       "it's a disk inside this Mac")
        // An external disk that isn't removable media (a USB SSD) neither.
        let g = FakeDiskutil()
        g.addCard(6, mount: try vol("SSD"), name: "SSD", removable: false)
        XCTAssertTrue(disks(g).scan(marker: marker).targets.isEmpty)
        XCTAssertEqual(disks(g).scan(marker: marker).others, 0)
    }

    func testTheMacsOwnSDSlotIsACardReader() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        f.addCard(4, mount: try vol("bootfs"), bus: "Secure Digital", isInternal: true, media: "SDXC Card")
        XCTAssertEqual(disks(f).scan(marker: marker).targets.map(\.partition.id), ["disk4s1"])
    }

    func testAnAPFSContainerIsRefused() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        // A USB stick formatted APFS: its container disk7 and the volume on it.
        let mount = try vol("Stick")
        f.info["disk6"] = ["DeviceIdentifier": "disk6", "WholeDisk": true, "Internal": false, "RemovableMedia": true, "Ejectable": true, "BusProtocol": "USB",
                           "VirtualOrPhysical": "Physical", "Size": 64_000_000_000]
        f.info["disk7"] = ["DeviceIdentifier": "disk7", "WholeDisk": true, "Internal": false, "RemovableMedia": true, "Ejectable": true, "BusProtocol": "USB",
                           "APFSPhysicalStores": [["APFSPhysicalStore": "disk6s2"]], "Size": 63_000_000_000]
        f.info["disk7s1"] = ["DeviceIdentifier": "disk7s1", "ParentWholeDisk": "disk7", "WholeDisk": false, "Internal": false, "RemovableMedia": true,
                             "Ejectable": true, "BusProtocol": "USB", "FilesystemType": "apfs", "APFSContainerReference": "disk7", "MountPoint": mount,
                             "VolumeName": "Stick", "WritableVolume": true]
        f.list.append(["DeviceIdentifier": "disk7", "APFSVolumes": [["DeviceIdentifier": "disk7s1", "MountPoint": mount]]])
        let d = disks(f)
        let s = d.scan(marker: marker)
        XCTAssertTrue(s.targets.isEmpty, "even with a Pi's files on it")
        XCTAssertEqual(s.others, 1, "a removable disk, left out")
        XCTAssertEqual(d.notACard(DiskFacts.parse(f.info["disk7s1"]!), DiskFacts.parse(f.info["disk7"]!)),
                       "it's an APFS disk (a Mac disk), not a Raspberry Pi card")
        XCTAssertThrowsError(try d.recheck(volume: mount, partition: "disk7s1", marker: marker)) { XCTAssertTrue("\($0)".contains("APFS"), "\($0)") }
    }

    func testTwoCardsAreBothOfferedAndNeitherPicked() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        f.addCard(5, mount: try vol("bootfs 1"), name: "bootfs 1", size: 15_931_539_456)
        f.addCard(4, mount: try vol("bootfs"))
        let s = disks(f).scan(marker: marker)
        XCTAssertEqual(s.targets.map(\.name), ["bootfs", "bootfs 1"])
        XCTAssertEqual(s.targets.map { CardTarget.cardSize($0.whole.size) }, ["32 GB", "16 GB"])
    }

    func testRemovableButNotAFreshPiCardIsCountedAsLeftOut() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        let photos = volumes.appendingPathComponent("NIKON")
        try FileManager.default.createDirectory(at: photos, withIntermediateDirectories: true)
        f.addCard(4, mount: photos.path, name: "NIKON")
        f.addCard(5, mount: try vol("EXFAT"), name: "EXFAT", fs: "exfat")
        f.addCard(6, mount: try vol("LOCKED"), name: "LOCKED", writable: false)
        let s = disks(f).scan(marker: marker)
        XCTAssertTrue(s.targets.isEmpty)
        XCTAssertEqual(s.others, 3)
    }

    func testTheBootDiskIsNeverOffered() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        let boot = disks(f).bootDisks()
        XCTAssertEqual(boot, ["disk3", "disk0"])
        var p = DiskFacts(), w = DiskFacts()
        p.id = "disk0s3"; p.parent = "disk0"; p.removable = true; p.ejectable = true; p.isInternal = false
        w.id = "disk0"; w.removable = true; w.ejectable = true; w.isInternal = false
        XCTAssertEqual(CardDisks.neverOffered(p, w, boot: boot), "it's the disk this Mac starts from")
    }

    func testTheHelpersRecheck() throws {
        let f = FakeDiskutil()
        f.addBootDisk()
        let mount = try vol("bootfs")
        f.addCard(4, mount: mount)
        let d = disks(f)
        XCTAssertEqual(try d.recheck(volume: mount, partition: "disk4s1", marker: marker).whole.id, "disk4")
        XCTAssertThrowsError(try d.recheck(volume: volumes.path + "/other", partition: "disk4s1", marker: marker)) { XCTAssertTrue("\($0)".contains("moved"), "\($0)") }
        XCTAssertThrowsError(try d.recheck(volume: mount, partition: "disk9s1", marker: marker)) { XCTAssertTrue("\($0)".contains("isn't there"), "\($0)") }
        f.info["disk4s1"]?["WritableVolume"] = false
        XCTAssertThrowsError(try d.recheck(volume: mount, partition: "disk4s1", marker: marker)) { XCTAssertTrue("\($0)".contains("locked"), "\($0)") }
        f.info["disk4s1"]?["WritableVolume"] = true
        try FileManager.default.removeItem(atPath: mount + "/" + marker)
        XCTAssertThrowsError(try d.recheck(volume: mount, partition: "disk4s1", marker: marker)) { XCTAssertTrue("\($0)".contains("no longer looks"), "\($0)") }
    }

    func testTheItemNeedsTheSystemTools() {
        XCTAssertTrue(CardDisks.available(isExecutable: { _ in true }))
        XCTAssertFalse(CardDisks.available(isExecutable: { $0 != CardZip.zipinfo }))
        XCTAssertFalse(CardDisks.available(isExecutable: { $0 != CardDisks.diskutil }))
    }
}

final class CardWriteTests: XCTestCase {
    var dir: URL!
    override func setUpWithError() throws { dir = try realTempDir() }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func fixture(portal: Bool = true) throws -> (CardPackage, CardFolderSource, URL) {
        let f = dir.appendingPathComponent("files")
        try CardFixture.write(f, portal: portal)
        let src = try CardFolderSource(path: f.path)
        let card = dir.appendingPathComponent("bootfs")
        try CardFixture.card(card)
        return (try CardZip.load(src), src, card)
    }

    func testTheCardGetsTheServersFilesAndEdits() throws {
        let (pkg, src, card) = try fixture()
        var steps: [Int] = []
        let r = try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: "K7TQ mzr4wd") { _, pct in steps.append(pct) }
        XCTAssertEqual(steps.last, 100)
        XCTAssertEqual(steps, steps.sorted(), "progress only goes up")
        let read = { (n: String) in try Data(contentsOf: card.appendingPathComponent(n)) }
        XCTAssertEqual(try read("domovoi/payload.tar.gz"), Data(CardFixture.payload))
        XCTAssertEqual(try read("userconf.txt"), Data(CardFixture.userconf.utf8))
        XCTAssertEqual([UInt8](try read("domovoi/ap.json")), CardApJson.bytes(ssid: CardFixture.ssid, psk: "k7tqmzr4wd"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: card.appendingPathComponent("domovoi/claim.json").path), "the pass never goes on the card")
        XCTAssertFalse(FileManager.default.fileExists(atPath: card.appendingPathComponent("README.txt").path))
        let cfg = String(decoding: try read("config.txt"), as: UTF8.self)
        XCTAssertTrue(cfg.hasPrefix("# Pi OS\narm_64bit=1\n" + CardText.marker))
        let cmd = String(decoding: try read("cmdline.txt"), as: UTF8.self)
        XCTAssertTrue(cmd.hasPrefix("console=serial0,115200 console=tty1 root=PARTUUID=1234-02"))
        XCTAssertTrue(cmd.hasSuffix(" " + CardFixture.cmdline), cmd)
        XCTAssertTrue(r.configChanged && r.cmdlineChanged)
        XCTAssertEqual(r.written.suffix(2), ["config.txt", "cmdline.txt"], "the boot files last")
        XCTAssertTrue(r.checked.contains("domovoi/payload.tar.gz against payload.sha256"))

        // A second write over its own: the two boot files keep their bytes.
        let before = try read("cmdline.txt")
        let again = try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: "k7tqmzr4wd")
        XCTAssertFalse(again.configChanged || again.cmdlineChanged)
        XCTAssertEqual(try read("cmdline.txt"), before)
    }

    func testAWrongPasswordAnotherFirstBootScriptOrNoRoomIsRefusedBeforeWriting() throws {
        let (pkg, src, card) = try fixture()
        XCTAssertThrowsError(try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: "k7tqmzr4w0")) {
            XCTAssertTrue("\($0)".contains("never use 0, 1, l or o"), "\($0)")
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: card.appendingPathComponent("domovoi").path))

        try Data("console=tty1 root=PARTUUID=1 rootwait systemd.run=/boot/firstrun.sh\n".utf8).write(to: card.appendingPathComponent("cmdline.txt"))
        XCTAssertThrowsError(try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: "k7tqmzr4wd")) {
            XCTAssertTrue("\($0)".contains("another first-boot script"), "\($0)")
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: card.appendingPathComponent("domovoi").path))

        XCTAssertThrowsError(try CardWriter.checkFree(1 << 20, needed: 50 << 20)) { XCTAssertTrue("\($0)".contains("1 MB free"), "\($0)") }
        XCTAssertNoThrow(try CardWriter.checkFree(60 << 20, needed: 50 << 20))
    }

    func testAUSBCardNeedsNoPasswordAndFindersLeftoversGo() throws {
        let (pkg, src, card) = try fixture(portal: false)
        try FileManager.default.createDirectory(at: card.appendingPathComponent("domovoi"), withIntermediateDirectories: true)
        try Data([0]).write(to: card.appendingPathComponent("domovoi/._firstrun.sh"))
        let r = try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: nil)
        XCTAssertEqual(r.removed, ["domovoi/._firstrun.sh"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: card.appendingPathComponent("domovoi/ap.json").path))
    }

    func testANewConsoleLoginReplacesTheZipsAndABadOneIsRefused() throws {
        let (pkg, src, card) = try fixture()
        let line = "domovoi:$6$zzzzzzzz$" + String(repeating: "B", count: 86) + "\n"
        _ = try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: "k7tqmzr4wd", userconf: line)
        XCTAssertEqual(try String(contentsOf: card.appendingPathComponent("userconf.txt"), encoding: .utf8), line)
        XCTAssertThrowsError(try CardWriter.write(pkg, from: src, to: try CardVolume(path: card.path), psk: "k7tqmzr4wd", userconf: "root:x\n"))
    }
}

/// The whole write, as the card window runs it (the signed-in account, no
/// administrator): diskutil asked again, the volume identified, the write,
/// the eject.
final class CardJobTests: XCTestCase {
    var dir: URL!
    var volumes: URL!
    var card: URL!
    var fake: FakeDiskutil!
    var runner: FakeRunner!
    var pkg: CardPackage!
    var src: CardFolderSource!

    override func setUpWithError() throws {
        dir = try realTempDir()
        let files = dir.appendingPathComponent(CardJob.filesName)
        try CardFixture.write(files)
        src = try CardFolderSource(path: files.path)
        pkg = try CardZip.load(src)
        volumes = dir.appendingPathComponent("Volumes")
        card = volumes.appendingPathComponent("bootfs")
        try CardFixture.card(card)
        fake = FakeDiskutil()
        fake.addBootDisk()
        fake.addCard(4, mount: card.path)
        runner = FakeRunner()
        fake.install(on: runner)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func job(_ id: (fsType: String, from: String, on: String)?) -> CardJob {
        CardJob(runner: runner, files: LocalCardFiles(), identity: FakeIdentity(id), volumesRoot: volumes.path)
    }

    var good: (fsType: String, from: String, on: String) { ("msdos", "/dev/disk4s1", card.path) }

    func write(_ j: CardJob, psk: String? = "k7tqmzr4wd", progress: (Int, String) -> Void = { _, _ in }) throws -> CardJob.Result {
        try j.run(pkg, from: src, volume: card.path, partition: "disk4s1", psk: psk, progress: progress)
    }

    func nothingWritten() -> Bool { !FileManager.default.fileExists(atPath: card.appendingPathComponent("domovoi").path) }

    func testWriteCheckAndEject() throws {
        var pcts: [Int] = []
        let d = try write(job(good)) { p, _ in pcts.append(p) }
        XCTAssertTrue(d.ejected)
        XCTAssertNil(d.ejectProblem)
        XCTAssertEqual(fake.ejected, ["disk4"], "the whole card, after the write")
        XCTAssertGreaterThanOrEqual(d.checked, 9)
        XCTAssertTrue(FileManager.default.fileExists(atPath: card.appendingPathComponent("domovoi/ap.json").path))
        XCTAssertEqual(pcts.last, 100)
        // diskutil was asked about the card again, just before the write.
        XCTAssertTrue(runner.ran("diskutil", "info").contains { $0.arguments.last == "disk4s1" })
        XCTAssertTrue(runner.calls.allSatisfy { $0.executable != AdminScript.osascript }, "no administrator prompt")
    }

    func testAnEjectThatFailsStillReportsTheWrittenCard() throws {
        fake.ejectFails = true
        let d = try write(job(good))
        XCTAssertFalse(d.ejected)
        XCTAssertTrue(d.ejectProblem?.contains("failed to unmount") ?? false)
    }

    func testAVolumeThatIsntTheCardIsNeverWritten() throws {
        for id: (fsType: String, from: String, on: String)? in [("apfs", "/dev/disk4s1", card.path), ("msdos", "/dev/disk9s1", card.path), nil] {
            XCTAssertThrowsError(try write(job(id))) { XCTAssertTrue("\($0)".contains("Nothing was written"), "\($0)") }
        }
        XCTAssertTrue(nothingWritten())
        XCTAssertTrue(fake.ejected.isEmpty)
    }

    func testACardThatTurnedIntoAnInternalDiskIsRefused() throws {
        // Picked from the list, then diskutil says otherwise at the last look.
        fake.info["disk4"]?["Internal"] = true
        fake.info["disk4"]?["BusProtocol"] = "Apple Fabric"
        XCTAssertThrowsError(try write(job(good))) { XCTAssertTrue("\($0)".contains("a disk inside this Mac"), "\($0)") }
        XCTAssertTrue(nothingWritten())
    }

    func testACardTakenOutBeforeTheWriteIsRefused() throws {
        fake.info["disk4s1"] = nil
        XCTAssertThrowsError(try write(job(good))) { XCTAssertTrue("\($0)".contains("isn't there any more"), "\($0)") }
        XCTAssertTrue(nothingWritten())
    }

    func testNoPasswordIsRefusedBeforeAnything() throws {
        XCTAssertThrowsError(try write(job(good), psk: nil)) { XCTAssertTrue("\($0)".contains("password is missing"), "\($0)") }
        XCTAssertTrue(runner.calls.isEmpty, "not even diskutil")
        XCTAssertTrue(nothingWritten())
    }
}

final class CardKeyTests: XCTestCase {
    func pkg(claim: CardClaim?) -> CardPackage {
        var p = CardPackage()
        p.setupTransport = "portal"
        p.apSsid = CardFixture.ssid
        p.claim = claim
        return p
    }

    func testThePassIsTradedOnceAndItsProblemsAreSaid() {
        let k = FakeKeys()
        let c = CardClaim(jobId: 17, secret: String(repeating: "s", count: 43), expiresAt: Date(timeIntervalSinceNow: 3600))
        XCTAssertEqual(CardKeyFlow.fetch(k, pkg(claim: c), now: Date()).psk, "k7tqmzr4wd")
        XCTAssertEqual(CardKeyFlow.fetch(k, pkg(claim: nil), now: Date()).problem, .noClaim)
        var old = c
        old.expiresAt = Date(timeIntervalSinceNow: -60)
        XCTAssertEqual(CardKeyFlow.fetch(k, pkg(claim: old), now: Date()).problem, .expired)
        XCTAssertEqual(k.claims, 1, "an expired pass isn't even sent")
        k.claimAnswer = .got("k7tqmzr4w0")
        XCTAssertEqual(CardKeyFlow.fetch(k, pkg(claim: c), now: Date()).problem, .refused, "a password the card can't use is none")
        k.claimAnswer = .fail(.used)
        let used = CardKeyFlow.fetch(k, pkg(claim: c), now: Date())
        XCTAssertEqual(CardKeyWords.reason(used), CardKeyWords.used)
        XCTAssertTrue(CardKeyWords.offersSignIn(used))
        XCTAssertFalse(CardKeyWords.offersRetry(used))
        let down = KeyAnswer.fail(.unreachable)
        XCTAssertTrue(CardKeyWords.nextStep(down).contains("menu-bar icon"))
        XCTAssertTrue(CardKeyWords.offersRetry(down))
        XCTAssertFalse(CardKeyWords.offersSignIn(.fail(.noClaim)))
    }

    func testTheDashboardsAnswers() {
        var calls: [(String, String, String?)] = []
        var answers: [String: (status: Int, body: [String: Any])] = [:]
        let h = HTTPCardKeys { method, path, _, bearer in
            calls.append((method, path, bearer))
            return answers[method + " " + path]
        }
        let c = CardClaim(jobId: 17, secret: "x", expiresAt: nil)
        XCTAssertEqual(h.claim(c).problem, .unreachable)
        answers["POST /api/satellites/media/claim"] = (409, ["reason": "used"])
        XCTAssertEqual(h.claim(c).problem, .used)
        answers["POST /api/satellites/media/claim"] = (410, ["reason": "forgotten"])
        XCTAssertEqual(h.claim(c).problem, .forgotten)
        answers["POST /api/satellites/media/claim"] = (200, ["ap": ["ssid": CardFixture.ssid, "psk": "k7tqmzr4wd"]])
        XCTAssertEqual(h.claim(c).psk, "k7tqmzr4wd")

        answers["POST /api/auth/login"] = (401, ["detail": "wrong password"])
        let wrong = h.adminFetch(jobId: 17, ssid: CardFixture.ssid, adminPassword: "nope")
        XCTAssertEqual(wrong.problem, .wrongPassword)
        XCTAssertEqual(CardKeyWords.reason(wrong), "wrong password")

        answers["POST /api/auth/login"] = (200, ["token": "t0k"])
        answers["GET /api/satellites/media/jobs/17/credentials"] = (200, ["ap": ["ssid": CardFixture.ssid, "psk": "k7tqmzr4wd"]])
        calls = []
        XCTAssertEqual(h.adminFetch(jobId: 17, ssid: CardFixture.ssid, adminPassword: "pw").psk, "k7tqmzr4wd")
        XCTAssertEqual(calls.last?.1, "/api/auth/logout", "the session ends at once")
        XCTAssertEqual(calls.last?.2, "t0k")

        // Forgotten: a new password for the same network, with a new console login.
        let line = "domovoi:$6$zzzzzzzz$" + String(repeating: "B", count: 86) + "\n"
        answers["GET /api/satellites/media/jobs/17/credentials"] = (404, [:])
        answers["POST /api/satellites/media/jobs/17/setup-key"] = (200, ["ap": ["psk": "m3nq8rtw2x"], "reissued": true, "userconf": line,
                                                                         "console": ["username": "domovoi", "password": "pass-word"]])
        let made = CardKeyFlow.fetchAsAdmin(h, pkg(claim: c), adminPassword: "pw")
        XCTAssertEqual(made.psk, "m3nq8rtw2x")
        XCTAssertTrue(made.reissued)
        XCTAssertEqual(made.userconf, line)
        XCTAssertEqual(made.consoleUser, "domovoi")
    }
}

final class CardMenuTests: XCTestCase {
    func titles(_ m: [MenuEntry]) -> [String] { m.map { $0.kind == .separator ? "-" : $0.title } }

    func testTheItemIsThereOnlyWhenItCanWork() {
        let running = MenuVerdict(state: .running, line: "Domovoi is running")
        let without = MenuItems.build(verdict: running, attention: [], keepAwake: nil, update: nil)
        XCTAssertFalse(without.contains { $0.action == .prepareCard }, "no item without the system tools")
        let with = MenuItems.build(verdict: running, attention: [], keepAwake: nil, update: nil, cardHelper: true)
        XCTAssertEqual(Array(titles(with).prefix(6)), ["Domovoi is running", "-", "Open Domovoi", "User manual", "-", "Prepare an SD card..."])
        let busy = MenuItems.build(verdict: running, attention: [], keepAwake: nil, update: nil, busy: [.prepareCard], cardHelper: true)
        XCTAssertTrue(titles(busy).contains("Writing an SD card..."))
        let notSetUp = MenuItems.build(verdict: MenuVerdict(state: .notInstalled, line: "x"), attention: [], keepAwake: nil, update: nil, cardHelper: true)
        XCTAssertFalse(notSetUp.contains { $0.action == .prepareCard }, "nothing to write a card for before Domovoi is set up")
    }
}
