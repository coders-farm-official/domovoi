import XCTest
@testable import DomovoiKit

/// What part 3 added to the shell: the trust-again yes, the forget, the
/// `pin this identity` button, the toast, and whether the pairing screen may
/// ask for a token at all.
///
/// Separate from `ShellModelTests` because its `Rig` replaces the
/// reachability probe, and three of these need the **real** identity gate to
/// reach a verdict a person would actually meet: a server that proves a
/// different key.
@MainActor
final class ShellModelPartThreeTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// A model over a store, a fake clock and a `tick` that parks, so the
    /// toast's own timer never waits on real time. The reachability loop's
    /// probe answers false, which keeps the shell out of the workspace and
    /// out of the way.
    private func makeModel(
        store: InMemoryShellStore,
        clock: Shared<Int64> = Shared(0),
        ticks: Shared<Int> = Shared(0)
    ) -> (model: ShellModel, container: AppContainer) {
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { clock.current },
            configuration: StubURLProtocol.session(timeout: 2),
            log: { _, _ in }
        )
        let model = ShellModel(
            container: container,
            probe: { true },
            clock: { clock.current },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in
                ticks.mutate { $0 += 1 }
                try await Task.sleep(for: .seconds(3600))
            }
        )
        return (model, container)
    }

    /// A store pointed at `server`, pinned to the real key and paired.
    private func pinnedStore(token: String? = "household-abc") -> InMemoryShellStore {
        let store = InMemoryShellStore(serverURL: server, token: token)
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.upsertKnownServer(url: server, name: "kitchen-box")
        return store
    }

    /// Make the gate see whoever answers prove `identity`, and wait for the
    /// verdict.
    private func proveIdentity(_ identity: TestIdentity, container: AppContainer) async {
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/health" else { return .json("{}", status: 404) }
            let challenge = request.url?.query?
                .split(separator: "&")
                .first { $0.hasPrefix("challenge=") }
                .map { String($0.dropFirst("challenge=".count)) }
            let body = (try? identity.healthBody(challenge: challenge)) ?? "{}"
            return .json(body)
        }
        guard let base = TokenScope.baseOf(server) else { return XCTFail("the server did not parse") }
        await container.identity.prepare(base)
    }

    // MARK: - the trust-again yes (finding S-T1)

    /// **The yes drops the household token before it pins the new key.** The
    /// old token belongs to the old key and must never go to whatever holds
    /// the new one.
    func testTheTrustAgainYesDropsTheOldTokenAndPinsTheKeyTheServerProved() async {
        let store = pinnedStore()
        let (model, container) = makeModel(store: store)
        await proveIdentity(.rogue, container: container)
        model.refresh()

        guard let changed = model.changedIdentityHere else {
            return XCTFail("no mismatch: \(String(describing: container.identity.status?.verdict))")
        }
        XCTAssertEqual(TestIdentity.real.fingerprint, changed.expected)
        XCTAssertEqual(TestIdentity.rogue.fingerprint, changed.seen)

        model.askTrustAgain()
        XCTAssertEqual(.trustAgain, model.sheet)
        XCTAssertEqual("household-abc", store.token(for: server), "and nothing has happened yet")

        XCTAssertTrue(model.confirmTrustAgain())
        XCTAssertNil(store.token(for: server), "the old household's token is gone")
        XCTAssertEqual(TestIdentity.rogue.pin, store.pin(forServer: server))
        XCTAssertTrue(store.isTrusted(server), "the server itself is still trusted: they said yes to it")
        XCTAssertNil(model.sheet)
        XCTAssertNil(model.trustAgain)
        XCTAssertEqual(
            "trusting 10.0.0.42:6369 again — pair this phone with it",
            model.toast?.text
        )
        XCTAssertFalse(model.isPaired, "so the phone pairs from scratch, which is what the toast says")
        model.stop()
    }

    /// Every old verdict is stale after the pin changes: the gate's epoch is
    /// bumped, so the next request re-proves rather than reusing the
    /// mismatch that is on screen.
    func testTheYesMakesEveryCachedVerdictStale() async {
        let store = pinnedStore()
        let (model, container) = makeModel(store: store)
        await proveIdentity(.rogue, container: container)
        model.refresh()
        model.askTrustAgain()
        XCTAssertNotNil(container.identity.status)

        XCTAssertTrue(model.confirmTrustAgain())
        guard let base = TokenScope.baseOf(server) else { return XCTFail("the server did not parse") }
        XCTAssertNil(
            container.identity.cachedVerdict(base),
            "the mismatch is not answered from the cache any more"
        )
        model.stop()
    }

    /// **The yes cannot be given when there is nothing to trust**, and a
    /// second tap cannot give it twice.
    func testTheYesCannotBeGivenTwiceOrWithNothingToTrust() async {
        let store = pinnedStore()
        let (model, container) = makeModel(store: store)
        XCTAssertFalse(model.confirmTrustAgain(), "nothing pending")
        XCTAssertEqual(TestIdentity.real.pin, store.pin(forServer: server), "and the pin is untouched")

        await proveIdentity(.rogue, container: container)
        model.refresh()
        model.askTrustAgain()
        XCTAssertTrue(model.confirmTrustAgain())
        XCTAssertFalse(model.confirmTrustAgain(), "the payload went with the sheet")
        model.stop()
    }

    // MARK: - the forget

    /// The forget leaves the app with no server, **asks for the picker on its
    /// way out**, and toasts. The picker request is the only way the dialog
    /// can outlive the screen that opened it: the workspace is gone the
    /// instant the server is.
    func testTheForgetLeavesNoServerAndAsksForThePicker() {
        let store = pinnedStore()
        let (model, container) = makeModel(store: store)
        // A device-tier refusal from our own server, which is the one thing
        // that raises the pairing screen.
        container.client.notePossiblePairingRefusal(
            status: 401,
            body: #"{"detail":"X-Device-Token or admin session required"}"#
        )

        XCTAssertEqual(server, model.forgetTheActiveServer())
        XCTAssertEqual("", store.serverURL)
        XCTAssertNil(store.token(for: server))
        XCTAssertNil(store.pin(forServer: server))
        XCTAssertEqual([], store.trustedServers)
        XCTAssertEqual([], store.knownServers)
        XCTAssertFalse(container.client.pairingRequired, "the pairing flag goes with the server")
        XCTAssertEqual("forgot 10.0.0.42:6369 — pick a server", model.toast?.text)
        // `refresh()` takes the picker request, which opens the sheet — above
        // both shells, so it survives the workspace dropping to local media.
        XCTAssertEqual(.connection, model.sheet)
        XCTAssertEqual(
            .localMedia(.noServer), model.screen,
            "the workspace is gone the instant the server is"
        )
        model.stop()
    }

    func testForgettingWithNoServerDoesNothing() {
        let (model, _) = makeModel(store: InMemoryShellStore())
        XCTAssertNil(model.forgetTheActiveServer())
        XCTAssertNil(model.toast)
        XCTAssertNil(model.sheet)
        model.stop()
    }

    /// What the confirmation will say, from the model: the pin and what is
    /// proved now, side by side.
    func testTheForgetConfirmationShowsBothFingerprints() async {
        let store = pinnedStore()
        let (model, container) = makeModel(store: store)
        await proveIdentity(.rogue, container: container)
        model.refresh()

        guard let ask = model.forgetActiveAsk() else { return XCTFail("nothing to ask about") }
        XCTAssertEqual("10.0.0.42:6369", ask.address)
        XCTAssertEqual(TestIdentity.real.fingerprint, ask.pinned)
        XCTAssertEqual(TestIdentity.rogue.fingerprint, ask.proven)
        XCTAssertTrue(ask.body.contains("it now proves: \(TestIdentity.rogue.fingerprint)"))
        model.stop()
    }

    // MARK: - pin this identity

    /// A server trusted while it offered no identity, which proves one now:
    /// the button pins it, and **there is no button when there is nothing to
    /// pin**, so a pin is never written without the person looking at the
    /// fingerprint.
    func testPinThisIdentityWritesOnlyWhatIsOffered() async {
        // Trusted with no identity, so nothing is pinned.
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: nil)
        store.upsertKnownServer(url: server, name: nil)
        let (model, container) = makeModel(store: store)

        XCTAssertEqual(.notPinnedYet, model.identityDisplay)
        XCTAssertFalse(model.pinTheOfferedIdentity(), "nothing offered, nothing written")
        XCTAssertNil(store.pin(forServer: server))

        await proveIdentity(.real, container: container)
        model.refresh()
        XCTAssertEqual(.offered(TestIdentity.real.pin), model.identityDisplay)
        XCTAssertTrue(model.isUnverified, "and the workspace says so beside the name")

        XCTAssertTrue(model.pinTheOfferedIdentity())
        XCTAssertEqual(TestIdentity.real.pin, store.pin(forServer: server))
        XCTAssertEqual("pinned \(TestIdentity.real.fingerprint)", model.toast?.text)
        XCTAssertEqual(.pinned(fingerprint: TestIdentity.real.fingerprint), model.identityDisplay)
        model.stop()
    }

    // MARK: - §5.1, the pairing screen's own question (A6-03)

    /// Answer `/api/health` with `body` from now on, bump the network so the
    /// cached verdict cannot stand, and take the new verdict.
    private func reproveWith(body: String, container: AppContainer) async {
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/health" else { return .json("{}", status: 404) }
            return .json(body)
        }
        container.identity.networkChanged()
        guard let base = TokenScope.baseOf(server) else { return XCTFail("the server did not parse") }
        await container.identity.prepare(base)
    }

    /// Raise the pairing screen the way a person does: a device-tier refusal
    /// from a server that has proved itself. The flag is sticky, which is why
    /// the verdict can go bad underneath it.
    private func raisePairingScreen(container: AppContainer) async {
        await proveIdentity(.real, container: container)
        container.client.notePossiblePairingRefusal(
            status: 401,
            body: #"{"detail":"X-Device-Token or admin session required"}"#
        )
        XCTAssertTrue(container.client.pairingRequired, "the 401 is what raises the screen")
    }

    /// **A mismatch never reaches the pairing screen**, which is the whole
    /// reason its words could not be the screen's words.
    ///
    /// `shellMode` asks `identityChanged` before `pairingRequired`, so the
    /// local shell and its banner win. Asserted on `model.screen`, not only
    /// on the gate: a test about this screen that does not say which screen
    /// it is on is the test that let F1 through.
    func testAMismatchGoesToTheLocalShellNotThePairingScreen() async {
        let store = pinnedStore(token: nil)
        let (model, container) = makeModel(store: store)
        await raisePairingScreen(container: container)
        model.refresh()
        XCTAssertEqual(.pairing, model.screen, "with a good verdict it is the pairing screen")
        XCTAssertTrue(model.pairingOffersTheTokenField)
        XCTAssertNil(model.pairingWithheld)

        container.identity.networkChanged()
        await proveIdentity(.rogue, container: container)
        model.refresh()
        guard case .mismatch = container.identity.status?.verdict else {
            return XCTFail("expected a mismatch, got \(String(describing: container.identity.status?.verdict))")
        }
        XCTAssertEqual(
            .localMedia(.identityChanged), model.screen,
            "the impostor warning is the local shell's, not this screen's"
        )
        model.stop()
    }

    /// **A server whose core is not answering is not told it is an
    /// impostor.** This is the harm F1 named: the box is restarting, a 401
    /// got in first, and the screen used to tell the person their Domovoi had
    /// answered with the wrong identity and to run the forgetting cascade.
    func testACoreThatIsNotAnsweringGetsTheCouldNotCheckWordsNotTheImpostorWords() async {
        let store = pinnedStore(token: nil)
        let (model, container) = makeModel(store: store)
        await raisePairingScreen(container: container)
        // web/backend/main.py leaves `identity` out and says why when its
        // core did not answer in time: a restart, an update, a busy box.
        await reproveWith(
            body: #"{"status":"degraded","db_reachable":true,"domovoi_reachable":false,"identity":null}"#,
            container: container
        )
        model.refresh()
        guard case .unavailable = container.identity.status?.verdict else {
            return XCTFail("expected unavailable, got \(String(describing: container.identity.status?.verdict))")
        }
        XCTAssertEqual(.pairing, model.screen, "and this really is the screen in front of the person")
        XCTAssertFalse(model.pairingOffersTheTokenField, "the gate still withholds the field (A6-03)")
        XCTAssertEqual("Couldn't check it with the server — try again.", model.pairingWithheld)
        model.stop()
    }

    /// A pinned server that proves nothing also reaches this screen, and also
    /// gets the honest sentence rather than the banner's.
    func testAServerThatProvesNothingIsNotOfferedTheFieldAndIsNotCalledAnImpostor() async {
        let store = pinnedStore(token: nil)
        let (model, container) = makeModel(store: store)
        await raisePairingScreen(container: container)
        // A plain web server at the saved address: no identity block at all,
        // with a key pinned for it.
        await reproveWith(body: #"{"status":"ok"}"#, container: container)
        model.refresh()
        guard case .unproven = container.identity.status?.verdict else {
            return XCTFail("expected unproven, got \(String(describing: container.identity.status?.verdict))")
        }
        XCTAssertEqual(.pairing, model.screen)
        XCTAssertFalse(model.pairingOffersTheTokenField)
        guard let withheld = model.pairingWithheld else { return XCTFail("nothing was said instead") }
        XCTAssertEqual("Couldn't check it with the server — try again.", withheld)
        XCTAssertFalse(withheld.contains("forget"), "forgetting a server is not the answer to this")
        model.stop()
    }

    /// A server that proves its pinned key is offered the field, which is the
    /// ordinary case this screen exists for.
    func testAProvedServerIsOfferedTheTokenField() async {
        let store = pinnedStore(token: nil)
        let (model, container) = makeModel(store: store)
        await proveIdentity(.real, container: container)
        model.refresh()
        XCTAssertTrue(model.pairingOffersTheTokenField)
        XCTAssertNil(model.pairingWithheld)
        model.stop()
    }

    // MARK: - the toast

    /// One at a time, and it goes away by itself.
    func testAToastReplacesTheOneBeforeItAndIsDismissable() {
        let (model, _) = makeModel(store: InMemoryShellStore())
        model.showToast("first")
        let first = model.toast
        XCTAssertEqual("first", first?.text)
        model.showToast("second")
        XCTAssertEqual("second", model.toast?.text)
        XCTAssertNotEqual(first?.id, model.toast?.id, "a second toast, not the first one edited")
        // The claim "even with the same words" has to be made with the same
        // words: the id is what a view keys the animation on, so a repeat of
        // an identical message must still be a new toast.
        let second = model.toast
        model.showToast("second")
        XCTAssertNotEqual(second?.id, model.toast?.id, "two toasts, even with the same words")

        model.dismissToast()
        XCTAssertNil(model.toast)
        model.stop()
    }

    /// The toast's own timer is the injected tick, so nothing in the suite
    /// waits 2.4 seconds — and `stop()` takes it down with everything else.
    func testTheToastTimerIsCancelledWithTheShell() async {
        let ticks = Shared<Int>(0)
        let (model, _) = makeModel(store: InMemoryShellStore(), ticks: ticks)
        model.showToast("something happened")
        for _ in 0..<settleYieldLimit where ticks.current == 0 { await Task.yield() }
        XCTAssertEqual(1, ticks.current, "the toast is counting its own 2.4 seconds")
        model.stop()
        XCTAssertNotNil(model.toast, "stopping the shell does not blank what is on screen")
    }
}
