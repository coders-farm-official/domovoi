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

    public init(scheme: String, host: String, port: Int) {
        self.scheme = scheme
        self.host = host
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

    /// Enough of a check to tell an IPv6 literal from rubbish: hex digits,
    /// colons and at most one `%zone`, with at least one colon. The exact
    /// shape is the system's business when it resolves it.
    private static func isIPv6Literal(_ text: String) -> Bool {
        let parts = text.split(separator: "%", maxSplits: 1, omittingEmptySubsequences: false)
        guard let address = parts.first, !address.isEmpty, address.contains(":") else { return false }
        if parts.count == 2 && parts[1].isEmpty { return false }
        for b in address.utf8 {
            let ok = (b >= 0x30 && b <= 0x39) || (b >= 0x61 && b <= 0x66) || (b >= 0x41 && b <= 0x46)
                || b == 0x3a || b == 0x2e
            if !ok { return false }
        }
        return true
    }
}
