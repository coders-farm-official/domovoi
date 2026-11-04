import Foundation
import XCTest
@testable import DomovoiKit

/// The transcript's cap (D64, Kamron's answer 64: "cap the transcript (last N
/// plus 'show earlier')").
///
/// Android has nothing to compare against — its transcript is a `LazyColumn`
/// and needs no cap — so every rule here is specified by this file and nowhere
/// else. The four that matter:
///
///  1. **A short conversation is not windowed at all**, so no case and no
///     ordinary thread ever sees the control. Fifty is the page, and the
///     longest thread the stub house serves is eight.
///  2. **The newest message is always drawn.** A reply arriving where it
///     cannot be seen is the whole screen failing.
///  3. **A grown window is an absolute pin**, so a reply landing underneath it
///     does not walk the line somebody is reading off the top.
///  4. **A different thread opens at its newest page**, because the window is
///     a reading position in the thread that was open.
final class ChatTranscriptWindowTests: XCTestCase {

    // MARK: - the type on its own

    func testAShortConversationIsDrawnWhole() {
        let window = ChatTranscriptWindow()
        for count in [0, 1, 7, ChatTranscriptWindow.page] {
            XCTAssertEqual(0, window.firstDrawn(of: count), "\(count) messages were windowed")
            XCTAssertFalse(window.hasEarlier(of: count), "\(count) messages offered show earlier")
            XCTAssertEqual(0, window.earlierCount(of: count))
        }
    }

    func testALongConversationDrawsTheNewestPage() {
        let window = ChatTranscriptWindow()
        XCTAssertEqual(950, window.firstDrawn(of: 1000))
        XCTAssertEqual(950, window.earlierCount(of: 1000))
        XCTAssertTrue(window.hasEarlier(of: 1000))
    }

    func testShowEarlierAddsOnePageAtATime() {
        var window = ChatTranscriptWindow()
        window.showEarlier(of: 1000)
        XCTAssertEqual(900, window.firstDrawn(of: 1000), "one tap is one page")
        window.showEarlier(of: 1000)
        XCTAssertEqual(850, window.firstDrawn(of: 1000))
    }

    func testShowEarlierStopsAtTheStartAndTheControlGoesWithIt() {
        var window = ChatTranscriptWindow()
        for _ in 0..<40 { window.showEarlier(of: 120) }
        XCTAssertEqual(0, window.firstDrawn(of: 120), "the last tap did not reach the first message")
        XCTAssertFalse(
            window.hasEarlier(of: 120),
            "show earlier is still offered over a conversation that is drawn whole"
        )
    }

    /// Rule 3. The pin is an index from the **start**, which is the one choice
    /// that needs no hook in `append` or `beginRetry`: twenty replies landing
    /// under a window somebody grew leave its oldest drawn message exactly
    /// where it was.
    func testAGrownWindowKeepsItsOldestMessageWhileTheChatGrows() {
        var window = ChatTranscriptWindow()
        window.showEarlier(of: 1000)
        XCTAssertEqual(900, window.firstDrawn(of: 1000))
        for extra in 1...20 {
            XCTAssertEqual(
                900, window.firstDrawn(of: 1000 + extra),
                "message \(extra) walked the top of the window"
            )
        }
    }

    /// An untouched window is the opposite, and deliberately so: it follows
    /// the end, which is rule 2.
    func testAnUntouchedWindowFollowsTheNewestMessage() {
        let window = ChatTranscriptWindow()
        XCTAssertEqual(950, window.firstDrawn(of: 1000))
        XCTAssertEqual(970, window.firstDrawn(of: 1020))
    }

    /// A pin past the end — a thread reloaded shorter while the window was
    /// grown — draws the last message rather than nothing.
    func testAPinPastTheEndStillDrawsSomething() {
        var window = ChatTranscriptWindow()
        window.showEarlier(of: 1000)
        XCTAssertEqual(900, window.pinned)
        XCTAssertEqual(3, window.firstDrawn(of: 4), "a shorter thread drew none of itself")
        XCTAssertEqual(0, window.firstDrawn(of: 0))
    }

    func testResetGoesBackToTheNewestPage() {
        var window = ChatTranscriptWindow()
        window.showEarlier(of: 1000)
        window.reset()
        XCTAssertNil(window.pinned)
        XCTAssertEqual(950, window.firstDrawn(of: 1000))
    }

    // MARK: - the house's transcript

    private func thread(_ count: Int) -> [ChatMessage] {
        (1...count).map { index in
            ChatMessage(
                id: Int64(index),
                role: index.isMultiple(of: 2) ? "assistant" : "user",
                content: "message \(index)"
            )
        }
    }

    func testTheHousesTranscriptDrawsTheNewestPageOfALongThread() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        XCTAssertEqual(1000, transcript.messages.count, "the whole thread is still held")
        XCTAssertEqual(50, transcript.drawn.count, "the cap is not applied")
        XCTAssertEqual(950, transcript.earlierCount)
        XCTAssertEqual("message 951", transcript.drawn.first?.content)
        XCTAssertEqual("message 1000", transcript.drawn.last?.content)
    }

    func testTheHousesShortThreadsAreDrawnWhole() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(8), thisDeviceID: "ios-1"))
        XCTAssertEqual(8, transcript.drawn.count)
        XCTAssertEqual(0, transcript.earlierCount, "an eight-message thread was windowed")
    }

    /// The slice's indices are the **transcript's**, which is what the window
    /// divider's index and the last-row rule are compared against: a window
    /// that renumbered its messages would draw the divider in the wrong place
    /// and offer a heads-up card on the wrong row.
    func testTheDrawnSliceKeepsTheTranscriptsOwnIndices() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        XCTAssertEqual(950, transcript.drawn.startIndex)
        XCTAssertEqual(1000, transcript.drawn.endIndex)
        XCTAssertEqual("message 951", transcript.messages[transcript.drawn.startIndex].content)
    }

    func testASendIsAlwaysDrawnHoweverLongTheThread() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        transcript.beginSend(content: "and one more thing", atISO: "2026-11-03T09:00:00Z")
        let mine = transcript.drawn.first { $0.role == "user" && $0.content == "and one more thing" }
        XCTAssertNotNil(mine, "a send into a windowed thread is not on screen")
        XCTAssertEqual(true, transcript.drawn.last?.pending, "the pending reply is outside the window")
    }

    func testShowEarlierOnTheHousesTranscript() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        transcript.showEarlier()
        XCTAssertEqual(100, transcript.drawn.count)
        XCTAssertEqual("message 901", transcript.drawn.first?.content)
        XCTAssertEqual(900, transcript.earlierCount)
    }

    /// Rule 3 through the real transcript: a question and its reply land under
    /// a grown window and the oldest drawn message does not move. Without the
    /// absolute pin this is `message 903`.
    func testASendUnderAGrownWindowLeavesItsTopAlone() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        transcript.showEarlier()
        XCTAssertEqual("message 901", transcript.drawn.first?.content)
        transcript.beginSend(content: "still reading", atISO: "2026-11-03T09:00:00Z")
        XCTAssertEqual(
            "message 901", transcript.drawn.first?.content,
            "a send walked the top of a window somebody was reading"
        )
    }

    /// And a "try again", which takes one message out and puts one back.
    func testATryAgainUnderAGrownWindowLeavesItsTopAlone() {
        var transcript = ChatTranscript()
        var rows = thread(1000)
        rows[999] = ChatMessage(id: 1000, role: "assistant", content: "message 1000", retryable: true)
        XCTAssertTrue(transcript.load(rows, thisDeviceID: "ios-1"))
        transcript.showEarlier()
        XCTAssertTrue(transcript.beginRetry(), "the last reply was not retryable")
        XCTAssertEqual(
            "message 901", transcript.drawn.first?.content,
            "a retry walked the top of a window somebody was reading"
        )
    }

    func testOpeningAnotherThreadStartsAtItsNewestPage() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        transcript.showEarlier()
        XCTAssertEqual(900, transcript.earlierCount)
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        XCTAssertEqual(
            950, transcript.earlierCount,
            "the window carried a reading position into the next thread"
        )
    }

    func testClearingPutsTheWindowBack() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(thread(1000), thisDeviceID: "ios-1"))
        transcript.showEarlier()
        transcript.clear()
        XCTAssertNil(transcript.window.pinned)
    }
}
