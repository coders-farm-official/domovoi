import Foundation
import XCTest
@testable import DomovoiKit

/// Before the household token goes to the saved server on a network, the
/// server proves it holds the key this phone pinned (security round 3,
/// A6-03): the client asks the gate, the gate asks `/api/health?challenge=…`
/// without a token, and only a signed answer from the pinned key lets the
/// request out. A different key, or no identity where one is pinned, and the
/// request never leaves. A rogue's `401` does not raise the pairing screen
/// either.
///
/// The cases are `android/.../net/IdentityGateTest.kt`'s, driven through
/// ``StubURLProtocol`` where Android drives a `MockWebServer`, plus the ones
/// that only exist because this gate is split in two (``TokenGate``).
final class IdentityGateTests: XCTestCase {

    // ---- a Domovoi that is not a Domovoi -----------------------------------

    /// The fake web backend's switches, each one a case from the Kotlin's
    /// `FakeDomovoi`.
    private let identity = Shared<TestIdentity?>(TestIdentity.real)
    private let healthStatus = Shared<Int>(200)
    /// The web backend answers, but its core did not (`web/backend/main.py`).
    private let coreDown = Shared<Bool>(false)
    /// Nothing answers the health route at all — the Kotlin's `healthStalls`,
    /// as a transport failure rather than a stalled socket, because a stub
    /// that never answers would cost the suite its timeout.
    private let healthFails = Shared<Bool>(false)

    private let server = "http://10.0.0.9:6369"
    private let now = Shared<Int64>(1_000_000)
    /// The network fingerprint the phone is on (``NetworkWatch`` in the app).
    private let net = Shared<String>("P|default|satisfied|dns,ipv4,wifi||en0/wifi/14||192.168.1.1:0||")
    private let token = Shared<String?>("household-abc")
    private let log = Shared<[String]>([])
    /// Extra work the fake does when a health probe arrives — the hook the
    /// straddling-network tests need.
    private let onProbe = Shared<Int>(0)
    private var straddle: (@Sendable (Int) -> Void)?

    private var pins: TestPins!
    private var gate: IdentityGate!
    private var client: DomovoiClient!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        pins = TestPins()
        wire()
    }

    /// Wired as the app's container wires it: the probe runs on a copy of the
    /// app's own client that cannot carry a token, and the verdict is keyed
    /// to the network fingerprint.
    private func wire() {
        let box = Shared<DomovoiClient?>(nil)
        let identity = self.identity
        let healthStatus = self.healthStatus
        let coreDown = self.coreDown
        let healthFails = self.healthFails
        let onProbe = self.onProbe
        let straddle = self.straddle
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            switch path {
            case "/api/health":
                let count = onProbe.mutate { $0 += 1; return $0 }
                straddle?(count)
                if healthFails.current { return .init(error: URLError(.cannotConnectToHost)) }
                if healthStatus.current != 200 { return .json("{}", status: healthStatus.current) }
                if coreDown.current { return .json(TestIdentity.coreDownBody) }
                guard let identity = identity.current else { return .json(TestIdentity.noIdentityBody) }
                let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "challenge" }?.value
                return .json((try? identity.healthBody(challenge: challenge)) ?? "{}")
            case "/api/refuse":
                return .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
            default:
                return .json("{}")
            }
        }
        let now = self.now
        let net = self.net
        let log = self.log
        gate = IdentityGate(
            probe: { base, challenge in
                try await IdentityGate.httpProbe(box.current!.unauthenticatedProbe)(base, challenge)
            },
            pins: pins,
            network: { net.current },
            clock: { now.current },
            log: { line in log.mutate { $0.append(line) } }
        )
        let token = self.token
        let server = self.server
        client = DomovoiClient(
            book: ServerBook(activeServer: { server }, tokenForServer: { _ in token.current }),
            gate: gate,
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 2)
        )
        box.set(client)
    }

    /// The pin book, with the Kotlin's `decided` set: the servers whose trust
    /// decision was taken with an identity in view.
    private final class TestPins: IdentityGate.PinStore {
        private let state = Shared<(book: [String: ServerIdentity.Pin], decided: Set<String>)>(([:], []))

        var book: [String: ServerIdentity.Pin] { state.current.book }

        func pinFor(_ key: String) -> ServerIdentity.Pin? { state.current.book[key] }
        func pin(_ key: String, _ pin: ServerIdentity.Pin) { state.mutate { $0.book[key] = pin } }
        func mayPinOnFirstProof(_ key: String) -> Bool { !state.current.decided.contains(key) }

        func put(_ key: String, _ pin: ServerIdentity.Pin) { state.mutate { $0.book[key] = pin } }
        func decide(_ key: String) { state.mutate { $0.decided.insert(key) } }
        func undecide() { state.mutate { $0.decided = [] } }
    }

    // ---- what the tests say ------------------------------------------------

    private var key: String { IdentityGate.pinKey(server)! }
    private var base: ServerURL { TokenScope.baseOf(server)! }

    @discardableResult
    private func read() async throws -> [String: String] {
        try await client.get("/api/timers", as: [String: String].self)
    }

    private func failedRead() async -> DomovoiError? {
        do {
            _ = try await read()
            return nil
        } catch let error as DomovoiError {
            return error
        } catch {
            XCTFail("not a DomovoiError: \(error)")
            return nil
        }
    }

    /// The message of a refusal, or a clean failure.
    ///
    /// **Never `error!`.** A force-unwrap that finds nil here traps, and a
    /// trap takes the whole `xctest` process with it — the run reports
    /// "unexpected signal code 5" and every test after this one simply does
    /// not happen, so one broken expectation hides the rest of the suite.
    /// Found by deliberately breaking the gate: the suite crashed instead of
    /// reporting four failures.
    private func heldMessage(_ error: DomovoiError?, line: UInt = #line) -> String {
        guard let error else {
            XCTFail("expected the request to be refused", line: line)
            return ""
        }
        return error.message
    }

    private var probes: [URLRequest] { StubURLProtocol.recorded.filter { $0.url?.path == "/api/health" } }
    private var reads: [URLRequest] { StubURLProtocol.recorded.filter { $0.url?.path == "/api/timers" } }

    private func logged(_ fragment: String) -> Bool {
        log.current.contains { $0.contains(fragment) }
    }

    // ---- first contact -----------------------------------------------------

    func testTheFirstContactPinsTheServerAndTheTokenFlows() async throws {
        try await read()
        // One token-less probe carrying a challenge, then the read with the
        // token.
        XCTAssertEqual(1, probes.count)
        XCTAssertNil(probes[0].value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertTrue(probes[0].url!.query?.contains("challenge=") == true)
        XCTAssertEqual(32, URLComponents(url: probes[0].url!, resolvingAgainstBaseURL: false)?
            .queryItems?.first { $0.name == "challenge" }?.value?.count)
        XCTAssertEqual("household-abc", reads.first?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertEqual(TestIdentity.real.fingerprint, pins.book[key]?.fingerprint)
        XCTAssertEqual(.verified(fingerprint: TestIdentity.real.fingerprint, pinnedNow: true), gate.status?.verdict)
        XCTAssertTrue(logged("identity: pinned "))

        // The proof holds for the rest of this network: no second probe.
        try await read()
        try await read()
        XCTAssertEqual(1, probes.count)
        XCTAssertEqual(3, reads.count)
    }

    func testAServerFromBeforeIdentityIsTreatedAsBeforeAndSaidSo() async throws {
        identity.set(nil)
        try await read()
        XCTAssertEqual("household-abc", reads.first?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertTrue(pins.book.isEmpty)
        XCTAssertEqual(.legacy(offered: nil), gate.status?.verdict)
        XCTAssertTrue(logged("offers no identity"))
    }

    // ---- the pin is the trust decision -------------------------------------

    func testTheIdentityTheDialogPinnedWinsOverWhoeverAnswersFirst() async throws {
        // The picker's dialog showed the real server's advertised key and the
        // person said yes, so the pin was written then. The first network the
        // phone meets afterwards has a rogue at the address, proving its own
        // key.
        pins.put(key, TestIdentity.real.pin)
        pins.decide(key)
        identity.set(TestIdentity.rogue)
        let error = await failedRead()
        guard case .identityHeld(let reason) = error else {
            return XCTFail("expected the gate to hold the token, got \(String(describing: error))")
        }
        XCTAssertTrue(reason.contains("mismatch"), reason)
        XCTAssertTrue(reads.isEmpty, "the rogue saw a token-less probe and nothing else")
        // Home again: the real key proves itself against the dialog's pin.
        identity.set(TestIdentity.real)
        gate.networkChanged()
        try await read()
        XCTAssertEqual(.verified(fingerprint: TestIdentity.real.fingerprint, pinnedNow: false), gate.status?.verdict)
    }

    func testAServerTrustedWhileItOfferedNoIdentityIsNotPinnedBehindTheUsersBack() async throws {
        // The dialog showed no identity (an older web backend) and the person
        // trusted it anyway; the server has since been updated and proves a
        // key. The token goes out as it always did, nothing is pinned
        // silently, and Settings can pin what it offers.
        pins.decide(key)
        try await read()
        XCTAssertEqual("household-abc", reads.first?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertTrue(pins.book.isEmpty, "nothing pinned")
        XCTAssertEqual(.legacy(offered: TestIdentity.real.pin), gate.status?.verdict)
        XCTAssertTrue(logged("has not pinned"))
        // An upgraded install (trusted before the dialog showed identities)
        // is still pinned on its first proof.
        pins.undecide()
        gate.networkChanged()
        try await read()
        XCTAssertEqual(TestIdentity.real.fingerprint, pins.book[key]?.fingerprint)
    }

    // ---- after a network change --------------------------------------------

    func testAfterANetworkChangeADifferentKeyHoldsTheTokenBack() async throws {
        try await read()
        gate.networkChanged()
        // Away from home: whoever answers at the saved address has its own key.
        identity.set(TestIdentity.rogue)
        let error = await failedRead()
        XCTAssertTrue(heldMessage(error).contains("mismatch"), heldMessage(error))
        XCTAssertEqual(2, probes.count, "the rogue saw a token-less probe and nothing else")
        XCTAssertEqual(1, reads.count)
        XCTAssertNil(probes[1].value(forHTTPHeaderField: deviceTokenHeader))
        guard case .mismatch(let expected, let seen, let seenPin) = gate.status?.verdict else {
            return XCTFail("expected a mismatch, got \(String(describing: gate.status?.verdict))")
        }
        XCTAssertEqual(TestIdentity.real.fingerprint, expected)
        XCTAssertEqual(TestIdentity.rogue.fingerprint, seen)
        XCTAssertEqual(TestIdentity.rogue.fingerprint, seenPin?.fingerprint, "the key it proved travels with the verdict")
        // A server in the mismatch state is NOT an answer: the shell drops to
        // local media rather than claiming the house is reachable.
        let answers = await client.answers()
        XCTAssertFalse(answers, "not our server, so out of reach")
        // The pin is untouched: coming home, the real server proves itself.
        XCTAssertEqual(TestIdentity.real.fingerprint, pins.book[key]?.fingerprint)
    }

    func testAReinstalledServerIsTrustedAgainOnlyByReplacingThePinAndNeverGetsTheOldToken() async throws {
        // 2026-10-17: the same address, a reinstalled Domovoi with a new key.
        // The token is held until the person says "it's mine": the shell then
        // pins the key the server PROVED (the verdict carries it), DROPS the
        // old household's token (finding S-T1) and asks the gate to forget
        // the old verdict.
        try await read()
        identity.set(TestIdentity.rogue)
        gate.networkChanged()
        _ = await failedRead()
        guard case .mismatch(_, _, let seenPin) = gate.status?.verdict, let seen = seenPin else {
            return XCTFail("expected a mismatch carrying the key it proved")
        }
        XCTAssertEqual(1, reads.count, "nothing re-trusted by itself")

        pins.put(key, seen)   // the shell's replacePin, after the yes ...
        token.set(nil)        // ... which drops the old household's token
        gate.pinChanged()
        try await read()
        XCTAssertEqual(2, reads.count)
        XCTAssertNil(reads.last?.value(forHTTPHeaderField: deviceTokenHeader), "the old token never reaches the new key")
        // Paired again with the new household's token: it flows, proved
        // against the new pin.
        token.set("new-household")
        try await read()
        XCTAssertEqual("new-household", reads.last?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertEqual(.verified(fingerprint: TestIdentity.rogue.fingerprint, pinnedNow: false), gate.status?.verdict)
    }

    func testAfterANetworkChangeTheSameServerProvesItselfOnceMore() async throws {
        try await read()
        gate.networkChanged()
        try await read()
        XCTAssertEqual(2, probes.count)
        XCTAssertEqual(2, reads.count)
        XCTAssertEqual(.verified(fingerprint: TestIdentity.real.fingerprint, pinnedNow: false), gate.status?.verdict)
    }

    func testAPinnedServerThatOffersNoIdentityIsNotTrustedWithTheToken() async throws {
        try await read()
        gate.networkChanged()
        identity.set(nil)   // a plain web server at the saved address
        let error = await failedRead()
        XCTAssertTrue(heldMessage(error).contains("unproven"), heldMessage(error))
        XCTAssertEqual(1, reads.count)
        guard case .unproven = gate.status?.verdict else {
            return XCTFail("expected unproven, got \(String(describing: gate.status?.verdict))")
        }
    }

    // ---- what is remembered, and for how long ------------------------------

    func testARefusalIsRememberedThenRetried() async throws {
        try await read()
        gate.networkChanged()
        identity.set(nil)
        for _ in 0..<3 { _ = await failedRead() }
        XCTAssertEqual(2, probes.count, "one probe, not one per attempt")
        now.advance(IdentityGate.retryAfterMs)
        _ = await failedRead()
        XCTAssertEqual(3, probes.count)
        // The real server is back at the address (home again): the next retry
        // proves it and the read goes through.
        identity.set(TestIdentity.real)
        now.advance(IdentityGate.retryAfterMs)
        try await read()
        XCTAssertEqual(2, reads.count)
    }

    func testAProofOlderThanTheTtlIsTakenAgainOnTheSameNetwork() async throws {
        try await read()
        now.advance(IdentityGate.verdictTtlMs - 1)
        try await read()
        XCTAssertEqual(1, probes.count, "still within the ttl")
        now.advance(1)
        try await read()
        XCTAssertEqual(2, probes.count, "the ttl ran out: proved again")
        XCTAssertEqual(3, reads.count)
    }

    func testAVerdictHoldsOnlyOnTheNetworkFingerprintItWasTakenOn() async throws {
        try await read()
        XCTAssertEqual(1, probes.count)
        // The VPN case: nothing called networkChanged(), but the watch's
        // fingerprint moved (the Wi-Fi under the VPN was replaced).
        net.set("P|default|satisfied|ipv4,other||utun3/other/20||||;I|en0|||||10.5.0.99/24|||")
        try await read()
        XCTAssertEqual(2, probes.count, "proved again on the new network")
        XCTAssertEqual(2, reads.count)
        // The same fingerprint again: the proof stands.
        try await read()
        XCTAssertEqual(2, probes.count)
        // And back on the first network, the old proof is not revived — one
        // entry per server, not one per network, exactly as the Kotlin.
        net.set("P|default|satisfied|dns,ipv4,wifi||en0/wifi/14||192.168.1.1:0||")
        try await read()
        XCTAssertEqual(3, probes.count)
    }

    func testComingBackToTheForegroundAfterASpellInTheBackgroundProvesAgain() async throws {
        try await read()
        // The first start is not a return from the background.
        gate.appForegrounded()
        try await read()
        XCTAssertEqual(1, probes.count)
        // Home button, then back (or screen off, then unlock).
        gate.appBackgrounded()
        now.advance(5_000)
        gate.appForegrounded()
        try await read()
        XCTAssertEqual(2, probes.count)
        XCTAssertTrue(logged("back in the foreground"))
        // And a second foreground with no backgrounding in between proves
        // nothing new.
        gate.appForegrounded()
        try await read()
        XCTAssertEqual(2, probes.count)
    }

    // ---- a proof that straddled a network change ---------------------------

    func testAProofThatStraddledANetworkChangeBelongsToTheNewNetwork() async throws {
        // The path update lands while the probe is in flight: the answer came
        // over one network, the request would leave on another. The gate asks
        // again rather than trusting a proof it cannot place.
        let net = self.net
        straddle = { count in
            if count == 1 { net.set("P|default|satisfied|dns,ipv4,wifi||en0/wifi/14||192.168.1.254:0||") }
        }
        wire()
        try await read()
        XCTAssertEqual(2, probes.count, "the straddling proof was thrown away and taken again")
        XCTAssertEqual(1, reads.count)
        XCTAssertTrue(logged("changed while"))
        guard case .verified = gate.cachedVerdict(base) else {
            return XCTFail("the second proof should have been kept")
        }
        // ...and under the old fingerprint nothing was remembered.
        net.set("P|default|satisfied|dns,ipv4,wifi||en0/wifi/14||192.168.1.1:0||")
        XCTAssertNil(gate.cachedVerdict(base))
    }

    func testAProofThatKeepsStraddlingGivesUpAsUnavailableAndCachesNothing() async throws {
        // Three attempts, then the gate stops: not an impostor, nothing
        // cached, and the token held.
        let net = self.net
        straddle = { count in net.set("P|default|satisfied|changing-\(count)|||||") }
        wire()
        let error = await failedRead()
        XCTAssertEqual(IdentityGate.proofAttempts, probes.count)
        XCTAssertTrue(heldMessage(error).contains("not fully up"), heldMessage(error))
        XCTAssertTrue(heldMessage(error).contains("the network kept changing"), heldMessage(error))
        XCTAssertFalse(heldMessage(error).contains("mismatch"), "never an impostor")
        XCTAssertTrue(reads.isEmpty)
        guard case .unavailable = gate.status?.verdict else {
            return XCTFail("expected unavailable, got \(String(describing: gate.status?.verdict))")
        }
        XCTAssertNil(gate.cachedVerdict(base), "nothing cached: the next request asks again")
    }

    // ---- not a verdict: unreachable, non-200, a core that is down ----------

    func testAServerThatDoesNotAnswerIsUnreachableNotRemembered() async throws {
        try await read()
        gate.networkChanged()
        healthFails.set(true)
        let error = await failedRead()
        // The health probe failed at the transport, so the gate has no
        // verdict and the token is held. **This is where iOS and Android
        // report different words for the same outcome**, and it falls out of
        // the split: Android's gate throws the probe's own IOException
        // through the interceptor, so the toast reads "(offline?)"; here
        // `prepare` swallows an unreachable probe (it is not a verdict about
        // who the server is) and the synchronous reader then finds no verdict
        // and holds. Both refuse to send the token and both say the server
        // cannot be relied on; the iPhone says "has not proved itself" where
        // the Android phone says "offline". Written up in the batch report.
        XCTAssertTrue(heldMessage(error).contains("has not proved itself"), heldMessage(error))
        XCTAssertEqual(1, reads.count)
        XCTAssertNil(gate.cachedVerdict(base), "nothing cached for this network")
    }

    func testAHealthThatIsNot200ProvesNothing() async throws {
        try await read()
        gate.networkChanged()
        healthStatus.set(404)
        let error = await failedRead()
        XCTAssertTrue(heldMessage(error).contains("did not answer /api/health"), heldMessage(error))
        XCTAssertTrue(heldMessage(error).contains("unproven"), heldMessage(error))
    }

    func testAServerWhoseCoreIsDownIsNotAnImpostorAndIsAskedAgainAtOnce() async throws {
        try await read()
        gate.networkChanged()
        // The core restarts (an update, a reboot): the web hop answers but
        // passes no identity through.
        coreDown.set(true)
        let error = await failedRead()
        XCTAssertTrue(heldMessage(error).contains("not fully up"), heldMessage(error))
        XCTAssertFalse(heldMessage(error).contains("mismatch"), "never an impostor")
        XCTAssertEqual(1, reads.count)
        guard case .unavailable = gate.status?.verdict else {
            return XCTFail("expected unavailable, got \(String(describing: gate.status?.verdict))")
        }
        XCTAssertNil(gate.cachedVerdict(base), "nothing cached: the next request asks again")
        _ = await failedRead()
        XCTAssertEqual(3, probes.count, "asked again at once, not after the refusal window")
        // The pin is untouched, and when the core is back the proof goes
        // through with no network change and nothing from the person.
        XCTAssertEqual(TestIdentity.real.fingerprint, pins.book[key]?.fingerprint)
        coreDown.set(false)
        try await read()
        XCTAssertEqual(2, reads.count)
        XCTAssertEqual(.verified(fingerprint: TestIdentity.real.fingerprint, pinnedNow: false), gate.status?.verdict)
    }

    func testAServerWhoseCoreIsDownHoldsTheTokenEvenWhenNothingIsPinned() async throws {
        // No pin yet (an upgraded install's first contact happens to meet a
        // core restart): still not Legacy — the token waits for the proof.
        coreDown.set(true)
        let error = await failedRead()
        XCTAssertTrue(heldMessage(error).contains("not fully up"), heldMessage(error))
        XCTAssertTrue(reads.isEmpty)
        XCTAssertTrue(pins.book.isEmpty)
    }

    // ---- the split: what a synchronous reader does with no verdict ---------

    func testTheSynchronousGateHoldsTheTokenWhenThereIsNoVerdict() throws {
        // Nothing has prepared anything. This is the case `requireAdmitted`'s
        // doc comment argues about: the gate cannot probe from here, so the
        // only two answers are "send it to a server that has not proved
        // itself" and "hold it".
        XCTAssertNil(gate.cachedVerdict(base))
        do {
            try gate.requireAdmitted(base)
            XCTFail("the token must be held when there is no verdict")
        } catch let error as DomovoiError {
            XCTAssertTrue(error.message.contains("has not proved itself"), error.message)
            // The address is in the message, as it is in all four of the
            // Kotlin's: the shell shows this and a person has to know which
            // box it is about.
            XCTAssertTrue(error.message.contains("10.0.0.9:6369"), error.message)
        }
        // The pairing prompt is the same answer for the same reason: no
        // verdict is a no, so a rogue's 401 cannot invite a paste.
        XCTAssertFalse(gate.admitsPairingPrompt(base))
        XCTAssertFalse(gate.admitsPairingPrompt(nil))
    }

    func testADownloadTokenIsHeldUntilSomethingHasPreparedAProof() async throws {
        // `tokenForDownload` is synchronous for the same reason the hop is.
        let file = URL(string: server + "/api/videos/1/file")!
        do {
            _ = try client.tokenForDownload(file)
            XCTFail("expected the token to be held")
        } catch {
            // Caught untyped and matched afterwards: I1-A's report §6 records
            // a Swift 6.3 SILGen crash on `catch DomovoiError.x` inside an
            // `async` function calling a typed-throws function, which is
            // exactly this shape.
            // `tokenForDownload` has typed throws, so `error` is already a
            // `DomovoiError` and a cast would warn. Matched by message
            // rather than by pattern: I1-A's report §6 records a Swift 6.3
            // SILGen crash on `catch DomovoiError.x` inside an `async`
            // function calling a typed-throws function, which is this shape.
            XCTAssertTrue(error.message.contains("has not proved itself"), error.message)
        }
        // After a proof it is handed over — which is why the method's doc
        // comment tells the caller to `await prepareIdentity` first.
        await client.prepareIdentity(forAddress: server)
        XCTAssertEqual("household-abc", try client.tokenForDownload(file))
    }

    func testASocketUpgradeMintsNothingUntilAProofHasBeenTaken() async throws {
        let upgrade = try XCTUnwrap(client.webSocketUpgrade("ws://10.0.0.9:6369/ws/state"))
        XCTAssertNil(upgrade.mint(), "a held token is not a bare socket")
        await client.prepareIdentity(forAddress: "ws://10.0.0.9:6369/ws/state", wsUpgrade: true)
        XCTAssertEqual("household-abc", upgrade.mint()?.value(forHTTPHeaderField: deviceTokenHeader))
        // And a mismatch on the next network closes it again, at the next
        // mint rather than at the next app launch.
        identity.set(TestIdentity.rogue)
        gate.networkChanged()
        await client.prepareIdentity(forAddress: "ws://10.0.0.9:6369/ws/state", wsUpgrade: true)
        XCTAssertNil(upgrade.mint())
    }

    func testAVerdictThatExpiresMIDCHAINStopsTheChainRatherThanCarryingTheToken() async throws {
        // The hop is the one place a verdict can run out between the proof
        // and the request, because `prepare` happens once per call and the
        // chain can take a while. Hop 1 carries the token; the clock then
        // passes the TTL; hop 2 asks, finds nothing usable, and the chain
        // stops instead of sending the token to a server whose proof has
        // expired.
        try await read()
        let now = self.now
        StubURLProtocol.dispatch { request in
            switch request.url?.path {
            case "/api/timers":
                now.advance(IdentityGate.verdictTtlMs)
                return .moved(302, to: "/api/timers/all")
            default:
                return .json("{}")
            }
        }
        let error = await failedRead()
        XCTAssertTrue(heldMessage(error).contains("has not proved itself"), heldMessage(error))
        // Hop 1 carried the token (its proof was good); hop 2 was never sent.
        let hops = StubURLProtocol.recorded.filter { $0.url?.path.hasPrefix("/api/timers") == true }
        XCTAssertEqual("/api/timers", hops.last?.url?.path, "nothing was sent to the hop's URL")
    }

    // ---- the other paths through the same client ---------------------------

    func testARoguesRefusalDoesNotRaiseThePairingScreen() async throws {
        try await read()
        gate.networkChanged()
        identity.set(TestIdentity.rogue)
        // An unpaired phone (nothing to protect, so the request goes out
        // without a token) meets a 401 that names the header...
        token.set(nil)
        do {
            _ = try await client.get("/api/refuse", as: [String: String].self)
            XCTFail("expected a refusal")
        } catch {
            XCTAssertEqual(false, error.failure?.deviceTokenRequired, "not a pairing refusal from a server that is not ours")
        }
        XCTAssertFalse(client.pairingRequired)
        client.notePossiblePairingRefusal(status: 401, body: "X-Device-Token or admin session required")
        XCTAssertFalse(client.pairingRequired)

        // From the real server the same refusal does ask the phone to pair.
        identity.set(TestIdentity.real)
        gate.networkChanged()
        token.set("household-abc")
        await client.prepareIdentity(forAddress: server)
        do {
            _ = try await client.get("/api/refuse", as: [String: String].self)
            XCTFail("expected a refusal")
        } catch {
            XCTAssertEqual(true, error.failure?.deviceTokenRequired)
        }
        XCTAssertTrue(client.pairingRequired)
    }

    func testTheGateIsNotAskedForAForeignHost() async throws {
        _ = try await client.get("http://10.0.0.10:6369/api/x", as: [String: String].self)
        XCTAssertNil(StubURLProtocol.recorded.first?.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertTrue(probes.isEmpty, "no probe anywhere")
    }

    func testAnUnpairedPhoneProbesNoServerAboutItsIdentity() async throws {
        // The five rules in front of the gate are what keeps this true, and
        // `prepareIdentity` applies them: a phone with no token has nothing
        // to protect, so it asks nobody to prove anything and the request
        // goes out bare, exactly as Android's interceptor never reaches its
        // gate in that case.
        token.set(nil)
        try await read()
        XCTAssertTrue(probes.isEmpty)
        XCTAssertEqual(1, reads.count)
        XCTAssertNil(reads[0].value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertNil(gate.status)
    }

    func testRoomModeProbesNoServerEither() async throws {
        let lock = HouseholdLock(roomModeActive: true)
        let token = self.token
        let server = self.server
        let client = DomovoiClient(
            book: ServerBook(activeServer: { server }, tokenForServer: { _ in token.current }),
            gate: gate,
            lock: lock,
            configuration: StubURLProtocol.session(timeout: 2)
        )
        _ = try await client.get("/api/timers", as: [String: String].self)
        XCTAssertTrue(probes.isEmpty, "a room asks nobody about the household's server")
        XCTAssertNil(reads[0].value(forHTTPHeaderField: deviceTokenHeader))
    }

    func testOneProbePerServerWhenRequestsRace() async throws {
        // Twenty reads at once: the Kotlin holds a per-key lock so the others
        // wait for the first probe's answer. Here they share its task.
        // The client, not `self`: an XCTestCase is not `Sendable` and a task
        // group's closure is.
        let client = self.client!
        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<20 {
                group.addTask {
                    _ = try? await client.get("/api/timers", as: [String: String].self)
                }
            }
        }
        XCTAssertEqual(1, probes.count, "twenty racing reads, one proof")
        XCTAssertEqual(20, reads.count)
        XCTAssertTrue(reads.allSatisfy { $0.value(forHTTPHeaderField: deviceTokenHeader) == "household-abc" })
    }

    // ---- the pin key -------------------------------------------------------

    func testPinKeysAreOneSpellingPerServer() {
        XCTAssertEqual("http://domovoi.lan:6369", IdentityGate.pinKey("http://Domovoi.LAN:6369/"))
        XCTAssertEqual("http://domovoi.lan:6369", IdentityGate.pinKey("http://domovoi.lan:6369"))
        XCTAssertEqual("https://domovoi.lan:443", IdentityGate.pinKey("https://domovoi.lan"))
        // `ws://` is read as its http form, as `TokenScope.baseOf` reads it,
        // so the state socket and the JSON calls share one pin.
        XCTAssertEqual("http://domovoi.lan:6369", IdentityGate.pinKey("ws://domovoi.lan:6369"))
        XCTAssertNil(IdentityGate.pinKey(""))
        XCTAssertNil(IdentityGate.pinKey("nonsense"))
        XCTAssertNil(IdentityGate.pinKey(nil))
        // An IPv6 literal keeps its brackets off in the key, as the parser
        // leaves it — one spelling, whatever the person typed.
        XCTAssertEqual("http://fc00::1:6369", IdentityGate.pinKey("http://[FC00::1]:6369"))
    }

    func testTheVerdictsAnswerWhatTheyAllow() {
        XCTAssertTrue(IdentityVerdict.verified(fingerprint: "f", pinnedNow: false).admitsToken)
        XCTAssertTrue(IdentityVerdict.legacy(offered: nil).admitsToken)
        XCTAssertFalse(IdentityVerdict.mismatch(expected: "a", seen: "b", seenPin: nil).admitsToken)
        XCTAssertFalse(IdentityVerdict.unproven(expected: nil, reason: "r").admitsToken)
        XCTAssertFalse(IdentityVerdict.unavailable(expected: nil, reason: "r").admitsToken)
        // Everything is cacheable except `unavailable`, which is not a
        // verdict at all.
        XCTAssertTrue(IdentityVerdict.verified(fingerprint: "f", pinnedNow: true).cacheable)
        XCTAssertTrue(IdentityVerdict.legacy(offered: nil).cacheable)
        XCTAssertTrue(IdentityVerdict.mismatch(expected: "a", seen: "b", seenPin: nil).cacheable)
        XCTAssertTrue(IdentityVerdict.unproven(expected: nil, reason: "r").cacheable)
        XCTAssertFalse(IdentityVerdict.unavailable(expected: nil, reason: "r").cacheable)
    }
}
