import Foundation
import XCTest

/// A request count to measure "nothing more was asked" against, read only
/// once requests have **stopped arriving**.
///
/// A test that says "after this, nothing is asked" takes a baseline and
/// compares it later. Taken straight after an `await`, the baseline can
/// miss a request the model started before the action under test but that
/// hadn't reached the stub yet. That request then lands after the baseline
/// and reads as one the action made: CI's "6 is not equal to 7" in
/// `RoomsModelTests.testTheScreenHoldsNothingAfterItGoesAway`, and "3 is
/// not equal to 4" in `ChatModelTests.testClearingTheFieldAsksForNothing`
/// (2026-11-22), each on a pull request that didn't touch either model.
/// ``StubURLProtocol``'s case stamp already stops a previous case's
/// requests; this is the same race inside one case.
///
/// The count is read until it has held still for `quiet`, on the clock
/// rather than a number of yields (a slower runner yields as often and
/// does less), and the wait gives up at `within` with a failure that names
/// what never settled.
@MainActor
func quietCount(
    _ what: String,
    quiet: Duration = .milliseconds(150),
    within: Duration = .seconds(5),
    file: StaticString = #filePath,
    line: UInt = #line,
    _ read: () -> Int
) async -> Int {
    let clock = ContinuousClock()
    let deadline = clock.now + within
    var last = read()
    var stillSince = clock.now
    while clock.now < deadline {
        try? await Task.sleep(for: .milliseconds(10))
        let now = read()
        if now != last {
            last = now
            stillSince = clock.now
        } else if clock.now - stillSince >= quiet {
            return now
        }
    }
    XCTFail("\(what) never stopped changing within \(within)", file: file, line: line)
    return last
}

extension StubURLProtocol {
    /// ``requestCount`` once requests have stopped arriving (``quietCount(_:quiet:within:file:line:_:)``).
    @MainActor
    static func quietRequestCount(file: StaticString = #filePath, line: UInt = #line) async -> Int {
        await quietCount("the stub's request count", file: file, line: line) { requestCount }
    }
}
