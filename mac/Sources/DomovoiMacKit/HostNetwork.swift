import Darwin
import Foundation

/// The VM's address on Apple's private VM network (Lima's vzNAT, `lima0` in
/// the guest): where the pf redirect sends the household's connections.
public struct GuestAddress: Equatable, Codable {
    public var ipv4: String
    public var prefix: Int
    /// The Mac's side of that network (the guest's default gateway there).
    public var gateway: String?
    /// The guest's preferred default route leaves through lima0 (spike S0:
    /// Lima gives it metric 100, eth0 200), so replies to the household go
    /// back the way they came.
    public var defaultViaLan: Bool
    /// The policy rule that sends anything from this address out through
    /// lima0 whatever the main table says (`ip rule ... lookup 166`).
    public var policyRoute: Bool

    public init(ipv4: String, prefix: Int, gateway: String? = nil, defaultViaLan: Bool = true, policyRoute: Bool = false) {
        self.ipv4 = ipv4
        self.prefix = prefix
        self.gateway = gateway
        self.defaultViaLan = defaultViaLan
        self.policyRoute = policyRoute
    }

    public var network: String? { IPv4.network(ipv4, prefix: prefix) }
}

public enum GuestNet {
    /// The guest interface the redirect delivers on (Lima's vzNAT).
    public static let interface = "lima0"
    /// The policy routing table for replies from the lima0 address (spike
    /// S3's choice).
    public static let table = 166

    /// One guest command, as root: reads lima0's address and the default
    /// routes, then makes sure replies from that address leave through
    /// lima0 (rule 165: the main table without its default routes, so the
    /// VM's own networks still route as before; rule 166: from the lima0
    /// address, table 166, whose only route is the default via lima0). Every
    /// line it prints is `key value`. Nothing persists in the guest: the
    /// helper puts it back after a VM restart.
    public static let script = """
    set -u
    i=\(interface); t=\(table)
    a=$(ip -4 -o addr show dev "$i" 2>/dev/null | awk '{print $4; exit}')
    echo "addr ${a:-none}"
    gw=$(ip -4 route show default dev "$i" 2>/dev/null | awk '{print $3; exit}')
    echo "gw ${gw:-none}"
    best=$(ip -4 route show default 2>/dev/null | awk '{m=0; for(k=1;k<NF;k++) if($k=="metric") m=$(k+1); d=""; for(k=1;k<NF;k++) if($k=="dev") d=$(k+1); print m, d}' | sort -n | awk '{print $2; exit}')
    echo "default-dev ${best:-none}"
    [ -n "$a" ] && [ -n "$gw" ] || { echo "rule none"; exit 0; }
    ip=${a%/*}
    ip rule show | grep -q "lookup main suppress_prefixlength 0" || ip rule add priority 165 lookup main suppress_prefixlength 0
    if ! ip rule show | grep -q "from $ip lookup $t"; then
      while ip rule del priority 166 2>/dev/null; do :; done
      ip rule add priority 166 from "$ip" lookup "$t"
    fi
    ip route replace default via "$gw" dev "$i" table "$t"
    echo "rule ok"
    """

    public static func parse(_ out: String) -> GuestAddress? {
        var kv: [String: String] = [:]
        for line in out.split(separator: "\n") {
            let p = line.split(separator: " ", maxSplits: 1)
            if p.count == 2 { kv[String(p[0])] = String(p[1]).trimmingCharacters(in: .whitespaces) }
        }
        guard let a = kv["addr"], a != "none" else { return nil }
        let parts = a.split(separator: "/")
        guard parts.count == 2, IPv4.parse(String(parts[0])) != nil, let len = Int(parts[1]), (8...30).contains(len) else { return nil }
        let gw = kv["gw"].flatMap { $0 == "none" ? nil : $0 }
        return GuestAddress(ipv4: String(parts[0]), prefix: len, gateway: gw.flatMap { IPv4.parse($0) != nil ? $0 : nil },
                            defaultViaLan: kv["default-dev"] == interface, policyRoute: kv["rule"] == "ok")
    }
}

/// What the helper last did about the home network: `state/network.json`
/// (root's, 0644: the menu-bar icon may read it; nothing secret in it).
public struct NetworkStatus: Codable, Equatable {
    public var updated: String
    /// The interfaces the redirect listens on.
    public var interfaces: [LanInterface]
    /// The address lan.json hands satellites.
    public var lanAddress: String?
    public var lanInterface: String?
    public var vm: GuestAddress?
    /// The anchor holds rules now.
    public var redirect: Bool
    public var forwardingBefore: String?
    /// `published`, `taken` (another device answers for domovoi.local),
    /// `this-mac` (the Mac's own name is domovoi), `off` (no LAN address),
    /// `failed: ...`. Filled by the running helper.
    public var localName: String?
    public var watcher: String?
    /// Why the household can't reach Domovoi right now, in words; nil when
    /// it can.
    public var problem: String?

    public init(updated: String, interfaces: [LanInterface] = [], lanAddress: String? = nil, lanInterface: String? = nil, vm: GuestAddress? = nil,
                redirect: Bool = false, forwardingBefore: String? = nil, localName: String? = nil, watcher: String? = nil, problem: String? = nil) {
        self.updated = updated
        self.interfaces = interfaces
        self.lanAddress = lanAddress
        self.lanInterface = lanInterface
        self.vm = vm
        self.redirect = redirect
        self.forwardingBefore = forwardingBefore
        self.localName = localName
        self.watcher = watcher
        self.problem = problem
    }
}

/// A blocking exclusive flock on a file: the install's network step and the
/// running helper never change pf at the same moment.
final class FileLock {
    let fd: Int32
    init(_ url: URL) throws {
        let f = open(url.path, O_RDWR | O_CREAT | O_NOFOLLOW, 0o600)
        guard f >= 0 else { throw HelperError("can't open \(url.path): \(String(cString: strerror(errno)))") }
        guard flock(f, LOCK_EX) == 0 else { close(f); throw HelperError("can't lock \(url.path)") }
        fd = f
    }
    deinit { close(fd) }
}

/// One look at the network and the VM, and what follows from it: the pf
/// redirect (PfRules), lan.json and lan_name.json in the VM (HostFiles, LanNameReport). Run by the install's
/// `lan` step, then by the running helper whenever the network changes, the
/// VM restarts, and every few minutes (mac-installer PLAN 4.2a, 4.8).
public final class NetworkSync {
    let layout: Layout
    let lima: Lima
    public let pf: PfController
    let fileOps: FileOps
    let log: (String) -> Void
    /// The Mac's `en*` interfaces, home network or not (tests answer): the
    /// fence covers them all, the redirect and lan.json the usable ones.
    public var readInterfaces: () -> [LanInterface] = { LanInterfaces.current(all: true) }
    public var fallbackAddress: () -> LanAddress? = { LanAddress.current() }
    public var now: () -> Date = { Date() }
    /// The running helper's domovoi.local state (LocalNamePublisher.state),
    /// for the network-name report; nil during the install's own sync.
    public var localNameState: () -> String? = { nil }

    /// Remembered between runs of the same process.
    public private(set) var vm: GuestAddress?
    public private(set) var lanWritten: LanAddress?
    public private(set) var lanNameWritten: LanNameReport?

    public init(layout: Layout, lima: Lima, runner: CommandRunner, fileOps: FileOps, log: @escaping (String) -> Void = { _ in }) {
        self.layout = layout
        self.lima = lima
        self.fileOps = fileOps
        self.log = log
        pf = PfController(runner: runner, fileOps: fileOps, stateFile: Self.pfStateFile(layout), log: log)
    }

    public static func pfStateFile(_ l: Layout) -> URL { l.state.appendingPathComponent("pf.json") }
    public static func statusFile(_ l: Layout) -> URL { l.state.appendingPathComponent("network.json") }
    public static func lockFile(_ l: Layout) -> URL { l.state.appendingPathComponent("network.lock") }
    /// Every file the network part writes, for the install record.
    public static func files(_ l: Layout) -> [URL] {
        [pfStateFile(l), statusFile(l), lockFile(l), l.state.appendingPathComponent("pf-domovoi.conf")]
    }

    /// Asks the guest for its lima0 address (and puts the reply route in).
    public func readVM() -> GuestAddress? {
        guard let r = try? lima.shell(Names.instance, ["sudo", "sh", "-c", GuestNet.script], timeout: 60), r.ok else { return nil }
        return GuestNet.parse(r.stdout)
    }

    /// One sync. `refreshVM`: ask the guest again (at start, after the VM
    /// job restarted, every few minutes); else the address last seen.
    @discardableResult
    public func sync(refreshVM: Bool) throws -> NetworkStatus {
        let lock = try FileLock(Self.lockFile(layout))
        defer { _ = lock }
        let seen = readInterfaces()
        let interfaces = seen.filter(LanInterfaces.usable)
        if refreshVM || vm == nil {
            if let v = readVM() { vm = v } else if vm != nil { log("the VM didn't answer; keeping its last address \(vm!.ipv4)") }
        }
        var status = NetworkStatus(updated: HostFiles.iso(now()), interfaces: interfaces, vm: vm)
        var problems: [String] = []

        // The redirect.
        do {
            let rules = try PfRules.text(interfaces: seen, vm: vm)
            let redirects = PfRules.redirects(rules)
            let old = pf.loadState()
            let loaded = rules.isEmpty ? false : anchorLoaded(redirects: redirects)
            if old?.rules != rules || (!rules.isEmpty && !loaded) || (old != nil && (try? pf.bootID()) != old?.boot) {
                let st = try pf.apply(rules: rules, tokenDir: layout.state)
                status.forwardingBefore = st.forwardingBefore
                let why = interfaces.isEmpty ? "no home network" : "no VM address"
                log(rules.isEmpty ? "pf rules off (no Wi-Fi or Ethernet address)"
                    : !redirects ? "pf redirect off (\(why)); the forwarding fence stays on \(seen.filter(PfRules.fenceable).map(\.name).joined(separator: ", "))"
                    : "pf redirect: \(interfaces.map { "\($0.name) \($0.ipv4)" }.joined(separator: ", ")) -> \(vm?.ipv4 ?? "?")")
            } else {
                status.forwardingBefore = old?.forwardingBefore
            }
            status.redirect = redirects
        } catch {
            problems.append("\(error)")
        }
        if interfaces.isEmpty { problems.append("this Mac isn't on a home network (no private IPv4 address on Wi-Fi or Ethernet)") }
        if vm == nil { problems.append("the VM's network address isn't known yet") }
        if let v = vm, !v.defaultViaLan && !v.policyRoute {
            problems.append("the VM's replies would leave through the wrong network (its default route isn't lima0)")
        }

        // lan.json: what satellites are handed.
        if let lan = LanInterfaces.handedOut(interfaces, fallback: fallbackAddress()) {
            status.lanAddress = lan.ipv4
            status.lanInterface = lan.interface
            if lan != lanWritten {
                do {
                    try writeLan(lan)
                    lanWritten = lan
                    log("lan.json: \(lan.ipv4) (\(lan.interface ?? "?"))")
                } catch {
                    problems.append("\(error)")
                }
            }
        }

        // lan_name.json: the addresses the redirect forwards to the VM,
        // which the core may sign its identity for (S2-10: without it a
        // room dialling the Mac's address took the core for an impostor).
        // Rewritten when they change (another network, a hotspot, Wi-Fi to
        // Ethernet), the first time in each helper process, and when the
        // domovoi.local state changes.
        let report = LanNameReport.make(interfaces: interfaces, localName: localNameState())
        if report != lanNameWritten {
            do {
                try writeLanName(report)
                lanNameWritten = report
                log("lan_name.json: \(report.addresses.isEmpty ? "no home-network address to vouch for" : "vouches for " + report.addresses.joined(separator: ", ")) (\(report.state))")
            } catch {
                problems.append("\(error)")
            }
        }
        status.problem = problems.isEmpty ? nil : problems.joined(separator: "; ")
        try? writeStatus(status)
        return status
    }

    /// The anchor still holds the rules (someone may have flushed it by
    /// hand): the redirect when there is one, and the fence.
    func anchorLoaded(redirects: Bool) -> Bool {
        if redirects {
            guard let r = try? pf.runnerRun(["-a", PfRules.anchor, "-s", "nat"]), r.ok, r.stdout.contains("rdr") else { return false }
        }
        guard let r = try? pf.runnerRun(["-a", PfRules.anchor, "-s", "rules"]), r.ok else { return false }
        return r.stdout.contains(PfRules.fenceTable)
    }

    public func writeLan(_ lan: LanAddress) throws {
        let data = try HostFiles.lanJSON(lan, at: now())
        let r = try lima.shell(Names.instance, ["sudo", "sh", "-c", HostFiles.guestWriteScript(path: HostFiles.lanFile)], stdin: data, timeout: 60)
        guard r.ok else { throw HelperError("can't write \(HostFiles.lanFile) in the VM: \(r.tail)") }
    }

    public func writeLanName(_ report: LanNameReport) throws {
        let data = try report.json(at: now())
        let r = try lima.shell(Names.instance, ["sudo", "sh", "-c", HostFiles.guestWriteScript(path: HostFiles.lanNameFile)], stdin: data, timeout: 60)
        guard r.ok else { throw HelperError("can't write \(HostFiles.lanNameFile) in the VM: \(r.tail)") }
    }

    public func writeStatus(_ s: NetworkStatus) throws {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        var d = try enc.encode(s)
        d.append(0x0a)
        try fileOps.writeAtomically(d, to: Self.statusFile(layout), mode: 0o644)
    }

    public static func readStatus(_ l: Layout) -> NetworkStatus? {
        guard let d = try? Data(contentsOf: statusFile(l)) else { return nil }
        return try? JSONDecoder().decode(NetworkStatus.self, from: d)
    }

    /// Uninstall: the redirect off, the token released, forwarding back.
    public static func teardown(layout: Layout, runner: CommandRunner, fileOps: FileOps, log: (String) -> Void) throws -> [String] {
        let pf = PfController(runner: runner, fileOps: fileOps, stateFile: pfStateFile(layout))
        let done = try pf.release()
        for f in files(layout) { try? FileManager.default.removeItem(at: f) }
        done.forEach(log)
        return done
    }
}

extension PfController {
    func runnerRun(_ args: [String]) throws -> CommandResult {
        try runner.run(Command(Self.pfctl, args, timeout: 20))
    }
}
