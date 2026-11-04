import XCTest
@testable import DomovoiKit

/// A folder book's parts as one timeline, both ways, at every boundary.
///
/// Android has no test for this because it has no such type — over there
/// `ConcatenatingMediaSource2` does the arithmetic inside ExoPlayer. Here the
/// arithmetic is ours, which is why it is pinned this hard: an off-by-one at a
/// part boundary is a book that jumps a chapter every hour, and a nil total
/// slipping through as zero is a seek bar whose thumb is always at the end.
final class PlayerBookTimelineTests: XCTestCase {

    /// 100 + 150 + 50 = 300.
    private let whole = PlayerBookTimeline(durations: [100, 150, 50])

    func testTheTotalIsTheSumOfThePartsWhenEveryOneIsKnown() {
        XCTAssertTrue(whole.isMappable)
        XCTAssertEqual(whole.totalSec, 300)
        XCTAssertEqual(whole.partCount, 3)
    }

    func testEachPartsOffsetIsWhatCameBeforeIt() {
        XCTAssertEqual(whole.offset(ofPart: 0), 0)
        XCTAssertEqual(whole.offset(ofPart: 1), 100)
        XCTAssertEqual(whole.offset(ofPart: 2), 250)
        XCTAssertNil(whole.offset(ofPart: 3), "out of range")
        XCTAssertNil(whole.offset(ofPart: -1))
    }

    func testGlobalToPartAtEveryBoundary() {
        // The boundary belongs to the part that STARTS there, so a seek to
        // exactly 100 plays part 1 from its top rather than part 0's last
        // sample.
        XCTAssertEqual(whole.part(at: 0)?.index, 0)
        XCTAssertEqual(whole.part(at: 0)?.within, 0)
        XCTAssertEqual(whole.part(at: 99.5)?.index, 0)
        XCTAssertEqual(whole.part(at: 99.5)?.within, 99.5)
        XCTAssertEqual(whole.part(at: 100)?.index, 1)
        XCTAssertEqual(whole.part(at: 100)?.within, 0)
        XCTAssertEqual(whole.part(at: 175)?.index, 1)
        XCTAssertEqual(whole.part(at: 175)?.within, 75)
        XCTAssertEqual(whole.part(at: 250)?.index, 2)
        XCTAssertEqual(whole.part(at: 250)?.within, 0)
        XCTAssertEqual(whole.part(at: 299)?.index, 2)
        XCTAssertEqual(whole.part(at: 299)?.within, 49)
    }

    func testAPositionPastTheEndAnswersTheLastPartsEndRatherThanNothing() {
        // That is where a seek to the end of a book goes; a nil here would
        // make the end of the last chapter unreachable.
        XCTAssertEqual(whole.part(at: 300)?.index, 2)
        XCTAssertEqual(whole.part(at: 300)?.within, 50)
        XCTAssertEqual(whole.part(at: 9_999)?.index, 2)
    }

    func testANegativePositionIsTheBooksStart() {
        XCTAssertEqual(whole.part(at: -10)?.index, 0)
        XCTAssertEqual(whole.part(at: -10)?.within, 0)
    }

    func testPartToGlobalIsTheInverse() {
        for (part, within) in [(0, 0.0), (0, 99.5), (1, 0.0), (1, 75.0), (2, 49.0)] {
            let global = whole.global(part: part, within: within)
            XCTAssertNotNil(global)
            let back = whole.part(at: global!)
            XCTAssertEqual(back?.index, part, "part \(part) at \(within) round-tripped")
            XCTAssertEqual(back?.within ?? -1, within, accuracy: 0.0001)
        }
    }

    func testOneUnknownDurationMakesTheWholeBookUnmappable() {
        let gap = PlayerBookTimeline(durations: [100, nil, 50])
        XCTAssertFalse(gap.isMappable)
        XCTAssertNil(gap.totalSec, "a guessed total is a lying seek bar (MAP §5.6)")
        XCTAssertNil(gap.part(at: 10), "nothing can be mapped, not even inside part 0")
        XCTAssertEqual(gap.offset(ofPart: 0), 0, "part 0 still starts at 0")
        XCTAssertNil(gap.offset(ofPart: 2), "nothing after the gap has a known start")
        XCTAssertNil(gap.global(part: 2, within: 5))
    }

    func testAZeroDurationCountsAsUnknown() {
        // A part the server reported as 0 s is a part whose length it does not
        // know; treating it as a real zero would stack two parts at one offset.
        let zero = PlayerBookTimeline(durations: [100, 0, 50])
        XCTAssertFalse(zero.isMappable)
        XCTAssertNil(zero.totalSec)
    }

    // MARK: - from a queue item

    func testASingleFileBookIsOnePartOfItsOwnLength() {
        let item = PlayerItem.fromBook(id: 3, title: "One File", durationSec: 900)
        let timeline = PlayerBookTimeline(item)
        XCTAssertEqual(timeline.partCount, 1)
        XCTAssertEqual(timeline.totalSec, 900)
        XCTAssertEqual(timeline.part(at: 450)?.index, 0)
        XCTAssertEqual(timeline.part(at: 450)?.within, 450)
    }

    func testAFolderBooksTimelineComesFromItsParts() {
        let timeline = PlayerBookTimeline(PlayerSeed.folderBook)
        XCTAssertEqual(timeline.totalSec, 300)
        // Chapter "Coming Home" starts at 250, which is part 2's top — the
        // property the Android comment asks for: the chapters, the saved place
        // and the seek bar all read one position from the book's start.
        XCTAssertEqual(timeline.part(at: 250)?.index, 2)
    }

    func testABookWhoseLengthIsUnknownAtAllHasNoTotal() {
        let item = PlayerItem.fromBook(id: 4, title: "No Length")
        XCTAssertNil(PlayerBookTimeline(item).totalSec)
    }
}
