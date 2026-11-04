import XCTest
@testable import DomovoiKit

/// What next and previous do for a book, and when a next button is greyed.
///
/// `ChapterSkipTest` on Android. The 0.5 s tolerance is the one that reads
/// like an accident and is not: a seek to a chapter reads back a hair before
/// its start, so without it a next right after a chapter jump would land on
/// the chapter just left.
final class PlayerChapterSkipTests: XCTestCase {

    private let chapters = [
        PlayerChapter(title: "Setting Out", startSec: 0),
        PlayerChapter(title: "The Long Road", startSec: 100),
        PlayerChapter(title: "Coming Home", startSec: 250),
    ]

    func testOnlyABookMovesByChapter() {
        XCTAssertTrue(PlayerChapterSkip.applies(PlayerSeed.folderBook))
        XCTAssertFalse(PlayerChapterSkip.applies(PlayerSeed.episode), "a podcast keeps the queue's next")
        XCTAssertFalse(PlayerChapterSkip.applies(PlayerSeed.transmission))
        XCTAssertFalse(PlayerChapterSkip.applies(nil))
    }

    // MARK: - the at-start tolerance

    func testAChapterCountsAsReachedAHairBeforeItsStart() {
        let starts = chapters.map(\.startSec)
        XCTAssertEqual(PlayerChapterSkip.at(starts, 99.6), 1, "within 0.5 s of 100 is chapter 1")
        XCTAssertEqual(PlayerChapterSkip.at(starts, 99.4), 0, "0.6 s short is still chapter 0")
        XCTAssertEqual(PlayerChapterSkip.at(starts, 100), 1)
        XCTAssertEqual(PlayerChapterSkip.at(starts, -5), -1, "before the first chapter")
    }

    func testNextRightAfterAChapterJumpDoesNotLandBackOnTheOneJustLeft() {
        // The bug the tolerance exists for: a seek to 100 reads back 99.7.
        XCTAssertEqual(
            PlayerChapterSkip.next(chapters: chapters, positionSec: 99.7, hasNextItem: false),
            .seek(250)
        )
    }

    // MARK: - next

    func testNextGoesToTheNextChaptersStart() {
        XCTAssertEqual(
            PlayerChapterSkip.next(chapters: chapters, positionSec: 10, hasNextItem: false), .seek(100)
        )
        XCTAssertEqual(
            PlayerChapterSkip.next(chapters: chapters, positionSec: 150, hasNextItem: false), .seek(250)
        )
    }

    func testNextFromTheLastChapterGoesToTheQueuesNextItemOrNowhere() {
        XCTAssertEqual(
            PlayerChapterSkip.next(chapters: chapters, positionSec: 300, hasNextItem: true), .nextItem
        )
        XCTAssertEqual(
            PlayerChapterSkip.next(chapters: chapters, positionSec: 300, hasNextItem: false), .none,
            "the last chapter of the last item: next does nothing, and its button is greyed"
        )
    }

    func testABookWithNoChaptersMovesByQueueItem() {
        XCTAssertEqual(PlayerChapterSkip.next(chapters: [], positionSec: 10, hasNextItem: true), .nextItem)
        XCTAssertEqual(PlayerChapterSkip.next(chapters: [], positionSec: 10, hasNextItem: false), .none)
    }

    func testChaptersOutOfOrderAreSortedBeforeAnythingIsDecided() {
        let jumbled = [
            PlayerChapter(title: "Coming Home", startSec: 250),
            PlayerChapter(title: "Setting Out", startSec: 0),
            PlayerChapter(title: "The Long Road", startSec: 100),
        ]
        XCTAssertEqual(PlayerChapterSkip.next(chapters: jumbled, positionSec: 10, hasNextItem: false), .seek(100))
    }

    // MARK: - previous

    func testPreviousRestartsTheChapterPastThreeSecondsIn() {
        XCTAssertEqual(PlayerChapterSkip.restartAfterSec, 3.0)
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: chapters, positionSec: 104, hasPreviousItem: true),
            .seek(100), "4 s into the chapter: back to its start"
        )
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: chapters, positionSec: 102, hasPreviousItem: true),
            .seek(0), "2 s in: the chapter before"
        )
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: chapters, positionSec: 103, hasPreviousItem: true),
            .seek(0), "exactly 3 s is not PAST 3 s"
        )
    }

    func testPreviousInTheFirstChapterGoesToTheQueuesPreviousItemOrTheBooksStart() {
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: chapters, positionSec: 1, hasPreviousItem: true),
            .previousItem
        )
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: chapters, positionSec: 1, hasPreviousItem: false),
            .seek(0), "nothing before it: the book's start, which is always something"
        )
    }

    /// `previous` sorts the chapters too.
    ///
    /// `next` has had ``testChaptersOutOfOrderAreSortedBeforeAnythingIsDecided``
    /// since the port; `previous` had nothing of the kind, so a `.sorted()`
    /// dropped from `previous` alone broke nothing in this suite. Unsorted,
    /// `at()` scans [250, 0, 100] and answers the LAST index at or under the
    /// position, which is the 100 — so the book's own start reads as its last
    /// chapter.
    func testPreviousSortsTheChaptersToo() {
        let jumbled = [
            PlayerChapter(title: "Coming Home", startSec: 250),
            PlayerChapter(title: "Setting Out", startSec: 0),
            PlayerChapter(title: "The Long Road", startSec: 100),
        ]
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: jumbled, positionSec: 0.5, hasPreviousItem: true),
            .previousItem,
            "half a second into the book: out of it — unsorted this is a jump to the last chapter"
        )
        XCTAssertEqual(
            PlayerChapterSkip.previous(chapters: jumbled, positionSec: 260, hasPreviousItem: true),
            .seek(250),
            "10 s into the last chapter: back to ITS start — unsorted this restarts The Long Road"
        )
    }

    // MARK: - canNext, which every next button reads

    func testCanNextIsAlwaysTrueWhileCasting() {
        // A room's skip always does something: it has its own queue on the
        // server's disk, whatever this phone holds.
        XCTAssertTrue(PlayerChapterSkip.canNext(queue: [], index: 0, positionSec: 0, casting: true))
        XCTAssertTrue(PlayerChapterSkip.canNext(
            queue: [PlayerSeed.folderBook], index: 0, positionSec: 9_999, casting: true
        ))
    }

    func testCanNextFollowsABooksChaptersOnThisPhone() {
        let one = [PlayerSeed.folderBook]
        XCTAssertTrue(PlayerChapterSkip.canNext(queue: one, index: 0, positionSec: 10, casting: false))
        XCTAssertFalse(
            PlayerChapterSkip.canNext(queue: one, index: 0, positionSec: 300, casting: false),
            "in the last chapter of the only item, next does nothing — so its button is not live"
        )
    }

    func testCanNextForAnOrdinaryQueueIsJustWhetherThereIsANextRow() {
        let two = [PlayerSeed.transmission, PlayerSeed.atmosphere]
        XCTAssertTrue(PlayerChapterSkip.canNext(queue: two, index: 0, positionSec: 0, casting: false))
        XCTAssertFalse(PlayerChapterSkip.canNext(queue: two, index: 1, positionSec: 0, casting: false))
    }

    func testAnEmptyQueueOffersNeitherNextNorPrevious() {
        XCTAssertFalse(PlayerChapterSkip.canNext(queue: [], index: 0, positionSec: 0, casting: false))
        XCTAssertFalse(PlayerChapterSkip.canPrevious(queue: [], index: 0, casting: false))
    }

    func testPreviousIsLiveWheneverSomethingIsLoaded() {
        // There is always something for previous to do with a loaded item:
        // restart it, or step back a chapter (`CastAwarePlayer.kt:190`).
        XCTAssertTrue(PlayerChapterSkip.canPrevious(queue: [PlayerSeed.transmission], index: 0, casting: false))
        XCTAssertTrue(PlayerChapterSkip.canPrevious(queue: [], index: 0, casting: true))
    }
}
