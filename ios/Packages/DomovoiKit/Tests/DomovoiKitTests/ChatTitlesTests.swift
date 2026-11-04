import Foundation
import XCTest
@testable import DomovoiKit

/// ``ChatTitles/firstWords(_:)`` against `ChatTitles.kt:63`, which is the
/// third link of ``ChatModel/openTitle``'s chain: what the header says for a
/// thread the server has not named yet.
final class ChatTitlesTests: XCTestCase {

    func testTheFirstFiveWordsAndNoMore() {
        XCTAssertEqual("how long do lentils take", ChatTitles.firstWords("how long do lentils take"))
        XCTAssertEqual(
            "how long do lentils take\u{2026}",
            ChatTitles.firstWords("how long do lentils take to cook?"),
            "five words and an ellipsis, U+2026, because there were more"
        )
        XCTAssertEqual("one", ChatTitles.firstWords("one"))
    }

    func testNoWordsAtAllIsNilRatherThanEmpty() {
        XCTAssertNil(ChatTitles.firstWords(nil))
        XCTAssertNil(ChatTitles.firstWords(""))
        XCTAssertNil(ChatTitles.firstWords("   \n\t  "), "whitespace is not a word")
        XCTAssertNil(ChatTitles.firstWords("\u{200b}"), "and a zero-width mark is not one either")
    }

    func testSpacingIsFlattenedAndControlsBecomeSpaces() {
        XCTAssertEqual("a b c", ChatTitles.firstWords("  a\t\tb \n c  "))
    }

    /// **A title cannot reorder or hide what the list draws around it**
    /// (`ChatTitles.kt:42`). A bidi override in the first words of a message
    /// is what `plainText` is for: format characters go rather than becoming
    /// spaces, so the words either side are not split.
    func testFormatCharactersAreRemovedAndDoNotSplitAWord() {
        XCTAssertEqual("lentils", ChatTitles.firstWords("len\u{202e}tils"))
        XCTAssertEqual("ab", ChatTitles.firstWords("a\u{200b}b"))
    }

    /// Past ``ChatTitles/maxChars`` the title is cut and marked cut, even when
    /// it was five words or fewer.
    func testAVeryLongWordIsCutAndSaysSo() {
        let long = String(repeating: "x", count: 70)
        let title = ChatTitles.firstWords(long)
        XCTAssertEqual(ChatTitles.maxChars + 1, title?.count, "60 characters and the ellipsis")
        XCTAssertEqual(true, title?.hasSuffix("\u{2026}"))
    }

    func testTrailingSpaceIsNotLeftBeforeTheEllipsis() {
        // Four words of fourteen and three spaces is 59 characters, so the
        // 60th is the space before the fifth word: the cut lands **on** a
        // space, and the trim is what keeps it out of the ellipsis.
        let text = ["a", "b", "c", "d"].map { String(repeating: $0, count: 14) }.joined(separator: " ")
            + " " + String(repeating: "e", count: 5)
        let title = ChatTitles.firstWords(text)
        XCTAssertEqual(59 + 1, title?.count, "59 characters and the ellipsis, with no space between")
        XCTAssertEqual(false, title?.contains(" \u{2026}"))
        XCTAssertEqual(true, title?.hasSuffix("\u{2026}"))
    }
}
