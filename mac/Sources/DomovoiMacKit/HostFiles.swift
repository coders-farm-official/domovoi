import Foundation
import SystemConfiguration

/// The Mac's LAN address, as the household's devices reach it.
public struct LanAddress: Equatable {
    public var ipv4: String
    public var interface: String?

    public init(ipv4: String, interface: String?) {
        self.ipv4 = ipv4
        self.interface = interface
    }

    /// The primary IPv4 service's address (SystemConfiguration's
    /// `State:/Network/Global/IPv4`), or nil with no network.
    public static func current() -> LanAddress? {
        guard let store = SCDynamicStoreCreate(nil, "DomovoiHelper" as CFString, nil, nil),
              let global = SCDynamicStoreCopyValue(store, "State:/Network/Global/IPv4" as CFString) as? [String: Any],
              let ifname = global["PrimaryInterface"] as? String,
              let ifv4 = SCDynamicStoreCopyValue(store, "State:/Network/Interface/\(ifname)/IPv4" as CFString) as? [String: Any],
              let addrs = ifv4["Addresses"] as? [String] else { return nil }
        for a in addrs where HostFiles.usable(a) { return LanAddress(ipv4: a, interface: ifname) }
        return nil
    }
}

/// The files the Mac side writes into the VM, as root, under
/// `/var/lib/domovoi-mac` (docs/LINUX_HOST.md, "ARM and VMs"; the Windows
/// side's are under `/var/lib/domovoi-windows`): the folder root:root 0755,
/// each file root's and 0644, or the core ignores it. `lan_name.json`
/// (LanNameReport, below) is the third.
/// - `lan.json`: the Mac's LAN address, which `domovoi/lan_address.py`
///   prefers to the VM's own (satellites must be handed the Mac's).
/// - `side.json`: the Mac side's version and commit, for the Version card
///   (`domovoi/windows_side.py` `read_host_side`, `"kind": "mac"`), and
///   `"updates": 1` when it can update itself from a signed release.
public enum HostFiles {
    public static let folder = "/var/lib/domovoi-mac"
    public static let lanFile = folder + "/lan.json"
    public static let sideFile = folder + "/side.json"

    /// The rule `lan_address._usable` applies: an IPv4 address that is
    /// neither loopback nor unspecified.
    public static func usable(_ ip: String) -> Bool {
        let parts = ip.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return false }
        var octets: [Int] = []
        for p in parts {
            guard !p.isEmpty, p.count <= 3, p.allSatisfy({ $0.isASCII && $0.isNumber }), let n = Int(p), n <= 255 else { return false }
            // ipaddress.IPv4Address refuses leading zeros ("010").
            if p.count > 1 && p.first == "0" { return false }
            octets.append(n)
        }
        if octets[0] == 127 { return false }
        if octets == [0, 0, 0, 0] { return false }
        return true
    }

    public static func parseISO(_ s: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: s)
    }

    static func iso(_ d: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: d)
    }

    static func json(_ obj: [String: Any]) throws -> Data {
        var d = try JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
        d.append(0x0a)
        return d
    }

    public static func lanJSON(_ a: LanAddress, at date: Date) throws -> Data {
        guard usable(a.ipv4) else { throw HelperError("\(a.ipv4) is no LAN address to hand satellites") }
        var obj: [String: Any] = ["schema": 1, "ipv4": a.ipv4, "written_at": iso(date)]
        if let i = a.interface { obj["interface"] = i }
        let d = try json(obj)
        // lan_address reads at most 4096 bytes.
        guard d.count <= 4096 else { throw HelperError("lan.json would be too big") }
        return d
    }

    /// `updates`: this Mac side has the updater (MacUpdate.swift, the system
    /// install): the core offers the Mac step only then (windows_side.py
    /// "updates": 1; without it the card says to open the package by hand).
    /// `cardHelper`: this Mac's menu-bar icon has "Prepare an SD card..."
    /// (CardWindow.swift): the dashboard and the zip's README point there
    /// only then (host_kind.mac_card_helper reads "card_helper": 1; without
    /// it they keep the Finder steps).
    public static func sideJSON(version: String, commit: String?, at date: Date, updates: Bool = false, cardHelper: Bool = false) throws -> Data {
        var obj: [String: Any] = ["schema": 1, "kind": "mac", "version": version, "written_at": iso(date)]
        if updates { obj["updates"] = 1 }
        if cardHelper { obj["card_helper"] = 1 }
        // windows_side.read_side reads "build_commit", a full SHA-1, and
        // ignores anything else there.
        if let c = commit, c.count == 40, c.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) { obj["build_commit"] = c }
        return try json(obj)
    }

    /// The network-name report the core reads from the machine hosting it
    /// (Train 1 S2-10): `domovoi/lan_name/service.py` REPORT_FILES on main,
    /// where the Mac's entry is named `("mac", "/var/lib/domovoi-mac/lan_name.json")`.
    /// Its `addresses` are what `server_identity.VOUCHED_ADDRESS_SOURCES`
    /// vouches for: the core signs its identity proof for them, which a
    /// room dialling the Mac's own address needs (the core lives in the VM,
    /// reached through the pf redirect). Believed only while root owns the
    /// file and its folder and neither is writable by anyone else
    /// (`guestWriteScript`'s modes). In the same folder as lan.json and
    /// side.json.
    public static let lanNameFile = folder + "/lan_name.json"

    /// The guest command that puts `data` (on its stdin) at `path` as root,
    /// atomically, with the folder and modes the core insists on.
    public static func guestWriteScript(path: String) -> String {
        let dir = (path as NSString).deletingLastPathComponent
        let tmp = "\(dir)/.\((path as NSString).lastPathComponent).tmp"
        return "set -e; install -d -o root -g root -m 0755 '\(dir)'; umask 022; cat > '\(tmp)'; chown root:root '\(tmp)'; chmod 0644 '\(tmp)'; mv -f '\(tmp)' '\(path)'"
    }
}

/// `HostFiles.lanNameFile`'s content: main's `lan_name.service.parse_report`
/// schema 1, every field as it checks them, or it believes none of it:
/// `requested` a name label, `state` one of its STATES, `addresses` at most
/// four RFC 1918 IPv4 addresses (`responder.lan_address_ok`), no ports.
///
/// The addresses are the ones the pf redirect forwards to the VM (the
/// usable home-network interfaces, primary first). It keeps the port, so a
/// bare address vouches for the core at its own port 6370 and nothing
/// else: the core signs its identity at no other port, and the other
/// forwarded ports (the dashboard, music streams) aren't the core's.
///
/// The name fields say what the helper does about `domovoi.local`
/// (LocalNamePublisher.state): main reads them only for a host it runs
/// in "host" mode, which a Mac isn't yet, so they never decide anything;
/// they are filled truthfully all the same.
public struct LanNameReport: Equatable {
    public static let requested = "domovoi"
    /// What `parse_report` takes at most.
    public static let maxAddresses = 4

    public var addresses: [String]
    public var state: String
    public var claimed: String?
    public var taken: String?
    public var error: String?

    /// An address the core believes here: an IPv4 address `HostFiles`
    /// would hand out, in a home network's private range.
    public static func vouchable(_ ip: String) -> Bool { HostFiles.usable(ip) && IPv4.isPrivate(ip) }

    /// From the redirect's interfaces (LanInterfaces.usable, sorted) and
    /// the helper's domovoi.local state (nil: not known yet, as during the
    /// install's own sync).
    public static func make(interfaces: [LanInterface], localName: String?) -> LanNameReport {
        var list: [String] = []
        for i in interfaces where vouchable(i.ipv4) && !list.contains(i.ipv4) && list.count < maxAddresses { list.append(i.ipv4) }
        var r = LanNameReport(addresses: list, state: list.isEmpty ? "no_lan" : "checking", claimed: nil, taken: nil, error: nil)
        guard !list.isEmpty, let n = localName else { return r }
        switch n {
        case "published", "this-mac":
            r.state = "announced"
            r.claimed = requested
        case "taken":
            r.state = "taken"
            r.taken = requested
        case "off":
            r.state = "off"
        case "probing":
            r.state = "checking"
        default:
            if n.hasPrefix("failed") {
                r.state = "failed"
                let why = n.hasPrefix("failed: ") ? String(n.dropFirst(8)) : n
                // service._short: 1-200 characters, no control characters.
                var clean = String.UnicodeScalarView()
                for s in why.unicodeScalars where s.value >= 32 && s.value != 127 && clean.count < 200 { clean.append(s) }
                r.error = clean.isEmpty ? nil : String(clean)
            }
        }
        return r
    }

    public func json(at date: Date) throws -> Data {
        let obj: [String: Any] = [
            "schema": 1,
            "request_id": NSNull(),
            "requested": Self.requested,
            "claimed": claimed.map { $0 as Any } ?? NSNull(),
            "state": state,
            "taken": taken.map { $0 as Any } ?? NSNull(),
            "addresses": addresses,
            "error": error.map { $0 as Any } ?? NSNull(),
            "written_at": HostFiles.iso(date),
        ]
        let d = try HostFiles.json(obj)
        // service._REPORT_MAX_BYTES.
        guard d.count <= 4096 else { throw HelperError("lan_name.json would be too big") }
        return d
    }
}
