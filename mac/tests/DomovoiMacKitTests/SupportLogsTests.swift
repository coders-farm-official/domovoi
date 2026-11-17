import Darwin
import XCTest
@testable import DomovoiMacKit

/// "Save logs for support...": the copy, its secrets blanked, written only
/// into the asking account's own private folder, and never through a link.
final class SupportLogsTests: XCTestCase {
    var dir: URL!
    var layout: Layout!
    var out: URL!
    var runner: FakeRunner!

    let code = "amber-birch-cedar-delta-ember-fjord-grove-heron"

    /// The folder with every link resolved, /private included: the copy
    /// refuses a link anywhere on the way, and /var is one. (URL's
    /// resolvingSymlinksInPath, which tempDir uses, strips /private again.)
    static func real(_ u: URL) throws -> URL {
        guard let r = realpath(u.path, nil) else { throw HelperError("no realpath for \(u.path)") }
        defer { free(r) }
        return URL(fileURLWithPath: String(cString: r), isDirectory: true)
    }

    override func setUpWithError() throws {
        dir = try Self.real(tempDir())
        layout = try makeInstalledLayout(dir)
        let fm = FileManager.default
        // A limactl, so the Linux side's commands are asked for.
        try fm.createDirectory(at: layout.limactl.deletingLastPathComponent(), withIntermediateDirectories: true)
        fm.createFile(atPath: layout.limactl.path, contents: Data("x".utf8), attributes: [.posixPermissions: 0o755])
        try fm.createDirectory(at: layout.logs, withIntermediateDirectories: true)
        try "start\nsetup code \(code) for the admin\nPOSTGRES_PASSWORD=Xy7q\nok\n".write(to: layout.logs.appendingPathComponent("helper.log"), atomically: true, encoding: .utf8)
        try "pulling\n".write(to: layout.logs.appendingPathComponent("provision.log"), atomically: true, encoding: .utf8)
        try "old\n".write(to: layout.logs.appendingPathComponent("vm.log.1"), atomically: true, encoding: .utf8)
        try "not a log\n".write(to: layout.logs.appendingPathComponent("notes.txt"), atomically: true, encoding: .utf8)
        let inst = layout.limaHome.appendingPathComponent("domovoi")
        try fm.createDirectory(at: inst, withIntermediateDirectories: true)
        try "Authorization: Bearer abcdefghijklmnop\n".write(to: inst.appendingPathComponent("ha.stdout.log"), atomically: true, encoding: .utf8)
        out = dir.appendingPathComponent("out")
        try fm.createDirectory(at: out, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        runner = FakeRunner()
        runner.on { c in
            if c.executable == "/usr/bin/sw_vers" { return CommandResult(status: 0, stdout: "ProductVersion: 26.4.1\n") }
            if c.arguments == ["print-disabled", "system"] {
                return CommandResult(status: 0, stdout: "disabled services = {\n\t\"com.example.private-app\" => disabled\n\t\"com.domovoi.mac.vm\" => enabled\n}\n")
            }
            return nil
        }
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func bundle() -> SupportBundle { SupportBundle(layout: layout, runner: runner, now: { Date(timeIntervalSince1970: 1_800_000_000) }) }

    func read(_ name: String) throws -> String { try String(contentsOf: out.appendingPathComponent(name), encoding: .utf8) }

    func testTheCopyIsRedactedAndPrivate() throws {
        let r = try bundle().write(into: OwnedFolder(out.path, owner: getuid()))
        XCTAssertTrue(r.written.contains("helper.log"))
        XCTAssertTrue(r.written.contains("vm.log.1"), "rotations too")
        XCTAssertFalse(r.written.contains("notes.txt"), "only *.log from the logs folder")
        XCTAssertTrue(r.written.contains("lima-ha.stdout.log"))
        XCTAssertTrue(r.written.contains("install.json"))
        XCTAssertTrue(r.written.contains("README.txt"))
        let helper = try read("helper.log")
        XCTAssertFalse(helper.contains(code), "no setup code")
        XCTAssertFalse(helper.contains("Xy7q"), "no password")
        XCTAssertTrue(helper.contains("setup code [hidden] for the admin"))
        XCTAssertEqual(try read("lima-ha.stdout.log"), "Authorization: Bearer [hidden]\n")
        XCTAssertEqual(try read("mac.txt"), "$ sw_vers\nProductVersion: 26.4.1\n")
        let jobs = try read("jobs-disabled.txt")
        XCTAssertTrue(jobs.contains("com.domovoi.mac.vm"))
        XCTAssertFalse(jobs.contains("com.example.private-app"), "the household's other programs stay out")
        XCTAssertTrue(try read("README.txt").contains("look through the files before you share them"))
        for name in r.written {
            let a = try FileManager.default.attributesOfItem(atPath: out.appendingPathComponent(name).path)
            XCTAssertEqual((a[.posixPermissions] as? NSNumber)?.intValue, 0o600, name)
        }
        // The Linux side, as the role account, with the units' journal.
        let journal = try XCTUnwrap(runner.calls.first { $0.arguments.contains("journalctl") })
        XCTAssertEqual(Array(journal.arguments.prefix(3)), ["-n", "-u", "_domovoi"])
        XCTAssertTrue(journal.arguments.contains("domovoi-core.service"))
    }

    func testNeverASecretFile() throws {
        // The fixed list: nothing under the Linux side's .env, the setup
        // code, ~/.domovoi or an SSH folder is ever asked for.
        let rec = try RecordStore.load(layout.installRecord)
        let paths = bundle().sources().map { "\($0.dir)/\($0.name)" } + bundle().commands(rec).flatMap { $0.1.arguments }
        XCTAssertTrue(paths.contains { $0.contains("journalctl") })
        for bad in [".env", "setup-code", "/.domovoi", ".ssh", "id_ed25519"] {
            XCTAssertFalse(paths.contains { $0.contains(bad) }, bad)
        }
    }

    func testLinksAndHardLinksAreSkipped() throws {
        let fm = FileManager.default
        let secret = dir.appendingPathComponent("secret.txt")
        try "password=hunter2\nroot only\n".write(to: secret, atomically: true, encoding: .utf8)
        try fm.removeItem(at: layout.logs.appendingPathComponent("provision.log"))
        try fm.createSymbolicLink(at: layout.logs.appendingPathComponent("provision.log"), withDestinationURL: secret)
        XCTAssertEqual(link(secret.path, layout.logs.appendingPathComponent("ollama.log").path), 0)
        // The instance folder itself a link (a role account's trick).
        let inst = layout.limaHome.appendingPathComponent("domovoi")
        try fm.removeItem(at: inst)
        try fm.createSymbolicLink(at: inst, withDestinationURL: dir)
        let r = try bundle().write(into: OwnedFolder(out.path, owner: getuid()))
        XCTAssertFalse(r.written.contains("provision.log"))
        XCTAssertFalse(r.written.contains("ollama.log"))
        XCTAssertFalse(r.written.contains { $0.hasPrefix("lima-") })
        XCTAssertTrue(r.skipped.contains { $0.contains("hard links") }, "\(r.skipped)")
        XCTAssertTrue(try read("README.txt").contains("Skipped:"))
    }

    func testOnlyIntoTheAccountsOwnPrivateFolder() throws {
        let fm = FileManager.default
        // Open to others.
        let open = dir.appendingPathComponent("open")
        try fm.createDirectory(at: open, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o755])
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: open.path)
        XCTAssertThrowsError(try OwnedFolder(open.path, owner: getuid())) { XCTAssertTrue("\($0)".contains("open to other accounts")) }
        // Someone else's.
        XCTAssertThrowsError(try OwnedFolder(out.path, owner: getuid() + 1))
        // A link on the way, or as the folder itself.
        let link = dir.appendingPathComponent("link")
        try fm.createSymbolicLink(at: link, withDestinationURL: out)
        XCTAssertThrowsError(try OwnedFolder(link.path, owner: getuid()))
        XCTAssertThrowsError(try OwnedFolder(link.appendingPathComponent("x").path, owner: getuid()))
        XCTAssertThrowsError(try OwnedFolder("relative/path", owner: getuid()))
        // Never over an existing name.
        let f = try OwnedFolder(out.path, owner: getuid())
        try f.create("a.txt", Data("1".utf8))
        XCTAssertThrowsError(try f.create("a.txt", Data("2".utf8)))
        XCTAssertEqual(try read("a.txt"), "1")
        for bad in ["../x", ".hidden", "a/b", ""] { XCTAssertThrowsError(try f.create(bad, Data())) }
    }

    func testOnlyTheTailOfABigLog() throws {
        let b = bundle()
        b.maxBytes = 10
        try "0123456789ABCDEFGHIJ".write(to: layout.logs.appendingPathComponent("helper.log"), atomically: true, encoding: .utf8)
        let (data, why) = b.readTail(.init(dir: layout.logs.path, name: "helper.log", copyAs: "helper.log"))
        XCTAssertNil(why)
        XCTAssertEqual(data.map { String(decoding: $0, as: UTF8.self) }, "ABCDEFGHIJ")
    }
}
