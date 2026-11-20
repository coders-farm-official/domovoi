import Darwin
import Foundation
import SystemConfiguration

/// The check of the Mac before Setup changes anything (mac-installer PLAN 5,
/// step 2). Read only: the setup pages run it as the signed-in user, the
/// helper again as root just before the work (root can name every port's
/// owner; the user only its own programs').
public struct PreflightCheck: Codable, Equatable {
    public var id: String
    public var label: String
    /// ok, fail (Setup can't go on), warn (it can, the page says why), info.
    public var status: String
    public var detail: String

    public init(_ id: String, _ label: String, _ status: String, _ detail: String) {
        self.id = id
        self.label = label
        self.status = status
        self.detail = detail
    }
}

/// One program holding some of Domovoi's ports: one pop-up each (PLAN 5,
/// "one pop-up per owner").
public struct PortConflict: Codable, Equatable {
    public var owner: String
    public var ports: [Int]
    public var program: String?
    public var pid: Int?
    /// Ollama answered on 11434: the household's own Ollama.
    public var ollamaVersion: String?
    /// ... and it is new enough to be used instead of Domovoi's own (PLAN 12.2).
    public var ollamaUsable: Bool
    /// Ollama.app (a login item: it runs only while someone is signed in).
    public var ollamaIsApp: Bool
}

public struct PreflightReport: Codable, Equatable {
    public var ok: Bool
    public var checks: [PreflightCheck]
    public var conflicts: [PortConflict]
    public var fileVault: String
    public var laptop: Bool
    public var battery: Bool
    public var onAC: Bool?
    public var memoryGB: Double
    public var tier: String
    /// none, installed, kept (a keep-data uninstall left the data).
    public var installState: String
    public var lanIPv4: String?
    public var lanInterface: String?
    public var wifi: Bool?
    public var freeDiskGB: Double
    public var macOS: String
    public var chip: String
    public var asRoot: Bool
}

/// Domovoi's ports (PLAN 5, step 2): dashboard, core, Postgres, the rooms'
/// streams, the plugin ports the pf redirect will carry, Ollama, the speech
/// service (PHASE8A-STT.md).
public enum PortTable {
    public static let all: [Int] = [6369, 6370, 6432] + Array(8050...8099) + [8096, 7676, 9467, 11434, Ports.sttDefault]
}

public final class Preflight {
    let runner: CommandRunner
    let http: HTTPClient
    let layout: Layout
    /// Whether something holds `port` (tests answer for it).
    public var portInUse: (Int) -> Bool = Preflight.realPortInUse
    /// Whether an HTTPS request to `url` gets an answer (tests answer).
    public var reachable: (String) -> Bool = Preflight.realReachable
    public var memoryBytes: UInt64 = ProcessInfo.processInfo.physicalMemory
    public var osVersion = ProcessInfo.processInfo.operatingSystemVersion
    public var freeDiskBytes: () -> Int64? = Preflight.realFreeDisk
    public var sysctl: (String) -> String? = Preflight.realSysctl
    public var lan: () -> LanAddress? = { LanAddress.current() }
    public var isWiFi: (String) -> Bool? = Preflight.realIsWiFi
    public var isRoot: Bool = geteuid() == 0
    /// /etc/pf.conf's text (world-readable), nil when it can't be read.
    public var pfConfText: () -> String? = { try? String(contentsOfFile: "/etc/pf.conf", encoding: .utf8) }

    public init(layout: Layout, runner: CommandRunner, http: HTTPClient) {
        self.layout = layout
        self.runner = runner
        self.http = http
    }

    public static let minMacOS = 26
    /// 16 GB (PLAN 4.10): a 16 GB Mac reports 16 GiB.
    public static let minMemoryBytes: UInt64 = 16 << 30
    public static let minFreeDisk: Int64 = 50_000_000_000

    public func run(pins: Pins?, ollamaMinimum: OllamaVersion?) -> PreflightReport {
        var checks: [PreflightCheck] = []
        let arm = sysctl("hw.optional.arm64") == "1"
        let chip = sysctl("machdep.cpu.brand_string") ?? (arm ? "Apple Silicon" : "Intel")
        checks.append(.init("chip", "Apple Silicon", arm ? "ok" : "fail", arm ? chip : "\(chip): Domovoi needs a Mac with Apple Silicon (M1 or later)"))

        let v = osVersion
        let ver = "\(v.majorVersion).\(v.minorVersion)\(v.patchVersion > 0 ? ".\(v.patchVersion)" : "")"
        checks.append(.init("macos", "macOS 26 or later", v.majorVersion >= Self.minMacOS ? "ok" : "fail",
                            v.majorVersion >= Self.minMacOS ? "macOS \(ver)" : "macOS \(ver): update to macOS \(Self.minMacOS) or later"))

        let gb = Double(memoryBytes) / Double(1 << 30)
        let tier = Tier.forMac(memoryBytes: memoryBytes)
        checks.append(.init("memory", "Memory", memoryBytes >= Self.minMemoryBytes ? "ok" : "fail",
                            memoryBytes >= Self.minMemoryBytes ? String(format: "%.0f GB (%@ AI models)", gb, tier == .standard ? "standard" : "smaller")
                                : String(format: "%.0f GB: Domovoi needs at least 16 GB", gb)))

        let hv = sysctl("kern.hv_support") == "1"
        checks.append(.init("virtualization", "Virtualization", hv ? "ok" : "fail",
                            hv ? "available" : "not available (is this Mac itself a virtual machine?)"))

        let state = installState()
        let free = freeDiskBytes() ?? -1
        let freeGB = Double(free) / 1e9
        if free < 0 {
            checks.append(.init("disk", "Free space", "warn", "couldn't read the startup disk's free space"))
        } else if free >= Self.minFreeDisk {
            checks.append(.init("disk", "Free space", "ok", String(format: "%.0f GB free", freeGB)))
        } else {
            // A repair needs far less: the VM and the models are there.
            checks.append(.init("disk", "Free space", state == "none" ? "fail" : "warn",
                                String(format: "%.0f GB free: Domovoi needs about 50 GB", freeGB)))
        }

        let conflicts = portConflicts(installState: state, ollamaMinimum: ollamaMinimum)
        if conflicts.isEmpty {
            checks.append(.init("ports", "Domovoi's ports", "ok", state == "installed" ? "free, or Domovoi's own" : "free"))
        } else {
            checks.append(.init("ports", "Domovoi's ports", "fail",
                                conflicts.map { "\($0.owner): \(Self.portList($0.ports))" }.joined(separator: "; ")))
        }

        let lanAddr = lan()
        var wifi: Bool?
        if let l = lanAddr {
            wifi = l.interface.flatMap(isWiFi)
            checks.append(.init("network", "Home network", "ok", "\(l.ipv4)\(wifi == true ? " (Wi-Fi)" : wifi == false ? " (wired)" : "")"))
        } else {
            checks.append(.init("network", "Home network", "fail", "this Mac has no network address: connect it to your home network"))
        }
        // Whether phones and room speakers will reach it (Phase 4's pf
        // redirect): a warning, never a stop; Domovoi still works on this Mac.
        if let l = lanAddr, !IPv4.isPrivate(l.ipv4) {
            checks.append(.init("lan-reach", "Other devices", "warn",
                                "\(l.ipv4) isn't a home-network address: phones and room speakers can reach Domovoi only on a home network (Wi-Fi or Ethernet at home)"))
        } else if let conf = pfConfText(), !PfRules.hasAnchorPoints(conf) {
            checks.append(.init("lan-reach", "Other devices", "warn",
                                "/etc/pf.conf has been changed, so Setup can't add Domovoi's network rule: Domovoi will work on this Mac, but phones and room speakers won't reach it"))
        }

        if let p = pins {
            var hosts: [String] = []
            for u in [p.ollama.url, p.ubuntuImage.url] + Self.vmHosts { if !hosts.contains(u) { hosts.append(u) } }
            let failed = hosts.filter { !reachable($0) }
            checks.append(.init("internet", "Internet now", failed.isEmpty ? "ok" : "fail",
                                failed.isEmpty ? "the download sites answer"
                                    : "can't reach " + failed.compactMap { URL(string: $0)?.host }.joined(separator: ", ") + ": Setup needs the internet to download Domovoi"))
        }

        let fv = fileVault()
        checks.append(.init("filevault", "FileVault", "info", fv == "on" ? "on" : fv == "off" ? "off" : "couldn't tell"))
        let model = sysctl("hw.model") ?? ""
        let batt = (try? runner.run(Command("/usr/bin/pmset", ["-g", "batt"], timeout: 10)))?.stdout ?? ""
        let battery = batt.contains("InternalBattery")
        let laptop = Self.isLaptop(model: model, pmsetBatt: batt)
        let onAC: Bool? = batt.isEmpty ? nil : batt.contains("'AC Power'")
        checks.append(.init("power", "Mac", "info", "\(laptop ? "laptop" : "desktop")\(battery ? (onAC == true ? ", on the charger" : ", on battery") : "")"))
        if state != "none" {
            checks.append(.init("installed", "Domovoi", "info", state == "installed" ? "already installed: Setup repairs it" : "data kept from an earlier install: Setup uses it"))
        }

        return PreflightReport(ok: !checks.contains { $0.status == "fail" }, checks: checks, conflicts: conflicts, fileVault: fv,
                               laptop: laptop, battery: battery, onAC: onAC, memoryGB: gb, tier: tier.rawValue, installState: state,
                               lanIPv4: lanAddr?.ipv4, lanInterface: lanAddr?.interface, wifi: wifi, freeDiskGB: freeGB,
                               macOS: ver, chip: chip, asRoot: isRoot)
    }

    /// Where provision.sh downloads from inside the VM (arm64 Ubuntu comes
    /// from ports.ubuntu.com): an early "no internet" beats a failure 20
    /// minutes in.
    public static let vmHosts = ["https://ports.ubuntu.com/", "https://download.docker.com/", "https://pypi.org/", "https://registry.ollama.ai/v2/"]

    public static func portList(_ ports: [Int]) -> String {
        // 8050-8099 as a range, the rest one by one.
        var out: [String] = [], run: [Int] = []
        func flush() {
            if run.count > 2 { out.append("\(run.first!)-\(run.last!)") } else { out += run.map(String.init) }
            run = []
        }
        for p in ports.sorted() {
            if let l = run.last, p == l + 1 { run.append(p) } else { flush(); run = [p] }
        }
        flush()
        return out.joined(separator: ", ")
    }

    /// none, installed or kept, from the install record.
    public func installState() -> String {
        guard let r = try? RecordStore.load(layout.installRecord) else { return "none" }
        return r.uninstalledAt == nil ? "installed" : "kept"
    }

    func fileVault() -> String {
        guard let r = try? runner.run(Command("/usr/bin/fdesetup", ["status"], timeout: 20)), r.ok else { return "unknown" }
        if r.stdout.contains("FileVault is On") { return "on" }
        if r.stdout.contains("FileVault is Off") { return "off" }
        return "unknown"
    }

    /// The ports something else holds, one entry per owning program. On a
    /// repair, the dashboard, core and Ollama ports are Domovoi's own.
    func portConflicts(installState: String, ollamaMinimum: OllamaVersion?) -> [PortConflict] {
        var ours: Set<Int> = []
        if installState == "installed" {
            ours = [6369, 6370]
            if let r = try? RecordStore.load(layout.installRecord) {
                if r.answers.ollama == "own" { ours.insert(11434) }
                if r.sttURL != nil { ours.insert(Ports.sttDefault) }
            }
        }
        var byOwner: [String: PortConflict] = [:]
        var order: [String] = []
        for port in PortTable.all where !ours.contains(port) && portInUse(port) {
            var who = owner(of: port)
            var ollamaVersion: String?
            var usable = false
            if port == 11434 {
                let found = OllamaProbe(http: http, runner: runner).look(port: port)
                if let t = found.versionText {
                    ollamaVersion = t
                    who.name = "Ollama"
                    if let v = found.version, let m = ollamaMinimum { usable = v >= m }
                }
            }
            let key = who.name + (who.pid.map { "#\($0)" } ?? "")
            if byOwner[key] == nil {
                order.append(key)
                byOwner[key] = PortConflict(owner: who.name, ports: [], program: who.program, pid: who.pid.map(Int.init),
                                            ollamaVersion: nil, ollamaUsable: false, ollamaIsApp: who.program?.contains("Ollama.app/") == true)
            }
            byOwner[key]!.ports.append(port)
            if let ov = ollamaVersion { byOwner[key]!.ollamaVersion = ov; byOwner[key]!.ollamaUsable = usable }
        }
        return order.map { byOwner[$0]! }
    }

    /// Who listens on `port`, as far as this account can see (lsof shows a
    /// standard user only its own programs). `-a`: the selectors AND.
    func owner(of port: Int) -> (name: String, program: String?, pid: Int32?) {
        guard let r = try? runner.run(Command("/usr/sbin/lsof", ["-nP", "-a", "-iTCP:\(port)", "-sTCP:LISTEN", "-Fpc"], timeout: 10)), r.ok,
              let pline = r.stdout.split(separator: "\n").first(where: { $0.hasPrefix("p") }), let pid = Int32(pline.dropFirst()) else {
            return ("another program\(isRoot ? "" : " (perhaps another account's)")", nil, nil)
        }
        let comm = r.stdout.split(separator: "\n").first(where: { $0.hasPrefix("c") }).map { String($0.dropFirst()) }
        let path = ProcessJobs.executable(of: pid)
        return (Self.friendlyName(command: comm, path: path), path, pid)
    }

    public static func friendlyName(command: String?, path: String?) -> String {
        let p = path ?? "", c = command ?? ""
        if p.contains("Ollama.app/") || c.lowercased() == "ollama" { return "Ollama" }
        if p.contains("Docker.app/") || c.hasPrefix("com.docker") || c.lowercased().hasPrefix("docker") { return "Docker Desktop" }
        if p.contains("OrbStack.app/") || c.lowercased().hasPrefix("orbstack") { return "OrbStack" }
        if let r = p.range(of: ".app/") {
            return URL(fileURLWithPath: String(p[..<r.lowerBound]) + ".app").deletingPathExtension().lastPathComponent
        }
        if !c.isEmpty { return c }
        if !p.isEmpty { return (p as NSString).lastPathComponent }
        return "another program"
    }

    // ─── the real probes ───────────────────────────────────────────────

    /// A listener on the port: a wildcard bind fails (EADDRINUSE: someone
    /// has the port on some address), or a loopback connect succeeds.
    public static func realPortInUse(_ port: Int) -> Bool {
        var inUse = false
        let s = socket(AF_INET, SOCK_STREAM, 0)
        if s >= 0 {
            var a = sockaddr_in()
            a.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            a.sin_family = sa_family_t(AF_INET)
            a.sin_port = in_port_t(UInt16(port).bigEndian)
            a.sin_addr.s_addr = INADDR_ANY
            let rc = withUnsafePointer(to: &a) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
            if rc != 0 && errno == EADDRINUSE { inUse = true }
            close(s)
        }
        if inUse { return true }
        let c = socket(AF_INET, SOCK_STREAM, 0)
        guard c >= 0 else { return false }
        defer { close(c) }
        var a = sockaddr_in()
        a.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        a.sin_family = sa_family_t(AF_INET)
        a.sin_port = in_port_t(UInt16(port).bigEndian)
        a.sin_addr.s_addr = inet_addr("127.0.0.1")
        let rc = withUnsafePointer(to: &a) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(c, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        return rc == 0
    }

    public static func realReachable(_ url: String) -> Bool {
        guard let u = URL(string: url) else { return false }
        var req = URLRequest(url: u)
        req.httpMethod = "HEAD"
        req.timeoutInterval = 12
        let c = URLSessionConfiguration.ephemeral
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        let session = URLSession(configuration: c)
        defer { session.invalidateAndCancel() }
        let done = DispatchSemaphore(value: 0)
        var ok = false
        session.dataTask(with: req) { _, resp, _ in
            // Any HTTP answer: the question is whether this Mac gets
            // through, not what the site says to a bare HEAD (the Ollama
            // registry answers its root with 503, 2026-10-30).
            ok = resp is HTTPURLResponse
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 15)
        return ok
    }

    /// Free space where Domovoi's folder goes (the Data volume). statfs, not
    /// `volumeAvailableCapacityForImportantUsage`: that one said 0 bytes over
    /// SSH with nobody's session behind it (2026-10-30), and a helper run by
    /// launchd is in the same place. It counts purgeable space too, so the
    /// larger of the two is used when it does answer.
    public static func realFreeDisk() -> Int64? {
        var s = statfs()
        var free: Int64?
        for path in ["/System/Volumes/Data", "/"] where statfs(path, &s) == 0 {
            free = Int64(s.f_bavail) * Int64(s.f_bsize)
            break
        }
        let v = try? URL(fileURLWithPath: "/").resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        if let important = v?.volumeAvailableCapacityForImportantUsage, important > (free ?? 0) { return important }
        return free
    }

    /// hw.model alone can't tell: since the M2, MacBooks report "MacNN,N"
    /// like every other Mac. A built-in battery (`pmset -g batt`) can.
    public static func isLaptop(model: String, pmsetBatt: String) -> Bool {
        model.hasPrefix("MacBook") || pmsetBatt.contains("InternalBattery")
    }

    /// This Mac, by the same rule, for what a laptop must never get (Start
    /// after a power failure). When `pmset -g batt` can't be read it counts
    /// as a laptop: not knowing never turns such a setting on.
    public static func thisMacIsLaptop(runner: CommandRunner, sysctl: (String) -> String? = Preflight.realSysctl) -> Bool {
        guard let r = try? runner.run(Command("/usr/bin/pmset", ["-g", "batt"], timeout: 10)), r.ok, !r.stdout.isEmpty else { return true }
        return isLaptop(model: sysctl("hw.model") ?? "", pmsetBatt: r.stdout)
    }

    public static func realSysctl(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buf = [UInt8](repeating: 0, count: size)
        guard sysctlbyname(name, &buf, &size, nil, 0) == 0 else { return nil }
        // Integers (4 or 8 bytes) or a C string.
        if name.hasPrefix("hw.optional") || name == "kern.hv_support" || name == "hw.memsize" {
            if size == 4 { return String(buf.withUnsafeBytes { $0.load(as: Int32.self) }) }
            if size == 8 { return String(buf.withUnsafeBytes { $0.load(as: Int64.self) }) }
        }
        return String(decoding: buf.prefix { $0 != 0 }, as: UTF8.self)
    }

    public static func realIsWiFi(_ bsdName: String) -> Bool? {
        guard let all = SCNetworkInterfaceCopyAll() as? [SCNetworkInterface] else { return nil }
        for i in all where (SCNetworkInterfaceGetBSDName(i) as String?) == bsdName {
            return (SCNetworkInterfaceGetInterfaceType(i) as String?) == (kSCNetworkInterfaceTypeIEEE80211 as String)
        }
        return nil
    }
}
