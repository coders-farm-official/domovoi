/// Where the app may talk plain http / ws: the home network only.
///
/// A line-for-line port of `android/.../net/CleartextPolicy.kt`. On Android
/// `res/xml/network_security_config.xml` is the platform half of the rule and
/// this object is the other half, because the platform can match a host or a
/// DNS suffix but cannot say "any RFC 1918 address" — and the usual server
/// address is one. iOS is the same story with different names: the platform
/// half is App Transport Security in `ios/project.yml`
/// (`NSAllowsLocalNetworking`, which since iOS 17 is what lets the app reach
/// an IP address at all, plus `NSExceptionDomains` for the suffixes ATS does
/// not know), and ATS is *looser* than we want — `NSAllowsLocalNetworking`
/// admits every IP address, public ones included. So this policy still runs
/// on top of it, as on Android, and a public IP over plain http is refused.
///
/// Permitted in the clear:
///  - IPv4 private ranges 10/8, 172.16/12, 192.168/16 (RFC 1918)
///  - IPv4 loopback 127/8 and link-local 169.254/16
///  - IPv4 100.64/10 (RFC 6598 shared space, used by Tailscale-style overlays)
///  - IPv6 loopback ::1, unique-local fc00::/7, link-local fe80::/10
///  - the name "localhost" and any name under ``privateNameSuffixes``
/// Everything else must use https.
///
/// The enforcement point — Android's OkHttp interceptor, which throws
/// `UnknownServiceException` before DNS or a socket is touched — lands with
/// `DomovoiClient` in a later batch: one `URLSession` delegate that refuses
/// the task instead of starting it. This file is only the rule, so it stays
/// pure and unit-tested as written.
public enum CleartextPolicy {

    /// Name suffixes that only resolve on a home network (RFC 6762 .local,
    /// RFC 8375 .home.arpa, ICANN-reserved .internal, and the never-delegated
    /// .lan / .home / .localhost that home routers hand out).
    public static let privateNameSuffixes: [String] =
        [".local", ".home.arpa", ".internal", ".lan", ".home", ".localhost"]

    /// Whether plain http / ws to `host` (a name or an IP literal) is allowed.
    public static func cleartextPermitted(_ host: String?) -> Bool {
        guard let host else { return false }
        // Kotlin's `trim()` drops Unicode whitespace at both ends; Swift's
        // `.whitespacesAndNewlines` set is the same intent.
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        while h.hasSuffix(".") { h = String(h.dropLast()) }   // Kotlin trimEnd('.')
        h = h.lowercased()
        if h.isEmpty { return false }
        if h == "localhost" { return true }
        // `hasSuffix` on Swift String compares by canonical equivalence, not
        // by code units. Every suffix here is ASCII, where the two agree.
        if privateNameSuffixes.contains(where: { h.hasSuffix($0) }) { return true }
        if let octets = parseIPv4(h) { return isPrivateIPv4(octets) }
        if h.contains(":") { return isPrivateIPv6(h) }
        return false
    }

    /// https always; http only when ``cleartextPermitted(_:)`` says so.
    /// ws / wss addresses arrive here as http / https — ``ServerURL`` rewrites
    /// them, as OkHttp's `Request.Builder` does on Android.
    public static func permits(_ url: ServerURL) -> Bool {
        url.isHttps || cleartextPermitted(url.host)
    }

    /// The one-line reason shown when a plain-http server address is refused.
    public static func refusalMessage(_ host: String) -> String {
        "plain http to \(host) is only allowed on the home network; use https://"
    }

    // ---- IP literal helpers (pure, testable without a Simulator) -----------

    /// Four dotted decimal octets, each 0...255, or nil. Over `utf8` because
    /// Kotlin's `it in '0'..'9'` is ASCII only, while Swift's `Int(String)`
    /// and character ranges are not.
    static func parseIPv4(_ text: String) -> [Int]? {
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var octets: [Int] = []
        octets.reserveCapacity(4)
        for part in parts {
            let bytes = Array(part.utf8)
            guard (1...3).contains(bytes.count) else { return nil }
            var value = 0
            for b in bytes {
                guard b >= 0x30, b <= 0x39 else { return nil }
                value = value * 10 + Int(b - 0x30)
            }
            guard value <= 255 else { return nil }
            octets.append(value)
        }
        return octets
    }

    static func isPrivateIPv4(_ o: [Int]) -> Bool {
        if o[0] == 10 { return true }                               // 10.0.0.0/8
        if o[0] == 172 && (16...31).contains(o[1]) { return true }  // 172.16.0.0/12
        if o[0] == 192 && o[1] == 168 { return true }               // 192.168.0.0/16
        if o[0] == 127 { return true }                              // loopback
        if o[0] == 169 && o[1] == 254 { return true }               // link-local
        if o[0] == 100 && (64...127).contains(o[1]) { return true } // 100.64.0.0/10
        return false
    }

    /// Textual IPv6 (brackets and a zone id are tolerated): loopback,
    /// unique-local fc00::/7 or link-local fe80::/10.
    static func isPrivateIPv6(_ text: String) -> Bool {
        var h = text
        if h.hasPrefix("[") { h = String(h.dropFirst()) }
        h = String(h.prefix(while: { $0 != "]" }))
        h = String(h.prefix(while: { $0 != "%" }))
        h = h.lowercased()
        if h == "::1" || h == "0:0:0:0:0:0:0:1" { return true }
        let first = String(h.prefix(while: { $0 != ":" }))
        let bytes = Array(first.utf8)
        guard (1...4).contains(bytes.count) else { return false }
        var hextet = 0
        for b in bytes {
            let digit: Int
            switch b {
            case 0x30...0x39: digit = Int(b - 0x30)
            case 0x61...0x66: digit = Int(b - 0x61) + 10
            default: return false
            }
            hextet = hextet * 16 + digit
        }
        return (hextet & 0xfe00) == 0xfc00      // fc00::/7
            || (hextet & 0xffc0) == 0xfe80      // fe80::/10
    }
}
