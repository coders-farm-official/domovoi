import Foundation
import XCTest
@testable import DomovoiKit

/// All eight tools, at every boundary that moved a mark in the wrong place
/// while this was being written.
///
/// A toolbar is the one part of an editor whose bugs land **in the person's
/// own file**: a wrap that keeps the old selection applies the next tool to
/// the marks instead of the words, and a line prefix that finds the wrong
/// line heads the paragraph above the one the caret is in.
final class DocumentsMarkdownToolsTests: XCTestCase {

    private func tool(_ label: String) throws -> DocumentsMarkdownTool {
        try XCTUnwrap(DocumentsMarkdownTools.all.first { $0.label == label })
    }

    func testTheEightToolsAreAndroidsInOrder() {
        XCTAssertEqual(
            ["B", "I", "H1", "H2", "•", "1.", "`", ">"],
            DocumentsMarkdownTools.all.map(\.label)
        )
        XCTAssertEqual(
            [false, false, true, true, true, true, false, true],
            DocumentsMarkdownTools.all.map(\.linePrefix)
        )
        // Every icon-only-looking control has a word for a screen reader:
        // "B" read aloud is a letter, not a verb.
        XCTAssertTrue(DocumentsMarkdownTools.all.allSatisfy { !$0.spoken.isEmpty })
    }

    // MARK: - wrapping

    func testBoldWrapsTheSelectionAndKeepsItOnTheWords() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("B"), to: "buy a kettle", selection: 6..<12)
        XCTAssertEqual("buy a **kettle**", edit.text)
        // The selection is on `kettle`, not on the marks — so a second tool
        // wraps the word again rather than the asterisks.
        XCTAssertEqual(8..<14, edit.selection)
        XCTAssertEqual("kettle", String(Array(edit.text)[edit.selection]))
    }

    func testASecondWrapNestsAroundTheFirst() throws {
        let bold = DocumentsMarkdownTools.apply(try tool("B"), to: "buy a kettle", selection: 6..<12)
        let both = DocumentsMarkdownTools.apply(try tool("I"), to: bold.text, selection: bold.selection)
        XCTAssertEqual("buy a ***kettle***", both.text)
    }

    func testCodeAndItalicUseOneMarkEachSide() throws {
        XCTAssertEqual(
            "`ls -l`",
            DocumentsMarkdownTools.apply(try tool("`"), to: "ls -l", selection: 0..<5).text
        )
        XCTAssertEqual(
            "*ls -l*",
            DocumentsMarkdownTools.apply(try tool("I"), to: "ls -l", selection: 0..<5).text
        )
    }

    /// An empty selection is a caret: the marks go in and the caret sits
    /// between them, ready to type.
    func testAWrapWithNoSelectionLeavesTheCaretBetweenTheMarks() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("B"), to: "buy a ", selection: 6..<6)
        XCTAssertEqual("buy a ****", edit.text)
        XCTAssertEqual(8..<8, edit.selection)
    }

    // MARK: - line prefixes

    func testAHeadingPrefixesTheLineTheSelectionStartsOn() throws {
        let text = "title\nbody line\ntail"
        let edit = DocumentsMarkdownTools.apply(try tool("H1"), to: text, selection: 8..<12)
        XCTAssertEqual("title\n# body line\ntail", edit.text)
        // The caret lands where the selection ended, moved along by what was
        // inserted (`applyMdTool`: `TextRange(e + before.length)`).
        XCTAssertEqual(14..<14, edit.selection)
    }

    func testTheOtherPrefixesAreTheirMarks() throws {
        let text = "one\ntwo"
        XCTAssertEqual("one\n## two", DocumentsMarkdownTools.apply(try tool("H2"), to: text, selection: 4..<4).text)
        XCTAssertEqual("one\n- two", DocumentsMarkdownTools.apply(try tool("•"), to: text, selection: 4..<4).text)
        XCTAssertEqual("one\n1. two", DocumentsMarkdownTools.apply(try tool("1."), to: text, selection: 4..<4).text)
        XCTAssertEqual("one\n> two", DocumentsMarkdownTools.apply(try tool(">"), to: text, selection: 4..<4).text)
    }

    /// A selection spanning two lines heads **the first** one, as Android's
    /// does: the mark goes at the start of the line the selection begins on
    /// and nowhere else.
    func testASelectionAcrossLinesHeadsTheFirstOne() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("•"), to: "one\ntwo\nthree", selection: 1..<9)
        XCTAssertEqual("- one\ntwo\nthree", edit.text)
        XCTAssertEqual(11..<11, edit.selection)
    }

    func testAPrefixAtTheVeryStartHeadsTheFirstLine() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("H1"), to: "title\nbody", selection: 0..<0)
        XCTAssertEqual("# title\nbody", edit.text)
    }

    /// **The one departure from the Kotlin**, pinned so it cannot be
    /// "corrected" back: Kotlin's `lastIndexOf('\n', (s - 1).coerceAtLeast(0))`
    /// searches index 0 for a caret at 0, finds the leading newline of a
    /// document that starts with one, and heads the SECOND line from a caret
    /// on the first. A caret at 0 is the start of the first line here.
    func testACaretAtZeroHeadsTheFirstLineEvenWhenItIsEmpty() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("H1"), to: "\nbody", selection: 0..<0)
        XCTAssertEqual("# \nbody", edit.text)
    }

    func testAPrefixAfterATrailingNewlineHeadsTheEmptyLastLine() throws {
        let edit = DocumentsMarkdownTools.apply(try tool(">"), to: "one\n", selection: 4..<4)
        XCTAssertEqual("one\n> ", edit.text)
    }

    // MARK: - a selection that is not where the text is

    /// A stale caret — a keystroke the model has not seen yet — must not
    /// trap: a crash in a formatting button is a worse answer than a mark in
    /// the wrong place.
    ///
    /// Only the past-the-end case is testable, and that is the argument for
    /// taking a `Range`: a reversed selection cannot be built at all —
    /// `4..<2` traps in the **literal**, in `Range`'s own initialiser, before
    /// any of this is reached (it did, in this file, on the Mac).
    func testASelectionPastTheEndIsClamped() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("B"), to: "short", selection: 99..<120)
        XCTAssertEqual("short****", edit.text)
        let heading = DocumentsMarkdownTools.apply(try tool("H1"), to: "short", selection: 99..<120)
        XCTAssertEqual("# short", heading.text)
    }

    /// Characters, not UTF-16 units: a selection counted in code units would
    /// split an emoji and produce a string that is not what the person saw.
    func testOffsetsAreCharactersSoAnEmojiIsOneOfThem() throws {
        let edit = DocumentsMarkdownTools.apply(try tool("B"), to: "a 🏠 home", selection: 2..<3)
        XCTAssertEqual("a **🏠** home", edit.text)
    }
}
