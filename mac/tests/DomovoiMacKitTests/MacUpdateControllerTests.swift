import XCTest
@testable import DomovoiMacKit

/// The menu-bar icon's side (MacUpdateController): which item it offers,
/// when it starts by itself, and what it tells the household. MacUpdateTests'
/// fakes.
extension MacUpdateTests {
    func controller() -> MacUpdateController { MacUpdateController(agent: MacUpdateAgent(server: server, env: env)) }

    func testTheItemIsHiddenWhenItCantDoAnything() {
        let c = controller()
        server.s = nil
        c.poll()
        XCTAssertNil(c.menuOffer(), "Domovoi isn't answering")
        var s = state()
        s.internet = "never"
        server.s = s
        c.poll()
        XCTAssertNil(c.menuOffer(), "internet access is never")
        server.s = state()
        env.helper = nil
        c.poll()
        XCTAssertNil(c.menuOffer(), "not installed by the package")
    }

    func testTheItemsWords() {
        let c = controller()
        server.s = state(step: false)
        c.poll()
        XCTAssertEqual(c.menuOffer()?.title, "Update Domovoi...", "both halves, as the tray's")
        server.s = state(request: "0123456789abcdef")
        c.poll()
        XCTAssertEqual(c.menuOffer()?.title, "Finish updating Domovoi...")
        XCTAssertEqual(c.menuOffer()?.bold, true)
        XCTAssertEqual(c.takeNotice()?.title, "Finish updating Domovoi", "a request from another device is a notification")
        XCTAssertNil(c.takeNotice(), "once")
        c.poll()
        XCTAssertNil(c.takeNotice(), "the same request isn't told twice")
    }

    func testARequestFromThisMacStartsAtOnceAndSaysHowItWent() {
        let c = controller()
        server.s = state(request: "0123456789abcdef", onThisMac: true)
        c.poll()
        c.queue.sync {}
        XCTAssertEqual(server.claims, ["0123456789abcdef"])
        XCTAssertEqual(admin.calls.count, 1)
        let n = c.takeNotice()
        XCTAssertEqual(n?.title, "Domovoi is updated")
        XCTAssertEqual(n?.body, "Domovoi is updated: the Mac side is now 1.2.0.")
        // The core still shows it until its next answer: never a second run.
        c.poll()
        c.queue.sync {}
        XCTAssertEqual(admin.calls.count, 1)
    }

    func testTheMenuShowsTheControllersItem() {
        // Through the menu-bar seam, as MenuBar.swift asks it.
        let c: MacSideUpdates = controller()
        server.s = state(step: false)
        c.poll()
        let running = MenuVerdict(state: .running, line: "Domovoi is running")
        let m = MenuItems.build(verdict: running, attention: [], keepAwake: nil, update: c.offer())
        let item = m.first { $0.action == .update }
        XCTAssertEqual(item?.title, "Update Domovoi...")
        XCTAssertEqual(item?.enabled, true)
        server.s = nil
        c.poll()
        XCTAssertNil(MenuItems.build(verdict: running, attention: [], keepAwake: nil, update: c.offer()).first { $0.action == .update },
                     "no item while Domovoi doesn't answer")
    }

    func testARunStillGoingIsNeverJoinedByASecond() throws {
        // An earlier app's run, its helper still at work (no result yet).
        try MacPendingRun(run: "20270115-075500-0a1b2c3d", requestID: nil, claim: nil, kind: "package", started: "2027-01-15T07:55:00Z",
                          version: "1.2.0", afterLinux: false).save(env.updatesRoot)
        let r = MacUpdateAgent(server: server, env: env).run(state(), fromMenu: true).result
        XCTAssertEqual(r.outcome, MacOutcome.failed)
        XCTAssertTrue(r.detail!.contains("right now"))
        XCTAssertTrue(admin.calls.isEmpty && downloads.fetched.isEmpty)
    }

    func testTheItemDoesTheWaitingRequestElseBothHalves() {
        let c = controller()
        server.s = state(request: "0123456789abcdef")
        c.poll()
        c.start()
        c.queue.sync {}
        XCTAssertEqual(server.claims, ["0123456789abcdef"])
        XCTAssertFalse(admin.calls[0].1.contains("--linux-first"), "the dashboard did the Linux part")
        server.s = state()
        c.poll()
        c.start()
        c.queue.sync {}
        XCTAssertEqual(admin.calls.last?.1.last, "--linux-first", "the menu's own: both halves")
    }
}
