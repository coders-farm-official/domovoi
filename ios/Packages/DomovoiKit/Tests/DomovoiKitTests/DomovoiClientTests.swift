import XCTest
import Synchronization
@testable import DomovoiKit

/// The one HTTP client: where the household token goes, where it does not,
/// what is refused before a connection is made, and what the server's answers
/// become.
///
/// The twin of `android/.../net/DeviceAuthTest.kt` and `ApiClientTest.kt`.
/// Android drives these through a `MockWebServer`; here it is
/// ``StubURLProtocol``, which reaches no network and records what left the
/// phone.
final class DomovoiClientTests: XCTestCase {

    private let server = "http://10.0.0.9:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    /// The default gate is `AdmitsAnyServer`, not an absent one:
    /// ``DomovoiClient/gate`` is non-optional with no default argument since
    /// I1-C part 2, so the choice is stated here, once, for the many tests
    /// below whose subject is one of the six rules **in front** of the gate —
    /// the cleartext policy, the scope, room mode, the saved token, token
    /// health, the wait — or the redirect chain, or the error mapping. The
    /// tests whose subject IS the gate pass `ClosedGate` and say so.
    ///
    /// It is deliberately not ``AdmitsNoServer``: with that, every ordinary
    /// request in this file would be held and the rules in front of the gate
    /// would never be reached.
    private func makeClient(
        server: String? = nil,
        token: String? = "household-abc",
        book: ServerBook? = nil,
        health: TokenHealth? = nil,
        lock: HouseholdLock = HouseholdLock(),
        gate: any TokenGate = AdmitsAnyServer(),
        timeout: TimeInterval? = nil
    ) -> DomovoiClient {
        DomovoiClient(
            book: book ?? .fixed(server: server ?? self.server, token: token),
            gate: gate,
            health: health,
            lock: lock,
            configuration: StubURLProtocol.session(timeout: timeout)
        )
    }

    /// `XCTAssertThrowsError` has no async form, so this is the shape every
    /// failing case below uses.
    private func failure<T>(_ body: () async throws -> T) async -> DomovoiError? {
        do {
            _ = try await body()
            return nil
        } catch let error as DomovoiError {
            return error
        } catch {
            XCTFail("not a DomovoiError: \(error)")
            return nil
        }
    }

    private func token(at index: Int) -> String? {
        StubURLProtocol.header(deviceTokenHeader, at: index)
    }

    // ---- the token goes to the active server and nowhere else (A6-01/02) ----

    func testEveryRequestToTheActiveServerCarriesTheToken() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        let client = makeClient()
        _ = try await client.get("/api/music/library", as: [String: String].self)
        XCTAssertEqual(token(at: 0), "household-abc")
        XCTAssertEqual(StubURLProtocol.header("X-Requested-With", at: 0), "DomovoiApp")
        try await client.send(method: "POST", path: "/api/music/play")
        XCTAssertEqual(token(at: 1), "household-abc")
    }

    func testARequestToAnotherHostCarriesNoToken() async throws {
        StubURLProtocol.enqueue(.json("{}"))
        // An absolute URL through the JSON client is the same request — the
        // radio stream's host, the sweep's probe, whatever a library was
        // handed.
        _ = try await makeClient().get("http://10.0.0.10:6369/grab.mp3", as: [String: String].self)
        XCTAssertNil(token(at: 0), "the attacker's listener sees no token")
    }

    func testARequestToAnotherPortOnTheSameHostCarriesNoToken() async throws {
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await makeClient().get("http://10.0.0.9:6370/api/x", as: [String: String].self)
        XCTAssertNil(token(at: 0))
    }

    func testAnExplicitHeaderOnAForeignRequestIsStrippedNotHonoured() async throws {
        StubURLProtocol.enqueue(.json("{}"))
        var request = URLRequest(url: URL(string: "http://10.0.0.10:6369/api/x")!)
        request.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        _ = try await makeClient().perform(request)
        XCTAssertNil(token(at: 0), "the client decides, not the caller")
    }

    /// And on a request to the active server the caller's value is *replaced*
    /// rather than left alone — the strip is unconditional and the decision is
    /// made afresh, so a value from a library, a deep link or a stale screen
    /// cannot become what this phone authenticates with.
    func testAnExplicitHeaderOnTheActiveServerIsReplacedByTheStoredToken() async throws {
        StubURLProtocol.enqueue(.json("{}"))
        var request = URLRequest(url: URL(string: "\(server)/api/rooms")!)
        request.setValue("whatever-a-caller-set", forHTTPHeaderField: deviceTokenHeader)
        _ = try await makeClient().perform(request)
        XCTAssertEqual(token(at: 0), "household-abc")
    }

    func testTheTokenIsLookedUpByTheServerTheRequestIsScopedTo() async throws {
        // A server switch writes the address, then the token. A request
        // intercepted between the two would pair the NEW address with the OLD
        // household's token if the token were read on its own (A6-01 review).
        let active = Mutex<String>("http://10.0.0.9:6369")
        let tokens = [
            "http://10.0.0.9:6369": "household-abc",
            "http://10.0.0.20:6369": "other-house",
        ]
        let client = makeClient(book: ServerBook(
            activeServer: { active.withLock { $0 } },
            tokenForServer: { tokens[$0] }
        ))
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        _ = try await client.get("/api/x", as: [String: String].self)
        XCTAssertEqual(token(at: 0), "household-abc")

        active.withLock { $0 = "http://10.0.0.20:6369" }
        _ = try await client.get("/api/x", as: [String: String].self)
        XCTAssertEqual(token(at: 1), "other-house")
        XCTAssertEqual(client.deviceToken, "other-house")

        // A server with no token of its own: nothing, not the other one's.
        active.withLock { $0 = "http://10.0.0.30:6369" }
        XCTAssertNil(client.deviceToken)
        XCTAssertNil(client.webSocketUpgrade("ws://10.0.0.30:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader))
    }

    func testTheSharedClientStillAuthenticatesTheActiveServerAfterAForeignCall() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        let client = makeClient()
        _ = try await client.get("http://10.0.0.10:6369/x", as: [String: String].self)
        _ = try await client.get("/api/music/library", as: [String: String].self)
        XCTAssertNil(token(at: 0))
        XCTAssertEqual(token(at: 1), "household-abc")
    }

    // ---- room mode ----------------------------------------------------------

    /// While this phone is a room, the household token goes on NO request,
    /// whoever builds it, and comes back the moment room mode ends (the port
    /// of `HouseholdLockTest`).
    func testRoomModeSealsTheTokenAndUnsealingBringsItBack() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"), .json("{}"))
        let lock = HouseholdLock()
        let client = makeClient(lock: lock)

        _ = try await client.get("/api/music/library", as: [String: String].self)
        XCTAssertEqual(token(at: 0), "household-abc")

        lock.setRoomModeActive(true)
        _ = try await client.get("/api/music/library", as: [String: String].self)
        XCTAssertNil(token(at: 1))
        XCTAssertNil(client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertNil(try client.tokenForDownload(URL(string: "http://10.0.0.9:6369/api/videos/1/stream")!))
        let check = await client.checkDeviceToken("household-abc")
        XCTAssertEqual(check, .unavailable)

        lock.setRoomModeActive(false)
        _ = try await client.get("/api/music/library", as: [String: String].self)
        XCTAssertEqual(token(at: 2), "household-abc")
    }

    // ---- cleartext, before any connection -----------------------------------

    func testAPublicIPOverPlainHttpIsRefusedBeforeAnyConnection() async {
        let client = makeClient(server: "http://203.0.113.5:6369")
        let error = await failure { try await client.get("/api/x", as: [String: String].self) }
        XCTAssertEqual(error, .notPermitted(CleartextPolicy.refusalMessage("203.0.113.5")))
        XCTAssertEqual(StubURLProtocol.requestCount, 0, "nothing was attempted")
    }

    func testAPublicNameOverPlainHttpIsRefusedAndHttpsToItIsNot() async throws {
        let refused = makeClient(server: "http://domovoi.example")
        let error = await failure { try await refused.get("/api/x", as: [String: String].self) }
        XCTAssertEqual(error, .notPermitted(CleartextPolicy.refusalMessage("domovoi.example")))
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await makeClient(server: "https://domovoi.example").get("/api/x", as: [String: String].self)
        XCTAssertEqual(StubURLProtocol.requestCount, 1)
    }

    // ---- redirects: every hop decides afresh --------------------------------

    func testARedirectWithinTheServerKeepsTheToken() async throws {
        StubURLProtocol.enqueue(
            .moved(302, to: "/api/music/library/7/audio?download=1"),
            .json("{}")
        )
        _ = try await makeClient().get("/api/music/library/7/audio", as: [String: String].self)
        XCTAssertEqual(StubURLProtocol.requestCount, 2)
        XCTAssertEqual(token(at: 0), "household-abc")
        XCTAssertEqual(token(at: 1), "household-abc")
        XCTAssertEqual(
            StubURLProtocol.recorded[1].url?.absoluteString,
            "http://10.0.0.9:6369/api/music/library/7/audio?download=1"
        )
    }

    func testARedirectToAnotherHostDropsTheToken() async throws {
        StubURLProtocol.enqueue(.moved(302, to: "http://10.0.0.10:6369/grab.mp3"), .json("{}"))
        _ = try await makeClient().get("/api/music/library/7/audio", as: [String: String].self)
        XCTAssertEqual(token(at: 0), "household-abc")
        XCTAssertNil(token(at: 1), "the host it was sent on to sees no token")
    }

    func testARedirectToAnotherPortOnTheSameHostDropsTheToken() async throws {
        StubURLProtocol.enqueue(.moved(302, to: "http://10.0.0.9:6370/api/x"), .json("{}"))
        _ = try await makeClient().get("/api/x", as: [String: String].self)
        XCTAssertNil(token(at: 1), "a URL that differs only by port is a different origin")
    }

    func testARedirectToAPublicPlainHttpHostIsRefusedBeforeAnyConnection() async {
        StubURLProtocol.enqueue(.moved(302, to: "http://203.0.113.5/grab.mp3"))
        let client = makeClient()
        let started = Date()
        let error = await failure { try await client.get("/api/x", as: [String: String].self) }
        XCTAssertEqual(error, .notPermitted(CleartextPolicy.refusalMessage("203.0.113.5")))
        XCTAssertEqual(StubURLProtocol.requestCount, 1, "nothing reached the host it named")
        XCTAssertLessThan(Date().timeIntervalSince(started), 2, "refused by the rule, not by a timeout")
    }

    func testAnHttpsServerIsNeverDowngradedMidChain() async {
        StubURLProtocol.enqueue(.moved(302, to: "http://10.0.0.9:6369/api/y"))
        let client = makeClient(server: "https://domovoi.example")
        // The chain stops, and what comes back is the 3xx itself (the
        // platform's answer to a declined redirect — see PlatformFactsTests),
        // which the client reports as the status it is.
        let error = await failure { try await client.get("/api/x", as: [String: String].self) }
        XCTAssertEqual(error?.failure?.status, 302)
        XCTAssertEqual(StubURLProtocol.requestCount, 1)
    }

    /// A chain that never ends is stopped, and measurably so: `URLSession`
    /// follows exactly 20 redirects — the same number ``RedirectPolicy``
    /// names — and then reports `.httpTooManyRedirects`, which the client
    /// maps the way Android's `ProtocolException` reads. The delegate's own
    /// counter would fire one hop later and so never does; it stays because
    /// nothing in Apple's documentation promises that ceiling.
    func testTheHopCeilingStopsAChainThatNeverEnds() async {
        StubURLProtocol.dispatch { _ in .moved(302, to: "/api/\(Int.random(in: 1...1_000_000))") }
        let client = makeClient()
        let error = await failure { try await client.get("/api/x", as: [String: String].self) }
        XCTAssertEqual(error, .unreachable("too many redirects"))
        XCTAssertEqual(
            StubURLProtocol.requestCount, RedirectPolicy.maxHops + 1,
            "one request, then twenty hops, then no more"
        )
    }

    /// **A hop carries what the CALL was allowed to carry, re-asked — not the
    /// scope rule regardless.**
    ///
    /// The paste check's whole job is to ask the server about a candidate the
    /// vault has not accepted. Android runs it on a client with
    /// `DeviceAuthInterceptor` removed, so a hop carries nothing at all. If a
    /// hop ran the scope rule instead it would re-acquire the SAVED token —
    /// and a wrong paste plus a `302` (a reverse proxy, or FastAPI's
    /// `redirect_slashes`) would be answered 200 on the strength of the valid
    /// token and reported as `.accepted`, which is the 2026-10-17 incident
    /// this check was added to prevent.
    func testAPasteCheckThatIsRedirectedDoesNotReacquireTheSavedToken() async {
        StubURLProtocol.dispatch { request in
            // A server whose satellites read redirects once, and which
            // answers only to the token it really issued. The hop is marked
            // with a query rather than a trailing slash, because `URL.path`
            // drops a trailing slash — so the stub would have redirected for
            // ever, and found the hop ceiling instead of the bug.
            if request.url?.query == nil {
                return .moved(302, to: "\(DomovoiClient.tokenCheckPath)?hop=1")
            }
            if request.value(forHTTPHeaderField: deviceTokenHeader) == "household-abc" {
                return .json("[]")
            }
            return .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
        }
        let check = await makeClient().checkDeviceToken("wrong-paste-abcd")
        XCTAssertEqual(StubURLProtocol.requestCount, 2)
        XCTAssertEqual(token(at: 0), "wrong-paste-abcd")
        XCTAssertNil(token(at: 1), "a hop of the paste check carries nothing, as on a client with no auth interceptor")
        XCTAssertEqual(check, .refused, "a wrong paste plus a 302 must never read as accepted")
    }

    /// The same rule for `.none`, which is what I1-B's /24 sweep will use:
    /// "the sweep sends no token" has to mean no hop either, or finding A6-02
    /// comes back through a redirect. Aimed at the ACTIVE server, where the
    /// scope rule would have put the token on both hops.
    func testAProbeThatCarriesNoTokenCarriesNoneOnAHopEither() async throws {
        StubURLProtocol.enqueue(.moved(302, to: "/api/health/"), .json("{}"))
        var request = URLRequest(url: URL(string: "\(server)/api/health")!)
        request.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        _ = try await makeClient().perform(request, mode: .none)
        XCTAssertEqual(StubURLProtocol.requestCount, 2)
        XCTAssertNil(token(at: 0), "the caller's header is stripped and nothing is put back")
        XCTAssertNil(token(at: 1), "and a redirect is not where a sweep acquires one")
    }

    // ---- what a redirected chain's answer is about ---------------------------

    /// **A stranger's 401 must not end this phone's pairing with its own
    /// server.**
    ///
    /// ``TokenHealth`` is told about an answer only when the origin that
    /// answered is the one that issued the token. The response here is the
    /// LAST hop's, so checking the FIRST hop's URL would have filed a foreign
    /// host's verdict against the active server: requests would go out bare
    /// for good and the shell would say "Domovoi doesn't recognise this phone
    /// any more" on a stranger's say-so.
    func testAForeignHostsRefusalIsNotFiledAgainstTheActiveServersToken() async {
        let health = TokenHealth()
        StubURLProtocol.enqueue(
            .moved(302, to: "http://10.0.0.99:6369/api/rooms"),
            .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
        )
        let client = makeClient(health: health)
        _ = await failure { try await client.get("/api/rooms", as: [String: String].self) }
        XCTAssertEqual(token(at: 0), "household-abc")
        XCTAssertNil(token(at: 1), "the hop went out bare, as it must")
        XCTAssertFalse(health.isRejected(server), "and what the stranger said about it is not about it")
        XCTAssertEqual(
            client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader),
            "household-abc",
            "so the phone is still paired"
        )
    }

    /// The other half of the same rule, and the reason it is written as "the
    /// origin that answered" rather than "no redirect happened": a redirect
    /// WITHIN the active server carried the token and the server's own verdict
    /// on it still teaches, exactly as Android's per-hop interceptor learns it.
    func testARefusalAfterARedirectWithinTheServerStillTeaches() async {
        let health = TokenHealth()
        StubURLProtocol.enqueue(
            .moved(302, to: "/api/rooms/"),
            .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
        )
        let client = makeClient(health: health)
        _ = await failure { try await client.get("/api/rooms", as: [String: String].self) }
        XCTAssertEqual(token(at: 1), "household-abc", "the hop is the active server's own")
        XCTAssertTrue(health.isRejected(server))
    }

    // ---- what the answers become --------------------------------------------

    func testADeviceTokenRefusalIsRefusedAndAsksThePhoneToPair() async {
        StubURLProtocol.enqueue(.json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401))
        let client = makeClient()
        let error = await failure { try await client.get("/api/rooms", as: [String: String].self) }
        guard case .refused(let failure) = error else { return XCTFail("not refused: \(String(describing: error))") }
        XCTAssertEqual(failure.status, 401)
        XCTAssertTrue(failure.deviceTokenRequired)
        XCTAssertTrue(client.pairingRequired)
        client.clearPairingRequired()
        XCTAssertFalse(client.pairingRequired)
    }

    func testTheCookieOnly403IsARefusalToo() async {
        StubURLProtocol.enqueue(.json(
            #"{"detail":"X-Device-Token required — the dashboard cookie does not authorize device-tier actions"}"#,
            status: 403
        ))
        let client = makeClient()
        let error = await failure { try await client.get("/api/rooms", as: [String: String].self) }
        XCTAssertEqual(error?.failure?.status, 403)
        XCTAssertTrue(client.pairingRequired)
    }

    func testAnAdminRefusalIsNotAPairingRefusal() async {
        StubURLProtocol.enqueue(.json(#"{"detail":"admin session required"}"#, status: 401))
        let client = makeClient()
        let error = await failure { try await client.get("/api/admin/x", as: [String: String].self) }
        guard case .refused(let failure) = error else { return XCTFail("not refused") }
        XCTAssertFalse(failure.deviceTokenRequired)
        XCTAssertFalse(client.pairingRequired)
    }

    func testARateLimitCarriesItsCountdown() async {
        StubURLProtocol.enqueue(.json(
            #"{"detail":"too many wrong X-Device-Token values — retry in 30s"}"#,
            status: 429,
            headers: ["Retry-After": "30"]
        ))
        let error = await failure { try await makeClient().get("/api/rooms", as: [String: String].self) }
        guard case .rateLimited(let failure, let waitMs) = error else { return XCTFail("not rate limited") }
        XCTAssertEqual(failure.status, 429)
        XCTAssertEqual(waitMs, 30_000)
    }

    func testServerTroubleKeepsItsWholeBodyAndThePartTheServerNamed() async {
        // The core names what failed in `failed` (2026-10-01); a long detail
        // before it must not cut it off, as the message's 200 characters
        // would.
        let body = #"{"detail":"\#(String(repeating: "x", count: 300))","failed":"music_player"}"#
        StubURLProtocol.enqueue(.json(body, status: 502))
        let error = await failure { try await makeClient().post("/api/music/play-tracks", as: [String: String].self) }
        guard case .serverTrouble(let failure) = error else { return XCTFail("not server trouble") }
        XCTAssertEqual(failure.status, 502)
        XCTAssertEqual(failure.body, body)
        XCTAssertEqual(failure.failedPart, "music_player")
        XCTAssertFalse(failure.message.contains("music_player"), "the message keeps 200 characters")
    }

    func testAnyOtherStatusIsAPlainHttpError() async {
        StubURLProtocol.enqueue(.json(#"{"detail":"nope"}"#, status: 404))
        let error = await failure { try await makeClient().get("/api/x", as: [String: String].self) }
        guard case .httpError(let failure) = error else { return XCTFail("not an http error") }
        XCTAssertEqual(failure.status, 404)
        XCTAssertEqual(failure.message, #"404: {"detail":"nope"}"#)
    }

    func testARefusalCarriesTheServersOwnRefusalHeader() async {
        StubURLProtocol.enqueue(.json(
            #"{"detail":"AI features are off"}"#,
            status: 403,
            headers: [refusalHeader: "ai-off"]
        ))
        let error = await failure { try await makeClient().post("/api/chat/threads", as: [String: String].self) }
        XCTAssertEqual(error?.failure?.refusal, "ai-off")
    }

    func testTheErrorBodyIsTruncatedForTheMessageAndKeptForTheBody() async {
        StubURLProtocol.enqueue(.json(String(repeating: "x", count: 5000), status: 500))
        let error = await failure { try await makeClient().get("/boom", as: [String: String].self) }
        XCTAssertEqual(error?.failure?.message, "500: " + String(repeating: "x", count: 200))
        XCTAssertEqual(error?.failure?.body.count, 4096, "the body keeps 4,096")
    }

    func testAnAnswerThatIsNotDomovoiSaysSo() async {
        // A router's login page on the port, answering 200.
        StubURLProtocol.enqueue(.json("<html><body>Please sign in</body></html>"))
        let error = await failure { try await makeClient().get("/api/health", as: Health.self) }
        guard case .notDomovoi(let reason) = error else { return XCTFail("not notDomovoi: \(String(describing: error))") }
        XCTAssertTrue(reason.hasPrefix("that isn't Domovoi"), reason)
    }

    func testAJsonAnswerMissingWhatThisAppNeedsIsNotDomovoiEither() async {
        StubURLProtocol.enqueue(.json(#"{"something":"else"}"#))
        let error = await failure { try await makeClient().get("/api/health", as: Health.self) }
        guard case .notDomovoi(let reason) = error else { return XCTFail("not notDomovoi") }
        XCTAssertTrue(reason.contains("status"), reason)
    }

    func testNothingAnsweringIsUnreachable() async {
        StubURLProtocol.enqueue(StubURLProtocol.Answer(error: URLError(.cannotConnectToHost)))
        let error = await failure { try await makeClient().get("/api/x", as: Health.self) }
        guard case .unreachable = error else { return XCTFail("not unreachable: \(String(describing: error))") }
    }

    func testNoServerConfiguredFailsBeforeAnyRequest() async {
        let error = await failure { try await makeClient(server: "").get("/api/health", as: Health.self) }
        XCTAssertEqual(error, .noServer)
        XCTAssertEqual(StubURLProtocol.requestCount, 0)
    }

    // ---- the verbs ----------------------------------------------------------

    func testAbsolutePrefixesRelativePathsOnly() {
        let client = makeClient()
        XCTAssertEqual(client.absolute("/api/health"), "http://10.0.0.9:6369/api/health")
        XCTAssertEqual(client.absolute("https://x.example/a.mp3"), "https://x.example/a.mp3")
    }

    func testPostSendsItsJsonBodyAndDefaultsToAnEmptyObject() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        let client = makeClient()
        _ = try await client.post("/api/a", body: Data(#"{"room_id":"kitchen"}"#.utf8), as: [String: String].self)
        let first = StubURLProtocol.recorded[0]
        XCTAssertEqual(first.httpMethod, "POST")
        XCTAssertEqual(first.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(first.httpBody.map { String(decoding: $0, as: UTF8.self) }, #"{"room_id":"kitchen"}"#)
        _ = try await client.post("/api/b", as: [String: String].self)
        XCTAssertEqual(StubURLProtocol.recorded[1].httpBody.map { String(decoding: $0, as: UTF8.self) }, "{}")
    }

    func testAnEmptyBodyIsNull() async throws {
        StubURLProtocol.enqueue(StubURLProtocol.Answer(status: 204))
        let answer = try await makeClient().delete("/api/things/1", as: Health?.self)
        XCTAssertNil(answer)
        XCTAssertEqual(StubURLProtocol.recorded[0].httpMethod, "DELETE")
        XCTAssertNil(StubURLProtocol.recorded[0].httpBody, "a DELETE sends a body only when it is given one")
    }

    func testAGoodAnswerDecodes() async throws {
        StubURLProtocol.enqueue(.json(#"{"status":"ok","bot_name":"domovoi","extra":1}"#))
        let health = try await makeClient().get("/api/health", as: Health.self)
        XCTAssertEqual(health.status, "ok")
        XCTAssertEqual(health.botName, "domovoi")
    }

    // ---- is it there at all? ------------------------------------------------

    func testAnswersCountsAnyHttpStatusEvenARefusal() async {
        // An unpaired phone: the server is there, it just says no.
        StubURLProtocol.enqueue(StubURLProtocol.Answer(status: 403))
        let client = makeClient()
        var answered = await client.answers()
        XCTAssertTrue(answered)
        XCTAssertEqual(StubURLProtocol.recorded[0].url?.path, "/api/health")
        StubURLProtocol.enqueue(.json("{}"))
        answered = await client.answers()
        XCTAssertTrue(answered)
    }

    func testAnswersIsFalseOnlyWhenNothingAnswers() async {
        StubURLProtocol.enqueue(StubURLProtocol.Answer(error: URLError(.timedOut)))
        let unreachable = await makeClient().answers()
        XCTAssertFalse(unreachable)
        let noServer = await makeClient(server: "").answers()
        XCTAssertFalse(noServer)
    }

    func testAnswersCountsACleartextRefusalAsAnAnswerToFix() async {
        // Not an outage: a setting to correct, and the workspace stays up to
        // say so.
        let answered = await makeClient(server: "http://203.0.113.5:6369").answers()
        XCTAssertTrue(answered)
        XCTAssertEqual(StubURLProtocol.requestCount, 0)
    }

    // ---- the socket upgrade and the download token ---------------------------

    func testTheUpgradeCarriesTheTokenOnlyWhereItMay() {
        let client = makeClient()
        XCTAssertEqual(
            client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader),
            "household-abc"
        )
        XCTAssertEqual(
            client.webSocketUpgrade("ws://10.0.0.9:6370/v1/dropin/kitchen")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader),
            "household-abc",
            "the drop-in socket to the core's own port is the documented exception"
        )
        XCTAssertNil(
            client.webSocketUpgrade("ws://10.0.0.10:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader),
            "an upgrade is still host-bound"
        )
        XCTAssertNil(
            client.webSocketUpgrade("ws://203.0.113.5:6369/ws/state"),
            "a socket the cleartext rule refuses is not offered at all"
        )
        XCTAssertNil(
            client.webSocketUpgrade("ws://not a server/ws/state"),
            "nor one at an address that does not parse"
        )
    }

    /// **The seal is read when the socket connects, not when it was minted.**
    ///
    /// Android's interceptor runs on the upgrade's own connection, so room
    /// mode is re-read at connect and at every reconnect. A `URLRequest`
    /// handed out of this client would carry what was true when it was made,
    /// for as long as the socket held it: the shell mints it, the person
    /// starts room mode, the state socket reconnects from the held value — and
    /// the household token goes on the wire while the phone is a room.
    func testAnUpgradeMintedBeforeRoomModeIsResealedWhenItReconnects() throws {
        let lock = HouseholdLock()
        let client = makeClient(lock: lock)
        let upgrade = try XCTUnwrap(client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state"))
        XCTAssertEqual(upgrade.mint()?.value(forHTTPHeaderField: deviceTokenHeader), "household-abc")

        lock.setRoomModeActive(true)
        let reconnect = try XCTUnwrap(upgrade.mint(), "a room still opens a bare state socket")
        XCTAssertNil(
            reconnect.value(forHTTPHeaderField: deviceTokenHeader),
            "the household token must not reach the wire while this phone is a room"
        )

        lock.setRoomModeActive(false)
        XCTAssertEqual(
            upgrade.mint()?.value(forHTTPHeaderField: deviceTokenHeader), "household-abc",
            "and it comes back, from the value the socket is still holding"
        )
    }

    /// A refused token is re-read at connect too — the same held value stops
    /// carrying it the moment ``TokenHealth`` learns.
    func testAnUpgradeStopsCarryingATokenTheServerRefusesAfterItWasMinted() throws {
        let health = TokenHealth()
        let client = makeClient(health: health)
        let upgrade = try XCTUnwrap(client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state"))
        XCTAssertEqual(upgrade.mint()?.value(forHTTPHeaderField: deviceTokenHeader), "household-abc")
        health.observe(
            url: server,
            sent: "household-abc",
            current: "household-abc",
            status: 401,
            body: #"{"detail":"X-Device-Token or admin session required"}"#,
            retryAfter: nil
        )
        XCTAssertNil(upgrade.mint()?.value(forHTTPHeaderField: deviceTokenHeader))
    }

    /// `nil` from ``SocketUpgrade/mint()`` is "we refused", never "there is no
    /// token": a wait and a held request are the two places Android's
    /// interceptor throws instead of proceeding, so no socket connects there;
    /// an unpaired phone's bare socket is legitimate — the server's pre-setup
    /// grace — and still opens.
    func testNoSocketIsMintedWhileTheServerSaysWaitOrTheGateHoldsIt() throws {
        let now = Shared<Int64>(1_000)
        let health = TokenHealth(clock: { now.current })
        health.noteWait(server, waitMs: 30_000)
        let waiting = try XCTUnwrap(makeClient(health: health).webSocketUpgrade("ws://10.0.0.9:6369/ws/state"))
        XCTAssertNil(waiting.mint(), "a wait is not a bare socket; Android throws here and never connects")

        let held = try XCTUnwrap(makeClient(gate: ClosedGate()).webSocketUpgrade("ws://10.0.0.9:6369/ws/state"))
        XCTAssertNil(held.mint(), "nor is a server that has not proved itself")

        let unpaired = try XCTUnwrap(makeClient(token: nil).webSocketUpgrade("ws://10.0.0.9:6369/ws/state"))
        let bare = try XCTUnwrap(unpaired.mint(), "an unpaired phone legitimately opens a bare socket")
        XCTAssertNil(bare.value(forHTTPHeaderField: deviceTokenHeader))

        // And once the wait has run out, the same held value opens again.
        now.advance(30_001)
        XCTAssertEqual(
            waiting.mint()?.value(forHTTPHeaderField: deviceTokenHeader), "household-abc",
            "the wait was the only reason, and it is over"
        )
    }

    func testTheDownloadTokenFollowsTheSameRule() throws {
        let client = makeClient()
        XCTAssertEqual(try client.tokenForDownload(URL(string: "http://10.0.0.9:6369/api/files/1/raw")!), "household-abc")
        XCTAssertNil(try client.tokenForDownload(URL(string: "http://10.0.0.10:6369/api/files/1/raw")!))
        XCTAssertNil(
            try client.tokenForDownload(URL(string: "http://10.0.0.9:6370/api/files/1/raw")!),
            "another port is not the active server, and a download is not an upgrade"
        )
        XCTAssertNil(try makeClient(token: nil).tokenForDownload(URL(string: "http://10.0.0.9:6369/api/files/1/raw")!))
    }

    // ---- a token the header could not carry ----------------------------------

    func testATokenAnAdminChoseGoesOutVerbatim() async throws {
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await makeClient(token: "Maple Street, 1984!").get("/api/health", as: [String: String].self)
        XCTAssertEqual(token(at: 0), "Maple Street, 1984!")
    }

    func testANonAsciiTokenIsDroppedRatherThanRaised() async throws {
        StubURLProtocol.enqueue(.json("{}"))
        let client = makeClient(token: "café-token-abcdef")
        _ = try await client.get("/api/health", as: [String: String].self)
        XCTAssertNil(token(at: 0), "the server answers 401 and the phone is sent to the pairing screen")
        XCTAssertNil(client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state")?.mint()?.value(forHTTPHeaderField: deviceTokenHeader))
    }

    func testAnUnpairedPhoneSendsNoHeaderAtAll() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        let client = makeClient(token: nil)
        _ = try await client.get("/api/health", as: [String: String].self)
        XCTAssertNil(token(at: 0))
        _ = try await makeClient(token: "   ").get("/api/health", as: [String: String].self)
        XCTAssertNil(token(at: 1), "blank is not paired")
    }

    // ---- the pairing screen --------------------------------------------------

    func testAnUntrackedReadNeitherRaisesNorClearsThePairingScreen() async throws {
        StubURLProtocol.enqueue(
            .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401),
            .json("{}")
        )
        let client = makeClient()
        _ = await failure { try await client.get("/api/rooms", as: [String: String].self) }
        XCTAssertTrue(client.pairingRequired)
        _ = try await client.getUntracked("/api/containers/summary", as: [String: String].self)
        XCTAssertTrue(client.pairingRequired, "a 200 from an open endpoint must not clear it")
    }

    func testASuccessfulTrackedCallClearsThePairingScreen() async throws {
        StubURLProtocol.enqueue(
            .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401),
            .json("{}")
        )
        let client = makeClient()
        _ = await failure { try await client.get("/api/rooms", as: [String: String].self) }
        XCTAssertTrue(client.pairingRequired)
        _ = try await client.get("/api/rooms", as: [String: String].self)
        XCTAssertFalse(client.pairingRequired)
    }

    func testARefusedUpgradeAsksThePhoneToPair() {
        // The socket listeners see a status and a reason, never a body.
        let client = makeClient()
        client.notePossiblePairingRefusal(status: 401, body: "X-Device-Token or admin session required")
        XCTAssertTrue(client.pairingRequired)
        client.clearPairingRequired()
        client.notePossiblePairingRefusal(status: 401, body: "Unauthorized")
        XCTAssertFalse(client.pairingRequired)
    }

    // ---- the gate ------------------------------------------------------------

    func testAGateThatRefusesHoldsTheWholeRequestBack() async {
        let client = makeClient(gate: ClosedGate())
        let error = await failure { try await client.get("/api/x", as: [String: String].self) }
        XCTAssertEqual(error, .identityHeld("this server has not proved itself on this network"))
        XCTAssertEqual(StubURLProtocol.requestCount, 0, "the request never left")
    }

    /// A held request is not an answer, where a cleartext refusal is: the
    /// split Android makes by catching `UnknownServiceException` as true and
    /// letting `ServerIdentityException` fall through to its `IOException`
    /// branch. Sharing one `.notPermitted` case made the two shells disagree
    /// about whether a `Mismatch` server is there — the one case the gate
    /// exists for.
    func testAServerThatFailsTheIdentityGateDoesNotCountAsAnswering() async {
        let answered = await makeClient(gate: ClosedGate()).answers()
        XCTAssertFalse(answered, "the gate held it; nothing was asked and nothing answered")
        XCTAssertEqual(StubURLProtocol.requestCount, 0)
    }

    /// And the toast says which it was, rather than blaming the network for a
    /// request the app chose not to send.
    func testFailureTextNamesAHeldRequestAndAWaitRatherThanTheConnection() {
        XCTAssertEqual(
            failureText("Poll", .identityHeld("this server has not proved itself on this network")),
            "Poll failed: this server has not proved itself on this network"
        )
        XCTAssertEqual(
            failureText("Poll", .tokenThrottled(waitMs: 30_000)),
            "Poll failed: Domovoi asked this phone to wait 30 s before it tries the household token again"
        )
    }

    /// `requireAdmitted` is asked only when a token would actually go out —
    /// which is not the same question as whether the gate is asked anything
    /// at all. Since the review's fix A, `prepare` IS called for an unpaired
    /// phone's request to the active server, so that a refusal from it can
    /// raise the pairing screen (`IdentityGateTests`). What must never happen
    /// is the one this pins: a request going out held, or bare-but-refused,
    /// because a gate was consulted about a token that was never going to be
    /// sent.
    func testTheGateIsNotAskedToADMITWhenThereIsNoTokenToProtect() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        // No token: nothing to protect, so the request goes out bare even
        // though this gate refuses everything.
        _ = try await makeClient(token: nil, gate: ClosedGate()).get("/api/x", as: [String: String].self)
        // Another host: out of scope, so there is nothing to ask about.
        _ = try await makeClient(gate: ClosedGate()).get("http://10.0.0.10:6369/x", as: [String: String].self)
        XCTAssertEqual(StubURLProtocol.requestCount, 2)
        XCTAssertNil(token(at: 0))
        XCTAssertNil(token(at: 1))
    }

    func testAGateThatHasNotProvedItselfDoesNotInviteAPaste() {
        let client = makeClient(gate: ClosedGate(admitsPrompt: false))
        client.notePossiblePairingRefusal(status: 401, body: "X-Device-Token or admin session required")
        XCTAssertFalse(client.pairingRequired, "a rogue's 401 must not invite a paste (A6-03)")
    }

    // ---- the toast text ------------------------------------------------------

    func testFailureTextReportsTheServerErrorNotOffline() async {
        StubURLProtocol.enqueue(.json(#"{"detail":"boom"}"#, status: 500))
        let error = await failure { try await makeClient().post("/api/podcasts/poll", as: [String: String].self) }
        let text = failureText("Poll", error!)
        XCTAssertEqual(text, #"Poll failed: 500: {"detail":"boom"}"#)
        XCTAssertFalse(text.contains("offline"))
    }

    func testFailureTextBlamesTheConnectionOnlyForTransportFailures() {
        XCTAssertEqual(failureText("Poll", .unreachable("could not connect")), "Poll failed (offline?)")
        XCTAssertEqual(failureText("Discovery", .noServer), "Discovery failed (offline?)")
        XCTAssertEqual(
            failureText("Poll", .notPermitted(CleartextPolicy.refusalMessage("203.0.113.5"))),
            "Poll failed: plain http to 203.0.113.5 is only allowed on the home network; use https://"
        )
        XCTAssertEqual(failureText("Poll", .httpError(ApiFailure(status: 502, message: ""))), "Poll failed: HTTP 502")
    }

    private struct Health: Decodable, Equatable {
        let status: String
        let botName: String?
        enum CodingKeys: String, CodingKey {
            case status
            case botName = "bot_name"
        }
    }

    /// I1-B's identity gate, stubbed: it refuses, and optionally refuses to
    /// let a refusal raise the pairing screen.
    private struct ClosedGate: TokenGate {
        var admitsPrompt = true
        func requireAdmitted(_ base: ServerURL) throws {
            throw DomovoiError.notPermitted("this server has not proved itself on this network")
        }
        func admitsPairingPrompt(_ base: ServerURL?) -> Bool { admitsPrompt }
    }
}
