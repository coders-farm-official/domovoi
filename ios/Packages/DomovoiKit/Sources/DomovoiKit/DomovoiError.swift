import Foundation

/// What the server said when it refused.
///
/// The fields of Android's `ApiException` (`android/.../net/ApiClient.kt`),
/// which is the app's whole error contract with the web backend and the same
/// one `web/static/data.js` keeps.
public struct ApiFailure: Equatable, Sendable {
    public let status: Int

    /// Android's `"{status} {reason}: {body first 200 chars}"`.
    ///
    /// **The one place iOS cannot match the Kotlin.** OkHttp gives
    /// `Response.message`, the reason phrase the server actually sent ("Bad
    /// Gateway"), and Android puts it in this string, in its tests and in
    /// every toast. Foundation's `HTTPURLResponse` has no API for a reason
    /// phrase at all: `statusCode`, `allHeaderFields` and
    /// `localizedString(forStatusCode:)`, the last of which returns a
    /// *localized generic* phrase from the platform rather than anything the
    /// server said — so on a Spanish phone it would not even be English. So
    /// the message here is `"{status}: {body first 200 chars}"`, and a person
    /// comparing an iPhone's toast with an Android phone's will see one word
    /// missing from the iPhone's. Everything the message is *used* for — the
    /// status, the server's own detail — is still in it.
    public let message: String

    /// The server asked this phone to pair, not to log in
    /// (``isDeviceTokenRefusal(status:body:)``) — the shell routes to the
    /// pairing screen.
    public let deviceTokenRequired: Bool

    /// The response body, its first 4,096 characters (the message keeps 200);
    /// empty when there was none.
    public let body: String

    /// The server's `X-Domovoi-Refusal` header, when it sent one: why it
    /// refused in a word ("ai-off": the household's AI features are off;
    /// "internet-off"). Nil for any other error.
    public let refusal: String?

    public init(status: Int, message: String, deviceTokenRequired: Bool = false, body: String = "", refusal: String? = nil) {
        self.status = status
        self.message = message
        self.deviceTokenRequired = deviceTokenRequired
        self.body = body
        self.refusal = refusal
    }

    /// The part of the house the server says failed, from `failed` in a JSON
    /// error body: "music_player" (a room's music player, on the domovoi
    /// server) or "satellite" (2026-10-01, the core's music controls and
    /// casts). Nil when the server named none — an older core, or any other
    /// error. Read from ``body``, or from the message's copy of it.
    public var failedPart: String? {
        let text = body.isEmpty ? String(message.drop(while: { $0 != ":" }).dropFirst(2)) : body
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return object["failed"] as? String
    }
}

/// The header a Domovoi server names a deliberate refusal in (the core's
/// ai_features.py / egress.py `REFUSAL_HEADER`): see ``ApiFailure/refusal``.
public let refusalHeader = "X-Domovoi-Refusal"

/// Everything ``DomovoiClient`` can fail with, in the shapes a screen has to
/// tell a person apart.
///
/// Android has one `IOException` tree and sorts it out at the call site
/// (`failureText`, and `is ApiException` checks in the screens). Swift's
/// typed throws make the sorting the type's job instead, which is the one
/// place this port is deliberately more structured than its original: a
/// screen that must show a countdown should not have to parse it out of a
/// message.
public enum DomovoiError: Error, Equatable, Sendable {
    /// No server is saved. Android's `IOException("no server configured")`.
    case noServer

    /// The rule refused the request before anything was connected: a
    /// plain-http address ``CleartextPolicy`` does not allow, a redirect this
    /// app will not follow, or an address this app cannot parse. Android's
    /// `UnknownServiceException`.
    case notPermitted(String)

    /// The identity gate held a token-bearing request back: this server has
    /// not proved itself on this network, or it proved itself a different
    /// server (the `Mismatch` verdict).
    ///
    /// Android's `ServerIdentityException`, which is a plain `IOException` and
    /// NOT an `UnknownServiceException` — so it is a case of its own here, and
    /// not a shape of ``notPermitted(_:)``. Two things turn on the difference:
    /// ``DomovoiClient/answers()`` counts a cleartext refusal as an answer and
    /// a held request as no answer, exactly as Android's two catch clauses do,
    /// and ``failureText(_:_:)`` says which of the two happened.
    case identityHeld(String)

    /// Nothing answered: no route to the host, a refused connection, a
    /// timeout. Android's plain `IOException`.
    case unreachable(String)

    /// 401 or 403. ``ApiFailure/deviceTokenRequired`` says whether it was
    /// the server asking to be paired.
    case refused(ApiFailure)

    /// 429, with how long the server said to wait.
    case rateLimited(ApiFailure, waitMs: Int64)

    /// 5xx.
    case serverTrouble(ApiFailure)

    /// Any other non-2xx.
    case httpError(ApiFailure)

    /// The address answered, with a 2xx, and what came back is not what a
    /// Domovoi server sends — a router's login page, a captive portal, some
    /// other service on the port. Android gets a `SerializationException`
    /// here and shows it raw.
    case notDomovoi(String)

    /// The request was NOT sent: the server asked this phone to wait before
    /// it looks at a household token from it again (``TokenHealth``).
    case tokenThrottled(waitMs: Int64)

    /// A wiring mistake, not a condition of the network: the identity probe
    /// for a server needed the very gate it belongs to. The gate needs a
    /// client and the client needs the gate, so the circular wiring is easy
    /// to build by accident; see ``IdentityGate/httpProbe(_:)``. Without this
    /// the probe would wait on the task it is running inside, which is a
    /// hang rather than a failure — the one outcome a person cannot report
    /// usefully.
    case probeWiringCycle(String)

    /// The failure a status brought, if this error carries one.
    public var failure: ApiFailure? {
        switch self {
        case .refused(let f), .rateLimited(let f, _), .serverTrouble(let f), .httpError(let f): return f
        case .noServer, .notPermitted, .identityHeld, .unreachable, .notDomovoi, .tokenThrottled,
             .probeWiringCycle: return nil
        }
    }

    /// Android's `TokenThrottledException` message, word for word.
    public var message: String {
        switch self {
        case .noServer: return "no server configured"
        case .notPermitted(let reason), .identityHeld(let reason),
             .unreachable(let reason), .notDomovoi(let reason),
             .probeWiringCycle(let reason): return reason
        case .refused(let f), .rateLimited(let f, _), .serverTrouble(let f), .httpError(let f): return f.message
        case .tokenThrottled(let waitMs):
            return "Domovoi asked this phone to wait \((waitMs + 999) / 1000) s before it tries the household token again"
        }
    }
}

/// Toast text for a failed mutation (CONVENTIONS rule 4).
///
/// A non-2xx response is the backend's fault, not the network's, so it
/// reports the server's own "{status}: {body}" message; only a real transport
/// failure is blamed on the connection. A port of `failureText` in
/// `android/.../net/ApiClient.kt`, branch for branch.
public func failureText(_ action: String, _ error: DomovoiError) -> String {
    let detail = error.message.trimmingCharacters(in: .whitespacesAndNewlines)
    switch error {
    case .refused(let f), .rateLimited(let f, _), .serverTrouble(let f), .httpError(let f):
        return "\(action) failed: \(detail.isEmpty ? "HTTP \(f.status)" : detail)"
    case .notPermitted:
        // A cleartext refusal (``CleartextPolicy`` or ATS) is not an outage:
        // say why, so the address can be corrected.
        return "\(action) failed: \(detail.isEmpty ? "not permitted" : detail)"
    case .unreachable, .noServer:
        return "\(action) failed (offline?)"
    case .tokenThrottled, .identityHeld:
        // A request the app chose not to send is not the network's fault.
        // Both of these used to read "(offline?)" on both phones, because
        // `TokenThrottledException` and `ServerIdentityException` are
        // `IOException`s and `failureText` tests `IOException` after the two
        // named types — so the toast blamed the connection for a wait the
        // server asked for, and for a server that did not prove itself.
        // Android now reports each one's own message (2026-10-29); these say
        // the same, which is what keeps the two phones' toasts in step.
        return "\(action) failed: \(detail.isEmpty ? "not permitted" : detail)"
    case .notDomovoi:
        return "\(action) failed: \(detail)"
    case .probeWiringCycle:
        // Ours, not theirs. Say the server could not be checked rather than
        // showing a person the wiring; the detail is in the log.
        return "\(action) failed: this server could not be checked"
    }
}
