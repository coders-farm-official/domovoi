import Foundation
import XCTest
@testable import DomovoiKit

/// ``BookTimeout`` — the bound that keeps a server out of reach from holding
/// up play, and the sleep a case passes it.
///
/// **This file exists because review 3 of the 2026-11-03 batch said the
/// guard was probabilistic.** ``BookTimeout/heldShut(yields:)`` was added to
/// fix an intermittent red in `AudiobooksModelTests`, and the only thing
/// standing against a regression to `{ _ in }` was that same intermittent
/// case: reverting `heldShut` made it fail **2 of 6** runs, not 6 of 6. A
/// guard that fires a third of the time is a guard the next reader will
/// delete, so the rule is measured here instead, where it is deterministic:
///
/// * `bounded` returns whichever child lands first, so a sleep that returns
///   at once is a timeout that wins on a coin toss.
/// * `heldShut` yields rather than waiting on a clock, so a case costs no
///   real time and the leg that answers is always the work.
/// * and it is **bounded**: a `work` that never finishes must make a run go
///   red rather than hang, which an unbounded sleep would prevent.
///
/// No clock, no server and no audio: `heldShut`'s whole contract is about the
/// cooperative pool, so these are the only assertions that can be made about
/// it at all.
final class BookTimeoutTests: XCTestCase {

    /// A `work` that only yields still beats the bound.
    ///
    /// This is the shape the audiobook cases are in — a read answered by a
    /// stub, which costs several hops of the pool and no time — and the one
    /// `sleep: { _ in }` lost at random.
    func testTheWorkWinsAgainstASleepThatIsHeldShut() async {
        let answer = await BookTimeout.bounded(ms: 1, sleep: BookTimeout.heldShut()) {
            for _ in 0..<500 { await Task.yield() }
            return 7
        }
        XCTAssertEqual(
            7, answer,
            "the bound fired over work that only yielded, so the sleep did not hold shut"
        )
    }

    /// The sleep does not come back on its own.
    ///
    /// The assertion that fails **every** time against `{ _ in }`, which is
    /// what the intermittent case could not do: two thousand turns of the
    /// pool is far short of `heldShut`'s own ceiling, so a sleep that is
    /// holding shut has not returned and one that returns at once has.
    func testTheHeldShutSleepDoesNotReturnOnItsOwn() async {
        let sleeper = BookTimeout.heldShut()
        let came = Shared(false)
        let task = Task {
            try? await sleeper(3_000)
            came.set(true)
        }

        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertFalse(
            came.current,
            "the sleep returned without being cancelled: in `bounded` that is a timeout "
                + "racing the read, which is the flake this helper was written to end"
        )

        // And it ends on cancellation, which is what `bounded` does to the
        // losing child: a sleep that ignored it would leave a task per call
        // turning the pool for fifty thousand yields.
        task.cancel()
        for _ in 0..<settleYieldLimit {
            if came.current { break }
            await Task.yield()
        }
        XCTAssertTrue(came.current, "the sleep outlived its cancellation")
    }

    /// The ceiling is a real ceiling: when the yields run out the bound
    /// fires.
    ///
    /// `heldShut` is deliberately finite, so `work` that never finishes gives
    /// a red run rather than a hung one — a hung train is worse evidence than
    /// a failed case, and an orphaned `xctest` on the shared Mac is worse than
    /// both. Two yields here stands in for fifty thousand.
    func testTheSleepsCeilingStillLetsTheBoundFire() async {
        let answer = await BookTimeout.bounded(ms: 1, sleep: BookTimeout.heldShut(yields: 2)) {
            for _ in 0..<10_000 { await Task.yield() }
            return 7
        }
        XCTAssertNil(
            answer,
            "work that never finishes must time out: an unbounded sleep would hang the run"
        )
    }
}
