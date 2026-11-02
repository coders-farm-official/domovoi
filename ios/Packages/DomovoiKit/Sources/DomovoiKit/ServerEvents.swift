import Foundation

/// One server-sent event: the `event:` name and the `data:` payload.
///
/// `name` is `"message"` for an event that named none, which is what the SSE
/// specification says an unnamed event is called. The server names all of
/// its (`delta`, `done`, `error`, `replace`, `job`, `progress`), so an
/// unnamed one arriving is a server this app does not understand rather than
/// something to guess at.
public struct ServerEvent: Equatable, Sendable {
    public let name: String
    public let data: String

    public init(name: String, data: String) {
        self.name = name
        self.data = data
    }

    /// The payload decoded as `T`, or nil when it is not that shape.
    ///
    /// Nil rather than a throw because **an event this app cannot read must
    /// not end the stream**: the server sends `job` and `progress` events for
    /// features this app has not built, and a reply that stopped halfway
    /// because of one would be a far worse bug than an ignored event.
    public func payload<T: Decodable>(as type: T.Type = T.self) -> T? {
        guard let bytes = data.data(using: .utf8) else { return nil }
        return try? DomovoiClient.decode(bytes, as: T.self)
    }
}

/// The pure half of reading an event stream: lines in, events out.
///
/// Separate from the connection because this is where the mistakes live and
/// a socket cannot be unit-tested. `ChatScreen.kt:370-405` is the same reader
/// in Kotlin, with two differences, both deliberate:
///
///  * **`data:` with no space is accepted**, as the specification requires
///    and as Android does not. A server that trims its padding would hand
///    Android an event whose data is empty.
///  * **Two `data:` lines are joined with a newline**, as the specification
///    says, where Android concatenates them with nothing between. For the
///    one-line JSON this server sends the two are identical; for anything
///    else the spec's answer is the one that parses.
public struct ServerEventReader {

    /// The default name for an event that does not give one.
    public static let unnamed = "message"

    private var name = ServerEventReader.unnamed
    private var data: [String] = []

    public init() {}

    /// Feed one line. Returns an event when that line ended one.
    ///
    /// A blank line ends an event, and **only when something was gathered**:
    /// a stream that opens with a blank line, or sends two in a row, does not
    /// deliver an empty event to a caller that would read it as a reply
    /// saying nothing.
    public mutating func take(_ rawLine: String) -> ServerEvent? {
        // `AsyncLineSequence` splits on the line terminators, but a reader
        // fed from somewhere else may still carry the CR of a CRLF.
        let line = rawLine.hasSuffix("\r") ? String(rawLine.dropLast()) : rawLine

        if line.isEmpty {
            guard !data.isEmpty else {
                // A stray blank line is not an event, but it DOES reset the
                // name: `event: delta` followed by nothing is not a claim
                // about the next payload.
                name = Self.unnamed
                return nil
            }
            let event = ServerEvent(name: name, data: data.joined(separator: "\n"))
            name = Self.unnamed
            data = []
            return event
        }
        // "If the line starts with a U+003A COLON character, ignore the
        // line" — a comment, which is how a server keeps a connection warm
        // through a proxy that times idle ones out.
        if line.hasPrefix(":") { return nil }

        guard let colon = line.firstIndex(of: ":") else {
            // A field with no colon is the field name with an empty value.
            // Nothing this server sends, and ignored rather than guessed at.
            return nil
        }
        let field = String(line[line.startIndex..<colon])
        var value = String(line[line.index(after: colon)...])
        // "If value starts with a U+0020 SPACE character, remove it" — one,
        // not all of them: a delta of "  indented" keeps its indentation.
        if value.hasPrefix(" ") { value.removeFirst() }

        switch field {
        case "event": name = value.trimmingCharacters(in: .whitespaces)
        case "data": data.append(value)
        default: break  // `id` and `retry`, which nothing here reconnects for.
        }
        return nil
    }
}

extension DomovoiClient {

    /// Open a server-sent event stream, through the same gate everything else
    /// goes through.
    ///
    /// The chat reply arrives this way (`POST /api/chat/threads/{id}/messages`,
    /// `chat.py:932`) and it is the only streamed route in the app.
    ///
    /// **It carries the token the same way an ordinary request does**, which
    /// is the whole reason this lives here rather than in the chat code:
    /// ``prepare(_:mode:wsUpgrade:)`` makes the cleartext check, the identity
    /// proof and the first-hop token decision, and the `RedirectDelegate` it
    /// returns decides every hop after that. `URLSession.bytes(for:delegate:)`
    /// takes a per-request delegate exactly as `data(for:delegate:)` does, so
    /// a streamed request is policed identically to a read one — Android gets
    /// the same property a different way, by putting its three interceptors on
    /// the shared `OkHttpClient` its SSE call also uses (`ApiClient.kt:137`).
    ///
    /// The errors of **opening** the stream are thrown here, where a caller
    /// can word them (an `ai-off` refusal, a 404 for a thread somebody else
    /// deleted, a held identity). What goes wrong *after* the first byte
    /// arrives on the wire ends the returned stream instead.
    ///
    /// There is no separate timeout: the session's
    /// `timeoutIntervalForRequest` is the gap between arrivals rather than
    /// the whole journey, so a reply that takes four minutes to write is fine
    /// and one whose model has not said anything for sixty seconds is not —
    /// which is Android's `readTimeout(60)` to the second.
    public func events(
        method: String,
        path: String,
        body: Data? = nil
    ) async throws(DomovoiError) -> AsyncThrowingStream<ServerEvent, any Error> {
        guard !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw DomovoiError.noServer
        }
        guard let url = URL(string: absolute(path)) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let ready = try await prepare(request, mode: .scoped, wsUpgrade: false)
        let bytes: URLSession.AsyncBytes
        let response: URLResponse
        do {
            (bytes, response) = try await session.bytes(for: ready.outgoing, delegate: ready.hops)
        } catch {
            if let refusal = ready.hops.refusal { throw refusal }
            throw Self.transportError(error)
        }
        if let refusal = ready.hops.refusal { throw refusal }
        guard let http = response as? HTTPURLResponse else {
            throw DomovoiError.notDomovoi("the answer was not an HTTP response")
        }

        guard (200...299).contains(http.statusCode) else {
            // The refusal's own words, which for this route are the
            // difference between "AI chat is turned off on your home server"
            // and "409 Conflict". Bounded, because the body of a refusal is
            // a sentence and anything claiming to be larger is not one.
            let text = await Self.firstBytes(of: bytes, limit: 64 * 1024)
            noteTokenHealth(ready, http: http, body: text)
            let pairing = isPairingRefusalFromOurServer(status: http.statusCode, body: text)
            // Through the public door rather than the flag, because only a
            // server that has proved itself may raise the pairing screen and
            // that rule lives in there (A6-03).
            notePossiblePairingRefusal(status: http.statusCode, body: text)
            throw Self.failure(response: http, body: text, deviceTokenRequired: pairing)
        }
        // A 200 says the token was accepted. The body is the reply being
        // written and is not a thing to hold in memory to show the health
        // observer, which only reads a body to explain a refusal.
        noteTokenHealth(ready, http: http, body: "")
        clearPairingRequired()

        return AsyncThrowingStream { continuation in
            let task = Task {
                var reader = ServerEventReader()
                do {
                    for try await line in bytes.lines {
                        if let event = reader.take(line) { continuation.yield(event) }
                    }
                    // A stream that ends without its blank line still ends an
                    // event — the server closing after `data: {...}` with no
                    // trailing newline would otherwise lose the `done` row,
                    // and with it the reply's id and model.
                    if let last = reader.take("") { continuation.yield(last) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: DomovoiClient.transportError(error))
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// At most `limit` bytes of `bytes`, as text.
    ///
    /// Reading a refusal, never a reply: a caller that wanted the whole body
    /// would be using ``raw(method:path:body:contentType:trackPairing:timeout:)``.
    static func firstBytes(of bytes: URLSession.AsyncBytes, limit: Int) async -> String {
        var collected = Data()
        do {
            for try await byte in bytes {
                collected.append(byte)
                if collected.count >= limit { break }
            }
        } catch {
            // Half a refusal explains more than none of one.
        }
        return text(collected)
    }
}
