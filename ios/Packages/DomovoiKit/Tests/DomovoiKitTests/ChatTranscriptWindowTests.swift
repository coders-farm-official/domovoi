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

// MARK: - the phone's conversation

/// The **same** cap on the phone's own transcript
/// (``AssistantPhoneModel/transcriptPage``), which round 1 of this branch
/// built and left with no package test at all — the view windowed the array
/// inline and `drawnTranscript` had no caller.
///
/// It is in this file rather than `AssistantPhoneModelTests` because it is one
/// rule in one type (``ChatTranscriptWindow``) read through two models, and a
/// reader checking what the cap does wants both halves in front of them. The
/// cases are the Assistant-side twins of the house ones above: the newest page
/// on open, one page a tap, the absolute pin under a reply, and the reset on
/// every one of the three ways a conversation changes.
@MainActor
final class AssistantPhoneTranscriptWindowTests: XCTestCase {

    private var directory: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("assistant-window-\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
        try super.tearDownWithError()
    }

    /// A model over a temporary store, with the clock and the waits in the
    /// test's hands — `AssistantPhoneModelTests`'s own rig, cut down to what
    /// a window needs.
    private func make() -> (AssistantPhoneModel, AssistantConversationStore) {
        let store = AssistantConversationStore(directory: directory)
        let model = AssistantPhoneModel(
            engine: FakeAssistantEngine(),
            store: store,
            lastPlace: SavedStateBox(),
            nowMs: { 1_762_000_000_000 },
            stamp: { _ in "2026-11-03 09:00" },
            wait: { _ in }
        )
        return (model, store)
    }

    /// `count` messages, alternating, named by their number so a case can say
    /// which one is at the top of the window.
    private func conversation(_ id: String, _ count: Int) -> AssistantConversation {
        AssistantConversation(
            id: id,
            created: 1,
            updated: 2,
            messages: (1...count).map { number in
                AssistantMessage(
                    from: number.isMultiple(of: 2) ? .domovoi : .user,
                    text: "message \(number)",
                    at: Int64(number)
                )
            },
            name: "the long one \(id)"
        )
    }

    private func seed(_ store: AssistantConversationStore, _ id: String, _ count: Int) {
        XCTAssertTrue(store.save(conversation(id, count)), "the conversation was not written")
    }

    func testALongConversationOnThePhoneOpensAtItsNewestPage() {
        let (model, store) = make()
        seed(store, "a", 1000)
        model.open("a")
        let page = model.transcriptPage
        XCTAssertEqual(1000, page.all.count, "the whole conversation is still held")
        XCTAssertEqual(950, page.hidden, "the cap is not applied")
        XCTAssertEqual(50, page.drawn.count)
        XCTAssertEqual("message 951", page.drawn.first?.text)
        XCTAssertEqual("message 1000", page.drawn.last?.text)
        XCTAssertEqual(999, page.lastIndex)
    }

    func testAShortConversationOnThePhoneIsDrawnWhole() {
        let (model, store) = make()
        seed(store, "b", 8)
        model.open("b")
        let page = model.transcriptPage
        XCTAssertEqual(0, page.hidden, "an eight-message conversation was windowed")
        XCTAssertEqual(8, page.drawn.count)
    }

    /// The slice carries the array's own indices, which is what the window
    /// divider and the "writing" spinner are keyed on: a window that
    /// renumbered would put the spinner on the wrong bubble.
    func testThePhonesDrawnSliceKeepsTheConversationsOwnIndices() {
        let (model, store) = make()
        seed(store, "c", 1000)
        model.open("c")
        let page = model.transcriptPage
        XCTAssertEqual(950, page.drawn.startIndex)
        XCTAssertEqual(1000, page.drawn.endIndex)
        XCTAssertEqual("message 951", page.all[page.drawn.startIndex].text)
    }

    func testShowEarlierOnThePhoneAddsOnePage() {
        let (model, store) = make()
        seed(store, "d", 1000)
        model.open("d")
        model.showEarlier()
        let page = model.transcriptPage
        XCTAssertEqual(900, page.hidden)
        XCTAssertEqual(100, page.drawn.count)
        XCTAssertEqual(
            "message 901", page.drawn.first?.text,
            "one tap of a fifty-message page makes 901 the oldest drawn message"
        )
        model.showEarlier()
        XCTAssertEqual("message 851", model.transcriptPage.drawn.first?.text)
    }

    /// A reply landing under a grown window must not walk the message somebody
    /// is reading off the top. ``AssistantPhoneModel/transcript`` appends the
    /// reply being written, so this is the growth without a round trip: a
    /// count from the end would read `message 903` here.
    func testAReplyUnderAGrownPhoneWindowLeavesItsTopAlone() async {
        let (model, store) = make()
        seed(store, "e", 1000)
        model.open("e")
        model.showEarlier()
        XCTAssertEqual("message 901", model.transcriptPage.drawn.first?.text)
        model.send("and one more thing")
        // The person's own message is saved before the model is asked, and the
        // reply's empty row is appended in the same turn — so the transcript
        // is already 1,002 long and the last row is the reply being written.
        // The question is the one before it, and both are inside the window.
        let sent = model.transcriptPage
        XCTAssertEqual(
            "message 901", sent.drawn.first?.text,
            "a send walked the top of a window somebody was reading"
        )
        XCTAssertTrue(
            sent.drawn.contains { $0.text == "and one more thing" },
            "a send into a windowed conversation is not on screen: \(sent.drawn.count) drawn"
        )
        XCTAssertEqual(1001, sent.lastIndex, "the question and its reply are not both appended")

        // And again once the reply has actually been written, which is what
        // `sending` going false means here. Asserted on the reply's own words
        // so that a loop which measured an empty row cannot pass this.
        for _ in 0..<600 where model.sending {
            try? await Task.sleep(for: .milliseconds(5))
        }
        let settled = model.transcriptPage
        XCTAssertFalse(
            (settled.drawn.last?.text ?? "").isEmpty,
            "the fake engine's reply never arrived, so nothing was measured under it"
        )
        XCTAssertEqual(
            "message 901", settled.drawn.first?.text,
            "the reply walked the top of a window somebody was reading"
        )
    }

    /// Rule 4, through all three of the ways this model leaves a conversation:
    /// the window is a reading position in the one that was open.
    func testEveryWayOutOfAPhoneConversationStartsTheNextOneAtItsNewestPage() {
        let (model, store) = make()
        seed(store, "f", 1000)
        seed(store, "g", 1000)

        model.open("f")
        model.showEarlier()
        XCTAssertEqual(900, model.transcriptPage.hidden)
        model.open("g")
        XCTAssertEqual(
            950, model.transcriptPage.hidden,
            "open carried a reading position into the next conversation"
        )

        model.showEarlier()
        model.close()
        model.open("f")
        XCTAssertEqual(
            950, model.transcriptPage.hidden,
            "close left a reading position behind for the next conversation"
        )

        model.showEarlier()
        model.newChat()
        XCTAssertNil(
            model.transcriptWindow.pinned,
            "a new chat opened with the last conversation's reading position"
        )
    }
}
