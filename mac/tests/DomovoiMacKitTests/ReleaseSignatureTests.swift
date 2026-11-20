import XCTest
@testable import DomovoiMacKit

/// The Mac update's trust in a download (ReleaseSignature.swift), against
/// real `ssh-keygen -Y sign` output: mac/tests/release-sig/ (made with a
/// throwaway key whose private half was deleted at once: package.bin, two
/// manifests, their signatures, one under the wrong namespace, one by
/// another key) and the Windows fixtures in windows/tests/release-sig/, so
/// this verifier and ReleaseSignature.cs agree on the same bytes.
final class ReleaseSignatureTests: XCTestCase {
    let fx = macDir.appendingPathComponent("tests/release-sig")
    let win = repoDir.appendingPathComponent("windows/tests/release-sig")
    let commit = "c0ffee0123456789abcdef0123456789abcdef01"

    func data(_ u: URL) throws -> Data { try Data(contentsOf: u) }
    func key(_ u: URL) throws -> Data { try XCTUnwrap(SSHKey.parseEd25519(String(contentsOf: u, encoding: .utf8))) }

    // ─── versions ──────────────────────────────────────────────────────

    func testVersionsOrderAsTheCoreAndTheTrayOrderThem() {
        // windows_side.compare_versions / ReleaseVersion.Compare's cases.
        let ascending = ["1.0.0-rc1", "1.0.0-rc2", "1.0.0-rc10", "1.0.0", "1.0.1", "1.2.0", "1.10.0", "2.0.0"]
        for i in 0..<ascending.count {
            for j in 0..<ascending.count {
                let c = ReleaseVersion.compare(ascending[i], ascending[j])
                XCTAssertEqual(c, i < j ? -1 : i == j ? 0 : 1, "\(ascending[i]) vs \(ascending[j])")
            }
        }
        XCTAssertEqual(ReleaseVersion.compare("v1.2.0", "1.2.0"), 0)
        XCTAssertEqual(ReleaseVersion.compare("1.2.0+build.5", "1.2.0"), 0)
        for bad in ["", "1.2", "1.2.0.4", "1.2.0-", "x1.2.0", "1.2.0\n", "1.2.0 "] {
            XCTAssertFalse(ReleaseVersion.isValid(bad), bad)
            XCTAssertNil(ReleaseVersion.compare(bad, "1.0.0"), bad)
        }
    }

    func testTagsAndRepositoriesAndURLs() throws {
        XCTAssertTrue(MacReleaseNames.isTag("v1.2.0"))
        XCTAssertTrue(MacReleaseNames.isTag("v1.2.0-rc3"))
        for bad in ["1.2.0", "v1.2", "v1.2.0\n", "v1.2.0/../x", "v1.2.0 "] { XCTAssertFalse(MacReleaseNames.isTag(bad), bad) }
        XCTAssertTrue(MacReleaseNames.isRepo("coders-farm-official/domovoi"))
        XCTAssertTrue(MacReleaseNames.isRepo("alex/domovoi-train"))
        for bad in ["alex", "alex/", "/domovoi", "alex/a/b", "alex/..", "-alex/domovoi", "alex/do movoi"] {
            XCTAssertFalse(MacReleaseNames.isRepo(bad), bad)
        }
        XCTAssertEqual(try MacReleaseNames.url(repo: MacReleaseNames.defaultRepo, tag: "v1.2.0", asset: MacReleaseNames.package),
                       "https://github.com/coders-farm-official/domovoi/releases/download/v1.2.0/DomovoiSetup-mac.pkg")
        XCTAssertThrowsError(try MacReleaseNames.url(repo: MacReleaseNames.defaultRepo, tag: "v1.2.0", asset: "DomovoiSetup.exe"))
        // The names finish-release attaches (scripts/release/finish_release.py).
        let fr = try String(contentsOf: repoDir.appendingPathComponent("scripts/release/finish_release.py"), encoding: .utf8)
        for (k, v) in [("MAC_SETUP_ASSET", MacReleaseNames.package), ("MAC_SETUP_SIG_ASSET", MacReleaseNames.packageSig),
                       ("MAC_MANIFEST_ASSET", MacReleaseNames.manifest), ("MAC_MANIFEST_SIG_ASSET", MacReleaseNames.manifestSig),
                       ("MAC_SETUP_NAMESPACE", MacReleaseNames.packageNamespace), ("MAC_MANIFEST_NAMESPACE", MacReleaseNames.manifestNamespace),
                       ("MAC_MANIFEST_KIND", MacReleaseNames.manifestKind)] {
            XCTAssertTrue(fr.contains("\(k) = \"\(v)\""), "\(k) is \(v) in finish_release.py")
        }
    }

    // ─── keys and signatures ───────────────────────────────────────────

    func testKeyLines() throws {
        XCTAssertEqual(try key(fx.appendingPathComponent("test-key.pub")).count, 32)
        XCTAssertNotNil(SSHKey.parseEd25519("# a comment\n\n" + (try String(contentsOf: fx.appendingPathComponent("test-key.pub"), encoding: .utf8))))
        XCTAssertNotNil(SSHKey.parseEd25519(try String(contentsOf: repoDir.appendingPathComponent("windows/installer/release-signing.pub"), encoding: .utf8)),
                        "the project's own release key reads")
        for bad in ["", "ssh-rsa AAAAB3NzaC1yc2E= x", "ssh-ed25519 notbase64!", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5"] {
            XCTAssertNil(SSHKey.parseEd25519(bad), bad)
        }
    }

    func testAGoodSignatureChecksOut() throws {
        let k = try key(fx.appendingPathComponent("test-key.pub"))
        XCTAssertNil(SSHSig.problem(try data(fx.appendingPathComponent("package.bin.sig")), message: try data(fx.appendingPathComponent("package.bin")),
                                    key: k, namespace: MacReleaseNames.packageNamespace))
        XCTAssertNil(SSHSig.problem(try data(fx.appendingPathComponent("package.bin.sig")), file: fx.appendingPathComponent("package.bin"),
                                    key: k, namespace: MacReleaseNames.packageNamespace), "hashed from the file, in pieces")
        XCTAssertNil(SSHSig.problem(try data(fx.appendingPathComponent("manifest-1.2.0.json.sig")), message: try data(fx.appendingPathComponent("manifest-1.2.0.json")),
                                    key: k, namespace: MacReleaseNames.manifestNamespace))
    }

    func testTheWindowsFixturesCheckOutHereToo() throws {
        let k = try key(win.appendingPathComponent("test-key.pub"))
        XCTAssertNil(SSHSig.problem(try data(win.appendingPathComponent("setup.bin.sig")), message: try data(win.appendingPathComponent("setup.bin")),
                                    key: k, namespace: "domovoi-windows-setup"))
        XCTAssertNotNil(SSHSig.problem(try data(win.appendingPathComponent("setup.bin.other-key.sig")), message: try data(win.appendingPathComponent("setup.bin")),
                                       key: k, namespace: "domovoi-windows-setup"))
        XCTAssertNotNil(SSHSig.problem(try data(win.appendingPathComponent("setup.bin.sig")), message: try data(win.appendingPathComponent("setup.bin")),
                                       key: k, namespace: MacReleaseNames.packageNamespace), "a Windows Setup's signature is no Mac package's")
    }

    func testEveryWayASignatureFails() throws {
        let k = try key(fx.appendingPathComponent("test-key.pub"))
        let pkg = try data(fx.appendingPathComponent("package.bin"))
        let sig = try data(fx.appendingPathComponent("package.bin.sig"))
        var changed = pkg
        changed[10] ^= 1
        XCTAssertEqual(SSHSig.problem(sig, message: changed, key: k, namespace: MacReleaseNames.packageNamespace),
                       "its signature doesn't match the file (it was changed, or signed by someone else)")
        XCTAssertEqual(SSHSig.problem(try data(fx.appendingPathComponent("package.bin.other-key.sig")), message: pkg, key: k, namespace: MacReleaseNames.packageNamespace),
                       "it is signed with a key other than Domovoi's release key")
        XCTAssertEqual(SSHSig.problem(sig, message: pkg, key: try key(fx.appendingPathComponent("other-key.pub")), namespace: MacReleaseNames.packageNamespace),
                       "it is signed with a key other than Domovoi's release key")
        let wrongNs = SSHSig.problem(try data(fx.appendingPathComponent("manifest-1.2.0.json.wrong-namespace.sig")),
                                     message: try data(fx.appendingPathComponent("manifest-1.2.0.json")), key: k, namespace: MacReleaseNames.manifestNamespace)
        XCTAssertTrue(wrongNs?.hasPrefix("its signature is for something else") == true, wrongNs ?? "nil")
        XCTAssertEqual(SSHSig.problem(Data("not a signature".utf8), message: pkg, key: k, namespace: MacReleaseNames.packageNamespace),
                       "its signature file isn't an SSH signature")
        XCTAssertEqual(SSHSig.problem(sig, message: pkg, key: nil, namespace: MacReleaseNames.packageNamespace), "there is no release key to check it with")
        // A truncated armor body.
        let text = String(decoding: sig, as: UTF8.self)
        let cut = text.replacingOccurrences(of: "\n-----END", with: "AAAA\n-----END")
        XCTAssertNotNil(SSHSig.problem(Data(cut.utf8), message: pkg, key: k, namespace: MacReleaseNames.packageNamespace))
        // CRLF line ends (a signature that went through a Windows editor) still read.
        XCTAssertNil(SSHSig.problem(Data(text.replacingOccurrences(of: "\n", with: "\r\n").utf8), message: pkg, key: k, namespace: MacReleaseNames.packageNamespace))
    }

    // ─── the whole check ───────────────────────────────────────────────

    /// A run folder with the four files as a release has them.
    func files(manifest: String = "manifest-1.2.0.json", manifestSig: String? = nil, packageSig: String = "package.bin.sig",
               change: ((inout Data) -> Void)? = nil) throws -> MacSetupFiles {
        let d = try tempDir()
        addTeardownBlock { try? FileManager.default.removeItem(at: d) }
        var pkg = try data(fx.appendingPathComponent("package.bin"))
        change?(&pkg)
        let p = d.appendingPathComponent(MacReleaseNames.package)
        try pkg.write(to: p)
        return MacSetupFiles(manifest: try data(fx.appendingPathComponent(manifest)),
                             manifestSig: try data(fx.appendingPathComponent(manifestSig ?? manifest + ".sig")),
                             packageSig: try data(fx.appendingPathComponent(packageSig)), package: p)
    }

    func want(tag: String = "v1.2.0", commit: String? = "c0ffee0123456789abcdef0123456789abcdef01", installed: String? = "1.1.0") throws -> MacSetupExpectation {
        MacSetupExpectation(tag: tag, commit: commit, installed: installed, key: try key(fx.appendingPathComponent("test-key.pub")))
    }

    func testTheReleaseAskedForChecksOut() throws {
        let v = MacSetupCheck.run(try want(), try files())
        XCTAssertNil(v.problem)
        XCTAssertEqual(v.manifest?.version, "1.2.0")
        XCTAssertEqual(v.manifest?.size, 3000)
        XCTAssertTrue(MacSetupCheck.run(try want(commit: nil), try files()).ok, "no commit to compare: not checked")
    }

    func testEveryRefusalHasItsCodeAndWords() throws {
        func check(_ w: MacSetupExpectation, _ f: MacSetupFiles, _ code: String, _ words: String, line: UInt = #line) {
            let v = MacSetupCheck.run(w, f)
            XCTAssertEqual(v.code, code, v.problem ?? "nil", line: line)
            XCTAssertTrue(v.problem?.contains(words) == true, "\(v.problem ?? "nil") lacks \(words)", line: line)
        }
        var noKey = try want()
        noKey.key = nil
        check(noKey, try files(), MacSetupCheck.noKey, "pins no release key")
        var f = try files()
        f.manifest = nil
        check(try want(), f, MacSetupCheck.noSignedSetup, "no signed manifest")
        f = try files()
        f.packageSig = nil
        check(try want(), f, MacSetupCheck.noSignedSetup, "no package signature")
        check(try want(), try files(manifestSig: "manifest-1.2.0.json.wrong-namespace.sig"), MacSetupCheck.badSignature, "its manifest can't be trusted")
        check(try want(), try files(manifestSig: "manifest-1.1.0.json.sig"), MacSetupCheck.badSignature, "its manifest can't be trusted")
        check(try want(tag: "v1.3.0"), try files(), MacSetupCheck.badSignature, "its manifest is for v1.2.0, not v1.3.0")
        check(try want(commit: String(repeating: "d", count: 40)), try files(), MacSetupCheck.badSignature, "it was built from c0ffee012345")
        check(try want(installed: "1.2.0"), try files(), MacSetupCheck.notNewer, "isn't newer than the installed 1.2.0")
        check(try want(installed: "1.3.0"), try files(), MacSetupCheck.notNewer, "isn't newer")
        check(try want(tag: "v1.1.0"), try files(manifest: "manifest-1.1.0.json"), MacSetupCheck.notNewer, "it is version 1.1.0")
        check(try want(installed: nil), try files(), MacSetupCheck.notNewer, "isn't known")
        check(try want(installed: "0.2.0 dev"), try files(), MacSetupCheck.notNewer, "isn't known")
        check(try want(), try files(packageSig: "package.bin.other-key.sig"), MacSetupCheck.badSignature, "the package can't be trusted")
        check(try want(), try files(change: { $0[100] ^= 0xff }), MacSetupCheck.badSignature, "the package can't be trusted")
        check(try want(), try files(change: { $0.append(0) }), MacSetupCheck.badSignature, "the package can't be trusted")
    }

    func testAManifestThatIsntOneIsRefused() {
        let good: [String: Any] = ["kind": "domovoi-mac-setup", "schema": 1, "tag": "v1.2.0", "version": "1.2.0", "commit": commit,
                                   "file": "DomovoiSetup-mac.pkg", "size": 3000, "sha256": String(repeating: "a", count: 64)]
        func parse(_ d: [String: Any]) -> String? {
            MacSetupManifest.parse(try! JSONSerialization.data(withJSONObject: d)).1
        }
        XCTAssertNil(parse(good))
        XCTAssertEqual(parse(good.merging(["kind": "domovoi-windows-setup"]) { $1 }), "its manifest is for something else")
        XCTAssertEqual(parse(good.merging(["schema": 2]) { $1 }), "its manifest is for something else")
        XCTAssertEqual(parse(good.merging(["schema": true]) { $1 }), "its manifest is for something else")
        XCTAssertEqual(parse(good.merging(["tag": "v1.2.1"]) { $1 }), "its manifest names no release")
        XCTAssertEqual(parse(good.merging(["commit": "abc"]) { $1 }), "its manifest names no commit")
        XCTAssertEqual(parse(good.merging(["file": "DomovoiSetup.exe"]) { $1 }), "its manifest doesn't describe a Mac package")
        XCTAssertEqual(parse(good.merging(["size": 0]) { $1 }), "its manifest doesn't describe a Mac package")
        XCTAssertEqual(parse(good.merging(["size": 1.5]) { $1 }), "its manifest doesn't describe a Mac package")
        XCTAssertEqual(MacSetupManifest.parse(Data("[]".utf8)).1, "its manifest isn't readable")
    }

    // ─── what the install pinned ───────────────────────────────────────

    func testThePinnedKeyAndBuildAreBelievedOnlyFromRootsFiles() throws {
        let share = try tempDir()
        defer { try? FileManager.default.removeItem(at: share) }
        try FileManager.default.createDirectory(at: share.appendingPathComponent("installer"), withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: fx.appendingPathComponent("test-key.pub"), to: share.appendingPathComponent(MacReleaseNames.keyFile))
        try #"{"schema":1,"version":"1.1.0","commit":"\#(commit)","repo_url":"https://example.org/d.git","release_repo":"alex/domovoi-train","tag":"v1.1.0"}"#
            .write(to: share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        XCTAssertEqual(PinnedRelease.key(share: share, trust: { _ in true }), try key(fx.appendingPathComponent("test-key.pub")))
        XCTAssertEqual(PinnedRelease.build(share: share, trust: { _ in true })?.releaseRepo, "alex/domovoi-train")
        XCTAssertNil(PinnedRelease.key(share: share, trust: { _ in false }))
        XCTAssertNil(PinnedRelease.build(share: share, trust: { _ in false }))
        // The real rule: a file this test made is the test account's, not root's.
        XCTAssertFalse(PinnedRelease.rootOnly(share.appendingPathComponent(MacReleaseNames.keyFile)))
        // A system file is root's all the way up.
        XCTAssertTrue(PinnedRelease.rootOnly(URL(fileURLWithPath: "/System/Library/CoreServices/SystemVersion.plist")))
        // A tag that isn't v + the version, or a repository that isn't one, makes the record unreadable.
        try #"{"schema":1,"version":"1.1.0","commit":"\#(commit)","repo_url":"x","tag":"v1.2.0"}"#
            .write(to: share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        XCTAssertNil(PinnedRelease.build(share: share, trust: { _ in true }))
        try #"{"schema":1,"version":"1.1.0","commit":"\#(commit)","repo_url":"x","release_repo":"https://evil.example/x"}"#
            .write(to: share.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        XCTAssertNil(PinnedRelease.build(share: share, trust: { _ in true }))
    }

    func testThePackageShipsTheKeyTheUpdatesTrust() throws {
        let build = try read("build.sh")
        XCTAssertTrue(build.contains(#"cp "$RELEASE_KEY" "$S/installer/release-signing.pub""#), "share/installer/release-signing.pub")
        XCTAssertTrue(build.contains("PROJECT_KEY=$REPO/windows/installer/release-signing.pub"), "the project's key by default")
        XCTAssertTrue(build.contains("needs --release-repo (a throwaway fork)"), "a throwaway key never points at the project's releases")
        XCTAssertEqual(MacReleaseNames.keyFile, "installer/release-signing.pub")
    }
}
