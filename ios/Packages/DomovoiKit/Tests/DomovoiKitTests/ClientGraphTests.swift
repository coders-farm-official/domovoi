import XCTest
@testable import DomovoiKit

/// The circularity between the identity gate and the client, and the one
/// thing about it that must never be got wrong: **the gate's probe client
/// carries no gate.**
///
/// There is no Kotlin counterpart to port. Android resolves the same problem
/// with a forward reference in a lambda and `Discovery.client(api.http, …)`,
/// and its test for the property is indirect — the probe simply does not
/// deadlock in `IdentityGateTest`. These tests assert the wiring itself,
/// because the net that turns a hang into ``DomovoiError/probeWiringCycle(_:)``
/// is a net and not the design.
final class ClientGraphTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// A gate that records being asked anything, so a test can assert that a
    /// probe asked it nothing. The state is in a ``Shared`` rather than in
    /// plain properties because ``TokenGate/prepare(_:)`` is `async`.
    private final class RecordingGate: TokenGate {
        let prepared = Shared<[String]>([])
        let required = Shared<[String]>([])
        func prepare(_ base: ServerURL) async { prepared.mutate { $0.append(base.host) } }
        func requireAdmitted(_ base: ServerURL) throws { required.mutate { $0.append(base.host) } }
        func admitsPairingPrompt(_ base: ServerURL?) -> Bool { true }
    }

    // MARK: - the property the brief asks for

    /// **The one that must fail if the wiring is wrong.** Watched failing by
    /// changing `graph`'s first `DomovoiClient(… gate: nil …)` to pass the
    /// gate: it reports the gate.
    func testTheGatesProbeClientHoldsNoGate() {
        let gate = RecordingGate()
        let graph = DomovoiClient.graph(
            book: .fixed(server: "http://10.0.0.42:6369", token: "tttttttttttt"),
            health: TokenHealth(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session()
        ) { probe in
            XCTAssertNil(probe.gate, "the client handed to the gate must not already hold one")
            return gate
        }
        XCTAssertNil(graph.probe.gate, "the gate's probe client must carry no gate")
        XCTAssertNotNil(graph.app.gate, "the app's client must carry the gate")
        XCTAssertTrue(graph.gate === gate, "the gate the call returns is the one that was built")
    }

    /// The same property through the container, which is how the app gets
    /// here. The gate it builds is a real ``IdentityGate``, so this is the
    /// case that would recurse.
    func testTheContainerWiresItTheSameWay() {
        let container = makeContainer()
        XCTAssertNil(container.probeClient.gate)
        XCTAssertNotNil(container.client.gate)
        XCTAssertTrue((container.client.gate as? IdentityGate) === container.identity)
    }

    /// Both clients share the ingredients, so the cleartext and redirect
    /// policies — and the book, the token health and the household lock —
    /// cannot differ between them. This is Android's "every 'not our server'
    /// client is the real one copied, never built fresh", which here is "both
    /// come out of one call".
    func testBothClientsShareTheBookTheHealthAndTheLock() {
        let book = ServerBook.fixed(server: "http://10.0.0.42:6369", token: "tttttttttttt")
        let health = TokenHealth()
        let lock = HouseholdLock()
        let graph = DomovoiClient.graph(
            book: book, health: health, lock: lock,
            configuration: StubURLProtocol.session()
        ) { _ in RecordingGate() }
        XCTAssertEqual(graph.probe.baseURL, graph.app.baseURL)
        XCTAssertEqual(graph.probe.deviceToken, graph.app.deviceToken)
        XCTAssertTrue(graph.probe.health === health)
        XCTAssertTrue(graph.app.health === health)
        XCTAssertTrue(graph.probe.lock === lock)
        XCTAssertTrue(graph.app.lock === lock)
    }

    /// The cleartext policy is the one the map singles out: a hand-built
    /// second client is how you lose it. Both clients refuse a public address
    /// in the clear, before any connection is made.
    func testBothClientsRefusePlainHttpToAPublicAddress() async {
        let graph = DomovoiClient.graph(
            book: .fixed(server: "http://203.0.113.5:6369", token: "tttttttttttt"),
            health: TokenHealth(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session()
        ) { _ in RecordingGate() }
        let expected = DomovoiError.notPermitted(CleartextPolicy.refusalMessage("203.0.113.5"))
        for (which, client) in [("probe", graph.probe), ("app", graph.app)] {
            do {
                _ = try await client.raw(method: "GET", path: "/api/health")
                XCTFail("\(which): a public address in the clear must be refused")
            } catch {
                XCTAssertEqual(expected, error, "\(which): refused for the wrong reason")
            }
        }
        XCTAssertEqual(0, StubURLProtocol.requestCount, "nothing should have reached the transport")
    }

    // MARK: - what the gate's probe actually does

    /// A probe through the derived client sends no token and asks no gate.
    ///
    /// Two things at once, and both matter: `TokenMode.none` means nothing is
    /// carried (I1-A/B), and the absent gate means nothing is asked — so the
    /// recursion the whole shape exists to prevent has no way to start.
    func testAProbeThroughTheDerivedClientSendsNoTokenAndAsksNoGate() async throws {
        let gate = RecordingGate()
        let graph = DomovoiClient.graph(
            book: .fixed(server: "http://10.0.0.42:6369", token: "tttttttttttt"),
            health: TokenHealth(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session()
        ) { _ in gate }
        StubURLProtocol.enqueue(.json("{\"ok\":true}"))
        let answer = try await graph.probe.unauthenticatedProbe.get(
            URL(string: "http://10.0.0.42:6369/api/health?challenge=abc")!,
            timeout: 3
        )
        XCTAssertEqual(200, answer.status)
        XCTAssertEqual(1, StubURLProtocol.requestCount)
        XCTAssertNil(
            StubURLProtocol.header("X-Device-Token", at: 0),
            "a probe of the active server must carry no household token"
        )
        XCTAssertEqual([], gate.prepared.current, "the probe must not ask the gate to take a proof")
        XCTAssertEqual([], gate.required.current, "the probe must not ask the gate to admit it")
    }

    /// **A gate-less client still sends the household token on an ordinary
    /// `.scoped` request.** Measured, not assumed, and it is why
    /// ``AppContainer/probeClient`` is not public.
    ///
    /// ``DomovoiClient/tokenOutcome(for:wsUpgrade:)`` reads
    /// `if let gate { try gate.requireAdmitted(base) }`, so an absent gate is
    /// an absent check rather than a refusal. `gate: nil` therefore buys the
    /// probe client exactly one thing — it asks no gate — and buys it
    /// **nothing** about the token. The token is kept off the probe by
    /// ``UnauthenticatedProbe``'s `TokenMode.none`, which is a different
    /// mechanism entirely.
    ///
    /// I wrote the opposite in a comment first, and this test is what
    /// corrected it. It is left asserting the uncomfortable truth rather than
    /// the comfortable one, so the next person to reach for a gate-less
    /// client as a "safe" client finds out here.
    func testAGateLessClientStillSendsTheTokenOnAScopedRequest() async throws {
        let gate = RecordingGate()
        let graph = DomovoiClient.graph(
            book: .fixed(server: "http://10.0.0.42:6369", token: "household-abc"),
            health: TokenHealth(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session()
        ) { _ in gate }
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await graph.probe.raw(method: "GET", path: "/api/state", trackPairing: false)
        XCTAssertEqual([], gate.prepared.current, "no proof is taken for a client with no gate")
        XCTAssertEqual([], gate.required.current, "and no gate is asked to admit it")
        XCTAssertEqual(
            "household-abc", StubURLProtocol.header("X-Device-Token", at: 0),
            "an absent gate is an absent CHECK, not a refusal: the token goes out unproven"
        )
        // The app's client, with the gate, takes the proof first.
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await graph.app.raw(method: "GET", path: "/api/state", trackPairing: false)
        XCTAssertEqual(["10.0.0.42"], gate.prepared.current, "the app's client takes the proof")
        XCTAssertEqual("household-abc", StubURLProtocol.header("X-Device-Token", at: 1))
    }

    /// So what the container hands out is the probe, not the client: a type
    /// that carries no token whatever it is pointed at.
    ///
    /// The test above is the other half of this one: it points the *client*
    /// at the same path and finds the token on it. I did not sabotage this
    /// one separately, because the pair is the measurement — the same request
    /// through the probe carries nothing and through the client carries the
    /// token.
    func testTheContainerHandsOutAProbeAndNotAClient() async throws {
        let container = makeContainer()
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await container.unauthenticatedProbe.get(
            URL(string: "http://10.0.0.42:6369/api/state")!,
            timeout: 3
        )
        XCTAssertEqual(1, StubURLProtocol.requestCount)
        XCTAssertNil(
            StubURLProtocol.header("X-Device-Token", at: 0),
            "the probe carries nothing, even to the active server"
        )
    }

    /// The gate's own probe, wired through the container, reaches the
    /// transport once and comes back with a plain verdict.
    ///
    /// **This does not test the absence of the gate**, and the comment that
    /// said it did was wrong: `TokenMode.none` means `perform` never reaches
    /// `prepareIdentity` at all, so a probe client that held a gate would
    /// pass this too — I watched it do so. What it does pin is that the
    /// container's own wiring reaches the network once, with no token, and
    /// produces a verdict rather than ``DomovoiError/probeWiringCycle(_:)``.
    func testTheContainersGateProvesAServerWithoutReenteringItself() async {
        let identity = TestIdentity.real
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "tttttttttttt")
        store.pin(IdentityGate.pinKey("http://10.0.0.42:6369")!, identity.pin)
        let container = makeContainer(store: store)
        StubURLProtocol.dispatch { request in
            let challenge = Self.challenge(in: request.url)
            return .json((try? identity.healthBody(challenge: challenge)) ?? "{}")
        }
        let base = ServerURL.parse("http://10.0.0.42:6369")!
        await container.identity.prepare(base)
        let verdict = container.identity.cachedVerdict(base)
        guard case .verified(let fingerprint, let pinnedNow) = verdict else {
            return XCTFail("expected a proof, got \(String(describing: verdict))")
        }
        XCTAssertEqual(identity.pin.fingerprint, fingerprint)
        XCTAssertFalse(pinnedNow, "the pin was already there")
        XCTAssertEqual(1, StubURLProtocol.requestCount, "one probe, not a chain of them")
        XCTAssertNil(StubURLProtocol.header("X-Device-Token", at: 0))
    }

    // MARK: - helpers

    private func makeContainer(store: InMemoryShellStore? = nil) -> AppContainer {
        AppContainer(
            store: store ?? InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "tttttttttttt"),
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session()
        )
    }

    private static func challenge(in url: URL?) -> String? {
        guard let url, let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems
        else { return nil }
        return items.first { $0.name == "challenge" }?.value
    }
}
