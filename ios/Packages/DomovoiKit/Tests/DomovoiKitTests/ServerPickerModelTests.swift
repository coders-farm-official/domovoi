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
    ///
    /// A fresh ``InMemoryShellStore`` is a **fresh install**, so the empty
    /// sweep gets the first-install wording; the settled one is the test
    /// below. That is not a detail of the rig: this loop is exactly what the
    /// person who has just installed the app sees, and it used to accuse
    /// their server.
    func testEachOutcomeReachesThePickerAsItsOwnLine() async {
        for (outcome, expected) in [
            (SweepOutcome.swept(servers: []), ServerPickerNotice.nothingAnsweredYet(port: 6369)),
            (.notOnLan, .notOnWifi),
            (.noLocalNetwork, .noAddressYet),
            (.localNetworkDenied, .localNetworkDenied),
        ] {
            let picker = makePicker(store: InMemoryShellStore(), outcome: outcome)
            await sweepAndSettle(picker)
            XCTAssertEqual(expected, picker.notice, "for \(outcome)")
        }
    }

    /// A sweep that **found** something settles the permission question for
    /// this install, and it is settled in the store — so it is still settled
    /// after the dialog is closed and the next panel is built, which is where
    /// the picker's own `remember`s go.
    func testASweepThatFoundSomethingSettlesThePermissionForGood() async {
        let store = InMemoryShellStore()
        XCTAssertFalse(store.localNetworkHasAnswered, "a fresh install has heard nothing")

        let hit = FoundDomovoi(url: server, name: nil, identity: nil)
        await sweepAndSettle(makePicker(store: store, outcome: .swept(servers: [hit])))
        XCTAssertTrue(store.localNetworkHasAnswered, "a dashboard answered and nothing wrote it down")

        // A second panel, over the same store, finding nothing this time: the
        // house is the question now, and the backend may be asked about.
        let later = makePicker(store: store, outcome: .swept(servers: []))
        await sweepAndSettle(later)
        XCTAssertEqual(.nothingAnswered(port: 6369), later.notice)
    }

    /// An empty sweep proves nothing, so it must not settle anything: this is
    /// the run that happens while Apple's alert is up.
    func testAnEmptySweepLeavesThePermissionQuestionOpen() async {
        let store = InMemoryShellStore()
        await sweepAndSettle(makePicker(store: store, outcome: .swept(servers: [])))
        XCTAssertFalse(store.localNetworkHasAnswered)
    }

    /// A typed address that reached a dashboard proves the same thing a sweep
    /// hit does — and it is the way out for someone whose first sweep found
    /// nothing, so it is the one that most needs to count.
    func testATypedAddressThatAnsweredSettlesItToo() async {
        let store = InMemoryShellStore()
        let picker = makePicker(
            store: store,
            hit: FoundDomovoi(url: server, name: "kitchen-box", identity: nil)
        )
        picker.manual = "10.0.0.42:6369"
        _ = await picker.addManual()
        XCTAssertTrue(store.localNetworkHasAnswered)
    }

    /// A typed address that answered nothing proves nothing.
    func testATypedAddressThatAnsweredNothingSettlesNothing() async {
        let store = InMemoryShellStore()
        let picker = makePicker(store: store, hit: nil)
        picker.manual = "10.0.0.42:6369"
        _ = await picker.addManual()
        XCTAssertFalse(store.localNetworkHasAnswered)
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

    // MARK: - coming back from the Local Network alert

    /// A picker whose sweeps answer `outcomes` in turn (the last one again
    /// once they run out), counting the runs. While `held` is true a sweep
    /// waits, so a test can act during one.
    private func makeScriptedPicker(
        store: InMemoryShellStore,
        outcomes: [SweepOutcome],
        runs: Shared<Int>,
        held: Shared<Bool> = Shared(false)
    ) -> ServerPickerModel {
        ServerPickerModel(
            store: store,
            sweep: { _ in
                let run = runs.mutate { count -> Int in
                    count += 1
                    return count
                }
                for _ in 0..<settleYieldLimit where held.current { await Task.yield() }
                return outcomes[min(run, outcomes.count) - 1]
            },
            probe: { _ in nil }
        )
    }

    /// Let a sweep started by the code under test finish, and any sweep it
    /// starts in turn.
    private func settleAll(_ picker: ServerPickerModel) async {
        for _ in 0..<4 {
            for _ in 0..<settleYieldLimit where picker.isScanning { await Task.yield() }
            for _ in 0..<64 { await Task.yield() }
        }
        XCTAssertFalse(picker.isScanning, "a sweep never finished")
    }

    /// **The device-test finding (2026-11-22).** The opening sweep runs under
    /// Apple's alert and hears nothing; the person taps Allow and the app
    /// becomes active again. That is when the picker sweeps again, so the
    /// server they were asked about appears without a rescan tap.
    func testComingBackFromTheAlertSweepsAgainAndFindsTheServer() async {
        let runs = Shared<Int>(0)
        let hit = FoundDomovoi(url: server, name: "kitchen-box", identity: nil)
        let picker = makeScriptedPicker(
            store: InMemoryShellStore(),
            outcomes: [.swept(servers: []), .swept(servers: [hit])],
            runs: runs
        )
        await sweepAndSettle(picker)
        XCTAssertEqual(.nothingAnsweredYet(port: 6369), picker.notice)

        picker.appBecameActive()
        await settleAll(picker)

        XCTAssertEqual(2, runs.current)
        XCTAssertEqual([server], picker.found.map(\.url))
        XCTAssertEqual(.nothing, picker.notice)
    }

    /// Once anything has answered on this install the permission is not the
    /// question, so an empty sweep is the house's answer and a trip to the
    /// background and back does not re-sweep 254 addresses.
    func testComingBackDoesNotSweepAgainOnceTheNetworkHasAnswered() async {
        let store = InMemoryShellStore()
        store.noteLocalNetworkAnswered()
        let runs = Shared<Int>(0)
        let picker = makeScriptedPicker(store: store, outcomes: [.swept(servers: [])], runs: runs)
        await sweepAndSettle(picker)

        picker.appBecameActive()
        await settleAll(picker)
        XCTAssertEqual(1, runs.current)
    }

    /// A sweep that found something, one that is about the house rather than
    /// the permission, and no sweep at all: none of them sweeps again.
    func testComingBackLeavesEveryOtherOutcomeAlone() async {
        let hit = FoundDomovoi(url: server, name: nil, identity: nil)
        for outcome in [SweepOutcome.swept(servers: [hit]), .notOnLan, .noLocalNetwork] {
            let runs = Shared<Int>(0)
            let picker = makeScriptedPicker(store: InMemoryShellStore(), outcomes: [outcome], runs: runs)
            await sweepAndSettle(picker)
            picker.appBecameActive()
            await settleAll(picker)
            XCTAssertEqual(1, runs.current, "for \(outcome)")
        }

        let runs = Shared<Int>(0)
        let unopened = makeScriptedPicker(store: InMemoryShellStore(), outcomes: [.swept(servers: [])], runs: runs)
        unopened.appBecameActive()
        await settleAll(unopened)
        XCTAssertEqual(0, runs.current, "nothing had been swept, so there is nothing to sweep again")
    }

    /// A refusal is the permission talking on **any** install, and coming
    /// back is the person returning from Settings, perhaps having turned it
    /// on.
    func testComingBackAfterARefusalSweepsAgainEvenOnASettledInstall() async {
        let store = InMemoryShellStore()
        store.noteLocalNetworkAnswered()
        let runs = Shared<Int>(0)
        let hit = FoundDomovoi(url: server, name: nil, identity: nil)
        let picker = makeScriptedPicker(
            store: store,
            outcomes: [.localNetworkDenied, .swept(servers: [hit])],
            runs: runs
        )
        await sweepAndSettle(picker)
        XCTAssertEqual(.localNetworkDenied, picker.notice)

        picker.appBecameActive()
        await settleAll(picker)
        XCTAssertEqual(2, runs.current)
        XCTAssertEqual([server], picker.found.map(\.url))
    }

    /// The alert answered **while the opening sweep is still probing**: some
    /// of its probes went out under the alert, so its empty answer is not
    /// believed and it is run once more, and only once.
    func testComingBackMidSweepSweepsOnceMoreWhenItEndsEmpty() async {
        let runs = Shared<Int>(0)
        let held = Shared(true)
        let picker = makeScriptedPicker(
            store: InMemoryShellStore(),
            outcomes: [.swept(servers: [])],
            runs: runs,
            held: held
        )
        picker.rescan()
        for _ in 0..<64 { await Task.yield() }
        XCTAssertTrue(picker.isScanning)

        picker.appBecameActive()
        XCTAssertEqual(1, runs.current, "nothing is started over a running sweep")
        held.mutate { $0 = false }
        await settleAll(picker)

        XCTAssertEqual(2, runs.current, "the empty sweep ran again, and the second one did not loop")
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

    // MARK: - the row the list says is not tappable

    /// **The model refuses the active row itself.**
    ///
    /// `ServerPickerRow.isSelectable` is `clickable(enabled = !active)`, and
    /// until a review measured it the only thing enforcing it was an
    /// `onTapGesture` in a `body`: handed the active row directly, the model
    /// answered `selectReturned=true` and wrote `preferLocal=false`, moving
    /// someone who had chosen "this phone" onto the server by way of a row
    /// drawn as untappable.
    func testTheActiveRowIsRefusedByTheModelAndNotOnlyByTheView() {
        let store = InMemoryShellStore(serverURL: server, preferLocal: true)
        store.upsertKnownServer(url: server, name: "kitchen-box")
        let picker = makePicker(store: store)

        guard let active = picker.rows.first(where: { $0.active }) else {
            return XCTFail("no active row")
        }
        XCTAssertFalse(active.isSelectable, "the list draws it as untappable")
        XCTAssertFalse(picker.select(active), "and the model says so too")
        XCTAssertTrue(store.preferLocal, "their own choice is still theirs")
        XCTAssertNil(picker.gate.pending, "and no trust dialog was raised about a server already trusted")
    }

    /// Any other row still goes through, so the guard above is not simply
    /// switching the picker off.
    func testAnotherRowIsStillSelectable() {
        let store = InMemoryShellStore(serverURL: server, preferLocal: true)
        store.upsertKnownServer(url: server, name: "kitchen-box")
        store.trustServer(other)
        store.upsertKnownServer(url: other, name: "shed-box")
        let picker = makePicker(store: store)

        guard let row = picker.rows.first(where: { !$0.active }) else {
            return XCTFail("no other row")
        }
        XCTAssertTrue(row.isSelectable)
        XCTAssertTrue(picker.select(row), "already trusted, so the dialog closes")
        XCTAssertEqual(other, store.serverURL)
        XCTAssertFalse(store.preferLocal, "and picking a server means using it")
    }
}
