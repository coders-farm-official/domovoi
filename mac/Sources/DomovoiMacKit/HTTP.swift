import Foundation

public struct HTTPResponse: Equatable {
    public var status: Int
    public var body: Data
    public init(status: Int, body: Data = Data()) {
        self.status = status
        self.body = body
    }
    public var text: String { String(decoding: body, as: UTF8.self) }
}

/// Health checks and Ollama's version (GET); the media watcher's notices
/// to the core (POST, JSON).
public protocol HTTPClient: AnyObject {
    /// nil when nothing answered (refused, timed out).
    func get(_ url: String, timeout: TimeInterval) -> HTTPResponse?
    func post(_ url: String, json: Data, headers: [String: String], timeout: TimeInterval) -> HTTPResponse?
}

extension HTTPClient {
    /// A client that only reads (the tests' fakes) answers nothing.
    public func post(_ url: String, json: Data, headers: [String: String], timeout: TimeInterval) -> HTTPResponse? { nil }
}

public final class URLSessionHTTP: HTTPClient {
    let session: URLSession

    public init() {
        let c = URLSessionConfiguration.ephemeral
        c.connectionProxyDictionary = [:]       // loopback only: never a proxy
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: c)
    }

    public func get(_ url: String, timeout: TimeInterval) -> HTTPResponse? {
        guard let u = URL(string: url) else { return nil }
        var req = URLRequest(url: u)
        req.timeoutInterval = timeout
        return send(req, timeout: timeout)
    }

    public func post(_ url: String, json: Data, headers: [String: String], timeout: TimeInterval) -> HTTPResponse? {
        guard let u = URL(string: url) else { return nil }
        var req = URLRequest(url: u)
        req.httpMethod = "POST"
        req.httpBody = json
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        req.timeoutInterval = timeout
        return send(req, timeout: timeout)
    }

    func send(_ req: URLRequest, timeout: TimeInterval) -> HTTPResponse? {
        let done = DispatchSemaphore(value: 0)
        var result: HTTPResponse?
        session.dataTask(with: req) { data, resp, _ in
            if let h = resp as? HTTPURLResponse { result = HTTPResponse(status: h.statusCode, body: data ?? Data()) }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + timeout + 2)
        return result
    }
}
