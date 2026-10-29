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
///  * **The gate's probe client gets a shorter one.**
///    ``graph(book:health:lock:configuration:gate:)`` copies this
///    configuration for it and sets `timeoutIntervalForRequest` to
///    ``IdentityGate/probeTimeout`` (shortening only, never lengthening),
///    which is Android's `Discovery.client(http, timeoutMs)`. Every probe
///    sets its own `URLRequest.timeoutInterval` as well, so this is the floor
///    under a caller that forgets rather than something relied on.
///  * `URLSessionWebSocketTask` has no automatic ping: Apple gives
///    `sendPing(pongReceiveHandler:)` and the app must call it. Android's 15 s
///    ping exists because walking out of Wi-Fi range leaves the state socket
///    half-open with no FIN, so without it the socket reads "live" for ever
///    and the shell never falls back to local media. That ping belongs to the
///    state socket, which is I1-D; this batch only mints its upgrade request.
public final class DomovoiClient: Sendable {

    public let book: ServerBook
    /// Not optional, and the initialiser gives it no default: see
    /// ``TokenGate``. A client with no identity rules says so with
    /// ``AdmitsNoServer``, which refuses, rather than by leaving the argument
    /// out, which used to admit.
    let gate: any TokenGate
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

    /// **Internal, so that ``graph(book:health:lock:configuration:gate:)`` is
    /// the only way to build one of these from outside the module.**
    ///
    /// Part 2 closed the fail-open — ``gate`` is non-optional with no default,
    /// so a client cannot be built without saying which identity rules apply.
    /// What it left open was the *capability*: this initialiser was `public`
    /// and ``TokenGate`` is a `public` protocol whose `admitsPairingPrompt`
    /// and `prepare` have permissive and no-op default bodies, so three lines
    /// in the app target — a type conforming with `requireAdmitted` doing
    /// nothing — were a client that puts the household token on any address.
    /// A6-03 by a different route.
    ///
    /// Nothing legitimate wanted it: ``AppContainer`` is the production path
    /// and is in this module, and every other call is a test with
    /// `@testable import` (which reaches an internal initialiser). The one
    /// public constructor left is `graph`, which hands the probe client
    /// ``AdmitsNoServer`` itself and takes the gate as a closure it calls once.
    init(
        book: ServerBook,
        gate: any TokenGate,
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

    /// The decision as far as the gate, and no further.
    ///
    /// Split out because the gate is asked in two places that cannot share
    /// one call: ``prepareIdentity(for:wsUpgrade:)``, which is `async` and
    /// takes the proof, and ``tokenOutcome(for:wsUpgrade:)``, which is
    /// synchronous and reads it. Both have to apply the five rules in front
    /// of the gate, in the same order, and there is one copy of them here.
    ///
    /// **Why "send it bare" is two cases.** The rules refuse the token for
    /// two quite different kinds of reason, and only the first means "ask
    /// this server nothing":
    ///
    ///  * ``bare`` — room mode (``HouseholdLock``'s seal), a host that is not
    ///    the active server, an address that does not parse. Nothing about
    ///    this server's identity is any of this phone's business right now.
    ///  * ``unpaired(base:)`` — this request IS aimed at the active server
    ///    and the only reason the token is not going is that this phone has
    ///    not been given one yet, or the one it has was refused. The proof is
    ///    still taken, because the answer to "may this server's 401 ask for a
    ///    paste?" is read from the cache and nothing else fills it.
    enum PreGate: Equatable {
        /// Send it bare and ask nobody anything.
        case bare

        /// Send it bare, and still take the proof: see above. `base` is the
        /// active server.
        case unpaired(base: ServerURL)

        case wait(ms: Int64)

        /// The rules so far admit it; the gate decides. `base` is the active
        /// server, which is what the gate is asked about — never the hop's
        /// own URL, which may be another port on the same host.
        case ask(base: ServerURL, token: String, server: String)
    }

    func tokenDecision(for url: ServerURL, wsUpgrade: Bool) -> PreGate {
        // Room mode: the household token goes nowhere (HouseholdLock), and a
        // room asks nobody about the household's server either.
        if lock.roomModeActive { return .bare }
        let raw = book.activeServer()
        guard let base = TokenScope.baseOf(raw) else { return .bare }
        guard TokenScope.admits(base, url, wsUpgrade: wsUpgrade) else { return .bare }
        // No token yet: an unpaired phone, which is the state the whole
        // pairing path starts from.
        guard let token = headerSafeToken(book.tokenForServer(raw)) else { return .unpaired(base: base) }
        if let health {
            // Refused once: never again, until a new token (or the person)
            // says otherwise. Without this the request is an unpaired
            // phone's, which the server's wrong-token count never sees — and
            // it is an unpaired phone's in the other sense too, so the server
            // still gets to prove itself and ask for a fresh paste.
            if health.isRejected(raw) { return .unpaired(base: base) }
            let wait = health.waitMs(raw)
            if wait > 0 { return .wait(ms: wait) }
        }
        return .ask(base: base, token: token, server: raw)
    }

    func tokenOutcome(for url: ServerURL, wsUpgrade: Bool) -> TokenOutcome {
        switch tokenDecision(for: url, wsUpgrade: wsUpgrade) {
        case .bare, .unpaired:
            return .bare
        case .wait(let ms):
            return .wait(ms: ms)
        case .ask(let base, let token, let server):
            do { try gate.requireAdmitted(base) }
            catch { return .held(reason: (error as? DomovoiError)?.message ?? "\(error)") }
            return .send(token: token, server: server)
        }
    }

    /// Take or refresh the identity proof this request will need, before it
    /// is sent.
    ///
    /// The `async` half of the gate's split (see ``TokenGate``): the proof is
    /// a network call, the per-hop reader is a synchronous platform callback,
    /// so the call happens here and the callback reads what it left. One call
    /// covers a whole redirect chain that stays within the active server,
    /// because every hop of it asks about the same `base` — and a chain that
    /// does not is divergence 9, at the bottom of this comment.
    ///
    /// It asks nothing when the request has nothing to do with the active
    /// server's identity — room mode, a foreign host, an address that does
    /// not parse, or a server that has asked this phone to wait — so a
    /// sweep, a radio stream and a room probe no server about its identity.
    ///
    /// **It DOES take the proof when the phone is unpaired** (``PreGate``
    /// explains, and it is the review's blocker A). An unpaired phone sends
    /// everything bare, so the token decision never reaches the gate; if the
    /// proof were skipped for that reason the cache would stay empty for
    /// ever, and ``IdentityGate/admitsPairingPrompt(_:)`` — which can only
    /// read the cache — would answer no to every device-tier `401` from the
    /// real server. The pairing screen would be unreachable (map §8 step 5),
    /// and so would re-pairing after a refused token. Android gets there
    /// because its `admitsPairingPrompt` is allowed to probe on the spot
    /// (`IdentityGate.kt:232-235`); the synchronous reader here cannot, so
    /// the proof is taken in front of the request instead. That costs one
    /// token-less `GET /api/health` per network per ten minutes on a phone
    /// that is not paired yet, which is the phone most likely to want one.
    ///
    /// A **wait** takes nothing, which is the one place this is deliberately
    /// stricter than the Kotlin: Android's interceptor throws
    /// `TokenThrottledException` before it reaches the gate
    /// (`DeviceAuth.kt:118-120`), but its `admitsPairingPrompt` does not
    /// consult `TokenHealth` at all, so a refusal arriving while the phone is
    /// throttled would still be probed there. Nothing this phone sends can
    /// produce such a refusal — every request throws before it leaves — and
    /// probing a server that has just asked this phone to stop asking is the
    /// wrong instinct, so the cache stays cold until the wait is over.
    ///
    /// **It covers the whole redirect chain only when the chain starts on the
    /// active server**, which is divergence 9: a first hop to a foreign host
    /// takes no proof (correctly — it is not our server's business), so if
    /// that host redirects ONTO the active server, the hop asks, finds
    /// nothing and is held. Android's per-hop interceptor would probe and
    /// send the token. iOS is the stricter of the two, which is the safe
    /// direction for a redirect this app did not choose.
    func prepareIdentity(for url: ServerURL, wsUpgrade: Bool) async {
        switch tokenDecision(for: url, wsUpgrade: wsUpgrade) {
        case .ask(let base, _, _), .unpaired(let base):
            await gate.prepare(base)
        case .bare, .wait:
            return
        }
    }

    /// ``prepareIdentity(for:wsUpgrade:)`` for a caller that is about to ask
    /// a **synchronous** question about `address`: ``tokenForDownload(_:)``
    /// and ``SocketUpgrade/mint()``, neither of which can take a proof of its
    /// own.
    ///
    /// I1-D must `await` this immediately before each `mint()`, and I1-C
    /// before handing a URL to the system downloader. Without it the first
    /// such call after a network change is refused — correctly, and
    /// needlessly.
    ///
    /// **`wsUpgrade` has no default on purpose.** It did, and the default was
    /// `false`, which is wrong for the caller most likely to take it: the
    /// drop-in call socket. A socket prepared with `false` fails
    /// ``TokenScope/admits(_:_:wsUpgrade:)`` — the socket runs on the core's
    /// own port, which the scope admits only for an upgrade — so the decision
    /// is bare, no proof is taken, `mint()` then returns nil and the socket
    /// never opens, with nothing anywhere saying why. Every caller says which
    /// it is.
    public func prepareIdentity(forAddress address: String, wsUpgrade: Bool) async {
        guard let parsed = ServerURL.parseAllowingWebSocket(address) else { return }
        await prepareIdentity(for: parsed, wsUpgrade: wsUpgrade)
    }

    // ---- making a request --------------------------------------------------

    /// Whether and how a request may carry the token.
    enum TokenMode: Sendable {
        /// The rule above.
        case scoped
        /// Never, on any hop — I1-B's /24 discovery sweep, which must send
        /// nothing to 254 strangers (finding A6-02, the thing Android fixed
        /// on 2026-10-08 by taking `DeviceAuthInterceptor` off its probe
        /// client).
        ///
        /// **Not the reachability probe.** ``answers()`` uses `.scoped` and
        /// carries the token when the scope admits it, because Android's
        /// `answers()` runs on `probeHttp` — a short-fused copy of the main
        /// client with every interceptor still on it — and because the shell
        /// reads a 401 from that probe as "this phone is not paired".
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
            // The proof, taken here because this is the last place that can
            // `await` before the task — and before the token decision, which
            // only reads it. See ``TokenGate``.
            await prepareIdentity(for: parsed, wsUpgrade: wsUpgrade)
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
                throw DomovoiError.identityHeld(reason)
            }
        }

        // What a HOP may carry, which is **not always the scope rule**: it is
        // the same `mode`, asked again for the hop's own URL.
        //
        // `.scoped` decides afresh, so a redirect within the active server
        // keeps the token and a redirect anywhere else loses it. `.candidate`
        // and `.none` carry nothing on any hop, and that is the whole point of
        // them:
        //
        //  * `.candidate` is the paste check. On Android it runs on a client
        //    with `DeviceAuthInterceptor` removed, so a hop carries nothing at
        //    all. Running the scope rule on hop 2 instead would re-acquire the
        //    SAVED token — so a wrong paste plus a `302` (a reverse proxy, or
        //    FastAPI's `redirect_slashes`) would answer 200 to the valid token
        //    and report `.accepted` for the wrong paste. That is exactly the
        //    incident of 2026-10-17 the paste check was added to prevent.
        //  * `.none` is the sweep. "The sweep sends no token" has to mean no
        //    hop either, or finding A6-02 comes back through a redirect.
        let decide: @Sendable (ServerURL) -> TokenOutcome
        switch mode {
        case .scoped:
            decide = { [weak self] url in
                guard let self else { return .bare }
                return tokenOutcome(for: url, wsUpgrade: false)
            }
        case .candidate, .none:
            decide = { _ in .bare }
        }
        let hops = RedirectDelegate(start: outgoing, wsUpgrade: wsUpgrade, decide: decide)
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

        // Only the server's own answers (its scheme, host and port) speak for
        // its token; the drop-in socket on the core's port keeps its own
        // count.
        //
        // **Gated on the URL that answered, not on the URL we asked.**
        // Android's interceptor runs per hop, so the scope check it makes
        // always concerns the response it is looking at. Here the response is
        // the LAST hop's and `parsed` is the FIRST hop's, so checking `parsed`
        // would have let a foreign host's verdict be filed against the active
        // server: the active server redirects off-origin, hop 2 correctly goes
        // out bare, the stranger answers `401 {"detail":"X-Device-Token or
        // admin session required"}` — and this phone's token would be marked
        // permanently rejected for the ACTIVE server on the stranger's say-so,
        // sending every request bare for good under "Domovoi doesn't recognise
        // this phone any more".
        //
        // A redirect WITHIN the active server still teaches, as on Android:
        // the hop carried the token (`decide` put it back) and the origin that
        // answered is the one that issued it.
        if let health, let sentToken, let tokenServer,
           let base = TokenScope.baseOf(tokenServer),
           let answered = http.url.flatMap({ ServerURL.parse($0.absoluteString) }),
           TokenScope.sameServer(base, answered) {
            health.observe(
                url: tokenServer,
                sent: sentToken,
                // `headerSafeToken`, as `DeviceAuth.kt:125` does, so the two
                // call sites read identically: the question `observe` asks is
                // whether the token held NOW is the one that was sent, and the
                // one that could be sent is the header-safe one.
                current: headerSafeToken(book.tokenForServer(tokenServer)),
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
    ///
    /// **The identity gate's hold is NOT an answer**, which is why it has a
    /// case of its own (``DomovoiError/identityHeld(_:)``) rather than sharing
    /// `.notPermitted` with the cleartext refusal. Android splits them by
    /// type: `UnknownServiceException` (cleartext) is caught as true, and
    /// `ServerIdentityException` is a plain `IOException` and so is false. A
    /// server in the identity `Mismatch` state must read the same on both
    /// phones — it is the one case the gate exists for.
    ///
    /// Two shapes of `.notPermitted` do still count as an answer, and
    /// deliberately: a redirect this app refused to follow (the server
    /// answered, we declined), and an active-server address this app cannot
    /// parse (where Android's `Request.Builder.url` throws
    /// `IllegalArgumentException` out of `answers()` altogether, so there is
    /// no behaviour to match — and no shell can reach that server anyway).
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
        if let health {
            let wait = health.waitMs(raw)
            if wait > 0 { return .throttled(waitMs: wait) }
        }
        // Android asks the gate here unconditionally — not through the token
        // decision, because the candidate is not the saved token and none of
        // the five rules in front of the gate is about it. So the proof is
        // taken here too, directly, rather than through `prepareIdentity`:
        // this call is not a request whose scope the decision can judge, and
        // the room-mode and wait rules it does share are applied above, in
        // this method's own words.
        //
        // (Since the review's fix A, `prepareIdentity` would also take the
        // proof for a phone with no saved token, so this is no longer the
        // only way to a warm cache on the pairing screen. It stays explicit
        // because this method must take it whether or not there is a token,
        // which is a stronger rule than the decision's.)
        await gate.prepare(base)
        do { try gate.requireAdmitted(base) } catch { return .unavailable }
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
    /// synchronous (see ``TokenGate``): **the caller must
    /// `await prepareIdentity(forAddress:)` first**, or the first call after
    /// a network change throws ``DomovoiError/identityHeld(_:)`` — which is
    /// the right answer to "is this proved?" and the wrong answer to "may
    /// this download go ahead?".
    public func tokenForDownload(_ url: URL) throws(DomovoiError) -> String? {
        guard let parsed = ServerURL.parse(url.absoluteString) else { return nil }
        switch tokenOutcome(for: parsed, wsUpgrade: false) {
        case .bare: return nil
        case .send(let token, _): return token
        case .wait(let ms): throw DomovoiError.tokenThrottled(waitMs: ms)
        case .held(let reason): throw DomovoiError.identityHeld(reason)
        }
    }

    /// A WebSocket upgrade this phone may open, which decides what it carries
    /// at **connect** time.
    ///
    /// Used by the state socket (`/ws/state`) and the drop-in call socket
    /// (`/v1/dropin/{room}`), both of which are later batches. Android marks
    /// the upgrades it builds itself with an OkHttp request tag so its
    /// interceptor can tell them from a URL some library was handed; here the
    /// marking is simply that this method is the only thing that builds one,
    /// and it is the only caller that passes `wsUpgrade: true`.
    ///
    /// ### Why a value with a `mint()` and not a request
    ///
    /// Android's `wsRequest` stamps the token in too, but its interceptor runs
    /// on the upgrade's own connection, so room mode, a refused token, a wait
    /// and the gate are all re-read when the socket connects **and again on
    /// every reconnect**. A `URLRequest` handed back from here would carry
    /// whatever was true when it was minted, for as long as the socket held
    /// it: the shell mints the request, the person starts room mode, the state
    /// socket reconnects from the request it is holding — and the household
    /// token goes on the wire while the phone is a room. ``HouseholdLock``
    /// argues at length that sampling the flag once per *call* is the bug to
    /// avoid; once per *mint* is longer-lived still.
    ///
    /// So this hands back a small value that re-asks the rule each time, and
    /// **I1-D must call ``SocketUpgrade/mint()`` immediately before each
    /// `webSocketTask(with:)`, including every reconnect** — never once into a
    /// stored property. A nil from `mint()` means "not now": do not connect,
    /// show the wait or the identity card, and try again when the shell says
    /// the state changed.
    ///
    /// Nil from *this* method means there is no socket to open at all: an
    /// address that does not parse, or one the cleartext rule refuses. Both
    /// are properties of the address, so they cannot change under a held
    /// value, which is why they are decided once, here. Android's `wsRequest`
    /// builds the request anyway and its interceptor refuses the connection
    /// when it is made; refusing to build it is the stricter half of the same
    /// rule, and it means no caller can be handed a socket request that cannot
    /// legitimately be opened ("no misleading controls").
    /// How the app opens a socket: this client's own `URLSession`, so the
    /// upgrade inherits its configuration exactly as Android's goes through
    /// the app's one `OkHttpClient` (map §1.2).
    ///
    /// The session itself stays internal. A transport can only open a request
    /// it is handed, and the only thing that builds one is
    /// ``webSocketUpgrade(_:)``, so handing out the transport hands out no
    /// way to make an unminted request.
    public var webSocketTransport: any StateSocketTransport {
        URLSessionStateSocketTransport(session: session)
    }

    public func webSocketUpgrade(_ url: String) -> SocketUpgrade? {
        guard let parsed = ServerURL.parseAllowingWebSocket(url), let target = URL(string: url) else { return nil }
        guard CleartextPolicy.permits(parsed) else { return nil }
        // `self` strongly, not weakly: this is the app's one client and it
        // outlives every socket. A weak capture would turn "the client is
        // gone" into a silently bare socket, which is the failure this whole
        // method exists to prevent.
        return SocketUpgrade(url: target) { [self] in
            tokenOutcome(for: parsed, wsUpgrade: true)
        }
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

/// A socket this phone may open, and the decision about what it carries,
/// deferred to the moment it is opened.
///
/// Made only by ``DomovoiClient/webSocketUpgrade(_:)``, whose doc comment has
/// the reasoning and I1-D's obligation.
public struct SocketUpgrade: Sendable {

    /// Where the socket is aimed. Fixed for the life of this value: the
    /// address passed the parser and the cleartext rule when it was made, and
    /// neither answer can change for the same address.
    public let url: URL

    private let decide: @Sendable () -> DomovoiClient.TokenOutcome

    init(url: URL, decide: @Sendable @escaping () -> DomovoiClient.TokenOutcome) {
        self.url = url
        self.decide = decide
    }

    /// The upgrade request to open **now**, or nil when this phone is not
    /// opening one at the moment.
    ///
    /// Call it immediately before each `webSocketTask(with:)`, reconnects
    /// included.
    ///
    /// * A request **with** the token: the scope rule admits this URL and the
    ///   token is good.
    /// * A request **without** it: bare is a legitimate socket, not a
    ///   failure — an unpaired phone opens one during the server's pre-setup
    ///   grace, room mode seals the household token while the phone is a
    ///   room, and a token the server has already refused is not sent again.
    ///   Android proceeds bare in each of those.
    /// * **Nil**: this app refused to send the request at all. The server
    ///   asked this phone to wait, or the identity gate held it — the two
    ///   places Android's interceptor *throws* instead of proceeding, so the
    ///   socket never connects there either. Falling through to a bare socket
    ///   here would open a connection Android would not have opened, and
    ///   would tell the shell the phone is unpaired when the truth is that it
    ///   is waiting.
    public func mint() -> URLRequest? {
        var request = URLRequest(url: url)
        switch decide() {
        case .bare:
            return request
        case .send(let token, _):
            request.setValue(token, forHTTPHeaderField: deviceTokenHeader)
            return request
        case .wait, .held:
            return nil
        }
    }
}

/// The redirect half of the client, as one request's delegate.
///
/// Why a class the compiler cannot check for me: `URLSessionTaskDelegate`
/// refines `URLSessionDelegate`, which refines `NSObjectProtocol`, so a
/// delegate must be an `NSObject` subclass, and `NSObject` is not `Sendable`.
/// There is no way to write a `URLSession` redirect delegate without this.
/// The `@unchecked` claim is sound here and that is checkable by reading the
/// stored properties: all three are `let`, two hold `Sendable` values (a
/// `Bool` and a `@Sendable` closure), and the third is a `Mutex`, which exists
/// for exactly this. Nothing mutable is reachable from here without going
/// through that lock.
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
    ///
    /// **The lock is not held across `decide`.** `Mutex` is non-reentrant,
    /// and `decide` calls out of this file into other locks: ``HouseholdLock``
    /// and ``TokenHealth`` today, I1-C's settings store (which `ServerBook`
    /// reads) after that, and I1-B's gate, which will hold a cache of its
    /// own. Nothing in this batch can take those in the other order, so
    /// nothing deadlocks today; two locks nested in a synchronous platform
    /// callback is a trap to be walked out of while it is still cheap, and it
    /// costs nothing, because one chain's hops are strictly sequential — the
    /// platform asks the next question only after this one has answered. So:
    /// read the state under the lock, decide outside it, write back under it.
    func followUp(response: HTTPURLResponse) -> URLRequest? {
        let (refused, current, hopsSoFar) = state.withLock { ($0.refusal != nil, $0.current, $0.hops) }
        guard !refused else { return nil }
        guard var next = RedirectPolicy.followUp(
            current,
            status: response.statusCode,
            location: response.value(forHTTPHeaderField: "Location"),
            isWebSocketUpgrade: wsUpgrade
        ) else { return nil }

        // Counted whether or not the hop is then allowed, as before: a chain
        // that is refused on its twenty-first hop was still twenty-one hops
        // long.
        let hops = hopsSoFar + 1
        if hops > RedirectPolicy.maxHops {
            return stop(at: hops, .notPermitted("too many redirects: \(hops)"))
        }
        guard let url = next.url, let parsed = ServerURL.parse(url.absoluteString) else {
            return stop(at: hops, .notPermitted("that is not a server address"))
        }
        // The cleartext rule for THIS hop, before it is connected. The reason
        // Android turns OkHttp's own follower off is that its follower would
        // have skipped this.
        guard CleartextPolicy.permits(parsed) else {
            return stop(at: hops, .notPermitted(CleartextPolicy.refusalMessage(parsed.host)))
        }
        // And the token for THIS hop's URL: the active server keeps it,
        // anywhere else loses it. `RedirectPolicy` has already removed the
        // header, so doing nothing here is "bare".
        switch decide(parsed) {
        case .bare:
            break
        case .send(let token, _):
            next.setValue(token, forHTTPHeaderField: deviceTokenHeader)
        case .wait(let ms):
            return stop(at: hops, .tokenThrottled(waitMs: ms))
        case .held(let reason):
            return stop(at: hops, .identityHeld(reason))
        }
        state.withLock {
            $0.hops = hops
            $0.current = next
        }
        return next
    }

    /// Record the hop and why the chain stopped. Always returns nil, so a
    /// refusal reads as one statement at the site that found it.
    private func stop(at hops: Int, _ why: DomovoiError) -> URLRequest? {
        state.withLock {
            $0.hops = hops
            // First refusal wins, as it did when this all ran under one lock.
            if $0.refusal == nil { $0.refusal = why }
        }
        return nil
    }
}
