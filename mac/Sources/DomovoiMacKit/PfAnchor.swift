import Foundation

/// The household's way in (mac-installer PLAN 4.2a, option N2): phones and
/// satellites reach the **Mac's own LAN address**, and the macOS packet
/// filter redirects Domovoi's ports to the VM's address on Apple's private
/// VM network (Lima's vzNAT, `lima0` in the guest). The redirect changes only
/// the destination, so the core sees each client's real address: a phone
/// never arrives as 127.0.0.1, which the core trusts as "this computer"
/// (domovoi/local_peer.py; PLAN risk R5).
///
/// - The rules live in an anchor of their own, `com.apple/domovoi`, under
///   the `com.apple/*` anchor points the stock /etc/pf.conf evaluates;
///   /etc/pf.conf is never edited.
/// - pf is switched on with a reference token (`pfctl -E`) and the token is
///   released (`pfctl -X`) at uninstall, so pf is never switched off under
///   another program that switched it on.
/// - The redirect needs the Mac to forward packets (`net.inet.ip.forwarding`,
///   spike S3's finding): set only when it is off, the old value recorded and
///   put back at uninstall.
/// - Only from the interface's own network, only to the Mac's own address on
///   it, only TCP to the LAN ports (the same list as the Windows installer's
///   firewall rules); new connections from the LAN straight to the VM
///   network (a device routing through the Mac) are dropped.
/// - The forwarding fence (owner decision 2026-11-03): with forwarding on, a
///   device that deliberately routes through the Mac could reach any other
///   network the Mac is on (a VPN, a second network). So every packet that
///   arrives on a Wi-Fi/Ethernet interface (`en*`, any address, home network
///   or not) for an address that isn't the Mac's own there (its addresses,
///   their broadcasts, 255.255.255.255, multicast) is dropped: the Mac
///   forwards nothing from them but the redirected ports, which `rdr pass`
///   hands on before filtering. Stateful by construction: packets of flows
///   pf already keeps state for (the redirect's, the Mac's own, the VM's
///   NAT replies) are matched by their state before any rule. Inbound only,
///   on `en*` only: the Mac's own traffic is never for another address, and
///   the VM's way out (in on the vmnet bridge, out through the Mac's NAT) is
///   never matched. It stays loaded while the redirect can't be (no VM
///   address yet, no home network) whenever the Mac has an `en*` address.
/// - Nothing of it survives a restart by itself: pf's rules, its token and
///   the forwarding switch are the running system's, and the helper puts
///   them back at every boot. A failed uninstall is undone by a restart.
public enum PfRules {
    public static let anchor = "com.apple/domovoi"
    /// The anchor points the stock /etc/pf.conf declares (macOS 10.10 on).
    public static let anchorPoints = ["rdr-anchor \"com.apple/*\"", "anchor \"com.apple/*\""]

    /// TCP ports the household reaches: the short address (80, the
    /// dashboard without :6369), dashboard, core, the rooms' music streams
    /// (8050+N), the Jellyfin, RomM and Kiwix plugins. The same as
    /// windows/common/Ports.cs's firewall ports and provision.sh's
    /// VM_GUARD_LAN_PORTS (domovoi/tests/test_lan_port_parity.py).
    /// Port 80 is redirected only while nothing on the Mac serves it
    /// (ShortAddressPort): `shortAddress: false` leaves it out.
    public static let lanPorts: [ClosedRange<Int>] = [80...80, 6369...6369, 6370...6370, 7676...7676, 8050...8099, 8096...8096, 9467...9467]

    /// The ports for a rule, pf's way ("80 6369 8050:8099").
    public static func portList(shortAddress: Bool = true) -> String {
        lanPorts.filter { shortAddress || $0 != ShortAddressPort.port...ShortAddressPort.port }
            .map { $0.count == 1 ? "\($0.lowerBound)" : "\($0.lowerBound):\($0.upperBound)" }.joined(separator: " ")
    }

    /// The same ports for people ("6369, 6370, 8050-8099"): Setup's consent
    /// page says them from this list, so the words can't drift from the rules.
    public static func portWords() -> String {
        lanPorts.map { $0.count == 1 ? "\($0.lowerBound)" : "\($0.lowerBound)-\($0.upperBound)" }.joined(separator: ", ")
    }

    /// The table of destinations a packet arriving on `en*` may not have:
    /// everything but the Mac's own (pf tables match the longest prefix, so
    /// the `!` entries carve the Mac's addresses out of the two halves).
    public static let fenceTable = "domovoi_elsewhere"

    /// Whether the forwarding fence covers it: any Wi-Fi/Ethernet interface
    /// with an IPv4 address, home network or not (the redirect needs a home
    /// network; the fence doesn't).
    public static func fenceable(_ i: LanInterface) -> Bool {
        LanInterface.validName(i.name) && i.name.hasPrefix("en") && IPv4.parse(i.ipv4) != nil && (0...32).contains(i.prefix)
    }

    /// The addresses a packet arriving on `i` may be for: the Mac's own
    /// address there and its network's broadcast.
    static func ownDestinations(_ i: LanInterface) -> [String] {
        var out = [i.ipv4]
        if i.prefix <= 30, let a = IPv4.parse(i.ipv4) {
            let host: UInt32 = i.prefix == 0 ? UInt32.max : (UInt32.max >> UInt32(i.prefix))
            out.append(IPv4.format(a | host))
        }
        return out
    }

    /// Whether `text` redirects (and so needs the Mac to forward).
    public static func redirects(_ text: String) -> Bool {
        text.split(separator: "\n").contains { $0.hasPrefix("rdr ") }
    }

    /// The anchor's rules for the Mac's `en*` interfaces (all of them, as
    /// LanInterfaces.current(all: true) reads them): the redirect on the
    /// ones on a home network when the VM's address is known, and the
    /// forwarding fence on every one. Empty (the anchor is flushed) when the
    /// Mac has no `en*` address at all. `shortAddress`: port 80 redirected
    /// too; only the caller that has just checked nothing on the Mac serves
    /// it (NetworkSync, ShortAddressPort) passes true, so by default it is
    /// left alone.
    public static func text(interfaces: [LanInterface], vm: GuestAddress?, shortAddress: Bool = false) throws -> String {
        let fenced = interfaces.filter(fenceable)
        guard !fenced.isEmpty else { return "" }
        var vmNet: String?
        if let vm {
            guard IPv4.parse(vm.ipv4) != nil, let n = vm.network else { throw HelperError("the VM's address \(vm.ipv4) isn't an IPv4 address") }
            vmNet = n
        }
        var rdr: [String] = [], filter: [String] = [], fence: [String] = []
        var here: [String] = []
        var seen = Set<String>(), fencedNames = Set<String>()
        for i in fenced {
            for a in ownDestinations(i) where !here.contains(a) { here.append(a) }
            if fencedNames.insert(i.name).inserted {
                fence.append("block drop in quick on \(i.name) inet from any to <\(fenceTable)>")
            }
            guard let vm, let vmNet, LanInterfaces.usable(i), let net = i.network, seen.insert(i.name).inserted else { continue }
            guard !IPv4.contains(vmNet, i.ipv4) else {
                throw HelperError("\(i.name)'s network \(net) overlaps the VM network \(vmNet)")
            }
            rdr.append("rdr pass on \(i.name) inet proto tcp from \(net) to (\(i.name)) port { \(portList(shortAddress: shortAddress)) } -> \(vm.ipv4)")
            filter.append("block drop in quick on \(i.name) inet proto tcp from any to \(vmNet) flags S/SA")
        }
        for a in ["255.255.255.255", "224.0.0.0/4"] where !here.contains(a) { here.append(a) }
        let table = (["0.0.0.0/1", "128.0.0.0/1"] + here.map { "!" + $0 }).joined(separator: ", ")
        let body = [["table <\(fenceTable)> const { \(table) }"], rdr, filter, fence].flatMap { $0 }
        return """
        # Domovoi: the home network reaches Domovoi in its VM (mac-installer PLAN 4.2a),
        # and nothing else through this Mac: the redirected ports only, no other forwarding.
        # Written by DomovoiHelper and replaced whole at every change; pf.conf is not edited.
        \(body.joined(separator: "\n"))

        """
    }

    /// Whether `pfConf` (the stock /etc/pf.conf, or the running main
    /// ruleset from `pfctl -s nat` and `-s rules`) declares the anchor points
    /// the rules hang from.
    public static func hasAnchorPoints(_ text: String) -> Bool {
        let lines = text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
        return anchorPoints.allSatisfy { p in lines.contains { $0.hasPrefix(p) } }
    }
}

/// What the helper keeps about pf between runs, in `state/pf.json` (root's,
/// 0600): the token it holds, the forwarding value it found, and the boot
/// both belong to (a token dies with its boot).
public struct PfState: Codable, Equatable {
    public var token: String?
    public var boot: String
    /// `net.inet.ip.forwarding` before the helper set it, when it did.
    public var forwardingBefore: String?
    /// The rules last loaded (for the status and to skip a no-op reload).
    public var rules: String

    public init(token: String? = nil, boot: String, forwardingBefore: String? = nil, rules: String = "") {
        self.token = token
        self.boot = boot
        self.forwardingBefore = forwardingBefore
        self.rules = rules
    }
}

/// pfctl and sysctl, through the CommandRunner (the tests answer for them).
public final class PfController {
    public static let pfctl = "/sbin/pfctl"
    public static let sysctl = "/usr/sbin/sysctl"
    public static let forwardingKey = "net.inet.ip.forwarding"

    let runner: CommandRunner
    let fileOps: FileOps
    public let stateFile: URL
    /// /etc/pf.conf (tests give their own).
    public var pfConf: URL = URL(fileURLWithPath: "/etc/pf.conf")
    let log: (String) -> Void

    public init(runner: CommandRunner, fileOps: FileOps, stateFile: URL, log: @escaping (String) -> Void = { _ in }) {
        self.runner = runner
        self.fileOps = fileOps
        self.stateFile = stateFile
        self.log = log
    }

    // ─── the state file ──────────────────────────────────────────────

    public func loadState() -> PfState? {
        guard let d = try? Data(contentsOf: stateFile) else { return nil }
        return try? JSONDecoder().decode(PfState.self, from: d)
    }

    func save(_ s: PfState) throws {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        try fileOps.writeAtomically(try enc.encode(s), to: stateFile, mode: 0o600)
    }

    /// `kern.boottime`'s seconds: the same for one boot, new after a restart.
    public func bootID() throws -> String {
        let r = try runner.check(Command(Self.sysctl, ["-n", "kern.boottime"], timeout: 10), "sysctl kern.boottime")
        // "{ sec = 1761868281, usec = 123 } Thu Oct 30 ..."
        if let s = r.stdout.range(of: "sec = ") {
            let rest = r.stdout[s.upperBound...]
            let digits = rest.prefix { $0.isNumber }
            if !digits.isEmpty { return String(digits) }
        }
        return r.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // ─── checks ──────────────────────────────────────────────────────

    /// The anchor points are in the running main ruleset; when they aren't
    /// (pf's main ruleset never loaded since boot) and /etc/pf.conf is the
    /// stock one that declares them, it is loaded as macOS itself does.
    /// Throws when /etc/pf.conf doesn't declare them: a Mac whose pf.conf
    /// someone rewrote is left alone, and Setup says why.
    public func ensureAnchorPoints() throws {
        let nat = (try? runner.run(Command(Self.pfctl, ["-s", "nat"], timeout: 20)))?.stdout ?? ""
        let rules = (try? runner.run(Command(Self.pfctl, ["-s", "rules"], timeout: 20)))?.stdout ?? ""
        if PfRules.hasAnchorPoints(nat + "\n" + rules) { return }
        guard let conf = try? String(contentsOf: pfConf, encoding: .utf8) else {
            throw HelperError("can't read \(pfConf.path), so Domovoi can't tell where its network rules would go")
        }
        guard PfRules.hasAnchorPoints(conf) else {
            throw HelperError("\(pfConf.path) has been changed and no longer has macOS's \"com.apple/*\" anchors; Domovoi doesn't edit it, so phones and room speakers can't reach Domovoi on this Mac")
        }
        log("pf's main ruleset isn't loaded: loading the stock \(pfConf.path)")
        try runner.check(Command(Self.pfctl, ["-f", pfConf.path], timeout: 30), "pfctl -f \(pfConf.path)")
    }

    // ─── apply / release ─────────────────────────────────────────────

    /// Loads `rules` into the anchor (or flushes it when empty), takes a
    /// token for this boot if it holds none, and switches forwarding on when
    /// the rules redirect.
    /// Returns the state saved.
    @discardableResult
    public func apply(rules: String, tokenDir: URL) throws -> PfState {
        let boot = try bootID()
        var st = loadState() ?? PfState(boot: boot)
        if st.boot != boot {
            // A new boot: the old token and forwarding value are gone with it.
            st = PfState(boot: boot)
        }
        if rules.isEmpty {
            try flushAnchor()
        } else {
            try ensureAnchorPoints()
            let file = tokenDir.appendingPathComponent("pf-domovoi.conf")
            try fileOps.writeAtomically(Data(rules.utf8), to: file, mode: 0o600)
            try runner.check(Command(Self.pfctl, ["-a", PfRules.anchor, "-f", file.path], timeout: 30), "pfctl -a \(PfRules.anchor) -f")
        }
        st.rules = rules
        if !rules.isEmpty {
            if st.token == nil || !tokenAlive(st.token!) {
                st.token = try enable()
            }
            // Forwarding only for a redirect: the fence alone needs none.
            if PfRules.redirects(rules), st.forwardingBefore == nil {
                let now = try forwarding()
                if now != "1" {
                    st.forwardingBefore = now
                    try runner.check(Command(Self.sysctl, ["-w", "\(Self.forwardingKey)=1"], timeout: 10), "sysctl \(Self.forwardingKey)=1")
                    log("switched on \(Self.forwardingKey) (it was \(now)); uninstall puts it back")
                } else {
                    st.forwardingBefore = "1"
                }
            }
        }
        try save(st)
        return st
    }

    public func forwarding() throws -> String {
        try runner.check(Command(Self.sysctl, ["-n", Self.forwardingKey], timeout: 10), "sysctl \(Self.forwardingKey)")
            .stdout.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// `pfctl -E`: pf on (if it wasn't), plus one reference. Its token.
    func enable() throws -> String {
        let r = try runner.check(Command(Self.pfctl, ["-E"], timeout: 20), "pfctl -E")
        for line in (r.stdout + "\n" + r.stderr).split(separator: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("Token"), let colon = t.firstIndex(of: ":") {
                let tok = t[t.index(after: colon)...].trimmingCharacters(in: .whitespaces)
                if !tok.isEmpty, tok.allSatisfy({ $0.isNumber }) { return tok }
            }
        }
        throw HelperError("pfctl -E gave no token: \(r.tail)")
    }

    /// Whether pf still lists `token` among its references (`pfctl -s
    /// References`). When pfctl can't say, the token is taken as alive: a
    /// second reference would only be released again at uninstall.
    func tokenAlive(_ token: String) -> Bool {
        guard let r = try? runner.run(Command(Self.pfctl, ["-s", "References"], timeout: 20)), r.ok else { return true }
        return (r.stdout + r.stderr).split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\n" }).contains { $0 == token }
    }

    public func flushAnchor() throws {
        let r = try runner.run(Command(Self.pfctl, ["-a", PfRules.anchor, "-F", "all"], timeout: 30))
        // An anchor that was never loaded flushes fine too; a real failure
        // (pfctl missing, not root) is said.
        if !r.ok { throw HelperError("pfctl -a \(PfRules.anchor) -F all failed: \(r.tail)") }
    }

    /// Everything off again: the anchor flushed, this boot's token released,
    /// forwarding put back when the helper switched it on (and it is still
    /// on). Fine to run twice; a state from another boot releases nothing
    /// (that token died with its boot) and restores nothing (forwarding is
    /// off after a restart).
    public func release() throws -> [String] {
        var done: [String] = []
        try flushAnchor()
        done.append("pf anchor \(PfRules.anchor) flushed")
        guard let st = loadState() else { return done }
        let boot = try bootID()
        if st.boot == boot {
            if let t = st.token, tokenAlive(t) {
                let r = try runner.run(Command(Self.pfctl, ["-X", t], timeout: 20))
                if r.ok { done.append("pf reference released") }
                else { throw HelperError("pfctl -X failed: \(r.tail)") }
            }
            if let before = st.forwardingBefore, before != "1", (try? forwarding()) == "1" {
                try runner.check(Command(Self.sysctl, ["-w", "\(Self.forwardingKey)=\(before)"], timeout: 10), "sysctl \(Self.forwardingKey)=\(before)")
                done.append("\(Self.forwardingKey) back to \(before)")
            }
        }
        try? FileManager.default.removeItem(at: stateFile)
        return done
    }
}
