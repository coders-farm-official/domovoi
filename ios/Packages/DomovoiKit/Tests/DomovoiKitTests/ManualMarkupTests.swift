import XCTest
@testable import DomovoiKit

/// The manual's inline markup — `parseInline` (`ManualContent.kt:202`).
///
/// Every case here is a boundary of the token pattern rather than a happy
/// path: the pattern is the one piece of this port where two regular
/// expression engines had to be made to agree, and the places they disagree
/// are `\s` and `\w` (see `ManualRules.swift`'s note).
final class ManualMarkupTests: XCTestCase {

    func testPlainTextIsOneSpan() {
        XCTAssertEqual([.plain("nothing to see")], parseManualInline("nothing to see"))
        XCTAssertEqual([], parseManualInline(""))
    }

    func testTheFourKindsOfEmphasis() {
        XCTAssertEqual(
            [.plain("say "), .bold("Hey Jarvis"), .plain(" or "), .italic("anything"),
             .plain(" into "), .code("/api/intent")],
            parseManualInline("say **Hey Jarvis** or *anything* into `/api/intent`")
        )
    }

    /// `**` before `*`: the double-star pattern comes first in the token
    /// alternation, so bold is never read as two italics.
    func testBoldIsNotTwoItalics() {
        XCTAssertEqual([.bold("both")], parseManualInline("**both**"))
    }

    /// `\*[^*\s][^*]*\*` — an asterisk followed by whitespace is **not**
    /// italic, which is what keeps `2 * 3 * 4` out of the markup.
    func testAnAsteriskWithSpaceAroundItIsArithmetic() {
        XCTAssertEqual([.plain("2 * 3 * 4")], parseManualInline("2 * 3 * 4"))
    }

    /// The difference between Java's `\s` and ICU's. Java (Android) does not
    /// count a non-breaking space as whitespace, so the token matches and the
    /// text is italic; ICU does, and it would not. The pattern spells the
    /// class out so both platforms italicise it.
    func testANonBreakingSpaceAfterTheStarIsStillItalic() {
        XCTAssertEqual(
            [.italic("\u{00A0}nearly")],
            parseManualInline("*\u{00A0}nearly*"),
            "ICU's \\s would have refused this and Android's does not"
        )
    }

    func testTheThreeKindsOfLink() {
        XCTAssertEqual(
            [.link(text: "Settings", kind: "route", target: "settings/rooms")],
            parseManualInline("[Settings](route:settings/rooms)")
        )
        XCTAssertEqual(
            [.link(text: "a Pi room", kind: "node", target: "pi-room")],
            parseManualInline("[a Pi room](node:pi-room)")
        )
        XCTAssertEqual(
            [.link(text: "More", kind: "doc", target: "README.md#2-claim-the-admin-account")],
            parseManualInline("[More](doc:README.md#2-claim-the-admin-account)")
        )
    }

    /// A target of a kind the manual does not define is not a link at all —
    /// it stays the text it was written as, brackets included. A parser that
    /// accepted any scheme would make `[this](https://example.com)` a
    /// clickable link the screen has no handler for.
    func testAnUnknownLinkKindIsPlainText() {
        XCTAssertEqual(
            [.plain("[this](https://example.com)")],
            parseManualInline("[this](https://example.com)")
        )
    }

    func testThePills() {
        XCTAssertEqual(
            [.plain("needs "), .tag("admin"), .plain(" and is "), .tag("preview")],
            parseManualInline("needs {admin} and is {preview}")
        )
        XCTAssertEqual([.tag("plugin")], parseManualInline("{plugin}"))
    }

    /// `{wake}` is its own span, not a tag: the view fills it with the word
    /// the rooms actually answer to.
    func testTheWakeWordIsItsOwnSpan() {
        XCTAssertEqual(
            [.plain("say "), .wake, .plain(", then ask")],
            parseManualInline("say {wake}, then ask")
        )
    }

    /// A brace the manual does not define is text: `{list}` is a template
    /// variable of the wake-word note, not a pill, and the pattern only
    /// matches the four names.
    func testAnUnknownBraceIsPlainText() {
        XCTAssertEqual([.plain("{list} {} {ADMIN}")], parseManualInline("{list} {} {ADMIN}"))
    }

    /// The whole of a real checklist line, which is where every kind meets:
    /// bold, a pill, two links and the plain text between them.
    func testARealChecklistLineSplitsIntoItsParts() {
        let line = "**Claim the admin account** {admin}: "
            + "[Settings → System → Advanced](route:settings/advanced), **set up admin**. "
            + "[More detail](doc:README.md#2-claim-the-admin-account)"
        let spans = parseManualInline(line)
        XCTAssertEqual(
            [
                .bold("Claim the admin account"),
                .plain(" "),
                .tag("admin"),
                .plain(": "),
                .link(text: "Settings → System → Advanced", kind: "route", target: "settings/advanced"),
                .plain(", "),
                .bold("set up admin"),
                .plain(". "),
                .link(text: "More detail", kind: "doc", target: "README.md#2-claim-the-admin-account"),
            ],
            spans
        )
    }

    /// Non-ASCII text either side of a token: the scan is over UTF-16 offsets
    /// and the slices have to land on character boundaries, so a curly quote
    /// or an arrow in the text must not shift the token's edges.
    func testAnEmojiAndAnArrowDoNotShiftTheSpans() {
        XCTAssertEqual(
            [.plain("\u{1F3E0} Settings \u{2192} "), .bold("Rooms"), .plain(" \u{201C}ok\u{201D}")],
            parseManualInline("\u{1F3E0} Settings \u{2192} **Rooms** \u{201C}ok\u{201D}")
        )
    }
}
