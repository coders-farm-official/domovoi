import Foundation

/// A GET that **cannot** carry the household token, whatever the caller does.
///
/// The one way ``Discovery`` and ``IdentityGate`` reach the network. Both ask
/// hosts that are not (yet) the active server — a sweep touches 254
/// addresses, and the identity probe asks a server to prove itself *before*
/// it is trusted with anything — so neither may ever send a token. Until
/// 2026-10-08 Android's sweep sent the active server's household token to all
/// 254 (finding A6-02), and the brief for this batch asks for that to be
/// **structurally impossible rather than remembered**.
///
/// How that is made structural, and the limits of the claim:
///
///  - The initializer is internal, so outside `DomovoiKit` the only way to
///    obtain one of these is ``DomovoiClient/unauthenticatedProbe``, which
///    hard-codes `TokenMode.none`.
///  - `TokenMode.none` carries nothing on the first hop **and on every
///    redirect hop** (I1-A's review finding 1: the hop is where this would
///    otherwise come back).
///  - Inside the module the type could be constructed with any closure. The
///    test target does exactly that, because a stubbed probe is how the
///    verdicts and the sweep are tested at all. So the claim is about
///    production call sites, of which there is one — and two tests drive
///    that one through the stubbed transport and read the headers off the
///    requests, rather than asking you to trust this paragraph:
///    `DiscoveryTests.testAProbeCarriesNoTokenEvenToTheActiveServer` for a
///    single probe of the active server, and that file's
///    `testAScanOfAPhoneThatIsOnALanProbesTwoHundredAndFiftyFourAddressesWithNoToken`
///    for all 254 of a sweep, arranged so that the one address which answers
///    is the active server's — the address a token would have gone to. The
///    second skips, announcing it, on a machine with no site-local IPv4.
public struct UnauthenticatedProbe: Sendable {

    public struct Answer: Equatable, Sendable {
        public let status: Int
        public let body: String

        public var isSuccessful: Bool { (200...299).contains(status) }

        public init(status: Int, body: String) {
            self.status = status
            self.body = body
        }
    }

    /// Throws when nothing answered (a ``DomovoiError`` from the client);
    /// otherwise the status and the body, whatever the status.
    let fetch: @Sendable (URL, TimeInterval) async throws -> Answer

    init(fetch: @Sendable @escaping (URL, TimeInterval) async throws -> Answer) {
        self.fetch = fetch
    }

    public func get(_ url: URL, timeout: TimeInterval) async throws -> Answer {
        try await fetch(url, timeout)
    }

    /// The body of a 2xx, or nil for any other status — the shape
    /// `IdentityGate.httpProbe` has in the Kotlin ("the body on a 2xx, null
    /// otherwise; throws when nothing answers").
    public func successBody(_ url: URL, timeout: TimeInterval) async throws -> String? {
        let answer = try await fetch(url, timeout)
        return answer.isSuccessful ? answer.body : nil
    }
}

public extension DomovoiClient {

    /// The app's own client with the token taken off it: the counterpart of
    /// Android's `Discovery.client(http, timeoutMs)`, which rebuilds the
    /// `OkHttpClient` without `DeviceAuthInterceptor` and keeps everything
    /// else — the cleartext policy above all, which still refuses a public
    /// address in the clear before any connection is made.
    ///
    /// No `X-Requested-With` header, deliberately: Android's two probes
    /// (`Discovery.probe`, `IdentityGate.httpProbe`) build a bare
    /// `Request.Builder`, and both routes are open reads with no side effect
    /// for that header to protect.
    var unauthenticatedProbe: UnauthenticatedProbe {
        UnauthenticatedProbe { [self] url, timeout in
            var request = URLRequest(url: url)
            request.timeoutInterval = timeout
            let (data, response, sentToken) = try await perform(request, mode: .none)
            // Belt and braces on the claim above: `perform` reports what the
            // first hop carried, and for `.none` that is always nil. If this
            // ever fires, the sweep has started carrying the token again and
            // the probe refuses to be the thing that did it.
            precondition(sentToken == nil, "an unauthenticated probe carried a token")
            return UnauthenticatedProbe.Answer(status: response.statusCode, body: Self.text(data))
        }
    }
}
