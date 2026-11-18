import Foundation
import XCTest
@testable import DomovoiKit

/// Security audit round 6, IOS-3: a WebSocket upgrade that carries the
/// household token is never redirected, on either socket transport.
///
/// Against two real loopback sockets (``LoopbackServer``), the way
/// `PlatformFactsTests` measured the hazard: the first answers the upgrade
/// with a `302` to the second, and what is asserted is the bytes that
/// reached the second, which must be none. Before ``SocketRedirectRefusal``
/// the platform followed that `302` by itself, token and all (facts 2 and 4).
final class SocketRedirectTests: XCTestCase {

    private static func overASocket() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return configuration
    }

    private func upgrade(to server: LoopbackServer) -> URLRequest {
        var request = URLRequest(url: URL(string: "ws://127.0.0.1:\(server.port)/ws/state")!)
        request.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
        return request
    }

    func testTheStateSocketNeverFollowsARedirectWithTheToken() throws {
        let elsewhere = try LoopbackServer(.init(body: "{}"))
        defer { elsewhere.stop() }
        let server = try LoopbackServer(.init(status: 302, location: elsewhere.url("/ws/state").absoluteString))
        defer { server.stop() }

        let ended = RoomLock(false)
        let transport = URLSessionStateSocketTransport(
            session: URLSession(configuration: Self.overASocket()),
            connectBudgetMs: 8_000
        )
        let link = transport.open(upgrade(to: server), reporting: StateSocketEvents(
            opened: { _ in XCTFail("a 302 is not an open socket") },
            text: { _ in },
            failed: { _, _ in ended.withLock { $0 = true } },
            closed: { ended.withLock { $0 = true } }
        ))
        XCTAssertTrue(RoomTestKit.wait(10) { ended.withLock { $0 } }, "the upgrade never ended")
        link.cancel()

        XCTAssertEqual("household-abc", server.header(deviceTokenHeader, at: 0), "the aimed host got the upgrade")
        XCTAssertEqual(0, elsewhere.requestCount, "the host the 302 named was never reached")
        XCTAssertNil(elsewhere.header(deviceTokenHeader, at: 0))
    }

    func testTheDropInSocketNeverFollowsARedirectWithTheToken() throws {
        let elsewhere = try LoopbackServer(.init(body: "{}"))
        defer { elsewhere.stop() }
        let server = try LoopbackServer(.init(status: 307, location: elsewhere.url("/v1/dropin/den").absoluteString))
        defer { server.stop() }

        let ended = RoomLock(false)
        let transport = URLSessionDropInTransport(configuration: Self.overASocket())
        let socket = transport.open(upgrade(to: server), reporting: DropInSocketEvents(
            opened: { XCTFail("a 307 is not an open socket") },
            binary: { _ in },
            text: { _ in },
            failed: { _ in ended.withLock { $0 = true } },
            closed: { ended.withLock { $0 = true } }
        ))
        XCTAssertTrue(RoomTestKit.wait(10) { ended.withLock { $0 } }, "the upgrade never ended")
        socket.close()

        XCTAssertEqual("household-abc", server.header(deviceTokenHeader, at: 0))
        XCTAssertEqual(0, elsewhere.requestCount, "the host the 307 named was never reached")
    }

    /// The delegate itself, with no network: every redirect is declined,
    /// whatever the status or the place it names.
    func testTheRefusalDeclinesEveryRedirect() throws {
        let session = URLSession(configuration: .ephemeral)
        let task = session.dataTask(with: URL(string: "http://192.168.1.20:6369/ws/state")!)
        for (status, location) in [(301, "http://192.168.1.20:6369/x"), (302, "ws://10.0.0.99/ws/state"),
                                   (307, "/same-origin"), (308, "https://192.168.1.20/ws/state")] {
            let response = try XCTUnwrap(HTTPURLResponse(
                url: URL(string: "http://192.168.1.20:6369/ws/state")!, statusCode: status,
                httpVersion: "HTTP/1.1", headerFields: ["Location": location]
            ))
            var proposal = URLRequest(url: URL(string: "http://10.0.0.99/ws/state")!)
            proposal.setValue("household-abc", forHTTPHeaderField: deviceTokenHeader)
            let answer = RoomLock<(asked: Bool, next: URLRequest?)>((false, nil))
            SocketRedirectRefusal.shared.urlSession(
                session, task: task, willPerformHTTPRedirection: response, newRequest: proposal
            ) { next in
                answer.withLock { $0 = (true, next) }
            }
            XCTAssertTrue(answer.withLock { $0.asked }, "answered at once")
            XCTAssertNil(answer.withLock { $0.next }, "\(status) to \(location)")
        }
        session.invalidateAndCancel()
    }
}
