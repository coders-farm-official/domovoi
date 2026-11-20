import Foundation
import XCTest
@testable import DomovoiKit

/// A sleeper a test lets through one wake at a time.
///
/// **Cancellation is checked inside the lock, before an allowance is taken**:
/// a cancelled loop must never eat a wake meant for the next one (the trap
/// that cost a train this week), and a loop that was cancelled while it
/// waited throws rather than running once more.
final class FhAllowance: @unchecked Sendable {
    private let lock = NSLock()
    private var left = 0
    private var _asked = 0

    /// How many sleeps have begun.
    var asked: Int { lock.withLock { _asked } }

    func give(_ n: Int = 1) { lock.withLock { left += n } }

    func sleep(_ ms: Int64) async throws {
        lock.withLock { _asked += 1 }
        while true {
            let took: Bool = try lock.withLock {
                if Task.isCancelled { throw CancellationError() }
                guard left > 0 else { return false }
                left -= 1
                return true
            }
            if took { return }
            await Task.yield()
        }
    }
}

/// Friend Houses' gate (``FriendHousesGate``) and its place in the shell —
/// Android's `FhGate`/`FriendHouses` triggers and `FriendHousesNavTest`:
/// hidden everywhere until the server says it is on, in Tools beside Lists,
/// on a shared screen too, with the unread count as its badge; read at start,
/// on a server or token change, on reconnect, on coming back and every
/// minute while open, conflated; never asked while unpaired or a room; and a
/// notification's tap that waits for the verdict.
@MainActor
final class FriendHousesGateTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func everythingLands() async {
        for _ in 0..<2_000 { await Task.yield() }
    }

    private func statusReads(_ fh: FhHouse) -> Int { fh.count("GET /api/friend-houses") }

    // MARK: - the gate on its own

    func testNothingReadsUntilStarted() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        let (container, _) = fhContainer(fh, server: server)
        let allowance = FhAllowance()
        let gate = FriendHousesGate(container: container, sleep: { try await allowance.sleep($0) })
        gate.refresh()
        gate.inputsMayHaveChanged()
        gate.setForeground(true)
        allowance.give(3)
        await everythingLands()
        XCTAssertEqual(0, statusReads(fh), "a gate nobody started asked the server")
        XCTAssertEqual(0, allowance.asked, "a gate nobody started began its minute")
        XCTAssertNil(gate.shownFor(server))
    }

    func testStartReadsAndShowsWithTheUnreadAsTheBadge() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on", unread: 3))
        let (container, _) = fhContainer(fh, server: server)
        let gate = FriendHousesGate(container: container)
        defer { gate.stop() }
        gate.start()
        await settleUntil({ gate.reads == 1 }, "the first read")
        XCTAssertEqual("on", gate.shownFor(server)?.status)
        XCTAssertEqual(3, gate.badge(server))
        XCTAssertNil(gate.shownFor(other), "the verdict is this server's only")
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on", unread: 0))
        gate.refresh()
        await settleUntil({ gate.reads == 2 }, "the second read")
        XCTAssertNotNil(gate.shownFor(server))
        XCTAssertNil(gate.badge(server), "no badge for nothing unread")
    }

    func testOffHidesAndNoAnswerKeepsTheLastVerdict() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("stopped"))
        let (container, _) = fhContainer(fh, server: server)
        let gate = FriendHousesGate(container: container)
        defer { gate.stop() }
        gate.start()
        await settleUntil({ gate.reads == 1 }, "the first read")
        XCTAssertEqual("stopped", gate.shownFor(server)?.status)
        fh.answer("GET", "/api/friend-houses", #"{"detail":"core unreachable"}"#, status: 502)
        gate.refresh()
        await settleUntil({ gate.reads == 2 }, "the 502")
        XCTAssertEqual("stopped", gate.shownFor(server)?.status, "a 5xx keeps the last verdict")
        fh.answer("GET", "/api/friend-houses", #"{"detail":"wait"}"#, status: 429)
        gate.refresh()
        await settleUntil({ gate.reads == 3 }, "the 429")
        XCTAssertNotNil(gate.shownFor(server), "a 429 keeps the last verdict")
        fh.answer("GET", "/api/friend-houses", FhHouse.status("off", unread: 9))
        gate.refresh()
        await settleUntil({ gate.reads == 4 }, "the off")
        XCTAssertNil(gate.shownFor(server), "off hides it, whatever unread says")
        XCTAssertNil(gate.badge(server))
    }

    func testAnUnpairedPhoneOrARoomAsksNothing() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        let (unpaired, _) = fhContainer(fh, server: server, token: nil)
        let gate = FriendHousesGate(container: unpaired)
        defer { gate.stop() }
        gate.start()
        await settleUntil({ gate.reads == 1 }, "the unpaired read")
        XCTAssertNil(gate.shownFor(server))
        XCTAssertEqual(0, statusReads(fh), "an unpaired phone asked anyway")

        let (room, _) = fhContainer(fh, server: server, lock: HouseholdLock(roomModeActive: true))
        let roomGate = FriendHousesGate(container: room)
        defer { roomGate.stop() }
        roomGate.start()
        await settleUntil({ roomGate.reads == 1 }, "the room's read")
        XCTAssertNil(roomGate.shownFor(server))
        XCTAssertEqual(0, statusReads(fh), "a phone that is a room asked anyway")
    }

    func testReadsAreConflated() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        let (container, _) = fhContainer(fh, server: server)
        let gate = FriendHousesGate(container: container)
        defer { gate.stop() }
        gate.start()
        gate.refresh()
        gate.refresh()
        gate.refresh()
        await settleUntil({ gate.reads >= 1 }, "the read")
        await everythingLands()
        XCTAssertEqual(1, gate.reads, "four asks before the first read ran are one read")
        XCTAssertEqual(1, statusReads(fh))
    }

    func testAServerOrTokenChangeReadsAgainAndNothingElseDoes() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on", unread: 1))
        let (container, store) = fhContainer(fh, server: server)
        let gate = FriendHousesGate(container: container)
        defer { gate.stop() }
        gate.start()
        await settleUntil({ gate.reads == 1 }, "the first read")
        gate.inputsMayHaveChanged()
        await everythingLands()
        XCTAssertEqual(1, gate.reads, "nothing changed, and it read again")

        store.setToken("household-token-5678", for: server)
        gate.inputsMayHaveChanged()
        await settleUntil({ gate.reads == 2 }, "the read after a new token")

        store.trustServer(other, identity: TestIdentity.real.pin)
        store.setToken("household-token-9", for: other)
        XCTAssertTrue(store.setServerURL(other))
        fh.answer("GET", "/api/friend-houses", FhHouse.status("off"))
        gate.inputsMayHaveChanged()
        await settleUntil({ gate.reads == 3 }, "the read after a server switch")
        XCTAssertNil(gate.shownFor(other), "the new server's verdict")
        XCTAssertNil(gate.shownFor(server), "the old server's verdict is not kept as this one's")
    }

    func testEveryMinuteWhileOpenAndNotWhileAway() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        let (container, _) = fhContainer(fh, server: server)
        let allowance = FhAllowance()
        let gate = FriendHousesGate(container: container, sleep: { try await allowance.sleep($0) })
        defer { gate.stop() }
        gate.start()
        gate.setForeground(true)
        await settleUntil({ gate.reads == 1 && allowance.asked == 1 }, "the first read and the minute's wait")
        allowance.give()
        await settleUntil({ gate.reads == 2 }, "the minute's read")
        gate.setForeground(false)
        allowance.give(5)
        await everythingLands()
        XCTAssertEqual(2, gate.reads, "the minute ran on while the app was away")
        XCTAssertEqual(60_000, FriendHousesGate.pollMs)
    }

    /// Room mode: the gate stops and hides, every Friend Houses
    /// notification goes, nothing reads while it runs; leaving starts it
    /// again. Notifications only while the app is on screen.
    func testRoomModeStopsHidesAndTakesBackThenLeavingStartsAgain() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on", unread: 1))
        let lock = HouseholdLock()
        let (container, _) = fhContainer(fh, server: server, lock: lock)
        let centre = FakeAlertsCentre()
        await centre.add(AlertsRequest(
            identifier: fhNoteIdentifier(serverKey: "k", house: String(repeating: "ab", count: 32)),
            title: "The Smiths", body: "words", threadIdentifier: FhNotificationSink.thread
        ))
        let api = FriendHousesApi(client: container.client)
        let alerts = FriendHouseAlerts(
            api: api, store: FhSeenBook(place: SavedStateBox()),
            sink: FhNotificationSink(centre: centre, roomMode: { lock.roomModeActive }),
            roomMode: { lock.roomModeActive }
        )
        let gate = FriendHousesGate(container: container, api: api)
        defer { gate.stop() }
        gate.start(alerts: alerts)
        await settleUntil({ gate.reads == 1 }, "the first read")
        XCTAssertNotNil(gate.shownFor(server))

        lock.setRoomModeActive(true)
        gate.pauseForRoomMode()
        XCTAssertNil(gate.shownFor(server), "a room still shows Friend houses")
        await settleUntil({ centre.log.current.delivered.isEmpty }, "the notification taken back")
        gate.refresh()
        gate.inputsMayHaveChanged()
        await everythingLands()
        XCTAssertEqual(1, statusReads(fh), "a room asked the server")

        lock.setRoomModeActive(false)
        gate.resumeAfterRoomMode(foreground: true)
        await settleUntil({ gate.reads == 2 }, "the read after room mode")
        XCTAssertNotNil(gate.shownFor(server))
    }

    func testNothingIsCheckedForNotificationsWhileTheAppIsAway() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on", unread: 1))
        fh.answer("GET", "/api/friend-houses/conversations", #"{"conversations":[],"unread":1}"#)
        let (container, _) = fhContainer(fh, server: server)
        let book = FhSeenBook(place: SavedStateBox())
        let api = FriendHousesApi(client: container.client)
        let alerts = FriendHouseAlerts(api: api, store: book, sink: FhNotificationSink(centre: FakeAlertsCentre()))
        let gate = FriendHousesGate(container: container, api: api)
        defer { gate.stop() }
        gate.start(alerts: alerts)
        await settleUntil({ gate.reads == 1 }, "the read while away")
        await everythingLands()
        XCTAssertNil(book.read(serverKey: AppContainer.serverKey(server) ?? ""), "checked while the app was away")
        gate.setForeground(true)
        gate.refresh()
        await settleUntil({ gate.reads == 2 }, "the read on screen")
        await settleUntil(
            { book.read(serverKey: AppContainer.serverKey(server) ?? "") != nil }, "the check on screen"
        )
    }

    // MARK: - the gate in the shell

    private func shell(_ container: AppContainer) -> ShellModel {
        ShellModel(
            container: container,
            probe: { true },
            wait: { _ in try await Task.sleep(for: .seconds(3600)) },
            tick: { _ in try await Task.sleep(for: .seconds(3600)) },
            serverAnsweredAtStart: true
        )
    }

    func testHiddenEverywhereUntilTheServerSaysOnThenInToolsBesideLists() async throws {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on", unread: 2))
        let (container, _) = fhContainer(fh, server: server)
        let model = shell(container)
        defer { model.stop() }
        // Not started: hidden on every launcher, and not rendered.
        XCTAssertFalse(Route.friendHouses.visibleWith(model.visibility))
        XCTAssertFalse(sidebarSections(model.visibility).workspace.contains(.friendHouses))
        XCTAssertFalse(model.everything.contains(.friendHouses))
        XCTAssertNil(model.badge(for: .friendHouses))

        model.startFriendHouses()
        await settleUntil({ model.friendHouses.reads == 1 }, "the first read")
        XCTAssertTrue(Route.friendHouses.visibleWith(model.visibility))
        // A shared screen too: one inbox for the whole house, not one person's.
        // (A paired phone counts as shared until the server answers.)
        XCTAssertTrue(model.visibility.sharedScreen)
        XCTAssertTrue(Route.friendHouses.visibleOn(model.visibility))
        XCTAssertTrue(model.everything.contains(.friendHouses))
        let tools = try XCTUnwrap(
            sidebarTree(model.visibility).compactMap { entry -> [Route]? in
                if case .group(let group, let routes) = entry, group.id == "tools" { return routes }
                return nil
            }.first
        )
        XCTAssertEqual([.calendar, .lists, .friendHouses], tools, "Tools, after Lists (a shared screen has no News or Files)")
        XCTAssertEqual(2, model.badge(for: .friendHouses))
        XCTAssertNil(model.badge(for: .lists))

        // Open it; the server turns it off; the shell goes home.
        model.navigate(.friendHouses)
        XCTAssertEqual(.friendHouses, model.navigation.route)
        fh.answer("GET", "/api/friend-houses", FhHouse.status("off", unread: 2))
        model.friendHouses.refresh()
        await settleUntil({ model.friendHouses.reads == 2 }, "the off")
        XCTAssertFalse(Route.friendHouses.visibleWith(model.visibility))
        XCTAssertEqual(.home, model.navigation.route, "a screen whose gate closed was left open")
        XCTAssertNil(model.badge(for: .friendHouses))
    }

    func testTheShellsTriggersReadTheStatus() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        let (container, _) = fhContainer(fh, server: server)
        let model = shell(container)
        defer { model.stop() }
        model.startFriendHouses()
        await settleUntil({ model.friendHouses.reads == 1 }, "the start")
        // The live socket coming back.
        model.socketConnectionChanged(true)
        await settleUntil({ model.friendHouses.reads == 2 }, "the reconnect")
        // Coming back to the app (a real departure first).
        model.scenePhaseChanged(to: .background)
        model.scenePhaseChanged(to: .active)
        await settleUntil({ model.friendHouses.reads == 3 }, "the return")
        // An ordinary refresh with nothing changed reads nothing more.
        model.refresh()
        await everythingLands()
        XCTAssertEqual(3, model.friendHouses.reads)
    }

    func testANotificationTapWaitsForTheVerdictThenOpensFriendHouses() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("on"))
        let (container, _) = fhContainer(fh, server: server)
        let model = shell(container)
        defer { model.stop() }
        XCTAssertTrue(container.requestRoute("friend-houses"))
        model.refresh()
        XCTAssertEqual("friend-houses", container.pendingRoute, "taken before the server said it is on")
        XCTAssertEqual(.home, model.navigation.route)
        model.startFriendHouses()
        await settleUntil({ model.navigation.route == .friendHouses }, "the tap's screen")
        XCTAssertNil(container.pendingRoute)
    }

    func testANotificationTapOnAServerThatHidItIsDropped() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/friend-houses", FhHouse.status("unavailable"))
        let (container, _) = fhContainer(fh, server: server)
        let model = shell(container)
        defer { model.stop() }
        model.startFriendHouses()
        await settleUntil({ model.friendHouses.reads == 1 }, "the verdict")
        XCTAssertTrue(container.requestRoute("friend-houses"))
        model.refresh()
        XCTAssertNil(container.pendingRoute, "a tap for a screen this server hides is dropped")
        XCTAssertEqual(.home, model.navigation.route)
    }

    // MARK: - the table (`FriendHousesNavTest`)

    func testItSitsInToolsBesideListsAndIsNoSettingsSection() {
        XCTAssertEqual("tools", defaultNav.groupOf[.friendHouses])
        XCTAssertEqual("friend-houses", navKeys[.friendHouses])
        XCTAssertEqual(.friendHouses, routeForNavKey("friend-houses"))
        XCTAssertEqual("Friend houses", Route.friendHouses.label)
        XCTAssertNil(Route.friendHouses.requiredCapability)
        XCTAssertNil(settingsTabFor(.friendHouses))
        XCTAssertFalse(sharedScreenHidden.contains(.friendHouses))
        // A server screen: not in the local shell.
        XCTAssertFalse(localTabs(phoneCanChat: true, sharedScreen: false, savedBooks: true).contains(.friendHouses))
        // With the gate open, the whole Tools group in order.
        let on = RouteVisibility(capabilities: Capabilities.empty.withFriendHouses(true), sharedScreen: false, phoneCanChat: false)
        let tree = navTree(workspaceRoutes.filter { $0.visibleOn(on) })
        let tools = tree.compactMap { entry -> [Route]? in
            if case .group(let group, let routes) = entry, group.id == "tools" { return routes }
            return nil
        }.first
        XCTAssertEqual([.news, .calendar, .lists, .friendHouses, .files], tools)
    }

    func testABarSavedWithItKeepsTheTabOnlyWhileOn() {
        let saved: [Route] = [.home, .friendHouses, .music]
        let off = RouteVisibility(capabilities: .empty, sharedScreen: false, phoneCanChat: false)
        let on = RouteVisibility(capabilities: Capabilities.empty.withFriendHouses(true), sharedScreen: false, phoneCanChat: false)
        XCTAssertEqual([.home, .music], PhoneBar.routes(saved: saved, visibility: off))
        XCTAssertEqual(saved, PhoneBar.routes(saved: saved, visibility: on))
        XCTAssertTrue(PhoneBar.choices.contains(.friendHouses))
    }

    func testTheFlagNeverComesFromTheManifest() throws {
        let decoded = try JSONDecoder().decode(
            Capabilities.self, from: Data(#"{"friendHouses":true,"friend_houses":true}"#.utf8)
        )
        XCTAssertFalse(decoded.friendHouses)
        XCTAssertEqual(.empty, decoded)
        XCTAssertTrue(Capabilities.empty.withFriendHouses(true).friendHouses)
        XCTAssertEqual(Capabilities.empty, Capabilities.empty.withFriendHouses(false))
    }
}
