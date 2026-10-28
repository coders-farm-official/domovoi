// `String.range(of:)`, used here and in the new IPv6 check, is Foundation's,
// not the standard library's (`firstRange(of:)` is the stdlib one). The file
// had no import and so could not have compiled; `CleartextPolicy.swift` and
// `ServerAddress.swift` have the same gap for `trimmingCharacters(in:)` and
// are reported rather than touched, since nothing in this round changes them
// in a way that needs it.
import Foundation

/// The little bit of a URL the pairing rules actually need: scheme, host and
/// port.
///
/// Android gets this from OkHttp's `HttpUrl`, which every one of its net rules
/// is written against (`CleartextPolicy`, `TokenScope`, `ServerAddress`). On
/// iOS the obvious stand-in is `URL` / `URLComponents`, but what those accept
/// has changed between Foundation versions and is not what the Kotlin tests
/// pin, so this type parses the authority itself. It is deliberately strict:
/// anything it is not sure about is refused, and a refused address is simply
/// "that is not a server address" to the person.
///
/// What it is NOT: a general URL parser. It keeps no path, query or fragment,
/// because no rule in this file's neighbourhood looks at them.
public struct ServerURL: Equatable, Hashable, Sendable {
    /// `"http"` or `"https"`, lowercased. `ws` / `wss` are normalised to
    /// these by ``parseAllowingWebSocket(_:)``, which is how Android's rules
    /// see a socket address too (OkHttp's `Request.Builder` rewrites them).
    public let scheme: String

    /// The host, lowercased, without the brackets of an IPv6 literal. A
    /// trailing dot is kept exactly as typed: ``CleartextPolicy`` strips one
    /// itself, and that is the only place it matters.
    public let host: String

    /// The port as typed, or the scheme's default (80 / 443) when none was.
    public let port: Int

    public var isHttps: Bool { scheme == "https" }

    /// Internal on purpose, and normalising as well. Every rule in this
    /// neighbourhood is written against a lowercased scheme, a lowercased
    /// host and a resolved port, and only ``parse(_:)`` establishes all
    /// three: `ServerURL(scheme: "HTTPS", host: "Domovoi.LAN", port: 443)`
    /// has ``isHttps`` false and makes ``TokenScope/sameServer(_:_:)``
    /// compare hosts that can never match, so it is a security bug waiting
    /// for a call site. Hence both halves of the guard — outside this module
    /// there is exactly one way to get one of these, which is to parse a
    /// string, and inside it the initializer lowercases scheme and host
    /// itself so a future call site here cannot skip that either.
    ///
    /// The port is not defaulted here on purpose: only the scheme knows 80
    /// from 443, and ``parse(_:)`` has already applied it.
    init(scheme: String, host: String, port: Int) {
        // `lowercased()` is locale-independent in Swift, as Kotlin's
        // `lowercase()` is; idempotent, so parse's own lowercasing stands.
        self.scheme = scheme.lowercased()
        self.host = host.lowercased()
        self.port = port
    }

    /// `http://` and `https://` only, as OkHttp's `toHttpUrlOrNull` accepts,
    /// so a `ws://` address typed into the Connection panel is refused the
    /// same way Android refuses it.
    public static func parse(_ text: String) -> ServerURL? {
        guard let split = text.range(of: "://") else { return nil }
        // Swift's `lowercased()` is locale-independent, like Kotlin's
        // `lowercase()`; both are full Unicode, which only matters for hosts
        // that would fail the ASCII check below anyway.
        let scheme = String(text[text.startIndex..<split.lowerBound]).lowercased()
        guard scheme == "http" || scheme == "https" else { return nil }
        let rest = String(text[split.upperBound...])
        guard let authority = Self.authority(of: rest) else { return nil }
        return Self.parse(authority: authority, scheme: scheme)
    }

    /// As ``parse(_:)``, and `ws://` / `wss://` become `http` / `https`
    /// first. Android does the same rewrite in two places: `Request.Builder`
    /// for the sockets the app opens, and `TokenScope.baseOf` for a saved
    /// address.
    public static func parseAllowingWebSocket(_ text: String) -> ServerURL? {
        guard let split = text.range(of: "://") else { return nil }
        let scheme = String(text[text.startIndex..<split.lowerBound]).lowercased()
        let rest = String(text[split.upperBound...])
        switch scheme {
        case "ws": return parse("http://" + rest)
        case "wss": return parse("https://" + rest)
        default: return parse(text)
        }
    }

    // ---- the authority ------------------------------------------------------

    /// Everything before the path, query or fragment.
    private static func authority(of rest: String) -> String? {
        let end = rest.firstIndex { $0 == "/" || $0 == "?" || $0 == "#" } ?? rest.endIndex
        let authority = String(rest[rest.startIndex..<end])
        // No userinfo. OkHttp would accept `user:pass@host`; nothing in the
        // app ever builds one, and a token rule that compared the wrong half
        // of such an address would be a security bug, so it is refused here.
        if authority.contains("@") { return nil }
        return authority.isEmpty ? nil : authority
    }

    private static func parse(authority: String, scheme: String) -> ServerURL? {
        let defaultPort = scheme == "https" ? 443 : 80
        if authority.hasPrefix("[") {
            // A bracketed IPv6 literal: `[fe80::1%25en0]:6369`.
            guard let close = authority.firstIndex(of: "]") else { return nil }
            let inside = String(authority[authority.index(after: authority.startIndex)..<close])
            guard isIPv6Literal(inside) else { return nil }
            let tail = String(authority[authority.index(after: close)...])
            if tail.isEmpty { return ServerURL(scheme: scheme, host: inside.lowercased(), port: defaultPort) }
            guard tail.hasPrefix(":"), let port = parsePort(String(tail.dropFirst())) else { return nil }
            return ServerURL(scheme: scheme, host: inside.lowercased(), port: port)
        }
        if authority.contains("]") { return nil }
        if let colon = authority.lastIndex(of: ":") {
            let name = String(authority[authority.startIndex..<colon])
            guard let port = parsePort(String(authority[authority.index(after: colon)...])),
                  isHostName(name) else { return nil }
            return ServerURL(scheme: scheme, host: name.lowercased(), port: port)
        }
        guard isHostName(authority) else { return nil }
        return ServerURL(scheme: scheme, host: authority.lowercased(), port: defaultPort)
    }

    /// One to five ASCII digits, 1...65535. `utf8` rather than character
    /// ranges on purpose: Swift's `Character` ranges and `Int(String)` both
    /// take more than ASCII digits (`Int("１")` is nil, but `Int("+1")` is 1),
    /// and Kotlin's `it in '0'..'9'` is ASCII only.
    private static func parsePort(_ text: String) -> Int? {
        let bytes = Array(text.utf8)
        guard (1...5).contains(bytes.count) else { return nil }
        var value = 0
        for b in bytes {
            guard b >= 0x30, b <= 0x39 else { return nil }
            value = value * 10 + Int(b - 0x30)
        }
        return (1...65535).contains(value) ? value : nil
    }

    /// A host name or a dotted-quad: ASCII letters, digits, `.`, `-` and `_`
    /// (home routers hand out names with underscores). No internationalised
    /// names: the app has never seen one, and punycode would need ICU.
    private static func isHostName(_ text: String) -> Bool {
        let bytes = Array(text.utf8)
        guard !bytes.isEmpty, bytes.count <= 253 else { return false }
        for b in bytes {
            let ok = (b >= 0x61 && b <= 0x7a) || (b >= 0x41 && b <= 0x5a) || (b >= 0x30 && b <= 0x39)
                || b == 0x2e || b == 0x2d || b == 0x5f
            if !ok { return false }
        }
        return true
    }

    /// An IPv6 literal as it appeared between the brackets of an authority:
    /// an address, optionally `%zone`.
    ///
    /// This is a real structural check, not a character filter. The version
    /// it replaced took "hex digits, colons and dots in any arrangement, at
    /// least one colon", so `[fc00:::::::::::::]:6369` parsed, `host` became
    /// `fc00:::::::::::::`, and ``CleartextPolicy`` read the first hextet off
    /// it and permitted plain http to it. OkHttp refuses that string outright
    /// — so Android refuses the address — and the two apps must agree.
    private static func isIPv6Literal(_ text: String) -> Bool {
        let parts = text.split(separator: "%", maxSplits: 1, omittingEmptySubsequences: false)
        guard let address = parts.first else { return false }
        if parts.count == 2 && !isZoneID(String(parts[1])) { return false }
        return isIPv6Address(String(address))
    }

    /// Eight hextets, written out or with one `::` standing in for a run of
    /// zeros, and a dotted-quad allowed only in the last 32 bits.
    private static func isIPv6Address(_ text: String) -> Bool {
        guard let gap = text.range(of: "::") else {
            // Nothing elided: all eight hextets are written out.
            return ipv6Units(text, allowIPv4: true) == 8
        }
        let head = String(text[text.startIndex..<gap.lowerBound])
        let tail = String(text[gap.upperBound...])
        // Only one `::` may elide zeros; a second (`1::2::3`, or the `:::`
        // of the literal above, whose tail starts with a stray colon) is
        // rubbish. A stray leading colon is caught by the hextet check,
        // which refuses an empty group.
        if tail.contains("::") { return false }
        // An embedded IPv4 is the low 32 bits, so it cannot precede the `::`.
        guard let headUnits = ipv6Units(head, allowIPv4: false),
              let tailUnits = ipv6Units(tail, allowIPv4: true) else { return false }
        // `::` stands for at least one hextet of zeros, so the written ones
        // must leave room for it. `::` on its own is seven elided hextets
        // and the unspecified address, which parses but is not private, so
        // ``CleartextPolicy`` refuses it in the clear anyway.
        return headUnits + tailUnits <= 7
    }

    /// How many 16-bit units a colon-separated run of hextets writes, or nil
    /// if any group is not one. A trailing dotted-quad is two units. An empty
    /// run is zero units: it is the side of a `::` with nothing on it.
    private static func ipv6Units(_ text: String, allowIPv4: Bool) -> Int? {
        if text.isEmpty { return 0 }
        let groups = text.split(separator: ":", omittingEmptySubsequences: false)
        var units = 0
        for (index, group) in groups.enumerated() {
            if index == groups.count - 1, allowIPv4, group.contains(".") {
                // One dotted-quad parser for the whole module, so an address
                // is read the same way here and in ``CleartextPolicy``.
                guard CleartextPolicy.parseIPv4(String(group)) != nil else { return nil }
                units += 2
                continue
            }
            guard isHextet(String(group)) else { return nil }
            units += 1
        }
        return units > 8 ? nil : units
    }

    /// One to four ASCII hex digits. Over `utf8` like the rest of this file:
    /// Swift's `Character.isHexDigit` takes fullwidth and other non-ASCII
    /// digits, and Kotlin's `it in '0'..'9'` does not.
    private static func isHextet(_ text: String) -> Bool {
        let bytes = Array(text.utf8)
        guard (1...4).contains(bytes.count) else { return false }
        for b in bytes {
            let ok = (b >= 0x30 && b <= 0x39) || (b >= 0x61 && b <= 0x66) || (b >= 0x41 && b <= 0x46)
            if !ok { return false }
        }
        return true
    }

    /// A zone id, RFC 6874's unreserved set only: ASCII letters, digits, `-`,
    /// `.`, `_`, `~`. Both the typed form (`fe80::1%en0`) and the encoded one
    /// (`fe80::1%25en0`, whose zone reads as `25en0` after the split) fit.
    ///
    /// Why validate it at all: the zone travels in ``host``, and a later
    /// batch puts `host` into a `URLRequest`. The characters a URL could not
    /// have carried this far are already gone (`/ ? # ` end the authority,
    /// `@` is refused as userinfo, `]` closed the bracket) — a NUL, a space
    /// or a control character are not, and were landing in `host` unchecked.
    private static func isZoneID(_ text: String) -> Bool {
        let bytes = Array(text.utf8)
        guard (1...64).contains(bytes.count) else { return false }
        for b in bytes {
            let ok = (b >= 0x61 && b <= 0x7a) || (b >= 0x41 && b <= 0x5a) || (b >= 0x30 && b <= 0x39)
                || b == 0x2d || b == 0x2e || b == 0x5f || b == 0x7e
            if !ok { return false }
        }
        return true
    }
}
