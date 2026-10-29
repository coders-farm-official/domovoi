import XCTest
@testable import DomovoiKit

/// A server that no longer knows this phone (2026-10-17): reinstalled at the
/// same address, replaced, or its household token rotated.
///
/// The phone used to keep sending the old token on every poll, and the
/// server's wrong-token ledger (5 free, then a doubling wait to 5 min, per
/// address) answered `429` for good: each time its window opened, the phone's
/// next poll failed again and started a new one.
///
/// Now: a token the server refuses is sent once and never again, and while
/// the server says "wait" nothing that would carry the token is sent at all.
/// The fake below keeps the server's real ledger, as Android's test does, so
/// "the app never extends a lockout by itself" is measured rather than
/// assumed. A port of `android/.../net/TokenHealthTest.kt`.
final class TokenHealthTests: XCTestCase {

    /// A Domovoi web backend's device tier with `DEVICE_TOKEN_BACKOFF`
    /// (domovoi/admin_auth.py): a throttled attempt is not looked at and not
    /// counted; a checked wrong token is a failure; a right one clears it.
    private struct Ledger: Sendable {
        var goodToken = "rotated-household-phrase"
        var failures = 0
        var lastFailure: Int64 = 0
        /// The credential check cannot reach its database: 503, never a
        /// verdict.
        var dbDown = false
        /// Every token the ledger actually LOOKED AT.
        var evaluated: [String] = []
    }

    private let server = "http://10.0.0.9:6369"
    private let now = Shared<Int64>(5_000_000)
    private let token = Shared<String?>("old-household-phrase")
    private let logged = Shared<[String]>([])
    private let ledger = Shared(Ledger())
    /// Called when a request arrives, before the answer — a re-pair racing
    /// one in flight.
    private let onArrival = Shared<(@Sendable (String?) -> Void)?>(nil)
    private var book: TokenHealth.InMemoryBook!
    private var health: TokenHealth!
    private var client: DomovoiClient!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        now.set(5_000_000)
        token.set("old-household-phrase")
        logged.set([])
        ledger.set(Ledger())
        onArrival.set(nil)

        book = TokenHealth.InMemoryBook()
        let clock = now
        let log = logged
        health = TokenHealth(
            book: book,
            clock: { clock.current },
            log: { line in log.mutate { $0.append(line) } }
        )
        let ledger = self.ledger
        let clockBox = self.now
        let arrival = self.onArrival
        StubURLProtocol.dispatch { request in
            Self.answer(request, ledger: ledger, now: clockBox, onArrival: arrival)
        }
        let held = token
        let address = server
        client = DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in held.current }),
            health: health,
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    private static let refusal = StubURLProtocol.Answer.json(
        #"{"detail":"X-Device-Token or admin session required"}"#,
        status: 401
    )

    /// What the ledger still makes this address wait, in ms.
    private static func serverWaitMs(_ ledger: Shared<Ledger>, _ now: Shared<Int64>) -> Int64 {
        ledger.mutate { ledger in
            let charged = ledger.failures - 5
            guard charged > 0 else { return 0 }
            let delay = min(Int64(1000) << min(charged - 1, 20), 300_000)
            return max(0, ledger.lastFailure + delay - now.current)
        }
    }

    private func serverWaitMs() -> Int64 { Self.serverWaitMs(ledger, now) }

    /// Static, and handed its state, so the dispatcher closure it is called
    /// from captures no `XCTestCase` — which is not `Sendable`, and which
    /// Swift 6 is right to refuse to let a background request touch.
    private static func answer(
        _ request: URLRequest,
        ledger: Shared<Ledger>,
        now: Shared<Int64>,
        onArrival: Shared<(@Sendable (String?) -> Void)?>
    ) -> StubURLProtocol.Answer {
        let path = request.url?.path ?? ""
        let sent = request.value(forHTTPHeaderField: deviceTokenHeader)
        onArrival.current?(sent)

        if path.hasPrefix("/api/health") { return .json(#"{"status":"ok"}"#) }
        if path.hasPrefix("/api/admin") {
            return .json(#"{"detail":"admin session required"}"#, status: 401)
        }
        if path.hasPrefix("/api/plugins/") {
            // A plugin's own refusal that happens to name the header.
            return .json(#"{"detail":"this plugin wants a fresh X-Device-Token"}"#, status: 401)
        }
        if ledger.current.dbDown {
            return .json(
                #"{"detail":"auth check unavailable — the server could not check credentials just now; try again shortly"}"#,
                status: 503,
                headers: ["Retry-After": "5"]
            )
        }
        if path.hasPrefix("/api/fetch") {
            return .json(
                #"{"detail":"too many failed attempts — retry in 30s"}"#,
                status: 429,
                headers: ["Retry-After": "30"]
            )
        }
        guard let sent else { return Self.refusal }
        let wait = serverWaitMs(ledger, now)
        if wait > 0 {
            let seconds = (wait + 999) / 1000
            return .json(
                #"{"detail":"too many wrong X-Device-Token values — retry in \#(seconds)s, or sign in as an admin"}"#,
                status: 429,
                headers: ["Retry-After": "\(seconds)"]
            )
        }
        return ledger.mutate { ledger in
            ledger.evaluated.append(sent)
            if sent == ledger.goodToken {
                ledger.failures = 0
                return .json("{}")
            }
            ledger.failures += 1
            ledger.lastFailure = now.current
            return Self.refusal
        }
    }

    private func get(_ path: String = "/api/rooms") async -> DomovoiError? {
        do {
            _ = try await client.raw(method: "GET", path: path)
            return nil
        } catch {
            // `raw` has typed throws, so this is already a DomovoiError —
            // `catch let error as DomovoiError` would be a tautology the
            // compiler warns about.
            return error
        }
    }

    private var evaluated: [String] { ledger.current.evaluated }
    private var goodToken: String { ledger.current.goodToken }
    private var withToken: Int {
        StubURLProtocol.recorded.filter { $0.value(forHTTPHeaderField: deviceTokenHeader) != nil }.count
    }

    private func deepInTheLedgersDoubling(failures: Int) {
        ledger.mutate { ledger in
            ledger.failures = failures
            ledger.lastFailure = now.current
        }
    }

    // ---- token wrong: stop at once ------------------------------------------

    func testARefusedTokenIsSentOnceAndThenNeverAgain() async {
        let first = await get()
        XCTAssertEqual(first?.failure?.status, 401)
        XCTAssertTrue(health.isRejected(server), "the refusal is remembered")
        XCTAssertTrue(logged.current.contains { $0.contains("refused this phone's household token") })

        for _ in 0..<4 { _ = await get() }
        XCTAssertEqual(evaluated, ["old-household-phrase"], "the ledger looked at the old token once")
        XCTAssertEqual(withToken, 1)
        XCTAssertEqual(StubURLProtocol.requestCount, 5, "every later request went out without it")
        XCTAssertEqual(ledger.current.failures, 1)
    }

    func testNoTokenAtAllIsNotAWrongToken() async {
        token.set(nil)
        _ = await get()
        XCTAssertFalse(health.isRejected(server), "an unpaired phone is the pairing screen's business")
    }

    func testAnAdminTierRefusalIsNotAboutTheHouseholdToken() async {
        token.set(goodToken)
        _ = await get("/api/admin/x")
        XCTAssertFalse(health.isRejected(server))
        let after = await get()
        XCTAssertNil(after)
    }

    func testOnlyTheServersOwnRefusalDropsTheToken() async {
        // N-T2: a plugin's 401 naming the header is not the core's verdict.
        token.set(goodToken)
        _ = await get("/api/plugins/x/thing")
        XCTAssertFalse(health.isRejected(server))
        XCTAssertTrue(isServerDeviceRefusal(status: 401, body: #"{"detail":"X-Device-Token or admin session required"}"#))
        XCTAssertTrue(isServerDeviceRefusal(
            status: 403,
            body: #"{"detail":"X-Device-Token required — the dashboard cookie does not authorize device-tier actions"}"#
        ))
        XCTAssertFalse(isServerDeviceRefusal(status: 401, body: #"{"detail":"this plugin wants a fresh X-Device-Token"}"#))
        XCTAssertFalse(isServerDeviceRefusal(status: 503, body: #"{"detail":"X-Device-Token or admin session required"}"#))
        XCTAssertFalse(
            isServerDeviceRefusal(
                status: 401,
                body: #"{"detail":"X-Device-Token required - the dashboard cookie does not authorize device-tier actions"}"#
            ),
            "a hyphen where the server writes an em dash is a different string, and must not count"
        )
    }

    func testARefusalOfATokenReplacedMeanwhileSaysNothingAboutTheNewOne() async {
        // The person pastes the new token while a request with the old one is
        // in flight; the old one's 401 must not mark the new one refused.
        let held = token
        let good = goodToken
        onArrival.set { sent in
            if sent == "old-household-phrase" { held.set(good) }
        }
        _ = await get()
        XCTAssertFalse(health.isRejected(server))
        onArrival.set(nil)
        let after = await get()
        XCTAssertNil(after)
        XCTAssertEqual(StubURLProtocol.recorded.last?.value(forHTTPHeaderField: deviceTokenHeader), good)
    }

    func testANewTokenIsSentAgainOnceTheRefusalIsCleared() async {
        _ = await get()
        XCTAssertTrue(health.isRejected(server))
        let good = goodToken
        token.set(good)
        book.clear(server)
        let after = await get()
        XCTAssertNil(after)
        XCTAssertEqual(StubURLProtocol.recorded.last?.value(forHTTPHeaderField: deviceTokenHeader), good)
    }

    func testTheUpgradeStopsCarryingARefusedTokenToo() async {
        _ = await get()
        XCTAssertNil(
            client.webSocketRequest("ws://10.0.0.9:6369/ws/state")?.value(forHTTPHeaderField: deviceTokenHeader),
            "the upgrade is built from the same rule, so a refused token is not on it"
        )
    }

    // ---- 429: back off to Retry-After ---------------------------------------

    func testTheServersWaitIsHonouredAndThePhoneNeverExtendsItsOwnLockout() async {
        // The owner's phone: it has already sent wrong tokens all day and the
        // ledger is deep in its doubling (14 failures: a 256 s step).
        deepInTheLedgersDoubling(failures: 14)
        let first = await get()
        XCTAssertEqual(first?.failure?.status, 429)
        let wait = health.waitMs(server)
        XCTAssertTrue((255_000...256_000).contains(wait), "waits what Retry-After said, \(wait)")

        // A minute of polling (the panels, the state socket, the reach
        // probe): nothing leaves the phone, and the shell still counts it as
        // there.
        let before = StubURLProtocol.requestCount
        for _ in 0..<12 {
            now.advance(5_000)
            let error = await get()
            guard case .tokenThrottled = error else {
                return XCTFail("held here, got \(String(describing: error))")
            }
        }
        let answered = await client.answers()
        XCTAssertTrue(answered, "it answered, and the shell shows the wait")
        XCTAssertEqual(StubURLProtocol.requestCount, before, "not one request during the wait")
        XCTAssertEqual(evaluated.count, 0, "not one token looked at during the wait")

        // Past the wait: the next request is checked once, refused, and the
        // phone stops. Over the whole session the ledger saw ONE attempt.
        now.advance(200_000)
        let past = await get()
        XCTAssertEqual(past?.failure?.status, 401)
        for _ in 0..<5 { _ = await get() }
        XCTAssertEqual(evaluated, ["old-household-phrase"])
        XCTAssertTrue(health.isRejected(server))
    }

    func testARightTokenWaitsOutTheServersWindowThenWorks() async {
        // Paired again with the right token, but the address is still inside
        // the wait the old token earned: the phone waits, then it works.
        let good = goodToken
        deepInTheLedgersDoubling(failures: 9)
        token.set(good)
        let first = await get()
        XCTAssertEqual(first?.failure?.status, 429)
        guard case .tokenThrottled = await get() else { return XCTFail("not held") }
        now.advance(health.waitMs(server) + 1)
        let after = await get()
        XCTAssertNil(after)
        XCTAssertEqual(evaluated, [good])
    }

    func testATooManyRequestsAboutSomethingElseDoesNotHoldTheToken() async {
        token.set(goodToken)
        _ = await get("/api/fetch")
        XCTAssertEqual(health.waitMs(server), 0)
        let after = await get()
        XCTAssertNil(after)
    }

    func testADatabaseBlipIsNotARefusal() async {
        // S-T2: the server answers 503 when it cannot check credentials; a
        // good token must survive it.
        let good = goodToken
        token.set(good)
        ledger.mutate { $0.dbDown = true }
        let blip = await get()
        XCTAssertEqual(blip?.failure?.status, 503)
        XCTAssertFalse(health.isRejected(server))
        XCTAssertEqual(health.waitMs(server), 0)
        ledger.mutate { $0.dbDown = false }
        let after = await get()
        XCTAssertNil(after)
        XCTAssertEqual(StubURLProtocol.recorded.last?.value(forHTTPHeaderField: deviceTokenHeader), good)
    }

    func testASaveToDeviceDownloadNeitherGetsARefusedTokenNorSkipsTheWait() async throws {
        let file = URL(string: "http://10.0.0.9:6369/api/files/1/raw")!
        _ = await get()
        XCTAssertNil(try client.tokenForDownload(file))

        book.clear(server)
        deepInTheLedgersDoubling(failures: 9)
        _ = await get()
        // `catch DomovoiError.tokenThrottled { }` here crashed the Swift
        // 6.3 compiler — "Found outside of lifetime use?!" in SILGenCleanup,
        // signal 6, measured 2026-10-29 on Xcode 26.4's toolchain. The same
        // pattern compiles in a non-async function (`DomovoiClient.answers`
        // has one), so the trigger is narrower than "pattern-matching a
        // typed throw"; what it is exactly is not something this batch can
        // say. Catching everything and matching afterwards is the
        // workaround, and it asserts the same thing. Written up in the batch
        // report.
        do {
            _ = try client.tokenForDownload(file)
            XCTFail("expected a wait")
        } catch {
            guard case .tokenThrottled = error else { return XCTFail("not a wait: \(error)") }
        }
    }

    // ---- a pasted token is checked before it is saved ------------------------

    func testAPastedTokenTheServerTakesIsAccepted() async {
        token.set(nil)
        let good = goodToken
        let check = await client.checkDeviceToken(good)
        XCTAssertEqual(check, .accepted)
        let probe = StubURLProtocol.recorded.last
        XCTAssertEqual(probe?.url?.path, DomovoiClient.tokenCheckPath)
        XCTAssertEqual(
            probe?.value(forHTTPHeaderField: deviceTokenHeader), good,
            "the candidate, not the stored token"
        )
        XCTAssertEqual(StubURLProtocol.requestCount, 1)
    }

    func testAWrongPasteIsRefusedOnceAndNeverRetried() async {
        let check = await client.checkDeviceToken("old-custom-token-1")
        XCTAssertEqual(check, .refused)
        XCTAssertEqual(evaluated.count, 1, "one wrong token on the server's count, no more")
        XCTAssertEqual(StubURLProtocol.requestCount, 1)
        XCTAssertFalse(health.isRejected(server), "not saved, so nothing is marked")
    }

    func testAPasteDuringTheServersWaitIsNotSentAndSaysHowLong() async {
        let good = goodToken
        deepInTheLedgersDoubling(failures: 9)
        let first = await client.checkDeviceToken(good)
        guard case .throttled(let waitMs) = first, (7_000...8_000).contains(waitMs) else {
            return XCTFail("not throttled with the server's countdown: \(first)")
        }
        let sent = StubURLProtocol.requestCount
        let second = await client.checkDeviceToken(good)
        guard case .throttled = second else { return XCTFail("not throttled") }
        XCTAssertEqual(StubURLProtocol.requestCount, sent, "the wait is respected: nothing sent")
    }

    func testAPasteTheServerCouldNotCheckIsUnavailable() async {
        ledger.mutate { $0.dbDown = true }
        let good = goodToken
        let unchecked = await client.checkDeviceToken(good)
        XCTAssertEqual(unchecked, .unavailable)
        StubURLProtocol.dispatch { _ in StubURLProtocol.Answer(error: URLError(.cannotConnectToHost)) }
        let unanswered = await client.checkDeviceToken(good)
        XCTAssertEqual(unanswered, .unavailable)
    }

    func testAPasteThatCouldNotBeAHeaderIsRefusedWithoutAsking() async {
        let check = await client.checkDeviceToken("café-token-abcdef")
        XCTAssertEqual(check, .refused)
        XCTAssertEqual(StubURLProtocol.requestCount, 0, "nothing was sent")
    }

    // ---- pure ---------------------------------------------------------------

    func testRetryAfterIsReadAsSecondsOrADateClampedAndDefaulted() {
        XCTAssertEqual(TokenHealth.retryAfterMs("120"), 120_000)
        XCTAssertEqual(TokenHealth.retryAfterMs(nil), TokenHealth.defaultWaitMs)
        XCTAssertEqual(TokenHealth.retryAfterMs("soon"), TokenHealth.defaultWaitMs)
        XCTAssertEqual(TokenHealth.retryAfterMs("0"), 1_000, "clamped up: a zero wait is still a wait")
        XCTAssertEqual(TokenHealth.retryAfterMs("86400"), TokenHealth.maxWaitMs)
        XCTAssertEqual(TokenHealth.retryAfterMs(" 30 "), 30_000, "the header is trimmed")
        let wall: Int64 = 1_800_000_000_000
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "GMT")
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        let header = formatter.string(from: Date(timeIntervalSince1970: Double(wall + 30_000) / 1000))
        XCTAssertEqual(TokenHealth.retryAfterMs(header, now: { wall }), 30_000)
    }

    func testOnlyTheDeviceTiersThrottleCounts() {
        XCTAssertTrue(TokenHealth.isDeviceTokenThrottle(
            #"{"detail":"too many wrong X-Device-Token values — retry in 53s, or sign in as an admin"}"#
        ))
        // The wording since 2026-10-17, which names the usual cause.
        XCTAssertTrue(TokenHealth.isDeviceTokenThrottle(
            #"{"detail":"too many wrong X-Device-Token values from this address — retry in 53s. A device or browser tab at this address may still be sending an old household token (after a reinstall or a token change): pair it again or close it, or sign in as an admin"}"#
        ))
        XCTAssertFalse(TokenHealth.isDeviceTokenThrottle(#"{"detail":"too many failed attempts — retry in 30s"}"#))
        XCTAssertFalse(TokenHealth.isDeviceTokenThrottle(nil))
    }

    func testAWaitIsKeyedByTheNormalisedAddressAndNeverShortened() {
        let clock = Shared<Int64>(1_000)
        let health = TokenHealth(clock: { clock.current })
        health.noteWait("http://10.0.0.9:6369/", waitMs: 60_000)
        XCTAssertEqual(health.waitMs("http://10.0.0.9:6369"), 60_000, "one spelling, one wait")
        health.noteWait("http://10.0.0.9:6369", waitMs: 1_000)
        XCTAssertEqual(health.waitMs("http://10.0.0.9:6369"), 60_000, "the longer wait wins")
        health.noteWait("http://10.0.0.9:6369", waitMs: 120_000)
        XCTAssertEqual(health.waitMs("http://10.0.0.9:6369"), 120_000)
        XCTAssertEqual(health.activeWaits()["http://10.0.0.9:6369"], 120_000)
        clock.advance(200_000)
        XCTAssertEqual(health.waitMs("http://10.0.0.9:6369"), 0, "a wait in the past is no wait")
        XCTAssertTrue(health.activeWaits().isEmpty)
        health.clearWait("http://10.0.0.9:6369")
        XCTAssertEqual(health.waitMs("http://10.0.0.9:6369"), 0)
    }

    /// The default clock is the one the comment claims: Darwin's
    /// `CLOCK_MONOTONIC`, which `man 3 clock_gettime` says keeps running
    /// while the system sleeps. A unit test cannot put a Mac to sleep, so
    /// what is checked here is the part that can be — that it moves forward
    /// and is not the wall clock (a clock measured from boot is far smaller
    /// than the epoch's milliseconds).
    func testTheDefaultClockIsMonotonicAndNotTheWallClock() {
        let first = TokenHealth.monotonicMs()
        XCTAssertGreaterThan(first, 0)
        XCTAssertLessThan(first, Int64(Date().timeIntervalSince1970 * 1000) / 2)
        XCTAssertGreaterThanOrEqual(TokenHealth.monotonicMs(), first)
    }
}
