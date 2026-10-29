import Foundation
import XCTest
@testable import DomovoiKit

/// The paste check: the five refusals **before** anything is sent, the
/// request itself, what each answer means, and the field's rules and words.
///
/// Added on Android 2026-10-17 after the owner pasted an old custom token
/// into a reinstalled server, it was saved without a word, and every page
/// then failed. The words are
/// `android/.../ui/screens/settings/PairingScreen.kt`'s, character for
/// character — this is the test that fails if only one phone's wording
/// moves.
final class TokenPasteFieldTests: XCTestCase {

    private let server = "http://10.0.0.9:6369"
    private let candidate = "household-candidate"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    /// The default gate is `AdmitsAnyServer`, stated rather than left out:
    /// most of this file's subject is the paste check's own rules — room
    /// mode, the wait, a candidate that is not header-safe, the `.candidate`
    /// mode on the first hop and nothing on any hop after it — and the tests
    /// whose subject is the gate pass `ClosedGate` or a real ``IdentityGate``.
    private func makeClient(
        server: String? = nil,
        token: String? = "household-saved",
        health: TokenHealth? = nil,
        lock: HouseholdLock = HouseholdLock(),
        gate: any TokenGate = AdmitsAnyServer()
    ) -> DomovoiClient {
        DomovoiClient(
            book: .fixed(server: server ?? self.server, token: token),
            gate: gate,
            health: health,
            lock: lock,
            configuration: StubURLProtocol.session(timeout: 2)
        )
    }

    /// A gate that always refuses, and one that always admits: the two shapes
    /// the paste check cares about.
    private struct ClosedGate: TokenGate {
        func requireAdmitted(_ base: ServerURL) throws { throw DomovoiError.identityHeld("not proved") }
        func admitsPairingPrompt(_ base: ServerURL?) -> Bool { false }
    }

    private struct OpenGate: TokenGate {
        func requireAdmitted(_ base: ServerURL) throws {}
        func admitsPairingPrompt(_ base: ServerURL?) -> Bool { true }
    }

    // ---- the request --------------------------------------------------------

    func testTheCheckIsOneDeviceTierReadCarryingTheCandidateAndNothingElse() async {
        StubURLProtocol.enqueue(.json(#"{"satellites":[]}"#))
        let result = await makeClient().checkDeviceToken(candidate)
        XCTAssertEqual(.accepted, result)
        XCTAssertEqual(1, StubURLProtocol.requestCount, "one attempt, never retried")
        let request = StubURLProtocol.recorded[0]
        XCTAssertEqual("/api/satellites", request.url?.path)
        XCTAssertEqual("GET", request.httpMethod)
        XCTAssertEqual(candidate, request.value(forHTTPHeaderField: deviceTokenHeader), "the candidate, not the saved token")
        XCTAssertEqual("DomovoiApp", request.value(forHTTPHeaderField: "X-Requested-With"))
        XCTAssertEqual(8, request.timeoutInterval, "Android's 8 s call timeout")
    }

    // ---- the five refusals, before anything is sent -------------------------

    func testRoomModeRefusesBeforeSendingAnything() async {
        let result = await makeClient(lock: HouseholdLock(roomModeActive: true)).checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, result)
        XCTAssertEqual(0, StubURLProtocol.requestCount)
    }

    func testNoParseableServerRefusesBeforeSendingAnything() async {
        for server in ["", "   ", "nonsense", "http://"] {
            StubURLProtocol.reset()
            let result = await makeClient(server: server).checkDeviceToken(candidate)
            XCTAssertEqual(.unavailable, result, server)
            XCTAssertEqual(0, StubURLProtocol.requestCount)
        }
    }

    func testACandidateAHeaderCouldNotCarryIsRefusedBeforeSendingAnything() async {
        // Blank, and anything outside 0x20…0x7E. `.refused` and not
        // `.unavailable`: the phone knows this one is wrong without asking.
        for bad in ["", "   ", "abc\ndef-123456", "abc\u{7F}def-123456", "héllo-token-123", "tab\there-123456"] {
            StubURLProtocol.reset()
            let result = await makeClient().checkDeviceToken(bad)
            XCTAssertEqual(.refused, result, bad.debugDescription)
            XCTAssertEqual(0, StubURLProtocol.requestCount)
        }
    }

    func testAnOutstandingWaitRefusesBeforeSendingAnything() async {
        let health = TokenHealth(clock: { 0 })
        health.noteWait(server, waitMs: 90_000)
        let result = await makeClient(health: health).checkDeviceToken(candidate)
        XCTAssertEqual(.throttled(waitMs: 90_000), result)
        XCTAssertEqual(0, StubURLProtocol.requestCount, "nothing is sent while the server says wait")
    }

    func testAGateThatRefusesRefusesBeforeSendingAnything() async {
        let result = await makeClient(gate: ClosedGate()).checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, result, "not `.refused`: the server gave no verdict on the token")
        XCTAssertEqual(0, StubURLProtocol.requestCount)
    }

    func testAGateThatAdmitsLetsTheCheckThrough() async {
        StubURLProtocol.enqueue(.json("{}"))
        let admitted = await makeClient(gate: OpenGate()).checkDeviceToken(candidate)
        XCTAssertEqual(.accepted, admitted)
    }

    func testTheRealGateHasItsProofTakenBeforeTheCheckRatherThanBeingRefused() async throws {
        // The split (``TokenGate``) in the one place it would have bitten:
        // the pairing screen may have NO saved token, so the token decision
        // never gets as far as `.ask` — which is why `checkDeviceToken`
        // prepares the gate itself. Without that, a fresh install's very
        // first paste would always have read "couldn't check it with the
        // server".
        //
        // (The review's fix A means `prepareIdentity` would now take a proof
        // for an unpaired phone too, so this is belt and braces rather than
        // the only route — but `checkDeviceToken` must take it whether or not
        // a token is saved, and that is its own rule, not the decision's.)
        let probes = Shared<Int>(0)
        StubURLProtocol.dispatch { request in
            switch request.url?.path {
            case "/api/health":
                probes.mutate { $0 += 1 }
                let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            default:
                return .json(#"{"satellites":[]}"#)
            }
        }
        let box = Shared<DomovoiClient?>(nil)
        let pins = OnePin()
        let gate = IdentityGate(
            probe: { base, challenge in try await IdentityGate.httpProbe(box.current!.unauthenticatedProbe)(base, challenge) },
            pins: pins
        )
        let client = DomovoiClient(
            book: .fixed(server: server, token: nil),   // not paired yet
            gate: gate,
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 2)
        )
        box.set(client)
        let checked = await client.checkDeviceToken(candidate)
        XCTAssertEqual(.accepted, checked)
        XCTAssertEqual(1, probes.current, "the check took its own proof")
    }

    private final class OnePin: IdentityGate.PinStore {
        private let state = Shared<[String: ServerIdentity.Pin]>([:])
        func pinFor(_ key: String) -> ServerIdentity.Pin? { state.current[key] }
        func pin(_ key: String, _ pin: ServerIdentity.Pin) { state.mutate { $0[key] = pin } }
        func mayPinOnFirstProof(_ key: String) -> Bool { true }
    }

    // ---- the response mapping ----------------------------------------------

    func testEveryTwoHundredIsAccepted() async {
        for status in [200, 201, 204, 299] {
            StubURLProtocol.reset()
            StubURLProtocol.enqueue(.json("{}", status: status))
            let result = await makeClient().checkDeviceToken(candidate)
            XCTAssertEqual(.accepted, result, "\(status)")
        }
    }

    func testAFourOhFourIsAcceptedToo() async {
        // An older server with no satellites route still proved it would
        // ANSWER this phone's token rather than refuse it, which is the only
        // question the check is asking.
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"detail":"Not Found"}"#, status: 404))
        let older = await makeClient().checkDeviceToken(candidate)
        XCTAssertEqual(.accepted, older)
    }

    func testOnlyTheServersOwnTwoRefusalsAreARefusal() async {
        for detail in [
            "X-Device-Token or admin session required",
            "X-Device-Token required — the dashboard cookie does not authorize device-tier actions",
        ] {
            for status in [401, 403] {
                StubURLProtocol.reset()
                StubURLProtocol.enqueue(.json(#"{"detail":"\#(detail)"}"#, status: status))
                let result = await makeClient().checkDeviceToken(candidate)
                XCTAssertEqual(.refused, result, "\(status) \(detail)")
            }
        }
        // The em dash is load-bearing: a hyphen is a different string and
        // must NOT read as the server's own verdict, because reading a
        // plugin's 401 as one is how a good token gets thrown away.
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"detail":"X-Device-Token required - the dashboard cookie does not authorize device-tier actions"}"#, status: 401))
        let hyphen = await makeClient().checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, hyphen)
        // A 401 from somewhere else that merely mentions the header is not
        // the server's verdict either (Android review N-T2).
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"detail":"a plugin wants an X-Device-Token"}"#, status: 403))
        let plugin = await makeClient().checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, plugin)
    }

    func testAFiveOhThreeIsCouldNotCheckAndNotRefused() async {
        // The map calls this out on its own: 503 is "couldn't check", not
        // "refused". A box that is restarting must not make a person throw
        // away a token that works.
        StubURLProtocol.enqueue(.json(#"{"detail":"starting"}"#, status: 503))
        let restarting = await makeClient().checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, restarting)
    }

    func testEveryOtherStatusAndAnUnreachableServerAreCouldNotCheck() async {
        for status in [400, 418, 500, 502, 504] {
            StubURLProtocol.reset()
            StubURLProtocol.enqueue(.json("{}", status: status))
            let result = await makeClient().checkDeviceToken(candidate)
            XCTAssertEqual(.unavailable, result, "\(status)")
        }
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.init(error: URLError(.cannotConnectToHost)))
        let silent = await makeClient().checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, silent)
    }

    func testAThrottleThatNamesTheTokenIsAWaitAndIsRemembered() async {
        let health = TokenHealth(clock: { 0 })
        StubURLProtocol.enqueue(.json(
            #"{"detail":"too many wrong device tokens from this address"}"#,
            status: 429,
            headers: ["Retry-After": "120"]
        ))
        let result = await makeClient(health: health).checkDeviceToken(candidate)
        XCTAssertEqual(.throttled(waitMs: 120_000), result)
        // Remembered, so the next request does not have to find out again —
        // Android's `health.noteWait`.
        XCTAssertEqual(120_000, health.waitMs(server))
    }

    func testAThrottleThatDoesNotNameTheTokenIsCouldNotCheck() async {
        let health = TokenHealth(clock: { 0 })
        StubURLProtocol.enqueue(.json(#"{"detail":"slow down"}"#, status: 429, headers: ["Retry-After": "120"]))
        let result = await makeClient(health: health).checkDeviceToken(candidate)
        XCTAssertEqual(.unavailable, result)
        XCTAssertEqual(0, health.waitMs(server), "a rate limit that is not about the token teaches nothing")
    }

    func testRetryAfterIsParsedClampedAndDefaulted() async {
        // I1-A's parser, reused rather than rewritten, and exercised through
        // the paste check because that is one of its two call sites.
        let cases: [(String?, Int64)] = [
            ("120", 120_000),
            ("0", 1_000),              // clamped up to 1 s
            ("100000", 15 * 60_000),   // clamped down to 15 min
            (nil, 60_000),             // the default
            ("not a number", 60_000),
        ]
        for (header, expected) in cases {
            StubURLProtocol.reset()
            var headers: [String: String] = [:]
            if let header { headers["Retry-After"] = header }
            StubURLProtocol.enqueue(.json(#"{"detail":"too many wrong device tokens"}"#, status: 429, headers: headers))
            let result = await makeClient(health: TokenHealth(clock: { 0 })).checkDeviceToken(candidate)
            XCTAssertEqual(.throttled(waitMs: expected), result, header ?? "nil")
        }
    }

    // ---- the field's rules --------------------------------------------------

    func testTheFieldIsLiveOnlyForTwelveToOneHundredAndTwentyEightPrintableAscii() {
        XCTAssertFalse(TokenPasteField.isUsable(""))
        XCTAssertFalse(TokenPasteField.isUsable("   "))
        XCTAssertFalse(TokenPasteField.isUsable(String(repeating: "a", count: 11)))
        XCTAssertTrue(TokenPasteField.isUsable(String(repeating: "a", count: 12)))
        XCTAssertTrue(TokenPasteField.isUsable(String(repeating: "a", count: 128)))
        XCTAssertFalse(TokenPasteField.isUsable(String(repeating: "a", count: 129)))
        // Space is allowed inside, and trimmed at the edges — so a pasted
        // token with a stray newline on the end is usable, which is most
        // pastes.
        XCTAssertTrue(TokenPasteField.isUsable("  household abc1  \n"))
        XCTAssertEqual("household abc1", TokenPasteField.trimmed("  household abc1  \n"))
        // A tab is refused, as on Android: the server trims it at the edges
        // and refuses it anywhere else.
        XCTAssertFalse(TokenPasteField.isUsable("household\tabc12"))
        XCTAssertFalse(TokenPasteField.isUsable("household-é-abc"))
    }

    func testTheHintNamesTheAlphabetBeforeTheLength() {
        XCTAssertNil(TokenPasteField.hint(""), "nothing to say about an empty field")
        XCTAssertNil(TokenPasteField.hint("   "))
        XCTAssertNil(TokenPasteField.hint(String(repeating: "a", count: 12)), "nor about a usable one")
        XCTAssertEqual("at least 12 characters", TokenPasteField.hint("short"))
        XCTAssertEqual("letters, digits, punctuation and spaces only", TokenPasteField.hint("ab\u{7F}"))
        // The order is the point: a four-character paste with a control
        // character in it is told about the alphabet, because that is the
        // part the person cannot see.
        XCTAssertEqual("letters, digits, punctuation and spaces only", TokenPasteField.hint("a\u{1}b"))
        XCTAssertEqual("letters, digits, punctuation and spaces only", TokenPasteField.hint("héllo-token-123"))
    }

    // ---- the words ----------------------------------------------------------

    func testTheMessagesAreAndroidsWordForWord() {
        XCTAssertNil(TokenPasteField.message(nil))
        XCTAssertNil(TokenPasteField.message(.accepted), "nothing is said about a token that worked")
        XCTAssertEqual(
            "That token doesn't match this server. Copy it from Settings → Phones & browsers → Household token "
                + "on this server's dashboard.",
            TokenPasteField.message(.refused)
        )
        XCTAssertEqual(
            "Couldn't check it with the server — try again.",
            TokenPasteField.message(.unavailable)
        )
        XCTAssertEqual(
            "Too many tries — wait 2:00 before the next one.",
            TokenPasteField.message(.throttled(waitMs: 120_000), waitLeftMs: 120_000)
        )
        // A wait that has run out says so rather than naming 0:00.
        XCTAssertEqual("You can try again now.", TokenPasteField.message(.throttled(waitMs: 120_000), waitLeftMs: 0))
        // The clock rounds UP, so it never reads 0:00 while the server still
        // waits — ``waitClock(_:)``, shared with the trouble card.
        XCTAssertEqual(
            "Too many tries — wait 0:01 before the next one.",
            TokenPasteField.message(.throttled(waitMs: 1), waitLeftMs: 1)
        )
        XCTAssertEqual(
            "Too many tries — wait 5:00 before the next one.",
            TokenPasteField.message(.throttled(waitMs: 300_000), waitLeftMs: 300_000)
        )
    }

    func testTheRefusalMessageNamesTheDashboardPathTheMenuActuallyHas() {
        // Renamed on the dashboard 2026-10-21: "Phones & browsers", not
        // "Connection". A message that names a menu entry which no longer
        // exists is worse than no message.
        let refused = TokenPasteField.message(.refused) ?? ""
        XCTAssertTrue(refused.contains("Settings → Phones & browsers → Household token"))
        XCTAssertFalse(refused.contains("Connection"))
    }
}
