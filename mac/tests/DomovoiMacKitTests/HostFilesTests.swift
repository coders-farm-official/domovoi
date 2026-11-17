import XCTest
@testable import DomovoiMacKit

final class HostFilesTests: XCTestCase {
    let when = Date(timeIntervalSince1970: 1_800_000_000)

    func obj(_ d: Data) throws -> [String: Any] { try XCTUnwrap(JSONSerialization.jsonObject(with: d) as? [String: Any]) }

    func testLanJSONIsWhatLanAddressReads() throws {
        let d = try HostFiles.lanJSON(LanAddress(ipv4: "192.168.1.20", interface: "en0"), at: when)
        let o = try obj(d)
        // domovoi/lan_address.py: schema 1, an "ipv4" string; the rest only logged.
        XCTAssertEqual(o["schema"] as? Int, 1)
        XCTAssertEqual(o["ipv4"] as? String, "192.168.1.20")
        XCTAssertEqual(o["interface"] as? String, "en0")
        XCTAssertEqual(o["written_at"] as? String, "2027-01-15T08:00:00Z")
        XCTAssertLessThanOrEqual(d.count, 4096)
        XCTAssertEqual(d.last, 0x0a)
    }

    func testTheAddressesLanAddressRefusesAreRefused() {
        for bad in ["127.0.0.1", "127.9.9.9", "0.0.0.0", "256.1.1.1", "192.168.1", "192.168.1.020", "a.b.c.d", "", "::1", "192.168.1.20 "] {
            XCTAssertFalse(HostFiles.usable(bad), bad)
            XCTAssertThrowsError(try HostFiles.lanJSON(LanAddress(ipv4: bad, interface: nil), at: when), bad)
        }
        for good in ["192.168.1.20", "10.0.0.5", "172.16.4.1", "100.64.0.1"] { XCTAssertTrue(HostFiles.usable(good), good) }
    }

    func testSideJSONIsWhatTheVersionCardReads() throws {
        let side = try String(contentsOf: repoDir.appendingPathComponent("domovoi/windows_side.py"), encoding: .utf8)
        let o = try obj(HostFiles.sideJSON(version: Names.macSideVersion, commit: String(repeating: "a", count: 40), at: when))
        XCTAssertEqual(o["schema"] as? Int, 1)
        XCTAssertEqual(o["kind"] as? String, "mac")
        XCTAssertEqual(o["version"] as? String, Names.macSideVersion)
        XCTAssertEqual(o["build_commit"] as? String, String(repeating: "a", count: 40))
        let short = try obj(HostFiles.sideJSON(version: Names.macSideVersion, commit: "5645c495", at: when))
        XCTAssertNil(short["build_commit"], "only a full SHA, as the core reads it")
        // The updater: the number 1 (windows_side.read_host_side), only when asked.
        XCTAssertNil(short["updates"])
        let upd = try obj(HostFiles.sideJSON(version: "1.2.0", commit: nil, at: when, updates: true))
        XCTAssertEqual(upd["updates"] as? Int, 1)
        XCTAssertTrue(side.contains("updates == 1"), "the core reads the number 1")
        // The menu-bar card helper: the number 1 (host_kind.mac_card_helper), only when asked.
        XCTAssertNil(short["card_helper"])
        XCTAssertNil(upd["card_helper"])
        let card = try obj(HostFiles.sideJSON(version: "1.2.0", commit: nil, at: when, cardHelper: true))
        XCTAssertEqual(card["card_helper"] as? Int, 1)
        let hostKind = try String(contentsOf: repoDir.appendingPathComponent("domovoi/host_kind.py"), encoding: .utf8)
        XCTAssertTrue(hostKind.contains("doc.get(\"card_helper\")") && hostKind.contains("v == 1"), "the key and the number the core reads")
        XCTAssertTrue(side.contains("doc.get(\"build_commit\")"), "the key the core reads")
        // windows_side._VERSION_RE: MAJOR.MINOR.PATCH with an optional -suffix.
        XCTAssertNotNil(Names.macSideVersion.range(of: #"^v?[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(-[0-9A-Za-z.]{1,40})?$"#, options: .regularExpression))
    }

    func testTheGuestWriteIsRootsAtomicAndMode0644() {
        let s = HostFiles.guestWriteScript(path: HostFiles.lanFile)
        XCTAssertEqual(s, "set -e; install -d -o root -g root -m 0755 '/var/lib/domovoi-mac'; umask 022; cat > '/var/lib/domovoi-mac/.lan.json.tmp'; chown root:root '/var/lib/domovoi-mac/.lan.json.tmp'; chmod 0644 '/var/lib/domovoi-mac/.lan.json.tmp'; mv -f '/var/lib/domovoi-mac/.lan.json.tmp' '/var/lib/domovoi-mac/lan.json'")
    }

    func testThePathsAreTheCoresOwn() throws {
        let lan = try String(contentsOf: repoDir.appendingPathComponent("domovoi/lan_address.py"), encoding: .utf8)
        XCTAssertTrue(lan.contains("HOST_LAN_FILE = \"\(HostFiles.lanFile)\""))
        let side = try String(contentsOf: repoDir.appendingPathComponent("domovoi/windows_side.py"), encoding: .utf8)
        XCTAssertTrue(side.contains("HOST_SIDE_FILE = \"\(HostFiles.sideFile)\""))
    }

    func testProvisionArgs() {
        let answers = InstallRecord.Answers(internet: "never", tier: .floor, ollama: "own", mediaRoot: "/m",
                                            media: [MediaMount(name: "Music", hostPath: "/m/Music")],
                                            ports: Ports(web: 6390, core: 6391, ollama: 11500),
                                            size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: "Europe/Berlin")
        var src = ProvisionSource(script: URL(fileURLWithPath: "/p.sh"), repoBundle: URL(fileURLWithPath: "/b.bundle"), ref: "main", trackBranch: "main")
        src.noVisionModel = true
        XCTAssertEqual(ProvisionArgs.build(source: src, answers: answers, ollamaURL: "http://host.lima.internal:11500"), [
            "--repo-url", "/var/lib/domovoi-mac-install/origin.bundle", "--ref", "main", "--track-branch", "main",
            "--internet", "never", "--tier", "floor", "--timezone", "Europe/Berlin", "--media", "music=/srv/media/music",
            "--ollama-url", "http://host.lima.internal:11500", "--vm-guard", "192.168.5.0/24", "--vm-guard-dns", "192.168.5.3",
            "--vm-guard-lan", "lima0",
            "--no-vision-model",
        ])
    }

    func testProgressLines() {
        let p = ProvisionProgress.parse(#"@@DOMOVOI {"v":1,"step":"finish","index":23,"total":23,"state":"done","percent":100,"message":"","final":true,"result":"ok","failed_step":null}"#)
        XCTAssertEqual(p?.final, true)
        XCTAssertEqual(p?.result, "ok")
        XCTAssertNil(p?.failedStep)
        XCTAssertNil(ProvisionProgress.parse("@@DOMOVOI not json"))
        XCTAssertNil(ProvisionProgress.parse("plain output"))
    }

    /// The final line's "phone_app" (provision.sh's phone-app step): false
    /// when it couldn't download the Android app, true when the app is on
    /// the server, null or absent (an older provision.sh) when unknown.
    func testProgressLinePhoneApp() {
        let line = #"@@DOMOVOI {"v":1,"step":"finish","index":24,"total":24,"state":"done","percent":100,"message":"","final":true,"result":"ok","failed_step":null,"phone_app":PA,"warnings":1}"#
        XCTAssertEqual(ProvisionProgress.parse(line.replacingOccurrences(of: "PA", with: "false"))?.phoneApp, false)
        XCTAssertEqual(ProvisionProgress.parse(line.replacingOccurrences(of: "PA", with: "true"))?.phoneApp, true)
        let unknown = ProvisionProgress.parse(line.replacingOccurrences(of: "PA", with: "null"))
        XCTAssertEqual(unknown?.result, "ok")
        XCTAssertNil(unknown?.phoneApp)
        XCTAssertNil(ProvisionProgress.parse(#"@@DOMOVOI {"v":1,"step":"finish","state":"done","final":true,"result":"ok"}"#)?.phoneApp)
        XCTAssertNil(ProvisionProgress.parse(#"@@DOMOVOI {"v":1,"step":"apt","state":"start","percent":1}"#)?.phoneApp)
        XCTAssertEqual(ProvisionProgress.phoneAppMissing,
                       "The phone app couldn't be downloaded: this server needs the internet once, at some point, to put it here (or upload it in Settings).")
    }
}
