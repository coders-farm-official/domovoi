import Foundation
import XCTest
@testable import DomovoiKit

/// The room talks to the one core it proved, and nowhere else: its session
/// follows no redirect, HTTP or WebSocket (Android's `RoomNetworkTest`,
/// security review of wf/room-mode F1 — a 302 on the stream upgrade once
/// handed the hello, with the room's pairing key, to whatever host
/// `Location` named). Real sockets on 127.0.0.1 (``LoopbackServer``).
final class RoomNetworkTests: XCTestCase {

    func testARedirectedStreamUpgradeFailsAndTheHelloGoesNowhere() throws {
        let elsewhere = try LoopbackServer()
        defer { elsewhere.stop() }
        let core = try LoopbackServer(.init(status: 302, location: "http://127.0.0.1:\(elsewhere.port)/somewhere-else"))
        defer { core.stop() }
        let network = RoomNetwork()
        defer { network.invalidate() }
        let failed = RoomLock<String?>(nil)
        let opened = RoomLock(false)
        let hello = RoomProtocol.text(RoomProtocol.hello(roomID: "kitchen", wakeWord: "hey_jarvis", pairingToken: "ROOM-PAIRING-KEY",
                                                         approvalCode: "123456", echoCanceller: false))
        let box = RoomLock<(any RoomSocket)?>(nil)
        let socket = network.open(RoomProtocol.streamURL(coreURL: "http://127.0.0.1:\(core.port)", roomID: "kitchen"), events: RoomSocketEvents(
            opened: {
                opened.withLock { $0 = true }
                box.withLock { $0 }?.sendText(hello)
            },
            text: { _ in },
            binary: { _ in },
            failed: { why in failed.withLock { $0 = why } },
            closed: { failed.withLock { $0 = "closed" } }
        ))
        box.withLock { $0 = socket }
        XCTAssertTrue(RoomTestKit.wait(10) { failed.withLock { $0 } != nil }, "the upgrade must fail on a 302")
        XCTAssertEqual(1, core.requestCount, "the core saw the upgrade")
        XCTAssertTrue(failed.withLock { $0 }?.contains("302") == true, failed.withLock { $0 } ?? "nil")
        XCTAssertFalse(opened.withLock { $0 })
        Thread.sleep(forTimeInterval: 1)
        XCTAssertEqual(0, elsewhere.requestCount, "nothing reached the other host: \(elsewhere.requests)")
    }

    func testARedirectedReadIsAnErrorNotAHop() throws {
        let elsewhere = try LoopbackServer()
        defer { elsewhere.stop() }
        let core = try LoopbackServer(.init(status: 301, location: "http://127.0.0.1:\(elsewhere.port)/v1/health"))
        defer { core.stop() }
        let network = RoomNetwork()
        defer { network.invalidate() }
        XCTAssertThrowsError(try network.get("http://127.0.0.1:\(core.port)/v1/health?challenge=x")) { error in
            XCTAssertEqual(301, (error as? RoomNetworkFailure)?.status)
        }
        Thread.sleep(forTimeInterval: 0.5)
        XCTAssertEqual(0, elsewhere.requestCount)
    }

    func testAReadCarriesNoCredentialAndAPlainReadOffTheHomeNetworkIsRefused() throws {
        let core = try LoopbackServer(.init(status: 200, body: "{}"))
        defer { core.stop() }
        let network = RoomNetwork()
        defer { network.invalidate() }
        XCTAssertEqual(Data("{}".utf8), try network.get("http://127.0.0.1:\(core.port)/v1/health"))
        XCTAssertNil(core.header("X-Device-Token", at: 0))
        XCTAssertNil(core.header("Authorization", at: 0))
        XCTAssertNil(core.header("Cookie", at: 0))
        // Public address, plain http: refused before anything leaves.
        XCTAssertThrowsError(try network.get("http://8.8.8.8/v1/health"))
        XCTAssertFalse(RoomNetwork.configuration().allowsCellularAccess)
        XCTAssertFalse(RoomNetwork.configuration().allowsExpensiveNetworkAccess)
    }

    func testABodyOverTheLimitIsRefused() throws {
        let core = try LoopbackServer(.init(status: 200, body: String(repeating: "x", count: 2_000)))
        defer { core.stop() }
        let network = RoomNetwork()
        defer { network.invalidate() }
        XCTAssertThrowsError(try network.get("http://127.0.0.1:\(core.port)/big", limit: 1_000))
    }
}
