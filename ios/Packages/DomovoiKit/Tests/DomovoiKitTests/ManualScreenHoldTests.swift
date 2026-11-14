import Foundation
import XCTest
@testable import DomovoiKit

/// Review round 1 of answer 32 ("a swipe back goes up one level"), read off
/// the screen's own source.
///
/// ### Why these cases read source text
///
/// None of the three defects was in this package, and none of them could be
/// red anywhere else.
///
/// Answer 32 made a level of the manual a **pushed screen** on the shell's
/// stack. That changes what `onDisappear` means on the manual: it used to
/// arrive only when the route was popped, and it now arrives whenever a level
/// goes on top. ``ManualView`` was carrying two things on it —
/// `manual?.stop()`, and the level host's "my child is gone" — and both are
/// wrong for a cover:
///
///  * `stop()` cancelled the document load and all six live reads, and
///    nothing restarted them, on a screen whose piece pages (depth 3) are
///    what *draw* those reads. Against the stub house the reads land during
///    the dwell on Start here and ``ApiRead/stop()`` keeps the data it has,
///    so all 23 of the manual's UI cases passed over it.
///  * the host's flag made every buried host believe its push had failed,
///    which fired a 700 ms fallback that **writes state on a covered
///    screen** — a body rebuild that re-declares that screen's own
///    `navigationDestination`, the family of thing train I003 measured
///    unwinding a push. The guard against repeating I003 was the likeliest
///    cause of one.
///
/// The third is a shape rather than a flag: the levels are presented on a
/// stack that the phone bar, a notification tap and a capability reset all
/// append to from outside this screen, so "the manual is still the route on
/// top" has to be part of whether a level is presented at all.
///
/// `ShellFoldViewBodyTests` established that reading the app target's source
/// from a package test is how this track gets a verdict on a view body with
/// no Simulator — it caught review 2's two recorded sabotages, which nothing
/// else did — and ``RoomsScreenHoldTests`` is the same reading for the same
/// `onDisappear` class on another screen. Its scanner is reused here, which
/// matters more on this file than on most: ``ManualView``'s doc comments
/// quote every symbol banned below, because they are the account of why it is
/// banned.
///
/// **Since answer 73** nothing of the manual is a pushed screen — every level
/// is drawn on one screen — so the host and its push are gone, and so are the
/// two cases that read them. What is left is still true: a route pushed over
/// the manual covers it and delivers `onDisappear`, so the hold stays; and the
/// manual's own swipe recogniser stays on the navigation controller's view
/// while it is covered, so whether the manual is the route on top is part of
/// whether that recogniser takes a swipe at all
/// (``testTheSwipeIsOnlyTakenWhileTheManualIsOnTop``).
///
/// What none of this can do is prove SwiftUI delivers `onDisappear` on a
/// cover, or that a recogniser in front of the pop gesture gets the touch. Those
/// are runtime facts and a train is what reads them
/// (`testManual_ASwipeFromAPieceGoesUpToItsTopic`). The point of the fixes is
/// that the screen no longer depends on the first answer.
final class ManualScreenHoldTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — ``RoomsScreenHoldTests``'
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
        in: ManualScreenHoldTests.source(ManualScreenHoldTests.manualScreenPath)
    )

    /// **The guard that stops every case below passing vacuously.** A renamed
    /// file reads as an empty string, and a ban over nothing is satisfied by
    /// nothing.
    func testTheManualScreenIsWhereThisFileLooksForIt() {
        XCTAssertFalse(
            Self.manualScreen.isEmpty,
            "\(Self.manualScreenPath) read as empty — the file moved or was "
                + "renamed, and until that is fixed the readings below pass on "
                + "nothing"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("struct ManualView"),
            "\(Self.manualScreenPath) has no `ManualView` in it, so this is "
                + "the wrong file"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("ManualBackSwipe("),
            "\(Self.manualScreenPath) no longer puts `ManualBackSwipe` on the "
                + "screen, so the reading about it below is about nothing"
        )
    }

    /// **Nothing on this screen is sent anything on its way out**, because
    /// `onDisappear` cannot tell a cover from a pop and both of the things
    /// that used to ride it meant the second.
    ///
    /// One ban, two defects:
    ///
    /// 1. `manual?.stop()` on that callback stopped the manual's six live
    ///    reads the moment the map was pushed over Start here. The lifetime
    ///    is ``ManualScreenHold``'s now: a `@State` object whose `deinit` is
    ///    the only thing in SwiftUI that fires for a destruction and not for
    ///    a cover.
    /// 2. the level host's `onDrawn(false)` told the host below it that its
    ///    pushed screen had gone whenever a *third* screen covered it, which
    ///    is how a working push reached the late-push fallback and wrote
    ///    state on a buried screen.
    func testTheManualWritesNothingOnItsWayOut() {
        XCTAssertFalse(
            Self.manualScreen.contains("onDisappear"),
            "\(Self.manualScreenPath) has an `onDisappear` again. A route "
                + "pushed over the manual covers it, so that callback arrives "
                + "for a cover: `stop()` on it stops the live reads the manual "
                + "draws when it is uncovered. `ManualScreenHold` is where the "
                + "lifetime belongs"
        )
        XCTAssertFalse(
            Self.manualScreen.contains("stop()"),
            "\(Self.manualScreenPath) calls `stop()` itself. The manual's "
                + "reads are stopped by `ManualScreenHold`'s `deinit` and by "
                + "nothing else — a view cannot tell its own cover from its "
                + "own destruction"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("@State private var hold: ManualScreenHold?"),
            "\(Self.manualScreenPath) no longer holds a `ManualScreenHold` in "
                + "`@State`. In any other storage it is not the view's "
                + "identity, and the whole fix is that it is"
        )
        XCTAssertTrue(
            Self.manualScreen.contains("ManualScreenHold(manual)"),
            "\(Self.manualScreenPath) never builds its `ManualScreenHold`, so "
                + "`ManualModel.start()` has no caller and the manual loads "
                + "nothing at all"
        )
    }

    /// **The manual takes a swipe only while it is the route on top** —
    /// answer 74's recogniser, read off the screen and the recogniser.
    ///
    /// The recogniser sits on the navigation controller's view and stays there
    /// while another route is pushed over the manual. If the decision it asks
    /// lost the "on top" half, a swipe on Music would move the manual
    /// underneath and pop nothing; if it lost the "fail at once" half, the
    /// system's pop would never get a touch from Start here and nobody could
    /// swipe out of the manual. `ManualAnswersTests.testASwipeBackGoesUpExactlyOneLevel`
    /// pins the decision itself; this pins that the screen asks it with the
    /// open route and that the recogniser obeys it.
    func testTheSwipeIsOnlyTakenWhileTheManualIsOnTop() {
        XCTAssertTrue(
            Self.manualScreen.contains("manualIsOnTop: model.navigation.route == .manual"),
            "`ManualView` no longer tells the swipe decision whether the "
                + "manual is the route on top, so its recogniser takes swipes "
                + "meant for a screen pushed over it"
        )
        let swipe = ShellFoldViewBodyTests.code(
            in: Self.source("ios/Domovoi/Manual/ManualBackSwipe.swift")
        )
        XCTAssertFalse(swipe.isEmpty, "ManualBackSwipe.swift read as empty — moved or renamed")
        XCTAssertTrue(
            swipe.contains("guard decide() == .upALevel else { return false }"),
            "the recogniser begins without asking the decision, so from Start "
                + "here it swallows the swipe the system's pop needs to leave "
                + "the manual"
        )
        XCTAssertTrue(
            swipe.contains("interactivePopGestureRecognizer?.require(toFail: edge)"),
            "the system's pop no longer waits for the manual's recogniser, so "
                + "a swipe from a level pops the route and leaves the manual — "
                + "what entry 74 described at the accessibility sizes"
        )
    }
}
