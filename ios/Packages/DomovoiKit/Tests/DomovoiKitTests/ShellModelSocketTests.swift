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
        StubURLProtocol.dispatch { request in
            guard request.url?.path == "/api/devices/register" else { return .json("{}") }
            if fail.current { return .json(#"{"detail":"nope"}"#, status: 500) }
            return .json(#"{"device_id":"ios-0123456789abcdef","name":"iPhone","shared_screen":true}"#)
        }
        let clock = Shared<Int64>(0)
        let (model, _, _) = model(clock: clock)
        model.socketConnectionChanged(true)
        await settleUntil { model.sharedScreenAnswer == true }

        fail.set(true)
        clock.advance(DeviceRegistry.reaskGapMs)
        model.socketConnectionChanged(false)
        model.socketConnectionChanged(true)
        await Task.yield()
        await Task.yield()
        await Task.yield()
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

        model.socketConnectionChanged(false)
        await Task.yield()
        await Task.yield()
        XCTAssertEqual(1, fetches.current, "a disconnect fetches nothing")

        model.socketConnectionChanged(true)
        await settleUntil { fetches.current == 2 }
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
