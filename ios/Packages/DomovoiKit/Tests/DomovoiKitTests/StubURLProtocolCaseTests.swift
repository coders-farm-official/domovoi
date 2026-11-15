import Foundation
import XCTest
@testable import DomovoiKit

/// The stub's case boundary: a request made by a session built in an earlier
/// test case is neither recorded nor answered by this case's script.
///
/// CI's red at `dbe3e6a4` was a leak of exactly that shape — a re-read queued
/// in the turn `RoomsModelTests.testCancellingATimerThatHasAlreadyGoneIsNotAFailure`
/// returned, recorded as request `[0]` of the next case. This pins the guard
/// that makes the whole suite immune to it, whichever file leaks.
@MainActor
final class StubURLProtocolCaseTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private let url = URL(string: "http://10.0.0.42:6369/api/satellites/kitchen/timers")
        ?? URL(fileURLWithPath: "/")

    func testARequestFromAnEarlierCaseIsNeitherRecordedNorAnswered() async {
        let asked = Shared(0)
        let sawStamp = Shared(false)
        StubURLProtocol.dispatch { request in
            asked.mutate { $0 += 1 }
            if request.value(forHTTPHeaderField: StubURLProtocol.caseHeader) != nil { sawStamp.set(true) }
            return .json("[]")
        }
        let earlier = URLSession(configuration: StubURLProtocol.session(timeout: 1))

        // This case's own request: recorded and answered, with the stamp
        // stripped from both.
        let own = try? await earlier.data(from: url)
        XCTAssertEqual(200, (own?.1 as? HTTPURLResponse)?.statusCode, "this case's own request was answered")
        XCTAssertEqual(1, StubURLProtocol.requestCount)
        XCTAssertEqual(1, asked.current)
        XCTAssertNil(
            StubURLProtocol.recorded.first?.value(forHTTPHeaderField: StubURLProtocol.caseHeader),
            "the stamp is not in what a test reads back"
        )
        XCTAssertFalse(sawStamp.current, "nor in what a dispatcher is handed")

        // The next case starts; the old session's late request is refused.
        let refusedBefore = StubURLProtocol.refusedFromEarlierCases
        StubURLProtocol.caseStarted()
        var failure: URLError.Code?
        do {
            _ = try await earlier.data(from: url)
        } catch let error as URLError {
            failure = error.code
        } catch {
            XCTFail("not a URLError: \(error)")
        }
        XCTAssertEqual(.cancelled, failure, "an earlier case's request was answered")
        XCTAssertEqual(1, StubURLProtocol.requestCount, "an earlier case's request was recorded in this case's log")
        XCTAssertEqual(1, asked.current, "an earlier case's request reached this case's dispatcher")
        XCTAssertEqual(refusedBefore + 1, StubURLProtocol.refusedFromEarlierCases)

        // A session built in the new case is that case's.
        let now = URLSession(configuration: StubURLProtocol.session(timeout: 1))
        _ = try? await now.data(from: url)
        XCTAssertEqual(2, StubURLProtocol.requestCount, "the new case's own session is recorded")
        earlier.invalidateAndCancel()
        now.invalidateAndCancel()
    }
}
