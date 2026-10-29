import Foundation

/// Redirects, decided by the app rather than by the platform.
///
/// A port of `android/.../net/RedirectPolicy.kt`, whose doc comment explains
/// why it exists: OkHttp's own follower runs AFTER the application
/// interceptors and builds the follow-up from the request they emitted —
/// which already carries `X-Device-Token` — stripping only `Authorization`
/// when the host changes. So a `3xx` from the active server would have
/// delivered the household token to whatever `Location` named, and the
/// cleartext rule, itself an interceptor, would never have seen that hop
/// (security round 3, A6-01: no route redirects off-host today; any plugin
/// route, reverse proxy or future endpoint that did would have).
///
/// iOS has the same problem in a different shape. `URLSession` follows
/// redirects by itself, and what it hands the delegate is a **proposed**
/// `URLRequest` that has already copied the headers across — our token
/// among them. So the delegate does not refine that proposal; it throws it
/// away and builds the follow-up from the original request, exactly as the
/// Kotlin does, with the token header gone and the scope rule asked afresh
/// for the hop's own URL (``DomovoiClient``).
///
/// This file is the Kotlin's `followUp`: pure, so the rules are tested as
/// written rather than inferred from a redirect chain. The three rules
/// stricter than the platform's are its, too — a WebSocket upgrade is never
/// redirected, an https→http downgrade is never followed, and the token
/// header never rides along.
public enum RedirectPolicy {

    /// OkHttp's own ceiling, kept so the two apps give up at the same place.
    /// `URLSession` has a ceiling of its own that Apple does not document a
    /// number for, so this is the one that is checked.
    public static let maxHops = 20

    /// The codes Android follows. Note what is not here: `304`, which is not
    /// a redirect, and `305`/`306`, which are dead.
    static let redirectCodes: Set<Int> = [300, 301, 302, 303, 307, 308]

    /// The request that follows a `status` / `Location` answer to `request`,
    /// or nil when it is not to be followed: not a redirect, no usable
    /// `Location`, a WebSocket upgrade, an https→http downgrade, or a
    /// `307`/`308` of anything but GET/HEAD (OkHttp's rule too).
    ///
    /// Taking the status and the header rather than a response object is the
    /// one shape change from the Kotlin, and it is what makes every rule
    /// testable without building a fake `HTTPURLResponse` — which Foundation
    /// will construct with any status and no body, but not with a reason
    /// phrase, so a test built on one would be testing less than it looks.
    public static func followUp(
        _ request: URLRequest,
        status: Int,
        location: String?,
        isWebSocketUpgrade: Bool
    ) -> URLRequest? {
        guard redirectCodes.contains(status) else { return nil }
        // The socket lands where it was aimed or it fails. See the note on
        // this in `DomovoiClient`: on iOS this rule is enforced here AND by
        // refusing to open a socket at an address we did not choose.
        guard !isWebSocketUpgrade else { return nil }
        guard let location, let from = request.url else { return nil }
        // Foundation's relative resolution, where Kotlin has
        // `HttpUrl.resolve`. Both refuse a `Location` they cannot read; what
        // each accepts at the edges (stray spaces, a bare `?query`) is the
        // library's business and is not pinned here, because a `Location` the
        // two read differently still has to pass every rule below.
        guard let resolved = URL(string: location, relativeTo: from)?.absoluteURL else { return nil }
        // http / https only, and parsed by the module's own parser so the
        // hop's host is read the way `CleartextPolicy` and `TokenScope` will
        // read it. A `Location` of `ftp://x/y` stops here.
        guard let to = ServerURL.parse(resolved.absoluteString) else { return nil }
        // An https→http downgrade is never followed. Compared on the parsed
        // scheme, not on `URL.scheme`, so `HTTP://` and `http://` are one
        // thing.
        if let origin = ServerURL.parse(from.absoluteString), origin.isHttps, !to.isHttps { return nil }

        var next = request
        next.url = resolved
        let method = request.httpMethod ?? "GET"
        let bodyless = method == "GET" || method == "HEAD"
        if status == 307 || status == 308 {
            // OkHttp's rule: a 307/308 of a POST is handed back to the
            // caller rather than replayed, because replaying it would resend
            // the body to an address the caller did not choose.
            guard bodyless else { return nil }
        } else if !bodyless {
            // 300/301/302/303: the follow-up is a GET without the body.
            next.httpMethod = "GET"
            next.httpBody = nil
            next.httpBodyStream = nil
            next.setValue(nil, forHTTPHeaderField: "Transfer-Encoding")
            next.setValue(nil, forHTTPHeaderField: "Content-Length")
            next.setValue(nil, forHTTPHeaderField: "Content-Type")
        }
        if !sameConnection(from, resolved) {
            next.setValue(nil, forHTTPHeaderField: "Authorization")
        }
        // Always. The hop's own scope check puts it back where it belongs,
        // and nowhere else.
        next.setValue(nil, forHTTPHeaderField: deviceTokenHeader)
        return next
    }

    /// Kotlin's `sameConnection`: one scheme, one host, one port. A URL
    /// neither side can parse is treated as a different connection, which is
    /// the direction that drops a credential rather than carrying it.
    static func sameConnection(_ a: URL, _ b: URL) -> Bool {
        guard let left = ServerURL.parse(a.absoluteString),
              let right = ServerURL.parse(b.absoluteString) else { return false }
        return left == right
    }
}
