import CoreImage
import XCTest
@testable import DomovoiMacKit

/// 1.5.0: the setup code's QR on Setup's pages (SetupQR), and the house
/// identity backup Setup can restore (IdentityBackup, S3-4).
final class SetupQRAndIdentityTests: XCTestCase {
    var dir: URL!

    override func setUpWithError() throws { dir = try tempDir() }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    // ─── the QR ──────────────────────────────────────────────────────────

    /// domovoi/setup_qr.py's claim_url: the code only in the fragment, the
    /// spaces percent-encoded, hyphens left alone.
    func testTheClaimAddressMatchesTheCoresOwn() {
        XCTAssertEqual(SetupQR.claimURL(lan: "192.168.1.20", code: "apple river stone cloud lamp seven harbor-x mint\n"),
                       "http://192.168.1.20:6369/#setup-code=apple%20river%20stone%20cloud%20lamp%20seven%20harbor-x%20mint")
        XCTAssertEqual(SetupQR.claimURL(lan: "192.168.1.20", port: 6390, code: "a&b#c"), "http://192.168.1.20:6390/#setup-code=a%26b%23c")
    }

    func testNoAddressAPhoneCantReachNoQR() {
        for lan in [nil, "127.0.0.1", "0.0.0.0", "", "192.168.1", "localhost"] as [String?] {
            XCTAssertNil(SetupQR.claimURL(lan: lan, code: "apple river"), "\(lan ?? "nil")")
        }
        XCTAssertNil(SetupQR.claimURL(lan: "192.168.1.20", code: "  \n"))
    }

    /// CoreImage's generator draws it, square, with the quiet zone, and a
    /// QR reader (CoreImage's own detector) reads the address back.
    func testTheQRReadsBackAsTheAddress() throws {
        let url = try XCTUnwrap(SetupQR.claimURL(lan: "192.168.1.20", code: "apple river stone cloud lamp seven harbor mint"))
        let img = try XCTUnwrap(SetupQR.image(for: url, scale: 6))
        XCTAssertEqual(img.width, img.height)
        let detector = try XCTUnwrap(CIDetector(ofType: CIDetectorTypeQRCode, context: nil, options: [CIDetectorAccuracy: CIDetectorAccuracyHigh]))
        let found = detector.features(in: CIImage(cgImage: img)).compactMap { ($0 as? CIQRCodeFeature)?.messageString }
        XCTAssertEqual(found, [url])
    }

    // ─── the identity backup ─────────────────────────────────────────────

    /// What server_identity.backup_document writes (the key is made up).
    let backup = #"""
    {
      "kind": "domovoi-server-identity-backup",
      "schema": 1,
      "created_at": "2026-11-24T10:00:00Z",
      "algorithm": "ed25519",
      "private_key": "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0MTI=",
      "public_key": "cHVibGljcHVibGljcHVibGljcHVibGljcHVibGljMTI=",
      "fingerprint": "ab12:cd34:ef56",
      "note": "This file is the Domovoi server's private key."
    }
    """#

    func testABackupsPublicFacts() throws {
        let s = try IdentityBackup.check(Data(backup.utf8)).get()
        XCTAssertEqual(s, IdentityBackup.Summary(fingerprint: "ab12:cd34:ef56", createdAt: "2026-11-24T10:00:00Z"))
    }

    func testWrongFilesAreRefusedWithAReason() {
        let cases: [(String, String)] = [
            ("[1, 2]", "isn't a JSON object"),
            ("not json", "isn't a JSON object"),
            (#"{"kind":"satellite-server-fingerprint","fingerprint":"ab"}"#, "room device's record"),
            (#"{"kind":"something-else","private_key":"x","fingerprint":"ab"}"#, "not a Domovoi house identity backup"),
            (#"{"kind":"domovoi-server-identity-backup","fingerprint":"ab"}"#, "no private key"),
            (#"{"kind":"domovoi-server-identity-backup","private_key":"x"}"#, "no fingerprint"),
            (String(repeating: " ", count: IdentityBackup.maxBytes + 1), "far too large"),
        ]
        for (text, why) in cases {
            guard case .failure(let e) = IdentityBackup.check(Data(text.utf8)) else { XCTFail("accepted: \(text.prefix(60))"); continue }
            XCTAssertTrue(e.message.contains(why), "\(e.message) / \(why)")
            XCTAssertFalse(e.message.contains("c2VjcmV0"))
        }
    }

    /// Root reads only the Setup account's own regular file: no link, no
    /// other owner, nothing too large.
    func testTheHelperReadsOnlyTheAccountsOwnFile() throws {
        let f = dir.appendingPathComponent("identity.json")
        FileManager.default.createFile(atPath: f.path, contents: Data(backup.utf8), attributes: [.posixPermissions: 0o600])
        XCTAssertEqual(try IdentityBackup.read(path: f.path, owner: getuid()), Data(backup.utf8))

        XCTAssertThrowsError(try IdentityBackup.read(path: f.path, owner: getuid() + 1)) {
            XCTAssertTrue("\($0)".contains("doesn't belong"), "\($0)")
        }
        let link = dir.appendingPathComponent("link.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: f)
        XCTAssertThrowsError(try IdentityBackup.read(path: link.path, owner: getuid())) { XCTAssertTrue("\($0)".contains("link"), "\($0)") }
        XCTAssertThrowsError(try IdentityBackup.read(path: dir.path, owner: getuid()))
        let big = dir.appendingPathComponent("big.json")
        FileManager.default.createFile(atPath: big.path, contents: Data(repeating: 0x20, count: IdentityBackup.maxBytes + 1))
        XCTAssertThrowsError(try IdentityBackup.read(path: big.path, owner: getuid())) { XCTAssertTrue("\($0)".contains("too large"), "\($0)") }
        let wrong = dir.appendingPathComponent("wrong.json")
        FileManager.default.createFile(atPath: wrong.path, contents: Data("{}".utf8))
        XCTAssertThrowsError(try IdentityBackup.read(path: wrong.path, owner: getuid()))
    }

    /// The app's answers file carries the backup's path; the helper's
    /// parser reads the bytes once. A repair never restores.
    func testTheAnswersCarryTheBackupToTheHelper() throws {
        let share = dir.appendingPathComponent("share")
        try FileManager.default.createDirectory(at: share, withIntermediateDirectories: true)
        try #"{"schema":1,"version":"0.2.0-dev","commit":"\#(String(repeating: "b", count: 40))","bundle":"origin.bundle","ref":"main","track_branch":"main"}"#
            .write(to: share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        let f = dir.appendingPathComponent("identity.json")
        FileManager.default.createFile(atPath: f.path, contents: Data(backup.utf8), attributes: [.posixPermissions: 0o600])
        let answers = dir.appendingPathComponent("answers.json")
        try JSONEncoder().encode(SetupAnswers(internet: "never", mediaRoot: "/Users/Shared/Domovoi", ollama: "own", restoreIdentity: f.path)).write(to: answers)
        XCTAssertTrue(try String(contentsOf: answers, encoding: .utf8).contains("\"restore_identity\""))
        let layout = Layout(mode: .system, root: dir.appendingPathComponent("root"))
        var a = try Args(SetupCommands.install(forUID: getuid(), codeTo: dir.appendingPathComponent("code").path, answersFile: answers.path))
        _ = try Machine.layout(&a, defaultSystem: false)
        let o = try SetupCommands.systemOptions(&a, layout: layout, share: share)
        XCTAssertEqual(o.restoreIdentity, Data(backup.utf8))
        try a.checkAllUsed()

        // Older answers files (no key) still read, and restore nothing.
        try #"{"internet":"never","media_root":"/Users/Shared/Domovoi","ollama":"own"}"#.write(to: answers, atomically: true, encoding: .utf8)
        var b = try Args(SetupCommands.install(forUID: getuid(), codeTo: dir.appendingPathComponent("code").path, answersFile: answers.path))
        _ = try Machine.layout(&b, defaultSystem: false)
        XCTAssertNil(try SetupCommands.systemOptions(&b, layout: layout, share: share).restoreIdentity)
    }

    func testProvisionArgsNameTheGuestFileOnly() {
        let src = ProvisionSource(script: URL(fileURLWithPath: "/x/provision.sh"), repoURL: "https://example.org/d.git", ref: "main", trackBranch: nil)
        let ans = InstallRecord.Answers(internet: "never", tier: .floor, ollama: "own", mediaRoot: "/m", media: [], ports: Ports.real,
                                        size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: nil)
        let with = ProvisionArgs.build(source: src, answers: ans, ollamaURL: "http://o", restoreIdentity: IdentityBackup.guestFile)
        XCTAssertNotNil(with.firstRange(of: ["--restore-identity", "/var/lib/domovoi-mac-install/house-identity.json"]))
        XCTAssertFalse(ProvisionArgs.build(source: src, answers: ans, ollamaURL: "http://o").contains("--restore-identity"))
    }

    func testRemoveEverythingSaysTheRoomsNeedSettingUpAgain() {
        XCTAssertTrue(IdentityBackup.removeEverythingWarning.contains("rooms will need setting up again"))
        XCTAssertTrue(IdentityBackup.removeEverythingWarning.contains("Settings > About & updates"))
        XCTAssertTrue(IdentityBackup.removeEverythingWarning.contains("Restore from a backup"))
    }
}
