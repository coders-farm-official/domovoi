import XCTest
@testable import DomovoiKit

/// The things this batch had to find out about `URLSession` rather than
/// assume, measured.
///
/// The I0-A review's standing rule is that a comment stating a fact must
/// state a fact somebody checked. Several of this batch's decisions rest on
/// claims about the platform that are easy to get wrong and expensive to get
/// wrong, so each one is measured here — and stays measured, so that an SDK
/// which changes one of them fails a test instead of quietly carrying the
/// household token somewhere.
///
/// Measured on the build Mac, macOS 26.4.1 / Swift 6.3, 2026-10-28, and
/// re-measured 2026-10-29 (the I1-A review). Facts that came out against the
/// guess written down first had their comments corrected to match the
/// measurement, not the other way about.
///
/// **What each fact is measured against, because it decides whether the test
/// can fail at all.**
///
///  * Facts 1, 4 and 5 run through ``StubURLProtocol`` and are real: the stub
///    answers, and what is asserted is what CFNetwork does with that answer —
///    whether a *task* delegate is consulted at all, whether a WebSocket
///    upgrade goes through a custom protocol and comes out as `http://`, and
///    what `addingPercentEncoding` makes of a character set. The stub fakes
///    none of that.
///  * Facts 2 and 3 are about the transport itself, so they cannot be: a
///    `URLProtocol` *is* the transport. The first version of them asserted
///    that the redirect proposal arrives carrying `X-Device-Token` — but the
///    proposal was built by `StubURLProtocol.Answer.moved`, which copies the
///    headers across itself, so the assertion measured the test folder and
///    could not have failed however the SDK changed. They now run against
///    ``LoopbackServer``, a real socket on 127.0.0.1, and read the token off
///    the bytes the other origin received.
final class PlatformFactsTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    /// A session that really connects — no `URLProtocol` in front of it — for
    /// the two facts that have to go down a socket.
    private static func overASocket() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return configuration
    }

    /// **Fact 1: a per-task delegate IS asked about a redirect.**
    ///
    /// `DomovoiClient` passes its delegate to `session.data(for:delegate:)`
    /// rather than setting one on the session, so that a redirect decision
    /// knows which request it belongs to and what that request was allowed
    /// to carry. That only works if a task delegate receives
    /// `urlSession(_:task:willPerformHTTPRedirection:…)`.
    func testATaskDelegateIsAskedAboutARedirect() async throws {
        StubURLProtocol.enqueue(
            .moved(302, to: "http://10.0.0.9:6369/api/y"),
            .json("{}")
        )
        let asked = Asked()
        let session = URLSession(configuration: StubURLProtocol.session())
        let request = URLRequest(url: URL(string: "http://10.0.0.9:6369/api/x")!)
        _ = try await session.data(for: request, delegate: asked)
        XCTAssertEqual(asked.redirections, 1, "the delegate was asked exactly once")
        XCTAssertEqual(asked.locations, ["http://10.0.0.9:6369/api/y"])
    }

    /// **Fact 2: the platform's proposed follow-up really does deliver
    /// `X-Device-Token` to another origin.**
    ///
    /// This is the one the port depends on most. `RedirectPolicy` throws the
    /// platform's proposal away and rebuilds the follow-up from the request we
    /// sent, and the reason is exactly this: left alone, the token goes where
    /// the `Location` said.
    ///
    /// Measured end to end, against two real sockets: the redirect's target is
    /// a **second** server, which is asked for the header it received, so what
    /// is asserted is the bytes that arrived somewhere they should not have.
    /// Two loopback ports are two origins to ``TokenScope`` and to the browser
    /// rule it follows, which is the hazard — a `302` to another port on the
    /// same machine is how a reverse proxy and the drop-in socket both look.
    ///
    /// The delegate here follows the proposal exactly as offered, which is
    /// what a client that "refined" the proposal rather than rebuilding it
    /// would be doing.
    func testTheProposedFollowUpCarriesTheTokenToAnotherOriginOnTheWire() async throws {
        let elsewhere = try LoopbackServer(.init(body: "{}"))
        defer { elsewhere.stop() }
        let server = try LoopbackServer(.init(status: 302, location: elsewhere.url("/api/y").absoluteString))
        defer { server.stop() }

        let asked = Asked()
        var request = URLRequest(url: server.url("/api/x"))
        request.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        _ = try await URLSession(configuration: Self.overASocket()).data(for: request, delegate: asked)

        XCTAssertEqual(server.header(deviceTokenHeader, at: 0), "household-abc", "hop 1 carried it, as asked")
        XCTAssertEqual(
            asked.proposedTokens, ["household-abc"],
            "the platform offers a follow-up to another origin with the token already on it"
        )
        XCTAssertEqual(
            elsewhere.header(deviceTokenHeader, at: 0), "household-abc",
            "and following that proposal really does put the household token on another origin's socket"
        )
    }

    /// **Fact 3: refusing a redirect delivers the 3xx; it does not fail.**
    ///
    /// `completionHandler(nil)` is how a delegate declines, and the caller
    /// then gets the redirect response itself — not an error. So
    /// `DomovoiClient` cannot let a refusal surface as a thrown error the way
    /// Android's interceptor does (OkHttp throws `UnknownServiceException`
    /// from inside the chain), which is why ``RedirectDelegate`` records the
    /// reason and the client raises it after the task is done. A client that
    /// trusted the status alone here would report a 301 to the screens as
    /// though the server had sent one on purpose.
    ///
    /// Against sockets for the same reason as Fact 2, and it buys a second
    /// assertion the stub could not make honestly: the host the `Location`
    /// named is a real server, and it accepted no connection at all.
    func testRefusingARedirectHandsBackThe3xxAndReachesNothing() async throws {
        let elsewhere = try LoopbackServer(.init(body: "{}"))
        defer { elsewhere.stop() }
        let server = try LoopbackServer(.init(status: 301, location: elsewhere.url("/api/y").absoluteString))
        defer { server.stop() }

        let asked = Asked(refuse: true)
        let request = URLRequest(url: server.url("/api/x"))
        let (_, response) = try await URLSession(configuration: Self.overASocket())
            .data(for: request, delegate: asked)

        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 301, "the 3xx itself, not an error")
        XCTAssertEqual(asked.redirections, 1, "and the delegate was the one that declined it")
        XCTAssertEqual(server.requestCount, 1)
        XCTAssertEqual(elsewhere.requestCount, 0, "nothing opened a connection to the host it named")
    }

    /// **Fact 4: a WebSocket upgrade IS redirected, and the delegate IS
    /// asked.** The brief's open question, answered.
    ///
    /// `URLSessionWebSocketTask` goes through a custom `URLProtocol` like any
    /// other task (which is itself worth knowing — it is what lets this suite
    /// test the socket rules without a network), its upgrade is sent as the
    /// `http://` GET it starts as, and when that answers `302` the task
    /// delegate's `willPerformHTTPRedirection` is called. So iOS *can* refuse
    /// a socket redirect, and ``RedirectPolicy`` does: the socket lands where
    /// it was aimed or it fails.
    ///
    /// Also measured: the upgrade's URL on the wire is `http://`, not `ws://`
    /// — the platform rewrites the scheme exactly as OkHttp's
    /// `Request.Builder` does, which is why ``ServerURL/parseAllowingWebSocket(_:)``
    /// is the right parser for a socket address on both phones.
    func testAWebSocketUpgradeIsRedirectedAndTheDelegateIsAsked() async {
        StubURLProtocol.enqueue(.moved(302, to: "ws://10.0.0.99:6369/ws/state"), .json("{}"))
        let asked = Asked()
        let session = URLSession(configuration: StubURLProtocol.session(timeout: 5))
        let socket = session.webSocketTask(with: URL(string: "ws://10.0.0.9:6369/ws/state")!)
        socket.delegate = asked
        socket.resume()
        // Receiving is how a socket that never opened reports itself.
        _ = try? await socket.receive()
        XCTAssertEqual(asked.redirections, 1, "the socket's redirect went past the delegate")
        XCTAssertEqual(
            StubURLProtocol.recorded.first?.url?.absoluteString, "http://10.0.0.9:6369/ws/state",
            "the upgrade goes out as http, not ws"
        )
        socket.cancel()
    }

    /// And with the delegate refusing, as ``RedirectPolicy`` refuses: the
    /// host the `Location` named is never reached at all.
    func testARefusedSocketRedirectNeverReachesTheOtherHost() async {
        StubURLProtocol.enqueue(.moved(302, to: "ws://10.0.0.99:6369/ws/state"))
        let asked = Asked(refuse: true)
        let session = URLSession(configuration: StubURLProtocol.session(timeout: 5))
        let socket = session.webSocketTask(with: URL(string: "ws://10.0.0.9:6369/ws/state")!)
        socket.delegate = asked
        socket.resume()
        _ = try? await socket.receive()
        XCTAssertEqual(StubURLProtocol.requestCount, 1)
        XCTAssertEqual(StubURLProtocol.recorded.first?.url?.host, "10.0.0.9")
        socket.cancel()
    }

    /// **Fact 5: `addingPercentEncoding` encodes a non-ASCII scalar whatever
    /// the allowed set says.**
    ///
    /// Measured 2026-10-29, for ``DeviceRegistry/pathEscaped(_:)``, which
    /// escapes a self-asserted device id for a path segment. It used to name
    /// `CharacterSet.alphanumerics`, which is Unicode-wide — "é", "д" and the
    /// Arabic-Indic digit "٣" are all alphanumeric to it — where Android's
    /// `Uri.encode` safe set is ASCII letters and digits plus `_-!.~'()*`. The
    /// review called that a difference between the two phones. **It is not**,
    /// and this is why: Foundation encodes every scalar outside ASCII however
    /// generous the set is, so the two spellings of the safe set produce the
    /// same bytes.
    ///
    /// `pathEscaped` names its set explicitly anyway, because a reader should
    /// not have to know this to check it against the Kotlin — and if this
    /// fact ever stops being true, the explicit set is what keeps the two
    /// apps agreeing and this test is what says so.
    func testPercentEncodingEncodesNonAsciiHoweverWideTheAllowedSetIs() {
        var unicodeWide = CharacterSet.alphanumerics
        unicodeWide.insert(charactersIn: "_-!.~'()*")
        XCTAssertTrue(unicodeWide.contains("\u{00E9}"), "the set really does claim to allow it")
        XCTAssertEqual(
            "ios-caf\u{00E9}".addingPercentEncoding(withAllowedCharacters: unicodeWide), "ios-caf%C3%A9",
            "and Foundation encodes it regardless"
        )
        XCTAssertEqual(
            "ios-caf\u{00E9}".addingPercentEncoding(withAllowedCharacters: DeviceRegistry.pathSafeCharacters),
            "ios-caf%C3%A9",
            "so the ASCII set this app names agrees with it, byte for byte"
        )
        // The ASCII halves of the two sets are identical, which is the part
        // that does decide what goes on the wire.
        for scalar in "aZ09_-!.~'()*".unicodeScalars {
            XCTAssertTrue(unicodeWide.contains(scalar), "\(scalar)")
            XCTAssertTrue(DeviceRegistry.pathSafeCharacters.contains(scalar), "\(scalar)")
        }
        for scalar in "/?#:@&=+$, %\"<>[]{}|\\^`".unicodeScalars {
            XCTAssertFalse(DeviceRegistry.pathSafeCharacters.contains(scalar), "\(scalar)")
        }
    }

    /// A delegate that only watches, and optionally declines.
    ///
    /// `@unchecked Sendable` for the same reason ``RedirectDelegate`` is: a
    /// `URLSession` delegate has to be an `NSObject`, and `NSObject` is not
    /// `Sendable`. Everything mutable here is behind the one lock.
    private final class Asked: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
        private let refuse: Bool
        private let seen = NSLock()
        private var _locations: [String] = []
        private var _proposedTokens: [String] = []

        init(refuse: Bool = false) {
            self.refuse = refuse
            super.init()
        }

        var locations: [String] { seen.withLock { _locations } }
        var proposedTokens: [String] { seen.withLock { _proposedTokens } }
        var redirections: Int { locations.count }

        func urlSession(
            _ session: URLSession,
            task: URLSessionTask,
            willPerformHTTPRedirection response: HTTPURLResponse,
            newRequest request: URLRequest,
            completionHandler: @escaping (URLRequest?) -> Void
        ) {
            seen.withLock {
                _locations.append(request.url?.absoluteString ?? "")
                if let token = request.value(forHTTPHeaderField: deviceTokenHeader) {
                    _proposedTokens.append(token)
                }
            }
            completionHandler(refuse ? nil : request)
        }
    }
}
