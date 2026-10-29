import Foundation
import Synchronization

/// Where the active server is and what token this phone holds for it.
///
/// Android passes two closures (`baseUrlProvider`, `tokenForServer`) and the
/// reason the second takes an address rather than answering "the active
/// token" is a security fix: a server switch writes the address first and the
/// token second, and a request intercepted between the two writes would
/// otherwise pair the NEW address with the OLD household's token (A6-01
/// review). The same shape is kept here, for the same reason.
///
/// Both closures are `@Sendable` and are called from whatever thread a
/// request or a redirect is on, so whatever they read has to be safe to read
/// there. The app's will read a `Mutex`-backed settings store (I1-C); a
/// test's reads a box.
public struct ServerBook: Sendable {
    public var activeServer: @Sendable () -> String
    public var tokenForServer: @Sendable (String) -> String?

    public init(
        activeServer: @Sendable @escaping () -> String,
        tokenForServer: @Sendable @escaping (String) -> String?
    ) {
        self.activeServer = activeServer
        self.tokenForServer = tokenForServer
    }

    /// One server, one token, neither changing. The shape Android's own tests
    /// use.
    public static func fixed(server: String, token: String? = nil) -> ServerBook {
        ServerBook(activeServer: { server }, tokenForServer: { _ in token })
    }
}

/// What the server said to a household token checked before it is saved.
public enum TokenCheck: Equatable, Sendable {
    case accepted
    case refused
    case throttled(waitMs: Int64)
    /// 503, no answer, or a server that did not prove itself: not a verdict.
    case unavailable
}

/// The ONE HTTP client the app uses.
///
/// A port of `android/.../net/ApiClient.kt` and the interceptor half of
/// `DeviceAuth.kt`. On Android the point of having one is that media3's data
/// source, Coil's image loads, both WebSockets and every JSON call share a
/// single `OkHttpClient`, so the three rules — follow redirects ourselves,
/// refuse cleartext the policy does not allow, put the household token on
/// what is addressed to the active server and on nothing else — hold for all
/// of them instead of for the call sites somebody remembered.
///
/// **iOS cannot have quite that.** `AVPlayer` and background downloads use
/// the system's own HTTP stacks, where a `URLSession` delegate does not run
/// (plan 1.1 and 1.4). So the rules hold for everything that goes through
/// this type, and the two system stacks are given open URLs only — plan 1.4
/// is the design for that, and ``tokenForDownload(_:)`` is the one place a
/// token is handed out for a request this client does not make.
///
/// ### The token decision, in one place
///
/// Every request — the first hop and every redirect after it — is decided by
/// ``tokenOutcome(for:wsUpgrade:)``, in this order, which is the
/// interceptor's order:
///
///  1. the header a caller set is **stripped**, always, before anything;
///  2. room mode seals the token (``HouseholdLock``);
///  3. ``TokenScope/admits(_:_:wsUpgrade:)`` must say this URL may have it;
///  4. there must be a header-safe token saved for the active server's own
///     spelling;
///  5. ``TokenHealth`` must not have seen that token refused;
///  6. the server must not have asked this phone to wait — and if it has,
///     the request is **not sent at all**;
///  7. the ``TokenGate`` must admit it.
///
/// ### Timeouts
///
/// Android: connect 6 s, read 60 s, write 120 s, no call timeout on the main
/// client, a 15 s WebSocket ping, and short-fused copies for the reachability
/// probe (5 s) and the paste check (8 s).
///
/// `URLSession` does not have those knobs. It has
/// `timeoutIntervalForRequest`, which Apple documents as the time to wait for
/// *new data* to arrive — the nearest thing to OkHttp's read timeout — and
/// `timeoutIntervalForResource`, a ceiling on the whole transfer. There is no
/// connect timeout at all, and nothing distinguishes a write from a read. So:
///
///  * `timeoutIntervalForRequest` = **60 s**, Android's read timeout. A
///    connect that hangs is covered by it rather than by its own 6 s, which
///    is the one real loss — a server that accepts a connection and then says
///    nothing takes ten times as long to give up on as it does on Android.
///    The probes below are what keeps that off the screens that matter.
///  * `timeoutIntervalForResource` is left alone (7 days, Apple's default),
///    matching Android's main client having no call timeout.
///  * The probes set `URLRequest.timeoutInterval` to Android's numbers (5 s
///    and 8 s), which overrides the configuration's per-request timeout for
///    that request. It is not a call timeout — a trickling response could
///    outlive it — but for a probe whose answer is one short JSON body, it is
///    the same thing in practice.
///  * `URLSessionWebSocketTask` has no automatic ping: Apple gives
///    `sendPing(pongReceiveHandler:)` and the app must call it. Android's 15 s
///    ping exists because walking out of Wi-Fi range leaves the state socket
///    half-open with no FIN, so without it the socket reads "live" for ever
///    and the shell never falls back to local media. That ping belongs to the
///    state socket, which is I1-D; this batch only mints its upgrade request.
public final class DomovoiClient: Sendable {

    public let book: ServerBook
    let gate: (any TokenGate)?
    public let health: TokenHealth?
    let lock: HouseholdLock
    let session: URLSession

    /// Flipped when the server refuses this phone for want of the device
    /// token; the shell shows the pairing screen while it is true, and
    /// pairing (or switching server) clears it.
    ///
    /// Android holds it in a `StateFlow` the shell collects. Here it is a
    /// plain value a `Mutex` guards, because the delegate has to be able to
    /// read and write it synchronously; the `@Observable` wrapper the shell
    /// watches is I1-C's, and reads this.
    private let pairing = Mutex<Bool>(false)

    public init(
        book: ServerBook,
        gate: (any TokenGate)? = nil,
        health: TokenHealth? = nil,
        lock: HouseholdLock = .shared,
        configuration: URLSessionConfiguration = DomovoiClient.defaultConfiguration()
    ) {
        self.book = book
        self.gate = gate
        self.health = health
        self.lock = lock
        // No session-wide delegate: every request carries its own, because a
        // redirect decision needs to know which request it belongs to and
        // what that request was allowed to carry. A session delegate would
        // have to look that up from the task.
        self.session = URLSession(configuration: configuration)
    }

    /// See the timeout notes on the type.
    public static func defaultConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 60
        // Android's OkHttpClient has no cookie jar at all — API_REFERENCE
        // says so where it explains that the app is not identified by the
        // `domovoi-device-id` cookie every browser carries. A cookie store
        // here would make this app a different kind of client to the server,
        // and the dashboard's admin cookie is explicitly refused on the
        // device tier, so there is nothing a cookie could usefully carry.
        configuration.httpShouldSetCookies = false
        configuration.httpCookieAcceptPolicy = .never
        configuration.httpCookieStorage = nil
        // OkHttp has no response cache unless one is configured, and
        // Android's is not. A cached 200 for a household read would be a
        // cached answer to a credential question.
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return configuration
    }

    // ---- what the app asks about the server --------------------------------

    public var baseURL: String { book.activeServer() }

    /// The token for the active server (by its address).
    public var deviceToken: String? { book.tokenForServer(baseURL) }

    public var pairingRequired: Bool { pairing.withLock { $0 } }

    public func clearPairingRequired() { pairing.withLock { $0 = false } }

    /// Called from the response path and from the socket listeners, which see
    /// a refusal as a failed upgrade rather than as a body. Only a server
    /// that has proved itself (or has nothing to prove) may raise the pairing
    /// screen: a rogue's 401 must not invite a paste (A6-03).
    public func notePossiblePairingRefusal(status: Int, body: String?) {
        if isPairingRefusalFromOurServer(status: status, body: body) {
            pairing.withLock { $0 = true }
        }
    }

    func isPairingRefusalFromOurServer(status: Int, body: String?) -> Bool {
        guard isDeviceTokenRefusal(status: status, body: body) else { return false }
        guard let gate else { return true }
        return gate.admitsPairingPrompt(TokenScope.baseOf(baseURL))
    }

    public func absolute(_ path: String) -> String {
        if path.hasPrefix("http://") || path.hasPrefix("https://") { return path }
        return baseURL + path
    }

    // ---- the token decision ------------------------------------------------

    /// What a request to `url` may carry, and why not when it may not.
    enum TokenOutcome: Equatable {
        /// Send it bare. Not an error: an unpaired phone, a foreign host, a
        /// refused token, room mode.
        case bare
        /// Send it with this token, which the server at `server` issued.
        case send(token: String, server: String)
        /// Do not send it at all: the server asked this phone to wait.
        case wait(ms: Int64)
        /// Do not send it at all: the gate held it back.
        case held(reason: String)
    }

    func tokenOutcome(for url: ServerURL, wsUpgrade: Bool) -> TokenOutcome {
        // Room mode: the household token goes nowhere (HouseholdLock).
        if lock.roomModeActive { return .bare }
        let raw = book.activeServer()
        guard let base = TokenScope.baseOf(raw) else { return .bare }
        guard TokenScope.admits(base, url, wsUpgrade: wsUpgrade) else { return .bare }
        guard let token = headerSafeToken(book.tokenForServer(raw)) else { return .bare }
        if let health {
            // Refused once: never again, until a new token (or the person)
            // says otherwise. Without this the request is an unpaired
            // phone's, which the server's wrong-token count never sees.
            if health.isRejected(raw) { return .bare }
            let wait = health.waitMs(raw)
            if wait > 0 { return .wait(ms: wait) }
        }
        if let gate {
            do { try gate.requireAdmitted(base) }
            catch { return .held(reason: (error as? DomovoiError)?.message ?? "\(error)") }
        }
        return .send(token: token, server: raw)
    }

    // ---- making a request --------------------------------------------------

    /// Whether and how a request may carry the token.
    enum TokenMode: Sendable {
        /// The rule above.
        case scoped
        /// Never — a discovery probe, the reachability probe.
        case none
        /// Exactly this value on the FIRST hop and nothing on any hop after
        /// it: the paste check, which carries a candidate the vault has not
        /// accepted yet. Android gets this by taking the auth interceptor off
        /// a copy of the client and setting the header on the request, which
        /// `RedirectPolicy` then strips from any follow-up.
        case candidate(String)
    }

    /// The whole of a request's journey: the cleartext rule, the token, the
    /// redirect chain and the error mapping.
    ///
    /// Returns the body, the response, and the token the FIRST hop carried
    /// (nil when it carried none), which is what ``TokenHealth`` needs to
    /// know whether an answer is about a token this phone sent.
    func perform(
        _ request: URLRequest,
        mode: TokenMode = .scoped,
        wsUpgrade: Bool = false
    ) async throws(DomovoiError) -> (body: Data, response: HTTPURLResponse, sentToken: String?) {
        guard let url = request.url, let parsed = ServerURL.parse(url.absoluteString) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        // Before any connection is attempted. ATS is configured to allow more
        // than we want — `NSAllowsLocalNetworking` admits every IP address,
        // public ones included — so this check, not ATS, is the real one,
        // exactly as on Android where the platform's
        // network_security_config.xml cannot say "any RFC 1918 address".
        guard CleartextPolicy.permits(parsed) else {
            throw DomovoiError.notPermitted(CleartextPolicy.refusalMessage(parsed.host))
        }

        // Whatever a caller set, the decision is made here.
        var outgoing = request
        outgoing.setValue(nil, forHTTPHeaderField: deviceTokenHeader)
        var sentToken: String?
        var tokenServer: String?
        switch mode {
        case .none:
            break
        case .candidate(let candidate):
            outgoing.setValue(candidate, forHTTPHeaderField: deviceTokenHeader)
            sentToken = candidate
        case .scoped:
            switch tokenOutcome(for: parsed, wsUpgrade: wsUpgrade) {
            case .bare:
                break
            case .send(let token, let server):
                outgoing.setValue(token, forHTTPHeaderField: deviceTokenHeader)
                sentToken = token
                tokenServer = server
            case .wait(let ms):
                throw DomovoiError.tokenThrottled(waitMs: ms)
            case .held(let reason):
                throw DomovoiError.notPermitted(reason)
            }
        }

        let hops = RedirectDelegate(
            start: outgoing,
            wsUpgrade: wsUpgrade,
            // A hop decides the token afresh, by the scope rule, whatever the
            // first hop was allowed to carry. A candidate never rides along.
            decide: { [weak self] url in
                guard let self else { return .bare }
                return tokenOutcome(for: url, wsUpgrade: false)
            }
        )
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: outgoing, delegate: hops)
        } catch {
            // A refusal recorded by the delegate explains the failure better
            // than whatever URLSession turned it into, so it wins.
            if let refusal = hops.refusal { throw refusal }
            throw Self.transportError(error)
        }
        if let refusal = hops.refusal { throw refusal }
        guard let http = response as? HTTPURLResponse else {
            // Not reachable over http(s), where every response is an
            // HTTPURLResponse; it is here because the type system cannot say
            // so and because a wrong answer to "did the server answer?" is
            // the kind of thing this client must not guess at.
            throw DomovoiError.notDomovoi("the answer was not an HTTP response")
        }

        if let health, let sentToken, let tokenServer,
           let base = TokenScope.baseOf(tokenServer),
           TokenScope.sameServer(base, parsed) {
            // Only the server's own answers (its scheme, host and port) speak
            // for its token; the drop-in socket on the core's port keeps its
            // own count.
            health.observe(
                url: tokenServer,
                sent: sentToken,
                current: book.tokenForServer(tokenServer),
                status: http.statusCode,
                body: Self.text(data),
                retryAfter: http.value(forHTTPHeaderField: "Retry-After")
            )
        }
        return (data, http, sentToken)
    }

    /// Android's `raw`: the body as text, or the server's error as an error.
    ///
    /// `trackPairing` false means the answer neither raises nor clears the
    /// pairing screen — a background read that falls back to an OPEN endpoint
    /// would otherwise clear the pairing screen with its 200 the moment its
    /// household read had raised it with a 401, and the shell would flicker
    /// between the two.
    @discardableResult
    public func raw(
        method: String,
        path: String,
        body: Data? = nil,
        contentType: String? = nil,
        trackPairing: Bool = true,
        timeout: TimeInterval? = nil
    ) async throws(DomovoiError) -> Data {
        guard !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw DomovoiError.noServer
        }
        guard let url = URL(string: absolute(path)) else {
            throw DomovoiError.notPermitted("that is not a server address")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        // Makes every call a preflighted one: a multipart or body-less POST
        // would otherwise be a CORS simple request the server can't refuse
        // before the side effect lands (WEB-6). The dashboard sends the same
        // header.
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        if let body {
            request.httpBody = body
            request.setValue(contentType ?? "application/json", forHTTPHeaderField: "Content-Type")
        }
        if let timeout { request.timeoutInterval = timeout }

        let (data, response, _) = try await perform(request)
        guard (200...299).contains(response.statusCode) else {
            let text = Self.text(data)
            let pairing = isPairingRefusalFromOurServer(status: response.statusCode, body: text)
            if pairing && trackPairing { self.pairing.withLock { $0 = true } }
            throw Self.failure(response: response, body: text, deviceTokenRequired: pairing)
        }
        if trackPairing { pairing.withLock { $0 = false } }
        return data
    }

    // ---- the JSON verbs ----------------------------------------------------

    public func get<T: Decodable>(_ path: String, as type: T.Type = T.self) async throws(DomovoiError) -> T {
        try Self.decode(try await raw(method: "GET", path: path), as: type)
    }

    /// A GET whose answer says nothing about pairing. See ``raw``.
    public func getUntracked<T: Decodable>(_ path: String, as type: T.Type = T.self) async throws(DomovoiError) -> T {
        try Self.decode(try await raw(method: "GET", path: path, trackPairing: false), as: type)
    }

    public func post<T: Decodable>(_ path: String, body: Data? = nil, as type: T.Type = T.self) async throws(DomovoiError) -> T {
        try Self.decode(try await raw(method: "POST", path: path, body: body ?? Self.emptyObject), as: type)
    }

    public func patch<T: Decodable>(_ path: String, body: Data? = nil, as type: T.Type = T.self) async throws(DomovoiError) -> T {
        try Self.decode(try await raw(method: "PATCH", path: path, body: body ?? Self.emptyObject), as: type)
    }

    public func put<T: Decodable>(_ path: String, body: Data? = nil, as type: T.Type = T.self) async throws(DomovoiError) -> T {
        try Self.decode(try await raw(method: "PUT", path: path, body: body ?? Self.emptyObject), as: type)
    }

    /// Android's `delete` sends a body only when it is given one.
    public func delete<T: Decodable>(_ path: String, body: Data? = nil, as type: T.Type = T.self) async throws(DomovoiError) -> T {
        try Self.decode(try await raw(method: "DELETE", path: path, body: body), as: type)
    }

    /// A call whose answer is not read. Android's verbs all parse; a Swift
    /// caller that wants nothing back should not have to name a type.
    public func send(method: String, path: String, body: Data? = nil) async throws(DomovoiError) {
        _ = try await raw(method: method, path: path, body: body)
    }

    static let emptyObject = Data("{}".utf8)

    /// Kotlin's `parse`: a blank body is JSON null, which decodes into an
    /// optional and fails anything else.
    static func decode<T: Decodable>(_ data: Data, as type: T.Type) throws(DomovoiError) -> T {
        let bytes = Self.text(data).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? Data("null".utf8)
            : data
        do {
            return try JSONDecoder().decode(type, from: bytes)
        } catch {
            // The address answered with a 2xx and this is not what a Domovoi
            // server sends: a router's login page, a captive portal, another
            // service on the port.
            throw DomovoiError.notDomovoi(Self.notDomovoiReason(error))
        }
    }

    static func notDomovoiReason(_ error: any Error) -> String {
        guard let decoding = error as? DecodingError else { return "that isn't Domovoi" }
        switch decoding {
        case .keyNotFound(let key, _):
            return "that isn't Domovoi: the answer has no \"\(key.stringValue)\""
        case .typeMismatch, .valueNotFound, .dataCorrupted:
            return "that isn't Domovoi: the answer is not what this app understands"
        @unknown default:
            return "that isn't Domovoi"
        }
    }

    // ---- probes ------------------------------------------------------------

    /// Whether the server answers at all.
    ///
    /// ANY HTTP status counts — a phone that is not paired yet has its live
    /// socket refused (403), and that server is plainly there — so only a
    /// transport failure means "out of reach". A cleartext refusal is a
    /// setting to fix, not an outage, so it counts as an answer too and the
    /// workspace stays up to say so. So does a server that asked this phone
    /// to wait: it answered, and the shell shows the wait.
    public func answers() async -> Bool {
        let base = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !base.isEmpty else { return false }
        guard let url = URL(string: base + "/api/health") else { return false }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        do {
            _ = try await perform(request)
            return true
        } catch DomovoiError.notPermitted {
            return true
        } catch DomovoiError.tokenThrottled {
            return true
        } catch {
            return false
        }
    }

    /// A device-tier read (the satellites list) any household token may make.
    static let tokenCheckPath = "/api/satellites"

    /// Whether the active server accepts `candidate` as the household token,
    /// asked ONCE before it is saved (the pairing screen, Settings →
    /// Connection, the refused-token card).
    ///
    /// One device-tier read carrying the candidate and nothing else. A wrong
    /// paste costs one wrong token on the server's count, as it must; nothing
    /// here retries. The candidate goes only to the active server, after the
    /// identity gate admits it, and not at all while the server makes this
    /// address wait.
    public func checkDeviceToken(_ candidate: String) async -> TokenCheck {
        if lock.roomModeActive { return .unavailable }
        let raw = baseURL
        guard let base = TokenScope.baseOf(raw) else { return .unavailable }
        guard let token = headerSafeToken(candidate) else { return .refused }
        if let health, health.waitMs(raw) > 0 { return .throttled(waitMs: health.waitMs(raw)) }
        if let gate {
            do { try gate.requireAdmitted(base) } catch { return .unavailable }
        }
        guard let url = URL(string: "\(base.scheme)://\(hostForURL(base.host)):\(base.port)\(Self.tokenCheckPath)") else {
            return .unavailable
        }
        var request = URLRequest(url: url)
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        request.timeoutInterval = 8
        do {
            let (data, response, _) = try await perform(request, mode: .candidate(token))
            let body = Self.text(data)
            if (200...299).contains(response.statusCode) || response.statusCode == 404 {
                // 404: an older server with no satellites route still proved
                // it would answer this phone's token rather than refuse it.
                return .accepted
            }
            if isServerDeviceRefusal(status: response.statusCode, body: body) { return .refused }
            if response.statusCode == 429, TokenHealth.isDeviceTokenThrottle(body) {
                let wait = TokenHealth.retryAfterMs(response.value(forHTTPHeaderField: "Retry-After"))
                health?.noteWait(raw, waitMs: wait)
                return .throttled(waitMs: wait)
            }
            return .unavailable
        } catch {
            return .unavailable
        }
    }

    /// The token a request to `url` made OUTSIDE this client may carry — the
    /// one save-to-device route on the device tier.
    ///
    /// The same rule the request path applies: the active server itself, not
    /// in room mode, not a refused token, nothing while the server says wait,
    /// and the gate consulted. Nil when `url` is not the active server or the
    /// phone is not paired; throws when the server asked for a wait or the
    /// gate held it, so the caller can say so rather than queue a request
    /// that would carry the token and fail.
    ///
    /// Android's version suspends because its gate takes a fresh network
    /// proof on the spot. This one does not, because the gate here is
    /// synchronous (see ``TokenGate``) — if I1-B needs to take a proof at
    /// this point, this is a function that will have to grow an `await`.
    public func tokenForDownload(_ url: URL) throws(DomovoiError) -> String? {
        guard let parsed = ServerURL.parse(url.absoluteString) else { return nil }
        switch tokenOutcome(for: parsed, wsUpgrade: false) {
        case .bare: return nil
        case .send(let token, _): return token
        case .wait(let ms): throw DomovoiError.tokenThrottled(waitMs: ms)
        case .held(let reason): throw DomovoiError.notPermitted(reason)
        }
    }

    /// A WebSocket upgrade request carrying this phone's household token.
    ///
    /// Used by the state socket (`/ws/state`) and the drop-in call socket
    /// (`/v1/dropin/{room}`), both of which are later batches. Android marks
    /// the upgrades it builds itself with an OkHttp request tag so its
    /// interceptor can tell them from a URL some library was handed; here the
    /// marking is simply that this method is the only thing that builds one,
    /// and it is the only caller that passes `wsUpgrade: true`.
    ///
    /// The scope rule still decides: an upgrade to another host gets nothing,
    /// an upgrade to another port on the active server's host keeps the token
    /// (the drop-in socket to the core's own port), and room mode seals it.
    public func webSocketRequest(_ url: String) -> URLRequest? {
        guard let parsed = ServerURL.parseAllowingWebSocket(url), let target = URL(string: url) else { return nil }
        guard CleartextPolicy.permits(parsed) else { return nil }
        var request = URLRequest(url: target)
        if case .send(let token, _) = tokenOutcome(for: parsed, wsUpgrade: true) {
            request.setValue(token, forHTTPHeaderField: deviceTokenHeader)
        }
        return request
    }

    // ---- turning answers into errors ---------------------------------------

    /// Up to 4,096 characters of a body, read as UTF-8 and, failing that, as
    /// Latin-1 so a page of bytes that is not UTF-8 still produces something
    /// a refusal check can look at rather than an empty string.
    static func text(_ data: Data) -> String {
        let text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        return String(text.prefix(4096))
    }

    static func failure(response: HTTPURLResponse, body: String, deviceTokenRequired: Bool) -> DomovoiError {
        let status = response.statusCode
        let failure = ApiFailure(
            status: status,
            // See ApiFailure.message: there is no reason phrase to put here.
            message: "\(status): \(body.prefix(200))",
            deviceTokenRequired: deviceTokenRequired,
            body: body,
            refusal: response.value(forHTTPHeaderField: refusalHeader)
        )
        switch status {
        case 401, 403:
            return .refused(failure)
        case 429:
            return .rateLimited(failure, waitMs: TokenHealth.retryAfterMs(response.value(forHTTPHeaderField: "Retry-After")))
        case 500...599:
            return .serverTrouble(failure)
        default:
            return .httpError(failure)
        }
    }

    /// A `URLError` in the shapes a person can be told about.
    ///
    /// `.appTransportSecurityRequiresSecureConnection` is ATS refusing a
    /// cleartext load the app's own ``CleartextPolicy`` let through — which
    /// should not happen, because the policy is the stricter of the two, but
    /// if it does it is a setting to correct and not an outage, so it maps
    /// the same way a policy refusal does.
    static func transportError(_ error: any Error) -> DomovoiError {
        guard let url = error as? URLError else { return .unreachable("\(error)") }
        switch url.code {
        case .appTransportSecurityRequiresSecureConnection:
            return .notPermitted("this address needs https")
        case .httpTooManyRedirects:
            // Measured on 2026-10-28: `URLSession` follows at most 20
            // redirects and then reports this — the same ceiling
            // ``RedirectPolicy/maxHops`` names, so the delegate's own counter
            // (which fires at 21) is belt and braces the platform never lets
            // us need. Android throws `ProtocolException` here, an
            // IOException, whose toast reads "(offline?)", so mapping this to
            // `.unreachable` is what keeps the two apps saying the same
            // thing about the same chain.
            return .unreachable("too many redirects")
        case .cancelled:
            return .unreachable("cancelled")
        default:
            return .unreachable(url.localizedDescription)
        }
    }

    /// An IPv6 literal goes back into a URL in brackets.
    private func hostForURL(_ host: String) -> String {
        host.contains(":") ? "[\(host)]" : host
    }
}

/// The redirect half of the client, as one request's delegate.
///
/// Why a class the compiler cannot check for me: `URLSessionTaskDelegate`
/// refines `URLSessionDelegate`, which refines `NSObjectProtocol`, so a
/// delegate must be an `NSObject` subclass, and `NSObject` is not `Sendable`.
/// There is no way to write a `URLSession` redirect delegate without this.
/// The `@unchecked` claim is sound here and that is checkable by reading the
/// stored properties: all four are `let`, three hold `Sendable` values, and
/// the fourth is a `Mutex`, which exists for exactly this. Nothing mutable is
/// reachable from here without going through that lock.
final class RedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {

    private struct State {
        var current: URLRequest
        var hops = 0
        var refusal: DomovoiError?
    }

    private let state: Mutex<State>
    private let wsUpgrade: Bool
    private let decide: @Sendable (ServerURL) -> DomovoiClient.TokenOutcome

    init(
        start: URLRequest,
        wsUpgrade: Bool,
        decide: @Sendable @escaping (ServerURL) -> DomovoiClient.TokenOutcome
    ) {
        state = Mutex(State(current: start))
        self.wsUpgrade = wsUpgrade
        self.decide = decide
        super.init()
    }

    /// Why the chain was stopped, or nil when it was not.
    var refusal: DomovoiError? { state.withLock { $0.refusal } }

    /// How many hops were taken. Read by the tests.
    var hops: Int { state.withLock { $0.hops } }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(followUp(response: response))
    }

    /// The decision, apart from the callback, so a test can drive it without
    /// a live task.
    ///
    /// **About sockets, measured rather than assumed** (the brief's open
    /// question; `PlatformFactsTests` holds the measurement). A
    /// `URLSessionWebSocketTask` goes through the same machinery as any other
    /// task: its upgrade is sent as the `http://` GET it starts as, and when
    /// that is answered with a `302` this delegate method IS called. So iOS
    /// can refuse a socket redirect, and `isWebSocketUpgrade` does — the
    /// socket lands where it was aimed or it fails, with nothing sent to the
    /// host the `Location` named. (For completeness: I1-D has to put one of
    /// these on the socket task it creates — `task.delegate` — or the
    /// platform will follow that redirect on its own.)
    ///
    /// **What the platform hands us is a trap.** `newRequest` is a *proposed*
    /// request that has already copied this request's headers onto the new
    /// URL — `X-Device-Token` among them — so it is ignored entirely. The
    /// follow-up is built from the request we sent, by ``RedirectPolicy``,
    /// which strips the token header; then the hop's own URL is run through
    /// the cleartext rule and the scope rule, in that order, exactly as
    /// Android's interceptor stack does it for every hop.
    func followUp(response: HTTPURLResponse) -> URLRequest? {
        state.withLock { state -> URLRequest? in
            guard state.refusal == nil else { return nil }
            guard var next = RedirectPolicy.followUp(
                state.current,
                status: response.statusCode,
                location: response.value(forHTTPHeaderField: "Location"),
                isWebSocketUpgrade: wsUpgrade
            ) else { return nil }

            state.hops += 1
            if state.hops > RedirectPolicy.maxHops {
                state.refusal = .notPermitted("too many redirects: \(state.hops)")
                return nil
            }
            guard let url = next.url, let parsed = ServerURL.parse(url.absoluteString) else {
                state.refusal = .notPermitted("that is not a server address")
                return nil
            }
            // The cleartext rule for THIS hop, before it is connected. The
            // reason Android turns OkHttp's own follower off is that its
            // follower would have skipped this.
            guard CleartextPolicy.permits(parsed) else {
                state.refusal = .notPermitted(CleartextPolicy.refusalMessage(parsed.host))
                return nil
            }
            // And the token for THIS hop's URL: the active server keeps it,
            // anywhere else loses it. `RedirectPolicy` has already removed
            // the header, so doing nothing here is "bare".
            switch decide(parsed) {
            case .bare:
                break
            case .send(let token, _):
                next.setValue(token, forHTTPHeaderField: deviceTokenHeader)
            case .wait(let ms):
                state.refusal = .tokenThrottled(waitMs: ms)
                return nil
            case .held(let reason):
                state.refusal = .notPermitted(reason)
                return nil
            }
            state.current = next
            return next
        }
    }
}
