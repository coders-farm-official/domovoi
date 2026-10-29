import Foundation
import XCTest
@testable import DomovoiKit

/// The LAN sweep and the manual-add probe ask `/api/health` and `/api/config`
/// of hosts that are not (yet) the server, before the trust dialog. **Neither
/// request may carry the household token** — until 2026-10-08 both did, for
/// every one of the 254 addresses swept (security round 3, A6-02).
///
/// The twin of `android/.../net/DiscoveryTest.kt`. Nothing here touches a
/// network: every probe goes through ``StubURLProtocol``, which is the point
/// as well as the convenience — this batch was told not to sweep anything,
/// and a suite that quietly opened 254 sockets would be the first step
/// towards one.
final class DiscoveryTests: XCTestCase {

    /// The phone is paired with the server at `10.0.0.9:6369`, which is the
    /// worst case: a probe of the ACTIVE server is inside the token's scope,
    /// so only the probe's own rule keeps the token off it.
    private let server = "http://10.0.0.9:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeClient(token: String? = "household-abc") -> DomovoiClient {
        DomovoiClient(
            book: .fixed(server: server, token: token),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 2)
        )
    }

    private var identityBlock: String {
        #"{"algorithm":"ed25519","public_key":"\#(TestIdentity.real.publicKey)","fingerprint":"\#(TestIdentity.real.fingerprint)"}"#
    }

    // ---- the probe carries nothing -----------------------------------------

    func testAProbeCarriesNoTokenEvenToTheActiveServer() async throws {
        // Hoisted: the dispatcher is `@Sendable` and an XCTestCase is not.
        let block = identityBlock
        StubURLProtocol.dispatch { request in
            switch request.url?.path {
            case "/api/health": return .json(#"{"status":"ok","identity":\#(block)}"#)
            case "/api/config": return .json(#"{"bot_name":"kitchen-box"}"#)
            default: return .json("{}", status: 404)
            }
        }
        let hit = await Discovery.probe(makeClient().unauthenticatedProbe, base: server + "/")
        XCTAssertEqual(server, hit?.url, "the trailing slash is off")
        XCTAssertEqual("kitchen-box", hit?.name)
        XCTAssertEqual(TestIdentity.real.fingerprint, hit?.fingerprint)
        XCTAssertEqual(TestIdentity.real.pin, hit?.identity)
        XCTAssertEqual(["/api/health", "/api/config"], StubURLProtocol.recorded.map { $0.url?.path ?? "" })
        for request in StubURLProtocol.recorded {
            XCTAssertNil(request.value(forHTTPHeaderField: deviceTokenHeader), "\(request.url!) saw a token")
        }
    }

    func testAProbeSendsNoRequestedWithHeaderEither() async throws {
        // Android's two probes build a bare `Request.Builder`: both routes
        // are open reads with no side effect for `X-Requested-With` to
        // protect, and the phone's probe should look like any other reader.
        StubURLProtocol.enqueue(.json(#"{"status":"ok"}"#), .json("{}"))
        _ = await Discovery.probe(makeClient().unauthenticatedProbe, base: server)
        XCTAssertNil(StubURLProtocol.header("X-Requested-With", at: 0))
    }

    func testAHostThatIsNotADashboardIsNotAHit() async throws {
        StubURLProtocol.enqueue(.json("nope", status: 404))
        let miss = await Discovery.probe(makeClient().unauthenticatedProbe, base: server)
        XCTAssertNil(miss)
        XCTAssertEqual(1, StubURLProtocol.requestCount, "no /api/config after a miss")
        XCTAssertNil(StubURLProtocol.header(deviceTokenHeader, at: 0))
    }

    func testAHostThatDoesNotAnswerIsNotAHitAndIsNotAnError() async throws {
        StubURLProtocol.enqueue(.init(error: URLError(.cannotConnectToHost)))
        let silent = await Discovery.probe(makeClient().unauthenticatedProbe, base: server)
        XCTAssertNil(silent)
    }

    func testAnyTwoHundredIsAHitWhateverTheBodyIs() async throws {
        // Kept from Android deliberately: a random web server on :6369
        // counts, with no identity and no name. The trust dialog shows what
        // was found and the identity gate is what decides whether it is our
        // Domovoi — narrowing it here would hide a server whose /api/health
        // has changed shape, which is the thing a person adding a server by
        // hand most needs to see.
        StubURLProtocol.dispatch { request in
            request.url?.path == "/api/health"
                ? .json("<html>router login</html>")
                : .json("<html>router login</html>", status: 500)
        }
        let hit = await Discovery.probe(makeClient().unauthenticatedProbe, base: server)
        XCTAssertEqual(server, hit?.url)
        XCTAssertNil(hit?.identity, "nothing to pin")
        XCTAssertNil(hit?.name)
    }

    func testAConfigThatFailsCostsOnlyTheName() async throws {
        StubURLProtocol.enqueue(.json(#"{"status":"ok","identity":\#(identityBlock)}"#), .json("{}", status: 500))
        let hit = await Discovery.probe(makeClient().unauthenticatedProbe, base: server)
        XCTAssertNil(hit?.name)
        XCTAssertEqual(TestIdentity.real.fingerprint, hit?.fingerprint)
    }

    func testTheProbeRefusesAPublicAddressInTheClearBeforeConnecting() async throws {
        // The cleartext rule stays on the probe client, as it stays on
        // Android's: `Discovery.client` removes only the auth interceptor.
        let hit = await Discovery.probe(makeClient().unauthenticatedProbe, base: "http://93.184.216.34:6369")
        XCTAssertNil(hit)
        XCTAssertEqual(0, StubURLProtocol.requestCount, "nothing was even attempted")
    }

    // ---- where a sweep looks ------------------------------------------------

    func testASweepIsAlwaysTheSlashTwentyFourAroundThePhone() {
        let targets = Discovery.sweepTargets(localIPv4: "192.168.1.57")
        XCTAssertEqual(254, targets.count)
        XCTAssertEqual("http://192.168.1.1:6369", targets.first)
        XCTAssertEqual("http://192.168.1.254:6369", targets.last)
        // The phone's own address is included, as on Android.
        XCTAssertTrue(targets.contains("http://192.168.1.57:6369"))
        // A /16 home network is still swept as the /24 around the phone,
        // which is Android's behaviour and not a bug being ported blindly:
        // the prefix is everything before the last dot, whatever the mask.
        XCTAssertEqual("http://10.5.0.1:6369", Discovery.sweepTargets(localIPv4: "10.5.0.99").first)
        XCTAssertTrue(Discovery.sweepTargets(localIPv4: "no dots").isEmpty)
    }

    func testTheSweepOfAPhoneWithNoLanIsItsOwnAnswerAndNotAnEmptyList() async {
        // Android returns an empty list here and the picker says "nothing
        // answered", which is the wrong thing to say to someone on cellular.
        // Nothing is probed either way.
        let outcome = await Discovery.scan(makeClient().unauthenticatedProbe, port: 6369)
        if Discovery.localIPv4() == nil {
            XCTAssertEqual(.noLocalNetwork, outcome)
            XCTAssertEqual(0, StubURLProtocol.requestCount)
        } else {
            // The build Mac IS on a LAN, so this is the other branch: the
            // sweep ran and every one of its requests went to the stub.
            XCTAssertNotEqual(.noLocalNetwork, outcome)
            XCTAssertEqual(254, StubURLProtocol.recorded.filter { $0.url?.path == "/api/health" }.count)
        }
    }

    func testAScanOfAPhoneThatIsOnALanProbesTwoHundredAndFiftyFourAddressesWithNoToken() async throws {
        // The real path, through the real client, on a stubbed transport:
        // this is the test that holds A6-02 shut. 254 health probes, every
        // one of them bare, including the one to the active server.
        try XCTSkipIf(Discovery.localIPv4() == nil, "SKIPPED, NOT PASSED — the build Mac has no site-local IPv4, so there is no /24 to sweep even in a stub")
        StubURLProtocol.dispatch { request in
            // Exactly one address answers, and it is the active server's, so
            // the hit is the one a token WOULD have gone to.
            request.url?.host == "10.0.0.9" ? .json(#"{"status":"ok"}"#) : .json("", status: 404)
        }
        let outcome = await Discovery.scan(makeClient().unauthenticatedProbe)
        let health = StubURLProtocol.recorded.filter { $0.url?.path == "/api/health" }
        XCTAssertEqual(254, health.count)
        XCTAssertTrue(StubURLProtocol.recorded.allSatisfy { $0.value(forHTTPHeaderField: deviceTokenHeader) == nil },
                      "a token reached one of the 254 (finding A6-02)")
        // And the sweep really did reach the addresses the targets name.
        XCTAssertEqual(254, Set(health.compactMap { $0.url?.host }).count)
        _ = outcome
    }

    // ---- the sweep's concurrency and order ----------------------------------

    func testASweepRunsAtMostFortyProbesAtOnce() async {
        let live = Shared<(now: Int, peak: Int)>((0, 0))
        let targets = (1...254).map { "http://10.0.0.\($0):6369" }
        let found = await Discovery.sweep(targets, onProgress: { _, _, _ in }) { _ in
            live.mutate { $0.now += 1; $0.peak = max($0.peak, $0.now) }
            // A suspension, so the group really does have several in flight:
            // a probe that returns without suspending would finish before the
            // next is added and the peak would be 1 however wide the window.
            await Task.yield()
            try? await Task.sleep(nanoseconds: 1_000_000)
            live.mutate { $0.now -= 1 }
            return nil
        }
        XCTAssertTrue(found.isEmpty)
        XCTAssertLessThanOrEqual(live.current.peak, Discovery.concurrency)
        // ...and it really is a window and not one at a time, which is the
        // half that would make a sweep take four minutes.
        XCTAssertGreaterThan(live.current.peak, 1)
    }

    func testASweepProbesEveryTargetExactlyOnce() async {
        let seen = Shared<[String]>([])
        let targets = (1...254).map { "http://10.0.0.\($0):6369" }
        _ = await Discovery.sweep(targets, onProgress: { _, _, _ in }) { base in
            seen.mutate { $0.append(base) }
            return nil
        }
        XCTAssertEqual(254, seen.current.count)
        XCTAssertEqual(Set(targets), Set(seen.current))
    }

    func testProgressCountsEveryProbeOnceAndFindsAlongTheWay() async {
        let reports = Shared<[(Int, Int, Int)]>([])
        let targets = (1...10).map { "http://10.0.0.\($0):6369" }
        _ = await Discovery.sweep(targets, onProgress: { done, total, found in
            reports.mutate { $0.append((done, total, found)) }
        }) { base in
            base.hasSuffix("3:6369") || base.hasSuffix("7:6369") ? FoundDomovoi(url: base) : nil
        }
        let all = reports.current
        XCTAssertEqual(10, all.count)
        XCTAssertEqual(Set(1...10), Set(all.map(\.0)), "each probe reported its own `done`, once")
        XCTAssertTrue(all.allSatisfy { $0.1 == 10 })
        XCTAssertEqual(2, all.last?.2)
        // `done` and `found` are read together, under one lock: a report can
        // never claim more finds than probes.
        XCTAssertTrue(all.allSatisfy { $0.2 <= $0.0 })
    }

    func testMultipleHitsAreAllKeptAndSortedByUrlStringAsAndroidSortsThem() async {
        let targets = ["http://10.0.0.20:6369", "http://10.0.0.100:6369", "http://10.0.0.3:6369"]
        let found = await Discovery.sweep(targets, onProgress: { _, _, _ in }) { FoundDomovoi(url: $0) }
        // A PLAIN STRING SORT, so `…100` comes before `…20`. Android's
        // `sortedBy { it.url }` does this and the two apps must list a
        // house's servers in the same order, so it is ported rather than
        // improved; the batch report asks for a numeric sort on both.
        XCTAssertEqual(
            ["http://10.0.0.100:6369", "http://10.0.0.20:6369", "http://10.0.0.3:6369"],
            found.map(\.url)
        )
        XCTAssertEqual(3, found.count, "no auto-pick and no ranking")
    }

    // ---- the Local Network permission ---------------------------------------

    func testLocalNetworkDeniedNeedsBothHalvesOfThePath() {
        // Pinned in `NetworkWatchTests` against a live path as well. The trap:
        // `unsatisfiedReason` is `.notAvailable` on a SATISFIED path, so the
        // reason alone says nothing, and a sweep that found nothing because
        // the person refused the permission would read as "nothing answered"
        // — which is a lie they cannot act on (TN3179: there is no API that
        // says whether it was granted).
        //
        // There is no way to construct an `NWPath`, so this holds the
        // *outcome* type instead: I1-C has a case to branch on, and it is not
        // the same case as an empty sweep.
        XCTAssertEqual([], SweepOutcome.localNetworkDenied.servers)
        XCTAssertEqual([], SweepOutcome.noLocalNetwork.servers)
        XCTAssertNotEqual(SweepOutcome.swept(servers: []), .localNetworkDenied)
        XCTAssertNotEqual(SweepOutcome.swept(servers: []), .noLocalNetwork)
        XCTAssertEqual([FoundDomovoi(url: "http://10.0.0.9:6369")], SweepOutcome.swept(servers: [FoundDomovoi(url: "http://10.0.0.9:6369")]).servers)
    }

    // ---- the progress a screen holds ----------------------------------------

    func testScanProgressKeepsDoneMonotonic() {
        var progress = ServerScanProgress()
        progress.start(total: 254)
        XCTAssertTrue(progress.running)
        progress.report(done: 40, total: 254, found: 1)
        progress.report(done: 39, total: 254, found: 1)
        XCTAssertEqual(40, progress.done, "a late probe from a superseded sweep cannot move the bar back")
        XCTAssertEqual(1, progress.found)
        progress.report(done: 41, total: 254, found: 0)
        XCTAssertEqual(1, progress.found, "nor un-find a server")
        progress.finish()
        XCTAssertFalse(progress.running)
        XCTAssertEqual(254, progress.done, "a finished sweep reads as finished")
        // ...and only `start` resets, which is what makes the rule hold
        // across a restart rather than within one sweep.
        progress.start(total: 254)
        XCTAssertEqual(0, progress.done)
        XCTAssertEqual(0, progress.found)
    }

    // ---- the manual-add path ------------------------------------------------

    func testAManualAddIsNormalisedThenProbedWithNoToken() async throws {
        // `ServerAddress.fromTyped` is I1-A's; this is the probe that follows
        // it, with Android's 3 s instead of the sweep's 700 ms because a
        // person is watching and a name has to be resolved.
        guard case .ok(let url) = ServerAddress.fromTyped("10.0.0.9") else {
            return XCTFail("a bare address should normalise")
        }
        XCTAssertEqual("http://10.0.0.9:6369", url)
        StubURLProtocol.enqueue(.json(#"{"status":"ok","identity":\#(identityBlock)}"#), .json(#"{"bot_name":"home"}"#))
        let hit = await Discovery.probe(makeClient().unauthenticatedProbe, base: url, timeout: Discovery.manualTimeout)
        XCTAssertEqual("home", hit?.name)
        XCTAssertNil(StubURLProtocol.header(deviceTokenHeader, at: 0))
        XCTAssertEqual(Discovery.manualTimeout, StubURLProtocol.recorded.first?.timeoutInterval)
    }

    func testTheSweepsTimeoutIsTheOneAndroidUses() async throws {
        StubURLProtocol.enqueue(.json(#"{"status":"ok"}"#), .json("{}"))
        _ = await Discovery.probe(makeClient().unauthenticatedProbe, base: server)
        XCTAssertEqual(0.7, StubURLProtocol.recorded.first?.timeoutInterval)
    }

    func testTrailingSlashesComeOffTheBaseWhateverThereAreOfThem() {
        XCTAssertEqual("http://10.0.0.9:6369", Discovery.trimmedTrailingSlashes("http://10.0.0.9:6369///"))
        XCTAssertEqual("http://10.0.0.9:6369", Discovery.trimmedTrailingSlashes("http://10.0.0.9:6369"))
        XCTAssertEqual("", Discovery.trimmedTrailingSlashes("///"))
    }
}
