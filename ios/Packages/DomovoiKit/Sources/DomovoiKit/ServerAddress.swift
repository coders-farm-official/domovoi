/// A server address as a person typed it, made into one the app can save:
/// `http://` when no scheme was given, `:6369` when no port, and the
/// cleartext rule applied up front — a plain-http address outside the home
/// network is refused with the reason, before anything is probed or stored.
///
/// One rule for both places an address can be typed: the picker's "add
/// manually" and Settings → Connection. A port of
/// `android/.../net/ServerAddress.kt`; on Android the Connection panel saved
/// the raw string with neither the normalisation nor the check until
/// 2026-10-08 (security round 3, A6-05 / P2-at-01).
public enum ServerAddress {

    /// The default port, the same constant Android's `Discovery` holds.
    public static let defaultPort = 6369

    public enum Outcome: Equatable, Sendable {
        /// The address to save, normalised.
        case ok(url: String)
        /// Why it was not accepted, in the user's words.
        case refused(message: String)
    }

    /// Nil for a blank input (nothing to do).
    public static func fromTyped(_ input: String, defaultPort: Int = ServerAddress.defaultPort) -> Outcome? {
        var url = input.trimmingCharacters(in: .whitespacesAndNewlines)
        while url.hasSuffix("/") { url = String(url.dropLast()) }   // Kotlin trimEnd('/')
        if url.isEmpty { return nil }
        if !url.contains("://") { url = "http://" + url }
        if !endsInPort(afterScheme(url)) { url = "\(url):\(defaultPort)" }
        guard let parsed = ServerURL.parse(url) else { return .refused(message: "that is not a server address") }
        guard CleartextPolicy.permits(parsed) else {
            return .refused(message: CleartextPolicy.refusalMessage(parsed.host))
        }
        return .ok(url: url)
    }

    /// Kotlin's `url.substringAfter("://")`.
    private static func afterScheme(_ url: String) -> String {
        guard let split = url.range(of: "://") else { return url }
        return String(url[split.upperBound...])
    }

    /// Kotlin's `Regex(":\\d+$").containsMatchIn(...)`, written out so no
    /// regex engine is involved: `NSRegularExpression` is ICU and its `\d`
    /// matches any Unicode decimal digit, which the port parser would then
    /// refuse — a difference that would turn "add a default port" into "that
    /// is not a server address".
    ///
    /// The quirk of running the check over everything after the scheme, path
    /// included, is Android's too, and so is what it does: an address with a
    /// path gets `:6369` glued onto the **path**, and the result is then
    /// saved, because the authority in front of it still parses.
    ///
    /// `"192.168.1.30/api"` is saved as `http://192.168.1.30/api:6369`, and
    /// `"http://192.168.1.30:6390/api/health"` as
    /// `http://192.168.1.30:6390/api/health:6369`. ``ServerURL`` does not
    /// refuse either: it reads the authority and stops at the first `/`, so
    /// it sees `192.168.1.30` (port 80) and `192.168.1.30:6390`, both on the
    /// home network, and the cleartext check passes. The nonsense is in the
    /// part of the string nothing reads — until the day something does.
    ///
    /// This is **not** fixed here. Android's `ServerAddress.kt` does exactly
    /// the same thing, the saved string is shared vocabulary between the two
    /// apps, and a one-sided fix would be the divergence. It is reported to
    /// the owner as a both-sides fix; `ServerAddressTests` pins the current
    /// behaviour, so the day it is fixed that test fails and says so.
    ///
    /// In practice nothing in the app types a path: both entry points offer
    /// a host and an optional port, and a trailing `/` is already gone.
    private static func endsInPort(_ text: String) -> Bool {
        var digits = 0
        var index = text.endIndex
        while index > text.startIndex {
            let before = text.index(before: index)
            let character = text[before]
            if character.isASCII && character.isNumber {
                digits += 1
                index = before
                continue
            }
            return character == ":" && digits > 0
        }
        return false
    }
}
