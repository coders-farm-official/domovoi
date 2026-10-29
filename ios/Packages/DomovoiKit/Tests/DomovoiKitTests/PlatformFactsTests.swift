import XCTest
@testable import DomovoiKit

/// The things this batch had to find out about `URLSession` rather than
/// assume, measured.
///
/// The I0-A review's standing rule is that a comment stating a fact must
/// state a fact somebody checked. Four of this batch's decisions rest on
/// claims about the platform that are easy to get wrong and expensive to get
/// wrong, so each one is measured here — and stays measured, so that an SDK
/// which changes one of them fails a test instead of quietly carrying the
/// household token somewhere.
///
/// Measured on the build Mac, macOS 26.4.1 / Swift 6.3, 2026-10-28. Two of
/// the four came out against the guess that was written down first; both
/// comments were corrected to match the measurement, not the other way
/// about.
final class PlatformFactsTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
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

    /// **Fact 2: the proposed request arrives with our headers copied onto
    /// it.**
    ///
    /// This is the one the port depends on most. `RedirectPolicy` throws the
    /// platform's proposal away and rebuilds the follow-up from the request
    /// we sent, and the reason is exactly this: the proposal arrives carrying
    /// `X-Device-Token`, for a host that is not the active server.
    func testTheProposedRequestArrivesCarryingOurHeaders() async throws {
        StubURLProtocol.enqueue(
            .moved(302, to: "http://10.0.0.99:6369/api/y"),
            .json("{}")
        )
        let asked = Asked()
        let session = URLSession(configuration: StubURLProtocol.session())
        var request = URLRequest(url: URL(string: "http://10.0.0.9:6369/api/x")!)
        request.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        _ = try await session.data(for: request, delegate: asked)
        XCTAssertEqual(
            asked.proposedTokens, ["household-abc"],
            "the platform offers a follow-up to another host with the token already on it"
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
    func testRefusingARedirectHandsBackThe3xxRatherThanAnError() async throws {
        StubURLProtocol.enqueue(.moved(301, to: "http://10.0.0.99:6369/api/y"))
        let asked = Asked(refuse: true)
        let session = URLSession(configuration: StubURLProtocol.session(timeout: 5))
        let request = URLRequest(url: URL(string: "http://10.0.0.9:6369/api/x")!)
        let (_, response) = try await session.data(for: request, delegate: asked)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 301)
        XCTAssertEqual(StubURLProtocol.requestCount, 1, "nothing was sent to the host it named")
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
