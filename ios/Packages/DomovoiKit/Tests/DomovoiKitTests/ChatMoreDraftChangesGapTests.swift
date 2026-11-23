import Foundation
import XCTest
@testable import DomovoiKit

/// The draft changes view (1.4.1, as the web's `chat_drafts.jsx` and
/// Android's `DraftChangesGapTest`): a paragraph gap between a long removed
/// run and the added run straight after it, and a screen reader told which
/// words went and which came.
final class ChatMoreDraftChangesGapTests: XCTestCase {

    private let long = "one two three four five six seven eight"

    private func runs(_ pairs: [(String, String)]) -> [ChatMoreDraftChange] {
        pairs.map { ChatMoreDraftChange(op: $0.0, text: $0.1) }
    }

    func testALongRemovedRunBeforeAnAddedRunGetsAGap() {
        let ops = runs([("=", "Dear Sam, "), ("-", long), ("+", "new words"), ("=", " end")])
        XCTAssertTrue(ChatMoreDraftChange.gapAfter(ops, 1))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(ops, 0))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(ops, 2))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(ops, 3))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(ops, 9))
    }

    func testALineBreakMakesARunLong() {
        XCTAssertTrue(ChatMoreDraftChange.gapAfter(runs([("-", "two\nlines"), ("+", "x")]), 0))
    }

    func testNoGapForShortRunsOrWhereTheLineAlreadyBreaks() {
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(runs([("-", "seven words are not quite enough here"), ("+", "x")]), 0))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(runs([("-", long + "\n"), ("+", "x")]), 0))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(runs([("-", long), ("+", "\nx")]), 0))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(runs([("-", long), ("=", "x")]), 0))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(runs([("-", long)]), 0))
        XCTAssertFalse(ChatMoreDraftChange.gapAfter(runs([("+", long), ("+", "x")]), 0))
    }

    func testTheScreenReaderHearsWhatWentAndWhatCame() {
        let ops = runs([("=", "Dear Sam, "), ("-", "I am writing "), ("+", "I write"), ("=", " to say hello."), ("+", "  ")])
        XCTAssertEqual("Dear Sam, removed: I am writing. added: I write. to say hello.", ChatMoreDraftChange.spoken(ops))
        XCTAssertEqual("removed: Thanks!", ChatMoreDraftChange.spoken(runs([("-", "Thanks!")])))
    }
}
