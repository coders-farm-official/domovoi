import Foundation
import XCTest
@testable import DomovoiKit

/// The conversation kept on the phone: its title chain, its first exchange,
/// the title job's gate, the meta merge — and above all that **a file written
/// by another milestone still reads** (`ConversationStore.kt:24`'s thirty
/// defaulted fields are the whole reason the decoder is written out by hand).
final class AssistantConversationTests: XCTestCase {

    private func message(
        _ from: AssistantSpeaker,
        _ text: String,
        at: Int64 = 0,
        error: String? = nil,
        stopped: Bool = false
    ) -> AssistantMessage {
        AssistantMessage(from: from, text: text, at: at, error: error, stopped: stopped)
    }

    // MARK: - the title chain (`ConversationStore.kt:137`)

    func testTitleIsTheNameWhenThereIsOne() {
        var chat = AssistantConversation(id: "c-1", messages: [message(.user, "what is a cubit")])
        chat.name = "Measurements"
        XCTAssertEqual(chat.title, "Measurements")
    }

    func testABlankNameFallsThroughToTheFirstWords() {
        var chat = AssistantConversation(id: "c-1", messages: [message(.user, "what is a cubit exactly, in inches")])
        chat.name = "   "
        XCTAssertEqual(chat.title, "what is a cubit exactly,…")
    }

    func testTitleSkipsAnEmptyQuestionAndARepliesText() {
        let chat = AssistantConversation(id: "c-1", messages: [
            message(.user, "   "),
            message(.domovoi, "Domovoi said this first"),
            message(.user, "the first real question"),
        ])
        XCTAssertEqual(chat.title, "the first real question")
    }

    func testTitleOfAnEmptyChatIsNewChat() {
        XCTAssertEqual(AssistantConversation(id: "c-1").title, ChatStrings.newChat)
        XCTAssertEqual(AssistantConversation.newTitle, "new chat")
    }

    // MARK: - the first exchange and the title job's gate

    func testFirstExchangeSkipsAFailedAndAStoppedReply() {
        let chat = AssistantConversation(id: "c-1", messages: [
            message(.user, "how far is the moon"),
            message(.domovoi, "", error: "The model stopped: out of memory"),
            message(.domovoi, "About two hundred", stopped: true),
            message(.domovoi, "About 384,400 kilometres."),
        ])
        XCTAssertEqual(chat.firstExchange()?.question, "how far is the moon")
        XCTAssertEqual(chat.firstExchange()?.answer, "About 384,400 kilometres.")
    }

    func testFirstExchangeIsNilWithNoGoodReply() {
        let chat = AssistantConversation(id: "c-1", messages: [
            message(.user, "how far is the moon"),
            message(.domovoi, "", error: "The model stopped: out of memory"),
        ])
        XCTAssertNil(chat.firstExchange())
        XCTAssertFalse(chat.needsTitle)
    }

    func testNeedsTitleStopsAtEveryOneOfItsFourGates() {
        let answered = AssistantConversation(id: "c-1", messages: [
            message(.user, "how far is the moon"),
            message(.domovoi, "About 384,400 kilometres."),
        ])
        XCTAssertTrue(answered.needsTitle)

        var named = answered
        named.titleByUser = true
        XCTAssertFalse(named.needsTitle, "a name the person set is never written over")

        var titled = answered
        titled.titled = true
        XCTAssertFalse(titled.needsTitle, "the job does not run twice")

        var tried = answered
        tried.titleTries = AssistantPersona.titleMaxTries
        XCTAssertFalse(tried.needsTitle, "it gives up after MAX_TRIES")
    }

    // MARK: - the meta merge (`ConversationStore.kt:156`)

    func testWithMetaOfTakesTheOtherNameAndLeavesTheMessages() {
        let mine = AssistantConversation(id: "c-1", messages: [message(.user, "one"), message(.domovoi, "two")])
        var renamedMeanwhile = AssistantConversation(id: "c-1", messages: [message(.user, "one")])
        renamedMeanwhile.name = "Renamed while the model wrote"
        renamedMeanwhile.titleByUser = true
        renamedMeanwhile.archived = true

        let merged = mine.withMetaOf(renamedMeanwhile)
        XCTAssertEqual(merged.name, "Renamed while the model wrote")
        XCTAssertTrue(merged.titleByUser)
        XCTAssertTrue(merged.archived)
        XCTAssertEqual(merged.messages.count, 2, "the reply that was being written is not lost")
    }

    func testWithMetaOfIgnoresADifferentConversation() {
        let mine = AssistantConversation(id: "c-1")
        var other = AssistantConversation(id: "c-2")
        other.name = "somebody else's"
        XCTAssertNil(mine.withMetaOf(other).name)
    }

    // MARK: - decoding, tolerantly

    private func decode(_ json: String) throws -> AssistantConversation {
        try JSONDecoder().decode(AssistantConversation.self, from: Data(json.utf8))
    }

    func testAFileWithNothingButAnIdDecodes() throws {
        let chat = try decode(#"{"id":"c-1"}"#)
        XCTAssertEqual(chat.id, "c-1")
        XCTAssertTrue(chat.messages.isEmpty)
        XCTAssertEqual(chat.titleTries, 0)
        XCTAssertFalse(chat.archived)
        XCTAssertNil(chat.windowFrom)
    }

    func testAnEmptyObjectDecodes() throws {
        // The store puts the file's name back as the id, so a file with no id
        // at all still opens (`ConversationStore.kt:285`).
        XCTAssertEqual(try decode("{}").id, "")
    }

    /// The case that pays for the hand-written decoder: a conversation written
    /// by **M2 or M5**, with fields this build has never heard of and marks on
    /// its messages it does not keep.
    func testAFileFromALaterMilestoneStillReads() throws {
        let chat = try decode("""
        {"id":"c-7","created":1,"updated":2,"windowFrom":1,"internetAllowed":true,"usedInternet":true,
         "documents":[{"id":"d1","name":"lease.pdf"}],
         "messages":[{"from":"USER","text":"read this","at":3,"spoken":true,"documents":["d1"]},
                     {"from":"DOMOVOI","text":"It is a lease.","at":4,"voiceMs":120,"tools":["find_music"]}]}
        """)
        XCTAssertEqual(chat.windowFrom, 1)
        XCTAssertEqual(chat.messages.count, 2)
        XCTAssertEqual(chat.messages[1].text, "It is a lease.")
        XCTAssertEqual(chat.messages[0].from, .user)
    }

    func testAnUnknownSpeakerDecodesAsThePerson() throws {
        // Kotlin's `coerceInputValues` on an enum with a default
        // (`ConversationStore.kt:233`). The person is the safe side: a reply
        // mistaken for a question is at worst re-sent to the model, where a
        // question mistaken for a reply would make the model answer itself.
        let chat = try decode(#"{"id":"c-1","messages":[{"from":"ROBOT","text":"hm"}]}"#)
        XCTAssertEqual(chat.messages.first?.from, .user)
    }

    func testAMessageKeepsTheEightFieldsAndNoId() throws {
        let message = AssistantMessage(
            from: .domovoi, text: "there", at: 9, error: "why", stopped: true,
            firstTextMs: 40, tokensPerS: 12.5, backend: "cpu"
        )
        let blob = try JSONEncoder().encode(message)
        let json = try XCTUnwrap(String(data: blob, encoding: .utf8))
        XCTAssertFalse(json.contains("\"id\""), "the list's identity is not kept in the file")
        let back = try JSONDecoder().decode(AssistantMessage.self, from: blob)
        XCTAssertEqual(back, message, "equality is the kept fields, not the UUID")
        XCTAssertNotEqual(back.id, message.id)
    }

    func testTheSummaryRowCarriesNoSnippetAndTheMatchItIsGiven() {
        let chat = AssistantConversation(
            id: "c-1", updated: 77,
            messages: [message(.user, "a question"), message(.domovoi, "an answer")]
        )
        let row = chat.summary.row(match: "…an answer…")
        XCTAssertEqual(row.id, "c-1")
        XCTAssertEqual(row.updatedMs, 77)
        XCTAssertEqual(row.messages, 2)
        XCTAssertNil(row.snippet, "the phone keeps no last-words field")
        XCTAssertEqual(row.match, "…an answer…")
        XCTAssertFalse(row.pinned)
    }
}
