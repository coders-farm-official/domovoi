import XCTest
@testable import DomovoiMacKit

/// Train 1 S2-10: the core (in the VM) refused to sign its identity proof
/// for the Mac's own LAN address, which rooms dial and the pf redirect
/// forwards, so no room could join. main's core takes such an address from
/// the host's root-owned network-name report
/// (`server_identity.VOUCHED_ADDRESS_SOURCES`); the helper writes it.
final class VouchedAddressTests: XCTestCase {
    let when = Date(timeIntervalSince1970: 1_800_000_000)
    let en0 = LanInterface(name: "en0", ipv4: "192.168.1.20", prefix: 24, wifi: true, primary: true)

    /// main's `lan_name.responder.STATES`: a report with any other state is
    /// not believed at all.
    static let coreStates: Set<String> = ["announced", "held", "checking", "taken", "off", "no_lan", "failed", "public"]

    func obj(_ d: Data) throws -> [String: Any] { try XCTUnwrap(JSONSerialization.jsonObject(with: d) as? [String: Any]) }

    func testTheReportIsWhatMainsCoreBelieves() throws {
        let r = LanNameReport.make(interfaces: [en0], localName: "published")
        let d = try r.json(at: when)
        let o = try obj(d)
        XCTAssertEqual(o["schema"] as? Int, 1)
        XCTAssertTrue(o["request_id"] is NSNull)
        XCTAssertEqual(o["requested"] as? String, "domovoi", "a name label (names.normalize keeps it as is)")
        XCTAssertEqual(o["claimed"] as? String, "domovoi")
        XCTAssertEqual(o["state"] as? String, "announced")
        XCTAssertTrue(o["taken"] is NSNull)
        XCTAssertEqual(o["addresses"] as? [String], ["192.168.1.20"])
        XCTAssertTrue(o["error"] is NSNull)
        XCTAssertEqual(o["written_at"] as? String, "2027-01-15T08:00:00Z")
        XCTAssertLessThanOrEqual(d.count, 4096, "service._REPORT_MAX_BYTES")
        XCTAssertEqual(d.last, 0x0a)
        XCTAssertEqual(HostFiles.lanNameFile, "/var/lib/domovoi-mac/lan_name.json", "the Mac's entry main's service.py names")
    }

    func testOnlyTheRedirectsHomeNetworkAddressesAtMostFour() {
        let many = (1...6).map { LanInterface(name: "en\($0)", ipv4: "10.0.\($0).5", prefix: 24) }
        XCTAssertEqual(LanNameReport.make(interfaces: many, localName: nil).addresses, ["10.0.1.5", "10.0.2.5", "10.0.3.5", "10.0.4.5"],
                       "parse_report refuses the whole report beyond four")
        let mixed = [en0, en0, LanInterface(name: "en7", ipv4: "100.64.0.1", prefix: 10), LanInterface(name: "en8", ipv4: "172.16.4.1", prefix: 16)]
        XCTAssertEqual(LanNameReport.make(interfaces: mixed, localName: nil).addresses, ["192.168.1.20", "172.16.4.1"],
                       "RFC 1918 only (lan_address_ok), once each, no port: the core's own port is the one vouched for")
        for bad in ["100.64.0.1", "169.254.1.2", "8.8.8.8", "127.0.0.1", "192.168.1.020"] { XCTAssertFalse(LanNameReport.vouchable(bad), bad) }
    }

    func testTheNameFieldsSayWhatTheHelperDoes() throws {
        func f(_ n: String?, _ interfaces: [LanInterface]? = nil) -> LanNameReport { LanNameReport.make(interfaces: interfaces ?? [en0], localName: n) }
        XCTAssertEqual(f(nil).state, "checking")
        XCTAssertEqual(f("probing").state, "checking")
        XCTAssertEqual(f("this-mac").claimed, "domovoi")
        XCTAssertEqual(f("taken").state, "taken")
        XCTAssertEqual(f("taken").taken, "domovoi")
        XCTAssertNil(f("taken").claimed)
        XCTAssertEqual(f("off").state, "off")
        let failed = f("failed: mDNSResponder said -65563")
        XCTAssertEqual(failed.state, "failed")
        XCTAssertEqual(failed.error, "mDNSResponder said -65563")
        XCTAssertEqual(f("published", []).state, "no_lan", "no address: nothing vouched for")
        XCTAssertEqual(f("published", []).addresses, [])
        for n in [nil, "probing", "published", "this-mac", "taken", "off", "failed: x", "something new"] as [String?] {
            let s = try obj(f(n).json(at: when))["state"] as? String
            XCTAssertTrue(Self.coreStates.contains(s ?? ""), "\(n ?? "nil") -> \(s ?? "nil")")
        }
        let long = f("failed: " + String(repeating: "x", count: 300) + "\u{7}")
        XCTAssertEqual(long.error?.count, 200, "service._short: at most 200, no control characters")
    }

    func testTheGuestWriteIsRootsAlone() {
        XCTAssertEqual(HostFiles.guestWriteScript(path: HostFiles.lanNameFile),
                       "set -e; install -d -o root -g root -m 0755 '/var/lib/domovoi-mac'; umask 022; cat > '/var/lib/domovoi-mac/.lan_name.json.tmp'; chown root:root '/var/lib/domovoi-mac/.lan_name.json.tmp'; chmod 0644 '/var/lib/domovoi-mac/.lan_name.json.tmp'; mv -f '/var/lib/domovoi-mac/.lan_name.json.tmp' '/var/lib/domovoi-mac/lan_name.json'",
                       "service._root_only: root's, writable by nobody else, and so is its folder")
    }

    func testTheReportFollowsTheNetwork() throws {
        let w = try NetworkTests.World()
        var writes: [Data] = []
        w.runner.on { [unowned w] c in
            // A VM that is down: World's own handler answers.
            guard w.vmUp, c.executable == "/L/limactl", c.arguments.first == "shell" else { return nil }
            let inner = Array(c.arguments.dropFirst(4))
            guard inner.count == 4, inner[3].contains(HostFiles.lanNameFile) else { return nil }
            writes.append(c.stdin ?? Data())
            return CommandResult(status: 0)
        }
        var name: String?
        w.sync.localNameState = { name }
        func last() throws -> [String: Any] { try obj(try XCTUnwrap(writes.last)) }

        w.interfaces = [en0]
        var s = try w.sync.sync(refreshVM: true)
        XCTAssertNil(s.problem)
        XCTAssertEqual(writes.count, 1)
        XCTAssertEqual(try last()["addresses"] as? [String], ["192.168.1.20"])

        // Nothing changed: not written again.
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(writes.count, 1)

        // domovoi.local answered meanwhile.
        name = "published"
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(writes.count, 2)
        XCTAssertEqual(try last()["state"] as? String, "announced")

        // A hotspot (the sitting's Block H step 2), then home again.
        w.interfaces = [LanInterface(name: "en0", ipv4: "172.20.10.4", prefix: 24, wifi: true, primary: true)]
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(try last()["addresses"] as? [String], ["172.20.10.4"])
        w.interfaces = [en0]
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(try last()["addresses"] as? [String], ["192.168.1.20"])
        XCTAssertEqual(writes.count, 4)

        // No home network: nothing is vouched for any more.
        w.interfaces = []
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(try last()["addresses"] as? [String], [])
        XCTAssertEqual(try last()["state"] as? String, "no_lan")

        // A VM that doesn't take the write: said, and tried again next time.
        w.interfaces = [en0]
        w.vmUp = false
        s = try w.sync.sync(refreshVM: false)
        XCTAssertTrue(s.problem?.contains(HostFiles.lanNameFile) == true, s.problem ?? "")
        w.vmUp = true
        let before = writes.count
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(writes.count, before + 1)
    }

    /// Once main is merged: its core must read the Mac's report (one line,
    /// Core's half: the ("mac", ...) entry in REPORT_FILES).
    func testTheCoreReadsIt() throws {
        let service = repoDir.appendingPathComponent("domovoi/lan_name/service.py")
        guard FileManager.default.fileExists(atPath: service.path) else {
            throw XCTSkip("this branch's core predates main's network-name report (domovoi/lan_name): it can't read lan_name.json yet")
        }
        let text = try String(contentsOf: service, encoding: .utf8)
        // The tuple spans several lines: take it from `REPORT_FILES` to the
        // first line that is only ")".
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        let start = lines.firstIndex { $0.hasPrefix("REPORT_FILES") } ?? lines.endIndex
        let rest = lines[start...]
        let end = rest.firstIndex { $0.trimmingCharacters(in: .whitespaces) == ")" } ?? rest.endIndex
        let block = rest[..<min(end + 1, rest.endIndex)].joined(separator: "\n")
        XCTAssertTrue(block.contains("(\"mac\", \"\(HostFiles.lanNameFile)\")"), "REPORT_FILES must name the Mac's report: \(block)")
    }
}
