import Foundation

/// The household device token on the wire.
///
/// A port of `android/.../net/DeviceAuth.kt`. Domovoi asks a household device
/// to prove it belongs before it may change anything (`X-Device-Token`, see
/// docs/SECURITY_PRIVACY.md), and on Android one phone-wide OkHttp
/// interceptor is what makes that true of EVERY request the app makes to its
/// server rather than of the handful of call sites somebody remembered.
///
/// ``DomovoiClient`` is the iOS half of that, and this file is the vocabulary
/// it decides with: the header's name, what a storable token may contain, and
/// how to tell the server asking to be *paired* from the server asking for an
/// admin password.
public let deviceTokenHeader = "X-Device-Token"

/// Smallest household token the server will store — the pairing screen
/// refuses anything shorter so a typo never becomes a saved credential.
/// Mirrors `DEVICE_TOKEN_MIN_LEN` in domovoi/admin_auth.py.
public let deviceTokenMinLength = 12

/// Largest, likewise (`DEVICE_TOKEN_MAX_LEN`).
public let deviceTokenMaxLength = 128

/// True when `token` is a value this phone may put in a header at all:
/// printable ASCII, 0x20 through 0x7E, 12 to 128 of them.
///
/// On Android the reason is sharp: OkHttp's `Headers` does not degrade
/// gracefully, it throws `IllegalArgumentException` with the offending value
/// in the message. Foundation is the other extreme —
/// `URLRequest.setValue(_:forHTTPHeaderField:)` is documented to accept a
/// `String` and says nothing about what happens to one a header cannot
/// carry — so the iOS reason to check is not "it throws" but "we do not know
/// what it does, and the pairing screen is a paste target". The range is the
/// server's own, so this is the right check on both platforms for the same
/// reason the server has it.
///
/// Tab is legal to OkHttp and still refused here, as on Android: the server
/// trims it away at the edges and refuses it anywhere else.
public func isStorableDeviceToken(_ token: String) -> Bool {
    // Over `utf8` rather than `Character`: Kotlin counts UTF-16 code units
    // and tests `Char.code`, Swift's `count` counts grapheme clusters, and
    // the two disagree for anything outside ASCII — a cat emoji is one
    // Character, two Kotlin chars and four bytes. It cannot change a verdict,
    // because every byte of a non-ASCII scalar is ≥ 0x80 and so fails the
    // alphabet test anyway, but counting bytes keeps the two sides' *reason*
    // for refusing the same as well as their answer.
    let bytes = Array(token.utf8)
    guard (deviceTokenMinLength...deviceTokenMaxLength).contains(bytes.count) else { return false }
    return bytes.allSatisfy { $0 >= 0x20 && $0 <= 0x7E }
}

/// The token as it may appear in a header, or nil: blank is "not paired", and
/// a value a header could not carry is dropped rather than raised.
///
/// Dropping is the Android behaviour and the point of it survives the port:
/// a throw here would escape through every call in the app AND carry the
/// token in its message. A dropped token means the request goes out bare, the
/// server answers 401, and the phone is sent to the pairing screen — which is
/// the truth about a token it cannot send.
///
/// One difference from the Kotlin, in the safe direction. Kotlin's `trim()`
/// uses `Char.isWhitespace`, which covers U+001C…U+001F (the file, group,
/// record and unit separators); Swift's `.whitespacesAndNewlines` does not.
/// So `"abcdefghijkl\u{001F}"` is trimmed to a sendable token by Kotlin and
/// dropped by Swift, because the separator stays attached and fails the
/// 0x20…0x7E test. Swift sends nothing where Android sends something; it
/// cannot send something where Android sends nothing. Same reasoning as the
/// trim note in ``CleartextPolicy``.
func headerSafeToken(_ token: String?) -> String? {
    guard let token else { return nil }
    let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    guard trimmed.utf8.allSatisfy({ $0 >= 0x20 && $0 <= 0x7E }) else { return nil }
    return trimmed
}

/// Something the active server has to prove before the token goes to it
/// (security round 3, A6-03: its identity, after every network change).
///
/// ``requireAdmitted(_:)`` returns normally when a token-bearing request to
/// `base` may go out now and throws otherwise — the request never leaves.
/// Consulted only when there is a token to protect.
///
/// **I1-B implements this.** Nothing in this batch does; the client takes it
/// as an optional and a nil gate means "nothing beyond being the active
/// server", which is the shape Android's own tests use.
///
/// It is synchronous, like the Kotlin's `fun interface`, because the redirect
/// delegate that must ask it cannot `await` (see ``HouseholdLock``). The
/// Kotlin gate takes a fresh network proof on the spot when it has none,
/// which on iOS cannot happen inside this call; I1-B will have to keep the
/// proof where a synchronous reader can see it, and say what it does when
/// there is none.
public protocol TokenGate: Sendable {
    /// Throws when a token-bearing request to `base` must not go out now.
    func requireAdmitted(_ base: ServerURL) throws

    /// Whether a refusal from `base` may raise the pairing screen: a server
    /// that has not proved itself must not invite a paste of the token.
    func admitsPairingPrompt(_ base: ServerURL?) -> Bool
}

public extension TokenGate {
    /// Kotlin's default body, verbatim: `base != null`.
    func admitsPairingPrompt(_ base: ServerURL?) -> Bool { base != nil }
}

/// True when a refusal is the server asking to be **paired** rather than
/// asking for the admin password.
///
/// The device tier answers `401` (nothing, or a stale token) and `403` (a
/// credential that does not authorize this tier), and names the header in the
/// detail. An admin-tier `401 admin session required` is a different
/// conversation and must not send the phone to the pairing screen.
public func isDeviceTokenRefusal(status: Int, body: String?) -> Bool {
    guard status == 401 || status == 403 else { return false }
    let text = body ?? ""
    return text.range(of: "device token", options: .caseInsensitive) != nil
        || text.range(of: deviceTokenHeader, options: .caseInsensitive) != nil
}

/// The core's and the web's own device-tier refusals, word for word
/// (domovoi/admin_auth.py `require_device` / `require_device_read`, the
/// guest-queue gate): the only bodies that mean "this household token is not
/// accepted".
///
/// The second one carries an em dash, not a hyphen. It is copied from the
/// Kotlin, which is copied from the Python, and a hyphen here would quietly
/// stop a real refusal from ever being recognised.
let serverDeviceRefusals: Set<String> = [
    "\(deviceTokenHeader) or admin session required",
    "\(deviceTokenHeader) required — the dashboard cookie does not authorize device-tier actions",
]

/// Whether this refusal is the SERVER's own verdict on the household token.
///
/// ``isDeviceTokenRefusal(status:body:)`` matches any body naming the header,
/// which is right for raising the pairing screen. This is the stricter
/// question ``TokenHealth`` asks before it stops sending a token for good: a
/// plugin's own 401 that happens to mention the header must not make the
/// phone drop its token (Android review N-T2, 2026-10-17).
public func isServerDeviceRefusal(status: Int, body: String?) -> Bool {
    guard status == 401 || status == 403 else { return false }
    guard let detail = jsonDetail(of: body) else { return false }
    return serverDeviceRefusals.contains(detail)
}

/// The `detail` string of a JSON error body, or nil when the body is not a
/// JSON object, has no `detail`, or has one that is not a string.
///
/// Kotlin reads this with `DomovoiJson`, which is lenient; `JSONSerialization`
/// is not, so a body that is *nearly* JSON reads as no detail here and as a
/// detail there. Both then fail closed — no detail means "not the server's
/// own refusal", which means the token is NOT dropped — so the strictness
/// costs a phone one more refused request before it learns, and never drops a
/// token it should have kept.
func jsonDetail(of body: String?) -> String? {
    guard let body, let data = body.data(using: .utf8) else { return nil }
    guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
    return object["detail"] as? String
}
