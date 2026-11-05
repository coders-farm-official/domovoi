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

    /// One top-level type's **own** source: everything after its declaration,
    /// up to the next top-level one. Every type in `ManualView.swift` is
    /// declared at column 0, which is what `"\nstruct "` bounds the span at.
    ///
    /// `declarations` is how many times the declaration was found, so a case
    /// can refuse to read a span it cannot locate exactly once.
    private static func ownSource(
        of typeName: String
    ) -> (span: String, declarations: Int) {
        let parts = Self.manualScreen.components(separatedBy: "struct \(typeName)")
        let span = (parts.last ?? "").components(separatedBy: "\nstruct ").first ?? ""
        return (span, parts.count - 1)
    }

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
    /// Three sayings: the host has to be *given* the action (`links`), it has
    /// to put it in its own environment, and `ManualView` has to keep its own
    /// install for what the route draws outside a host. All three are
    /// textual, so a reflow of any of those lines reds this — the failure
    /// messages say what each line was for.
    ///
    /// ### Which install covers which screen
    ///
    /// Corrected 2026-11-04 by the integrator, on the verifier's open
    /// finding. This case used to say that `ManualView`'s install is what
    /// covers "every level at depth 0" and that the host covers "the pushed
    /// levels only". The app refutes both halves: `ManualView.swift:340-347`
    /// builds `ManualLevelHost(depth: 0, …, links: links)`, so the depth-0
    /// level is drawn *inside* a host and is covered by that host's own
    /// install at `:784`, exactly as every deeper level is. What is true:
    ///
    /// * `ManualView.swift:209` is the **outermost** install, so it reaches
    ///   everything the route draws that is not inside a host —
    ///   ``ManualTopBar``, the empty state, the cached line — and is a second
    ///   cover over the depth-0 host's content as well.
    /// * **Every** level, depth 0 included, is covered by the host that draws
    ///   it. A *pushed* level has nothing else: it is presented through
    ///   `navigationDestination(isPresented:)` and so is not an environment
    ///   descendant of the view that installed the action, which is the I005
    ///   measurement this whole file exists for.
    ///
    /// So the third saying holds a belt over the route's own chrome, not the
    /// only cover for depth 0. Whether a document link is ever drawn outside
    /// the depth-0 host is **not measured** — if it is not, that install is
    /// belt without braces and the manual unit may want to drop the saying
    /// rather than keep a message that has to explain itself.
    ///
    /// ### Each saying is read inside the **one type** it is about, and that
    /// is the whole case
    ///
    /// `.environment(\.openURL, links)` is written twice in this file:
    /// `ManualView`'s own body (`ManualView.swift:209`), which was never the
    /// bug, and ``ManualLevelHost``'s (`:784`), which is the fix. A `contains`
    /// over a span that holds both cannot tell them apart, and twice it was
    /// measured not to:
    ///
    /// * `wf/ios-b-hx-manual-sab1`: the host's three lines deleted — the I005
    ///   defect restored — and all three cases here passed, `PKGTEST exit 0`,
    ///   when the host's saying was a `contains` over the whole stripped
    ///   source.
    /// * `wf/ios-b-hx-manual-sab2`: `ManualView.swift:209` deleted and one
    ///   install moved into `ManualLevelView`, and the last saying passed,
    ///   `Executed 3 tests, with 0 failures`, when it was a `contains` over
    ///   everything *before* the host — a span that holds four top-level
    ///   types, `ManualView`, `ManualEmptyState`, `ManualCachedLine` and
    ///   `ManualLevelView`, while the failure message names the first.
    ///
    /// So each saying is read in ``ownSource(of:)``'s span: after that type's
    /// own declaration, up to the next top-level one (every type in this file
    /// is at column 0). `wf/ios-b-hx-manual-sab3` is the measurement that the
    /// last saying now bites — sab2's injection again, the install deleted
    /// from `ManualView` and the line written into `ManualLevelView`, and
    /// this case fails on it: `Executed 3 tests, with 1 failure`,
    /// `PKGTEST exit: 1`. (That injection is text, not a build: pkgtest
    /// compiles `DomovoiKit` and not `ios/Domovoi/`, and text is all these
    /// readings read.)
    func testAPushedLevelInstallsTheManualsLinkAction() {
        let host = Self.ownSource(of: "ManualLevelHost")
        XCTAssertEqual(
            host.declarations, 1,
            "`struct ManualLevelHost` is written \(host.declarations) times in "
                + "the stripped source and this reading needs exactly one, "
                + "because it reads the host's own body apart from "
                + "`ManualView`'s"
        )
        XCTAssertTrue(
            host.span.contains("let links: OpenURLAction"),
            "`ManualLevelHost` no longer takes the manual's link action. "
                + "`.environment(\\.openURL, …)` on `ManualView` reaches that "
                + "screen's descendants and not a level presented through "
                + "`navigationDestination(isPresented:)`, which train I005 "
                + "measured: a `route:` link tapped on a pushed piece did "
                + "nothing at all"
        )
        XCTAssertTrue(
            host.span.contains(".environment(\\.openURL, links)"),
            "`ManualLevelHost` carries `links` and never installs it on its "
                + "own content, so a pushed level's page still has whatever "
                + "action the system handed it: a tapped link goes to "
                + "`openURL` and nowhere, which is what train I005 measured. "
                + "This is read inside the host and not over the file, "
                + "because `ManualView`'s own body says the same line and a "
                + "reading that cannot tell the two apart stays green with "
                + "the fix deleted — see this case's note"
        )
        let route = Self.ownSource(of: "ManualView")
        XCTAssertEqual(
            route.declarations, 1,
            "`struct ManualView` is written \(route.declarations) times in the "
                + "stripped source and this reading needs exactly one, because "
                + "it reads the route's own body apart from the three types "
                + "that sit between it and the host"
        )
        XCTAssertTrue(
            route.span.contains(".environment(\\.openURL, links)"),
            "`ManualView`'s own body no longer installs the link action, so a "
                + "link in the route's own chrome — the top bar, the empty "
                + "state, the cached line, anything it draws outside a "
                + "`ManualLevelHost` — goes to `openURL` and nowhere. This is "
                + "NOT the cover for depth 0: `ManualView` builds "
                + "`ManualLevelHost(depth: 0, …, links: links)` and that host "
                + "installs the action for the level itself, the same way "
                + "every pushed level is covered. See this case's note. This "
                + "is read inside `ManualView` and not over everything before "
                + "the host, because `ManualEmptyState`, `ManualCachedLine` "
                + "and `ManualLevelView` are in that span too and a reading "
                + "that cannot tell them apart stays green with this install "
                + "deleted — see this case's note"
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
