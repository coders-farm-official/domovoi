import XCTest
@testable import DomovoiKit

/// ``ShellBackButtonWord`` and the one body that draws its answer, read on a
/// machine with no Simulator.
///
/// The rule is two lines and the reading that matters is the call site, for
/// exactly the reason `ShellFoldViewBodyTests` exists: answer 29 was sabotaged
/// twice in view bodies with the rule left alone, and `pkgtest.sh` builds this
/// package and nothing else. So this file reads
/// `ios/Domovoi/Shell/ShellChromeViews.swift` as source text, through that
/// file's own `code(in:)` (comments and string literals stripped), and fails
/// if the modifier goes missing or stops asking the rule.
///
/// What it cannot catch: whether UIKit then draws a chevron. That is
/// `testShell_TheBackButtonDropsItsWordAtTheLargestTypeSize`, which needs a
/// train, and the limit is in
/// `design-notes/ios/batch-2026-11-03/gx-shell/REPORT.md`.
final class ShellBackButtonWordTests: XCTestCase {

    /// `ios/Domovoi/Shell/ShellChromeViews.swift`'s code, comments and string
    /// literals removed — the same reading `ShellFoldViewBodyTests` takes of
    /// the same file, through the same stripper, so the two cannot disagree
    /// about what the body says.
    private static let chrome: String = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        url = url.appendingPathComponent("ios/Domovoi/Shell/ShellChromeViews.swift")
        let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        return ShellFoldViewBodyTests.code(in: text)
    }()

    /// **The guard that stops the rest of this file passing vacuously**, the
    /// same one `ShellFoldViewBodyTests` opens with: a moved file reads as an
    /// empty string, and every `contains` below would then be false and every
    /// assertion satisfied by nothing.
    func testTheChromeBodyIsWhereThisFileLooksForIt() {
        XCTAssertFalse(
            Self.chrome.isEmpty,
            "ShellChromeViews.swift read as empty — the file moved or was "
                + "renamed, and until this is fixed every other reading here "
                + "passes on nothing"
        )
        XCTAssertTrue(Self.chrome.contains("struct ShellScrollAwayChrome"))
    }

    /// The rule itself. Two values, both spelt out, because the whole of it is
    /// which way round the negation goes.
    func testTheWordIsDrawnEverywhereButTheAccessibilitySizes() {
        XCTAssertTrue(
            ShellBackButtonWord.isDrawn(isAccessibilitySize: false),
            "the back button's word is not drawn below the accessibility "
                + "sizes — that is every screen in the app at the size almost "
                + "everybody uses, and `back to home` is the control's name there"
        )
        XCTAssertFalse(
            ShellBackButtonWord.isDrawn(isAccessibilitySize: true),
            "the back button's word is drawn at the accessibility sizes. "
                + "`UINavigationBar` caps that label's font, so it is the one "
                + "piece of text on an AX5 screen still at about 17 points — "
                + "which is what Apple's audit reported as `Text clipped` on "
                + "every pushed screen in train I004"
        )
    }

    /// The body applies the display mode, once, from the rule.
    ///
    /// Not `contains(".minimal")` alone: a body that pinned `.minimal` at
    /// every size would satisfy that and would take the word off the bar for
    /// everybody. The reading is the whole expression.
    func testTheChromeBodyAsksTheRuleForTheDisplayMode() {
        XCTAssertEqual(
            ShellFoldViewBodyTests.occurrences(
                of: "navigationBarBackButtonDisplayMode(",
                in: Self.chrome
            ).count,
            1,
            "ShellChromeViews.swift applies "
                + "`navigationBarBackButtonDisplayMode` "
                + "\(ShellFoldViewBodyTests.occurrences(of: "navigationBarBackButtonDisplayMode(", in: Self.chrome).count) "
                + "time(s), not once. One modifier, on the one chain every "
                + "shell screen goes through, is the whole of this fix"
        )
        let applied = ShellFoldViewBodyTests.arguments(
            ofCallsTo: "navigationBarBackButtonDisplayMode(",
            in: Self.chrome
        ).first
        XCTAssertNotNil(applied, "the modifier is applied with no argument this file can read")
        guard let applied else { return }
        XCTAssertTrue(
            applied.contains(
                "ShellBackButtonWord.isDrawn( isAccessibilitySize: typeSize.isAccessibilitySize )"
            ) || applied.contains(
                "ShellBackButtonWord.isDrawn(isAccessibilitySize: typeSize.isAccessibilitySize)"
            ),
            "the display mode is `\(applied)`. It must be "
                + "`ShellBackButtonWord.isDrawn(isAccessibilitySize:)` asked "
                + "with the `@Environment(\\.dynamicTypeSize)` value — a "
                + "literal, or the rule asked with a constant, is the shape "
                + "review 2's sabotage of answer 29 took twice"
        )
        XCTAssertTrue(
            applied.contains(".default") && applied.contains(".minimal"),
            "the display mode is `\(applied)` and does not name both arms. "
                + "`.default` is the word beside the chevron and `.minimal` is "
                + "the chevron alone; a body naming only one of them has "
                + "stopped choosing"
        )
    }
}
