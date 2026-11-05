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
/// The handler cannot have run **past its guard**: once it has a link and a
/// model it collapses the manual's levels before it navigates
/// (`ManualView.act(_:in:)`), so a run that gets that far moves the screen.
/// Two branches do move nothing — the first line's
/// `guard let link = manualLink(of: url), let manual else { return .systemAction }`
/// (`ManualView.swift:221`) and `act`'s `.nothing` arm (`:475`) — and neither
/// fits: this link's URL is one this file minted and parses, and `route:` with
/// a target is not `.nothing`. The same case passed in trains I002 and I004
/// with that same minting and parsing code, where every level was drawn by the
/// route's own screen; the push is the only thing that changed.
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
    ///
    /// ### Each saying is read in the host's **own** source, and that is the
    /// whole case
    ///
    /// `.environment(\.openURL, links)` is written twice in this file:
    /// `ManualView`'s own body, which was never the bug, and
    /// ``ManualLevelHost``'s, which is the fix. A `contains` over the whole
    /// stripped source cannot tell them apart, and it was measured not to:
    /// on throwaway branch `wf/ios-b-hx-manual-sab1` the three lines that
    /// install the action on the host were deleted — the I005 defect
    /// restored — and all three cases in this file passed, `PKGTEST exit 0`.
    /// So the source is split on the host's own declaration and the second
    /// saying is required **inside that one type**, bounded at the next
    /// top-level declaration (every type in this file is at column 0, and
    /// `struct ManualTopBar` is what follows the host).
    ///
    /// `wf/ios-b-hx-manual-sab2` is the measurement that this version bites:
    /// the same three lines deleted, and this case fails on its second
    /// assertion.
    func testAPushedLevelInstallsTheManualsLinkAction() {
        let halves = Self.manualScreen.components(separatedBy: "struct ManualLevelHost")
        XCTAssertEqual(
            halves.count, 2,
            "`struct ManualLevelHost` is written \(halves.count - 1) times in "
                + "the stripped source and this reading needs exactly one, "
                + "because it splits the file there to read the host's own "
                + "body apart from `ManualView`'s"
        )
        // The host's own source: after its declaration, up to the next
        // top-level one.
        let host = (halves.last ?? "").components(separatedBy: "\nstruct ").first ?? ""
        XCTAssertTrue(
            host.contains("let links: OpenURLAction"),
            "`ManualLevelHost` no longer takes the manual's link action. "
                + "`.environment(\\.openURL, …)` on `ManualView` reaches that "
                + "screen's descendants and not a level presented through "
                + "`navigationDestination(isPresented:)`, which train I005 "
                + "measured: a `route:` link tapped on a pushed piece did "
                + "nothing at all"
        )
        XCTAssertTrue(
            host.contains(".environment(\\.openURL, links)"),
            "`ManualLevelHost` carries `links` and never installs it on its "
                + "own content, so a pushed level's page still has whatever "
                + "action the system handed it: a tapped link goes to "
                + "`openURL` and nowhere, which is what train I005 measured. "
                + "This is read inside the host and not over the file, "
                + "because `ManualView`'s own body says the same line and a "
                + "reading that cannot tell the two apart stays green with "
                + "the fix deleted — see this case's note"
        )
        XCTAssertTrue(
            (halves.first ?? "").contains(".environment(\\.openURL, links)"),
            "`ManualView`'s own body no longer installs the link action, so a "
                + "link on the level the route itself draws — Start here, the "
                + "map, every level at depth 0 — goes to `openURL` and "
                + "nowhere. The host above covers the pushed levels only"
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
