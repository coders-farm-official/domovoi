import XCTest
@testable import DomovoiKit

/// Opening the app away from home (owner report, 2026-11-19): the workspace
/// was drawn for the whole grace period while its pages could only spin, then
/// swapped for the phone's own media with nothing that said why. Now there
/// is a plain Connecting screen until the server first answers, the phone's
/// own media with a "Looking for" line once ``lookingAfterMs`` has gone by,
/// and a cover over the workspace when a server that did answer goes quiet.
/// `ShellModeTest.kt` and `ReachabilityTest.kt` pin the same on Android.
@MainActor
final class ShellAwayTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the pure rules

    func testBeforeTheFirstAnswerItIsTheConnectingScreen() {
        XCTAssertEqual(
            .connecting,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, answeredOnce: false)
        )
        XCTAssertEqual(
            .local,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, answeredOnce: false, looking: true)
        )
        XCTAssertEqual(
            .workspace,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, answeredOnce: true)
        )
        XCTAssertEqual(
            .connecting,
            shellScreen(serverURL: server, unreachable: false, pairingRequired: false, answeredOnce: false)
        )
        XCTAssertEqual(
            .localMedia(.outOfReach),
            shellScreen(serverURL: server, unreachable: false, pairingRequired: false, answeredOnce: false, looking: true)
        )
    }

    func testTheOldRulesStillComeFirst() {
        XCTAssertEqual(.local, shellMode(serverURL: "", unreachable: false, pairingRequired: false, answeredOnce: false))
        XCTAssertEqual(
            .local,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, preferLocal: true, answeredOnce: false)
        )
        XCTAssertEqual(
            .local,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, identityChanged: true, answeredOnce: false)
        )
        // A pairing refusal or a token card is itself an answer.
        XCTAssertEqual(.workspace, shellMode(serverURL: server, unreachable: false, pairingRequired: true, answeredOnce: false))
        XCTAssertEqual(
            .workspace,
            shellMode(serverURL: server, unreachable: false, pairingRequired: false, tokenTrouble: true, answeredOnce: false)
        )
        XCTAssertEqual(.local, shellMode(serverURL: server, unreachable: true, pairingRequired: false, answeredOnce: false))
    }

    func testLookingCoversTheWorkspaceOnlyAfterAnAnswer() {
        XCTAssertTrue(coversWorkspace(mode: .workspace, answeredOnce: true, looking: true, unreachable: false))
        XCTAssertFalse(coversWorkspace(mode: .workspace, answeredOnce: true, looking: false, unreachable: false))
        XCTAssertFalse(coversWorkspace(mode: .local, answeredOnce: true, looking: true, unreachable: true))
        XCTAssertFalse(coversWorkspace(mode: .local, answeredOnce: false, looking: true, unreachable: false))
        XCTAssertFalse(coversWorkspace(mode: .connecting, answeredOnce: false, looking: false, unreachable: false))
    }

    func testLookingIsItsOwnChoiceAndCannotBePicked() {
        let looking = serverChoice(
            serverURL: server, label: "Hearth", unreachable: false, notOurs: false, pairingRequired: false, looking: true
        )
        XCTAssertEqual(.looking(label: "Hearth"), looking)
        XCTAssertFalse(looking.isEnabled)
        XCTAssertEqual("looking for it…", looking.reason)
        XCTAssertEqual("Hearth", looking.title)
        XCTAssertEqual(
            .unreachable(label: "Hearth"),
            serverChoice(serverURL: server, label: "Hearth", unreachable: true, notOurs: false, pairingRequired: false, looking: true)
        )
        XCTAssertEqual(
            .notOurs(label: "Hearth"),
            serverChoice(serverURL: server, label: "Hearth", unreachable: false, notOurs: true, pairingRequired: false, looking: true)
        )
    }

    func testTheLocalShellSaysWhereItIs() {
        XCTAssertEqual("Away from home", ServerChoice.unreachable(label: "Hearth").localTitle)
        XCTAssertEqual("Looking for home…", ServerChoice.looking(label: "Hearth").localTitle)
        XCTAssertEqual("On this phone", ServerChoice.noServer.localTitle)
        XCTAssertEqual("On this phone", ServerChoice.available(label: "Hearth").localTitle)
        XCTAssertEqual("On this phone", ServerChoice.coreDown(label: "Hearth").localTitle)
        XCTAssertEqual("Away", ServerChoice.unreachable(label: "Hearth").localCrumb)
        XCTAssertEqual("This phone", ServerChoice.noServer.localCrumb)
        XCTAssertNil(ServerChoice.available(label: "Hearth").awayLine)
        XCTAssertNil(ServerChoice.noServer.awayLine)
        XCTAssertNil(ServerChoice.notOurs(label: "Hearth").awayLine)
    }

    // MARK: - the shell

    /// A shell over a server that answers or doesn't, whose looking timer
    /// goes off only when the test says.
    private func shell(answers: Bool, answeredAtStart: Bool = false, lookingGoes: Shared<Bool>) -> ShellModel {
        let store = InMemoryShellStore(serverURL: server, preferLocal: false, token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1),
            log: { _, _ in }
        )
        let model = ShellModel(
            container: container,
            probe: { answers },
            clock: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in try await Task.sleep(for: .seconds(3600)) },
            lookingWait: { _ in
                while !lookingGoes.current { try await Task.sleep(for: .milliseconds(1)) }
            },
            serverAnsweredAtStart: answeredAtStart
        )
        // The app's `AppShellView` reports the scene phase at launch
        // (`initial: true`), which is what starts the asking.
        model.scenePhaseChanged(to: .active)
        return model
    }

    /// Bounded, so a wiring that never lands fails rather than hangs.
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<5_000 {
            if condition() { return true }
            await Task.yield()
            try? await Task.sleep(for: .milliseconds(1))
        }
        return condition()
    }

    func testOpeningTheAppShowsConnectingThenTheWorkspaceOnTheFirstAnswer() async {
        let model = shell(answers: true, lookingGoes: Shared(false))
        XCTAssertEqual(.connecting, model.screen, "no home page before the server has answered")
        XCTAssertNil(model.chrome.pill, "nothing to tap on the Connecting screen")
        let landed = await eventually { model.screen == .workspace }
        XCTAssertTrue(landed, "the first answer brings the workspace; it is \(model.screen)")
        XCTAssertTrue(model.answeredOnce)
        model.stop()
    }

    func testStillUnansweredItIsThePhonesOwnMediaWithALookingLine() async {
        let goes = Shared(false)
        let model = shell(answers: false, lookingGoes: goes)
        XCTAssertEqual(.connecting, model.screen)
        goes.set(true)
        let moved = await eventually { model.screen == .localMedia(.outOfReach) }
        XCTAssertTrue(moved, "it is \(model.screen)")
        XCTAssertEqual(.looking(label: "10.0.0.42:6369"), model.serverChoice)
        XCTAssertTrue((model.chrome.banner ?? "").hasPrefix("Looking for 10.0.0.42:6369."), model.chrome.banner ?? "")
        model.stop()
    }

    func testAServerThatAnsweredAndWentQuietIsCoveredNotSpun() async {
        let goes = Shared(true)
        let model = shell(answers: false, answeredAtStart: true, lookingGoes: goes)
        XCTAssertEqual(.workspace, model.screen)
        let covered = await eventually { model.coversWorkspace }
        XCTAssertTrue(covered, "the pages that can only spin are covered")
        XCTAssertEqual(.workspace, model.screen, "kept underneath, not swapped, until the grace says")
        model.stop()
    }

    func testAnAnswerForAnotherServerIsNoEvidence() {
        let model = shell(answers: false, lookingGoes: Shared(false))
        model.reachEventArrived(.answered(server: "http://10.0.0.99:6369"))
        XCTAssertEqual(.connecting, model.screen)
        model.reachEventArrived(.answered(server: model.inputs.serverURL))
        XCTAssertEqual(.workspace, model.screen)
        model.stop()
    }
}
