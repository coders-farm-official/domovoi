import Foundation
import XCTest
@testable import DomovoiKit

/// A link in the document works on **every** level of the manual, at every
/// text size, read off the screen's own source.
///
/// ### What was measured, and why a reading is what holds it
///
/// The action a tapped `route:`, `node:` or `doc:` link runs is installed
/// with `.environment(\.openURL, …)` on `ManualView`, which reaches that
/// view's descendants and nothing else. Answer 32 made a level a pushed
/// screen (`navigationDestination(isPresented:)`), which is **not** a
/// descendant, and train I005 measured the cost: in
/// `testManual_AnUnopenableRouteLinkIsNotALink` the openable link was tapped
/// on its own glyphs (`the rooms list`, `{{16.0, 426.0}, {103.0, 20.3}}`) on
/// a pushed piece and **nothing happened**
/// (`overnight-trains/I005/ALL/att/testManual_AnUnopenableRouteLinkIsNotALink/manual-link-landed-on-rooms_0_*.png`).
/// I005's fix installed the action again on each pushed host.
///
/// Answer 73 removed the pushed levels: every level is drawn inside
/// `ManualView`'s own body, in both of `ManualView.ready`'s branches — the
/// pinned bar below the accessibility sizes, and the one scroll view at them
/// (the `isAccessibilitySize` arm, which I005's verifier noted had always
/// been covered by `ManualView`'s install and by nothing else). So the shape
/// that keeps links working is now two facts about one file:
///
/// 1. `ManualView` installs the action on its own body;
/// 2. both branches of `ready` draw the level **inside** that body, and
///    nothing in the manual presents a level through `navigationDestination`,
///    which would put it outside again.
///
/// Nothing in this repository can run SwiftUI's environment, so what this
/// gets is a reading of the source; the behaviour is the next train's to say,
/// through the case above and its accessibility-size twin. The instrument is
/// `ShellFoldViewBodyTests.code(in:)`, which strips comments — and it matters
/// here, because `ManualView`'s doc comments quote every symbol below.
final class ManualLinkHostTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up.
    private static let manualDirectory = "ios/Domovoi/Manual/"
    private static let manualScreenPath = manualDirectory + "ManualView.swift"
    private static let manualFiles = [
        "ManualView.swift", "ManualMapView.swift", "ManualPageView.swift",
        "ManualLiveView.swift", "ManualBackSwipe.swift",
    ]

    private static func source(_ relativePath: String) -> String {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return (try? String(
            contentsOf: url.appendingPathComponent(relativePath),
            encoding: .utf8
        )) ?? ""
    }

    private static let manualScreen = ShellFoldViewBodyTests.code(
        in: ManualLinkHostTests.source(ManualLinkHostTests.manualScreenPath)
    )

    /// One top-level type's **own** source: everything after its declaration,
    /// up to the next top-level one. Every type in `ManualView.swift` is
    /// declared at column 0, which is what `"\nstruct "` bounds the span at.
    private static func ownSource(
        of typeName: String
    ) -> (span: String, declarations: Int) {
        let parts = Self.manualScreen.components(separatedBy: "struct \(typeName):")
        let span = (parts.last ?? "").components(separatedBy: "\nstruct ").first ?? ""
        return (span, parts.count - 1)
    }

    /// **The guard that stops the readings below passing vacuously.**
    func testTheManualScreenIsWhereThisFileLooksForIt() {
        XCTAssertFalse(
            Self.manualScreen.isEmpty,
            "\(Self.manualScreenPath) read as empty — the file moved or was "
                + "renamed, and until that is fixed the readings below pass on "
                + "nothing"
        )
        for file in Self.manualFiles {
            XCTAssertFalse(
                Self.source(Self.manualDirectory + file).isEmpty,
                "\(file) read as empty, so the ban below is over nothing"
            )
        }
    }

    /// **Every level, at every size, is drawn inside the view that installs
    /// the link action.**
    ///
    /// Read inside `ManualView`'s own span, so that an install written in any
    /// other type of the file does not satisfy it (I005's `sab2`/`sab3`
    /// measured a reading over the whole file staying green with the install
    /// deleted). The level must be drawn in **both** of `ready`'s branches:
    /// the accessibility-size one is the branch where a reader would most
    /// easily move the level out into a type of its own, and it is where a
    /// dead link would be seen by the fewest people.
    func testEveryLevelIsDrawnInsideTheViewThatInstallsTheLinks() {
        let route = Self.ownSource(of: "ManualView")
        XCTAssertEqual(
            route.declarations, 1,
            "`struct ManualView:` is written \(route.declarations) times in the "
                + "stripped source and this reading needs exactly one"
        )
        XCTAssertTrue(
            route.span.contains(".environment(\\.openURL, links)"),
            "`ManualView`'s own body no longer installs the link action. Every "
                + "level is drawn inside that body — both branches of "
                + "`ManualView.ready`, the pinned bar and the accessibility-size "
                + "scroll view — so without it a tapped `route:`, `node:` or "
                + "`doc:` link goes to the system's `openURL` and nowhere, at "
                + "every level and every size: what train I005 measured on a "
                + "pushed piece"
        )
        XCTAssertTrue(
            route.span.contains("ManualLevelView(doc: doc, manual: manual, scrolls: false)"),
            "the accessibility-size branch of `ManualView.ready` (the "
                + "`isAccessibilitySize` arm, bar and page in one scroll view) "
                + "no longer draws the level inside `ManualView`, so its links "
                + "are only as live as wherever it went"
        )
        XCTAssertTrue(
            route.span.contains("ManualLevelView(doc: doc, manual: manual, scrolls: true)"),
            "the ordinary-size branch of `ManualView.ready` (the pinned bar) no "
                + "longer draws the level inside `ManualView`"
        )
    }

    /// **Nothing in the manual presents a level outside its environment.**
    ///
    /// `navigationDestination` is the modifier that put a level outside
    /// `ManualView`'s environment in answer 32's build, and the one I005
    /// measured killing a link. It is banned from every manual file, so a
    /// future rebuild that pushes a level again has to come through here and
    /// install the action on what it pushes.
    func testNoLevelIsPresentedOutsideTheManualsEnvironment() {
        for file in Self.manualFiles {
            let code = ShellFoldViewBodyTests.code(in: Self.source(Self.manualDirectory + file))
            XCTAssertFalse(
                code.contains("navigationDestination"),
                "\(file) presents something through `navigationDestination`. "
                    + "A presented screen is not an environment descendant of "
                    + "`ManualView`, so a link drawn on it does nothing — train "
                    + "I005. Install `.environment(\\.openURL, …)` on it, and "
                    + "widen this ban to say so"
            )
        }
    }
}
