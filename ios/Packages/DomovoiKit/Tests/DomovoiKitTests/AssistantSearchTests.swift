import Foundation
import XCTest
@testable import DomovoiKit

/// The search over conversations kept on the phone
/// (`ConversationStore.kt:184-215`), boundary by boundary.
final class AssistantSearchTests: XCTestCase {

    private func chat(_ id: String, updated: Int64, name: String? = nil, _ texts: [String]) -> AssistantConversation {
        var out = AssistantConversation(id: id, created: 1, updated: updated)
        out.name = name
        out.messages = texts.enumerated().map {
            AssistantMessage(from: $0.offset.isMultiple(of: 2) ? .user : .domovoi, text: $0.element, at: updated)
        }
        return out
    }

    func testABlankQueryFindsNothing() {
        let all = [chat("c-1", updated: 1, ["anything at all"])]
        XCTAssertTrue(assistantSearchConversations(all, query: "").isEmpty)
        XCTAssertTrue(assistantSearchConversations(all, query: "   \n ").isEmpty)
    }

    /// The case `fixtures/README.md`'s lesson is about: the hit must be the
    /// **message**, with its snippet, and a title-only match must be
    /// distinguishable from it.
    func testAMessageMatchCarriesASnippetAndATitleMatchDoesNot() {
        let all = [
            chat("c-1", updated: 2, name: "Garden", ["when do I prune the quince"]),
            chat("c-2", updated: 1, name: "quince", ["nothing about fruit in here"]),
        ]
        let hits = assistantSearchConversations(all, query: "quince")
        XCTAssertEqual(hits.map(\.summary.id), ["c-1", "c-2"])
        XCTAssertEqual(hits[0].snippet, "when do I prune the quince")
        XCTAssertNil(hits[1].snippet, "the name matched, not a message")
    }

    func testCaseAndSpacingAreIgnored() {
        let all = [chat("c-1", updated: 1, ["The   Quince\n Tree"])]
        let hits = assistantSearchConversations(all, query: "  quince   tree ")
        XCTAssertEqual(hits.count, 1)
        XCTAssertEqual(hits[0].snippet, "The Quince Tree")
    }

    /// `asReversed()` (`ConversationStore.kt:192`): the snippet comes from the
    /// most recent place the words appear, not the first.
    func testTheSnippetIsFromTheNewestMatchingMessage() {
        let all = [chat("c-1", updated: 1, [
            "quince in the first message",
            "quince in the reply",
            "quince in the last message",
        ])]
        XCTAssertEqual(assistantSearchConversations(all, query: "quince")[0].snippet, "quince in the last message")
    }

    func testNewestFirstAndTiesBreakOnTheIdDescending() {
        let all = [
            chat("c-a", updated: 10, ["quince"]),
            chat("c-b", updated: 30, ["quince"]),
            chat("c-c", updated: 30, ["quince"]),
        ]
        XCTAssertEqual(assistantSearchConversations(all, query: "quince").map(\.summary.id), ["c-c", "c-b", "c-a"])
    }

    func testNothingMatchesNothing() {
        let all = [chat("c-1", updated: 1, ["apples"])]
        XCTAssertTrue(assistantSearchConversations(all, query: "quince").isEmpty)
    }

    // MARK: - the snippet (`matchSnippet`, `ConversationStore.kt:208`)

    func testASnippetOfAShortMessageHasNoEllipses() {
        XCTAssertEqual(assistantMatchSnippet("a quince tree", "quince"), "a quince tree")
    }

    func testFortyCharactersEitherSideAndEllipsesWhereItWasCut() throws {
        let before = String(repeating: "b", count: 60)
        let after = String(repeating: "a", count: 60)
        let snippet = try XCTUnwrap(assistantMatchSnippet("\(before) quince \(after)", "quince"))
        XCTAssertTrue(snippet.hasPrefix("\u{2026}"))
        XCTAssertTrue(snippet.hasSuffix("\u{2026}"))
        XCTAssertTrue(snippet.contains("quince"))
        // 40 either side, plus the match, plus the two ellipses — and the
        // trim that takes the space the cut landed on.
        XCTAssertEqual(snippet.count, 40 + "quince".count + 40 + 2)
    }

    func testAMatchAtTheVeryStartHasNoLeadingEllipsis() throws {
        let snippet = try XCTUnwrap(assistantMatchSnippet("quince \(String(repeating: "a", count: 60))", "quince"))
        XCTAssertFalse(snippet.hasPrefix("\u{2026}"))
        XCTAssertTrue(snippet.hasSuffix("\u{2026}"))
    }

    func testAnEmptyQueryAndAMissingWordBothAnswerNil() {
        XCTAssertNil(assistantMatchSnippet("a quince tree", ""))
        XCTAssertNil(assistantMatchSnippet("a quince tree", "apple"))
    }

    func testTheSnippetDoesNotSliceAGrapheme() throws {
        // The Kotlin counts UTF-16 units and this counts Characters; the
        // difference is deliberate (`AssistantSearch.swift`), and this is the
        // case that says what it buys: no half emoji in a snippet.
        let flag = String(repeating: "\u{1F1FA}\u{1F1F8}", count: 60)
        let snippet = try XCTUnwrap(assistantMatchSnippet("\(flag) quince", "quince"))
        XCTAssertTrue(snippet.hasPrefix("\u{2026}"), "sixty flags is past the forty-character context")
        XCTAssertTrue(snippet.contains("quince"))
        XCTAssertFalse(snippet.contains("\u{FFFD}"), "nothing was cut in half")
        // Every flag in the snippet is whole: two regional indicators each.
        for character in snippet where character.unicodeScalars
            .contains(where: { (0x1F1E6...0x1F1FF).contains($0.value) }) {
            XCTAssertEqual(character.unicodeScalars.count, 2, "a flag kept both of its halves")
        }
    }
}
