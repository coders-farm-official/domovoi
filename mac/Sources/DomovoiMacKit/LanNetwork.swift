import Foundation
import SystemConfiguration

/// IPv4 arithmetic for the pf rules (mac-installer PLAN 4.2a): the strict
/// dotted form `HostFiles.usable` accepts, no leading zeros.
public enum IPv4 {
    public static func parse(_ s: String) -> UInt32? {
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var v: UInt32 = 0
        for p in parts {
            guard !p.isEmpty, p.count <= 3, p.allSatisfy({ $0.isASCII && $0.isNumber }), let n = UInt32(p), n <= 255 else { return nil }
            if p.count > 1 && p.first == "0" { return nil }
            v = v << 8 | n
        }
        return v
    }

    public static func format(_ v: UInt32) -> String {
        "\(v >> 24 & 255).\(v >> 16 & 255).\(v >> 8 & 255).\(v & 255)"
    }

    /// The prefix length of a contiguous mask ("255.255.255.0" -> 24), nil
    /// for anything else.
    public static func prefixLength(mask: String) -> Int? {
        guard let m = parse(mask) else { return nil }
        let len = m.nonzeroBitCount
        let want: UInt32 = len == 0 ? 0 : UInt32.max << (32 - UInt32(len))
        return m == want ? len : nil
    }

    /// "192.168.1.0/24" for 192.168.1.20 / 255.255.255.0.
    public static func network(_ ip: String, prefix: Int) -> String? {
        guard let a = parse(ip), (0...32).contains(prefix) else { return nil }
        let m: UInt32 = prefix == 0 ? 0 : UInt32.max << (32 - UInt32(prefix))
        return "\(format(a & m))/\(prefix)"
    }

    public static func network(_ ip: String, mask: String) -> String? {
        prefixLength(mask: mask).flatMap { network(ip, prefix: $0) }
    }

    /// Whether `cidr` ("A.B.C.D/N") holds `ip`.
    public static func contains(_ cidr: String, _ ip: String) -> Bool {
        let parts = cidr.split(separator: "/")
        guard parts.count == 2, let n = parse(String(parts[0])), let len = Int(parts[1]), (0...32).contains(len), let a = parse(ip) else { return false }
        let m: UInt32 = len == 0 ? 0 : UInt32.max << (32 - UInt32(len))
        return a & m == n & m
    }

    /// A home network address (RFC 1918): the only kind the Windows
    /// installer opens its firewall rules for, and so the only kind the pf
    /// redirect listens on (Windows PLAN 4.10).
    public static func isPrivate(_ ip: String) -> Bool {
        contains("10.0.0.0/8", ip) || contains("172.16.0.0/12", ip) || contains("192.168.0.0/16", ip)
    }
}

/// One of the Mac's own connections to the home network (Wi-Fi, Ethernet,
/// a USB or Thunderbolt adapter): where the household's devices reach it.
public struct LanInterface: Equatable, Codable {
    public var name: String
    public var ipv4: String
    /// The prefix length of its network.
    public var prefix: Int
    public var wifi: Bool?
    /// The one macOS routes the internet through (State:/Network/Global/IPv4).
    public var primary: Bool

    public init(name: String, ipv4: String, prefix: Int, wifi: Bool? = nil, primary: Bool = false) {
        self.name = name
        self.ipv4 = ipv4
        self.prefix = prefix
        self.wifi = wifi
        self.primary = primary
    }

    public var network: String? { IPv4.network(ipv4, prefix: prefix) }

    /// A BSD interface name pf and the rules may carry: letters then digits.
    public static func validName(_ n: String) -> Bool {
        guard let f = n.first, f.isASCII, f.isLetter, n.count <= 15 else { return false }
        return n.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber) }
    }
}

public enum LanInterfaces {
    /// The Mac's network services that have an IPv4 address, from
    /// SystemConfiguration (`State:/Network/Service/<id>/IPv4`), the primary
    /// one first. Only `en*` interfaces (Wi-Fi, Ethernet, USB and
    /// Thunderbolt adapters): not VPN tunnels, not the Thunderbolt Bridge,
    /// not the VM network's bridge. `all`: every `en*` address, home network
    /// or not (PfRules.fenceable), for the forwarding fence; the redirect
    /// and lan.json filter that list with `usable`.
    public static func current(all: Bool = false, isWiFi: (String) -> Bool? = Preflight.realIsWiFi) -> [LanInterface] {
        guard let store = SCDynamicStoreCreate(nil, "DomovoiHelper" as CFString, nil, nil) else { return [] }
        let primary = (SCDynamicStoreCopyValue(store, "State:/Network/Global/IPv4" as CFString) as? [String: Any])?["PrimaryInterface"] as? String
        let keys = (SCDynamicStoreCopyKeyList(store, "State:/Network/Service/[^/]+/IPv4" as CFString) as? [String]) ?? []
        var out: [LanInterface] = []
        for k in keys.sorted() {
            guard let d = SCDynamicStoreCopyValue(store, k as CFString) as? [String: Any],
                  let name = d["InterfaceName"] as? String,
                  let addrs = d["Addresses"] as? [String] else { continue }
            let masks = d["SubnetMasks"] as? [String] ?? []
            for (i, a) in addrs.enumerated() {
                let prefix = i < masks.count ? IPv4.prefixLength(mask: masks[i]) : nil
                let cand = LanInterface(name: name, ipv4: a, prefix: prefix ?? 32, wifi: nil, primary: name == primary)
                guard all ? PfRules.fenceable(cand) : (prefix != nil && usable(cand)) else { continue }
                var c = cand
                c.wifi = isWiFi(name)
                if !out.contains(where: { $0.name == c.name && $0.ipv4 == c.ipv4 }) { out.append(c) }
            }
        }
        return sorted(out)
    }

    /// Whether the pf redirect may listen on it: an `en*` interface with a
    /// home-network address that HostFiles would hand satellites, on a
    /// network of a sane size (/8 to /30).
    public static func usable(_ i: LanInterface) -> Bool {
        LanInterface.validName(i.name) && i.name.hasPrefix("en") && HostFiles.usable(i.ipv4) && IPv4.isPrivate(i.ipv4)
            && (8...30).contains(i.prefix)
    }

    /// Primary first, then by name, so the rules and lan.json don't change
    /// order between two reads of the same network.
    public static func sorted(_ list: [LanInterface]) -> [LanInterface] {
        list.sorted { a, b in a.primary != b.primary ? a.primary : (a.name, a.ipv4) < (b.name, b.ipv4) }
    }

    /// The address lan.json hands satellites: the primary interface's when it
    /// is on a home network, else the first usable one, else
    /// `LanAddress.current()`'s (which is what Phase 3's install wrote).
    public static func handedOut(_ list: [LanInterface], fallback: LanAddress?) -> LanAddress? {
        if let p = list.first(where: { $0.primary }) ?? list.first { return LanAddress(ipv4: p.ipv4, interface: p.name) }
        return fallback
    }
}
