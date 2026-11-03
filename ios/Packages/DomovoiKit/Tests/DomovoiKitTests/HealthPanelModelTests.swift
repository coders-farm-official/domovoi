import XCTest
@testable import DomovoiKit

/// Settings → Health's data layer against a server that is not a server: the
/// six reads, which answer lands under which, the two guard requests folded
/// into one value, which push re-reads what, the thirty-second clock, and a
/// slow guard read that lands after the house changed.
///
/// Android has **no test of any of this**: `HealthPanel` is a composable, so
/// the Kotlin's `rememberApi` wiring is specified by nothing at all.
@MainActor
final class HealthPanelModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// Every `wait` the tick loop asks for; it parks until the task is
    /// cancelled, so a test can say "thirty seconds was asked for and nothing
    /// else happened".
    private final class Waits: Sendable {
        let requested = Shared<[Int64]>([])

        var sleeper: @Sendable (Int64) async throws -> Void {
            { [requested] ms in
                requested.mutate { $0.append(ms) }
                try await Task.sleep(for: .seconds(3_600))
            }
        }
    }

    private func rig(
        serverURL: String? = nil,
        token: String? = "household-abc",
        waits: Waits = Waits(),
        wall: Shared<Int64> = Shared(1_762_000_000_000)
    ) -> (HealthPanelModel, AppContainer, InMemoryShellStore, Waits) {
        let store = InMemoryShellStore(serverURL: serverURL ?? server, token: token)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = HealthPanelModel(
            in: container,
            wallClock: { wall.current },
            wait: waits.sleeper
        )
        return (model, container, store, waits)
    }

    /// A house whose every body names its **own** path, so an answer under
    /// the wrong read is a wrong name rather than a decode failure that any
    /// crossed wire would produce.
    private func answerEveryPath(
        label: String = "a",
        incidentsStatus: Int = 200,
        seen: Shared<[String]>? = nil
    ) {
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            seen?.mutate { $0.append(path) }
            switch path {
            case "/api/config":
                return .json(#"{"bot_name":"config-\#(label)","home_problems_visibility":"everyone"}"#)
            case "/api/health":
                return .json(
                    #"{"status":"health-\#(label)","db_reachable":true,"domovoi_reachable":true,"stt":"ok"}"#
                )
            case "/api/satellites":
                return .json(#"[{"room_id":"rooms-\#(label)","status":"online"}]"#)
            case "/api/plugins":
                return .json(#"{"plugins":[{"slug":"plugins-\#(label)","status":"ok","enabled":true}]}"#)
            case "/api/acquisitions":
                return .json(
                    #"{"acquisitions":[{"id":7,"kind":"query","status":"done"}],"core_reachable":true,"can_fulfill_query":true}"#
                )
            case "/api/containers/incidents":
                guard incidentsStatus == 200 else {
                    return .json(#"{"detail":"nope"}"#, status: incidentsStatus)
                }
                return .json("""
                {"incidents":[
                  {"id":41,"class":"tamper","kind":"image","severity":"error",
                   "summary":"incidents-\(label) is running a different image.",
                   "container":"domovoi-searxng","opened_at":"2026-11-02T18:53:00+00:00",
                   "open":true,"freezes":true}],
                 "banner":{"show":true,"tone":"tamper","text":"incidents-\(label)","count":1,
                           "incident_ids":[41],"frozen":true},
                 "frozen":true,"attention":true}
                """)
            case "/api/containers/summary":
                return .json(
                    #"{"attention":true,"tone":"err","text":"Something needs an admin's attention."}"#
                )
            default:
                return .json("{}", status: 404)
            }
        }
    }

    private func allSix(_ model: HealthPanelModel) -> [any Answerable] {
        [model.config, model.health, model.rooms, model.plugins, model.acquisitions, model.guardRead]
    }

    // MARK: - each read lands under itself

    func testEachOfTheSixReadsLandsUnderItself() async {
        answerEveryPath()
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }

        XCTAssertEqual("config-a", model.config.data?.botName)
        XCTAssertEqual("health-a", model.health.data?.status)
        XCTAssertEqual(["rooms-a"], model.rooms.data?.map(\.roomID))
        XCTAssertEqual(["plugins-a"], model.plugins.data?.plugins.map(\.slug))
        XCTAssertEqual(7, model.acquisitions.data?.acquisitions.first?.id)
        XCTAssertEqual(.household, model.guardRead.data?.tier)
        XCTAssertEqual([41], model.guardRead.data?.openIncidents.map(\.id))
        XCTAssertTrue(
            allSix(model).allSatisfy { $0.error == nil },
            "and not one of them failed: \(allSix(model).compactMap(\.error))"
        )
        XCTAssertTrue(allSix(model).allSatisfy { $0.fetches == 1 }, "each asked once")
    }

    /// The paired path makes **one** guard request, not two: the summary is
    /// read only when the list did not parse.
    func testAPairedPhoneNeverAsksForTheOpenSummary() async {
        let seen = Shared<[String]>([])
        answerEveryPath(seen: seen)
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }
        XCTAssertTrue(seen.current.contains("/api/containers/incidents"))
        XCTAssertFalse(
            seen.current.contains("/api/containers/summary"),
            "the list parsed, so the open summary was never asked for: \(seen.current)"
        )
    }

    /// An unpaired phone is refused the list and falls to the summary, and
    /// the screen says so in the guest line.
    func testARefusedPhoneFallsToTheSummaryAndShowsTheGuestLine() async {
        let seen = Shared<[String]>([])
        answerEveryPath(incidentsStatus: 401, seen: seen)
        let (model, _, _, _) = rig(token: nil)
        model.start()
        defer { model.stop() }
        await settleUntil { model.guardRead.data?.tier == .open }
        XCTAssertTrue(seen.current.contains("/api/containers/summary"))
        let panel = model.panel(shared: false)
        guard case .rows(let rows) = panel.body else { return XCTFail("\(panel.body)") }
        XCTAssertEqual(.guest(line: HealthPanelStrings.pairToSee), rows.containersFoot)
        XCTAssertFalse(rows.showsOpenIncidents, "and it lists no incident it may not see")
    }

    /// **Both guard reads are untracked**, which is the one thing about these
    /// two requests that is not obvious (`IncidentEngine.kt:219`): a tracked
    /// 401 sets the client's pairing flag, and the shell routes that to the
    /// pairing screen — throwing an unpaired phone off the very screen whose
    /// job is to say what it cannot see.
    ///
    /// Only the guard read is made here, so nothing else can clear the flag
    /// and make this pass for the wrong reason.
    func testARefusedGuardReadDoesNotRaiseThePairingScreen() async {
        answerEveryPath(incidentsStatus: 401)
        let (model, container, _, _) = rig(token: nil)
        model.guardRead.refresh()
        await settleUntil { model.guardRead.data?.tier == .open }
        XCTAssertFalse(container.client.pairingRequired)
        model.stop()
    }

    // MARK: - the pushes

    func testContainersChangedRereadsTheGuardAndNothingElse() async {
        answerEveryPath()
        let (model, container, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }

        container.stateEventArrived(StateEvent(type: "containers.changed", frame: "{}"))
        await settleUntil { model.guardRead.fetches == 2 }
        XCTAssertEqual(1, model.config.fetches)
        XCTAssertEqual(1, model.health.fetches)
        XCTAssertEqual(1, model.rooms.fetches)
        XCTAssertEqual(1, model.plugins.fetches)
        XCTAssertEqual(1, model.acquisitions.fetches)
    }

    /// The **three** room events this screen listens for, and not Home's six:
    /// `satellites.wifi.changed` must not re-read `/api/satellites`, because
    /// every such read opens an MPD connection per room and every room sends
    /// one a minute.
    func testTheRoomsReadListensForThreeEventsAndNotWifi() async {
        answerEveryPath()
        let (model, container, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }

        for type in healthRoomEvents.sorted() {
            container.stateEventArrived(StateEvent(type: type, frame: "{}"))
        }
        await settleUntil { model.rooms.fetches == 4 }

        container.stateEventArrived(StateEvent(type: "satellites.wifi.changed", frame: "{}"))
        container.stateEventArrived(StateEvent(type: "music.now_playing", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(
            4, model.rooms.fetches,
            "a Wi-Fi push and a song change are Home's events, not this screen's"
        )
        XCTAssertEqual(
            ["satellites.presence.changed", "satellites.display.changed", "satellites.pending.changed"]
                .sorted(),
            healthRoomEvents.sorted()
        )
    }

    func testPluginsAndAcquisitionsEachHaveTheirOwnPush() async {
        answerEveryPath()
        let (model, container, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }

        container.stateEventArrived(StateEvent(type: "plugins.changed", frame: "{}"))
        await settleUntil { model.plugins.fetches == 2 }
        XCTAssertEqual(1, model.acquisitions.fetches)

        container.stateEventArrived(StateEvent(type: "acquisitions.changed", frame: "{}"))
        await settleUntil { model.acquisitions.fetches == 2 }
        XCTAssertEqual(1, model.config.fetches, "and the config is never re-read by a push")
    }

    // MARK: - the clock

    func testTheClockTicksEveryThirtySeconds() async {
        answerEveryPath()
        let wall = Shared<Int64>(1_762_000_000_000)
        let (model, _, _, waits) = rig(wall: wall)
        model.start()
        defer { model.stop() }
        await settleUntil { !waits.requested.current.isEmpty }
        XCTAssertEqual(
            [30_000], waits.requested.current,
            "thirty seconds: nothing on this screen counts down, and relTime moves no faster than a minute"
        )
        XCTAssertEqual(wall.current, model.tick)

        wall.set(wall.current + 5_000)
        XCTAssertEqual(
            wall.current, model.nowMs,
            "a redraw for any other reason still sees a current clock — `maxOf(tick, now())`"
        )
    }

    func testStoppingEndsTheLoopAndLeavesTheRegistry() async {
        answerEveryPath()
        let (model, container, _, _) = rig()
        model.start()
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }
        model.stop()
        XCTAssertFalse(model.onScreen)

        container.stateEventArrived(StateEvent(type: "containers.changed", frame: "{}"))
        await nothingHappens()
        XCTAssertEqual(
            1, model.guardRead.fetches,
            "a screen that is gone does not read: `rememberApi`'s state dies with the composable"
        )
    }

    // MARK: - the panel the view asks for

    func testThePanelIsBuiltFromTheSixReads() async {
        answerEveryPath()
        let (model, _, _, _) = rig()
        model.start()
        defer { model.stop() }
        await settleUntil { self.allSix(model).allSatisfy(\.answered) }

        let panel = model.panel(shared: false)
        guard case .rows(let rows) = panel.body else { return XCTFail("\(panel.body)") }
        XCTAssertEqual(["Server", "Rooms", "Plugins", "Containers"], rows.parts.map(\.label))
        XCTAssertTrue(rows.freeze, "the stub's incident is an open tamper one")
        XCTAssertEqual(1, rows.openCount)
        XCTAssertEqual(
            "incidents-a is running a different image.", rows.openIncidents.first?.summary,
            "the incident on screen came from the incidents route and no other"
        )
        XCTAssertEqual(
            "http://10.0.0.42:6369/#settings/health", panel.dashboardURL
        )

        let tablet = model.panel(shared: true)
        XCTAssertEqual(.summary(count: 1), tablet.body, "the same reads, a shared screen's view of them")
    }

    /// While the config read has not come back, the screen is a spinner and
    /// not a house with no problems.
    func testTheScreenIsLoadingUntilTheVisibilitySettingHasBeenRead() {
        answerEveryPath()
        let (model, _, _, _) = rig()
        XCTAssertEqual(.loading, model.panel(shared: false).body)
        model.stop()
    }

    // MARK: - the read-after-await rule

    /// **[[read-after-await-wrong-key]] in its guard form.** A slow
    /// incidents read of house A that lands after the person switched to
    /// house B must not become B's snapshot, and must not leave A's list
    /// behind as B's `previous` — which is what would make `keepList` keep
    /// house A's incidents on a screen showing house B.
    ///
    /// The switch happens **inside** the round trip, which is the only honest
    /// way to test it.
    func testAGuardReadThatLandsAfterTheHouseChangedIsDropped() async {
        // **`parkRequest` is not used here**, and the reason is the test: its
        // `answering` closure cannot see the request, so both houses would
        // answer with the same body and "house A's incident is not under
        // house B" could not be stated. The park loop below is its loop,
        // bounded the same way and for the same reason — an unbounded spin
        // once left a three-and-a-half-hour `xctest` on the shared Mac.
        let park = ParkedRequest()
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            let host = request.url?.host ?? ""
            guard path == "/api/containers/incidents" else {
                switch path {
                // **Both houses answer `/api/health`**, and they have to: a
                // token-bearing request is held until the server has proved
                // itself on this network, and no verdict is a hold
                // (`IdentityGate.requireAdmitted`). A 404 here means the
                // guard read is never sent at all, which is how this test
                // first failed.
                case "/api/health":
                    return .json(
                        #"{"status":"ok","db_reachable":true,"domovoi_reachable":true,"stt":"ok"}"#
                    )
                case "/api/config":
                    return .json(#"{"bot_name":"either","home_problems_visibility":"everyone"}"#)
                case "/api/containers/summary":
                    return .json(#"{"attention":false,"tone":null,"text":""}"#)
                default:
                    return .json("{}", status: 404)
                }
            }
            guard host == "10.0.0.42" else {
                return .json("""
                {"incidents":[{"id":7,"class":"failure","kind":"stopped","severity":"error",
                 "summary":"house B's own incident","open":true}]}
                """)
            }
            park.arrived.set(true)
            for step in 0..<400 {
                if park.release.current { break }
                if step == 399 { park.ranOut.set(true) }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json("""
            {"incidents":[{"id":41,"class":"tamper","kind":"image","severity":"error",
             "summary":"house A's own incident","open":true,"freezes":true}]}
            """)
        }
        let memory = HealthGuardMemory()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(otherServer)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = HealthPanelModel(in: container, memory: memory)
        model.guardRead.refresh()
        await settle(for: park.arrived, what: "house A's incidents read")

        // The person moves house while that read is still open, and only
        // then does house A's answer arrive. (The release comes before the
        // wait for house B because a parked `URLProtocol` holds the thread it
        // is loading on — `ParkedRequest` says so — so house B's request
        // cannot finish while house A's is still in the dispatcher.)
        XCTAssertTrue(store.setServerURL(otherServer))
        container.serverAddressChanged()
        park.release.set(true)
        await settleUntil(
            { model.guardRead.data?.openIncidents.first?.summary == "house B's own incident" },
            "house B's read"
        )
        await nothingHappens()
        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")

        XCTAssertEqual(
            "house B's own incident", model.guardRead.data?.openIncidents.first?.summary,
            "house A's answer landed after the switch and must not have replaced house B's"
        )
        // The assertion that does not depend on which answer landed first:
        // house A's read never got as far as remembering anything, because
        // it was cancelled when the address changed.
        XCTAssertEqual(
            "house B's own incident", memory.snapshot(for: otherServer)?.openIncidents.first?.summary,
            "and house B's `previous` is its own — house A's list under this key is what keepList would keep"
        )
        XCTAssertNil(
            memory.snapshot(for: server),
            "nothing was remembered for house A either: its read was cancelled before it could"
        )
    }

    /// The other half of the same rule: a snapshot **is** remembered for the
    /// server it was read from, which is what `keepList` needs to work at
    /// all.
    func testASnapshotIsRememberedUnderItsOwnServer() async {
        answerEveryPath()
        let memory = HealthGuardMemory()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = HealthPanelModel(in: container, memory: memory)
        model.guardRead.refresh()
        await settleUntil { model.guardRead.data != nil }
        XCTAssertEqual(.household, memory.snapshot(for: server)?.tier)
        XCTAssertNil(memory.snapshot(for: otherServer))
        model.stop()
    }

    // MARK: - helpers

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Every task that could have started gets its chance to run — for
    /// asserting a **negative**.
    private func nothingHappens() async {
        for _ in 0..<3_000 { await Task.yield() }
    }
}
