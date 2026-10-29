import XCTest
@testable import DomovoiKit

/// The picker in flight: the sweep, the typed address, and the two forgets.
///
/// Nothing here touches a network. The sweep is a closure, which is also how
/// the four outcomes are reached at all — a real sweep answers whatever the
/// machine running the suite happens to be plugged into.
@MainActor
final class ServerPickerModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.7:6369"

    /// A picker whose sweep answers `outcome` and reports `reports` on the
    /// way, and whose typed-address probe answers `hit`.
    private func makePicker(
        store: InMemoryShellStore,
        outcome: SweepOutcome = .swept(servers: []),
        reports: [(Int, Int, Int)] = [],
        hit: FoundDomovoi? = nil,
        toasts: Shared<[String]> = Shared([])
    ) -> ServerPickerModel {
        ServerPickerModel(
            store: store,
            sweep: { report in
                for next in reports { report(next.0, next.1, next.2) }
                return outcome
            },
            probe: { _ in hit },
            toast: { text in toasts.mutate { $0.append(text) } }
        )
    }

    /// A sweep whose progress has landed and which has finished, without
    /// waiting on real time: the reports are made synchronously inside the
    /// sweep, so one `settle` on the task is enough.
    private func sweepAndSettle(_ picker: ServerPickerModel) async {
        picker.rescan()
        for _ in 0..<settleYieldLimit where picker.isScanning {
            await Task.yield()
        }
        XCTAssertFalse(picker.isScanning, "the sweep never finished in \(settleYieldLimit) yields")
        // The progress reports hop to the main actor one task each, so give
        // those tasks the same bounded chance to land.
        for _ in 0..<64 { await Task.yield() }
    }

    // MARK: - the sweep

    /// The button greys out **on the tap**, not when the sweep's task
    /// happens to start — which is what Android buys with
    /// `CoroutineStart.UNDISPATCHED`.
    func testTheRescanButtonGreysOutOnTheSameTap() {
        let picker = makePicker(store: InMemoryShellStore())
        XCTAssertFalse(picker.isScanning)
        picker.rescan()
        XCTAssertTrue(picker.isScanning, "nothing has awaited yet and the button is already off")
        picker.stop()
    }

    func testASecondRescanWhileOneIsGoingDoesNothing() async {
        let runs = Shared<Int>(0)
        let picker = ServerPickerModel(
            store: InMemoryShellStore(),
            sweep: { _ in
                runs.mutate { $0 += 1 }
                return .swept(servers: [])
            },
            probe: { _ in nil }
        )
        picker.rescan()
        picker.rescan()
        for _ in 0..<settleYieldLimit where picker.isScanning { await Task.yield() }
        XCTAssertEqual(1, runs.current)
    }

    /// The outcome and the hits land, and `hasSwept` is what tells the notice
    /// that something was asked.
    func testASweepThatFoundSomethingLandsItsHitsAndItsProgress() async {
        let hit = FoundDomovoi(url: server, name: "kitchen-box", identity: TestIdentity.real.pin)
        let picker = makePicker(
            store: InMemoryShellStore(),
            outcome: .swept(servers: [hit]),
            reports: [(1, 254, 0), (254, 254, 1)]
        )
        await sweepAndSettle(picker)

        XCTAssertTrue(picker.hasSwept)
        XCTAssertEqual([server], picker.found.map(\.url))
        XCTAssertEqual(254, picker.progress.done)
        XCTAssertEqual(254, picker.progress.total)
        XCTAssertEqual(.nothing, picker.notice, "something answered, so nothing is claimed")
        XCTAssertEqual(ShellStrings.domovois, picker.sectionLabel)
    }

    /// A sweep that probed 254 addresses and heard nothing says so — and a
    /// sweep that probed **nothing** says one of the other three things.
    /// These are the states a person actually meets.
    func testEachOutcomeReachesThePickerAsItsOwnLine() async {
        for (outcome, expected) in [
            (SweepOutcome.swept(servers: []), ServerPickerNotice.nothingAnswered(port: 6369)),
            (.notOnLan, .notOnWifi),
            (.noLocalNetwork, .noAddressYet),
            (.localNetworkDenied, .localNetworkDenied),
        ] {
            let picker = makePicker(store: InMemoryShellStore(), outcome: outcome)
            await sweepAndSettle(picker)
            XCTAssertEqual(expected, picker.notice, "for \(outcome)")
        }
    }

    /// The auto-scan on open happens once.
    func testTheAutoScanOnOpenHappensOnce() async {
        let runs = Shared<Int>(0)
        let picker = ServerPickerModel(
            store: InMemoryShellStore(),
            sweep: { _ in
                runs.mutate { $0 += 1 }
                return .swept(servers: [])
            },
            probe: { _ in nil }
        )
        picker.scanOnOpen()
        for _ in 0..<settleYieldLimit where picker.isScanning { await Task.yield() }
        picker.scanOnOpen()
        for _ in 0..<64 { await Task.yield() }
        XCTAssertEqual(1, runs.current)
    }

    // MARK: - §3.5, a typed address

    /// Blank is silently nothing.
    func testABlankTypedAddressIsNothing() async {
        let picker = makePicker(store: InMemoryShellStore())
        picker.manual = "   "
        let outcome = await picker.addManual()
        XCTAssertEqual(.ignored, outcome)
        XCTAssertNil(picker.manualError)
        XCTAssertNil(picker.gate.pending)
    }

    /// **A plain-http address outside the home network is refused up front,
    /// with the reason** — not probed and then reported as "couldn't reach",
    /// which would blame the network for a rule.
    func testAPublicPlainHttpAddressIsRefusedBeforeAnythingIsProbed() async {
        let probes = Shared<Int>(0)
        let picker = ServerPickerModel(
            store: InMemoryShellStore(),
            sweep: { _ in .swept(servers: []) },
            probe: { _ in
                probes.mutate { $0 += 1 }
                return nil
            }
        )
        picker.manual = "example.com"
        guard case .refused(let message) = await picker.addManual() else {
            return XCTFail("a public address in the clear was not refused")
        }
        XCTAssertEqual(0, probes.current, "nothing was sent")
        XCTAssertEqual(message, picker.manualError)
        XCTAssertFalse(message.contains("couldn't reach"))
        XCTAssertNil(picker.gate.pending)
    }

    /// Nothing answered at a perfectly good address: the address is named, so
    /// the person can see what was tried.
    func testAnAddressNothingAnswersAtNamesTheAddressAndTrustsNothing() async {
        let picker = makePicker(store: InMemoryShellStore(), hit: nil)
        picker.manual = "10.0.0.42"
        guard case .noAnswer(let message) = await picker.addManual() else {
            return XCTFail("an unanswered address was treated as a hit")
        }
        XCTAssertEqual("couldn't reach a dashboard at http://10.0.0.42:6369", message)
        XCTAssertNil(picker.gate.pending)
        XCTAssertFalse(picker.manualBusy)
    }

    /// A typed address that answers goes **into the trust gate**, carrying
    /// the identity the probe saw at that moment — it is not connected to
    /// behind the person's back.
    func testATypedAddressIsProbedAndThenTrusted() async {
        let hit = FoundDomovoi(url: server, name: "kitchen-box", identity: TestIdentity.real.pin)
        let store = InMemoryShellStore()
        let picker = makePicker(store: store, hit: hit)
        picker.manual = "10.0.0.42:6369"

        guard case .found = await picker.addManual() else { return XCTFail("the hit was lost") }
        XCTAssertEqual(server, picker.gate.pending?.url)
        XCTAssertEqual(TestIdentity.real.fingerprint, picker.gate.pending?.fingerprint)
        XCTAssertEqual("", store.serverURL, "and nothing is written until the yes")
        XCTAssertEqual([], store.trustedServers)
    }

    /// Typing clears the error, so a person is not told about the last
    /// attempt while they fix it.
    func testTypingClearsTheError() async {
        let picker = makePicker(store: InMemoryShellStore(), hit: nil)
        picker.manual = "10.0.0.42"
        _ = await picker.addManual()
        XCTAssertNotNil(picker.manualError)
        picker.manual = "10.0.0.43"
        XCTAssertNil(picker.manualError)
    }

    func testTheAddButtonIsOffWhileItIsChecking() {
        let picker = makePicker(store: InMemoryShellStore())
        XCTAssertFalse(picker.manualButtonEnabled, "and off for a blank field")
        picker.manual = "10.0.0.42"
        XCTAssertTrue(picker.manualButtonEnabled)
        XCTAssertEqual(ShellStrings.add, picker.manualButtonLabel)
    }

    // MARK: - the two forgets (§6.4, and §11.8's asymmetry)

    /// A saved row's forget takes effect on the tap, toasts, and **takes that
    /// server's token and pin with it**.
    func testASavedRowsForgetHappensAtOnceAndTakesItsTokenWithIt() async {
        let store = InMemoryShellStore(serverURL: server)
        store.trustServer(other, identity: TestIdentity.rogue.pin)
        store.upsertKnownServer(url: other, name: "study")
        store.setToken("household-other", for: other)
        store.upsertKnownServer(url: server, name: nil)
        let toasts = Shared<[String]>([])
        let picker = makePicker(store: store, toasts: toasts)

        let row = picker.rows.first { $0.url == other }
        XCTAssertEqual(.immediate, row?.forgetAffordance)
        picker.forget(row!)

        XCTAssertFalse(store.isTrusted(other))
        XCTAssertNil(store.token(for: other))
        XCTAssertNil(store.pin(forServer: other))
        XCTAssertEqual(["forgot 10.0.0.7:6369"], toasts.current)
        XCTAssertEqual(server, store.serverURL, "and the server in use is untouched")
    }

    /// The active row's forget **asks first and forgets nothing** until the
    /// answer comes.
    func testTheActiveRowsForgetAsksFirstAndForgetsNothingYet() {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.upsertKnownServer(url: server, name: nil)
        let picker = makePicker(store: store)

        let row = picker.rows.first { $0.active }
        XCTAssertEqual(.confirmFirst, row?.forgetAffordance)
        picker.forget(row!)
        XCTAssertEqual(server, store.serverURL, "the immediate path refuses to act on it")

        picker.askToForgetActive()
        XCTAssertNotNil(picker.forgetActiveAsk(status: nil))
        XCTAssertEqual(server, store.serverURL)
        XCTAssertEqual("household-abc", store.token(for: server))

        picker.cancelForgetActive()
        XCTAssertNil(picker.forgetActiveAsk(status: nil))
        XCTAssertEqual(server, store.serverURL)
    }

    /// There is nothing to ask about when there is no server.
    func testThereIsNothingToForgetWithNoServer() {
        let picker = makePicker(store: InMemoryShellStore())
        picker.askToForgetActive()
        XCTAssertNil(picker.forgetActiveAsk(status: nil))
    }
}
