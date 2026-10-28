/// Where the household device token may go: to the active server, and
/// nowhere else.
///
/// A port of `android/.../net/TokenScope.kt`. Until 2026-10-08 Android put
/// the token on EVERY request its shared OkHttpClient made, whatever the
/// host — so anything that could hand the client an absolute URL (a radio
/// stream's address, a LAN sweep's 254 probes, a second app driving the
/// exported media session) collected the token. The rule is now the
/// dashboard's: the token goes only to the origin that issued it, compared on
/// scheme, host and port.
///
/// The one documented exception is the drop-in call socket, which the web
/// backend points at the core's own port on the SAME host
/// (`/api/satellites/{room}/dropin/phone-info`). Android marks the upgrades
/// it builds itself with an OkHttp request tag, so its interceptor can tell
/// them from a URL some library was handed. On iOS that marking belongs to
/// `DomovoiClient` (a later batch), which will carry the flag on the task it
/// creates rather than guess from the URL. The rule itself takes the flag as
/// an argument and stays pure.
public enum TokenScope {

    /// The active server's address, or nil when there is none (or it does not
    /// parse). `ws://` / `wss://` are read as their http forms.
    public static func baseOf(_ serverURL: String?) -> ServerURL? {
        guard let serverURL else { return nil }
        var raw = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        while raw.hasSuffix("/") { raw = String(raw.dropLast()) }
        if raw.isEmpty { return nil }
        return ServerURL.parseAllowingWebSocket(raw)
    }

    /// `url` is the active server itself: same scheme, host and port.
    /// ``ServerURL`` has already lowercased both hosts and filled in the
    /// scheme's default port, which is how Android's `equals(ignoreCase)` and
    /// OkHttp's implied port show up here.
    public static func sameServer(_ base: ServerURL?, _ url: ServerURL) -> Bool {
        guard let base else { return false }
        return base.scheme == url.scheme && base.host == url.host && base.port == url.port
    }

    /// `url` is on the active server's host, in the same scheme family, on
    /// any port — what the drop-in socket to the core's port needs.
    public static func sameHost(_ base: ServerURL?, _ url: ServerURL) -> Bool {
        guard let base else { return false }
        return base.scheme == url.scheme && base.host == url.host
    }

    /// Whether a request to `url` may carry the active server's token: the
    /// active server itself, or — for an upgrade the app built — another port
    /// on its host.
    public static func admits(_ base: ServerURL?, _ url: ServerURL, wsUpgrade: Bool) -> Bool {
        sameServer(base, url) || (wsUpgrade && sameHost(base, url))
    }
}
