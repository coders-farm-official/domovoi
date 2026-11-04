import XCTest
@testable import DomovoiKit

/// The window a long list is cut to.
///
/// `QueueWindowTest` on Android. The numbers matter more here than there: 500
/// `AVPlayerItem`s is a lot of key-value observing, and `AVQueuePlayer` has no
/// jump-to-index, so every change inside the window is a rebuild (MAP §4.2).
final class PlayerQueueWindowTests: XCTestCase {

    private func list(_ n: Int) -> [Int] { Array(0..<n) }

    func testAListWithinTheMaximumComesBackWhole() {
        let slice = PlayerQueueWindow.around(list(30), index: 7, max: 500)
        XCTAssertEqual(slice.items.count, 30)
        XCTAssertEqual(slice.index, 7)
        XCTAssertEqual(slice.items.first, 0)
        XCTAssertEqual(slice.items[slice.index], 7, "the tapped row is the one at `index`")
    }

    func testAnEmptyListAnswersIndexZeroRatherThanMinusOne() {
        let slice = PlayerQueueWindow.around([Int](), index: 4, max: 10)
        XCTAssertTrue(slice.items.isEmpty)
        XCTAssertEqual(slice.index, 0, "a clamp, as playItems would do")
    }

    func testTheWindowKeepsALeadBeforeTheTappedRow() {
        // 1000 items, tap 600, max 100, lead 10 → 590..<690, tapped at 10.
        let slice = PlayerQueueWindow.around(list(1000), index: 600, max: 100, lead: 10)
        XCTAssertEqual(slice.items.count, 100)
        XCTAssertEqual(slice.items.first, 590, "previous still goes somewhere")
        XCTAssertEqual(slice.items.last, 689)
        XCTAssertEqual(slice.index, 10)
        // Pinned by IDENTITY, not by the number above: the near-the-end case
        // had this assertion and this one did not. Insurance rather than a
        // closed hole — measured against `index: at - provisional`, which the
        // foot-of-the-list case catches and this one cannot, because in the
        // middle of a list `provisional` IS the start. It costs a line and it
        // stops `index` from being asserted only against itself.
        XCTAssertEqual(slice.items[slice.index], 600, "the tapped row is still the one at `index`")
    }

    func testNearTheEndTheWindowFillsFromBefore() {
        // Tapping the last row: there is nothing after it, so the window is
        // taken from before rather than coming back short (`QueueWindow.kt:34`).
        let slice = PlayerQueueWindow.around(list(1000), index: 999, max: 100, lead: 10)
        XCTAssertEqual(slice.items.count, 100, "a full window, not the ten rows that fit after the lead")
        XCTAssertEqual(slice.items.first, 900)
        XCTAssertEqual(slice.items.last, 999)
        XCTAssertEqual(slice.index, 99)
        XCTAssertEqual(slice.items[slice.index], 999, "the tapped row is still the one at `index`")
    }

    func testAnOutOfRangeIndexIsClamped() {
        let past = PlayerQueueWindow.around(list(10), index: 99, max: 5)
        XCTAssertEqual(past.items[past.index], 9)
        let before = PlayerQueueWindow.around(list(10), index: -4, max: 5)
        XCTAssertEqual(before.items[before.index], 0)
    }

    func testALeadWiderThanTheWindowStillLeavesRoomForTheTappedRow() {
        let slice = PlayerQueueWindow.around(list(1000), index: 500, max: 5, lead: 50)
        XCTAssertEqual(slice.items.count, 5)
        XCTAssertEqual(slice.items[slice.index], 500, "the row that was tapped is in the window")
    }

    func testTheMaximumIsTheServersOwnCapOnACast() {
        XCTAssertEqual(
            PlayerQueueWindow.maximum, PlayerCastPlanner.maxIDs,
            "play-tracks takes at most 500 ids, so a phone queue never holds more than a room would take"
        )
        XCTAssertEqual(PlayerQueueWindow.maximum, 500)
        XCTAssertEqual(PlayerQueueWindow.lead, 50)
    }
}
