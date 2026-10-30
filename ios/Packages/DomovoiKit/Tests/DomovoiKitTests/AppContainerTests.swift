import XCTest
@testable import DomovoiKit

/// The one small graph: what it owns, in what order, and the two directions
/// of the gate/client circularity.
///
/// There is no Kotlin test to port — `AppContainer.kt` has none, which is
/// part of why the forward references in it are only checked by the app
/// running. The gate-less probe client is `ClientGraphTests`; this file is
/// the rest: the network-change wiring, the foreground epoch, the deep-link
/// allow-list, and the inputs the shell reads.
final class AppContainerTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func makeContainer(
        store: InMemoryShellStore = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc"),
        lock: HouseholdLock = HouseholdLock(),
        clock: @Sendable @escaping () -> Int64 = { 0 }
    ) -> AppContainer {
        AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: lock,
            clock: clock,
            configuration: StubURLProtocol.session(timeout: 1)
        )
    }

    // MARK: - construction

    /// Nothing is left nil, and the pieces the shell reads are the pieces the
    /// container built — not copies.
    func testEverythingIsBuiltAndTheClientGetsThePiecesTheContainerOwns() {
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        let container = makeContainer(store: store)
        XCTAssertEqual("http://10.0.0.42:6369", container.client.baseURL)
        XCTAssertEqual("household-abc", container.client.deviceToken)
        XCTAssertTrue(container.client.health === container.tokenHealth)
        XCTAssertTrue((container.client.gate as? IdentityGate) === container.identity)
        // The book reads the store live, rather than having copied it: a
        // server switch must be seen by the next request, not by the next
        // launch.
        store.trustServer("http://10.0.0.7:6369", identity: nil)
        XCTAssertTrue(store.setServerURL("http://10.0.0.7:6369"))
        XCTAssertEqual("http://10.0.0.7:6369", container.client.baseURL)
    }

    /// The container does **not** start the network monitors: `start()` does.
    /// A container built in a test takes no side effect on the process.
    func testTheWatchIsNotStartedUntilStartIsCalled() {
        let container = makeContainer()
        XCTAssertEqual(NetworkWatch.none, container.network.fingerprint)
        XCTAssertEqual(0, container.network.changeCount)
    }

    // MARK: - the circularity, the other way round

    /// A network change invalidates every verdict: the watch's `onChange`
    /// reaches the gate, which is Android's `identity.networkChanged()`.
    ///
    /// Watched failing by not calling `sink.connect(…)` in `init`: the cached
    /// verdict survives the change, and `missedNetworkChanges` goes to 1.
    func testANetworkChangeMakesTheServerProveItselfAgain() async {
        let identity = TestIdentity.real
        let lines = Shared<[String]>([])
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        store.pin(IdentityGate.pinKey("http://10.0.0.42:6369")!, identity.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1),
            log: { who, what in lines.mutate { $0.append("\(who): \(what)") } }
        )
        StubURLProtocol.dispatch { request in
            let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "challenge" }?.value
            return .json((try? identity.healthBody(challenge: challenge)) ?? "{}")
        }
        // Seed the watch FIRST. The first picture is not a change
        // (`NetworkWatch.recompute`), and a proof taken before it would be
        // stale for the fingerprint rather than for the epoch — which would
        // make this test pass without the wiring under test.
        container.network.seen(.path, "default") { facts in
            var next = facts
            next.status = "satisfied"
            next.addresses = ["192.168.1.5/24"]
            return next
        }
        XCTAssertEqual(0, container.network.changeCount, "the first picture is not a change")
        let base = ServerURL.parse("http://10.0.0.42:6369")!
        await container.identity.prepare(base)
        XCTAssertNotNil(container.identity.cachedVerdict(base), "a proof was taken")

        // A new lease on the same interface: the phone moved.
        container.network.seen(.path, "default") { facts in
            var next = facts
            next.addresses = ["10.1.2.3/24"]
            return next
        }
        XCTAssertEqual(1, container.network.changeCount)
        // The wiring itself: the gate was told, and said so in its own log.
        XCTAssertTrue(
            lines.current.contains("IdentityGate: network changed; the server will have to prove its identity again"),
            "the watch's onChange must reach IdentityGate.networkChanged(); log was \(lines.current)"
        )
        XCTAssertNil(
            container.identity.cachedVerdict(base),
            "the epoch moved, so the verdict taken on the old network is stale"
        )
        XCTAssertEqual(0, container.networkSink.missed, "nothing arrived before the sink was connected")
    }

    /// The hole in the middle of that wiring, on its own: a callback that
    /// arrives before anything is connected is **counted**, not lost in
    /// silence. In the container it cannot arrive — no monitor exists before
    /// `start()`, and `init` connects the sink before it returns — but the
    /// number is there to be read rather than argued about.
    func testADeferredSinkCountsWhatArrivedBeforeItWasConnected() {
        let lines = Shared<[String]>([])
        let sink = DeferredSink(log: { line in lines.mutate { $0.append(line) } })
        sink.fire()
        sink.fire()
        XCTAssertEqual(2, sink.missed)
        XCTAssertEqual(2, lines.current.count)
        XCTAssertTrue(lines.current[0].contains("nothing was invalidated"), lines.current[0])

        let fired = Shared<Int>(0)
        sink.connect { fired.mutate { $0 += 1 } }
        sink.fire()
        XCTAssertEqual(1, fired.current)
        XCTAssertEqual(2, sink.missed, "connecting does not retrospectively deliver what was missed")
    }

    // MARK: - the foreground epoch (map §9.2)

    /// `appForegrounded()` bumps the epoch only if a backgrounding happened
    /// first, so the first start is not a return. That rule is the gate's;
    /// this asserts the container does not defeat it.
    /// Asserted through the gate's own log rather than through a cached
    /// verdict, deliberately: ``AppContainer/appForegrounded()`` also calls
    /// ``NetworkWatch/refresh()``, which re-reads the machine's real
    /// interfaces, and a verdict can go stale because the fingerprint moved
    /// instead of because the epoch did. The log line is only written when
    /// the epoch is bumped, so it says which happened.
    func testTheFirstForegroundingIsNotAReturnButTheSecondIs() {
        let lines = Shared<[String]>([])
        let container = AppContainer(
            store: InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc"),
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1),
            log: { who, what in lines.mutate { $0.append("\(who): \(what)") } }
        )
        container.appForegrounded()
        XCTAssertFalse(
            lines.current.contains { $0.contains("back in the foreground") },
            "a first start is not a return: \(lines.current)"
        )
        container.appBackgrounded()
        container.appForegrounded()
        XCTAssertTrue(
            lines.current.contains { $0.contains("back in the foreground") },
            "the phone may have moved to a network the watch could not see change: \(lines.current)"
        )
    }

    /// The other half of `appForegrounded`: the interface addresses are
    /// re-read, because the phone may have moved to a network no monitor saw
    /// change. Skips, announcing it, on a machine with no interface to read.
    func testForegroundingReReadsTheInterfaces() throws {
        let readable = NetworkInterfaces.current().contains { $0.isUp && !$0.isLoopback }
        try XCTSkipUnless(readable, "this machine reports no interface that is up and not loopback")
        let container = makeContainer()
        XCTAssertEqual(NetworkWatch.none, container.network.fingerprint, "nothing has been read yet")
        container.appForegrounded()
        XCTAssertNotEqual(
            NetworkWatch.none, container.network.fingerprint,
            "appForegrounded must re-read the interfaces, not only bump the epoch"
        )
    }

    // MARK: - room mode (map §2.10)

    /// Room mode seals the token before anything can make a request. On
    /// Android the flag is read before `AppContainer` is built at all; here
    /// the lock is passed in, so a container built with a sealed lock must
    /// send nothing.
    func testRoomModeSealsTheTokenBeforeAnyRequestIsBuilt() async throws {
        let lock = HouseholdLock(roomModeActive: true)
        let container = makeContainer(lock: lock)
        StubURLProtocol.enqueue(.json("{}"))
        _ = try await container.client.raw(method: "GET", path: "/api/state", trackPairing: false)
        XCTAssertEqual(1, StubURLProtocol.requestCount)
        XCTAssertNil(
            StubURLProtocol.header("X-Device-Token", at: 0),
            "no request carries the household token while room mode is on"
        )
    }

    // MARK: - out-of-band navigation (map §1.5)

    /// The allow-list: a deep link may not name an arbitrary screen.
    ///
    /// Watched failing by letting `requestRoute` set anything: `"settings"`
    /// lands and `pendingRoute` is no longer nil.
    func testOnlyThreeRouteNamesMayComeFromOutside() {
        XCTAssertEqual(["home", "containers", "chat"], AppContainer.routesFromOutside)
        for allowed in ["home", "containers", "chat"] {
            let container = makeContainer()
            XCTAssertTrue(container.requestRoute(allowed), allowed)
            XCTAssertEqual(allowed, container.pendingRoute)
        }
        for refused in ["settings", "picker", "music", "", "home/../settings", "HOME"] {
            let container = makeContainer()
            XCTAssertFalse(container.requestRoute(refused), refused)
            XCTAssertNil(container.pendingRoute, refused)
        }
    }

    /// The picker is reachable from inside the app and from nowhere outside
    /// it: a forget from Settings reopens it (map §10.4), a notification
    /// cannot.
    func testThePickerIsAskedForFromInsideTheAppOnly() {
        let container = makeContainer()
        XCTAssertFalse(AppContainer.routesFromOutside.contains(AppContainer.pickerRoute))
        XCTAssertFalse(container.requestRoute("picker"))
        container.requestPicker()
        XCTAssertEqual("picker", container.pendingRoute)
    }

    /// Taking it clears it, so a later recomposition does not navigate twice.
    func testTakingAPendingRouteClearsIt() {
        let container = makeContainer()
        XCTAssertNil(container.takePendingRoute())
        container.requestRoute("chat")
        XCTAssertEqual("chat", container.takePendingRoute())
        XCTAssertNil(container.pendingRoute)
        XCTAssertNil(container.takePendingRoute())
    }

    // MARK: - what the shell reads (map §8.3)

    func testTheInputsOfAFreshInstall() {
        let container = makeContainer(store: InMemoryShellStore())
        let inputs = container.shellInputs()
        XCTAssertEqual("", inputs.serverURL)
        XCTAssertFalse(inputs.preferLocal)
        XCTAssertFalse(inputs.pairingRequired)
        XCTAssertNil(inputs.trouble)
        XCTAssertEqual(.localMedia(.noServer), shellScreen(
            serverURL: inputs.serverURL,
            unreachable: false,
            pairingRequired: inputs.pairingRequired,
            preferLocal: inputs.preferLocal,
            identityChanged: inputs.identityChanged,
            trouble: inputs.trouble
        ))
        XCTAssertEqual(.noServer, container.serverChoice(inputs, label: "", unreachable: false))
    }

    /// A paired phone whose token the server refused: the card, and a server
    /// card in the dialog that says the token is wanted.
    func testARefusedTokenBecomesTheCardAndThePickableServer() {
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        let container = makeContainer(store: store)
        store.markTokenRejected("http://10.0.0.42:6369")
        let inputs = container.shellInputs()
        XCTAssertEqual(.notRecognised(waitMs: 0), inputs.trouble)
        XCTAssertFalse(inputs.pairingRequired, "no refusal was seen on the wire")
        let choice = container.serverChoice(inputs, label: "box", unreachable: false)
        XCTAssertEqual(.available(label: "box", needsPairing: true), choice)
        XCTAssertEqual("available · needs the household token", choice.reason)
        // And the card holds the workspace up even while the probes fail.
        XCTAssertEqual(.tokenTroubleCard(.notRecognised(waitMs: 0)), shellScreen(
            serverURL: inputs.serverURL,
            unreachable: true,
            pairingRequired: inputs.pairingRequired,
            preferLocal: inputs.preferLocal,
            identityChanged: inputs.identityChanged,
            trouble: inputs.trouble
        ))
    }

    /// A wait, with no token this phone knows to be refused: the other card.
    func testAWaitBecomesTheWaitingCard() {
        let now = Shared<Int64>(1_000_000)
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { now.current },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        container.tokenHealth.noteWait("http://10.0.0.42:6369", waitMs: 42_000)
        XCTAssertEqual(.waiting(waitMs: 42_000), container.shellInputs().trouble)
        now.advance(42_000)
        XCTAssertNil(container.shellInputs().trouble, "over: the workspace, which asks again")
    }

    /// An unpaired phone has no card, whatever the server said about tokens:
    /// a wait only matters while there is a token to wait with.
    func testAnUnpairedPhoneHasNoCard() {
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369")
        let container = makeContainer(store: store)
        store.markTokenRejected("http://10.0.0.42:6369")
        XCTAssertNil(container.shellInputs().trouble)
    }

    /// Only **this** server's verdict is read: another server's is ignored
    /// (`AppShell.kt:172`).
    ///
    /// Watched failing by dropping the `status.base == key` check: the
    /// verdict taken for the other address turns this server into "a
    /// different Domovoi".
    func testAnotherServersVerdictIsIgnored() async {
        let identity = TestIdentity.real
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        // The pin belongs to a DIFFERENT address, and that is the address the
        // proof is taken for — so the status the gate publishes is about a
        // server this phone is not on.
        store.pin(IdentityGate.pinKey("http://10.0.0.99:6369")!, TestIdentity.rogue.pin)
        let container = makeContainer(store: store)
        StubURLProtocol.dispatch { request in
            let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "challenge" }?.value
            return .json((try? identity.healthBody(challenge: challenge)) ?? "{}")
        }
        await container.identity.prepare(ServerURL.parse("http://10.0.0.99:6369")!)
        guard case .mismatch = container.identity.status?.verdict else {
            return XCTFail("expected the other address to read as a mismatch")
        }
        let inputs = container.shellInputs()
        XCTAssertFalse(inputs.notOurs, "that verdict is about another server")
        XCTAssertFalse(inputs.identityChanged)
        XCTAssertFalse(inputs.coreDown)
    }

    /// The three identity readings the shell makes of the five verdicts.
    func testWhichVerdictMeansWhatToTheShell() async {
        let cases: [(IdentityVerdict, notOurs: Bool, coreDown: Bool, changed: Bool)] = [
            (.verified(fingerprint: "SHA256:a", pinnedNow: false), false, false, false),
            (.legacy(offered: nil), false, false, false),
            (.mismatch(expected: "SHA256:a", seen: "SHA256:b", seenPin: nil), true, false, true),
            (.unproven(expected: "SHA256:a", reason: "offered no identity"), true, false, false),
            (.unavailable(expected: "SHA256:a", reason: "its core is not answering"), false, true, false),
        ]
        for (verdict, notOurs, coreDown, changed) in cases {
            // The mapping itself, called directly on each verdict: this is
            // the `switch` that `shellInputs` feeds `identity.status` into,
            // and no gate is involved here. The path from a real gate's
            // status through `shellInputs` is covered by the tests above,
            // which take an actual proof; this one is about all five
            // verdicts in one table.
            let readings = AppContainer.readings(of: verdict)
            XCTAssertEqual(notOurs, readings.notOurs, "\(verdict)")
            XCTAssertEqual(coreDown, readings.coreDown, "\(verdict)")
            XCTAssertEqual(changed, readings.identityChanged, "\(verdict)")
            // Only a mismatch is told to `shellMode`, and `notOurs` covers
            // two verdicts: the `unproven` case reaches local media through
            // the reachability probe, not through `identityChanged`.
            XCTAssertEqual(
                changed ? ShellMode.local : ShellMode.workspace,
                shellMode(
                    serverURL: "http://10.0.0.42:6369",
                    unreachable: false,
                    pairingRequired: false,
                    identityChanged: readings.identityChanged
                ),
                "\(verdict)"
            )
        }
    }
}
