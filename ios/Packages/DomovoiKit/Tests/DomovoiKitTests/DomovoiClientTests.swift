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

    private func makeClient(
        server: String? = nil,
        token: String? = "household-abc",
        book: ServerBook? = nil,
        health: TokenHealth? = nil,
        lock: HouseholdLock = HouseholdLock(),
        gate: (any TokenGate)? = nil,
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
        XCTAssertNil(client.webSocketRequest("ws://10.0.0.30:6369/ws/state")?.value(forHTTPHeaderField: deviceTokenHeader))
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
        XCTAssertNil(client.webSocketRequest("ws://10.0.0.9:6369/ws/state")?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertNil(try client.tokenForDownload(URL(string: "http://10.0.0.9:6369/api/videos/1/stream")!))
        XCTAssertEqual(await client.checkDeviceToken("household-abc"), .unavailable)

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
        XCTAssertEqual(
            await failure { try await refused.get("/api/x", as: [String: String].self) },
            .notPermitted(CleartextPolicy.refusalMessage("domovoi.example"))
        )
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

    func testTheHopCeilingStopsAChainThatNeverEnds() async {
        StubURLProtocol.dispatch { request in
            .moved(302, to: "/api/\(Int.random(in: 1...1_000_000))")
        }
        let client = makeClient()
        let error = await failure { try await client.get("/api/x", as: [String: String].self) }
        XCTAssertNotNil(error, "the chain was stopped")
        XCTAssertLessThanOrEqual(
            StubURLProtocol.requestCount, RedirectPolicy.maxHops + 1,
            "no more hops than the ceiling allows"
        )
        print("MEASURED endless chain: \(StubURLProtocol.requestCount) requests, error \(String(describing: error))")
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
        XCTAssertFalse(await makeClient().answers())
        XCTAssertFalse(await makeClient(server: "").answers())
    }

    func testAnswersCountsACleartextRefusalAsAnAnswerToFix() async {
        // Not an outage: a setting to correct, and the workspace stays up to
        // say so.
        XCTAssertTrue(await makeClient(server: "http://203.0.113.5:6369").answers())
        XCTAssertEqual(StubURLProtocol.requestCount, 0)
    }

    // ---- the socket upgrade and the download token ---------------------------

    func testTheUpgradeCarriesTheTokenOnlyWhereItMay() {
        let client = makeClient()
        XCTAssertEqual(
            client.webSocketRequest("ws://10.0.0.9:6369/ws/state")?.value(forHTTPHeaderField: deviceTokenHeader),
            "household-abc"
        )
        XCTAssertEqual(
            client.webSocketRequest("ws://10.0.0.9:6370/v1/dropin/kitchen")?.value(forHTTPHeaderField: deviceTokenHeader),
            "household-abc",
            "the drop-in socket to the core's own port is the documented exception"
        )
        XCTAssertNil(
            client.webSocketRequest("ws://10.0.0.10:6369/ws/state")?.value(forHTTPHeaderField: deviceTokenHeader),
            "an upgrade is still host-bound"
        )
        XCTAssertNil(
            client.webSocketRequest("ws://203.0.113.5:6369/ws/state"),
            "a socket the cleartext rule refuses is not built at all"
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
        XCTAssertNil(client.webSocketRequest("ws://10.0.0.9:6369/ws/state")?.value(forHTTPHeaderField: deviceTokenHeader))
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
        XCTAssertEqual(error, .notPermitted("this server has not proved itself on this network"))
        XCTAssertEqual(StubURLProtocol.requestCount, 0, "the request never left")
    }

    func testTheGateIsOnlyAskedWhenThereIsATokenToProtect() async throws {
        StubURLProtocol.enqueue(.json("{}"), .json("{}"))
        // No token: nothing to protect, so the request goes out bare.
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
