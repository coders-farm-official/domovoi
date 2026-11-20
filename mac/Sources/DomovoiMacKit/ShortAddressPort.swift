import Foundation

/// The short address on a Mac (`http://domovoi.local` with no `:6369`).
///
/// The dashboard in the VM listens on port 80 too and sends a browser on to
/// 6369 (web/backend/short_address.py on main). For the household to reach
/// it, the pf redirect must carry port 80 as well. But a redirect on 80
/// would silently take port 80 away from any program on the Mac that
/// already serves it there (Apache through apachectl, a development server,
/// Docker Desktop publishing 80, a Pi-hole container): the LAN would reach
/// Domovoi instead, with no error on either side. So before every sync the
/// helper asks who listens on the Mac's own port 80 (`lsof`, as root,
/// read-only), and redirects it only when nobody does; otherwise 80 stays
/// out of the rules (the other ports unchanged) and the next sync looks
/// again. The VM is told (`HostFiles.shortAddressFile`), so the dashboard
/// says the short address is off instead of offering an address that
/// reaches the Mac's other program.
///
/// A listener bound only to the Mac's loopback (127.0.0.1, ::1) isn't in
/// the way: the redirect matches connections from the home network to the
/// Mac's own address there, which a loopback listener never sees.
public enum MacPort80: Equatable {
    /// Nothing on the Mac serves port 80 to the network: redirected.
    case free
    /// Another program serves it (its name, when lsof said one): not redirected.
    case taken(holder: String?)
    /// lsof couldn't say: not redirected, to be safe.
    case unchecked(String)

    public var redirects: Bool { self == .free }

    /// The report's `state` (HostFiles.shortAddressFile; the web's reader
    /// knows these three).
    public var state: String {
        switch self {
        case .free: return "on"
        case .taken: return "taken"
        case .unchecked: return "unchecked"
        }
    }

    public var holder: String? {
        if case .taken(let h) = self { return h }
        return nil
    }
}

public enum ShortAddressPort {
    public static let port = 80
    public static let lsof = "/usr/sbin/lsof"

    /// `lsof -nP -iTCP:80 -sTCP:LISTEN -Fcn`: every process with a TCP
    /// socket listening on local port 80, as fields (`p` pid, `c` command,
    /// `n` the address it listens on). Numeric, so it never waits on DNS.
    public static var arguments: [String] { ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-Fcn"] }

    public static func check(runner: CommandRunner) -> MacPort80 {
        guard let r = try? runner.run(Command(lsof, arguments, timeout: 20)) else {
            return .unchecked("lsof didn't run")
        }
        if r.timedOut { return .unchecked("lsof took too long") }
        return parse(status: r.status, stdout: r.stdout, stderr: r.stderr)
    }

    /// lsof's answer. It exits 1 when it lists nothing (and also on some
    /// errors, which then say so on stderr): only a quiet 1, or a 0 whose
    /// listeners are all on loopback, means free.
    public static func parse(status: Int32, stdout: String, stderr: String) -> MacPort80 {
        let quietErr = stderr.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if status == 1 && stdout.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && quietErr { return .free }
        guard status == 0 else {
            let why = stderr.split(separator: "\n").first.map(String.init) ?? "exit \(status)"
            return .unchecked("lsof: \(why)")
        }
        var command: String?
        for raw in stdout.split(separator: "\n") {
            let line = String(raw)
            guard let tag = line.first else { continue }
            let value = String(line.dropFirst())
            switch tag {
            case "p": command = nil
            case "c": command = value
            case "n":
                if !loopbackOnly(value) { return .taken(holder: command.flatMap(cleanName)) }
            default: break
            }
        }
        return .free
    }

    /// `127.0.0.1:80`, `[::1]:80` (and any 127.x): the Mac's own loopback.
    /// `*:80`, a LAN address, or anything not understood is in the way.
    static func loopbackOnly(_ name: String) -> Bool {
        guard let colon = name.lastIndex(of: ":") else { return false }
        var host = String(name[..<colon])
        if host.hasPrefix("[") && host.hasSuffix("]") { host = String(host.dropFirst().dropLast()) }
        if host == "::1" { return true }
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 4 && parts[0] == "127" && IPv4.parse(host) != nil
    }

    /// A program's name for the dashboard: printable ASCII letters, digits
    /// and `._ -`, at most 32 characters; nil when nothing is left.
    static func cleanName(_ s: String) -> String? {
        var out = String.UnicodeScalarView()
        for c in s.unicodeScalars where out.count < 32 {
            let v = c.value
            let letterOrDigit = (48...57).contains(v) || (65...90).contains(v) || (97...122).contains(v)
            if letterOrDigit || c == "." || c == "_" || c == " " || c == "-" { out.append(c) }
        }
        let t = String(out).trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : t
    }
}

/// `HostFiles.shortAddressFile`'s content: `{"schema": 1, "state": "on" |
/// "taken" | "unchecked", "holder": name or null, "written_at": ...}`,
/// read by web/backend/short_address.py (`read_host_report`), believed only
/// while root owns it and its folder, like lan_name.json beside it.
public struct ShortAddressReport: Equatable {
    public var state: String
    public var holder: String?

    public init(_ port80: MacPort80) {
        state = port80.state
        holder = port80.holder
    }

    public func json(at date: Date) throws -> Data {
        let obj: [String: Any] = [
            "schema": 1,
            "state": state,
            "holder": holder.map { $0 as Any } ?? NSNull(),
            "written_at": HostFiles.iso(date),
        ]
        let d = try HostFiles.json(obj)
        guard d.count <= 1024 else { throw HelperError("short_address.json would be too big") }
        return d
    }
}
