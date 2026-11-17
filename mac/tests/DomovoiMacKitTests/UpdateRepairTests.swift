import XCTest
@testable import DomovoiMacKit

/// What an update's repair does with a newer package's pins (PLAN 4.12: the
/// Mac side is the app, the helper, the plists and the Lima, Ollama and
/// Ubuntu image pins), through InstallerTests' fakes.
extension InstallerTests {
    func rewritePins(lima: String = "2.2.1", ollama: String = "0.34.4", image: String = "ubuntu-26.04-server-cloudimg-arm64.img") throws {
        try """
        LIMA_VERSION=\(lima)
        LIMA_URL=https://example.org/v\(lima)/lima-\(lima)-Darwin-arm64.tar.gz
        LIMA_SHA256=\(sha(blobs["lima"]!))
        OLLAMA_VERSION=\(ollama)
        OLLAMA_URL=https://example.org/v\(ollama)/ollama-darwin.tgz
        OLLAMA_SHA256=\(sha(blobs["ollama"]!))
        OLLAMA_MIN_VERSION=0.12.7
        UBUNTU_IMAGE_URL=https://example.org/\(image)
        UBUNTU_IMAGE_SHA256=\(sha(blobs["ubuntu"]!))
        """.write(to: share.appendingPathComponent("installer/pins.conf"), atomically: true, encoding: .utf8)
    }

    func testANewerLimaPinRestartsTheVMsJobSoItRunsTheNewLimactl() throws {
        try installer(options()).run()
        XCTAssertEqual(limactlVersion, "2.2.1")
        try rewritePins(lima: "2.3.0")
        launchd.bootstrapped = []
        try installer(options()).run()
        XCTAssertEqual(limactlVersion, "2.3.0", "the new tarball was unpacked")
        XCTAssertEqual(launchd.bootstrapped, [Labels.ollama, Labels.vm], "the VM's job restarted for the new limactl")
        XCTAssertTrue(progress.lines.contains { $0.contains("restarting the VM's job: Lima was updated") })
        XCTAssertEqual(runner.ran("limactl", "create").count, 1, "the same VM: never made again")
    }

    func testANewerUbuntuImagePinNeverRemakesTheVM() throws {
        try installer(options()).run()
        try rewritePins(image: "ubuntu-26.04.1-server-cloudimg-arm64.img")
        launchd.bootstrapped = []
        try installer(options()).run()
        XCTAssertEqual(runner.ran("limactl", "create").count, 1, "the existing VM keeps its disk; the new image is for a new install")
        XCTAssertEqual(launchd.bootstrapped, [Labels.ollama], "the running VM is left running")
    }

    func testAChangedVMJobFileIsLoaded() throws {
        try installer(options()).run()
        // A newer package's template for the VM's job.
        let t = share.appendingPathComponent("launchd/\(Labels.vm).plist")
        var text = try String(contentsOf: t, encoding: .utf8)
        text = text.replacingOccurrences(of: "</dict>\n</plist>", with: "\t<key>ProcessType</key>\n\t<string>Standard</string>\n</dict>\n</plist>")
        try text.write(to: t, atomically: true, encoding: .utf8)
        launchd.bootstrapped = []
        try installer(options()).run()
        XCTAssertEqual(launchd.bootstrapped, [Labels.ollama, Labels.vm])
        XCTAssertTrue(progress.lines.contains { $0.contains("its job file changed") })
    }

    func testTheSystemRepairWritesThePackagesVersionAndTheUpdater() throws {
        _ = try systemRoot()
        var o = try options()
        o.macVersion = "1.2.0"
        try installer(o).run()
        let side = try XCTUnwrap(guestFiles[HostFiles.sideFile])
        let doc = try XCTUnwrap(JSONSerialization.jsonObject(with: side) as? [String: Any])
        XCTAssertEqual(doc["version"] as? String, "1.2.0", "the package's version, not Layout's constant")
        XCTAssertEqual(doc["updates"] as? Int, 1, "the system install has the updater")
        XCTAssertTrue(progress.events.contains { $0.0 == "host-files" && $0.2.contains("side.json 1.2.0") })
    }

    func testTheUserModeSaysNoUpdater() throws {
        try installer(options()).run()
        let doc = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(guestFiles[HostFiles.sideFile])) as? [String: Any])
        XCTAssertNil(doc["updates"], "a test install in a folder can't update itself")
    }

    func testSystemOptionsTakeTheVersionFromThePackage() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        try #"{"schema":1,"version":"1.2.0","commit":"\#(String(repeating: "b", count: 40))","repo_url":"https://example.org/d.git","tag":"v1.2.0"}"#
            .write(to: d.appendingPathComponent("build.json"), atomically: true, encoding: .utf8)
        var a = try Args(["install", "--system", "--internet", "never", "--media-root", "/Users/Shared/Domovoi"])
        _ = try Machine.layout(&a, defaultSystem: false)
        let o = try SetupCommands.systemOptions(&a, layout: Layout(mode: .system, root: d.appendingPathComponent("root")), share: d)
        XCTAssertEqual(o.macVersion, "1.2.0")
    }
}
