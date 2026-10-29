import Foundation
import Synchronization
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

        /// A redirect whose proposed follow-up carries the headers across —
        /// which is what the platform really does, and the trap the delegate
        /// has to not fall into.
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
        let configuration = DomovoiClient.defaultConfiguration()
        configuration.protocolClasses = [StubURLProtocol.self]
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
        let request = self.request
        var recordedCopy = request
        recordedCopy.httpBody = Self.body(of: request)
        let answer = Self.script.withLock { script -> Answer in
            script.recorded.append(recordedCopy)
            if let dispatch = script.dispatch { return dispatch(request) }
            if script.queued.isEmpty { return Answer.json("{}") }
            return script.queued.removeFirst()
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
            // The platform's proposal: the same method, and the headers
            // copied across — `X-Device-Token` among them. A delegate that
            // refined this proposal instead of rebuilding the request would
            // carry the token to the new host, which is the whole reason
            // `RedirectPolicy` exists.
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
