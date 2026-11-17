import CoreServices
import XCTest
@testable import DomovoiMacKit

/// Phase 4: the household's way in (mac-installer PLAN 4.2a). The pf
/// redirect's rules, the token and forwarding bookkeeping, the guest's
/// reply route, lan.json following the network, domovoi.local's rule, and
/// the media watcher's batching, all with fakes: pf, the VM and the core
/// are answered by FakePf, a FakeRunner and FakeHTTP.
final class NetworkTests: XCTestCase {
    let en0 = LanInterface(name: "en0", ipv4: "192.168.1.20", prefix: 24, wifi: true, primary: true)
    let en7 = LanInterface(name: "en7", ipv4: "192.168.1.21", prefix: 24, wifi: false, primary: false)
    let vm = GuestAddress(ipv4: "192.168.64.5", prefix: 24, gateway: "192.168.64.1", defaultViaLan: true, policyRoute: true)

    // ─── addresses ───────────────────────────────────────────────────

    func testIPv4Arithmetic() {
        XCTAssertEqual(IPv4.prefixLength(mask: "255.255.255.0"), 24)
        XCTAssertEqual(IPv4.prefixLength(mask: "255.255.252.0"), 22)
        XCTAssertEqual(IPv4.prefixLength(mask: "0.0.0.0"), 0)
        XCTAssertNil(IPv4.prefixLength(mask: "255.0.255.0"), "not contiguous")
        XCTAssertNil(IPv4.prefixLength(mask: "255.255.255.00"))
        XCTAssertEqual(IPv4.network("192.168.1.20", prefix: 24), "192.168.1.0/24")
        XCTAssertEqual(IPv4.network("10.20.30.40", mask: "255.255.240.0"), "10.20.16.0/20")
        XCTAssertTrue(IPv4.contains("192.168.64.0/24", "192.168.64.5"))
        XCTAssertFalse(IPv4.contains("192.168.64.0/24", "192.168.65.5"))
        for ip in ["10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.200.10"] { XCTAssertTrue(IPv4.isPrivate(ip), ip) }
        for ip in ["172.32.0.1", "100.64.0.1", "169.254.1.1", "8.8.8.8", "127.0.0.1"] { XCTAssertFalse(IPv4.isPrivate(ip), ip) }
    }

    func testOnlyHomeNetworkInterfacesCarryTheRedirect() {
        XCTAssertTrue(LanInterfaces.usable(en0))
        XCTAssertFalse(LanInterfaces.usable(LanInterface(name: "utun3", ipv4: "10.8.0.2", prefix: 24)), "a VPN tunnel")
        XCTAssertFalse(LanInterfaces.usable(LanInterface(name: "bridge100", ipv4: "192.168.64.1", prefix: 24)), "the VM network's bridge")
        XCTAssertFalse(LanInterfaces.usable(LanInterface(name: "en0", ipv4: "100.70.1.2", prefix: 10)), "not a private address")
        XCTAssertFalse(LanInterfaces.usable(LanInterface(name: "en0", ipv4: "192.168.1.20", prefix: 32)), "no network to speak of")
        XCTAssertFalse(LanInterfaces.usable(LanInterface(name: "en0;x", ipv4: "192.168.1.20", prefix: 24)), "never into a rule")
        XCTAssertEqual(LanInterfaces.sorted([en7, en0]).map(\.name), ["en0", "en7"], "primary first")
        XCTAssertEqual(LanInterfaces.handedOut([en7, en0], fallback: nil), LanAddress(ipv4: "192.168.1.20", interface: "en0"))
        XCTAssertEqual(LanInterfaces.handedOut([], fallback: LanAddress(ipv4: "203.0.113.9", interface: "en0"))?.ipv4, "203.0.113.9",
                       "no home network: what Phase 3 handed out")
    }

    // ─── the rules ───────────────────────────────────────────────────

    func testTheRulesRedirectOnlyTheLanPortsFromEachInterfacesOwnNetwork() throws {
        let t = try PfRules.text(interfaces: [en0, en7], vm: vm)
        let lines = t.split(separator: "\n").map(String.init).filter { !$0.hasPrefix("#") }
        XCTAssertEqual(lines, [
            "table <domovoi_elsewhere> const { 0.0.0.0/1, 128.0.0.0/1, !192.168.1.20, !192.168.1.255, !192.168.1.21, !255.255.255.255, !224.0.0.0/4 }",
            "rdr pass on en0 inet proto tcp from 192.168.1.0/24 to (en0) port { 6369 6370 7676 8050:8099 8096 9467 } -> 192.168.64.5",
            "rdr pass on en7 inet proto tcp from 192.168.1.0/24 to (en7) port { 6369 6370 7676 8050:8099 8096 9467 } -> 192.168.64.5",
            "block drop in quick on en0 inet proto tcp from any to 192.168.64.0/24 flags S/SA",
            "block drop in quick on en7 inet proto tcp from any to 192.168.64.0/24 flags S/SA",
            "block drop in quick on en0 inet from any to <domovoi_elsewhere>",
            "block drop in quick on en7 inet from any to <domovoi_elsewhere>",
        ], "the table first, translation before filtering, as pf requires")
        XCTAssertTrue(PfRules.redirects(t))
        XCTAssertFalse(t.contains("6432"), "never the database")
        XCTAssertFalse(t.contains("11434"), "never the AI engine")
        XCTAssertFalse(t.contains("6650"), "never a room's control port")
        XCTAssertFalse(t.contains("pass in"), "never a pass rule: other programs' rules after the anchor still see every packet")
        XCTAssertEqual(try PfRules.text(interfaces: [], vm: vm), "", "no Wi-Fi or Ethernet address: no rules")
        let fenceOnly = try PfRules.text(interfaces: [en0], vm: nil)
        XCTAssertFalse(PfRules.redirects(fenceOnly), "no VM address: no redirect")
        XCTAssertTrue(fenceOnly.contains("block drop in quick on en0 inet from any to <domovoi_elsewhere>"), "but the fence stays")
        XCTAssertThrowsError(try PfRules.text(interfaces: [LanInterface(name: "en0", ipv4: "192.168.64.20", prefix: 24, primary: true)], vm: vm),
                             "a home network that is the VM network")
    }

    /// pf's table lookup as the kernel does it: the longest matching prefix
    /// decides, and a `!` entry means "not in the table".
    func inTable(_ text: String, _ ip: String) throws -> Bool {
        let line = try XCTUnwrap(text.split(separator: "\n").first { $0.hasPrefix("table <\(PfRules.fenceTable)>") })
        let lo = try XCTUnwrap(line.firstIndex(of: "{")), hi = try XCTUnwrap(line.firstIndex(of: "}"))
        var best: (len: Int, neg: Bool)?
        for raw in line[line.index(after: lo)..<hi].split(separator: ",") {
            var e = raw.trimmingCharacters(in: .whitespaces)
            let neg = e.hasPrefix("!")
            if neg { e.removeFirst() }
            let cidr = e.contains("/") ? e : e + "/32"
            let len = Int(cidr.split(separator: "/")[1])!
            if IPv4.contains(cidr, ip), len > (best?.len ?? -1) { best = (len, neg) }
        }
        return best.map { !$0.neg } ?? false
    }

    func testTheFenceDropsEverythingForwardedButKeepsTheMacsOwnTraffic() throws {
        let public8 = LanInterface(name: "en8", ipv4: "203.0.113.9", prefix: 24)
        let vpn = LanInterface(name: "utun3", ipv4: "10.8.0.2", prefix: 24)
        let t = try PfRules.text(interfaces: [en0, public8, vpn], vm: vm)
        // The redirect only on the home network; the fence on every en*.
        XCTAssertTrue(t.contains("rdr pass on en0 "))
        XCTAssertFalse(t.contains("rdr pass on en8"), "not a home network: no redirect")
        XCTAssertTrue(t.contains("block drop in quick on en8 inet from any to <domovoi_elsewhere>"), "but fenced")
        XCTAssertFalse(t.contains("utun3"), "a VPN tunnel is never touched")
        // For the Mac itself (dropped means "in the table").
        for ip in ["192.168.1.20", "192.168.1.255", "203.0.113.9", "203.0.113.255", "255.255.255.255", "224.0.0.251", "239.255.255.250"] {
            XCTAssertFalse(try inTable(t, ip), "\(ip) is the Mac's own (or a broadcast / multicast): never dropped")
        }
        // Somewhere else: a device routing through the Mac is dropped.
        for ip in ["10.8.0.5", "10.8.0.2", "192.168.64.5", "192.168.64.1", "192.168.1.30", "8.8.8.8", "172.16.0.1", "0.0.0.1", "127.0.0.1"] {
            XCTAssertTrue(try inTable(t, ip), "\(ip) is elsewhere: forwarded packets to it are dropped")
        }
        XCTAssertEqual(PfRules.ownDestinations(LanInterface(name: "en0", ipv4: "10.0.0.33", prefix: 16)), ["10.0.0.33", "10.0.255.255"])
        XCTAssertEqual(PfRules.ownDestinations(LanInterface(name: "en0", ipv4: "10.0.0.33", prefix: 32)), ["10.0.0.33"], "no broadcast on a /32")
        XCTAssertTrue(PfRules.fenceable(LanInterface(name: "en0", ipv4: "169.254.3.4", prefix: 16)), "a self-assigned address is fenced too")
        XCTAssertFalse(PfRules.fenceable(LanInterface(name: "bridge100", ipv4: "192.168.64.1", prefix: 24)), "the VM's way out is never fenced")
        XCTAssertFalse(PfRules.fenceable(LanInterface(name: "en0;x", ipv4: "192.168.1.20", prefix: 24)), "never into a rule")
    }

    func testTheStockPfConfHasTheAnchorPoints() {
        XCTAssertTrue(PfRules.hasAnchorPoints(FakePf.stockPfConf))
        XCTAssertFalse(PfRules.hasAnchorPoints("scrub-anchor \"com.apple/*\"\nanchor \"com.apple/*\"\n"), "no rdr-anchor")
        XCTAssertFalse(PfRules.hasAnchorPoints("# rdr-anchor \"com.apple/*\"\n# anchor \"com.apple/*\"\n"), "commented out")
    }

    // ─── pf: token, forwarding, release ──────────────────────────────

    func pf() throws -> (PfController, FakePf, FakeRunner, URL) {
        let d = try tempDir()
        let r = FakeRunner()
        let f = FakePf()
        f.install(on: r)
        let c = PfController(runner: r, fileOps: LocalFileOps(), stateFile: d.appendingPathComponent("pf.json"))
        let conf = d.appendingPathComponent("pf.conf")
        try FakePf.stockPfConf.write(to: conf, atomically: true, encoding: .utf8)
        c.pfConf = conf
        return (c, f, r, d)
    }

    func testOneTokenPerBootAndForwardingRecordedThenPutBack() throws {
        let (c, f, _, d) = try pf()
        defer { try? FileManager.default.removeItem(at: d) }
        let rules = try PfRules.text(interfaces: [en0], vm: vm)
        var st = try c.apply(rules: rules, tokenDir: d)
        XCTAssertEqual(f.anchor, rules, "loaded into com.apple/domovoi")
        XCTAssertEqual(f.refs, ["4001"])
        XCTAssertEqual(st.token, "4001")
        XCTAssertEqual(f.forwarding, "1")
        XCTAssertEqual(st.forwardingBefore, "0")
        let mode = (try FileManager.default.attributesOfItem(atPath: c.stateFile.path)[.posixPermissions] as? NSNumber)?.intValue
        XCTAssertEqual(mode, 0o600)

        // Again (a resync, a new address): the same token, forwarding untouched.
        st = try c.apply(rules: try PfRules.text(interfaces: [en7], vm: vm), tokenDir: d)
        XCTAssertEqual(f.refs, ["4001"], "never a second reference")
        XCTAssertEqual(f.calls.filter { $0.first == "sysctl" }.count, 1, "forwarding set once")
        XCTAssertTrue(f.anchor.contains("on en7"))

        // Uninstall: everything back as it was found.
        let done = try c.release()
        XCTAssertEqual(f.anchor, "")
        XCTAssertEqual(f.refs, [], "our reference released: pf is off again if nobody else holds one")
        XCTAssertEqual(f.forwarding, "0")
        XCTAssertEqual(done.count, 3, "\(done)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: c.stateFile.path))
        XCTAssertNoThrow(try c.release(), "a second run does nothing harmful")
    }

    func testAnotherProgramsPfAndForwardingAreLeftAlone() throws {
        let (c, f, _, d) = try pf()
        defer { try? FileManager.default.removeItem(at: d) }
        f.refs = ["777"]          // someone else's reference: pf already on
        f.forwarding = "1"        // someone else's forwarding
        try c.apply(rules: try PfRules.text(interfaces: [en0], vm: vm), tokenDir: d)
        XCTAssertEqual(f.refs, ["777", "4001"])
        _ = try c.release()
        XCTAssertEqual(f.refs, ["777"], "theirs stays, so pf stays on")
        XCTAssertEqual(f.forwarding, "1", "forwarding was on before Setup: it stays on")
        XCTAssertFalse(f.calls.contains { $0.first == "sysctl" }, "never written")
    }

    func testTheFenceAloneTakesAReferenceButNeverSwitchesForwardingOn() throws {
        let (c, f, _, d) = try pf()
        defer { try? FileManager.default.removeItem(at: d) }
        var st = try c.apply(rules: try PfRules.text(interfaces: [en0], vm: nil), tokenDir: d)
        XCTAssertTrue(f.anchor.contains("<domovoi_elsewhere>"))
        XCTAssertEqual(f.refs, ["4001"], "pf must be on for the fence to drop anything")
        XCTAssertEqual(f.forwarding, "0", "nothing to forward yet")
        XCTAssertNil(st.forwardingBefore)
        st = try c.apply(rules: try PfRules.text(interfaces: [en0], vm: vm), tokenDir: d)
        XCTAssertEqual(f.forwarding, "1", "the redirect needs it")
        XCTAssertEqual(st.forwardingBefore, "0")
        XCTAssertEqual(f.refs, ["4001"])
        _ = try c.release()
        XCTAssertEqual(f.forwarding, "0")
        XCTAssertEqual(f.refs, [])
    }

    func testAfterARestartTheOldTokenIsNotReleasedAndANewOneTaken() throws {
        let (c, f, _, d) = try pf()
        defer { try? FileManager.default.removeItem(at: d) }
        let rules = try PfRules.text(interfaces: [en0], vm: vm)
        try c.apply(rules: rules, tokenDir: d)
        f.reboot()
        f.refs = ["4001"]  // a number the new boot happens to reuse for another program
        let st = try c.apply(rules: rules, tokenDir: d)
        XCTAssertEqual(st.token, "4002")
        XCTAssertEqual(f.forwarding, "1")
        f.reboot()
        f.refs = ["4002"]  // again someone else's by now
        _ = try c.release()
        XCTAssertEqual(f.refs, ["4002"], "a token from another boot is never released")
        XCTAssertFalse(f.calls.contains(["-X", "4002"]))
    }

    func testAMainRulesetNotLoadedIsLoadedFromTheStockFileButAChangedFileIsLeftAlone() throws {
        let (c, f, _, d) = try pf()
        defer { try? FileManager.default.removeItem(at: d) }
        f.mainLoaded = false
        try c.apply(rules: try PfRules.text(interfaces: [en0], vm: vm), tokenDir: d)
        XCTAssertTrue(f.ran(["-f", c.pfConf.path]), "the stock pf.conf, as macOS loads it")
        f.mainLoaded = false
        try "# the household's own firewall\npass all\n".write(to: c.pfConf, atomically: true, encoding: .utf8)
        XCTAssertThrowsError(try c.apply(rules: try PfRules.text(interfaces: [en0], vm: vm), tokenDir: d)) {
            XCTAssertTrue("\($0)".contains("doesn't edit it"), "\($0)")
        }
        XCTAssertEqual(f.calls.filter { $0 == ["-f", c.pfConf.path] }.count, 1, "never loads a pf.conf someone changed")
    }

    // ─── the guest's side ────────────────────────────────────────────

    func testTheGuestsAnswerIsRead() {
        let g = GuestNet.parse("addr 192.168.64.5/24\ngw 192.168.64.1\ndefault-dev lima0\nrule ok\n")
        XCTAssertEqual(g, GuestAddress(ipv4: "192.168.64.5", prefix: 24, gateway: "192.168.64.1", defaultViaLan: true, policyRoute: true))
        XCTAssertEqual(GuestNet.parse("addr 192.168.64.5/24\ngw none\ndefault-dev eth0\nrule none\n")?.defaultViaLan, false)
        XCTAssertNil(GuestNet.parse("addr none\ngw none\ndefault-dev eth0\nrule none\n"))
        XCTAssertNil(GuestNet.parse("addr 192.168.64.5\n"))
        XCTAssertTrue(GuestNet.script.contains("lookup main suppress_prefixlength 0"), "the VM's own networks keep routing as before")
        XCTAssertTrue(GuestNet.script.contains("from \"$ip\" lookup \"$t\""))
    }

    /// A NetworkSync on fakes: interfaces as given, a VM that answers the
    /// guest script and takes lan.json.
    final class World {
        let dir: URL
        let runner: FakeRunner
        let pf: FakePf
        var interfaces: [LanInterface] = []
        var guestOut = "addr 192.168.64.5/24\ngw 192.168.64.1\ndefault-dev lima0\nrule ok\n"
        var vmUp = true
        var lanWrites: [Data] = []
        let sync: NetworkSync

        init() throws {
            let d = try tempDir()
            let r = FakeRunner()
            let f = FakePf()
            let layout = Layout(mode: .system, root: d)
            try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
            f.install(on: r)
            let lima = Lima(limactl: "/L/limactl", limaHome: "/A/lima", home: "/A/home", runner: r)
            let s = NetworkSync(layout: layout, lima: lima, runner: r, fileOps: LocalFileOps())
            let conf = d.appendingPathComponent("pf.conf")
            try FakePf.stockPfConf.write(to: conf, atomically: true, encoding: .utf8)
            s.pf.pfConf = conf
            s.fallbackAddress = { nil }
            dir = d
            runner = r
            pf = f
            sync = s
            sync.readInterfaces = { [unowned self] in self.interfaces }
            runner.on { [unowned self] c in
                guard c.executable == "/L/limactl", c.arguments.first == "shell" else { return nil }
                let inner = Array(c.arguments.dropFirst(4))
                guard self.vmUp else { return CommandResult(status: 255, stderr: "ssh: connect refused") }
                if inner.count == 4, inner[3] == GuestNet.script { return CommandResult(status: 0, stdout: self.guestOut) }
                if inner.count == 4, inner[3].contains(HostFiles.lanFile) { self.lanWrites.append(c.stdin ?? Data()); return CommandResult(status: 0) }
                return nil
            }
        }

        var lastLan: String? {
            lanWrites.last.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }?["ipv4"] as? String
        }
        deinit { try? FileManager.default.removeItem(at: dir) }
    }

    func testTheRedirectAndLanJSONFollowTheNetwork() throws {
        let w = try World()
        w.interfaces = [en0]
        var s = try w.sync.sync(refreshVM: true)
        XCTAssertNil(s.problem)
        XCTAssertTrue(s.redirect)
        XCTAssertEqual(w.lastLan, "192.168.1.20")
        XCTAssertTrue(w.pf.anchor.contains("on en0 inet proto tcp from 192.168.1.0/24"))
        let status = try XCTUnwrap(NetworkSync.readStatus(Layout(mode: .system, root: w.dir)))
        XCTAssertEqual(status.lanAddress, "192.168.1.20")
        XCTAssertEqual(status.vm?.ipv4, "192.168.64.5")

        // Nothing changed: nothing reloaded, nothing rewritten.
        let loads = w.pf.calls.filter { $0.count == 4 && $0[2] == "-f" }.count
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(w.pf.calls.filter { $0.count == 4 && $0[2] == "-f" }.count, loads)
        XCTAssertEqual(w.lanWrites.count, 1)

        // Wi-Fi off, Ethernet on (another address on the same network).
        w.interfaces = [LanInterface(name: "en7", ipv4: "192.168.1.21", prefix: 24, wifi: false, primary: true)]
        s = try w.sync.sync(refreshVM: false)
        XCTAssertEqual(w.lastLan, "192.168.1.21", "satellites are handed the new address")
        XCTAssertTrue(w.pf.anchor.contains("on en7") && !w.pf.anchor.contains("on en0"))

        // A DHCP renewal onto another network.
        w.interfaces = [LanInterface(name: "en7", ipv4: "10.0.0.33", prefix: 16, primary: true)]
        s = try w.sync.sync(refreshVM: false)
        XCTAssertTrue(w.pf.anchor.contains("from 10.0.0.0/16 to (en7)"))
        XCTAssertEqual(w.lastLan, "10.0.0.33")

        // Someone flushed the anchor by hand: put back at the next sync.
        w.pf.anchor = ""
        s = try w.sync.sync(refreshVM: false)
        XCTAssertTrue(w.pf.anchor.contains("rdr pass"))

        // Only a network that isn't a home one: no redirect, the fence stays.
        w.interfaces = [LanInterface(name: "en7", ipv4: "203.0.113.9", prefix: 24, primary: true)]
        s = try w.sync.sync(refreshVM: false)
        XCTAssertFalse(s.redirect)
        XCTAssertFalse(w.pf.anchor.contains("rdr"))
        XCTAssertTrue(w.pf.anchor.contains("block drop in quick on en7 inet from any to <domovoi_elsewhere>"))
        XCTAssertTrue(s.problem?.contains("isn't on a home network") == true)

        // Someone flushed only the filter rules: put back too.
        w.interfaces = [LanInterface(name: "en7", ipv4: "10.0.0.33", prefix: 16, primary: true)]
        s = try w.sync.sync(refreshVM: false)
        w.pf.anchor = w.pf.anchor.split(separator: "\n").filter { !$0.hasPrefix("block") }.joined(separator: "\n")
        s = try w.sync.sync(refreshVM: false)
        XCTAssertTrue(w.pf.anchor.contains("<domovoi_elsewhere>"))

        // No network at all: the redirect off, the problem said, lan.json left.
        w.interfaces = []
        s = try w.sync.sync(refreshVM: false)
        XCTAssertFalse(s.redirect)
        XCTAssertEqual(w.pf.anchor, "")
        XCTAssertTrue(s.problem?.contains("isn't on a home network") == true)
        XCTAssertEqual(w.lanWrites.count, 3)
        XCTAssertEqual(w.pf.refs.count, 1, "one reference throughout")
    }

    func testTheVMsNewAddressAfterARestartIsFollowedAndAQuietVMKeepsTheOldOne() throws {
        let w = try World()
        w.interfaces = [en0]
        try w.sync.sync(refreshVM: true)
        w.vmUp = false
        var s = try w.sync.sync(refreshVM: true)
        XCTAssertEqual(s.vm?.ipv4, "192.168.64.5", "the last address while the VM doesn't answer")
        w.vmUp = true
        w.guestOut = "addr 192.168.64.9/24\ngw 192.168.64.1\ndefault-dev lima0\nrule ok\n"
        s = try w.sync.sync(refreshVM: true)
        XCTAssertTrue(w.pf.anchor.contains("-> 192.168.64.9"))
        w.guestOut = "addr 192.168.64.9/24\ngw none\ndefault-dev eth0\nrule none\n"
        s = try w.sync.sync(refreshVM: true)
        XCTAssertTrue(s.problem?.contains("wrong network") == true, "replies would leave through eth0: said")
    }

    func testAChangedPfConfIsAProblemNotACrash() throws {
        let w = try World()
        w.interfaces = [en0]
        w.pf.mainLoaded = false
        try "pass all\n".write(to: w.sync.pf.pfConf, atomically: true, encoding: .utf8)
        let s = try w.sync.sync(refreshVM: true)
        XCTAssertFalse(s.redirect)
        XCTAssertTrue(s.problem?.contains("pf.conf") == true, s.problem ?? "")
        XCTAssertEqual(w.lastLan, "192.168.1.20", "lan.json still follows")
        XCTAssertEqual(w.pf.refs, [], "pf never switched on for rules it couldn't load")
    }

    func testTeardownLeavesNothing() throws {
        let w = try World()
        w.interfaces = [en0]
        try w.sync.sync(refreshVM: true)
        let layout = Layout(mode: .system, root: w.dir)
        let done = try NetworkSync.teardown(layout: layout, runner: w.runner, fileOps: LocalFileOps(), log: { _ in })
        XCTAssertFalse(done.isEmpty)
        XCTAssertEqual(w.pf.anchor, "")
        XCTAssertEqual(w.pf.refs, [])
        XCTAssertEqual(w.pf.forwarding, "0")
        for f in NetworkSync.files(layout) { XCTAssertFalse(FileManager.default.fileExists(atPath: f.path), f.path) }
    }

    // ─── the check before Setup ──────────────────────────────────────

    func testThePreflightWarnsWhenOtherDevicesWontReachIt() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        func run(_ lan: LanAddress?, pfConf: String?) -> PreflightCheck? {
            let p = Preflight(layout: Layout(mode: .system, root: d), runner: FakeRunner(), http: FakeHTTP())
            p.portInUse = { _ in false }
            p.lan = { lan }
            p.pfConfText = { pfConf }
            return p.run(pins: nil, ollamaMinimum: nil).checks.first { $0.id == "lan-reach" }
        }
        let home = LanAddress(ipv4: "192.168.1.20", interface: "en0")
        XCTAssertNil(run(home, pfConf: FakePf.stockPfConf), "a home network and the stock pf.conf: nothing to say")
        XCTAssertNil(run(home, pfConf: nil), "pf.conf unreadable: Setup's own step says so later")
        XCTAssertEqual(run(home, pfConf: "pass all\n")?.status, "warn", "a changed pf.conf: a warning, not a stop")
        XCTAssertEqual(run(LanAddress(ipv4: "203.0.113.9", interface: "en0"), pfConf: FakePf.stockPfConf)?.status, "warn")
    }

    // ─── domovoi.local ───────────────────────────────────────────────

    func testDomovoiLocalIsPublishedOnlyWhenItMakesSense() {
        let lan = LanAddress(ipv4: "192.168.1.20", interface: "en0")
        XCTAssertEqual(LocalNamePolicy.decide(localHostName: "Alexs-Mac-mini", lan: lan), .publish)
        XCTAssertEqual(LocalNamePolicy.decide(localHostName: "Domovoi", lan: lan), .thisMac, "the Mac answers for it already")
        XCTAssertEqual(LocalNamePolicy.decide(localHostName: "x", lan: nil), .off)
        XCTAssertEqual(LocalNamePolicy.decide(localHostName: "x", lan: LanAddress(ipv4: "203.0.113.9", interface: "en0")), .off,
                       "never a public address")
        XCTAssertEqual(LocalNamePolicy.name, "domovoi.local.")
    }

    // ─── the media watcher ───────────────────────────────────────────

    let mounts = [MediaMount(name: "Music", hostPath: "/Users/Shared/Domovoi/Music"),
                  MediaMount(name: "Audiobooks", hostPath: "/Users/Shared/Domovoi/Audiobooks")]

    func testMacPathsMapToTheVMsNames() {
        XCTAssertEqual(MediaPaths.guestPath(for: "/Users/Shared/Domovoi/Music/Artist/01 Song.mp3", mounts: mounts),
                       "/srv/media/music/Artist/01 Song.mp3")
        XCTAssertEqual(MediaPaths.guestPath(for: "/Users/Shared/Domovoi/Music", mounts: mounts), "/srv/media/music")
        XCTAssertNil(MediaPaths.guestPath(for: "/Users/Shared/Domovoi/Musical/x.mp3", mounts: mounts), "a neighbour, not inside")
        XCTAssertNil(MediaPaths.guestPath(for: "/Users/alex/Music/x.mp3", mounts: mounts))
        for junk in ["/a/.DS_Store", "/a/._01.mp3", "/a/album.mp3.part", "/a/x.crdownload", "/a/.Spotlight-V100/Store", "/a/~$doc.docx"] {
            XCTAssertTrue(MediaPaths.ignored(junk), junk)
        }
        XCTAssertFalse(MediaPaths.ignored("/a/Album/01 Song.mp3"))
    }

    func testChangesWaitUntilQuietAndManyBecomeTheFolder() {
        let b = ChangeBatcher(roots: ["/srv/media/music", "/srv/media/audiobooks"], quiet: 5, maxPaths: 3)
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        b.add("/srv/media/music/A/01.mp3", at: t0)
        b.add("/srv/media/music/A/01.mp3", at: t0 + 3)   // still being copied
        XCTAssertEqual(b.due(at: t0 + 5), [], "changed 2 s ago: not yet")
        XCTAssertEqual(b.due(at: t0 + 8.5), ["/srv/media/music/A/01.mp3"])
        XCTAssertEqual(b.due(at: t0 + 20), [], "each once")
        for i in 0..<4 { b.add("/srv/media/music/B/0\(i).mp3", at: t0 + 30) }
        b.add("/srv/media/audiobooks/Book.m4b", at: t0 + 30)
        XCTAssertEqual(b.due(at: t0 + 40), ["/srv/media/audiobooks/Book.m4b", "/srv/media/music"], "four in one folder: the folder")
        b.add("/elsewhere/x.mp3", at: t0)
        XCTAssertTrue(b.isEmpty)
    }

    func testTheWatcherPostsToTheCoreAndRetriesWhenItIsDown() {
        let http = FakeHTTP()
        let w = MediaWatcher(mounts: mounts, corePort: 6370, http: http, log: { _ in }, quiet: 5)
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        w.tick(at: t0)
        let ev = { (p: String, f: Int) in FSEventsWatcher.Event(path: p, flags: UInt32(f)) }
        w.take([ev("/Users/Shared/Domovoi/Music/A/01.mp3", kFSEventStreamEventFlagItemCreated),
                ev("/Users/Shared/Domovoi/Music/A/.DS_Store", kFSEventStreamEventFlagItemModified)], at: t0)
        http.postStatus = nil   // the core is restarting
        w.tick(at: t0 + 6)
        XCTAssertEqual(http.postedPaths, [["/srv/media/music/A/01.mp3"]])
        XCTAssertTrue(w.status.contains("last send failed"))
        http.postStatus = 202
        w.tick(at: t0 + 7)
        XCTAssertEqual(http.postedPaths.last, ["/srv/media/music/A/01.mp3"], "sent again")
        XCTAssertEqual(http.posts.last?.url, "http://127.0.0.1:6370/v1/host/media-changed")
        XCTAssertEqual(http.posts.last?.headers["X-Requested-With"], "DomovoiHelper")
        // FSEvents lost track: the whole folder.
        w.take([ev("/Users/Shared/Domovoi/Audiobooks/x", kFSEventStreamEventFlagMustScanSubDirs)], at: t0 + 10)
        w.tick(at: t0 + 16)
        XCTAssertEqual(http.postedPaths.last, ["/srv/media/audiobooks"])
        // A day on: every folder whole, the fallback for anything missed.
        w.tick(at: t0 + MediaWatcher.fullEvery + 1)
        XCTAssertEqual(http.postedPaths.last, ["/srv/media/music", "/srv/media/audiobooks"])
    }
}
