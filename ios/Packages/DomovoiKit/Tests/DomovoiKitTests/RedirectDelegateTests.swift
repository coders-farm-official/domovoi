import XCTest
@testable import DomovoiKit

/// The redirect delegate, driven directly.
///
/// `DomovoiClientTests` proves the rules hold for a real redirect chain
/// through `URLSession`, and `PlatformFactsTests` proves the platform calls
/// this method at all. This file is for the cases a chain cannot easily
/// produce: the hop ceiling without twenty round trips, a wait that starts
/// mid-chain, and what the delegate does with the proposal it is handed.
final class RedirectDelegateTests: XCTestCase {

    private let base = ServerURL.parse("http://10.0.0.9:6369")!

    private func response(_ status: Int, location: String?) -> HTTPURLResponse {
        HTTPURLResponse(
            url: URL(string: "http://10.0.0.9:6369/api/x")!,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: location.map { ["Location": $0] } ?? [:]
        )!
    }

    private func delegate(
        from url: String = "http://10.0.0.9:6369/api/x",
        token: String? = "household-abc",
        wsUpgrade: Bool = false,
        outcome: DomovoiClient.TokenOutcome? = nil
    ) -> RedirectDelegate {
        var request = URLRequest(url: URL(string: url)!)
        if let token { request.setValue(token, forHTTPHeaderField: deviceTokenHeader) }
        let base = base
        return RedirectDelegate(start: request, wsUpgrade: wsUpgrade) { hop in
            if let outcome { return outcome }
            guard TokenScope.admits(base, hop, wsUpgrade: false) else { return .bare }
            return .send(token: "household-abc", server: "http://10.0.0.9:6369")
        }
    }

    func testTheTokenIsDecidedAfreshForTheHopAndNotCopiedFromTheProposal() throws {
        let onServer = delegate()
        let kept = try XCTUnwrap(onServer.followUp(response: response(302, location: "/api/y")))
        XCTAssertEqual(kept.value(forHTTPHeaderField: deviceTokenHeader), "household-abc")

        let offServer = delegate()
        let dropped = try XCTUnwrap(offServer.followUp(response: response(302, location: "http://10.0.0.10:6369/y")))
        XCTAssertNil(dropped.value(forHTTPHeaderField: deviceTokenHeader))
    }

    func testAHopToAHostTheCleartextRuleRefusesStopsTheChainWithTheReason() {
        let hops = delegate()
        XCTAssertNil(hops.followUp(response: response(302, location: "http://203.0.113.5/grab.mp3")))
        XCTAssertEqual(hops.refusal, .notPermitted(CleartextPolicy.refusalMessage("203.0.113.5")))
    }

    func testAWaitThatStartsMidChainStopsItRatherThanSendingTheTokenAnyway() {
        let hops = delegate(outcome: .wait(ms: 30_000))
        XCTAssertNil(hops.followUp(response: response(302, location: "/api/y")))
        XCTAssertEqual(hops.refusal, .tokenThrottled(waitMs: 30_000))
    }

    func testAGateThatRefusesMidChainStopsIt() {
        let hops = delegate(outcome: .held(reason: "not proved on this network"))
        XCTAssertNil(hops.followUp(response: response(302, location: "/api/y")))
        XCTAssertEqual(hops.refusal, .identityHeld("not proved on this network"))
    }

    func testTheCeilingIsCountedAndTheReasonRecorded() {
        let hops = delegate()
        for hop in 1...RedirectPolicy.maxHops {
            XCTAssertNotNil(hops.followUp(response: response(302, location: "/api/\(hop)")), "hop \(hop)")
        }
        XCTAssertNil(hops.followUp(response: response(302, location: "/api/last")))
        XCTAssertEqual(hops.refusal, .notPermitted("too many redirects: 21"))
    }

    func testOnceTheChainIsRefusedNothingElseIsFollowed() {
        let hops = delegate()
        _ = hops.followUp(response: response(302, location: "http://203.0.113.5/x"))
        XCTAssertNotNil(hops.refusal)
        XCTAssertNil(hops.followUp(response: response(302, location: "/api/y")), "a refusal is final")
    }

    func testASocketUpgradeIsNeverFollowed() {
        let socket = delegate(from: "http://10.0.0.9:6369/ws/state", wsUpgrade: true)
        XCTAssertNil(socket.followUp(response: response(302, location: "/ws/elsewhere")))
        XCTAssertNil(socket.refusal, "it is not an error, it is simply not followed — as on Android")
    }

    func testEachHopIsResolvedAgainstThePreviousHopNotTheFirst() throws {
        let hops = delegate()
        let first = try XCTUnwrap(hops.followUp(response: response(301, location: "http://10.0.0.9:6369/api/a/b")))
        XCTAssertEqual(first.url?.absoluteString, "http://10.0.0.9:6369/api/a/b")
        let second = try XCTUnwrap(hops.followUp(response: response(301, location: "c")))
        XCTAssertEqual(second.url?.absoluteString, "http://10.0.0.9:6369/api/a/c")
    }
}
