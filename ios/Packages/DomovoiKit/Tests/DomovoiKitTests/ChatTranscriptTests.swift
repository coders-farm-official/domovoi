import Foundation
import XCTest
@testable import DomovoiKit

/// The conversation and the stream that writes into it, with no socket: the
/// reader is tested in `ServerEventsTests`, and this is what the events *mean*.
///
/// Android has no test for any of this — `ChatScreen.kt` is a composable and
/// its JVM suite is unit-only — so the retry's two failure shapes, the `done`
/// row replacing the stream's guess, and the ignored event names are specified
/// by nothing over there.
final class ChatTranscriptTests: XCTestCase {

    private let now = "2026-11-02T18:00:00Z"
    private let later = "2026-11-02T18:00:09Z"

    private func event(_ name: String, _ data: String) -> ServerEvent {
        ServerEvent(name: name, data: data)
    }

    /// A thread that ends in a retryable reply, which is the state most of
    /// these start from.
    private func answered() -> ChatTranscript {
        var transcript = ChatTranscript()
        transcript.load(
            [
                ChatMessage(id: 1, role: "user", content: "how long?"),
                ChatMessage(id: 2, role: "assistant", content: "ten minutes", retryable: true),
            ],
            thisDeviceID: "ios-1"
        )
        return transcript
    }

    // MARK: - loading

    func testLoadingAThreadKeepsTheRowsInOrderAndNamesThisPhone() {
        var transcript = ChatTranscript()
        XCTAssertTrue(transcript.load(
            [
                ChatMessage(id: 1, role: "user", content: "hi", deviceID: "ios-1", deviceName: "my iPhone"),
                ChatMessage(id: 2, role: "user", content: "hi", deviceID: "android-7", deviceName: "kitchen tablet"),
                ChatMessage(id: 3, role: "user", content: "hi", deviceID: "unknown-9"),
            ],
            thisDeviceID: "ios-1"
        ))
        XCTAssertEqual(
            [ChatStrings.thisPhone, "kitchen tablet", "unknown-9"],
            transcript.messages.map { $0.device ?? "" }
        )
        XCTAssertEqual([1, 2, 3], transcript.messages.compactMap(\.id))
        XCTAssertEqual([0, 1, 2], transcript.messages.map(\.ordinal))
    }

    /// A phone with no device id of its own attributes nothing to itself —
    /// otherwise every message in the house would read "this phone".
    func testAPhoneWithNoDeviceIdClaimsNothing() {
        var transcript = ChatTranscript()
        transcript.load([ChatMessage(id: 1, role: "user", deviceID: "android-7")], thisDeviceID: nil)
        XCTAssertEqual("android-7", transcript.messages.first?.device)
        transcript = ChatTranscript()
        transcript.load([ChatMessage(id: 1, role: "user", deviceID: "")], thisDeviceID: "")
        XCTAssertNotEqual(ChatStrings.thisPhone, transcript.messages.first?.device)
    }

    /// **A load during a stream is refused.** It would take the reply being
    /// written away mid-sentence, and `chat.changed` arrives on every message
    /// — including the one this phone is in the middle of sending.
    func testALoadDuringAStreamIsRefused() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        XCTAssertFalse(transcript.load([ChatMessage(id: 9, role: "user")], thisDeviceID: nil))
        XCTAssertEqual(["hi", ""], transcript.messages.map(\.content))
        XCTAssertTrue(transcript.streaming)
    }

    // MARK: - sending

    func testSendingPutsTheWordsUpAndAPendingReplyUnderThem() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "how long?", atISO: now)
        XCTAssertEqual(2, transcript.messages.count)
        XCTAssertEqual("user", transcript.messages[0].role)
        XCTAssertEqual(now, transcript.messages[0].createdAt, "stamped here: the server sends no row back")
        XCTAssertEqual(ChatStrings.thisPhone, transcript.messages[0].device)
        XCTAssertNil(transcript.messages[0].id, "a person's turn has no row id")
        XCTAssertTrue(transcript.messages[1].pending)
        XCTAssertEqual(1, transcript.liveIndex)
    }

    func testDeltasAccumulateIntoTheLiveReply() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        XCTAssertEqual(.delta("ten "), transcript.apply(event("delta", #"{"text":"ten "}"#)))
        XCTAssertEqual(.delta("minutes"), transcript.apply(event("delta", #"{"text":"minutes"}"#)))
        XCTAssertEqual("ten minutes", transcript.live?.content)
    }

    /// **The `done` row replaces what streamed.**
    ///
    /// It is not a nicety: the row carries the real id, the model and
    /// `retryable`, and for a reply the server checked before keeping it the
    /// row and not the stream is what the reply says. A live reply left as the
    /// stream's guess also has no id, so nothing could be retried or shared.
    func testTheDoneRowReplacesTheStreamsGuess() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        transcript.apply(event("delta", #"{"text":"ten minutes maybe"}"#))
        let step = transcript.apply(event("done", """
        {"id":7,"role":"assistant","content":"ten minutes","model":"llama3.2:latest",
         "created_at":"2026-11-02T18:00:08Z","retryable":true}
        """))
        XCTAssertEqual(.finished(kept: false, replaces: nil), step)
        let reply = try? XCTUnwrap(transcript.messages.last)
        XCTAssertEqual("ten minutes", reply?.content, "the kept text, not the streamed one")
        XCTAssertEqual(7, reply?.id)
        XCTAssertEqual("llama3.2:latest", reply?.model)
        XCTAssertEqual("2026-11-02T18:00:08Z", reply?.createdAt)
        XCTAssertEqual(true, reply?.retryable)
        XCTAssertFalse(reply?.pending ?? true)
        XCTAssertFalse(transcript.streaming)
        XCTAssertTrue(transcript.canRetry)
    }

    /// A `done` row that carries an error **keeps the words already on
    /// screen**: the row's `content` is empty for a reply that failed
    /// half-written, and taking the words away would leave an error with
    /// nothing above it.
    func testADoneRowWithAnErrorKeepsTheWordsThatWereWritten() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        transcript.apply(event("delta", #"{"text":"half a sent"}"#))
        transcript.apply(event("done", """
        {"id":7,"role":"assistant","content":"","error":"the model stopped",
         "stats":{"error_code":"model_memory"}}
        """))
        XCTAssertEqual("half a sent", transcript.messages.last?.content)
        XCTAssertEqual("the model stopped", transcript.messages.last?.error)
        XCTAssertEqual("model_memory", transcript.messages.last?.errorCode)
    }

    /// The `error` event's three fields, and the one that must **not** blank
    /// what a done row gave.
    func testAnErrorEventCarriesItsDetailCodeAndModel() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        let step = transcript.apply(event("error", """
        {"detail":"Pictures need a vision model","code":"vision_model_missing","model":"llava:7b"}
        """))
        XCTAssertEqual(
            .failed(detail: "Pictures need a vision model", code: "vision_model_missing", model: "llava:7b"),
            step
        )
        XCTAssertEqual("vision_model_missing", transcript.live?.errorCode)
        XCTAssertEqual("llava:7b", transcript.live?.model)

        // A second error that names no model leaves the model alone.
        transcript.apply(event("error", #"{"detail":"and then this"}"#))
        XCTAssertEqual("llava:7b", transcript.live?.model)
        XCTAssertNil(transcript.live?.errorCode, "a code the server did not send is not kept from before")
    }

    /// An `error` event with no `detail` is unreadable rather than a reply
    /// that says nothing went wrong.
    func testAnErrorEventWithNoDetailIsUnreadable() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        XCTAssertEqual(.unreadable("error"), transcript.apply(event("error", #"{"code":"x"}"#)))
        XCTAssertNil(transcript.live?.error)
    }

    /// **The events this app does not read, ignored by name.**
    ///
    /// `replace`, `job` and `progress` are in the map; `status` and
    /// `lookup_permission` are not and are in the server (`chat.py:1431`,
    /// `:1862`). An event that ended the reply because this build had no case
    /// for it would be the worst failure this screen has.
    func testTheEventsThisAppDoesNotReadAreIgnoredAndDoNotEndTheReply() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        transcript.apply(event("delta", #"{"text":"ten"}"#))
        for name in ["replace", "job", "progress", "status", "lookup_permission", "message", "nonsense"] {
            XCTAssertEqual(.ignored(name), transcript.apply(event(name, #"{"anything":true}"#)), name)
        }
        XCTAssertEqual("ten", transcript.live?.content, "nothing was written or taken away")
        XCTAssertTrue(transcript.streaming, "and the reply is still being written")
    }

    /// A `done` body this build cannot read leaves the words on screen and the
    /// reply pending — the stream carries on, and ``ChatTranscript/finishStream(atISO:)``
    /// settles it. Half a reply beats none.
    func testADoneBodyThatWillNotDecodeDoesNotThrowTheWordsAway() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "hi", atISO: now)
        transcript.apply(event("delta", #"{"text":"ten minutes"}"#))
        XCTAssertEqual(.unreadable("done"), transcript.apply(event("done", "<html>a portal</html>")))
        XCTAssertEqual("ten minutes", transcript.live?.content)
        XCTAssertEqual(.unfinished(nil), transcript.finishStream(atISO: later))
        XCTAssertEqual("ten minutes", transcript.messages.last?.content)
        XCTAssertEqual(later, transcript.messages.last?.createdAt, "stamped so the bubble has a time")
        XCTAssertFalse(transcript.messages.last?.pending ?? true)
    }

    /// An event arriving after the stream was settled writes into nothing.
    func testAnEventAfterTheStreamIsSettledIsIgnored() {
        var transcript = answered()
        XCTAssertEqual(.ignored("delta"), transcript.apply(event("delta", #"{"text":"late"}"#)))
        XCTAssertEqual(["how long?", "ten minutes"], transcript.messages.map(\.content))
    }

    // MARK: - try again

    func testARetryTakesTheOldReplyOutAndPutsAPendingOneIn() {
        var transcript = answered()
        XCTAssertTrue(transcript.beginRetry())
        XCTAssertEqual(2, transcript.messages.count)
        XCTAssertTrue(transcript.messages[1].pending)
        XCTAssertEqual("", transcript.messages[1].content)
        XCTAssertTrue(transcript.retrying)
        XCTAssertFalse(transcript.beginRetry(), "one at a time")
    }

    func testARetryIsRefusedWhereCanRetryRefusesIt() {
        var nothing = ChatTranscript()
        XCTAssertFalse(nothing.beginRetry())
        var notRetryable = ChatTranscript()
        notRetryable.load(
            [
                ChatMessage(id: 1, role: "user", content: "set a timer"),
                ChatMessage(id: 2, role: "assistant", content: "Timer set.", retryable: false),
            ],
            thisDeviceID: nil
        )
        XCTAssertFalse(notRetryable.beginRetry())
    }

    func testASuccessfulRetryReplacesTheReplyAndSaysWhichOne() {
        var transcript = answered()
        transcript.beginRetry()
        transcript.apply(event("delta", #"{"text":"twenty "}"#))
        let step = transcript.apply(event("done", """
        {"id":9,"role":"assistant","content":"twenty minutes","retryable":true,"replaces":2}
        """))
        XCTAssertEqual(.finished(kept: false, replaces: 2), step)
        XCTAssertEqual(["how long?", "twenty minutes"], transcript.messages.map(\.content))
        XCTAssertEqual(9, transcript.messages.last?.id)
        XCTAssertFalse(transcript.retrying)
        XCTAssertEqual(.finished, transcript.finishStream(atISO: later))
    }

    /// **A retry that failed puts the old reply back** — and the row the server
    /// sent is what goes back, not a remembered copy.
    ///
    /// `chat.py:1641`: a failed retry writes nothing, and the stream ends with
    /// `error` then a `done` carrying the reply that was **kept**. Android
    /// shows the live reply dressed up as the kept one and then re-reads the
    /// whole thread to correct itself (`ChatScreen.kt:880`); this is the same
    /// end state without the extra read and without the moment in between
    /// where the screen shows a reply the server does not have.
    func testAFailedRetryPutsTheKeptReplyBackAndSaysWhyOnce() {
        var transcript = answered()
        transcript.beginRetry()
        transcript.apply(event("delta", #"{"text":"twen"}"#))
        transcript.apply(event("error", #"{"detail":"the model is not installed","code":"model_missing"}"#))
        let step = transcript.apply(event("done", """
        {"id":2,"role":"assistant","content":"ten minutes","retryable":true,"kept":true}
        """))
        XCTAssertEqual(.finished(kept: true, replaces: nil), step)
        XCTAssertEqual(["how long?", "ten minutes"], transcript.messages.map(\.content))
        XCTAssertEqual(2, transcript.messages.last?.id, "the id the thread still has")
        XCTAssertNil(transcript.messages.last?.error, "the old reply did not fail; the retry did")
        XCTAssertFalse(transcript.messages.last?.pending ?? true)
        XCTAssertFalse(transcript.retrying)
        XCTAssertTrue(transcript.canRetry, "and it can be tried again")
        XCTAssertEqual(.finished, transcript.finishStream(atISO: later))
    }

    /// **A retry can end with no `done` at all.**
    ///
    /// The `already_retried` branch sends `error` and closes (`chat.py:1656`).
    /// The old reply is still the thread's, so it comes back; without this the
    /// transcript would keep a pending reply that is never coming, and the
    /// person would be left with a spinner where their answer was.
    func testARetryThatEndsWithNoDoneAtAllPutsTheOldReplyBack() {
        var transcript = answered()
        transcript.beginRetry()
        transcript.apply(event("error", """
        {"detail":"this reply was already tried again","code":"already_retried"}
        """))
        XCTAssertEqual(
            .keptOldReply("this reply was already tried again"),
            transcript.finishStream(atISO: later)
        )
        XCTAssertEqual(["how long?", "ten minutes"], transcript.messages.map(\.content))
        XCTAssertEqual(2, transcript.messages.last?.id)
        XCTAssertNil(transcript.messages.last?.error)
        XCTAssertFalse(transcript.streaming)
    }

    /// A retry whose request never reached the server at all: the same
    /// restoration, with this phone's own words for the failure.
    func testARetryThatNeverLeftThePhonePutsTheOldReplyBack() {
        var transcript = answered()
        transcript.beginRetry()
        transcript.failLive("try again failed (offline?)")
        XCTAssertEqual(.keptOldReply("try again failed (offline?)"), transcript.finishStream(atISO: later))
        XCTAssertEqual(["how long?", "ten minutes"], transcript.messages.map(\.content))
    }

    // MARK: - a send that failed

    /// A send that never reached the server shows why **where the reply would
    /// have been** (`ChatScreen.kt:773`), and the person's own words stay on
    /// screen.
    func testASendThatNeverLeftThePhoneSaysSoWhereTheReplyWouldBe() {
        var transcript = ChatTranscript()
        transcript.beginSend(content: "how long?", atISO: now)
        transcript.failLive("send failed (offline?)")
        XCTAssertEqual(.unfinished("send failed (offline?)"), transcript.finishStream(atISO: later))
        XCTAssertEqual("how long?", transcript.messages.first?.content)
        XCTAssertEqual("send failed (offline?)", transcript.messages.last?.error)
        XCTAssertFalse(transcript.messages.last?.pending ?? true)
        XCTAssertFalse(transcript.canRetry, "a reply that was never written is not retryable")
    }

    /// ``ChatTranscript/finishStream(atISO:)`` with nothing running says so
    /// rather than writing into the last message.
    func testFinishingWithNothingRunningDoesNothing() {
        var transcript = answered()
        XCTAssertEqual(.nothingRunning, transcript.finishStream(atISO: later))
        XCTAssertEqual(["how long?", "ten minutes"], transcript.messages.map(\.content))
    }

    // MARK: - the divider, from the transcript

    func testTheTranscriptSaysWhereItsDividerGoes() {
        var transcript = ChatTranscript()
        transcript.load(
            [
                ChatMessage(id: 1, role: "user", content: "a"),
                ChatMessage(id: 2, role: "assistant", content: "b"),
                ChatMessage(id: 3, role: "user", content: "c"),
                ChatMessage(id: 4, role: "assistant", content: "d", stats: ChatStats(windowFromId: 3)),
            ],
            thisDeviceID: nil
        )
        XCTAssertEqual(2, transcript.windowDividerIndex)

        var whole = ChatTranscript()
        whole.load(
            [
                ChatMessage(id: 1, role: "user", content: "a"),
                ChatMessage(id: 2, role: "assistant", content: "b", stats: ChatStats(windowFromId: 1)),
            ],
            thisDeviceID: nil
        )
        XCTAssertNil(whole.windowDividerIndex, "the model saw the whole thread")
    }

    // MARK: - identity

    /// The ordinal is the list's identity and is never reused, so a pending
    /// reply keeps its own row across every token rather than being
    /// re-created — which would re-animate it on each delta.
    func testEveryMessageGetsItsOwnOrdinalAndNoneIsReused() {
        var transcript = answered()
        transcript.beginSend(content: "and again?", atISO: now)
        transcript.apply(event("done", #"{"id":5,"role":"assistant","content":"yes","retryable":true}"#))
        transcript.beginRetry()
        let ordinals = transcript.messages.map(\.ordinal)
        XCTAssertEqual(ordinals.count, Set(ordinals).count, "no two rows share an identity")
        XCTAssertEqual(ordinals, ordinals.sorted(), "and they are in list order")
    }

    func testClearingForgetsEverythingIncludingAStashedReply() {
        var transcript = answered()
        transcript.beginRetry()
        transcript.clear()
        XCTAssertTrue(transcript.isEmpty)
        XCTAssertFalse(transcript.streaming)
        XCTAssertFalse(transcript.retrying)
    }
}
