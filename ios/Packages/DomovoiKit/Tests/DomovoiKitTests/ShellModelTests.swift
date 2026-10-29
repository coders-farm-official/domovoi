import XCTest
@testable import DomovoiKit

/// The shell as one object: what it draws, where the lifecycle goes, and the
/// four out-of-band payloads (``ShellModel``).
///
/// `AppShell.kt` has no test of its own — its state lives inside the
/// composable that draws it — so there is no file to port case for case.
/// These are its `remember`s, `LaunchedEffect`s and `repeatOnLifecycle`
/// blocks, driven one at a time.
@MainActor
final class ShellModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// A model over a fake clock, a counted probe and a `wait` that parks, so
    /// nothing here waits on real time or on how two tasks interleave.
    @MainActor
    private final class Rig {
        let clock = Shared<Int64>(0)
        let probes = Shared<Int>(0)
        let parked = Shared<Bool>(false)
        let lines = Shared<[String]>([])
        let store: InMemoryShellStore
        let container: AppContainer
        let model: ShellModel

        /// - Parameters:
        ///   - answers: whether the server answers the probe.
        ///   - costMs: how long each probe takes on the fake clock.
        init(
            serverURL: String,
            token: String? = nil,
            preferLocal: Bool = false,
            phoneCanChat: Bool = false,
            answers: Bool = true,
            costMs: Int64 = 300
        ) {
            let store = InMemoryShellStore(serverURL: serverURL, preferLocal: preferLocal, token: token)
            self.store = store
            let lines = self.lines
            container = AppContainer(
                store: store,
                vault: TokenVault(items: InMemoryKeychain()),
                lock: HouseholdLock(),
                clock: { 0 },
                configuration: StubURLProtocol.session(timeout: 1),
                log: { who, what in lines.mutate { $0.append("\(who): \(what)") } }
            )
            let clock = self.clock
            let probes = self.probes
            let parked = self.parked
            model = ShellModel(
                container: container,
                phoneCanChat: phoneCanChat,
                probe: {
                    clock.advance(costMs)
                    probes.mutate { $0 += 1 }
                    return answers
                },
                clock: { clock.current },
                wait: { _ in
                    parked.set(true)
                    try await Task.sleep(for: .seconds(3600))
                }
            )
        }

        /// Wait until the loop has probed and is sitting in its `wait`.
        ///
        /// Bounded — see `settle(for:what:reset:file:line:)` in
        /// `Shared.swift`, and `settleVerdict(_:)` below, which has said why
        /// since this batch began.
        func settle(file: StaticString = #filePath, line: UInt = #line) async {
            await DomovoiKitTests.settle(
                for: parked,
                what: "the shell's reachability loop",
                file: file,
                line: line
            )
        }

        /// The server refuses the token this phone holds, through a real
        /// request, so the client latches `pairingRequired` the way it does on
        /// a phone: the gate admits a pairing prompt only for a server that
        /// has proved itself, so the stub answers the identity probe as the
        /// real server and everything else with a 401 (A6-03 - a rogue's 401
        /// must not invite a paste).
        func serverRefusesTheToken() async {
            let identity = TestIdentity.real
            StubURLProtocol.dispatch { request in
                let challenge = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "challenge" }?.value
                if let challenge {
                    return .json((try? identity.healthBody(challenge: challenge)) ?? "{}")
                }
                return .json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401)
            }
            _ = try? await container.client.get("/api/devices/me", as: [String: String].self)
        }

        /// Give the main-actor hop a chance to land. Bounded, so a wiring
        /// that never publishes fails rather than hangs.
        func settleVerdict(_ condition: () -> Bool) async -> Bool {
            for _ in 0..<5_000 {
                if condition() { return true }
                await Task.yield()
            }
            return false
        }
    }

    // MARK: - what is drawn

    /// A paired server that answers: the workspace, at Home, with the name in
    /// the pill.
    func testAPairedServerOpensOnTheWorkspaceAtHome() {
        let rig = Rig(serverURL: server, token: "household-abc")
        XCTAssertEqual(.workspace, rig.model.screen)
        XCTAssertTrue(rig.model.isPaired)
        XCTAssertEqual(startRoute, rig.model.navigation.route)
        XCTAssertEqual("10.0.0.42:6369", rig.model.serverLabel)
        XCTAssertEqual("10.0.0.42:6369", rig.model.chrome.pill?.label)
        rig.model.stop()
    }

    /// With no server at all the first screen is the local shell, which is
    /// the state a fresh install is in — and the state the launch test now
    /// asserts a `connect` button in.
    func testAFreshInstallIsTheLocalShell() {
        let rig = Rig(serverURL: "")
        XCTAssertEqual(.localMedia(.noServer), rig.model.screen)
        XCTAssertFalse(rig.model.isPaired)
        XCTAssertEqual("", rig.model.serverLabel)
        XCTAssertEqual("no server yet · pick one below", rig.model.chrome.serverCard.reason)
        rig.model.stop()
    }

    /// A paired install reads as a **shared screen** until the server says
    /// otherwise (``DeviceRegistry/isSharedScreen(answer:paired:)``), and
    /// that shows: the bar and the local tabs lose the private pages until
    /// the answer lands. The conservative way round, as part 1 chose it.
    func testAPairedInstallIsASharedScreenUntilTheServerSaysOtherwise() {
        let rig = Rig(serverURL: server, token: "household-abc", phoneCanChat: true)
        XCTAssertTrue(rig.model.visibility.sharedScreen)
        XCTAssertFalse(rig.model.phoneBar.contains(.chat))
        XCTAssertEqual([.music, .videos], rig.model.localShellTabs)

        rig.model.sharedScreenAnswered(false)
        XCTAssertFalse(rig.model.visibility.sharedScreen)
        XCTAssertTrue(rig.model.phoneBar.contains(.chat))
        XCTAssertEqual([.music, .videos, .files, .chat], rig.model.localShellTabs)
        rig.model.stop()
    }

    /// The open local tab is never out of range: the device being marked as a
    /// shared screen takes two tabs away under it, and it falls back to the
    /// first rather than selecting nothing (Android's `LaunchedEffect(tabs)`).
    func testATabListThatShrinksUnderTheOpenTabFallsBackToTheFirst() {
        let rig = Rig(serverURL: server, token: "household-abc", preferLocal: true, phoneCanChat: true)
        rig.model.sharedScreenAnswered(false)
        rig.model.localSelection = .chat
        XCTAssertEqual(.chat, rig.model.localRoute)

        rig.model.sharedScreenAnswered(true)
        XCTAssertEqual([.music, .videos], rig.model.localShellTabs)
        XCTAssertEqual(.music, rig.model.localRoute, "not an index into a list that no longer has it")
        // Setting by route, not by index, is the other half of that: a route
        // the list does not have is not selected at all.
        rig.model.localSelection = .files
        XCTAssertEqual(.music, rig.model.localRoute)
        rig.model.stop()
    }

    /// The capability manifest is empty until a fetch lands, and a failed
    /// fetch leaves it empty: both gated screens stay hidden rather than
    /// appearing and then 404ing.
    func testAFailedCapabilityFetchHidesTheGatedScreens() async {
        StubURLProtocol.dispatch { _ in .json("{}", status: 500) }
        let rig = Rig(serverURL: server, token: "household-abc")
        rig.model.sharedScreenAnswered(false)
        await rig.model.refreshCapabilities()
        XCTAssertFalse(rig.model.everything.contains(.stations))
        XCTAssertFalse(rig.model.everything.contains(.images))
        XCTAssertTrue(rig.model.everything.contains(.podcasts), "an ungated screen is still offered")
        XCTAssertFalse(rig.model.everything.contains(.music), "Music is on the bar, so it is not also a tile")
        rig.model.stop()
    }

    // MARK: - the lifecycle (map §9.2, and the hazard part 1 left)

    /// The loop's verdict reaches the shell: it runs off the main actor and
    /// hops, and the hop is wiring that can be absent. Watched failing by not
    /// calling `relay.connect(…)` in `init`: `unreachable` stays false and
    /// ``ShellModel/VerdictRelay/missed`` goes to 1.
    func testTheVerdictFromTheLoopReachesTheShell() async {
        // Each probe costs twice the grace, so the first missed one is
        // already a fallback.
        let rig = Rig(serverURL: server, token: "household-abc", answers: false, costMs: 20_000)
        rig.model.refresh()
        let arrived = await rig.settleVerdict { rig.model.unreachable }
        XCTAssertTrue(arrived, "the verdict never arrived")
        XCTAssertEqual(0, rig.model.verdicts.missed)
        XCTAssertEqual(.localMedia(.outOfReach), rig.model.screen, "and the shell swapped under it")
        rig.model.stop()
    }

    /// **The hazard, through the model.** The loop stops when the app leaves
    /// the screen and starts again when it comes back; two minutes away is
    /// not evidence of anything.
    ///
    /// Watched failing by deleting the `.stopCounting` branch's cancel and
    /// pause from ``ReachabilityDriver/update(serverURL:connected:phase:)``:
    /// the probe count keeps climbing while the app is away and the shell is
    /// on the local media by the time it is back.
    func testTheServerIsOnlyAskedWhileTheAppIsOnScreen() async {
        let rig = Rig(serverURL: server, token: "household-abc", answers: false)
        rig.model.refresh()
        await rig.settle()
        XCTAssertEqual(1, rig.probes.current)

        rig.model.scenePhaseChanged(to: .background)
        rig.clock.advance(120_000)
        XCTAssertEqual(1, rig.probes.current, "nothing is asked while the app is away")
        XCTAssertFalse(rig.model.unreachable)

        rig.model.scenePhaseChanged(to: .active)
        await rig.settle()
        XCTAssertEqual(2, rig.probes.current, "one probe on the way back")
        XCTAssertFalse(rig.model.unreachable, "two minutes in the background cost nothing")
        XCTAssertEqual(.workspace, rig.model.screen)
        rig.model.stop()
    }

    /// Going away and coming back makes the server prove itself again; a peek
    /// at the app switcher does **not**, because `.inactive` is not a
    /// departure (`MainActivity.onStop` is).
    func testOnlyARealDepartureBumpsTheIdentityEpoch() {
        let rig = Rig(serverURL: server, token: "household-abc")
        func cameBack() -> Bool {
            rig.lines.current.contains { $0.contains("back in the foreground after") }
        }
        rig.model.scenePhaseChanged(to: .inactive)
        rig.model.scenePhaseChanged(to: .active)
        XCTAssertFalse(cameBack(), "a peek is not a return")

        rig.model.scenePhaseChanged(to: .inactive)
        rig.model.scenePhaseChanged(to: .background)
        rig.model.scenePhaseChanged(to: .inactive)
        rig.model.scenePhaseChanged(to: .active)
        XCTAssertTrue(cameBack(), "log was \(rig.lines.current)")
        rig.model.stop()
    }

    /// Coming back re-asks the device register, no more often than the gap —
    /// and never for a phone that has no server at all.
    func testComingBackReAsksTheRegisterAtTheRightCadence() {
        let rig = Rig(serverURL: server, token: "household-abc")
        rig.model.scenePhaseChanged(to: .background)
        rig.model.scenePhaseChanged(to: .active)
        XCTAssertFalse(rig.model.registerIfDue(reason: .changed), "just asked")
        rig.clock.advance(DeviceRegistry.reaskGapMs)
        XCTAssertTrue(rig.model.registerIfDue(reason: .changed))

        let noServer = Rig(serverURL: "")
        noServer.model.scenePhaseChanged(to: .background)
        noServer.model.scenePhaseChanged(to: .active)
        XCTAssertFalse(noServer.model.registerIfDue(reason: .changed), "nowhere to register")
        rig.model.stop()
        noServer.model.stop()
    }

    /// A network change is noticed, because anything whose answer depends on
    /// which network this is has to be re-drawn when it changes.
    func testANetworkChangeIsNoticed() async {
        let rig = Rig(serverURL: server, token: "household-abc")
        XCTAssertEqual(0, rig.model.networkGeneration)
        rig.container.network.seen(.path, "default") { facts in
            var next = facts
            next.status = "satisfied"
            next.addresses = ["192.168.0.5/24"]
            return next
        }
        XCTAssertEqual(0, rig.container.network.changeCount, "the first picture is not a change")
        rig.container.network.seen(.path, "default") { facts in
            var next = facts
            next.addresses = ["10.1.2.3/24"]
            return next
        }
        let told = await rig.settleVerdict { rig.model.networkGeneration == 1 }
        XCTAssertTrue(told, "the shell was not told; generation is \(rig.model.networkGeneration)")
        rig.model.stop()
    }

    // MARK: - the four out-of-band payloads (map §1.5)

    /// `"picker"` opens the connection dialog, and it does so from **either**
    /// shell: the dialog is owned above both, because picking a side swaps
    /// the shell underneath it.
    func testThePickerOpensFromEitherShell() {
        for preferLocal in [false, true] {
            let rig = Rig(serverURL: server, token: "household-abc", preferLocal: preferLocal)
            XCTAssertFalse(rig.model.showConnection)
            rig.container.requestPicker()
            rig.model.refresh()
            XCTAssertTrue(rig.model.showConnection, "preferLocal: \(preferLocal)")
            XCTAssertNil(rig.container.pendingRoute, "and it was taken, so it does not reopen")
            rig.model.stop()
        }
    }

    /// In the workspace an allowed route is navigated to, and Containers —
    /// a container incident's tap — lands on Settings → Health.
    func testAnOutsideRouteNavigatesInTheWorkspace() {
        let rig = Rig(serverURL: server, token: "household-abc")
        XCTAssertTrue(rig.container.requestRoute("containers"))
        rig.model.refresh()
        XCTAssertEqual(.settings, rig.model.navigation.route)
        XCTAssertEqual("health", rig.model.navigation.settingsTab)
        XCTAssertNil(rig.container.pendingRoute)
        rig.model.stop()
    }

    /// In the local shell only `"chat"` is acted on, and only when there is a
    /// Chat tab to select. **Anything else waits**: a timer alert tapped
    /// while the server is out of reach lands on Home when the workspace
    /// comes back, rather than being thrown away.
    func testTheLocalShellTakesChatAndLeavesTheRestPending() {
        let rig = Rig(serverURL: server, token: "household-abc", preferLocal: true, phoneCanChat: true)
        rig.model.sharedScreenAnswered(false)

        XCTAssertTrue(rig.container.requestRoute("home"))
        rig.model.refresh()
        XCTAssertEqual("home", rig.container.pendingRoute, "the local shell has no Home to go to")

        XCTAssertTrue(rig.container.requestRoute("chat"))
        rig.model.refresh()
        XCTAssertNil(rig.container.pendingRoute)
        XCTAssertEqual(.chat, rig.model.localRoute)
        rig.model.stop()
    }

    /// A phone with no Chat tab — a shared screen — leaves even `"chat"`
    /// pending rather than selecting a tab that is not there.
    func testASharedScreenLeavesChatPendingRatherThanSelectingNothing() {
        let rig = Rig(serverURL: server, token: "household-abc", preferLocal: true, phoneCanChat: true)
        rig.model.sharedScreenAnswered(true)
        XCTAssertTrue(rig.container.requestRoute("chat"))
        rig.model.refresh()
        XCTAssertEqual("chat", rig.container.pendingRoute)
        XCTAssertEqual(.music, rig.model.localRoute)
        rig.model.stop()
    }

    /// The pairing screen and the token card can navigate nowhere, so a
    /// request waits for a shell that can.
    func testAScreenThatReplacesTheShellLeavesTheRequestPending() async {
        let rig = Rig(serverURL: server)
        await rig.serverRefusesTheToken()
        XCTAssertTrue(rig.container.requestRoute("chat"))
        rig.model.refresh()
        XCTAssertEqual(.pairing, rig.model.screen)
        XCTAssertEqual("chat", rig.container.pendingRoute)

        // Pairing done: the workspace is up, and the request it was holding
        // is acted on.
        rig.container.client.clearPairingRequired()
        rig.store.setToken("household-abc", for: server)
        rig.model.pairingDone()
        XCTAssertEqual(.workspace, rig.model.screen)
        XCTAssertEqual(.chat, rig.model.navigation.route)
        XCTAssertNil(rig.container.pendingRoute)
        rig.model.stop()
    }

    /// A help notification's thread is opened **only if this phone is still
    /// on the server that sent it**. That is a privacy rule, not a tidiness
    /// one: the thread id means nothing on another household's server, and
    /// asking for it would tell that server an id from this one.
    func testAHelpThreadOpensOnlyOnTheServerThatSentIt() {
        let rig = Rig(serverURL: server, token: "household-abc")
        let mine = AppContainer.serverKey(server)
        XCTAssertNotNil(mine)
        XCTAssertFalse(rig.container.requestChatThread(serverKey: "deadbeef", thread: 7, title: "Satellite"))
        XCTAssertNil(rig.container.pendingChatThread)
        XCTAssertFalse(rig.container.requestChatThread(serverKey: nil, thread: 7, title: nil),
                       "a notification with no key is not a match for anything")

        XCTAssertTrue(rig.container.requestChatThread(serverKey: mine, thread: 7, title: nil))
        XCTAssertEqual(PendingChatThread(thread: 7, title: "Help"), rig.container.pendingChatThread)
        XCTAssertEqual(7, rig.container.takePendingChatThread()?.thread)
        XCTAssertNil(rig.container.pendingChatThread, "taken, so a redraw does not open it twice")

        // The key is the **normalised** URL's, so the same server written
        // with a trailing slash still matches. Only the trailing slash:
        // ``ServerCredentials/normalize(_:)`` does not fold the scheme's
        // case, so "HTTP://" is a different key - which is the Kotlin's
        // behaviour too, and both ends build the key the same way.
        XCTAssertTrue(rig.container.requestChatThread(
            serverKey: AppContainer.serverKey("http://10.0.0.42:6369/"),
            thread: 9,
            title: "Satellite"
        ))
        XCTAssertEqual("Satellite", rig.container.pendingChatThread?.title)

        // Switch servers and the same notification no longer matches.
        rig.store.setServerURL("http://10.0.0.7:6369")
        XCTAssertFalse(rig.container.requestChatThread(serverKey: mine, thread: 11, title: nil))
        rig.model.stop()
    }

    /// A blank address has no key, so a phone with no server matches nothing
    /// — including another `nil`.
    func testAPhoneWithNoServerMatchesNoNotification() {
        let rig = Rig(serverURL: "")
        XCTAssertNil(AppContainer.serverKey(""))
        XCTAssertNil(AppContainer.serverKey("   "))
        XCTAssertFalse(rig.container.requestChatThread(serverKey: nil, thread: 7, title: nil))
        XCTAssertNil(rig.container.pendingChatThread)
        rig.model.stop()
    }

    /// The other two payloads: a chat on **this phone** (asked for in
    /// Settings → About, where there may be no home server to ask), and a
    /// particular chat in a particular place (Files' "ask about this
    /// document"). Both are taken once.
    func testThePhoneChatAndTheParticularChatAreEachTakenOnce() {
        let rig = Rig(serverURL: server, token: "household-abc")
        XCTAssertFalse(rig.container.pendingPhoneChat)
        rig.container.requestPhoneChat()
        XCTAssertTrue(rig.container.pendingPhoneChat)
        XCTAssertTrue(rig.container.takePendingPhoneChat())
        XCTAssertFalse(rig.container.takePendingPhoneChat())

        let asked = ChatOpenRequest(place: .server, id: "42")
        rig.container.requestChat(asked)
        XCTAssertEqual(asked, rig.container.pendingChat)
        XCTAssertEqual(asked, rig.container.takePendingChat())
        XCTAssertNil(rig.container.takePendingChat())
        // A chat on the phone and a chat on the server are different places
        // with their own ids, which is why the request carries one.
        XCTAssertNotEqual(ChatOpenRequest(place: .phone, id: "42"), asked)
        rig.model.stop()
    }

    // MARK: - the ways out of a stuck state

    /// "not now" on the pairing screen lasts exactly as long as the token it
    /// was tapped with: a new token puts the screen back, and so does a fresh
    /// launch (Android's `remember(deviceToken)`).
    func testNotNowLastsAsLongAsTheTokenItWasTappedWith() async {
        let rig = Rig(serverURL: server)
        await rig.serverRefusesTheToken()
        rig.model.refresh()
        XCTAssertEqual(.pairing, rig.model.screen)

        rig.model.dismissPairing()
        XCTAssertTrue(rig.model.pairingDismissed)
        XCTAssertEqual(.workspace, rig.model.screen, "they asked to get on with it")

        // A token arrives — the server issued one, or the person pasted one
        // on another screen. The dismissal was about the old state.
        rig.store.setToken("household-abc", for: server)
        XCTAssertFalse(rig.model.pairingDismissed)
        XCTAssertEqual(.pairing, rig.model.screen)
        rig.model.stop()
    }

    /// `use this phone's media` on the token card is sticky: it is the
    /// person's own choice, it survives a restart, and nothing but
    /// `chooseServer` undoes it. A button that only looked like it did this
    /// would be the misleading control the house rule forbids.
    func testUseThisPhonesMediaIsStickyAndReversible() {
        let rig = Rig(serverURL: server, token: "household-abc")
        XCTAssertEqual(.workspace, rig.model.screen)
        rig.model.chooseThisPhone()
        XCTAssertEqual(.localMedia(.choseThisPhone), rig.model.screen)
        XCTAssertTrue(rig.store.preferLocal, "written through, so a restart keeps it")

        rig.model.chooseServer()
        XCTAssertFalse(rig.store.preferLocal)
        XCTAssertEqual(.workspace, rig.model.screen)
        rig.model.stop()
    }

    /// `try the old token again` drops the **rejection** and keeps the
    /// token: it means "send the same one once more", which is what a person
    /// wants after a server restart that lost its device table for a moment.
    /// A version of it that cleared the token would be a different button
    /// wearing this one's label.
    func testTryingTheOldTokenAgainDropsTheRejection() {
        let rig = Rig(serverURL: server, token: "household-abc")
        rig.store.markTokenRejected(server)
        rig.model.refresh()
        XCTAssertEqual(.tokenTroubleCard(.notRecognised(waitMs: 0)), rig.model.screen)
        XCTAssertTrue(tokenTroubleOffersOldTokenRetry(.notRecognised(waitMs: 0)))

        rig.model.tryTheOldTokenAgain()
        XCTAssertFalse(rig.container.tokenHealth.isRejected(server))
        XCTAssertTrue(rig.model.isPaired, "the same token, sent again")
        XCTAssertEqual(.workspace, rig.model.screen, "and the card is gone until the server refuses it again")
        rig.model.stop()
    }

    /// The trust-again confirmation cannot be raised when there is nothing to
    /// trust: an `Unproven` answer reads as a mismatch but proved no key, so
    /// there is no key to pin and no yes to offer.
    func testTrustAgainCannotBeRaisedWithNothingToTrust() {
        let rig = Rig(serverURL: server, token: "household-abc")
        XCTAssertNil(rig.model.changedIdentityHere)
        rig.model.askTrustAgain()
        XCTAssertNil(rig.model.trustAgain)
        rig.model.stop()
    }
}
