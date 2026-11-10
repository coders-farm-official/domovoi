import XCTest
@testable import DomovoiKit

/// A room's history records every voice turn since server V043 (2026-11-09):
/// a pending one says it is waiting, a failed one says there was no answer
/// and why, an overheard one says it was ignored on purpose, and a request
/// speech recognition never made out says so with its recording's length. An
/// older server sends no status at all, and its turns read as answered.
///
/// The port of `SatTurnStatusTest.kt` (df499168, a35b5540), every Android
/// assertion kept, plus what Android's suite does not pin: each field missing,
/// null and wrongly typed, the pill for every state, and the rounding of the
/// recording's length.
///
/// No array is indexed here: a Swift trap kills the whole run.
final class RoomsTurnStatusTests: XCTestCase {

    private func decode(_ json: String) throws -> RoomsTurn {
        try JSONDecoder().decode(RoomsTurn.self, from: Data(json.utf8))
    }

    private func decodeList(_ json: String) throws -> [RoomsTurn] {
        try JSONDecoder().decode([RoomsTurn].self, from: Data(json.utf8))
    }

    // MARK: - decoding

    /// `anOlderServerReadsAsAnswered`: a row from before V043 has none of the
    /// four fields, and draws exactly as it did.
    func testAnOlderServersRowHasNoStatusAndReadsAsAnswered() throws {
        let turn = try decode("""
        {"id":3,"user_text":"hi","assistant_text":"Hello.","utterance_trigger":"wake_word"}
        """)
        XCTAssertNil(turn.status)
        XCTAssertNil(turn.noAnswerReason)
        XCTAssertNil(turn.audioMs)
        XCTAssertNil(turn.wakeScore)
        XCTAssertNil(roomsTurnUnanswered(turn), "no status is answered, so the reply is drawn")
        XCTAssertEqual("“hi”", roomsTurnSaid(turn))
        XCTAssertEqual("qa", roomsTurnPill(turn).text, "and the pill is the handler's, as before")
    }

    func testEveryNewFieldNullReadsAsAbsent() throws {
        let turn = try decode("""
        {"id":4,"user_text":"hi","assistant_text":"Hello.",
         "status":null,"no_answer_reason":null,"audio_ms":null,"wake_score":null}
        """)
        XCTAssertNil(turn.status)
        XCTAssertNil(turn.noAnswerReason)
        XCTAssertNil(turn.audioMs)
        XCTAssertNil(turn.wakeScore)
        XCTAssertNil(roomsTurnUnanswered(turn))
    }

    func testEveryNewFieldPresentIsRead() throws {
        let turn = try decode("""
        {"id":9,"user_text":"","assistant_text":null,"status":"unheard",
         "no_answer_reason":"timed_out","audio_ms":3400,"wake_score":0.871}
        """)
        XCTAssertEqual("unheard", turn.status)
        XCTAssertEqual("timed_out", turn.noAnswerReason)
        XCTAssertEqual(3400, turn.audioMs)
        XCTAssertEqual(0.871, turn.wakeScore ?? -1, accuracy: 0.0001)
    }

    /// The list is a bare array, so one row that threw would blank every turn
    /// in the room. A wrongly typed new field reads as absent instead, and
    /// the rows around it survive.
    func testAWronglyTypedNewFieldDoesNotLoseTheList() throws {
        let turns = try decodeList("""
        [{"id":1,"user_text":"a","assistant_text":"b","status":7,
          "no_answer_reason":["x"],"audio_ms":"long","wake_score":"high"},
         {"id":2,"user_text":"c","assistant_text":"d","status":"answered"}]
        """)
        XCTAssertEqual([1, 2], turns.map(\.id))
        let odd = turns.first { $0.id == 1 }
        XCTAssertNil(odd?.status)
        XCTAssertNil(odd?.noAnswerReason)
        XCTAssertNil(odd?.audioMs)
        XCTAssertNil(odd?.wakeScore)
        XCTAssertNil(odd.flatMap(roomsTurnUnanswered), "a status that could not be read is answered")
    }

    // MARK: - each state's words

    /// `anAnsweredTurnShowsItsReply`.
    func testAnAnsweredTurnShowsItsReply() {
        let turn = RoomsTurn(userText: "what time is it", assistantText: "It's noon.", status: "answered")
        XCTAssertNil(roomsTurnUnanswered(turn))
        XCTAssertEqual("“what time is it”", roomsTurnSaid(turn))
    }

    /// `pendingAndFailedTurnsSaySo`, every reason Android names.
    func testPendingAndFailedTurnsSaySo() {
        XCTAssertEqual("waiting for an answer…", roomsTurnUnanswered(RoomsTurn(status: "pending")))
        XCTAssertEqual(
            "no answer — timed out",
            roomsTurnUnanswered(RoomsTurn(status: "no_answer", noAnswerReason: "timed_out"))
        )
        XCTAssertEqual(
            "no answer — something went wrong",
            roomsTurnUnanswered(RoomsTurn(status: "no_answer", noAnswerReason: "error"))
        )
        XCTAssertEqual(
            "no answer — interrupted",
            roomsTurnUnanswered(RoomsTurn(status: "no_answer", noAnswerReason: "interrupted"))
        )
        XCTAssertEqual(
            "no answer — Domovoi restarted",
            roomsTurnUnanswered(RoomsTurn(status: "no_answer", noAnswerReason: "restarted"))
        )
        XCTAssertEqual(
            "no answer — speech recognition is off",
            roomsTurnUnanswered(RoomsTurn(status: "no_answer", noAnswerReason: "speech_off"))
        )
        XCTAssertEqual("no answer", roomsTurnUnanswered(RoomsTurn(status: "no_answer")))
    }

    /// A reason this app has never heard of is a plain `no answer` — never a
    /// blank line and never the raw word. The server's list has already grown
    /// once (V044 added `overheard` with no migration).
    func testAnUnknownReasonIsAPlainNoAnswer() {
        XCTAssertEqual(
            "no answer",
            roomsTurnUnanswered(RoomsTurn(status: "no_answer", noAnswerReason: "gremlins"))
        )
        XCTAssertEqual(
            "no answer",
            roomsTurnUnanswered(RoomsTurn(status: "unheard", noAnswerReason: "gremlins"))
        )
        XCTAssertEqual("no answer", roomsTurnPill(RoomsTurn(status: "no_answer", noAnswerReason: "gremlins")).text)
    }

    /// A status this app has never heard of reads as answered — Android's
    /// `else -> null` — so the reply the server sent is still drawn.
    func testAnUnknownStatusReadsAsAnswered() {
        let turn = RoomsTurn(matchedHandler: "lists", assistantText: "added it", status: "deferred")
        XCTAssertNil(roomsTurnUnanswered(turn))
        XCTAssertEqual("lists", roomsTurnPill(turn).text)
        XCTAssertEqual(.brand, roomsTurnPill(turn).tone)
    }

    /// `anUnheardRequestSaysItWasNotMadeOut`.
    func testAnUnheardRequestSaysItWasNotMadeOut() throws {
        let turn = try decode("""
        {"id":9,"user_text":"","assistant_text":null,"status":"unheard",
         "no_answer_reason":"timed_out","audio_ms":3400}
        """)
        XCTAssertEqual("couldn’t make out what was said · 3.4 s recording", roomsTurnSaid(turn))
        XCTAssertEqual("no answer — timed out", roomsTurnUnanswered(turn))
        XCTAssertEqual("couldn’t make out what was said", roomsTurnSaid(RoomsTurn(status: "unheard")))
        XCTAssertTrue(roomsTurnUnheard(turn))
    }

    /// `anOverheardTurnReadsIgnoredNotFailed`.
    func testAnOverheardTurnReadsIgnoredNotFailed() throws {
        let turn = try decode("""
        {"id":11,"user_text":"and then the guy says","assistant_text":null,
         "status":"no_answer","no_answer_reason":"overheard","audio_ms":30000}
        """)
        XCTAssertTrue(roomsTurnIgnored(turn))
        XCTAssertEqual("ignored — sounded like background talk", roomsTurnUnanswered(turn))
        XCTAssertFalse(roomsTurnIgnored(RoomsTurn(status: "no_answer", noAnswerReason: "timed_out")))
        XCTAssertFalse(roomsTurnIgnored(RoomsTurn(status: "answered", noAnswerReason: "overheard")))
        // The recording's length is drawn on an unheard turn only.
        XCTAssertEqual("“and then the guy says”", roomsTurnSaid(turn))
    }

    /// An answered turn with no words keeps this app's older rule: no line.
    /// An unheard one's words are `""` by definition and still get one.
    func testOnlyAnUnheardTurnDrawsAnEmptyTranscript() {
        XCTAssertNil(roomsTurnSaid(RoomsTurn(userText: "", assistantText: "ok", status: "answered")))
        XCTAssertNil(roomsTurnSaid(RoomsTurn(userText: nil, status: "pending")))
        XCTAssertNotNil(roomsTurnSaid(RoomsTurn(userText: "", status: "unheard")))
    }

    // MARK: - the pills

    /// `SatHistoryTabs.kt:170-175`, in Android's order: `ignored` is asked
    /// first, so an overheard turn — which IS a `no_answer` — wears the idle
    /// tone and not the warn tone a failure wears.
    func testEachStatesPillAndTone() {
        let ignored = roomsTurnPill(RoomsTurn(status: "no_answer", noAnswerReason: "overheard"))
        XCTAssertEqual("ignored", ignored.text)
        XCTAssertEqual(.idle, ignored.tone, "background talk is not a failure")

        let waiting = roomsTurnPill(RoomsTurn(matchedHandler: "lists", status: "pending"))
        XCTAssertEqual("waiting", waiting.text, "a pending turn's handler is not the news")
        XCTAssertEqual(.idle, waiting.tone)

        for status in ["no_answer", "unheard"] {
            let failed = roomsTurnPill(RoomsTurn(status: status, noAnswerReason: "timed_out"))
            XCTAssertEqual("no answer", failed.text, status)
            XCTAssertEqual(.warn, failed.tone, status)
        }

        let answered = roomsTurnPill(RoomsTurn(matchedHandler: "timer", status: "answered"))
        XCTAssertEqual("timer", answered.text)
        XCTAssertEqual(.brand, answered.tone)
        XCTAssertEqual("qa", roomsTurnPill(RoomsTurn(status: "answered")).text)
        XCTAssertEqual(.idle, roomsTurnPill(RoomsTurn(status: "answered")).tone)
    }

    // MARK: - the recording's length

    /// Java's `%.1f` rounds half **up**; C's rounds the binary double half to
    /// even. 3250 ms is the case where they disagree, and Android says `3.3`.
    func testTheRecordingsLengthRoundsAsAndroidDoes() {
        XCTAssertEqual("3.4", roomsTenthsOfASecond(3400))
        XCTAssertEqual("3.3", roomsTenthsOfASecond(3250))
        XCTAssertEqual("3.2", roomsTenthsOfASecond(3249))
        XCTAssertEqual("0.0", roomsTenthsOfASecond(0))
        XCTAssertEqual("0.1", roomsTenthsOfASecond(50))
        XCTAssertEqual("30.0", roomsTenthsOfASecond(30000))
        XCTAssertEqual("12.4", roomsTenthsOfASecond(12_350))
        XCTAssertEqual("12.3", roomsTenthsOfASecond(12_349))
    }

    // MARK: - the poll's rule

    /// `theListIsReadAgainOnlyWhileATurnIsPending`.
    func testTheListIsReadAgainOnlyWhileATurnIsPending() {
        XCTAssertTrue(roomsAnyPending([RoomsTurn(id: 1, status: "answered"), RoomsTurn(id: 2, status: "pending")]))
        XCTAssertFalse(roomsAnyPending([RoomsTurn(id: 1, status: "answered"), RoomsTurn(id: 2, status: "no_answer")]))
        XCTAssertFalse(roomsAnyPending([RoomsTurn(id: 1)]), "an older server's rows are never pending")
        XCTAssertFalse(roomsAnyPending([]))
        XCTAssertFalse(roomsAnyPending(nil))
        XCTAssertEqual(3_000, roomsPendingPollMs, "SAT_PENDING_POLL_MS")
    }
}
