import Foundation
import XCTest
@testable import DomovoiKit

/// A link in the document works on **every** screen of the manual, read off
/// the screen's own source.
///
/// ### What was measured, and why a reading is what holds it
///
/// Answer 32 made a level of the manual a pushed screen
/// (`ManualLevelHost`). The action that a tapped `route:`, `node:` or `doc:`
/// link runs was installed with `.environment(\.openURL, …)` on `ManualView`,
/// which reaches that view's descendants — and a screen presented through
/// `navigationDestination(isPresented:)` is not one of them.
///
/// Train I005 is the measurement, and it is the first train in which a piece
/// was a pushed screen. In `testManual_AnUnopenableRouteLinkIsNotALink` the
/// openable link was tapped on its own glyphs (`the rooms list`,
/// `{{16.0, 426.0}, {103.0, 20.3}}`, tapped at its centre) and **nothing
/// happened**: the piece was still drawn, the link still underlined, the
/// crumbs unchanged
/// (`overnight-trains/I005/ALL/att/testManual_AnUnopenableRouteLinkIsNotALink/manual-link-landed-on-rooms_0_*.png`).
/// The handler cannot have run: it collapses the manual's levels before it
/// navigates (`ManualView.act(_:in:)`), so any run of it moves the screen.
/// The same case passed in trains I002 and I004, where every level was drawn
/// by the route's own screen.
///
/// Nothing in this repository can run a `NavigationStack`, let alone read
/// SwiftUI's environment, so what the fix gets here is a reading of the
/// source: the host declares the action and installs it. The behaviour itself
/// is the next train's to say, through the case above. The instrument is
/// `ShellFoldViewBodyTests.code(in:)` and the precedent is
/// ``ManualScreenHoldTests``, on the same file, for the same reason — and it
/// matters here too that comments are stripped, because `ManualView`'s doc
/// comments quote every symbol below.
final class ManualLinkHostTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — ``ManualScreenHoldTests``'
    /// hop exactly.
    private static let manualScreenPath = "ios/Domovoi/Manual/ManualView.swift"

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

    /// **The guard that stops the readings below passing vacuously.** A
    /// renamed file reads as an empty string, and a requirement over nothing
    /// is satisfied by nothing.
    func testTheManualScreenIsWhereThisFileLooksForIt() {
        XCTAssertFalse(
            Self.manualScreen.isEmpty,
            "\(Self.manualScreenPath) read as empty — the file moved or was "
                + "renamed, and until that is fixed the readings below pass on "
                + "nothing"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("struct ManualLevelHost"),
            "\(Self.manualScreenPath) has no `ManualLevelHost` in it, so the "
                + "reading below is about nothing"
        )
    }

    /// **Every screen of the manual installs the link action.**
    ///
    /// Two sayings, because one without the other is a link that still does
    /// nothing: the host has to be *given* the action (`links`) and has to
    /// put it in its own environment. Both are textual, so a reflow of either
    /// line reds this — the failure messages say what each line was for.
    func testAPushedLevelInstallsTheManualsLinkAction() {
        XCTAssertTrue(
            Self.manualScreen.contains("let links: OpenURLAction"),
            "`ManualLevelHost` no longer takes the manual's link action. "
                + "`.environment(\\.openURL, …)` on `ManualView` reaches that "
                + "screen's descendants and not a level presented through "
                + "`navigationDestination(isPresented:)`, which train I005 "
                + "measured: a `route:` link tapped on a pushed piece did "
                + "nothing at all"
        )
        XCTAssertTrue(
            Self.manualScreen.contains(".environment(\\.openURL, links)"),
            "`links` is carried and never installed, so a pushed level's page "
                + "still has whatever action the system handed it: a tapped "
                + "link goes to `openURL` and nowhere. Both sayings are "
                + "needed — see this case's note"
        )
    }

    /// **The action reaches every depth**, not only the first pushed screen.
    ///
    /// A host builds the screen above it (`deeper`), so the value has to be
    /// passed on there as well. Depth 3 — a piece opened from the search
    /// field — is where I005 measured the dead link, and it is two
    /// hand-overs deep.
    func testTheLinkActionIsPassedUpTheStack() {
        let deeper = Self.manualScreen.components(separatedBy: "depth: depth + 1")
        XCTAssertEqual(
            deeper.count, 2,
            "`ManualLevelHost` does not build the screen above it with "
                + "`depth: depth + 1` any more, so this reading cannot find "
                + "the hand-over it is about"
        )
        XCTAssertTrue(
            deeper.last?.prefix(200).contains("links: links") ?? false,
            "a host builds the screen above it without handing on the link "
                + "action, so links work at depth 1 and die at every depth "
                + "below it — which is exactly the depth train I005 measured "
                + "(a piece opened from the search field is three screens up)"
        )
    }
}
