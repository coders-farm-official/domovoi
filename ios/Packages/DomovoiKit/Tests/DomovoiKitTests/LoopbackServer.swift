import Foundation

/// A real HTTP server, inside the test process, on 127.0.0.1.
///
/// **Why this exists and ``StubURLProtocol`` does not do.** Two of the
/// platform facts this batch rests on are facts about the *transport*: the
/// follow-up request CFNetwork proposes for a redirect, and what the caller is
/// handed when a delegate declines one. A `URLProtocol` replaces the
/// transport, so a test that drives one through the stub measures the stub —
/// it is `StubURLProtocol.Answer.moved` that copies the headers onto the
/// proposal, and an assertion that the proposal carries `X-Device-Token` is
/// then an assertion about eleven lines in the test folder. It cannot fail
/// when the SDK changes, which is the one job it was written for. Nothing a
/// `URLProtocol` can do fixes that. A socket can.
///
/// **What it does and does not reach.** It binds 127.0.0.1 and nothing else,
/// on a port in **6600-6699** — this session's lane, the same range
/// `remote.sh --forward` is limited to, and one that excludes 6369, 6370,
/// 6432 and 11434. A loopback listener is not reachable from another machine,
/// so this is still a suite that touches no network. It speaks just enough
/// HTTP/1.1 to answer a GET with a status, a `Location` and a body, and it
/// records the **bytes of each request's head**, so "what left the phone"
/// means what went down a socket rather than what a `URLRequest` in this
/// process happened to hold.
///
/// `@unchecked Sendable` with the reason, as the house rule asks: the two
/// mutable fields are read and written only inside `seen`, the accept loop is
/// the only writer, and the file descriptor is a `let`.
final class LoopbackServer: @unchecked Sendable {

    /// One scripted answer. The replies are served in order; a request past
    /// the end of the script gets an empty 200.
    struct Reply: Sendable {
        var status: Int = 200
        var location: String?
        var body: String = ""

        init(status: Int = 200, location: String? = nil, body: String = "") {
            self.status = status
            self.location = location
            self.body = body
        }
    }

    enum Trouble: Error, CustomStringConvertible {
        case noFreePort(errno: Int32)

        var description: String {
            switch self {
            case .noFreePort(let code):
                return "could not listen on 127.0.0.1 on any port in 6600-6699 (errno \(code))"
            }
        }
    }

    /// The port it is listening on, which the tests put in their URLs.
    let port: UInt16

    private let listening: Int32
    private let seen = NSLock()
    private var heads: [String] = []

    init(_ replies: Reply...) throws {
        var opened: Int32 = -1
        var chosen: UInt16 = 0
        var lastErrno: Int32 = 0
        for candidate in UInt16(6600)...UInt16(6699) {
            let fd = socket(AF_INET, SOCK_STREAM, 0)
            guard fd >= 0 else { lastErrno = errno; continue }
            var yes: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = candidate.bigEndian
            address.sin_addr.s_addr = inet_addr("127.0.0.1")
            let bound = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            if bound == 0, listen(fd, 4) == 0 {
                opened = fd
                chosen = candidate
                break
            }
            lastErrno = errno
            close(fd)
        }
        guard opened >= 0 else { throw Trouble.noFreePort(errno: lastErrno) }
        listening = opened
        port = chosen
        let script = replies
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            serve(script)
        }
    }

    /// Closes the listening socket, which is also how the accept loop is told
    /// to stop: `accept` on a closed descriptor fails and the loop returns.
    /// Every test that makes one of these stops it in a `defer`.
    func stop() {
        close(listening)
    }

    // ---- what a test asks ---------------------------------------------------

    /// The head of every request that reached the socket, in order, as it
    /// arrived.
    var requests: [String] { seen.withLock { heads } }

    var requestCount: Int { requests.count }

    /// A header's value in request `index`, read off the wire.
    func header(_ name: String, at index: Int) -> String? {
        let all = requests
        guard all.indices.contains(index) else { return nil }
        for line in all[index].components(separatedBy: "\r\n").dropFirst() {
            let halves = line.split(separator: ":", maxSplits: 1).map(String.init)
            guard halves.count == 2, halves[0].caseInsensitiveCompare(name) == .orderedSame else { continue }
            return halves[1].trimmingCharacters(in: .whitespaces)
        }
        return nil
    }

    /// The request line of request `index` — `GET /api/x HTTP/1.1`.
    func requestLine(at index: Int) -> String? {
        let all = requests
        guard all.indices.contains(index) else { return nil }
        return all[index].components(separatedBy: "\r\n").first
    }

    func url(_ path: String) -> URL {
        URL(string: "http://127.0.0.1:\(port)\(path)")!
    }

    // ---- the loop -----------------------------------------------------------

    private func serve(_ replies: [Reply]) {
        var next = 0
        while true {
            let client = accept(listening, nil, nil)
            if client < 0 { return }
            // Two seconds. A connection that opens and then says nothing must
            // cost the suite a moment, not a hung run over SSH with nobody
            // there to stop it.
            var limit = timeval(tv_sec: 2, tv_usec: 0)
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &limit, socklen_t(MemoryLayout<timeval>.size))
            var head = ""
            var buffer = [UInt8](repeating: 0, count: 2048)
            while !head.contains("\r\n\r\n") {
                let read = recv(client, &buffer, buffer.count, 0)
                if read <= 0 { break }
                head += String(decoding: buffer[0..<read], as: UTF8.self)
            }
            seen.withLock { heads.append(head) }

            let reply = replies.indices.contains(next) ? replies[next] : Reply()
            next += 1
            var answer = "HTTP/1.1 \(reply.status) Because\r\n"
            if let location = reply.location { answer += "Location: \(location)\r\n" }
            answer += "Content-Type: application/json\r\n"
            answer += "Content-Length: \(reply.body.utf8.count)\r\n"
            // One request per connection, so a reply can never be read as the
            // answer to the next request.
            answer += "Connection: close\r\n\r\n"
            answer += reply.body
            let bytes = Array(answer.utf8)
            _ = bytes.withUnsafeBufferPointer { send(client, $0.baseAddress, bytes.count, 0) }
            close(client)
        }
    }
}
