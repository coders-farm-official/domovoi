import Foundation
import XCTest
@testable import DomovoiKit

/// A drop-in marked `await_ready` (Android's `DropinAfterReplyTest`; the Pi's
/// satellite/tests/test_dropin_after_reply.py): the reply announcing the call
/// plays out (at most 8 s), THEN the call opens and `dropin_ready` goes out;
/// a `dropin_end`, a newer `dropin_start` or a disconnect while waiting drops
/// it; without `await_ready` the call opens at once, as before.
///
/// A fake clock: each poll moves it on by the poll's length, and `onPoll`
/// lets a test end the reply or cancel the call at a given time.
final class DropinAfterReplyTests: XCTestCase {

    private final class Room: @unchecked Sendable {
        var clock: Int64 = 0
        var playing = false
        var log: [String] = []
        var onPoll: (Int64) -> Void = { _ in }
        private var queued: [@Sendable () -> Void] = []
        private(set) var dropin: DropinAfterReply!

        init() {
            dropin = DropinAfterReply(
                stillPlaying: { [unowned self] in playing },
                sendReady: { [unowned self] in log.append("dropin_ready@\(clock)") },
                now: { [unowned self] in clock },
                launch: { [unowned self] work in queued.append(work) },
                pause: { [unowned self] ms in
                    clock += ms
                    onPoll(clock)
                }
            )
        }

        func enter(_ name: String = "call") -> @Sendable () -> Void {
            { [unowned self] in log.append("enter \(name)@\(clock)") }
        }

        /// Runs the waits launched so far, as the engine's worker would.
        func runLaunched() {
            while !queued.isEmpty { queued.removeFirst()() }
        }
    }

    func testWithoutAwaitReadyTheCallOpensAtOnceAndNoReadyIsSent() {
        let r = Room()
        r.playing = true
        r.dropin.start(awaitReady: false, enter: r.enter())
        r.runLaunched()
        XCTAssertEqual(["enter call@0"], r.log)
    }

    func testWithNothingPlayingTheCallOpensAtOnceThenReady() {
        let r = Room()
        r.dropin.start(awaitReady: true, enter: r.enter())
        XCTAssertEqual(["enter call@0", "dropin_ready@0"], r.log)
        XCTAssertFalse(r.dropin.waiting)
    }

    func testTheReplyPlaysOutThenTheCallOpensThenReady() {
        let r = Room()
        r.playing = true
        r.onPoll = { t in if t >= 3_000 { r.playing = false } }
        r.dropin.start(awaitReady: true, enter: r.enter())
        XCTAssertTrue(r.log.isEmpty, "nothing opens while the reply plays")
        XCTAssertTrue(r.dropin.waiting)
        r.runLaunched()
        XCTAssertEqual(["enter call@3000", "dropin_ready@3000"], r.log)
        XCTAssertFalse(r.dropin.waiting)
    }

    func testAReplyThatNeverEndsOpensTheCallAtTheEightSecondCap() {
        let r = Room()
        r.playing = true
        r.dropin.start(awaitReady: true, enter: r.enter())
        r.runLaunched()
        XCTAssertEqual(["enter call@8000", "dropin_ready@8000"], r.log)
        XCTAssertEqual(8_000, DropinAfterReply.capMS)
    }

    func testADropinEndWhileWaitingDropsTheCall() {
        let r = Room()
        r.playing = true
        r.onPoll = { t in
            if t == 1_000 { r.dropin.cancel() }
            if t >= 2_000 { r.playing = false }
        }
        r.dropin.start(awaitReady: true, enter: r.enter())
        r.runLaunched()
        XCTAssertEqual([], r.log)
        XCTAssertFalse(r.dropin.waiting)
    }

    func testADisconnectAfterTheReplyEndedStillDropsTheCall() {
        let r = Room()
        r.playing = true
        r.onPoll = { t in
            if t >= 500 {
                r.playing = false
                r.dropin.cancel()
            }
        }
        r.dropin.start(awaitReady: true, enter: r.enter())
        r.runLaunched()
        XCTAssertEqual([], r.log)
    }

    func testANewerDropinStartReplacesTheWaitingOne() {
        let r = Room()
        r.playing = true
        r.onPoll = { t in
            if t == 1_000 { r.dropin.start(awaitReady: true, enter: r.enter("second")) }
            if t >= 2_000 { r.playing = false }
        }
        r.dropin.start(awaitReady: true, enter: r.enter("first"))
        r.runLaunched()
        XCTAssertEqual(["enter second@2000", "dropin_ready@2000"], r.log)
    }

    func testANewerStartWithoutAwaitReadyAlsoDropsTheWaitingOne() {
        let r = Room()
        r.playing = true
        r.onPoll = { t in
            if t == 1_000 { r.dropin.start(awaitReady: false, enter: r.enter("second")) }
            if t >= 2_000 { r.playing = false }
        }
        r.dropin.start(awaitReady: true, enter: r.enter("first"))
        r.runLaunched()
        XCTAssertEqual(["enter second@1000"], r.log)
    }

    func testACallOpensOnceEvenIfAskedTwice() {
        let r = Room()
        r.playing = true
        let n = r.dropin.start(awaitReady: true, enter: r.enter())
        r.playing = false
        XCTAssertTrue(r.dropin.open(n, enter: r.enter()))
        XCTAssertFalse(r.dropin.open(n, enter: r.enter()))
        r.runLaunched()
        XCTAssertEqual(["enter call@0", "dropin_ready@0"], r.log)
    }
}
