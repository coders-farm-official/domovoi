import XCTest
@testable import DomovoiKit

/// Load as you scroll (``Paging``).
///
/// A port of `nearEnd` in `android/.../ui/components/Paged.kt:87` and the
/// constants of `net/Paging.kt`. The `Pager` itself is a later batch's; the
/// guard is here because map §1.9 says to port it and because the iOS shape
/// of the same trap is `onAppear` on the last row of a one-row list.
final class PaginationTests: XCTestCase {

    func testTheNumbersAreTheServersAndAndroids() {
        XCTAssertEqual(50, Paging.defaultPageSize)
        XCTAssertEqual(200, Paging.serverMaxPage)
        XCTAssertEqual(1000, Paging.reloadCap)
        XCTAssertEqual(8, Paging.listThreshold)
        XCTAssertEqual(12, Paging.gridThreshold)
    }

    /// The guard: a screen still showing its header and a spinner has two or
    /// three "rows", the last of which is trivially within eight of the end.
    /// Read without the guard it asked for page two before anyone scrolled
    /// (Music, 2026-10-12).
    func testAScreenShowingASpinnerIsNotNearItsEnd() {
        for total in 0..<16 {
            XCTAssertFalse(
                Paging.nearEnd(totalItems: total, lastVisible: total - 1, threshold: 8),
                "\(total) rows, the last one visible, must not read as near the end"
            )
        }
        // Twice the threshold is where it starts counting.
        XCTAssertTrue(Paging.nearEnd(totalItems: 16, lastVisible: 7, threshold: 8))
    }

    func testAListNearItsEndAsksAndOneAtTheTopDoesNot() {
        // 50 rows, threshold 8: asking starts once row 41 is visible.
        XCTAssertFalse(Paging.nearEnd(totalItems: 50, lastVisible: 40, threshold: 8))
        XCTAssertTrue(Paging.nearEnd(totalItems: 50, lastVisible: 41, threshold: 8))
        XCTAssertTrue(Paging.nearEnd(totalItems: 50, lastVisible: 49, threshold: 8))
        // Nothing visible yet (an empty layout answers -1 on both platforms).
        XCTAssertFalse(Paging.nearEnd(totalItems: 50, lastVisible: -1, threshold: 8))
        // A grid's threshold is larger, so it asks earlier in the same list.
        XCTAssertTrue(Paging.nearEnd(totalItems: 50, lastVisible: 41, threshold: 12))
        XCTAssertFalse(Paging.nearEnd(totalItems: 50, lastVisible: 36, threshold: 12))
    }
}
