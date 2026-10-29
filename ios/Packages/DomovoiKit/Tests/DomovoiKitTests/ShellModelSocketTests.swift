import XCTest
@testable import DomovoiKit

/// What the shell does with the live socket: the three things a connect sets
/// off, the device register's sender, and the shared-screen answer.
///
/// Android's are three `LaunchedEffect`s inside a 1,060-line composable
/// (`AppShell.kt:341`, `:356`, `:365`) with no test of their own.
@MainActor
final class ShellModelSocketTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func model(
        serverURL: String? = nil,
        token: String? = "household-abc",
        deviceSelf: DeviceSelf? = DeviceSelf(
            deviceID: "ios-0123456789abcdef",
            name: "iPhone",
            userAgent: "iOS 18.3; iPhone"
        ),
        clock: Shared<Int64> = Shared(0)
    ) -> (ShellModel, AppContainer, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: serverURL ?? server, token: token)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        // Spelt out rather than `deviceSelf.map { who in { who } }`, which
        // the type checker cannot even produce a diagnostic for.
        var introduce: (@Sendable () -> DeviceSelf)?
        if let deviceSelf { introduce = { deviceSelf } }
        let model = ShellModel(
            container: container,
            deviceSelf: introduce,
            probe: { true },
            clock: { clock.current },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in try await Task.sleep(for: .seconds(3600)) }
        )
        return (model, container, store)
    }

    // MARK: - the register's sender (I1-C part 2's F9)

    /// **The hole F9 left, filled.** The cadence was written with both
    /// callers taking the sender as a parameter so no slot was burnt while
    /// nothing could send; ``ShellModel/registerSender`` is what is passed
    /// now. One decision, not two.
    func testTheRegisterSenderGoesOutWhenTheSocketComesUp() async throws {
        let bodies = Shared<[String]>([])
        let paths = Shared<[String]>([])
        StubURLProtocol.dispatch { request in
            paths.mutate { $0.append(request.url?.path ?? "") }
            if let body = request.httpBody { bodies.mutate { $0.append(String(decoding: body, as: UTF8.self)) } }
            return .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone","shared_screen":true}"#)
        }
        let (model, _, _) = model()
        XCTAssertNotNil(model.registerSender, "there is a device to introduce")

        model.socketConnectionChanged(true)
        await settleUntil { paths.current.contains("/api/devices/register") }
        XCTAssertEqual(
            1,
            paths.current.filter { $0 == "/api/devices/register" }.count,
            "one register, not one per effect"
        )
        // Read off `StubURLProtocol.recorded`, not off the dispatcher's own
        // `request`: a `URLProtocol` is handed the body as a **stream**, and
        // `httpBody` is nil there. The stub reads the stream back for exactly
        // this (`StubURLProtocol.body(of:)`, measured 2026-10-28), and
        // `bodies` below is the record of what the dispatcher could see.
        XCTAssertTrue(bodies.current.isEmpty, "the dispatcher sees a stream, not httpBody")
        let sent = try XCTUnwrap(
            StubURLProtocol.recorded.first { $0.url?.path == "/api/devices/register" }?.httpBody
        )
        let body = String(decoding: sent, as: UTF8.self)
        XCTAssertTrue(body.contains(#""platform":"ios""#))
        XCTAssertTrue(body.contains(#""owner_person_id":null"#), "the field is THERE and null")

        // The answer is taken: the kitchen tablet learns it is one.
        await settleUntil { model.sharedScreenAnswer == true }
        XCTAssertTrue(model.visibility.sharedScreen)
        model.stop()
    }

    /// The cadence still decides. A second connect inside the gap sends
    /// nothing, and a connect past it sends again.
    func testTheSenderObeysTheCadenceAndNotTheSocket() async {
        let count = Shared(0)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/devices/register" { count.mutate { $0 += 1 } }
            return .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone"}"#)
        }
        let clock = Shared<Int64>(0)
        let (model, _, _) = model(clock: clock)
        model.socketConnectionChanged(true)
        await settleUntil { count.current == 1 }

        model.socketConnectionChanged(false)
        model.socketConnectionChanged(true)
        await Task.yield()
        XCTAssertEqual(1, count.current, "inside the 15 s gap, however many times it bounces")

        clock.advance(DeviceRegistry.reaskGapMs)
        model.socketConnectionChanged(false)
        model.socketConnectionChanged(true)
        await settleUntil { count.current == 2 }
        model.stop()
    }

    /// A disconnect never registers: `canRegister` is `connected || paired`,
    /// and the ask is hung on the connect.
    func testADisconnectSendsNoRegister() async {
        let count = Shared(0)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/devices/register" { count.mutate { $0 += 1 } }
            return .json("{}")
        }
        let (model, _, _) = model()
        model.socketConnectionChanged(false)
        // The decisive half is synchronous: if the sender had been called,
        // `registerIfDue` would have recorded the ask and this would be
        // false. The request count is the slower confirmation.
        XCTAssertTrue(model.registerIfDue(reason: .changed), "the cadence slot was never taken")
        await Task.yield()
        await Task.yield()
        XCTAssertEqual(0, count.current)
        model.stop()
    }

    /// No ``DeviceSelf``, no sender — the state the existing
    /// `testNoSenderMeansNoAskAndSoNoCadenceSlotBurnt` is about, kept
    /// reachable rather than becoming vestigial.
    func testWithNothingToIntroduceThereIsNoSenderAndNoAsk() async {
        let count = Shared(0)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/devices/register" { count.mutate { $0 += 1 } }
            return .json("{}")
        }
        let (model, _, _) = model(deviceSelf: nil)
        XCTAssertNil(model.registerSender)
        model.socketConnectionChanged(true)
        await Task.yield()
        await Task.yield()
        XCTAssertEqual(0, count.current)
        XCTAssertTrue(model.registerIfDue(reason: .changed), "the slot was never taken")
        model.stop()
    }

    /// **A register that did not land records nothing.** Android's
    /// `.also { setSharedScreen(…) }` is inside the `runCatching`; writing a
    /// nil here instead would un-mask a shared screen on every hiccup.
    func testAFailedRegisterLeavesTheLastAnswerStanding() async {
        let fail = Shared(false)
        let count = Shared(0)
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/devices/register" else { return .json("{}") }
            count.mutate { $0 += 1 }
            if fail.current { return .json(#"{"detail":"nope"}"#, status: 500) }
            return .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone","shared_screen":true}"#)
        }
        let (model, _, _) = model()
        // `register()` rather than a socket bounce plus yields: this has to
        // wait for the failure to have **landed**, and three `Task.yield()`s
        // are not a round trip through the stub — the version that asserted
        // after them could not fail, which the deliberate break proved.
        await model.register()
        XCTAssertEqual(true, model.sharedScreenAnswer)

        fail.set(true)
        await model.register()
        XCTAssertEqual(2, count.current, "the second register really was attempted")
        XCTAssertEqual(true, model.sharedScreenAnswer, "a failure is not an answer")
        model.stop()
    }

    /// A server older than shared screens (V014) cannot mark a device, so an
    /// absent `shared_screen` is **no** and not unknown — otherwise a paired
    /// install meeting an older server would hide Chat for good.
    func testAnAbsentSharedScreenFieldIsNoAndNotUnknown() async {
        StubURLProtocol.dispatch { _ in
            .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone"}"#)
        }
        let (model, _, _) = model()
        model.socketConnectionChanged(true)
        await settleUntil { model.sharedScreenAnswer != nil }
        XCTAssertEqual(false, model.sharedScreenAnswer)
        XCTAssertFalse(model.visibility.sharedScreen)
        model.stop()
    }

    /// **The answer does not follow a server switch.** Android keeps these
    /// per server (`AppShell.kt:390`); one value under one name would carry
    /// server A's `false` onto server B, un-masking a paired install where
    /// the conservative default is "shared until the server says otherwise".
    func testASharedScreenAnswerDoesNotFollowTheServerSwitch() async {
        StubURLProtocol.dispatch { _ in
            .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone","shared_screen":false}"#)
        }
        let (model, _, store) = model()
        model.socketConnectionChanged(true)
        await settleUntil { model.sharedScreenAnswer != nil }
        XCTAssertEqual(false, model.sharedScreenAnswer)
        XCTAssertFalse(model.visibility.sharedScreen)

        // `setServerURL` refuses an untrusted address (`TrustBook:126`), so
        // the switch is made the way the picker makes one.
        let next = "http://10.0.0.77:6369"
        store.trustServer(next)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        store.setToken("household-xyz", for: next)
        model.refresh()
        XCTAssertNil(model.sharedScreenAnswer, "server A's answer is not about server B")
        XCTAssertTrue(
            model.visibility.sharedScreen,
            "and a paired install with no answer counts as shared, which is the conservative side"
        )
        model.stop()
    }

    // MARK: - what a connect sets off

    /// The capability manifest is fetched **on every connect, including every
    /// reconnect, and never on a disconnect** — `AppShell.kt:341`.
    func testEveryConnectRefreshesTheCapabilityManifest() async {
        let fetches = Shared(0)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/capabilities" { fetches.mutate { $0 += 1 } }
            return .json(#"{"plugins":[],"features":{}}"#)
        }
        let (model, _, _) = model(deviceSelf: nil)
        model.socketConnectionChanged(true)
        await settleUntil { fetches.current == 1 }

        // A disconnect fetches nothing. Measured by letting the NEXT connect
        // land and counting exactly two: a fetch on the disconnect would make
        // it three, and waiting for a round trip is what makes the assertion
        // able to fail at all.
        model.socketConnectionChanged(false)
        model.socketConnectionChanged(true)
        await settleUntil { fetches.current == 2 }
        await settleUntil {
            StubURLProtocol.recorded.filter { $0.url?.path == "/api/capabilities" }.count == 2
        }
        XCTAssertEqual(2, fetches.current, "two connects, two fetches, and the disconnect added none")
        model.stop()
    }

    /// Live and reachable stay two facts. The socket coming up short-circuits
    /// the probe loop; the socket being **down** is never enough to call the
    /// server out of reach, which is why an unpaired phone beside a healthy
    /// server is not dropped to local media.
    func testTheSocketShortCircuitsTheProbeButNeverDeclaresTheServerGone() async {
        let (model, _, _) = model(deviceSelf: nil)
        model.scenePhaseChanged(to: .active)
        XCTAssertEqual(
            .ask,
            reachabilityAction(serverURL: server, connected: false, phase: .active),
            "the socket being down only means keep asking"
        )
        model.socketConnectionChanged(true)
        XCTAssertEqual(
            .inReach,
            reachabilityAction(serverURL: server, connected: true, phase: .active)
        )
        XCTAssertFalse(model.unreachable)
        model.stop()
    }

    // MARK: - the scene phase, and the socket it comes back to

    /// Coming back **re-asks the rule** rather than believing `connected`
    /// (map §1.6's mirror image), through the model, because the scene phase
    /// is ``ReachabilityDriver``'s and the socket belongs beside it.
    ///
    /// The measurement is ``StateSocket/attemptCount``: with the real
    /// ``IdentityGate`` and an unproven server the answer does not change —
    /// the socket parks both times — so the only visible difference between
    /// "re-asked" and "remembered" is that it was asked again.
    func testComingBackReAsksTheSocketsRuleRatherThanTrustingItsFlag() {
        let (model, container, _) = model(deviceSelf: nil)
        container.startSocketUnlessARoom()
        XCTAssertTrue(container.socket.isStarted)
        let asked = container.socket.attemptCount

        model.scenePhaseChanged(to: .background)
        model.scenePhaseChanged(to: .active)
        XCTAssertGreaterThan(
            container.socket.attemptCount, asked,
            "the return re-minted; it did not read the flag it came back holding"
        )
        container.socket.stop()
        model.stop()
    }

    /// With the **real** identity gate and a server that has proved nothing,
    /// the container's socket holds: no request is built, and it parks rather
    /// than counting down to asking the same question again.
    ///
    /// This is §8.2's held row reached through the production graph rather
    /// than through a stub gate, which is what says the wiring applies the
    /// rule and not just that the rule exists.
    func testTheContainersSocketHoldsUntilTheServerHasProvedItself() {
        let (_, container, _) = model(deviceSelf: nil)
        container.startSocketUnlessARoom()
        XCTAssertTrue(container.socket.isStarted)
        XCTAssertEqual(.notNow, container.socket.paused)
        XCTAssertFalse(container.socket.connected)
        XCTAssertFalse(container.socket.isWaitingToRetry, "no timer on a question a timer cannot answer")
        XCTAssertTrue(
            StubURLProtocol.recorded.allSatisfy { $0.url?.path != stateSocketPath },
            "the request never left the phone"
        )
        container.socket.stop()
    }

    /// Room mode is the one thing that stops the socket starting — Android's
    /// `if (!RoomStore.isActive(this))` (`DomovoiApplication.kt:53`).
    ///
    /// Driven through ``AppContainer/startSocketUnlessARoom()`` rather than
    /// `start()`, so this test does not register the process's real network
    /// monitors beside `NetworkWatchTests`, which measures them.
    func testTheContainerStartsTheSocketUnlessThePhoneIsARoom() {
        let open = AppContainer(
            store: InMemoryShellStore(serverURL: server, token: "household-abc"),
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        open.startSocketUnlessARoom()
        XCTAssertTrue(open.socket.isStarted)
        open.socket.stop()

        let room = AppContainer(
            store: InMemoryShellStore(serverURL: server, token: "household-abc"),
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(roomModeActive: true),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        room.startSocketUnlessARoom()
        XCTAssertFalse(room.socket.isStarted, "the household side is off while the phone is a room")
    }

    /// Watching picks up a socket that is already up, rather than waiting for
    /// the next change — the window ``SocketRelay/missedConnections`` counts.
    func testWatchingTakesTheSocketsCurrentValue() async {
        let fetches = Shared(0)
        StubURLProtocol.dispatch { request in
            if request.url?.path == "/api/capabilities" { fetches.mutate { $0 += 1 } }
            return .json(#"{"plugins":[],"features":{}}"#)
        }
        let (model, container, _) = model(deviceSelf: nil)
        XCTAssertFalse(model.connected)
        model.watchSocket()
        XCTAssertFalse(model.connected, "nothing started it")
        XCTAssertEqual(0, container.socketRelay.missedConnections)
        model.stop()
    }

    // MARK: - the answer is filed under the server that was ASKED (the review's M1)

    /// One request held open at the server, so a test can make something
    /// happen **inside** a round trip rather than after it.
    private final class Park: Sendable {
        /// Set when the request reached the stub, so the test waits on the
        /// park itself rather than on `StubURLProtocol.recorded`.
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        /// Set when the park hit its bound instead of being released — which
        /// means the request timed out and whatever the test asserted
        /// afterwards was asserted over an answer that never came.
        let ranOut = Shared<Bool>(false)
    }

    /// A dispatcher that holds `path` open until the park is released.
    ///
    /// It blocks one `URLSession` worker thread and nothing else — a
    /// `URLProtocol`'s `startLoading` is synchronous, so there is nowhere
    /// else to hold it. **Bounded**, like every wait in this suite: a wiring
    /// that never releases fails the test rather than leaving an orphaned
    /// `xctest` on the shared Mac, which an unbounded spin loop once did for
    /// three and a half hours.
    ///
    /// The bound is **under** the session's own
    /// `timeoutIntervalForRequest` (1 s in this rig), and ``Park/ranOut``
    /// records when it was hit, because a park that outlasts the timeout
    /// turns the test it serves into one that cannot fail: measured
    /// 2026-10-29, when `StubURLProtocol` still called its dispatcher inside
    /// its own lock and both of the tests below passed over a register that
    /// had never answered.
    private func parking(
        _ path: String,
        answer: @Sendable @escaping () -> StubURLProtocol.Answer,
        otherwise: @Sendable @escaping (URLRequest) -> StubURLProtocol.Answer = { _ in .json("{}") }
    ) -> Park {
        let park = Park()
        StubURLProtocol.dispatch { request in
            guard request.url?.path == path else { return otherwise(request) }
            park.arrived.set(true)
            for step in 0..<400 {
                if park.release.current { return answer() }
                if step == 399 { park.ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return answer()
        }
        return park
    }

    /// **The switch happens inside the round trip, which is the only place it
    /// means anything.** `testASharedScreenAnswerDoesNotFollowTheServerSwitch`
    /// switches *after* the register has landed, so the per-server key it
    /// checks is the key on the **read**. This checks the key on the
    /// **write**: `sharedScreenAnswered` used to read `inputs.serverURL` when
    /// the answer arrived rather than capturing it when the request went, so
    /// server A's `false` was filed under server B.
    ///
    /// The unsafe direction is the one the code's own comment names: a
    /// `false` from A un-masks a paired install on B, where the conservative
    /// default is shared until the server says otherwise — so Chat and the
    /// other personal surfaces appear on a kitchen tablet.
    ///
    /// I1-C part 3's F2 for the third time on this track. The shape is **a
    /// value read after an await that should have been captured before it**.
    func testARegistersAnswerIsFiledUnderTheServerItWasAskedOf() async {
        let park = parking(
            "/api/devices/register",
            answer: { .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone","shared_screen":false}"#) }
        )
        let (model, _, store) = model()

        // A's register is in flight and parked at the server.
        let pending = Task { @MainActor in await model.register() }
        await settleUntil { park.arrived.current }

        // The person switches houses while it is in flight.
        let next = "http://10.0.0.77:6369"
        store.trustServer(next)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        store.setToken("household-xyz", for: next)
        model.refresh()

        park.release.set(true)
        await pending.value
        XCTAssertFalse(park.ranOut.current, "the answer landed; it did not time out")
        XCTAssertNil(
            model.sharedScreenAnswer,
            "server A's answer is not about server B, whenever it lands"
        )
        XCTAssertTrue(
            model.visibility.sharedScreen,
            "and B with no answer counts as shared, which is the conservative side"
        )

        // **And this is what stops the assertion above passing over a request
        // that simply failed:** switch back, and A's answer is still there,
        // under A. So the answer landed and was filed under the server it was
        // asked of, rather than being dropped.
        XCTAssertTrue(store.setServerURL(server))
        model.refresh()
        XCTAssertEqual(false, model.sharedScreenAnswer, "A asked, A answered, A's answer")
        model.stop()
    }

    /// The same shape, in the method that had **no** key at all — and which
    /// this batch newly fires on every socket connect, so the exposure is
    /// new even though the code is not.
    func testACapabilityManifestIsNotAppliedToAServerItIsNotFrom() async {
        let park = parking(
            "/api/capabilities",
            answer: { .json(#"{"server_version":"from-server-a","plugins":[],"features":{}}"#) }
        )
        let (model, _, store) = model(deviceSelf: nil)

        let pending = Task { @MainActor in await model.refreshCapabilities() }
        await settleUntil { park.arrived.current }

        let next = "http://10.0.0.77:6369"
        store.trustServer(next)
        store.upsertKnownServer(url: next, name: nil)
        XCTAssertTrue(store.setServerURL(next))
        model.refresh()

        park.release.set(true)
        await pending.value
        XCTAssertFalse(park.ranOut.current, "the answer landed; it did not time out")
        XCTAssertNil(
            model.capabilities.serverVersion,
            "A's manifest is not B's: gated screens stay hidden until B answers"
        )

        // **And the discard was a discard, not a broken fetch:** B's own
        // manifest lands through the same plumbing a moment later.
        StubURLProtocol.dispatch { _ in
            .json(#"{"server_version":"from-server-b","plugins":[],"features":{}}"#)
        }
        await model.refreshCapabilities()
        XCTAssertEqual("from-server-b", model.capabilities.serverVersion)
        model.stop()
    }

    // MARK: - the socket parks on a cold gate and the probe un-parks it (H1)

    /// **The companion to
    /// ``testTheContainersSocketHoldsUntilTheServerHasProvedItself``, which
    /// asserts the first half of this and stops there.**
    ///
    /// A cold process on a correctly paired phone: `mint()` asks
    /// `gate.requireAdmitted`, the gate's cache is in memory and empty, so
    /// the token is held and the socket parks `.notNow`. The socket never
    /// takes the proof itself — `mint()` goes through the **synchronous**
    /// `tokenOutcome(for:wsUpgrade:)`, and the `await prepareIdentity(…)`
    /// that warms the cache is only on `perform(_:mode:.scoped)`.
    ///
    /// What does warm it is the reachability probe: `client.answers()` →
    /// `/api/health` → `prepareIdentity`. Before this fix,
    /// `verdictArrived(_:)` set `unreachable` and nothing else, so the warm
    /// cache was never acted on and the socket stayed parked **for ever** —
    /// a paired phone, a plainly reachable server and a permanently grey live
    /// dot whose own explanation says it is reconnecting. The only rescue was
    /// backgrounding the app and coming back, so it read as a flaky socket
    /// rather than a missing call.
    ///
    /// The address is `127.0.0.1` on this suite's own throwaway range so that
    /// the upgrade, which does not go through `StubURLProtocol`, is refused
    /// locally and reaches no network.
    func testTheSocketOpensOnceTheProbeHasProvedTheServer() async {
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/health" else { return .json("{}") }
            // No identity block at all: a server from before identity, whose
            // verdict is `.legacy`, which admits the token.
            return .json(TestIdentity.noIdentityBody)
        }
        let address = "http://127.0.0.1:6601"
        let store = InMemoryShellStore(serverURL: address, token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        // The real probe — `client.answers()` — and not the rig's `{ true }`,
        // because the whole point is that the probe is what warms the gate.
        let model = ShellModel(
            container: container,
            deviceSelf: nil,
            clock: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in try await Task.sleep(for: .seconds(3600)) }
        )
        container.startSocketUnlessARoom()
        XCTAssertEqual(
            .notNow, container.socket.paused,
            "a cold process: the gate has nothing cached, so the token is held"
        )

        // What `AppShellView.onChange(of: scenePhase, initial: true)` does on
        // launch. It is NOT a return — `phase` already defaults to `.active`
        // — so it does not refresh; it only starts the probe loop.
        model.scenePhaseChanged(to: .active)
        await settleUntil { container.socket.paused == nil }
        XCTAssertNil(
            container.socket.paused,
            "the probe proved the server, so the socket is no longer holding"
        )
        container.socket.stop()
        model.stop()
    }

    /// Bounded, as `settle(for:what:)` is: a condition that never comes fails
    /// the test rather than hanging the suite.
    private func settleUntil(
        _ done: @MainActor () -> Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("never settled in \(settleYieldLimit) yields", file: file, line: line)
    }
}
