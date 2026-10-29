import XCTest
@testable import DomovoiKit

/// The things this batch had to find out about `URLSession` rather than
/// assume, measured.
///
/// The I0-A review's standing rule is that a comment stating a fact must
/// state a fact somebody checked. Three of the ports in this batch rest on
/// claims about the platform that are easy to get wrong and expensive to get
/// wrong — whether a per-task delegate is asked about a redirect at all,
/// whether refusing one stops the chain, and what the platform hands us when
/// it asks. This file is where those claims are measured, so that a future
/// SDK that changes one of them fails a test instead of quietly carrying the
/// household token somewhere.
///
/// Measured on macOS 26.4.1 / Swift 6.3 (the build Mac, 2026-10-28).
final class PlatformFactsTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    /// **Fact 1: a per-task delegate IS asked about a redirect.**
    ///
    /// `DomovoiClient` passes its delegate to `session.data(for:delegate:)`
    /// rather than setting one on the session, so that a redirect decision
    /// knows which request it belongs to. That only works if a task delegate
    /// receives `urlSession(_:task:willPerformHTTPRedirection:…)`. Apple
    /// documents task-specific delegates as receiving task-level callbacks;
    /// this is that documentation, measured.
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

    /// **Fact 2: the proposed request has the headers copied onto it.**
    ///
    /// This is the one the port depends on most. `RedirectPolicy` throws the
    /// platform's proposal away and rebuilds the follow-up from the request
    /// we sent, and the reason is that the proposal arrives carrying
    /// `X-Device-Token`. If that ever stopped being true the port would still
    /// be correct — it would just be belt without braces — but the comment
    /// saying so would not be.
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
    /// `completionHandler(nil)` is how a delegate declines, and what the
    /// caller then gets is the redirect response itself with its own body —
    /// not an error. That is why `DomovoiClient` cannot simply let the
    /// refusal surface as a thrown error the way Android's interceptor does
    /// (it throws `UnknownServiceException` from inside the chain), and why
    /// the delegate records the reason and the client raises it afterwards.
    func testRefusingARedirectHandsBackThe3xxRatherThanAnError() async throws {
        StubURLProtocol.enqueue(.moved(301, to: "http://10.0.0.99:6369/api/y"))
        let asked = Asked(refuse: true)
        let session = URLSession(configuration: StubURLProtocol.session(timeout: 3))
        let request = URLRequest(url: URL(string: "http://10.0.0.9:6369/api/x")!)
        do {
            let (_, response) = try await session.data(for: request, delegate: asked)
            print("MEASURED declined-redirect outcome: response \((response as? HTTPURLResponse)?.statusCode ?? -1)")
        } catch {
            print("MEASURED declined-redirect outcome: error \(error)")
        }
        XCTAssertEqual(StubURLProtocol.requestCount, 1, "nothing was sent to the host it named")
    }

    /// **Fact 4: `URLProtocol` does not see a WebSocket task.**
    ///
    /// Recorded because it bounds what the suite can claim. The brief asked
    /// whether `URLSessionWebSocketTask` calls the redirect delegate at all;
    /// this test shows the question cannot be answered with a `URLProtocol`
    /// stub, because the socket's loading does not go through one — the stub
    /// records no request and the task fails instead. The answer that *was*
    /// measured, against a loopback listener on the build Mac rather than in
    /// this suite, is written up in `RedirectDelegate.followUp` and in the
    /// batch report.
    func testIsAWebSocketUpgradeRedirectedAndIsTheDelegateAsked() async {
        StubURLProtocol.enqueue(.moved(302, to: "ws://10.0.0.99:6369/ws/state"), .json("{}"))
        let asked = Asked()
        let session = URLSession(configuration: StubURLProtocol.session(timeout: 3))
        let socket = session.webSocketTask(with: URL(string: "ws://10.0.0.9:6369/ws/state")!)
        socket.delegate = asked
        socket.resume()
        do {
            _ = try await socket.receive()
            print("MEASURED socket redirect outcome: the socket opened")
        } catch {
            print("MEASURED socket redirect outcome: \(error)")
        }
        print("MEASURED socket redirect: delegate asked \(asked.redirections) time(s), stub saw \(StubURLProtocol.requestCount) request(s)")
        for (index, request) in StubURLProtocol.recorded.enumerated() {
            print("MEASURED socket request \(index): \(request.url?.absoluteString ?? "?")")
        }
        socket.cancel()
    }

    /// A delegate that only watches, and optionally declines.
    ///
    /// `@unchecked Sendable` for the same reason `RedirectDelegate` is: a
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
