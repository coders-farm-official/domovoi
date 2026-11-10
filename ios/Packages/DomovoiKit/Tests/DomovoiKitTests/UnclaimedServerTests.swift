import Foundation
import XCTest
@testable import DomovoiKit

/// The app on a Domovoi nobody has claimed yet (owner, 2026-11-04): one
/// screen that says to claim it on the dashboard, instead of a workspace for
/// a house with no admin. Only an explicit `setup_complete: false` from the
/// open status read shows it; anything doubtful leaves the house alone.
///
/// The first block is Android's `UnclaimedServerTest` (70ce74be, cfe9b0dd)
/// case for case. The rest is what Android gets from `remember(serverUrl)`
/// and a `LaunchedEffect` that cancels — the per-server keying of a read
/// that runs to its end here — which is this track's top defect class, so
/// the switch happens **inside** the round trip.
@MainActor
final class UnclaimedServerTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.77:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - Android's five (UnclaimedServerTest.kt)

    func testTheStatusReadSaysWhetherSetupIsDone() {
        XCTAssertEqual(false, setupCompleteOf(Data(#"{"setup_complete": false, "authenticated": false}"#.utf8)))
        XCTAssertEqual(true, setupCompleteOf(Data(#"{"setup_complete": true, "authenticated": true}"#.utf8)))
    }

    func testAStatusThatDoesNotSayIsNotAnAnswer() {
        XCTAssertNil(setupCompleteOf(nil))
        XCTAssertNil(setupCompleteOf(Data(#"{"authenticated": false}"#.utf8)))
        XCTAssertNil(setupCompleteOf(Data(#"{"setup_complete": "no"}"#.utf8)))
        XCTAssertNil(setupCompleteOf(Data("[]".utf8)))
        XCTAssertNil(setupCompleteOf(Data(#"{"setup_complete": null}"#.utf8)))
        // And two Android's JSON tree could not be handed: an empty body and
        // something that is not JSON at all (a router's login page).
        XCTAssertNil(setupCompleteOf(Data()))
        XCTAssertNil(setupCompleteOf(Data("<html>login</html>".utf8)))
    }

    func testOnlyAnExplicitFalseOnAConfiguredServerBlocks() {
        XCTAssertTrue(showsUnclaimed(serverURL: server, setupComplete: false))
        XCTAssertFalse(showsUnclaimed(serverURL: server, setupComplete: true), "claimed")
        XCTAssertFalse(showsUnclaimed(serverURL: server, setupComplete: nil), "not read yet, or the read failed")
        XCTAssertFalse(showsUnclaimed(serverURL: "", setupComplete: false), "no server: the local shell")
        XCTAssertFalse(showsUnclaimed(serverURL: "   ", setupComplete: false))
    }

    func testTheWordsSayWhatToDoAndWhere() {
        XCTAssertEqual("This Domovoi isn't set up yet", UnclaimedServerStrings.title)
        let text = UnclaimedServerStrings.text(address: "192.168.1.20:6369")
        XCTAssertTrue(text.hasPrefix("Open its dashboard in a web browser and claim it, then come back."))
        XCTAssertTrue(text.contains("192.168.1.20:6369"))
        XCTAssertTrue(text.contains("setup code"))
        // The household token is not asked for here: before the claim there
        // is none to give out.
        XCTAssertFalse(text.contains("household token"))
        // The rest, letter for letter, because a UI test finds them by label.
        XCTAssertEqual(
            "Open its dashboard in a web browser and claim it, then come back. On a computer or phone on "
                + "the same network, go to 192.168.1.20:6369: it asks for the setup code from the computer "
                + "Domovoi is installed on, and an admin password.",
            text
        )
        XCTAssertEqual("still not set up — claim it on the dashboard first", UnclaimedServerStrings.still)
        XCTAssertEqual(
            "Not the one setting it up? Ask whoever installed Domovoi, or use this phone's own music, "
                + "videos and files until it's ready.",
            UnclaimedServerStrings.hint
        )
        XCTAssertEqual("Check again", UnclaimedServerStrings.checkAgain)
        XCTAssertEqual("checking…", UnclaimedServerStrings.checking)
        XCTAssertEqual("open the dashboard", UnclaimedServerStrings.openTheDashboard)
        XCTAssertEqual("no browser on this phone", UnclaimedServerStrings.noBrowser)
        XCTAssertEqual("use this phone's media", UnclaimedServerStrings.useThisPhonesMedia)
        XCTAssertEqual("use a different server", UnclaimedServerStrings.useADifferentServer)
    }

    func testOnlyTheButtonsOwnReadSaysStillNotSetUp() {
        XCTAssertTrue(saysStillUnclaimed(fromButton: true, read: false))
        XCTAssertFalse(saysStillUnclaimed(fromButton: true, read: true), "claimed now: the screen just goes")
        XCTAssertFalse(saysStillUnclaimed(fromButton: true, read: nil), "the read failed: nothing to say")
        XCTAssertFalse(saysStillUnclaimed(fromButton: false, read: false), "a resume or socket change")
    }

    func testAnUnclaimedServerIsStillTheWorkspaceSideSoItsScreenShows() {
        // The screen renders in place of the workspace, so a reachable
        // unclaimed server must not drop to local media...
        XCTAssertEqual(.workspace, shellMode(serverURL: server, unreachable: false, pairingRequired: false))
        // ...while "use this phone's media" (preferLocal) still wins.
        XCTAssertEqual(
            .local,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, preferLocal: true)
        )
    }

    // MARK: - the rest of the rules

    /// Android's effect returns early for no server, a server out of reach,
    /// and one already claimed (`AppShell.kt:178`).
    func testItIsNotAskedWithoutAServerOutOfReachOrOnceClaimed() {
        XCTAssertTrue(unclaimedAsks(serverURL: server, unreachable: false, setupComplete: nil))
        XCTAssertTrue(unclaimedAsks(serverURL: server, unreachable: false, setupComplete: false), "check again")
        XCTAssertFalse(unclaimedAsks(serverURL: server, unreachable: false, setupComplete: true), "claimed")
        XCTAssertFalse(unclaimedAsks(serverURL: server, unreachable: true, setupComplete: nil), "out of reach")
        XCTAssertFalse(unclaimedAsks(serverURL: "", unreachable: false, setupComplete: nil), "no server")
    }

    /// `ShellContent` returns for it before the token card and the pairing
    /// screen (`AppShell.kt:317-322`), and the local shell is never replaced.
    func testTheBlockerTakesTheWholeWorkspaceSideAndNeverTheLocalShell() {
        let workspaceSide: [ShellScreen] = [
            .workspace, .pairing, .tokenTroubleCard(.waiting(waitMs: 5_000)), .tokenTroubleCard(.notRecognised(waitMs: 0)),
        ]
        for screen in workspaceSide {
            XCTAssertTrue(unclaimedReplaces(screen, blocks: true), "\(screen)")
            XCTAssertFalse(unclaimedReplaces(screen, blocks: false), "\(screen) on a claimed house")
        }
        for reason in [LocalReason.noServer, .choseThisPhone, .identityChanged, .outOfReach] {
            XCTAssertFalse(unclaimedReplaces(.localMedia(reason), blocks: true), "\(reason)")
        }
    }

    /// `serverUrl.trim().trimEnd('/') + "/"`.
    func testTheDashboardIsTheServersPlainAddress() {
        XCTAssertEqual("http://10.0.0.42:6369/", unclaimedDashboardURL(server)?.absoluteString)
        XCTAssertEqual("http://10.0.0.42:6369/", unclaimedDashboardURL("  http://10.0.0.42:6369// ")?.absoluteString)
        XCTAssertNil(unclaimedDashboardURL(""), "no server, no button")
        XCTAssertNil(unclaimedDashboardURL("   "))
    }

    // MARK: - the model, with reads the test answers

    /// A failed read keeps what was known; a `false` blocks; a `true`
    /// unblocks and is final for that server.
    func testAnswersAreKeptAFailureChangesNothingAndClaimedIsFinal() async {
        let rig = ModelRig(server: server)
        let first = rig.model.ask(.socket, unreachable: false)
        XCTAssertTrue(rig.model.checking, "checking… while the read is out")
        await rig.answer(oldest: false, first)
        XCTAssertTrue(rig.model.blocks)
        XCTAssertFalse(rig.model.checking)

        let failing = rig.model.ask(.foreground, unreachable: false)
        await rig.answer(oldest: nil, failing)
        XCTAssertTrue(rig.model.blocks, "a doubtful answer neither blocks nor unblocks: it keeps what was known")

        let claimed = rig.model.ask(.foreground, unreachable: false)
        await rig.answer(oldest: true, claimed)
        XCTAssertFalse(rig.model.blocks)
        let sent = rig.model.readsSent
        XCTAssertNil(rig.model.ask(.socket, unreachable: false), "claimed: not asked again for this server")
        XCTAssertNil(rig.model.checkAgain(unreachable: false))
        XCTAssertEqual(sent, rig.model.readsSent)
    }

    func testAFailedFirstReadNeverBlocks() async {
        let rig = ModelRig(server: server)
        await rig.answer(oldest: nil, rig.model.ask(.socket, unreachable: false))
        XCTAssertFalse(rig.model.blocks)
        XCTAssertNil(rig.model.setupComplete)
    }

    func testNothingIsAskedOutOfReachOrWithNoServer() {
        let rig = ModelRig(server: server)
        XCTAssertNil(rig.model.ask(.socket, unreachable: true))
        let none = ModelRig(server: "")
        XCTAssertNil(none.model.ask(.socket, unreachable: false))
        XCTAssertEqual(0, rig.model.readsSent + none.model.readsSent)
    }

    /// The button is off while a read is out, and the model refuses it too.
    func testCheckAgainIsOffWhileAReadIsOut() async {
        let rig = ModelRig(server: server)
        let out = rig.model.ask(.socket, unreachable: false)
        XCTAssertNil(rig.model.checkAgain(unreachable: false), "checking…: the button does nothing")
        XCTAssertEqual(1, rig.model.readsSent)
        await rig.answer(oldest: false, out)
        let again = rig.model.checkAgain(unreachable: false)
        XCTAssertNotNil(again)
        await rig.answer(oldest: false, again)
    }

    /// cfe9b0dd: "still not set up" only for the read `Check again` started
    /// — not for the socket read before it, and not for the resume after it.
    func testOnlyTheReadCheckAgainStartedSaysStillNotSetUp() async {
        let rig = ModelRig(server: server)
        await rig.answer(oldest: false, rig.model.ask(.socket, unreachable: false))
        XCTAssertEqual([], rig.said.current, "the socket's read is quiet")

        await rig.answer(oldest: false, rig.model.checkAgain(unreachable: false))
        XCTAssertEqual([UnclaimedServerStrings.still], rig.said.current, "the button's read says it")

        await rig.answer(oldest: false, rig.model.ask(.foreground, unreachable: false))
        await rig.answer(oldest: false, rig.model.ask(.socket, unreachable: false))
        XCTAssertEqual([UnclaimedServerStrings.still], rig.said.current, "a resume or socket change after it is quiet")

        await rig.answer(oldest: nil, rig.model.checkAgain(unreachable: false))
        XCTAssertEqual(1, rig.said.current.count, "a failed check says nothing")
    }

    /// Android restarts its effect on a socket change, so the button's flag
    /// passes to the read that replaced the cancelled one. Here the older
    /// read lands late and is dropped (below), so the newer one says it.
    func testAReadStartedAfterTheButtonCarriesItsWordsWhenItLandsFirst() async {
        let rig = ModelRig(server: server)
        let button = rig.model.checkAgain(unreachable: false)
        let socket = rig.model.ask(.socket, unreachable: false)
        await rig.answer(newest: false, socket)
        XCTAssertEqual([UnclaimedServerStrings.still], rig.said.current)
        await rig.answer(oldest: false, button)
        XCTAssertEqual(1, rig.said.current.count, "said once, not twice")
    }

    /// The owner claims the house while two reads are out: the one asked
    /// before says `false`, the one asked after says `true`. Whichever lands
    /// last, the house is claimed.
    func testAnOlderReadLandingLateDoesNotUndoANewerOne() async {
        let rig = ModelRig(server: server)
        await rig.answer(oldest: false, rig.model.ask(.socket, unreachable: false))
        XCTAssertTrue(rig.model.blocks)

        let before = rig.model.ask(.socket, unreachable: false)
        let after = rig.model.ask(.foreground, unreachable: false)
        await rig.answer(newest: true, after)
        XCTAssertFalse(rig.model.blocks)
        await rig.answer(oldest: false, before)
        XCTAssertFalse(rig.model.blocks, "a false asked before the claim cannot land after the true asked after it")
    }

    /// **The switch inside the round trip**, at the model's own seam: A's
    /// `false` lands after the phone moved to B. It must not block B, and it
    /// must not end B's own `checking…`.
    func testAReadFromBeforeASwitchNeitherBlocksNorTouchesTheNewServer() async {
        let rig = ModelRig(server: server)
        let fromA = rig.model.ask(.socket, unreachable: false)

        rig.active.set(other)
        XCTAssertTrue(rig.model.serverIs(other))
        let fromB = rig.model.ask(.serverPicked, unreachable: false)
        XCTAssertTrue(rig.model.checking)

        await rig.answer(oldest: false, fromA)
        XCTAssertFalse(rig.model.blocks, "A's answer is not about B")
        XCTAssertNil(rig.model.setupComplete)
        XCTAssertTrue(rig.model.checking, "B's read is still out: A's landing does not end it")

        await rig.answer(oldest: true, fromB)
        XCTAssertFalse(rig.model.checking)
        XCTAssertEqual(true, rig.model.setupComplete, "B's own answer lands through the same plumbing")
    }

    /// The picker writes the store; `refresh()`, which calls `serverIs`,
    /// runs a hop later. A read landing in that gap is about A, and the
    /// store is on B already — it is dropped, and so is a read asked in it.
    func testAReadLandingBeforeTheShellCaughtUpWithTheStoreIsDropped() async {
        let rig = ModelRig(server: server)
        let fromA = rig.model.ask(.socket, unreachable: false)
        rig.active.set(other)
        XCTAssertNil(
            rig.model.ask(.socket, unreachable: false),
            "the client would send this to B and file it under A: not asked; the refresh asks B"
        )
        await rig.answer(oldest: false, fromA)
        XCTAssertNil(rig.model.setupComplete, "the store is on B: A's answer is not written anywhere B can see")
        XCTAssertFalse(rig.model.blocks)
    }

    // MARK: - through the shell, against a stubbed server

    /// The whole path: `ShellModel`, its real client and `/api/auth/status`
    /// on the wire, with A's read **held at the server** while the person
    /// switches to B. A says `false`, B says `true`: B is never blocked.
    func testShell_AnUnclaimedAnswerThatLandsAfterASwitchDoesNotBlockTheNewServer() async {
        let park = statusPark(heldHost: "10.0.0.42", held: false, others: true)
        let (shell, store) = shellRig()

        let pending = shell.unclaimed.ask(.socket, unreachable: false)
        XCTAssertNotNil(pending)
        await settleUntil { park.arrived.current }

        switchTo(other, store: store, shell: shell)
        await settleUntil { shell.unclaimed.setupComplete == true }

        park.release.set(true)
        await pending?.value
        XCTAssertFalse(park.ranOut.current, "the answer landed; it did not time out")
        XCTAssertFalse(shell.unclaimed.blocks, "server A's false is not about server B")
        XCTAssertFalse(unclaimedReplaces(shell.screen, blocks: shell.unclaimed.blocks))
        XCTAssertEqual(
            2,
            StubURLProtocol.recorded.filter { $0.url?.path == authStatusPath }.count,
            "one read of each house, and both really went out"
        )
        shell.stop()
    }

    /// The other direction: B is unclaimed and says so; A's late `true`
    /// must not take B's blocker away.
    func testShell_AClaimedAnswerThatLandsAfterASwitchDoesNotUnblockTheNewServer() async {
        let park = statusPark(heldHost: "10.0.0.42", held: true, others: false)
        let (shell, store) = shellRig()

        let pending = shell.unclaimed.ask(.socket, unreachable: false)
        await settleUntil { park.arrived.current }

        switchTo(other, store: store, shell: shell)
        await settleUntil { shell.unclaimed.blocks }

        park.release.set(true)
        await pending?.value
        XCTAssertFalse(park.ranOut.current, "the answer landed; it did not time out")
        XCTAssertTrue(shell.unclaimed.blocks, "server A's true does not claim server B")
        XCTAssertTrue(unclaimedReplaces(shell.screen, blocks: shell.unclaimed.blocks))
        shell.stop()
    }

    /// Android's keys, as the shell's hooks: the socket coming or going, a
    /// server picked, and a **real** return asks; an unchanged refresh and
    /// the `.inactive` step on the way in do not.
    func testShell_TheReadIsAskedWhereAndroidAsksIt() async {
        StubURLProtocol.dispatch { _ in .json(#"{"setup_complete": false}"#) }
        let (shell, store) = shellRig()
        XCTAssertEqual(0, shell.unclaimed.readsSent, "building the shell asks nothing")

        shell.socketChanged(connected: true)
        XCTAssertEqual(1, shell.unclaimed.readsSent, "the socket came up (and this is the launch read)")
        await settleUntil { !shell.unclaimed.checking }
        XCTAssertTrue(shell.unclaimed.blocks)

        shell.socketChanged(connected: false)
        XCTAssertEqual(2, shell.unclaimed.readsSent, "the socket went down")
        await settleUntil { !shell.unclaimed.checking }

        shell.refresh()
        XCTAssertEqual(2, shell.unclaimed.readsSent, "the same server: the countdown's refresh asks nothing")

        shell.scenePhaseChanged(to: .inactive)
        shell.scenePhaseChanged(to: .active)
        XCTAssertEqual(2, shell.unclaimed.readsSent, "Control Centre is not a return")

        shell.scenePhaseChanged(to: .background)
        shell.scenePhaseChanged(to: .inactive)
        shell.scenePhaseChanged(to: .active)
        XCTAssertEqual(3, shell.unclaimed.readsSent, "back on screen: the owner may have just claimed it")
        await settleUntil { !shell.unclaimed.checking }

        switchTo(other, store: store, shell: shell)
        XCTAssertEqual(4, shell.unclaimed.readsSent, "a new server is asked")
        XCTAssertNil(shell.unclaimed.setupComplete, "and starts from not known, as remember(serverUrl) does")
        await settleUntil { !shell.unclaimed.checking }
        XCTAssertTrue(shell.unclaimed.blocks)
        shell.stop()
    }

    /// `Check again` through the shell: the toast is the shell's own.
    func testShell_CheckAgainOnAStillUnclaimedHouseSaysSo() async {
        StubURLProtocol.dispatch { _ in .json(#"{"setup_complete": false}"#) }
        let (shell, _) = shellRig()
        await shell.unclaimed.ask(.socket, unreachable: false)?.value
        XCTAssertNil(shell.toast, "the socket's read is quiet")

        await shell.unclaimed.checkAgain(unreachable: shell.unreachable)?.value
        XCTAssertEqual(UnclaimedServerStrings.still, shell.toast?.text)
        shell.stop()
    }

    // MARK: - rigs

    /// A model whose reads the test answers by hand, and whose "store" is a
    /// value the test moves.
    private struct ModelRig {
        let reads: ScriptedReads
        let active: Shared<String>
        let said: Shared<[String]>
        let model: UnclaimedServerModel

        @MainActor
        init(server: String) {
            let reads = ScriptedReads()
            let active = Shared(server)
            let said = Shared<[String]>([])
            let model = UnclaimedServerModel(
                server: server,
                read: { await reads.read() },
                activeServer: { active.current }
            )
            model.say = { text in said.mutate { $0.append(text) } }
            self.reads = reads
            self.active = active
            self.said = said
            self.model = model
        }

        /// Answer the oldest read still out, then wait for its task.
        @MainActor
        func answer(oldest value: Bool?, _ task: Task<Void, Never>?) async {
            await waitForReads()
            XCTAssertTrue(reads.answerOldest(value), "there was a read to answer")
            await task?.value
        }

        /// Answer the newest read still out, then wait for its task.
        @MainActor
        func answer(newest value: Bool?, _ task: Task<Void, Never>?) async {
            await waitForReads()
            XCTAssertTrue(reads.answerNewest(value), "there was a read to answer")
            await task?.value
        }

        /// Every task started so far has reached the read. Bounded.
        @MainActor
        private func waitForReads() async {
            for _ in 0..<settleYieldLimit {
                if reads.started >= model.readsSent { return }
                await Task.yield()
            }
            XCTFail("a read never reached the server in \(settleYieldLimit) yields")
        }
    }

    private func shellRig() -> (ShellModel, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        // A known-server row, so switching back would be allowed; and B
        // trusted, because `setServerURL` refuses an untrusted address.
        store.upsertKnownServer(url: server, name: nil)
        store.trustServer(other)
        store.upsertKnownServer(url: other, name: nil)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let shell = ShellModel(
            container: container,
            probe: { true },
            clock: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in try await Task.sleep(for: .seconds(3600)) }
        )
        return (shell, store)
    }

    /// The switch the picker makes: the store first, then the refresh.
    private func switchTo(_ url: String, store: InMemoryShellStore, shell: ShellModel) {
        XCTAssertTrue(store.setServerURL(url))
        store.setToken("household-xyz", for: url)
        shell.refresh()
    }

    /// `/api/auth/status` held open at one host until released, answering
    /// `held`; every other host answers `others` at once.
    private func statusPark(heldHost: String, held: Bool, others: Bool) -> ParkedRequest {
        let park = ParkedRequest()
        let heldBody = #"{"setup_complete": \#(held), "authenticated": false}"#
        let othersBody = #"{"setup_complete": \#(others), "authenticated": false}"#
        StubURLProtocol.dispatch { request in
            guard request.url?.path == authStatusPath else { return .json("{}") }
            guard request.url?.host == heldHost else { return .json(othersBody) }
            park.arrived.set(true)
            for step in 0..<400 {
                if park.release.current { return .json(heldBody) }
                if step == 399 { park.ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(heldBody)
        }
        return park
    }

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

/// Status reads the test answers by hand, oldest or newest first.
///
/// `@unchecked Sendable` with the reason spelt out: the two stored
/// properties are touched only inside `lock`, which is never held across a
/// suspension.
final class ScriptedReads: @unchecked Sendable {
    private let lock = NSLock()
    private var waiting: [CheckedContinuation<Bool?, Never>] = []
    private var count = 0

    /// How many reads have reached this "server" in all.
    var started: Int { lock.withLock { count } }

    func read() async -> Bool? {
        await withCheckedContinuation { continuation in
            lock.withLock {
                waiting.append(continuation)
                count += 1
            }
        }
    }

    func answerOldest(_ value: Bool?) -> Bool {
        let next: CheckedContinuation<Bool?, Never>? = lock.withLock {
            waiting.isEmpty ? nil : waiting.removeFirst()
        }
        next?.resume(returning: value)
        return next != nil
    }

    func answerNewest(_ value: Bool?) -> Bool {
        let next: CheckedContinuation<Bool?, Never>? = lock.withLock {
            waiting.popLast()
        }
        next?.resume(returning: value)
        return next != nil
    }
}
