import Foundation
import XCTest

/// **A filled button's label colour is its style's, never a site's.**
///
/// Train I011's forget-server card drew `forget` white on amber, **2.05:1**
/// — `(255,255,255)` on `(242,166,24)` in its screenshot — because it was a
/// `.borderedProminent` with `.tint(Color.brand)` and no label colour, and a
/// prominent button draws its label white unless a site names another. The
/// comment beside it said the pair was 8.49:1 "with this label colour"; there
/// was none. Apple's audit had named it on every train from I004, and the UI
/// case that audits the card excused its own findings (unit ix-fill,
/// `testShell_ADialogLeavesNothingBehindIt`).
///
/// It was one of a class. Every `.borderedProminent` in `ios/Domovoi` relied
/// on a second modifier a site could leave out — S007's `subscribe` and
/// S010's `search` had left it out before — and five named no tint at all, so
/// they filled with the app's `brandInk` and drew white on it, **1.91:1** in
/// dark mode. Disabled, the style draws grey on grey (1.47:1 measured on
/// Images' `generate`, S004–S006). So the rule is structural rather than
/// "every prominent button is followed by a colour":
///
/// 1. **No `.borderedProminent` in the app at all.** A filled button takes
///    `BrandFillButtonStyle` or `DangerFillButtonStyle`
///    (`ios/Domovoi/Shell/InkButtonStyles.swift`), which draw the fill and its
///    text as one pair and have a disabled look with no fill.
/// 2. **Those two styles pass the pair**, `brand`/`brandFg` and
///    `danger`/`dangerFg` — the pairs whose ratios are measured (8.50:1 and
///    9.92:1; 5.91:1 and 6.53:1, the latter recomputed by `ShellFoldTests`).
/// 3. **No site names a colour inside a fill-styled button's label.** A
///    `.foregroundStyle` on the label's own view wins over the one the style
///    sets, which is how a fill comes to carry a colour nobody measured
///    against it.
///
/// `ios/Domovoi/` is compiled only by the app target, which nothing in this
/// package builds, so this is a reading of the source with comments and
/// string literals stripped (`ShellFoldViewBodyTests.code(in:)`): a doc
/// comment that quotes `.borderedProminent` — and several do, as history —
/// cannot trip it. Whether the pixels are right is the UI case's to say; this
/// is what stops the shape coming back between trains.
final class FilledButtonLabelTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static let appDirectory = "ios/Domovoi"
    /// Matched as a suffix: the walk's URLs and `#filePath` may spell the
    /// same root differently (a resolved symlink), and then a path is not
    /// shortened to the root-relative one.
    private static let stylesPath = "ios/Domovoi/Shell/InkButtonStyles.swift"

    /// Every Swift file of the app target, as (path from the repository root,
    /// code with comments and string literals stripped).
    private static let appSources: [(path: String, code: String)] = {
        let root = repositoryRoot.appendingPathComponent(appDirectory)
        guard let walk = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) else { return [] }
        var found: [(path: String, code: String)] = []
        for case let url as URL in walk where url.pathExtension == "swift" {
            let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            let relative = url.path.replacingOccurrences(
                of: repositoryRoot.path + "/",
                with: ""
            )
            found.append((relative, ShellFoldViewBodyTests.code(in: text)))
        }
        return found.sorted { $0.path < $1.path }
    }()

    /// The spellings of a system style that fills the button with its tint
    /// and picks the label colour itself.
    private static let systemFillStyles = [
        ".borderedProminent",
        "BorderedProminentButtonStyle",
        ".glassProminent",
        "GlassProminentButtonStyle",
    ]

    /// The two styles that may fill a button.
    private static let fillStyles = ["BrandFillButtonStyle()", "DangerFillButtonStyle()"]

    /// **The guard that stops every reading below passing on nothing.** The
    /// app has well over a hundred Swift files and two dozen filled buttons;
    /// a walk that found a handful read the wrong directory.
    func testTheWalkReadsTheApp() {
        XCTAssertGreaterThan(
            Self.appSources.count,
            100,
            "read \(Self.appSources.count) Swift files under "
                + "\(Self.repositoryRoot.appendingPathComponent(Self.appDirectory).path) "
                + "— the directory moved, and every reading here passes on nothing"
        )
        let brand = Self.appSources.reduce(0) { $0 + Self.count(of: "BrandFillButtonStyle()", in: $1.code) }
        let danger = Self.appSources.reduce(0) { $0 + Self.count(of: "DangerFillButtonStyle()", in: $1.code) }
        XCTAssertGreaterThanOrEqual(
            brand,
            20,
            "found \(brand) `BrandFillButtonStyle()` call sites, where unit ix-fill "
                + "left 33 — a filled button went back to something else, or the "
                + "comment stripper is eating code"
        )
        XCTAssertGreaterThanOrEqual(
            danger,
            2,
            "found \(danger) `DangerFillButtonStyle()` call sites, where the drop-in "
                + "`leave` and the forget card's `forget` both take it"
        )
    }

    /// Rule 1: no system prominent style anywhere in the app's code.
    func testNoButtonIsFilledByASystemProminentStyle() {
        var offenders: [String] = []
        for (path, code) in Self.appSources {
            for spelling in Self.systemFillStyles {
                for line in Self.lines(of: spelling, in: code) {
                    offenders.append("\(path):\(line) \(spelling)")
                }
            }
        }
        XCTAssertEqual(
            offenders,
            [],
            "a system prominent button style draws its label WHITE unless the site "
                + "names a colour (2.05:1 on the amber, 1.91:1 on the dark ink), and "
                + "grey on grey while disabled (1.47:1). Use BrandFillButtonStyle() or "
                + "DangerFillButtonStyle() from InkButtonStyles.swift, which draw the "
                + "fill and its text as one pair: \(offenders)"
        )
    }

    /// Rule 2: the two fill styles hand the label view a measured pair.
    func testTheFillStylesPassTheirMeasuredPairs() {
        let styles = Self.appSources.first { $0.path.hasSuffix(Self.stylesPath) }?.code ?? ""
        XCTAssertFalse(
            styles.isEmpty,
            "\(Self.stylesPath) is not among the files read — it moved, and the "
                + "pairs below are read from nothing"
        )
        for pair in [
            "FillButtonLabel(configuration: configuration, fill: .brand, text: .brandFg)",
            "FillButtonLabel(configuration: configuration, fill: .danger, text: .dangerFg)",
        ] {
            XCTAssertEqual(
                Self.count(of: pair, in: styles),
                1,
                "InkButtonStyles.swift no longer builds `\(pair)` exactly once: a fill "
                    + "style is pairing its fill with a text colour nobody measured "
                    + "(brandFg on brand is 8.50:1, dangerFg on danger 5.91:1)"
            )
        }
        // And the label view draws the text it was handed, on the fill it was
        // handed — not a constant of its own.
        for drawn in [
            ".foregroundStyle(isEnabled ? text : Color.fgMuted)",
            ".fill(isEnabled ? fill : Color.clear)",
        ] {
            XCTAssertEqual(
                Self.count(of: drawn, in: styles),
                1,
                "`FillButtonLabel` no longer draws `\(drawn)`, so the pair its "
                    + "two styles pass is not the pair on the screen"
            )
        }
    }

    /// Rule 3: nothing between a `Button` and its fill style names a colour.
    ///
    /// The span read is from the nearest `Button` before the
    /// `.buttonStyle(…FillButtonStyle())` to the style itself: the button's
    /// title or label builder, plus its action. A `.foregroundStyle` there is
    /// on the label's own view and beats the style's.
    func testNoFillStyledLabelNamesItsOwnColour() {
        var offenders: [String] = []
        var sites = 0
        for (path, code) in Self.appSources {
            for style in Self.fillStyles {
                var from = code.startIndex
                while let found = code.range(of: ".buttonStyle(\(style))", range: from..<code.endIndex) {
                    from = found.upperBound
                    sites += 1
                    guard let start = Self.lastButton(before: found.lowerBound, in: code) else {
                        offenders.append("\(path):\(Self.line(at: found.lowerBound, in: code)) no `Button` before \(style)")
                        continue
                    }
                    let span = code[start..<found.lowerBound]
                    for colour in [".foregroundStyle(", ".foregroundColor("] where span.contains(colour) {
                        offenders.append(
                            "\(path):\(Self.line(at: found.lowerBound, in: code)) \(colour) inside a \(style) label"
                        )
                    }
                }
            }
        }
        XCTAssertGreaterThan(sites, 0, "no fill-styled button was found to read")
        XCTAssertEqual(
            offenders,
            [],
            "a colour named on a fill-styled button's own label wins over the "
                + "style's, so the fill carries a text colour nobody measured against "
                + "it. Take the colour off the label: \(offenders)"
        )
    }

    // MARK: - reading the source

    private static func count(of needle: String, in code: String) -> Int {
        lines(of: needle, in: code).count
    }

    /// The 1-based line of every occurrence of `needle`.
    private static func lines(of needle: String, in code: String) -> [Int] {
        guard !needle.isEmpty else { return [] }
        var found: [Int] = []
        var from = code.startIndex
        while let range = code.range(of: needle, range: from..<code.endIndex) {
            found.append(line(at: range.lowerBound, in: code))
            from = range.upperBound
        }
        return found
    }

    /// The line an index is on. `code(in:)` keeps every newline, so this is
    /// the line in the file.
    private static func line(at index: String.Index, in code: String) -> Int {
        code[code.startIndex..<index].reduce(1) { $1 == "\n" ? $0 + 1 : $0 }
    }

    /// The start of the last `Button` before `limit` that is the type itself
    /// and not the tail of another name (`SaveToDeviceButton`), followed by
    /// its title or its builder.
    private static func lastButton(before limit: String.Index, in code: String) -> String.Index? {
        var cursor = limit
        while let found = code.range(of: "Button", options: .backwards, range: code.startIndex..<cursor) {
            cursor = found.lowerBound
            let before = found.lowerBound > code.startIndex
                ? code[code.index(before: found.lowerBound)]
                : " "
            if before.isLetter || before.isNumber || before == "_" { continue }
            let after = code[found.upperBound...].first { !$0.isWhitespace }
            if after == "(" || after == "{" { return found.lowerBound }
        }
        return nil
    }
}
