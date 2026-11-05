import XCTest
@testable import DomovoiKit

/// The hint `ShellHintInkAppearance`'s attributed setter writes, read on a
/// machine with no Simulator.
///
/// ### The defect this exists for
///
/// Commit `ccba3f1c` made the setter ink only the runs that carried **no**
/// colour. SwiftUI hands a prompt-less single-line `TextField`'s placeholder
/// over with its own grey already on every run, so no single-line field in the
/// app was inked: train I006 read the Music search box at **1.72:1**,
/// `UIColor.placeholderText` to the integer, against I005's **7.43:1** with
/// the whole-range write. The run-wise rule fails
/// ``testARunThatArrivesColouredIsInkedToo`` and
/// ``testAMixedHintIsInkedEndToEnd``.
///
/// ### What this can and cannot catch
///
/// It catches the rule changing back to "keep a coloured run" inside
/// ``HintInk/inkingTheWholeHint(_:key:ink:)``, and the setter no longer
/// routing through that function (the source reading below). It cannot catch
/// SwiftUI changing which setter it calls, or the swap not being installed:
/// `testShell_TheHintInkIsReadable` reads the field's pixels, and that is the
/// behavioural half.
final class HintInkPlaceholderTests: XCTestCase {

    /// A stand-in key and two stand-in colours: the rule is "whatever the
    /// value under the key was, it is now the ink", so strings show it as well
    /// as `UIColor`s would, on a machine that has no `UIColor`.
    private let key = NSAttributedString.Key("HintInkPlaceholderTests.colour")
    private let applesGrey = "placeholderText"
    private let ink = "fgMuted"

    /// Every run's value under ``key``, in order, with its text.
    private func runs(_ text: NSAttributedString) -> [(String, String?)] {
        var found: [(String, String?)] = []
        text.enumerateAttribute(key, in: NSRange(location: 0, length: text.length)) { value, range, _ in
            found.append(((text.string as NSString).substring(with: range), value as? String))
        }
        return found
    }

    /// SwiftUI's shape: one hint, coloured end to end with Apple's grey. This
    /// is the case `ccba3f1c` left grey.
    func testARunThatArrivesColouredIsInkedToo() {
        let swiftUIs = NSAttributedString(
            string: "search title, artist, album, path…",
            attributes: [key: applesGrey]
        )
        let inked = HintInk.inkingTheWholeHint(swiftUIs, key: key, ink: ink)
        let found = runs(inked)
        XCTAssertEqual(
            found.map { $0.1 }, [Optional(ink)],
            "a hint that arrived in Apple's grey came back as \(found) — the run-wise "
                + "rule, which leaves every SwiftUI placeholder at 1.72:1"
        )
        XCTAssertEqual(inked.string, swiftUIs.string, "the hint's words changed")
    }

    /// A hint with one coloured run and one plain one is inked end to end.
    func testAMixedHintIsInkedEndToEnd() {
        let mixed = NSMutableAttributedString(string: "find ", attributes: [key: applesGrey])
        mixed.append(NSAttributedString(string: "a room"))
        let found = runs(HintInk.inkingTheWholeHint(mixed, key: key, ink: ink))
        XCTAssertEqual(found.map { $0.1 }, [Optional(ink)], "the mixed hint came back as \(found)")
    }

    /// The other attributes a hint carries (a font, say) survive the write.
    func testTheOtherAttributesAreKept() {
        let other = NSAttributedString.Key("HintInkPlaceholderTests.font")
        let hint = NSAttributedString(string: "message", attributes: [key: applesGrey, other: "body"])
        let inked = HintInk.inkingTheWholeHint(hint, key: key, ink: ink)
        XCTAssertEqual(inked.attribute(other, at: 0, effectiveRange: nil) as? String, "body")
        XCTAssertEqual(inked.attribute(key, at: inked.length - 1, effectiveRange: nil) as? String, ink)
    }

    // MARK: - the setter routes through the rule

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so the
    /// repository root is six directories up — the hop `AlertsStringsTests`
    /// makes.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    /// `ShellHintInkAppearance.swift` with its `//` and `///` lines dropped,
    /// so that a doc comment naming the function cannot satisfy the reading.
    private static func setterCode() -> String {
        let url = repositoryRoot.appendingPathComponent("ios/Domovoi/Shell/ShellHintInkAppearance.swift")
        let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        return text
            .split(separator: "\n", omittingEmptySubsequences: false)
            .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }
            .joined(separator: "\n")
    }

    /// The attributed setter hands on what the rule above returns, with the
    /// real key and the real ink, and does no run-wise walk of its own.
    /// Brittle about spelling on purpose, like `ShellFoldViewBodyTests`: a
    /// reformatting that fails this is a one-line edit here.
    func testTheAttributedSetterWritesThroughTheWholeHintRule() {
        let code = Self.setterCode()
        XCTAssertTrue(
            code.contains("func shellHintInkSetAttributedPlaceholder"),
            "ShellHintInkAppearance.swift read as \(code.count) characters from "
                + "\(Self.repositoryRoot.path) with no attributed setter in it — the "
                + "file moved, and every other reading here passes on nothing"
        )
        XCTAssertTrue(
            code.contains("HintInk.inkingTheWholeHint(text, key: .foregroundColor, ink: HintInk.ink)"),
            "the attributed setter no longer inks through HintInk.inkingTheWholeHint "
                + "with .foregroundColor and HintInk.ink"
        )
        XCTAssertFalse(
            code.contains("enumerateAttribute("),
            "the setter walks the hint's runs again — the shape of ccba3f1c, which "
                + "left every SwiftUI placeholder in Apple's grey"
        )
    }
}
