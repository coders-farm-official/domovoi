import XCTest
@testable import DomovoiKit

/// The state socket: the ladder, the park, the credential, and §8 in full.
///
/// **Android has no test of `StateBus` at all** — no reconnect test, no
/// backoff test, no subscribe-frame test, no `connected` transition test (map
/// §1.9). What it does pin is what the *upgrade request* carries
/// (`test/net/IdentityGateTest.kt`, `test/net/TokenHealthTest.kt`,
/// `test/net/HouseholdLockTest.kt`), and §8.2's table is ported case for case
/// below. So a divergence in the reconnect loop will not show up as a failing
/// Android test, which is why these exist.
final class StateSocketTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    // MARK: - the ladder, and what it is NOT for

    /// 1000, 1600, 2560, 4096, 6553, 10484, 15000, 15000 — and the sixth rung
    /// is 10484, not the 10485 `ANDROID-HOME-MAP.md` §1.4 writes: 6553 × 1.6
    /// is 10484.8 and `toLong()` truncates. The map is wrong by one and the
    /// Kotlin is what this follows.
    func testTheBackoffLadderIsTheKotlinLadder() {
        var backoff = SocketBackoff()
        var rungs: [Int64] = []
        for _ in 0..<8 { rungs.append(backoff.take()) }
        XCTAssertEqual([1000, 1600, 2560, 4096, 6553, 10484, 15000, 15000], rungs)
        backoff.reset()
        XCTAssertEqual(1000, backoff.take(), "onOpen resets it to the first second")
    }

    /// **The rule this batch exists to get right** (map §8.3): the ladder is
    /// for transport failures. A nil `mint()` parks.
    func testOnlyATransportFailureTakesARungOffTheLadder() {
        var backoff = SocketBackoff()
        XCTAssertEqual(.park(.notNow), socketNext(after: .notNow, backoff: &backoff))
        XCTAssertEqual(.park(.noServer), socketNext(after: .noServer, backoff: &backoff))
        XCTAssertEqual(.park(.addressUnusable), socketNext(after: .addressUnusable, backoff: &backoff))
        XCTAssertEqual(
            SocketBackoff.firstMs, backoff.delayMs,
            "three parks and the ladder has not moved"
        )
        XCTAssertEqual(.retry(afterMs: 1000), socketNext(after: .lost, backoff: &backoff))
        XCTAssertEqual(.retry(afterMs: 1600), socketNext(after: .lost, backoff: &backoff))
        XCTAssertEqual(.live, socketNext(after: .opened, backoff: &backoff))
        XCTAssertEqual(SocketBackoff.firstMs, backoff.delayMs, "and an open resets it")
    }

    // MARK: - the address

    func testTheSocketAddressIsTheBaseWithItsSchemeSwapped() {
        XCTAssertEqual("ws://10.0.0.42:6369/ws/state", stateSocketURL(base: "http://10.0.0.42:6369"))
        XCTAssertEqual("wss://domovoi.example:443/ws/state", stateSocketURL(base: "https://domovoi.example:443"))
        XCTAssertNil(stateSocketURL(base: ""), "no server, no address")
        XCTAssertNil(stateSocketURL(base: "   "), "and blank is no server")
    }

    // MARK: - what a frame is

    func testOnlyAJsonObjectWithAStringTypeIsAFrame() {
        XCTAssertEqual("timers.changed", StateEvent.parse(#"{"type":"timers.changed"}"#)?.type)
        XCTAssertNil(StateEvent.parse("not json at all"), "unparseable is dropped")
        XCTAssertNil(StateEvent.parse("[1,2,3]"), "a JSON array is not an object")
        XCTAssertNil(StateEvent.parse(#"{"data":{"a":1}}"#), "a frame with no type is dropped")
    }

    /// The one divergence from Kotlin's `jsonPrimitive.content`, written down
    /// so it is a decision and not a surprise.
    func testAFrameWhoseTypeIsANumberIsDropped() {
        XCTAssertNil(StateEvent.parse(#"{"type":5}"#))
    }

    func testAFramesPayloadDecodesFromTheOriginalBytes() {
        struct Room: Decodable, Equatable { let room_id: String; let rx_mbits: Double? }
        let frame = #"{"type":"satellites.wifi.changed","data":[{"room_id":"kitchen","rx_mbits":48.5}]}"#
        let event = StateEvent.parse(frame)
        XCTAssertEqual([Room(room_id: "kitchen", rx_mbits: 48.5)], event?.data(as: [Room].self))
    }

    // MARK: - §8.2's table, case for case

    /// One socket over a real ``DomovoiClient``, so `mint()` applies the real
    /// six rules rather than a stub's idea of them.
    private func socket(
        base: String? = nil,
        token: String? = nil,
        gate: any TokenGate = AdmitsAnyServer(),
        health: TokenHealth? = nil,
        lock: HouseholdLock = HouseholdLock(),
        transport: FakeSocketTransport,
        refusals: Shared<[(Int, String?)]>? = nil,
        up: Shared<[Bool]>? = nil,
        events: Shared<[String]>? = nil,
        clock: (@Sendable () -> Int64)? = nil,
        wait: (@Sendable (Int64) async throws -> Void)? = nil
    ) -> (StateSocket, DomovoiClient) {
        let address = server
        let held = token
        let client = DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in held }),
            gate: gate,
            health: health,
            lock: lock,
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let aim = base ?? address
        let socket = StateSocket(
            base: { aim },
            upgrade: { client.webSocketUpgrade($0) },
            transport: transport,
            noteRefusal: { status, body in refusals?.mutate { $0.append((status, body)) } },
            clock: clock ?? { 0 },
            wait: wait ?? { _ in try await Task.sleep(for: .seconds(3600)) },
            publish: { value in up?.mutate { $0.append(value) } },
            event: { frame in events?.mutate { $0.append(frame.type) } }
        )
        return (socket, client)
    }

    /// A verified, paired phone: the token rides the **upgrade request** as
    /// `X-Device-Token`, on `GET /ws/state`.
    func testTheTokenRidesTheUpgradeRequest() {
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", transport: fake)
        socket.start()
        XCTAssertEqual(1, fake.opens)
        XCTAssertEqual("/ws/state", fake.last?.path)
        XCTAssertEqual("household-abc", fake.last?.token)
    }

    /// Room mode: `chain.proceed(bare)` — the upgrade goes, with no token.
    /// Android's `HouseholdLockTest.anUpgradeThatSetTheHeaderItselfLosesItInRoomMode`.
    func testRoomModeOpensTheSocketWithNoToken() {
        let lock = HouseholdLock()
        lock.setRoomModeActive(true)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", lock: lock, transport: fake)
        socket.start()
        XCTAssertEqual(1, fake.opens, "the connection is still attempted")
        XCTAssertNil(fake.last?.token)
    }

    /// Not the active server's scope: bare, as Android proceeds bare.
    func testASocketAimedAtAnotherHostCarriesNoToken() {
        let fake = FakeSocketTransport()
        let (socket, _) = socket(base: "http://10.0.0.99:6369", token: "household-abc", transport: fake)
        socket.start()
        XCTAssertEqual(1, fake.opens)
        XCTAssertNil(fake.last?.token)
    }

    /// An unpaired phone: bare, and **that is the load-bearing case** (map
    /// §8.4). Its socket is refused 403 by a server standing right next to
    /// it, which is why `unreachable` cannot be derived from the socket.
    func testAnUnpairedPhoneStillOpensASocket() {
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: nil, transport: fake)
        socket.start()
        XCTAssertEqual(1, fake.opens)
        XCTAssertNil(fake.last?.token)
    }

    /// **The one asymmetry, ported as-is** (map §8.2): a *rejected* token is
    /// withheld, and the upgrade is still attempted **bare**, so the socket
    /// becomes an unpaired phone's socket. That is what stops the reconnect
    /// loop from feeding the server's wrong-token counter, and it is not the
    /// same thing as "nothing is sent".
    ///
    /// Android's `TokenHealthTest.theWebSocketUpgradeStopsCarryingARefusedTokenToo`
    /// asserts both halves — the builder sets it, the interceptor has the last
    /// word. iOS refuses to build it, so the halves here are: a request went,
    /// and it had no header, while the phone demonstrably holds a token.
    func testARejectedTokenIsWithheldButTheUpgradeIsStillAttemptedBare() {
        let book = TokenHealth.InMemoryBook()
        let health = TokenHealth(book: book, clock: { 1_000 })
        book.markTokenRejected(server)
        let fake = FakeSocketTransport()
        let (socket, client) = socket(token: "household-abc", health: health, transport: fake)
        XCTAssertEqual("household-abc", client.deviceToken, "the phone HAS a token")
        socket.start()
        XCTAssertEqual(1, fake.opens, "the upgrade is still attempted")
        XCTAssertNil(fake.last?.token, "...bare")
        XCTAssertNil(socket.paused, "this is not a park: a bare socket is a socket")
    }

    /// A wait: **nothing leaves the phone**, and no timer is set to ask again.
    ///
    /// Android throws `TokenThrottledException` from the interceptor, so the
    /// request never reaches the wire, and `TokenHealthTest.kt:204` asserts
    /// "not one request during the wait", naming the state socket. Here
    /// `mint()` is nil and the transport is never asked.
    func testAWaitSendsNothingAndSetsNoTimer() {
        let book = TokenHealth.InMemoryBook()
        let now = Shared<Int64>(1_000_000)
        let health = TokenHealth(book: book, clock: { now.current })
        health.noteWait(server, waitMs: 30_000)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", health: health, transport: fake)
        socket.start()
        XCTAssertEqual(0, fake.opens, "not one request during the wait")
        XCTAssertTrue(fake.paths.allSatisfy { !$0.hasPrefix("/ws/state") })
        XCTAssertEqual(.notNow, socket.paused)
        XCTAssertFalse(socket.isWaitingToRetry, "a nil mint waits for a state change, not a timer")
        XCTAssertFalse(socket.connected)
    }

    /// The gate refusing — `Mismatch`, `Unproven` or `Unavailable` — is the
    /// same answer: the request never leaves the phone. Android's
    /// `IdentityGateTest.theStateSocketUpgradeIsHeldBackTheSameWay`.
    func testAHeldIdentityNeverBuildsTheUpgrade() {
        struct ClosedGate: TokenGate {
            func requireAdmitted(_ base: ServerURL) throws {
                throw DomovoiError.identityHeld("this looks like a different Domovoi")
            }
            func admitsPairingPrompt(_ base: ServerURL?) -> Bool { false }
        }
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", gate: ClosedGate(), transport: fake)
        socket.start()
        XCTAssertEqual(0, fake.opens)
        XCTAssertTrue(
            fake.paths.none { $0.hasPrefix("/ws/state") },
            "the request never left the phone"
        )
        XCTAssertEqual(.notNow, socket.paused)
        XCTAssertFalse(socket.isWaitingToRetry)
    }

    /// No server saved: parked, not spinning. Android wakes every ≤15 s to
    /// find there is still no address (map §1.2, §9.1).
    func testNoServerParksRatherThanSpinning() {
        let fake = FakeSocketTransport()
        let (socket, _) = socket(base: "", transport: fake)
        socket.start()
        XCTAssertEqual(0, fake.opens)
        XCTAssertEqual(.noServer, socket.paused)
        XCTAssertFalse(socket.isWaitingToRetry)
    }

    /// An address no socket can be opened on at all — `webSocketUpgrade`
    /// returning nil, which is the cleartext rule or an unparseable address.
    /// Android builds the request anyway and fails on the wire; refusing to
    /// build it is "no misleading controls" applied to a socket.
    func testAnAddressTheCleartextRuleRefusesIsNotDialled() {
        let fake = FakeSocketTransport()
        let address = "http://example.com:6369"
        let client = DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in nil }),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let socket = StateSocket(
            base: { address },
            upgrade: { client.webSocketUpgrade($0) },
            transport: fake
        )
        socket.start()
        XCTAssertEqual(0, fake.opens)
        XCTAssertEqual(.addressUnusable, socket.paused)
    }

    // MARK: - the parks, and what un-parks them

    /// A parked socket waits for a **state change**, and waking it while it
    /// is still refused sends nothing — however often.
    ///
    /// This is the measurement behind ``StateSocket/wake()`` being safe to
    /// call from ``ShellModel/refresh()``, which the token-trouble card's
    /// countdown calls once a second for the whole length of a wait.
    func testAWaitingSocketSendsNothingHoweverOftenItIsWoken() {
        let book = TokenHealth.InMemoryBook()
        let now = Shared<Int64>(1_000_000)
        let health = TokenHealth(book: book, clock: { now.current })
        health.noteWait(server, waitMs: 300_000)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", health: health, transport: fake)
        socket.start()
        for _ in 0..<300 { socket.wake() }
        XCTAssertEqual(0, fake.opens, "five minutes of waking, not one request")

        // ...and the wait running out is a state change, which a wake acts on.
        now.advance(300_001)
        socket.wake()
        XCTAssertEqual(1, fake.opens)
        XCTAssertEqual("household-abc", fake.last?.token)
        XCTAssertNil(socket.paused)
    }

    /// A token arriving is a state change too — the other half of "wait for a
    /// state change, not a timer". The park was `.notNow` for a held
    /// identity; the gate opening is what a wake acts on.
    func testTheGateOpeningIsAStateChangeAWakeActsOn() {
        final class Swinging: TokenGate, @unchecked Sendable {
            let open = Shared<Bool>(false)
            func requireAdmitted(_ base: ServerURL) throws {
                guard open.current else { throw DomovoiError.identityHeld("unproven") }
            }
            func admitsPairingPrompt(_ base: ServerURL?) -> Bool { open.current }
        }
        let gate = Swinging()
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", gate: gate, transport: fake)
        socket.start()
        XCTAssertEqual(0, fake.opens)
        socket.wake()
        XCTAssertEqual(0, fake.opens, "waking a question whose answer has not changed changes nothing")
        gate.open.set(true)
        socket.wake()
        XCTAssertEqual(1, fake.opens)
        XCTAssertEqual("household-abc", fake.last?.token)
    }

    /// A server switch reconnects from scratch, with the ladder reset
    /// (`StateBus.restart()`, map §1.5) — not a wake on the old address.
    func testAServerSwitchReconnectsToTheNewAddress() {
        let fake = FakeSocketTransport()
        let aim = Shared<String>(server)
        let token = Shared<String?>("household-abc")
        let client = DomovoiClient(
            book: ServerBook(activeServer: { aim.current }, tokenForServer: { _ in token.current }),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let socket = StateSocket(
            base: { aim.current },
            upgrade: { client.webSocketUpgrade($0) },
            transport: fake
        )
        socket.start()
        XCTAssertEqual(1, fake.opens)
        socket.serverChanged()
        XCTAssertEqual(1, fake.opens, "the address has not changed, so nothing is redialled")
        aim.set("http://10.0.0.77:6369")
        socket.serverChanged()
        XCTAssertEqual(2, fake.opens)
        XCTAssertEqual(true, fake.links.first?.isCancelled, "the old socket is dropped")
        XCTAssertEqual("10.0.0.77", fake.last?.request.url?.host)
    }

    // MARK: - open, subscribe, carry

    func testAnOpenSocketSubscribesToEveryChannelAndPublishesLive() {
        let fake = FakeSocketTransport()
        let up = Shared<[Bool]>([])
        let seen = Shared<[String]>([])
        let (socket, _) = socket(token: "household-abc", transport: fake, up: up, events: seen)
        socket.start()
        XCTAssertEqual([], up.current, "an attempt is not a connection")
        fake.last?.reportOpen()
        XCTAssertEqual([true], up.current)
        XCTAssertEqual([#"{"subscribe": []}"#], fake.last?.sent)
        XCTAssertTrue(socket.connected)

        fake.last?.deliver(#"{"type":"timers.changed","data":null}"#)
        fake.last?.deliver("rubbish")
        fake.last?.deliver(#"{"type":"plugins.changed"}"#)
        XCTAssertEqual(["timers.changed", "plugins.changed"], seen.current)
    }

    /// A transport that reports `opened` from inside `open`, before it has
    /// returned. The subscribe frame used to be sent through the link the
    /// socket had **stored**, which in this order was still nil.
    func testASynchronousOpenStillSubscribes() {
        let fake = FakeSocketTransport(opensImmediately: true)
        let up = Shared<[Bool]>([])
        let (socket, _) = socket(token: "household-abc", transport: fake, up: up)
        socket.start()
        XCTAssertEqual([true], up.current)
        XCTAssertEqual([#"{"subscribe": []}"#], fake.last?.sent)
        XCTAssertFalse(fake.last?.isCancelled ?? true, "and the link is not thrown away")
    }

    /// `connected` is published on a change and only on a change: Android's
    /// `StateFlow` dedupes, and a repeat here would burn a device-register
    /// cadence slot.
    func testLiveIsPublishedOnlyWhenItChanges() {
        let fake = FakeSocketTransport()
        let up = Shared<[Bool]>([])
        let (socket, _) = socket(token: "household-abc", transport: fake, up: up)
        socket.start()
        fake.last?.reportOpen()
        fake.last?.reportOpen()
        fake.last?.deliver(#"{"type":"timers.changed"}"#)
        XCTAssertEqual([true], up.current)
    }

    // MARK: - a drop

    /// A drop publishes `false` and schedules the first retry at one second.
    func testADropSchedulesTheFirstRetryAtOneSecond() async {
        let fake = FakeSocketTransport()
        let up = Shared<[Bool]>([])
        let delays = Shared<[Int64]>([])
        let parked = Shared<Bool>(false)
        let (socket, _) = socket(
            token: "household-abc",
            transport: fake,
            up: up,
            wait: { ms in
                delays.mutate { $0.append(ms) }
                parked.set(true)
                try await Task.sleep(for: .seconds(3600))
            }
        )
        socket.start()
        fake.last?.reportOpen()
        fake.last?.closeFromPeer()
        await settle(for: parked, what: "the socket's reconnect")
        XCTAssertEqual([true, false], up.current)
        XCTAssertEqual([1000], delays.current)
        XCTAssertTrue(socket.isWaitingToRetry)
        XCTAssertNil(socket.paused, "a drop is not a park")
    }

    /// Repeated failures climb the ladder, and an open resets it. Driven with
    /// a `wait` that returns at once and a transport that does nothing on
    /// open, so the loop is stepped rather than raced.
    func testRepeatedFailuresClimbTheLadderAndAnOpenResetsIt() async {
        let fake = FakeSocketTransport()
        let delays = Shared<[Int64]>([])
        let (socket, _) = socket(
            token: "household-abc",
            transport: fake,
            wait: { ms in delays.mutate { $0.append(ms) } }
        )
        socket.start()
        for rung in 1...4 {
            fake.opened.set(false)
            fake.links.last?.fail(status: nil, body: nil)
            await settle(for: fake.opened, what: "retry \(rung)")
        }
        XCTAssertEqual([1000, 1600, 2560, 4096], delays.current)
        XCTAssertEqual(5, fake.opens)

        fake.links.last?.reportOpen()
        fake.opened.set(false)
        fake.links.last?.fail(status: nil, body: nil)
        await settle(for: fake.opened, what: "the retry after a good connection")
        XCTAssertEqual([1000, 1600, 2560, 4096, 1000], delays.current, "an open resets the ladder")
    }

    /// A failed upgrade's status is passed on, and a request that never got
    /// an answer says nothing — so a *withheld* token cannot be mistaken for
    /// a refused one.
    func testOnlyAnAnsweredUpgradeNotesAPossibleRefusal() async {
        let fake = FakeSocketTransport()
        let refusals = Shared<[(Int, String?)]>([])
        let parked = Shared<Bool>(false)
        let (socket, _) = socket(
            token: "household-abc",
            transport: fake,
            refusals: refusals,
            wait: { _ in parked.set(true); try await Task.sleep(for: .seconds(3600)) }
        )
        socket.start()
        fake.links.last?.fail(status: 403, body: nil)
        await settle(for: parked, what: "the socket's reconnect")
        XCTAssertEqual(1, refusals.current.count)
        XCTAssertEqual(403, refusals.current.first?.0)
        XCTAssertNil(refusals.current.first?.1, "there is no body on this path")
    }

    /// **§11.6, confirmed on iOS**: a refused upgrade cannot raise the
    /// pairing screen, because the only thing this path has is a status and
    /// ``isDeviceTokenRefusal(status:body:)`` needs the server's wording —
    /// which lives in the WebSocket close reason, not in anything an
    /// `HTTPURLResponse` carries.
    ///
    /// Asserted with a gate that admits **every** pairing prompt, so the
    /// `false` below is the missing body and nothing else. The pairing screen
    /// is still raised, by `/api/satellites`' tracked 401 (map §11.1); the
    /// socket's own contribution is nil, on both phones. Whether it should be
    /// raised by status alone is an owner decision, in `I1D1-REPORT.md`.
    func testARefusedUpgradeDoesNotRaiseThePairingScreen() async {
        let fake = FakeSocketTransport()
        let parked = Shared<Bool>(false)
        let (socket, client) = socket(
            token: "household-abc",
            transport: fake,
            wait: { _ in parked.set(true); try await Task.sleep(for: .seconds(3600)) }
        )
        XCTAssertFalse(client.pairingRequired)
        socket.start()
        fake.links.last?.fail(status: 403, body: nil)
        await settle(for: parked, what: "the socket's reconnect")
        XCTAssertFalse(
            client.pairingRequired,
            "a status with no wording is not recognisable as a device-token refusal"
        )
        // And the wording, when something does have it, still works — so what
        // is missing is the body and not the predicate.
        client.notePossiblePairingRefusal(status: 403, body: "X-Device-Token or admin session required")
        XCTAssertTrue(client.pairingRequired)
    }

    // MARK: - start, stop, and the scene phase

    func testStartIsIdempotentAndStopMeansStop() {
        let fake = FakeSocketTransport()
        let up = Shared<[Bool]>([])
        let (socket, _) = socket(token: "household-abc", transport: fake, up: up)
        socket.start()
        socket.start()
        socket.start()
        XCTAssertEqual(1, fake.opens)
        fake.last?.reportOpen()

        socket.stop()
        XCTAssertEqual([true, false], up.current)
        XCTAssertFalse(socket.isStarted)
        XCTAssertEqual(true, fake.last?.isCancelled)
        socket.wake()
        socket.serverChanged()
        socket.sceneReturned()
        XCTAssertEqual(1, fake.opens, "a stopped socket is not woken by anything")
    }

    /// **The frozen-app hazard, in the socket's shape** (map §1.6). Nothing
    /// reports a close while the process is suspended, so the flag survives a
    /// dead socket. The return drops the link and re-mints.
    func testASceneReturnDoesNotBelieveTheConnectedFlag() {
        let fake = FakeSocketTransport()
        let up = Shared<[Bool]>([])
        let mints = Shared<Int>(0)
        let token = Shared<String?>("household-abc")
        let address = server
        let client = DomovoiClient(
            book: ServerBook(
                activeServer: { address },
                tokenForServer: { _ in mints.mutate { $0 += 1 }; return token.current }
            ),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let socket = StateSocket(
            base: { address },
            upgrade: { client.webSocketUpgrade($0) },
            transport: fake,
            publish: { value in up.mutate { $0.append(value) } }
        )
        socket.start()
        fake.last?.reportOpen()
        XCTAssertTrue(socket.connected)
        let before = mints.current

        socket.sceneReturned()
        XCTAssertFalse(socket.connected, "the flag is not believed")
        XCTAssertEqual([true, false], up.current)
        XCTAssertEqual(true, fake.links.first?.isCancelled, "and the link is dropped, not reused")
        XCTAssertEqual(2, fake.opens)
        XCTAssertGreaterThan(mints.current, before, "the rule was re-asked, not remembered")
    }

    /// A scene return while the socket is parked re-asks the rule too, and
    /// still parks when the answer has not changed — no request either way.
    func testASceneReturnReAsksAParkedSocketWithoutSendingAnything() {
        let book = TokenHealth.InMemoryBook()
        let health = TokenHealth(book: book, clock: { 1_000 })
        health.noteWait(server, waitMs: 60_000)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", health: health, transport: fake)
        socket.start()
        socket.sceneReturned()
        XCTAssertEqual(0, fake.opens)
        XCTAssertEqual(.notNow, socket.paused)
    }

    /// A socket that has never been up says nothing when it restarts.
    ///
    /// ``StateSocket``'s `restart()` publishes `false` through `tell(connected:)`
    /// because it knows it dropped the link — and a scene return restarts
    /// unconditionally (trap 1), so on a phone that is parked unpaired every
    /// return to the foreground runs that path. Without the dedupe inside
    /// `tell` each one would push another `false` into
    /// `ShellModel.socketConnectionChanged`, which is not a no-op: it is the
    /// header's `live` and the register cadence's input. The rung above —
    /// `advance`'s own `if !current.connected` — does not cover this path,
    /// which is how BREAK 3 removed this guard and no test noticed.
    func testASocketThatWasNeverUpPublishesNothingWhenItRestarts() {
        let book = TokenHealth.InMemoryBook()
        let health = TokenHealth(book: book, clock: { 1_000 })
        health.noteWait(server, waitMs: 60_000)
        let fake = FakeSocketTransport()
        let up = Shared<[Bool]>([])
        let (socket, _) = socket(token: "household-abc", health: health, transport: fake, up: up)
        socket.start()
        for _ in 0..<5 { socket.sceneReturned() }
        XCTAssertEqual(0, fake.opens, "parked, so nothing was dialled")
        XCTAssertEqual([], up.current, "and a drop that was never an open is not news")
    }

    // MARK: - the relay

    /// A connect that lands before anything is listening is counted, not
    /// pretended away: it is a connect whose capability refresh and device
    /// register never happened.
    func testAConnectBeforeAnybodyIsListeningIsCounted() {
        let relay = SocketRelay()
        relay.connectionChanged(true)
        XCTAssertEqual(1, relay.missedConnections)
        let seen = Shared<[Bool]>([])
        relay.connect(connection: { value in seen.mutate { $0.append(value) } }, event: { _ in })
        relay.connectionChanged(false)
        XCTAssertEqual([false], seen.current)
        XCTAssertEqual(1, relay.missedConnections, "and the tally does not grow once it is wired")

        relay.disconnect()
        relay.connectionChanged(true)
        XCTAssertEqual([false], seen.current, "a dropped sink is not called")
        XCTAssertEqual(2, relay.missedConnections)
    }

    // MARK: - the real transport, against a real socket on 127.0.0.1

    /// The same claim as `testTheTokenRidesTheUpgradeRequest`, measured on the
    /// wire: a listener on this session's lane records **the bytes of the
    /// request head**, so "the token rides the upgrade" is what went down a
    /// socket and not what a `URLRequest` in this process happened to hold.
    ///
    /// The listener answers plain HTTP, so the upgrade fails — which is the
    /// point: the assertion is about what arrived, not about a working
    /// socket. See ``LoopbackServer``, which binds 127.0.0.1 on 6600-6699 and
    /// nothing else.
    func testOnTheWireTheUpgradeCarriesTheTokenAndAsksForWsState() async throws {
        let listener = try LoopbackServer(.init(status: 200, body: "{}"))
        defer { listener.stop() }
        let address = "http://127.0.0.1:\(listener.port)"
        let client = DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in "household-on-the-wire" }),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock()
        )
        let parked = Shared<Bool>(false)
        let socket = StateSocket(
            base: { address },
            upgrade: { client.webSocketUpgrade($0) },
            transport: client.webSocketTransport,
            wait: { _ in parked.set(true); try await Task.sleep(for: .seconds(3600)) }
        )
        socket.start()
        await settle(for: parked, what: "the real socket's first failure")
        socket.stop()

        XCTAssertGreaterThanOrEqual(listener.requestCount, 1)
        XCTAssertEqual("GET /ws/state HTTP/1.1", listener.requestLine(at: 0))
        XCTAssertEqual("household-on-the-wire", listener.header(deviceTokenHeader, at: 0))
    }

    /// And the withheld case on the wire: during a wait, **nothing reaches
    /// the listener at all**. Android's `TokenHealthTest.kt:204` — "A minute
    /// of polling (the panels, the state socket, the reach probe): nothing
    /// leaves the phone".
    func testOnTheWireAWaitingPhoneOpensNoSocket() throws {
        let listener = try LoopbackServer(.init(status: 200, body: "{}"))
        defer { listener.stop() }
        let address = "http://127.0.0.1:\(listener.port)"
        let book = TokenHealth.InMemoryBook()
        let health = TokenHealth(book: book, clock: { 1_000 })
        health.noteWait(address, waitMs: 60_000)
        let client = DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in "household-on-the-wire" }),
            gate: AdmitsAnyServer(),
            health: health,
            lock: HouseholdLock()
        )
        let socket = StateSocket(
            base: { address },
            upgrade: { client.webSocketUpgrade($0) },
            transport: client.webSocketTransport
        )
        socket.start()
        for _ in 0..<50 { socket.wake() }
        XCTAssertEqual(.notNow, socket.paused)
        // `paused` is the measurement; the wire count **corroborates** it and
        // is not the proof. This read is synchronous, and had the socket
        // dialled, `URLSession` could not have completed a TCP connect and
        // sent the request head before it — so a bug that made the socket
        // dial would still leave this at 0, and the assertion above is the
        // one that caught it in BREAK 1 (I1-D part 1's review, L3).
        XCTAssertEqual(0, listener.requestCount, "and nothing on the wire either")
        socket.stop()
    }

    // MARK: - an attempt that neither opens nor ends (the review's H2)

    /// **A socket that was accepted and then went silent is not a
    /// connection.** The state is `started`, `paused == nil`, `retry == nil`,
    /// `connected == false` — indistinguishable from "connecting" from
    /// outside, and before this fix nothing recovered it: `wake()` guarded on
    /// `paused != nil`, `start()` was a no-op, `serverChanged()` a no-op
    /// unless the address moved. Only backgrounding the app and returning got
    /// out, so the observed behaviour was "the dot goes green if you leave
    /// the app and come back, and not otherwise".
    ///
    /// The invariant, written as the thing the socket wants: **an attempt
    /// that has neither opened nor ended, and has had
    /// ``stateSocketConnectBudgetMs`` to do one of them, is not a
    /// connection.**
    func testAnAttemptThatNeverOpensOrEndsIsRescuedByAWake() {
        let now = Shared<Int64>(1_000_000)
        let fake = FakeSocketTransport()
        // Reports nothing: not `opened`, not `failed`, not `closed`. A proxy
        // or a captive-portal box that completes the 101 and then answers
        // nothing is the realistic producer.
        let (socket, _) = socket(token: "household-abc", transport: fake, clock: { now.current })
        socket.start()
        XCTAssertEqual(1, fake.opens)
        XCTAssertNil(socket.paused, "not parked — the upgrade was made")
        XCTAssertFalse(socket.connected, "and never answered")
        XCTAssertFalse(socket.isWaitingToRetry, "so nothing at all is pending")

        now.advance(stateSocketConnectBudgetMs)
        socket.wake()
        XCTAssertEqual(2, fake.opens, "the budget ran out, so it is not a connection")
        socket.stop()
    }

    /// **The measurement H2's fix needs, because H1's fix makes `wake()` fire
    /// on every reachability verdict.** A probe answers every few seconds; a
    /// `wake()` that restarted a socket which is legitimately still
    /// connecting would tear down and re-dial on each one, which is worse
    /// than the bug it fixes.
    ///
    /// So the line is time, not state alone, and this is where it is: inside
    /// the budget, however many verdicts land, the attempt count does not
    /// climb.
    func testWakingASocketThatIsStillConnectingDoesNotThrashIt() {
        let now = Shared<Int64>(1_000_000)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", transport: fake, clock: { now.current })
        socket.start()
        XCTAssertEqual(1, fake.opens)

        for _ in 0..<300 { socket.wake() }
        XCTAssertEqual(1, fake.opens, "300 verdicts inside the budget, still one attempt")

        now.advance(stateSocketConnectBudgetMs - 1)
        for _ in 0..<50 { socket.wake() }
        XCTAssertEqual(1, fake.opens, "and right up to the last millisecond of it")

        now.advance(1)
        socket.wake()
        XCTAssertEqual(2, fake.opens, "past it, once")
        for _ in 0..<300 { socket.wake() }
        XCTAssertEqual(2, fake.opens, "and the new attempt gets the whole budget of its own")
        socket.stop()
    }

    /// A socket that **is** up is never restarted by a wake, however stale
    /// the attempt is. The flag is believed here; the one place it is not is
    /// ``StateSocket/sceneReturned()``, which has its own test.
    func testWakingALiveSocketNeverRestartsIt() {
        let now = Shared<Int64>(1_000_000)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", transport: fake, clock: { now.current })
        socket.start()
        fake.last?.reportOpen()
        XCTAssertTrue(socket.connected)

        now.advance(stateSocketConnectBudgetMs * 100)
        for _ in 0..<300 { socket.wake() }
        XCTAssertEqual(1, fake.opens, "an open socket is a connection however old the attempt")
        socket.stop()
    }

    /// And a retry that is counting down is not rescued either — that would
    /// collapse the ladder into a spin, which is the one thing
    /// ``socketNext(after:backoff:)`` exists to prevent.
    func testWakingASocketThatIsCountingDownToARetryDoesNotJumpTheLadder() {
        let now = Shared<Int64>(1_000_000)
        let fake = FakeSocketTransport()
        let (socket, _) = socket(token: "household-abc", transport: fake, clock: { now.current })
        socket.start()
        fake.last?.fail()
        XCTAssertTrue(socket.isWaitingToRetry, "the ladder is counting")
        XCTAssertNil(socket.paused)

        now.advance(stateSocketConnectBudgetMs * 10)
        for _ in 0..<300 { socket.wake() }
        XCTAssertEqual(1, fake.opens, "the countdown owns the next attempt, not a wake")
        XCTAssertEqual(1600, socket.backoffMs, "and the ladder is where the one failure left it")
        socket.stop()
    }

    // MARK: - a frame that is too big, or too deep (the review's U1)

    /// The bound is **chosen**, not inherited from the platform, and `parse`
    /// applies it before `JSONSerialization` sees the bytes.
    func testAFrameOverTheMaximumIsDroppedBeforeItIsParsed() {
        let padding = String(repeating: "a", count: stateSocketMaximumFrameBytes)
        let huge = #"{"type":"timers.changed","data":"\#(padding)"}"#
        XCTAssertGreaterThan(huge.utf8.count, stateSocketMaximumFrameBytes)
        XCTAssertNil(StateEvent.parse(huge), "over the maximum: dropped, unparsed")

        // And the frame that is just inside it is still a frame, so the bound
        // is a bound and not a ban.
        let fits = #"{"type":"timers.changed","data":"\#(String(repeating: "a", count: 1024))"}"#
        XCTAssertEqual("timers.changed", StateEvent.parse(fits)?.type)
    }

    /// What `JSONSerialization` actually does with a deeply nested frame on a
    /// cooperative-pool thread, measured rather than assumed — the review
    /// could not settle it without running Swift.
    ///
    /// **The number this found: see `I1D1-FIX-REPORT.md`.** The assertion is
    /// that it is a dropped frame and not a dead process at every depth a
    /// 1 MiB frame can reach, which is what the size bound caps: two bytes
    /// per level, so ``stateSocketMaximumFrameBytes`` admits at most about
    /// 512 000 levels, and this walks past that.
    func testADeeplyNestedFrameIsDroppedRatherThanCrashing() async {
        func frame(depth: Int) -> String {
            #"{"type":"x","data":"# + String(repeating: "[", count: depth)
                + String(repeating: "]", count: depth) + "}"
        }
        // On the cooperative pool, whose stack is far smaller than the main
        // thread's — which is where a real frame is parsed, so it is where
        // this is measured.
        for depth in [100, 1_000, 10_000, 50_000, 600_000] {
            let text = frame(depth: depth)
            let outcome = await Task.detached {
                StateEvent.parse(text) == nil ? "dropped" : "parsed"
            }.value
            print("U1 depth \(depth) (\(text.utf8.count) bytes): \(outcome)")
            fflush(stdout)
        }
    }
}

extension Array {
    /// `none { … }`, so the assertion reads as Android's does.
    func none(where predicate: (Element) -> Bool) -> Bool {
        !contains(where: predicate)
    }
}
