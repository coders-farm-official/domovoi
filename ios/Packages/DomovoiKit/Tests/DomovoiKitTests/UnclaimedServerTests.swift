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

    /// Train I014's audit finding, answered the way the app answered it
    /// twice before: VoiceOver hears `the server at <address>`, in the same
    /// words as the forget card and the phone-source switch.
    func testTheAddressReadsAsTheServerAtItAndNotAsBareDigits() {
        XCTAssertEqual("the server at 127.0.0.1:6606", UnclaimedServerStrings.addressLabel("127.0.0.1:6606"))
        XCTAssertEqual(
            forgetServerAddressLabel("192.168.1.20:6369"),
            UnclaimedServerStrings.addressLabel("192.168.1.20:6369"),
            "one wording for an address on its own, app-wide"
        )
        XCTAssertEqual(
            PhoneMediaStrings.serverSideLabel("192.168.1.20:6369"),
            UnclaimedServerStrings.addressLabel("192.168.1.20:6369")
        )
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

    /// The read is **sent to** the address it will be filed under. A store
    /// that moves A → B → A before the shell catches up would otherwise send
    /// A's read to B, and B's answer would pass every check on landing.
    func testTheReadIsHandedTheAddressItWasAskedOf() async {
        let rig = ModelRig(server: server)
        let fromA = rig.model.ask(.socket, unreachable: false)
        rig.active.set(other)
        rig.active.set(server)
        await rig.answer(oldest: false, fromA)
        XCTAssertEqual([server], rig.reads.sentTo, "A's read went to A, wherever the store was meanwhile")
        XCTAssertTrue(rig.model.blocks)
    }

    // MARK: - through the shell, against a stubbed server

    /// The real read goes to the address it is handed, **not** to the
    /// client's current server: the client below is on B, and the read of A
    /// reaches A and brings back A's answer.
    func testTheRealReadGoesToTheAddressItIsHandedNotTheClientsCurrentOne() async {
        StubURLProtocol.dispatch { request in
            guard request.url?.path == authStatusPath else { return .json("{}") }
            let isA = request.url?.host == "10.0.0.42"
            return .json(#"{"setup_complete": \#(!isA)}"#)
        }
        let (shell, store) = shellRig()
        XCTAssertTrue(store.setServerURL(other))
        guard let answer = await readStatus(shell.container.client, server: server, "the real read of A") else {
            shell.stop()
            return
        }
        XCTAssertEqual(false, answer, "A's own answer")
        let hosts = StubURLProtocol.recorded.filter { $0.url?.path == authStatusPath }.map { $0.url?.host ?? "" }
        XCTAssertEqual(["10.0.0.42"], hosts, "sent to A, not to the client's current server B")
        shell.stop()
    }

    /// A refusal is not an answer, **whatever its body says**, and neither is
    /// a page that is not JSON — both through the real client, and neither
    /// blocks the house. The refusal does not raise pairing either: the read
    /// is untracked.
    func testShell_ARefusalOrAPageThatIsNotJSONIsNotAnAnswer() async {
        let html = Shared(false)
        StubURLProtocol.dispatch { request in
            guard request.url?.path == authStatusPath else { return .json("{}") }
            if html.current {
                return StubURLProtocol.Answer(
                    status: 200,
                    headers: ["Content-Type": "text/html"],
                    body: Data("<html><body>router login</body></html>".utf8)
                )
            }
            return .json(#"{"setup_complete": false, "detail": "no"}"#, status: 401)
        }
        let (shell, _) = shellRig()

        guard let refused = await readStatus(shell.container.client, server: server, "the read refused 401") else {
            shell.stop()
            return
        }
        XCTAssertNil(refused, "a 401 carrying setup_complete: false is a refusal, not an answer")
        await land(shell.unclaimed.ask(.socket, unreachable: false), "the shell's read refused 401")
        XCTAssertNil(shell.unclaimed.setupComplete)
        XCTAssertFalse(shell.unclaimed.blocks, "a refusal never blocks the house")
        XCTAssertFalse(shell.container.client.pairingRequired, "an untracked read raises no pairing screen")

        html.set(true)
        guard let page = await readStatus(shell.container.client, server: server, "the read answered HTML") else {
            shell.stop()
            return
        }
        XCTAssertNil(page, "a 200 that is not JSON is not an answer")
        await land(shell.unclaimed.ask(.socket, unreachable: false), "the shell's read answered HTML")
        XCTAssertNil(shell.unclaimed.setupComplete)
        XCTAssertFalse(shell.unclaimed.blocks, "a page that is not Domovoi never blocks the house")
        XCTAssertEqual(
            4,
            StubURLProtocol.recorded.filter { $0.url?.path == authStatusPath }.count,
            "all four reads really went out"
        )
        shell.stop()
    }

    /// **The back-in-reach guard.** The reachability loop publishes a
    /// verdict on every probe while the socket is down
    /// (``askWhileOnScreen(reach:probe:clock:wait:publish:)``), so "in reach"
    /// arrives every few seconds for an unpaired phone beside a healthy
    /// server. Only the change from out of reach to in reach may ask;
    /// without that, the status would be read every probe, for ever.
    func testShell_OnlyComingBackIntoReachAsksNotEveryInReachVerdict() async {
        StubURLProtocol.dispatch { _ in .json(#"{"setup_complete": false}"#) }
        let (shell, _) = shellRig()
        let before = shell.unclaimed.readsSent

        shell.verdictArrived(false)
        shell.verdictArrived(false)
        shell.verdictArrived(false)
        XCTAssertEqual(before, shell.unclaimed.readsSent, "in reach, and in reach again: nothing new to ask")

        shell.verdictArrived(true)
        XCTAssertEqual(before, shell.unclaimed.readsSent, "out of reach: never asked")

        shell.verdictArrived(false)
        XCTAssertEqual(before + 1, shell.unclaimed.readsSent, "back in reach: asked exactly once")

        shell.verdictArrived(false)
        XCTAssertEqual(before + 1, shell.unclaimed.readsSent, "and the next probe's verdict asks nothing")
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }
        shell.stop()
    }

    /// The whole path: `ShellModel`, its real client and `/api/auth/status`
    /// on the wire, with A's read **held at the server** while the person
    /// switches to B. A says `false`, B says `true`: B is never blocked.
    ///
    /// **Both houses are held, and released in turn.** A held request holds
    /// the stub session's loading thread (`startLoading` is synchronous), so
    /// B's read cannot even start until A is let go — measured on the first
    /// run of this case, which waited for B before releasing A and saw one
    /// request on the wire. So A lands first, into a shell that has already
    /// switched, and B lands after; the assertion between the two is the one
    /// that can fail.
    func testShell_AnUnclaimedAnswerThatLandsAfterASwitchDoesNotBlockTheNewServer() async {
        let parks = statusParks(answerA: false, answerB: true)
        let (shell, store) = shellRig()

        let pending = shell.unclaimed.ask(.socket, unreachable: false)
        XCTAssertNotNil(pending)
        await settleUntil("server A's read to reach the stub") { parks.a.arrived.current }

        switchTo(other, store: store, shell: shell)
        XCTAssertTrue(shell.unclaimed.checking, "B's own read is out")

        parks.a.release.set(true)
        await land(pending, "server A's held read")
        XCTAssertFalse(parks.a.ranOut.current, "A's answer landed; it did not time out")
        XCTAssertNil(shell.unclaimed.setupComplete, "server A's false is not written under server B")
        XCTAssertFalse(shell.unclaimed.blocks, "server A's false is not about server B")
        XCTAssertTrue(shell.unclaimed.checking, "and it did not end B's checking…")

        await settleUntil("server B's read to reach the stub") { parks.b.arrived.current }
        parks.b.release.set(true)
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }
        XCTAssertFalse(parks.b.ranOut.current, "B's answer landed; it did not time out")
        XCTAssertEqual(true, shell.unclaimed.setupComplete, "B's own answer lands through the same plumbing")
        XCTAssertFalse(unclaimedReplaces(shell.screen, blocks: shell.unclaimed.blocks))
        XCTAssertEqual(
            2,
            StubURLProtocol.recorded.filter { $0.url?.path == authStatusPath }.count,
            "one read of each house, and both really went out"
        )
        shell.stop()
    }

    /// The other direction: B is unclaimed and says so; A's late `true`
    /// must not claim B, before B answers or after.
    func testShell_AClaimedAnswerThatLandsAfterASwitchDoesNotUnblockTheNewServer() async {
        let parks = statusParks(answerA: true, answerB: false)
        let (shell, store) = shellRig()

        let pending = shell.unclaimed.ask(.socket, unreachable: false)
        await settleUntil("server A's read to reach the stub") { parks.a.arrived.current }

        switchTo(other, store: store, shell: shell)

        parks.a.release.set(true)
        await land(pending, "server A's held read")
        XCTAssertFalse(parks.a.ranOut.current, "A's answer landed; it did not time out")
        XCTAssertNil(shell.unclaimed.setupComplete, "server A's true does not claim server B")

        await settleUntil("server B's read to reach the stub") { parks.b.arrived.current }
        parks.b.release.set(true)
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }
        XCTAssertFalse(parks.b.ranOut.current, "B's answer landed; it did not time out")
        XCTAssertTrue(shell.unclaimed.blocks, "B said false: B is blocked")
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
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }
        XCTAssertTrue(shell.unclaimed.blocks)

        shell.socketChanged(connected: false)
        XCTAssertEqual(2, shell.unclaimed.readsSent, "the socket went down")
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }

        shell.refresh()
        XCTAssertEqual(2, shell.unclaimed.readsSent, "the same server: the countdown's refresh asks nothing")

        shell.scenePhaseChanged(to: .inactive)
        shell.scenePhaseChanged(to: .active)
        XCTAssertEqual(2, shell.unclaimed.readsSent, "Control Centre is not a return")

        shell.scenePhaseChanged(to: .background)
        shell.scenePhaseChanged(to: .inactive)
        shell.scenePhaseChanged(to: .active)
        XCTAssertEqual(3, shell.unclaimed.readsSent, "back on screen: the owner may have just claimed it")
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }

        switchTo(other, store: store, shell: shell)
        XCTAssertEqual(4, shell.unclaimed.readsSent, "a new server is asked")
        XCTAssertNil(shell.unclaimed.setupComplete, "and starts from not known, as remember(serverUrl) does")
        await settleUntil("every status read out to have landed") { !shell.unclaimed.checking }
        XCTAssertTrue(shell.unclaimed.blocks)
        shell.stop()
    }

    /// `Check again` through the shell: the toast is the shell's own.
    func testShell_CheckAgainOnAStillUnclaimedHouseSaysSo() async {
        StubURLProtocol.dispatch { _ in .json(#"{"setup_complete": false}"#) }
        let (shell, _) = shellRig()
        await land(shell.unclaimed.ask(.socket, unreachable: false), "the socket's read")
        XCTAssertNil(shell.toast, "the socket's read is quiet")

        await land(shell.unclaimed.checkAgain(unreachable: shell.unreachable), "Check again's read")
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
                read: { asked in await reads.read(asked) },
                activeServer: { active.current }
            )
            model.say = { text in said.mutate { $0.append(text) } }
            self.reads = reads
            self.active = active
            self.said = said
            self.model = model
        }

        /// Answer the oldest read still out, then wait -- **bounded** -- for
        /// `task` to land.
        ///
        /// Bounded because the test picks which continuation to resume and
        /// `task` is the read it expects that to be: if they are not the
        /// same read, `task` never resumes, and an unbounded `await
        /// task.value` parks the whole suite (a pkgtest run on 2026-11-10
        /// sat for 20 minutes right after `TrustBookTests`, with this class
        /// next). A miss now fails the case, says which wait it was, and
        /// resumes every read still out with nil so nothing is left parked.
        @MainActor
        func answer(
            oldest value: Bool?,
            _ task: Task<Void, Never>?,
            file: StaticString = #filePath,
            line: UInt = #line
        ) async {
            guard await waitForReads(file: file, line: line) else { return }
            XCTAssertTrue(reads.answerOldest(value), "there was a read to answer", file: file, line: line)
            await land(task, "the read answered oldest-first (\(String(describing: value)))", file: file, line: line)
        }

        /// Answer the newest read still out, then wait -- bounded -- for
        /// `task` to land.
        @MainActor
        func answer(
            newest value: Bool?,
            _ task: Task<Void, Never>?,
            file: StaticString = #filePath,
            line: UInt = #line
        ) async {
            guard await waitForReads(file: file, line: line) else { return }
            XCTAssertTrue(reads.answerNewest(value), "there was a read to answer", file: file, line: line)
            await land(task, "the read answered newest-first (\(String(describing: value)))", file: file, line: line)
        }

        @MainActor
        private func land(_ task: Task<Void, Never>?, _ what: String, file: StaticString, line: UInt) async {
            let landed = await finishes(task, within: unclaimedWaitSeconds, what, file: file, line: line)
            if !landed {
                // Unstick whatever is still parked, so the case ends rather
                // than leaving a task suspended on a continuation for ever.
                reads.drain()
            }
        }

        /// Every task started so far has reached the scripted server.
        /// Bounded by the clock, not by a count of yields.
        @MainActor
        private func waitForReads(file: StaticString, line: UInt) async -> Bool {
            let reached = await settles(within: unclaimedWaitSeconds, file: file, line: line, {
                "every read the model sent (\(model.readsSent)) to reach the scripted server "
                    + "(\(reads.started) did)"
            }) { reads.started >= model.readsSent }
            if !reached { reads.drain() }
            return reached
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

    /// `/api/auth/status` held open at each house until that house's park
    /// is released: A is `10.0.0.42`, B is `10.0.0.77`. Bounded under the
    /// session's 1 s timeout, as `parkRequest` is.
    private func statusParks(answerA: Bool, answerB: Bool) -> (a: ParkedRequest, b: ParkedRequest) {
        let a = ParkedRequest()
        let b = ParkedRequest()
        StubURLProtocol.dispatch { request in
            guard request.url?.path == authStatusPath else { return .json("{}") }
            let isA = request.url?.host == "10.0.0.42"
            let park = isA ? a : b
            let body = #"{"setup_complete": \#(isA ? answerA : answerB), "authenticated": false}"#
            park.arrived.set(true)
            for step in 0..<400 {
                if park.release.current { return .json(body) }
                if step == 399 { park.ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(body)
        }
        return (a, b)
    }

    /// Bounded by the clock: a condition that never comes fails the case
    /// naming `what`, rather than spinning a count of yields that a loaded
    /// Mac can use up before a 1 s round trip has even answered.
    private func settleUntil(
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line,
        _ done: @MainActor () -> Bool
    ) async {
        _ = await settles(within: unclaimedWaitSeconds, file: file, line: line, { what }, done)
    }

    /// `task` to its end, bounded; false (and a failure naming `what`) when
    /// it did not get there.
    @discardableResult
    private func land(
        _ task: Task<Void, Never>?,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async -> Bool {
        await finishes(task, within: unclaimedWaitSeconds, what, file: file, line: line)
    }

    /// One real status read through a real client, bounded. `.some(nil)` is
    /// "the read answered nil"; `nil` is "it never came back" (and failed).
    private func readStatus(
        _ client: DomovoiClient,
        server: String,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async -> Bool?? {
        let answer = Shared<Bool??>(nil)
        let task = Task { @MainActor in
            answer.set(.some(await UnclaimedServerModel.read(using: client, server: server)))
        }
        guard await finishes(task, within: unclaimedWaitSeconds, what, file: file, line: line) else {
            task.cancel()
            return nil
        }
        return answer.current
    }
}

/// How long any one wait in `UnclaimedServerTests` may take before it fails
/// the case. Every network leg in these rigs is under the stub session's 1 s
/// timeout and every park is bounded at 0.8 s, so 10 s is only ever reached
/// by a wait that is never going to end.
let unclaimedWaitSeconds: Double = 10

/// Wait for `task` to finish, **by the clock**: true when it did, false and
/// a failure naming `what` when `seconds` passed first. The task itself is
/// left alone -- cancelling it would not resume a continuation it is parked
/// on -- and the caller decides how to unstick it.
@MainActor
func finishes(
    _ task: Task<Void, Never>?,
    within seconds: Double,
    _ what: String,
    file: StaticString = #filePath,
    line: UInt = #line
) async -> Bool {
    guard let task else { return true }
    let done = Shared(false)
    // A watcher, so the test never awaits `task.value` itself: an `await` on
    // a task that never ends is the one wait with no way out.
    let watcher = Task { @MainActor in
        await task.value
        done.set(true)
    }
    let deadline = Date().addingTimeInterval(seconds)
    while !done.current {
        if Date() >= deadline {
            watcher.cancel()
            XCTFail("waited \(seconds) s for \(what) to finish, and it never did", file: file, line: line)
            return false
        }
        try? await Task.sleep(for: .milliseconds(5))
    }
    return true
}

/// Poll `done` until it holds, **by the clock**: true when it did, false and
/// a failure naming what was awaited when `seconds` passed first.
@MainActor
func settles(
    within seconds: Double,
    file: StaticString = #filePath,
    line: UInt = #line,
    _ what: @MainActor () -> String,
    _ done: @MainActor () -> Bool
) async -> Bool {
    let deadline = Date().addingTimeInterval(seconds)
    while !done() {
        if Date() >= deadline {
            XCTFail("waited \(seconds) s for \(what()), and it never came", file: file, line: line)
            return false
        }
        try? await Task.sleep(for: .milliseconds(5))
    }
    return true
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
    private var addresses: [String] = []

    /// How many reads have reached this "server" in all.
    var started: Int { lock.withLock { count } }

    /// The address each read was sent to, in order.
    var sentTo: [String] { lock.withLock { addresses } }

    func read(_ address: String) async -> Bool? {
        await withCheckedContinuation { continuation in
            lock.withLock {
                waiting.append(continuation)
                addresses.append(address)
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

    /// Resume every read still out with nil: how a failed case leaves
    /// nothing suspended behind it.
    func drain() {
        let all: [CheckedContinuation<Bool?, Never>] = lock.withLock {
            defer { waiting.removeAll() }
            return waiting
        }
        for continuation in all { continuation.resume(returning: nil) }
    }

    func answerNewest(_ value: Bool?) -> Bool {
        let next: CheckedContinuation<Bool?, Never>? = lock.withLock {
            waiting.popLast()
        }
        next?.resume(returning: value)
        return next != nil
    }
}
