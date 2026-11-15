import Foundation
import Synchronization
import XCTest
@testable import DomovoiKit

/// A server that is not a server: a `URLProtocol` the test session loads
/// instead of the network.
///
/// This is the iOS counterpart of Android's `MockWebServer`, which every test
/// in `android/.../net/` is built on, and it is deliberately the same shape:
/// answers are enqueued or dispatched, and every request that was *attempted*
/// is recorded with its headers, so a test can assert what left the phone
/// rather than only what came back.
///
/// It reaches no network. That matters here beyond hygiene: this batch's
/// brief forbids scanning anything, a /24 sweep belongs to I1-B, and a test
/// suite that quietly opened sockets would be the first step towards one.
/// `URLProtocol` is also the only way to drive a redirect chain, a 429 with a
/// `Retry-After` and a malformed body exactly, which is most of what there is
/// to test.
final class StubURLProtocol: URLProtocol {

    struct Answer: Sendable {
        var status: Int = 200
        var headers: [String: String] = [:]
        var body: Data = Data()
        /// A transport failure instead of an answer: nothing answered.
        var error: URLError?
        /// A `Location`, signalled the way CFNetwork signals a redirect.
        var redirect: String?

        static func json(_ text: String, status: Int = 200, headers: [String: String] = [:]) -> Answer {
            Answer(status: status, headers: headers, body: Data(text.utf8))
        }

        /// A redirect whose proposed follow-up carries the headers across.
        ///
        /// That the real platform does this is measured against a socket, in
        /// `PlatformFactsTests` fact 2, and **not here**: the copying below is
        /// this file imitating the measurement so that the rules can be tested
        /// against the same trap without a network. An assertion that the
        /// proposal carries `X-Device-Token`, made through this stub, would be
        /// an assertion about `startLoading` a few lines down — which is how
        /// two tests that could not fail got written in the first place.
        static func moved(_ status: Int, to location: String) -> Answer {
            Answer(status: status, headers: ["Location": location], redirect: location)
        }
    }

    /// Everything the stub knows, as one value under one lock.
    private struct Script {
        var queued: [Answer] = []
        var dispatch: (@Sendable (URLRequest) -> Answer)?
        var recorded: [URLRequest] = []
    }

    private static let script = Mutex(Script())

    // ---- which case a request belongs to ----------------------------------

    /// The case now running, counted by ``CaseBoundary`` as each one starts.
    ///
    /// **Why this exists.** The script above is process-global, and a case's
    /// work can outlive the case: a write's re-read queued in the turn the
    /// case returned, a request the slower CI runner had not yet handed to
    /// `startLoading`. Such a request used to be recorded in the **next**
    /// case's log and answered by the next case's dispatcher — CI's red at
    /// `dbe3e6a4` was `GET .../kitchen/timers` as request `[0]` of
    /// `RoomsModelTests.testEachTabIsReadWhenItIsShownAndNotBefore`.
    ///
    /// So every session ``session(timeout:_:)`` builds is stamped with the
    /// case it was built in, and a request stamped with an earlier case is
    /// refused here: not recorded, not dispatched, failed as cancelled. A
    /// reset **inside** a case (fifty-odd cases do one) does not move this,
    /// because it is the case boundary that matters, not the script's.
    private static let currentCase = Atomic<Int>(0)

    /// The header the stamp travels in. Stripped from what is recorded and
    /// from what a dispatcher sees, so no test's header assertion sees it.
    static let caseHeader = "X-Stub-Case"

    /// A case started. Called by ``CaseBoundary``, and by the test that pins
    /// the boundary to stand in for one.
    static func caseStarted() {
        currentCase.add(1, ordering: .sequentiallyConsistent)
    }

    /// Whether a request was made by a session built in an earlier case.
    /// A request with no stamp (a configuration whose headers were replaced
    /// after ``session(timeout:_:)``) is taken as this case's.
    static func isFromAnEarlierCase(_ request: URLRequest) -> Bool {
        guard
            let stamp = request.value(forHTTPHeaderField: caseHeader),
            let made = Int(stamp)
        else { return false }
        return made != currentCase.load(ordering: .sequentiallyConsistent)
    }

    /// How many requests from an earlier case were refused — for the test
    /// that pins the boundary.
    static var refusedFromEarlierCases: Int { refused.load(ordering: .sequentiallyConsistent) }
    private static let refused = Atomic<Int>(0)

    // ---- what a test says --------------------------------------------------

    static func reset() {
        script.withLock { $0 = Script() }
    }

    /// One answer, taken in turn. `MockResponse` + `enqueue`.
    static func enqueue(_ answer: Answer) {
        script.withLock { $0.queued.append(answer) }
    }

    static func enqueue(_ answers: Answer...) {
        script.withLock { $0.queued.append(contentsOf: answers) }
    }

    /// An answer per request, for the tests that need a server with a mind of
    /// its own. `MockWebServer.dispatcher`.
    static func dispatch(_ answer: @Sendable @escaping (URLRequest) -> Answer) {
        script.withLock { $0.dispatch = answer }
    }

    /// Every request that was attempted, in order, with its headers.
    static var recorded: [URLRequest] { script.withLock { $0.recorded } }

    static var requestCount: Int { script.withLock { $0.recorded.count } }

    static func header(_ name: String, at index: Int) -> String? {
        let all = recorded
        guard all.indices.contains(index) else { return nil }
        return all[index].value(forHTTPHeaderField: name)
    }

    /// A session that answers from this stub and from nothing else.
    /// `timeout` is there so a test that expects nothing to answer does not
    /// spend the client's real 60 s finding that out.
    static func session(
        timeout: TimeInterval? = nil,
        _ configure: (URLSessionConfiguration) -> Void = { _ in }
    ) -> URLSessionConfiguration {
        CaseBoundary.install()
        let configuration = DomovoiClient.defaultConfiguration()
        configuration.protocolClasses = [StubURLProtocol.self]
        var headers = configuration.httpAdditionalHeaders ?? [:]
        headers[caseHeader] = String(currentCase.load(ordering: .sequentiallyConsistent))
        configuration.httpAdditionalHeaders = headers
        if let timeout { configuration.timeoutIntervalForRequest = timeout }
        configure(configuration)
        return configuration
    }

    // ---- URLProtocol -------------------------------------------------------

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    /// A `URLProtocol` is handed the body as a stream, not as `httpBody`:
    /// measured 2026-10-28, where a test asserting on `recorded[0].httpBody`
    /// saw nil for a POST that plainly had one. So the recorded copy gets the
    /// stream read back into `httpBody`, and a test can assert on the bytes
    /// that left.
    private static func body(of request: URLRequest) -> Data? {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let size = 4096
        var buffer = [UInt8](repeating: 0, count: size)
        while stream.hasBytesAvailable {
            let read = stream.read(&buffer, maxLength: size)
            if read <= 0 { break }
            data.append(contentsOf: buffer[0..<read])
        }
        return data.isEmpty ? nil : data
    }

    override func startLoading() {
        if Self.isFromAnEarlierCase(self.request) {
            Self.refused.add(1, ordering: .sequentiallyConsistent)
            client?.urlProtocol(self, didFailWithError: URLError(.cancelled))
            return
        }
        var request = self.request
        request.setValue(nil, forHTTPHeaderField: Self.caseHeader)
        var recordedCopy = request
        recordedCopy.httpBody = Self.body(of: request)
        // The dispatcher is called **outside** the lock, and the recording is
        // made before it. Measured 2026-10-29: with `dispatch` called inside
        // `withLock`, a test whose dispatcher parked a request until it was
        // released held this mutex for the whole park — so the test's own
        // `StubURLProtocol.recorded` read blocked the main actor, the park ran
        // to its bound, the request hit `timeoutIntervalForRequest` and the
        // register it was testing failed instead of landing. The test then
        // asserted nil over a request that never answered and could not fail.
        //
        // This is the same rule the library itself follows — `StateSocket`
        // runs every injected closure outside `state.withLock` — and it is
        // why a test may hold a request open at all.
        let decided = Self.script.withLock { script -> (@Sendable (URLRequest) -> Answer)? in
            script.recorded.append(recordedCopy)
            if let dispatch = script.dispatch { return dispatch }
            return nil
        }
        let answer: Answer
        if let decided {
            answer = decided(request)
        } else {
            answer = Self.script.withLock { script -> Answer in
                script.queued.isEmpty ? Answer.json("{}") : script.queued.removeFirst()
            }
        }
        guard let client, let url = request.url else { return }

        if let error = answer.error {
            client.urlProtocol(self, didFailWithError: error)
            return
        }
        guard let response = HTTPURLResponse(
            url: url,
            statusCode: answer.status,
            httpVersion: "HTTP/1.1",
            headerFields: answer.headers
        ) else { return }

        if let redirect = answer.redirect, let next = URL(string: redirect, relativeTo: url) {
            // The platform's proposal, imitated: the same method, and the
            // headers copied across — `X-Device-Token` among them. A delegate
            // that refined this proposal instead of rebuilding the request
            // would carry the token to the new host, which is the whole reason
            // `RedirectPolicy` exists. The real platform's version of this is
            // measured over a socket (`PlatformFactsTests` fact 2); these
            // lines are what lets the rules be exercised against it offline.
            var proposed = URLRequest(url: next.absoluteURL)
            proposed.httpMethod = request.httpMethod
            proposed.allHTTPHeaderFields = request.allHTTPHeaderFields
            proposed.httpBody = request.httpBody
            client.urlProtocol(self, wasRedirectedTo: proposed, redirectResponse: response)
            // And then finish, as a `URLProtocol` must: the loading system
            // has no way to tell a protocol instance "carry on, the delegate
            // declined". Measured on 2026-10-28: a stub that signalled the
            // redirect and then returned without finishing left the task
            // hanging until `timeoutIntervalForRequest` expired, 60 s later.
            client.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client.urlProtocolDidFinishLoading(self)
            return
        }

        client.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if !answer.body.isEmpty { client.urlProtocol(self, didLoad: answer.body) }
        client.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

/// Counts each test case as it starts, for ``StubURLProtocol``'s case stamp.
///
/// Registered the first time any stub session is built, which is inside a
/// case: that case keeps stamp 0 until the next one starts, and every case
/// after it is counted before its `setUp` runs.
private final class CaseBoundary: NSObject, XCTestObservation, @unchecked Sendable {

    private static let installed = Mutex(false)

    static func install() {
        let first = installed.withLock { done -> Bool in
            defer { done = true }
            return !done
        }
        guard first else { return }
        let observer = CaseBoundary()
        // Every rig builds its session on the main actor; one that did not
        // registers a hop later rather than blocking on the main thread.
        if Thread.isMainThread {
            MainActor.assumeIsolated { XCTestObservationCenter.shared.addTestObserver(observer) }
        } else {
            DispatchQueue.main.async {
                MainActor.assumeIsolated { XCTestObservationCenter.shared.addTestObserver(observer) }
            }
        }
    }

    func testCaseWillStart(_ testCase: XCTestCase) {
        StubURLProtocol.caseStarted()
    }
}
